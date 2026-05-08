"""
realized_pnl.py — Realized P&L page
────────────────────────────────────
Shows items that have been (fully or partially) sold.
Data is computed on-the-fly from the raw ledger (no extra DB table needed):
  csfloat_parsed.csv  — CSFloat trades (sells have Quantity = -1)
  manual_ledger.csv   — manually logged transactions

Summary bar at the top:   Total Invested | Total Revenue | Realized P&L | Return %
Table below:              one row per sold item with per-item breakdown
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import processor

CAT_MAP = {1: "Normal", 2: "StatTrak™", 3: "Souvenir"}

st.title("💸 Realized P&L")


@st.cache_data(ttl=120, show_spinner=False)
def load_realized() -> pd.DataFrame:
    return processor.get_realized_pnl_df()


df = load_realized()

if df.empty:
    st.info(
        "No sold items found yet.  \n"
        "Sells from CSFloat are imported automatically during **📦 Sync Inventory**.  \n"
        "You can also log a manual sale in **✏️ Transactions → Sell from Inventory**."
    )
    st.stop()

# ── Summary metrics ───────────────────────────────────────────────────────────
total_cost     = df["total_cost"].sum()
total_revenue  = df["total_revenue"].sum()
total_pnl      = df["realized_pnl"].sum()
total_return   = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
total_qty_sold = int(df["qty_sold"].sum())
n_items        = len(df)

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Total Invested",
    f"${total_cost:,.2f}",
    help="Sum of avg buy price × qty sold for all sold items",
)
c2.metric(
    "Total Revenue",
    f"${total_revenue:,.2f}",
    help="Sum of actual sell prices",
)
delta_sign = "+" if total_pnl >= 0 else ""
c3.metric(
    "Realized P&L",
    f"${total_pnl:,.2f}",
    delta=f"{delta_sign}{total_pnl:,.2f}",
)
c4.metric(
    "Return %",
    f"{total_return:.1f}%",
    delta=f"{delta_sign}{total_return:.1f}%",
)

st.caption(
    f"{n_items} distinct item{'s' if n_items != 1 else ''} sold  ·  "
    f"{total_qty_sold} total unit{'s' if total_qty_sold != 1 else ''}"
)

st.divider()

# ── Filters ───────────────────────────────────────────────────────────────────
search = st.text_input(
    "🔍 Search item name",
    placeholder="AK-47, Karambit, Fade…",
    key="rpl_search",
)

f1, f2, f3 = st.columns(3)
with f1:
    type_opts = ["All"] + sorted(df["item_type"].dropna().unique().tolist())
    sel_type  = st.selectbox("Type", type_opts, key="rpl_type")
with f2:
    wear_vals = df["wear"].fillna("").unique()
    wear_opts = ["All"] + [w for w in processor.WEAR_ORDER if w in wear_vals]
    sel_wear  = st.selectbox("Wear", wear_opts, key="rpl_wear")
with f3:
    sel_result = st.selectbox(
        "Result", ["All", "Profit ✅", "Loss 🔴"], key="rpl_result"
    )

display = df.copy()
if sel_type != "All":
    display = display[display["item_type"] == sel_type]
if sel_wear != "All":
    display = display[display["wear"] == sel_wear]
if sel_result == "Profit ✅":
    display = display[display["realized_pnl"] >= 0]
elif sel_result == "Loss 🔴":
    display = display[display["realized_pnl"] < 0]
if search.strip():
    display = display[
        display["item_name"].str.contains(search.strip(), case=False, na=False)
    ]

# ── Holdings table ────────────────────────────────────────────────────────────
SHOW_COLS = [
    "item_name", "item_type", "wear",
    "float_val", "paint_seed",
    "qty_sold", "avg_buy", "avg_sell",
    "total_cost", "total_revenue", "realized_pnl", "return_pct",
    "last_sell_date",
]
# Only include float/paint_seed columns if there are skins in the filtered set
has_skins = display["item_type"].isin(["Skin", "Knife"]).any()
if not has_skins:
    SHOW_COLS = [c for c in SHOW_COLS if c not in ("float_val", "paint_seed")]

col_config = {
    "item_name":      st.column_config.TextColumn("Item"),
    "item_type":      st.column_config.TextColumn("Type",       width="small"),
    "wear":           st.column_config.TextColumn("Wear",       width="small"),
    "float_val":      st.column_config.NumberColumn("Float",    format="%.4f"),
    "paint_seed":     st.column_config.NumberColumn("Pattern",  format="%d"),
    "qty_sold":       st.column_config.NumberColumn("Qty Sold", format="%d",   width="small"),
    "avg_buy":        st.column_config.NumberColumn("Avg Buy",  format="$%.2f"),
    "avg_sell":       st.column_config.NumberColumn("Avg Sell", format="$%.2f"),
    "total_cost":     st.column_config.NumberColumn("Cost",     format="$%.2f"),
    "total_revenue":  st.column_config.NumberColumn("Revenue",  format="$%.2f"),
    "realized_pnl":   st.column_config.NumberColumn("P&L",      format="$%.2f"),
    "return_pct":     st.column_config.NumberColumn("Return %", format="%.1f%%"),
    "last_sell_date": st.column_config.TextColumn("Last Sold",  width="small"),
}

show_df = display[[c for c in SHOW_COLS if c in display.columns]]

row_h  = 35
height = min(600, max(120, len(show_df) * row_h + 38))

st.dataframe(
    show_df,
    use_container_width=True,
    hide_index=True,
    height=height,
    column_config=col_config,
)
st.caption(f"Showing {len(display)} of {len(df)} sold items")

st.divider()

# ── Top/bottom items bar chart ─────────────────────────────────────────────────
if not display.empty:
    with st.expander("📊 P&L chart", expanded=True):
        pl_chart = display[["item_name", "realized_pnl"]].copy()
        pl_chart["color"] = pl_chart["realized_pnl"].apply(
            lambda x: "#ef476f" if x < 0 else "#06d6a0"
        )
        pl_chart = pl_chart.sort_values("realized_pnl")

        row_height   = 38
        chart_height = max(400, len(pl_chart) * row_height + 80)

        fig = go.Figure(go.Bar(
            x=pl_chart["realized_pnl"],
            y=pl_chart["item_name"],
            orientation="h",
            marker_color=pl_chart["color"],
            hovertemplate="<b>%{y}</b><br>Realized P&L: $%{x:,.2f}<extra></extra>",
        ))
        fig.update_layout(
            title="Realized P&L per item",
            xaxis_title="P&L (USD)", yaxis_title=None,
            height=chart_height,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(fixedrange=True),
            xaxis=dict(fixedrange=True),
        )
        st.plotly_chart(fig, width="stretch")
