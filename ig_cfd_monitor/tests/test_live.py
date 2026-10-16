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
  for method,path in [('POST','/positions/otc'),('DELETE','/positions/otc'),('PUT','/working-orders/otc/x'),('GET','https://evil.test/accounts'),('GET','//evil.test/accounts'),('GET','/../accounts'),('POST','/session?x=1')]:
   with self.assertRaises(IGError):IGClient.validate_path(method,path)
  for method,path in [('POST','/session'),('PUT','/session'),('GET','/accounts'),('GET','/history/activity?from=x'),('GET','/positions'),('GET','/markets/IX.D.TEST.IP')]:IGClient.validate_path(method,path)
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
  self.m.state.update(status='connected',account={'equity':12000},account_at=now());self.m.funding=0;self.m.funding_at=now()
  self.assertEqual(self.m.project()['risk']['status'],'TRIGGERED')
  self.m.funding=10000;self.m.state['account']['equity']=22000
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
  row={'dateUtc':now(),'profitAndLoss':'HK$ 100.00','currency':'HKD'}
  class Fake:
   def request(_,method,path,version,params=None):
    return {'transactions':[row,row] if params['type']=='DEPOSIT' else [],'metadata':{'pageData':{'totalPages':1}}}
  self.m.client=Fake();self.m.funding_sync();self.assertEqual(self.m.funding,100)

if __name__=='__main__':unittest.main()
