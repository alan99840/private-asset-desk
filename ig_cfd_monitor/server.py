"""Public read-only dashboard. Only this loopback port may be Funnel-proxied."""
import argparse
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import tempfile
import threading
import time
from collections import deque
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from runtime import Monitor

ROOT=Path(__file__).resolve().parent
ASSETS={'/':('index.html','text/html; charset=utf-8'),'/index.html':('index.html','text/html; charset=utf-8'),
 '/style.css':('style.css','text/css; charset=utf-8'),'/live.js':('live.js','text/javascript; charset=utf-8'),
 '/favicon.svg':('favicon.svg','image/svg+xml')}
SLOTS=threading.BoundedSemaphore(32)
MONITOR=None
ACCESS_FILE=ROOT/'state'/'viewer_access.json'
COOKIE='harbour_viewer'
SESSION_SECONDS=12*60*60
LOGIN_LOCK=threading.Lock()
LOGIN_FAILURES=deque()
LOGIN_STYLE='''body{margin:0;background:#f4f2ec;color:#233d37;font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}main{box-sizing:border-box;background:#fff;padding:40px;border:1px solid #dddcd4;border-radius:16px;width:min(420px,92vw)}small{letter-spacing:.15em}h1{font-size:26px;margin:20px 0 10px}p{line-height:1.6;color:#64716b}label{display:block;margin:24px 0 8px}input,button{box-sizing:border-box;width:100%;padding:13px;font:inherit;border-radius:8px}input{border:1px solid #b8c3bc}button{margin-top:16px;border:0;background:#244e42;color:white;cursor:pointer}.error{color:#a23c36}'''
LOGIN_STYLE_HASH=base64.b64encode(hashlib.sha256(LOGIN_STYLE.encode()).digest()).decode()


