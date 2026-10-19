"""Execution semantics, UTC provenance, deduplication and authenticated HTTP."""
import copy
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from chart_history import ChartError
from trade_markers import TradeMarkers, build_markers, utc_time

EPIC = 'CC.D.SYNTHETIC.IP'
OTHER = 'IX.D.OTHER.IP'
DATE = '2026-10-16T12:00:00'
TIME = 1792152000


def position(deal='open-1', side='BUY', level=100, size=.4, date=DATE, epic=EPIC):
    return {'position': {'dealId': deal, 'direction': side, 'level': level, 'size': size, 'createdDateUTC': date}, 'market': {'epic': epic}}


def activity(action='POSITION_OPENED', side='BUY', level=100, size=1, date=DATE+'Z', deal='open-1', affected='open-1', ref='ref-1', epic=EPIC, status='ACCEPTED'):
    return {'kind': 'activity', 'raw': {'dealId': deal, 'epic': epic, 'date': date, 'period': 'DFB', 'status': status, 'type': 'POSITION',
            'details': {'direction': side, 'level': level, 'size': size, 'dealReference': ref, 'actions': [{'actionType': action, 'affectedDealId': affected}]}}}


def transaction(ref='close-1', opened=DATE, closed='2026-10-16T13:00:00', size='+0.4', open_level='100', close_level='110', cash=False):
    return {'kind': 'transactions', 'raw': {'reference': ref, 'cashTransaction': cash, 'dateUtc': closed, 'openDateUtc': opened, 'size': size,
            'openLevel': open_level, 'closeLevel': close_level, 'period': 'DFB', 'instrumentName': 'SYNTHETIC DISPLAY NAME'}}


