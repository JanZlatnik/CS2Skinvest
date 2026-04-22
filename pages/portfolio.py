import streamlit as st
import pandas as pd
import requests
import processor, database
from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv("CSFLOAT_API_KEY")

CAT_MAP = {1: "Normal", 2: "StatTrak™", 3: "Souvenir"}

COL_CONFIG = {
    "item_name":        st.column_config.TextColumn("Item"),
    "item_type":        st.column_config.TextColumn("Type"),
    "wear":             st.column_config.TextColumn("Wear"),
    "category":         st.column_config.TextColumn("Category"),
    "float_val":        st.column_config.NumberColumn("Float",   format="%.4f"),
    "paint_seed":       st.column_config.NumberColumn("Pattern", format="%d"),
    "quantity":         st.column_config.NumberColumn("Qty",     format="%d"),
    "avg_cost":         st.column_config.NumberColumn("Avg Buy", format="$%.2f"),
    "cf_price_display": st.column_config.TextColumn("CSFloat",
                            help="⚠️ = carried from last known price   🔴 = never fetched"),
    "steam_price":      st.column_config.NumberColumn("Steam",   format="$%.2f"),
    "total_cost":       st.column_config.NumberColumn("Cost",    format="$%.2f"),
    "cf_value":         st.column_config.NumberColumn("Value",   format="$%.2f"),
    "cf_pnl":           st.column_config.NumberColumn("P&L",     format="$%.2f"),
}

DISPLAY_COLS = [
    "item_name", "item_type", "wear", "category",
    "float_val", "paint_seed", "quantity", "avg_cost",
    "cf_price_display", "steam_price", "total_cost", "cf_value", "cf_pnl",
]


def _make_cf_display(row) -> str:
    price = row["cf_price"]
    stale = row.get("cf_stale", False)
    if price == 0:
        return "🔴 N/A"
    if stale:
        return f"⚠️ ${price:,.2f}"
    return f"${price:,.2f}"


# ── User info (still used for main-area title row) ────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_user_info():
    if not API_KEY:
        return None
    try:
        r = requests.get(
            "https://csfloat.com/api/v1/me",
            headers={"Authorization": API_KEY},
            timeout=6,
        )
        if r.status_code == 200:
            return r.json().get("user", {})
    except Exception:
        pass
    return None


# ── Sidebar – Controls only (user info + nav are rendered by app.py) ──────────
with st.sidebar:
    st.header("⚙️ Controls")

    if st.button("📦 Sync Inventory", use_container_width=True,
                 help="Fetch new trades from CSFloat, rebuild inventory"):
        with st.spinner("Fetching trades & rebuilding inventory…"):
            n = processor.sync_inventory()
            st.cache_data.clear()
            st.success(f"Inventory updated — {n} active items")
            st.rerun()

    if st.button("💰 Sync Prices", use_container_width=True,
                 help="Fetch latest prices from CSFloat & Steam"):
        st.switch_page("pages/sync_page.py")

    st.divider()
    inv_sync   = database.meta_get("last_inventory_sync")
    price_sync = database.meta_get("last_price_sync")
    st.caption(f"Inventory: **{inv_sync or 'never'}**")
    st.caption(f"Prices: **{price_sync or 'never'}**")


# ── Load ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner=False)
def load_portfolio():
    return processor.build_portfolio_from_db()

portfolio = load_portfolio()

# ── Title row with user info ──────────────────────────────────────────────────
title_col, user_col = st.columns([3, 1])
with title_col:
    st.title("💼 Portfolio")
with user_col:
    user = fetch_user_info()
    if user:
        username = user.get("username", "—")
        steam_id = user.get("steam_id", "")
        st.markdown(
            f"<div style='text-align:right; padding-top:16px'>"
            f"<span style='font-size:1rem; font-weight:600'>{username}</span><br>"
            f"<span style='font-size:0.72rem; color:gray'>{steam_id}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

if not portfolio.empty:
    cf_total    = portfolio["cf_value"].sum()
    steam_total = portfolio["steam_value"].sum()
    cost_total  = portfolio["total_cost"].sum()
    cf_pnl      = portfolio["cf_pnl"].sum()
    pnl_pct     = (cf_pnl / cost_total * 100) if cost_total else 0
    has_steam   = portfolio["steam_price"].gt(0).any()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Value (CSFloat)",  f"${cf_total:,.2f}")
    c2.metric("Total Cost",       f"${cost_total:,.2f}")
    c3.metric("Unrealized P&L",   f"${cf_pnl:,.2f}", delta=f"{cf_pnl:+,.2f}")
    c4.metric("Return",           f"{pnl_pct:.1f}%", delta=f"{pnl_pct:+.1f}%")

    if has_steam:
        st.caption(f"Steam estimated value: **${steam_total:,.2f}**")

    st.divider()

    # ── Filters ───────────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns(3)
    with f1:
        type_opts = ["All"] + sorted(portfolio["item_type"].dropna().unique().tolist())
        sel_type  = st.selectbox("Type", type_opts)
    with f2:
        wear_vals = portfolio["wear"].fillna("").unique()
        wear_opts = ["All"] + [w for w in processor.WEAR_ORDER if w in wear_vals]
        sel_wear  = st.selectbox("Wear", wear_opts)
    with f3:
        sel_cat = st.selectbox("Category", ["All", "Normal", "StatTrak™", "Souvenir"])

    display = portfolio.copy()
    display["category"]         = display["category"].map(CAT_MAP)
    display["cf_price_display"] = display.apply(_make_cf_display, axis=1)
    if sel_type != "All": display = display[display["item_type"] == sel_type]
    if sel_wear != "All": display = display[display["wear"]      == sel_wear]
    if sel_cat  != "All": display = display[display["category"]  == sel_cat]

    cols = [c for c in DISPLAY_COLS if c in display.columns
            and (c != "steam_price" or has_steam)]

    st.dataframe(display[cols], column_config=COL_CONFIG,
                 use_container_width=True, hide_index=True, height=600)

    # Price quality legend
    stale_count   = int(portfolio.get("cf_stale", pd.Series(dtype=bool)).sum())
    missing_count = int((portfolio["cf_price"] == 0).sum())
    parts = []
    if stale_count:   parts.append(f"⚠️ {stale_count} item(s) using last known price")
    if missing_count: parts.append(f"🔴 {missing_count} item(s) with no price data")
    if parts:
        st.caption("  ·  ".join(parts))

    st.caption(f"Showing {len(display)} of {len(portfolio)} items")

else:
    st.info("No inventory data. Click **📦 Sync Inventory** to start.")