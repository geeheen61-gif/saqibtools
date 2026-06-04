import threading, json, tempfile, subprocess, sys, os, importlib

ANTI_THEFT_JS = """
(function(){
    document.addEventListener('contextmenu',function(e){e.preventDefault()});
    document.addEventListener('keydown',function(e){
        var key = e.key.toUpperCase();
        if (key === 'F12' || (e.ctrlKey && (key === 'U' || key === 'S' || key === 'C')) || (e.ctrlKey && e.shiftKey && ['I','J','C','K'].includes(key))) {
            e.preventDefault();
            return false;
        }
    });
    ['log','warn','error','info','debug','table','dir','trace'].forEach(function(m){try{window.console[m]=function(){}}catch(e){}});
    // Removed aggressive resize-based page redirect to avoid closing valid browser sessions when users switch windows or use the taskbar.
})();
"""

LAUNCH_ARGS = [
    '--disable-extensions','--disable-plugins','--disable-translate',
    '--no-first-run','--disable-sync','--no-default-browser-check',
    '--disable-features=Translate','--disable-save-password-bubble',
    '--start-maximized','--disable-blink-features=AutomationControlled',
    '--no-existing-browser-frame',
]


def _root_domain(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.split('@')[-1].split(':')[0]
    parts = host.split('.')
    if len(parts) >= 3:
        return '.'.join(parts[1:])
    return host


def _format_cookies(raw_cookies, url=''):
    root = _root_domain(url) if url else ''
    result = []
    for c in raw_cookies:
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


def _build_watermark_script(username: str) -> str:
    safe_name = json.dumps('\U0001f512 ' + username)
    return f"""
        window.addEventListener('DOMContentLoaded',function(){{
            var wm=document.createElement('div');
            wm.id='__wm__'; wm.innerText={safe_name};
            wm.style.cssText='position:fixed;bottom:12px;right:12px;z-index:2147483647;background:rgba(0,0,0,.6);color:#fff;padding:5px 12px;border-radius:6px;font:bold 13px/1.5 Arial,sans-serif;pointer-events:none;user-select:none;letter-spacing:.5px';
            document.body.appendChild(wm);
        }});
    """


def _install_playwright():
    """Install playwright pip package and chromium browser."""
    log = []
    try:
        log.append('[install] Installing playwright pip package...')
        print('--- Installing playwright pip package ---')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'playwright'],
                       timeout=120)
        log.append('[install] Installing Chromium browser (1-2 min)...')
        print('--- Installing Chromium browser (1-2 minutes) ---')
        subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'],
                       timeout=300)
        log.append('[install] Done')
        print('--- Installation complete ---')
    except subprocess.TimeoutExpired:
        log.append('[install] TIMEOUT - install may still complete')
        print('[install] TIMEOUT')
    except Exception as e:
        log.append(f'[install] ERROR: {e}')
        print(f'[install] ERROR: {e}')
    return log


def _ensure_playwright() -> list:
    """Check if playwright+chromium is available; if not, auto-install."""
    logs = []
    try:
        import playwright  # noqa: F401
        logs.append('[check] playwright package found')
    except ImportError:
        logs.append('[check] playwright missing - installing...')
        logs.extend(_install_playwright())
        importlib.invalidate_caches()

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if not exe or not os.path.isfile(exe):
                raise FileNotFoundError(f'chromium not at {exe}')
        logs.append('[check] chromium binary found')
    except Exception:
        logs.append('[check] chromium missing - installing...')
        logs.extend(_install_playwright())
    return logs