class ExecutionNormalizationTests(unittest.TestCase):
    def get(self, positions=(), events=()):
        return build_markers(EPIC, list(positions), list(events))

    def test_current_open_uses_utc_and_original_not_remaining_size(self):
        a = activity(date=DATE)
        out = self.get([position(size=.4)], [a])
        self.assertEqual(len(out['markers']), 1)
        self.assertEqual(out['markers'][0]['time'], TIME)
        self.assertEqual(out['markers'][0]['size'], 1)
        self.assertEqual(out['markers'][0]['action'], 'OPEN')
        self.assertEqual(out['omitted'], [])

    def test_missing_original_quantity_stays_unknown(self):
        row = self.get([position(size=.4)])['markers'][0]
        self.assertIsNone(row['size'])
        self.assertEqual(row['price'], 100)

    def test_conflicting_historical_open_never_draws_second_point_for_current_deal(self):
        out = self.get([position()], [activity(date='2026-10-16T14:00:00Z')])
        self.assertEqual(len(out['markers']), 1)
        self.assertEqual(out['markers'][0]['time'], TIME)
        self.assertIsNone(out['markers'][0]['size'])
        self.assertEqual(out['omitted'][0]['reason'], 'conflicting_open')

    def test_long_and_short_trade_sides_and_partial_quantity(self):
        rows = self.get(events=[activity(), activity(action='POSITION_PARTIALLY_CLOSED', side='SELL', level=110, size=.4, date='2026-10-16T13:00:00Z', deal='close-1'),
                               activity(side='SELL', deal='short-1', affected='short-1', ref='short-ref'),
                               activity(action='POSITION_CLOSED', side='BUY', level=90, size=.5, date='2026-10-16T13:00:01Z', deal='short-close', affected='short-1', ref='short-close-ref')])['markers']
        self.assertEqual([(r['action'], r['side'], r['size']) for r in rows if r['action']=='CLOSE'], [('CLOSE', 'SELL', .4), ('CLOSE', 'BUY', .5)])
        self.assertEqual(len(rows), 4)

    def test_orders_modifications_rejections_and_stream_updates_are_not_fills(self):
        records = [activity(action=a) for a in ('WORKING_ORDER_DELETED', 'STOP_LIMIT_AMENDED', 'POSITION_DELETED', 'STOP_ORDER_OPENED', 'LIMIT_ORDER_FILLED')]
        records += [activity(status='REJECTED'), {'kind':'stream','raw':{'status':'UPDATED','level':100,'timestamp':DATE+'Z'}}]
        self.assertEqual(self.get(events=records)['markers'], [])

    def test_naive_generic_activity_date_not_assumed_utc_or_from_event_at(self):
        row = activity(date=DATE)
        row['at'] = DATE+'Z'
        out = self.get(events=[row])
        self.assertEqual(out['markers'], [])
        self.assertEqual(out['omitted'][0]['reason'], 'missing_utc')

    def test_explicit_offset_is_converted_and_bad_utc_rejected(self):
        self.assertEqual(utc_time('2026-10-16T20:00:00+08:00'), TIME)
        for value in (DATE, '2026-10-16', 'bad', None, True):
            self.assertIsNone(utc_time(value))
        self.assertIsNone(utc_time('2026-10-16', True))

    def test_unique_closed_activity_joins_transaction_utc_not_display_name(self):
        closed = activity(action='POSITION_CLOSED', side='SELL', level=110, size=.4, date='2026-10-16T21:00:00', deal='close-1', ref='close-ref')
        out = self.get(events=[activity(), closed, transaction(ref='close-ref')])
        row = next(r for r in out['markers'] if r['action']=='CLOSE')
        self.assertEqual((row['time'], row['side'], row['action']), (TIME+3600, 'SELL', 'CLOSE'))
        self.assertIn('transactions UTC', row['source'])
        self.assertEqual(out['omitted'], [])

    def test_close_direction_requires_unique_original_and_opposite_reported_side(self):
        closed = activity(action='POSITION_CLOSED', side='SELL', level=110, size=.4, deal='close-1')
        out = self.get(events=[closed])
        self.assertEqual(out['markers'], [])
        self.assertEqual(out['omitted'][0]['reason'], 'unverified_close_side')
        same_side = activity(action='POSITION_CLOSED', side='BUY', level=110, size=.4, deal='close-1')
        out = self.get(events=[activity(), same_side])
        self.assertEqual([r for r in out['markers'] if r['action']=='CLOSE'], [])
        self.assertIn('unverified_close_side', [r['reason'] for r in out['omitted']])
        # A conflicting original-side history is not resolved by guessing.
        conflict = activity(side='SELL')
        out = self.get([position(side='BUY')], [conflict, closed])
        self.assertEqual([r for r in out['markers'] if r['action']=='CLOSE'], [])
        self.assertIn('unverified_close_side', [r['reason'] for r in out['omitted']])

    def test_current_position_can_validate_short_buy_close_without_open_activity(self):
        closed = activity(action='POSITION_PARTIALLY_CLOSED', side='BUY', level=90, size=.4, deal='close-1')
        rows = self.get([position(side='SELL')], [closed])['markers']
        self.assertEqual([(r['side'],r['size']) for r in rows if r['action']=='CLOSE'], [('BUY',.4)])

    def test_historical_open_joins_explicit_transaction_open_date(self):
        opened = activity(date=DATE)
        out = self.get(events=[opened, transaction(ref='open-1')])
        self.assertEqual(len(out['markers']), 1)
        self.assertEqual(out['markers'][0]['time'], TIME)
        self.assertEqual(out['markers'][0]['action'], 'OPEN')

    def test_transaction_name_alone_and_wrong_quantity_or_period_cannot_map(self):
        self.assertEqual(self.get(events=[transaction()])['markers'], [])
        closed = activity(action='POSITION_CLOSED', side='SELL', level=110, size=.6, date=DATE, deal='close-1')
        self.assertEqual([r for r in self.get(events=[activity(), closed, transaction()])['markers'] if r['action']=='CLOSE'], [])
        closed['raw']['details']['size'] = .4
        tx = transaction();tx['raw']['period'] = 'OTHER-EXPIRY'
        self.assertEqual([r for r in self.get(events=[activity(), closed, tx])['markers'] if r['action']=='CLOSE'], [])

    def test_cash_transaction_cannot_supply_trade_time(self):
        opened = activity(date=DATE)
        self.assertEqual(self.get(events=[opened, transaction(ref='open-1',cash=True)])['markers'], [])

    def test_reference_reused_across_products_does_not_map(self):
        opened = activity(date=DATE, ref='shared-ref')
        other = activity(epic=OTHER, ref='shared-ref', deal='other-deal', affected='other-deal')
        out = self.get(events=[opened, other, transaction(ref='shared-ref')])
        self.assertEqual(out['markers'], [])

    def test_bulk_netting_action_is_not_replicated_as_individual_fills(self):
        row = activity();row['raw']['details']['actions'].append({'actionType':'POSITION_CLOSED','affectedDealId':'another'})
        out = self.get(events=[row])
        self.assertEqual(out['markers'], [])
        self.assertEqual(out['omitted'][0]['reason'], 'ambiguous_actions')

    def test_same_reference_same_second_price_distinct_real_partial_closes_remain(self):
        first = activity(action='POSITION_PARTIALLY_CLOSED', side='SELL', level=110, size=.1, deal='close-1', ref='shared-close')
        second = activity(action='POSITION_PARTIALLY_CLOSED', side='SELL', level=110, size=.2, deal='close-2', ref='shared-close')
        rows = [r for r in self.get(events=[activity(), first, second, copy.deepcopy(first)])['markers'] if r['action']=='CLOSE']
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['size'] for r in rows}, {.1,.2})
        self.assertEqual(len({r['id'] for r in rows}), 2)

    def test_millisecond_distinct_executions_keep_distinct_ids_at_same_second(self):
        first = activity(action='POSITION_CLOSED', side='SELL', level=110, date=DATE+'.100Z', deal='close-1')
        second = activity(action='POSITION_CLOSED', side='SELL', level=110, date=DATE+'.900Z', deal='close-2')
        rows = [r for r in self.get(events=[activity(), first, second])['markers'] if r['action']=='CLOSE']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['time'], rows[1]['time'])
        self.assertNotEqual(rows[0]['id'], rows[1]['id'])

    def test_nan_inf_boolean_rejected_but_nonpositive_oil_prices_are_valid(self):
        for level in (float('nan'), float('inf'), True):
            self.assertEqual(self.get(events=[activity(level=level)])['markers'], [])
        for level in (0,-37.63):
            self.assertEqual(self.get(events=[activity(level=level)])['markers'][0]['price'], level)

    def test_ambiguous_naive_close_times_not_guessed(self):
        a = activity(action='POSITION_CLOSED', side='SELL', level=110, size=.4, date=DATE, deal='close-1')
        out = self.get(events=[activity(), a, transaction(), transaction(closed='2026-10-16T14:00:00')])
        self.assertEqual([r for r in out['markers'] if r['action']=='CLOSE'], [])
        self.assertIn('ambiguous_transaction', [r['reason'] for r in out['omitted']])


class TradeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        db = sqlite3.connect(':memory:',check_same_thread=False)
        db.execute('CREATE TABLE events(scope,kind,at,payload)')
        other = activity(epic=EPIC, deal='other-account-secret')
        db.execute('INSERT INTO events VALUES (?,?,?,?)',('other-account','activity',DATE,json.dumps(other['raw'])))
        row = activity(date=DATE)
        db.execute('INSERT INTO events VALUES (?,?,?,?)',('selected-account','activity',DATE,json.dumps(row['raw'])))
        cls.monitor = SimpleNamespace(lock=threading.RLock(),scope='selected-account',market_info={EPIC:{}},watch=[],state={'positions':[position()]},
            store=SimpleNamespace(lock=threading.RLock(),db=db))
        cls.api = TradeMarkers(cls.monitor)
        cls.patches = [patch.object(server,'ACCESS_FILE',cls.root/'access.json'), patch.object(server,'TRADES',cls.api)]
        for p in cls.patches:p.start()
        server.set_viewer_password('synthetic-trades-password')
        cls.cookie = server.COOKIE+'='+server.session_token(server.access_config())
        cls.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.httpd.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown();cls.httpd.server_close();cls.thread.join()
        for p in reversed(cls.patches):p.stop()
        cls.monitor.store.db.close();cls.temp.cleanup()

    def request(self,path,auth=True,method='GET'):
        headers={'Cookie':self.cookie} if auth else {}
        c=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=3)
        c.request(method,path,headers=headers);r=c.getresponse();result=r.status,dict(r.getheaders()),r.read();c.close();return result

    def test_http_auth_query_validation_readonly_and_safe_projection(self):
        self.assertEqual(self.request('/api/chart/trades?epic='+EPIC,False)[0],401)
        status,headers,body=self.request('/api/chart/trades?epic='+EPIC)
        self.assertEqual(status,200)
        payload=json.loads(body)
        self.assertEqual(len(payload['markers']),1)
        self.assertNotIn('other-account-secret',body.decode())
        self.assertEqual(set(payload['markers'][0]),{'id','time','price','side','action','size','reference','source'})
        self.assertFalse(any(k.lower().startswith('access-control-') for k in headers))
        for suffix in ('','?epic='+EPIC+'&epic='+EPIC,'?epic='+EPIC+'&token=bad','?epic=../secret'):
            self.assertEqual(self.request('/api/chart/trades'+suffix)[0],400)
        self.assertEqual(self.request('/api/chart/trades?epic=NOT.MONITORED')[0],403)
        self.assertEqual(self.request('/api/chart/trades?epic='+EPIC,method='POST')[0],405)

    def test_no_account_is_explicit_empty_and_scope_never_crossed(self):
        with patch.object(self.monitor,'scope',None):
            out=self.api.get(EPIC)
            self.assertEqual(out['status'],'account_not_connected')
            self.assertEqual(out['markers'],[])
        self.assertEqual(self.api.get(EPIC)['markers'][0]['reference'],'ref-1')


if __name__=='__main__':unittest.main()
