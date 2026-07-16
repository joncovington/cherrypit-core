"""cherrypick.core.fees — tastytrade cost model (one home for the fee schedule).

Two related pieces the suite previously kept in two places:

1. **Cost-adjusted paper fills** (originally from EarningsAgent's `costs.py`): tastytrade's open-only
   commission ($1/contract open, $0 close, $10/leg cap) + clearing/regulatory pass-throughs + a slippage
   haircut off each leg's bid-ask width (recalibrated 2026-07-16 to 12.5% of spread, capped at 15% of
   leg mid so deep-OTM "junk" wings don't dominate -- see `_slippage`). Used to keep paper P&L honest.

2. **The IC open-fee schedule** (behind MEICAgent's hardcoded `fee_estimate_fallback_per_contract`
   constants): the same tastytrade schedule plus the per-symbol *broad-based index exchange fee* that
   makes SPX materially pricier per IC than XSP. `ic_open_fee` computes those constants from the
   schedule (SPX→6.89, XSP/DEFAULT→4.49, NDX→5.49, RUT→5.21) instead of hand-maintaining them.

Source: tastytrade.com/pricing + the Commissions & Fees doc (rates change — re-check and update here).
Pure functions; no broker, no I/O.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- 1. cost-adjusted fills
DEFAULT_COSTS = {
    "commission_open_per_contract": 1.00,
    "commission_close_per_contract": 0.00,
    "commission_cap_per_leg": 10.00,
    "clearing_fee_per_contract": 0.10,
    "regulatory_fee_per_contract": 0.04,
    # Slippage: concede this fraction of each leg's bid-ask from mid, per fill. 0.125 = a quarter of
    # the way from mid to the far touch, a realistic worked-combo-limit fill (recalibrated 2026-07-16
    # from 0.25, a market-order assumption that made slippage ~98% of earnings paper cost).
    "slippage_frac_of_spread": 0.125,
    # Guardrail: never charge a leg more slippage than this fraction of its mid. A bid>=0 quote always
    # has spread <= 2*mid, so at frac 0.125 this binds only when spread > 1.2*mid -- i.e. deep-OTM wings
    # quoted wide relative to their value, which would otherwise contribute outsized slippage.
    "slippage_cap_frac_of_mid": 0.15,
}


def _costs_config(config: dict) -> dict:
    return {**DEFAULT_COSTS, **config.get("tastytrade_costs", {})}


def _leg_count(order: dict) -> int:
    legs = order.get("order", {}).get("legs", [])
    return len(legs)


def _commission(num_legs: int, quantity: int, per_contract: float, cap_per_leg: float) -> float:
    """Open-only model: min(quantity * per_contract, cap) per leg, summed. Passing
    commission_close_per_contract (0.00 by default) yields $0 to close with no special-casing."""
    return num_legs * min(quantity * per_contract, cap_per_leg)


def _pass_through(num_legs: int, quantity: int, clearing: float, regulatory: float) -> float:
    return num_legs * quantity * (clearing + regulatory)


def _slippage(leg_quotes: list[dict], quantity: int, frac_of_spread: float,
              cap_frac_of_mid: float | None = None) -> float:
    """Per-leg slippage = frac_of_spread of that leg's bid-ask width, optionally capped at
    cap_frac_of_mid of the leg's mid; summed across legs, x100 x quantity.

    Summing per-leg spreads is deliberate and correct: a multi-leg combo's net bid-ask exactly equals
    the sum of its legs' spreads (the mids net out, the spreads add), so this is identical to
    fractioning the net combo spread -- there is nothing to de-duplicate. The cap is a realism
    guardrail for deep-OTM wings whose spread is large relative to their value."""
    total = 0.0
    for q in leg_quotes:
        bid = q.get("bid", 0.0)
        ask = q.get("ask", 0.0)
        slip = max(ask - bid, 0.0) * frac_of_spread
        if cap_frac_of_mid is not None:
            slip = min(slip, cap_frac_of_mid * max((bid + ask) / 2.0, 0.0))
        total += slip
    return total * 100 * quantity


def _apply_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict,
                 commission_key: str) -> dict:
    costs_cfg = _costs_config(config)
    num_legs = _leg_count(order)
    commission = _commission(num_legs, quantity, costs_cfg[commission_key],
                             costs_cfg["commission_cap_per_leg"])
    pass_through = _pass_through(num_legs, quantity, costs_cfg["clearing_fee_per_contract"],
                                 costs_cfg["regulatory_fee_per_contract"])
    slippage = _slippage(leg_quotes, quantity, costs_cfg["slippage_frac_of_spread"],
                         costs_cfg.get("slippage_cap_frac_of_mid"))
    total = commission + pass_through + slippage
    return {
        "commission": round(commission, 2),
        "pass_through_fees": round(pass_through, 2),
        "slippage": round(slippage, 2),
        "total_cost": round(total, 2),
    }


def apply_entry_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of opening `order` at `quantity` contracts, given `leg_quotes` (one {"bid","ask"} per leg,
    in order["order"]["legs"] order). Returns commission / pass_through_fees / slippage / total_cost."""
    return _apply_costs(order, leg_quotes, quantity, config, "commission_open_per_contract")


