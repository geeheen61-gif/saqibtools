"""
Saqib Tools - Silent Launcher
Runs with pythonw.exe (no console). Users see NOTHING except the browser.
Error messages appear as Windows message boxes.
"""
import sys, json, urllib.request, subprocess, os, tempfile, shutil, ctypes, importlib

BASE = os.path.dirname(os.path.abspath(__file__))
# Use bundled Playwright if running from portable package
sp = os.path.join(BASE, "python", "Lib", "site-packages")
if os.path.isdir(sp):
    sys.path.insert(0, sp)

MB_OK = 0
MB_ICONERROR = 16
MB_ICONINFO = 64

def msgbox(text, title="Saqib Tools", flags=MB_OK | MB_ICONERROR):
    try: ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except: pass

def main():
    # Hide console window (safety)
    try: ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except: pass

    if len(sys.argv) < 2:
        msgbox("Missing launch token.", "Error")
        return 1

    token = None
    server = None
    for i, arg in enumerate(sys.argv):
        if arg == '--token' and i + 1 < len(sys.argv): token = sys.argv[i + 1]
        elif arg == '--server' and i + 1 < len(sys.argv): server = sys.argv[i + 1]

    if not token or not server:
        msgbox("Missing --token or --server arguments", "Error")
        return 1

    # Contact server to claim launch
    try:
        req = urllib.request.Request(f'{server}/api/claim-launch/{token}')
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        msgbox(f"Failed to connect to server:\n{e}\n\nCheck your internet connection.", "Connection Error")
        return 1

    if not data.get('ok'):
        msgbox(data.get('error', 'Launch failed'), "Error")
        return 1

    tool_name = data.get('tool_name', 'Tool')
    url = data['url']
    cookies_raw = json.loads(data['cookies'])
    username = data['username']

    # Auto-install Playwright if needed (silent)
    try:
        import playwright
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'playwright'],
                       capture_output=True, timeout=120)
        importlib.invalidate_caches()

    # Auto-install Chromium if needed (silent, may take 1-2 min)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if not exe or not os.path.isfile(exe):
                subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'],
                               capture_output=True, timeout=300)
    except:
        subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'],
                       capture_output=True, timeout=300)

    # Format cookies
    def root_domain(url):
        from urllib.parse import urlparse
        host = urlparse(url).netloc.split('@')[-1].split(':')[0]
        parts = host.split('.')
        return '.'.join(parts[1:]) if len(parts) >= 3 else host

    root = root_domain(url)
    cookies = []
    for c in cookies_raw:
        if not c.get('name'): continue
        domain = c.get('domain', '') or ''
        if not domain or domain in ('null', 'undefined'):
            domain = '.' + root if root else ''
        if not domain: continue
        if not domain.startswith('.') and not c.get('hostOnly', False):
            domain = '.' + domain
        cookie = {'name': c['name'], 'value': str(c.get('value', '')),
                  'domain': domain, 'path': c.get('path', '/') or '/',
                  'secure': bool(c.get('secure', False)),
                  'httpOnly': bool(c.get('httpOnly', False))}
        ss = (c.get('sameSite') or '').lower()
        if ss in ('no_restriction', 'none'):
            cookie['sameSite'] = 'None'; cookie['secure'] = True
        elif ss == 'strict': cookie['sameSite'] = 'Strict'
        elif ss == 'lax': cookie['sameSite'] = 'Lax'
        exp = c.get('expirationDate')
        if exp and not c.get('session'):
            cookie['expires'] = int(float(exp))
        cookies.append(cookie)

    # Open Chromium
    user_data_dir = tempfile.mkdtemp(prefix='st_')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            is_semrush = 'semrush.com' in url
            tool_args = ['--disable-blink-features=AutomationControlled', '--no-sandbox']
            if is_semrush:
                tool_args.append('--kiosk')
            else:
                tool_args.append('--start-maximized')
            context = p.chromium.launch_persistent_context(
                user_data_dir, headless=False,
                args=tool_args,
                ignore_default_args=['--enable-automation'],
                no_viewport=True,
            )
            # Anti-theft protections (NOTA: document.cookie is NOT blocked — the target site's JS needs to read it for auth)
            context.add_init_script("""
                document.addEventListener('contextmenu',function(e){e.preventDefault()});
                document.addEventListener('keydown',function(e){
                    if(e.key==='F12'||(e.ctrlKey&&e.shiftKey&&['I','J','C','K'].includes(e.key))||(e.ctrlKey&&['U','S'].includes(e.key))){e.preventDefault();return false}
                });
                ['log','warn','error','info','debug'].forEach(function(m){try{window.console[m]=function(){}}catch(e){}});
            """)

            page = context.new_page()
            if is_semrush:
                page.add_init_script("""
                    (function(){
                        var el = document.evaluate(
                            '/html/body/div[1]/div[3]/div/header/div/div[3]',
                            document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
                        ).singleNodeValue;
                        if(el) el.style.display='none';
                    })();
                """)
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            context.add_cookies(cookies)
            page.reload(wait_until='domcontentloaded', timeout=60000)
            page.wait_for_event('close', timeout=0)
            context.close()
    except Exception as e:
        msgbox(f"Error launching browser:\n{e}", "Error")
        return 1
    finally:
        try: shutil.rmtree(user_data_dir, ignore_errors=True)
        except: pass

    return 0

if __name__ == '__main__':
    sys.exit(main())
