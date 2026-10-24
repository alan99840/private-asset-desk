"""Cache-only historical research. No IG calls, order paths or fitted parameters.

The dashboard describes conditions, not a complete trading strategy. This module
makes the additional execution/exit/sizing assumptions explicit. All amounts are
dimensionless equity-index units or instrument price points, never account HKD.
"""
import math
import random
import time
from datetime import datetime, timezone

from chart_history import RESOLUTIONS, ChartError
from market_sessions import gap_details, open_seconds

DEFAULTS = {'mode': 'breakout20', 'risk_pct': .5, 'stop_atr': 2.,
            'slippage': 0., 'financing_bps_daily': 0., 'fee_points': 0., 'max_bars': 200}
WARMUP = 60
MIN_BARS = 120
SEED = 20261024
FIELDS = ('open', 'high', 'low', 'close')


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def validate_options(options=None):
    if options is not None and not isinstance(options, dict):
        raise ChartError('invalid_strategy_options')
    options = options or {}
    if set(options) - set(DEFAULTS):
        raise ChartError('invalid_strategy_options')
    out = {**DEFAULTS, **options}
    if out['mode'] not in ('breakout20', 'alignment'):
        raise ChartError('invalid_strategy_mode')
    window = _number(out['max_bars'])
    if window not in (200, 500, 2000, 20000):
        raise ChartError('invalid_strategy_max_bars')
    out['max_bars'] = int(window)
    limits = {'risk_pct': (.1, 2), 'stop_atr': (2, 2),
              'slippage': (0, 1000000), 'financing_bps_daily': (0, 1000),
              'fee_points': (0, 1000000)}
    for key, (low, high) in limits.items():
        value = _number(out[key])
        if value is None or not low <= value <= high:
            raise ChartError('invalid_strategy_' + key)
        out[key] = value
    return out


def _series(bars):
    """Causal SMA-seeded EMA and Wilder series, identical to quant.py."""
    rows, closes, changes, trs = [], [], [], []
    fast = slow = atr = gain = loss = None
    for i, bar in enumerate(bars):
        c = bar['close']
        closes.append(c)
        if len(closes) == 20:
            fast = sum(closes) / 20
        elif len(closes) > 20:
            fast += 2 / 21 * (c - fast)
        if len(closes) == 50:
            slow = sum(closes) / 50
        elif len(closes) > 50:
            slow += 2 / 51 * (c - slow)
        if i:
            change = c - closes[-2]
            changes.append(change)
            tr = max(bar['high'] - bar['low'], abs(bar['high'] - closes[-2]),
                     abs(bar['low'] - closes[-2]))
            trs.append(tr)
            if len(changes) == 14:
                atr = sum(trs) / 14
                gain = sum(max(0, x) for x in changes) / 14
                loss = sum(max(0, -x) for x in changes) / 14
            elif len(changes) > 14:
                atr = (atr * 13 + tr) / 14
                gain = (gain * 13 + max(0, change)) / 14
                loss = (loss * 13 + max(0, -change)) / 14
        rsi = None if gain is None else 50. if gain == loss == 0 else 100. if loss == 0 else 100 - 100 / (1 + gain / loss)
        rows.append({'close': c, 'ema20': fast, 'ema50': slow, 'atr14': atr,
                     'rsi14': rsi,
                     'high20': max(b['high'] for b in bars[i-20:i]) if i >= 20 else None,
                     'low20': min(b['low'] for b in bars[i-20:i]) if i >= 20 else None})
    return rows


def _direction(signal, mode, use_rsi=True):
    c, f, s, r = (signal[k] for k in ('close', 'ema20', 'ema50', 'rsi14'))
    if f is None or s is None or r is None or signal['atr14'] is None or signal['atr14'] <= 0:
        return 0
    long = c > f > s and (not use_rsi or r > 50)
    short = c < f < s and (not use_rsi or r < 50)
    if mode == 'breakout20':
        long = long and signal['high20'] is not None and c > signal['high20']
        short = short and signal['low20'] is not None and c < signal['low20']
    return 1 if long else -1 if short else 0


def _invalid(signal, direction, use_rsi):
    return (signal['close'] <= signal['ema20'] or (use_rsi and signal['rsi14'] <= 50)) if direction == 1 else \
           (signal['close'] >= signal['ema20'] or (use_rsi and signal['rsi14'] >= 50))


