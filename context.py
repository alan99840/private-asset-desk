"""Evidence and market freshness gates. Read-only; no trading interfaces."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from advisor import dt, ET, HK

HERE = Path(__file__).resolve().parent
INDICES = {'US..DJI': '道琼斯工业平均指数', 'US..IXIC': '纳斯达克综合指数', 'US..SPX': '标普500指数'}

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default

def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + '\n')
    temp.replace(path)

def regular_session(now, trading_dates):
    et = dt(now).astimezone(ET)
    return et.date().isoformat() in trading_dates and (9, 30) <= (et.hour, et.minute) < (16, 0)

def normalize_quotes(response, now, trading_dates):
    now = dt(now)
    regular = regular_session(now, trading_dates)
    rows = response.get('data', {}).get('quote_list', []) if response.get('ret_code') == 0 else []
    result = []
    for row in rows:
        stamp = row.get('data_time') or row.get('update_time')
        source = dt(stamp) if stamp else None
        age = (now-source).total_seconds() if source else None
        price, previous = row.get('last_price'), row.get('prev_close_price')
        try:
            valid_price = float(price) > 0 and float(previous) > 0
        except (ValueError, TypeError):
            valid_price = False
        status = ('INVALID' if not valid_price or age is None or age < -2 else
                  'STALE' if regular and (age > 90 or row.get('data_date') != now.astimezone(ET).date().isoformat()) else
                  'FRESH' if regular else 'LAST_SESSION')
        result.append({'symbol': row['code'], 'name': INDICES.get(row['code'], row.get('name', row['code'])),
                       'price': price, 'previous_close': previous,
                       'change_pct': (float(price)/float(previous)-1)*100 if valid_price else None,
                       'exchange_at': source.isoformat() if source else None,
                       'exchange_HK': source.astimezone(HK).isoformat() if source else None,
                       'source_age_seconds': round(age, 2) if age is not None else None,
                       'data_date': row.get('data_date'), 'status': status,
                       'open': row.get('open_price'), 'high': row.get('high_price'), 'low': row.get('low_price'),
                       'source': 'Futu quote_stock_quote', 'proxy': row['code'] not in INDICES})
    return result

def market_gate(market, now):
    """New entry needs current real indices. Protect/exit do not depend on this gate."""
    now = dt(now)
    if not market.get('calendar_verified'):
        return '指数监控的交易日历未核验'
    if not regular_session(now, market.get('trading_dates', [])):
        return '当前不是已核验的美国正常交易时段'
    rows = {r['symbol']: r for r in market.get('quotes', [])}
    for symbol in INDICES:
        row = rows.get(symbol)
        if not row or not row.get('exchange_at'):
            return '三大指数缺少实际报价，不能用ETF替代后放行'
        age = (now-dt(row['exchange_at'])).total_seconds()
        if age < -2 or age > 90 or row.get('data_date') != now.astimezone(ET).date().isoformat():
            return '三大指数报价陈旧或时点无效，暂停新增风险'
    if market.get('status') == 'DATA_BLOCKED':
        return '本次指数采集失败，暂停新增风险'
    return None

def select_events(research, now, symbols):
    """Reject future facts, unreviewed stories and unsupported asset links; deduplicate."""
    now = dt(now)
    rows, seen = [], set()
    for event in research.get('events', []):
        if event.get('review_status') != 'verified' or not event.get('fact') or not event.get('mechanism'):
            continue
        if not event.get('sources') or not any(s.get('primary') and s.get('url', '').startswith('https://') for s in event['sources']):
            continue
        try:
            known = dt(event['published_at'])
            reviewed = dt(event['reviewed_at'])
        except (KeyError, ValueError, TypeError):
            continue
        if known > now or reviewed > now:
            continue
        links = event.get('affected_assets', [])
        # An asset link requires its own economic explanation and confidence.
        links = [a for a in links if a.get('symbol') in symbols and a.get('path') and a.get('evidence')]
        if not links:
            continue
        key = event.get('event_key') or hashlib.sha256((event['fact']+event['published_at']).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        item = dict(event, affected_assets=links)
        item['within_past_24h'] = now-timedelta(hours=24) <= known <= now
        rows.append(item)
    rank = {'高': 3, '中': 2, '低': 1, '待验证': 0}
    rows.sort(key=lambda e: (-rank.get(e.get('strength'), 0), -dt(e['published_at']).timestamp()))
    return rows[:5]

def entry_context(packet, market, research):
    now = dt(packet['asof'])
    block = market_gate(market, now)
    cards = select_events(research, now, {c['symbol'] for c in packet.get('candidates', [])})
    if not research.get('asof') or dt(research['asof']) > now or dt(research['asof']).astimezone(ET).date() != now.astimezone(ET).date():
        block = block or '当日事件影响分析未完成，暂停新增风险'
    elif not research.get('watchlist_review_complete'):
        block = block or '冻结清单的公司及行业影响链尚未核验'
    adverse = {a['symbol'] for e in cards if e.get('entry_effect') == 'exclude_new_entry'
               for a in e['affected_assets']}
    return {'block': block, 'events': cards, 'adverse_symbols': adverse,
            'note': '指数涨跌仅提供市场背景，不单独构成买卖信号；正面新闻不能跳过技术和风险条件。'}
