"""`portal extract-p2` — the spend gate, and the path through it (9c).

**No test here makes a live call, and none could.** The submitting path takes
an injected `llm.LLMProvider`; every test that reaches it passes a fake. The
tests that go through `cli.main` — where the real Anthropic provider is built
— run with `ANTHROPIC_API_KEY` explicitly absent, so `count_tokens` raises
`MissingKeyError` before any network attempt and the assertion is exactly that
nothing was reserved. A key in scope would turn that test into a paid call,
which is why it removes the variable rather than trusting the environment.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from portal import cli, db, ledger, llm, llm_anthropic, migrate

IMPRESSUM = (
    "<html><body><h1>Impressum</h1><p>Muster GmbH, Musterstraße 1, 12345 "
    "Berlin. Geschäftsführer: Erika Muster.</p></body></html>"
)


class FakeProvider:
    """9b's shape: counts tokens, submits, records what it was handed."""

    name = "anthropic"
    model = "claude-haiku-4-5"

    def __init__(self) -> None:
        self.submitted: list[llm.BatchRequest] = []
        self.counted = 0

    def limits(self) -> llm.ModelLimits:
        return llm.limits_for(self.name, self.model)

    def count_input_tokens(self, request: llm.BatchRequest) -> int:
        self.counted += 1
        return len(request.user_text) // 4

    def token_counter(self) -> llm.TokenCounter:
        def count(*, system: str, user_text: str) -> int:
            self.counted += 1
            return len(user_text) // 4

        return count

    def submit_batch(self, requests, *, clearance: ledger.LedgerClearance) -> str:
        self.submitted = list(requests)
        return "msgbatch_fake_9c"

    def poll_batch(self, provider_batch_id: str):  # pragma: no cover
        raise NotImplementedError


class ExtractP2CliTestCase(unittest.TestCase):
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
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(llm_anthropic.API_KEY_ENV, None)

    def company(self, domain: str, *, admitted: int = 1) -> int:
        cur = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES (?, 'seed_csv', '2026-08-01T00:00:00Z')",
            (domain,),
        )
        company_id = int(cur.lastrowid or 0)
        (self.artifacts / f"{company_id}.html").write_text(IMPRESSUM, encoding="utf-8")
        (self.artifacts / f"{company_id}-home.html").write_text(
            "<html><body>Startseite</body></html>", encoding="utf-8"
        )
        (self.artifacts / f"{company_id}-robots.txt").write_text(
            "User-agent: *\nAllow: /\n", encoding="utf-8"
        )
        for kind, name in (
            ("impressum", f"{company_id}.html"),
            ("homepage", f"{company_id}-home.html"),
            ("robots", f"{company_id}-robots.txt"),
        ):
            url = f"https://{domain}/{'' if kind == 'homepage' else kind}"
            if kind == "robots":
                url = f"https://{domain}/robots.txt"
            self.conn.execute(
                "INSERT INTO artifact (company_id, kind, url, http_status, "
                "content_hash, body_path, fetched_at) VALUES (?,?,?,200,?,?,'x')",
                (company_id, kind, url, f"h-{kind}-{company_id}", name),
            )
        cur = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES "
            "(datetime('now'), datetime('now'), 'score-p1')"
        )
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, method, "
            "evidence_url, observed_at) VALUES (?,?,'gate.phase2_admitted',?,"
            "'deterministic','',datetime('now'))",
            (company_id, cur.lastrowid, admitted),
        )
        return company_id

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--db", str(self.db_path), *argv])
        return code, out.getvalue() + err.getvalue()

    def submit(self, provider: FakeProvider, **kwargs) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_extract_p2(
                self.db_path,
                dry_run=False,
                submit=True,
                purpose=kwargs.get("purpose", "impressum"),
                provider=provider,
            )
        return code, out.getvalue() + err.getvalue()

    def batches(self) -> list:
        return list(self.conn.execute("SELECT * FROM llm_batch ORDER BY id"))

    def p2_runs(self) -> list:
        return list(
            self.conn.execute(
                "SELECT * FROM run WHERE stage = 'extract-p2' ORDER BY id"
            )
        )


