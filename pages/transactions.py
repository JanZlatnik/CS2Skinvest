import streamlit as st
import pandas as pd
import requests
import os
from datetime import date as date_type
import processor
import database

MANUAL_CSV = "data/manual_ledger.csv"
CAT_MAP    = {"Normal": 1, "StatTrak™": 2, "Souvenir": 3}
WEAR_OPTS  = ["", "Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]

ITEM_TYPE_OPTS = [
    "Skin", "Container", "Sticker", "Agent",
    "Charm", "Patch", "Collectible", "Music Kit", "Unknown"
]

# Supported input currencies (display label → ISO code)
CURRENCIES = {
    "USD ($)": "USD",
    "EUR (€)": "EUR",
    "CZK (Kč)": "CZK",
    "GBP (£)": "GBP",
    "PLN (zł)": "PLN",
    "HUF (Ft)": "HUF",
    "CHF (Fr)": "CHF",
    "CAD (C$)": "CAD",
    "AUD (A$)": "AUD",
}

# ── Frankfurter exchange rate API (ECB data, no key needed) ───────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_rate_on_date(from_currency: str, to_currency: str = "USD",
                     on_date: str | None = None) -> float | None:
    """
    Return exchange rate from_currency → to_currency on a given date.
    Uses Frankfurter API (ECB data). Cached per hour.
    Returns None on failure (caller should fall back to raw price).
    """
    if from_currency == to_currency:
        return 1.0
    try:
        # Use latest if date is today or future, otherwise historical
        endpoint = "latest" if on_date is None else on_date
        r = requests.get(
            f"https://api.frankfurter.dev/v2/{endpoint}",
            params={"base": from_currency, "quotes": to_currency},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            rates = data.get("rates", {})
            return float(rates.get(to_currency, 0)) or None
    except Exception:
        pass
    return None


def convert_to_usd(amount: float, currency: str, on_date: str | None = None) -> float:
    """Convert amount in currency to USD using ECB rate for the given date."""
    if currency == "USD":
        return amount
    rate = get_rate_on_date(currency, "USD", on_date)
    if rate:
        return round(amount * rate, 2)
    # Fallback: return unconverted with warning
    st.warning(f"Could not fetch {currency}/USD rate for {on_date or 'today'} — price saved as-is.")
    return amount


# ── Steam Market search ───────────────────────────────────────────────────────

def steam_search(query: str) -> list[str]:
    try:
        r = requests.get(
            "https://steamcommunity.com/market/search/render/",
            params={"query": query, "appid": 730, "norender": 1,
                    "count": 10, "search_descriptions": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if r.status_code == 200:
            return [item["name"] for item in r.json().get("results", []) if "name" in item]
    except Exception:
        pass
    return []


# ── Auto-fill helpers ─────────────────────────────────────────────────────────

def infer_wear(name: str) -> str:
    for w in ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]:
        if f"({w})" in name:
            return w
    return ""


def infer_type(name: str) -> str:
    n = name.lower()
    if n.startswith("sticker |"):   return "Sticker"
    if n.startswith("patch |"):     return "Patch"
    if n.startswith("charm |"):     return "Charm"
    if any(x in n for x in ["case", "capsule", "crate", "package"]): return "Container"
    if "music kit |" in n or n.startswith("music kit"): return "Music Kit"
    if any(x in n for x in ["| pin", "coin |", "trophy |"]): return "Collectible"
    return "Skin"


def infer_category(name: str) -> str:
    if "StatTrak™" in name: return "StatTrak™"
    if "Souvenir"  in name: return "Souvenir"
    return "Normal"


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state():
    defaults = {
        "tx_search_results": [],
        "tx_item_name":      "",
        "tx_item_type":      "Skin",
        "tx_wear":           "",
        "tx_category":       "Normal",
        "tx_currency":       "USD ($)",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

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

# ── Page ──────────────────────────────────────────────────────────────────────

st.title("✏️ Transactions")

tab_manual, tab_bulk = st.tabs(["➕ Add Single", "📥 Bulk Import"])

# ══════════════════════════════════════════════════════════════════════════════
with tab_manual:
    st.subheader("Add transaction")

    # ── Currency selector (page-level, persists across entries) ──────────────
    curr_col, _ = st.columns([2, 4])
    with curr_col:
        selected_currency_label = st.selectbox(
            "Input currency",
            list(CURRENCIES.keys()),
            index=list(CURRENCIES.keys()).index(st.session_state.tx_currency),
            help="All prices will be converted to USD at the exchange rate on the transaction date.",
            key="currency_selector",
        )
        st.session_state.tx_currency = selected_currency_label
        input_currency = CURRENCIES[selected_currency_label]

    if input_currency != "USD":
        today_rate = get_rate_on_date(input_currency, "USD")
        if today_rate:
            st.caption(f"Today's rate: 1 {input_currency} = ${today_rate:.4f} USD")

    st.divider()

    # ── Step 1: Search ────────────────────────────────────────────────────────
    st.markdown("##### 🔍 Step 1 — Find the item")

    search_col, btn_col = st.columns([5, 1])
    with search_col:
        query = st.text_input(
            "Search Steam Market",
            placeholder="e.g.  smoking kills,  karambit fade,  operation bravo case…",
            label_visibility="collapsed",
        )
    with btn_col:
        if st.button("Search", use_container_width=True):
            if query.strip():
                with st.spinner("Searching Steam Market…"):
                    st.session_state.tx_search_results = steam_search(query.strip())

    if st.session_state.tx_search_results:
        for result_name in st.session_state.tx_search_results:
            col_name, col_btn = st.columns([6, 1])
            with col_name:
                st.markdown(f"`{result_name}`")
            with col_btn:
                if st.button("Select", key=f"sel_{result_name}"):
                    st.session_state.tx_item_name      = result_name
                    st.session_state.tx_item_type      = infer_type(result_name)
                    st.session_state.tx_wear           = infer_wear(result_name)
                    st.session_state.tx_category       = infer_category(result_name)
                    st.session_state.tx_search_results = []
                    st.rerun()

    if st.session_state.tx_item_name:
        c_badge, c_clear = st.columns([6, 1])
        with c_badge:
            st.success(f"✅ **{st.session_state.tx_item_name}**")
        with c_clear:
            if st.button("Clear", key="clear_item"):
                st.session_state.tx_item_name  = ""
                st.session_state.tx_item_type  = "Skin"
                st.session_state.tx_wear       = ""
                st.session_state.tx_category   = "Normal"
                st.rerun()

    st.divider()

    # ── Step 2: Form ──────────────────────────────────────────────────────────
    st.markdown("##### 📝 Step 2 — Fill in details")

    with st.form("manual_f", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)

        with c1:
            tx_date = st.date_input("Date")

            item_name_input = st.text_input(
                "Market Hash Name",
                value=st.session_state.tx_item_name,
                placeholder="AK-47 | Redline (Field-Tested)",
                help="Auto-filled by search above, or type manually",
            )

            type_index = ITEM_TYPE_OPTS.index(st.session_state.tx_item_type) \
                         if st.session_state.tx_item_type in ITEM_TYPE_OPTS else 0
            itype = st.selectbox("Type", ITEM_TYPE_OPTS, index=type_index)

        with c2:
            cat_index = list(CAT_MAP.keys()).index(st.session_state.tx_category) \
                        if st.session_state.tx_category in CAT_MAP else 0
            cat = st.selectbox("Category", list(CAT_MAP.keys()), index=cat_index)

            wear_index = WEAR_OPTS.index(st.session_state.tx_wear) \
                         if st.session_state.tx_wear in WEAR_OPTS else 0
            wear = st.selectbox("Wear", WEAR_OPTS, index=wear_index,
                                help="Auto-filled for Skins; ignored for other types")

            action = st.selectbox("Action", ["Buy", "Sell"])

        with c3:
            qty = st.number_input("Quantity", min_value=1, value=1)

            currency_symbol = selected_currency_label.split("(")[1].rstrip(")")
            price_input = st.number_input(
                f"Price ({currency_symbol})",
                min_value=0.0, format="%.2f",
                help=f"Enter price in {input_currency}. Will be converted to USD at the rate on the transaction date.",
            )

            if itype == "Skin":
                float_str = st.text_input(
                    "Float",
                    value="",
                    placeholder="0.14500388503074646",
                    help="Full precision float value (leave empty if unknown)",
                )
            else:
                float_str = ""
                st.text_input("Float", value="N/A", disabled=True,
                              help="Float only applies to Skins")

            if itype in ("Skin", "Charm"):
                seed_label = "Pattern (paint seed)" if itype == "Skin" else "Keychain pattern"
                paint_seed = st.number_input(seed_label, min_value=0,
                                              max_value=100000, value=0,
                                              help="0 = unknown / not applicable")
            else:
                paint_seed = 0
                st.number_input("Pattern", value=0, disabled=True,
                                help="Pattern only applies to Skins and Charms")

        submitted = st.form_submit_button("💾 Save transaction",
                                          use_container_width=True, type="primary")

    # ── Save ──────────────────────────────────────────────────────────────────
    if submitted:
        final_name = item_name_input.strip()
        if not final_name:
            st.error("Item name cannot be empty. Use the search above or type it manually.")
        else:
            if itype == "Skin" and wear and f"({wear})" not in final_name:
                final_name = f"{final_name} ({wear})"

            # Parse float
            float_val = None
            if itype == "Skin" and float_str.strip():
                try:
                    float_val = float(float_str.strip())
                    if not (0.0 <= float_val <= 1.0):
                        st.error("Float must be between 0.0 and 1.0.")
                        st.stop()
                except ValueError:
                    st.error(f"Invalid float value: '{float_str}'")
                    st.stop()

            # Convert price to USD
            date_str  = str(tx_date)
            price_usd = convert_to_usd(price_input, input_currency, date_str)

            # Show conversion info if not USD
            if input_currency != "USD" and price_input > 0:
                rate = get_rate_on_date(input_currency, "USD", date_str)
                if rate:
                    st.info(
                        f"💱 {price_input:.2f} {input_currency} "
                        f"× {rate:.4f} = **${price_usd:.2f} USD** "
                        f"(ECB rate on {date_str})"
                    )

            qty_signed = qty if action == "Buy" else -qty
            new_row = {
                "Date":       date_str,
                "Item_Name":  final_name,
                "Item_Type":  itype,
                "Category":   CAT_MAP[cat],
                "Float":      float_val,
                "Paint_Seed": paint_seed if itype in ("Skin", "Charm") and paint_seed > 0 else None,
                "Action":     action,
                "Quantity":   qty_signed,
                "Price_USD":  price_usd,
            }

            if os.path.exists(MANUAL_CSV):
                existing = pd.read_csv(MANUAL_CSV)
                updated  = pd.concat([existing, pd.DataFrame([new_row])], ignore_index=True)
            else:
                updated = pd.DataFrame([new_row])

            os.makedirs("data", exist_ok=True)
            updated.to_csv(MANUAL_CSV, index=False)
            st.cache_data.clear()

            st.session_state.tx_item_name      = ""
            st.session_state.tx_item_type      = "Skin"
            st.session_state.tx_wear           = ""
            st.session_state.tx_category       = "Normal"
            st.session_state.tx_search_results = []

            st.success(f"✅ Saved: **{final_name}** × {qty} @ ${price_usd:.2f} USD")
            st.info("Run **📦 Sync Inventory** to update the portfolio.", icon="ℹ️")
            st.rerun()

    with st.expander("💡 How it works"):
        st.markdown("""
**Search** queries the Steam Market API and returns exact item names including wear,
knife star prefix (★), and StatTrak™. Selecting a result auto-fills Name, Type and Wear.

Examples of what gets filled in:
- `smoking kills` → `MP7 | Smoking Kills (Minimal Wear)` · Type: Skin · Wear: Minimal Wear
- `karambit fade fn` → `★ Karambit | Fade (Factory New)` · Type: Skin
- `operation bravo` → `Operation Bravo Case` · Type: Container
- `charm semi` → `Charm | Semi-Precious` · Type: Charm

**Currency conversion** — select your input currency at the top of the page.
The price is converted to USD using the ECB exchange rate for the transaction date
(sourced from [Frankfurter API](https://frankfurter.dev), no API key required).

**Float** — enter full precision from CSFloat, e.g. `0.14500388503074646` (Skin only).
**Pattern** — paint seed (0–1000) for Skin, keychain pattern for Charm (visible on CSFloat item page).
        """)

# ══════════════════════════════════════════════════════════════════════════════
with tab_bulk:
    st.subheader("Bulk import via CSV")
    st.markdown("""
All prices in the CSV must be in **USD**. For non-USD purchases, convert manually before import.

| Column | Example | Notes |
|---|---|---|
| `Date` | `2024-03-15` | required |
| `Item_Name` | `AK-47 \| Redline (Field-Tested)` | required, exact Steam market hash name |
| `Action` | `Buy` | required — Buy or Sell |
| `Quantity` | `1` | required, negative = sell |
| `Price_USD` | `14.50` | required, in USD |
| `Item_Type` | `Skin` | optional — Skin / Container / Sticker / Agent / Charm / Patch / Collectible / Music Kit |
| `Category` | `1` | optional — 1=Normal / 2=StatTrak™ / 3=Souvenir |
| `Float` | `0.14500388503074646` | optional, full precision, Skin only |
| `Paint_Seed` | `664` | optional — paint seed for Skin, keychain pattern for Charm |
    """)

    template = pd.DataFrame(columns=[
        "Date", "Item_Name", "Item_Type", "Category",
        "Float", "Paint_Seed", "Action", "Quantity", "Price_USD"
    ])
    st.download_button(
        "⬇️ Download template",
        template.to_csv(index=False).encode(),
        file_name="import_template.csv",
        mime="text/csv",
    )

    uploaded = st.file_uploader("Upload filled CSV", type="csv")
    if uploaded:
        try:
            df_up    = pd.read_csv(uploaded)
            required = {"Date", "Item_Name", "Action", "Quantity", "Price_USD"}
            missing  = required - set(df_up.columns)
            if missing:
                st.error(f"Missing columns: {', '.join(missing)}")
            else:
                for col, default in [("Item_Type", "Unknown"), ("Category", 1),
                                     ("Float", None), ("Paint_Seed", None)]:
                    if col not in df_up.columns:
                        df_up[col] = default

                st.dataframe(df_up, use_container_width=True, hide_index=True)
                if st.button("✅ Confirm import", type="primary"):
                    if os.path.exists(MANUAL_CSV):
                        existing = pd.read_csv(MANUAL_CSV)
                        updated  = pd.concat([existing, df_up], ignore_index=True)
                    else:
                        updated = df_up
                    os.makedirs("data", exist_ok=True)
                    updated.to_csv(MANUAL_CSV, index=False)
                    st.cache_data.clear()
                    st.success(
                        f"Imported {len(df_up)} rows. "
                        "Run **📦 Sync Inventory** to update portfolio."
                    )
        except Exception as e:
            st.error(f"Could not parse file: {e}")