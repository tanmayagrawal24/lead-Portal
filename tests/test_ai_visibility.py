"""§5.5c / M6 — `portal ai-check`, fixtures only. No key, no network, no spend.

What is pinned, in order of what it would cost to get wrong: the paid surface
is guarded at import; the gate refuses a query count that would silence the
+15 rule; the dry run writes nothing; `--submit` reserves BEFORE the first
call and reconciles to the measured actual AFTER the last; a balance that runs
dry mid-run finishes the run with what was paid for (M1.105(c)); and the six
keys land under a stage of their own so `company_profile` serves them.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from portal import ai_visibility as av
from portal import cli, db, ledger, llm, llm_anthropic, migrate

USAGE = llm.Usage(input_tokens=12_000, output_tokens=120, web_searches=1)


class FakeProvider:
    """`ai_visibility.SearchProvider` with scripted answers."""

    name = "anthropic"
    model = "claude-haiku-4-5"

    def __init__(
        self,
        answers: dict[str, str],
        *,
        dry_after: int | None = None,
        fail_after: int | None = None,
    ):
        self.answers = answers
        self.calls: list[tuple[str, int, object]] = []
        self.dry_after = dry_after
        self.fail_after = fail_after

    def token_counter(self) -> llm.TokenCounter:
        def count(*, system: str, user_text: str) -> int:
            return 350

        return count

    def ask_with_search(
        self, *, system, user_text, max_tokens, max_searches, clearance
    ):
        if not isinstance(clearance, ledger.LedgerClearance):
            raise TypeError("called without a clearance")
        if self.dry_after is not None and len(self.calls) >= self.dry_after:
            raise llm_anthropic.BalanceExhausted("dry")
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("rate_limit_error: overloaded")
        self.calls.append((user_text, max_searches, clearance))
        return llm.SearchAnswer(
            text=self.answers[user_text],
            usage=USAGE,
            stop_reason="end_turn",
            model=self.model,
        )


class DerivationTestCase(unittest.TestCase):
    def test_paid_surface_is_guarded_at_import(self) -> None:
        self.assertIn("ask_with_search", llm_anthropic.PAID_SURFACES)
        self.assertTrue(
            getattr(
                llm_anthropic.AnthropicProvider.ask_with_search,
                "_ledger_guarded",
                False,
            )
        )

    def test_first_category_is_the_term(self) -> None:
        self.assertEqual(
            av.category_term("Ultraschallzahnbürste | Zahnpflege"),
            "Ultraschallzahnbürste",
        )
        self.assertEqual(av.category_term(" | Lampen"), "Lampen")
        self.assertIsNone(av.category_term(None))
        self.assertIsNone(av.category_term("ab"))

    def test_queries_are_the_fixed_templates(self) -> None:
        self.assertEqual(
            av.derive_queries("Ultraschallzahnbürste"),
            ("beste Ultraschallzahnbürste", "Ultraschallzahnbürste Test"),
        )
        self.assertEqual(len(av.derive_queries("x", 3)), 3)
        with self.assertRaises(ValueError):
            av.derive_queries("x", 1)
        with self.assertRaises(ValueError):
            av.derive_queries("x", 4)

    def test_brand_terms_strip_legal_forms_and_short_labels(self) -> None:
        self.assertEqual(
            av.brand_terms("zecplus.de", "ZecPlus GmbH & Co. KG"),
            ("zecplus.de", "zecplus"),
        )
        self.assertEqual(
            av.brand_terms("abc.de", "Opulent Wohnen e.K."),
            ("abc.de", "opulent wohnen"),
        )

    def test_mentioned_is_punctuation_and_case_insensitive(self) -> None:
        terms = av.brand_terms("zecplus.de", None)
        self.assertTrue(av.mentioned("Ich empfehle Zec-Plus und Philips.", terms))
        self.assertTrue(av.mentioned("siehe zecplus.de", terms))
        self.assertFalse(av.mentioned("Philips Sonicare, Curaprox", terms))

    def test_parse_brands_takes_the_last_object_and_tolerates_prose(self) -> None:
        text = 'Hier meine Empfehlung: {"brands": ["Emmi-Dent", "Philips Sonicare", "Emmi-Dent"], "note": "x"}'
        brands, parsed = av.parse_brands(text)
        self.assertTrue(parsed)
        self.assertEqual(brands, ("Emmi-Dent", "Philips Sonicare"))
        self.assertEqual(av.parse_brands("keine Ahnung"), ((), False))
        self.assertEqual(av.parse_brands('{"x": 1}'), ((), False))

    def test_reservation_prices_searches_at_live_rates(self) -> None:
        plan = av.Plan(1, "a.de", "t", ("q1", "q2"), ("a.de",))
        est = av.reservation(
            [plan], provider="anthropic", model="claude-haiku-4-5", prompt_tokens=300
        )
        self.assertEqual(est.web_searches, 2)
        self.assertEqual(est.input_tokens, (300 + av.SEARCH_CONTEXT_TOKENS) * 2)
        self.assertFalse(est.price.batch)
        self.assertGreater(est.total_usd, 0.02)


class AiCheckCliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "portal.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(llm_anthropic.API_KEY_ENV, None)

    def company(
        self,
        domain: str,
        *,
        admitted: int = 1,
        categories: str | None = "Zahnbürste | Pflege",
        legal_name: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at, legal_name) "
            "VALUES (?, 'seed_csv', '2026-08-01T00:00:00Z', ?)",
            (domain, legal_name),
        )
        company_id = int(cur.lastrowid or 0)
        score_run = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES "
            "('2026-08-02T00:00:00Z', '2026-08-02T00:01:00Z', 'score-p1')"
        ).lastrowid
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, method, evidence_url, "
            "observed_at) VALUES (?, ?, 'gate.phase2_admitted', ?, 'deterministic', ?, 'x')",
            (company_id, score_run, admitted, f"https://{domain}/"),
        )
        if categories is not None:
            p2_run = self.conn.execute(
                "INSERT INTO run (started_at, finished_at, stage) VALUES "
                "('2026-08-03T00:00:00Z', '2026-08-03T00:01:00Z', 'extract-p2')"
            ).lastrowid
            for key, text, num in (
                ("offer.product_categories", categories, None),
                ("llm.homepage_extracted", None, 1),
            ):
                self.conn.execute(
                    "INSERT INTO signal (company_id, run_id, key, value_text, value_num, method, "
                    "confidence, evidence_url, observed_at) VALUES (?,?,?,?,?,'llm',1.0,?,'x')",
                    (company_id, p2_run, key, text, num, f"https://{domain}/"),
                )
        return company_id

    def score(self, company_id: int, total: int, phase: int) -> None:
        run_id = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES "
            "('2026-09-05T00:00:00Z','2026-09-05T00:01:00Z','score-p2')"
        ).lastrowid
        self.conn.execute(
            "INSERT INTO score (company_id, run_id, phase, total, band, "
            "ruleset_version, computed_at) VALUES (?,?,?,?,'B','v3',"
            "'2026-09-05T00:00:00Z')",
            (company_id, run_id, phase, total),
        )
        self.conn.commit()

    def run_cli(self, **kwargs) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        kwargs.setdefault("dry_run", False)
        kwargs.setdefault("submit", False)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_ai_check(self.db_path, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def profile(self, company_id: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM company_profile WHERE company_id = ?", (company_id,)
        ).fetchone()
        return dict(row)

    # ── gate ────────────────────────────────────────────────────────────

    def test_submit_and_dry_run_contradict(self) -> None:
        code, _, err = self.run_cli(submit=True, dry_run=True)
        self.assertEqual(code, 2)
        self.assertIn("contradict", err)

    def test_one_query_is_refused(self) -> None:
        code, _, err = self.run_cli(queries=1)
        self.assertEqual(code, 2)
        self.assertIn("M1.23", err)

    def test_dry_run_prints_the_literal_queries_and_writes_nothing(self) -> None:
        self.company("zahn.de", legal_name="Zahn GmbH")
        self.company("stopped.de", admitted=0)
        self.company("noprose.de", categories="")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("„beste Zahnbürste“", out)
        self.assertIn("„Zahnbürste Test“", out)
        self.assertIn("stopped.de", out)
        self.assertIn("§5.4 gate", out)
        self.assertIn("returned none", out)
        self.assertIn("Nothing was sent", out)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM run WHERE stage='ai_check'"
            ).fetchone()[0],
            0,
        )

    def test_submit_without_a_key_stops_before_any_run_row(self) -> None:
        self.company("zahn.de")
        code, _, err = self.run_cli(submit=True)
        self.assertEqual(code, 2)
        self.assertIn(llm_anthropic.API_KEY_ENV, err)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM run WHERE stage='ai_check'"
            ).fetchone()[0],
            0,
        )

    # ── the paid path ───────────────────────────────────────────────────

    def test_submit_reserves_asks_writes_and_reconciles(self) -> None:
        zahn = self.company("zahn.de", legal_name="Zahn Dental GmbH")
        provider = FakeProvider(
            {
                "beste Zahnbürste": '{"brands": ["Philips Sonicare", "Oral-B", "Zahn Dental"], "note": ""}',
                "Zahnbürste Test": 'Ergebnis: {"brands": ["Oral-B", "Curaprox"], "note": ""}',
            }
        )
        code, out, _ = self.run_cli(submit=True, provider=provider)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            [c[0] for c in provider.calls], ["beste Zahnbürste", "Zahnbürste Test"]
        )
        self.assertEqual({c[1] for c in provider.calls}, {av.SEARCHES_PER_QUERY})

        run = self.conn.execute("SELECT * FROM run WHERE stage='ai_check'").fetchone()
        self.assertIsNotNone(run["finished_at"])
        self.assertIsNone(run["aborted_reason"])
        self.assertEqual(run["web_searches"], 2)
        self.assertEqual(run["llm_input_tokens"], 24_000)
        # Reconciled to the measured actual, which is below the reservation.
        actual = llm.estimate_cost(
            input_tokens=24_000,
            output_tokens=240,
            provider="anthropic",
            model="claude-haiku-4-5",
            batch=False,
            web_searches=2,
        ).total_usd
        self.assertAlmostEqual(run["est_cost_usd"], actual, places=6)
        self.assertIn("measured", out)

        profile = self.profile(zahn)
        self.assertEqual(profile["ai_queries_checked"], 2)
        self.assertEqual(profile["ai_brand_mentions"], 1)
        self.assertEqual(profile["ai_query_text"], "beste Zahnbürste | Zahnbürste Test")
        self.assertEqual(
            profile["ai_competitors_mentioned"], "Philips Sonicare, Oral-B, Curaprox"
        )
        self.assertEqual(profile["ai_model_used"], "claude-haiku-4-5")
        self.assertRegex(profile["ai_checked_at"], r"^\d{4}-\d{2}-\d{2}$")

    def test_a_checked_company_is_withheld_until_recheck(self) -> None:
        self.company("zahn.de")
        provider = FakeProvider(
            {"beste Zahnbürste": '{"brands": []}', "Zahnbürste Test": '{"brands": []}'}
        )
        self.assertEqual(self.run_cli(submit=True, provider=provider)[0], 0)
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("already checked", out)
        code, out, _ = self.run_cli(recheck=True)
        self.assertIn("„beste Zahnbürste“", out)

    def test_a_dry_balance_finishes_the_run_with_what_was_paid_for(self) -> None:
        a = self.company("a-shop.de")
        b = self.company("b-shop.de")
        provider = FakeProvider(
            {
                "beste Zahnbürste": '{"brands": ["X"]}',
                "Zahnbürste Test": '{"brands": ["Y"]}',
            },
            dry_after=2,
        )
        code, _, err = self.run_cli(submit=True, provider=provider)
        self.assertEqual(code, 2)
        self.assertIn("b-shop.de", err)
        run = self.conn.execute("SELECT * FROM run WHERE stage='ai_check'").fetchone()
        # Finished, not aborted: `company_profile` must serve a's paid signals.
        self.assertIsNotNone(run["finished_at"])
        self.assertIsNone(run["aborted_reason"])
        self.assertEqual(run["companies_seen"], 1)
        self.assertEqual(self.profile(a)["ai_queries_checked"], 2)
        self.assertIsNone(self.profile(b)["ai_queries_checked"])

    def test_any_mid_run_failure_finishes_the_run_and_names_itself(self) -> None:
        """Unit 10 audit (M1.108): a rate limit between two companies is the
        balance case for everything that matters. No traceback, exit 2, the
        paid company's signals served, the failure printed by name."""
        a = self.company("a-shop.de")
        b = self.company("b-shop.de")
        provider = FakeProvider(
            {
                "beste Zahnbürste": '{"brands": ["X"]}',
                "Zahnbürste Test": '{"brands": ["Y"]}',
            },
            fail_after=2,
        )
        code, _, err = self.run_cli(submit=True, provider=provider)
        self.assertEqual(code, 2)
        self.assertIn("rate_limit_error", err)
        self.assertIn("b-shop.de", err)
        run = self.conn.execute("SELECT * FROM run WHERE stage='ai_check'").fetchone()
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(run["companies_seen"], 1)
        self.assertEqual(self.profile(a)["ai_queries_checked"], 2)
        self.assertIsNone(self.profile(b)["ai_queries_checked"])

    def test_run_ceiling_refuses_before_any_row(self) -> None:
        self.company("zahn.de")
        provider = FakeProvider({})
        # Unit 11 (M1.109): one ceiling constant, and it lives in `ledger`.
        with mock.patch.object(ledger, "RUN_CEILING_USD", 0.0001):
            code, _, err = self.run_cli(submit=True, provider=provider)
        self.assertEqual(code, 2)
        self.assertIn("control 3", err)
        self.assertEqual(provider.calls, [])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM run WHERE stage='ai_check'"
            ).fetchone()[0],
            0,
        )

    def test_opp_ai_invisible_fires_off_the_written_signals(self) -> None:
        from portal import score

        zahn = self.company("zahn.de")
        provider = FakeProvider(
            {
                "beste Zahnbürste": '{"brands": ["Oral-B"]}',
                "Zahnbürste Test": '{"brands": ["Curaprox"]}',
            }
        )
        self.assertEqual(self.run_cli(submit=True, provider=provider)[0], 0)
        _, results = score.run(self.conn, phase=2)
        (result,) = [r for r in results if r.company_id == zahn]
        fired = {c.rule_id: c.points for c in result.components}
        self.assertEqual(fired.get("opp.ai_invisible"), 15)


