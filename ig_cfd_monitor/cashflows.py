"""Explicit IG cash ledger normalization. No exchange rates are inferred."""
import re
import math
from decimal import Decimal, InvalidOperation

ALIASES = {'HK': 'HKD', 'HKD': 'HKD', 'HK$': 'HKD', 'USD': 'USD', 'US$': 'USD',
           'GBP': 'GBP', 'EUR': 'EUR', 'JPY': 'JPY', 'SGD': 'SGD', 'AUD': 'AUD'}
PREFIXES = {'HKD': ('HKD', 'HK$', 'HK'), 'USD': ('USD', 'US$', '$'),
            'GBP': ('GBP', '£'), 'EUR': ('EUR', '€'), 'JPY': ('JPY', '¥'),
            'SGD': ('SGD', 'S$'), 'AUD': ('AUD', 'A$')}


def currency(value):
    return ALIASES.get(str(value or '').strip().upper())


def money(value, code):
    """Require an explicit supported currency and unambiguous grouping/sign."""
    code = currency(code)
    if code is None or value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1].strip()
    for prefix in PREFIXES[code]:
        if text.upper().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', text):
        return None
    try:
        result = Decimal(text.replace(',', ''))
        if negative:
            if result < 0 or text.startswith('+'):
                return None
            result = -result
        value=float(result) if result.is_finite() else None
        return value if value is not None and math.isfinite(value) else None
    except InvalidOperation:
        return None


def classify(row):
    """WITH includes interest/adjustments. Do not treat every WITH as withdrawal."""
    kind = str(row.get('transactionType') or '').upper()
    description = ' '.join(str(row.get(k) or '') for k in ('instrumentName', 'description')).lower()
    if kind in ('DEPO', 'DEPOSIT'):
        return 'deposit'
    if re.search(r'\b(?:long|short|overnight) interest\b|\bfunding charge\b|隔夜|利息', description):
        return 'financing'
    if re.search(r'\bcommission\b|佣金', description):
        return 'commission'
    if re.search(r'\bdividend\b|股息|股利', description):
        return 'dividend'
    if re.search(r'\brollover\b|\broll adjustment\b|展期', description):
        return 'roll_adjustment'
    if kind in ('WITH', 'WITHDRAWAL') and re.search(r'\bwithdrawal\b|提款|提取资金|取款', description):
        return 'withdrawal'
    if kind in ('DEAL', 'TRADE'):
        return 'realized_pnl'
    return 'unclassified'


def ledger_summary(events):
    totals = {}; counts = {}; unresolved = 0
    for event in events:
        row = event.get('raw', event)
        category = classify(row); code = currency(row.get('currency'))
        value = money(row.get('profitAndLoss'), row.get('currency'))
        counts[category] = counts.get(category, 0) + 1
        if code != 'HKD' or value is None:
            unresolved += 1
            continue
        totals[category] = totals.get(category, 0) + value
    return {'hkd': totals, 'counts': counts, 'unresolved_amounts': unresolved,
            'basis': 'posted_IG_ledger_not_future_fee_quote'}
