"""Synthetic cache/API checks: never log in, fetch IG data or read real state."""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accounts import AccountFleet
from chart_history import ChartHistory, ChartError

EPIC = 'IX.D.SYNTHETIC.CFD.IP'
OTHER = 'IX.D.OTHER.CFD.IP'
SECOND = 'p_0123456789abcdef'
START = 1704067200


def fixture_monitor():
    client = SimpleNamespace(request=Mock(side_effect=AssertionError('Research must never call IG')))
    return SimpleNamespace(lock=threading.RLock(), scope='live:synthetic-same-account-id',
                           client=client, market_info={EPIC: {}}, watch=[], state={'positions': []})


def seed(charts, count=180, start=START, value=100, scope=None, epic=EPIC, resolution='HOUR'):
    scope = scope or charts.monitor.scope
    rows = []
    for i in range(count):
        price = value + i * .2
        candle = dict(time=start+i*3600, open=price, high=price+.5, low=price-.5, close=price+.1)
        candle['ask'] = {k: candle[k]+.05 for k in ('open', 'high', 'low', 'close')}
        rows.append((scope, epic, resolution, candle['time'], json.dumps(candle)))
    with charts.db:
        charts.db.executemany('INSERT INTO chart_candles VALUES (?,?,?,?,?)', rows)
        charts._put_meta((scope, epic, resolution), {'updated_at': '2024-01-09T00:00:00Z',
                         'paging_version': 0, 'next_before': start, 'has_more': True,
                         'status': 'limited', 'error': 'synthetic.limit', 'skipped_rows': 0})


class StrategySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.monitor = fixture_monitor()
        self.charts = ChartHistory(self.monitor, ':memory:', start_worker=False)

    def tearDown(self):
        self.charts.close()
        self.charts.db.close()

    def test_all_cached_rows_without_fetch_queue_or_paging_migration(self):
        seed(self.charts, count=350)
        with self.charts.db:
            self.charts.db.execute('INSERT INTO chart_allowance VALUES (?,?)',
                                  (self.monitor.scope, json.dumps({'remainingAllowance': 321,
                                   'totalAllowance': 10000, '_blocked_until': 9999999999})))
        key = (self.monitor.scope, EPIC, 'HOUR')
        original_meta = self.charts._meta(key)
        original_changes = self.charts.db.total_changes
        with patch.object(self.charts, 'get', side_effect=AssertionError('No live chart read')), \
             patch.object(self.charts, '_upgrade_paging', side_effect=AssertionError('No metadata upgrade')), \
             patch.object(self.charts, '_put_meta', side_effect=AssertionError('No metadata write')):
            out = self.charts.snapshot(EPIC, 'HOUR')
        self.assertEqual(len(out['candles']), 350)
        self.assertEqual(out['candles'][0]['time'], START)
        self.assertEqual(out['candles'][-1]['time'], START+349*3600)
        self.assertEqual(out['status'], 'cached')
        self.assertEqual(out['last_fetch_status'], 'limited')
        self.assertEqual(out['last_fetch_error'], 'synthetic.limit')
        self.assertEqual(out['allowance'], {'remainingAllowance': 321, 'totalAllowance': 10000})
        self.assertEqual(self.charts._meta(key), original_meta)
        self.assertEqual(self.charts.db.total_changes, original_changes)
        self.assertEqual(self.charts.jobs.qsize(), 0)
        self.assertEqual(self.charts.pending, set())
        self.monitor.client.request.assert_not_called()

    def test_empty_cache_and_disconnected_client_never_trigger_fetch(self):
        self.monitor.client = None
        out = self.charts.snapshot(EPIC, 'HOUR')
        self.assertEqual(out['candles'], [])
        self.assertEqual(out['status'], 'cached')
        self.assertEqual(self.charts.jobs.qsize(), 0)
        seed(self.charts)
        self.assertEqual(len(self.charts.snapshot(EPIC, 'HOUR')['candles']), 180)

    def test_exact_scope_epic_and_resolution_are_isolated(self):
        seed(self.charts, count=2)
        seed(self.charts, count=3, scope='demo:another-scope', value=500)
        seed(self.charts, count=4, epic=OTHER, value=900)
        seed(self.charts, count=5, resolution='DAY', value=1100)
        out = self.charts.snapshot(EPIC, 'HOUR')
        self.assertEqual(len(out['candles']), 2)
        self.assertEqual(out['candles'][0]['open'], 100)
        self.monitor.scope = 'demo:another-scope'
        out = self.charts.snapshot(EPIC, 'HOUR')
        self.assertEqual(len(out['candles']), 3)
        self.assertEqual(out['candles'][0]['open'], 500)
        self.monitor.scope = 'live:no-cache-yet'
        self.assertEqual(self.charts.snapshot(EPIC, 'HOUR')['candles'], [])

    def test_unknown_product_resolution_and_unidentified_account_fail_closed(self):
        for epic, resolution, status in [(OTHER, 'HOUR', 403), (EPIC, 'SECOND', 400),
                                         ('../private', 'HOUR', 400)]:
            with self.subTest(epic=epic, resolution=resolution):
                with self.assertRaises(ChartError) as error:
                    self.charts.snapshot(epic, resolution)
                self.assertEqual(error.exception.status, status)
        self.monitor.scope = None
        with self.assertRaises(ChartError) as error:
            self.charts.snapshot(EPIC, 'HOUR')
        self.assertEqual(error.exception.status, 503)


class StrategyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.monitor = fixture_monitor()
        self.charts = ChartHistory(self.monitor, ':memory:', start_worker=False)
        self.key = (self.monitor.scope, EPIC, 'HOUR')

    def tearDown(self):
        self.charts.close()
        self.charts.db.close()

    def allowance(self, remaining, **extra):
        with self.charts.db:
            self.charts.db.execute('INSERT OR REPLACE INTO chart_allowance VALUES (?,?)',
                                   (self.monitor.scope, json.dumps(dict(remainingAllowance=remaining,
                                    totalAllowance=10000, **extra))))

    def discard_job(self):
        job = self.charts.jobs.get_nowait()
        self.charts.pending.discard(job[0])
        self.charts.jobs.task_done()
        return job

    def test_concurrent_refresh_deduplicates_one_bounded_job(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.charts.refresh_research(EPIC, 'HOUR'), range(20)))
        self.assertTrue(all(x['status'] == 'loading' for x in results))
        self.assertEqual(self.charts.jobs.qsize(), 1)
        self.assertEqual(self.charts.pending, {self.key})
        job = self.discard_job()
        self.assertEqual(job, (self.key, self.monitor.client, None, 200, None))
        self.monitor.client.request.assert_not_called()

    def test_refresh_throttle_is_120_seconds_after_queue_completion(self):
        epoch = START+100000
        with patch('chart_history.time.time', return_value=epoch):
            self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'loading')
        self.discard_job()
        for elapsed in (0, 119):
            with patch('chart_history.time.time', return_value=epoch+elapsed):
                self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'recent')
                self.assertEqual(self.charts.jobs.qsize(), 0)
        with patch('chart_history.time.time', return_value=epoch+120):
            self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'loading')
        self.monitor.client.request.assert_not_called()

    def test_low_allowance_retry_and_full_queue_do_not_schedule(self):
        self.allowance(699)  # 500 reserved; fewer than 200 research points remain.
        self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'limited')
        self.assertEqual(self.charts.jobs.qsize(), 0)
        self.assertEqual(self.charts.pending, set())
        self.assertNotIn('research_refresh_at', self.charts._meta(self.key))
        self.allowance(10000, _blocked_until=time.time()+1000)
        self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'limited')
        self.allowance(10000)
        with self.charts.db:
            self.charts._put_meta(self.key, {'retry_at': time.time()+1000, 'message': 'Synthetic retry'})
        self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'limited')
        with self.charts.db:
            self.charts._put_meta(self.key, {})
        for _ in range(self.charts.jobs.maxsize):
            self.charts.jobs.put_nowait(None)
        self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'limited')
        self.assertEqual(self.charts.pending, set())
        self.assertNotIn('research_refresh_at', self.charts._meta(self.key))
        self.monitor.client.request.assert_not_called()

    def test_adequate_allowance_queues_only_this_product_and_period(self):
        self.allowance(700)
        result = self.charts.refresh_research(EPIC, 'HOUR')
        self.assertEqual(result['status'], 'loading')
        job = self.discard_job()
        self.assertEqual(job[0], self.key)
        self.assertEqual(job[3], 200)
        self.assertEqual(self.charts.jobs.qsize(), 0)

    def test_explicit_refresh_uses_one_read_only_ig_request_and_retains_throttle(self):
        self.monitor.client.request.side_effect = None
        self.monitor.client.request.return_value = {'prices': [], 'metadata': {}}
        self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'loading')
        self.assertTrue(self.charts.process_pending())
        self.monitor.client.request.assert_called_once_with('GET', '/prices/'+EPIC, 3,
                                                           params={'resolution': 'HOUR', 'max': 200, 'pageSize': 0})
        self.assertEqual(self.charts.pending, set())
        self.assertEqual(self.charts.jobs.qsize(), 0)
        self.assertEqual(self.charts.refresh_research(EPIC, 'HOUR')['status'], 'recent')

    def test_allowance_drop_after_enqueue_preserves_collector_reserve(self):
        self.allowance(700)
        self.charts.refresh_research(EPIC, 'HOUR')
        # Another already queued request consumes part of the available quota.
        self.allowance(550)
        self.monitor.client.request.side_effect = None
        self.monitor.client.request.return_value = {'prices': [], 'metadata': {}}
        self.charts.process_pending()
        if self.monitor.client.request.called:
            self.assertLessEqual(self.monitor.client.request.call_args.kwargs['params']['max'], 50)
        self.assertEqual(self.charts.pending, set())

    def test_missing_client_unknown_product_and_invalid_period_fail_closed(self):
        for epic, period, expected in [(OTHER, 'HOUR', 403), (EPIC, 'SECOND', 400)]:
            with self.assertRaises(ChartError) as error:
                self.charts.refresh_research(epic, period)
            self.assertEqual(error.exception.status, expected)
        self.monitor.client = None
        with self.assertRaises(ChartError) as error:
            self.charts.refresh_research(EPIC, 'HOUR')
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.charts.jobs.qsize(), 0)


class StrategyEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.module = server
        cls.temp = tempfile.TemporaryDirectory()
        cls.access = patch.object(server, 'ACCESS_FILE', Path(cls.temp.name)/'viewer.json')
        cls.access.start()
        server.set_viewer_password('synthetic-tests-only')
        cls.cookie = server.COOKIE+'='+server.session_token(server.access_config())
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.access.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.fleet = AccountFleet()
        for i, identity in enumerate(('default', SECOND)):
            monitor = fixture_monitor()
            charts = ChartHistory(monitor, ':memory:', start_worker=False)
            # Equal broker scope IDs must still remain separate between logins.
            seed(charts, start=START+i*86400, value=100+i*1000)
            self.fleet.bundles[identity] = SimpleNamespace(monitor=monitor, charts=charts,
                                                          trades=Mock(), analytics=Mock())
        self.fleet_patch = patch.object(self.module, 'FLEET', self.fleet)
        self.fleet_patch.start()

    def tearDown(self):
        self.fleet_patch.stop()
        for bundle in self.fleet.bundles.values():
            bundle.monitor.client.request.assert_not_called()
            self.assertEqual(bundle.charts.jobs.qsize(), 0)
            self.assertEqual(bundle.charts.pending, set())
            bundle.charts.close()
            bundle.charts.db.close()

    def request(self, query='', method='GET', auth=True, endpoint='/api/strategy'):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Cookie'] = self.cookie
        body = '{}' if method in ('POST', 'PUT', 'PATCH', 'DELETE') else None
        connection.request(method, endpoint+'?'+query, body, headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        connection.close()
        return status, json.loads(raw) if raw else None

    def test_authentication_precedes_cache_access(self):
        with patch.object(self.fleet.bundles['default'].charts, 'snapshot') as snapshot:
            status, out = self.request('epic='+EPIC, auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(out['error'], 'authentication_required')
        snapshot.assert_not_called()

    def test_profile_route_uses_own_cache_and_actual_engine(self):
        for identity, start in [('default', START), (SECOND, START+86400)]:
            with self.subTest(profile=identity):
                status, out = self.request('epic='+EPIC+'&resolution=HOUR&mode=alignment&profile='+identity)
                self.assertEqual(status, 200)
                self.assertEqual(out['status'], 'READY')
                self.assertEqual(out['coverage']['start'], start)
                self.assertEqual(out['coverage']['bars'], 179)
                self.assertEqual(out['cache']['last_fetch_error'], 'synthetic.limit')
                self.assertNotIn('candles', out['cache'])
                self.assertNotIn('client', out)

    def test_unknown_or_duplicate_profile_is_not_default(self):
        for tail, expected in [('profile=missing', 404), ('profile=', 400),
                               ('profile=default&profile='+SECOND, 400)]:
            with self.subTest(tail=tail):
                self.assertEqual(self.request('epic='+EPIC+'&'+tail)[0], expected)

    def test_unmonitored_epic_and_invalid_query_are_rejected(self):
        self.assertEqual(self.request('epic='+OTHER)[0], 403)
        base = 'epic='+EPIC+'&resolution=HOUR'
        for extra in ['&epic='+EPIC, '&resolution=DAY', '&mode=alignment&mode=breakout20',
                      '&unknown=x', '&risk_pct=0.5&risk_pct=1', '&slippage=', '&mode=',
                      '&stop_atr=2', '&profile=default&profile=default']:
            with self.subTest(extra=extra):
                self.assertEqual(self.request(base+extra)[0], 400)
        for query in ['', 'epic=', 'epic='+EPIC+'&resolution=SECOND']:
            with self.subTest(query=query):
                self.assertEqual(self.request(query)[0], 400)

    def test_numeric_options_must_be_finite_and_bounded(self):
        base = 'epic='+EPIC+'&resolution=HOUR'
        for key in ('risk_pct', 'slippage', 'financing_bps_daily', 'fee_points'):
            for value in ('NaN', 'Infinity', '-Infinity', '-1', '1e1000'):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.request(base+'&'+key+'='+value)[0], 400)
        for option in ('risk_pct=0', 'risk_pct=2.1', 'financing_bps_daily=1001',
                       'slippage=1000001', 'fee_points=1000001'):
            with self.subTest(option=option):
                self.assertEqual(self.request(base+'&'+option)[0], 400)

    def test_study_endpoint_has_no_write_method(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(method=method):
                status, out = self.request('epic='+EPIC, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(out['error'], 'read_only')

    def test_busy_limit_fails_without_reading_cache_and_is_retryable(self):
        slots = threading.BoundedSemaphore(1)
        slots.acquire()
        with patch.object(self.module, 'STUDY_SLOTS', slots), \
             patch.object(self.fleet.bundles['default'].charts, 'snapshot') as snapshot:
            status, out = self.request('epic='+EPIC)
        self.assertEqual(status, 429)
        self.assertEqual(out['error'], 'study_busy')
        snapshot.assert_not_called()
        slots.release()
        with patch.object(self.module, 'STUDY_SLOTS', slots):
            self.assertEqual(self.request('epic='+EPIC+'&risk_pct=NaN')[0], 400)
            self.assertEqual(self.request('epic='+EPIC)[0], 200)

    def test_history_refresh_endpoint_auth_and_exact_query(self):
        endpoint = '/api/strategy/history'
        valid = 'epic='+EPIC+'&resolution=HOUR'
        self.assertEqual(self.request(valid, auth=False, endpoint=endpoint)[0], 401)
        self.assertEqual(self.request(valid+'&profile=missing', endpoint=endpoint)[0], 404)
        self.assertEqual(self.request('epic='+OTHER+'&resolution=HOUR', endpoint=endpoint)[0], 403)
        for query in ['', 'epic='+EPIC, 'resolution=HOUR', valid+'&x=1', valid+'&epic='+EPIC,
                      'epic='+EPIC+'&resolution=SECOND', valid+'&profile=default&profile='+SECOND]:
            with self.subTest(query=query):
                self.assertEqual(self.request(query, endpoint=endpoint)[0], 400)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.request(valid, method=method, endpoint=endpoint)[0], 405)

    def test_history_refresh_routes_to_only_selected_profile_and_deduplicates(self):
        endpoint = '/api/strategy/history'
        query = 'epic='+EPIC+'&resolution=HOUR&profile='+SECOND
        for _ in range(3):
            status, out = self.request(query, endpoint=endpoint)
            self.assertEqual(status, 200)
            self.assertEqual(out['status'], 'loading')
        primary, secondary = (self.fleet.bundles[x].charts for x in ('default', SECOND))
        self.assertEqual(primary.jobs.qsize(), 0)
        self.assertEqual(secondary.jobs.qsize(), 1)
        job = secondary.jobs.get_nowait()
        self.assertIs(job[1], self.fleet.bundles[SECOND].monitor.client)
        self.assertEqual(job[3], 200)
        secondary.pending.discard(job[0])
        secondary.jobs.task_done()
        self.assertEqual(self.request(query, endpoint=endpoint)[1]['status'], 'recent')

    def test_sample_window_enum_validation(self):
        base = 'epic='+EPIC+'&resolution=HOUR&max_bars='
        for value in ('NaN', 'Infinity', '-1', '0', '201', '20000.1', ''):
            with self.subTest(value=value):
                self.assertEqual(self.request(base+value)[0], 400)
        for value in ('200', '500', '2000', '20000'):
            with self.subTest(value=value):
                status, out = self.request(base+value)
                self.assertEqual(status, 200)
                self.assertEqual(out['options']['max_bars'], int(value))


if __name__ == '__main__':
    unittest.main()
