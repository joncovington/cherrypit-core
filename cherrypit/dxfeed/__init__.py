"""cherrypit.dxfeed — shared on-demand DXLink event collectors.

Unifies MEICAgent's and EarningsAgent's near-identical `_collect_events` + per-event wrappers (Quote,
Greeks, Trade, Summary). One deliberate design fix over MEIC's version: the broker **session is
injected**, not fetched from a module global inside the function. That (a) satisfies the suite
invariant that core code never reaches back into a consumer, and (b) fixes MEIC's latent bug where a
missing-credentials error raised by `get_session()` was swallowed by the broad `except` and misreported
as a feed timeout — the caller now builds the session first, so that error propagates to the caller.

The persistent streamer *daemon* (MEICAgent's `streamer.py`) is intentionally out of scope; this module
is the on-demand path used by `tt.py get_quote` / `get_gex` / `get_vix1d` etc.

`tastytrade` is imported lazily (and the streamer is factory-injected), so this module imports and unit
tests without the broker SDK or a network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

# A streamer factory turns a broker session into an async-context-manager DXLink streamer exposing
# `await subscribe(event_cls, symbols)` and `listen(event_cls)` (an async iterator of events).
StreamerFactory = Callable[[Any], Any]
Extract = Callable[[Any], Any]


def _num(value: Any) -> float | None:
    """Coerce to float, or None if not numeric. (Byte-for-byte the `_num` both repos already use.)"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_streamer_factory(session: Any) -> Any:
    from tastytrade import DXLinkStreamer  # imported lazily so core imports without the broker SDK
    return DXLinkStreamer(session)


async def collect_events(session: Any, event_cls: Any, symbols: list[str], timeout: float,
                         extract: Extract | None = None,
                         streamer_factory: StreamerFactory | None = None) -> dict:
    """Subscribe to `event_cls` for `symbols`, collecting the latest value per symbol until either all
    symbols have reported or `timeout` seconds elapse. Returns {event_symbol: value}. `value` is the
    raw event unless `extract` is given, in which case `extract(event)` is stored (skipped when None).

    The broker session is the caller's responsibility (build it before calling); feed-level errors and
    the timeout return whatever was collected so far.
    """
    factory = streamer_factory or _default_streamer_factory
    out: dict = {}
    symbols = [s for s in symbols if s]
    if not symbols:
        return out

    async def _drain(streamer: Any) -> None:
        remaining = set(symbols)
        async for event in streamer.listen(event_cls):
            value = extract(event) if extract else event
            if value is not None:
                out[event.event_symbol] = value
            remaining.discard(event.event_symbol)
            if not remaining:
                return

    try:
        async with factory(session) as streamer:
            await streamer.subscribe(event_cls, symbols)
            await asyncio.wait_for(_drain(streamer), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass
    return out


# --- per-event convenience wrappers (session injected; event classes imported lazily) -----------
async def collect_greeks(session: Any, symbols: list[str], timeout: float, **kw) -> dict:
    from tastytrade.dxfeed import Greeks
    return await collect_events(session, Greeks, symbols, timeout, **kw)


async def collect_quotes(session: Any, symbols: list[str], timeout: float, **kw) -> dict:
    from tastytrade.dxfeed import Quote
    return await collect_events(session, Quote, symbols, timeout, **kw)


async def collect_last_prices(session: Any, symbols: list[str], timeout: float, **kw) -> dict:
    from tastytrade.dxfeed import Trade
    return await collect_events(session, Trade, symbols, timeout, extract=lambda e: _num(e.price), **kw)


async def collect_open_interest(session: Any, symbols: list[str], timeout: float, **kw) -> dict:
    """Summary events carry open_interest (fire within seconds on a fresh on-demand subscribe)."""
    from tastytrade.dxfeed import Summary
    return await collect_events(session, Summary, symbols, timeout, extract=lambda e: e.open_interest, **kw)


async def collect_option_volume(session: Any, symbols: list[str], timeout: float, **kw) -> dict:
    """Trade events carry day_volume (total volume traded for the day) alongside price."""
    from tastytrade.dxfeed import Trade
    return await collect_events(session, Trade, symbols, timeout, extract=lambda e: _num(e.day_volume), **kw)
