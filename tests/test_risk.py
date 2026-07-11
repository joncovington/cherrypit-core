"""Tests for cherrypit.risk.evaluate_deploy_limit — pure account-buying-power cap math, no broker."""

from decimal import Decimal

from cherrypit import risk


def _eval(used, available, consume, pct):
    return risk.evaluate_deploy_limit(Decimal(str(used)), Decimal(str(available)),
                                      Decimal(str(consume)), pct)


def test_allows_when_projected_stays_at_or_below_limit():
    # capacity 10000, 50% limit = 5000; used 2000 + consume 3000 = 5000 (exactly at the limit)
    allowed, info = _eval(2000, 8000, 3000, 50)
    assert allowed is True
    assert info["account_buying_power_capacity"] == "10000"
    assert info["account_deployed_after"] == "5000"
    assert info["account_deploy_limit"] == "5000"
    assert info["account_deploy_limit_pct"] == 50


def test_blocks_when_projected_exceeds_limit():
    # capacity 10000, 50% = 5000; used 2000 + consume 3001 = 5001 > 5000
    allowed, info = _eval(2000, 8000, 3001, 50)
    assert allowed is False
    assert info["account_deployed_after"] == "5001"


def test_credit_or_closing_order_floors_projected_at_zero():
    # a closing order returns buying power (negative consume); projected can't go below 0
    allowed, info = _eval(1000, 9000, -5000, 10)  # limit = 1000
    assert allowed is True
    assert info["account_deployed_after"] == "0"


def test_zero_percent_limit_blocks_any_new_deployment():
    allowed, _ = _eval(0, 10000, 1, 0)
    assert allowed is False


def test_current_deployment_at_limit_still_allows_a_closing_order():
    # fully deployed to the cap; a credit/closing order (negative consume) is still allowed
    allowed, info = _eval(5000, 5000, -100, 50)  # capacity 10000, limit 5000
    assert allowed is True
    assert info["account_deployed_after"] == "4900"


def test_fractional_limit_pct():
    # capacity 10000, 2.5% limit = 250
    allowed_ok, _ = _eval(0, 10000, 250, 2.5)
    allowed_no, _ = _eval(0, 10000, 251, 2.5)
    assert allowed_ok is True
    assert allowed_no is False
