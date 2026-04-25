import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import src.processor as processor, src.database as database

st.title("📊 Charts & Analytics")


@st.cache_data(ttl=120, show_spinner=False)
def load_portfolio():
    return processor.build_portfolio_from_db()

portfolio = load_portfolio()

tab1, tab2, tab3, tab4 = st.tabs([
    "💼 Portfolio Value",
    "📦 Item Price History",
    "💰 Profit / Loss",
    "🥧 Distribution",
])

CHART_HEIGHT = 520

# ── Tab 1 · Portfolio value over time ─────────────────────────────────────────
with tab1:
    ph = database.get_portfolio_history()
    if not ph.empty:
        ph["timestamp"] = pd.to_datetime(ph["timestamp"])
        has_steam_hist  = ph["steam_value"].gt(0).any()

        src2 = "CSFloat"
        if has_steam_hist:
            _, src_col = st.columns([4, 1])
            with src_col:
                src2 = st.radio("Source", ["CSFloat", "Steam"], horizontal=True, key="src_port")

        val_col = "cf_value" if src2 == "CSFloat" else "steam_value"
        color   = "#06d6a0"  if src2 == "CSFloat" else "#4c9be8"

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ph["timestamp"], y=ph[val_col],
            name=f"Value ({src2})", fill="tozeroy",
            line=dict(color=color, width=2),
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=ph["timestamp"], y=ph["total_cost"],
            name="Cost", line=dict(color="#f4a261", width=2, dash="dash"),
            hovertemplate="%{x|%Y-%m-%d}<br>Cost: $%{y:,.2f}<extra></extra>",
        ))
        fig.update_layout(
            title=f"Portfolio value vs. cost  [{src2}]",
            xaxis_title="Date", yaxis_title="USD",
            hovermode="x unified",
            height=CHART_HEIGHT,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, width='stretch')
        if not has_steam_hist:
            st.caption("Steam values will appear here after the first Sync Prices.")
    else:
        st.info("No snapshots yet. Run **Sync Prices** to start tracking.")

# ── Tab 2 · Item price history ────────────────────────────────────────────────
with tab2:
    if not portfolio.empty:
        def _label(row):
            if row["item_type"] == "Skin":
                parts = []
                if pd.notna(row["float_val"]):  parts.append(f"float {row['float_val']:.4f}")
                if pd.notna(row["paint_seed"]): parts.append(f"#{int(row['paint_seed'])}")
                return f"{row['item_name']}  ({', '.join(parts)})" if parts else row["item_name"]
            return row["item_name"]

        labels       = {row["item_key"]: _label(row) for _, row in portfolio.iterrows()}
        label_to_key = {v: k for k, v in labels.items()}

        col_sel, col_src = st.columns([3, 1])
        with col_sel:
            selected_label = st.selectbox("Select item", sorted(label_to_key.keys()))
        with col_src:
            src = st.radio("Source", ["CSFloat", "Steam"], horizontal=True)

        selected_key = label_to_key[selected_label]
        source_code  = "cf" if src == "CSFloat" else "steam"
        history      = database.get_price_history_for_item(selected_key, source_code)

        if not history.empty:
            history["timestamp"] = pd.to_datetime(history["timestamp"])
            avg_cost = portfolio.loc[portfolio["item_key"] == selected_key, "avg_cost"].iloc[0]

            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=history["timestamp"], y=history["price_usd"],
                mode="lines+markers",
                name=f"{src} floor",
                line=dict(color="#00b4d8" if src == "CSFloat" else "#4c9be8", width=2),
                marker=dict(size=5),
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:.2f}<extra></extra>",
            ))
            fig2.add_hline(
                y=avg_cost, line_dash="dash", line_color="#f4a261",
                annotation_text=f"Avg buy  ${avg_cost:.2f}",
                annotation_position="bottom right",
            )
            fig2.update_layout(
                title=selected_label,
                xaxis_title="Date", yaxis_title="Price (USD)",
                height=CHART_HEIGHT,
                hovermode="x unified",
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig2, width='stretch')
        else:
            tip = "Run **Sync Prices**." if src == "CSFloat" else "No Steam data yet — run **Sync Prices**."
            st.info(f"No {src} price history yet. {tip}")
    else:
        st.info("No inventory data. Sync Inventory first.")

# ── Tab 3 · Profit / Loss ─────────────────────────────────────────────────────
with tab3:
    if not portfolio.empty:
        pl = portfolio[["item_name", "cf_pnl", "float_val", "paint_seed"]].copy()

        def _pl_label(row):
            if pd.notna(row["float_val"]):
                return f"{row['item_name']} ({row['float_val']:.4f})"
            return row["item_name"]

        pl["label"] = pl.apply(_pl_label, axis=1)
        pl = pl.sort_values("cf_pnl")
        pl["color"] = pl["cf_pnl"].apply(lambda x: "#ef476f" if x < 0 else "#06d6a0")

        fig3 = go.Figure(go.Bar(
            x=pl["cf_pnl"], y=pl["label"], orientation="h",
            marker_color=pl["color"],
            hovertemplate="<b>%{y}</b><br>P&L: $%{x:,.2f}<extra></extra>",
        ))
        fig3.update_layout(
            title="Unrealized P&L per item  (CSFloat prices)",
            xaxis_title="P&L (USD)", yaxis_title=None,
            height=max(CHART_HEIGHT, len(pl) * 38),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig3, width='stretch')
    else:
        st.info("No portfolio data.")

# ── Tab 4 · Portfolio distribution ───────────────────────────────────────────
with tab4:
    if not portfolio.empty:
        grp_df = (
            portfolio.groupby("item_type", dropna=False)["cf_value"]
            .sum()
            .reset_index()
            .rename(columns={"item_type": "Type", "cf_value": "Value"})
        )
        grp_df = grp_df[grp_df["Value"] > 0].copy()
        grp_df["Type"] = grp_df["Type"].fillna("Unknown")

        total_val = grp_df["Value"].sum()

        left, right = st.columns([3, 2])
        with left:
            fig4 = px.pie(
                grp_df, values="Value", names="Type",
                title="Portfolio value by item type",
                hole=0.42,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig4.update_traces(
                textposition="inside",
                textinfo="percent+label",
                hovertemplate="<b>%{label}</b><br>$%{value:,.2f}<br>%{percent}<extra></extra>",
            )
            fig4.update_layout(
                showlegend=True,
                legend=dict(orientation="v", x=1.02, y=0.5),
                margin=dict(t=40, b=10, l=10, r=10),
                height=440,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig4, width='stretch')

        with right:
            st.markdown("**Breakdown**")
            tbl = grp_df.sort_values("Value", ascending=False).copy()
            tbl["Share"] = (tbl["Value"] / total_val * 100).map("{:.1f}%".format)
            tbl["Value"] = tbl["Value"].map("${:,.2f}".format)
            st.dataframe(tbl[["Type", "Value", "Share"]],
                         hide_index=True, width='stretch')
    else:
        st.info("No portfolio data. Run **Sync Prices** first.")