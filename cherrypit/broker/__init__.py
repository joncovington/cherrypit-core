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

Order **construction** (`build_order`) is included — it's pure (a dict spec -> a NewOrder object,
no network) and unifies both repos' `_build_order`. The tastytrade order classes are imported lazily
(or injected as `order_ns`), so it too unit-tests without the broker SDK.

Order **submission** (`place_order`) is also included — it unifies the preflight-then-optionally-live
core of both repos' `cmd_execute_trade`, with the safety invariant that a live order is placed on
exactly one path (`live=True` with an error-free preflight). The account is passed in (its
`place_order` is mocked in tests), so this too runs offline. The per-module CLI gating
(`--live`/`--dry_run`), the live-trading-enabled check, and try/except response shaping stay in the
consumer.

Deliberately out of scope (module-local): MEIC's stream-cache-aware / futures chain fetch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
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


# --------------------------------------------------------------------------- order construction
# Order-action spec string -> tastytrade OrderAction enum name. Byte-for-byte identical in both repos.
ACTION_MAP = {
    "buy to open": "BUY_TO_OPEN",
    "sell to open": "SELL_TO_OPEN",
    "buy to close": "BUY_TO_CLOSE",
    "sell to close": "SELL_TO_CLOSE",
}


def _default_order_ns() -> Any:
    # Imported lazily so the module imports without the broker SDK.
    from tastytrade.order import Leg, NewOrder, OrderAction, OrderTimeInForce, OrderType
    return SimpleNamespace(Leg=Leg, NewOrder=NewOrder, OrderAction=OrderAction,
                           OrderTimeInForce=OrderTimeInForce, OrderType=OrderType)


def build_order(spec: dict, *, order_ns: Any = None) -> Any:
    """Construct a tastytrade NewOrder from a plain dict `spec` — pure construction, no submission.

    Unifies both repos' `_build_order` (MEIC's is the superset — the extra `stop_trigger` is simply
    omitted when absent, so it also serves EarningsAgent). `spec` keys:
      - time_in_force (default "Day"), order_type (default "Limit")
      - legs: [{instrument_type, symbol, action, quantity}] — `action` is a human string
        ("buy to open", ...) mapped through ACTION_MAP to the OrderAction enum
      - price (optional) with price_effect "credit"/"debit" to sign it (credit -> negative)
      - stop_trigger (optional)

    The tastytrade order classes are imported lazily, or injected via `order_ns` (an object exposing
    Leg / NewOrder / OrderAction / OrderTimeInForce / OrderType) for offline tests.
    """
    ns = order_ns or _default_order_ns()
    tif = ns.OrderTimeInForce(str(spec.get("time_in_force", "Day")))
    otype = ns.OrderType(str(spec.get("order_type", "Limit")))
    legs = []
    for leg in spec.get("legs", []):
        action = ns.OrderAction[ACTION_MAP[str(leg["action"]).strip().lower()]]
        legs.append(ns.Leg(
            instrument_type=leg["instrument_type"],
            symbol=leg["symbol"],
            action=action,
            quantity=Decimal(str(leg["quantity"])),
        ))
    kwargs: dict = {"time_in_force": tif, "order_type": otype, "legs": legs}
    if spec.get("price") is not None:
        price = Decimal(str(spec["price"]))
        effect = spec.get("price_effect")
        if effect is not None:
            magnitude = abs(price)
            price = -magnitude if str(effect).strip().lower() == "credit" else magnitude
        kwargs["price"] = price
    if spec.get("stop_trigger") is not None:
        kwargs["stop_trigger"] = Decimal(str(spec["stop_trigger"]))
    return ns.NewOrder(**kwargs)


# --------------------------------------------------------------------------- order submission
def _buying_power_summary(preflight: Any) -> dict:
    warnings = [str(w) for w in (getattr(preflight, "warnings", None) or [])]
    summary: dict = {"warnings": warnings}
    bpe = getattr(preflight, "buying_power_effect", None)
    if bpe:
        summary.update({
            "current_buying_power": str(getattr(bpe, "current_buying_power", None)),
            "new_buying_power": str(getattr(bpe, "new_buying_power", None)),
            "change_in_buying_power": str(getattr(bpe, "change_in_buying_power", None)),
        })
    return summary


async def place_order(account: Any, session: Any, order: Any, *, live: bool,
                      serialize: Callable[[Any], Any] | None = None) -> dict:
    """Preflight an order (always a dry-run first), then submit it live **only** if `live` is True
    and the preflight reported no errors. Unifies the submission core of both repos'
    `cmd_execute_trade`; the CLI gating (how `--live`/`--dry_run` map to `live`), the
    live-trading-enabled check, and try/except shaping stay in the caller.

    Safety invariant: a live order (`dry_run=False`) is placed on exactly one path — `live=True`
    with an error-free preflight. Any preflight error returns early; `live=False` returns the
    dry-run result without a second call.

    Returns a JSON-safe dict (`serialize` shapes the raw tastytrade preflight/response objects;
    defaults to identity):
      - preflight errors: {ok: False, error: "pre-flight validation failed", problems, buying_power}
      - dry run:          {ok: True, dry_run: True,  account_number, buying_power, response}
      - live:             {ok: True, dry_run: False, account_number, buying_power, response}
    """
    serialize = serialize or (lambda x: x)

    preflight = await account.place_order(session, order, dry_run=True)
    errors = [str(e) for e in (getattr(preflight, "errors", None) or [])]
    bp_summary = _buying_power_summary(preflight)
    if errors:
        return {"ok": False, "error": "pre-flight validation failed",
                "problems": errors, "buying_power": bp_summary}

    if not live:
        return {"ok": True, "dry_run": True, "account_number": account.account_number,
                "buying_power": bp_summary, "response": serialize(preflight)}

    response = await account.place_order(session, order, dry_run=False)
    return {"ok": True, "dry_run": False, "account_number": account.account_number,
            "buying_power": bp_summary, "response": serialize(response)}
