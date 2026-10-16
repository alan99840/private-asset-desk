"""Offline tests: no IG login, market requests, or real account data."""
import http.client
import json
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chart_history import ChartHistory, ChartError, normalize_prices, validate_drawings, older_window, infer_query_offset
from ig_client import IGClient, IGError

EPIC = 'IX.D.SYNTHETIC.CFD.IP'


def price(stamp, value=10, **extra):
    try:dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:dt = datetime.strptime(stamp, '%Y/%m/%d %H:%M:%S')
    if dt.tzinfo is not None:dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return {'snapshotTimeUTC': stamp, 'snapshotTime': dt.strftime('%Y/%m/%d %H:%M:%S'),
            'openPrice': {'bid': value, 'ask': value+1},
            'highPrice': {'bid': value+2, 'ask': value+3},
            'lowPrice': {'bid': value-2, 'ask': value-1},
            'closePrice': {'bid': value+1, 'ask': value+2}, **extra}


def response(rows, remaining=9500, page_number=1, total_pages=1, page_size=200):
    return {'prices': rows, 'metadata': {'allowance': {'remainingAllowance': remaining,
            'totalAllowance': 10000, 'allowanceExpiry': 3600},
            'pageData': {'pageNumber': page_number, 'pageSize': page_size, 'totalPages': total_pages}}}


class FakeClient:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def request(self, method, path, version, params=None):
        self.calls.append((method, path, version, params))
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def monitor(client):
    return SimpleNamespace(lock=threading.RLock(), scope='demo:synthetic-account', client=client,
                           market_info={EPIC: {}}, watch=[], state={'positions': []})


def drawing(identity='line1', **extra):
    return {'id': identity, 'type': 'trend', 'points': [{'time': 1704067200, 'price': 10},
            {'time': 1704067260, 'price': 12}], 'color': '#14AABB', 'width': 2, **extra}


class NormalizationTests(unittest.TestCase):
    def test_utc_bid_and_duplicate_update(self):
        rows = [price('2024/01/01 00:00:00', 10, lastTradedVolume=None),
                price('2024-01-01T01:00:00+01:00', 20, lastTradedVolume=3),
                price('2024-01-01T00:01:00Z', -4)]
        candles, skipped = normalize_prices(rows)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0], {'time': 1704067200, 'open': 20, 'high': 22,
                                     'low': 18, 'close': 21, 'volume': 3})
        self.assertEqual(candles[1]['open'], -4)
        self.assertNotIn('volume', candles[1])

    def test_missing_utc_bid_and_invalid_ohlc_are_not_invented(self):
        missing_utc = price('2024/01/01 00:00:00');del missing_utc['snapshotTimeUTC']
        missing_bid = price('2024/01/01 00:00:00');missing_bid['closePrice'] = {'ask': 5}
        invalid = price('2024/01/01 00:00:00');invalid['highPrice']['bid'] = 0
        nan = price('2024/01/01 00:00:00');nan['openPrice']['bid'] = float('nan')
        candles, skipped = normalize_prices([missing_utc, missing_bid, invalid, nan])
        self.assertEqual(candles, [])
        self.assertEqual(skipped, 4)
        with self.assertRaises(IGError):
            normalize_prices(None)

    def test_query_offset_comes_only_from_paired_ig_timestamps(self):
        row = price('2026-10-16T01:56:00', snapshotTime='2026/10/16 09:56:00')
        policy = infer_query_offset([row])
        self.assertEqual(policy['offset_seconds'], 8*3600)
        self.assertIsNone(infer_query_offset([{'snapshotTime': '2026/10/16 09:56:00'}]))
        mixed = infer_query_offset([row, price('2026-10-16T01:56:00')])
        self.assertIsNone(mixed['offset_seconds'])
        self.assertEqual(mixed['observed_offsets'], [0, 28800])

    def test_prices_allowlist_is_read_only_and_no_extra_path(self):
        IGClient.validate_path('GET', '/prices/' + EPIC + '?resolution=DAY')
        for method, path in [('POST', '/prices/'+EPIC), ('GET', '/prices/../accounts'),
                             ('GET', '/prices/'+EPIC+'/DAY'), ('GET', '/prices/x%2fy')]:
            with self.assertRaises(IGError):
                IGClient.validate_path(method, path)


class ChartCacheTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(response([price('2024-01-01T00:01:00Z'), price('2024-01-01T00:02:00Z')]))
        self.monitor = monitor(self.client)
        self.charts = ChartHistory(self.monitor, ':memory:', start_worker=False)

    def tearDown(self):
        self.charts.close()
        self.charts.db.close()

    def test_poll_dedup_paging_and_cache_survives_disconnect(self):
        for _ in range(5):
            self.assertEqual(self.charts.get(EPIC, 'MINUTE')['status'], 'loading')
        self.assertEqual(self.charts.jobs.qsize(), 1)
        self.charts.process_pending()
        first = self.charts.get(EPIC, 'MINUTE')
        self.assertEqual(first['status'], 'ready')
        self.assertEqual(len(first['candles']), 2)
        self.assertEqual(self.client.calls[0][:3], ('GET', '/prices/'+EPIC, 3))
        self.assertEqual(self.client.calls[0][3], {'resolution': 'MINUTE', 'max': 200, 'pageSize': 0})
        before = first['earliest']
        self.client.results.append(response([price('2024-01-01T00:00:00Z')]))
        self.assertEqual(self.charts.get(EPIC, 'MINUTE', before)['status'], 'loading')
        self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(self.charts.jobs.qsize(), 1)
        self.charts.process_pending()
        old = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(len(old['candles']), 3)
        self.assertEqual(self.client.calls[1][3]['to'], '2024-01-01T00:00:59')
        self.assertEqual(self.client.calls[1][3]['from'], '2023-12-31T20:41:00')
        self.assertEqual(self.client.calls[1][3]['pageSize'], 200)
        self.assertEqual(self.client.calls[1][3]['pageNumber'], 1)
        self.assertNotIn('max', self.client.calls[1][3])
        self.assertFalse(self.charts.process_pending())
        self.client.results.append(response([price('2024-01-01T00:00:00Z', 40)]))
        key = (self.monitor.scope, EPIC, 'MINUTE')
        with self.charts.lock, self.charts.db:
            meta = self.charts._meta(key);meta['last_success'] = time.time()-301
            self.charts._put_meta(key, meta)
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        updated = self.charts.get(EPIC, 'MINUTE')
        self.assertEqual(len(updated['candles']), 3)
        self.assertEqual(updated['candles'][0]['open'], 40)
        self.assertLess(self.client.calls[-1][3]['max'], 10)
        self.monitor.client = None
        with self.charts.lock, self.charts.db:
            meta = self.charts._meta(key);meta['last_success'] = 0
            self.charts._put_meta(key, meta)
        offline = self.charts.get(EPIC, 'MINUTE')
        self.assertEqual(offline['status'], 'error')
        self.assertEqual(len(offline['candles']), 3)

    def test_monitored_scope_and_all_eight_resolutions(self):
        for resolution in ['MINUTE', 'MINUTE_5', 'MINUTE_15', 'MINUTE_30', 'HOUR', 'DAY', 'WEEK', 'MONTH']:
            self.assertEqual(self.charts.get(EPIC, resolution)['status'], 'loading')
        for epic, res in [('UNMONITORED', 'DAY'), (EPIC, 'SECOND'), ('../accounts', 'DAY')]:
            with self.assertRaises(ChartError):
                self.charts.get(epic, res)
        self.assertEqual(self.charts.jobs.qsize(), 8)
        self.monitor.scope = 'live:different-account'
        self.charts.process_pending()
        self.assertEqual(len(self.client.calls), 0)
        self.assertEqual(self.charts.get(EPIC, 'MINUTE')['candles'], [])

    def test_global_allowance_and_reserve_prevent_background_exhaustion(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        key = (self.monitor.scope, EPIC, 'MINUTE')
        with self.charts.lock, self.charts.db:
            meta = self.charts._meta(key);meta['last_success'] = 0;self.charts._put_meta(key, meta)
            self.charts.db.execute('UPDATE chart_allowance SET payload=? WHERE scope=?',
                                  (json.dumps({'remainingAllowance': 500, 'totalAllowance': 10000,
                                               '_expires_at': time.time()+3600}), self.monitor.scope))
        self.assertEqual(self.charts.get(EPIC, 'MINUTE')['status'], 'limited')
        self.assertEqual(self.charts.jobs.qsize(), 0)
        self.client.results.append(IGError('error.public-api.exceeded-account-historical-data-allowance', 403))
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        self.assertEqual(self.charts.get(EPIC, 'MINUTE', before)['status'], 'limited')
        # The quota is shared across all resolutions, not reset per browser/chart.
        self.assertEqual(self.charts.get(EPIC, 'HOUR')['status'], 'limited')
        self.assertEqual(self.charts.jobs.qsize(), 0)

    def test_rest_rate_limit_is_not_confused_with_historical_period(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        now = time.time()
        self.charts._failure((self.monitor.scope, EPIC, 'HOUR'), 'error.public-api.exceeded-account-allowance')
        with self.charts.lock:
            allowance = self.charts._allowance(self.monitor.scope)
        self.assertLess(allowance['_blocked_until'], now+100)
        with patch('chart_history.time.time', return_value=now+120):
            self.assertEqual(self.charts._budget(self.monitor.scope, 200, False), 200)

    def test_minute_ttl_and_small_incremental_batch_preserve_allowance(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        self.client.results.append(response([price('2024-01-01T00:02:00Z')]))
        now = time.time()
        with patch('chart_history.time.time', return_value=now+59):
            self.assertEqual(self.charts.get(EPIC, 'MINUTE')['status'], 'ready')
        with patch('chart_history.time.time', return_value=now+61):
            self.assertEqual(self.charts.get(EPIC, 'MINUTE')['status'], 'loading')
            self.charts.process_pending()
        self.assertEqual(self.client.calls[-1][3]['max'], 3)
        self.client.results.append(response([price('2024-01-01T00:02:00Z')]))
        self.charts.get(EPIC, 'MINUTE_5');self.charts.process_pending()
        now = time.time()
        with patch('chart_history.time.time', return_value=now+61):
            self.assertEqual(self.charts.get(EPIC, 'MINUTE_5')['status'], 'ready')

    def test_empty_date_windows_do_not_mean_history_ended(self):
        self.charts.get(EPIC, 'DAY');self.charts.process_pending()
        before = self.charts.get(EPIC, 'DAY')['earliest']
        self.client.results.extend([response([], total_pages=0) for _ in range(6)])
        self.charts.get(EPIC, 'DAY', before);self.charts.process_pending()
        out = self.charts.get(EPIC, 'DAY', before)
        self.assertTrue(out['has_more']);self.assertEqual(out['status'], 'ready')
        self.assertLess(out['next_before'], before)
        self.assertIsInstance(out['next_cursor'], str)
        self.assertEqual(self.charts.jobs.qsize(), 0)
        self.assertEqual(len(self.client.calls), 7)
        # The next explicit click, rather than a repeated poll, advances search.
        self.charts.get(EPIC, 'DAY', out['next_before'], out['next_cursor'])
        self.assertEqual(self.charts.jobs.qsize(), 1)
        self.charts.jobs.get_nowait();self.charts.pending.clear()
        self.charts.jobs.task_done()
        self.client.results.append(IGError('ig.network.or.tls.error'))
        self.charts.get(EPIC, 'HOUR');self.charts.process_pending()
        for _ in range(3):
            self.assertEqual(self.charts.get(EPIC, 'HOUR')['status'], 'error')
        self.assertEqual(self.charts.jobs.qsize(), 0)

    def test_ignored_history_upper_bound_is_not_polled_again(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        self.client.results.append(response([price('2024-01-01T00:02:00Z')]))
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        key = (self.monitor.scope, EPIC, 'MINUTE')
        with self.charts.lock, self.charts.db:
            meta = self.charts._meta(key);meta['retry_at'] = 0;self.charts._put_meta(key, meta)
        for _ in range(3):
            result = self.charts.get(EPIC, 'MINUTE', before)
            self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error'], 'ig.chart.range.bound.ignored')
        self.assertEqual(self.charts.jobs.qsize(), 0)

    def test_v3_date_ranges_fix_api_that_ignores_to_only(self):
        recent = response([price('2024-01-01T00:01:00Z')])
        older = response([price('2024-01-01T00:00:00Z')], remaining=9600)
        class IgnoreToOnly:
            def __init__(self):self.calls = []
            def request(inner, method, path, version, params=None):
                inner.calls.append(params)
                return older if 'from' in params and 'to' in params else recent
        client = IgnoreToOnly();self.monitor.client = client
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        result = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(len(result['candles']), 2)
        self.assertLess(result['earliest'], before)
        self.assertIn('from', client.calls[1]);self.assertIn('to', client.calls[1])
        self.assertNotIn('max', client.calls[1])
        self.assertEqual(result['allowance']['remainingAllowance'], 9600)

    def test_multpage_window_is_staged_until_complete_and_each_click_is_bounded(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        initial = self.charts.get(EPIC, 'MINUTE');before = initial['earliest']
        self.client.results.append(response([price('2023-12-31T22:00:00Z')], total_pages=2))
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        first_page = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(first_page['candles'], initial['candles'])
        self.assertTrue(first_page['range_pending'])
        self.assertEqual(first_page['next_before'], before)
        self.assertTrue(first_page['next_cursor'].endswith(':p2'))
        self.assertEqual(self.charts.jobs.qsize(), 0)
        self.client.results.append(response([price('2023-12-31T23:00:00Z')], page_number=2, total_pages=2))
        cursor = first_page['next_cursor']
        self.charts.get(EPIC, 'MINUTE', before, cursor);self.charts.process_pending()
        complete = self.charts.get(EPIC, 'MINUTE', before, cursor)
        self.assertEqual(len(complete['candles']), 4)
        self.assertFalse(complete['range_pending']);self.assertIsNone(complete['next_cursor'])
        self.assertEqual(complete['next_before'], complete['earliest'])
        self.assertEqual(self.client.calls[-1][3]['pageNumber'], 2)
        self.assertEqual(self.client.calls[-1][3]['from'], self.client.calls[-2][3]['from'])
        self.assertEqual(self.client.calls[-1][3]['to'], self.client.calls[-2][3]['to'])
        self.assertLessEqual(self.client.calls[-1][3]['pageSize'], 200)
        self.assertEqual(self.charts.jobs.qsize(), 0)

    def test_closed_empty_window_is_skipped_without_losing_older_data(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        self.client.results.extend([response([], total_pages=0), response([price('2023-12-31T20:00:00Z')])])
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        result = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(result['status'], 'ready');self.assertTrue(result['has_more'])
        self.assertEqual(len(result['candles']), 3)
        self.assertEqual(len(self.client.calls), 3)
        self.assertEqual(self.client.calls[2][3]['to'], '2023-12-31T20:40:59')

    def test_legacy_to_only_failure_migrates_without_deleting_real_candles(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        key = (self.monitor.scope, EPIC, 'MINUTE')
        with self.charts.lock, self.charts.db:
            meta = self.charts._meta(key)
            meta.pop('paging_version', None)
            meta.update(error='ig.chart.upper.bound.ignored', retry_at=time.time()+86400,
                        cursor_errors={str(before): 'ig.chart.upper.bound.ignored'})
            self.charts._put_meta(key, meta)
        self.client.results.append(response([price('2024-01-01T00:00:00Z')]))
        self.assertEqual(self.charts.get(EPIC, 'MINUTE', before)['status'], 'loading')
        self.charts.process_pending()
        result = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(len(result['candles']), 3);self.assertEqual(result['status'], 'ready')
        self.assertNotIn('error', result)

    def test_observed_account_local_time_is_used_for_range_query_and_utc_storage(self):
        self.client.results = [response([price('2026-10-16T13:16:00', snapshotTime='2026/10/16 21:16:00')]),
                               response([price('2026-10-16T13:15:00', snapshotTime='2026/10/16 21:15:00')])]
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        params = self.client.calls[-1][3]
        self.assertEqual(params['from'], '2026-10-16T17:56:00')
        self.assertEqual(params['to'], '2026-10-16T21:15:59')
        result = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['candles'][0]['time'], 1792156500)
        self.assertEqual(len(result['candles']), 2)

    def test_pre_timezone_cache_bootstrap_spends_one_plus_at_most_199_points(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        key = (self.monitor.scope, EPIC, 'MINUTE')
        with self.charts.lock, self.charts.db:
            self.charts.db.execute('DELETE FROM chart_settings')
            meta = self.charts._meta(key)
            meta.update(paging_version=2, error='ig.chart.range.bound.ignored', retry_at=time.time()+86400,
                        cursor_errors={'before:'+str(before): 'ig.chart.range.bound.ignored'})
            self.charts._put_meta(key, meta)
        self.client.results.extend([response([price('2024-01-01T00:02:00Z')], page_size=1),
                                    response([price('2024-01-01T00:00:00Z')], page_size=199)])
        self.assertEqual(self.charts.get(EPIC, 'MINUTE', before)['status'], 'loading')
        self.charts.process_pending()
        result = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(result['status'], 'ready');self.assertEqual(len(result['candles']), 3)
        self.assertEqual(self.client.calls[-2][3]['max'], 1)
        self.assertEqual(self.client.calls[-1][3]['pageSize'], 199)
        self.assertNotIn('max', self.client.calls[-1][3])

    def test_changed_query_timezone_is_rejected_and_does_not_shift_cached_utc(self):
        self.client.results = [response([price('2024-01-01T00:01:00Z', snapshotTime='2024/01/01 08:01:00')]),
                               response([price('2024-01-01T00:00:00Z', snapshotTime='2024/01/01 07:00:00')])]
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        initial = self.charts.get(EPIC, 'MINUTE');before = initial['earliest']
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        result = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(result['error'], 'ig.chart.query.timezone.changed')
        self.assertEqual(result['candles'], initial['candles'])
        self.assertEqual(self.charts.jobs.qsize(), 0)

    def test_ambiguous_dst_offsets_do_not_guess_one_for_older_windows(self):
        self.client.results = [response([price('2024-01-01T00:01:00Z'),
                                         price('2024-07-01T00:01:00Z', snapshotTime='2024/07/01 01:01:00')])]
        self.charts.get(EPIC, 'MONTH');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MONTH')['earliest']
        self.charts.get(EPIC, 'MONTH', before);self.charts.process_pending()
        result = self.charts.get(EPIC, 'MONTH', before)
        self.assertEqual(result['error'], 'ig.chart.query.timezone.ambiguous')
        self.assertEqual(len(self.client.calls), 1)

    def test_calendar_windows_and_explicit_search_floor(self):
        epoch = int(datetime(2024, 3, 31, tzinfo=timezone.utc).timestamp())
        month = older_window(epoch, 'MONTH', 2)
        self.assertEqual(datetime.fromtimestamp(month['from'], timezone.utc).isoformat(), '2024-02-29T00:00:00+00:00')
        day = older_window(epoch, 'DAY', 200)
        week = older_window(epoch, 'WEEK', 200)
        self.assertEqual(epoch-day['from'], 199*86400)
        self.assertEqual(epoch-week['from'], 199*604800)
        self.assertEqual(older_window(100, 'MONTH', 200)['from'], 0)

    def test_pending_page_cannot_spend_more_than_remaining_points(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        self.client.results.append(response([price('2023-12-31T22:00:00Z')], remaining=100, total_pages=2))
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        first = self.charts.get(EPIC, 'MINUTE', before)
        result = self.charts.get(EPIC, 'MINUTE', first['next_before'], first['next_cursor'])
        self.assertEqual(result['status'], 'limited')
        self.assertEqual(self.charts.jobs.qsize(), 0)

    def test_invalid_price_rows_do_not_trigger_six_paid_windows(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        before = self.charts.get(EPIC, 'MINUTE')['earliest']
        invalid = price('2024-01-01T00:00:00Z');invalid['openPrice']['bid'] = None
        self.client.results.append(response([invalid]*200))
        self.charts.get(EPIC, 'MINUTE', before);self.charts.process_pending()
        result = self.charts.get(EPIC, 'MINUTE', before)
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(result['skipped_rows'], 200)
        self.assertTrue(result['has_more']);self.assertIsNotNone(result['next_cursor'])

    def test_long_offline_refresh_flags_unverified_internal_history_gap(self):
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        key = (self.monitor.scope, EPIC, 'MINUTE')
        with self.charts.lock, self.charts.db:
            meta = self.charts._meta(key);meta['last_success'] = 0;self.charts._put_meta(key, meta)
        self.client.results.append(response([price('2024-01-02T00:00:00Z'), price('2024-01-02T00:01:00Z')]))
        self.charts.get(EPIC, 'MINUTE');self.charts.process_pending()
        result = self.charts.get(EPIC, 'MINUTE')
        self.assertEqual(len(result['candles']), 4)
        self.assertEqual(result['gaps'], [{'from': 1704067320, 'to': 1704153600, 'status': 'unverified'}])
        self.assertIn('尚未核对', result['message'])
        self.assertEqual(len(self.client.calls), 2)

    def test_disk_persistence_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'charts.sqlite3'
            cache = ChartHistory(self.monitor, path, start_worker=False)
            cache.get(EPIC, 'MONTH');cache.process_pending()
            cache.save_drawings(EPIC, 0, [drawing()])
            cache.db.close()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            cache = ChartHistory(self.monitor, path, start_worker=False)
            self.assertEqual(len(cache.get(EPIC, 'MONTH')['candles']), 2)
            self.assertEqual(cache.drawings(EPIC)['revision'], 1)
            cache.db.close()


class DrawingTests(unittest.TestCase):
    def test_revision_and_account_isolation(self):
        m = monitor(None);cache = ChartHistory(m, ':memory:', start_worker=False)
        self.assertEqual(cache.drawings(EPIC)['revision'], 0)
        saved, ok = cache.save_drawings(EPIC, 0, [drawing()])
        self.assertTrue(ok);self.assertEqual(saved['revision'], 1)
        stale, ok = cache.save_drawings(EPIC, 0, [])
        self.assertFalse(ok);self.assertEqual(stale, saved)
        m.scope = 'live:different-account'
        self.assertEqual(cache.drawings(EPIC)['drawings'], [])
        cache.db.close()

    def test_untrusted_drawing_content(self):
        good = validate_drawings([drawing()])[0]
        self.assertEqual(good['color'], '#14aabb')
        self.assertEqual(len(validate_drawings([drawing(type='horizontal', points=[{'time': 1, 'price': 0}])])), 1)
        bad = [drawing(width=True), drawing(width=6), drawing(type='script'), drawing(color='url(x)'),
               drawing(points=[{'time': 1, 'price': float('inf')}, {'time': 2, 'price': 1}]),
               drawing(points=[{'time': 1, 'price': 1}]), drawing(id='<script>'), drawing(extra='ignored')]
        for item in bad:
            with self.assertRaises(ChartError):validate_drawings([item])
        for items in [[drawing(), drawing()], [drawing(str(i)) for i in range(201)], [drawing(type='pen', points=[{'time': 1, 'price': 1}]*1001)]]:
            with self.assertRaises(ChartError):validate_drawings(items)


class ChartHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server;cls.temp = tempfile.TemporaryDirectory()
        cls.access_patch = patch.object(server, 'ACCESS_FILE', Path(cls.temp.name)/'access.json');cls.access_patch.start()
        server.set_viewer_password('synthetic-chart-password')
        cls.cache = ChartHistory(monitor(None), ':memory:', start_worker=False)
        cls.charts_patch = patch.object(server, 'CHARTS', cls.cache);cls.charts_patch.start()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True);cls.thread.start()
        config = server.access_config()
        cls.cookie = server.COOKIE+'='+server.session_token(config)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join()
        cls.cache.db.close();cls.charts_patch.stop();cls.access_patch.stop();cls.temp.cleanup()

    def request(self, path, method='GET', payload=None, auth=True, origin='http://monitor.example', **headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        sent = {'Host': 'monitor.example', **headers}
        if auth:sent['Cookie'] = self.cookie
        if origin is not None:sent['Origin'] = origin
        if payload is not None:
            sent['Content-Type'] = 'application/json';payload = json.dumps(payload).encode()
        conn.request(method, path, payload, sent)
        reply = conn.getresponse();status = reply.status;raw = reply.read();conn.close()
        return status, json.loads(raw) if raw else None

    def test_chart_and_drawings_require_password(self):
        for path in ['/api/chart?epic='+EPIC+'&resolution=DAY', '/api/chart/drawings?epic='+EPIC]:
            self.assertEqual(self.request(path, auth=False)[0], 401)
        self.assertEqual(self.request('/api/chart/drawings?epic='+EPIC, 'PUT', {}, auth=False)[0], 401)

    def test_chart_validation_and_cached_no_connection_response(self):
        self.assertEqual(self.request('/api/chart?epic='+EPIC+'&resolution=DAY')[1]['status'], 'error')
        for query in ['epic=x&resolution=DAY', 'epic='+EPIC+'&resolution=SECOND',
                      'epic='+EPIC+'&resolution=DAY&before=nan', 'epic='+EPIC+'&resolution=DAY&resolution=DAY']:
            self.assertIn(self.request('/api/chart?'+query)[0], (400, 403))

    def test_put_origin_validation_revision_and_read_only_other_endpoints(self):
        path = '/api/chart/drawings?epic='+EPIC
        payload = {'epic': EPIC, 'revision': 0, 'drawings': [drawing()]}
        for origin in [None, 'null', 'https://other.example']:
            self.assertEqual(self.request(path, 'PUT', payload, origin=origin)[0], 403)
        status, result = self.request(path, 'PUT', payload)
        self.assertEqual(status, 200);self.assertEqual(result['revision'], 1)
        status, conflict = self.request(path, 'PUT', payload)
        self.assertEqual(status, 409);self.assertEqual(conflict['drawings'], result['drawings'])
        self.assertEqual(conflict['error'], 'drawing_revision_conflict')
        self.assertEqual(self.request(path, 'PUT', dict(payload, revision=1, drawings=[drawing(width=0)]))[0], 400)
        self.assertEqual(self.request('/api/state', 'PUT', payload)[0], 405)
        self.assertEqual(self.request('/api/chart/drawings?epic=UNKNOWN', 'PUT', dict(payload, epic='UNKNOWN'))[0], 403)
        self.assertEqual(self.request(path, 'PUT', dict(payload, drawings=[drawing(type='pen', points=[{'time': 1, 'price': 1}]*5000)]))[0], 413)


if __name__ == '__main__':
    unittest.main()
