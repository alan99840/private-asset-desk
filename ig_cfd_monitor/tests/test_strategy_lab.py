import copy
import json
import math
import unittest
from unittest.mock import patch

from chart_history import ChartError
from market_sessions import open_seconds
from quant import ema, wilder
from strategy_lab import (DEFAULTS, _backtest, _bootstrap, _cost_stress, _direction,
                          _series, run_study, validate_options)


def bar(i, op=100, high=102, low=98, close=100, spread=1, start=1700000000):
    return {'time': start+i*3600, 'open': op, 'high': high, 'low': low, 'close': close,
            'ask': {'open': op+spread, 'high': high+spread, 'low': low+spread, 'close': close+spread}}


def history(n=401):
    rows = []
    for i in range(n):
        close = 100 + 12*math.sin(i/18) + .03*i
        op = close-.3
        rows.append(bar(i, op, close+.5, op-.5, close, spread=.1))
    return {'status': 'cached', 'candles': rows, 'source': 'Synthetic tests'}


def signal(direction=1):
    return {'close': 110 if direction == 1 else 90 if direction == -1 else 100,
            'ema20': 105 if direction == 1 else 95 if direction == -1 else 100,
            'ema50': 100, 'rsi14': 60 if direction == 1 else 40 if direction == -1 else 50,
            'atr14': 2, 'high20': 108, 'low20': 92}


def execute(rows, signals, **options):
    return _backtest(rows, signals, 1, len(rows), {**DEFAULTS, **options}, 'HOUR', rows[-1]['time']+3600)


class IndicatorTests(unittest.TestCase):
    def test_matches_existing_sma_seeded_ema_wilder_atr_rsi(self):
        bars = history(201)['candles']
        result = _series(bars)
        for length in (61, 83, 130, 200):
            sample = bars[:length]
            closes = [b['close'] for b in sample]
            changes = [b-a for a, b in zip(closes, closes[1:])]
            trs = [max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close']))
                   for a, b in zip(sample, sample[1:])]
            gain, loss = wilder([max(0, x) for x in changes]), wilder([max(0, -x) for x in changes])
            row = result[length-1]
            self.assertAlmostEqual(row['ema20'], ema(closes, 20), places=11)
            self.assertAlmostEqual(row['ema50'], ema(closes, 50), places=11)
            self.assertAlmostEqual(row['atr14'], wilder(trs), places=11)
            self.assertAlmostEqual(row['rsi14'], 100-100/(1+gain/loss), places=11)
            self.assertEqual(row['high20'], max(b['high'] for b in sample[-21:-1]))

    def test_future_prices_do_not_change_prefix_indicators(self):
        rows = history(201)['candles']
        self.assertEqual(_series(rows)[:100], _series(rows[:100]))
        modified = copy.deepcopy(rows)
        modified[100]['high'] = 10000
        modified[100]['close'] = 9999
        self.assertEqual(_series(rows)[:100], _series(modified)[:100])

    def test_rsi50_neutral_and_extremes_not_contrarian(self):
        up = signal(1)
        up['rsi14'] = 50
        self.assertEqual(_direction(up, 'alignment'), 0)
        up['rsi14'] = 90
        self.assertEqual(_direction(up, 'alignment'), 1)
        down = signal(-1)
        down['rsi14'] = 10
        self.assertEqual(_direction(down, 'alignment'), -1)
        self.assertEqual(_direction({**up, 'close': 107}, 'breakout20'), 0)
        self.assertEqual(_direction({**up, 'close': 107}, 'alignment'), 1)