def _end_time(bars, i, resolution, final_successor):
    start = bars[i]['time']
    next_at = bars[i+1]['time'] if i+1 < len(bars) else final_successor
    if resolution == 'MONTH':
        date = datetime.fromtimestamp(start, timezone.utc)
        boundary = date.replace(year=date.year + (date.month == 12), month=date.month % 12 + 1,
                                day=1).timestamp()
    else:
        boundary = start + RESOLUTIONS[resolution]
    return min(next_at, boundary)


def _quantile(values, p):
    if not values:
        return None
    xs = sorted(values)
    idx = (len(xs)-1) * p
    left = int(idx)
    return xs[left] + (xs[min(left+1, len(xs)-1)] - xs[left]) * (idx-left)


def _compact_curve(rows, cap=600):
    if len(rows) <= cap:
        return rows
    # Retain local extrema as well as endpoints; metrics always use all bars.
    result = [rows[0]]
    size = math.ceil((len(rows)-2) / ((cap-2)//2))
    for start in range(1, len(rows)-1, size):
        chunk = rows[start:min(len(rows)-1, start+size)]
        ids = sorted({min(range(len(chunk)), key=lambda j: chunk[j]['equity']),
                      max(range(len(chunk)), key=lambda j: chunk[j]['equity'])})
        result.extend(chunk[j] for j in ids)
    result.append(rows[-1])
    return result


def _metrics(trades, equity, max_dd, close_dd):
    rs = [t['net_r'] for t in trades]
    positive = sum(max(0, r) for r in rs)
    negative = -sum(min(0, r) for r in rs)
    return {'trades': len(trades), 'completed_trades': sum(t['exit_reason'] != 'sample_end' for t in trades),
            'terminal_exits': sum(t['exit_reason'] == 'sample_end' for t in trades),
            'win_rate': sum(r > 0 for r in rs) / len(rs) if rs else None,
            'profit_factor': positive / negative if negative > 0 else None,
            'profit_factor_basis': 'net_trade_R',
            'profit_factor_note': '没有亏损交易，利润因子不定义' if rs and negative == 0 else '',
            'mean_r': sum(rs) / len(rs) if rs else None,
            'return_pct': equity - 100., 'max_drawdown_pct': max_dd * 100,
            'close_drawdown_pct': close_dd * 100,
            'equity_end': equity, 'depleted': equity <= 0}


def _backtest(bars, signals, start, end, options, resolution, final_successor, use_rsi=True):
    equity, position, trades = 100., None, []
    peak, max_dd, close_peak, close_dd = 100., 0., 100., 0.
    curve = [{'time': bars[start]['time'], 'equity': equity}]
    risk_fraction = options['risk_pct'] / 100

    def funding(pos, at):
        # Explicit calendar-day debit estimate, not an IG funding calculation.
        return abs(pos['entry_price']) * options['financing_bps_daily'] / 10000 * max(0, at-pos['entry_time']) / 86400

    def value(pos, price, at):
        return pos['equity_before'] + pos['quantity'] * (pos['direction_value'] * (price-pos['entry_price'])
               - options['fee_points'] * 2 - funding(pos, at))

    def observe(favourable, adverse, closing):
        nonlocal peak, max_dd, close_peak, close_dd
        # OHLC cannot establish high/low order. Take favourable first, adverse
        # second as an explicit conservative bound, not an exact tick drawdown.
        peak = max(peak, favourable)
        max_dd = max(max_dd, (peak-adverse)/peak)
        close_peak = max(close_peak, closing)
        close_dd = max(close_dd, (close_peak-closing)/close_peak)

    def close(pos, quote, at, reason, spread_proxy, bar_index):
        nonlocal equity
        exit_price = quote - pos['direction_value'] * options['slippage']
        gross = pos['direction_value'] * (exit_price-pos['entry_price'])
        financing = funding(pos, at)
        fees = 2 * options['fee_points']
        net = gross - financing - fees
        equity = pos['equity_before'] + pos['quantity'] * net
        trade = {k: pos[k] for k in ('direction', 'signal_time', 'entry_time', 'entry_price',
                 'stop_price', 'initial_risk_points', 'equity_before', 'atr_entry', 'rsi_entry')}
        trade.update(exit_time=at, exit_price=exit_price, exit_reason=reason,
                     gross_points=gross, financing_points=financing, fee_points=fees,
                     slippage_points=options['slippage']*2,
                     spread_proxy_points=pos['entry_spread'] + (spread_proxy if pos['direction_value'] == -1 else 0),
                     net_points=net, net_r=net/pos['initial_risk_points'],
                     equity_after=equity, bars_held=bar_index-pos['entry_index']+1)
        trades.append(trade)
        return equity

    for i in range(start, end):
        b, sig = bars[i], signals[i-1]
        at, end_at = b['time'], _end_time(bars, i, resolution, final_successor)
        exited = False
        if position:
            p = position
            side = b if p['direction_value'] == 1 else b['ask']
            gap = side['open'] <= p['stop_price'] if p['direction_value'] == 1 else side['open'] >= p['stop_price']
            if gap or _invalid(sig, p['direction_value'], use_rsi):
                after = close(p, side['open'], at, 'gap_stop' if gap else 'signal', b['ask']['open']-b['open'], i)
                observe(after, after, after)
                position, exited = None, True
        if position is None and not exited and equity > 0:
            direction = _direction(sig, options['mode'], use_rsi)
            if direction:
                entry = (b['ask']['open'] if direction == 1 else b['open']) + direction * options['slippage']
                distance = options['stop_atr'] * sig['atr14']
                # Avoid pathological division/overflow from negligible ranges.
                if distance > max(1, abs(entry)) * 1e-12:
                    position = {'direction_value': direction, 'direction': 'BUY' if direction == 1 else 'SELL',
                                'signal_time': bars[i-1]['time'], 'entry_time': at, 'entry_price': entry,
                                'entry_index': i, 'stop_price': entry-direction*distance,
                                'initial_risk_points': distance, 'equity_before': equity,
                                'quantity': equity*risk_fraction/distance, 'atr_entry': sig['atr14'],
                                'rsi_entry': sig['rsi14'],
                                'entry_spread': b['ask']['open']-b['open'] if direction == 1 else 0}
        if position:
            p = position
            side = b if p['direction_value'] == 1 else b['ask']
            gap = side['open'] <= p['stop_price'] if p['direction_value'] == 1 else side['open'] >= p['stop_price']
            touched = side['low'] <= p['stop_price'] if p['direction_value'] == 1 else side['high'] >= p['stop_price']
            favourable_price = side['high'] if p['direction_value'] == 1 else side['low']
            adverse_price = side['low'] if p['direction_value'] == 1 else side['high']
            if gap or touched:
                after = close(p, side['open'] if gap else p['stop_price'], at if gap else end_at,
                              'gap_stop' if gap else 'stop', b['ask']['open']-b['open'], i)
                favourable = after if gap else max(after, value(p, favourable_price, at))
                observe(favourable, after, after)
                position = None
            elif i == end-1:
                after = close(p, side['close'], end_at, 'sample_end', b['ask']['close']-b['close'], i)
                observe(max(after, value(p, favourable_price, at)), min(after, value(p, adverse_price, end_at)), after)
                position = None
            else:
                equity = value(p, side['close'], end_at)
                observe(max(equity, value(p, favourable_price, at)), min(equity, value(p, adverse_price, end_at)), equity)
                # If the marked equity is depleted, liquidate in this research
                # model and never initiate another trade. Not an IG margin rule.
                if equity <= 0:
                    equity = close(p, side['close'], end_at, 'model_depletion', b['ask']['close']-b['close'], i)
                    observe(equity, equity, equity)
                    position = None
        else:
            observe(equity, equity, equity)
        curve.append({'time': end_at, 'equity': equity})
        if abs(equity) > 1e100:
            raise ChartError('strategy_numeric_range')
    return {'metrics': _metrics(trades, equity, max_dd, close_dd), 'curve': curve, 'trades': trades,
            'start': bars[start]['time'], 'end': _end_time(bars, end-1, resolution, final_successor)}


def _cost_stress(segment, risk_pct):
    equity, peak, dd = 100., 100., 0.
    curve = [{'time': segment['start'], 'equity': equity}]
    stressed = []
    for t in segment['trades']:
        if equity <= 0:
            break
        extra = t['spread_proxy_points'] + t['slippage_points'] + t['financing_points'] + t['fee_points']
        r = t['net_r'] - extra / t['initial_risk_points']
        equity *= 1 + risk_pct/100 * r
        peak = max(peak, equity)
        dd = max(dd, (peak-equity)/peak)
        stressed.append({**t, 'net_r': r, 'equity_after': equity})
        curve.append({'time': t['exit_time'], 'equity': equity})
    return {'metrics': _metrics(stressed, equity, dd, dd), 'curve': _compact_curve(curve),
            'drawdown_basis': 'trade_close_only'}


def _bootstrap(rs, risk_pct, block_length=3, paths=500, horizon=50, seed=SEED):
    rng = random.Random(seed)
    values, drawdowns, samples = [], [], [[] for _ in range(horizon+1)]
    for _ in range(paths):
        equity, peak, dd, j = 100., 100., 0., 0
        samples[0].append(equity)
        while j < horizon:
            start = rng.randrange(len(rs))
            for offset in range(block_length):
                if j == horizon:
                    break
                if equity > 0:
                    equity *= 1 + risk_pct / 100 * rs[(start+offset) % len(rs)]
                    if not math.isfinite(equity) or abs(equity) > 1e100:
                        raise ChartError('strategy_numeric_range')
                peak = max(peak, equity)
                dd = max(dd, (peak-equity)/peak)
                j += 1
                samples[j].append(equity)
        values.append(equity)
        drawdowns.append(dd * 100)
    return {'status': 'READY', 'paths': paths, 'horizon': horizon, 'block_length': block_length, 'seed': seed,
            'fan': [{'trade': i, 'p05': _quantile(v, .05), 'p50': _quantile(v, .5), 'p95': _quantile(v, .95)}
                    for i, v in enumerate(samples)],
            'terminal': {'p05': _quantile(values, .05), 'p50': _quantile(values, .5), 'p95': _quantile(values, .95)},
            'drawdown': {'p50': _quantile(drawdowns, .5), 'p95': _quantile(drawdowns, .95),
                         'probability_ge_60_pct': sum(x >= 60 for x in drawdowns)/paths*100},
            'depletion_probability_pct': sum(x <= 0 for x in values)/paths*100}


def _pack(segment):
    return {**segment, 'curve': _compact_curve(segment['curve']),
            'trade_rows_total': len(segment['trades']), 'trade_rows_shown': len(segment['trades'])}


def run_study(history, epic, resolution, options=None, epoch=None):
    """Return finite JSON-ready research results; never request additional data."""
    options = validate_options(options)
    if resolution not in RESOLUTIONS:
        raise ChartError('invalid_strategy_resolution')
    epoch = time.time() if epoch is None else epoch
    out = {'status': 'BLOCKED', 'reasons': [], 'epic': epic, 'resolution': resolution,
           'generated_at': epoch, 'options': options, 'coverage': {}, 'warnings': [],
           'assumptions': [
               '研究假设补全了原有观察规则；未拟合参数，不代表用户实际交易记录。',
               'EMA20/50 与 RSI14 在收盘后确认；默认再要求突破前20根高低点；RSI70/30仅分档，不反向抄底摸顶。',
               '信号下一根开盘执行：多仓买Ask卖Bid，空仓卖Bid买Ask；单仓、不加仓，不在退出同根反向开仓。',
               '初始固定止损为入场价外2×信号ATR14；无固定止盈。收盘跌破/升破EMA20或RSI回到50后，下一根开盘退出。',
               '跳空越过普通止损按不利开盘处理；K线内触发按止损位加滑点，无法重建逐笔路径。',
               '权益指数从100开始，每笔按当时权益的设定风险比例除以初始止损距离计算抽象仓位；费用和跳空可令亏损超过该比例。不等于真实HKD账户，也未模拟保证金或仓位上限。',
               '历史Bid/Ask计入点差；额外滑点、佣金与每日融资为假设输入。0表示未计入，不表示IG实际免费。',
               '融资按入场名义价绝对值×每日基点×日历天数估算；K线内止损保守计至该根结束，非IG实际融资及三倍扣费规则。',
               '前70%/后30%按时间分开且边界平仓；后段是历史留出检验，用户已看过的行情不能宣称真正前瞻样本外。',
               '持仓按Bid/Ask逐根盯市；区间末尾强制退出单列。最大回撤按同根有利极值先发生、不利极值后发生估算保守上界，非精确逐笔回撤。',
               'Monte Carlo只重采样后段规则退出的交易R值，不重采样区间末平仓；不会预测下一根价格或纳入样本未见过的极端事件。',
           ], 'in_sample': None, 'out_of_sample': None, 'sensitivity': None, 'ablation': None,
           'monte_carlo': {'status': 'INSUFFICIENT', 'reason': '等待有效历史样本。'}, 'recommendations': []}

    def reason(code, message):
        out['reasons'].append({'code': code, 'message': message})

    if not isinstance(history, dict) or not isinstance(history.get('candles'), list):
        reason('missing_history', '尚无本机历史缓存。请先在图表页读取该产品及周期。')
        return out
    cached_rows = history['candles']
    if len(cached_rows) > 20000:
        reason('history_limit', '研究最多处理20000根缓存K线。')
        return out
    # The user selects a fixed trailing window before any performance is known.
    # Exclude future rows first so they cannot change the historical start date.
    future, candidates = 0, []
    for raw in cached_rows:
        stamp = _number(raw.get('time')) if isinstance(raw, dict) else None
        if stamp is not None and stamp > epoch:
            future += 1
        else:
            candidates.append(raw)
    rows = candidates[-options['max_bars']:]
    bars, bad, missing_ask, prior = [], 0, 0, None
    for raw in rows:
        stamp = _number(raw.get('time')) if isinstance(raw, dict) else None
        if stamp is None or not 0 <= stamp <= 253402214400:
            bad += 1
            continue
        if prior is not None and stamp <= prior:
            bad += 1
        prior = stamp
        b = {'time': stamp}
        valid = True
        for side in ('bid', 'ask'):
            source = raw if side == 'bid' else raw.get('ask')
            if not isinstance(source, dict):
                missing_ask += side == 'ask'
                valid = False
                continue
            prices = {k: _number(source.get(k)) for k in FIELDS}
            if any(v is None or abs(v) > 1e9 for v in prices.values()):
                if side == 'ask':
                    missing_ask += 1
                else:
                    bad += 1
                valid = False
                continue
            if prices['low'] > min(prices.values()) or prices['high'] < max(prices.values()):
                bad += 1
                valid = False
            if side == 'bid':
                b.update(prices)
            else:
                b['ask'] = prices
        if valid and any(b['ask'][k] < b[k] for k in FIELDS):
            bad += 1
            valid = False
        if valid:
            bars.append(b)
    successor = bars[-1]['time'] if bars else None
    bars = bars[:-1]  # Same confirmation rule as the chart indicators.
    out['coverage'] = {'bars': len(bars), 'raw_bars': len(rows), 'cached_bars': len(cached_rows),
                       'selected_bars': len(rows), 'source_window': 'last_n_bars',
                       'excluded_tail': int(successor is not None),
                       'excluded_future': future, 'ask_bars': len(bars),
                       'start': bars[0]['time'] if bars else None, 'end': bars[-1]['time'] if bars else None,
                       'split_at': None, 'warmup_bars': WARMUP, 'session_gaps': 0,
                       'unexplained_gaps': 0, 'updated_at': history.get('updated_at'),
                       'source': history.get('source', '本机 IG 历史缓存'), 'basis': 'historical_only',
                       'has_more': bool(history.get('has_more'))}
    if bad:
        reason('invalid_bars', f'{bad}项时间、Bid/Ask或OHLC校验未通过；不排序或修补后继续回测。')
    if missing_ask:
        reason('missing_ask', f'{missing_ask}根缺少完整Ask OHLC；无法可信地模拟空仓止损和双边成交成本。')
    if history.get('skipped_rows'):
        reason('skipped_rows', '最近读取曾跳过无效行情，需先核验缓存完整性。')
    if len(bars) < MIN_BARS:
        reason('insufficient_bars', f'至少需要{MIN_BARS}根已确认K线；当前{len(bars)}根。即使达到门槛也不代表样本充分。')
    if out['reasons']:
        return out
    gaps, conflicts = [], 0
    step = RESOLUTIONS[resolution]
    for a, b in zip(bars, bars[1:]):
        gaps.extend(gap_details(epic, [a, b], resolution, step))
    if step < 86400:
        conflicts = sum(open_seconds(epic, b['time'], b['time']+step) == 0 for b in bars)
    unexplained = sum(g['kind'] != 'session' for g in gaps)
    out['coverage'].update(session_gaps=len(gaps)-unexplained, unexplained_gaps=unexplained,
                           session_conflicts=conflicts)
    if unexplained:
        reason('unexplained_gaps', f'完整研究区间内有{unexplained}段无法由常规休市解释的缺口；暂停研究结果。')
    if conflicts:
        reason('session_conflict', f'{conflicts}根行情落在参考休市时段；需核验时段或历史时间戳。')
    if out['reasons']:
        return out
    split = int(len(bars)*.7)
    out['coverage']['split_at'] = bars[split]['time']
    out['coverage']['span_days'] = (successor-bars[0]['time'])/86400
    out['warnings'] = ['此结果仅覆盖已缓存历史，不代表完整市场周期；重复查看并调整参数会污染历史留出检验。',
                       '实际隔夜融资、汇兑、合约调整、保证金约束和盘口深度未完整建模；不能把模型收益称为实际净利润。']
    if options['max_bars'] == 200:
        out['warnings'].insert(0, '当前预设只检验最近200根（含末根待确认）；属于短样本初检，不能据此证明交易优势。')
    if not options['slippage'] or not options['financing_bps_daily'] or not options['fee_points']:
        out['warnings'].append('部分成本输入为0：结果属于成本未完整核验的研究情景，建议输入实际或偏保守估计后重跑。')
    if history.get('last_fetch_error') or history.get('last_fetch_status') in ('error', 'limited'):
        out['warnings'].append('最近行情刷新存在错误或额度限制；本次只检验明确覆盖日期内的缓存，不形成当前交易信号。')
    if epoch-successor > step*3:
        out['warnings'].append('历史末端未覆盖当前时点；这是历史实验，不是实时预测。')
    signals = _series(bars)
    first = _backtest(bars, signals, WARMUP, split, options, resolution, successor)
    last = _backtest(bars, signals, split, len(bars), options, resolution, successor)
    no_rsi = _backtest(bars, signals, split, len(bars), options, resolution, successor, use_rsi=False)
    sensitivity = {'basis': 'same_trade_sequence_extra_1x_costs',
                   'spread_proxy_note': '原交易序列重估：追加一倍点差、滑点、费用和融资。空仓K线内止损的额外点差按该根开盘价差代理；不是改变报价路径后重跑止损。',
                   'in_sample': _cost_stress(first, options['risk_pct']),
                   'out_of_sample': _cost_stress(last, options['risk_pct'])}
    rs = [t['net_r'] for t in last['trades'] if t['exit_reason'] != 'sample_end']
    mc = {'status': 'INSUFFICIENT', 'samples': len(rs), 'required': 30,
          'reason': f'后段只有{len(rs)}笔按规则退出的交易；至少30笔才展示重采样。30仅是显示门槛，不是统计可靠性的保证。'}
    if last['metrics']['depleted']:
        mc.update(status='BLOCKED', reason='后段假设权益已耗尽；暂停续存路径模拟，不能排除灾难性亏损后只重采样幸存交易。')
    elif len(rs) >= 30:
        mc = _bootstrap(rs, options['risk_pct'])
        mc.update(samples=len(rs), required=30, basis='conditional_trade_close_block_bootstrap',
                  note='固定500次、50笔交易、循环块长3、固定种子。百分位是给定历史样本和仓位假设的情景分布，不是未来概率保证；平仓序列遗漏持仓内风险与新极端事件。')
        mc['block_sensitivity'] = []
        for length in (1, 3, 5):
            candidate = mc if length == 3 else _bootstrap(rs, options['risk_pct'], block_length=length)
            mc['block_sensitivity'].append({'block_length': length, 'terminal': candidate['terminal'],
                                             'drawdown': candidate['drawdown']})
    recommendations = ['先固定规则并保留后续新行情作为前瞻检验，再讨论实盘；不要根据后段表现反复挑选参数。',
                       '用同一组规则跨不同市场阶段及持仓品种检验；RSI超买/超卖只提示状态，不直接逆势开仓。',
                       '先核实实际点差、隔夜融资和滑点；2ATR止损与单笔风险比例是本实验假设，尚未证明适合实盘。']
    if last['metrics']['completed_trades'] < 30:
        recommendations.insert(0, '后段交易数不足，先增加有效历史覆盖，不根据当前胜率或利润因子判断策略有效。')
    elif last['metrics']['mean_r'] is not None and last['metrics']['mean_r'] <= 0:
        recommendations.insert(0, '后段平均R不为正；本样本未支持现有规则具有正优势，不应据此提高仓位。')
    if sensitivity['out_of_sample']['metrics']['return_pct'] <= 0 < last['metrics']['return_pct']:
        recommendations.insert(0, '后段基础情景盈利但成本加倍后转负，优势对成本敏感；需先核实成交条件。')
    out.update(status='READY', in_sample=_pack(first), out_of_sample=_pack(last), sensitivity=sensitivity,
               ablation={'label': '移除RSI确认及RSI退出条件', 'basis': 'fixed_counterfactual_no_selection',
                         'note': '仅后段固定对照，不据此自动选出最优版本。', 'out_of_sample': _pack(no_rsi)},
               monte_carlo=mc, recommendations=recommendations)
    return out