if __name__ == "__main__":
    unittest.main()


class TheLimitFlag(AiCheckCliTestCase):
    """M1.125. `--limit N` checks the top N eligible by score.

    The property under test is not "N were sent" — it is **where the cap is
    applied**. A company outside the budget must read as eligible-but-deferred,
    never as ineligible: the first is a fact about this run's money, the second
    is a claim about the company, and A7 spent four instances learning not to
    confuse the two.
    """

    def seed_scored(self, domain: str, total: int, phase: int = 2) -> int:
        company_id = self.company(domain)
        self.score(company_id, total, phase)
        return company_id

    def test_it_keeps_the_highest_scoring(self) -> None:
        self.seed_scored("low.de", 10)
        self.seed_scored("high.de", 90)
        self.seed_scored("mid.de", 50)
        plans, _ = av.prepare(self.conn, limit=2)
        self.assertEqual({p.domain for p in plans}, {"high.de", "mid.de"})

    def test_the_deferred_are_withheld_as_budget_not_as_ineligible(self) -> None:
        self.seed_scored("low.de", 10)
        self.seed_scored("high.de", 90)
        _, withheld = av.prepare(self.conn, limit=1)
        deferred = [w for w in withheld if w.domain == "low.de"]
        self.assertEqual(len(deferred), 1)
        reason = deferred[0].reason
        self.assertIn("--limit", reason)
        self.assertIn("only about the budget", reason)
        self.assertNotIn("not admitted", reason)

    def test_phase_1_ranks_where_no_phase_2_exists(self) -> None:
        self.seed_scored("p1only.de", 80, phase=1)
        self.seed_scored("p2low.de", 20, phase=2)
        plans, _ = av.prepare(self.conn, limit=1)
        self.assertEqual([p.domain for p in plans], ["p1only.de"])

    def test_phase_2_wins_over_phase_1_for_the_same_company(self) -> None:
        company_id = self.seed_scored("both.de", 90, phase=1)
        self.score(company_id, 5, 2)
        self.seed_scored("other.de", 50, phase=2)
        plans, _ = av.prepare(self.conn, limit=1)
        self.assertEqual(
            [p.domain for p in plans],
            ["other.de"],
            "the better-informed phase must rank, not the higher number",
        )

    def test_an_unscored_company_sorts_last_but_stays_eligible(self) -> None:
        self.company("unscored.de")
        self.seed_scored("scored.de", 1)
        plans, withheld = av.prepare(self.conn, limit=1)
        self.assertEqual([p.domain for p in plans], ["scored.de"])
        self.assertTrue(
            any(
                w.domain == "unscored.de" and "no score to rank on" in w.reason
                for w in withheld
            )
        )

    def test_a_limit_at_or_above_the_eligible_count_changes_nothing(self) -> None:
        self.seed_scored("a.de", 10)
        self.seed_scored("b.de", 20)
        unlimited, _ = av.prepare(self.conn)
        limited, withheld = av.prepare(self.conn, limit=99)
        self.assertEqual([p.domain for p in unlimited], [p.domain for p in limited])
        self.assertFalse([w for w in withheld if "--limit" in w.reason])

    def test_the_sent_list_keeps_corpus_order(self) -> None:
        """Passing a limit must not silently reorder the report."""
        self.seed_scored("zzz.de", 90)
        self.seed_scored("aaa.de", 80)
        plans, _ = av.prepare(self.conn, limit=2)
        self.assertEqual([p.domain for p in plans], ["aaa.de", "zzz.de"])

    def test_the_dry_run_prints_both_numbers(self) -> None:
        self.seed_scored("a.de", 10)
        self.seed_scored("b.de", 20)
        code, out, err = self.run_cli(dry_run=True, submit=False, limit=1)
        self.assertEqual(code, 0, out + err)
        self.assertIn("1 companies would be checked", out)
        self.assertIn("1 more are eligible but outside --limit 1", out)

    def test_a_limit_below_one_is_refused(self) -> None:
        code, _out, err = self.run_cli(dry_run=True, submit=False, limit=0)
        self.assertEqual(code, 2)
        self.assertIn("--limit must be 1 or more", err)


