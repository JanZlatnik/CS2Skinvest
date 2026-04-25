"""
launcher.py
-----------
CS2 SkInvest -- desktop launcher.

What it does
------------
1. Applies any pending update (staged by updater.py) before Streamlit loads.
2. Finds a free port (default 8501).
3. Starts Streamlit with:
     - CWD = ROOT_DIR  (so all relative "data/..." paths in modules work)
     - PYTHONPATH includes SRC_DIR  (so "import database" etc. work from pages/)
4. Waits for the server, then opens the default browser.
5. Shows a system-tray icon (right-click -> Quit).
   Falls back to a tiny tkinter window if pystray is not installed.

Desktop shortcut target
-----------------------
    pythonw.exe  "C:\\...\\src\\launcher.py"
    Working dir: C:\\...\\  (repo root)
The installer creates this automatically.
"""

import sys
import os
import subprocess
import time
import webbrowser
import socket
import threading
from pathlib import Path

SRC_DIR  = Path(__file__).resolve().parent   # .../repo/src
ROOT_DIR = SRC_DIR.parent                    # .../repo

# ── Logging -------------------------------------------------------------------
import logging
from logging.handlers import RotatingFileHandler

_LOG_DIR = ROOT_DIR / "data"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_handler = RotatingFileHandler(
    _LOG_DIR / "launcher.log", maxBytes=200_000, backupCount=1, encoding="utf-8"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[_handler],
)
if sys.stderr and hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
log = logging.getLogger("launcher")

# ── Apply any pending update before Streamlit loads --------------------------
sys.path.insert(0, str(SRC_DIR))
try:
    import updater as _updater
    applied, msg = _updater.apply_pending()
    if applied:
        log.info("Update applied: {}".format(msg))
        print("Update applied: {}".format(msg))
except Exception as _ue:
    log.warning("Could not check for pending update: {}".format(_ue))


# ── Port helpers --------------------------------------------------------------

def _find_free_port(start: int = 8501, attempts: int = 20) -> int:
    for p in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


# ── Start Streamlit -----------------------------------------------------------

def _start_streamlit(port: int) -> subprocess.Popen:
    app_py = SRC_DIR / "app.py"

    # Inject SRC_DIR into PYTHONPATH so every Streamlit page can "import database" etc.
    env = os.environ.copy()
    sep = ";" if sys.platform == "win32" else ":"
    env["PYTHONPATH"] = str(SRC_DIR) + sep + env.get("PYTHONPATH", "")

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_py),
        "--server.port",              str(port),
        "--server.headless",          "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType",   "none",
    ]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),          # <-- data/ paths resolve from here
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _wait_for_server(port: int, timeout: float = 45.0) -> bool:
    import urllib.request
    url      = "http://localhost:{}/_stcore/health".format(port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


# ── System tray (pystray) -----------------------------------------------------

def _run_tray(proc: subprocess.Popen, port: int):
    try:
        import pystray
        from PIL import Image as PILImage

        icon_path = ROOT_DIR / "assets" / "icon.png"
        if icon_path.exists():
            img = PILImage.open(icon_path).convert("RGBA").resize((64, 64))
        else:
            img = PILImage.new("RGBA", (64, 64), (10, 124, 110, 255))

        def _open(_icon, _item):
            webbrowser.open("http://localhost:{}".format(port))

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
        log.info("pystray not installed -- falling back to tkinter window.")
        _run_tkinter_fallback(proc, port)
    except Exception as exc:
        log.warning("Tray icon failed ({}) -- falling back to tkinter.".format(exc))
        _run_tkinter_fallback(proc, port)


# ── tkinter fallback (always available) --------------------------------------

def _run_tkinter_fallback(proc: subprocess.Popen, port: int):
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("CS2 SkInvest")
        root.geometry("260x100")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        ico_path = ROOT_DIR / "assets" / "icon.ico"
        if ico_path.exists():
            try:
                root.iconbitmap(str(ico_path))
            except Exception:
                pass

        tk.Label(root, text="CS2 SkInvest is running",
                 pady=8, font=("Segoe UI", 10)).pack()
        btn_frame = tk.Frame(root)
        btn_frame.pack()

        def _open():
            webbrowser.open("http://localhost:{}".format(port))

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
        log.warning("tkinter failed ({}). Blocking on process.".format(exc))
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()


# ── Watchdog: restart Streamlit once if it crashes ---------------------------

def _monitor(proc: subprocess.Popen, port: int, stop_event: threading.Event):
    proc.wait()
    if stop_event.is_set():
        return
    log.warning("Streamlit exited unexpectedly. Restarting...")
    time.sleep(2)
    new_proc = _start_streamlit(port)
    if _wait_for_server(port, timeout=30):
        log.info("Streamlit restarted OK.")
        proc.__dict__.update(new_proc.__dict__)


# ── Entry point ---------------------------------------------------------------

def main():
    log.info("=" * 55)
    log.info("CS2 SkInvest launcher starting")

    port = _find_free_port()
    log.info("Using port {}".format(port))

    proc = _start_streamlit(port)
    log.info("Streamlit PID {} started".format(proc.pid))
    print("Starting CS2 SkInvest on http://localhost:{} ...".format(port))

    if not _wait_for_server(port):
        log.error("Streamlit did not respond within 45 s -- aborting.")
        proc.terminate()
        sys.exit(1)

    log.info("Server ready -- opening browser")
    webbrowser.open("http://localhost:{}".format(port))

    stop_event = threading.Event()
    threading.Thread(
        target=_monitor, args=(proc, port, stop_event), daemon=True
    ).start()

    try:
        _run_tray(proc, port)
    finally:
        stop_event.set()
        proc.terminate()


if __name__ == "__main__":
    main()