def set_viewer_password(password):
    if not isinstance(password,str) or not 1<=len(password)<=256:raise ValueError('invalid_password_length')
    salt=secrets.token_bytes(16)
    data={'version':1,'salt':salt.hex(),'digest':hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000).hex(),'signing_key':secrets.token_hex(32)}
    ACCESS_FILE.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(ACCESS_FILE.parent,0o700)
    fd,temp=tempfile.mkstemp(prefix='.viewer-',dir=ACCESS_FILE.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(data,f);f.flush();os.fsync(f.fileno())
        os.replace(temp,ACCESS_FILE)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def access_config():
    try:
        if ACCESS_FILE.stat().st_mode & 0o077:return None
        c=json.loads(ACCESS_FILE.read_text())
        if c.get('version')!=1:return None
        for key,length in [('salt',16),('digest',32),('signing_key',32)]:
            if len(bytes.fromhex(c[key]))!=length:return None
        return c
    except (OSError,ValueError,KeyError,TypeError):return None


def session_token(config):
    value=str(int(time.time())+SESSION_SECONDS)+'.'+secrets.token_hex(16)
    return value+'.'+hmac.new(bytes.fromhex(config['signing_key']),value.encode(),hashlib.sha256).hexdigest()


def session_expiry(cookie_header,config):
    try:
        cookies=SimpleCookie();cookies.load(cookie_header or '')
        token=cookies[COOKIE].value
        if len(token)>200:return 0
        expiry,nonce,signature=token.split('.')
        expected=hmac.new(bytes.fromhex(config['signing_key']),(expiry+'.'+nonce).encode(),hashlib.sha256).hexdigest()
        end=int(expiry)
        return end if time.time()<end<=time.time()+SESSION_SECONDS and hmac.compare_digest(signature,expected) else 0
    except (CookieError,ValueError,KeyError,TypeError):return 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def headers_for(self,mime,status=200,length=None,extra=None):
        self.send_response(status);self.send_header('Content-Type',mime)
        if length is not None:self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        # no-referrer makes native form POSTs send Origin: null in browsers.
        # Keep same-site form origins while withholding referrers from other sites.
        self.send_header('Referrer-Policy','same-origin');self.send_header('X-Robots-Tag','noindex, nofollow, noarchive')
        self.send_header('Content-Security-Policy',f"default-src 'self'; script-src 'self'; style-src 'self' 'sha256-{LOGIN_STYLE_HASH}'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'")
        for key,value in (extra or {}).items():self.send_header(key,value)
        self.end_headers()
    def reply(self,payload,status=200,mime='application/json; charset=utf-8',extra=None):
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode() if isinstance(payload,(dict,list)) else payload
        self.headers_for(mime,status,len(body),extra)
        if self.command!='HEAD':self.wfile.write(body)
    def login_page(self,message='',status=200):
        html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Harbour · 访问验证</title><style>{LOGIN_STYLE}</style><main><small>HARBOUR / PRIVATE MONITOR</small><h1>访问账户监控</h1><p>输入共享访问密码，查看账户与实时行情。</p><form method="post" action="/auth/login"><label for="password">访问密码</label><input id="password" name="password" type="password" autocomplete="current-password" maxlength="256" required autofocus><button type="submit">进入监控平台</button></form><p class="error" role="status">{message}</p><p>登录有效期为 12 小时。</p></main></html>'''
        self.reply(html.encode(),status,'text/html; charset=utf-8')
    def authorize(self,path):
        config=access_config()
        if not config:
            self.reply({'error':'viewer_access_not_configured'},503);return False
        self.session_until=session_expiry(self.headers.get('Cookie'),config)
        if self.session_until:return True
        if path.startswith('/api/'):
            self.reply({'error':'authentication_required'},401)
        else:self.login_page()
        return False
    def do_GET(self):
        uri=urlsplit(self.path)
        try:
            if not self.authorize(uri.path):return
            if uri.path in ASSETS:
                filename,mime=ASSETS[uri.path];self.reply((ROOT/'web'/filename).read_bytes(),mime=mime)
            elif uri.path=='/api/state':self.reply(MONITOR.project())
            elif uri.path=='/api/ledger':
                query=parse_qs(uri.query);page=max(1,min(100000,int(query.get('page',['1'])[0])))
                rows=MONITOR.ledger();kind=query.get('kind',['all'])[0]
                if kind!='all':rows=[r for r in rows if r['kind']==kind]
                self.reply({'rows':rows[(page-1)*50:page*50],'page':page,'total':len(rows),'page_size':50,'history':MONITOR.project()['history']})
            elif uri.path=='/api/ledger.csv':
                stream=io.StringIO();fields=['kind','at','reference','type','name','amount','currency','size','open','close','status','description']
                writer=csv.writer(stream);writer.writerow(fields)
                for row in MONITOR.ledger():
                    cells=[]
                    for key in fields:
                        v=str(row.get(key) or '')
                        if v[:1] in '=+-@\t\r':v="'"+v
                        cells.append(v)
                    writer.writerow(cells)
                self.reply(('\ufeff'+stream.getvalue()).encode(),mime='text/csv; charset=utf-8')
            elif uri.path=='/api/events' and self.command!='HEAD':self.events()
            else:self.reply({'error':'not_found'},404)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        except ValueError:self.reply({'error':'invalid_query'},400)
    do_HEAD=do_GET
    def events(self):
        if not SLOTS.acquire(blocking=False):self.reply({'error':'capacity'},503);return
        try:
            self.connection.settimeout(15);self.headers_for('text/event-stream; charset=utf-8')
            # IG ticks are projected to browsers at up to 4 Hz. No REST requests per viewer.
            end=time.monotonic()+55;last='';last_heartbeat=0
            while time.monotonic()<end and time.time()<self.session_until:
                out=MONITOR.project()
                # Age fields make payload change; a 1 Hz heartbeat handles quiet markets.
                comparison=json.dumps({k:v for k,v in out.items() if k not in ('generated_at','account_age_seconds','markets')},sort_keys=True)
                comparison+=json.dumps([{k:v for k,v in q.items() if k!='age_seconds'} for q in out['markets']],sort_keys=True)
                if comparison!=last or time.monotonic()-last_heartbeat>=1:
                    self.wfile.write(('data: '+json.dumps(out,ensure_ascii=False,allow_nan=False)+'\n\n').encode());self.wfile.flush()
                    last=comparison;last_heartbeat=time.monotonic()
                time.sleep(.25)
        finally:SLOTS.release()
    def reject(self):self.reply({'error':'read_only'},405)
    def do_POST(self):
        if urlsplit(self.path).path!='/auth/login':self.reject();return
        try:
            self.connection.settimeout(5)
            origin=self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
                self.reply({'error':'invalid_origin'},403);return
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096 or self.headers.get('Transfer-Encoding'):
                self.reply({'error':'invalid_request'},400);return
            if self.headers.get_content_type()!='application/x-www-form-urlencoded':
                self.reply({'error':'invalid_content_type'},415);return
            form=parse_qs(self.rfile.read(length).decode('utf-8'),max_num_fields=4)
            password=form.get('password',[''])[0]
            config=access_config()
            if not config:self.reply({'error':'viewer_access_not_configured'},503);return
            with LOGIN_LOCK:
                now=time.monotonic()
                while LOGIN_FAILURES and LOGIN_FAILURES[0]<now-60:LOGIN_FAILURES.popleft()
                if len(LOGIN_FAILURES)>=10:
                    self.reply({'error':'too_many_attempts','retry_after':60},429,extra={'Retry-After':'60'});return
                digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(config['salt']),600000).hex() if 1<=len(password)<=256 else ''
                if not hmac.compare_digest(digest,config['digest']):
                    LOGIN_FAILURES.append(now);self.login_page('密码不正确，请重试。',401);return
            host=urlsplit('//'+self.headers.get('Host','')).hostname
            secure='' if host in ('127.0.0.1','localhost','::1') else '; Secure'
            cookie=f'{COOKIE}={session_token(config)}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict{secure}'
            self.reply(b'',303,'text/plain',{'Set-Cookie':cookie,'Location':'/'})
        except (ValueError,UnicodeError):self.reply({'error':'invalid_request'},400)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
    do_PUT=do_PATCH=do_DELETE=do_OPTIONS=reject


def main():
    global MONITOR
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8786);parser.add_argument('--set-password',action='store_true');args=parser.parse_args()
    if args.set_password:
        from getpass import getpass
        password=getpass('Viewer password: ')
        if password!=getpass('Confirm password: '):raise SystemExit('Passwords do not match.')
        set_viewer_password(password);print('Viewer password saved. Existing sessions are invalidated.');return
    MONITOR=Monitor();MONITOR.start()
    print(f'Harbour read-only monitor: http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

if __name__=='__main__':main()
