"""Migration runner: numbered `.sql` files applied in order.

Applied state lives in `PRAGMA user_version`, which holds the number of the
highest migration applied. There is deliberately no bookkeeping table: the
database then contains exactly the objects §4 describes and nothing else, so
"every table and the view exist" is a claim about the spec rather than about
the spec plus some runner's private furniture. Numbered files applied in
order means version N implies 001..N ran.

Failing loudly is the house style, so this checks more than it strictly needs
to: filenames must be well-formed, numbering must be gapless, and a database
newer than the code is an error rather than a shrug.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_FILENAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """A migration could not be applied, or the set on disk is malformed."""


def discover(directory: Path | None = None) -> list[tuple[int, Path]]:
    """Every migration on disk, as (number, path), lowest first."""
    directory = directory or MIGRATIONS_DIR
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")

    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"migration filename does not match NNN_lower_snake.sql: {path.name}"
            )
        found.append((int(match.group(1)), path))

    numbers = [number for number, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise MigrationError(
            f"migration numbering must be gapless and start at 001, got: {numbers}"
        )
    return found


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_pending(conn: sqlite3.Connection, directory: Path | None = None) -> list[int]:
    """Apply every migration newer than the database's version.

    Returns the numbers applied — empty when the database was already current,
    which is what makes re-running `portal init` a no-op.
    """
    migrations = discover(directory)
    version = current_version(conn)
    highest = migrations[-1][0] if migrations else 0
    if version > highest:
        raise MigrationError(
            f"database is at migration {version:03d} but the code only ships "
            f"{highest:03d} — this database was written by a newer version"
        )

    applied: list[int] = []
    for number, path in migrations:
        if number <= version:
            continue
        sql = path.read_text(encoding="utf-8")
        # DDL and the version bump land in one transaction, so a failure
        # leaves neither a half-built schema nor a lying user_version.
        try:
            conn.executescript(
                f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # no transaction was open; the failure is still the real error
            raise MigrationError(f"migration {path.name} failed: {exc}") from exc
        applied.append(number)
    return applied
