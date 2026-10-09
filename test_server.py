"""Authentication and privacy gates using an isolated disposable test identity."""
import hashlib,json,secrets,tempfile,threading
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
import server

passed=[]
def check(name,ok):assert ok,name;passed.append(name)
with tempfile.TemporaryDirectory() as folder:
 server.AUTH=Path(folder)/'synthetic_auth.json';server.ATTEMPTS.clear();server.SESSIONS.clear()
 original_read_json=server.read_json
 test_deployment={'remote_enabled':True,'allowed_hosts':['desk.example.test'],'allowed_tailscale_login':'synthetic-owner@example.test'}
 server.read_json=lambda path: test_deployment if path==server.HERE/'state/deployment.json' else original_read_json(path)
 http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
 threading.Thread(target=http.serve_forever,daemon=True).start()
 base=f'http://127.0.0.1:{http.server_address[1]}'
 def request(path,body=None,cookie=None,origin=True,extra=None):
  headers={}
  if body is not None:
   headers['Content-Type']='application/x-www-form-urlencoded'
   if origin:headers['Origin']=base
  if cookie:headers['Cookie']=cookie
  headers.update(extra or {})
  req=Request(base+path,data=urlencode(body).encode() if body is not None else None,headers=headers)
  try:
   with urlopen(req) as response:return response.status,response.headers,response.read()
  except HTTPError as err:return err.code,err.headers,err.read()
 check('unauthenticated_api_denied',request('/api/dashboard')[0]==401)
 check('browser_form_keeps_same_origin_metadata',request('/login')[1]['Referrer-Policy']=='same-origin')
 check('unknown_host_denied',request('/health',extra={'Host':'attacker.example'})[0]==403)
 password=secrets.token_urlsafe(22)
 check('cross_origin_account_setup_denied',request('/login',{'username':'synthetic','password':password},origin=False)[0]==403)
 check('remote_proxy_cannot_create_account',request('/login',{'username':'synthetic','password':password},extra={'X-Forwarded-For':'192.0.2.1'})[0]==403)
 proxy={'X-Forwarded-Proto':'https','Tailscale-User-Login':'synthetic-owner@example.test'}
 check('private_owner_cannot_create_initial_account_remotely',request('/login',{'username':'synthetic','password':password},extra=proxy)[0]==403)
 # Avoid redirect following so Set-Cookie remains inspectable.
 import urllib.request
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args):return None
 opener=urllib.request.build_opener(NoRedirect)
 req=Request(base+'/login',data=urlencode({'username':'synthetic','password':password}).encode(),headers={'Origin':base})
 try:opener.open(req)
 except HTTPError as err:
  check('local_account_setup_logs_in',err.code==303)
  cookie=err.headers['Set-Cookie'].split(';')[0]
  check('cookie_is_http_only_and_same_site','HttpOnly' in err.headers['Set-Cookie'] and 'SameSite=Strict' in err.headers['Set-Cookie'])
 data=json.loads(server.AUTH.read_text())
 check('password_is_not_saved_plaintext',password not in server.AUTH.read_text() and len(data['password_hash'])==128)
 check('authenticated_dashboard_works',request('/api/dashboard',cookie=cookie)[0]==200)
 check('raw_credentials_not_served',request('/state/auth.json',cookie=cookie)[0]==404)
 check('private_response_not_cacheable',request('/api/dashboard',cookie=cookie)[1]['Cache-Control']=='no-store')
 check('private_owner_still_requires_dashboard_login',request('/api/dashboard',extra=proxy)[0]==401)
 check('private_owner_with_session_can_read',request('/api/dashboard',cookie=cookie,extra=proxy)[0]==200)
 check('other_tailnet_user_denied_even_with_session',request('/api/dashboard',cookie=cookie,extra={'X-Forwarded-Proto':'https','Tailscale-User-Login':'another-user@example.test'})[0]==403)
 check('remote_request_without_owner_identity_denied',request('/api/dashboard',cookie=cookie,extra={'X-Forwarded-Proto':'https'})[0]==403)
 req=Request(base+'/login',data=urlencode({'username':'synthetic','password':password}).encode(),headers={'Origin':base,**proxy})
 try:opener.open(req)
 except HTTPError as err:
  check('remote_owner_login_has_secure_cookie',err.code==303 and '; Secure' in err.headers['Set-Cookie'])
 request('/logout',{},cookie=cookie)
 check('logout_revokes_session',request('/api/dashboard',cookie=cookie)[0]==401)
 http.shutdown();http.server_close()
 server.read_json=original_read_json
print(json.dumps({'auth_checks_passed':len(passed),'test_identity_only':True,'checks':passed}))
