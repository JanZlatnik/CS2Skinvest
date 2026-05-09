"""
sync_history.py  —  📋 Price History page

Tab 1 — Sync History   : date picker → run selector → results table (unchanged)
Tab 2 — Sync Log       : auto_sync.log viewer with adjustable line count
"""
import streamlit as st
import pandas as pd
from datetime import date
import database
import scheduler

st.title("📋 Price History")

tab_history, tab_log = st.tabs(["📋 Sync History", "📄 Sync Log"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Sync History  (unchanged from original)
# NOTE: never call st.stop() inside a tab — it kills all other tabs too.
# ══════════════════════════════════════════════════════════════════════════════
with tab_history:

    METHOD_LABELS = {
        "basic":      "🔍 Basic",
        "imprecise":  "⚠️ Imprecise",
        "float":      "📐 + Float",
        "seed":       "🎨 Paint seed",
        "seed_float": "🎨📐 Seed + float",
        "stale":      "♻️ Last known",
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
                ok_n        = int(((df["cf_price"] > 0) & (df["stale"] == 0) & (df["method"] != "imprecise")).sum())
                imprecise_n = int(((df["cf_price"] > 0) & (df["stale"] == 0) & (df["method"] == "imprecise")).sum())
                stale_n     = int(df["stale"].sum())
                miss_n      = int((df["cf_price"] == 0).sum())
                steam_n     = int((df["steam_price"] > 0).sum())

                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("✅ Fresh",      ok_n)
                m2.metric("⚠️ Imprecise", imprecise_n)
                m3.metric("♻️ Stale",     stale_n)
                m4.metric("🔴 Missing",   miss_n)
                m5.metric("🌐 Steam",     steam_n)

                st.divider()

                display = df.copy()
                display["Status"] = display.apply(
                    lambda r: (
                        "♻️ Stale"      if r["stale"]
                        else "🔴 Missing"   if r["cf_price"] == 0
                        else "⚠️ Imprecise" if r["method"] == "imprecise"
                        else "✅ Fresh"
                    ),
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
                        "Filter by status",
                        ["All", "✅ Fresh", "⚠️ Imprecise", "♻️ Stale", "🔴 Missing"],
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
                    width="stretch",
                    hide_index=True,
                    height=min(600, 60 + len(display) * 35),
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
# TAB 2 — Sync Log
# ══════════════════════════════════════════════════════════════════════════════
with tab_log:

    log_path = scheduler._app_dir() / "data" / "auto_sync.log"

    if not log_path.exists():
        st.info(
            "No log file yet — it will appear here after the first auto-sync run.  \n"
            f"Expected path: `{log_path}`"
        )
    else:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()

        total_lines = len(all_lines)

        # ── Line count selector ───────────────────────────────────────────────
        count_col, _ = st.columns([2, 4])
        with count_col:
            LINE_OPTIONS = {
                "Last 100":   100,
                "Last 500":   500,
                "Last 1 000": 1000,
                "All":        None,
            }
            # Default to whichever option is closest to 100 but still valid
            default_label = "Last 100" if total_lines >= 100 else "All"
            chosen_label  = st.selectbox(
                "Show lines",
                list(LINE_OPTIONS.keys()),
                index=list(LINE_OPTIONS.keys()).index(default_label),
            )
        n_lines = LINE_OPTIONS[chosen_label]

        if n_lines is None or n_lines >= total_lines:
            shown_lines = all_lines
            n_shown     = total_lines
        else:
            shown_lines = all_lines[-n_lines:]
            n_shown     = n_lines

        st.caption(
            f"Log: `{log_path}`  ·  showing last **{n_shown}** of **{total_lines}** lines"
        )

        # Newest lines on top
        log_text = "".join(reversed(shown_lines))
        st.code(log_text, language=None)