def apply_exit_costs(order: dict, leg_quotes: list[dict], quantity: int, config: dict) -> dict:
    """Cost of closing `order`. Same shape; commission uses commission_close_per_contract (0.00 by
    tastytrade's open-only default, but computed rather than hardcoded so a charge-to-close schedule
    would work)."""
    return _apply_costs(order, leg_quotes, quantity, config, "commission_close_per_contract")


# --------------------------------------------------------------------------- 2. IC open-fee schedule
COMMISSION_OPEN_PER_CONTRACT = 1.00   # tastytrade: $1/contract to open
CLEARING_FEE_PER_CONTRACT = 0.10
ORF_PER_CONTRACT = 0.02               # Options Regulatory Fee
TAF_PER_SELL_CONTRACT = 0.00329       # FINRA Trading Activity Fee — sell legs only

# Single-Listed Exchange Proprietary Index Options fee per contract (broad-based index options).
# XSP is $0.00 under 10 contracts/leg. Symbols not listed use 0.00 (plain equity/ETF options schedule).
INDEX_EXCHANGE_FEE_PER_CONTRACT = {"SPX": 0.60, "XSP": 0.00, "NDX": 0.25, "RUT": 0.18}


def _ic_fee(symbol: str, quantity: int, legs: int, sell_legs: int, *,
            commission_per_contract: float, ndigits: int) -> float:
    """Shared IC fee stack: (commission + clearing + ORF + per-symbol index exchange fee) per leg
    per contract, plus FINRA TAF on the sell legs. `commission_per_contract` is the only difference
    between opening ($1) and closing/expiring ($0). `ndigits` sets the rounding precision."""
    exch = INDEX_EXCHANGE_FEE_PER_CONTRACT.get(symbol.upper(), 0.0)
    per_contract = commission_per_contract + CLEARING_FEE_PER_CONTRACT + ORF_PER_CONTRACT + exch
    fee = legs * quantity * per_contract + sell_legs * quantity * TAF_PER_SELL_CONTRACT
    return round(fee, ndigits)


def ic_open_fee(symbol: str, quantity: int = 1, legs: int = 4, sell_legs: int = 2, *,
                ndigits: int = 2) -> float:
    """Open-only fee for one iron condor (4 legs; 2 sells) at `quantity` contracts, per tastytrade's
    schedule including the per-symbol index exchange fee. Reproduces MEICAgent's
    `fee_estimate_fallback_per_contract` constants (SPX 6.89, XSP 4.49, NDX 5.49, RUT 5.21, else 4.49).
    `ndigits` chooses display precision: 2 (dollars-and-cents) by default; a caller wanting exact
    sub-cent parity (e.g. MEIC's paper engine) passes 4."""
    return _ic_fee(symbol, quantity, legs, sell_legs,
                   commission_per_contract=COMMISSION_OPEN_PER_CONTRACT, ndigits=ndigits)


def ic_close_fee(symbol: str, quantity: int = 1, legs: int = 4, sell_legs: int = 2, *,
                 ndigits: int = 2) -> float:
    """Fee to actively close IC legs — the same schedule MINUS the open-only $1/contract commission
    (clearing + ORF + per-symbol index exchange fee per leg, plus FINRA TAF on the sell legs).
    `legs`/`sell_legs` let a one-side close (2 legs, 1 sell) fee correctly vs a full 4-leg close
    (4 legs, 2 sells). `ndigits` as in `ic_open_fee`."""
    return _ic_fee(symbol, quantity, legs, sell_legs,
                   commission_per_contract=0.0, ndigits=ndigits)


def ic_expire_fee() -> float:
    """Expired-OTM options incur no closing transaction, so no fee."""
    return 0.0


def ic_open_fee_table(symbols=("SPX", "XSP", "NDX", "RUT")) -> dict:
    """{symbol: ic_open_fee(symbol)} plus a DEFAULT (equity/ETF, no index exchange fee)."""
    table = {s: ic_open_fee(s) for s in symbols}
    table["DEFAULT"] = ic_open_fee("__default__")  # unknown symbol -> 0.0 exchange fee
    return table
