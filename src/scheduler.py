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


def create_task(run_time: str = "06:00") -> tuple:
    """
    Create (or overwrite) the scheduled task.
    run_time: "HH:MM" 24-hour, e.g. "06:00"
    Returns (success: bool, message: str)
    """
    if not is_windows():
        return False, "Task Scheduler is only available on Windows."

    python_exe = _python_exe()
    script     = SRC_DIR / "auto_sync.py"

    if not script.exists():
        return False, "auto_sync.py not found at: {}".format(script)

    xml = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>CS2 SkInvest - daily price sync</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T{}:00</StartBoundary>
      <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{}</Command>
      <Arguments>"{}"</Arguments>
      <WorkingDirectory>{}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>""".format(run_time, python_exe, script, ROOT_DIR)

    xml_path = ROOT_DIR / "data" / "_task_tmp.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")

    result = _schtasks("/create", "/tn", TASK_NAME, "/xml", str(xml_path), "/f")

    try:
        xml_path.unlink()
    except Exception:
        pass

    if result.returncode == 0:
        return True, "Task '{}' created -- runs daily at {}.".format(TASK_NAME, run_time)
    else:
        err = result.stderr.strip() or result.stdout.strip()
        return False, "Failed to create task: {}".format(err)


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