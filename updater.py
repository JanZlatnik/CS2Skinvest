"""
updater.py
──────────
GitHub-based auto-update system for CS2 SkInvest.

Flow
────
  check_for_update()   → dict  (call from sidebar; cached 1 h)
  download_update(info)→ bool  (call when user clicks Update button)
  apply_pending()      → bool  (call from launcher.py at startup)

Staged update approach (Windows-safe)
──────────────────────────────────────
Running Python files cannot be replaced on Windows while in use.
So updates are *staged*:
  1. download_update() downloads the GitHub release zip,
     extracts it to  data/_pending_update/<files>,
     and writes      data/_update_ready.txt  with the new version.
  2. apply_pending()  is called by launcher.py before Streamlit starts.
     It copies the staged files over, removes the staging dir, and
     deletes the flag file.  Streamlit then loads the fresh code.

Protected paths (never overwritten)
────────────────────────────────────
  data/          – all user data lives here
  .env           – API keys
  .gitignore

GitHub repo setup
─────────────────
  1. Push your code to a public GitHub repo.
  2. Set GITHUB_OWNER and GITHUB_REPO below.
  3. To release a new version:
       a. Bump version.txt to e.g. "1.2.3"
       b. Commit and push
       c. Create a GitHub Release with tag "v1.2.3"
          (GitHub auto-generates the source zip)
     The app picks up the new tag via the /releases/latest API.
"""

import os
import sys
import json
import shutil
import zipfile
import requests
from pathlib import Path
from datetime import datetime

APP_DIR      = Path(__file__).resolve().parent
VERSION_FILE = APP_DIR / "version.txt"
PENDING_DIR  = APP_DIR / "data" / "_pending_update"
READY_FLAG   = APP_DIR / "data" / "_update_ready.txt"

# ── ✏️  Edit these to match your GitHub repository ────────────────────────────
GITHUB_OWNER = "JanZlatnik"
GITHUB_REPO  = "CS2Skinvest"
# ─────────────────────────────────────────────────────────────────────────────

PROTECTED = {"data", ".env", ".gitignore"}


# ── Version helpers ───────────────────────────────────────────────────────────

def get_local_version() -> str:
    """Return version string from version.txt, e.g. '1.0.0'."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.0.0"


def _ver_tuple(v: str) -> tuple[int, ...]:
    """'1.2.3' → (1, 2, 3).  Strips leading 'v'."""
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0, 0, 0)


# ── Remote check ──────────────────────────────────────────────────────────────

def check_for_update(timeout: int = 6) -> dict:
    """
    Query GitHub releases/latest and compare to local version.

    Returns
    -------
    {
        update_available : bool,
        local_version    : str,
        latest_version   : str,
        download_url     : str | None,   # zipball URL
        release_notes    : str | None,
        error            : str | None,
    }
    """
    local = get_local_version()
    base = {
        "update_available": False,
        "local_version":    local,
        "latest_version":   local,
        "download_url":     None,
        "release_notes":    None,
        "error":            None,
    }

    # If repo is not configured yet, skip silently
    if GITHUB_OWNER == "your-github-username":
        return {**base, "error": "GitHub repo not configured in updater.py"}

    try:
        api_url = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
            "/releases/latest"
        )
        r = requests.get(
            api_url,
            timeout=timeout,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r.status_code == 404:
            return {**base, "error": "No releases found on GitHub"}
        r.raise_for_status()

        data         = r.json()
        latest_tag   = data.get("tag_name", "0.0.0").lstrip("v")
        notes        = data.get("body", "")
        zipball_url  = data.get("zipball_url")

        newer = _ver_tuple(latest_tag) > _ver_tuple(local)
        return {
            "update_available": newer,
            "local_version":    local,
            "latest_version":   latest_tag,
            "download_url":     zipball_url,
            "release_notes":    notes or None,
            "error":            None,
        }
    except Exception as exc:
        return {**base, "error": str(exc)}


# ── Download (stage the update) ───────────────────────────────────────────────

def download_update(info: dict, progress_cb=None) -> tuple[bool, str]:
    """
    Download and stage the update.  Does NOT replace any running files.

    progress_cb(pct: float, msg: str) is optional.

    Returns (success, message).
    """
    url = info.get("download_url")
    if not url:
        return False, "No download URL available."

    def _prog(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    try:
        _prog(0.05, "Connecting to GitHub…")

        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()

        zip_path = APP_DIR / "data" / "_update_download.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        total  = int(r.headers.get("content-length", 0)) or None
        done   = 0
        _prog(0.10, "Downloading update…")

        with open(zip_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    _prog(0.10 + 0.50 * done / total, f"Downloading… {done // 1024} KB")

        _prog(0.65, "Extracting…")

        extract_tmp = APP_DIR / "data" / "_update_extract_tmp"
        if extract_tmp.exists():
            shutil.rmtree(extract_tmp)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_tmp)

        # GitHub zips have exactly one top-level folder: "owner-repo-<sha>/"
        subdirs = [p for p in extract_tmp.iterdir() if p.is_dir()]
        src_dir = subdirs[0] if len(subdirs) == 1 else extract_tmp

        _prog(0.80, "Staging files…")

        if PENDING_DIR.exists():
            shutil.rmtree(PENDING_DIR)
        PENDING_DIR.mkdir(parents=True)

        _copy_tree(src_dir, PENDING_DIR)

        _prog(0.95, "Cleaning up…")

        zip_path.unlink(missing_ok=True)
        shutil.rmtree(extract_tmp, ignore_errors=True)

        # Write the flag so launcher.py knows there's a pending update
        READY_FLAG.write_text(info["latest_version"], encoding="utf-8")

        _prog(1.00, f"v{info['latest_version']} ready — restart to apply")
        return True, f"v{info['latest_version']} staged successfully"

    except Exception as exc:
        return False, f"Download failed: {exc}"


# ── Apply pending update (called by launcher at startup) ─────────────────────

def apply_pending() -> tuple[bool, str]:
    """
    If a staged update exists, apply it now (before Streamlit starts).
    Safe to call on every launch — no-op if nothing is pending.

    Returns (applied: bool, message: str).
    """
    if not READY_FLAG.exists():
        return False, "No pending update."

    new_version = READY_FLAG.read_text(encoding="utf-8").strip()

    if not PENDING_DIR.exists():
        READY_FLAG.unlink(missing_ok=True)
        return False, "Staged files missing — flag cleared."

    try:
        _copy_tree(PENDING_DIR, APP_DIR)
        VERSION_FILE.write_text(new_version, encoding="utf-8")
        shutil.rmtree(PENDING_DIR, ignore_errors=True)
        READY_FLAG.unlink(missing_ok=True)
        return True, f"Updated to v{new_version}"
    except Exception as exc:
        return False, f"Failed to apply update: {exc}"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _copy_tree(src: Path, dst: Path):
    """Recursively copy src → dst, skipping protected top-level paths."""
    for item in src.iterdir():
        if item.name in PROTECTED:
            continue
        if item.name.startswith("."):
            continue
        target = dst / item.name
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_tree(item, target)
        else:
            shutil.copy2(item, target)