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

# A migration that must REBUILD a table other objects depend on declares it on
# a line of its own. SQLite cannot relax an inline CHECK, so the only way to
# widen one is the 12-step procedure from its own documentation — create,
# copy, drop, rename — and two of its steps need a pragma that a migration
# cannot set for itself. `_apply_table_rebuild` explains which and why.
_REBUILD = "-- pragma: table-rebuild"


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
        try:
            if _REBUILD in sql:
                _apply_table_rebuild(conn, sql, number, path)
            else:
                # DDL and the version bump land in one transaction, so a failure
                # leaves neither a half-built schema nor a lying user_version.
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


def _apply_table_rebuild(
    conn: sqlite3.Connection, sql: str, number: int, path: Path
) -> None:
    """SQLite's own 12-step table-rebuild procedure, in the order it prescribes.

    Two pragmas, and a migration can set neither for itself.

    **`foreign_keys = OFF`.** Step 3 of the procedure is `DROP TABLE`, which
    performs an implicit DELETE that fires every `ON DELETE CASCADE` hanging
    off the table. Fifteen tables reference `company(id)`; run with the pragma
    ON, a rebuild of it would empty the corpus rather than widen it. The pragma
    is silently a no-op inside a transaction, so it has to be set OUTSIDE one —
    which only the runner can do.

    **`legacy_alter_table = ON`.** Step 4 is `ALTER TABLE … RENAME`, and modern
    SQLite reparses every view and trigger to rewrite references. At that
    moment `company` has just been dropped, so `company_profile` — which
    references it — cannot be parsed and the rename fails outright with
    *"error in view company_profile: no such table: main.company"*. Legacy mode
    renames without reparsing, which is exactly right HERE and only here: no
    object references the `_new` table, every object references the name it is
    about to be renamed to, and the alternative is copying a view that seven
    migrations have already redefined into an eighth place where it can drift.

    **`PRAGMA foreign_key_check` runs BEFORE the COMMIT, not after.** A rebuild
    that lost a parent row is a corrupt corpus, and after COMMIT the only
    remedy is a backup; before it, ROLLBACK costs nothing. That is why these
    scripts cannot carry their own `COMMIT` the way every other one does:
    `executescript` commits any open transaction before it runs, so the BEGIN
    lives inside the script and the COMMIT comes after the check.

    Both pragmas are restored in a `finally`, so a failure cannot leave the
    connection quietly unguarded for everything that runs after it.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        # The transaction is closed HERE, inside the inner try, and never left
        # for the caller to close. `PRAGMA foreign_keys` is a no-op while a
        # transaction is open, so restoring it in the `finally` below with a
        # failed script still pending would silently leave the connection
        # unguarded for everything that runs after it — measured by
        # `test_a_failing_rebuild_rolls_back_and_keeps_the_corpus`, which read
        # back `foreign_keys = 0` from exactly that ordering.
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};")
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MigrationError(
                    f"migration {path.name} rebuilt a table and left "
                    f"{len(violations)} dangling foreign key reference(s): "
                    f"{violations[:5]}. Rolled back; nothing was written."
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # nothing was open; the original failure is the real one
            raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
