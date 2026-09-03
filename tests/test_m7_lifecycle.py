"""M7 — §8's lifecycle and §9's three writing actions (M1.106).

The brief's definition of done: *"Export fails loudly when `ai.*` basis fields
are missing; `forget --domain X` leaves zero rows anywhere."* Both are pinned
as measurements rather than as claims — `forget` is checked against every
table that has a `company_id`, read from the schema, plus the one that hangs
off `score`, plus the directory on disk. The rest pins what §8 says in words:
no email channel, a blocked company's outreach refused by the trigger and
explained by the command, a purge that actually deletes.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from portal import brief, cli, db, lifecycle, migrate, score, serve

TODAY = date(2026, 9, 3)


class M7TestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "portal.db"
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.run_id = int(
            self.conn.execute(
                "INSERT INTO run (started_at, finished_at, stage) VALUES "
                "('2026-08-15T00:00:00Z','2026-08-15T00:01:00Z','extract-p1')"
            ).lastrowid
        )

    # ── fixtures ────────────────────────────────────────────────────────

    def company(self, domain: str, **columns) -> int:
        keys = "".join(f", {k}" for k in columns)
        marks = "".join(", ?" for _ in columns)
        company_id = int(
            self.conn.execute(
                f"INSERT INTO company (domain, discovery_source, discovered_at{keys}) "
                f"VALUES (?, 'seed_csv', '2026-08-15T00:00:00Z'{marks})",
                (domain, *columns.values()),
            ).lastrowid
        )
        directory = self.artifacts / domain.replace("/", "-")
        directory.mkdir()
        (directory / "homepage-x.html").write_text("<html>x</html>", encoding="utf-8")
        artifact = int(
            self.conn.execute(
                "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
                "body_path, fetched_at) VALUES (?, 'homepage', ?, 200, ?, ?, 'x')",
                (
                    company_id,
                    f"https://{domain}/",
                    f"h-{company_id}",
                    f"{domain}/homepage-x.html",
                ),
            ).lastrowid
        )
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, method, evidence_url, "
            "artifact_id, observed_at) VALUES (?, ?, 'i18n.hreflang_count', 0, 'deterministic', "
            "?, ?, 'x')",
            (company_id, self.run_id, f"https://{domain}/", artifact),
        )
        return company_id

    def ai(self, company_id: int, *, basis: bool = True, mentions: int = 0) -> None:
        run = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES "
            "('2026-09-01T00:00:00Z','2026-09-01T00:01:00Z','ai_check')"
        ).lastrowid
        rows = [
            ("ai.queries_checked", None, 2.0, None),
            ("ai.brand_mentions", None, float(mentions), None),
            ("ai.competitors_mentioned", "Emmi-Dent, Philips Sonicare", 2.0, None),
        ]
        if basis:
            rows += [
                ("ai.query_text", "beste Zahnbürste | Zahnbürste Test", None, None),
                ("ai.checked_at", None, None, "2026-09-01"),
                ("ai.model_used", "claude-haiku-4-5", None, None),
            ]
        for key, text, num, day in rows:
            self.conn.execute(
                "INSERT INTO signal (company_id, run_id, key, value_text, value_num, value_date, "
                "method, confidence, evidence_url, observed_at) VALUES (?,?,?,?,?,?,'llm',1.0,"
                "'ai-check:x','x')",
                (company_id, run, key, text, num, day),
            )

    def contact(self, company_id: int, *, purge_after: str) -> int:
        return int(
            self.conn.execute(
                "INSERT INTO contact (company_id, full_name, role, source_url, collected_at, "
                "purge_after) VALUES (?, 'Max Muster', 'Inhaber', 'https://x/impressum', "
                "'2025-08-01T00:00:00Z', ?)",
                (company_id, purge_after),
            ).lastrowid
        )

    def block(self, company_id: int) -> None:
        self.conn.execute(
            "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at) "
            "VALUES (?, 'blog_cadence_unmeasurable', ?, 'x')",
            (company_id, self.run_id),
        )

    def cli_out(self, fn, *args, **kwargs) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fn(*args, **kwargs)
        return code, out.getvalue(), err.getvalue()

    # ── purge ───────────────────────────────────────────────────────────

    def test_purge_deletes_only_expired_contacts(self) -> None:
        c = self.company("a.de")
        old = self.contact(c, purge_after="2026-08-01T00:00:00Z")
        fresh = self.contact(c, purge_after="2027-08-01T00:00:00Z")
        code, out, _ = self.cli_out(cli.cmd_purge, self.db_path, dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("would be deleted", out)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM contact").fetchone()[0], 2
        )
        code, out, _ = self.cli_out(cli.cmd_purge, self.db_path, dry_run=False)
        self.assertEqual(code, 0)
        left = [r[0] for r in self.conn.execute("SELECT id FROM contact")]
        self.assertEqual(left, [fresh])
        self.assertNotIn(old, left)

    # ── forget ──────────────────────────────────────────────────────────

    def test_forget_leaves_zero_rows_anywhere_and_no_directory(self) -> None:
        gone = self.company("gone.de")
        kept = self.company("kept.de")
        self.contact(gone, purge_after="2027-01-01T00:00:00Z")
        self.block(gone)
        score.run(self.conn, today=TODAY)
        # A batch request naming the company, through the denormalised column
        # migration 015 added for exactly this.
        run = self.conn.execute(
            "INSERT INTO run (started_at, stage, est_cost_usd) VALUES ('x','extract_p2',0.5)"
        ).lastrowid
        batch = self.conn.execute(
            "INSERT INTO llm_batch (provider_batch_id, run_id, purpose, request_count, "
            "reserved_at, est_cost_usd, status, submitted_at) VALUES ('b1', ?, 'impressum', 1, "
            "'x', 0.5, 'submitted', 'x')",
            (run,),
        ).lastrowid
        artifact = self.conn.execute(
            "SELECT id FROM artifact WHERE company_id = ?", (gone,)
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO llm_batch_request (batch_id, custom_id, company_id, artifact_id, "
            "sent_text_sha256, sent_bytes) VALUES (?, 'impressum:x', ?, ?, 's', 1)",
            (batch, gone, artifact),
        )
        self.assertTrue(lifecycle.residue(self.conn, gone))

        code, out, err = self.cli_out(cli.cmd_forget, self.db_path, "gone.de", yes=True)
        self.assertEqual(code, 0, err)
        self.assertIn("verified: zero rows", out)
        self.assertEqual(lifecycle.residue(self.conn, gone), {})
        self.assertFalse((self.artifacts / "gone.de").exists())
        # The other company, and the ledger, are untouched.
        self.assertTrue(lifecycle.residue(self.conn, kept))
        self.assertTrue((self.artifacts / "kept.de").exists())
        self.assertEqual(
            self.conn.execute("SELECT SUM(est_cost_usd) FROM run").fetchone()[0], 0.5
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM llm_batch").fetchone()[0], 1
        )

    def test_forget_refuses_without_yes_and_unknown_domain(self) -> None:
        self.company("a.de")
        code, _, err = self.cli_out(cli.cmd_forget, self.db_path, "a.de", yes=False)
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        code, _, err = self.cli_out(cli.cmd_forget, self.db_path, "nope.de", yes=True)
        self.assertEqual(code, 2)
        self.assertIn("nope.de", err)

    # ── exclude / outreach ──────────────────────────────────────────────

    def test_exclude_needs_a_reason_and_lifts(self) -> None:
        self.company("a.de")
        code, _, err = self.cli_out(
            cli.cmd_exclude, self.db_path, "a.de", reason="  ", lift=False
        )
        self.assertEqual(code, 2)
        self.assertIn("reason", err)
        code, _, _ = self.cli_out(
            cli.cmd_exclude,
            self.db_path,
            "a.de",
            reason="duplicate of b.de",
            lift=False,
        )
        self.assertEqual(code, 0)
        row = self.conn.execute(
            "SELECT excluded, excluded_reason FROM company"
        ).fetchone()
        self.assertEqual((row[0], row[1]), (1, "duplicate of b.de"))
        self.cli_out(cli.cmd_exclude, self.db_path, "a.de", reason="", lift=True)
        row = self.conn.execute(
            "SELECT excluded, excluded_reason FROM company"
        ).fetchone()
        self.assertEqual((row[0], row[1]), (0, None))

    def test_outreach_refuses_email_and_a_blocked_company(self) -> None:
        c = self.company("a.de")
        with self.assertRaises(ValueError):
            lifecycle.log_outreach(self.conn, "a.de", channel="email")
        self.block(c)
        code, _, err = self.cli_out(
            cli.cmd_outreach,
            self.db_path,
            "a.de",
            channel="phone",
            occurred_at=None,
            notes="",
            outcome=None,
        )
        self.assertEqual(code, 2)
        self.assertIn("blog_cadence_unmeasurable", err)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0], 0
        )

    def test_outreach_logs_with_outcome(self) -> None:
        self.company("a.de")
        code, _, _ = self.cli_out(
            cli.cmd_outreach,
            self.db_path,
            "a.de",
            channel="post",
            occurred_at="2026-09-01T10:00:00Z",
            notes="Brief verschickt",
            outcome="no_response",
        )
        self.assertEqual(code, 0)
        row = self.conn.execute(
            "SELECT channel, occurred_at, notes, outcome FROM outreach"
        ).fetchone()
        self.assertEqual(
            tuple(row),
            ("post", "2026-09-01T10:00:00Z", "Brief verschickt", "no_response"),
        )

    # ── brief ───────────────────────────────────────────────────────────

    def test_brief_omits_the_ai_section_before_phase_2(self) -> None:
        c = self.company("a.de", legal_name="A GmbH")
        score.run(self.conn, today=TODAY)
        text = brief.render(self.conn, c)
        self.assertIn("# Research-Brief: A GmbH", text)
        self.assertIn("## Befunde", text)
        self.assertNotIn("KI-Sichtbarkeit", text)

    def test_brief_states_the_basis_in_the_proven_format(self) -> None:
        c = self.company("a.de")
        self.ai(c)
        score.run(self.conn, today=TODAY, phase=2)
        text = brief.render(self.conn, c)
        self.assertIn("## KI-Sichtbarkeit", text)
        self.assertIn(
            "Geprüft am 01.09.2026 über Claude (`claude-haiku-4-5`) mit aktivierter Websuche.",
            text,
        )
        self.assertIn("Abfragen: „beste Zahnbürste“ · „Zahnbürste Test“", text)
        self.assertIn("Bei 2 von 2 Abfragen wurde Ihre Marke nicht genannt.", text)
        self.assertIn("Genannt wurden stattdessen: Emmi-Dent, Philips Sonicare.", text)
        self.assertNotIn("unsichtbar", text)

    def test_brief_fails_loudly_without_the_basis(self) -> None:
        c = self.company("a.de")
        self.ai(c, basis=False)
        score.run(self.conn, today=TODAY, phase=2)
        with self.assertRaises(brief.MissingBasis) as ctx:
            brief.render(self.conn, c)
        self.assertIn("ai_query_text", str(ctx.exception))
        code, _, err = self.cli_out(cli.cmd_brief, self.db_path, "a.de", out=None)
        self.assertEqual(code, 2)
        self.assertIn("refused", err)

    def test_brief_refuses_a_blocked_company_and_an_unscored_one(self) -> None:
        c = self.company("a.de")
        with self.assertRaises(brief.NotScored):
            brief.render(self.conn, c)
        score.run(self.conn, today=TODAY)
        self.block(c)
        with self.assertRaises(brief.ContactBlocked):
            brief.render(self.conn, c)

    def test_brief_cli_writes_a_file(self) -> None:
        self.company("a.de")
        score.run(self.conn, today=TODAY)
        target = self.root / "out" / "brief.md"
        code, _, _ = self.cli_out(cli.cmd_brief, self.db_path, "a.de", out=target)
        self.assertEqual(code, 0)
        self.assertIn("# Research-Brief", target.read_text(encoding="utf-8"))

    # ── serve ───────────────────────────────────────────────────────────

    def client(self) -> TestClient:
        return TestClient(serve.create_app(self.db_path, self.artifacts))

    def test_serve_actions(self) -> None:
        c = self.company("a.de")
        score.run(self.conn, today=TODAY)
        client = self.client()
        page = client.get(f"/company/{c}/detail").text
        self.assertIn("Aktionen", page)
        self.assertIn("Brief exportieren", page)

        r = client.post(f"/company/{c}/exclude", data={"reason": ""})
        self.assertEqual(r.status_code, 400)
        r = client.post(f"/company/{c}/exclude", data={"reason": "test"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ausschluss aufheben", r.text)
        r = client.post(f"/company/{c}/exclude", data={"lift": "1"})
        self.assertIn("ausschließen", r.text)

        r = client.post(
            f"/company/{c}/outreach", data={"channel": "phone", "notes": "ok"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0], 1
        )

        r = client.get(f"/company/{c}/brief.md")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertIn("# Research-Brief", r.text)

    def test_serve_blocked_outreach_and_cross_site(self) -> None:
        c = self.company("a.de")
        score.run(self.conn, today=TODAY)
        self.block(c)
        client = self.client()
        r = client.post(f"/company/{c}/outreach", data={"channel": "post"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("blog_cadence_unmeasurable", r.text)
        r = client.get(f"/company/{c}/brief.md")
        self.assertEqual(r.status_code, 409)
        r = client.post(
            f"/company/{c}/exclude",
            data={"reason": "x"},
            headers={"origin": "https://evil.example", "host": "127.0.0.1:8000"},
        )
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
