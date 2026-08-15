"""`portal diff-signals` (M1.28).

The three defects this command exists for are named in `portal/diff.py`. The
tests here pin the two properties that make it useful for them: a value that
*changed* is reported with both sides, and a key that stopped being written is
reported at all — the second being the case a test suite structurally misses,
because nothing asserts the absence of a signal nobody thought to remove.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portal import cli, db, diff, migrate


class DiffTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "portal.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.company_id = int(
            self.conn.execute(
                "INSERT INTO company (domain, discovery_source, discovered_at) "
                "VALUES ('muster.de', 'seed_csv', '2026-08-15T00:00:00Z')"
            ).lastrowid
        )

    def run_with(self, signals: dict[str, object], stage: str = "extract-p1") -> int:
        run_id = int(
            self.conn.execute(
                "INSERT INTO run (started_at, stage) VALUES ('2026-08-15T00:00:00Z', ?)",
                (stage,),
            ).lastrowid
        )
        for key, value in signals.items():
            num = value if isinstance(value, (int, float)) else None
            text = value if isinstance(value, str) else None
            self.conn.execute(
                "INSERT INTO signal (company_id, run_id, key, value_num, value_text, "
                "method, evidence_url, observed_at) "
                "VALUES (?,?,?,?,?, 'deterministic', 'https://muster.de/', ?)",
                (self.company_id, run_id, key, num, text, "2026-08-15T00:00:00Z"),
            )
        self.conn.commit()
        return run_id

    def changes(self, before: int, after: int) -> list[diff.Change]:
        return diff.compare(
            diff.snapshot(self.conn, before), diff.snapshot(self.conn, after)
        )


class TestSignalDiff(DiffTestCase):
    def test_a_changed_value_reports_both_sides(self) -> None:
        """The `<image:loc>` shape: a count that halved still looks like a
        count, and is a defect only against what it was."""
        before = self.run_with({"catalog.product_url_count": 613})
        after = self.run_with({"catalog.product_url_count": 306})
        (change,) = self.changes(before, after)
        self.assertEqual(
            (change.kind, change.before, change.after), ("changed", "613", "306")
        )

    def test_a_key_that_stopped_being_written_is_reported(self) -> None:
        """The gzip shape: the catalogue signal vanished and a plausible
        `catalog.not_measurable` took its place, so nothing looked wrong."""
        before = self.run_with({"catalog.product_url_count": 682})
        after = self.run_with({"catalog.not_measurable": 1})
        kinds = {change.key: change.kind for change in self.changes(before, after)}
        self.assertEqual(
            kinds,
            {
                "catalog.product_url_count": "disappeared",
                "catalog.not_measurable": "appeared",
            },
        )

    def test_a_flipped_boolean_is_a_change_not_a_silence(self) -> None:
        """The snocks.com shape: `blog_exists` 1 → 0 is `opp.no_blog`'s +25
        against a shop that publishes weekly, and it is one digit."""
        before = self.run_with({"content.blog_exists": 1})
        after = self.run_with({"content.blog_exists": 0})
        (change,) = self.changes(before, after)
        self.assertEqual((change.before, change.after), ("1", "0"))

    def test_an_unchanged_run_reports_nothing(self) -> None:
        """Anti-vacuity, and the property that makes the command readable: a
        re-run over unchanged inputs must be silent, or nobody will read it."""
        signals = {"catalog.product_url_count": 306, "platform.detected": "Shopify"}
        self.assertEqual(
            self.changes(self.run_with(signals), self.run_with(signals)), []
        )

    def test_an_integral_count_does_not_drift_on_storage_type(self) -> None:
        """`value_num` is REAL, so 306 and 306.0 are the same measurement. A
        diff that reported it would train its reader to skim."""
        self.assertEqual(
            self.changes(
                self.run_with({"catalog.product_url_count": 306}),
                self.run_with({"catalog.product_url_count": 306.0}),
            ),
            [],
        )

    def test_the_report_names_the_domain_and_both_runs(self) -> None:
        before = self.run_with({"catalog.product_url_count": 6})
        after = self.run_with({"catalog.product_url_count": 194})
        text = diff.report(self.changes(before, after), before, after)
        self.assertIn("muster.de", text)
        self.assertIn(f"run {before} → run {after}", text)
        self.assertIn("6 → 194", text)

    def test_no_differences_says_so_rather_than_printing_nothing(self) -> None:
        self.assertIn("no differences", diff.report([], 1, 2))


class TestRunSelection(DiffTestCase):
    def test_a_run_that_wrote_no_signals_is_not_a_comparison_point(self) -> None:
        """Diffing against one would report every signal as disappeared."""
        self.run_with({"catalog.product_url_count": 306})
        self.run_with({})  # a fetch that wrote nothing, or a crashed stage
        self.assertEqual(len(diff.runs(self.conn, "extract-p1")), 1)

    def test_runs_are_newest_first_so_the_default_pair_is_the_last_two(self) -> None:
        first = self.run_with({"a": 1})
        second = self.run_with({"a": 2})
        self.assertEqual(
            [int(row["id"]) for row in diff.runs(self.conn)][:2], [second, first]
        )

    def test_the_stage_filter_keeps_fetch_runs_out_of_an_extract_diff(self) -> None:
        self.run_with(
            {"catalog.product_sample_url": "https://muster.de/x"}, stage="fetch"
        )
        self.run_with({"catalog.product_url_count": 1})
        self.assertEqual(len(diff.runs(self.conn, "extract-p1")), 1)
        self.assertEqual(len(diff.runs(self.conn, None)), 2)


class TestDiffCli(DiffTestCase):
    def test_defaults_to_the_last_two_runs_of_a_stage(self) -> None:
        self.run_with({"catalog.product_url_count": 6})
        self.run_with({"catalog.product_url_count": 194})
        self.assertEqual(cli.main(["--db", str(self.path), "diff-signals"]), 0)

    def test_refuses_rather_than_guesses_when_there_is_only_one_run(self) -> None:
        self.run_with({"catalog.product_url_count": 6})
        self.assertEqual(cli.main(["--db", str(self.path), "diff-signals"]), 2)


if __name__ == "__main__":
    unittest.main()
