"""SQLite connection handling.

WAL mode and foreign-key enforcement are properties of every connection this
tool opens; nothing else in the codebase should be setting pragmas.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if absent) the database at `path`.

    `isolation_level=None` puts the connection in autocommit mode so that
    transactions are started and ended explicitly. The migration runner
    depends on this: `executescript` implicitly commits any transaction
    Python is managing on its behalf, which would silently break the
    all-or-nothing guarantee migrations need.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL is persistent — recorded in the file header, not per connection —
    # but setting it every time costs nothing and keeps it true.
    conn.execute("PRAGMA journal_mode = WAL")
    # Off by default in SQLite, and ON DELETE CASCADE is load-bearing for
    # `portal forget` (§8). Must be set outside a transaction to take effect.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def object_inventory(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Names of the schema objects present, grouped by type.

    Used by `portal init` to show what it created, and by the tests to assert
    the applied schema matches §4.
    """
    rows = conn.execute(
        """
        SELECT type, name FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    inventory: dict[str, list[str]] = {}
    for row in rows:
        inventory.setdefault(row["type"], []).append(row["name"])
    return inventory
