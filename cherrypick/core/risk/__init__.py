"""cherrypick.core.risk — account-level risk primitives (fail-closed, opt-in).

Seeded from tastytrade-mcp's `risk.py`. These are pure functions over *live account state* that a
consumer can consult before placing an order; the core never calls the broker itself — the caller
passes the account figures in (invariant: core never reaches into a consumer).

Currently one primitive: `evaluate_deploy_limit`, an account-global ceiling on how much buying power
may be deployed at once. It's measured from live account state (used + available buying power) rather
than an in-memory counter, so it stays correct across restarts and multiple processes and reflects
reality, not just orders this process placed.

Strategy-specific position sizing (e.g. EarningsAgent's per-contract-max-loss / risk-budget sizing)
is deliberately NOT here — it's single-consumer and written in one module's strategy vocabulary; it
belongs with the risk-profiling work (`profiles/`) if/when a second consumer needs it.
"""

from __future__ import annotations

from decimal import Decimal


def evaluate_deploy_limit(
    used_bp: Decimal,
    available_bp: Decimal,
    consume: Decimal,
    limit_pct: float,
) -> tuple[bool, dict[str, object]]:
    """Check whether deploying ``consume`` more buying power stays within the account cap.

    Args:
        used_bp: Buying power already deployed by live positions.
        available_bp: Buying power currently available.
        consume: Buying power this order would consume (positive for a debit, negative for a
            credit/closing order).
        limit_pct: Maximum percent of total capacity that may be deployed.

    Total capacity = ``used_bp + available_bp``; the limit is ``limit_pct`` percent of that. The order
    is allowed only if the resulting deployed buying power stays at or below the limit (a
    credit/closing order that would drive projected deployment below zero is floored at zero).

    Returns ``(allowed, info)`` where ``info`` reports the capacity, current/projected deployment, and
    the limit (all as strings) for logging.
    """
    capacity = used_bp + available_bp
    limit = capacity * Decimal(str(limit_pct)) / Decimal(100)
    projected = used_bp + consume
    if projected < 0:
        projected = Decimal(0)
    allowed = projected <= limit
    info: dict[str, object] = {
        "account_buying_power_capacity": str(capacity),
        "account_deployed_current": str(used_bp),
        "account_deployed_after": str(projected),
        "account_deploy_limit": str(limit),
        "account_deploy_limit_pct": limit_pct,
    }
    return allowed, info
