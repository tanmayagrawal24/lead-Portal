"""§5.7 — `score --phase 2` warns loudly if unreconciled batches exist.

The sentence had been in the spec since v0.2 and nothing read it: `cmd_score`
accepted `--phase 2` and wrote a `phase=2` row over whatever `company_profile`
held, with no word about the batch still in flight for those companies. That
is a score a reader will copy into a brief before `reconcile` moves it.

Two things are pinned. The query answers on `reconciled_at` alone — §7 control
12(b) makes that the one column that says *the measured actual has been
written* — so a `reserved` batch whose submit call never returned an id is
still unreconciled, correctly. And Phase 1 is untouched: it reads no Phase-2
signal, so a batch in flight is not its business and it must not start
warning about it.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from portal import cli, db, migrate, score


class Phase2WarningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "portal.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def company(self, domain: str, *, excluded: int = 0) -> int:
        cur = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at, excluded) "
            "VALUES (?, 'seed_csv', '2026-08-01T00:00:00Z', ?)",
            (domain, excluded),
        )
        company_id = int(cur.lastrowid or 0)
        # One finished extract-p1 run with one signal so the company has a
        # profile row and `score` has something to rank.
        run = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES "
            "('2026-08-02T00:00:00Z', '2026-08-02T00:01:00Z', 'extract_p1')"
        )
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, method, "
            "evidence_url, observed_at) VALUES (?, ?, 'i18n.hreflang_count', 0, "
            "'deterministic', ?, '2026-08-02T00:00:30Z')",
            (company_id, run.lastrowid, f"https://{domain}/"),
        )
        return company_id

    def batch(
        self, *companies: int, status: str = "submitted", reconciled: bool = False
    ) -> int:
        run = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage, est_cost_usd) VALUES "
            "('2026-08-03T00:00:00Z', '2026-08-03T00:01:00Z', 'extract_p2', 0.12)"
        )
        cur = self.conn.execute(
            "INSERT INTO llm_batch (provider_batch_id, run_id, purpose, request_count, reserved_at, "
            "est_cost_usd, status, submitted_at, reconciled_at) VALUES "
            "(?, ?, 'impressum', ?, '2026-08-03T00:00:20Z', 0.12, ?, ?, ?)",
            (
                # A `reserved` batch has no provider id and no submit time yet
                # (migration 014's CHECK): the reservation is all there is.
                None if status == "reserved" else f"msgbatch_{status}_{run.lastrowid}",
                run.lastrowid,
                len(companies),
                status,
                None if status == "reserved" else "2026-08-03T00:00:30Z",
                "2026-08-04T00:00:00Z" if reconciled else None,
            ),
        )
        batch_id = int(cur.lastrowid or 0)
        for company_id in companies:
            artifact = self.conn.execute(
                "INSERT INTO artifact (company_id, kind, url, http_status, "
                "content_hash, body_path, fetched_at) VALUES (?, 'impressum', ?, 200, "
                "?, ?, 'x')",
                (
                    company_id,
                    f"https://c{company_id}/impressum",
                    f"h{company_id}{batch_id}",
                    f"{company_id}-{batch_id}.html",
                ),
            )
            self.conn.execute(
                "INSERT INTO llm_batch_request (batch_id, custom_id, company_id, "
                "artifact_id, sent_text_sha256, sent_bytes) VALUES (?, ?, ?, ?, 'sha', 10)",
                (
                    batch_id,
                    f"impressum:{company_id}:{artifact.lastrowid}",
                    company_id,
                    artifact.lastrowid,
                ),
            )
        return batch_id

    def run_cli(self, phase: int) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_score(self.db_path, phase=phase)
        return code, out.getvalue(), err.getvalue()

    # ── the query ────────────────────────────────────────────────────────

    def test_no_batches_means_nothing_pending(self) -> None:
        self.company("a.de")
        self.assertEqual(score.unreconciled_batches(self.conn), [])

    def test_an_open_batch_names_its_companies(self) -> None:
        a, b = self.company("a.de"), self.company("b.de")
        self.batch(a, b)
        pending = score.unreconciled_batches(self.conn)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].domains, ("a.de", "b.de"))
        self.assertEqual(pending[0].status, "submitted")
        self.assertEqual(pending[0].purpose, "impressum")

    def test_reconciled_at_is_the_only_thing_that_settles_it(self) -> None:
        """A `reserved` batch with no provider id is still unreconciled; a
        batch marked reconciled is not, whatever its status string says."""
        a = self.company("a.de")
        self.batch(a, status="reserved")
        self.batch(a, status="failed", reconciled=True)
        pending = score.unreconciled_batches(self.conn)
        self.assertEqual([p.status for p in pending], ["reserved"])

    def test_an_excluded_company_does_not_appear_in_the_warning(self) -> None:
        a = self.company("a.de")
        gone = self.company("gone.de", excluded=1)
        self.batch(a, gone)
        (pending,) = score.unreconciled_batches(self.conn)
        self.assertEqual(pending.domains, ("a.de",))

    # ── the CLI ──────────────────────────────────────────────────────────

    def test_phase_2_warns_on_stderr_and_still_scores(self) -> None:
        a = self.company("a.de")
        self.batch(a)
        code, out, err = self.run_cli(phase=2)
        self.assertEqual(code, 0)
        self.assertIn("NOT reconciled", err)
        self.assertIn("a.de", err)
        self.assertIn("provisional", err)
        self.assertIn("score --phase 2", out)
        # The score row was written: the warning is loud, not a refusal.
        row = self.conn.execute(
            "SELECT COUNT(*) FROM score WHERE phase = 2 AND company_id = ?", (a,)
        ).fetchone()
        self.assertEqual(row[0], 1)

    def test_phase_2_is_quiet_when_every_batch_is_reconciled(self) -> None:
        a = self.company("a.de")
        self.batch(a, status="reconciled", reconciled=True)
        code, _, err = self.run_cli(phase=2)
        self.assertEqual(code, 0)
        self.assertNotIn("NOT reconciled", err)

    def test_phase_1_never_warns(self) -> None:
        a = self.company("a.de")
        self.batch(a)
        code, _, err = self.run_cli(phase=1)
        self.assertEqual(code, 0)
        self.assertNotIn("reconciled", err)


if __name__ == "__main__":
    unittest.main()