class ExecutionTests(unittest.TestCase):
    def test_next_open_entry_not_signal_close(self):
        rows = [bar(0), bar(1, 120, 123, 119, 122), bar(2, 123, 125, 122, 124)]
        result = execute(rows, [signal(1), signal(1), signal(1)])
        trade = result['trades'][0]
        self.assertEqual(trade['signal_time'], rows[0]['time'])
        self.assertEqual(trade['entry_time'], rows[1]['time'])
        self.assertEqual(trade['entry_price'], 121)
        self.assertEqual(trade['exit_reason'], 'sample_end')
        self.assertEqual(trade['exit_price'], 124)
        self.assertEqual(result['metrics']['terminal_exits'], 1)

    def test_long_gap_stop_uses_worse_bid_open_and_no_same_bar_reentry(self):
        rows = [bar(0), bar(1, 100, 102, 99, 101), bar(2, 90, 93, 89, 92)]
        result = execute(rows, [signal(1)]*3)
        self.assertEqual(len(result['trades']), 1)
        trade = result['trades'][0]
        self.assertEqual(trade['entry_price'], 101)
        self.assertEqual(trade['stop_price'], 97)
        self.assertEqual(trade['exit_price'], 90)
        self.assertEqual(trade['exit_reason'], 'gap_stop')
        self.assertEqual(trade['net_r'], -2.75)
        self.assertLess(result['metrics']['return_pct'], -.5)

    def test_short_stop_uses_ask_high_even_when_bid_never_reaches_stop(self):
        rows = [bar(0), bar(1, 100, 103.5, 99, 101, spread=1)]
        trade = execute(rows, [signal(-1)]*2)['trades'][0]
        self.assertEqual(trade['entry_price'], 100)
        self.assertEqual(trade['stop_price'], 104)
        self.assertLess(rows[1]['high'], trade['stop_price'])
        self.assertGreater(rows[1]['ask']['high'], trade['stop_price'])
        self.assertEqual(trade['exit_reason'], 'stop')
        self.assertEqual(trade['exit_price'], 104)
        self.assertEqual(trade['net_r'], -1)

    def test_short_gap_and_each_side_slippage_are_adverse(self):
        rows = [bar(0), bar(1, 100, 101, 98, 99), bar(2, 110, 112, 108, 109)]
        trade = execute(rows, [signal(-1)]*3, slippage=.25)['trades'][0]
        self.assertEqual(trade['entry_price'], 99.75)
        self.assertEqual(trade['exit_price'], 111.25)
        self.assertEqual(trade['exit_reason'], 'gap_stop')
        self.assertEqual(trade['slippage_points'], .5)

    def test_signal_exit_at_next_open_and_no_same_bar_reversal(self):
        rows = [bar(0), bar(1, 100, 102, 99, 101), bar(2, 102, 103, 100, 101)]
        result = execute(rows, [signal(1), signal(-1), signal(-1)])
        self.assertEqual(len(result['trades']), 1)
        trade = result['trades'][0]
        self.assertEqual(trade['exit_reason'], 'signal')
        self.assertEqual(trade['exit_price'], rows[2]['open'])
        self.assertEqual(trade['exit_time'], rows[2]['time'])

    def test_financing_accrues_over_calendar_gap_and_fees_two_sides(self):
        rows = [bar(0), bar(1, 100, 102, 99, 101), bar(2, 102, 103, 100, 101)]
        rows[2]['time'] = rows[1]['time']+3*86400
        trade = execute(rows, [signal(1), signal(-1), signal(-1)], financing_bps_daily=10, fee_points=.2)['trades'][0]
        self.assertAlmostEqual(trade['financing_points'], 101*.001*3)
        self.assertAlmostEqual(trade['fee_points'], .4)
        self.assertAlmostEqual(trade['net_points'], trade['gross_points']-.303-.4)

    def test_cost_stress_reprices_same_sequence_and_never_improves_it(self):
        rows = [bar(0), bar(1, 100, 102, 99, 101), bar(2, 102, 104, 101, 103)]
        segment = execute(rows, [signal(1)]*3, slippage=.1, financing_bps_daily=2, fee_points=.2)
        stressed = _cost_stress(segment, .5)
        self.assertLess(stressed['metrics']['return_pct'], segment['metrics']['return_pct'])
        self.assertEqual(stressed['metrics']['trades'], segment['metrics']['trades'])
        self.assertEqual(stressed['drawdown_basis'], 'trade_close_only')

    def test_intrabar_drawdown_and_spread_are_not_hidden_by_close_only_curve(self):
        rows = [bar(0), bar(1, 100, 110, 98, 105), bar(2, 105, 107, 103, 106)]
        result = execute(rows, [signal(1)]*3)
        self.assertGreater(result['metrics']['max_drawdown_pct'], result['metrics']['close_drawdown_pct'])
        self.assertGreater(result['metrics']['max_drawdown_pct'], 0)


