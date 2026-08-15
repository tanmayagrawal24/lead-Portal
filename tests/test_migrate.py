"""Migration runner behaviour, including the M0 re-run-is-a-no-op gate."""

from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portal import cli, db, migrate

# §4 uses a partial index (uq_artifact_identity, idx_review_flag_open).
assert sqlite3.sqlite_version_info >= (3, 8, 0), "SQLite 3.8+ required"


def run_cli(*argv: str) -> int:
    """Invoke the CLI, swallowing its stdout so test output stays readable."""
    with contextlib.redirect_stdout(io.StringIO()):
        return cli.main(list(argv))


class TempDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "portal.db"

    def open_db(self) -> sqlite3.Connection:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn


class TestDiscover(unittest.TestCase):
    def test_ships_at_least_migration_001(self) -> None:
        migrations = migrate.discover()
        self.assertEqual(migrations[0][0], 1)
        self.assertTrue(migrations[0][1].name.startswith("001_"))

    def test_rejects_malformed_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "initial.sql").write_text("SELECT 1;")
            with self.assertRaisesRegex(migrate.MigrationError, "does not match"):
                migrate.discover(Path(tmp))

    def test_rejects_gap_in_numbering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "001_a.sql").write_text("SELECT 1;")
            (Path(tmp) / "003_c.sql").write_text("SELECT 1;")
            with self.assertRaisesRegex(migrate.MigrationError, "gapless"):
                migrate.discover(Path(tmp))

    def test_rejects_missing_directory(self) -> None:
        with self.assertRaisesRegex(migrate.MigrationError, "not found"):
            migrate.discover(Path("/nonexistent/migrations"))


class TestApplyPending(TempDbTestCase):
    def test_applies_then_is_a_no_op(self) -> None:
        conn = self.open_db()

        self.assertEqual(migrate.apply_pending(conn), [1])
        version = migrate.current_version(conn)
        inventory = db.object_inventory(conn)

        self.assertEqual(migrate.apply_pending(conn), [])
        self.assertEqual(migrate.current_version(conn), version)
        self.assertEqual(db.object_inventory(conn), inventory)

    def test_user_version_matches_highest_migration(self) -> None:
        conn = self.open_db()
        migrate.apply_pending(conn)
        self.assertEqual(migrate.current_version(conn), migrate.discover()[-1][0])

    def test_rejects_database_newer_than_the_code(self) -> None:
        conn = self.open_db()
        conn.execute("PRAGMA user_version = 99")
        with self.assertRaisesRegex(migrate.MigrationError, "newer version"):
            migrate.apply_pending(conn)

    def test_failed_migration_leaves_no_partial_schema(self) -> None:
        """DDL and the version bump are one transaction, so a failure part-way
        through a file must roll the whole file back, not leave half a schema."""
        conn = self.open_db()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "001_broken.sql").write_text(
                "CREATE TABLE good (id INTEGER PRIMARY KEY);\n"
                "CREATE TABLE bad (id INTEGER PRIMARY KEY, ,);\n"
            )
            with self.assertRaises(migrate.MigrationError):
                migrate.apply_pending(conn, Path(tmp))

        self.assertEqual(migrate.current_version(conn), 0)
        self.assertEqual(db.object_inventory(conn), {})

    def test_runner_creates_no_bookkeeping_table(self) -> None:
        """Applied state is user_version, so the database holds exactly §4."""
        conn = self.open_db()
        migrate.apply_pending(conn)
        for name in db.object_inventory(conn).get("table", []):
            self.assertNotIn("migration", name.lower())
            self.assertNotIn("schema_version", name.lower())


class TestConnection(TempDbTestCase):
    def test_wal_and_foreign_keys_are_on(self) -> None:
        conn = self.open_db()
        self.assertEqual(
            conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal"
        )
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)


class TestInitCommand(TempDbTestCase):
    def test_creates_database_and_re_running_is_a_no_op(self) -> None:
        self.assertFalse(self.db_path.exists())
        self.assertEqual(run_cli("--db", str(self.db_path), "init"), 0)
        self.assertTrue(self.db_path.exists())

        conn = self.open_db()
        version = migrate.current_version(conn)
        inventory = db.object_inventory(conn)
        self.assertEqual(version, 1)

        self.assertEqual(run_cli("--db", str(self.db_path), "init"), 0)
        self.assertEqual(migrate.current_version(conn), version)
        self.assertEqual(db.object_inventory(conn), inventory)

    def test_does_not_destroy_existing_rows(self) -> None:
        run_cli("--db", str(self.db_path), "init")
        conn = self.open_db()
        conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES ('example.de', 'seed_csv', '2026-08-15T00:00:00Z')"
        )
        run_cli("--db", str(self.db_path), "init")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], 1)

    def test_honours_db_flag_over_default(self) -> None:
        elsewhere = self.db_path.parent / "nested" / "other.db"
        run_cli("--db", str(elsewhere), "init")
        self.assertTrue(elsewhere.exists())
        self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
