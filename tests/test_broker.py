"""Tests for cherrypit.broker using a fake Account class + fake option objects.

No tastytrade, no network: the account class is injected and options are hand-built. Async is driven
via asyncio.run() so no pytest-asyncio plugin is required.
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cherrypit import broker


def _run(coro):
    return asyncio.run(coro)


class FakeAccount:
    def __init__(self, number, nickname=None, type_name=None):
        self.account_number = number
        self.nickname = nickname
        self.account_type_name = type_name


def make_account_cls(accounts, by_number=None):
    """Build a stand-in for tastytrade.account.Account: get(session)->list, get(session, n)->one."""
    by_number = by_number or {}
    calls = {"list": 0, "by_number": []}

    class _Acct:
        @classmethod
        async def get(cls, session, number=None):
            if number is not None:
                calls["by_number"].append(number)
                return by_number[number]
            calls["list"] += 1
            return list(accounts)

    return _Acct, calls


class FakeOption:
    def __init__(self, strike):
        self.strike_price = strike


# --------------------------------------------------------------------------- resolve_account
def test_resolve_account_explicit_number_takes_precedence():
    a1, a2 = FakeAccount("A1"), FakeAccount("A2")
    cls, calls = make_account_cls([a1], by_number={"A2": a2})
    got = _run(broker.resolve_account("sess", account_number="A2", default_number="A1",
                                      account_cls=cls))
    assert got is a2
    assert calls["by_number"] == ["A2"]  # explicit beats default; no list fetch
    assert calls["list"] == 0


def test_resolve_account_falls_back_to_default_number():
    a2 = FakeAccount("A2")
    cls, calls = make_account_cls([], by_number={"A2": a2})
    got = _run(broker.resolve_account("sess", default_number="A2", account_cls=cls))
    assert got is a2
    assert calls["by_number"] == ["A2"]


def test_resolve_account_uses_first_when_no_number():
    first, second = FakeAccount("A1"), FakeAccount("A2")
    cls, _ = make_account_cls([first, second])
    got = _run(broker.resolve_account("sess", account_cls=cls))
    assert got is first


def test_resolve_account_raises_when_no_accounts():
    cls, _ = make_account_cls([])
    with pytest.raises(RuntimeError, match="No accounts found"):
        _run(broker.resolve_account("sess", account_cls=cls))


# --------------------------------------------------------------------------- list_accounts / count
def test_list_accounts_serializes_fields():
    accounts = [FakeAccount("A1", nickname="main", type_name="Margin"), FakeAccount("A2")]
    cls, _ = make_account_cls(accounts)
    out = _run(broker.list_accounts("sess", account_cls=cls))
    assert out == [
        {"account_number": "A1", "nickname": "main", "account_type": "Margin"},
        {"account_number": "A2", "nickname": None, "account_type": None},
    ]


def test_account_count():
    cls, _ = make_account_cls([FakeAccount("A1"), FakeAccount("A2"), FakeAccount("A3")])
    assert _run(broker.account_count("sess", account_cls=cls)) == 3


# --------------------------------------------------------------------------- option-chain helpers
def test_strike_of_valid_and_invalid():
    assert broker.strike_of(FakeOption(430)) == 430.0
    assert broker.strike_of(FakeOption(None)) is None
    assert broker.strike_of(FakeOption("nope")) is None


def test_nearest_expiration_picks_closest_to_target():
    today = date.today()
    exps = [today + timedelta(days=d) for d in (1, 7, 30, 60)]
    assert broker.nearest_expiration(exps, target_days=0) == exps[0]
    assert broker.nearest_expiration(exps, target_days=28) == exps[2]


def test_atm_window_keeps_strikes_around_center():
    opts = [FakeOption(s) for s in (90, 95, 100, 105, 110)]
    kept = broker.atm_window(opts, strike_count=1, around_price=100)
    assert sorted(broker.strike_of(o) for o in kept) == [95.0, 100.0, 105.0]


def test_atm_window_defaults_to_median_strike():
    opts = [FakeOption(s) for s in (90, 95, 100, 105, 110)]
    kept = broker.atm_window(opts, strike_count=0)  # just the ATM (median) strike
    assert [broker.strike_of(o) for o in kept] == [100.0]


def test_atm_window_empty_options_passthrough():
    assert broker.atm_window([], strike_count=5) == []


# --------------------------------------------------------------------------- build_order
class _FakeActionEnum:
    def __getitem__(self, name):  # mimics OrderAction[name]
        return f"Action.{name}"


class FakeLeg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeNewOrder:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_order_ns():
    return SimpleNamespace(
        Leg=FakeLeg,
        NewOrder=FakeNewOrder,
        OrderAction=_FakeActionEnum(),
        OrderTimeInForce=lambda v: f"TIF:{v}",
        OrderType=lambda v: f"Type:{v}",
    )


def _leg(action="sell to open", qty=1):
    return {"instrument_type": "Equity Option", "symbol": "AAPL  240119C00190000",
            "action": action, "quantity": qty}


def test_build_order_defaults_and_leg_mapping():
    order = broker.build_order({"legs": [_leg("buy to open", 2)]}, order_ns=_fake_order_ns())
    assert order.time_in_force == "TIF:Day"      # default
    assert order.order_type == "Type:Limit"      # default
    assert len(order.legs) == 1
    leg = order.legs[0]
    assert leg.action == "Action.BUY_TO_OPEN"
    assert leg.quantity == Decimal("2")          # coerced to Decimal
    assert "price" not in order.__dict__         # no price key when unset


def test_build_order_credit_price_is_negative():
    order = broker.build_order(
        {"legs": [_leg()], "price": 1.50, "price_effect": "Credit"}, order_ns=_fake_order_ns())
    assert order.price == Decimal("-1.5")


def test_build_order_debit_price_is_positive():
    order = broker.build_order(
        {"legs": [_leg()], "price": 1.50, "price_effect": "Debit"}, order_ns=_fake_order_ns())
    assert order.price == Decimal("1.5")


def test_build_order_price_without_effect_kept_as_is():
    order = broker.build_order({"legs": [_leg()], "price": "2.25"}, order_ns=_fake_order_ns())
    assert order.price == Decimal("2.25")


def test_build_order_maps_all_actions():
    for spec_str, enum_name in broker.ACTION_MAP.items():
        order = broker.build_order({"legs": [_leg(spec_str)]}, order_ns=_fake_order_ns())
        assert order.legs[0].action == f"Action.{enum_name}"


def test_build_order_stop_trigger_passed_through_when_present():
    order = broker.build_order(
        {"legs": [_leg()], "stop_trigger": "3.10"}, order_ns=_fake_order_ns())
    assert order.stop_trigger == Decimal("3.10")
    # ...and omitted entirely when absent (EarningsAgent never sets it)
    order2 = broker.build_order({"legs": [_leg()]}, order_ns=_fake_order_ns())
    assert "stop_trigger" not in order2.__dict__


# --------------------------------------------------------------------------- place_order
class FakeBPE:
    def __init__(self, current, new, change):
        self.current_buying_power = current
        self.new_buying_power = new
        self.change_in_buying_power = change


class FakePreflight:
    def __init__(self, errors=(), warnings=(), bpe=None, tag="preflight"):
        self.errors = list(errors)
        self.warnings = list(warnings)
        self.buying_power_effect = bpe
        self.tag = tag


class FakeBalances:
    def __init__(self, used, available):
        self.used_derivative_buying_power = used
        self.derivative_buying_power = available


class FakeSubmitAccount:
    """Records every place_order call so tests can assert whether a live submit happened."""
    def __init__(self, preflight, live_response=None, account_number="A1", balances=None,
                 balances_raise=False):
        self.account_number = account_number
        self._preflight = preflight
        self._live_response = live_response or FakePreflight(tag="live")
        self._balances = balances
        self._balances_raise = balances_raise
        self.calls = []  # list of dry_run flags, in order

    async def place_order(self, session, order, dry_run):
        self.calls.append(dry_run)
        return self._preflight if dry_run else self._live_response

    async def get_balances(self, session):
        if self._balances_raise:
            raise RuntimeError("balances endpoint down")
        return self._balances


def test_place_order_dry_run_never_submits_live():
    acct = FakeSubmitAccount(FakePreflight(bpe=FakeBPE("1000", "800", "-200")))
    out = _run(broker.place_order(acct, "sess", "order", live=False, serialize=lambda p: p.tag))
    assert out["ok"] is True and out["dry_run"] is True
    assert out["account_number"] == "A1"
    assert out["response"] == "preflight"
    assert out["buying_power"]["change_in_buying_power"] == "-200"
    # safety: only the dry-run preflight call, never a live submit
    assert acct.calls == [True]


def test_place_order_preflight_errors_block_submission():
    acct = FakeSubmitAccount(FakePreflight(errors=["insufficient buying power"]))
    out = _run(broker.place_order(acct, "sess", "order", live=True, serialize=lambda p: p.tag))
    assert out["ok"] is False
    assert out["error"] == "pre-flight validation failed"
    assert out["problems"] == ["insufficient buying power"]
    # safety: even with live=True, an errored preflight must NOT place a live order
    assert acct.calls == [True]


def test_place_order_live_submits_after_clean_preflight():
    acct = FakeSubmitAccount(FakePreflight(warnings=["near the close"]),
                             live_response=FakePreflight(tag="live"))
    out = _run(broker.place_order(acct, "sess", "order", live=True, serialize=lambda p: p.tag))
    assert out["ok"] is True and out["dry_run"] is False
    assert out["response"] == "live"
    assert out["buying_power"]["warnings"] == ["near the close"]
    # dry-run preflight first, then the live submit
    assert acct.calls == [True, False]


def test_place_order_default_serialize_is_identity():
    pf = FakePreflight()
    acct = FakeSubmitAccount(pf)
    out = _run(broker.place_order(acct, "sess", "order", live=False))
    assert out["response"] is pf  # raw object passed through when no serialize given


# --------------------------------------------------------------------------- place_order + governor
def _acct(change, used, available, **kw):
    # preflight whose order consumes -`change` BP, against an account with `used`/`available` BP
    pf = FakePreflight(bpe=FakeBPE("x", "y", change))
    return FakeSubmitAccount(pf, balances=FakeBalances(used, available), **kw)


def test_governor_off_by_default_never_fetches_balances():
    acct = _acct(change="-1000", used="9999999", available="0")  # would blow any cap if consulted
    out = _run(broker.place_order(acct, "sess", "order", live=True))  # no deploy_limit_pct
    assert out["ok"] is True and out["dry_run"] is False
    assert "governor" not in out
    assert acct.calls == [True, False]  # live submit happened; governor never ran


def test_governor_allows_within_cap_and_submits_live():
    # capacity 10000, 50% cap = 5000; used 1000 + consume 1000 = 2000 <= 5000
    acct = _acct(change="-1000", used="1000", available="9000")
    out = _run(broker.place_order(acct, "sess", "order", live=True, deploy_limit_pct=50))
    assert out["ok"] is True and out["dry_run"] is False
    assert out["governor"]["deploy_governor"] == "enforced"
    assert out["governor"]["account_deployed_after"] == "2000"
    assert acct.calls == [True, False]


def test_governor_blocks_over_cap_and_never_submits_live():
    # capacity 10000, 50% cap = 5000; used 4800 + consume 500 = 5300 > 5000
    acct = _acct(change="-500", used="4800", available="5200")
    out = _run(broker.place_order(acct, "sess", "order", live=True, deploy_limit_pct=50))
    assert out["ok"] is False
    assert out["error"] == "account deploy limit exceeded"
    assert out["governor"]["deploy_governor"] == "enforced"
    # safety: blocked before the live submit
    assert acct.calls == [True]


def test_governor_fail_closed_when_change_unknown():
    # preflight has no buying_power_effect -> change unverifiable -> block on live
    acct = FakeSubmitAccount(FakePreflight(bpe=None), balances=FakeBalances("0", "10000"))
    out = _run(broker.place_order(acct, "sess", "order", live=True, deploy_limit_pct=50))
    assert out["ok"] is False
    assert out["error"] == "account deploy limit: could not verify account state"
    assert out["governor"]["deploy_governor"] == "unverified"
    assert acct.calls == [True]


def test_governor_fail_closed_when_balances_unavailable():
    acct = FakeSubmitAccount(FakePreflight(bpe=FakeBPE("x", "y", "-100")), balances_raise=True)
    out = _run(broker.place_order(acct, "sess", "order", live=True, deploy_limit_pct=50))
    assert out["ok"] is False
    assert out["error"] == "account deploy limit: could not verify account state"
    assert acct.calls == [True]


def test_governor_on_dry_run_reports_but_does_not_block():
    # over-cap order, but live=False -> informational governor, no block, no live submit
    acct = _acct(change="-9000", used="0", available="10000")  # consume 9000 > 5000 cap
    out = _run(broker.place_order(acct, "sess", "order", live=False, deploy_limit_pct=50))
    assert out["ok"] is True and out["dry_run"] is True
    assert out["governor"]["deploy_governor"] == "enforced"
    assert out["governor"]["account_deployed_after"] == "9000"
    assert acct.calls == [True]  # dry run only


def test_governor_get_balances_override_is_used():
    calls = {"n": 0}

    async def fake_get_balances(account, session):
        calls["n"] += 1
        return FakeBalances("1000", "9000")

    acct = FakeSubmitAccount(FakePreflight(bpe=FakeBPE("x", "y", "-1000")))  # no balances on account
    out = _run(broker.place_order(acct, "sess", "order", live=True, deploy_limit_pct=50,
                                  get_balances=fake_get_balances))
    assert out["ok"] is True and calls["n"] == 1
