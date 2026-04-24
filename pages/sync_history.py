"""
sync_history.py  —  Sync History & Auto-Sync Setup page
"""
import streamlit as st
import pandas as pd
from datetime import datetime, date
import database
import scheduler

st.title("🕘 Sync History & Auto-Sync")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Controls")
    import processor
    if st.button("📦 Sync Inventory", use_container_width=True):
        with st.spinner("Rebuilding inventory…"):
            n = processor.sync_inventory()
            st.cache_data.clear()
            st.success(f"{n} active items")
            st.rerun()
    if st.button("💰 Sync Prices", use_container_width=True):
        st.switch_page("pages/sync_page.py")
    st.divider()
    st.caption(f"Prices: **{database.meta_get('last_price_sync') or 'never'}**")

tab_history, tab_auto = st.tabs(["📋 Sync History", "⚙️ Auto-Sync Setup"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Sync History
# NOTE: never call st.stop() inside a tab — it kills all other tabs too.
# ══════════════════════════════════════════════════════════════════════════════
with tab_history:

    METHOD_LABELS = {
        "basic":      "🔍 Basic",
        "float":      "📐 + Float",
        "seed":       "🎨 Paint seed",
        "seed_float": "🎨📐 Seed + float",
        "stale":      "⚠️ Last known",
        "no_price":   "🔴 Not found",
    }
    TRIGGER_LABELS = {"manual": "👆 Manual", "auto": "🤖 Auto"}

    run_dates = database.get_sync_run_dates()

    if not run_dates:
        st.info("No sync history yet. Run **💰 Sync Prices** to start recording history.")
    else:
        # ── Date picker ───────────────────────────────────────────────────────
        col_date, col_run = st.columns([2, 3])
        with col_date:
            available_dates = [date.fromisoformat(d) for d in run_dates]
            selected_date = st.date_input(
                "Select date",
                value=available_dates[0],
                min_value=available_dates[-1],
                max_value=available_dates[0],
            )

        runs = database.get_sync_runs_for_date(selected_date.isoformat())

        if not runs:
            st.info(f"No sync runs recorded for {selected_date}.")
        else:
            with col_run:
                def _run_label(r: dict) -> str:
                    trig  = TRIGGER_LABELS.get(r["trigger"], r["trigger"])
                    t     = r["timestamp"][11:16]
                    items = r["item_count"]
                    return f"{t}  ·  {trig}  ·  {items} items"

                run_labels   = {_run_label(r): r["run_id"] for r in runs}
                chosen_label = st.selectbox("Sync run", list(run_labels.keys()))

            chosen_run_id = run_labels[chosen_label]

            df = database.get_sync_log_for_run(chosen_run_id)

            if df.empty:
                st.warning("No data found for this run.")
            else:
                ok_n    = int(((df["cf_price"] > 0) & (df["stale"] == 0)).sum())
                stale_n = int(df["stale"].sum())
                miss_n  = int((df["cf_price"] == 0).sum())
                steam_n = int((df["steam_price"] > 0).sum())

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("✅ Priced",  ok_n)
                m2.metric("⚠️ Stale",  stale_n)
                m3.metric("🔴 Missing", miss_n)
                m4.metric("🌐 Steam",  steam_n)

                st.divider()

                display = df.copy()
                display["Status"] = display.apply(
                    lambda r: "⚠️ Stale"   if r["stale"]
                              else ("🔴 Missing" if r["cf_price"] == 0 else "✅ OK"),
                    axis=1,
                )
                display["Method"]   = display["method"].map(METHOD_LABELS).fillna(display["method"])
                display["CF Price"] = display["cf_price"].apply(
                    lambda p: f"${p:.2f}" if p > 0 else "—"
                )
                display["Steam"] = display["steam_price"].apply(
                    lambda p: f"${p:.2f}" if p > 0 else "—"
                )
                display = display.rename(columns={"item_name": "Item", "item_type": "Type"})
                display = display[["Status", "Item", "Type", "Method", "CF Price", "Steam"]]

                f1, f2 = st.columns(2)
                with f1:
                    status_filter = st.selectbox(
                        "Filter by status", ["All", "✅ OK", "⚠️ Stale", "🔴 Missing"]
                    )
                with f2:
                    search_term = st.text_input(
                        "Search item name", placeholder="AK-47, Karambit…"
                    )

                if status_filter != "All":
                    display = display[display["Status"] == status_filter]
                if search_term:
                    display = display[
                        display["Item"].str.contains(search_term, case=False, na=False)
                    ]

                st.dataframe(
                    display,
                    use_container_width=True,
                    hide_index=True,
                    height=min(700, 60 + len(display) * 35),
                    column_config={
                        "Status":   st.column_config.TextColumn("Status",  width="small"),
                        "Item":     st.column_config.TextColumn("Item"),
                        "Type":     st.column_config.TextColumn("Type",    width="small"),
                        "Method":   st.column_config.TextColumn("Method"),
                        "CF Price": st.column_config.TextColumn("CSFloat", width="small"),
                        "Steam":    st.column_config.TextColumn("Steam",   width="small"),
                    },
                )
                st.caption(
                    f"Showing {len(display)} of {len(df)} items  ·  Run ID: {chosen_run_id}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Auto-Sync Setup
# ══════════════════════════════════════════════════════════════════════════════
with tab_auto:

    on_windows = scheduler.is_windows()

    if not on_windows:
        st.warning(
            "**Auto-Sync Setup requires Windows.**  \n"
            "This feature uses Windows Task Scheduler to run price syncs "
            "automatically in the background.  \n\n"
            "On macOS/Linux you can achieve the same result with a cron job:  \n"
            "```\n0 6 * * * cd /path/to/app && python auto_sync.py\n```"
        )

    # ── Current status card ───────────────────────────────────────────────────
    st.subheader("Status")
    status    = scheduler.get_task_status()
    last_auto = database.meta_get("last_auto_sync")

    if status["exists"] and status["enabled"]:
        st.success("🟢 Auto-Sync is **enabled**")
    elif status["exists"] and not status["enabled"]:
        st.warning("🟡 Task exists but is **disabled** in Task Scheduler")
    else:
        st.info("⚪ Auto-Sync is **not set up** — configure it below")

    c1, c2, c3 = st.columns(3)
    c1.metric("Last auto-sync", last_auto or "never")
    c2.metric("Next scheduled", status["next_run"] or "—")
    c3.metric("Scheduled time", status["run_time"] or "—")

    if status["last_result"] is not None:
        code         = status["last_result"]
        result_label = "✅ Success (0)" if code == "0" else f"⚠️ Exit code {code}"
        st.caption(f"Last task result: **{result_label}**")

    st.divider()

    # ── Configure section ─────────────────────────────────────────────────────
    st.subheader("Configure")

    col_time, col_btn = st.columns([2, 3])

    with col_time:
        default_hour, default_minute = 6, 0
        if status["run_time"] and ":" in status["run_time"]:
            try:
                h, m = status["run_time"].split(":")
                default_hour, default_minute = int(h), int(m)
            except ValueError:
                pass

        run_hour   = st.number_input("Hour (0–23)",   min_value=0, max_value=23,
                                     value=default_hour,   step=1)
        run_minute = st.number_input("Minute (0–59)", min_value=0, max_value=59,
                                     value=default_minute, step=5)
        run_time_str = f"{int(run_hour):02d}:{int(run_minute):02d}"
        st.caption(f"Will run daily at **{run_time_str}**")
        st.caption(
            "⚡ If the computer is **off** at that time, the sync runs "
            "automatically the **next time it starts up** — no missed days."
        )

    with col_btn:
        st.markdown("&nbsp;", unsafe_allow_html=True)

        if on_windows:
            b1, b2 = st.columns(2)
            with b1:
                btn_label = "✅ Enable Auto-Sync" if not status["exists"] else "🔄 Update Schedule"
                if st.button(btn_label, use_container_width=True, type="primary"):
                    ok, msg = scheduler.create_task(run_time_str)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
            with b2:
                if st.button("🗑️ Remove Task", use_container_width=True,
                             disabled=not status["exists"], type="secondary"):
                    ok, msg = scheduler.delete_task()
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

            st.markdown("&nbsp;", unsafe_allow_html=True)

            if st.button("▶ Run Now (test)", use_container_width=True,
                         disabled=not status["exists"],
                         help="Trigger the task immediately to verify it works"):
                ok, msg = scheduler.run_task_now()
                if ok:
                    st.success(msg)
                    st.info(
                        "Sync is running in the background.  \n"
                        "Check the log below in ~1 minute, or open **📋 Sync History** "
                        "to see the new run appear."
                    )
                else:
                    st.error(msg)
        else:
            st.info(
                "Buttons are disabled on non-Windows systems.  \n"
                "Use a cron job to schedule `auto_sync.py` instead."
            )

    st.divider()

    # ── Auto-Sync Log viewer ──────────────────────────────────────────────────
    st.subheader("Auto-Sync Log")
    log_path = scheduler._app_dir() / "data" / "auto_sync.log"
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        recent = "".join(reversed(lines[-80:]))
        st.code(recent, language=None)
        st.caption(
            f"Log: `{log_path}`  ·  showing last {min(80, len(lines))} of {len(lines)} lines"
        )
    else:
        st.info("No log file yet — it will appear here after the first auto-sync run.")

    st.divider()

    # ── How it works explainer ────────────────────────────────────────────────
    with st.expander("ℹ️ How Auto-Sync works"):
        st.markdown("""
**Auto-Sync** uses **Windows Task Scheduler** to run `auto_sync.py` silently
in the background — no Streamlit window needed.

**What it does:**
- Fetches the latest CSFloat floor prices for every item in your inventory
- Fetches Steam market prices
- Saves everything to the database exactly like a manual Sync Prices run
- Writes a detailed log to `data/auto_sync.log`
- Records the run in **Sync History** with trigger = 🤖 Auto

**Missed runs:**
If your computer is off at the scheduled time, Windows runs the sync
automatically the **next time you start the computer** — so you never skip a day.

**Changing the time:**
Adjust hour/minute above and click **Update Schedule**.
You can also edit it directly in Windows Task Scheduler (`taskschd.msc`)
under the task name `CS2SkInvest_AutoSync`.

**Removing:**
Click **Remove Task**. Your sync history and price data are not affected.
        """)