def _run_browser(url: str, cookies_json: str, username: str, install_logs=None):
    try:
        from playwright.sync_api import sync_playwright

        raw = json.loads(cookies_json)
        cookies = _format_cookies(raw, url)
        print(f'[browser] Launching  user={username!r}  cookies={len(cookies)}  url={url}')

        user_data_dir = tempfile.mkdtemp(prefix='pw_profile_')

        with sync_playwright() as p:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir, headless=False, args=LAUNCH_ARGS,
                    channel='chrome',
                    ignore_default_args=['--enable-automation'],
                    no_viewport=True,
                    accept_downloads=False,
                )
            except Exception as e:
                print(f'[browser] Google Chrome launch failed, falling back to standard Chromium: {e}')
                context = p.chromium.launch_persistent_context(
                    user_data_dir, headless=False, args=LAUNCH_ARGS,
                    ignore_default_args=['--enable-automation'],
                    no_viewport=True,
                    accept_downloads=False,
                )
            context.add_init_script(ANTI_THEFT_JS)
            context.add_init_script(_build_watermark_script(username))

            page = context.new_page()
            is_chatgpt = 'chatgpt.com' in url or 'chat.openai.com' in url
            is_grammarly = 'grammarly.com' in url
            is_primevideo = 'primevideo.com' in url or '/video' in url
            if is_chatgpt:
                page.add_init_script("""
                    (function(){
                        var sels=['#stage-slideover-sidebar > div > div > div > nav','#stage-slideover-sidebar'];
                        var s=document.createElement('style');
                        s.textContent=sels.join(',')+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}}
                        h();setInterval(h,200);
                    })();
                """)
            if 'semrush.com' in url:
                page.add_init_script("""
                    (function(){
                        var sel='#srf-header > div > div.srf-header__end,#srf-header__end';
                        var s=document.createElement('style');
                        s.textContent=sel+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){var e=document.querySelectorAll(sel);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}
                        h();setInterval(h,300);
                    })();
                """)
            if is_grammarly:
                page.add_init_script("""
                    (function(){
                        var sels=['header','[class*="header"]','[class*="nav"]','a[href*="logout"]','[data-testid="header"]','.app-header','.nav-bar','.nav-container'];
                        var s=document.createElement('style');
                        s.textContent=sels.join(',')+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}}
                        h();setInterval(h,300);
                    })();
                """)
            if is_primevideo:
                page.add_init_script("""
                    (function(){
                        var sels=['#navbar','#dv-web-nav-header','#av-breadcrumb','footer','[class*="nav-"]','.nav-links','.nav-banner','[data-testid="navbar"]','[data-testid="footer"]'];
                        var s=document.createElement('style');
                        s.textContent=sels.join(',')+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}}
                        h();setInterval(h,300);
                    })();
                """)

            page.goto(url, wait_until='domcontentloaded', timeout=60_000)
            context.add_cookies(cookies)
            page.reload(wait_until='domcontentloaded', timeout=60_000)
            if is_chatgpt:
                page.evaluate("""
                    (function(){
                        var sels=['#stage-slideover-sidebar > div > div > div > nav','#stage-slideover-sidebar'];
                        var s=document.createElement('style');
                        s.textContent=sels.join(',')+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}}
                        h();setInterval(h,200);
                        if(document.body){var mo=new MutationObserver(function(){h()});mo.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['style','class']})}
                    })();
                """)
            if 'semrush.com' in url:
                page.evaluate("""
                    (function(){
                        var sel='#srf-header > div > div.srf-header__end,#srf-header__end';
                        var s=document.createElement('style');
                        s.textContent=sel+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){var e=document.querySelectorAll(sel);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}
                        h();setInterval(h,300);
                    })();
                """)
            if is_grammarly:
                page.evaluate("""
                    (function(){
                        var sels=['header','[class*="header"]','[class*="nav"]','a[href*="logout"]','[data-testid="header"]','.app-header','.nav-bar','.nav-container'];
                        var s=document.createElement('style');
                        s.textContent=sels.join(',')+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}}
                        h();setInterval(h,300);
                        if(document.body){var mo=new MutationObserver(function(){h()});mo.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['style','class']})}
                    })();
                """)
            if is_primevideo:
                page.evaluate("""
                    (function(){
                        var sels=['#navbar','#dv-web-nav-header','#av-breadcrumb','footer','[class*="nav-"]','.nav-links','.nav-banner','[data-testid="navbar"]','[data-testid="footer"]'];
                        var s=document.createElement('style');
                        s.textContent=sels.join(',')+'{display:none!important}';
                        if(document.head)document.head.appendChild(s);
                        function h(){for(var si=0;si<sels.length;si++){var e=document.querySelectorAll(sels[si]);for(var i=0;i<e.length;i++){e[i].style.setProperty('display','none','important')}}}
                        h();setInterval(h,300);
                        if(document.body){var mo=new MutationObserver(function(){h()});mo.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['style','class']})}
                    })();
                """)
            injected = context.cookies()
            print(f'[browser] Cookies in jar: {len(injected)} of {len(cookies)} requested')
            print(f'[browser] Page reloaded: {url}')
            try:
                page.wait_for_event('close', timeout=0)
            except Exception as e:
                print(f'[browser] Warning waiting for page close: {e}')
            try:
                if not getattr(context, 'is_closed', lambda: False)():
                    context.close()
            except Exception as e:
                print(f'[browser] Warning closing context: {e}')

        import shutil
        try:
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass
        print(f'[browser] Session ended  user={username!r}')

    except Exception as e:
        import traceback
        print(f'[browser] ERROR  user={username!r}  \u2192 {e}')
        traceback.print_exc()


def open_tool(url: str, cookies_json: str, username: str) -> dict:
    logs = _ensure_playwright()
    for l in logs:
        print(f'[browser] {l}')

    t = threading.Thread(target=_run_browser, args=(url, cookies_json, username), daemon=True)
    t.start()
    return {'ok': True, 'msg': 'Chromium window opened on your desktop.'}
