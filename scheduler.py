"""
scheduler.py
────────────
Windows Task Scheduler integration for CS2 SkInvest auto-sync.

Registers a daily task that:
  • Runs auto_sync.py at the configured time (default 06:00)
  • Also runs on next startup if the scheduled time was missed
    (computer was off — Task Scheduler "start missed tasks" flag)

Uses only built-in Windows tools (schtasks.exe) — no extra dependencies.
Safe to call on every app startup (query is read-only; create/delete only on demand).
"""

import subprocess
import os
import sys
import re
from pathlib import Path
from datetime import datetime

TASK_NAME = "CS2SkInvest_AutoSync"


def _app_dir() -> Path:
    return Path(__file__).resolve().parent


def _python_exe() -> str:
    """
    Return the pythonw.exe path (silent, no console window).
    Falls back to python.exe if pythonw is not found.
    """
    py = Path(sys.executable)
    pythonw = py.parent / "pythonw.exe"
    return str(pythonw) if pythonw.exists() else str(py)


def _schtasks(*args) -> subprocess.CompletedProcess:
    """Run schtasks.exe with given arguments. Returns CompletedProcess."""
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def is_windows() -> bool:
    return sys.platform == "win32"


def get_task_status() -> dict:
    """
    Query the scheduled task and return a status dict:
    {
        exists       : bool,
        enabled      : bool,
        last_run     : str | None,   # "YYYY-MM-DD HH:MM" or None
        next_run     : str | None,
        last_result  : str | None,   # e.g. "0" = success
        run_time     : str,          # "06:00" parsed from task, or ""
        error        : str | None,
    }
    """
    if not is_windows():
        return _non_windows_stub()

    result = _schtasks("/query", "/tn", TASK_NAME, "/fo", "LIST", "/v")

    if result.returncode != 0:
        # Task doesn't exist
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

    def _parse_dt(raw: str) -> str | None:
        if not raw or raw.upper() in ("N/A", "NEVER"):
            return None
        # schtasks returns locale-dependent format; try to normalise
        for fmt in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                continue
        return raw[:16]   # return raw if parsing fails

    # Extract scheduled run time from "Start Time" field
    raw_start = info.get("start time", "")
    run_time  = raw_start[:5] if len(raw_start) >= 5 else ""

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


def create_task(run_time: str = "06:00") -> tuple[bool, str]:
    """
    Create (or overwrite) the scheduled task.

    run_time : "HH:MM" 24-hour format, e.g. "06:00"

    Returns (success: bool, message: str)
    """
    if not is_windows():
        return False, "Task Scheduler is only available on Windows."

    app_dir    = _app_dir()
    python_exe = _python_exe()
    script     = app_dir / "auto_sync.py"

    if not script.exists():
        return False, f"auto_sync.py not found at: {script}"

    # Build the task XML so we can set RunOnlyIfIdle=false and
    # StartWhenAvailable=true (run missed task on next login/startup).
    # Using /xml pipe is the most reliable cross-locale approach.
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>CS2 SkInvest — daily price sync</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T{run_time}:00</StartBoundary>
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
      <Command>{python_exe}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{app_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    # Write XML to a temp file (Task Scheduler requires a file path for /xml)
    xml_path = app_dir / "data" / "_task_tmp.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")

    result = _schtasks(
        "/create",
        "/tn",  TASK_NAME,
        "/xml", str(xml_path),
        "/f",          # overwrite if already exists
    )

    # Clean up temp file
    try:
        xml_path.unlink()
    except Exception:
        pass

    if result.returncode == 0:
        return True, f"Task '{TASK_NAME}' created — runs daily at {run_time}."
    else:
        err = result.stderr.strip() or result.stdout.strip()
        return False, f"Failed to create task: {err}"


def delete_task() -> tuple[bool, str]:
    """Remove the scheduled task. Returns (success, message)."""
    if not is_windows():
        return False, "Task Scheduler is only available on Windows."

    result = _schtasks("/delete", "/tn", TASK_NAME, "/f")
    if result.returncode == 0:
        return True, f"Task '{TASK_NAME}' removed."
    err = result.stderr.strip() or result.stdout.strip()
    return False, f"Could not remove task: {err}"


def run_task_now() -> tuple[bool, str]:
    """Trigger the task to run immediately (for testing)."""
    if not is_windows():
        return False, "Task Scheduler is only available on Windows."
    result = _schtasks("/run", "/tn", TASK_NAME)
    if result.returncode == 0:
        return True, "Task triggered — check the log in a few minutes."
    err = result.stderr.strip() or result.stdout.strip()
    return False, f"Could not trigger task: {err}"


# ── Non-Windows stub ──────────────────────────────────────────────────────────

def _non_windows_stub() -> dict:
    return {
        "exists": False, "enabled": False,
        "last_run": None, "next_run": None,
        "last_result": None, "run_time": "",
        "error": "Task Scheduler is only available on Windows.",
    }