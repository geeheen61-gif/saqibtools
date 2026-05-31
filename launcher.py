"""
Saqib Tools Launcher — standalone script for the user's PC.
Usage: python launcher.py --token <token> --server <server_url>

Downloads and runs automatically via launcher.bat served from the domain.
"""
import sys, json, urllib.request, subprocess, os, tempfile, importlib


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
        print('ERROR: Missing --token or --server arguments')
        return 1

    print('[*] Contacting server...')
    try:
        req = urllib.request.Request(f'{server}/api/claim-launch/{token}')
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f'[x] Failed to contact server: {e}')
        print('    Make sure your internet is working and the server is online.')
        input('Press Enter to exit...')
        return 1

    if not data.get('ok'):
        print(f'[x] Launch failed: {data.get("error", "unknown")}')
        input('Press Enter to exit...')
        return 1

    tool_name = data.get('tool_name', 'Tool')
    url = data['url']
    cookies_raw = json.loads(data['cookies'])
    username = data['username']
    print(f'[*] Launching: {tool_name}  (user: {username})')

    # Auto-install playwright if missing
    try:
        import playwright
        print('[*] Playwright package found')
    except ImportError:
        print('[*] Installing Playwright package...')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'playwright'], timeout=120)
        importlib.invalidate_caches()
        print('[*] Playwright installed')

    # Auto-install chromium if missing
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = p.chromium.executable_path
        if not exe or not os.path.isfile(exe):
            print('[*] Installing Chromium browser (1-2 minutes)...')
            subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], timeout=300)
            print('[*] Chromium installed')
        else:
            print('[*] Chromium browser found')

    # Format cookies and launch browser
    print('[*] Opening Chromium with your session...')
    cookies = format_cookies(cookies_raw, url)
    user_data_dir = tempfile.mkdtemp(prefix='pw_profile_')

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir, headless=False,
            args=['--start-maximized', '--disable-blink-features=AutomationControlled'],
            ignore_default_args=['--enable-automation'],
            no_viewport=True,
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/124.0.0.0 Safari/537.36'),
        )
        # Inject cookies BEFORE any navigation
        context.add_cookies(cookies)
        print(f'[*] Injected {len(context.cookies())} cookies')

        page = context.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=60_000)
        print(f'[+] {tool_name} is ready! Close the browser window to end the session.')

        page.wait_for_event('close', timeout=0)
        context.close()

    try:
        import shutil
        shutil.rmtree(user_data_dir, ignore_errors=True)
    except Exception:
        pass

    print('[+] Session ended.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
