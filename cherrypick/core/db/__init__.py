"""cherrypick.core.db — shared SQLite engine mechanics (connection + additive migrations).

Unifies the near-identical connection setup and the byte-identical additive-migration runner that
MEICAgent's and EarningsAgent's db modules duplicate (`db.py`, `db_paper.py`). This is engine
plumbing only — pure stdlib, no ORM. Table **schemas stay in the consumers**; only the mechanics
(create parent dir, `row_factory`, pragmas, "add missing columns" migration) live here.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

# A migration is (table, column, alter_sql): run `alter_sql` only when `column` is missing from `table`.
Migration = tuple[str, str, str]


def connect(path: Any, *, row_factory: Any = sqlite3.Row,
            pragmas: Sequence[str] = ()) -> sqlite3.Connection:
    """Open a SQLite connection with the suite's shared conventions.

    Creates the database's parent directory, opens the connection, sets `row_factory` (defaults to
    `sqlite3.Row`; pass `None` to keep raw tuples), and applies each `PRAGMA` in `pragmas`
    (e.g. `("journal_mode=WAL", "foreign_keys=ON")`). Uses `os.path` so `":memory:"` and bare
    filenames (no parent dir) are handled without a spurious mkdir.
    """
    path = str(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    if row_factory is not None:
        conn.row_factory = row_factory
    for pragma in pragmas:
        conn.execute(f"PRAGMA {pragma}")
    return conn


def apply_additive_migrations(conn: sqlite3.Connection,
                              migrations: Iterable[Migration]) -> list[str]:
    """Idempotently add missing columns. For each `(table, column, alter_sql)`, run `alter_sql` only
    if `column` is absent from `table` (checked via `PRAGMA table_info`). Commits once at the end.

    Returns the list of `"table.column"` actually added (for logging/tests). Safe to run on every
    startup — a no-op once every column exists. This is the exact `_migrate` both Earnings db modules
    already share, lifted to core.
    """
    added: list[str] = []
    for table, column, alter_sql in migrations:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(alter_sql)
            added.append(f"{table}.{column}")
    conn.commit()
    return added
