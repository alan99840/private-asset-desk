"""On-demand IG bid candles and shared drawings in a private, scoped cache.

Only the monitor's existing authenticated client is used. A browser poll never
blocks on IG and repeated polls share one bounded queue. No synthetic bars or
fallback ask/mid prices are inserted into the historical series.
"""
import calendar
import hashlib
import json
import math
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from credentials import STATE, prepare_state
from ig_client import IGError

RESOLUTIONS = {'MINUTE': 60, 'MINUTE_5': 300, 'MINUTE_15': 900,
               'MINUTE_30': 1800, 'HOUR': 3600, 'DAY': 86400,
               'WEEK': 604800, 'MONTH': 2678400}
EPIC = re.compile(r'[A-Za-z0-9_.-]{1,120}')
BATCH = 200
MAX_CANDLES = 20000
MAX_DRAWING_BYTES = 100000
PAGING_VERSION = 3
MAX_EMPTY_WINDOWS = 6


class ChartError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def iso_now(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat()


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (OverflowError, ValueError):
        return None


def utc_seconds(value):
    """IG snapshotTimeUTC is authoritative; never parse snapshotTime as UTC."""
    if not isinstance(value, str) or len(value) > 60:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = datetime.strptime(value, '%Y/%m/%d %H:%M:%S')
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    result = int(dt.timestamp())
    return result if 0 <= result <= 4102444800 else None


def normalize_prices(rows):
    if not isinstance(rows, list):
        raise IGError('ig.chart.response.invalid')
    candles = {}
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        stamp = utc_seconds(row.get('snapshotTimeUTC'))
        values = [finite((row.get(k) or {}).get('bid')) if isinstance(row.get(k), dict) else None
                  for k in ('openPrice', 'highPrice', 'lowPrice', 'closePrice')]
        if stamp is None or any(v is None for v in values):
            skipped += 1
            continue
        op, high, low, close = values
        if high < max(op, close, low) or low > min(op, close, high):
            skipped += 1
            continue
        candle = {'time': stamp, 'open': op, 'high': high, 'low': low, 'close': close}
        volume = finite(row.get('lastTradedVolume'))
        if volume is not None and volume >= 0:
            candle['volume'] = volume
        candles[stamp] = candle
    return sorted(candles.values(), key=lambda c: c['time']), skipped


def infer_query_offset(rows):
    """Observe the account-local wall clock from IG's paired timestamps.

    This is not inferred from account currency or this computer's timezone.
    A changing/ambiguous offset is deliberately not guessed as an IANA zone.
    """
    offsets = set()
    observations = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        utc = utc_seconds(row.get('snapshotTimeUTC'))
        local = row.get('snapshotTime')
        if utc is None or not isinstance(local, str):
            continue
        try:
            local_dt = datetime.fromisoformat(local)
        except ValueError:
            try:
                local_dt = datetime.strptime(local, '%Y/%m/%d %H:%M:%S')
            except ValueError:
                continue
        if local_dt.tzinfo is not None:
            continue
        offset = int(local_dt.replace(tzinfo=timezone.utc).timestamp()) - utc
        if -14*3600 <= offset <= 14*3600 and offset % 900 == 0:
            offsets.add(offset);observations += 1
    if not observations:
        return None
    return {'offset_seconds': next(iter(offsets)) if len(offsets) == 1 else None,
            'observed_offsets': sorted(offsets), 'observations': observations,
            'source': 'IG snapshotTime minus snapshotTimeUTC', 'observed_at': iso_now()}


def older_window(before, resolution, page_size):
    """A bounded explicit date range; MONTH follows calendar months, not 31 days.

    Daily/weekly/monthly windows leave one point of slack for calendar/session
    boundaries. Pagination still handles any extra point returned by IG.
    """
    dt = datetime.fromtimestamp(before, timezone.utc)
    units = max(1, page_size-1) if resolution in ('DAY', 'WEEK', 'MONTH') else page_size
    if resolution == 'MONTH':
        month_number = dt.year*12 + dt.month-1 - units
        year, month_index = divmod(month_number, 12)
        if year < 1970:
            start = 0
        else:
            month = month_index+1
            start = int(dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1])).timestamp())
    else:
        start = max(0, before - RESOLUTIONS[resolution]*units)
    return {'id': uuid.uuid4().hex, 'before': before, 'from': start,
            'to': before-1, 'page_size': page_size, 'page_number': 1,
            'total_pages': None, 'staged': [], 'fingerprints': [], 'skipped': 0}


