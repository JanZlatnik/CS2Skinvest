import streamlit as st
import pandas as pd
import processor
import database

CAT_MAP = {1: "Normal", 2: "StatTrak™", 3: "Souvenir"}

COL_CONFIG = {
    "item_name":  st.column_config.TextColumn("Item"),
    "item_type":  st.column_config.TextColumn("Type"),
    "wear":       st.column_config.TextColumn("Wear"),
    "category":   st.column_config.TextColumn("Category"),
    "float_val":  st.column_config.NumberColumn("Float",   format="%.4f"),
    "paint_seed": st.column_config.NumberColumn("Pattern", format="%d"),
    "quantity":   st.column_config.NumberColumn("Qty",     format="%d"),
    "avg_cost":   st.column_config.NumberColumn("Avg Buy", format="$%.2f"),
    "cf_price":   st.column_config.NumberColumn("CSFloat", format="$%.2f",
                      help="Sortable CSFloat floor price in USD"),
    "csfs":       st.column_config.TextColumn("CSFS",
                      help="✅ Fresh  ·  ⚠️ Stale (last known)  ·  🔴 No price"),
    "steam_price":st.column_config.NumberColumn("Steam",   format="$%.2f"),
    "total_cost": st.column_config.NumberColumn("Cost",    format="$%.2f"),
    "cf_value":   st.column_config.NumberColumn("Value",   format="$%.2f"),
    "cf_pnl":     st.column_config.NumberColumn("P&L",     format="$%.2f"),
}

DISPLAY_COLS = [
    "item_name", "item_type", "wear", "category",
    "float_val", "paint_seed", "quantity", "avg_cost",
    "cf_price", "csfs", "steam_price", "total_cost", "cf_value", "cf_pnl",
]


def _csfs(row) -> str:
    """Icon-only status indicator — keeps cf_price numeric and sortable."""
    if row["cf_price"] == 0:
        return "🔴"
    if row.get("cf_stale", False):
        return "⚠️"
    return "✅"


@st.cache_data(ttl=120, show_spinner=False)
def load_portfolio():
    return processor.build_portfolio_from_db()


st.title("💼 Portfolio")

portfolio = load_portfolio()

if portfolio.empty:
    st.info("No inventory data. Click **📦 Sync Inventory** in the sidebar to start.")
    st.stop()

# ── Summary metrics with yesterday delta ─────────────────────────────────────
cf_total    = portfolio["cf_value"].sum()
steam_total = portfolio["steam_value"].sum()
cost_total  = portfolio["total_cost"].sum()
cf_pnl      = portfolio["cf_pnl"].sum()
pnl_pct     = (cf_pnl / cost_total * 100) if cost_total else 0.0
has_steam   = portfolio["steam_price"].gt(0).any()

yest_cf_val, yest_cost = database.get_yesterday_portfolio_value()
if yest_cf_val > 0:
    yest_pnl     = yest_cf_val - yest_cost
    delta_pnl    = cf_pnl - yest_pnl
    yest_pct     = (yest_pnl / yest_cost * 100) if yest_cost else 0.0
    delta_pct    = pnl_pct - yest_pct
    delta_pnl_str = f"{delta_pnl:+,.2f}"
    delta_pct_str = f"{delta_pct:+.1f}%"
else:
    delta_pnl_str = None
    delta_pct_str = None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Value (CSFloat)", f"${cf_total:,.2f}")
c2.metric("Total Cost",      f"${cost_total:,.2f}")
c3.metric("Unrealized P&L",  f"${cf_pnl:,.2f}",  delta=delta_pnl_str)
c4.metric("Return",          f"{pnl_pct:.1f}%",   delta=delta_pct_str)

if has_steam:
    st.caption(f"Steam estimated value: **${steam_total:,.2f}**")

st.divider()

# ── Holdings table ───────────────────────────────────────────────────────────
if True:  # keep indentation level consistent
    # ── Search bar (full width, above dropdowns) ──────────────────────────────
    search = st.text_input("🔍 Search item name", placeholder="AK-47, Karambit, Fade…",
                           key="pf_search")

    # ── Filters ───────────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns(3)
    with f1:
        type_opts = ["All"] + sorted(portfolio["item_type"].dropna().unique().tolist())
        sel_type  = st.selectbox("Type", type_opts, key="pf_type")
    with f2:
        wear_vals = portfolio["wear"].fillna("").unique()
        wear_opts = ["All"] + [w for w in processor.WEAR_ORDER if w in wear_vals]
        sel_wear  = st.selectbox("Wear", wear_opts, key="pf_wear")
    with f3:
        sel_cat = st.selectbox("Category", ["All", "Normal", "StatTrak™", "Souvenir"],
                               key="pf_cat")

    display = portfolio.copy()
    display["category"] = display["category"].map(CAT_MAP)
    display["csfs"]     = display.apply(_csfs, axis=1)

    if sel_type != "All":
        display = display[display["item_type"] == sel_type]
    if sel_wear != "All":
        display = display[display["wear"] == sel_wear]
    if sel_cat != "All":
        display = display[display["category"] == sel_cat]
    if search.strip():
        display = display[display["item_name"].str.contains(
            search.strip(), case=False, na=False)]

    cols = [c for c in DISPLAY_COLS if c in display.columns
            and (c != "steam_price" or has_steam)]

    st.dataframe(display[cols], column_config=COL_CONFIG,
                 use_container_width=True, hide_index=True, height=580)

    # Legend
    stale_n   = int(portfolio.get("cf_stale", pd.Series(dtype=bool)).sum())
    missing_n = int((portfolio["cf_price"] == 0).sum())
    parts = []
    if stale_n:   parts.append(f"⚠️ {stale_n} stale")
    if missing_n: parts.append(f"🔴 {missing_n} missing")
    legend = "  ·  ".join(parts) + "  ·  " if parts else ""
    st.caption(f"{legend}Showing {len(display)} of {len(portfolio)} items")