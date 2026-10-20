"""Loopback-only setup on a separate port. Never proxy this server through Funnel."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from credentials import save_credentials, credential_status, STATE, profile_ids, profile_directory

PORT = 8787
ORIGIN = f'http://127.0.0.1:{PORT}'
CSRF = secrets.token_urlsafe(32)
ROOT = Path(__file__).resolve().parent


def profile_status(profile_id):
    status = credential_status(profile_id)
    try:
        connection = json.loads((profile_directory(profile_id)/'connection_status.json').read_text())
    except (OSError, ValueError):
        connection = {'status': 'waiting_for_service'}
    if not status.get('enabled', True):
        connection = {'status': 'paused', 'message': '此账户已暂停监控。'}
    return {'credentials': status, 'connection': connection}


class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def allowed(self, write=False):
        if self.client_address[0] != '127.0.0.1' or self.headers.get('Host') != f'127.0.0.1:{PORT}':
            return False
        forbidden = ('Forwarded', 'X-Forwarded-For', 'X-Forwarded-Host',
                     'X-Forwarded-Proto', 'Tailscale-User-Login')
        if any(self.headers.get(h) for h in forbidden):
            return False
        if self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
            return False
        if write:
            return (self.headers.get('Origin') == ORIGIN and
                    secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), CSRF) and
                    self.headers.get('Content-Type', '').split(';')[0] == 'application/json')
        return True

    def reply(self, payload, status=200, mime='application/json; charset=utf-8'):
        body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'; object-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            self.reply({'error': '仅允许通过当前 Mac 的 127.0.0.1:8787 访问。'}, 403)
            return
        path = urlsplit(self.path).path
        if path == '/':
            html = (ROOT/'setup/index.html').read_text().replace('__CSRF__', CSRF)
            self.reply(html.encode(), mime='text/html; charset=utf-8')
        elif path in ('/setup.js','/setup.css'):
            self.reply((ROOT/'setup'/path[1:]).read_bytes(), mime='text/javascript' if path.endswith('.js') else 'text/css')
        elif path == '/api/status':
            self.reply(profile_status('default'))
        elif path == '/api/profiles':
            self.reply({'profiles': [profile_status(p) for p in profile_ids()]})
        else:
            self.reply({'error':'not_found'},404)

    def do_POST(self):
        if not self.allowed(write=True):
            self.reply({'error': '本机请求验证失败，请重新打开配置页。'},403)
            return
        if self.path != '/api/configure':
            self.reply({'error':'not_found'},404)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 1 <= size <= 8192:
                raise ValueError('请求大小异常。')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('配置格式错误。')
            profile_id = save_credentials(payload)
            self.reply({'ok':True,'profile_id':profile_id,
                        'message':'已保存到本机。后台会自动应用此账户配置；下方显示各账户连接结果。'})
        except (ValueError, OSError) as e:
            self.reply({'error':str(e) if isinstance(e,ValueError) and not isinstance(e,json.JSONDecodeError) else '保存失败，请检查输入。'},400)


if __name__ == '__main__':
    print(f'Private setup: {ORIGIN} (never expose this port)', flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),SetupHandler).serve_forever()
