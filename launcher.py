"""
launcher.py
───────────
CS2 SkInvest — desktop launcher.

What it does
────────────
1. Applies any pending update (staged by updater.py) before Streamlit loads.
2. Finds a free port (default 8501).
3. Starts Streamlit as a background process (no console window).
4. Waits for the server to be ready, then opens the default browser.
5. Shows a system-tray icon so the user can quit cleanly.
   └─ If pystray is not installed → falls back to a tiny tkinter window.

Desktop shortcut
────────────────
Point the shortcut to:
    Target : C:\\path\\to\\pythonw.exe  "C:\\path\\to\\launcher.py"
    Icon   : C:\\path\\to\\assets\\icon.ico
The installer creates this automatically.

Cross-platform notes
────────────────────
• Windows : pythonw.exe used via the shortcut → no console window
• macOS   : python launcher.py  (no system tray; falls back to tkinter)
• Linux   : same as macOS; pystray works on Linux too if AppIndicator is
            available, but tkinter fallback always works
"""

import sys
import os
import subprocess
import time
import webbrowser
import socket
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

# ── Logging (goes to data/launcher.log) ──────────────────────────────────────
import logging
from logging.handlers import RotatingFileHandler

_LOG_PATH = APP_DIR / "data" / "launcher.log"
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_handler = RotatingFileHandler(_LOG_PATH, maxBytes=200_000, backupCount=1, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[_handler],
)
# Also log to stdout if a console is attached
if sys.stderr and sys.stderr.isatty():
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

log = logging.getLogger("launcher")

# ── Apply any pending update before everything else ───────────────────────────
try:
    import updater as _updater
    applied, msg = _updater.apply_pending()
    if applied:
        log.info(f"Update applied: {msg}")
        print(f"✅ {msg}")
except Exception as _ue:
    log.warning(f"Could not check for pending update: {_ue}")


# ── Port helpers ──────────────────────────────────────────────────────────────

def _find_free_port(start: int = 8501, attempts: int = 20) -> int:
    for p in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start   # fallback — Streamlit will handle the conflict


# ── Streamlit process ─────────────────────────────────────────────────────────

def _start_streamlit(port: int) -> subprocess.Popen:
    app_py = APP_DIR / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_py),
        "--server.port",          str(port),
        "--server.headless",      "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType", "none",   # no auto-reload needed
    ]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW   # suppresses any flashing console
    return subprocess.Popen(
        cmd,
        cwd=str(APP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _wait_for_server(port: int, timeout: float = 45.0) -> bool:
    """Poll the Streamlit health endpoint until it responds."""
    import urllib.request, urllib.error
    url      = f"http://localhost:{port}/_stcore/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


# ── System tray (pystray) ─────────────────────────────────────────────────────

def _run_tray(proc: subprocess.Popen, port: int):
    """Run the system-tray icon. Blocks until the user chooses Quit."""
    try:
        import pystray
        from PIL import Image as PILImage

        icon_path = APP_DIR / "assets" / "icon.png"
        if icon_path.exists():
            img = PILImage.open(icon_path).convert("RGBA").resize((64, 64))
        else:
            img = PILImage.new("RGBA", (64, 64), (10, 124, 110, 255))

        def _open(icon, _item):
            webbrowser.open(f"http://localhost:{port}")

        def _quit(icon, _item):
            log.info("Quit requested via tray.")
            icon.stop()
            proc.terminate()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("Open CS2 SkInvest", _open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _quit),
        )
        tray = pystray.Icon("CS2 SkInvest", img, "CS2 SkInvest", menu)
        log.info("System tray icon active.")
        tray.run()

    except ImportError:
        log.info("pystray not installed — falling back to tkinter window.")
        _run_tkinter_fallback(proc, port)
    except Exception as exc:
        log.warning(f"Tray icon failed ({exc}) — falling back to tkinter.")
        _run_tkinter_fallback(proc, port)


# ── tkinter fallback (always available) ──────────────────────────────────────

def _run_tkinter_fallback(proc: subprocess.Popen, port: int):
    """Tiny control window shown when pystray isn't available."""
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("CS2 SkInvest")
        root.geometry("260x100")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        ico_path = APP_DIR / "assets" / "icon.ico"
        if ico_path.exists():
            try:
                root.iconbitmap(str(ico_path))
            except Exception:
                pass

        tk.Label(root, text="CS2 SkInvest is running", pady=8,
                 font=("Segoe UI", 10)).pack()

        btn_frame = tk.Frame(root)
        btn_frame.pack()

        def _open():
            webbrowser.open(f"http://localhost:{port}")

        def _quit():
            log.info("Quit via tkinter window.")
            proc.terminate()
            root.destroy()
            os._exit(0)

        ttk.Button(btn_frame, text="Open Browser", command=_open).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Stop App",     command=_quit).pack(side="left", padx=6)
        root.protocol("WM_DELETE_WINDOW", _quit)
        root.mainloop()

    except Exception as exc:
        # Absolute last resort: block on process
        log.warning(f"tkinter failed ({exc}). Press Ctrl+C to stop.")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()


# ── Monitor thread (restart Streamlit if it crashes) ─────────────────────────

def _monitor(proc: subprocess.Popen, port: int, restart_event: threading.Event):
    """Watch the Streamlit process; restart it once if it dies unexpectedly."""
    proc.wait()
    if restart_event.is_set():
        return   # intentional quit — do nothing
    log.warning("Streamlit exited unexpectedly. Attempting restart…")
    time.sleep(2)
    new_proc = _start_streamlit(port)
    if _wait_for_server(port, timeout=30):
        log.info("Streamlit restarted successfully.")
        # Replace the reference so the tray/tkinter can kill the new one
        proc.__class__ = new_proc.__class__
        proc.__dict__.update(new_proc.__dict__)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    log.info("=" * 55)
    log.info("CS2 SkInvest launcher starting")

    port = _find_free_port()
    log.info(f"Using port {port}")

    proc = _start_streamlit(port)
    log.info(f"Streamlit PID {proc.pid} started")

    print(f"Starting CS2 SkInvest on http://localhost:{port} …")
    if not _wait_for_server(port):
        log.error("Streamlit did not respond within 45 s — aborting.")
        proc.terminate()
        sys.exit(1)

    log.info("Server ready — opening browser")
    webbrowser.open(f"http://localhost:{port}")

    # Optional watchdog thread
    quit_event = threading.Event()
    watcher    = threading.Thread(
        target=_monitor, args=(proc, port, quit_event), daemon=True
    )
    watcher.start()

    try:
        _run_tray(proc, port)
    finally:
        quit_event.set()
        proc.terminate()


if __name__ == "__main__":
    main()