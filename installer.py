"""
installer.py
────────────
CS2 SkInvest — interactive setup wizard.

Designed to be run by someone with zero programming experience:
  • Checks Python version (3.9+)
  • Installs all pip dependencies
  • Asks for the CSFloat API key → writes .env
  • Converts assets/icon.png → assets/icon.ico  (needed for the shortcut)
  • Creates a desktop shortcut (Windows) pointing to launcher.py

Run this once after downloading the app.
Re-running it is safe — it only overwrites things you confirm.
"""

import sys
import os
import subprocess
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

# ── Colour helpers (Windows supports ANSI in modern terminals) ────────────────
_USE_COLOUR = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def ok(msg):   print(_c(f"  ✅  {msg}", "32"))
def err(msg):  print(_c(f"  ❌  {msg}", "31"))
def warn(msg): print(_c(f"  ⚠️   {msg}", "33"))
def info(msg): print(_c(f"  ℹ️   {msg}", "36"))
def head(msg): print(_c(f"\n{'─'*50}\n  {msg}\n{'─'*50}", "1"))


# ── Step 1 : Check Python ─────────────────────────────────────────────────────

def check_python() -> bool:
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 9):
        err(f"Python 3.9+ required. You have {v.major}.{v.minor}.{v.micro}.")
        info("Download Python from:  https://www.python.org/downloads/")
        info("During install, check ☑ 'Add Python to PATH'")
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro}")
    return True


# ── Step 2 : Install requirements ────────────────────────────────────────────

def install_requirements() -> bool:
    req = APP_DIR / "requirements.txt"
    if not req.exists():
        err(f"requirements.txt not found at {req}")
        return False

    info("Installing Python packages (this may take a minute)…")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req),
         "--quiet", "--disable-pip-version-check"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        ok("All packages installed")
        return True
    else:
        err("pip install failed. Output:")
        print(result.stderr[-800:])
        return False


# ── Step 3 : Create / update .env ─────────────────────────────────────────────

def setup_env() -> bool:
    env_file = APP_DIR / ".env"

    # If .env already has a key, ask whether to keep it
    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
        lines   = {k.strip(): v.strip() for k, _, v in
                   (line.partition("=") for line in content.splitlines() if "=" in line)}
        existing_key = lines.get("CSFLOAT_API_KEY", "")
        if existing_key:
            masked = existing_key[:6] + "…" + existing_key[-4:] if len(existing_key) > 12 else "***"
            print(f"\n  .env already contains an API key: {masked}")
            keep = input("  Keep the existing key? [Y/n]: ").strip().lower()
            if keep != "n":
                ok(".env unchanged")
                return True

    print()
    info("Get your CSFloat API key at:  https://csfloat.com/profile  → API Keys")
    api_key = input("  Enter your CSFloat API key (or press Enter to skip): ").strip()

    if not api_key:
        warn("No key entered.  Edit .env manually before running the app.")
        # Write blank placeholder so the file exists
        env_file.write_text("CSFLOAT_API_KEY=\n", encoding="utf-8")
        return True

    env_file.write_text(f"CSFLOAT_API_KEY={api_key}\n", encoding="utf-8")
    ok(".env created with your API key")
    return True


# ── Step 4 : Convert icon.png → icon.ico ─────────────────────────────────────

def create_ico() -> Path | None:
    assets = APP_DIR / "assets"
    png    = assets / "icon.png"
    ico    = assets / "icon.ico"

    if not png.exists():
        warn(f"assets/icon.png not found — shortcut will use a default icon.")
        return None

    if ico.exists():
        ok("icon.ico already exists")
        return ico

    try:
        from PIL import Image
        img = Image.open(png).convert("RGBA")
        # Multi-resolution ICO: 16, 32, 48, 128, 256
        img.save(ico, format="ICO",
                 sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)])
        ok("icon.ico created")
        return ico
    except Exception as e:
        warn(f"Could not create icon.ico: {e}")
        return None


# ── Step 5a : Windows desktop shortcut ───────────────────────────────────────

