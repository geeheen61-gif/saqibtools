@echo off
setlocal enabledelayedexpansion
title AccountHub - Easy Local Setup & Launch

echo ====================================================================
echo             AccountHub - Setup & Launch Companion
echo ====================================================================
echo.
echo This script will verify your Python environment, install required
echo libraries, configure the Playwright browser, and start the site.
echo.

:: 1. Verify Python Installation
echo [*] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python is not installed or not added to your system PATH.
    echo [*] Attempting to install Python 3.12 automatically via winget...
    winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    if !errorlevel! neq 0 (
        echo [x] Automatic Python installation failed.
        echo [!] Please download and install Python manually from:
        echo     https://www.python.org/downloads/
        echo     IMPORTANT: Make sure to check the box "Add Python to PATH" during installation.
        echo.
        pause
        exit /b 1
    )
    echo [OK] Python has been successfully installed!
    echo [!] Please restart this script (double-click again) to continue setup.
    pause
    exit /b 0
)
echo [OK] Python is installed.

:: 2. Install/Upgrade Pip and Requirements
echo.
echo [*] Installing required Python libraries from requirements.txt...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [x] Failed to install Python library dependencies.
    pause
    exit /b 1
)
echo [OK] Python dependencies installed successfully.

:: 3. Check and Resolve Missing C++ Runtime DLLs (greenlet requirement)
echo.
echo [*] Checking for Microsoft Visual C++ Runtime (msvcp140.dll)...
python -c "import ctypes; ctypes.CDLL('msvcp140.dll')" >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Missing VC++ Redistributable (msvcp140.dll is required for browser automation).
    echo [*] Attempting to install VC++ Redistributable...
    winget install --id Microsoft.VCRedist.2015+.x64 --silent --accept-source-agreements --accept-package-agreements
    if !errorlevel! neq 0 (
        echo [*] Winget failed. Downloading official installer from Microsoft...
        curl -L -o vc_redist.x64.exe https://aka.ms/vs/17/release/vc_redist.x64.exe
        if exist vc_redist.x64.exe (
            echo [*] Running VC++ Redistributable installer...
            vc_redist.x64.exe /passive /norestart
            del vc_redist.x64.exe
            echo [OK] Installer complete.
        ) else (
            echo [x] Failed to download installer. Please install VC++ Redistributable 2015-2022 manually.
        )
    ) else (
        echo [OK] Installed VC++ Redistributable.
    )
) else (
    echo [OK] VC++ Runtime is present.
)

:: 4. Verify & Install Playwright Chromium
echo.
echo [*] Checking Playwright Chromium browser binary...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo [x] Failed to install Playwright Chromium.
    pause
    exit /b 1
)
echo [OK] Playwright Chromium is ready.

:: 5. Launch local web server
echo.
echo ====================================================================
echo   Success! Launching AccountHub Local Flask Server...
echo ====================================================================
echo.
echo   * Web App:     http://localhost:5000
echo   * Local Helper: http://localhost:5000/local-launch
echo     (Used by the Render cloud app to open Chromium on your PC)
echo   * Shared DB:   Connecting to Neon cloud database...
echo.
echo   Press CTRL+C in this window to stop the server at any time.
echo ====================================================================
echo.
echo [*] Starting Flask server (web UI + local helper)...
python app.py
pause
