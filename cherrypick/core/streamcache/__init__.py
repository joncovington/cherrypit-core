"""cherrypick.core.streamcache — the shared stream-cache schema + SQLite helpers.

The persistent option-chain cache a streamer daemon writes and readers (GEX, dashboards, a trading
loop) read: latest Quote / Greeks / Trade(volume) / Summary(open-interest) per option symbol, plus the
option-chain structure and a small daemon-status row. Extracted from MEIC's streamer so any consumer —
MEIC's own daemon, the standalone GEX module — writes and reads one identical schema instead of each
carrying a private copy (plan Phase A of the streamer extraction).

Pure SQLite + stdlib; no broker, no network, no tastytrade import. A streaming *engine*
(`cherrypick.core.streamer`) fills this cache; a provider (`cherrypick-gex`) reads it read-only.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# The schema every consumer shares. orb_ranges/stream_rest_cache are used only by MEIC's daemon today
# but are kept here so MEIC can adopt this DDL verbatim when it migrates onto the core engine.
DDL = """
CREATE TABLE IF NOT EXISTS stream_chain (
    streamer_symbol   TEXT PRIMARY KEY,
    expiration        TEXT NOT NULL,
    underlying_symbol TEXT,
    data_json         TEXT NOT NULL,
    updated_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chain_expiration ON stream_chain(expiration);
CREATE TABLE IF NOT EXISTS stream_quotes (
    symbol      TEXT PRIMARY KEY,
    bid         REAL,
    ask         REAL,
    mid         REAL,
    bid_size    REAL,
    ask_size    REAL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_greeks (
    symbol      TEXT PRIMARY KEY,
    delta       REAL,
    gamma       REAL,
    theta       REAL,
    vega        REAL,
    rho         REAL,
    iv          REAL,
    price       REAL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_trades (
    symbol      TEXT PRIMARY KEY,
    last        REAL,
    change      REAL,
    volume      REAL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_oi (
    symbol        TEXT PRIMARY KEY,
    open_interest INTEGER,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_rest_cache (
    key         TEXT PRIMARY KEY,
    data_json   TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_status (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    pid                 INTEGER,
    connected_since     TEXT,
    last_event_at       TEXT,
    subscribed_symbols  INTEGER DEFAULT 0,
    reconnect_count     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orb_ranges (
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    orb_high    REAL,
    orb_low     REAL,
    captured_at REAL,
    PRIMARY KEY (symbol, trade_date)
);
"""


def to_float(value) -> float | None:
    """NaN-safe float coercion for event fields (DXLink sends NaN for missing greeks/prices)."""
    if value is None:
        return None
    try:
        v = float(value)
        return None if v != v else v  # NaN guard
    except (TypeError, ValueError):
        return None


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating + migrating) the write-side cache. WAL + NORMAL for a daemon that commits often
    while readers open the same file read-only. `check_same_thread=False`: MEIC's daemon touches the
    connection from its DXLink loop and a status flusher."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    for stmt in DDL.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    # Additive migration for caches created before underlying_symbol existed (XSP/SPX share 0DTE dates,
    # so an expiration-only filter would blend chains — the column lets readers disambiguate).
    existing = {row[1] for row in conn.execute("PRAGMA table_info(stream_chain)")}
    if "underlying_symbol" not in existing:
        conn.execute("ALTER TABLE stream_chain ADD COLUMN underlying_symbol TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chain_underlying "
                 "ON stream_chain(underlying_symbol, expiration)")
    conn.commit()
    return conn


def upsert_status(conn: sqlite3.Connection, **kwargs) -> None:
    """Upsert the single daemon-status row (id=1) with whatever fields are supplied."""
    fields = dict(kwargs)
    cols = ", ".join(fields)
    vals = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields if k != "id")
    conn.execute(
        f"INSERT INTO stream_status (id, {cols}) VALUES (1, {vals}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        list(fields.values()),
    )
    conn.commit()


def write_chain(conn: sqlite3.Connection, option_map: dict) -> int:
    """Persist an option-chain structure ({streamer_symbol: option}). Tags each row with its
    underlying_symbol so lookups can filter by underlying. Returns rows written."""
    now = time.time()
    rows = []
    for sym, o in option_map.items():
        dump = getattr(o, "model_dump", None)
        data = dump(mode="json") if callable(dump) else {"streamer_symbol": sym}
        rows.append((sym, str(data.get("expiration_date", "")), data.get("underlying_symbol"),
                     json.dumps(data), now))
    conn.executemany(
        "INSERT INTO stream_chain (streamer_symbol, expiration, underlying_symbol, data_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(streamer_symbol) DO UPDATE SET "
        "expiration=excluded.expiration, underlying_symbol=excluded.underlying_symbol, "
        "data_json=excluded.data_json, updated_at=excluded.updated_at",
        rows,
    )
    conn.commit()
    return len(rows)


def current_underlying_price(conn: sqlite3.Connection, underlying: str) -> float | None:
    """Latest last-trade price for an underlying from the cache (used to centre the ATM window)."""
    try:
        row = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (underlying,)).fetchone()
        return float(row["last"]) if row and row["last"] is not None else None
    except sqlite3.Error:
        return None


def atm_window_syms(option_map: dict, center: float, strike_count: int) -> list[str]:
    """Streamer symbols within `strike_count` strikes of `center` on each side."""
    strikes = sorted({float(o.strike_price) for o in option_map.values()})
    if not strikes:
        return []
    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
    lo = max(0, nearest - strike_count)
    hi = min(len(strikes), nearest + strike_count + 1)
    keep = set(strikes[lo:hi])
    return [sym for sym, o in option_map.items() if float(o.strike_price) in keep]
