"""Tests for cherrypit.broker using a fake Account class + fake option objects.

No tastytrade, no network: the account class is injected and options are hand-built. Async is driven
via asyncio.run() so no pytest-asyncio plugin is required.
"""

import asyncio
from datetime import date, timedelta

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