class StudyValidationTests(unittest.TestCase):
    def study(self, h=None, **kwargs):
        h = history() if h is None else h
        kwargs['options'] = {'max_bars': 20000, **kwargs.get('options', {})}
        return run_study(h, 'SYNTHETIC', 'HOUR', epoch=h['candles'][-1]['time']+10, **kwargs)

    def test_chronological_flat_split_and_json_serializable(self):
        result = self.study()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['coverage']['bars'], 400)
        split = result['coverage']['split_at']
        self.assertEqual(result['in_sample']['end'], split)
        self.assertEqual(result['out_of_sample']['start'], split)
        for t in result['in_sample']['trades']:
            self.assertLess(t['entry_time'], split)
            self.assertLessEqual(t['exit_time'], split)
        for t in result['out_of_sample']['trades']:
            self.assertGreaterEqual(t['entry_time'], split)
        self.assertEqual(result['out_of_sample']['curve'][0]['equity'], 100)
        self.assertEqual(result['ablation']['out_of_sample']['curve'][0]['equity'], 100)
        self.assertEqual(result['monte_carlo']['status'], 'INSUFFICIENT')
        json.dumps(result, allow_nan=False)

    def test_oos_prices_do_not_change_training_result(self):
        h = history()
        original = self.study(h)
        for b in h['candles'][280:]:
            for name in ('open', 'high', 'low', 'close'):
                b[name] += 20
                b['ask'][name] += 20
        changed = self.study(h)
        self.assertEqual(changed['status'], 'READY')
        self.assertEqual(original['in_sample'], changed['in_sample'])

    def test_unconfirmed_tail_and_future_bars_never_enter_results(self):
        h = history()
        original = self.study(h)
        changed = copy.deepcopy(h)
        changed['candles'][-1] = bar(400, 500, 550, 450, 525)
        actual = self.study(changed)
        self.assertEqual(original['in_sample'], actual['in_sample'])
        self.assertEqual(original['out_of_sample'], actual['out_of_sample'])
        future = copy.deepcopy(changed)
        future['candles'].append(bar(500, 999, 1000, 998, 999))
        actual = run_study(future, 'SYNTHETIC', 'HOUR', {'max_bars': 20000}, epoch=h['candles'][-1]['time']+10)
        self.assertEqual(actual['coverage']['excluded_future'], 1)
        self.assertEqual(original['out_of_sample'], actual['out_of_sample'])

    def test_invalid_ohlc_missing_ask_order_and_spread_block(self):
        mutations = [lambda rows: rows[10].pop('ask'),
                     lambda rows: rows[10].update(high=0),
                     lambda rows: rows[10].update(time=rows[9]['time']),
                     lambda rows: rows[10]['ask'].update(open=rows[10]['open']-1),
                     lambda rows: rows[10].update(close=float('nan'))]
        for change in mutations:
            h = history()
            change(h['candles'])
            result = self.study(h)
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertIsNone(result['out_of_sample'])
            self.assertTrue(result['reasons'])
            json.dumps(result, allow_nan=False)

    def test_whole_history_gaps_are_checked_not_only_recent61(self):
        h = history()
        for b in h['candles'][10:]:
            b['time'] += 3600
        result = self.study(h)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('unexplained_gaps', [r['code'] for r in result['reasons']])
        self.assertEqual(result['coverage']['unexplained_gaps'], 1)

    def test_known_regular_market_closures_are_allowed(self):
        epic = 'CC.D.CL.BMU.IP'
        raw = history(201)['candles']
        stamp = 1792411200  # A UTC timestamp; choose only reference-open starts.
        for b in raw:
            while open_seconds(epic, stamp, stamp+3600) == 0:
                stamp += 3600
            b['time'] = stamp
            stamp += 3600
        result = run_study({'candles': raw}, epic, 'HOUR', epoch=stamp)
        self.assertEqual(result['status'], 'READY', result['reasons'])
        self.assertGreater(result['coverage']['session_gaps'], 0)
        self.assertEqual(result['coverage']['unexplained_gaps'], 0)

    def test_historical_stale_cache_is_labeled_and_never_claimed_live(self):
        h = history()
        result = run_study(h, 'SYNTHETIC', 'HOUR', epoch=h['candles'][-1]['time']+365*86400)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['coverage']['basis'], 'historical_only')
        self.assertTrue(any('不是实时预测' in x for x in result['warnings']))
        self.assertTrue(any('成本' in x and '0' in x for x in result['warnings']))

    def test_small_sample_blocked_without_fake_metrics(self):
        result = self.study(history(120))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['coverage']['bars'], 119)
        self.assertIsNone(result['in_sample'])

    def test_selected_window_excludes_old_missing_ask_without_hiding_selected_gaps(self):
        h = history(601)
        for b in h['candles'][:401]:
            b.pop('ask')
        result = self.study(h, options={'max_bars': 200})
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['coverage']['cached_bars'], 601)
        self.assertEqual(result['coverage']['selected_bars'], 200)
        self.assertEqual(result['coverage']['bars'], 199)
        self.assertEqual(result['coverage']['start'], h['candles'][-200]['time'])
        self.assertEqual(result['coverage']['source_window'], 'last_n_bars')
        full = self.study(h, options={'max_bars': 20000})
        self.assertEqual(full['status'], 'BLOCKED')
        self.assertIn('missing_ask', [x['code'] for x in full['reasons']])

    def test_future_rows_do_not_shift_selected_window(self):
        h = history(401)
        epoch = h['candles'][-1]['time']+10
        first = run_study(h, 'SYNTHETIC', 'HOUR', epoch=epoch)
        h['candles'].append(bar(500))
        last = run_study(h, 'SYNTHETIC', 'HOUR', epoch=epoch)
        self.assertEqual(first['coverage']['start'], last['coverage']['start'])
        self.assertEqual(first['in_sample'], last['in_sample'])
        self.assertEqual(first['out_of_sample'], last['out_of_sample'])

    def test_options_reject_unbounded_and_nonfinite_values(self):
        for options in ({'risk_pct': 10}, {'risk_pct': True}, {'slippage': 'nan'},
                        {'fee_points': -1}, {'stop_atr': 3}, {'paths': 1e9}, {'mode': 'fit_best'},
                        {'max_bars': 201}, {'max_bars': True}):
            with self.assertRaises(ChartError):
                validate_options(options)
        self.assertEqual(validate_options({'risk_pct': '1'})['risk_pct'], 1.)

    def test_depleted_history_blocks_mc_not_just_its_catastrophic_trade(self):
        segment = {'metrics': {'depleted': True, 'completed_trades': 35, 'mean_r': -1, 'return_pct': -120},
                   'curve': [{'time': 1, 'equity': 100}, {'time': 2, 'equity': -20}],
                   'start': 1, 'end': 2,
                   'trades': [{'exit_reason': 'signal', 'net_r': 1} for _ in range(34)] +
                             [{'exit_reason': 'model_depletion', 'net_r': -250}]}
        stress = {'metrics': {'return_pct': -120}}
        with patch('strategy_lab._backtest', return_value=segment), patch('strategy_lab._cost_stress', return_value=stress):
            result = self.study()
        self.assertEqual(result['monte_carlo']['status'], 'BLOCKED')
        self.assertEqual(result['monte_carlo']['samples'], 35)
        self.assertIn('耗尽', result['monte_carlo']['reason'])


