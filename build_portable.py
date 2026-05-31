"""
Saqib Tools - Build Portable Package
====================================
Run this ONCE on your Windows PC to create a ZIP that users can
download, extract, and run like any normal Windows software.

Usage:  python build_portable.py
Output: saqib_tools_portable.zip  (~250MB)
"""
import os, sys, json, shutil, subprocess, zipfile, tempfile, site, glob, importlib, struct

RENDER_URL = "https://saqibtools.onrender.com"  # <--- CHANGE THIS
BUILD_DIR  = os.path.join(tempfile.gettempdir(), "st_portable_build")
OUTPUT_ZIP = os.path.join(os.path.dirname(__file__), "saqib_tools_portable.zip")

# ─── STEP 1: Ensure Playwright + Chromium are installed ────────────────
print("[1/5] Installing Playwright + Chromium (if needed)...")
try:
    import playwright
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
    importlib.invalidate_caches()

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    exe_path = p.chromium.executable_path
    if not exe_path or not os.path.isfile(exe_path):
        print("  -> Downloading Chromium (~300MB, first time only)...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        with sync_playwright() as p2:
            exe_path = p2.chromium.executable_path
print(f"  -> Chromium at: {exe_path}")

pw_browsers = os.path.dirname(os.path.dirname(os.path.dirname(exe_path)))

# ─── STEP 2: Clean build dir ──────────────────────────────────────────
print("[2/5] Setting up build directory...")
if os.path.isdir(BUILD_DIR):
    shutil.rmtree(BUILD_DIR)
os.makedirs(BUILD_DIR, exist_ok=True)

# ─── STEP 3: Copy Python embedded + Playwright + Chromium ──────────────
print("[3/5] Copying Python + Playwright + Chromium...")

EMBEDDED_PYTHON = os.path.join(BUILD_DIR, "python")
os.makedirs(EMBEDDED_PYTHON, exist_ok=True)

py_root = os.path.dirname(sys.executable)

for f in ["python.exe", "pythonw.exe"]:
    src = os.path.join(py_root, f)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(EMBEDDED_PYTHON, f))

for f in os.listdir(py_root):
    if f.endswith(".dll"):
        shutil.copy2(os.path.join(py_root, f), os.path.join(EMBEDDED_PYTHON, f))

py_ziplib = [f for f in os.listdir(py_root) if f.startswith("python3") and f.endswith(".zip")]
if py_ziplib:
    shutil.copy2(os.path.join(py_root, py_ziplib[0]), os.path.join(EMBEDDED_PYTHON, py_ziplib[0]))
else:
    shutil.copytree(os.path.join(py_root, "Lib"), os.path.join(EMBEDDED_PYTHON, "Lib"),
                    ignore=shutil.ignore_patterns("test", "turtledemo", "site-packages"))

# Copy ALL .pyd and .dll from Python's DLLs folder (ctypes, socket, ssl, tkinter, etc.)
dlls_src = os.path.join(py_root, "DLLs")
if os.path.isdir(dlls_src):
    dlls_dst = os.path.join(EMBEDDED_PYTHON, "DLLs")
    os.makedirs(dlls_dst, exist_ok=True)
    for f in os.listdir(dlls_src):
        if f.endswith(".pyd") or f.endswith(".dll"):
            shutil.copy2(os.path.join(dlls_src, f), os.path.join(dlls_dst, f))

# Copy Tcl/Tk script files (needed for tkinter splash screen)
tcl_src = os.path.join(py_root, "tcl")
if os.path.isdir(tcl_src):
    tcl_dst = os.path.join(EMBEDDED_PYTHON, "lib")
    for sub in ["tcl8.6", "tk8.6"]:
        src = os.path.join(tcl_src, sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tcl_dst, sub), ignore=shutil.ignore_patterns("__pycache__"))
    # Also copy to python/tcl/ (some builds look here)
    tcl_alt = os.path.join(EMBEDDED_PYTHON, "tcl")
    for sub in ["tcl8.6", "tk8.6"]:
        src = os.path.join(tcl_src, sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tcl_alt, sub), ignore=shutil.ignore_patterns("__pycache__"))

# Playwright deps
site_packages = [p for p in site.getsitepackages() if p.endswith("site-packages")][0]
for pkg in ["playwright", "greenlet", "pyee"]:
    src = os.path.join(site_packages, pkg)
    dst = os.path.join(EMBEDDED_PYTHON, "Lib", "site-packages", pkg)
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))

# Chromium
chromium_folders = glob.glob(os.path.join(pw_browsers, "chromium-*"))
if chromium_folders:
    cf = chromium_folders[0]
    print(f"  -> Copying Chromium: {os.path.basename(cf)}")
    shutil.copytree(cf, os.path.join(BUILD_DIR, os.path.basename(cf)))

# ─── STEP 4: Create launcher with attractive splash UI ─────────────────
print("[4/5] Creating launcher with attractive UI...")

