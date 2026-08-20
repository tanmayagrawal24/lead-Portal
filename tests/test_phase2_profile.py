"""The Phase-2 read model: migration 011's columns and migration 012's filter.

Two guarantees are under test here and they are deliberately in one file,
because the second one is only safe *because* of the first. A4's confidence
filter turns a rejected extraction into an absent column; without A2's stage
facts surviving that filter, "the tool read the page and rejected the answer"
and "Phase 2 never ran here" would be one state, and §6.1's three-state
predicates would have nothing to read.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portal import db, migrate

NOW = "2026-08-20T12:00:00Z"


class ProfileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.connect(Path(self._tmp.name) / "t.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def company(self, domain: str = "muster.de", legal_form: str | None = None) -> int:
        return int(
            self.conn.execute(
                "INSERT INTO company (domain, discovery_source, discovered_at, "
                "legal_form) VALUES (?,'seed_csv',?,?)",
                (domain, NOW, legal_form),
            ).lastrowid
        )

    def new_run(self, stage: str = "extract_p2", finished: bool = True) -> int:
        return int(
            self.conn.execute(
                "INSERT INTO run (started_at, stage, finished_at) VALUES (?,?,?)",
                (NOW, stage, NOW if finished else None),
            ).lastrowid
        )

    def signal(
        self,
        company_id: int,
        run_id: int,
        key: str,
        *,
        num: float | None = None,
        text: str | None = None,
        method: str = "llm",
        confidence: float | None = 1.0,
        evidence: str = "https://muster.de/",
    ) -> None:
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, value_text, "
            "method, confidence, evidence_url, observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (company_id, run_id, key, num, text, method, confidence, evidence, NOW),
        )

    def profile(self, company_id: int) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT * FROM company_profile WHERE company_id = ?", (company_id,)
        ).fetchone()


class TestPhase2Columns(ProfileTestCase):
    """Migration 011 — A2's mapping, as columns."""

    def test_the_phase2_columns_exist(self) -> None:
        row = self.profile(self.company())
        for column in (
            "own_brand",
            "homepage_extracted",
            "impressum_extracted",
            "one_line_offer",
            "owner_named",
            "gf_count",
        ):
            with self.subTest(column=column):
                self.assertIn(column, row.keys())

    def test_own_brand_reaches_the_view_from_its_key(self) -> None:
        """`qual.own_brand` was a live +10 rule with `reads=()` because this
        path did not exist (M1.76)."""
        company_id, run_id = self.company(), self.new_run()
        self.signal(company_id, run_id, "brand.own_brand", num=1)
        self.assertEqual(self.profile(company_id)["own_brand"], 1)

    def test_owner_named_reads_the_site_key_not_the_impressum_one(self) -> None:
        """A2 item 3. The value is read off the homepage; a key prefixed
        `impressum.` would assert a provenance it does not have (M1.42)."""
        company_id, run_id = self.company(), self.new_run()
        self.signal(company_id, run_id, "impressum.owner_named", num=1)
        self.assertIsNone(self.profile(company_id)["owner_named"])
        self.signal(company_id, run_id, "site.owner_named", num=1)
        self.assertEqual(self.profile(company_id)["owner_named"], 1)

    def test_the_llm_legal_form_wins_over_the_column(self) -> None:
        """A2 item 8: §5.5b rules the LLM wins, and the view is where that is
        one expression rather than a race between two UPDATEs."""
        company_id = self.company(legal_form="GmbH")
        self.assertEqual(self.profile(company_id)["legal_form"], "GmbH")
        run_id = self.new_run()
        self.signal(company_id, run_id, "impressum.legal_form", text="e.K.")
        self.assertEqual(self.profile(company_id)["legal_form"], "e.K.")

    def test_the_column_survives_when_the_llm_says_nothing(self) -> None:
        """COALESCE, not replacement: extract-p1's value is not blanked by a
        Phase 2 that did not reach this field."""
        company_id = self.company(legal_form="GmbH")
        self.signal(company_id, self.new_run(), "llm.impressum_extracted", num=1)
        self.assertEqual(self.profile(company_id)["legal_form"], "GmbH")

    def test_the_demoted_agency_hint_has_no_column(self) -> None:
        """M1.77. Giving `agency.footer_credit_llm` a view column is the first
        half of giving it a reader on a −20 rule."""
        row = self.profile(self.company())
        self.assertNotIn("agency_credit_llm", row.keys())
        self.assertNotIn("footer_credit_llm", row.keys())


class TestConfidenceFilter(ProfileTestCase):
    """Migration 012 — A4. `confidence = 0` is §5.5b's record of a value the
    tool verified and does not believe."""

    def test_a_rejected_value_does_not_reach_the_read_model(self) -> None:
        company_id, run_id = self.company(), self.new_run()
        self.signal(company_id, run_id, "brand.own_brand", num=1, confidence=0.0)
        self.assertIsNone(self.profile(company_id)["own_brand"])

    def test_a_believed_value_does(self) -> None:
        company_id, run_id = self.company(), self.new_run()
        self.signal(company_id, run_id, "brand.own_brand", num=1, confidence=0.4)
        self.assertEqual(self.profile(company_id)["own_brand"], 1)

    def test_deterministic_signals_are_untouched(self) -> None:
        """`confidence` is NULL for every deterministic signal (§4). A filter
        that excluded NULL would blank Phase 1 entirely — all 2,404 rows in the
        stored corpus."""
        company_id, run_id = self.company(), self.new_run("extract_p1")
        self.signal(
            company_id,
            run_id,
            "platform.detected",
            text="JTL",
            method="deterministic",
            confidence=None,
        )
        self.assertEqual(self.profile(company_id)["platform"], "JTL")

    def test_the_stage_fact_survives_a_rejected_extraction(self) -> None:
        """The interlock. Without this, a rejection and a never-run Phase 2 are
        one state and §6.1's three-state predicates cannot tell them apart."""
        company_id, run_id = self.company(), self.new_run()
        self.signal(company_id, run_id, "brand.own_brand", num=1, confidence=0.0)
        self.signal(company_id, run_id, "llm.homepage_extracted", num=1)
        row = self.profile(company_id)
        self.assertIsNone(row["own_brand"])
        self.assertEqual(row["homepage_extracted"], 1)

    def test_a_run_of_only_rejected_values_stays_its_stages_authority(self) -> None:
        """**The placement test.** Filtering inside `observed` would drop this
        run out of `current_run`, and run 1's superseded value would be served
        as current — M1.36, re-created by the guard meant to strengthen it.

        Run 1 believed the shop was an own-brand manufacturer. Run 2 looked
        again, could not verify it, and wrote `confidence = 0`. The correct
        answer is that the profile knows nothing, not that it knows run 1's
        retracted value.
        """
        company_id = self.company()
        first, second = self.new_run(), self.new_run()
        self.signal(company_id, first, "brand.own_brand", num=1)
        self.signal(company_id, second, "brand.own_brand", num=1, confidence=0.0)
        self.assertIsNone(self.profile(company_id)["own_brand"])

    def test_an_unfinished_run_is_still_ignored_wholesale(self) -> None:
        """Migration 007's guarantee, re-checked after the view was rebuilt
        twice: a rebuild is where a property gets dropped by omission."""
        company_id = self.company()
        finished, crashed = self.new_run(), self.new_run(finished=False)
        self.signal(company_id, finished, "brand.own_brand", num=1)
        self.signal(company_id, crashed, "brand.own_brand", num=0)
        self.assertEqual(self.profile(company_id)["own_brand"], 1)


if __name__ == "__main__":
    unittest.main()
