"""cherrypick.core.streamer — a generic persistent DXLink option-chain streaming engine.

Maintains one WebSocket to tastytrade's DXLink feed and writes the latest Quote / Greeks / Trade /
Summary events to a `cherrypick.core.streamcache` cache, giving each traded underlying its own
near-the-money strike window (which doubles as that symbol's GEX profile). Extracted from MEIC's
streamer daemon (plan Phase A) so both MEIC and the standalone GEX module run one engine instead of two.

Everything MEIC-specific is injected, so the engine itself has no MEIC dependency:
  * `session_factory()` -> a tastytrade Session (thread-appropriate; the engine calls it on its loop).
  * `extra_subscriptions(symbols)` -> {event_type: [streamer_symbol]} — extra symbols to keep subscribed
    beyond each symbol's live window (MEIC adds its open-position legs; the default is underlyings only).
  * `protected_symbols()` -> a set never unsubscribed when a window re-centres (MEIC's open legs).
  * `trade_hook(engine, symbol, price, ts)` -> called on every underlying Trade tick (MEIC's ORB capture).

Pure engine: no argparse, no HTTP server, no PID file, no config file — a thin per-consumer wrapper adds
those. `run()` blocks with reconnect/backoff until `stop()` (or SIGTERM/SIGINT if `install_signals`).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from cherrypick.core import streamcache

_RECONNECT_BASE = 2.0
_RECONNECT_MAX = 60.0
_COMMIT_BATCH_INTERVAL_S = 0.5
_COMMIT_BATCH_MAX_PENDING = 25


class _State:
    """Engine state for one connection lifetime (the connection is recreated across reconnects, but
    the cache connection and per-symbol window tracking persist for the daemon's whole run)."""

    def __init__(self, conn, symbols: list[str]) -> None:
        self.stop_event = asyncio.Event()
        self.subscribed: dict[str, list[str]] = {"Trade": [], "Quote": [], "Greeks": [], "Summary": []}
        self.reconnect_count = 0
        self.last_event_at: str | None = None
        self.conn = conn
        self.symbols = list(symbols)
        self.chains: dict[str, dict] = {}          # symbol -> {streamer_symbol: option}
        self.window_syms: dict[str, list[str]] = {}  # symbol -> subscribed window symbols
        self.centers: dict[str, float] = {}         # symbol -> price the window is centred on
        self.pending_writes = 0
        self.last_commit_at = 0.0


class ChainStreamer:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        db_path: Path | str,
        symbols: list[str],
        extra_subscriptions: Callable[[list[str]], dict[str, list[str]]] | None = None,
        protected_symbols: Callable[[], set[str]] | None = None,
        trade_hook: Callable[[ChainStreamer, str, float | None, float], None] | None = None,
        window_strike_count: int = 20,
        window_refresh_pts: float = 1.0,
        window_poll_s: float = 5.0,
        subscription_poll_s: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.db_path = Path(db_path)
        self.symbols = [s.strip().upper() for s in symbols]
        self._extra_subscriptions = extra_subscriptions
        self._protected_symbols = protected_symbols or (lambda: set())
        self._trade_hook = trade_hook
        self.window_strike_count = window_strike_count
        self.window_refresh_pts = window_refresh_pts
        self.window_poll_s = window_poll_s
        self.subscription_poll_s = subscription_poll_s
        self.log = logger or logging.getLogger("cherrypick.core.streamer")
        self.state: _State | None = None

    # -- injected policy defaults ----------------------------------------------------------------
    def _subscriptions(self) -> dict[str, list[str]]:
        if self._extra_subscriptions is not None:
            return self._extra_subscriptions(self.symbols)
        # Default: subscribe Trade + Summary for the underlyings (spot + session data); per-symbol
        # windows add the option Quote/Greeks/Summary/Trade themselves.
        return {"Trade": list(self.symbols), "Quote": [], "Greeks": [], "Summary": list(self.symbols)}

    # -- commit batching -------------------------------------------------------------------------
    def _maybe_commit(self, state: _State) -> None:
        state.pending_writes += 1
        now = time.time()
        if (state.pending_writes >= _COMMIT_BATCH_MAX_PENDING
                or (now - state.last_commit_at) >= _COMMIT_BATCH_INTERVAL_S):
            state.conn.commit()
            state.pending_writes = 0
            state.last_commit_at = now

    def _total_subscribed(self, state: _State) -> int:
        window_union: set[str] = set()
        for syms in state.window_syms.values():
            window_union.update(syms)
        total = 0
        for key in ("Trade", "Quote", "Greeks", "Summary"):
            total += len(set(state.subscribed.get(key, [])) | window_union)
        return total

    # -- connection lifetime ---------------------------------------------------------------------
    async def _run_stream(self, state: _State) -> None:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Greeks, Quote, Summary, Trade

        session = self.session_factory()
        self.log.info("Connecting DXLinkStreamer…")
        async with DXLinkStreamer(session) as streamer:
            streamcache.upsert_status(
                state.conn, pid=None, connected_since=datetime.now(UTC).isoformat(),
                reconnect_count=state.reconnect_count,
            )
            self.log.info("DXLinkStreamer connected (reconnects: %d)", state.reconnect_count)
            await self._apply_subscriptions(streamer, state, self._subscriptions(),
                                            Trade, Quote, Greeks, Summary)
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._listen_trade(streamer, state, Trade))
                tg.create_task(self._listen_quote(streamer, state, Quote))
                tg.create_task(self._listen_greeks(streamer, state, Greeks))
                tg.create_task(self._listen_summary(streamer, state, Summary))
                tg.create_task(self._poll_subscriptions(streamer, state, Trade, Quote, Greeks, Summary))
                tg.create_task(self._flush_status(state))
                tg.create_task(self._watch_stop(state))
                for sym in self.symbols:
                    tg.create_task(
                        self._symbol_refresher(streamer, state, sym, Quote, Greeks, Summary, Trade))

    async def _apply_subscriptions(self, streamer, state: _State, subs: dict,
                                   Trade, Quote, Greeks, Summary) -> None:
        cls_map = {"Trade": Trade, "Quote": Quote, "Greeks": Greeks, "Summary": Summary}
        window_union: set[str] = set()
        for syms in state.window_syms.values():
            window_union.update(syms)
        for key, symbols in subs.items():
            current = set(state.subscribed.get(key, []))
            wanted = set(symbols)
            add = wanted - current
            remove = current - wanted
            if key in ("Quote", "Greeks", "Summary"):
                remove -= window_union  # a window still wants these even if the extra-policy dropped them
            cls = cls_map[key]
            if add:
                await streamer.subscribe(cls, list(add))
                self.log.info("Subscribed %s %s", key, list(add))
            if remove:
                await streamer.unsubscribe(cls, list(remove))
                self.log.info("Unsubscribed %s %s", key, list(remove))
            state.subscribed[key] = list(wanted)
        streamcache.upsert_status(state.conn, subscribed_symbols=self._total_subscribed(state))

    async def _poll_subscriptions(self, streamer, state: _State, Trade, Quote, Greeks, Summary) -> None:
        while not state.stop_event.is_set():
            await asyncio.sleep(self.subscription_poll_s)
            if state.stop_event.is_set():
                break
            try:
                await self._apply_subscriptions(streamer, state, self._subscriptions(),
                                                Trade, Quote, Greeks, Summary)
                if state.last_event_at:
                    streamcache.upsert_status(state.conn, last_event_at=state.last_event_at)
            except Exception as exc:
                self.log.warning("Subscription poll error: %s", exc)

    # -- listeners -------------------------------------------------------------------------------
    def _touch(self, state: _State, ts: float) -> None:
        state.last_event_at = datetime.fromtimestamp(ts, tz=UTC).isoformat()

    async def _listen_trade(self, streamer, state: _State, Trade) -> None:
        conn = state.conn
        async for event in streamer.listen(Trade):
            if state.stop_event.is_set():
                break
            ts = time.time()
            try:
                conn.execute(
                    "INSERT INTO stream_trades (symbol, last, change, volume, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "last=excluded.last, change=excluded.change, volume=excluded.volume, "
                    "updated_at=excluded.updated_at",
                    (event.event_symbol, streamcache.to_float(event.price),
                     streamcache.to_float(event.change), streamcache.to_float(event.day_volume), ts),
                )
                self._maybe_commit(state)
                self._touch(state, ts)
                if self._trade_hook is not None:
                    self._trade_hook(self, event.event_symbol, streamcache.to_float(event.price), ts)
            except Exception as exc:
                self.log.warning("Trade write error: %s", exc)

    async def _listen_quote(self, streamer, state: _State, Quote) -> None:
        conn = state.conn
        async for event in streamer.listen(Quote):
            if state.stop_event.is_set():
                break
            ts = time.time()
            bid = streamcache.to_float(event.bid_price)
            ask = streamcache.to_float(event.ask_price)
            mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
            try:
                conn.execute(
                    "INSERT INTO stream_quotes (symbol, bid, ask, mid, bid_size, ask_size, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "bid=excluded.bid, ask=excluded.ask, mid=excluded.mid, "
                    "bid_size=excluded.bid_size, ask_size=excluded.ask_size, updated_at=excluded.updated_at",
                    (event.event_symbol, bid, ask, mid,
                     streamcache.to_float(event.bid_size), streamcache.to_float(event.ask_size), ts),
                )
                self._maybe_commit(state)
                self._touch(state, ts)
            except Exception as exc:
                self.log.warning("Quote write error: %s", exc)

    async def _listen_greeks(self, streamer, state: _State, Greeks) -> None:
        conn = state.conn
        async for event in streamer.listen(Greeks):
            if state.stop_event.is_set():
                break
            ts = time.time()
            try:
                conn.execute(
                    "INSERT INTO stream_greeks "
                    "(symbol, delta, gamma, theta, vega, rho, iv, price, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "delta=excluded.delta, gamma=excluded.gamma, theta=excluded.theta, "
                    "vega=excluded.vega, rho=excluded.rho, iv=excluded.iv, "
                    "price=excluded.price, updated_at=excluded.updated_at",
                    (event.event_symbol, streamcache.to_float(event.delta), streamcache.to_float(event.gamma),
                     streamcache.to_float(event.theta), streamcache.to_float(event.vega),
                     streamcache.to_float(event.rho), streamcache.to_float(event.volatility),
                     streamcache.to_float(event.price), ts),
                )
                self._maybe_commit(state)
                self._touch(state, ts)
            except Exception as exc:
                self.log.warning("Greeks write error: %s", exc)

    async def _listen_summary(self, streamer, state: _State, Summary) -> None:
        conn = state.conn
        async for event in streamer.listen(Summary):
            if state.stop_event.is_set():
                break
            oi = event.open_interest
            if oi is None:
                continue
            ts = time.time()
            try:
                conn.execute(
                    "INSERT INTO stream_oi (symbol, open_interest, updated_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "open_interest=excluded.open_interest, updated_at=excluded.updated_at",
                    (event.event_symbol, int(oi), ts),
                )
                self._maybe_commit(state)
                self._touch(state, ts)
            except Exception as exc:
                self.log.warning("Summary write error: %s", exc)

    # -- per-symbol ATM/GEX window ---------------------------------------------------------------
    async def _fetch_dte0_chain(self, underlying: str) -> dict:
        from tastytrade.instruments import get_option_chain
        session = self.session_factory()
        chain = await get_option_chain(session, underlying)
        if not chain:
            return {}
        nearest = min(chain.keys(), key=lambda e: abs((e - date.today()).days))
        return {o.streamer_symbol: o for o in chain[nearest] if getattr(o, "streamer_symbol", None)}

    async def _symbol_refresher(self, streamer, state: _State, symbol: str,
                                Quote, Greeks, Summary, Trade) -> None:
        self.log.info("[%s] Fetching 0DTE option chain…", symbol)
        try:
            chain = await self._fetch_dte0_chain(symbol)
            state.chains[symbol] = chain
            self.log.info("[%s] 0DTE chain loaded: %d options", symbol, len(chain))
            streamcache.write_chain(state.conn, chain)
        except Exception as exc:
            self.log.warning("[%s] Failed to fetch 0DTE chain: %s — window disabled", symbol, exc)
            return

        state.window_syms.setdefault(symbol, [])
        while not state.stop_event.is_set():
            price = streamcache.current_underlying_price(state.conn, symbol)
            if price is None:
                await asyncio.sleep(1)
                continue
            center = state.centers.get(symbol)
            if center is None or abs(price - center) >= self.window_refresh_pts:
                new_syms = streamcache.atm_window_syms(state.chains[symbol], price, self.window_strike_count)
                current_syms = state.window_syms.get(symbol, [])
                if new_syms != current_syms:
                    old_set, new_set = set(current_syms), set(new_syms)
                    add, remove = new_set - old_set, old_set - new_set
                    try:
                        if add:
                            add_list = list(add)
                            await streamer.subscribe(Quote, add_list)
                            await streamer.subscribe(Greeks, add_list)
                            await streamer.subscribe(Summary, add_list)
                            await streamer.subscribe(Trade, add_list)
                        if remove:
                            safe_remove = remove - self._protected_symbols()
                            if safe_remove:
                                srl = list(safe_remove)
                                await streamer.unsubscribe(Quote, srl)
                                await streamer.unsubscribe(Greeks, srl)
                                await streamer.unsubscribe(Summary, srl)
                                await streamer.unsubscribe(Trade, srl)
                        state.window_syms[symbol] = new_syms
                        streamcache.upsert_status(
                            state.conn, subscribed_symbols=self._total_subscribed(state))
                        self.log.info("[%s] window re-centered at %.2f (+%d/-%d symbols, total: %d)",
                                      symbol, price, len(add), len(remove), len(new_syms))
                    except Exception as exc:
                        self.log.warning("[%s] window update error: %s", symbol, exc)
                state.centers[symbol] = price
            await asyncio.sleep(self.window_poll_s)

    async def _flush_status(self, state: _State) -> None:
        while not state.stop_event.is_set():
            await asyncio.sleep(5)
            if state.last_event_at:
                try:
                    streamcache.upsert_status(state.conn, last_event_at=state.last_event_at)
                except Exception:
                    pass

    async def _watch_stop(self, state: _State) -> None:
        await state.stop_event.wait()
        raise asyncio.CancelledError("stop requested")

    # -- public entrypoints ----------------------------------------------------------------------
    def stop(self) -> None:
        if self.state is not None:
            self.state.stop_event.set()

    async def run_async(self) -> None:
        """Connect and stream with reconnect/backoff until stopped."""
        conn = streamcache.connect(self.db_path)
        state = _State(conn, self.symbols)
        self.state = state
        self.log.info("Streaming symbols: %s (±%d strikes each)", self.symbols, self.window_strike_count)
        delay = _RECONNECT_BASE
        while not state.stop_event.is_set():
            try:
                await self._run_stream(state)
                delay = _RECONNECT_BASE
            except asyncio.CancelledError:
                if state.stop_event.is_set():
                    break
                self.log.warning("Stream cancelled unexpectedly — will reconnect")
            except Exception as exc:
                if state.stop_event.is_set():
                    break
                # TaskGroup wraps failures in an ExceptionGroup whose str() hides detail — log each.
                if isinstance(exc, BaseExceptionGroup):
                    for i, sub in enumerate(exc.exceptions):
                        self.log.warning(
                            "Stream error sub-exception %d/%d: %s", i + 1, len(exc.exceptions),
                            "".join(traceback.format_exception(type(sub), sub, sub.__traceback__)))
                self.log.warning("Stream error: %s — reconnecting in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX)
                state.reconnect_count += 1
        conn.close()
        self.log.info("Streamer stopped.")

    def run(self, install_signals: bool = True) -> None:
        """Blocking run: set up SIGTERM/SIGINT (optional) and drive the async reconnect loop."""
        if install_signals:
            def _handle(sig, frame):
                self.log.info("Signal %s received — stopping", sig)
                self.stop()
            try:
                signal.signal(signal.SIGTERM, _handle)
                signal.signal(signal.SIGINT, _handle)
            except ValueError:
                pass  # not on the main thread — caller drives stop() itself
        asyncio.run(self.run_async())
