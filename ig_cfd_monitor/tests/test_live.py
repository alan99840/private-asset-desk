import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ig_client import IGClient,IGError
from storage import Store
from runtime import Monitor,number,amount,now

class TransportTests(unittest.TestCase):
 def test_no_dealing(self):
  for method,path in [('POST','/positions/otc'),('DELETE','/positions/otc'),('PUT','/workingorders/otc/x'),('POST','/workingorders/otc'),('DELETE','/workingorders/otc/x'),('GET','/working-orders'),('GET','https://evil.test/accounts'),('GET','//evil.test/accounts'),('GET','/../accounts'),('POST','/session?x=1')]:
   with self.assertRaises(IGError):IGClient.validate_path(method,path)
  for method,path in [('POST','/session'),('PUT','/session'),('GET','/accounts'),('GET','/history/activity?from=x'),('GET','/positions'),('GET','/workingorders'),('GET','/markets/IX.D.TEST.IP')]:IGClient.validate_path(method,path)
 def test_numeric_unknown(self):
  self.assertIsNone(number('NaN'));self.assertIsNone(number('inf'));self.assertIsNone(amount('US$ 3'))
  self.assertEqual(amount('(HK$ 1,234.56)'),-1234.56)

class CollectorTests(unittest.TestCase):
 def setUp(self):
  self.patch=patch('runtime.Store',lambda:Store(':memory:'));self.patch.start();self.m=Monitor();self.m.scope='demo:A'
  self.m.creds={'risk_baseline_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}
 def tearDown(self):self.m.store.db.close();self.patch.stop()
 def test_scope_dedup(self):
  r={'reference':'a','profitAndLoss':'1'}
  self.m.store.event('demo:A','transactions',r,now());self.m.store.event('demo:A','transactions',r,now());self.m.store.event('live:B','transactions',r,now())
  self.assertEqual(self.m.store.count('demo:A'),1);self.assertEqual(self.m.store.count('live:B'),1)
 def test_stale_and_funding_gate(self):
  self.m.state.update(status='connected',account={'equity':12000},account_at=now());self.m.funding=30000;self.m.funding_at=now()
  self.assertEqual(self.m.project()['risk']['status'],'TRIGGERED')
  self.m.funding=40000;self.m.state['account']['equity']=16000
  self.assertEqual(self.m.project()['risk']['status'],'TRIGGERED')
  self.m.state['account_at']=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat()
  self.assertEqual(self.m.project()['risk']['status'],'STALE')
  self.m.funding=None
  self.assertEqual(self.m.project()['risk']['status'],'UNKNOWN')
 def test_price_ordering_and_epoch(self):
  self.m.on_stream('price','PRICE:A:EPIC',{'TIMESTAMP':'2000','BIDPRICE1':'10'},0)
  self.m.on_stream('price','PRICE:A:EPIC',{'TIMESTAMP':'1000','BIDPRICE1':'1'},0)
  self.assertEqual(self.m.quotes['EPIC']['bid'],10)
  self.m.on_stream('price','PRICE:A:EPIC',{'TIMESTAMP':'3000','BIDPRICE1':'20'},-1)
  self.assertEqual(self.m.quotes['EPIC']['bid'],10)
 def test_public_projection_allowlist(self):
  self.m.state['positions']=[{'position':{'dealId':'D','password':'SECRET'},'market':{'epic':'E','api_key':'SECRET'}}]
  self.assertNotIn('SECRET',json.dumps(self.m.project()))
 def test_history_pagination(self):
  class Fake:
   def request(_,method,path,version,params=None):
    return {'transactions':[{'reference':str(params['pageNumber']),'dateUtc':now()}],'metadata':{'pageData':{'totalPages':2}}}
  self.m.client=Fake();self.m.history_jobs=[{'kind':'transactions','from':now(),'to':now(),'page':1,'next':None}]
  self.m.history_step();self.assertEqual(len(self.m.history_jobs),1)
  self.m.history_step();self.assertEqual(len(self.m.history_jobs),0);self.assertEqual(len(self.m.ledger()),2)
 def test_history_missing_pagination_is_incomplete(self):
  class Fake:
   def request(*a,**kw):return {'transactions':[],'metadata':{}}
  self.m.client=Fake();self.m.history_jobs=[{'kind':'transactions','from':now(),'to':now(),'page':1,'next':None}]
  with self.assertRaises(IGError):self.m.history_step()
  self.assertEqual(len(self.m.history_jobs),1)
 def test_funding_explicit_filters_and_dedup(self):
  row={'transactionType':'DEPO','dateUtc':now(),'profitAndLoss':'HK$ 100.00','currency':'HKD'}
  class Fake:
   def request(_,method,path,version,params=None):
    return {'transactions':[row,row] if params['type']=='DEPOSIT' else [],'metadata':{'pageData':{'totalPages':1}}}
  self.m.client=Fake();self.m.funding_sync();self.assertEqual(self.m.funding,100)

class ViewerAccessTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  import server, threading
  cls.server_module=server;cls.temp=tempfile.TemporaryDirectory()
  cls.path_patch=patch.object(server,'ACCESS_FILE',Path(cls.temp.name)/'viewer_access.json');cls.path_patch.start()
  server.set_viewer_password('synthetic-viewer-password')
  cls.config=server.access_config()
  cls.monitor_patch=patch.object(server,'MONITOR');cls.monitor=cls.monitor_patch.start()
  cls.monitor.project.return_value={'status':'connected','markets':[],'history':{},'private_test_value':123}
  cls.monitor.ledger.return_value=[]
  cls.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
  cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):
  cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join()
  cls.monitor_patch.stop();cls.path_patch.stop();cls.temp.cleanup()
 def request(self,path,method='GET',body=None,headers=None):
  import http.client
  c=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=3)
  c.request(method,path,body=body,headers=headers or {})
  r=c.getresponse();status=r.status;h=dict(r.getheaders());data=r.read();c.close()
  return status,h,data
 def login(self,password='synthetic-viewer-password',**headers):
  from urllib.parse import urlencode
  return self.request('/auth/login','POST',urlencode({'password':password}),{'Content-Type':'application/x-www-form-urlencoded',**headers})
 def test_analytics_validation(self):
  cookie='harbour_viewer='+self.server_module.session_token(self.config)
  for query in ['shock=nan','mode=atr&shock=11','spread=0','fx=51','cost=-1','resolution=BAD','mode=percent&shock=101','shock=1&shock=2']:
   status,_,_=self.request('/api/analytics?'+query,headers={'Cookie':cookie});self.assertEqual(status,400)
  with patch.object(self.server_module,'ANALYTICS') as analytics:
   analytics.get.return_value={'status':'synthetic'}
   status,_,data=self.request('/api/analytics?mode=points&shock=-2',headers={'Cookie':cookie})
   self.assertEqual(status,200);self.assertIn(b'synthetic',data)
 def test_unauthed_data_and_export_are_blocked(self):
  for path in ['/api/state','/api/analytics','/api/ledger','/api/ledger.csv','/api/events']:
   for method in ['GET','HEAD']:
    status,_,data=self.request(path,method)
    self.assertEqual(status,401);self.assertNotIn(b'private_test_value',data)
  status,headers,data=self.request('/')
  self.assertEqual(status,200);self.assertIn(b'type="password"',data);self.assertNotIn(b'private_test_value',data)
  self.assertEqual(headers['Referrer-Policy'],'same-origin')
 def test_login_cookie_and_authorized_reads(self):
  status,h,_=self.login(Host='monitor.example',Origin='https://monitor.example')
  self.assertEqual(status,303);self.assertEqual(h['Location'],'/')
  for flag in ['HttpOnly','Secure','SameSite=Strict']:self.assertIn(flag,h['Set-Cookie'])
  cookie=h['Set-Cookie'].split(';')[0]
  for path in ['/','/live.js','/api/state','/api/ledger','/api/ledger.csv']:
   self.assertEqual(self.request(path,headers={'Cookie':cookie})[0],200)
  self.assertIn(b'private_test_value',self.request('/api/state',headers={'Cookie':cookie})[2])
 def test_stream_requires_valid_session(self):
  import http.client
  cookie=self.login()[1]['Set-Cookie'].split(';')[0]
  c=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=3)
  c.request('GET','/api/events',headers={'Cookie':cookie});r=c.getresponse()
  self.assertEqual(r.status,200);self.assertTrue(r.readline().startswith(b'data: '));r.close();c.close()
 def test_wrong_password_tampered_expired_and_rotated_sessions(self):
  self.assertEqual(self.login('wrong')[0],401)
  s=self.server_module;token=s.session_token(self.config)
  for bad in [token+'x','broken']:
   self.assertEqual(self.request('/api/state',headers={'Cookie':s.COOKIE+'='+bad})[0],401)
  with patch.object(s.time,'time',return_value=1):expired=s.session_token(self.config)
  self.assertEqual(s.session_expiry(s.COOKIE+'='+expired,self.config),0)
  rotated=dict(self.config,signing_key='00'*32)
  self.assertEqual(s.session_expiry(s.COOKIE+'='+token,rotated),0)
  with patch.object(s,'access_config',return_value=None):self.assertEqual(self.request('/api/state')[0],503)
  self.assertNotIn('synthetic-viewer-password',s.ACCESS_FILE.read_text())
  self.assertEqual(s.ACCESS_FILE.stat().st_mode & 0o777,0o600)
 def test_login_origin_and_rate_limit(self):
  self.assertEqual(self.login(Host='monitor.example',Origin='https://other.example')[0],403)
  self.assertEqual(self.login(Host='monitor.example',Origin='null')[0],403)
  import collections,time
  with patch.object(self.server_module,'LOGIN_FAILURES',collections.deque([time.monotonic()]*10)):
   self.assertEqual(self.login('wrong')[0],429)
  self.assertEqual(self.request('/api/state','POST')[0],405)

if __name__=='__main__':unittest.main()
