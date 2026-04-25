import streamlit as st
import requests
from dotenv import load_dotenv
import os
from PIL import Image
import database
import scheduler
import processor

# ── Update system (optional — gracefully disabled if updater.py missing) ──────
try:
    import updater as _updater
    _UPDATER_AVAILABLE = True
except ImportError:
    _UPDATER_AVAILABLE = False

load_dotenv()
API_KEY = os.getenv("CSFLOAT_API_KEY")

# Ensure DB schema is present on every cold start before any page code runs
database.init_db()

img_icon = Image.open("assets/icon.png")
st.set_page_config(page_title="CS2 SkInvest", layout="wide", page_icon=img_icon)

st.markdown("""
<style>
/* Hide auto-generated sidebar nav */
[data-testid="stSidebarNav"] { display: none !important; }

/* Primary buttons: mint green with dark text — replaces default red/orange */
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

/* Form submit buttons inherit the same style */
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
    """Check GitHub for updates once per hour."""
    if not _UPDATER_AVAILABLE:
        return {}
    try:
        return _updater.check_for_update()
    except Exception:
        return {}


with st.sidebar:
    # ── 1. User info ──────────────────────────────────────────────────────────
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
                f"<div style='padding-top:6px'>"
                f"<span style='font-size:1rem;font-weight:600'>{username}</span><br>"
                f"<span style='font-size:0.72rem;color:gray'>{steam_id}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with img_col:
            if avatar_url:
                st.markdown(
                    f"<div style='text-align:right;padding-top:4px'>"
                    f"<img src='{avatar_url}' style='width:46px;height:46px;"
                    f"border:2px solid white;border-radius:4px;object-fit:cover;'/>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── 2. Navigation ─────────────────────────────────────────────────────────
    st.page_link("pages/portfolio.py",    label="💼  Portfolio",    use_container_width=True)
    st.page_link("pages/charts.py",       label="📊  Charts",       use_container_width=True)
    st.page_link("pages/transactions.py", label="✏️  Transactions", use_container_width=True)
    st.page_link("pages/sync_history.py", label="🕘  Sync History", use_container_width=True)

    st.divider()

    # ── 3. Controls (shared across all pages — no more per-page duplication) ──
    st.markdown("**⚙️ Controls**")

    if st.button("📦 Sync Inventory", use_container_width=True,
                 help="Fetch new trades from CSFloat and rebuild inventory"):
        with st.spinner("Fetching trades & rebuilding inventory…"):
            n = processor.sync_inventory()
            st.cache_data.clear()
            st.success(f"Done — {n} active items")
            st.rerun()

    if st.button("💰 Sync Prices", use_container_width=True,
                 help="Fetch latest prices from CSFloat & Steam"):
        st.switch_page("pages/sync_page.py")

    st.divider()

    inv_sync   = database.meta_get("last_inventory_sync")
    price_sync = database.meta_get("last_price_sync")
    st.caption(f"Inventory: **{inv_sync or 'never'}**")
    st.caption(f"Prices: **{price_sync or 'never'}**")

    st.divider()

    # ── 4. Auto-sync status ───────────────────────────────────────────────────
    task      = scheduler.get_task_status()
    last_auto = database.meta_get("last_auto_sync")
    if task["exists"] and task["enabled"]:
        st.markdown(
            "<span style='color:#06d6a0;font-size:0.82rem'>🟢 Auto-Sync enabled</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Last auto: **{last_auto or 'never'}**  \n"
            f"Next: **{task['next_run'] or '—'}**"
        )
    else:
        st.markdown(
            "<span style='color:#aaa;font-size:0.82rem'>⚪ Auto-Sync off</span>",
            unsafe_allow_html=True,
        )
        st.caption("Set up in **🕘 Sync History**")

    st.divider()

    # ── 5. Version & update ───────────────────────────────────────────────────
    local_ver = _updater.get_local_version() if _UPDATER_AVAILABLE else "—"
    st.caption(f"Version: **{local_ver}**")

    if _UPDATER_AVAILABLE:
        update_info = _cached_update_check()

        if update_info.get("update_available"):
            latest = update_info["latest_version"]
            st.markdown(
                f"<span style='color:#f4a261;font-size:0.82rem'>"
                f"🔄 Update available: v{latest}</span>",
                unsafe_allow_html=True,
            )

            # Show release notes in an expander
            notes = update_info.get("release_notes")
            if notes:
                with st.expander("What's new"):
                    st.markdown(notes[:800])

            if st.button(f"⬇️ Download v{latest}", use_container_width=True,
                         help="Download update (applied on next app restart)"):
                prog_bar = st.progress(0.0, text="Preparing…")

                def _prog(pct, msg):
                    prog_bar.progress(min(pct, 1.0), text=msg[:80])

                success, msg = _updater.download_update(update_info, progress_cb=_prog)
                if success:
                    prog_bar.progress(1.0, text="✅ Download complete!")
                    st.success(
                        f"v{latest} is ready.  \n"
                        "Close and re-open the app to apply the update."
                    )
                    st.cache_data.clear()
                else:
                    prog_bar.empty()
                    st.error(f"Download failed: {msg}")

        elif update_info.get("error") and "not configured" not in update_info["error"]:
            # Only show network/API errors (not the "not configured" placeholder)
            st.caption("_(update check failed)_")


# ── Page registration ─────────────────────────────────────────────────────────
portfolio_page    = st.Page("pages/portfolio.py",    title="Portfolio",    icon="💼")
charts_page       = st.Page("pages/charts.py",       title="Charts",       icon="📊")
transactions_page = st.Page("pages/transactions.py", title="Transactions", icon="✏️")
sync_page         = st.Page("pages/sync_page.py",    title="Sync Prices",  icon="💰")
sync_history_page = st.Page("pages/sync_history.py", title="Sync History", icon="🕘")

pg = st.navigation([portfolio_page, charts_page, transactions_page,
                    sync_page, sync_history_page])
pg.run()