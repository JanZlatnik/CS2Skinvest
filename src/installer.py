"""
installer.py
------------
CS2 SkInvest -- interactive setup wizard.

Designed to be run by someone with zero programming experience:
  * Checks Python version (3.10+)
  * Installs all pip dependencies
  * Asks for the CSFloat API key -> writes .env at repo root
  * Creates a desktop shortcut (Windows) pointing to src/launcher.py

Run once after downloading the app.
Re-running is safe -- it only overwrites things you confirm.

Called by setup.bat as:  python src\\installer.py
"""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional

# src/ is where this file lives; ROOT is one level up
SRC_DIR  = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent


def ok(msg):   print("  [OK]  {}".format(msg))
def err(msg):  print("  [!!]  {}".format(msg))
def warn(msg): print("  [??]  {}".format(msg))
def info(msg): print("  [..]  {}".format(msg))
def head(msg): print("\n{}\n  {}\n{}".format("-"*50, msg, "-"*50))


# -- Step 1 : Check Python -----------------------------------------------------

def check_python() -> bool:
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        err("Python 3.10 or later is required. You have {}.{}.{}.".format(
            v.major, v.minor, v.micro))
        info("Download Python from:  https://www.python.org/downloads/")
        info("During install, tick 'Add Python to PATH'")
        return False
    ok("Python {}.{}.{}".format(v.major, v.minor, v.micro))
    return True


# -- Step 2 : Install requirements --------------------------------------------

def install_requirements() -> bool:
    req = ROOT_DIR / "requirements.txt"
    if not req.exists():
        err("requirements.txt not found at {}".format(req))
        return False

    info("Installing Python packages (this may take a minute)...")
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


# -- Step 3 : Create / update .env --------------------------------------------

def setup_env() -> bool:
    env_file = ROOT_DIR / ".env"

    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
        lines   = {k.strip(): v.strip() for k, _, v in
                   (line.partition("=") for line in content.splitlines() if "=" in line)}
        existing_key = lines.get("CSFLOAT_API_KEY", "")
        if existing_key:
            n = len(existing_key)
            masked = (existing_key[:6] + "..." + existing_key[-4:]) if n > 12 else "***"
            print("\n  .env already contains an API key: {}".format(masked))
            keep = input("  Keep the existing key? [Y/n]: ").strip().lower()
            if keep != "n":
                ok(".env unchanged")
                return True

    print()
    info("Get your CSFloat API key at:  https://csfloat.com/profile  -> API Keys")
    api_key = input("  Enter your CSFloat API key (or press Enter to skip): ").strip()

    if not api_key:
        warn("No key entered.  Edit .env manually before running the app.")
        env_file.write_text("CSFLOAT_API_KEY=\n", encoding="utf-8")
        return True

    env_file.write_text("CSFLOAT_API_KEY={}\n".format(api_key), encoding="utf-8")
    ok(".env created with your API key")
    return True


# -- Step 4 : Check / report icon.ico -----------------------------------------

def check_ico() -> Optional[Path]:
    """
    icon.ico should already be committed to assets/.
    If missing (old clone), tries to regenerate from icon.png.
    """
    assets = ROOT_DIR / "assets"
    ico    = assets / "icon.ico"
    png    = assets / "icon.png"

    if ico.exists():
        ok("assets/icon.ico found")
        return ico

    warn("assets/icon.ico not found -- trying to create from icon.png...")
    if not png.exists():
        warn("assets/icon.png also missing -- shortcut will use a default icon.")
        return None

    try:
        from PIL import Image
        img = Image.open(png).convert("RGBA")
        img.save(
            ico,
            format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (128, 128), (256, 256)],
        )
        ok("icon.ico created from icon.png")
        return ico
    except Exception as e:
        warn("Could not create icon.ico: {}".format(e))
        return None


# -- Step 5a : Windows desktop shortcut ---------------------------------------

