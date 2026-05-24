@echo off
setlocal enabledelayedexpansion
title AccountHub - Easy Local Setup & Launch
cd /d "%~dp0"

echo ====================================================================
echo             AccountHub - Setup ^& Launch Companion
echo ====================================================================
echo.
echo This script will verify your Python environment, install required
echo libraries, configure the Playwright browser, and start the site.
echo.

:: ── 1. Verify Python ─────────────────────────────────────────────────
echo [*] Step 1/5 - Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python is not installed or not added to PATH.
    echo [*] Attempting to install Python 3.12 automatically...
    winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    if !errorlevel! neq 0 (
        echo [x] Automatic Python installation failed.
        echo [!] Please download Python manually from:
        echo     https://www.python.org/downloads/
        echo     Make sure to check "Add Python to PATH" during installation.
        echo.
        echo Press any key to exit...
        pause >nul
        exit /b 1
    )
    echo [OK] Python installed. Please restart this script.
    pause
    exit /b 0
)
echo [OK] Python is installed.

:: ── 2. Install pip packages ──────────────────────────────────────────
echo.
echo [*] Step 2/5 - Installing required Python libraries...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [x] Failed to install Python dependencies.
    echo     Check your internet connection and try again.
    pause
    exit /b 1
)
echo [OK] Python dependencies installed.

:: ── 3. VC++ Runtime ──────────────────────────────────────────────────
echo.
echo [*] Step 3/5 - Checking Microsoft Visual C++ Runtime...
python -c "import ctypes; ctypes.CDLL('msvcp140.dll')" >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Missing VC++ Redistributable (msvcp140.dll).
    winget install --id Microsoft.VCRedist.2015+.x64 --silent --accept-source-agreements --accept-package-agreements >nul 2>&1
    if !errorlevel! neq 0 (
        echo [*] Downloading VC++ Redistributable from Microsoft...
        curl -sL -o vc_redist.x64.exe https://aka.ms/vs/17/release/vc_redist.x64.exe
        if exist vc_redist.x64.exe (
            echo [*] Running installer...
            start /wait vc_redist.x64.exe /passive /norestart
            del vc_redist.x64.exe
        ) else (
            echo [x] Failed to download. Please install manually:
            echo     https://aka.ms/vs/17/release/vc_redist.x64.exe
        )
    )
) else (
    echo [OK] VC++ Runtime present.
)

:: ── 4. Install Playwright Chromium ────────────────────────────────────
echo.
echo [*] Step 4/5 - Installing Playwright Chromium browser...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo [x] Failed to install Playwright Chromium.
    echo     Check internet connection and try: python -m playwright install chromium
    pause
    exit /b 1
)
echo [OK] Playwright Chromium is ready.

:: ── 5. Launch server ─────────────────────────────────────────────────
echo.
echo ====================================================================
echo   All done! Starting AccountHub server...
echo ====================================================================
echo.
echo   Web App:     http://localhost:5000
echo   Database:    Neon cloud (shared across all devices)
echo.
echo   Keep this window open while using the app.
echo   Close it with CTRL+C to stop the server.
echo ====================================================================
echo.
echo [*] Starting Flask (web UI + local helper)...
python app.py

:: ── If server exits (e.g. error), pause so user can see the message ───
echo.
echo [x] Server stopped unexpectedly.
pause