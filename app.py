import streamlit as st
import requests
from dotenv import load_dotenv
import os
from PIL import Image

load_dotenv()
API_KEY = os.getenv("CSFLOAT_API_KEY")

img_icon = Image.open("assets/icon.png")
st.set_page_config(page_title="CS2 SkInvest", layout="wide", page_icon=img_icon)

# ── Hide the auto-generated sidebar nav so we can build our own in the right order ──
st.markdown("""
<style>
[data-testid="stSidebarNav"] { display: none !important; }
</style>
""", unsafe_allow_html=True)


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


with st.sidebar:
    # ── 1. User info (top of sidebar) ─────────────────────────────────────────
    user = fetch_user_info()
    if user:
        username = user.get("username", "—")
        steam_id = user.get("steam_id", "")
        # CSFloat may return the avatar URL under several possible field names
        avatar_url = (
            user.get("avatar_url") or
            user.get("avatar")     or
            user.get("avatarUrl")  or
            user.get("avatarfull") or
            user.get("avatarmedium") or ""
        )

        info_col, img_col = st.columns([3, 1])
        with info_col:
            st.markdown(
                f"<div style='padding-top:6px'>"
                f"<span style='font-size:1rem; font-weight:600'>{username}</span><br>"
                f"<span style='font-size:0.72rem; color:gray'>{steam_id}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with img_col:
            if avatar_url:
                st.markdown(
                    f"<div style='text-align:right; padding-top:4px'>"
                    f"<img src='{avatar_url}' "
                    f"style='width:46px; height:46px; border:2px solid white; "
                    f"border-radius:4px; object-fit:cover;' />"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── 2. Navigation links ───────────────────────────────────────────────────
    st.page_link("pages/portfolio.py",    label="💼  Portfolio",    use_container_width=True)
    st.page_link("pages/charts.py",       label="📊  Charts",       use_container_width=True)
    st.page_link("pages/transactions.py", label="✏️  Transactions", use_container_width=True)

    st.divider()
    # ── 3. Controls section is added by each page below this point ────────────


# ── All pages must be registered here for routing to work ────────────────────
portfolio_page    = st.Page("pages/portfolio.py",    title="Portfolio",    icon="💼")
charts_page       = st.Page("pages/charts.py",       title="Charts",       icon="📊")
transactions_page = st.Page("pages/transactions.py", title="Transactions", icon="✏️")
sync_page         = st.Page("pages/sync_page.py",    title="Sync Prices",  icon="💰")

pg = st.navigation([portfolio_page, charts_page, transactions_page, sync_page])
pg.run()