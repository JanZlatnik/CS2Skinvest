"""
auto_sync.py
────────────
Headless price sync script — runs silently in the background.
Called by Windows Task Scheduler; no Streamlit UI required.

Two-pass strategy:
  Pass 1 — fetch all unpriced items with relaxed delays (cf=1.5s, steam=3s)
  Pass 2 — retry any items that came back with no price (rate-limit victims),
            with even longer delays (cf=3s, steam=3s), run 30 min later

Usage:
    pythonw auto_sync.py              # silent (no console window)
    python  auto_sync.py              # with console output (for testing)
    python  auto_sync.py --retry      # force retry-unpriced pass immediately

Exit codes:
    0 — sync completed (or nothing to do)
    1 — no inventory found
    2 — unexpected error
"""

import sys
import os
import time
import logging
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
os.chdir(APP_DIR)
sys.path.insert(0, str(APP_DIR))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_PATH = APP_DIR / "data" / "auto_sync.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(LOG_PATH, maxBytes=500_000, backupCount=2, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[handler, logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("auto_sync")

try:
    from dotenv import load_dotenv
    load_dotenv(APP_DIR / ".env")
except ImportError:
    pass

RETRY_DELAY_MINUTES = 30   # wait before second pass
CF_DELAY_PASS1      = 1.5  # seconds between CSFloat calls — pass 1
STEAM_DELAY_PASS1   = 3.0  # seconds between Steam calls   — pass 1
CF_DELAY_PASS2      = 3.0  # seconds between CSFloat calls — pass 2 (retry)
STEAM_DELAY_PASS2   = 3.0  # seconds between Steam calls   — pass 2


def _run_pass(pass_num: int, retry: bool, cf_delay: float, steam_delay: float) -> int:
    """Run one sync pass. Returns number of items with missing prices after the pass."""
    import database
    import processor

    label = f"Pass {pass_num}" + (" (retry unpriced)" if retry else "")
    log.info(f"── {label} starting ──")

    def _cb(pct: float, msg: str, log_line: str | None = None):
        if log_line:
            log.info(f"[{pct:5.1%}]  {log_line}")

    processor.sync_prices(
        progress_cb=_cb,
        trigger="auto",
        retry_unpriced=retry,
        cf_delay=cf_delay,
        steam_delay=steam_delay,
    )

    # Count how many items still have no price today after this pass
    unpriced = database.get_items_unpriced_today()
    log.info(f"── {label} done. Items still unpriced: {len(unpriced)} ──")
    return len(unpriced)


def main() -> int:
    log.info("=" * 60)
    force_retry = "--retry" in sys.argv
    log.info(f"Auto-sync started  (force_retry={force_retry})")

    try:
        import database
        import processor

        database.init_db()

        inv = database.get_active_inventory_df()
        if inv.empty:
            log.warning("No active inventory — nothing to sync.")
            return 1

        already = database.get_items_with_todays_price()
        unpriced_today = database.get_items_unpriced_today()
        todo = inv[~inv["item_key"].isin(already - unpriced_today)]

        if todo.empty and not force_retry:
            log.info("All items already have good prices today — skipping.")
            database.meta_set("last_auto_sync",
                              datetime.now().strftime("%Y-%m-%d %H:%M"))
            return 0

        log.info(f"Inventory: {len(inv)} total  |  To fetch: {len(todo)}")

        if force_retry:
            # Immediate retry pass only (called by second Task Scheduler trigger)
            _run_pass(2, retry=True, cf_delay=CF_DELAY_PASS2, steam_delay=STEAM_DELAY_PASS2)
        else:
            # Pass 1: full sync
            remaining = _run_pass(1, retry=False,
                                  cf_delay=CF_DELAY_PASS1, steam_delay=STEAM_DELAY_PASS1)

            if remaining > 0:
                log.info(
                    f"{remaining} items unpriced after pass 1. "
                    f"Waiting {RETRY_DELAY_MINUTES} min before retry pass…"
                )
                time.sleep(RETRY_DELAY_MINUTES * 60)
                _run_pass(2, retry=True, cf_delay=CF_DELAY_PASS2, steam_delay=STEAM_DELAY_PASS2)
            else:
                log.info("All items priced after pass 1 — no retry needed.")

        log.info("Auto-sync completed successfully.")
        return 0

    except Exception as exc:
        log.exception(f"Auto-sync failed: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())