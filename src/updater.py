"""
updater.py
----------
GitHub-based auto-update system for CS2 SkInvest.

Version detection strategy
--------------------------
1. Try /releases/latest  (works when you publish a formal GitHub Release)
2. If no releases exist (404), fall back to /tags  (works with plain git tags)

This means the update check works as soon as you push a tag -- you do NOT
need to create a formal GitHub Release first.

To release a new version
------------------------
  1. Bump version.txt  (e.g. "1.1.0")
  2. Commit and push
  3. Create a git tag:
       git tag v1.1.0
       git push origin v1.1.0
  That's it. The app will detect the new tag automatically.

  Optionally, also create a GitHub Release from that tag to get
  release notes shown in the sidebar.

Staged update (Windows-safe)
-----------------------------
Files cannot be replaced while Python is running them on Windows, so
updates are staged:
  1. download_update() downloads the zip to data/_pending_update/
     and writes data/_update_ready.txt with the new version.
  2. apply_pending() is called by launcher.py BEFORE Streamlit starts.
     It copies staged files over, removes staging dir, deletes the flag.

Protected paths (never overwritten by an update)
--------------------------------------------------
  data/       - all user data
  .env        - API key
  .gitignore

Git is NOT required -- updates are plain zip downloads over HTTPS.
"""

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

# !! Set these to your GitHub repo !!
GITHUB_OWNER = "JanZlatnik"
GITHUB_REPO  = "CS2Skinvest"

PROTECTED = {"data", ".env", ".gitignore"}

_API_BASE = "https://api.github.com/repos/{}/{}".format(GITHUB_OWNER, GITHUB_REPO)
_HEADERS  = {"Accept": "application/vnd.github.v3+json"}


# ── Version helpers -----------------------------------------------------------

def get_local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.0.0"


def _ver_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0, 0, 0)


# ── Remote version check ------------------------------------------------------

def _check_releases(timeout: int) -> dict | None:
    """
    Try the /releases/latest endpoint.
    Returns a partial result dict, or None if no releases exist.
    """
    r = requests.get(
        "{}/releases/latest".format(_API_BASE),
        headers=_HEADERS, timeout=timeout,
    )
    if r.status_code == 404:
        return None   # no releases -- caller should try tags
    r.raise_for_status()
    data = r.json()
    tag  = data.get("tag_name", "").lstrip("v")
    return {
        "latest_version": tag,
        "download_url":   data.get("zipball_url"),
        "release_notes":  data.get("body") or None,
    }


def _check_tags(timeout: int) -> dict | None:
    """
    Fall back to the /tags endpoint.
    Returns a partial result dict, or None if no tags exist.
    """
    r = requests.get(
        "{}/tags".format(_API_BASE),
        headers=_HEADERS, timeout=timeout,
    )
    r.raise_for_status()
    tags = r.json()
    if not tags:
        return None
    # tags are returned newest-first
    tag = tags[0]["name"].lstrip("v")
    # Build a zipball URL from the tag name (GitHub generates these automatically)
    zipball = "https://api.github.com/repos/{}/{}/zipball/v{}".format(
        GITHUB_OWNER, GITHUB_REPO, tag
    )
    return {
        "latest_version": tag,
        "download_url":   zipball,
        "release_notes":  None,   # tags have no release notes
    }


def check_for_update(timeout: int = 6) -> dict:
    """
    Query GitHub for the latest version.

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

    try:
        info = _check_releases(timeout)
        if info is None:
            info = _check_tags(timeout)
        if info is None:
            return {**base, "error": "No releases or tags found on GitHub."}

        latest = info["latest_version"]
        return {
            "update_available": _ver_tuple(latest) > _ver_tuple(local),
            "local_version":    local,
            "latest_version":   latest,
            "download_url":     info["download_url"],
            "release_notes":    info["release_notes"],
            "error":            None,
        }
    except Exception as exc:
        return {**base, "error": str(exc)}


# ── Download (stage) ----------------------------------------------------------

def download_update(info: dict, progress_cb=None) -> tuple:
    """
    Download and stage the update zip.
    Does NOT overwrite any running files.
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

        r = requests.get(url, stream=True, timeout=60, headers=_HEADERS)
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

        # GitHub zips have exactly one top-level folder: "owner-repo-<sha>/"
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


# ── Apply pending (called by launcher before Streamlit starts) ----------------

def apply_pending() -> tuple:
    """
    If a staged update exists, apply it now (before Streamlit starts).
    Safe to call on every launch -- no-op if nothing is pending.
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
    """Recursively copy src -> dst, skipping protected top-level names."""
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