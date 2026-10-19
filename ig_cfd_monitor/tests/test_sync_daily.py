import io
import ssl
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch,MagicMock
from urllib.error import URLError,HTTPError
from ig_client import IGClient,IGError
from storage import Store
from runtime import Monitor,now
from events_calendar import parse_schedule,event,upcoming

class TransportRecovery(unittest.TestCase):
 def setUp(self):self.client=IGClient({'environment':'demo','api_key':'synthetic'});self.sleep=patch('ig_client.time.sleep');self.sleep.start()
 def tearDown(self):self.sleep.stop()
 def test_retry_get_success(self):
  response=MagicMock();response.__enter__.return_value=response;response.headers={};response.read.return_value=b'{"accounts":[]}'
  self.client.opener.open=MagicMock(side_effect=[URLError('temporary'),response])
  self.assertEqual(self.client.request('GET','/accounts'),{'accounts':[]});self.assertEqual(self.client.opener.open.call_count,2)
 def test_certificate_not_retried(self):
  self.client.opener.open=MagicMock(side_effect=URLError(ssl.SSLCertVerificationError(1,'invalid')))
  with self.assertRaises(IGError) as error:self.client.request('GET','/accounts')
  self.assertEqual(error.exception.code,'ig.tls.certificate.invalid');self.assertEqual(self.client.opener.open.call_count,1)
 def test_post_and_auth_not_retried(self):
  self.client.opener.open=MagicMock(side_effect=URLError('temporary'))
  with self.assertRaises(IGError):self.client.request('POST','/session',payload={})
  self.assertEqual(self.client.opener.open.call_count,1)
  self.client.opener.open=MagicMock(side_effect=HTTPError('https://synthetic.test',401,'unauthorized',{},io.BytesIO(b'{}')))
  with self.assertRaises(IGError):self.client.request('GET','/accounts')
  self.assertEqual(self.client.opener.open.call_count,1)

class DailyTests(unittest.TestCase):
 def test_hkt_day_boundary_last_observation_scope_and_missing(self):
  store=Store(':memory:')
  try:
   for at,eq in [('2026-01-01T15:00:00+00:00',1),('2026-01-01T15:59:59+00:00',2),('2026-01-01T16:00:00+00:00',3),('2026-01-04T00:00:00+00:00',4)]:store.save_snapshot('a',at,{'equity':eq})
   store.save_snapshot('b','2026-01-01T16:00:00+00:00',{'equity':99})
   rows=store.daily_series('a');self.assertEqual([r['day'] for r in rows],['2026-01-01','2026-01-02','2026-01-04']);self.assertEqual([r['equity'] for r in rows],[2,3,4])
   self.assertFalse(any(r['provisional'] for r in rows))
  finally:store.db.close()

class IndependentSync(unittest.TestCase):
 def test_orders_failure_does_not_invalidate_good_equity(self):
  with patch('runtime.Store',lambda:Store(':memory:')),patch.object(Monitor,'status'):
   m=Monitor();m.scope='demo:a';m.creds={'history_since':'2026-01-01'};m.funding=30000;m.funding_at=now();m.client=MagicMock();m.client.account_id='a'
   m.client.request.side_effect=[{'accounts':[{'accountId':'a','balance':{'balance':12000,'profitLoss':0,'deposit':100,'available':11900}}]},{'positions':[]},IGError('temporary')]
   m.reconcile();out=m.project()
   self.assertEqual(out['risk']['status'],'TRIGGERED');self.assertEqual(out['sync']['orders']['status'],'retrying');self.assertEqual(out['positions'],[])
   m.store.db.close()
 def test_deposit_coverage_requires_all_pages_and_rejects_conflicts(self):
  with patch('runtime.Store',lambda:Store(':memory:')):
   m=Monitor();m.scope='demo:a';m.creds={'history_since':datetime.now(timezone.utc).date().isoformat()};m.client=MagicMock()
   row={'reference':'one','transactionType':'DEPO','currency':'HK','profitAndLoss':'HK30,000.00','dateUtc':(datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat()}
   m.client.request.return_value={'transactions':[row],'metadata':{'pageData':{'totalPages':2}}}
   m.funding_sync();self.assertIsNone(m.funding)
   m.funding_sync();self.assertEqual(m.funding,30000)
   row={**row,'profitAndLoss':'HK40,000.00'};m.client.request.return_value={'transactions':[row],'metadata':{'pageData':{'totalPages':1}}}
   with self.assertRaises(IGError):m.funding_sync()
   self.assertEqual(m.funding,30000);m.store.db.close()

class CalendarTests(unittest.TestCase):
 def test_future_events_survive_hkt_midnight(self):
  now=datetime(2026,11,12,16,30,tzinfo=timezone.utc)
  self.assertTrue(upcoming(event('EIA','test','2026-11-12','12:00'),now))
  self.assertFalse(upcoming(event('EIA','test','2026-11-12','11:00'),now))
  self.assertTrue(upcoming(event('Fed','test','2026-11-12'),now))
 def test_dst_and_date_only(self):
  self.assertIn('12:30',event('BLS','test','2026-10-30','08:30')['at'])
  self.assertIn('13:30',event('BLS','test','2026-11-06','08:30')['at'])
  self.assertIsNone(event('Fed','test','2026-10-28')['at'])
 def test_bls_parser_rejects_structure_changes(self):
  html='<tr><td>Friday, November 6, 2026</td><td>08:30 AM</td><td>Employment Situation for October</td></tr>'
  self.assertEqual(parse_schedule('BLS',html,2026)[0]['date'],'2026-11-06')
  with self.assertRaises(ValueError):parse_schedule('BLS','no data',2026)

if __name__=='__main__':unittest.main()
