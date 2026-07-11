"""Tests for cherrypick.core.dxfeed.collect_events using a fake session + fake DXLink streamer.

No tastytrade, no network: the streamer is factory-injected and events are hand-built. Async is driven
via asyncio.run() so no pytest-asyncio plugin is required.
"""

import asyncio

import pytest

from cherrypick.core import dxfeed


class FakeEvent:
    def __init__(self, event_symbol, **fields):
        self.event_symbol = event_symbol
        for k, v in fields.items():
            setattr(self, k, v)


class FakeStreamer:
    def __init__(self, events, raise_on_subscribe=False, listen_hangs=False):
        self._events = list(events)
        self._raise_on_subscribe = raise_on_subscribe
        self._listen_hangs = listen_hangs
        self.subscribed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe(self, event_cls, symbols):
        if self._raise_on_subscribe:
            raise RuntimeError("subscribe failed")
        self.subscribed.append((event_cls, list(symbols)))

    async def listen(self, event_cls):
        if self._listen_hangs:
            await asyncio.sleep(10)  # cancelled by wait_for's timeout well before this elapses
        for e in self._events:
            yield e


def _factory(streamer):
    return lambda session: streamer


def _run(coro):
    return asyncio.run(coro)


SENTINEL_CLS = object()  # event_cls is opaque to collect_events


def test_collects_events_keyed_by_symbol():
    events = [FakeEvent("A"), FakeEvent("B")]
    streamer = FakeStreamer(events)
    out = _run(dxfeed.collect_events("sess", SENTINEL_CLS, ["A", "B"], 1.0,
                                     streamer_factory=_factory(streamer)))
    assert set(out) == {"A", "B"}
    assert out["A"] is events[0]
    assert streamer.subscribed == [(SENTINEL_CLS, ["A", "B"])]


def test_extract_is_applied():
    events = [FakeEvent("A", price="12.5"), FakeEvent("B", price="7")]
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, ["A", "B"], 1.0,
                                     extract=lambda e: dxfeed._num(e.price),
                                     streamer_factory=_factory(FakeStreamer(events))))
    assert out == {"A": 12.5, "B": 7.0}


def test_returns_as_soon_as_all_symbols_seen():
    # A later update for A must not overwrite the first, because _drain returns once B completes the set.
    events = [FakeEvent("A", v=1), FakeEvent("B", v=2), FakeEvent("A", v=99)]
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, ["A", "B"], 1.0,
                                     extract=lambda e: e.v,
                                     streamer_factory=_factory(FakeStreamer(events))))
    assert out == {"A": 1, "B": 2}


def test_empty_symbols_returns_empty_without_subscribing():
    streamer = FakeStreamer([FakeEvent("A")])
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, [], 1.0, streamer_factory=_factory(streamer)))
    assert out == {}
    assert streamer.subscribed == []


def test_falsy_symbols_are_filtered():
    streamer = FakeStreamer([FakeEvent("X")])
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, [None, "", "X"], 1.0,
                                     streamer_factory=_factory(streamer)))
    assert set(out) == {"X"}
    assert streamer.subscribed == [(SENTINEL_CLS, ["X"])]


def test_none_extracted_value_is_skipped_but_symbol_completes():
    events = [FakeEvent("A", v=None), FakeEvent("B", v=5)]
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, ["A", "B"], 1.0,
                                     extract=lambda e: e.v,
                                     streamer_factory=_factory(FakeStreamer(events))))
    assert out == {"B": 5}  # A completed the remaining-set but its None value was not stored


def test_feed_ends_early_returns_partial():
    # Only A reports; the generator ends before B arrives -> partial result, no error.
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, ["A", "B"], 1.0,
                                     streamer_factory=_factory(FakeStreamer([FakeEvent("A")]))))
    assert set(out) == {"A"}


def test_timeout_returns_what_was_collected():
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, ["A", "B"], 0.05,
                                     streamer_factory=_factory(FakeStreamer([], listen_hangs=True))))
    assert out == {}


def test_subscribe_error_is_swallowed():
    out = _run(dxfeed.collect_events("s", SENTINEL_CLS, ["A"], 1.0,
                                     streamer_factory=_factory(FakeStreamer([], raise_on_subscribe=True))))
    assert out == {}


@pytest.mark.parametrize("value,expected", [("3.5", 3.5), (7, 7.0), (None, None), ("x", None),
                                            (float("nan"), float("nan"))])
def test_num(value, expected):
    result = dxfeed._num(value)
    if isinstance(expected, float) and expected != expected:  # NaN
        assert result != result
    else:
        assert result == expected
