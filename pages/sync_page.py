"""
sync_page.py  —  Dedicated price sync page with full-width live log.
Delegates all fetching to processor.sync_prices() so logic is never duplicated.
"""
import streamlit as st
import pandas as pd
import threading
import queue
import processor
import database

st.title("💰 Sync Prices")

METHOD_LABELS = {
    "basic":      "🔍 Basic",
    "float":      "📐 + Float",
    "seed":       "🎨 Paint seed",
    "seed_float": "🎨📐 Seed + float",
    "stale":      "⚠️ Last known",
    "no_price":   "🔴 Not found",
}

# ── Status / pre-flight ───────────────────────────────────────────────────────
last_sync = database.meta_get("last_price_sync")
st.caption(f"Last sync: **{last_sync}**" if last_sync else "No sync yet.")

inv     = database.get_active_inventory_df()
already = database.get_items_with_todays_price()
unpriced_today = database.get_items_unpriced_today()
todo    = inv[~inv["item_key"].isin(already)] if not inv.empty else pd.DataFrame()
retry_candidates = inv[inv["item_key"].isin(unpriced_today)] if not inv.empty else pd.DataFrame()

if inv.empty:
    st.info("No inventory found. Run **Sync Inventory** first.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total items",          len(inv))
c2.metric("Already priced today", len(already) - len(unpriced_today))
c3.metric("To fetch",             len(todo))
c4.metric("⚠️ Retry candidates",  len(retry_candidates),
          help="Items fetched today but with no price found or stale — eligible for retry")

st.divider()

# ── Action buttons ────────────────────────────────────────────────────────────
b1, b2 = st.columns(2)
with b1:
    start_full  = st.button(
        "▶ Sync Prices",
        type="primary",
        use_container_width=True,
        disabled=todo.empty,
        help="Fetch prices for all items not yet priced today",
    )
with b2:
    start_retry = st.button(
        "🔄 Retry Unpriced",
        use_container_width=True,
        disabled=retry_candidates.empty,
        help="Re-fetch only items that had no price or stale price in today's sync",
    )

if not start_full and not start_retry:
    if todo.empty and retry_candidates.empty:
        st.success("✅ All items have fresh prices today. Nothing to do.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# Live sync — processor.sync_prices streams progress via callback
# ══════════════════════════════════════════════════════════════════════════════

is_retry = start_retry and not start_full

current_item_display = st.empty()
progress_bar         = st.progress(0.0, text="Starting…")
st.divider()
log_area  = st.empty()
log_rows: list[dict] = []   # {status, name, method, cf_price, steam_price}


def _render_log():
    if not log_rows:
        return
    rows = []
    for r in reversed(log_rows):
        rows.append({
            "Status":   r["status"],
            "Item":     r["name"],
            "Method":   METHOD_LABELS.get(r.get("method", ""), r.get("method", "")),
            "CF Price": f"${r['cf_price']:.2f}" if r.get("cf_price", 0) > 0 else "—",
            "Steam":    f"${r['steam_price']:.2f}" if r.get("steam_price", 0) > 0 else "—",
        })
    log_area.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        height=min(700, 38 + len(rows) * 35),
        column_config={
            "Status":   st.column_config.TextColumn("Status",  width="small"),
            "Item":     st.column_config.TextColumn("Item"),
            "Method":   st.column_config.TextColumn("Method"),
            "CF Price": st.column_config.TextColumn("CSFloat", width="small"),
            "Steam":    st.column_config.TextColumn("Steam",   width="small"),
        },
    )


# The progress_cb is called from within sync_prices on the same thread.
# We parse the log_line to populate our display table.
current_name: dict = {"v": "", "method": "", "cf": 0.0, "stale": False, "steam": 0.0}


def _progress_cb(pct: float, msg: str, log_line: str | None = None):
    progress_bar.progress(min(pct, 1.0), text=msg[:120])
    current_item_display.markdown(f"### {msg[:120]}")

    if log_line is None:
        return

    line = log_line.strip()

    # Parse CSFloat result lines:  "✅ Name: method → $1.23"  or  "🔴 Name: ..."
    if line.startswith(("✅", "⚠️", "🔴")) and "→" in line:
        if line.startswith("✅"):
            status = "✅ Fresh"
        elif line.startswith("⚠️"):
            status = "⚠️ Stale"
        else:
            status = "🔴 Missing"

        # Extract name and method
        rest = line[2:].strip()   # strip emoji prefix
        if ":" in rest:
            name_part, detail = rest.split(":", 1)
            name   = name_part.strip()
            method = detail.split("→")[0].strip()
            try:
                cf_price = float(detail.split("$")[1].strip()) if "$" in detail else 0.0
            except (IndexError, ValueError):
                cf_price = 0.0

            # Find existing row for this name and update, or add new
            existing = next((r for r in log_rows if r["name"] == name), None)
            if existing:
                existing.update({"status": status, "method": method, "cf_price": cf_price})
            else:
                log_rows.append({
                    "status":    status,
                    "name":      name,
                    "method":    method,
                    "cf_price":  cf_price,
                    "steam_price": 0.0,
                })

    # Parse Steam result lines:  "   🌐 Steam → $1.23"
    elif "🌐 Steam →" in line:
        try:
            st_price = float(line.split("$")[1].strip()) if "$" in line else 0.0
        except (IndexError, ValueError):
            st_price = 0.0
        # Apply to the most recently added row that has no steam price yet
        # Steam always follows immediately after its CF call for the same item
        for r in reversed(log_rows):
            if r.get("steam_price", 0) == 0:
                r["steam_price"] = st_price
                break

    _render_log()


processor.sync_prices(
    progress_cb=_progress_cb,
    trigger="manual",
    retry_unpriced=is_retry,
    cf_delay=0.35,
    steam_delay=1.5,
)
st.cache_data.clear()

# ── Done ──────────────────────────────────────────────────────────────────────
progress_bar.progress(1.0, text="✅ Done!")
ok_n    = sum(1 for r in log_rows if r.get("status") == "✅ Fresh")
stale_n = sum(1 for r in log_rows if r.get("status") == "⚠️ Stale")
miss_n  = sum(1 for r in log_rows if r.get("status") == "🔴 Missing")
skipped = len(inv) - len(todo if not is_retry else retry_candidates)

current_item_display.markdown(
    f"### ✅ {'Retry' if is_retry else 'Sync'} complete  —  "
    f"{len(log_rows)} fetched · {skipped} skipped"
)

s1, s2, s3 = st.columns(3)
s1.metric("✅ Fresh",   ok_n)
s2.metric("⚠️ Stale",  stale_n)
s3.metric("🔴 Missing", miss_n)

if miss_n > 0:
    st.info(
        f"**{miss_n} item(s)** had no price found. "
        "Click **🔄 Retry Unpriced** to attempt them again with fresh rate-limit headroom."
    )

st.caption("Full history in **🕘 Sync History**")