class MonteCarloTests(unittest.TestCase):
    def test_study_gate_and_all_three_block_sensitivities(self):
        h = history(8001)
        result = run_study(h, 'SYNTHETIC', 'HOUR', {'max_bars': 20000, 'mode': 'alignment'},
                           epoch=h['candles'][-1]['time']+10)
        mc = result['monte_carlo']
        self.assertEqual(mc['status'], 'READY')
        self.assertGreaterEqual(mc['samples'], 30)
        self.assertEqual(mc['samples'], result['out_of_sample']['metrics']['completed_trades'])
        self.assertEqual([v['block_length'] for v in mc['block_sensitivity']], [1, 3, 5])
        self.assertEqual(len(result['out_of_sample']['trades']), result['out_of_sample']['metrics']['trades'])
        json.dumps(result, allow_nan=False)

    def test_seeded_bootstrap_repeatability_and_percentile_order(self):
        rs = [-1, -1, 2, .5, -2, 3]*5
        a = _bootstrap(rs, .5)
        b = _bootstrap(rs, .5)
        self.assertEqual(a, b)
        self.assertEqual(len(a['fan']), 51)
        self.assertEqual(a['fan'][0], {'trade': 0, 'p05': 100, 'p50': 100, 'p95': 100})
        self.assertEqual(a['paths'], 500)
        for point in a['fan']:
            self.assertLessEqual(point['p05'], point['p50'])
            self.assertLessEqual(point['p50'], point['p95'])
        json.dumps(a, allow_nan=False)

    def test_losses_and_depletion_not_clipped_to_positive_equity(self):
        result = _bootstrap([-250]*30, .5)
        self.assertEqual(result['terminal']['p50'], -25)
        self.assertEqual(result['drawdown']['p50'], 125)
        self.assertEqual(result['drawdown']['probability_ge_60_pct'], 100)
        self.assertEqual(result['depletion_probability_pct'], 100)

    def test_block_length_changes_sequencing_not_underlying_samples(self):
        rs = [-1]*15+[1]*15
        independent = _bootstrap(rs, .5, block_length=1)
        grouped = _bootstrap(rs, .5, block_length=5)
        self.assertNotEqual(independent['terminal'], grouped['terminal'])
        self.assertGreater(grouped['drawdown']['p95'], independent['drawdown']['p95'])


if __name__ == '__main__':
    unittest.main()
