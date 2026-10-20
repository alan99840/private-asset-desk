import unittest
from datetime import datetime, timedelta, timezone
from market_sessions import describe, gap_details, open_seconds
from quant import indicators

OIL = 'CC.D.CL.BMU.IP'
NIKKEI = 'IX.D.NIKKEI.BMU.IP'


def ts(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def history(end, epic=OIL, count=100, step=3600):
    now = ts(end)
    result = []
    for i in range(count * 4):
        at = now - i * step
        if open_seconds(epic, at, at + step):
            result.append({'time': at, 'open': 100, 'high': 102, 'low': 98, 'close': 101})
        if len(result) == count:
            return list(reversed(result))
    raise AssertionError('fixture history too short')


class SessionTests(unittest.TestCase):
    def test_oil_pause_each_intraday_resolution(self):
        for step, resolution in [(60, 'MINUTE'), (300, 'MINUTE_5'),
                                 (900, 'MINUTE_15'), (1800, 'MINUTE_30'), (3600, 'HOUR')]:
            a, b = ts('2026-10-19T21:00Z') - step, ts('2026-10-19T22:00Z')
            gap = gap_details(OIL, [{'time': a}, {'time': b}], resolution, step)
            self.assertEqual(gap[0]['kind'], 'session')
            # An additional missing open bar must not disappear into the pause.
            gap = gap_details(OIL, [{'time': a - step}, {'time': b}], resolution, step)
            self.assertEqual(gap[0]['kind'], 'unexplained')

    def test_daily_pause_summer_winter_and_us_uk_dst_mismatch(self):
        cases = [('2026-10-20T21:00Z', True), ('2026-10-27T21:00Z', True),
                 ('2026-11-03T22:00Z', False), ('2026-03-10T21:00Z', True),
                 ('2026-03-03T22:00Z', False)]
        for stamp, dst in cases:
            start = ts(stamp)
            self.assertEqual(open_seconds(OIL, start, start + 3600), 0)
            self.assertEqual(open_seconds(OIL, start - 3600, start), 3600)
            self.assertEqual(open_seconds(OIL, start + 3600, start + 7200), 3600)
            s = describe(OIL, start)
            self.assertEqual(s['dst_active'], dst)
            self.assertFalse(s['reference_open'])
            self.assertEqual(s['next_transition'], start + 3600)

    def test_weekend_crosses_actual_dst_transition(self):
        for friday, sunday in [('2026-03-06T22:00Z', '2026-03-08T22:00Z'),
                               ('2026-10-30T21:00Z', '2026-11-01T23:00Z')]:
            for epic in (OIL, NIKKEI):
                self.assertEqual(open_seconds(epic, ts(friday), ts(sunday)), 0)
                self.assertEqual(open_seconds(epic, ts(sunday), ts(sunday) + 3600), 3600)
                pair = [{'time': ts(friday) - 3600}, {'time': ts(sunday)}]
                self.assertEqual(gap_details(epic, pair, 'HOUR', 3600)[0]['kind'], 'session')
                pair[1]['time'] += 3600
                self.assertEqual(gap_details(epic, pair, 'HOUR', 3600)[0]['kind'], 'unexplained')

    def test_nikkei_has_no_oil_or_tokyo_lunch_pause(self):
        self.assertEqual(open_seconds(NIKKEI, ts('2026-10-20T21:00Z'), ts('2026-10-20T22:00Z')), 3600)
        self.assertEqual(open_seconds(NIKKEI, ts('2026-10-20T02:30Z'), ts('2026-10-20T03:30Z')), 3600)
        s = describe(NIKKEI, ts('2026-10-20T12:00Z'))
        self.assertEqual(s['next_transition'], ts('2026-10-23T21:00Z'))

    def test_unknown_or_similar_epic_not_whitelisted(self):
        for epic in (None, 'CC.D.CL.UNKNOWN.IP', 'CC.D.CL.WEEKEND.IP'):
            self.assertIsNone(open_seconds(epic, ts('2026-10-20T21:00Z'), ts('2026-10-20T22:00Z')))
            self.assertEqual(describe(epic, ts('2026-10-20T21:00Z'))['status'], 'UNVERIFIED')
            self.assertEqual(gap_details(epic, [{'time': ts('2026-10-16T20:00Z')},
                                              {'time': ts('2026-10-18T22:00Z')}], 'HOUR', 3600)[0]['kind'], 'unexplained')

    def test_live_incident_regression_no_fabrication_and_true_gap(self):
        bars = history('2026-10-20T15:00Z')
        original = [dict(b) for b in bars]
        out = indicators(bars, 'HOUR', ts('2026-10-20T15:30Z'), epic=OIL)
        self.assertEqual(out['status'], 'READY')
        self.assertGreater(out['session_gaps'], 0)
        self.assertEqual(out['unexplained_gaps'], 0)
        self.assertEqual(bars, original)
        bars = [b for b in bars if b['time'] != ts('2026-10-20T08:00Z')]
        out = indicators(bars, 'HOUR', ts('2026-10-20T15:30Z'), epic=OIL)
        self.assertEqual(out['status'], 'LIMITED')
        self.assertEqual(out['conditions'], [])
        self.assertIn('unexplained_gaps', [r['code'] for r in out['reasons']])

    def test_closed_weekend_does_not_age_but_reopened_missing_data_does(self):
        bars = history('2026-10-16T20:00Z')
        out = indicators(bars, 'HOUR', ts('2026-10-18T20:00Z'), epic=OIL)
        self.assertEqual(out['status'], 'READY')
        self.assertFalse(out['stale_bars'])
        out = indicators(bars, 'HOUR', ts('2026-10-19T02:00Z'), epic=OIL)
        self.assertTrue(out['stale_bars'])
        self.assertEqual(out['status'], 'LIMITED')
        # A failure already present before closing cannot be healed by a weekend.
        bars = history('2026-10-16T16:00Z')
        self.assertTrue(indicators(bars, 'HOUR', ts('2026-10-18T20:00Z'), epic=OIL)['stale_bars'])

    def test_unverified_holiday_is_not_exempt(self):
        bars = history('2026-12-28T15:00Z')
        bars = [b for b in bars if not ts('2026-12-25T00:00Z') <= b['time'] < ts('2026-12-25T21:00Z')]
        out = indicators(bars, 'HOUR', ts('2026-12-28T15:30Z'), epic=OIL)
        self.assertEqual(out['status'], 'LIMITED')

    def test_calendar_contradiction_blocks_signals(self):
        bars = history('2026-10-20T15:00Z')
        bars.append({**bars[-1], 'time': ts('2026-10-19T21:00Z')})
        bars.sort(key=lambda b: b['time'])
        out = indicators(bars, 'HOUR', ts('2026-10-20T15:30Z'), epic=OIL)
        self.assertEqual(out['status'], 'LIMITED')
        self.assertIn('session_conflict', [r['code'] for r in out['reasons']])

    def test_daily_weekly_monthly_missing_buckets(self):
        pair = [{'time': ts('2026-10-16T00:00Z')}, {'time': ts('2026-10-19T00:00Z')}]
        self.assertEqual(gap_details(OIL, pair, 'DAY', 86400)[0]['kind'], 'session')
        pair[1]['time'] = ts('2026-10-20T00:00Z')
        self.assertEqual(gap_details(OIL, pair, 'DAY', 86400)[0]['kind'], 'unexplained')
        pair = [{'time': ts('2026-09-01T00:00Z')}, {'time': ts('2026-11-01T00:00Z')}]
        self.assertEqual(gap_details(OIL, pair, 'MONTH', 2678400)[0]['missing_bars'], 1)
        pair[1]['time'] = ts('2026-10-01T00:00Z')
        self.assertEqual(gap_details(OIL, pair, 'MONTH', 2678400), [])


if __name__ == '__main__':
    unittest.main()
