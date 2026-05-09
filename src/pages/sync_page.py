"""
sync_page.py  —  Sync Prices + Auto-Sync Setup (unified page)

Architecture
------------
• Sync runs in a background daemon thread (survives page navigation).
• Progress is written to DB meta by processor.sync_prices() on every item.
• @st.fragment(run_every=0.5) polls DB meta and updates ONLY the status
  widget — no full-page refresh, no forced navigation back.
• When the user leaves the page the fragment stops. When they return it
  restarts and reads current state from DB.
"""

import threading
import logging
import streamlit as st
import pandas as pd
import processor
import database
import scheduler
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── File logger — appends manual sync entries to the same auto_sync.log ───────
_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "auto_sync.log"
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

def _get_sync_logger() -> logging.Logger:
    """Return a logger that writes to auto_sync.log (creates handler once)."""
    logger = logging.getLogger("manual_sync")
    if not logger.handlers:
        handler = RotatingFileHandler(
            _LOG_PATH, maxBytes=500_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger

# ── CSS: teal primary buttons + target both Streamlit selector variants ────────
st.markdown("""
<style>
/* Streamlit ≥ 1.31 uses data-testid, older uses kind attribute */
[data-testid="stBaseButton-primary"],
.stButton > button[kind="primary"] {
    background-color: #0a7c6e !important;
    color: #ffffff !important;
    border: none !important;
}
[data-testid="stBaseButton-primary"]:hover,
.stButton > button[kind="primary"]:hover {
    background-color: #0d9e8e !important;
    color: #ffffff !important;
}
[data-testid="stBaseButton-primary"]:active,
.stButton > button[kind="primary"]:active {
    background-color: #086358 !important;
}
/* Disabled primary buttons — muted, clearly inactive */
[data-testid="stBaseButton-primary"]:disabled,
.stButton > button[kind="primary"]:disabled {
    background-color: transparent !important;
    color: rgba(255,255,255,0.35) !important;
    border: 1px solid rgba(255,255,255,0.15) !important;
    cursor: not-allowed !important;
}
</style>
""", unsafe_allow_html=True)

st.title("💰 Sync Prices")

METHOD_LABELS = {
    "basic":      "🔍 Basic",
    "imprecise":  "⚠️ Imprecise",
    "float":      "📐 + Float",
    "seed":       "🎨 Paint seed",
    "seed_float": "🎨📐 Seed + float",
    "stale":      "♻️ Last known",
    "no_price":   "🔴 Not found",
}

# ── Session state — result rows accumulated by the background thread ──────────
for _k, _v in [
    ("_sync_rows",     []),
    ("_sync_is_retry", False),
    ("_sync_lock",     None),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

if st.session_state["_sync_lock"] is None:
    st.session_state["_sync_lock"] = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════════
# ① LIVE STATUS FRAGMENT
#    run_every=0.5 → Streamlit re-runs ONLY this function every 500 ms.
#    The rest of the page is untouched. When the user leaves, it stops.
# ═══════════════════════════════════════════════════════════════════════════════
@st.fragment(run_every=0.5)
def _live_status():
    status      = database.sync_status_get()
    is_running  = status["running"]
    is_stuck    = status["stuck"]
    pct         = status["pct"]
    msg         = status["msg"]

    if is_stuck:
        st.error(
            "⚠️ A sync started more than 3 hours ago and appears to be stuck.  \n"
            "If no sync is actually running, you can safely start a new one — "
            "the stuck flag will be cleared automatically."
        )
    elif is_running:
        st.info("⏳ **Sync is running** — you can navigate away and come back.", icon="ℹ️")
        st.progress(min(pct, 0.999), text=msg[:120] if msg else "Working…")
        st.caption(f"**{msg[:120]}**" if msg else "")
    else:
        # Show result table once sync has just finished (rows in session_state)
        with st.session_state["_sync_lock"]:
            log_rows = list(st.session_state["_sync_rows"])

        if log_rows and not is_running:
            is_retry_run = st.session_state["_sync_is_retry"]
            ok_n        = sum(1 for r in log_rows if r.get("status") == "✅ Fresh")
            imprecise_n = sum(1 for r in log_rows if r.get("status") == "⚠️ Imprecise")
            stale_n     = sum(1 for r in log_rows if r.get("status") == "♻️ Stale")
            miss_n      = sum(1 for r in log_rows if r.get("status") == "🔴 Missing")

            st.progress(1.0, text="✅ Done!")
            st.markdown(
                f"### ✅ {'Retry' if is_retry_run else 'Sync'} complete  —  "
                f"{len(log_rows)} fetched"
            )
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("✅ Fresh",      ok_n)
            s2.metric("⚠️ Imprecise", imprecise_n)
            s3.metric("♻️ Stale",     stale_n)
            s4.metric("🔴 Missing",   miss_n)

            if imprecise_n > 0:
                st.info(
                    f"**{imprecise_n} item(s)** got a wear-floor price without float or "
                    "pattern matching."
                )
            if miss_n > 0:
                st.info(
                    f"**{miss_n} item(s)** had no price found. "
                    "Click **🔄 Retry Unpriced** to attempt them again."
                )
            st.cache_data.clear()

            rows = []
            for r in reversed(log_rows):
                rows.append({
                    "Status":   r["status"],
                    "Item":     r["name"],
                    "Method":   METHOD_LABELS.get(r.get("method", ""), r.get("method", "")),
                    "CF Price": f"${r['cf_price']:.2f}" if r.get("cf_price", 0) > 0 else "—",
                    "Steam":    f"${r['steam_price']:.2f}" if r.get("steam_price", 0) > 0 else "—",
                })
            st.dataframe(
                pd.DataFrame(rows),
                width="stretch",
                hide_index=True,
                height=min(600, 38 + len(rows) * 35),
                column_config={
                    "Status":   st.column_config.TextColumn("Status",  width="small"),
                    "Item":     st.column_config.TextColumn("Item"),
                    "Method":   st.column_config.TextColumn("Method"),
                    "CF Price": st.column_config.TextColumn("CSFloat", width="small"),
                    "Steam":    st.column_config.TextColumn("Steam",   width="small"),
                },
            )
            st.caption("Full history in **📋 Price History**")
        else:
            last_sync = database.meta_get("last_price_sync")
            st.caption(f"Last sync: **{last_sync}**" if last_sync else "No sync yet.")

_live_status()

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# ② METRICS + ACTION BUTTONS  (static — only re-renders on real page load)
# ═══════════════════════════════════════════════════════════════════════════════
sync_status  = database.sync_status_get()
sync_running = sync_status["running"] or sync_status["stuck"]

inv              = database.get_active_inventory_df()
already          = database.get_items_with_todays_price()
unpriced_today   = database.get_items_unpriced_today()
todo             = inv[~inv["item_key"].isin(already)] if not inv.empty else pd.DataFrame()
retry_candidates = inv[inv["item_key"].isin(unpriced_today)] if not inv.empty else pd.DataFrame()

if inv.empty:
    st.info("No inventory found. Run **Sync Inventory** (sidebar) first.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total items",          len(inv))
    c2.metric("Already priced today", len(already) - len(unpriced_today))
    c3.metric("To fetch",             len(todo))
    c4.metric("⚠️ Retry candidates",  len(retry_candidates),
              help="Items fetched today but with no price found or stale — eligible for retry")

    st.divider()

    b1, b2, b3 = st.columns(3)
    with b1:
        start_full = st.button(
            "▶ Sync Prices",
            type="primary",
            width="stretch",
            disabled=todo.empty or sync_running,
            help="Fetch prices for all items not yet priced today",
        )
    with b2:
        start_retry = st.button(
            "🔄 Retry Unpriced",
            width="stretch",
            disabled=retry_candidates.empty or sync_running,
            help="Re-fetch only items that had no price or stale price in today's sync",
        )
    with b3:
        reset_today = st.button(
            "🗑️ Reset Today's Pricing",
            width="stretch",
            type="secondary",
            disabled=sync_running or (
                (already - unpriced_today == set()) and len(already) == 0
            ),
            help=(
                "Delete ALL today's price records, sync log entries and portfolio "
                "snapshots, then roll back last_price_sync to yesterday."
            ),
        )

    # ── Handle Reset ──────────────────────────────────────────────────────────
    if reset_today:
        result = database.reset_todays_pricing()
        ph = result["price_history"]
        ps = result["portfolio_snapshots"]
        rb = result["rolled_back_to"] or "—"
        st.success(
            f"✅ Reset complete — deleted **{ph}** price records, "
            f"**{ps}** portfolio snapshots. "
            f"Last sync rolled back to **{rb}**."
        )
        st.cache_data.clear()
        st.rerun()

    # ── Launch background sync ─────────────────────────────────────────────────
    if (start_full or start_retry) and not sync_running:
        is_retry = start_retry and not start_full

        with st.session_state["_sync_lock"]:
            st.session_state["_sync_rows"]     = []
            st.session_state["_sync_is_retry"] = is_retry

        lock = st.session_state["_sync_lock"]

        def _background_sync(is_retry: bool, lock: threading.Lock):
            log_rows: list[dict] = []
            log = _get_sync_logger()
            label = "Manual retry" if is_retry else "Manual sync"
            log.info("=" * 60)
            log.info(f"-- {label} starting --")

            def _cb(pct: float, msg: str, log_line: str | None = None):
                if log_line:
                    log.info("[{:5.1%}]  {}".format(pct, log_line))
                if log_line is None:
                    return
                line = log_line.strip()
                if line.startswith(("✅", "⚠️", "♻️", "🔴")) and "→" in line:
                    status = (
                        "✅ Fresh"     if line.startswith("✅") else
                        "⚠️ Imprecise" if line.startswith("⚠️") else
                        "♻️ Stale"     if line.startswith("♻️") else
                        "🔴 Missing"
                    )
                    rest = line[2:].strip()
                    if ":" in rest:
                        name_part, detail = rest.split(":", 1)
                        name   = name_part.strip()
                        method = detail.split("→")[0].strip()
                        try:
                            cf_price = float(detail.split("$")[1].strip()) if "$" in detail else 0.0
                        except (IndexError, ValueError):
                            cf_price = 0.0
                        existing = next((r for r in log_rows if r["name"] == name), None)
                        if existing:
                            existing.update({"status": status, "method": method, "cf_price": cf_price})
                        else:
                            log_rows.append({
                                "status": status, "name": name,
                                "method": method, "cf_price": cf_price, "steam_price": 0.0,
                            })
                elif "🌐 Steam →" in line:
                    try:
                        st_price = float(line.split("$")[1].strip()) if "$" in line else 0.0
                    except (IndexError, ValueError):
                        st_price = 0.0
                    for r in reversed(log_rows):
                        if r.get("steam_price", 0) == 0:
                            r["steam_price"] = st_price
                            break
                with lock:
                    st.session_state["_sync_rows"] = list(log_rows)

            processor.sync_prices(
                progress_cb=_cb,
                trigger="manual",
                retry_unpriced=is_retry,
                cf_delay=1.5,
                steam_delay=1.5,
            )
            unpriced = sum(1 for r in log_rows if r.get("status") == "🔴 Missing")
            log.info(f"-- {label} done. Items still unpriced: {unpriced} --")

        threading.Thread(
            target=_background_sync,
            args=(is_retry, lock),
            daemon=True,
            name="cs2_sync_thread",
        ).start()
        # Single rerun just to disable the buttons immediately
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# ③ AUTO-SYNC SETUP
# ═══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("⚙️ Auto-Sync Setup")

on_windows  = scheduler.is_windows()
task_status = scheduler.get_task_status()
last_auto   = database.meta_get("last_auto_sync")

# Status card
if task_status["exists"] and task_status["enabled"]:
    st.success("🟢 Auto-Sync is **enabled**")
elif task_status["exists"] and not task_status["enabled"]:
    st.warning("🟡 Task exists but is **disabled** in Task Scheduler")
else:
    st.info("⚪ Auto-Sync is **not set up** — configure it below")

m1, m2, m3 = st.columns(3)
m1.metric("Last auto-sync", last_auto or "never")
m2.metric("Next scheduled", task_status["next_run"] or "—")
raw_rt = task_status["run_time"] or ""
if raw_rt and ":" in raw_rt:
    h_r, m_r = raw_rt.split(":")[:2]
    formatted_rt = f"{int(h_r):02d}:{int(m_r):02d}"
else:
    formatted_rt = raw_rt or "—"
m3.metric("Scheduled time", formatted_rt)
if task_status["last_result"] is not None:
    code = task_status["last_result"]
    st.caption(f"Last task result: **{'✅ Success (0)' if code == '0' else f'⚠️ Exit code {code}'}**")

st.divider()

# Admin warning — full width, above the two-column configure section
if on_windows:
    is_admin = scheduler.is_admin()
    if not is_admin:
        st.warning(
            "⚠️ **Administrator rights not detected.**  \n"
            "Task Scheduler operations (create / update / remove) may fail without "
            "elevation. If you get an *Access denied* error, right-click the launcher "
            "and choose **\"Run as administrator\"**."
        )
else:
    st.warning(
        "**Auto-Sync Setup requires Windows.**  \n"
        "On macOS/Linux use a cron job:  \n"
        "```\n0 6 * * * cd /path/to/app && python src/auto_sync.py\n```"
    )

# Two-column configure: trigger mode left, buttons right
col_mode, col_btns = st.columns([3, 2])

with col_mode:
    saved_mode  = database.meta_get("auto_sync_trigger_mode") or "daily"
    mode_labels = list(scheduler.TRIGGER_MODES.values())
    mode_keys   = list(scheduler.TRIGGER_MODES.keys())
    default_idx = mode_keys.index(saved_mode) if saved_mode in mode_keys else 0

    sel_mode_label = st.radio(
        "Trigger mode",
        mode_labels,
        index=default_idx,
        key="auto_sync_mode",
    )
    sel_mode = mode_keys[mode_labels.index(sel_mode_label)]

    run_time_str = "06:00"
    if sel_mode == "daily":
        default_hour, default_minute = 6, 0
        if task_status["run_time"] and ":" in task_status["run_time"]:
            try:
                h, m = task_status["run_time"].split(":")
                default_hour, default_minute = int(h), int(m)
            except ValueError:
                pass
        t1, t2 = st.columns(2)
        run_hour   = t1.number_input("Hour (0–23)",   min_value=0, max_value=23,
                                     value=default_hour,   step=1)
        run_minute = t2.number_input("Minute (0–59)", min_value=0, max_value=59,
                                     value=default_minute, step=5)
        run_time_str = f"{int(run_hour):02d}:{int(run_minute):02d}"
        st.caption(
            f"Will run daily at **{run_time_str}**.  \n"
            "⚡ `StartWhenAvailable` ensures it fires on next startup if the PC was off."
        )
    elif sel_mode == "logon":
        st.caption(
            "Runs **every time you log in** or the PC boots up.  \n"
            "`auto_sync.py` skips the work if prices are already fresh today."
        )
    elif sel_mode == "hourly":
        st.caption(
            "Runs **every hour**. If prices were already fetched today the "
            "script exits in under a second — no extra load."
        )

with col_btns:
    st.markdown("&nbsp;", unsafe_allow_html=True)   # vertical breathing room
    if on_windows:
        btn_label = "✅ Enable Auto-Sync" if not task_status["exists"] else "🔄 Update Schedule"
        if st.button(btn_label, width="stretch", type="primary"):
            ok, msg = scheduler.create_task(run_time_str, trigger_mode=sel_mode)
            if ok:
                database.meta_set("auto_sync_trigger_mode", sel_mode)
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
                if "access is denied" in msg.lower() or "administrator" in msg.lower():
                    st.info("💡 Right-click the launcher / terminal → **Run as administrator**.")

        if st.button("🗑️ Remove Task", width="stretch",
                     disabled=not task_status["exists"], type="secondary"):
            ok, msg = scheduler.delete_task()
            if ok:
                database.meta_set("auto_sync_trigger_mode", "daily")
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
                if "access is denied" in msg.lower() or "administrator" in msg.lower():
                    st.info("💡 Right-click the launcher / terminal → **Run as administrator**.")
    else:
        st.info("Use a cron job to schedule `auto_sync.py`.")

with st.expander("ℹ️ How Auto-Sync works"):
    st.markdown("""
**Auto-Sync** uses **Windows Task Scheduler** to run `auto_sync.py` silently
in the background — no Streamlit window needed.

| Mode | When does it run? |
|---|---|
| **Daily at set time** | Once per day at your chosen hour. `StartWhenAvailable` fires it on next startup if the PC was off. |
| **At every startup / login** | Every time you log in or boot. Skips immediately if prices are already fresh. |
| **Every hour** | Every hour; exits in under a second if today's prices are already synced. |

**What it does:** fetches CSFloat + Steam prices for every inventory item,
saves to DB, records in **📋 Price History** with trigger = 🤖 Auto.

**Removing:** click **Remove Task**. Sync history and price data are unaffected.

Edit directly in Task Scheduler (`taskschd.msc`) under `CS2SkInvest_AutoSync`.
    """)