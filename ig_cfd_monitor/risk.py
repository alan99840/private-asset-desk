"""Account-level loss calculation. This module never executes broker actions."""
from decimal import Decimal, InvalidOperation


def evaluate_loss(equity, net_external_flows=0, initial_capital=30000,
                  loss_fraction="0.60", age_seconds=0, max_age_seconds=30):
    """Net external flows EXCLUDE the initial funding and include withdrawals < 0.

    Keep the original HKD risk budget fixed: deposits never erase a loss and
    withdrawals alone never count as trading losses. Unknown/stale inputs block
    a fresh evaluation; they never imply the account is safe.
    """
    try:
        values = [Decimal(str(x)) for x in
                  (equity, net_external_flows, initial_capital, loss_fraction,
                   age_seconds, max_age_seconds)]
    except (InvalidOperation, ValueError, TypeError):
        return {"status": "UNKNOWN", "reason": "missing_or_invalid_input"}
    if not all(x.is_finite() for x in values):
        return {"status": "UNKNOWN", "reason": "non_finite_input"}
    eq, flows, base, fraction, age, max_age = values
    if base <= 0 or not 0 < fraction <= 1 or max_age < 0:
        return {"status": "UNKNOWN", "reason": "invalid_policy"}
    if age < 0 or age > max_age:
        return {"status": "STALE", "reason": "timestamp_not_current"}
    adjusted = eq - flows
    pnl = adjusted - base
    loss = max(Decimal(0), -pnl)
    threshold = base * (1 - fraction) + flows
    return {
        "status": "TRIGGERED" if loss >= base * fraction else "BELOW_THRESHOLD",
        "equity": float(eq), "adjusted_equity": float(adjusted),
        "net_external_flows": float(flows), "economic_pnl": float(pnl),
        "loss_fraction": float(loss / base), "loss_budget": float(base * fraction),
        "threshold_equity": float(threshold),
        "distance_to_threshold": float(eq - threshold),
        "basis": "fixed_initial_capital_after_external_flow_adjustment",
        "automatic_execution": False,
    }