class TheSafeDefault(ExtractP2CliTestCase):
    def test_no_flag_is_a_dry_run_that_reserves_nothing(self) -> None:
        self.company("muster.de")
        code, text = self.run_cli("extract-p2")
        self.assertEqual(code, 0, text)
        self.assertIn("would be sent", text)
        self.assertIn("Nothing was sent", text)
        self.assertIn("--submit", text)
        self.assertEqual(self.batches(), [])
        self.assertEqual(self.p2_runs(), [])
        self.assertEqual(ledger.monthly_spend_usd(self.conn), 0.0)

    def test_dry_run_says_the_same_thing_explicitly(self) -> None:
        self.company("muster.de")
        code, text = self.run_cli("extract-p2", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertIn("Nothing was sent", text)
        self.assertEqual(self.batches(), [])

    def test_the_stale_refusal_text_is_gone(self) -> None:
        """The old message said the reservation *"is 9b's"*; 9b landed."""
        self.company("muster.de")
        for argv in (("extract-p2",), ("extract-p2", "--dry-run")):
            _, text = self.run_cli(*argv)
            self.assertNotIn("9b's", text)
            self.assertNotIn("can only be run with --dry-run", text)

    def test_submit_and_dry_run_together_are_refused(self) -> None:
        self.company("muster.de")
        code, text = self.run_cli("extract-p2", "--submit", "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("contradict", text)
        self.assertEqual(self.batches(), [])

    def test_submit_without_a_key_reserves_nothing_and_says_so(self) -> None:
        """Through `cli.main`, so the REAL provider is built — and stops in
        `count_tokens`, before anything is priced, because the key is absent.
        No batch, no reservation, and the run is marked rather than left open."""
        self.company("muster.de")
        code, text = self.run_cli("extract-p2", "--submit")
        self.assertEqual(code, 2, text)
        self.assertIn(llm_anthropic.API_KEY_ENV, text)
        self.assertEqual(self.batches(), [])
        self.assertEqual(ledger.monthly_spend_usd(self.conn), 0.0)
        (run,) = self.p2_runs()
        self.assertIn("MissingKeyError", run["aborted_reason"])
        self.assertIsNone(run["finished_at"])


class ThePaidPath(ExtractP2CliTestCase):
    def test_submit_reserves_both_rows_in_one_transaction_and_submits(self) -> None:
        company_id = self.company("muster.de")
        provider = FakeProvider()
        code, text = self.submit(provider)
        self.assertEqual(code, 0, text)

        (batch,) = self.batches()
        (run,) = self.p2_runs()
        self.assertEqual(batch["status"], "submitted")
        self.assertEqual(batch["provider_batch_id"], "msgbatch_fake_9c")
        self.assertEqual(batch["run_id"], run["id"])
        self.assertEqual(batch["purpose"], "impressum")
        self.assertEqual(batch["request_count"], 1)
        # §7 control 4, both halves, equal: the batch's reservation is the
        # line the ledger reads (M1.69/M1.72).
        self.assertGreater(batch["est_cost_usd"], 0.0)
        self.assertAlmostEqual(run["est_cost_usd"], batch["est_cost_usd"])
        self.assertAlmostEqual(
            ledger.monthly_spend_usd(self.conn), batch["est_cost_usd"]
        )
        # The submitting run is finished, so `company_profile` will serve the
        # signals `reconcile` writes under it (007, B4).
        self.assertIsNotNone(run["finished_at"])
        self.assertIsNone(run["aborted_reason"])
        self.assertEqual(run["companies_seen"], 1)
        # The request set (015) names the company.
        rows = list(self.conn.execute("SELECT * FROM llm_batch_request"))
        self.assertEqual([r["company_id"] for r in rows], [company_id])
        # What went out is what the dry run showed.
        self.assertEqual(len(provider.submitted), 1)
        self.assertTrue(provider.submitted[0].custom_id.startswith("impressum:"))
        self.assertIn("reserved $", text)
        self.assertIn("one transaction (M1.72)", text)
        self.assertIn("portal reconcile", text)

    def test_the_ceiling_is_consulted_before_anything_is_priced(self) -> None:
        """§7 control 2 first. A window already over the ceiling means the
        provider is not asked to count a single token."""
        self.company("muster.de")
        self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage, est_cost_usd) VALUES "
            "(datetime('now','-1 days'), datetime('now'), 'extract-p2', ?)",
            (ledger.MONTHLY_CEILING_USD + 1.0,),
        )
        provider = FakeProvider()
        code, text = self.submit(provider)
        self.assertEqual(code, 2)
        self.assertIn("§7 control 2", text)
        self.assertEqual(provider.counted, 0)
        self.assertEqual(provider.submitted, [])
        self.assertEqual(self.batches(), [])
        # No new run row either — the refusal happened before one was minted.
        self.assertEqual(len(self.p2_runs()), 1)

    def test_control_3_refuses_at_the_cli_with_nothing_on_the_books(self) -> None:
        """§7 control 3 wired into the command (M1.109), not just the write.

        The ceiling is enforced inside `_charge_run`, which is what makes it
        unbypassable — but before Unit 11 nothing caught the refusal here, so an
        over-ceiling run left `cmd_extract_p2` as a traceback. The two things
        this pins are the operator-facing half: the run is **aborted** rather
        than left open (007/M1.39), and the exit is 2 with the reservation
        named.
        """
        self.company("muster.de")
        provider = FakeProvider()
        # A ceiling below any real reservation. Control 2 is untouched and
        # still clears — the two bound different things.
        with mock.patch.object(ledger, "RUN_CEILING_USD", 0.0):
            code, text = self.submit(provider)

        self.assertEqual(code, 2, text)
        self.assertIn("§7 control 3", text)
        # Control 2 was consulted first and said yes, so the run was priced.
        self.assertGreater(provider.counted, 0)
        # Nothing submitted, nothing on the books: M1.72's transaction rolled
        # the batch row back with the refusal.
        self.assertEqual(provider.submitted, [])
        self.assertEqual(self.batches(), [])
        (run,) = self.p2_runs()
        self.assertAlmostEqual(run["est_cost_usd"] or 0.0, 0.0)
        # The run row exists (it is minted before the reservation) and must not
        # be left open, or `company_profile` declines a stage that never spent.
        self.assertIsNotNone(run["aborted_reason"])
        self.assertIn("control 3", run["aborted_reason"])

    def test_the_per_run_bound_is_announced_before_the_call(self) -> None:
        """It cannot be checked before `reserve_and_submit` — the reservation is
        priced from `count_tokens` inside it — so what the command owes the
        operator is the bound, named before the spend."""
        self.company("muster.de")
        code, text = self.submit(FakeProvider())
        self.assertEqual(code, 0, text)
        self.assertIn("§7 control 2", text)
        self.assertIn("§7 control 3", text)
        self.assertIn(f"${ledger.RUN_CEILING_USD:.2f}", text)
        self.assertLess(
            text.index("§7 control 3"),
            text.index("reserved $"),
            "control 3's bound must be printed before the reservation it bounds",
        )

    def test_a_stopped_company_is_not_sent(self) -> None:
        self.company("in.de")
        self.company("stopped.de", admitted=0)
        provider = FakeProvider()
        code, text = self.submit(provider)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(provider.submitted), 1)
        self.assertIn("stopped.de", text)
        self.assertIn("SKIPPED", text)

    def test_nothing_admitted_means_nothing_reserved(self) -> None:
        self.company("stopped.de", admitted=0)
        provider = FakeProvider()
        code, text = self.submit(provider)
        self.assertEqual(code, 2)
        self.assertIn("nothing to submit", text)
        self.assertEqual(self.batches(), [])
        self.assertEqual(self.p2_runs(), [])

    def test_a_dry_key_at_submit_leaves_the_reservation_on_the_books(self) -> None:
        """M1.53's submit-time seam, through the CLI. The reservation was
        committed before `create` was called (control 4's order), the submit
        failed, and the money stays counted: `reserved`, no provider id,
        released by nobody (migration 014)."""
        self.company("muster.de")

        class DryProvider(FakeProvider):
            def submit_batch(self, requests, *, clearance):
                raise llm_anthropic.BalanceExhausted("the key ran dry")

        code, text = self.submit(DryProvider())
        self.assertEqual(code, 2)
        self.assertIn("ran dry", text)
        (batch,) = self.batches()
        self.assertEqual(batch["status"], "reserved")
        self.assertIsNone(batch["provider_batch_id"])
        (run,) = self.p2_runs()
        self.assertAlmostEqual(run["est_cost_usd"], batch["est_cost_usd"])
        self.assertIn("BalanceExhausted", run["aborted_reason"])

    def test_the_homepage_purpose_is_its_own_batch(self) -> None:
        self.company("muster.de")
        provider = FakeProvider()
        code, text = self.submit(provider, purpose="homepage")
        self.assertEqual(code, 0, text)
        (batch,) = self.batches()
        self.assertEqual(batch["purpose"], "homepage")
        self.assertTrue(provider.submitted[0].custom_id.startswith("homepage:"))


if __name__ == "__main__":
    unittest.main()
