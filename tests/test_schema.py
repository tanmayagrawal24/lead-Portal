"""The applied schema matches §4 of the spec.

This is the M0 gate: every table and the view exist. The expected sets are
written out longhand rather than derived from the migration, so that a table
silently disappearing from the migration fails a test instead of quietly
agreeing with itself.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portal import db, migrate

SPEC_TABLES = {
    "company",
    "review_flag",
    "artifact",
    "signal",
    "score",
    "score_component",
    "contact",
    "outreach",
    "run",
    "llm_batch",
}

SPEC_VIEWS = {"company_profile"}

SPEC_TRIGGERS = {
    "trg_review_flag_after_insert",
    "trg_review_flag_after_update",
    "trg_review_flag_after_delete",
}

# Named in §4 because behaviour depends on them, not just performance.
SPEC_INDEXES = {
    "uq_artifact_identity",
    "uq_signal_identity",
    "uq_score_identity",
    "uq_review_flag",
}

NOW = "2026-08-15T12:00:00Z"


class SchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.connect(Path(self._tmp.name) / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def add_company(self, domain: str = "example.de") -> int:
        cur = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) VALUES (?,?,?)",
            (domain, "seed_csv", NOW),
        )
        return int(cur.lastrowid)

    def add_run(self, stage: str = "fetch") -> int:
        cur = self.conn.execute(
            "INSERT INTO run (started_at, stage) VALUES (?,?)", (NOW, stage)
        )
        return int(cur.lastrowid)

    def needs_review(self, company_id: int) -> int:
        return int(
            self.conn.execute(
                "SELECT needs_review FROM company WHERE id = ?", (company_id,)
            ).fetchone()[0]
        )


class TestObjectsExist(SchemaTestCase):
    def test_every_spec_table_exists(self) -> None:
        self.assertEqual(set(db.object_inventory(self.conn)["table"]), SPEC_TABLES)

    def test_the_view_exists(self) -> None:
        self.assertEqual(set(db.object_inventory(self.conn)["view"]), SPEC_VIEWS)

    def test_triggers_exist(self) -> None:
        self.assertEqual(set(db.object_inventory(self.conn)["trigger"]), SPEC_TRIGGERS)

    def test_behavioural_indexes_exist(self) -> None:
        present = set(db.object_inventory(self.conn)["index"])
        self.assertTrue(SPEC_INDEXES <= present, SPEC_INDEXES - present)

    def test_company_profile_is_queryable_and_empty(self) -> None:
        self.assertEqual(
            self.conn.execute("SELECT * FROM company_profile").fetchall(), []
        )

    def test_company_profile_exposes_a_row_per_company(self) -> None:
        """The view LEFT JOINs, so a company with no signals still appears."""
        self.add_company()
        rows = self.conn.execute("SELECT * FROM company_profile").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["domain"], "example.de")
        self.assertIsNone(rows[0]["platform"])

    def test_needs_review_reason_column_is_gone(self) -> None:
        """B2: the reason moved to review_flag."""
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(company)")
        }
        self.assertNotIn("needs_review_reason", columns)
        self.assertIn("needs_review", columns)


class TestReviewFlag(SchemaTestCase):
    """B2 semantics: co-occurring reasons, idempotent raising, maintained boolean."""

    def raise_flag(self, company_id: int, run_id: int, reason: str) -> None:
        """The documented write idiom: DO NOTHING on the uniqueness conflict
        only, so a bad `reason` still raises rather than vanishing."""
        self.conn.execute(
            "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at) "
            "VALUES (?,?,?,?) ON CONFLICT (company_id, reason) DO NOTHING",
            (company_id, reason, run_id, NOW),
        )

    def test_three_reasons_co_occur_on_one_company(self) -> None:
        company_id, run_id = self.add_company(), self.add_run()
        for reason in (
            "no_impressum",
            "possible_marketplace_only",
            "blog_date_unparseable",
        ):
            self.raise_flag(company_id, run_id, reason)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM review_flag WHERE company_id = ?", (company_id,)
        ).fetchone()[0]
        self.assertEqual(count, 3)

    def test_re_raising_is_idempotent(self) -> None:
        company_id, run_id = self.add_company(), self.add_run()
        self.raise_flag(company_id, run_id, "no_impressum")
        self.raise_flag(company_id, self.add_run(), "no_impressum")
        count = self.conn.execute("SELECT COUNT(*) FROM review_flag").fetchone()[0]
        self.assertEqual(count, 1)

    def test_unknown_reason_is_rejected(self) -> None:
        """A misspelled reason must raise, not become a silent fourth category.
        This is why the idiom is ON CONFLICT DO NOTHING and not OR IGNORE —
        the latter would swallow the CHECK violation as well."""
        company_id, run_id = self.add_company(), self.add_run()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raise_flag(company_id, run_id, "no_imprssum")

        # Pinning the trap itself: OR IGNORE neither raises nor writes.
        self.conn.execute(
            "INSERT OR IGNORE INTO review_flag "
            "(company_id, reason, raised_run_id, raised_at) VALUES (?,?,?,?)",
            (company_id, "no_imprssum", run_id, NOW),
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM review_flag").fetchone()[0], 0
        )

    def test_raising_sets_the_boolean(self) -> None:
        company_id, run_id = self.add_company(), self.add_run()
        self.assertEqual(self.needs_review(company_id), 0)
        self.raise_flag(company_id, run_id, "no_impressum")
        self.assertEqual(self.needs_review(company_id), 1)

    def test_boolean_clears_only_when_the_last_flag_resolves(self) -> None:
        company_id, run_id = self.add_company(), self.add_run()
        self.raise_flag(company_id, run_id, "no_impressum")
        self.raise_flag(company_id, run_id, "blog_date_unparseable")

        self.conn.execute(
            "UPDATE review_flag SET resolved_at = ?, resolved_by_human = 1 "
            "WHERE company_id = ? AND reason = 'no_impressum'",
            (NOW, company_id),
        )
        self.assertEqual(self.needs_review(company_id), 1)

        self.conn.execute(
            "UPDATE review_flag SET resolved_at = ?, resolved_by_human = 1 "
            "WHERE company_id = ? AND reason = 'blog_date_unparseable'",
            (NOW, company_id),
        )
        self.assertEqual(self.needs_review(company_id), 0)

    def test_resolution_is_sticky(self) -> None:
        """A later run re-detecting the same condition must not re-raise it."""
        company_id, run_id = self.add_company(), self.add_run()
        self.raise_flag(company_id, run_id, "no_impressum")
        self.conn.execute(
            "UPDATE review_flag SET resolved_at = ?, resolved_by_human = 1", (NOW,)
        )
        self.raise_flag(company_id, self.add_run(), "no_impressum")
        self.assertEqual(self.needs_review(company_id), 0)

    def test_resolved_pair_must_be_consistent(self) -> None:
        """resolved_at without resolved_by_human loses the distinction the
        soft tier exists for: not-yet-reviewed vs reviewed-and-dismissed."""
        company_id, run_id = self.add_company(), self.add_run()
        self.raise_flag(company_id, run_id, "no_impressum")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE review_flag SET resolved_at = ?", (NOW,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE review_flag SET resolved_by_human = 1")

    def test_deleting_flags_clears_the_boolean(self) -> None:
        company_id, run_id = self.add_company(), self.add_run()
        self.raise_flag(company_id, run_id, "no_impressum")
        self.conn.execute("DELETE FROM review_flag WHERE company_id = ?", (company_id,))
        self.assertEqual(self.needs_review(company_id), 0)


class TestConstraints(SchemaTestCase):
    def test_forget_cascades_across_tables(self) -> None:
        """§8's `portal forget` leans on ON DELETE CASCADE."""
        company_id, run_id = self.add_company(), self.add_run()
        self.conn.execute(
            "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at) "
            "VALUES (?,?,?,?)",
            (company_id, "no_impressum", run_id, NOW),
        )
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, method, evidence_url, observed_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                company_id,
                run_id,
                "content.blog_exists",
                "deterministic",
                "https://x/",
                NOW,
            ),
        )
        self.conn.execute(
            "INSERT INTO contact (company_id, source_url, collected_at, purge_after) "
            "VALUES (?,?,?,?)",
            (company_id, "https://x/impressum", NOW, NOW),
        )

        self.conn.execute("DELETE FROM company WHERE id = ?", (company_id,))
        for table in ("review_flag", "signal", "contact"):
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)

    def test_signal_identity_is_unique_within_a_run(self) -> None:
        """B4 rests on this: reconcile writing under the submitting run dedupes."""
        company_id, run_id = self.add_company(), self.add_run()
        row = (
            company_id,
            run_id,
            "impressum.gf_count",
            "llm",
            "https://x/impressum",
            NOW,
        )
        sql = (
            "INSERT OR IGNORE INTO signal "
            "(company_id, run_id, key, method, evidence_url, observed_at) VALUES (?,?,?,?,?,?)"
        )
        self.conn.execute(sql, row)
        self.conn.execute(sql, row)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0], 1
        )

    def test_outreach_channel_excludes_email(self) -> None:
        """§8: no outbound email capability, enforced in the schema."""
        company_id = self.add_company()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO outreach (company_id, channel, occurred_at) VALUES (?,?,?)",
                (company_id, "email", NOW),
            )

    def test_domain_is_unique(self) -> None:
        self.add_company("example.de")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_company("example.de")


if __name__ == "__main__":
    unittest.main()
