"""Multi-account isolation tests. All identities and balances are synthetic."""
import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import credentials
from accounts import AccountFleet, summarize
from runtime import Monitor, now
from chart_history import ChartHistory, ChartError
from risk import evaluate_loss


def config(name='owner-one', key='synthetic-key-one', **kw):
    return dict(api_key=key,identifier=name,password='synthetic-password',environment='live',
                history_since='2020-01-01',name='测试账户',**kw)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patches=[patch.object(credentials,'STATE',self.root),patch.object(credentials,'CREDENTIALS',self.root/'credentials.json')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def test_legacy_primary_and_new_credentials_stay_separate(self):
        credentials.save_credentials(config())
        before=(self.root/'credentials.json').read_bytes()
        other=credentials.save_credentials(config('owner-two','key-two',profile_id='new'))
        self.assertEqual((self.root/'credentials.json').read_bytes(),before)
        self.assertEqual(credentials.load_credentials(other)['identifier'],'owner-two')
        self.assertEqual(set(credentials.profile_ids()),{'default',other})
        self.assertEqual(credentials.credential_path(other).stat().st_mode&0o777,0o600)
        self.assertEqual(credentials.profile_directory(other).stat().st_mode&0o777,0o700)
    def test_edit_preserves_secrets_and_can_pause(self):
        credentials.save_credentials(config());old=credentials.load_credentials()
        credentials.save_credentials(dict(profile_id='default',name='已更名',api_key='',identifier='',password='',
            environment='live',history_since='2020-01-01',enabled=False))
        new=credentials.load_credentials()
        for key in ('api_key','identifier','password','risk_baseline_at'):self.assertEqual(old[key],new[key])
        self.assertFalse(new['enabled']);self.assertEqual(new['name'],'已更名')
    def test_duplicate_login_or_key_rejected_even_if_case_changed(self):
        credentials.save_credentials(config())
        for name,key in [('OWNER-ONE','different-key'),('new-owner','synthetic-key-one')]:
            with self.assertRaises(ValueError):credentials.save_credentials(config(name,key,profile_id='new'))
        self.assertEqual(credentials.profile_ids(),['default'])
    def test_new_owner_cannot_replace_existing_identity(self):
        credentials.save_credentials(config());before=credentials.load_credentials()
        with self.assertRaises(ValueError):credentials.save_credentials(config('another-owner','another-key'))
        self.assertEqual(credentials.load_credentials(),before)
    def test_paths_unknown_ids_and_non_boolean_enable_rejected(self):
        for identity in ('../escape','p_bad','/tmp','p_'+'a'*16):
            with self.assertRaises(ValueError):credentials.save_credentials(config(profile_id=identity))
        with self.assertRaises(ValueError):credentials.save_credentials(config(enabled='false'))
        with self.assertRaises(ValueError):credentials.profile_directory('../escape')
    def test_status_has_no_login_secrets(self):
        credentials.save_credentials(config());text=json.dumps(credentials.credential_status())
        for private in ('owner-one','synthetic-key-one','synthetic-password'):self.assertNotIn(private,text)
    def test_monitor_store_and_drawings_do_not_cross_profiles(self):
        credentials.save_credentials(config());other=credentials.save_credentials(config('two','key-two',profile_id='new'))
        # Default Store has a module-level legacy path; patch only that path here.
        with patch('storage.STATE',self.root):
            a=Monitor();b=Monitor(other)
        try:
            for m in (a,b):m.scope='live:SAME-ID';m.market_info={'EPIC':{}}
            a.store.event(a.scope,'transactions',{'reference':'one'},now())
            b.store.event(b.scope,'transactions',{'reference':'two'},now())
            self.assertEqual([r['reference'] for r in a.ledger()],['one'])
            self.assertEqual([r['reference'] for r in b.ledger()],['two'])
            a.status('connected');b.status('error','synthetic.error')
            self.assertEqual(json.loads((self.root/'connection_status.json').read_text())['status'],'connected')
            ca=ChartHistory(a,path=self.root/'a.sqlite',start_worker=False)
            cb=ChartHistory(b,path=self.root/'b.sqlite',start_worker=False)
            drawing={'id':'line-1','type':'horizontal','points':[{'time':1700000000,'price':100}],'color':'#123456','width':1}
            saved,ok=ca.save_drawings('EPIC',0,[drawing]);self.assertTrue(ok)
            self.assertEqual(len(ca.drawings('EPIC')['drawings']),1)
            self.assertEqual(cb.drawings('EPIC')['drawings'],[])
            ca.db.close();cb.db.close()
        finally:a.store.db.close();b.store.db.close()
    def test_pause_prevents_login_and_keeps_collector_alive(self):
        from storage import Store
        with patch('runtime.Store',lambda:Store(':memory:')):m=Monitor()
        c=config(enabled=False);c['saved_at']='revision-1'
        stop=Mock();stop.wait.side_effect=[False,True];m.stop=stop
        with patch('runtime.load_credentials',return_value=c),patch.object(m,'connect') as connect,patch.object(m,'status'):
            m.run();connect.assert_not_called()
        m.store.db.close()


def state(identity,equity,environment='live',enabled=True):
    return {'profile':{'id':identity,'name':identity,'configured':True,'environment':environment,'enabled':enabled},
        'status':'connected','account':{'currency':'HKD','equity':equity,'cash':equity,'available':equity-100,'margin':100,'unrealized':0},
        'account_at':now(),'stale':False,'markets':[],'positions':[],
        'risk':evaluate_loss(equity,1000),'funding':{'total_deposits':1000},'alerts':[]}


class SummaryTests(unittest.TestCase):
    def test_individual_trigger_not_offset_by_other_profit(self):
        result=summarize([state('A',400),state('B',2000)])
        self.assertEqual(result['totals'][0]['equity'],2400)
        self.assertEqual(result['totals'][0]['triggered_accounts'],['A'])
        self.assertEqual(result['profiles'][0]['risk']['status'],'TRIGGERED')
    def test_demo_and_disabled_excluded_from_live_total(self):
        result=summarize([state('A',900),state('B',2000,'demo'),state('C',100000,enabled=False)])
        self.assertEqual([(t['environment'],t['equity']) for t in result['totals']],[('live',900),('demo',2000)])
    def test_stale_unknown_or_non_hkd_blocks_complete_total(self):
        for field,value in [('stale',True),('status','error')]:
            a=state('A',900);a[field]=value;r=summarize([a,state('B',800)])['totals'][0]
            self.assertIsNone(r['equity']);self.assertFalse(r['complete'])
        a=state('A',900);a['account']['currency']='USD'
        self.assertIsNone(summarize([a])['totals'][0]['equity'])
    def test_unverified_funding_is_not_replaced_with_initial_budget(self):
        a=state('A',900);a['risk']={'status':'UNKNOWN'}
        r=summarize([a])['totals'][0]
        self.assertEqual(r['equity'],900);self.assertIsNone(r['total_deposits'])
    def test_unknown_profile_never_falls_back(self):
        with self.assertRaises(ChartError):AccountFleet().get('missing')


class RoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.module=server;cls.temp=tempfile.TemporaryDirectory()
        cls.access=patch.object(server,'ACCESS_FILE',Path(cls.temp.name)/'viewer.json');cls.access.start()
        server.set_viewer_password('test-only');cls.cookie=server.COOKIE+'='+server.session_token(server.access_config())
        cls.fleet=AccountFleet()
        for identity in ('default','p_0123456789abcdef'):
            m=Mock();m.project.return_value=state(identity,900);m.project.return_value['history']={}
            m.ledger.return_value=[{'name':identity,'id':identity}]
            charts=Mock();charts.get.return_value={'profile':identity};charts.drawings.return_value={'profile':identity}
            charts.save_drawings.return_value=({'profile':identity},True)
            trades=Mock();trades.get.return_value={'profile':identity}
            analytics=Mock();analytics.get.return_value={'profile':identity}
            cls.fleet.bundles[identity]=SimpleNamespace(monitor=m,charts=charts,trades=trades,analytics=analytics)
        cls.fleet.overview=Mock(return_value={'profiles':[{'id':'default'},{'id':'p_0123456789abcdef'}]})
        cls.fpatch=patch.object(server,'FLEET',cls.fleet);cls.fpatch.start()
        cls.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join();cls.fpatch.stop();cls.access.stop();cls.temp.cleanup()
    def request(self,path,method='GET',body=None,auth=True):
        c=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=3)
        headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.httpd.server_port}'}
        if auth:headers['Cookie']=self.cookie
        c.request(method,path,body,headers);r=c.getresponse();status=r.status
        data=r.readline() if '/api/events' in path and status==200 else r.read();c.close();return status,data
    def test_every_account_scoped_read_routes_to_selected_account(self):
        identity='p_0123456789abcdef'
        for endpoint in ['/api/state','/api/events','/api/ledger','/api/ledger.csv','/api/analytics',
                         '/api/chart?epic=EPIC&resolution=DAY','/api/chart/trades?epic=EPIC','/api/chart/drawings?epic=EPIC']:
            path=endpoint+('&' if '?' in endpoint else '?')+'profile='+identity
            status,data=self.request(path);self.assertEqual(status,200,endpoint);self.assertIn(identity.encode(),data,endpoint)
    def test_drawings_write_routes_to_selected_account(self):
        status,data=self.request('/api/chart/drawings?epic=EPIC&profile=p_0123456789abcdef','PUT',json.dumps({'epic':'EPIC','revision':0,'drawings':[]}))
        self.assertEqual(status,200);self.assertIn(b'p_0123456789abcdef',data)
        self.fleet.get('default').charts.save_drawings.assert_not_called()
    def test_unknown_and_duplicate_profile_fail_closed(self):
        for path,code in [('/api/state?profile=missing',404),('/api/state?profile=',400),('/api/state?profile=default&profile=other',400)]:
            self.assertEqual(self.request(path)[0],code)
    def test_all_profiles_require_viewer_auth_and_keep_business_writes_blocked(self):
        for path in ['/api/accounts','/api/state?profile=p_0123456789abcdef','/api/ledger?profile=default']:
            self.assertEqual(self.request(path,auth=False)[0],401)
        self.assertEqual(self.request('/api/accounts')[0],200)
        self.assertEqual(self.request('/api/configure','POST','{}')[0],405)
        self.assertEqual(self.request('/positions/otc','POST','{}')[0],405)

if __name__=='__main__':unittest.main()