class TheBudgetFilters(AiCheckCliTestCase):
    """M1.126. `--only-new` and `--skip-blocked`.

    Both are budget filters, like `--limit`: they say which companies this
    run's money goes to, never which companies are checkable. So the tests
    that matter are the ones about the reason text and about the two edges —
    a corpus that has never been checked, and a block that is not a verdict on
    eligibility.
    """

    def discovered(self, domain: str, when: str, *, blocked: bool = False) -> int:
        company_id = self.company(domain)
        self.conn.execute(
            "UPDATE company SET discovered_at = ? WHERE id = ?", (when, company_id)
        )
        if blocked:
            self.conn.execute(
                "UPDATE company SET contact_blocked = 1 WHERE id = ?", (company_id,)
            )
        self.conn.commit()
        return company_id

    def ai_run(self, started_at: str) -> None:
        self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES (?,?,?)",
            (started_at, started_at, av.STAGE),
        )
        self.conn.commit()

    # ── --only-new ──────────────────────────────────────────────────────

    def test_with_no_previous_run_every_company_is_new(self) -> None:
        """The edge that matters: on a corpus never checked, "new" must not
        silently mean "none"."""
        self.discovered("a.de", "2020-01-01T00:00:00Z")
        self.discovered("b.de", "2026-09-01T00:00:00Z")
        self.assertIsNone(av.last_check_started_at(self.conn))
        plans, _ = av.prepare(self.conn, only_new=True)
        self.assertEqual({p.domain for p in plans}, {"a.de", "b.de"})

    def test_it_keeps_only_what_was_discovered_after_the_last_run(self) -> None:
        self.discovered("old.de", "2026-08-01T00:00:00Z")
        self.discovered("new.de", "2026-09-04T00:00:00Z")
        self.ai_run("2026-09-01T00:00:00Z")
        plans, withheld = av.prepare(self.conn, only_new=True)
        self.assertEqual([p.domain for p in plans], ["new.de"])
        self.assertTrue(
            any(w.domain == "old.de" and "--only-new" in w.reason for w in withheld)
        )

    def test_a_company_discovered_during_the_last_run_is_not_new(self) -> None:
        """Equal timestamps are not after. Off by one here re-checks a company
        the previous run already paid for."""
        self.discovered("same.de", "2026-09-01T00:00:00Z")
        self.ai_run("2026-09-01T00:00:00Z")
        plans, _ = av.prepare(self.conn, only_new=True)
        self.assertEqual(plans, [])

    def test_an_aborted_run_does_not_move_the_clock(self) -> None:
        """An aborted run may have checked nothing; treating it as the
        boundary would hide companies that were never actually reached."""
        self.discovered("x.de", "2026-08-01T00:00:00Z")
        self.conn.execute(
            "INSERT INTO run (started_at, stage, aborted_reason) VALUES "
            "('2026-09-01T00:00:00Z', ?, 'BalanceExhausted')",
            (av.STAGE,),
        )
        self.conn.commit()
        self.assertIsNone(av.last_check_started_at(self.conn))
        plans, _ = av.prepare(self.conn, only_new=True)
        self.assertEqual([p.domain for p in plans], ["x.de"])

    def test_it_is_off_by_default(self) -> None:
        self.discovered("old.de", "2026-08-01T00:00:00Z")
        self.ai_run("2026-09-01T00:00:00Z")
        plans, _ = av.prepare(self.conn)
        self.assertEqual([p.domain for p in plans], ["old.de"])

    # ── --skip-blocked ──────────────────────────────────────────────────

    def test_a_blocked_company_is_dropped_when_asked(self) -> None:
        self.discovered("ok.de", "2026-09-01T00:00:00Z")
        self.discovered("blocked.de", "2026-09-01T00:00:00Z", blocked=True)
        plans, withheld = av.prepare(self.conn, skip_blocked=True)
        self.assertEqual([p.domain for p in plans], ["ok.de"])
        reason = next(w.reason for w in withheld if w.domain == "blocked.de")
        self.assertIn("--skip-blocked", reason)
        self.assertIn("eligible", reason)

    def test_a_block_is_not_a_verdict_on_eligibility(self) -> None:
        """Without the flag a blocked company is still checked. §6.4 blocks
        CONTACT; it does not say the company is unmeasurable."""
        self.discovered("blocked.de", "2026-09-01T00:00:00Z", blocked=True)
        plans, _ = av.prepare(self.conn)
        self.assertEqual([p.domain for p in plans], ["blocked.de"])

    def test_the_two_filters_compose_with_the_limit(self) -> None:
        self.discovered("old.de", "2026-08-01T00:00:00Z")
        self.discovered("blocked.de", "2026-09-04T00:00:00Z", blocked=True)
        for i, dom in enumerate(("new1.de", "new2.de", "new3.de")):
            company_id = self.discovered(dom, "2026-09-04T00:00:00Z")
            self.score(company_id, 90 - i * 10, 2)
        self.ai_run("2026-09-01T00:00:00Z")
        plans, withheld = av.prepare(
            self.conn, only_new=True, skip_blocked=True, limit=2
        )
        self.assertEqual({p.domain for p in plans}, {"new1.de", "new2.de"})
        reasons = {w.domain: w.reason for w in withheld}
        self.assertIn("--only-new", reasons["old.de"])
        self.assertIn("--skip-blocked", reasons["blocked.de"])
        self.assertIn("--limit", reasons["new3.de"])

    def test_the_cli_accepts_both_flags(self) -> None:
        self.discovered("ok.de", "2026-09-01T00:00:00Z")
        code, out, err = self.run_cli(
            dry_run=True, submit=False, only_new=True, skip_blocked=True
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("1 companies would be checked", out)
