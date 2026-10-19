"""User-selected gross-deposit capital retention policy; never broker actions."""
from decimal import Decimal, InvalidOperation


def evaluate_loss(equity, total_deposits=30000, loss_fraction='0.60',
                  age_seconds=0, max_age_seconds=30):
    """Loss = max(0, 1 - equity / cumulative gross deposits).

    Withdrawals reduce equity and count against this user-selected budget.
    This is capital retention, not trading performance.
    """
    try:
        eq, deposits, fraction, age, max_age = [Decimal(str(x)) for x in
            (equity, total_deposits, loss_fraction, age_seconds, max_age_seconds)]
    except (InvalidOperation, ValueError, TypeError):
        return {'status': 'UNKNOWN', 'reason': 'missing_or_invalid_input'}
    if not all(x.is_finite() for x in (eq, deposits, fraction, age, max_age)):
        return {'status': 'UNKNOWN', 'reason': 'non_finite_input'}
    if deposits <= 0 or not 0 < fraction <= 1 or max_age < 0:
        return {'status': 'UNKNOWN', 'reason': 'invalid_policy'}
    if age < 0 or age > max_age:
        return {'status': 'STALE', 'reason': 'timestamp_not_current'}
    retention = eq / deposits
    loss = max(Decimal(0), deposits - eq)
    threshold = deposits * (1 - fraction)
    return {'status': 'TRIGGERED' if eq <= threshold else 'BELOW_THRESHOLD',
            'equity': float(eq), 'total_deposits': float(deposits),
            'equity_ratio': float(retention), 'loss_fraction': float(loss / deposits),
            'capital_shortfall': float(loss), 'loss_budget': float(deposits * fraction),
            'threshold_equity': float(threshold), 'distance_to_threshold': float(eq - threshold),
            'basis': 'equity_divided_by_cumulative_gross_deposits',
            'withdrawals_reduce_ratio': True, 'automatic_execution': False}
