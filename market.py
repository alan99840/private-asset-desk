"""Model-free one-minute polling of real indices and selected securities."""
import argparse
import time
from datetime import datetime, timedelta, timezone
from context import HERE, INDICES, read_json, write_json, normalize_quotes
from advisor import clock, ET, HK
from quote_client import QuoteClient, redact

def selected_symbols():
    watch = read_json(HERE/'state/watchlist.json')
    frozen = read_json(HERE/'state/frozen_watchlist.json')
    state = read_json(HERE/'state/portfolio.json')
    today = datetime.now(timezone.utc).astimezone(ET).date().isoformat()
    codes = list(INDICES)
    for row in watch.get('core', [])+watch.get('discretionary', []):
        if row.get('verified') is not False: codes.append(row['symbol'])
    if frozen.get('session') == today:
        codes.extend(r['symbol'] for r in frozen.get('candidates', []))
    codes.extend((state.get('actual_positions') or {}).keys())
    return list(dict.fromkeys(codes))[:30]

def capture(client=None, force=False):
    before = datetime.now(timezone.utc)
    timing = clock(before)
    if not force and timing['phase'] == 'IDLE':
        return {'status': 'OUTSIDE_WINDOW'}
    own = client is None
    try:
        client = client or QuoteClient()
        cached = read_json(HERE/'state/calendar.json')
        if not cached.get('verified_at') or (before-datetime.fromisoformat(cached['verified_at'])).total_seconds() > 21600:
            day = before.astimezone(ET).date()
            raw, cal = client.call('quote_trading_days', {'market': 'US', 'start': (day-timedelta(days=40)).isoformat(), 'end': (day+timedelta(days=15)).isoformat()})
            cached = {'verified': cal.get('ret_code') == 0, 'verified_at': datetime.now(timezone.utc).isoformat(),
                      'dates': [r['time'] for r in cal.get('data', {}).get('trading_days', [])]}
            write_json(HERE/'state/calendar.json', cached)
        if not cached.get('verified'): raise RuntimeError('US trading calendar unverified')
        start = time.monotonic()
        raw, parsed = client.call('quote_stock_quote', {'code_list': selected_symbols()})
        elapsed = time.monotonic()-start
        after = datetime.now(timezone.utc)
        raw_path = HERE/'daily'/after.astimezone(ET).date().isoformat()/('indices_'+after.strftime('%H%M%S_%f_UTC')+'.json')
        write_json(raw_path, {'requested_at': before.isoformat(), 'received_at': after.isoformat(), 'elapsed_seconds': round(elapsed,3), 'response': raw})
        quotes = normalize_quotes(parsed, after, cached['dates'])
        present = {r['symbol'] for r in quotes}
        status = 'CAPTURED' if parsed.get('ret_code') == 0 and all(k in present for k in INDICES) else 'DATA_BLOCKED'
        history = read_json(HERE/'state/market.json').get('history', [])
        history.append({'at': after.isoformat(), 'values': {r['symbol']: r['price'] for r in quotes if r['symbol'] in INDICES},
                        'source_times': {r['symbol']: r['exchange_at'] for r in quotes if r['symbol'] in INDICES}})
        result = {'version': 'V2.3', 'status': status, 'requested_at': before.isoformat(), 'captured_at': after.isoformat(),
                  'api_elapsed_seconds': round(elapsed,3), 'poll_target_seconds': 60, 'quotes': quotes,
                  'history': history[-120:], 'calendar_verified': True, 'trading_dates': cached['dates'],
                  'source_response_ret_code': parsed.get('ret_code'), 'quote_only': True,
                  'message': '非正常盘显示上个交易时段行情，不作为当前入场依据。' if timing['phase'] in ['IDLE','PREMARKET'] else '定时采集有延迟；以交易所报价时间核验新鲜度。'}
        write_json(HERE/'state/market.json', result)
        return result
    except Exception as exc:
        old = read_json(HERE/'state/market.json')
        old.update(status='DATA_BLOCKED', failed_at=datetime.now(timezone.utc).isoformat(), error=redact(exc))
        write_json(HERE/'state/market.json', old)
        return old
    finally:
        if own and client: client.close()

def loop(stop):
    client = None
    target = time.monotonic()
    try:
        while not stop.is_set():
            try:
                if clock()['phase'] != 'IDLE':
                    client = client or QuoteClient()
                    capture(client)
                elif client:
                    client.close(); client = None
            except Exception as exc:
                write_json(HERE/'state/monitor_error.json', {'at':datetime.now(timezone.utc).isoformat(), 'error':redact(exc)})
                if client: client.close(); client = None
            target += 60
            if target < time.monotonic(): target = time.monotonic()+60
            stop.wait(max(0,target-time.monotonic()))
    finally:
        if client: client.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('--force', action='store_true');args=parser.parse_args()
    r=capture(force=args.force)
    print({k:r.get(k) for k in ['status','captured_at','api_elapsed_seconds','error']})