def validate_drawings(drawings):
    if not isinstance(drawings, list) or len(drawings) > 200:
        raise ChartError('invalid_drawings')
    try:
        if len(json.dumps(drawings, allow_nan=False, ensure_ascii=False).encode()) > MAX_DRAWING_BYTES:
            raise ChartError('drawings_too_large', 413)
    except (ValueError, TypeError):
        raise ChartError('invalid_drawings') from None
    normalized, ids = [], set()
    for drawing in drawings:
        if not isinstance(drawing, dict) or set(drawing) != {'id', 'type', 'points', 'color', 'width'}:
            raise ChartError('invalid_drawing')
        identity, kind, points = drawing['id'], drawing['type'], drawing['points']
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identity) or identity in ids:
            raise ChartError('invalid_drawing_id')
        if kind not in ('trend', 'horizontal', 'rectangle', 'pen') or not isinstance(points, list):
            raise ChartError('invalid_drawing_type')
        if not (1 <= len(points) <= 1000) or (kind in ('trend', 'rectangle') and len(points) != 2) or (kind == 'horizontal' and len(points) not in (1, 2)) or (kind == 'pen' and len(points) < 2):
            raise ChartError('invalid_drawing_points')
        clean_points = []
        for point in points:
            if not isinstance(point, dict) or set(point) != {'time', 'price'}:
                raise ChartError('invalid_drawing_point')
            stamp, price = finite(point['time']), finite(point['price'])
            if stamp is None or price is None or not 0 <= stamp <= 4102444800 or abs(price) > 1e15:
                raise ChartError('invalid_drawing_point')
            clean_points.append({'time': stamp, 'price': price})
        color, width = drawing['color'], drawing['width']
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ChartError('invalid_drawing_color')
        if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 5:
            raise ChartError('invalid_drawing_width')
        normalized.append({'id': identity, 'type': kind, 'points': clean_points, 'color': color.lower(), 'width': width})
        ids.add(identity)
    return normalized


