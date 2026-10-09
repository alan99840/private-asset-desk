"""Capture quote data for V2.3. No trade, account, email or model calls."""
import argparse
import fcntl
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from advisor import HERE, ET, RULES, clock
from quote_client import QuoteClient, redact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['premarket', 'intraday', 'smoke'], required=True)
    parser.add_argument('--symbols', nargs='*', default=[])
    parser.add_argument('--max-pages', type=int, default=20)
    args = parser.parse_args()
    for symbol in args.symbols:
        if not re.fullmatch(r'US\.[A-Za-z0-9._-]{1,24}', symbol):
            raise ValueError('Invalid US symbol')
    if not 1 <= args.max_pages <= 20:
        raise ValueError('max-pages outside 1..20')
    lock = (HERE / 'state/collector.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({'status': 'SKIPPED', 'reason': 'Another quote capture is running'}))
        return
    now = datetime.now(timezone.utc)
    info = clock(now)
    session = info['trade_date']
    directory = HERE / 'daily' / session / ('capture_' + now.strftime('%H%M%S_UTC'))
    directory.mkdir(parents=True, exist_ok=True)
    client = None
    manifest = {'version': 'V2.3', 'phase': args.phase, 'requested_at': now.isoformat(),
                'ET': info['ET'], 'HK': info['HK'], 'session': session,
                'quotes_only': True, 'actual_orders_submitted': False, 'captures': []}
    bundle = {'status': 'DATA_BLOCKED', 'session': session, 'calendar_verified': False,
              'candidates': [], 'macro_status': 'unknown', 'scan_complete': False}
    def call(name, params):
        raw, parsed = client.call(name, params)
        stamp = datetime.now(timezone.utc).isoformat()
        index = len(manifest['captures']) + 1
        record = {'tool': name, 'arguments': params, 'captured_at': stamp, 'response': raw}
        text = json.dumps(record, ensure_ascii=False, indent=2)
        filename = f'{index:03d}_{name}.json'
        (directory / filename).write_text(text + '\n')
        manifest['captures'].append({'file': filename, 'tool': name, 'captured_at': stamp,
                                     'ret_code': parsed.get('ret_code'),
                                     'sha256': hashlib.sha256(text.encode()).hexdigest()})
        return parsed
    try:
        client = QuoteClient()
        manifest.update(auth_status=client.auth_status, runtime_status=client.runtime_status)
        endday = datetime.fromisoformat(session).date()
        calendar = call('quote_trading_days', {'market': 'US',
                        'start': (endday - timedelta(days=50)).isoformat(),
                        'end': (endday + timedelta(days=10)).isoformat()})
        days = calendar.get('data', {}).get('trading_days', [])
        dates = [r['time'] for r in days]
        bundle['trading_dates'] = dates
        bundle['calendar_verified'] = calendar.get('ret_code') == 0
        bundle['is_US_session'] = session in dates
        bundle['next_US_session'] = next((d for d in dates if d > session), None)
        if args.phase == 'smoke':
            symbols = args.symbols or ['US.CAPR', 'US.QQQ', 'US.SPY']
            bundle['snapshot_probe'] = call('quote_market_snapshot', {'code_list': symbols})
            bundle['news_probe'] = call('quote_news_search', {'symbol': 'US.CAPR', 'size': 3, 'sort_type': 2, 'lang': 'en'})
            bundle['status'] = 'REST' if session not in dates else 'SMOKE_ONLY'
            bundle['capture_is_not_advice'] = True
        elif not bundle['calendar_verified']:
            bundle['error'] = 'Trading calendar unavailable'
        elif session not in dates:
            bundle['status'] = 'REST'
        elif info['phase'] == 'IDLE':
            bundle['status'] = 'WAIT_OUTSIDE_ADVISORY_WINDOW'
        else:
            macro = call('quote_economic_calendar_hot', {'date': session.replace('-', ''),
                         'timezone': 'America/New_York', 'limit': 20})
            bundle['macro'] = macro
            # Empty or partial provider data still requires primary-source verification.
            bundle['macro_status'] = 'requires_primary_source_verification'
            call('quote_market_state', {'code_list': ['US.QQQ'], 'is_contain_ba': True})
            candidates = []
            if args.phase == 'premarket':
                cursor, seen = None, set()
                for page in range(args.max_pages):
                    params = {'plate_code': 'US.USAALL', 'price_type': 'BEFORE',
                              'sort_field': 'PRE_CHANGE_RATE', 'ascend': False, 'limit': 1000}
                    if cursor:
                        params['next_key'] = cursor
                    stocks = call('quote_plate_stock', params)
                    if stocks.get('ret_code') != 0:
                        break
                    members = stocks.get('data', {}).get('stock_list', [])
                    codes = [r['code'] for r in members if r.get('stock_type') == 'STOCK'
                             and 'TEST' not in r['code'].upper() and r['code'] not in seen]
                    seen.update(codes)
                    for offset in range(0, len(codes), 400):
                        snap = call('quote_market_snapshot', {'code_list': codes[offset:offset+400]})
                        for s in snap.get('data', {}).get('snapshot_list', []):
                            pre, previous = s.get('pre_price'), s.get('prev_close_price')
                            if s.get('data_date') != session or not pre or not previous:
                                continue
                            if 5 <= pre <= 20 and pre / previous >= 1.10 and s.get('equity_valid') is True:
                                candidates.append({'symbol': s['code'], 'quote': s,
                                  'gap_fraction': pre / previous - 1,
                                  'float_status': 'pass' if 0 < (s.get('outstanding_shares') or 0) < 20_000_000 else
                                                  'fail' if (s.get('outstanding_shares') or 0) >= 20_000_000 else 'unknown',
                                  'security_type_status': 'requires_ordinary_share_verification'})
                    pagination = stocks.get('pagination', {})
                    if not pagination.get('has_more'):
                        bundle['scan_complete'] = True
                        break
                    next_cursor = pagination.get('next_key')
                    if not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
                bundle['scanned_codes'] = len(seen)
                candidates.sort(key=lambda x: (-x['gap_fraction'], -x['quote'].get('pre_volume', 0), x['symbol']))
                for candidate in candidates:
                    if candidate['float_status'] != 'pass':
                        continue
                    candidate['news'] = call('quote_news_search', {'symbol': candidate['symbol'], 'sort_type': 2, 'size': 10})
                bundle['candidates'] = candidates
                bundle['status'] = 'PREMARKET_DATA_REQUIRES_NEWS_RVOL_AND_SCOPE_REVIEW'
                bundle['RVOL_status'] = 'not_assumed; use calendar dates and full same-window historical bars'
            else:
                symbols = args.symbols
                if not symbols:
                    bundle['status'] = 'WAIT_NO_FROZEN_SYMBOLS'
                else:
                    for s in symbols:
                        kline = call('quote_cur_kline', {'symbol': s, 'num': 370, 'ktype': 1,
                                                       'autype': 0, 'extended_time': 0})
                        candidates.append({'symbol': s,
                                           'bars': kline.get('data', {}).get('kline_list', [])})
                    # Capture final snapshots and book after bars; never use their old
                    # timestamps as the actual evaluation time or claim tick coverage.
                    snapshots = call('quote_market_snapshot', {'code_list': symbols})
                    smap = {q['code']: q for q in snapshots.get('data', {}).get('snapshot_list', [])}
                    for candidate in candidates:
                        candidate['quote'] = smap.get(candidate['symbol'], {})
                        candidate['order_book_raw'] = call('quote_order_book', {'code': candidate['symbol'], 'num': 5})
                        candidate['order_book_requires_timestamp_and_permission_review'] = True
                    bundle['candidates'] = candidates
                    bundle['status'] = 'INTRADAY_RAW_DATA_REQUIRES_FROZEN_GATES_AND_USER_STATE'
        bundle['completed_at'] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        bundle.update(status='DATA_BLOCKED', error=redact(e))
    finally:
        if client:
            client.close()
        (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        (directory / 'bundle.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + '\n')
        (HERE / 'state/last_capture.json').write_text(json.dumps({'directory': str(directory),
                           'status': bundle['status'], 'session': session}, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': bundle['status'], 'directory': str(directory),
                           'captures': len(manifest['captures']), 'candidates': len(bundle['candidates']),
                           'next_US_session': bundle.get('next_US_session'), 'error': bundle.get('error')}, ensure_ascii=False))
        lock.close()


if __name__ == '__main__':
    main()
