"""
scheduler.py
------------
Windows Task Scheduler integration for CS2 SkInvest auto-sync.

Registers a daily task that runs src/auto_sync.py at the configured time.
The working directory is set to ROOT_DIR so all relative data/ paths resolve.
"""

import subprocess
import os
import sys
import re
from pathlib import Path
from datetime import datetime

SRC_DIR  = Path(__file__).resolve().parent   # .../repo/src
ROOT_DIR = SRC_DIR.parent                    # .../repo

TASK_NAME = "CS2SkInvest_AutoSync"

# Supported trigger modes
TRIGGER_MODES = {
    "daily":  "Daily at set time (+ run on startup if missed)",
    "logon":  "At every startup / login",
    "hourly": "Every hour",
}


def _python_exe() -> str:
    """Return pythonw.exe (silent) or fall back to python.exe."""
    py      = Path(sys.executable)
    pythonw = py.parent / "pythonw.exe"
    return str(pythonw) if pythonw.exists() else str(py)


def _schtasks(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


# ── Public API ----------------------------------------------------------------

def is_windows() -> bool:
    return sys.platform == "win32"


def get_task_status() -> dict:
    """
    Query the scheduled task.
    Returns dict: exists, enabled, last_run, next_run, last_result, run_time, error
    """
    if not is_windows():
        return _non_windows_stub()

    result = _schtasks("/query", "/tn", TASK_NAME, "/fo", "LIST", "/v")

    if result.returncode != 0:
        return {
            "exists": False, "enabled": False,
            "last_run": None, "next_run": None,
            "last_result": None, "run_time": "",
            "error": None,
        }

    lines = result.stdout.splitlines()
    info  = {}
    for line in lines:
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip().lower()] = v.strip()

    def _parse_dt(raw: str):
        if not raw or raw.upper() in ("N/A", "NEVER"):
            return None
        for fmt in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                continue
        return raw[:16]

    raw_start  = info.get("start time", "")
    run_time   = raw_start[:5] if len(raw_start) >= 5 else ""
    status_raw = info.get("status", info.get("scheduled task state", "")).lower()
    enabled    = "disabled" not in status_raw

    return {
        "exists":      True,
        "enabled":     enabled,
        "last_run":    _parse_dt(info.get("last run time", "")),
        "next_run":    _parse_dt(info.get("next run time", "")),
        "last_result": info.get("last result", None),
        "run_time":    run_time,
        "error":       None,
    }


def _make_trigger_xml(trigger_mode: str, run_time: str = "06:00") -> str:
    """
    Return the <Triggers> XML block for the given trigger_mode.

    trigger_mode:
        "daily"  – CalendarTrigger at run_time each day
                   (StartWhenAvailable in Settings means it also runs on the
                    next startup if the PC was off at the scheduled time)
        "logon"  – LogonTrigger: runs every time the user logs in / PC starts
        "hourly" – TimeTrigger with PT1H repetition; auto_sync.py skips the
                   run if prices were already fetched today
    """
    if trigger_mode == "logon":
        return """\
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>"""

    if trigger_mode == "hourly":
        return """\
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2024-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>"""

    # default: "daily"
    return """\
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T{}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>""".format(run_time)


