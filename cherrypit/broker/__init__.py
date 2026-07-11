"""cherrypit.broker — shared tastytrade broker primitives (account resolution + option-chain helpers).

Unifies the read-side broker surface that MEICAgent's and EarningsAgent's `tt.py` share: the
**verbatim-identical** account-resolution logic (`_get_account`), the near-identical connection /
list-accounts logic (both built on `tastytrade.account.Account`), and the pure option-chain
strike-window helpers (`_strike` / `_nearest_expiration` / `_atm_window`).

Same design point as `cherrypit.dxfeed`: the broker **session is injected** (never fetched from a
consumer global — invariant: core never reaches back into a module), and `tastytrade` is imported
lazily (and the account class is factory-injectable), so this module imports and unit-tests without
the broker SDK or a network.

The stored account-number lookup is *not* done here — the consumer passes it as `default_number`,
so the core stays decoupled from any module's `credentials` shim.

Deliberately out of scope (module-local for now; a later, supervised per-module cutover — see
CUTOVER.md — moves them): the live order-construction/execution write path (`execute_trade` /
`_build_order`) and MEIC's stream-cache-aware / futures chain fetch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

# An account class exposes an async classmethod `get(session)` -> list[Account] and
# `get(session, number)` -> Account, matching `tastytrade.account.Account`.
AccountClass = Callable[..., Any]


def _default_account_cls() -> Any:
    from tastytrade.account import Account  # imported lazily so core imports without the broker SDK
    return Account


# --------------------------------------------------------------------------- account primitives
async def resolve_account(session: Any, account_number: str | None = None,
                          default_number: str | None = None, *, account_cls: Any = None) -> Any:
    """Resolve a single Account. Precedence: explicit `account_number` > `default_number` (e.g. the
    consumer's stored ACCOUNT_NUMBER) > the first account on the credentials. Raises RuntimeError if
    the credentials have no accounts. Byte-for-byte the behavior of both repos' `_get_account`, with
    the stored-number lookup lifted out to the caller.
    """
    account = account_cls or _default_account_cls()
    number = account_number or default_number
    if number:
        return await account.get(session, number)
    accounts = await account.get(session)
    if not accounts:
        raise RuntimeError("No accounts found for these credentials.")
    return accounts[0]


async def list_accounts(session: Any, *, account_cls: Any = None) -> list[dict]:
    """Return [{account_number, nickname, account_type}] for every account on the session."""
    account = account_cls or _default_account_cls()
    accounts = await account.get(session)
    return [
        {
            "account_number": a.account_number,
            "nickname": getattr(a, "nickname", None),
            "account_type": getattr(a, "account_type_name", None),
        }
        for a in accounts
    ]


async def account_count(session: Any, *, account_cls: Any = None) -> int:
    """Number of accounts on the session — the connectivity signal both `get_connection_status`
    commands report."""
    account = account_cls or _default_account_cls()
    return len(await account.get(session))


# --------------------------------------------------------------------------- option-chain helpers
def strike_of(option: Any) -> float | None:
    """Option strike as a float, or None if absent/non-numeric. (Both repos' `_strike`.)"""
    try:
        return float(option.strike_price)
    except (TypeError, ValueError):
        return None


def nearest_expiration(expirations: list[date], target_days: int = 0) -> date:
    """The expiration closest to `target_days` out from today. (Both repos' `_nearest_expiration`.)"""
    today = date.today()
    return min(expirations, key=lambda e: abs((e - today).days - target_days))


def atm_window(options: list, strike_count: int, around_price: float | None = None) -> list:
    """Keep the `strike_count` strikes on each side of the ATM strike (or of `around_price` when
    given), preserving every option at those strikes. (Both repos' `_atm_window`.)
    """
    strikes = sorted({s for s in (strike_of(o) for o in options) if s is not None})
    if not strikes:
        return options
    center = around_price if around_price is not None else strikes[len(strikes) // 2]
    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
    lo = max(0, nearest - strike_count)
    hi = min(len(strikes), nearest + strike_count + 1)
    keep = set(strikes[lo:hi])
    return [o for o in options if strike_of(o) in keep]
