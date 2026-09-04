"""M1.119. `portal discover --source websearch` — §5.1's second, lower-fidelity
source, and the filters that decide what a model's answer is allowed to write
into `company`.

The tests that matter here are the ones about what is DROPPED. An answer from a
search-backed model is prose until something narrows it, and every row this
stage inserts is a row every later stage treats as a lead.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portal import ai_visibility, cli, db, discover_llm, ledger, llm, migrate


def _usage(inp: int = 1000, out: int = 200, searches: int = 2) -> llm.Usage:
    return llm.Usage(input_tokens=inp, output_tokens=out, web_searches=searches)


class FakeSearchProvider:
    """`ask_with_search` and `token_counter`, and nothing else."""

    name = "anthropic"
    model = "claude-haiku-4-5"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, object]] = []
        self.stop_reason = "end_turn"

    def token_counter(self) -> llm.TokenCounter:
        def count(*, system: str, user_text: str) -> int:
            return len(system + user_text) // 4

        return count

    def ask_with_search(
        self,
        *,
        system: str,
        user_text: str,
        max_tokens: int,
        max_searches: int,
        clearance: ledger.LedgerClearance,
    ) -> llm.SearchAnswer:
        self.calls.append(
            {
                "user_text": user_text,
                "max_tokens": max_tokens,
                "max_searches": max_searches,
            }
        )
        text = self.answers.pop(0) if self.answers else '{"shops": []}'
        return llm.SearchAnswer(
            text=text, usage=_usage(), stop_reason=self.stop_reason, model=self.model
        )


class TheMarketplaceFilter(unittest.TestCase):
    def test_marketplaces_and_aggregators_are_dropped(self) -> None:
        for domain in (
            "amazon.de",
            "ebay.de",
            "otto.de",
            "idealo.de",
            "geizhals.at",
            "check24.de",
            "zalando.ch",
            "kaufland.de",
        ):
            with self.subTest(domain=domain):
                self.assertTrue(discover_llm.is_marketplace(domain))

    def test_a_real_shop_whose_name_merely_starts_the_same_is_kept(self) -> None:
        """The reason this is an equality test on the registrable label and not
        a substring search: a substring rule drops a real lead silently."""
        for domain in (
            "amazonas-kaffee.de",
            "ottos-weinladen.de",
            "realschule-shop.de",
            "google-fonts-shop.de",
        ):
            with self.subTest(domain=domain):
                self.assertFalse(discover_llm.is_marketplace(domain))

    def test_one_entry_covers_every_cctld(self) -> None:
        for domain in ("amazon.de", "amazon.at", "amazon.ch", "amazon.com"):
            with self.subTest(domain=domain):
                self.assertTrue(discover_llm.is_marketplace(domain))


class TheAnswerParser(unittest.TestCase):
    def test_shops_are_read_out_of_a_json_object(self) -> None:
        shops = discover_llm.parse_shops(
            'Hier: {"shops": [{"domain": "a.de", "name": "A"}, '
            '{"domain": "b.de", "name": "B"}]}'
        )
        self.assertEqual(shops, [("a.de", "A"), ("b.de", "B")])

    def test_an_unparseable_answer_is_none_not_empty(self) -> None:
        """M1.59's tri-state. A call that returned prose was PAID FOR and
        yielded nothing readable — which is not the same as a call that
        searched and honestly found nothing."""
        self.assertIsNone(discover_llm.parse_shops("Ich konnte nichts finden."))
        self.assertIsNone(discover_llm.parse_shops('{"shops": "nope"}'))
        self.assertEqual(discover_llm.parse_shops('{"shops": []}'), [])

    def test_bare_strings_are_accepted_as_domains(self) -> None:
        self.assertEqual(
            discover_llm.parse_shops('{"shops": ["a.de", "b.de"]}'),
            [("a.de", ""), ("b.de", "")],
        )


class TheReservation(unittest.TestCase):
    def test_the_search_allowance_is_ai_visibilitys_and_not_a_second_copy(
        self,
    ) -> None:
        """One fact about one tool on one model. Two copies is how they drift
        (M1.42, M1.109)."""
        source = Path(discover_llm.__file__).read_text(encoding="utf-8")
        self.assertIn("ai_visibility.SEARCH_CONTEXT_TOKENS", source)
        self.assertNotIn("SEARCH_CONTEXT_TOKENS = ", source)

    def test_the_reservation_prices_every_search_max_uses_permits(self) -> None:
        est = discover_llm.reservation(
            5, provider="anthropic", model="claude-haiku-4-5", prompt_tokens=0
        )
        self.assertEqual(est.web_searches, 5 * discover_llm.SEARCHES_PER_CALL)

    def test_the_floor_is_positive_without_a_key(self) -> None:
        self.assertGreater(
            discover_llm.unmeasured_floor(
                1, provider="anthropic", model="claude-haiku-4-5"
            ),
            0.0,
        )


class RunTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "portal.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.clearance = ledger.check_ceiling(self.conn)

    def domains(self) -> list[tuple[str, str, str, str]]:
        return [
            tuple(r)
            for r in self.conn.execute(
                "SELECT domain, discovery_source, discovery_query, "
                "COALESCE(city, '') FROM company ORDER BY domain"
            )
        ]


class TheRun(RunTestCase):
    def test_shops_are_inserted_with_their_provenance_and_no_invented_address(
        self,
    ) -> None:
        provider = FakeSearchProvider(
            '{"shops": [{"domain": "https://www.Beispiel.de/", "name": "Beispiel"}]}'
        )
        report = discover_llm.run(
            self.conn,
            provider,
            "Onlineshop",
            region="Deutschland",
            max_calls=1,
            clearance=self.clearance,
            say=lambda *a: None,
        )
        self.assertEqual(report.inserted, 1)
        self.assertEqual(
            self.domains(),
            [("beispiel.de", "llm_websearch", "Onlineshop Deutschland", "")],
        )
        row = self.conn.execute(
            "SELECT city, postal_code, country FROM company"
        ).fetchone()
        self.assertEqual(
            tuple(row), (None, None, None), "no address a model made up (M1.52)"
        )

    def test_a_domain_named_by_two_calls_is_one_row_and_one_report_line(self) -> None:
        provider = FakeSearchProvider(
            '{"shops": [{"domain": "a.de", "name": "A"}]}',
            '{"shops": [{"domain": "a.de", "name": "A again"}]}',
        )
        report = discover_llm.run(
            self.conn,
            provider,
            "Onlineshop",
            max_calls=2,
            clearance=self.clearance,
            say=lambda *a: None,
        )
        self.assertEqual(len(report.found), 1)
        self.assertEqual(report.inserted, 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], 1
        )

    def test_a_domain_already_in_company_is_reported_but_not_reinserted(self) -> None:
        self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES ('a.de', 'seed_csv', '2026-01-01T00:00:00Z')"
        )
        self.conn.commit()
        report = discover_llm.run(
            self.conn,
            FakeSearchProvider('{"shops": [{"domain": "a.de", "name": "A"}]}'),
            "Onlineshop",
            max_calls=1,
            clearance=self.clearance,
            say=lambda *a: None,
        )
        self.assertEqual(report.inserted, 0)
        self.assertEqual(len(report.found), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT discovery_source FROM company WHERE domain = 'a.de'"
            ).fetchone()[0],
            "seed_csv",
            "ON CONFLICT DO NOTHING must not rewrite the original provenance",
        )

    def test_marketplaces_are_dropped_before_insert_and_named_in_the_report(
        self,
    ) -> None:
        provider = FakeSearchProvider(
            '{"shops": [{"domain": "amazon.de", "name": "Amazon"}, '
            '{"domain": "idealo.de", "name": "Idealo"}, '
            '{"domain": "echter-shop.de", "name": "Echter Shop"}]}'
        )
        report = discover_llm.run(
            self.conn,
            provider,
            "Onlineshop",
            max_calls=1,
            clearance=self.clearance,
            say=lambda *a: None,
        )
        self.assertEqual(report.inserted, 1)
        self.assertEqual(sorted(report.marketplaces), ["amazon.de", "idealo.de"])
        self.assertEqual([d[0] for d in self.domains()], ["echter-shop.de"])

    def test_max_uses_is_what_the_reservation_priced(self) -> None:
        provider = FakeSearchProvider()
        discover_llm.run(
            self.conn,
            provider,
            "Onlineshop",
            max_calls=1,
            clearance=self.clearance,
            say=lambda *a: None,
        )
        self.assertEqual(
            provider.calls[0]["max_searches"], discover_llm.SEARCHES_PER_CALL
        )

    def test_the_run_is_reconciled_to_measured_usage(self) -> None:
        report = discover_llm.run(
            self.conn,
            FakeSearchProvider('{"shops": []}'),
            "Onlineshop",
            max_calls=1,
            clearance=self.clearance,
            say=lambda *a: None,
        )
        row = self.conn.execute(
            "SELECT est_cost_usd, web_searches, llm_input_tokens, finished_at "
            "FROM run WHERE id = ?",
            (report.run_id,),
        ).fetchone()
        self.assertAlmostEqual(row[0], report.actual_usd)
        self.assertLess(
            report.actual_usd,
            report.reserved_usd,
            "the measured actual must replace the reservation, not add to it",
        )
        self.assertEqual(row[1], 2)
        self.assertEqual(row[2], 1000)
        self.assertIsNotNone(row[3])

    def test_an_unparseable_answer_is_counted_and_costs_are_still_booked(self) -> None:
        report = discover_llm.run(
            self.conn,
            FakeSearchProvider("Ich konnte leider nichts finden."),
            "Onlineshop",
            max_calls=1,
            clearance=self.clearance,
            say=lambda *a: None,
        )
        self.assertEqual(report.unparsed_answers, 1)
        self.assertGreater(report.actual_usd, 0.0, "it was paid for")

    def test_control_3_refuses_before_the_run_row_exists(self) -> None:
        with self.assertRaises(ai_visibility.RunCeilingExceeded):
            discover_llm.run(
                self.conn,
                FakeSearchProvider(),
                "Onlineshop",
                max_calls=5,
                clearance=self.clearance,
                per_run_ceiling_usd=0.0001,
                say=lambda *a: None,
            )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM run").fetchone()[0],
            0,
            "a refused run must reserve nothing at all",
        )

    def test_it_is_a_paid_surface_and_refuses_without_a_clearance(self) -> None:
        with self.assertRaises(llm.LedgerBypass):
            discover_llm.run(
                self.conn, FakeSearchProvider(), "Onlineshop", clearance=None
            )


class TheCommandSurface(RunTestCase):
    def test_the_dry_run_reserves_nothing_and_needs_no_key(self) -> None:
        code = cli.cmd_discover(
            self.path,
            "Onlineshop",
            region="Deutschland",
            submit=False,
            dry_run=True,
            source="websearch",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM run").fetchone()[0], 0)

    def test_submit_inserts_and_reconciles(self) -> None:
        code = cli.cmd_discover(
            self.path,
            "Onlineshop",
            region="Deutschland",
            submit=True,
            dry_run=False,
            max_calls=1,
            source="websearch",
            provider=FakeSearchProvider(
                '{"shops": [{"domain": "echter-shop.de", "name": "Echter"}]}'
            ),
        )
        self.assertEqual(code, 0)
        self.assertEqual([d[0] for d in self.domains()], ["echter-shop.de"])

    def test_max_calls_above_the_cap_is_refused(self) -> None:
        code = cli.cmd_discover(
            self.path,
            "Onlineshop",
            region="",
            submit=False,
            dry_run=True,
            max_calls=discover_llm.MAX_CALLS + 1,
            source="websearch",
        )
        self.assertEqual(code, 2)

    def test_places_stays_the_default_and_is_untouched(self) -> None:
        """The whole point of a second source is that it does not disturb the
        first. `--source` omitted must still be Places, dry, keyless."""
        code = cli.cmd_discover(
            self.path, "Zahnpflege", region="NRW", submit=False, dry_run=True
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM run").fetchone()[0], 0)


class TheAnswersThatCostTwelveOfTwentyFiveCalls(unittest.TestCase):
    """M1.121(a). Three answer shapes taken from the first real discovery run.

    None of them is malformed JSON. Every one of them defeated the original
    parser, which took the greedy first-`{`-to-last-`}` span and handed it
    straight to `json.loads` — so a source list after the object, or a second
    object anywhere in the text, made the whole call unparseable. **Half the
    spend of the first five runs bought nothing for this reason alone.**

    Retry-free on purpose: a retry would have hidden the defect behind a second
    paid call, which is the expensive way to not fix something.
    """

    PREAMBLE = (
        "Gerne! Ich habe im Web recherchiert und folgende Shops gefunden:\n\n"
        '{"shops": [{"domain": "beispiel.de", "name": "Beispiel"}]}'
    )
    FENCED = '```json\n{"shops": [{"domain": "beispiel.de", "name": "Beispiel"}]}\n```'
    TRAILING_SOURCES = (
        '{"shops": [{"domain": "beispiel.de", "name": "Beispiel"}]}\n\n'
        "Quellen:\n"
        '[1] {"title": "Beste Shops 2026", "url": "https://example.com/a"}\n'
        '[2] {"title": "Shop-Vergleich", "url": "https://example.com/b"}'
    )

    def test_all_three_now_parse_to_the_same_shop(self) -> None:
        for label, text in (
            ("preamble", self.PREAMBLE),
            ("fenced code block", self.FENCED),
            ("source list after the JSON", self.TRAILING_SOURCES),
        ):
            with self.subTest(shape=label):
                self.assertEqual(
                    discover_llm.parse_shops(text), [("beispiel.de", "Beispiel")]
                )

    def test_the_object_finder_is_shared_with_ai_visibility(self) -> None:
        """One expression. The drift between two copies is what this cost."""
        source = Path(discover_llm.__file__).read_text(encoding="utf-8")
        self.assertIn("llm.parse_last_json_object", source)
        self.assertNotIn("json.loads", source)

    def test_prose_with_no_object_at_all_is_still_none(self) -> None:
        """The lenient parser must not become a parser that invents a result:
        a call that returned no JSON was paid for and yielded nothing, and that
        stays distinguishable from an honest empty list (M1.59)."""
        self.assertIsNone(discover_llm.parse_shops("Ich konnte nichts finden."))
        self.assertEqual(discover_llm.parse_shops('{"shops": []}'), [])

    def test_the_prompt_names_the_three_shapes_it_must_not_produce(self) -> None:
        """The prompt is the cheaper half of the fix; the parser is the half
        that has to hold. Both shipped, and this asserts the cheaper half is
        actually about the measured failures."""
        prompt = discover_llm.SYSTEM_PROMPT
        self.assertIn("beginnt mit {", prompt)
        self.assertIn("Code-Fences", prompt)
        self.assertIn("Quellenangaben", prompt)
