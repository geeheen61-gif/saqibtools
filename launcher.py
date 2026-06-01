"""
Saqib Tools Launcher — standalone script for the user's PC.
Usage: python launcher.py --token <token> --server <server_url>
"""
import sys, json, urllib.request, subprocess, os, tempfile, shutil, importlib

try:
    import ctypes
    def msgbox(text, title="Saqib Tools"):
        try: ctypes.windll.user32.MessageBoxW(0, text, title, 0 | 16)
        except: pass
except:
    def msgbox(text, title="Saqib Tools"):
        print(f"[{title}] {text}")


def root_domain(url):
    from urllib.parse import urlparse
    host = urlparse(url).netloc.split('@')[-1].split(':')[0]
    parts = host.split('.')
    return '.'.join(parts[1:]) if len(parts) >= 3 else host


def format_cookies(raw, url):
    root = root_domain(url)
    result = []
    for c in raw:
        if not c.get('name'):
            continue
        domain = c.get('domain', '') or ''
        if not domain or domain in ('null', 'undefined'):
            domain = '.' + root if root else ''
        if not domain:
            continue
        if not domain.startswith('.') and not c.get('hostOnly', False):
            domain = '.' + domain
        cookie = {
            'name': c['name'], 'value': str(c.get('value', '')),
            'domain': domain, 'path': c.get('path', '/') or '/',
            'secure': bool(c.get('secure', False)),
            'httpOnly': bool(c.get('httpOnly', False)),
        }
        ss = (c.get('sameSite') or '').lower()
        if ss in ('no_restriction', 'none'):
            cookie['sameSite'] = 'None'
            cookie['secure'] = True
        elif ss == 'strict':
            cookie['sameSite'] = 'Strict'
        elif ss == 'lax':
            cookie['sameSite'] = 'Lax'
        exp = c.get('expirationDate')
        if exp and not c.get('session'):
            cookie['expires'] = int(float(exp))
        result.append(cookie)
    return result


def ensure_playwright():
    try:
        import playwright
    except ImportError:
        print('[*] Installing Playwright package...')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'playwright'], timeout=120)
        importlib.invalidate_caches()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if not exe or not os.path.isfile(exe):
                raise FileNotFoundError()
    except:
        print('[*] Installing Chromium browser (1-2 min)...')
        subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], timeout=300)


ANTI_THEFT_JS = """
(function(){
    document.addEventListener('contextmenu',function(e){e.preventDefault()});
    document.addEventListener('keydown',function(e){
        var k=e.key.toUpperCase();
        if(k==='F12'||(e.ctrlKey&&(k==='U'||k==='S'||k==='C'))||(e.ctrlKey&&e.shiftKey&&['I','J','C','K'].includes(k))){e.preventDefault();return false}
    });
    ['log','warn','error','info','debug','table','dir','trace'].forEach(function(m){try{window.console[m]=function(){}}catch(e){}});
})();
"""

SEMRUSH_JS = """
(function(){
    var sel='#srf-header > div > div.srf-header__end,#srf-header__end';
    var s=document.createElement('style');
    s.textContent=sel+'{display:none!important}';
    if(document.head)document.head.appendChild(s);
    function h(){var e=document.querySelectorAll(sel);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}
    h();setInterval(h,300);
})();
"""


def main():
    if len(sys.argv) < 2:
        print('Usage: python launcher.py --token <token> --server <url>')
        return 1

    token = None
    server = None
    for i, arg in enumerate(sys.argv):
        if arg == '--token' and i + 1 < len(sys.argv):
            token = sys.argv[i + 1]
        elif arg == '--server' and i + 1 < len(sys.argv):
            server = sys.argv[i + 1]

    if not token or not server:
        msgbox('Missing --token or --server arguments', 'Error')
        return 1

    print('[*] Contacting server...')
    try:
        req = urllib.request.Request(f'{server}/api/claim-launch/{token}')
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        msgbox(f'Failed to contact server:\n{e}\n\nCheck your internet connection.', 'Connection Error')
        return 1

    if not data.get('ok'):
        msgbox(data.get('error', 'Launch failed'), 'Error')
        return 1

    tool_name = data.get('tool_name', 'Tool')
    url = data['url']
    cookies_raw = json.loads(data['cookies'])
    username = data['username']
    print(f'[*] Launching: {tool_name}  (user: {username})')

    ensure_playwright()

    cookies = format_cookies(cookies_raw, url)
    print(f'[*] Prepared {len(cookies)} cookies')

    user_data_dir = tempfile.mkdtemp(prefix='st_')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            is_semrush = 'semrush.com' in url
            tool_args = ['--start-maximized', '--disable-blink-features=AutomationControlled', '--no-sandbox']
            context = p.chromium.launch_persistent_context(
                user_data_dir, headless=False,
                args=tool_args,
                ignore_default_args=['--enable-automation'],
                no_viewport=True,
                user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                            'AppleWebKit/537.36 (KHTML, like Gecko) '
                            'Chrome/124.0.0.0 Safari/537.36'),
            )
            page = context.new_page()
            page.add_init_script(ANTI_THEFT_JS)
            if is_semrush:
                page.add_init_script(SEMRUSH_JS)

            page.goto(url, wait_until='domcontentloaded', timeout=60_000)
            context.add_cookies(cookies)
            page.reload(wait_until='domcontentloaded', timeout=60_000)
            if is_semrush:
                page.evaluate(SEMRUSH_JS)
            print(f'[+] {tool_name} is ready! Close the browser window to end the session.')

            page.wait_for_event('close', timeout=0)
            context.close()
    except Exception as e:
        msgbox(f'Error launching browser:\n{e}', 'Error')
        import traceback
        traceback.print_exc()
        return 1
    finally:
        try:
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass

    print('[+] Session ended.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
