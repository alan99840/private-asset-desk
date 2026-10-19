"""Confirmed IG execution points; never infer fills from quotes, P/L or orders.

Activity dates without a timezone are deliberately not treated as UTC. Their
UTC time must be supplied by the same position's createdDateUTC or a uniquely
matched transaction's explicit UTC field. Nothing is sent to TradingView.
"""
import copy
import hashlib
import json
import math
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone

from chart_history import ChartError, EPIC

MAX_EVENTS = 20000
REASONS = {
    'missing_utc': '部分成交缺少可核验的 UTC 时间，未绘制',
    'missing_price_or_side': '部分成交缺少可靠成交价或买卖方向，未绘制',
    'ambiguous_actions': '一条活动涉及多笔成交且未提供逐笔分配，未绘制',
    'epic_unresolved': '部分成交流水无法可靠关联产品，未绘制',
    'ambiguous_transaction': '部分成交存在多条可能的流水匹配，未绘制',
    'conflicting_open': '部分历史开仓与当前持仓原始成交字段冲突，保留持仓记录',
    'unverified_close_side': '部分平仓买卖方向待核验，未绘制',
    'history_limit': '历史记录超过本次读取上限，较早记录未处理',
}


def finite(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def utc_time(value, utc_field=False):
    """A *UTC-named* field may omit Z. Generic dates must include an offset."""
    if not isinstance(value, str) or len(value) > 60 or not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', value):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            if not utc_field:
                return None
            stamp = stamp.replace(tzinfo=timezone.utc)
        result = stamp.timestamp()
        return result if math.isfinite(result) and 0 <= result <= 4102444800 else None
    except (ValueError, OverflowError, OSError):
        return None


def identity(value):
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def aliases(row):
    details = row.get('details') if isinstance(row.get('details'), dict) else {}
    actions = details.get('actions') if isinstance(details.get('actions'), list) else []
    return {v for v in [identity(row.get('dealId')), identity(details.get('dealReference')),
                        *(identity(a.get('affectedDealId')) for a in actions if isinstance(a, dict))] if v}


def build_markers(epic, positions, events, history_limited=False):
    """Pure normalization for one account's records. No network, no order API."""
    omitted = Counter({'history_limit': 1} if history_limited else {})
    candidates = []
    transactions = []
    open_directions = defaultdict(set)
    for outer in positions:
        p = outer.get('position') if isinstance(outer, dict) else None
        m = outer.get('market') if isinstance(outer, dict) else None
        if isinstance(p, dict) and isinstance(m, dict) and m.get('epic') == epic and identity(p.get('dealId')) and p.get('direction') in ('BUY', 'SELL'):
            open_directions[p['dealId']].add(p['direction'])
    for event in events:
        row = event.get('raw')
        if event.get('kind') != 'activity' or not isinstance(row, dict) or row.get('epic') != epic or row.get('status') != 'ACCEPTED':
            continue
        details = row.get('details') if isinstance(row.get('details'), dict) else {}
        actions = details.get('actions') if isinstance(details.get('actions'), list) else []
        if len(actions) == 1 and isinstance(actions[0], dict) and actions[0].get('actionType') == 'POSITION_OPENED' and details.get('direction') in ('BUY', 'SELL'):
            deal = identity(actions[0].get('affectedDealId')) or identity(row.get('dealId'))
            if deal:
                open_directions[deal].add(details['direction'])
    for event in events:
        row = event.get('raw')
        if not isinstance(row, dict):
            continue
        if event.get('kind') == 'transactions':
            # Cash flows, charges and adjustments are not executions.
            if row.get('cashTransaction') is False and finite(row.get('openLevel')) is not None and finite(row.get('closeLevel')) is not None:
                transactions.append(row)
            continue
        if event.get('kind') != 'activity' or row.get('epic') != epic or row.get('status') != 'ACCEPTED':
            continue
        details = row.get('details') if isinstance(row.get('details'), dict) else {}
        actions = details.get('actions') if isinstance(details.get('actions'), list) else []
        fills = [a for a in actions if isinstance(a, dict) and a.get('actionType') in ('POSITION_OPENED', 'POSITION_CLOSED', 'POSITION_PARTIALLY_CLOSED')]
        if not fills:
            continue
        # A single activity level/size does not allocate a bulk/netting fill to
        # several positions. Never replicate its total quantity onto each leg.
        if len(fills) != 1 or len(actions) != 1:
            omitted['ambiguous_actions'] += 1
            continue
        action = fills[0]
        kind = 'OPEN' if action['actionType'] == 'POSITION_OPENED' else 'CLOSE'
        side = details.get('direction')
        price = finite(details.get('level'))
        if side not in ('BUY', 'SELL') or price is None:
            omitted['missing_price_or_side'] += 1
            continue
        deal = identity(action.get('affectedDealId')) or identity(row.get('dealId'))
        if kind == 'CLOSE':
            original_sides = open_directions.get(deal, set())
            # The API calls this "Deal direction" without explicitly stating
            # whether close activity reports the old position or the execution.
            # Require a unique original side and an opposite reported side.
            if len(original_sides) != 1 or side in original_sides:
                omitted['unverified_close_side'] += 1
                continue
        candidates.append({'row': row, 'aliases': aliases(row), 'action': kind,
                           'side': side, 'price': price, 'size': finite(details.get('size'), True),
                           'deal': deal,
                           'execution': identity(row.get('dealId')) or '',
                           'reference': identity(details.get('dealReference')) or identity(row.get('dealId')) or '',
                           'time': utc_time(row.get('date')), 'source': 'IG history/activity v3'})

    results = []
    consumed = set()
    current_deals = set()
    # Current positions are authoritative for original open time and price.
    # Their size is the REMAINING size, so use original activity size or null.
    for outer in positions:
        position = outer.get('position') if isinstance(outer, dict) else None
        market = outer.get('market') if isinstance(outer, dict) else None
        if not isinstance(position, dict) or not isinstance(market, dict) or market.get('epic') != epic:
            continue
        deal = identity(position.get('dealId'))
        stamp = utc_time(position.get('createdDateUTC'), True)
        price = finite(position.get('level'))
        side = position.get('direction')
        if stamp is None:
            omitted['missing_utc'] += 1
            continue
        if price is None or side not in ('BUY', 'SELL') or not deal:
            omitted['missing_price_or_side'] += 1
            continue
        current_deals.add(deal)
        matches = [(i, c) for i, c in enumerate(candidates) if c['action'] == 'OPEN' and deal == c['deal'] and c['price'] == price and c['side'] == side and (c['time'] is None or abs(c['time']-stamp) < 1)]
        original_size = None
        reference = deal
        if len(matches) == 1:
            i, candidate = matches[0]
            original_size = candidate['size']
            reference = candidate['reference'] or deal
            consumed.add(i)
        results.append({'deal': deal, 'reference': reference, 'time': stamp, 'price': price,
                        'side': side, 'action': 'OPEN', 'size': original_size,
                        'source': 'IG positions createdDateUTC' + (' + history/activity' if len(matches) == 1 else '')})

    # Match transaction references exactly, never by instrumentName. A reference
    # must identify one accepted position action with matching execution fields.
    transaction_matches = defaultdict(list)
    mapped_transactions = set()
    reference_products = defaultdict(set)
    for event in events:
        row = event.get('raw')
        if event.get('kind') == 'activity' and isinstance(row, dict) and identity(row.get('epic')):
            for ref in aliases(row):
                reference_products[ref].add(row['epic'])
    for tx_index, tx in enumerate(transactions):
        ref = identity(tx.get('reference'))
        if not ref or reference_products.get(ref) != {epic}:
            continue
        for action in ('OPEN', 'CLOSE'):
            price = finite(tx.get('openLevel' if action == 'OPEN' else 'closeLevel'))
            stamp = utc_time(tx.get('openDateUtc' if action == 'OPEN' else 'dateUtc'), True)
            if price is None or stamp is None:
                continue
            matches = []
            for i, candidate in enumerate(candidates):
                if candidate['action'] != action or ref not in candidate['aliases'] or candidate['price'] != price:
                    continue
                period = candidate['row'].get('period')
                if period and tx.get('period') and period != tx['period']:
                    continue
                if candidate['time'] is not None and abs(candidate['time']-stamp) >= 1:
                    continue
                # Closed quantity must agree when the activity supplies it.
                tx_size = finite(tx.get('size'))
                if action == 'CLOSE' and candidate['size'] is not None and (tx_size is None or abs(tx_size) != candidate['size']):
                    continue
                matches.append(i)
            if len(matches) == 1:
                transaction_matches[matches[0]].append((stamp, tx_index))
                mapped_transactions.add(tx_index)
    # Only count unresolvable trade rows when their reference is not known to a
    # different product. We never disclose another product's raw records here.
    other_refs = set()
    for event in events:
        row = event.get('raw')
        if event.get('kind') == 'activity' and isinstance(row, dict) and row.get('epic') != epic:
            other_refs.update(aliases(row))
    for i, tx in enumerate(transactions):
        if i not in mapped_transactions and tx.get('reference') not in other_refs:
            omitted['epic_unresolved'] += 1

    for i, candidate in enumerate(candidates):
        if i in consumed:
            continue
        if candidate['action'] == 'OPEN' and candidate['deal'] in current_deals:
            # An existing deal cannot have two original opening executions.
            # Do not draw a second point when broker records disagree.
            omitted['conflicting_open'] += 1
            continue
        stamp = candidate['time']
        source = candidate['source']
        matches = transaction_matches.get(i, [])
        times = {t for t, _ in matches}
        if stamp is None:
            if len(times) == 1:
                stamp = next(iter(times))
                source += ' + history/transactions UTC'
            elif len(times) > 1:
                omitted['ambiguous_transaction'] += 1
                continue
            else:
                omitted['missing_utc'] += 1
                continue
        results.append({k: candidate[k] for k in ('deal', 'execution', 'reference', 'price', 'side', 'action', 'size')} | {'time': stamp, 'source': source})

    # An opening deal has exactly one original execution. For closures include
    # the execution reference and full-precision time, preserving separate fills
    # in the same second and separate partial closes at the same price.
    markers = {}
    for point in results:
        key = (epic, point['action'], point['deal'],
               (point.get('execution'), point['reference'], point['size']) if point['action'] == 'CLOSE' else '',
               point['time'], point['price'], point['side'])
        digest = hashlib.sha256(json.dumps(key, separators=(',', ':')).encode()).hexdigest()[:24]
        public = {k: point[k] for k in ('price', 'side', 'action', 'size', 'reference', 'source')}
        public.update(id='fill_'+digest, time=int(point['time']))
        if digest not in markers or (markers[digest]['size'] is None and public['size'] is not None):
            markers[digest] = public
    rows = sorted(markers.values(), key=lambda item: (item['time'], item['id']))
    omissions = [{'reason': reason, 'count': count, 'message': REASONS[reason]} for reason, count in sorted(omitted.items()) if count]
    return {'epic': epic, 'markers': rows, 'omitted': omissions,
            'message': '仅标记已核验时间、产品和价格的真实成交；持仓剩余数量不作为原始开仓数量。' if rows else '暂无可核验的成交点；挂单、修改及失败请求不作为成交。'}


class TradeMarkers:
    def __init__(self, monitor):
        self.monitor = monitor
        self.lock = threading.Lock()
        self.cache = {}

    def get(self, epic):
        if not isinstance(epic, str) or not EPIC.fullmatch(epic) or '..' in epic:
            raise ChartError('invalid_epic')
        with self.monitor.lock:
            scope = self.monitor.scope
            if not scope:
                return {'epic': epic, 'markers': [], 'omitted': [], 'message': '账户尚未连接，暂无成交点。', 'status': 'account_not_connected'}
            known = set(self.monitor.market_info)
            known.update(m.get('epic') for m in self.monitor.watch if isinstance(m, dict))
            positions = copy.deepcopy(self.monitor.state.get('positions') or [])
            known.update((p.get('market') or {}).get('epic') for p in positions)
            if epic not in known:
                raise ChartError('epic_not_monitored', 403)
            # Read the selected account only. A scoped query limit bounds memory
            # even if a viewer opens several charts in parallel.
            with self.monitor.store.lock:
                records = self.monitor.store.db.execute('SELECT kind,payload FROM events WHERE scope=? AND kind IN (?,?) ORDER BY at DESC LIMIT ?',
                    (scope, 'activity', 'transactions', MAX_EVENTS+1)).fetchall()
        fingerprint = hashlib.sha256(json.dumps([scope, positions, records], sort_keys=True).encode()).hexdigest()
        with self.lock:
            cached = self.cache.get((scope, epic))
            if cached and cached[0] == fingerprint:
                result = copy.deepcopy(cached[1])
            else:
                events = [{'kind': kind, 'raw': json.loads(payload)} for kind, payload in records[:MAX_EVENTS]]
                result = build_markers(epic, positions, events, len(records) > MAX_EVENTS)
                self.cache = {key: value for key, value in self.cache.items() if key[0] == scope}
                self.cache[(scope, epic)] = (fingerprint, copy.deepcopy(result))
        with self.monitor.lock:
            if self.monitor.scope != scope:
                raise ChartError('account_changed', 503)
        return result
