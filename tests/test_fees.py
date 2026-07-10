"""Tests for cherrypit.fees.

Part 1 pins the cost-model outputs (extracted verbatim from EarningsAgent's costs.py). Part 2 asserts
the IC open-fee schedule reproduces MEICAgent's hardcoded fee_estimate_fallback_per_contract constants.
"""

import pytest

from cherrypit import fees

# A 2-leg spread, 3 contracts; leg spreads 0.10 and 0.06.
ORDER_2LEG = {"order": {"legs": [{}, {}]}}
LEG_QUOTES = [{"bid": 1.00, "ask": 1.10}, {"bid": 0.50, "ask": 0.56}]


def test_entry_costs_open_commission_pass_through_slippage():
    out = fees.apply_entry_costs(ORDER_2LEG, LEG_QUOTES, quantity=3, config={})
    # commission: 2 legs * min(3*1.00, 10) = 6.00 ; pass-through: 2*3*(0.10+0.04)=0.84
    # slippage: (0.10+0.06)*0.25*100*3 = 12.00
    assert out == {"commission": 6.00, "pass_through_fees": 0.84, "slippage": 12.00, "total_cost": 18.84}


def test_exit_costs_are_open_only_zero_commission():
    out = fees.apply_exit_costs(ORDER_2LEG, LEG_QUOTES, quantity=3, config={})
    assert out == {"commission": 0.00, "pass_through_fees": 0.84, "slippage": 12.00, "total_cost": 12.84}


def test_commission_cap_per_leg():
    # 15 contracts * $1 = $15/leg, capped at $10/leg -> 2 legs = $20.
    out = fees.apply_entry_costs(ORDER_2LEG, [{"bid": 0, "ask": 0}, {"bid": 0, "ask": 0}],
                                 quantity=15, config={})
    assert out["commission"] == 20.00


def test_config_overrides_default_costs():
    out = fees.apply_entry_costs(ORDER_2LEG, LEG_QUOTES, quantity=3,
                                 config={"tastytrade_costs": {"slippage_frac_of_spread": 0.5}})
    assert out["slippage"] == 24.00  # doubled from 12.00


def test_negative_spread_clamped_to_zero_slippage():
    crossed = [{"bid": 1.10, "ask": 1.00}, {"bid": 0.56, "ask": 0.50}]  # ask < bid
    out = fees.apply_entry_costs(ORDER_2LEG, crossed, quantity=1, config={})
    assert out["slippage"] == 0.00


# --- Part 2: IC open-fee schedule reproduces MEIC's constants -----------------------------------
@pytest.mark.parametrize("symbol,expected", [
    ("SPX", 6.89), ("XSP", 4.49), ("NDX", 5.49), ("RUT", 5.21), ("AAPL", 4.49),  # AAPL -> DEFAULT
])
def test_ic_open_fee_matches_meic_constants(symbol, expected):
    assert fees.ic_open_fee(symbol) == expected


def test_ic_open_fee_table_matches_meic_fallback_dict():
    assert fees.ic_open_fee_table() == {
        "SPX": 6.89, "XSP": 4.49, "NDX": 5.49, "RUT": 5.21, "DEFAULT": 4.49,
    }


def test_ic_open_fee_scales_with_quantity():
    # Quantity 2 SPX IC: 4 legs * 2 * 1.72 + 2 sells * 2 * 0.00329 = 13.76 + 0.01316 -> 13.77.
    assert fees.ic_open_fee("SPX", quantity=2) == 13.77