class ChartHistory:
    def __init__(self, monitor, path=None, start_worker=True):
        self.monitor = monitor
        self.lock = threading.RLock()
        self.pending = set()
        self.jobs = queue.Queue(maxsize=32)
        if path is None:
            prepare_state()
            path = STATE / 'charts.sqlite3'
        self.path = path
        if str(path) != ':memory:':
            Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        if str(path) != ':memory:':
            os.chmod(path, 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS chart_candles (
            scope TEXT, epic TEXT, resolution TEXT, time INTEGER, payload TEXT,
            PRIMARY KEY (scope,epic,resolution,time));
          CREATE TABLE IF NOT EXISTS chart_meta (
            scope TEXT, epic TEXT, resolution TEXT, payload TEXT,
            PRIMARY KEY (scope,epic,resolution));
          CREATE TABLE IF NOT EXISTS chart_allowance (
            scope TEXT PRIMARY KEY, payload TEXT);
          CREATE TABLE IF NOT EXISTS chart_settings (
            scope TEXT PRIMARY KEY, payload TEXT);
          CREATE TABLE IF NOT EXISTS chart_drawings (
            scope TEXT, epic TEXT, revision INTEGER, payload TEXT, updated_at TEXT,
            PRIMARY KEY (scope,epic));
        ''')
        self.db.commit()
        self.closed = threading.Event()
        if start_worker:
            threading.Thread(target=self._run, name='ig-chart-history', daemon=True).start()

    def close(self):
        self.closed.set()

    def _context(self, epic):
        if not isinstance(epic, str) or not EPIC.fullmatch(epic) or '..' in epic:
            raise ChartError('invalid_epic')
        with self.monitor.lock:
            scope = self.monitor.scope
            client = self.monitor.client
            known = set(self.monitor.market_info)
            known.update(m.get('epic') for m in self.monitor.watch if isinstance(m, dict))
            for position in self.monitor.state.get('positions') or []:
                known.add((position.get('market') or {}).get('epic'))
            if epic not in known:
                raise ChartError('epic_not_monitored', 403)
        if not scope:
            raise ChartError('account_not_connected', 503)
        return scope, client

    def _meta(self, key):
        row = self.db.execute('SELECT payload FROM chart_meta WHERE scope=? AND epic=? AND resolution=?', key).fetchone()
        return json.loads(row[0]) if row else {}

    def _put_meta(self, key, meta):
        self.db.execute('INSERT OR REPLACE INTO chart_meta VALUES (?,?,?,?)', (*key, json.dumps(meta, allow_nan=False)))

    def _upgrade_paging(self, key, meta):
        if meta.get('paging_version') == PAGING_VERSION:
            return meta
        # The initial to-only implementation was ignored by IG. Keep genuine
        # cached candles/drawings and invalidate only its historical cursors.
        for field in ('cursor_errors', 'completed_before', 'completed_requests',
                      'older_window', 'next_cursor', 'next_before'):
            meta.pop(field, None)
        meta['paging_version'] = PAGING_VERSION
        meta['has_more'] = True
        if meta.get('error') in ('ig.chart.upper.bound.ignored', 'ig.chart.range.bound.ignored', 'ig.chart.batch.limit.exceeded'):
            meta.update(error=None, retry_at=0, status='ready', message='历史分页方式已更新，可重新加载更早历史。')
        with self.db:
            self._put_meta(key, meta)
        return meta

    def _allowance(self, scope):
        row = self.db.execute('SELECT payload FROM chart_allowance WHERE scope=?', (scope,)).fetchone()
        return json.loads(row[0]) if row else None

    def _public_allowance(self, scope):
        allowance = self._allowance(scope)
        if not allowance:
            return None
        public = {k: v for k, v in allowance.items() if not k.startswith('_')}
        if allowance.get('_expires_at') is not None:
            public['allowanceExpiry'] = max(0, int(allowance['_expires_at'] - time.time()))
        return public

    def _record_allowance(self, scope, response):
        policy = infer_query_offset(response.get('prices')) if isinstance(response, dict) else None
        if policy is not None:
            with self.lock, self.db:
                self.db.execute('INSERT OR REPLACE INTO chart_settings VALUES (?,?)', (scope, json.dumps(policy)))
        metadata = response.get('metadata') if isinstance(response, dict) else None
        allowance = metadata.get('allowance') if isinstance(metadata, dict) else None
        if not isinstance(allowance, dict):
            return
        now = time.time()
        clean = {k: finite(allowance.get(k)) for k in ('remainingAllowance', 'totalAllowance', 'allowanceExpiry')}
        clean = {k: v for k, v in clean.items() if v is not None and v >= 0}
        clean['as_of'] = iso_now(now)
        if 'allowanceExpiry' in clean:
            clean['_expires_at'] = now + clean['allowanceExpiry']
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO chart_allowance VALUES (?,?)', (scope, json.dumps(clean)))

    def _query_offset(self, scope):
        row = self.db.execute('SELECT payload FROM chart_settings WHERE scope=?', (scope,)).fetchone()
        if not row:
            return None
        policy = json.loads(row[0])
        if len(policy.get('observed_offsets', [])) > 1:
            raise IGError('ig.chart.query.timezone.ambiguous')
        return policy.get('offset_seconds')

    def _ensure_query_offset(self, key, client):
        scope, epic, resolution = key
        with self.lock:
            observed = self._query_offset(scope)
            if observed is not None:
                return observed, 0
            if self._budget(scope, 1, False) < 1:
                raise IGError('local.chart.allowance.reserved')
        # Existing pre-timezone caches have no raw local timestamps. Spend at
        # most one point to learn the offset, never another 200-point guess.
        response = client.request('GET', '/prices/'+epic, 3,
                                  params={'resolution': resolution, 'max': 1, 'pageSize': 1, 'pageNumber': 1})
        self._record_allowance(scope, response)
        rows = response.get('prices')
        if not isinstance(rows, list) or len(rows) > 1:
            raise IGError('ig.chart.batch.limit.exceeded')
        with self.lock:
            observed = self._query_offset(scope)
        if observed is None:
            raise IGError('ig.chart.query.timezone.unavailable')
        return observed, len(rows)

    def _budget(self, scope, requested, automatic):
        allowance = self._allowance(scope)
        if not allowance:
            return requested
        if allowance.get('_blocked_until', 0) > time.time():
            return 0
        if allowance.get('_expires_at', float('inf')) <= time.time():
            return requested  # IG, rather than this cache, confirms a new allowance period.
        remaining = allowance.get('remainingAllowance')
        if remaining is None:
            return requested
        reserve = min(500, max(20, int(allowance.get('totalAllowance', 10000) * .05))) if automatic else 0
        return min(requested, max(0, int(remaining) - reserve))

    def get(self, epic, resolution, before=None, cursor=None):
        scope, client = self._context(epic)
        if resolution not in RESOLUTIONS:
            raise ChartError('invalid_resolution')
        if before is not None:
            if isinstance(before, bool) or not isinstance(before, int) or not 0 < before <= int(time.time()) + 86400:
                raise ChartError('invalid_before')
        if cursor is not None and (before is None or not isinstance(cursor, str) or not re.fullmatch(r'[a-f0-9]{32}:p[1-9][0-9]{0,3}', cursor)):
            raise ChartError('invalid_cursor')
        key = (scope, epic, resolution)
        with self.lock:
            rows = self.db.execute('SELECT payload FROM chart_candles WHERE scope=? AND epic=? AND resolution=? ORDER BY time DESC LIMIT ?', (*key, MAX_CANDLES)).fetchall()
            candles = [json.loads(row[0]) for row in reversed(rows)]
            meta = self._upgrade_paging(key, self._meta(key))
            earliest = candles[0]['time'] if candles else None
            older = before is not None
            request_id = cursor or ('before:'+str(before))
            completed = meta.get('completed_requests', [])
            window = meta.get('older_window')
            if cursor and request_id not in completed and (cursor != meta.get('next_cursor') or not window or before != window['before']):
                raise ChartError('invalid_cursor')
            # A repeat of the previous cursor is now inside the loaded cache.
            ttl = 60 if resolution == 'MINUTE' else 300 if RESOLUTIONS[resolution] < 86400 else 900 if resolution == 'DAY' else 3600
            needed = (not candles or before <= earliest) if older else time.time() - meta.get('last_success', 0) >= ttl
            if older and meta.get('has_more') is False:
                needed = False
            if older and request_id in completed:
                needed = False
            if older and window:
                # A completed query is polled as-is. Only the next explicit
                # user action carrying the returned cursor advances a page.
                needed = cursor == meta.get('next_cursor') and request_id not in completed
            cursor_error = (meta.get('cursor_errors') or {}).get(request_id) if older else None
            if cursor_error:
                needed = False
            at_limit = len(candles) >= MAX_CANDLES
            if older and at_limit:
                needed = False
            automatic = bool(candles) and not older
            elapsed = max(0, time.time() - meta.get('last_success', 0))
            count = BATCH if not candles or older else min(BATCH, max(3, int(elapsed / RESOLUTIONS[resolution]) + 2))
            if older and window and cursor == meta.get('next_cursor'):
                count = window['page_size']
            budget = self._budget(scope, count, automatic)
            limited = needed and (budget == 0 or (older and window and budget < count))
            status = 'ready'
            message = meta.get('message') or 'IG 历史 Bid K 线；缓存按需更新。'
            if needed and not client:
                status, message = 'error', 'IG 暂未连接，已保留本机历史缓存。'
            elif limited:
                status, message = 'limited', '历史行情配额暂不可用，或已到自动刷新保留额度；已有缓存仍可查看。'
            elif needed and meta.get('retry_at', 0) > time.time():
                status = meta.get('status', 'error')
                message = meta.get('message', 'IG 历史行情暂不可用，稍后自动重试。')
            elif needed and key not in self.pending:
                try:
                    self.jobs.put_nowait((key, client, before, budget, cursor))
                    self.pending.add(key)
                except queue.Full:
                    status, message = 'limited', '历史行情加载队列繁忙，请稍后再试。'
            if key in self.pending:
                status, message = 'loading', '正在从 IG 加载历史 K 线；已缓存的数据可继续查看。'
            elif not needed and meta.get('error'):
                status, message = meta.get('status', 'error'), meta.get('message', '')
            if cursor_error:
                status = 'error'
                message = ('IG 历史价格的本地时区偏移无法确认或跨时段变化，已暂停更早加载；UTC 历史缓存仍可查看。'
                           if cursor_error.startswith('ig.chart.query.timezone.') else
                           'IG 未按预期返回此历史区间，已停止重复请求以保护行情配额。')
            if not candles and meta.get('last_success') and not needed:
                message = meta.get('message') or 'IG 未返回该产品与周期的可用 Bid 历史 K 线。'
            if at_limit and not needed:
                message = f'此产品与周期已缓存最近 {MAX_CANDLES:,} 根 K 线，已到本机显示上限。'
            result = {'status': status, 'epic': epic, 'resolution': resolution,
                      'candles': candles, 'price_basis': 'bid',
                      'has_more': bool(meta.get('has_more', True)) and not at_limit,
                      'earliest': earliest, 'latest': candles[-1]['time'] if candles else None,
                      'updated_at': meta.get('updated_at'), 'allowance': self._public_allowance(scope),
                      'message': message, 'source': 'IG REST /prices v3',
                      'skipped_rows': meta.get('skipped_rows', 0), 'cache_limit': MAX_CANDLES,
                      'gaps': meta.get('gaps', []),
                      'next_before': meta.get('next_before', earliest if earliest is not None else int(time.time())),
                      'next_cursor': meta.get('next_cursor'), 'range_pending': bool(window)}
            if meta.get('error'):
                result['error'] = meta['error']
            if cursor_error:
                result['error'] = cursor_error
            return result

    def _run(self):
        while not self.closed.is_set():
            self.process_pending(timeout=.5)

    @staticmethod
    def _window_cursor(window):
        return window['id']+':p'+str(window['page_number'])

    def _fetch_older(self, key, client, before, count, cursor):
        scope, epic, resolution = key
        query_offset, spent = self._ensure_query_offset(key, client)
        with self.lock:
            count = self._budget(scope, max(0, count-spent), False)
        if not count:
            raise IGError('local.chart.allowance.reserved')
        with self.lock:
            meta = self._meta(key)
            window = meta.get('older_window') if cursor else None
        window = json.loads(json.dumps(window)) if window else older_window(before, resolution, count)
        skipped_total = 0
        for attempt in range(MAX_EMPTY_WINDOWS):
            with self.lock:
                if self._budget(scope, window['page_size'], False) < window['page_size']:
                    raise IGError('local.chart.allowance.page.unavailable')
            params = {'resolution': resolution,
                      'from': datetime.fromtimestamp(window['from']+query_offset, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
                      'to': datetime.fromtimestamp(window['to']+query_offset, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
                      'pageSize': window['page_size'], 'pageNumber': window['page_number']}
            response = client.request('GET', '/prices/'+epic, 3, params=params)
            self._record_allowance(scope, response)
            with self.lock:
                if self._query_offset(scope) != query_offset:
                    raise IGError('ig.chart.query.timezone.changed')
            raw = response.get('prices')
            if isinstance(raw, list) and len(raw) > window['page_size']:
                raise IGError('ig.chart.batch.limit.exceeded')
            candles, skipped = normalize_prices(raw)
            skipped_total += skipped
            window['skipped'] = window.get('skipped', 0)+skipped
            if any(c['time'] < window['from'] or c['time'] > window['to'] for c in candles):
                raise IGError('ig.chart.range.bound.ignored')
            metadata = response.get('metadata') or {}
            page_data = metadata.get('pageData') if isinstance(metadata, dict) else None
            if not isinstance(page_data, dict):
                raise IGError('ig.chart.paging.missing')
            pages, current = page_data.get('totalPages'), page_data.get('pageNumber')
            if isinstance(pages, bool) or not isinstance(pages, int) or not 0 <= pages <= 1000 or isinstance(current, bool) or current != window['page_number']:
                raise IGError('ig.chart.paging.invalid')
            if (raw and pages < current) or page_data.get('pageSize', window['page_size']) != window['page_size']:
                raise IGError('ig.chart.paging.invalid')
            if window['total_pages'] is not None and pages != window['total_pages']:
                raise IGError('ig.chart.paging.changed')
            window['total_pages'] = pages
            if raw:
                fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
                if fingerprint in window['fingerprints']:
                    raise IGError('ig.chart.paging.loop')
                window['fingerprints'].append(fingerprint)
            staged = {c['time']: c for c in window['staged']}
            staged.update({c['time']: c for c in candles})
            if len(staged) > MAX_CANDLES:
                raise IGError('ig.chart.range.too.large')
            window['staged'] = sorted(staged.values(), key=lambda c: c['time'])
            if window['page_number'] < pages:
                window['page_number'] += 1
                return {'candles': [], 'skipped': window['skipped'], 'window': window,
                        'next_before': window['before'], 'next_cursor': self._window_cursor(window),
                        'has_more': True, 'message': f"历史区间第 {window['page_number']-1}/{pages} 页已暂存；点击加载更早继续，整段补齐后显示。"}
            if window['staged']:
                # Only complete date windows are merged. An older first page
                # must not move the visible cursor past pages not fetched yet.
                return {'candles': window['staged'], 'skipped': window['skipped'], 'window': None,
                        'next_before': window['staged'][0]['time'], 'next_cursor': None,
                        'has_more': window['from'] > 0, 'message': ''}
            if window['from'] == 0:
                return {'candles': [], 'skipped': skipped_total, 'window': None,
                        'next_before': None, 'next_cursor': None, 'has_more': False,
                        'message': '已检索至本机历史搜索下限 1970 年；空窗口不代表 IG 的实际历史起点。'}
            if raw:
                # Invalid UTC/Bid rows still consume IG price-point allowance.
                # Do not fetch another paid page within this user action.
                window = older_window(window['from'], resolution, count)
                return {'candles': [], 'skipped': skipped_total, 'window': window,
                        'next_before': window['before'], 'next_cursor': self._window_cursor(window),
                        'has_more': True, 'message': '此日期窗返回的历史价格缺少有效 UTC/Bid OHLC；可点击加载更早继续。'}
            # A closed market can make the entire window empty. Walk a bounded
            # number of empty windows, without declaring that history has ended.
            window = older_window(window['from'], resolution, count)
        return {'candles': [], 'skipped': skipped_total, 'window': window,
                'next_before': window['before'], 'next_cursor': self._window_cursor(window), 'has_more': True,
                'message': f'连续 {MAX_EMPTY_WINDOWS} 个日期窗口未返回有效 Bid K 线，可能为休市或历史不可用；可点击加载更早继续检索。'}

    def process_pending(self, timeout=0):
        """One queue step; also allows deterministic offline tests without threads."""
        try:
            key, client, before, requested, cursor = self.jobs.get(timeout=timeout)
        except queue.Empty:
            return False
        scope, epic, resolution = key
        try:
            current_scope, current_client = self._context(epic)
            if current_scope != scope or current_client is not client or client is None:
                raise IGError('local.chart.connection.changed')
            with self.lock:
                meta = self._meta(key)
                count = self._budget(scope, requested, before is None and bool(meta.get('last_success')))
            if not count:
                raise IGError('local.chart.allowance.reserved')
            older = None
            if before is not None:
                older = self._fetch_older(key, client, before, count, cursor)
                candles, skipped = older['candles'], older['skipped']
            else:
                params = {'resolution': resolution, 'max': count, 'pageSize': 0}
                response = client.request('GET', '/prices/' + epic, 3, params=params)
                self._record_allowance(scope, response)
                raw_prices = response.get('prices')
                if isinstance(raw_prices, list) and len(raw_prices) > count:
                    raise IGError('ig.chart.batch.limit.exceeded')
                candles, skipped = normalize_prices(raw_prices)
            now = time.time()
            with self.lock, self.db:
                meta = self._meta(key)
                old_latest = self.db.execute('SELECT MAX(time) FROM chart_candles WHERE scope=? AND epic=? AND resolution=?', key).fetchone()[0]
                gaps = meta.get('gaps', [])
                # Never imply that a 200-point catch-up after sleep contains all
                # intervening history. This can also be a market closure, so it
                # is explicitly unverified rather than an invented missing bar.
                if before is None and old_latest is not None and candles and candles[0]['time'] > old_latest + RESOLUTIONS[resolution] * 1.5:
                    gap = {'from': old_latest, 'to': candles[0]['time'], 'status': 'unverified'}
                    if gap not in gaps:
                        gaps = (gaps + [gap])[-20:]
                for candle in candles:
                    self.db.execute('INSERT OR REPLACE INTO chart_candles VALUES (?,?,?,?,?)', (*key, candle['time'], json.dumps(candle, allow_nan=False)))
                self.db.execute('DELETE FROM chart_candles WHERE scope=? AND epic=? AND resolution=? AND time NOT IN (SELECT time FROM chart_candles WHERE scope=? AND epic=? AND resolution=? ORDER BY time DESC LIMIT ?)', (*key, *key, MAX_CANDLES))
                messages = []
                if older and older['message']:
                    messages.append(older['message'])
                if skipped:
                    messages.append(f'IG 返回的 {skipped} 条缺少有效 UTC/Bid OHLC，已跳过。')
                if gaps:
                    messages.append('缓存存在尚未核对的时间区间，可能包含休市或未补齐的历史；跨区间趋势请结合 IG 平台核对。')
                meta.update(last_success=now, updated_at=iso_now(now), error=None, retry_at=0, status='ready', skipped_rows=skipped,
                            message=''.join(messages), gaps=gaps)
                if before is not None:
                    request_id = cursor or ('before:'+str(before))
                    meta['completed_requests'] = (meta.get('completed_requests', [])+[request_id])[-64:]
                    meta['older_window'] = older['window']
                    meta['next_before'] = older['next_before']
                    meta['next_cursor'] = older['next_cursor']
                    meta['has_more'] = older['has_more']
                else:
                    meta.setdefault('has_more', True)
                    if not meta.get('older_window') and meta['has_more']:
                        first = self.db.execute('SELECT MIN(time) FROM chart_candles WHERE scope=? AND epic=? AND resolution=?', key).fetchone()[0]
                        meta['next_before'] = first if first is not None else int(now)
                        meta['next_cursor'] = None
                self._put_meta(key, meta)
        except (IGError, ChartError) as exc:
            self._failure(key, exc.code, before, cursor)
        except Exception:
            # Do not leak SDK errors, authentication headers or arbitrary response text.
            self._failure(key, 'local.chart.fetch.error')
        finally:
            with self.lock:
                self.pending.discard(key)
            self.jobs.task_done()
        return True

    def _failure(self, key, code, before=None, cursor=None):
        limited = 'allowance' in code
        historical_limit = 'historical-data-allowance' in code
        message = 'IG 历史行情配额暂不可用，已有缓存仍可查看。' if limited else 'IG 历史行情暂不可用，已有缓存仍可查看。'
        if code == 'error.unsupported.epic':
            message = 'IG API 不提供此产品的历史行情；实时行情与持仓可继续查看。'
        if code.startswith('ig.chart.query.timezone.'):
            message = 'IG 历史价格的本地时区偏移无法确认或跨时段变化，已暂停更早加载；UTC 历史缓存仍可查看。'
        with self.lock, self.db:
            meta = self._meta(key)
            delay = (3600 if historical_limit else 90) if limited else 86400 if code == 'error.unsupported.epic' else 120
            if code in ('ig.chart.upper.bound.ignored', 'ig.chart.range.bound.ignored', 'ig.chart.batch.limit.exceeded',
                        'ig.chart.paging.missing', 'ig.chart.paging.invalid', 'ig.chart.paging.changed', 'ig.chart.paging.loop', 'ig.chart.range.too.large',
                        'ig.chart.query.timezone.unavailable', 'ig.chart.query.timezone.ambiguous', 'ig.chart.query.timezone.changed'):
                delay = 86400
                if not code.startswith('ig.chart.query.timezone.'):
                    message = 'IG 未按预期返回此历史区间，已停止重复请求以保护行情配额。'
                if before is not None:
                    failures = meta.get('cursor_errors') or {}
                    failures[cursor or ('before:'+str(before))] = code
                    meta['cursor_errors'] = dict(list(failures.items())[-20:])
            meta.update(error=code, status='limited' if limited else 'error', message=message, retry_at=time.time()+delay)
            self._put_meta(key, meta)
            if limited and not code.startswith('local.chart.allowance.'):
                allowance = self._allowance(key[0]) or {}
                # REST traffic allowance resets separately from the historical
                # point allowance; a minute-rate error must not block a week.
                allowance['_blocked_until'] = max(time.time()+3600, allowance.get('_expires_at', 0)) if historical_limit else time.time()+90
                self.db.execute('INSERT OR REPLACE INTO chart_allowance VALUES (?,?)', (key[0], json.dumps(allowance)))

    def drawings(self, epic):
        scope, _ = self._context(epic)
        with self.lock:
            row = self.db.execute('SELECT revision,payload,updated_at FROM chart_drawings WHERE scope=? AND epic=?', (scope, epic)).fetchone()
        return {'epic': epic, 'revision': row[0] if row else 0,
                'drawings': json.loads(row[1]) if row else [], 'updated_at': row[2] if row else None}

    def save_drawings(self, epic, revision, drawings):
        scope, _ = self._context(epic)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ChartError('invalid_revision')
        drawings = validate_drawings(drawings)
        with self.lock, self.db:
            row = self.db.execute('SELECT revision FROM chart_drawings WHERE scope=? AND epic=?', (scope, epic)).fetchone()
            actual = row[0] if row else 0
            if revision != actual:
                return self.drawings(epic), False
            self.db.execute('INSERT OR REPLACE INTO chart_drawings VALUES (?,?,?,?,?)', (scope, epic, revision+1, json.dumps(drawings, allow_nan=False), iso_now()))
            return self.drawings(epic), True
