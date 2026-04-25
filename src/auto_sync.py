"""
auto_sync.py
------------
Headless price sync script -- runs silently in the background.
Called by Windows Task Scheduler; no Streamlit UI required.

Two-pass strategy:
  Pass 1 -- fetch all unpriced items with relaxed delays (cf=1.5s, steam=3s)
  Pass 2 -- retry items that came back with no price (rate-limit victims),
            with longer delays (cf=3s, steam=3s), run 30 min later

Usage:
    pythonw src\\auto_sync.py              # silent (no console window)
    python  src\\auto_sync.py              # with console output (for testing)
    python  src\\auto_sync.py --retry      # force retry-unpriced pass immediately

Exit codes:
    0 -- sync completed (or nothing to do)
    1 -- no inventory found
    2 -- unexpected error
"""

import sys
import os
import time
import logging
from datetime import datetime
from pathlib import Path

# Resolve directories relative to this file's location
SRC_DIR  = Path(__file__).resolve().parent   # .../repo/src
ROOT_DIR = SRC_DIR.parent                    # .../repo

# Set CWD to ROOT_DIR so all relative "data/..." paths in modules work correctly
os.chdir(ROOT_DIR)

# Make sure src/ modules are importable (database, processor, etc.)
sys.path.insert(0, str(SRC_DIR))

# ── Logging ------------------------------------------------------------------
LOG_PATH = ROOT_DIR / "data" / "auto_sync.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

from logging.handlers import RotatingFileHandler

_handler = RotatingFileHandler(LOG_PATH, maxBytes=500_000, backupCount=2, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[_handler, logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("auto_sync")

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")   # .env lives at repo root
except ImportError:
    pass

RETRY_DELAY_MINUTES = 30
CF_DELAY_PASS1      = 1.5
STEAM_DELAY_PASS1   = 3.0
CF_DELAY_PASS2      = 3.0
STEAM_DELAY_PASS2   = 3.0


def _run_pass(pass_num: int, retry: bool, cf_delay: float, steam_delay: float) -> int:
    """Run one sync pass. Returns number of items with missing prices after the pass."""
    import database
    import processor

    label = "Pass {}".format(pass_num) + (" (retry unpriced)" if retry else "")
    log.info("-- {} starting --".format(label))

    def _cb(pct, msg, log_line=None):
        if log_line:
            log.info("[{:5.1%}]  {}".format(pct, log_line))

    processor.sync_prices(
        progress_cb=_cb,
        trigger="auto",
        retry_unpriced=retry,
        cf_delay=cf_delay,
        steam_delay=steam_delay,
    )

    unpriced = database.get_items_unpriced_today()
    log.info("-- {} done. Items still unpriced: {} --".format(label, len(unpriced)))
    return len(unpriced)


def main() -> int:
    log.info("=" * 60)
    force_retry = "--retry" in sys.argv
    log.info("Auto-sync started  (force_retry={})".format(force_retry))

    try:
        import database
        import processor

        database.init_db()

        inv = database.get_active_inventory_df()
        if inv.empty:
            log.warning("No active inventory -- nothing to sync.")
            return 1

        already        = database.get_items_with_todays_price()
        unpriced_today = database.get_items_unpriced_today()
        todo           = inv[~inv["item_key"].isin(already - unpriced_today)]

        if todo.empty and not force_retry:
            log.info("All items already have good prices today -- skipping.")
            database.meta_set("last_auto_sync",
                              datetime.now().strftime("%Y-%m-%d %H:%M"))
            return 0

        log.info("Inventory: {} total  |  To fetch: {}".format(len(inv), len(todo)))

        if force_retry:
            _run_pass(2, retry=True, cf_delay=CF_DELAY_PASS2, steam_delay=STEAM_DELAY_PASS2)
        else:
            remaining = _run_pass(1, retry=False,
                                  cf_delay=CF_DELAY_PASS1, steam_delay=STEAM_DELAY_PASS1)
            if remaining > 0:
                log.info(
                    "{} items unpriced after pass 1. "
                    "Waiting {} min before retry pass...".format(
                        remaining, RETRY_DELAY_MINUTES)
                )
                time.sleep(RETRY_DELAY_MINUTES * 60)
                _run_pass(2, retry=True, cf_delay=CF_DELAY_PASS2, steam_delay=STEAM_DELAY_PASS2)
            else:
                log.info("All items priced after pass 1 -- no retry needed.")

        log.info("Auto-sync completed successfully.")
        return 0

    except Exception as exc:
        log.exception("Auto-sync failed: {}".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())