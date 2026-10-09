"""Authenticated private dashboard. Loopback only; remote access needs a private HTTPS proxy."""
import argparse
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from context import HERE, read_json, write_json
from advisor import clock, dt
from market import loop

AUTH = Path(os.environ.get('V23_AUTH_FILE', str(HERE/'state/auth.json')))
SESSIONS = {}
ATTEMPTS = {}
LOCK = threading.Lock()

def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

def bundle():
    cards = read_json(HERE/'state/action_cards.json')
    now = datetime.now(timezone.utc)
    for card in cards.get('cards', []):
        expiry = card.get('valid_until_ET') or card.get('valid_until_HK')
        if expiry and now >= dt(expiry):
            card['status'] = 'WAIT';card['expired'] = True
            card['reason'] = ['原信号已过期，不能据此下单或重置有效期。']
    if cards.get('cards') and all(c.get('status') == 'WAIT' for c in cards['cards']): cards['status'] = 'WAIT'
    return {'server_at':now.isoformat(), 'clock':clock(now), 'version':'V2.3',
            'market':read_json(HERE/'state/market.json'), 'watchlist':read_json(HERE/'state/watchlist.json'),
            'frozen':read_json(HERE/'state/frozen_watchlist.json'), 'research':read_json(HERE/'state/research.json'),
            'calendar':read_json(HERE/'state/events_calendar.json'), 'brief':read_json(HERE/'state/brief.json'),
            'actions':cards, 'portfolio':read_json(HERE/'state/portfolio.json'),
            'automation':read_json(HERE/'automation_status.json'), 'deployment':read_json(HERE/'state/deployment.json')}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Avoid logging cookies, passwords, URLs or research data.

    def host_allowed(self):
        host = self.headers.get('Host', '')
        hostname = host.split(':')[0].lower()
        allowed = ['127.0.0.1','localhost']+read_json(HERE/'state/deployment.json').get('allowed_hosts', [])
        return hostname in allowed

    def remote_allowed(self):
        """Tailscale Serve is the only trusted proxy; it connects over loopback.

        Serve strips caller-supplied Tailscale identity headers and supplies the
        authenticated tailnet identity. Other tailnet users are denied even if
        they know the dashboard password. Direct local access stays available.
        """
        hostname = self.headers.get('Host', '').split(':')[0].lower()
        remote = (hostname not in ['127.0.0.1', 'localhost']
                  or bool(self.headers.get('X-Forwarded-For'))
                  or bool(self.headers.get('X-Forwarded-Proto'))
                  or bool(self.headers.get('Tailscale-User-Login')))
        if not remote:
            return True
        config = read_json(HERE/'state/deployment.json')
        expected = config.get('allowed_tailscale_login', '')
        actual = self.headers.get('Tailscale-User-Login', '')
        return bool(expected and actual and config.get('remote_enabled')) and hmac.compare_digest(actual.encode(), expected.encode())

    def authenticated(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie',''))
            token = cookie['v23_session'].value
            key = hashlib.sha256(token.encode()).hexdigest()
            with LOCK:
                until = SESSIONS.get(key,0)
                if until <= time.time(): SESSIONS.pop(key,None)
            return until > time.time()
        except (KeyError,ValueError):
            return False

    def respond(self, code, data=b'', content_type='text/html; charset=utf-8', headers=None):
        if isinstance(data,str): data=data.encode()
        self.send_response(code)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        # Chrome can send Origin:null on a normal POST under no-referrer.
        # same-origin keeps form Origin verifiable and still hides cross-site referrers.
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers();self.wfile.write(data)

    def login_page(self, error=''):
        setup = not AUTH.exists()
        title = '建立你的私人看板登录' if setup else '登录私人资产看板'
        info = '首次设置仅允许在这台电脑的本机地址完成。密码不会发送给advisor。' if setup else '请使用你在本机设置的看板账号；它与券商密码无关。'
        self.respond(200,f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>私人资产看板 · 登录</title><link rel="stylesheet" href="/style.css"><body class="login-body"><main class="login-box"><div class="eyebrow">PRIVATE ASSET DESK / V2.3</div><h1>{title}</h1><p>{info}</p><p class="negative">{html.escape(error)}</p><form method="post" action="/login"><label>看板用户名<input name="username" required maxlength="80" autocomplete="username"></label><label>看板密码<input name="password" type="password" required minlength="12" maxlength="128" autocomplete="{"new-password" if setup else "current-password"}"></label><small>至少12个字符。请不要使用券商登录密码。</small><button class="primary" type="submit">{"建立账户并进入" if setup else "登录"}</button></form><footer>行情只读 · 真实交易由你提交</footer></main></body></html>')

    def do_GET(self):
        if not self.host_allowed(): return self.respond(403,'Host not allowed')
        if not self.remote_allowed(): return self.respond(403,'Private owner access only')
        path = urlparse(self.path).path
        if path == '/login': return self.login_page()
        if path == '/style.css': return self.respond(200,(HERE/'web/style.css').read_bytes(),'text/css; charset=utf-8')
        if path == '/health': return self.respond(200,json.dumps({'status':'OK','auth_required':True}),'application/json')
        if not self.authenticated():
            return self.respond(401,'{"status":"LOGIN_REQUIRED"}','application/json') if path.startswith('/api/') else self.respond(303,headers={'Location':'/login'})
        if path == '/': return self.respond(200,(HERE/'web/index.html').read_bytes())
        if path == '/app.js': return self.respond(200,(HERE/'web/app.js').read_bytes(),'text/javascript; charset=utf-8')
        if path == '/api/dashboard': return self.respond(200,json.dumps(bundle(),ensure_ascii=False),'application/json; charset=utf-8')
        if path == '/api/brief.md':
            brief = read_json(HERE/'state/brief.json')
            return self.respond(200,brief.get('markdown','晨报尚未生成。'),'text/markdown; charset=utf-8',{'Content-Disposition':'attachment; filename="asset-morning-brief.md"'})
        return self.respond(404,'Not found')

    def do_POST(self):
        if not self.host_allowed(): return self.respond(403,'Host not allowed')
        if not self.remote_allowed(): return self.respond(403,'Private owner access only')
        path = urlparse(self.path).path
        origin = self.headers.get('Origin')
        if not origin or urlparse(origin).netloc.lower() != self.headers.get('Host','').lower():
            return self.respond(403,'Same-origin form required')
        if path == '/logout':
            try:
                token = SimpleCookie(self.headers.get('Cookie',''))['v23_session'].value
                with LOCK: SESSIONS.pop(hashlib.sha256(token.encode()).hexdigest(),None)
            except KeyError: pass
            return self.respond(303,headers={'Location':'/login','Set-Cookie':'v23_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'})
        if path != '/login': return self.respond(404,'Not found')
        try: length=int(self.headers.get('Content-Length','0'))
        except ValueError: return self.respond(400,'Invalid request')
        if not 0 < length <= 4096: return self.respond(400,'Invalid request')
        form=parse_qs(self.rfile.read(length).decode(),keep_blank_values=True)
        username=form.get('username',[''])[0].strip();password=form.get('password',[''])[0]
        if not 1 <= len(username) <= 80 or not 12 <= len(password) <= 128:
            return self.login_page('用户名无效或密码不足12个字符。')
        peer=self.client_address[0]
        with LOCK:
            attempts=[t for t in ATTEMPTS.get(peer,[]) if time.time()-t<60]
            if len(attempts)>=5: return self.respond(429,'Too many attempts; retry after one minute')
            attempts.append(time.time());ATTEMPTS[peer]=attempts
        if not AUTH.exists():
            # Proxy requests never get initial-account creation privileges.
            if peer not in ['127.0.0.1','::1'] or self.headers.get('X-Forwarded-For') or self.headers.get('X-Forwarded-Proto') or self.headers.get('Host','').split(':')[0] not in ['127.0.0.1','localhost']:
                return self.respond(403,'Finish initial account setup locally')
            salt=secrets.token_hex(16)
            write_json(AUTH,{'username':username,'salt':salt,'password_hash':hash_password(password,salt)})
            AUTH.chmod(0o600)
        config=read_json(AUTH)
        valid=hmac.compare_digest(username.encode(),config.get('username','').encode()) and hmac.compare_digest(hash_password(password,config['salt']),config['password_hash'])
        if not valid: return self.login_page('用户名或密码不匹配。')
        token=secrets.token_urlsafe(32)
        with LOCK: SESSIONS[hashlib.sha256(token.encode()).hexdigest()]=time.time()+28800
        secure='; Secure' if self.headers.get('Tailscale-User-Login') or self.headers.get('X-Forwarded-Proto')=='https' or self.headers.get('Host','').split(':')[0] not in ['127.0.0.1','localhost'] else ''
        self.respond(303,headers={'Location':'/','Set-Cookie':f'v23_session={token}; Path=/; Max-Age=28800; HttpOnly; SameSite=Strict{secure}'})

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);p.add_argument('--no-monitor',action='store_true');args=p.parse_args()
    stop=threading.Event()
    if not args.no_monitor: threading.Thread(target=loop,args=(stop,),daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    write_json(HERE/'state/server.json',{'pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat(),'url':f'http://127.0.0.1:{args.port}','bound_to':'127.0.0.1','authentication_required':True,'monitor_enabled':not args.no_monitor})
    print(f'V2.3 authenticated dashboard: http://127.0.0.1:{args.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: stop.set();server.server_close()

if __name__=='__main__':main()
