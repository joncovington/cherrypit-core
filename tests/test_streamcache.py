"""Tests for cherrypick.core.streamcache — schema/connect, status upsert, chain write, ATM window."""

from cherrypick.core import streamcache


class _Opt:
    """Minimal stand-in for a tastytrade Option (has model_dump + strike_price)."""
    def __init__(self, sym, strike, exp="2026-07-10", und="SPX"):
        self.streamer_symbol = sym
        self.strike_price = strike
        self._d = {"streamer_symbol": sym, "strike_price": strike,
                   "expiration_date": exp, "underlying_symbol": und}

    def model_dump(self, mode="json"):
        return dict(self._d)


def test_connect_creates_schema_and_is_reusable(tmp_path):
    db = tmp_path / "sc.db"
    conn = streamcache.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"stream_chain", "stream_greeks", "stream_oi", "stream_trades", "stream_status"} <= tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stream_chain)")}
    assert "underlying_symbol" in cols
    conn.close()
    streamcache.connect(db).close()  # idempotent re-open


def test_to_float_nan_and_none_safe():
    assert streamcache.to_float(None) is None
    assert streamcache.to_float(float("nan")) is None
    assert streamcache.to_float("1.5") == 1.5
    assert streamcache.to_float(3) == 3.0


def test_upsert_status_single_row(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    streamcache.upsert_status(conn, pid=123, subscribed_symbols=5)
    streamcache.upsert_status(conn, subscribed_symbols=9)  # partial update keeps pid
    row = conn.execute("SELECT id, pid, subscribed_symbols FROM stream_status").fetchone()
    assert row["id"] == 1 and row["pid"] == 123 and row["subscribed_symbols"] == 9
    assert conn.execute("SELECT COUNT(*) FROM stream_status").fetchone()[0] == 1
    conn.close()


def test_write_chain_tags_underlying(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    opts = {"C600": _Opt("C600", 600), "P600": _Opt("P600", 600)}
    assert streamcache.write_chain(conn, opts) == 2
    rows = conn.execute("SELECT streamer_symbol, underlying_symbol, expiration FROM stream_chain").fetchall()
    assert {r["streamer_symbol"] for r in rows} == {"C600", "P600"}
    assert all(r["underlying_symbol"] == "SPX" and r["expiration"] == "2026-07-10" for r in rows)
    conn.close()


def test_current_underlying_price_reads_last(tmp_path):
    conn = streamcache.connect(tmp_path / "sc.db")
    conn.execute("INSERT INTO stream_trades (symbol, last, updated_at) VALUES ('SPX', 605.5, 0)")
    conn.commit()
    assert streamcache.current_underlying_price(conn, "SPX") == 605.5
    assert streamcache.current_underlying_price(conn, "QQQ") is None
    conn.close()


def test_atm_window_syms_centres_and_bounds():
    opts = {f"S{k}": _Opt(f"S{k}", k) for k in range(600, 621)}  # strikes 600..620
    keep = streamcache.atm_window_syms(opts, center=610.4, strike_count=2)
    strikes = sorted(int(s[1:]) for s in keep)
    assert strikes == [608, 609, 610, 611, 612]  # nearest (610) ± 2
    assert streamcache.atm_window_syms({}, 610, 2) == []
