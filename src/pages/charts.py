import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import processor
import database

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

CHART_HEIGHT = 800

# ── Tab 1 · Portfolio value over time ─────────────────────────────────────────
with tab1:
    ph = database.get_portfolio_history()
    if not ph.empty:
        ph["timestamp"] = pd.to_datetime(ph["timestamp"])
        has_steam_hist  = ph["steam_value"].gt(0).any()

        _, csfloat_col, steam_col = st.columns([14, 1, 1])
        with csfloat_col:
            show_cf    = st.toggle("CSFloat",  value=True,            key="port_cf")
        with steam_col:
            show_steam = st.toggle("Steam",    value=has_steam_hist,  key="port_steam",disabled=not has_steam_hist)

        fig = go.Figure()

        if show_cf:
            fig.add_trace(go.Scatter(
                x=ph["timestamp"], y=ph["cf_value"],
                name="Value (CSFloat)", fill="tonexty",
                line=dict(color="#06d6a0", width=4, shape="spline"),
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
            ))

        if show_steam and has_steam_hist:
            fig.add_trace(go.Scatter(
                x=ph["timestamp"], y=ph["steam_value"],
                name="Value (Steam)",
                line=dict(color="#4c9be8", width=4, shape="spline"),
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
            ))

        fig.add_trace(go.Scatter(
            x=ph["timestamp"], y=ph["total_cost"],
            name="Cost", line=dict(color="#f4a261", width=4, dash="dashdot", shape="spline"),
            hovertemplate="%{x|%Y-%m-%d}<br>Cost: $%{y:,.2f}<extra></extra>",
        ))

        fig.update_layout(
            title="Portfolio value vs. cost",
            xaxis_title="Date", yaxis_title="USD",
            hovermode="x unified",
            height=CHART_HEIGHT,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        fig.update_xaxes(
            rangeselector=dict(
                buttons=[
                    dict(count=7,  label="1W",  step="day",   stepmode="backward"),
                    dict(count=1,  label="1M",  step="month", stepmode="backward"),
                    dict(count=3,  label="3M",  step="month", stepmode="backward"),
                    dict(count=1,  label="YTD", step="year",  stepmode="todate"),
                    dict(count=1,  label="1Y",  step="year",  stepmode="backward"),
                    dict(step="all", label="All"),
                ]
            ),
            rangeslider=dict(visible=False),
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
        all_labels   = sorted(label_to_key.keys())

        col_sel, _, col_csfloat, col_steam = st.columns([13,1,1,1])

        with col_sel:
            search = st.text_input("🔍 Search item", placeholder="e.g. AWP, Karambit, Fade…", key="item_search")
            filtered_labels = [l for l in all_labels if search.lower() in l.lower()] if search else all_labels
            if filtered_labels:
                selected_label = st.selectbox("Select item", filtered_labels, key="item_select")
            else:
                st.caption("No items match your search.")
                selected_label = None

        with col_csfloat:
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            show_cf_item    = st.toggle("CSFloat", value=True,  key="item_cf")
        with col_steam:
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            st.write("")
            show_steam_item = st.toggle("Steam",   value=True,  key="item_steam")

        if selected_label is not None:
            selected_key = label_to_key[selected_label]
            avg_cost     = portfolio.loc[portfolio["item_key"] == selected_key, "avg_cost"].iloc[0]

            fig2 = go.Figure()

            if show_cf_item:
                hist_cf = database.get_price_history_for_item(selected_key, "cf")
                if not hist_cf.empty:
                    hist_cf["timestamp"] = pd.to_datetime(hist_cf["timestamp"])
                    fig2.add_trace(go.Scatter(
                        x=hist_cf["timestamp"], y=hist_cf["price_usd"],
                        mode="lines+markers",
                        name="CSFloat floor",
                        line=dict(color="#06d6a0", width=4, shape="spline"),
                        marker=dict(size=5),
                        hovertemplate="%{x|%Y-%m-%d}<br>$%{y:.2f}<extra></extra>",
                    ))
                else:
                    st.caption("No CSFloat price history yet. Run **Sync Prices**.")

            if show_steam_item:
                hist_steam = database.get_price_history_for_item(selected_key, "steam")
                if not hist_steam.empty:
                    hist_steam["timestamp"] = pd.to_datetime(hist_steam["timestamp"])
                    fig2.add_trace(go.Scatter(
                        x=hist_steam["timestamp"], y=hist_steam["price_usd"],
                        mode="lines+markers",
                        name="Steam floor",
                        line=dict(color="#4c9be8", width=4, shape="spline"),
                        marker=dict(size=5),
                        hovertemplate="%{x|%Y-%m-%d}<br>$%{y:.2f}<extra></extra>",
                    ))
                else:
                    st.caption("No Steam price history yet. Run **Sync Prices**.")

            fig2.add_hline(
                y=avg_cost, line_dash="dashdot",
                line=dict(color="#f4a261", width=4),
                annotation_text=f"Avg buy  ${avg_cost:.2f}",
                annotation_position="bottom right"
            )
            fig2.update_layout(
                title=selected_label,
                xaxis_title="Date", yaxis_title="Price (USD)",
                height=CHART_HEIGHT,
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            fig2.update_xaxes(
                rangeselector=dict(
                    buttons=[
                        dict(count=7,  label="1W",  step="day",   stepmode="backward"),
                        dict(count=1,  label="1M",  step="month", stepmode="backward"),
                        dict(count=3,  label="3M",  step="month", stepmode="backward"),
                        dict(count=1,  label="YTD", step="year",  stepmode="todate"),
                        dict(count=1,  label="1Y",  step="year",  stepmode="backward"),
                        dict(step="all", label="All"),
                    ]
                ),
                rangeslider=dict(visible=False),
            )
            if show_cf_item or show_steam_item:
                st.plotly_chart(fig2, width='stretch')
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

        type_options = ["All"] + sorted(portfolio["item_type"].dropna().unique().tolist())
        sel_type = st.selectbox("Filter by type", type_options, key="pl_type_filter")
        if sel_type != "All":
            pl = pl[portfolio["item_type"] == sel_type]

        row_height   = 38
        chart_height = max(CHART_HEIGHT, len(pl) * row_height + 80)

        fig3 = go.Figure(go.Bar(
            x=pl["cf_pnl"], y=pl["label"], orientation="h",
            marker_color=pl["color"],
            hovertemplate="<b>%{y}</b><br>P&L: $%{x:,.2f}<extra></extra>",
        ))
        fig3.update_layout(
            title="Unrealized P&L per item  (CSFloat prices)",
            xaxis_title="P&L (USD)", yaxis_title=None,
            height=chart_height,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(fixedrange=True),
            xaxis=dict(fixedrange=True),
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
                textfont=dict(size=15, family="Arial Black"),
                textfont_size=14,
            )
            fig4.update_layout(
                showlegend=True,
                legend=dict(orientation="v", x=0.0, y=0.0),
                margin=dict(t=40, b=10, l=10, r=10),
                height=CHART_HEIGHT,
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