def create_task(run_time: str = "06:00", trigger_mode: str = "daily") -> tuple:
    """
    Create (or overwrite) the scheduled task using schtasks CLI flags.

    The CLI approach (/sc ONLOGON, /sc DAILY, /sc HOURLY) registers the task
    for the current interactive user without requiring administrator rights.
    XML-based registration requires elevation as soon as a <Principal> block
    is specified — so we avoid XML entirely here.

    auto_sync.py resolves all paths via Path(__file__), so WorkingDirectory
    does not need to be set explicitly.

    Parameters
    ----------
    run_time     : "HH:MM" 24-hour — used only when trigger_mode == "daily"
    trigger_mode : "daily" | "logon" | "hourly"

    Returns (success: bool, message: str)
    """
    if not is_windows():
        return False, "Task Scheduler is only available on Windows."

    if trigger_mode not in TRIGGER_MODES:
        trigger_mode = "daily"

    python_exe = _python_exe()
    script     = SRC_DIR / "auto_sync.py"

    if not script.exists():
        return False, "auto_sync.py not found at: {}".format(script)

    # /tr value: quoted exe + quoted script path
    tr = '"{}" "{}"'.format(python_exe, script)

    # First delete any existing task so /f can recreate it cleanly
    _schtasks("/delete", "/tn", TASK_NAME, "/f")

    if trigger_mode == "logon":
        result = _schtasks(
            "/create", "/tn", TASK_NAME,
            "/tr", tr,
            "/sc", "ONLOGON",
            "/f",
        )
    elif trigger_mode == "hourly":
        result = _schtasks(
            "/create", "/tn", TASK_NAME,
            "/tr", tr,
            "/sc", "HOURLY",
            "/mo", "1",
            "/f",
        )
    else:  # daily
        result = _schtasks(
            "/create", "/tn", TASK_NAME,
            "/tr", tr,
            "/sc", "DAILY",
            "/st", run_time,
            "/ri", "0",      # no repetition interval
            "/f",
        )

    if result.returncode == 0:
        # Apply advanced settings that schtasks CLI cannot set directly.
        # PowerShell's Set-ScheduledTask works for per-user tasks without admin.
        _apply_advanced_settings(trigger_mode)

        labels = {
            "daily":  "daily at {}".format(run_time),
            "logon":  "at every startup / login",
            "hourly": "every hour",
        }
        return True, "Task '{}' created — runs {}.".format(
            TASK_NAME, labels.get(trigger_mode, trigger_mode)
        )
    else:
        err = result.stderr.strip() or result.stdout.strip()
        return False, "Failed to create task: {}".format(err)


def _apply_advanced_settings(trigger_mode: str) -> None:
    """
    Use PowerShell to patch the task after schtasks CLI creation:
      • RestartOnFailure  — retry every 10 min, up to 3 times
      • ExecutionTimeLimit — max 2 hours
      • Delay             — 5 min startup delay (logon trigger only)

    This is best-effort: if PowerShell is unavailable or the call fails,
    the task still works; it just won't have the retry/delay behaviour.
    """
    # Build the PowerShell one-liner in two parts: settings + optional delay
    ps_settings = (
        "$s = New-ScheduledTaskSettingsSet"
        " -MultipleInstances IgnoreNew"
        " -RestartCount 3"
        " -RestartInterval (New-TimeSpan -Minutes 10)"
        " -ExecutionTimeLimit (New-TimeSpan -Hours 2);"
        " Set-ScheduledTask -TaskName '{tn}' -Settings $s"
    ).format(tn=TASK_NAME)

    if trigger_mode == "logon":
        # Patch the first trigger's Delay property to PT5M (5 minutes).
        # This gives the network time to come up before auto_sync.py runs.
        ps_delay = (
            " $t = Get-ScheduledTask -TaskName '{tn}';"
            " $trig = $t.Triggers[0];"
            " $trig.Delay = 'PT5M';"
            " $t | Set-ScheduledTask"
        ).format(tn=TASK_NAME)
        ps_script = ps_settings + ";" + ps_delay
    else:
        ps_script = ps_settings

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass  # silently ignore — basic task was already created successfully


def delete_task() -> tuple:
    """Remove the scheduled task. Returns (success, message)."""
    if not is_windows():
        return False, "Task Scheduler is only available on Windows."
    result = _schtasks("/delete", "/tn", TASK_NAME, "/f")
    if result.returncode == 0:
        return True, "Task '{}' removed.".format(TASK_NAME)
    err = result.stderr.strip() or result.stdout.strip()
    return False, "Could not remove task: {}".format(err)


def run_task_now() -> tuple:
    """Trigger the task to run immediately (for testing)."""
    if not is_windows():
        return False, "Task Scheduler is only available on Windows."
    result = _schtasks("/run", "/tn", TASK_NAME)
    if result.returncode == 0:
        return True, "Task triggered -- check the log in a few minutes."
    err = result.stderr.strip() or result.stdout.strip()
    return False, "Could not trigger task: {}".format(err)


# ── Helpers used by sync_history.py ------------------------------------------

def _app_dir() -> Path:
    """Returns ROOT_DIR for log path resolution in sync_history.py."""
    return ROOT_DIR


# ── Non-Windows stub ----------------------------------------------------------

def _non_windows_stub() -> dict:
    return {
        "exists": False, "enabled": False,
        "last_run": None, "next_run": None,
        "last_result": None, "run_time": "",
        "error": "Task Scheduler is only available on Windows.",
    }