def create_windows_shortcut(ico_path: Optional[Path]) -> bool:
    """
    Creates 'CS2 SkInvest.lnk' on the Desktop using a temporary VBScript.
    No extra libraries needed.
    """
    try:
        import winreg  # noqa -- confirms we are on Windows
    except ImportError:
        warn("winreg not available (not running on Windows).")
        return False

    desktop = Path(os.path.expanduser("~/Desktop"))
    if not desktop.exists():
        warn("Desktop folder not found at {}".format(desktop))
        return False

    shortcut_path = desktop / "CS2 SkInvest.lnk"
    launcher      = SRC_DIR / "launcher.py"

    # pythonw.exe suppresses the console window; fall back to python.exe
    py_dir     = Path(sys.executable).parent
    pythonw    = py_dir / "pythonw.exe"
    target_exe = str(pythonw if pythonw.exists() else sys.executable)

    # Build VBScript line by line to avoid any f-string / quoting issues.
    # Chr(34) produces a literal double-quote inside a VBScript string.
    vbs_lines = [
        'Set oWS = WScript.CreateObject("WScript.Shell")',
        'Set oLink = oWS.CreateShortcut("{}")'.format(shortcut_path),
        'oLink.TargetPath = "{}"'.format(target_exe),
        'oLink.Arguments = Chr(34) & "{}" & Chr(34)'.format(launcher),
        'oLink.WorkingDirectory = "{}"'.format(ROOT_DIR),
        'oLink.Description = "CS2 SkInvest - CS2 Skin Portfolio Tracker"',
    ]
    if ico_path and ico_path.exists():
        vbs_lines.append('oLink.IconLocation = "{}"'.format(ico_path))
    vbs_lines.append("oLink.Save")

    vbs_text = "\r\n".join(vbs_lines) + "\r\n"

    vbs_path = ROOT_DIR / "data" / "_create_shortcut.vbs"
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text(vbs_text, encoding="utf-8")

    result = subprocess.run(
        ["cscript", "//nologo", str(vbs_path)],
        capture_output=True, text=True,
    )
    vbs_path.unlink(missing_ok=True)

    if shortcut_path.exists():
        ok("Desktop shortcut created:  {}".format(shortcut_path))
        return True
    else:
        warn("Could not create shortcut: {}".format(
            result.stderr.strip() or result.stdout.strip()))
        return False


# -- Step 5b : macOS / Linux launch script ------------------------------------

def create_unix_launch_script() -> bool:
    script = ROOT_DIR / "start.sh"
    script.write_text(
        '#!/bin/bash\ncd "{}"\n{} src/launcher.py\n'.format(ROOT_DIR, sys.executable),
        encoding="utf-8",
    )
    try:
        os.chmod(script, 0o755)
        ok("Launch script created:  {}".format(script))
        info("Start the app with:  bash start.sh")
        return True
    except Exception as e:
        warn("Could not chmod start.sh: {}".format(e))
        return False


# -- Main wizard ---------------------------------------------------------------

def main():
    print()
    print("=" * 54)
    print("   CS2 SkInvest -- Setup Wizard")
    print("=" * 54)

    head("Step 1/4 -- Checking Python")
    if not check_python():
        input("\nPress Enter to exit...")
        sys.exit(1)

    head("Step 2/4 -- Installing dependencies")
    if not install_requirements():
        warn("You can try manually:  pip install -r requirements.txt")
        cont = input("Continue anyway? [y/N]: ").strip().lower()
        if cont != "y":
            sys.exit(1)

    head("Step 3/4 -- CSFloat API key")
    setup_env()

    head("Step 4/4 -- Desktop shortcut")
    ico = check_ico()

    if sys.platform == "win32":
        ans = input("  Create a desktop shortcut? [Y/n]: ").strip().lower()
        if ans != "n":
            try:
                create_windows_shortcut(ico)
            except Exception as e:
                warn("Shortcut creation failed: {}".format(e))
                info("You can still start the app with:  python src\\launcher.py")
    else:
        create_unix_launch_script()

    print()
    print("=" * 54)
    print("  Setup complete!")
    print("=" * 54)
    print()
    if sys.platform == "win32":
        info("Double-click 'CS2 SkInvest' on your desktop to start.")
        info("Or run:  python src\\launcher.py")
    else:
        info("Run:  bash start.sh   or   python src/launcher.py")
    print()
    input("Press Enter to close...")


if __name__ == "__main__":
    main()