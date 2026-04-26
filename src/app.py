import streamlit as st
import requests
from dotenv import load_dotenv
import os
from PIL import Image
from pathlib import Path
import database
import scheduler
import processor

# ── Paths --------------------------------------------------------------------
SRC_DIR  = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent

# ── Update system (optional) -------------------------------------------------
try:
    import updater as _updater
    _UPDATER_AVAILABLE = True
except ImportError:
    _UPDATER_AVAILABLE = False

load_dotenv()
API_KEY = os.getenv("CSFLOAT_API_KEY")

database.init_db()

img_icon = Image.open(ROOT_DIR / "assets" / "icon.png")
st.set_page_config(page_title="CS2 SkInvest", layout="wide", page_icon=img_icon)

st.markdown("""
<style>
[data-testid="stSidebarNav"] { display: none !important; }

.stButton > button[kind="primary"] {
    background-color: #0a7c6e !important;
    color: #ffffff !important;
    border: none !important;
}
.stButton > button[kind="primary"]:hover {
    background-color: #0d9e8e !important;
    color: #ffffff !important;
}
.stButton > button[kind="primary"]:active {
    background-color: #086358 !important;
}
.stFormSubmitButton > button {
    background-color: #0a7c6e !important;
    color: #ffffff !important;
    border: none !important;
}
.stFormSubmitButton > button:hover {
    background-color: #0d9e8e !important;
}
</style>
""", unsafe_allow_html=True)


# ── Page registration FIRST -- must happen before any st.page_link() call ----
# st.page_link() looks up pages in the navigation registry. If st.navigation()
# hasn't been called yet the registry is empty and a KeyError: 'url_pathname'
# is raised on reload / re-open. Defining pages and calling st.navigation()
# here ensures the registry is populated before the sidebar renders.
portfolio_page    = st.Page("pages/portfolio.py",    title="Portfolio",    icon="💼")
charts_page       = st.Page("pages/charts.py",       title="Charts",       icon="📊")
transactions_page = st.Page("pages/transactions.py", title="Transactions", icon="✏️")
sync_page         = st.Page("pages/sync_page.py",    title="Sync Prices",  icon="💰")
sync_history_page = st.Page("pages/sync_history.py", title="Sync History", icon="🕘")

pg = st.navigation(
    [portfolio_page, charts_page, transactions_page, sync_page, sync_history_page]
)


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


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_update_check() -> dict:
    if not _UPDATER_AVAILABLE:
        return {}
    try:
        return _updater.check_for_update()
    except Exception:
        return {}


with st.sidebar:
    # ── 1. User info ----------------------------------------------------------
    user = fetch_user_info()
    if user:
        username   = user.get("username", "—")
        steam_id   = user.get("steam_id", "")
        avatar_url = (
            user.get("avatar_url") or user.get("avatar") or
            user.get("avatarUrl")  or user.get("avatarfull") or
            user.get("avatarmedium") or ""
        )
        info_col, img_col = st.columns([3, 1])
        with info_col:
            st.markdown(
                "<div style='padding-top:6px'>"
                "<span style='font-size:1rem;font-weight:600'>{}</span><br>"
                "<span style='font-size:0.72rem;color:gray'>{}</span>"
                "</div>".format(username, steam_id),
                unsafe_allow_html=True,
            )
        with img_col:
            if avatar_url:
                st.markdown(
                    "<div style='text-align:right;padding-top:4px'>"
                    "<img src='{}' style='width:46px;height:46px;"
                    "border:2px solid white;border-radius:4px;object-fit:cover;'/>"
                    "</div>".format(avatar_url),
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── 2. Navigation -- pass Page objects directly, never string paths -------
    st.page_link(portfolio_page,    label="Portfolio",    use_container_width=True)
    st.page_link(charts_page,       label="Charts",       use_container_width=True)
    st.page_link(transactions_page, label="Transactions", use_container_width=True)
    st.page_link(sync_history_page, label="Sync History", use_container_width=True)

    st.divider()

    # ── 3. Controls ----------------------------------------------------------
    st.markdown("**⚙️ Controls**")

    if st.button("📦 Sync Inventory", use_container_width=True,
                 help="Fetch new trades from CSFloat and rebuild inventory"):
        with st.spinner("Fetching trades & rebuilding inventory..."):
            n = processor.sync_inventory()
            st.cache_data.clear()
            st.success("Done -- {} active items".format(n))
            st.rerun()

    if st.button("💰 Sync Prices", use_container_width=True,
                 help="Fetch latest prices from CSFloat & Steam"):
        st.switch_page(sync_page)   # Page object, not string

    st.divider()

    inv_sync   = database.meta_get("last_inventory_sync")
    price_sync = database.meta_get("last_price_sync")
    st.caption("Inventory: **{}**".format(inv_sync or "never"))
    st.caption("Prices: **{}**".format(price_sync or "never"))

    st.divider()

    # ── 4. Auto-sync status --------------------------------------------------
    task      = scheduler.get_task_status()
    last_auto = database.meta_get("last_auto_sync")
    if task["exists"] and task["enabled"]:
        st.markdown(
            "<span style='color:#06d6a0;font-size:0.82rem'>🟢 Auto-Sync enabled</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Last auto: **{}**  \nNext: **{}**".format(
                last_auto or "never", task["next_run"] or "—"
            )
        )
    else:
        st.markdown(
            "<span style='color:#aaa;font-size:0.82rem'>⚪ Auto-Sync off</span>",
            unsafe_allow_html=True,
        )
        st.caption("Set up in **🕘 Sync History**")

    st.divider()

    # ── 5. Version & update --------------------------------------------------
    local_ver = _updater.get_local_version() if _UPDATER_AVAILABLE else "—"
    st.caption("Version: **{}**".format(local_ver))

    if _UPDATER_AVAILABLE:
        update_info = _cached_update_check()

        if update_info.get("update_available"):
            latest = update_info["latest_version"]
            st.markdown(
                "<span style='color:#f4a261;font-size:0.82rem'>"
                "🔄 Update available: v{}</span>".format(latest),
                unsafe_allow_html=True,
            )

            notes = update_info.get("release_notes")
            if notes:
                with st.expander("What's new"):
                    st.markdown(notes[:800])

            if st.button("⬇️ Download v{}".format(latest), use_container_width=True,
                         help="Downloads update; applied on next app restart"):
                prog_bar = st.progress(0.0, text="Preparing...")

                def _prog(pct, msg):
                    prog_bar.progress(min(pct, 1.0), text=msg[:80])

                success, msg = _updater.download_update(update_info, progress_cb=_prog)
                if success:
                    prog_bar.progress(1.0, text="Download complete!")
                    st.success(
                        "v{} is ready.  \n"
                        "Close and re-open the app to apply the update.".format(latest)
                    )
                    st.cache_data.clear()
                else:
                    prog_bar.empty()
                    st.error("Download failed: {}".format(msg))

        elif update_info.get("error") and "not configured" not in str(update_info.get("error", "")):
            st.caption("_(update check failed)_")

    # ── 6. Bug report ----------------------------------------------------------
    st.markdown(
        "<a href='https://github.com/JanZlatnik/CS2Skinvest/issues/new' "
        "target='_blank' style='font-size:0.78rem;color:#888;text-decoration:none;'>"
        "🐛 Report a bug</a>",
        unsafe_allow_html=True,
    )


# ── Run the current page ------------------------------------------------------
pg.run()