def create_windows_shortcut(ico_path: Path | None) -> bool:
    """
    Create (or overwrite) 'CS2 SkInvest.lnk' on the user's Desktop.
    Uses Windows Script Host (cscript) via a temporary VBScript — no extra
    libraries required.
    """
    import winreg   # noqa: F401  (will fail gracefully if not Windows)

    desktop = Path(os.path.expanduser("~/Desktop"))
    if not desktop.exists():
        warn(f"Desktop folder not found at {desktop}")
        return False

    shortcut_path = desktop / "CS2 SkInvest.lnk"
    launcher      = APP_DIR / "launcher.py"

    # Use pythonw.exe if available — silences the console window
    py_dir    = Path(sys.executable).parent
    pythonw   = py_dir / "pythonw.exe"
    target_exe = str(pythonw) if pythonw.exists() else sys.executable

    # Normalize paths for VBScript (forward slashes work better)
    shortcut_path_vbs = str(shortcut_path).replace("\\", "/")
    target_exe_vbs = str(target_exe).replace("\\", "/")
    launcher_vbs = str(launcher).replace("\\", "/")
    app_dir_vbs = str(APP_DIR).replace("\\", "/")

    vbs_lines = [
        'Set oWS = WScript.CreateObject("WScript.Shell")',
        f'Set oLink = oWS.CreateShortcut("{shortcut_path_vbs}")',
        f'oLink.TargetPath = "{target_exe_vbs}"',
        f'oLink.Arguments = """{launcher_vbs}"""',
        f'oLink.WorkingDirectory = "{app_dir_vbs}"',
        'oLink.Description = "CS2 SkInvest - CS2 Skin Portfolio Tracker"',
    ]
    
    if ico_path and ico_path.exists():
        icon_path_vbs = str(ico_path).replace("\\", "/")
        vbs_lines.append(f'oLink.IconLocation = "{icon_path_vbs}"')
    
    vbs_lines.append('oLink.Save')
    vbs = "\n".join(vbs_lines)

    vbs_path = APP_DIR / "data" / "_create_shortcut.vbs"
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text(vbs, encoding="utf-8")

    result = subprocess.run(
        ["cscript", "//nologo", str(vbs_path)],
        capture_output=True, text=True,
    )
    vbs_path.unlink(missing_ok=True)

    if shortcut_path.exists():
        ok(f"Desktop shortcut created:  {shortcut_path}")
        return True
    else:
        warn(f"Could not create shortcut: {result.stderr.strip() or result.stdout.strip()}")
        return False


# ── Step 5b : macOS/Linux launch script ──────────────────────────────────────

def create_unix_launch_script() -> bool:
    script = APP_DIR / "start.sh"
    script.write_text(
        f"#!/bin/bash\ncd \"{APP_DIR}\"\n{sys.executable} launcher.py\n",
        encoding="utf-8",
    )
    try:
        os.chmod(script, 0o755)
        ok(f"Launch script created:  {script}")
        info("To start the app, double-click start.sh or run:  bash start.sh")
        return True
    except Exception as e:
        warn(f"Could not set executable bit on start.sh: {e}")
        return False


# ── Main wizard ───────────────────────────────────────────────────────────────

def main():
    print()
    print(_c("=" * 54, "1"))
    print(_c("   CS2 SkInvest — Setup Wizard", "1;36"))
    print(_c("=" * 54, "1"))

    # ── Python check ─────────────────────────────────────────────────────────
    head("Step 1/4 — Checking Python")
    if not check_python():
        input("\nPress Enter to exit…")
        sys.exit(1)

    # ── Install deps ──────────────────────────────────────────────────────────
    head("Step 2/4 — Installing dependencies")
    if not install_requirements():
        warn("You can try running manually:  pip install -r requirements.txt")
        cont = input("Continue anyway? [y/N]: ").strip().lower()
        if cont != "y":
            sys.exit(1)

    # ── API key ───────────────────────────────────────────────────────────────
    head("Step 3/4 — CSFloat API key")
    setup_env()

    # ── Shortcut ──────────────────────────────────────────────────────────────
    head("Step 4/4 — Desktop shortcut")
    ico = create_ico()   # always try to create .ico first

    if sys.platform == "win32":
        ans = input("  Create a desktop shortcut? [Y/n]: ").strip().lower()
        if ans != "n":
            try:
                create_windows_shortcut(ico)
            except Exception as e:
                warn(f"Shortcut creation failed: {e}")
                info(f"You can still start the app with:  python launcher.py")
    else:
        create_unix_launch_script()

    # ── Done ──────────────────────────────────────────────────────────────────
    print()
    print(_c("=" * 54, "1"))
    print(_c("  ✅  Setup complete!", "1;32"))
    print(_c("=" * 54, "1"))
    print()
    if sys.platform == "win32":
        info("Double-click 'CS2 SkInvest' on your desktop to start.")
        info("Or run:  python launcher.py")
    else:
        info("Run:  bash start.sh   or   python launcher.py")
    print()
    input("Press Enter to close…")


if __name__ == "__main__":
    main()