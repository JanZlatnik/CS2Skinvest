"""
sync_page.py  —  Dedicated price sync page with full-width live log.
"""
import streamlit as st
import pandas as pd
import time
import processor, database

st.title("💰 Sync Prices")

# ── Status info ───────────────────────────────────────────────────────────────
last_sync = database.meta_get("last_price_sync")
col_info, col_btn = st.columns([3, 1])
with col_info:
    if last_sync:
        st.caption(f"Last sync: **{last_sync}**")
    else:
        st.caption("No sync yet.")
with col_btn:
    start = st.button("▶ Start Sync", type="primary", use_container_width=True)

st.divider()

if not start:
    inv = database.get_active_inventory_df()
    already = database.get_items_with_todays_price()
    todo    = inv[~inv["item_key"].isin(already)] if not inv.empty else pd.DataFrame()

    if inv.empty:
        st.info("No inventory found. Run **Sync Inventory** first.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total items", len(inv))
        c2.metric("Already priced today", len(already))
        c3.metric("To fetch", len(todo))
        if todo.empty:
            st.success("✅ All items already have today's prices. Nothing to fetch.")
        else:
            st.info(f"Click **▶ Start Sync** to fetch prices for {len(todo)} items.")
    st.stop()

# ── Live sync ─────────────────────────────────────────────────────────────────

# Current item display (above progress bar)
current_item_display = st.empty()

# Progress bar
progress_bar = st.progress(0.0, text="Starting…")

st.divider()

log_area = st.empty()   # dataframe renders here, updated after each item

# ── State for the log table ───────────────────────────────────────────────────
log_rows: list[dict] = []   # {name, method, cf_price, steam_price, stale}

METHOD_LABELS = {
    "basic":      "🔍 Basic",
    "float":      "📐 + Float",
    "seed":       "🎨 Paint seed",
    "seed_float": "🎨📐 Seed + float",
    "stale":      "⚠️ Last known",
    "no_price":   "🔴 Not found",
    "steam_avg":  "🌐 Steam",
}


def _render_log():
    """Re-render the log table — newest entry at the top."""
    if not log_rows:
        return
    rows = []
    for r in reversed(log_rows):          # ← newest first
        cf_str = (f"⚠️ ${r['cf_price']:.2f}" if r.get("stale") else
                  (f"${r['cf_price']:.2f}" if r["cf_price"] > 0 else "🔴 N/A"))
        st_str = f"${r['steam_price']:.2f}" if r["steam_price"] > 0 else "—"
        rows.append({
            "Item":     r["name"],
            "Method":   METHOD_LABELS.get(r["method"], r["method"]),
            "CF Price": cf_str,
            "Steam":    st_str,
        })
    df = pd.DataFrame(rows)
    log_area.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(700, 38 + len(rows) * 35),   # ← taller table
    )


# Temporary store while we process both CF and Steam
cf_results:   dict[str, tuple[float, bool, str]] = {}   # item_key → (price, stale, method)
steam_prices: dict[str, float]                   = {}   # item_name → price

# ── Run sync ──────────────────────────────────────────────────────────────────
inv = database.get_active_inventory_df()
if inv.empty:
    st.info("No inventory.")
    st.stop()

already_priced = database.get_items_with_todays_price()
inv_todo = inv[~inv["item_key"].isin(already_priced)]

if inv_todo.empty:
    progress_bar.progress(1.0, text="✅ All items already priced today.")
    current_item_display.markdown("### ✅ All items already priced today — nothing to fetch.")
    st.stop()

total   = len(inv_todo)
n_names = len(inv_todo["item_name"].unique())

import csf_pricer

# ── Phase 1: CSFloat ──────────────────────────────────────────────────────────
for idx, (_, row) in enumerate(inv_todo.iterrows()):
    name   = row["item_name"]
    count  = idx + 1                              # ← 1-based display counter
    pct    = idx / (total + n_names)
    progress_bar.progress(pct, text=f"📦 CSFloat ({count}/{total}): {name}")
    current_item_display.markdown(f"### 📦 CSFloat ({count}/{total})\n`{name}`")

    price, stale, method = csf_pricer.fetch_cf_price(row.to_dict())
    cf_results[row["item_key"]] = (price, stale, method)

    log_rows.append({
        "name":        name,
        "method":      method,
        "cf_price":    price,
        "stale":       stale,
        "steam_price": 0.0,
    })
    _render_log()
    time.sleep(0.35)

# ── Phase 2: Steam ────────────────────────────────────────────────────────────
unique_names = inv_todo["item_name"].unique()

for idx, name in enumerate(unique_names):
    count    = idx + 1                            # ← 1-based display counter
    base_pct = total / (total + n_names)
    pct      = base_pct + idx / (n_names + total)
    progress_bar.progress(min(pct, 0.98), text=f"🌐 Steam ({count}/{total}): {name}")
    current_item_display.markdown(f"### 🌐 Steam ({count}/{total})\n`{name}`")

    sp = processor.fetch_steam_price(name)
    steam_prices[name] = sp

    # Update steam prices and collect indices of rows to move to the front
    indices_to_move = []
    for i, r in enumerate(log_rows):
        if r["name"] == name:
            r["steam_price"] = sp
            indices_to_move.append(i)
    
    # Move updated rows to the end (will appear at top when reversed in _render_log)
    for i in sorted(indices_to_move, reverse=True):
        row = log_rows.pop(i)
        log_rows.append(row)
    
    _render_log()
    time.sleep(1.5)

# ── Save ──────────────────────────────────────────────────────────────────────
progress_bar.progress(0.99, text="💾 Saving…")
current_item_display.markdown("### 💾 Saving snapshots…")

import database as db
from datetime import datetime

ts = datetime.now().strftime("%Y-%m-%d %H:%M")

for _, row in inv_todo.iterrows():
    key                   = row["item_key"]
    cf_price, stale, _mth = cf_results.get(key, (0.0, False, "no_price"))
    st_price              = steam_prices.get(row["item_name"], 0.0)
    if cf_price > 0 or st_price > 0:
        db.save_price_snapshot(key, cf_price, st_price, ts, stale=stale)

portfolio = processor.build_portfolio_from_db()
db.save_portfolio_snapshot(
    cf_value=portfolio["cf_value"].sum(),
    steam_value=portfolio["steam_value"].sum(),
    total_cost=portfolio["total_cost"].sum(),
    timestamp=ts,
)
db.meta_set("last_price_sync", datetime.now().strftime("%Y-%m-%d %H:%M"))
db.compress_old_price_history()
db.compress_old_portfolio_snapshots()
st.cache_data.clear()

# ── Done ──────────────────────────────────────────────────────────────────────
progress_bar.progress(1.0, text="✅ Done!")
skipped = len(inv) - len(inv_todo)
current_item_display.markdown(
    f"### ✅ Sync complete\n"
    f"Fetched **{len(inv_todo)}** items · Skipped **{skipped}** (already priced today)"
)

# Summary stats
ok      = sum(1 for r in log_rows if r["cf_price"] > 0 and not r["stale"])
stale_n = sum(1 for r in log_rows if r["stale"])
miss_n  = sum(1 for r in log_rows if r["cf_price"] == 0)
s1, s2, s3 = st.columns(3)
s1.metric("✅ Priced", ok)
s2.metric("⚠️ Stale",  stale_n)
s3.metric("🔴 Missing", miss_n)