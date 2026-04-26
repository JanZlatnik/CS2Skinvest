@echo off
setlocal EnableDelayedExpansion
title CS2 SkInvest - Setup

echo.
echo =====================================================
echo    CS2 SkInvest - Setup
echo =====================================================
echo.

:: --- Try to find Python -------------------------------------------------------
set PYTHON=

python --version >nul 2>&1
if !errorlevel! == 0 (
    set PYTHON=python
    goto :python_found
)

python3 --version >nul 2>&1
if !errorlevel! == 0 (
    set PYTHON=python3
    goto :python_found
)

py --version >nul 2>&1
if !errorlevel! == 0 (
    set PYTHON=py
    goto :python_found
)

:: --- Python not found ---------------------------------------------------------
echo   Python was not found on this computer.
echo.

:: Try to install via winget (Windows 10 1709+ / Windows 11)
winget --version >nul 2>&1
if !errorlevel! == 0 (
    echo   Attempting to install Python via Windows Package Manager...
    echo   This may take a few minutes and requires internet access.
    echo.
    winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
    echo.

    :: Refresh PATH so the newly installed python is visible in this session
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "USERPATH=%%B"
    for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do set "SYSPATH=%%B"
    set "PATH=%SYSPATH%;%USERPATH%"

    python --version >nul 2>&1
    if !errorlevel! == 0 (
        set PYTHON=python
        echo   Python installed successfully!
        goto :python_found
    )
)

:: --- Cannot install automatically - show manual instructions ------------------
echo.
echo   =====================================================
echo    ACTION REQUIRED
echo   =====================================================
echo.
echo   Please install Python manually:
echo.
echo     1. Open this link in your browser:
echo        https://www.python.org/downloads/
echo.
echo     2. Click "Download Python 3.x.x"
echo.
echo     3. Run the installer.
echo        IMPORTANT: tick the checkbox that says
echo        "Add Python to PATH"  (bottom of the first screen)
echo.
echo     4. After installation finishes, re-run this setup.bat
echo.
pause
exit /b 1

:python_found
echo   Found Python:
%PYTHON% --version
echo.

:: --- Check minimum version (3.10) --------------------------------------------
:: The app uses X | Y type hint syntax which requires Python 3.10+
%PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if !errorlevel! neq 0 (
    echo   ERROR: Python 3.10 or later is required.
    echo   Your version is too old. Please install a newer Python from:
    echo   https://www.python.org/downloads/
    echo.
    echo   During install, tick "Add Python to PATH" at the bottom
    echo   of the first installer screen.
    echo.
    pause
    exit /b 1
)

echo   Python version OK.
echo.
echo   Running installer...
echo.

%PYTHON% src\installer.py

echo.
pause