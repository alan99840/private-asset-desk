"""Futu same-window RVOL capture with coverage checks and forward date paging."""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from advisor import HERE, ET, dt
from quote_client import QuoteClient, redact


def normalize(rows, minutes):
    result = {}
    for r in rows:
        end = dt(r['time_key']).astimezone(ET)
        result[end] = {'end': end, 'start': end-timedelta(minutes=minutes), 'row': r}
    return result


def same_window(rows, day, minutes):
    start = datetime.fromisoformat(day+'T04:00:00').replace(tzinfo=ET)
    expected = [start + timedelta(minutes=minutes*(i+1)) for i in range(305//minutes)]
    missing = [t.isoformat() for t in expected if t not in rows]
    volume = sum(D(str(rows[t]['row']['volume'])) for t in expected if t in rows)
    return {'volume': str(volume), 'missing': missing}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', required=True)
    args = ap.parse_args()
    if any(not re.fullmatch(r'US\.[A-Za-z0-9._-]{1,24}', s) for s in args.symbols):
        raise ValueError('Invalid US symbol')
    now = datetime.now(timezone.utc)
    session = now.astimezone(ET).date().isoformat()
    folder = HERE/'daily'/session/('RVOL_'+now.strftime('%H%M%S_UTC'))
    folder.mkdir(parents=True, exist_ok=True)
    client, captures, results = None, [], []
    def call(name, params):
        raw, parsed = client.call(name, params)
        record = {'tool': name, 'arguments': params, 'captured_at': datetime.now(timezone.utc).isoformat(), 'response': raw}
        filename = f'{len(captures)+1:03d}_{name}.json'
        (folder/filename).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        captures.append(filename)
        return parsed
    try:
        client = QuoteClient()
        current = datetime.fromisoformat(session).date()
        cal = call('quote_trading_days', {'market': 'US', 'start': (current-timedelta(days=55)).isoformat(), 'end': session})
        dates = [r['time'] for r in cal.get('data',{}).get('trading_days',[])]
        previous = [d for d in dates if d < session][-20:]
        if cal.get('ret_code') != 0 or len(previous) != 20 or session not in dates:
            raise RuntimeError('Current US session or prior 20 sessions unavailable')
        for symbol in args.symbols:
            result = {'symbol': symbol, 'session': session, 'status': 'unknown', 'prior_dates': previous}
            rehab = call('quote_corporate_actions_rehab', {'symbol':symbol,'divi_mode':'include_divi'})
            splits = []
            for e in rehab.get('data',{}).get('rehabs',[]):
                day = str(e.get('ex_div_date') or e.get('ex_date') or '').replace('/','-')
                if len(day) == 8 and day.isdigit():
                    day = day[:4]+'-'+day[4:6]+'-'+day[6:]
                ratio = e.get('split_ratio')
                if ratio is not None and D(str(ratio)) not in [D(0),D(1)] and previous[0] < day <= session:
                    splits.append({'date':day,'ratio':str(ratio)})
            daily = call('quote_history_kline', {'symbol':symbol,'start':previous[0],'end':session,
                         'ktype':2,'autype':0,'num':370,'extended_time':0})
            daily_rows = daily.get('data',{}).get('kline_list',[])
            listed_dates = {str(r['date']) for r in daily_rows if D(str(r.get('close',r.get('close_price',0))))>0}
            baseline, cursor = {}, previous[0]
            last_required = datetime.fromisoformat(previous[-1]+'T09:05:00').replace(tzinfo=ET)
            for _ in range(12):
                raw = call('quote_history_kline',{'symbol':symbol,'start':cursor,'end':session,
                           'ktype':6,'autype':0,'num':370,'extended_time':1})
                rows = normalize(raw.get('data',{}).get('kline_list',[]),5)
                if not rows:
                    break
                baseline.update(rows)
                last = max(rows)
                if last >= last_required:
                    break
                next_cursor = last.date().isoformat()
                if next_cursor == cursor:
                    break
                cursor = next_cursor
            today = call('quote_history_kline',{'symbol':symbol,'start':session,'end':(current+timedelta(days=1)).isoformat(),
                        'ktype':1,'autype':0,'num':370,'extended_time':1})
            minute_rows = {k:v for k,v in normalize(today.get('data',{}).get('kline_list',[]),1).items() if k <= now}
            numerator = same_window(minute_rows,session,1)
            baseline_windows = [dict(date=d, **same_window(baseline,d,5)) for d in previous]
            unlisted = [d for d in previous if d.replace('-','') not in listed_dates and
                        not any(k.date().isoformat()==d and v['row']['volume']>0 for k,v in baseline.items())]
            mean = sum(D(w['volume']) for w in baseline_windows)/20
            missing = bool(numerator['missing'] or any(w['missing'] for w in baseline_windows) or unlisted)
            valid = (not missing and not splits and rehab.get('ret_code')==0 and mean>0)
            value = D(numerator['volume'])/mean if valid else None
            result.update(status=('pass' if value>=5 else 'fail') if value is not None else 'unknown',
                          RVOL=str(value) if value is not None else None,
                          numerator=numerator,baseline=baseline_windows,prior_mean=str(mean),
                          listing_unknown_dates=unlisted,split_original_publication_requires_review=splits,
                          evaluated_at=now.isoformat())
            results.append(result)
    except Exception as e:
        results.append({'status':'unknown','error':redact(e)})
    finally:
        if client:
            client.close()
        outfile=folder/'results.json'
        outfile.write_text(json.dumps({'asof':now.isoformat(),'session':session,'captures':captures,'results':results},ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'output':str(outfile),'states':[{'symbol':r.get('symbol'),'status':r['status'],'RVOL':r.get('RVOL')} for r in results]},ensure_ascii=False))


if __name__=='__main__':
    main()
