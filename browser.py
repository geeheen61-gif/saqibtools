"""
Playwright-based browser launcher.
Launches a SEPARATE Chromium window with cookies injected directly via
Playwright's add_cookies(). Anti-theft JS is injected on every page to
block F12, right-click, devtools detection, and cookie theft.
"""

import threading
import json
import tempfile

# ─── Anti-theft JS injected into EVERY page ──────────────────────────────────
ANTI_THEFT_JS = """
(function(){
    // Block right-click context menu
    document.addEventListener('contextmenu', function(e){ e.preventDefault(); });

    // Block F12 / DevTools keyboard shortcuts
    document.addEventListener('keydown', function(e){
        if (e.key === 'F12') { e.preventDefault(); return false; }
        if (e.ctrlKey && e.shiftKey && ['I','J','C','K'].includes(e.key)){
            e.preventDefault(); return false;
        }
        if (e.ctrlKey && e.key === 'U'){ e.preventDefault(); return false; }
        if (e.ctrlKey && e.key === 'S'){ e.preventDefault(); return false; }
    });

    // Hide real cookies from JS
    Object.defineProperty(document, 'cookie', {
        get: function(){ return ''; },
        configurable: false,
        set: function(){ return true; }
    });

    // Silence the console
    ['log','warn','error','info','debug','table','dir','trace'].forEach(function(m){
        try { window.console[m] = function(){}; } catch(e){}
    });

    // Detect DevTools open via size difference → redirect to blank
    setInterval(function(){
        if (window.outerWidth  - window.innerWidth  > 160 ||
            window.outerHeight - window.innerHeight > 160){
            window.location.replace('about:blank');
        }
    }, 1000);
})();
"""

# ─── Playwright launch flags ──────────────────────────────────────────────────
LAUNCH_ARGS = [
    '--disable-extensions',
    '--disable-plugins',
    '--disable-translate',
    '--no-first-run',
    '--disable-sync',
    '--no-default-browser-check',
    '--disable-features=Translate',
    '--disable-save-password-bubble',
    '--start-maximized',
    '--disable-blink-features=AutomationControlled',
    '--no-existing-browser-frame',
]


def _format_cookies(raw_cookies):
    """Convert Cookie-Editor JSON export to Playwright format."""
    result = []
    for c in raw_cookies:
        if not c.get('name'):
            continue
        cookie = {
            'name'    : c['name'],
            'value'   : str(c.get('value', '')),
            'domain'  : c.get('domain', ''),
            'path'    : c.get('path', '/'),
            'secure'  : bool(c.get('secure', False)),
            'httpOnly': bool(c.get('httpOnly', False)),
        }
        ss = (c.get('sameSite') or '').lower()
        if ss in ('no_restriction', 'none'):
            cookie['sameSite'] = 'None'
            cookie['secure']   = True        # required for SameSite=None
        elif ss == 'strict':
            cookie['sameSite'] = 'Strict'
        else:
            cookie['sameSite'] = 'Lax'
        exp = c.get('expirationDate')
        if exp and not c.get('session'):
            cookie['expires'] = int(float(exp))
        result.append(cookie)
    return result


def _build_watermark_script(username: str) -> str:
    """Return an init-script that stamps the username onto every page."""
    # Use json.dumps to safely embed the username string into JS
    safe_name = json.dumps('🔒 ' + username)
    return f"""
        window.addEventListener('DOMContentLoaded', function(){{
            var wm = document.createElement('div');
            wm.id  = '__wm__';
            wm.innerText = {safe_name};
            wm.style.cssText = (
                'position:fixed;bottom:12px;right:12px;z-index:2147483647;'
                'background:rgba(0,0,0,.6);color:#fff;padding:5px 12px;'
                'border-radius:6px;font:bold 13px/1.5 Arial,sans-serif;'
                'pointer-events:none;user-select:none;letter-spacing:.5px;'
            );
            document.body.appendChild(wm);
        }});
    """


def _run_browser(url: str, cookies_json: str, username: str):
    """Actual Playwright logic – runs inside a daemon thread."""
    try:
        from playwright.sync_api import sync_playwright

        raw     = json.loads(cookies_json)
        cookies = _format_cookies(raw)
        print(f'[browser] Launching  user={username!r}  cookies={len(cookies)}  url={url}')

        user_data_dir = tempfile.mkdtemp(prefix='pw_profile_')

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                args=LAUNCH_ARGS,
                ignore_default_args=['--enable-automation'],
                no_viewport=True,
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
                accept_downloads=False,
            )

            # Inject anti-theft + watermark on every new page / navigation
            context.add_init_script(ANTI_THEFT_JS)
            context.add_init_script(_build_watermark_script(username))

            # Inject cookies BEFORE navigating
            context.add_cookies(cookies)

            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=60_000)
            print(f'[browser] Page loaded: {url}')

            # Block until the user closes the window
            page.wait_for_event('close', timeout=0)
            context.close()

        import shutil
        try:
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass

        print(f'[browser] Session ended  user={username!r}')

    except ImportError:
        print('[browser] ERROR: Playwright not installed.\n'
              '          Run: pip install playwright && playwright install chromium')
    except Exception as e:
        import traceback
        print(f'[browser] ERROR  user={username!r}  → {e}')
        traceback.print_exc()


def open_tool(url: str, cookies_json: str, username: str) -> dict:
    """
    Spawn a daemon thread that opens the browser – non-blocking for Flask.
    Returns immediately with {ok, msg} or {ok, error}.
    """
    try:
        import os
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            executable = p.chromium.executable_path
            if not executable or not os.path.exists(executable):
                return {
                    'ok': False,
                    'error': (
                        f'Chromium executable not found at: {executable}\n'
                        'Please run: python -m playwright install chromium'
                    )
                }
    except ImportError as e:
        return {
            'ok'   : False,
            'error': (
                f'Playwright is not installed or import failed ({e}).\n'
                'Run: pip install playwright && playwright install chromium'
            ),
        }
    except Exception as e:
        return {
            'ok': False,
            'error': f'Failed to verify Playwright installation: {e}'
        }

    t = threading.Thread(
        target=_run_browser,
        args=(url, cookies_json, username),
        daemon=True,
    )
    t.start()
    return {'ok': True, 'msg': 'Browser window opened on your desktop.'}
