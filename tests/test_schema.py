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

    def desynced_companies(self) -> list[int]:
        """Companies where the cached boolean disagrees with the flag rows."""
        return [
            int(row[0])
            for row in self.conn.execute(
                """
                SELECT c.id FROM company c
                WHERE c.needs_review <> EXISTS (
                    SELECT 1 FROM review_flag f
                    WHERE f.company_id = c.id AND f.resolved_at IS NULL
                )
                """
            )
        ]

    def assert_cache_is_consistent(self) -> None:
        self.assertEqual(
            self.desynced_companies(),
            [],
            "company.needs_review disagrees with review_flag for these companies",
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

    def test_resolved_by_human_is_constrained_to_zero_or_one(self) -> None:
        company_id, run_id = self.add_company(), self.add_run()
        self.raise_flag(company_id, run_id, "no_impressum")
        for bad in (2, -1, 7):
            with self.assertRaises(sqlite3.IntegrityError, msg=f"accepted {bad}"):
                self.conn.execute(
                    "UPDATE review_flag SET resolved_at = ?, resolved_by_human = ?",
                    (NOW, bad),
                )
        for good in (0, 1):
            self.conn.execute(
                "UPDATE review_flag SET resolved_at = ?, resolved_by_human = ?",
                (NOW, good),
            )

    def test_moving_a_flag_between_companies_updates_both(self) -> None:
        """The UPDATE trigger recomputes OLD.company_id as well as NEW's, so
        the company a flag left behind does not stay marked."""
        source, target, run_id = (
            self.add_company("source.de"),
            self.add_company("target.de"),
            self.add_run(),
        )
        self.raise_flag(source, run_id, "no_impressum")
        self.assertEqual((self.needs_review(source), self.needs_review(target)), (1, 0))

        self.conn.execute(
            "UPDATE review_flag SET company_id = ? WHERE company_id = ?",
            (target, source),
        )
        self.assertEqual((self.needs_review(source), self.needs_review(target)), (0, 1))
        self.assert_cache_is_consistent()

    def test_deleting_flags_clears_the_boolean(self) -> None:
        company_id, run_id = self.add_company(), self.add_run()
        self.raise_flag(company_id, run_id, "no_impressum")
        self.conn.execute("DELETE FROM review_flag WHERE company_id = ?", (company_id,))
        self.assertEqual(self.needs_review(company_id), 0)


class TestNeedsReviewInvariant(SchemaTestCase):
    """`company.needs_review` == "has an unresolved review_flag", for every
    company, after any sequence of flag writes.

    The schema comment claims one write path; the triggers are what make that
    true, and this is what checks they do. Note the residual gap pinned at the
    bottom: nothing stops a direct UPDATE on company from breaking it.
    """

    def raise_flag(self, company_id: int, run_id: int, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at) "
            "VALUES (?,?,?,?) ON CONFLICT (company_id, reason) DO NOTHING",
            (company_id, reason, run_id, NOW),
        )

    def resolve(self, company_id: int, reason: str, by_human: int = 1) -> None:
        self.conn.execute(
            "UPDATE review_flag SET resolved_at = ?, resolved_by_human = ? "
            "WHERE company_id = ? AND reason = ?",
            (NOW, by_human, company_id, reason),
        )

    def test_invariant_holds_across_a_mixed_population(self) -> None:
        run_id = self.add_run()

        untouched = self.add_company("untouched.de")
        one_open = self.add_company("one-open.de")
        all_resolved = self.add_company("all-resolved.de")
        partly_resolved = self.add_company("partly-resolved.de")
        flags_deleted = self.add_company("flags-deleted.de")

        self.raise_flag(one_open, run_id, "no_impressum")

        self.raise_flag(all_resolved, run_id, "no_impressum")
        self.raise_flag(all_resolved, run_id, "blog_date_unparseable")
        self.resolve(all_resolved, "no_impressum")
        self.resolve(all_resolved, "blog_date_unparseable", by_human=0)

        self.raise_flag(partly_resolved, run_id, "no_impressum")
        self.raise_flag(partly_resolved, run_id, "possible_marketplace_only")
        self.resolve(partly_resolved, "no_impressum")

        self.raise_flag(flags_deleted, run_id, "no_impressum")
        self.conn.execute(
            "DELETE FROM review_flag WHERE company_id = ?", (flags_deleted,)
        )

        self.assert_cache_is_consistent()
        self.assertEqual(
            [
                self.needs_review(c)
                for c in (
                    untouched,
                    one_open,
                    all_resolved,
                    partly_resolved,
                    flags_deleted,
                )
            ],
            [0, 1, 0, 1, 0],
        )

    def test_invariant_survives_re_raising_and_cascade_delete(self) -> None:
        run_id = self.add_run()
        kept = self.add_company("kept.de")
        dropped = self.add_company("dropped.de")

        for reason in ("no_impressum", "possible_marketplace_only"):
            self.raise_flag(kept, run_id, reason)
            self.raise_flag(dropped, run_id, reason)
        self.raise_flag(kept, run_id, "no_impressum")  # re-raise, must be a no-op
        self.assert_cache_is_consistent()

        self.conn.execute("DELETE FROM company WHERE id = ?", (dropped,))
        self.assert_cache_is_consistent()
        self.assertEqual(self.needs_review(kept), 1)

    def test_direct_write_to_company_desyncs_the_cache(self) -> None:
        """The known gap, pinned rather than assumed away: the invariant holds
        because every writer goes through review_flag. Nothing in the schema
        enforces that, so `needs_review` must never be written directly."""
        company_id = self.add_company()
        self.conn.execute(
            "UPDATE company SET needs_review = 1 WHERE id = ?", (company_id,)
        )
        self.assertEqual(self.desynced_companies(), [company_id])


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

    #: §4's signal idiom. Targeted at the uniqueness conflict and nothing else,
    #: so a CHECK violation still raises.
    SIGNAL_INSERT = (
        "INSERT INTO signal "
        "(company_id, run_id, key, method, evidence_url, observed_at) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING"
    )

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
        self.conn.execute(self.SIGNAL_INSERT, row)
        self.conn.execute(self.SIGNAL_INSERT, row)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0], 1
        )

    def test_an_unknown_method_raises_rather_than_vanishing(self) -> None:
        """The same trap `review_flag` already avoids, now closed for `signal`.

        `INSERT OR IGNORE` suppresses CHECK violations as well as uniqueness
        conflicts, so a typo'd or renamed `method` would be dropped in silence —
        a signal that was never written, indistinguishable from one that was
        never observed. The targeted DO NOTHING dedupes and nothing more.
        """
        company_id, run_id = self.add_company(), self.add_run()
        row = (
            company_id,
            run_id,
            "impressum.gf_count",
            "LLM",  # the CHECK is exact: 'deterministic' or 'llm'
            "https://x/impressum",
            NOW,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(self.SIGNAL_INSERT, row)

        # Pinning the trap itself, as `review_flag` does: OR IGNORE would
        # neither raise nor write.
        self.conn.execute(
            "INSERT OR IGNORE INTO signal "
            "(company_id, run_id, key, method, evidence_url, observed_at) "
            "VALUES (?,?,?,?,?,?)",
            row,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0], 0
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
