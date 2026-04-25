"""
updater.py
----------
GitHub-based auto-update system for CS2 SkInvest.

Flow
----
  check_for_update()    -> dict   (call from sidebar; cache 1 h)
  download_update(info) -> bool   (call when user clicks Update)
  apply_pending()       -> bool   (call from launcher.py at startup)

Staged update (Windows-safe)
-----------------------------
Running files cannot be replaced on Windows while in use, so updates
are staged:
  1. download_update() saves the GitHub release zip to
       data/_pending_update/<files>
     and writes a flag file  data/_update_ready.txt  with the new version.
  2. apply_pending() is called by launcher.py BEFORE Streamlit starts.
     It copies staged files over, removes the staging dir, and deletes
     the flag.  Streamlit then loads the fresh code.

Protected paths (never overwritten)
-------------------------------------
  data/          - all user data
  .env           - API keys
  .gitignore

GitHub repo setup
-----------------
  1. Push code to a public GitHub repo.
  2. Set GITHUB_OWNER and GITHUB_REPO below.
  3. To release a new version:
       a. Bump version.txt to e.g. "1.2.3"
       b. Commit and push
       c. Create a GitHub Release with tag "v1.2.3"
          (GitHub auto-generates the source zip)
"""

import os
import shutil
import zipfile
import requests
from pathlib import Path

SRC_DIR  = Path(__file__).resolve().parent   # .../repo/src
ROOT_DIR = SRC_DIR.parent                    # .../repo

VERSION_FILE = ROOT_DIR / "version.txt"
DATA_DIR     = ROOT_DIR / "data"
PENDING_DIR  = DATA_DIR / "_pending_update"
READY_FLAG   = DATA_DIR / "_update_ready.txt"

# !! Edit these to match your GitHub repository !!
GITHUB_OWNER = "JanZlatnik"
GITHUB_REPO  = "CS2Skinvest"

PROTECTED = {"data", ".env", ".gitignore"}


# ── Version helpers -----------------------------------------------------------

def get_local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.0.0"


def _ver_tuple(v: str):
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0, 0, 0)


# ── Remote check -------------------------------------------------------------

def check_for_update(timeout: int = 6) -> dict:
    """
    Query GitHub releases/latest.

    Returns dict with keys:
      update_available, local_version, latest_version,
      download_url, release_notes, error
    """
    local = get_local_version()
    base  = {
        "update_available": False,
        "local_version":    local,
        "latest_version":   local,
        "download_url":     None,
        "release_notes":    None,
        "error":            None,
    }

    if GITHUB_OWNER == "your-github-username":
        return {**base, "error": "GitHub repo not configured in updater.py"}

    try:
        url = "https://api.github.com/repos/{}/{}/releases/latest".format(
            GITHUB_OWNER, GITHUB_REPO
        )
        r = requests.get(
            url, timeout=timeout,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r.status_code == 404:
            return {**base, "error": "No releases found on GitHub"}
        r.raise_for_status()

        data        = r.json()
        latest_tag  = data.get("tag_name", "0.0.0").lstrip("v")
        notes       = data.get("body", "")
        zipball_url = data.get("zipball_url")

        return {
            "update_available": _ver_tuple(latest_tag) > _ver_tuple(local),
            "local_version":    local,
            "latest_version":   latest_tag,
            "download_url":     zipball_url,
            "release_notes":    notes or None,
            "error":            None,
        }
    except Exception as exc:
        return {**base, "error": str(exc)}


# ── Download (stage) ---------------------------------------------------------

def download_update(info: dict, progress_cb=None) -> tuple:
    """
    Download and stage the update.
    Does NOT replace any running files.
    Returns (success: bool, message: str).
    """
    url = info.get("download_url")
    if not url:
        return False, "No download URL available."

    def _prog(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)

    try:
        _prog(0.05, "Connecting to GitHub...")

        r = requests.get(url, stream=True, timeout=60)
        r.raise_for_status()

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = DATA_DIR / "_update_download.zip"

        total = int(r.headers.get("content-length", 0)) or None
        done  = 0
        _prog(0.10, "Downloading update...")

        with open(zip_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    _prog(0.10 + 0.50 * done / total,
                          "Downloading... {} KB".format(done // 1024))

        _prog(0.65, "Extracting...")

        extract_tmp = DATA_DIR / "_update_extract_tmp"
        if extract_tmp.exists():
            shutil.rmtree(extract_tmp)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_tmp)

        # GitHub zips contain exactly one top-level folder: "owner-repo-<sha>/"
        subdirs = [p for p in extract_tmp.iterdir() if p.is_dir()]
        src_dir = subdirs[0] if len(subdirs) == 1 else extract_tmp

        _prog(0.80, "Staging files...")

        if PENDING_DIR.exists():
            shutil.rmtree(PENDING_DIR)
        PENDING_DIR.mkdir(parents=True)

        _copy_tree(src_dir, PENDING_DIR)

        _prog(0.95, "Cleaning up...")
        zip_path.unlink(missing_ok=True)
        shutil.rmtree(extract_tmp, ignore_errors=True)

        READY_FLAG.write_text(info["latest_version"], encoding="utf-8")

        _prog(1.00, "v{} ready -- restart to apply".format(info["latest_version"]))
        return True, "v{} staged successfully".format(info["latest_version"])

    except Exception as exc:
        return False, "Download failed: {}".format(exc)


# ── Apply pending (called by launcher before Streamlit starts) ---------------

def apply_pending() -> tuple:
    """
    If staged update exists, apply it now.
    Safe to call on every launch -- no-op when nothing is pending.
    Returns (applied: bool, message: str).
    """
    if not READY_FLAG.exists():
        return False, "No pending update."

    new_version = READY_FLAG.read_text(encoding="utf-8").strip()

    if not PENDING_DIR.exists():
        READY_FLAG.unlink(missing_ok=True)
        return False, "Staged files missing -- flag cleared."

    try:
        _copy_tree(PENDING_DIR, ROOT_DIR)
        VERSION_FILE.write_text(new_version, encoding="utf-8")
        shutil.rmtree(PENDING_DIR, ignore_errors=True)
        READY_FLAG.unlink(missing_ok=True)
        return True, "Updated to v{}".format(new_version)
    except Exception as exc:
        return False, "Failed to apply update: {}".format(exc)


# ── Internal helpers ----------------------------------------------------------

def _copy_tree(src: Path, dst: Path):
    """Recursively copy src -> dst, skipping protected top-level paths."""
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