LAUNCHER_CODE = r'''import sys, os, traceback

ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")

def log_error(msg):
    try:
        with open(ERROR_LOG, "a") as f:
            f.write(f"{msg}\n")
    except:
        pass

try:
    import subprocess, shutil, ctypes, json, urllib.request
    import tkinter as tk
    from tkinter import font as tkfont
except Exception as e:
    log_error(f"Import error: {e}")
    raise

SERVER = "SERVER_URL_PLACEHOLDER"
BASE = os.path.dirname(os.path.abspath(__file__))

def msgbox(text, title="Saqib Tools", flags=0x10):
    try: ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except:
        log_error(f"{title}: {text}")

# ── Attractive splash screen ──────────────────────────────────────────────
class SplashScreen:
    def __init__(self):
        self.win = tk.Tk()
        self.win.title("Saqib Tools")
        self.win.overrideredirect(True)
        self.win.configure(bg="#0d0d1a")

        w, h = 420, 260
        cx = (self.win.winfo_screenwidth() - w) // 2
        cy = (self.win.winfo_screenheight() - h) // 2
        self.win.geometry(f"{w}x{h}+{cx}+{cy}")
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.95)

        self.frame = tk.Frame(self.win, bg="#1a1a2e", bd=0, highlightbackground="#e94560", highlightthickness=2)
        self.frame.pack(fill="both", expand=True, padx=4, pady=4)

        tk.Label(self.frame, text="Saqib Tools", fg="#e94560", bg="#1a1a2e",
                 font=("Segoe UI", 24, "bold")).pack(pady=(40, 6))

        tk.Label(self.frame, text="Setting up your session...", fg="#8888aa",
                 bg="#1a1a2e", font=("Segoe UI", 11)).pack()

        self.dots_label = tk.Label(self.frame, text="", fg="#4fc3f7",
                                   bg="#1a1a2e", font=("Segoe UI", 20))
        self.dots_label.pack(pady=(16, 4))
        self.dots = 0
        self.update_dots()

        tk.Label(self.frame, text="Please wait while we set things up",
                 fg="#555577", bg="#1a1a2e", font=("Segoe UI", 8)).pack(side="bottom", pady=14)

    def update_dots(self):
        self.dots = (self.dots + 1) % 4
        self.dots_label.config(text="." * self.dots)
        self.win.after(400, self.update_dots)

    def close(self):
        self.win.destroy()

def main():
    try: ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except: pass

    try:
        splash = SplashScreen()
        splash.win.update()

        # Import bundled Playwright
        sp = os.path.join(BASE, "python", "Lib", "site-packages")
        if os.path.isdir(sp):
            sys.path.insert(0, sp)

        from playwright.sync_api import sync_playwright

        # Find Chromium
        cf_dirs = [d for d in os.listdir(BASE) if d.startswith("chromium-")]
        if not cf_dirs:
            splash.close()
            msgbox("Chromium not found. Re-download the package.", "Error")
            return
        chromium_path = os.path.join(BASE, cf_dirs[0], "chrome-win64", "chrome.exe")
        if not os.path.isfile(chromium_path):
            chromium_path = os.path.join(BASE, cf_dirs[0], "chrome-win", "chrome.exe")
        if not os.path.isfile(chromium_path):
            chromium_path = os.path.join(BASE, cf_dirs[0], "chrome", "chrome.exe")
        if not os.path.isfile(chromium_path):
            splash.close()
            msgbox("Chrome.exe not found in package.", "Error")
            log_error(f"Tried: {chromium_path}")
            return

        profile_dir = os.path.join(BASE, "profile")
        os.makedirs(profile_dir, exist_ok=True)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                profile_dir, headless=False,
                executable_path=chromium_path,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-gpu"],
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )
            splash.close()
            page = context.new_page()
            page.goto(SERVER, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_event("close", timeout=0)
            context.close()
    except Exception as e:
        log_error(traceback.format_exc())
        try: splash.close()
        except: pass
        msgbox(f"Error:\n{e}", "Error")

if __name__ == "__main__":
    main()
'''

launcher_path = os.path.join(BUILD_DIR, "launcher_portable.py")
with open(launcher_path, "w", encoding="utf-8") as f:
    f.write(LAUNCHER_CODE.replace("SERVER_URL_PLACEHOLDER", RENDER_URL))

# BAT launcher (zero console - uses pythonw.exe)
BAT = """@echo off
title Saqib Tools
start "" "%~dp0python\\pythonw.exe" "%~dp0launcher_portable.py"
exit
"""
with open(os.path.join(BUILD_DIR, "Launch_Saqib_Tools.bat"), "w", encoding="utf-8") as f:
    f.write(BAT.strip())

# README
README = f"""
═══ Saqib Tools - Portable Package ═══

HOW TO USE:
1. Extract this ZIP to any folder on your Windows PC
2. Double-click "Launch_Saqib_Tools.bat"
3. A nice splash screen appears, then Chromium opens with Saqib Tools ready

NO terminal, NO Python install, NO commands.
Everything is included. Just extract and run.
"""
with open(os.path.join(BUILD_DIR, "README.txt"), "w", encoding="utf-8") as f:
    f.write(README.strip())

# ─── STEP 5: ZIP it all ───────────────────────────────────────────────
print("[5/5] Creating ZIP...")

if os.path.isfile(OUTPUT_ZIP):
    os.remove(OUTPUT_ZIP)

with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
    for root, dirs, files in os.walk(BUILD_DIR):
        for f in files:
            fp = os.path.join(root, f)
            arcname = os.path.relpath(fp, BUILD_DIR)
            print(f"  Adding: {arcname}")
            zf.write(fp, arcname)
        dirs[:] = [d for d in dirs if d != "__pycache__"]

shutil.rmtree(BUILD_DIR, ignore_errors=True)

size_mb = os.path.getsize(OUTPUT_ZIP) / (1024 * 1024)
print(f"\nDone! Package created: {OUTPUT_ZIP} ({size_mb:.1f} MB)")
print(f"\nNext steps:")
print(f"1. Edit RENDER_URL in build_portable.py (line 12)")
print(f"2. Run: python build_portable.py")
print(f"3. Upload ZIP to Google Drive")
print(f"4. Share with users - they just extract and run")
