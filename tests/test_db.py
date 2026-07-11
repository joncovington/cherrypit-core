"""Tests for cherrypick.core.db — connection conventions + additive migrations, on real temp SQLite files."""

import sqlite3

from cherrypick.core import db


def test_connect_creates_parent_dir_and_row_factory(tmp_path):
    dbfile = tmp_path / "nested" / "sub" / "trades.db"
    conn = db.connect(dbfile)
    try:
        assert dbfile.parent.is_dir()  # parent dirs created
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'x')")
        row = conn.execute("SELECT * FROM t").fetchone()
        assert row["a"] == 1 and row["b"] == "x"  # sqlite3.Row by default -> mapping access
    finally:
        conn.close()


def test_connect_row_factory_none_gives_tuples(tmp_path):
    conn = db.connect(tmp_path / "t.db", row_factory=None)
    try:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (7)")
        assert conn.execute("SELECT a FROM t").fetchone() == (7,)
    finally:
        conn.close()


def test_connect_applies_pragmas(tmp_path):
    conn = db.connect(tmp_path / "t.db", pragmas=("journal_mode=WAL", "foreign_keys=ON"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_memory_db_has_no_parent_mkdir():
    conn = db.connect(":memory:")  # must not raise trying to mkdir a parent
    try:
        conn.execute("CREATE TABLE t (a)")
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0
    finally:
        conn.close()


_MIGRATIONS = [
    ("trades", "profile", "ALTER TABLE trades ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"),
    ("trades", "quantity", "ALTER TABLE trades ADD COLUMN quantity INTEGER"),
]


def _fresh_trades(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.commit()
    return conn


def test_apply_additive_migrations_adds_missing_columns(tmp_path):
    conn = _fresh_trades(tmp_path)
    try:
        added = db.apply_additive_migrations(conn, _MIGRATIONS)
        assert added == ["trades.profile", "trades.quantity"]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        assert {"profile", "quantity"} <= cols
        # the DEFAULT applied
        conn.execute("INSERT INTO trades (symbol) VALUES ('AAPL')")
        assert conn.execute("SELECT profile FROM trades").fetchone()["profile"] == "default"
    finally:
        conn.close()


def test_apply_additive_migrations_is_idempotent(tmp_path):
    conn = _fresh_trades(tmp_path)
    try:
        db.apply_additive_migrations(conn, _MIGRATIONS)
        added_again = db.apply_additive_migrations(conn, _MIGRATIONS)
        assert added_again == []  # nothing added the second time
    finally:
        conn.close()


def test_apply_additive_migrations_partial_when_some_exist(tmp_path):
    conn = _fresh_trades(tmp_path)
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN profile TEXT")  # pre-existing column
        conn.commit()
        added = db.apply_additive_migrations(conn, _MIGRATIONS)
        assert added == ["trades.quantity"]  # only the missing one
    finally:
        conn.close()


def test_apply_additive_migrations_commits(tmp_path):
    dbfile = tmp_path / "c.db"
    conn = _fresh_trades_at(dbfile)
    try:
        db.apply_additive_migrations(conn, _MIGRATIONS)
    finally:
        conn.close()
    # reopen from disk: the migration was committed
    conn2 = sqlite3.connect(dbfile)
    try:
        cols = {r[1] for r in conn2.execute("PRAGMA table_info(trades)").fetchall()}
        assert {"profile", "quantity"} <= cols
    finally:
        conn2.close()


def _fresh_trades_at(dbfile):
    conn = db.connect(dbfile)
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.commit()
    return conn
