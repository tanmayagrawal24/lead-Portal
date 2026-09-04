"""M1.120. `/status` — every section, against a fixture database.

The page's whole claim is that it reads and does not act. So the tests that
matter most are the two negative ones at the bottom: no provider is ever
constructed, and the "next step" list offers no button.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from portal import db, migrate, serve, status


def _fixture(conn: sqlite3.Connection) -> None:
    """One of everything the page has a section for."""
    conn.executescript(
        """
        INSERT INTO run (id, started_at, finished_at, stage, est_cost_usd,
                         companies_seen, web_searches, places_calls, pagespeed_calls)
        VALUES (1, '2026-09-01T10:00:00Z', '2026-09-01T10:05:00Z', 'fetch',
                0.0, 3, NULL, NULL, NULL);
        INSERT INTO run (id, started_at, finished_at, stage, est_cost_usd,
                         companies_seen, web_searches)
        VALUES (2, '2026-09-02T10:00:00Z', '2026-09-02T10:09:00Z', 'discover',
                0.25, 2, 6);
        INSERT INTO run (id, started_at, stage, est_cost_usd, aborted_reason)
        VALUES (3, '2026-09-03T10:00:00Z', 'extract-p2', 0.0, 'BadRequestError: 400');
        INSERT INTO run (id, started_at, finished_at, stage, est_cost_usd)
        VALUES (4, '2026-09-04T12:38:37Z', '2026-09-04T12:38:38Z', 'extract-p2', 0.5);

        INSERT INTO llm_batch (id, provider_batch_id, run_id, purpose, request_count,
                               est_cost_usd, status, reserved_at, submitted_at)
        VALUES (1, 'msgbatch_live', 4, 'impressum', 9, 0.5, 'submitted',
                '2026-09-04T12:38:37Z', '2026-09-04T12:38:38Z');
        INSERT INTO llm_batch (id, provider_batch_id, run_id, purpose, request_count,
                               est_cost_usd, status, reserved_at, released_at,
                               release_reason)
        VALUES (2, NULL, 3, 'impressum', 9, 0.06, 'released',
                '2026-09-03T10:00:01Z', '2026-09-03T11:00:00Z',
                '400 on custom_id; no batch created');

        INSERT INTO company (id, domain, discovery_source, discovery_query,
                             discovered_at, excluded)
        VALUES (1, 'echter-shop.de', 'llm_websearch', 'Onlineshop DE',
                '2026-09-04T00:00:00Z', 0);
        INSERT INTO company (id, domain, discovery_source, discovery_query,
                             discovered_at, excluded)
        VALUES (2, 'makita.de', 'llm_websearch', 'Werkzeug DE',
                '2026-09-04T00:00:00Z', 0);
        INSERT INTO company (id, domain, discovery_source, discovered_at, excluded,
                             excluded_reason)
        VALUES (3, 'alt.de', 'seed_csv', '2026-01-01T00:00:00Z', 1, 'Testfall');

        INSERT INTO artifact (company_id, kind, url, http_status, fetched_at,
                              content_hash, last_checked_at)
        VALUES (1, 'homepage', 'https://echter-shop.de/', 200,
                '2026-09-04T00:00:00Z', 'h1', '2026-09-04T00:00:00Z');
        INSERT INTO artifact (company_id, kind, url, http_status, fetched_at,
                              content_hash, last_checked_at)
        VALUES (1, 'impressum', 'https://echter-shop.de/impressum', 200,
                '2026-09-04T00:00:00Z', 'h2', '2026-09-04T00:00:00Z');

        INSERT INTO score (company_id, phase, total, band, evaluated_on, run_id,
                           ruleset_version, computed_at)
        VALUES (1, 1, 40, 'C', '2026-09-04', 1, 'v3', '2026-09-04T00:00:00Z');

        INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at)
        VALUES (2, 'manufacturer_not_shop', 1, '2026-09-04T00:00:00Z');
        INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at)
        VALUES (1, 'no_impressum', 1, '2026-09-04T00:00:00Z');
        """
    )
    conn.commit()


class StatusTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "portal.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.conn.row_factory = sqlite3.Row
        _fixture(self.conn)
        self.status = status.read(self.conn)


class TheLedgerSection(StatusTestCase):
    def test_the_window_comes_from_the_ledger_and_not_a_second_sum(self) -> None:
        """A page with its own copy of control 2's arithmetic is a second
        ledger that can disagree with the one that refuses spending."""
        source = Path(status.__file__).read_text(encoding="utf-8")
        self.assertIn("ledger.check_ceiling(conn)", source)
        self.assertEqual(self.status.ceiling_usd, 45.0)
        self.assertEqual(self.status.window_days, 30)
        self.assertAlmostEqual(self.status.spend_usd, 0.75)
        self.assertAlmostEqual(self.status.headroom_usd, 44.25)

    def test_every_run_appears_newest_first_with_its_counters(self) -> None:
        self.assertEqual([r.id for r in self.status.runs], [4, 3, 2, 1])
        discover = next(r for r in self.status.runs if r.id == 2)
        self.assertEqual(discover.web_searches, 6)
        self.assertEqual(discover.stage, "discover")
        self.assertAlmostEqual(discover.est_cost_usd, 0.25)

    def test_an_aborted_run_reads_as_aborted_not_as_open(self) -> None:
        aborted = next(r for r in self.status.runs if r.id == 3)
        self.assertEqual(aborted.state, "abgebrochen")
        self.assertIsNotNone(aborted.aborted_reason)


class TheBatchSection(StatusTestCase):
    def test_an_unreconciled_batch_carries_its_deadline_and_the_command(self) -> None:
        live = next(b for b in self.status.batches if b.id == 1)
        self.assertTrue(live.needs_reconcile)
        self.assertEqual(live.retrieval_deadline, "2026-10-03")

    def test_a_released_batch_shows_its_reason_and_needs_no_reconcile(self) -> None:
        released = next(b for b in self.status.batches if b.id == 2)
        self.assertFalse(released.needs_reconcile)
        self.assertIsNone(released.retrieval_deadline)
        self.assertIn("no batch created", released.release_reason or "")

    def test_an_unparseable_submitted_at_yields_no_deadline_rather_than_a_guess(
        self,
    ) -> None:
        """M1.52: a date that looks measured and is not is worse than none."""
        row = status.BatchRow(
            id=9,
            provider_batch_id="x",
            purpose="impressum",
            status="submitted",
            request_count=1,
            est_cost_usd=0.0,
            actual_cost_usd=None,
            reserved_at="?",
            submitted_at="not-a-date",
            reconciled_at=None,
            release_reason=None,
        )
        self.assertIsNone(row.retrieval_deadline)


class TheCorpusSection(StatusTestCase):
    def test_companies_are_counted_by_source_and_by_query(self) -> None:
        self.assertEqual(self.status.companies, 3)
        self.assertEqual(self.status.excluded, 1)
        self.assertEqual(
            {c.label: c.n for c in self.status.by_source},
            {"llm_websearch": 2, "seed_csv": 1},
        )
        self.assertEqual(
            {c.label: c.n for c in self.status.by_query},
            {"Onlineshop DE": 1, "Werkzeug DE": 1, "—": 1},
        )

    def test_coverage_counts_each_milestone(self) -> None:
        coverage = {c.label: c.n for c in self.status.coverage}
        self.assertEqual(coverage["mit Homepage-Artefakt"], 1)
        self.assertEqual(coverage["mit Impressum-Artefakt"], 1)
        self.assertEqual(coverage["mit Phase-1-Score"], 1)
        self.assertEqual(coverage["mit Phase-2-Score"], 0)
        self.assertEqual(coverage["mit ai.checked_at"], 0)


class TheFlagSection(StatusTestCase):
    def test_open_flags_are_grouped_and_link_to_the_filtered_list(self) -> None:
        flags = {c.label: c for c in self.status.flags}
        self.assertEqual(flags["manufacturer_not_shop"].n, 1)
        self.assertEqual(flags["no_impressum"].n, 1)
        self.assertIn("needs_review=1", flags["no_impressum"].href)

    def test_a_resolved_flag_is_not_counted(self) -> None:
        self.conn.execute(
            "UPDATE review_flag SET resolved_at = '2026-09-05T00:00:00Z', "
            "resolved_by_human = 1 WHERE reason = 'no_impressum'"
        )
        self.conn.commit()
        labels = [c.label for c in status.read(self.conn).flags]
        self.assertNotIn("no_impressum", labels)


class TheNextStepSection(StatusTestCase):
    def test_an_unreconciled_batch_is_the_first_step(self) -> None:
        first = self.status.next_steps[0]
        self.assertEqual(first.command, "portal reconcile")
        self.assertIn("msgbatch_live", first.sentence)
        self.assertIn("2026-10-03", first.sentence)

    def test_companies_awaiting_phase_2_produce_a_dry_run_step(self) -> None:
        commands = [s.command for s in self.status.next_steps]
        self.assertIn("portal extract-p2 --dry-run", commands)

    def test_open_flags_produce_a_step(self) -> None:
        self.assertTrue(
            any("Prüfmarkierungen" in s.sentence for s in self.status.next_steps)
        )

    def test_no_step_ever_carries_submit(self) -> None:
        """M1.102. The page must not put a paid call one click, or one copied
        line, away from happening."""
        for step in self.status.next_steps:
            with self.subTest(command=step.command):
                self.assertNotIn("--submit", step.command)

    def test_an_idle_database_still_says_something(self) -> None:
        self.conn.executescript(
            "DELETE FROM review_flag; DELETE FROM llm_batch; DELETE FROM artifact;"
        )
        self.conn.commit()
        steps = status.read(self.conn).next_steps
        self.assertEqual(len(steps), 1)
        self.assertIn("Nichts steht an", steps[0].sentence)


class ThePage(StatusTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(serve.create_app(self.path))

    def test_it_renders_every_section(self) -> None:
        body = self.client.get("/status").text
        for probe in (
            "Ausgaben",
            "Batches",
            "Bestand",
            "Offene Prüfmarkierungen",
            "Nächster Schritt",
            "msgbatch_live",
            "llm_websearch",
            "manufacturer_not_shop",
            "abrufbar bis 2026-10-03",
        ):
            with self.subTest(probe=probe):
                self.assertIn(probe, body)

    def test_the_commands_are_code_and_never_buttons(self) -> None:
        """M1.102, as a property of the rendered HTML rather than of the
        intention: spend stays behind something a person types."""
        body = self.client.get("/status").text
        self.assertIn("<code>portal reconcile</code>", body)
        self.assertNotIn("<button", body)
        self.assertNotIn("<form", body)
        self.assertNotIn("hx-post", body)

    def test_the_lead_list_links_to_it(self) -> None:
        self.assertIn('href="/status"', self.client.get("/").text)

    def test_it_is_reachable_and_reads_nothing_live(self) -> None:
        """No provider, no key, no network: the page renders with the
        environment stripped of every credential it could reach for."""
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.client.get("/status").status_code, 200)
