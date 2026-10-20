"""Narrow, source-backed regular-session references; not an IG holiday calendar.

Only exact held EPICs are mapped. Oil's daily pause is an inference corroborated
by this contract's October 2026 Bid history, not an openingHours API response.
All interval comparisons use UTC epochs; ZoneInfo applies US DST per date.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
HK = ZoneInfo('Asia/Hong_Kong')
CHECKED = '2026-10-21'
REFERENCES = {
    'CC.D.CL.BMU.IP': {
        'daily_break': True,
        'name': '美国原油 $1 · 常规参考时段',
        'basis': 'IG 油品说明、标的交易时段及本合约近期 Bid K 线交叉核对；每日暂停为据此推定，API 未提供完整开市日历。',
        'sources': [
            {'title': 'IG 油品交易时段', 'url': 'https://www.ig.com/en/trading-strategies/oil-trading-hours--when-to-trade-crude-oil-200414'},
            {'title': 'CME WTI 时段参考', 'url': 'https://www.cmegroup.com/articles/faqs/faq-tuesday-and-thursday-weekly-wti-options.html'},
        ],
    },
    'IX.D.NIKKEI.BMU.IP': {
        'daily_break': False,
        'name': '日本 225 现货 $1 · 常规参考时段',
        'basis': '按 IG 24 小时指数的周开闭市及夏令时说明，与本合约近期 Bid K 线交叉核对；不套用东京证券交易所午休。',
        'sources': [
            {'title': 'IG 指数延长交易时段', 'url': 'https://www.ig.com/ae/out-of-hours-trading/how-to-trade-pre-post-market'},
        ],
    },
}
DST_SOURCE = {'title': 'IG 季节性时段调整', 'url': 'https://www.ig.com/en/help-and-support/articles/692206-trading-hours-updates'}


def open_windows(epic, start, end):
    """Reference open intervals clipped to [start, end); None means unknown."""
    rule = REFERENCES.get(epic)
    if not rule or end < start or end - start > 370 * 86400:
        return None
    day = datetime.fromtimestamp(start, NY).date()
    last = datetime.fromtimestamp(end, NY).date()
    result = []
    while day <= last:
        wd = day.weekday()
        hours = ([(18, 24)] if wd == 6 else [] if wd == 5 else
                 [(0, 17)] if wd == 4 else
                 [(0, 17), (18, 24)] if rule['daily_break'] else [(0, 24)])
        for left, right in hours:
            a = (datetime.combine(day, time(), NY) + timedelta(hours=left)).timestamp()
            b = (datetime.combine(day, time(), NY) + timedelta(hours=right)).timestamp()
            if max(a, start) < min(b, end):
                result.append((max(a, start), min(b, end)))
        day += timedelta(days=1)
    return result


def open_seconds(epic, start, end):
    windows = open_windows(epic, start, end)
    return None if windows is None else sum(b - a for a, b in windows)


def describe(epic, epoch):
    rule = REFERENCES.get(epic)
    if not rule:
        return {'status': 'UNVERIFIED', 'name': '此合约时段待核验',
                'note': '没有匹配的 EPIC 时段规则；不会套用其他产品的休市时间。'}
    now = datetime.fromtimestamp(epoch, NY)
    summer = bool(now.dst())
    windows = open_windows(epic, epoch, epoch + 8 * 86400)
    is_open = bool(windows and windows[0][0] == epoch)
    next_at = windows[0][1 if is_open else 0] if windows else None
    # Merge midnight-adjacent intervals for a true next close.
    if is_open:
        for a, b in windows[1:]:
            if a != next_at:
                break
            next_at = b
    close_hk, open_hk = ('05:00', '06:00') if summer else ('06:00', '07:00')
    return {
        'status': 'REGULAR_REFERENCE', 'name': rule['name'], 'checked_at': CHECKED,
        'timezone': NY.key, 'timezone_abbr': now.tzname(), 'dst_active': summer,
        'reference_open': is_open, 'next_transition': next_at,
        'next_transition_kind': 'close' if is_open else 'open',
        'daily_break_hkt': f'周二至周五 {close_hk}–{open_hk}' if rule['daily_break'] else '不设固定每日休市',
        'week_hkt': f'周一 {open_hk} 至周六 {close_hk}',
        'schedule': '纽约时间：周日 18:00 至周五 17:00；' +
                    ('周一至周四 17:00–18:00 暂停。' if rule['daily_break'] else '工作日连续报价参考。'),
        'basis': rule['basis'],
        'note': '按每个日期自动换算美东夏令时；香港时间不变更时钟。仅识别常规时段，节假日、提前收市和临时停牌未自动豁免；以 IG 实际状态为准。',
        'sources': rule['sources'] + [DST_SOURCE],
    }


def gap_details(epic, bars, resolution, step):
    """Do not exempt an entire gap just because part of it is a closure."""
    details = []
    recent = bars[-61:]
    for a, b in zip(recent, recent[1:]):
        start, end = a['time'], b['time']
        if resolution == 'MONTH':
            da, db = datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc)
            missing = (db.year - da.year) * 12 + db.month - da.month - 1
            if missing <= 0:
                continue
            details.append({'start': start, 'end': end, 'kind': 'unexplained', 'missing_bars': missing})
            continue
        if end - start <= step * 1.5:
            continue
        missing_start = start + step
        seconds = open_seconds(epic, missing_start, end) if step < 86400 else None
        # Coarse daily bars are dated buckets, not full-day session intervals.
        # Only missing UTC weekend dates are exempt; weekday holidays stay unknown.
        if resolution == 'DAY' and epic in REFERENCES:
            day = datetime.fromtimestamp(missing_start, timezone.utc).date()
            last = datetime.fromtimestamp(end, timezone.utc).date()
            days = []
            while day < last and len(days) < 370:
                days.append(day.weekday())
                day += timedelta(days=1)
            if days and all(d >= 5 for d in days):
                seconds = 0
        closed = seconds == 0
        details.append({
            'start': missing_start, 'end': end,
            'kind': 'session' if closed else 'unexplained',
            'missing_bars': max(1, round((end - start) / step) - 1),
            'open_seconds': seconds,
            'explanation': ('完整缺口落在常规参考休市时段' if closed else
                            '缺口包含参考开市时段，或该合约时段尚未核验'),
        })
    return details
