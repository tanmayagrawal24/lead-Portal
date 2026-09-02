"""§5.5b's entry point, its verification backstop, and the gate around them.

**Nothing here contacts a provider.** `llm.LLMProvider` is a Protocol, so the
one paid surface takes an injected client and every test passes a fake. That is
what makes a paid stage testable under the CI M1.65 built, which forbids
`ANTHROPIC_API_KEY` outright.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portal import extract_p2, ledger, llm, verify
from portal.ledger import LedgerBypass

NOW = "2026-08-20T12:00:00Z"


class FakeProvider:
    """Unit 2's shape, driven from a test. Records what it was handed and
    returns a batch id; it cannot reach a network because there is nothing in
    it that could."""

    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.submitted: list[llm.BatchRequest] = []
        self.clearances: list[ledger.LedgerClearance] = []

    def limits(self) -> llm.ModelLimits:  # pragma: no cover - protocol shape
        return llm.limits_for("anthropic", "claude-haiku-4-5")

    def count_input_tokens(self, request: llm.BatchRequest) -> int:
        return len(request.user_text) // 4

    def submit_batch(self, requests, *, clearance: ledger.LedgerClearance) -> str:
        self.submitted = list(requests)
        self.clearances.append(clearance)
        return "batch_fake_1"

    def poll_batch(self, provider_batch_id: str):  # pragma: no cover
        raise NotImplementedError


class TestTheLedgerGateEngages(unittest.TestCase):
    """M1.83. `assert_ledger_guarded` has been correct and **unexercised** since
    Unit 7: no caller existed to prove it engages on a real path."""

    def test_the_paid_surface_refuses_without_a_clearance(self) -> None:
        with self.assertRaises(LedgerBypass):
            extract_p2.submit(FakeProvider(), [])

    def test_a_clearance_the_ledger_did_not_issue_is_not_accepted(self) -> None:
        """The gate takes a `LedgerClearance`, and only `ledger.check_ceiling`
        constructs one — a truthy stand-in is not a reading of the ledger."""
        with self.assertRaises(LedgerBypass):
            extract_p2.submit(FakeProvider(), [], clearance=object())

    def test_a_cleared_call_reaches_the_injected_provider(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from portal import db, migrate

        conn = db.connect(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        migrate.apply_pending(conn)

        clearance = ledger.check_ceiling(conn)
        provider = FakeProvider()
        request = llm.BatchRequest("impressum:1:1", "sys", "text", {}, 128)
        self.assertEqual(
            extract_p2.submit(provider, [request], clearance=clearance),
            "batch_fake_1",
        )
        self.assertEqual(provider.submitted, [request])

    def test_every_callable_is_classified(self) -> None:
        """The check that matters — a new paid path nobody declared. Re-run here
        rather than trusted from import, because the import-time assertion is
        the thing under test."""
        llm.assert_ledger_guarded(
            extract_p2,
            paid=extract_p2.PAID_SURFACES,
            free=extract_p2.FREE_SURFACES,
            where="test",
        )

    def test_an_unclassified_callable_fails_the_assertion(self) -> None:
        """The negative control, as a test: adding a paid path and forgetting to
        declare it must not be possible to ship."""
        with self.assertRaises(llm.LLMConfigError) as caught:
            llm.assert_ledger_guarded(
                extract_p2,
                paid=extract_p2.PAID_SURFACES,
                free=tuple(f for f in extract_p2.FREE_SURFACES if f != "clean"),
                where="test",
            )
        self.assertIn("neither paid nor free", str(caught.exception))


class TestInputPreparation(unittest.TestCase):
    def test_it_sends_cleaned_visible_text_not_raw_html(self) -> None:
        """M1.78. §5.5b's own input requirement *is* `visible_text`'s contract,
        and the choice fixes §10.2's base rate at the visible-text reading."""
        html = (
            "<html><head><script>var vendor='Inhaber Fake GmbH';</script></head>"
            "<body><p>Angaben gemäß § 5 TMG</p><style>b{}</style></body></html>"
        )
        sent, truncated = extract_p2.clean(html)
        self.assertIn("Angaben gemäß § 5 TMG", sent)
        self.assertNotIn("Inhaber Fake GmbH", sent)
        self.assertNotIn("<p>", sent)
        self.assertFalse(truncated)

    def test_the_60kb_cap_truncates_from_the_end(self) -> None:
        """§5.5b calls the cap *the primary defence against unbounded token
        spend*, and truncating from the end is deliberate: Impressum content is
        near the top of an Impressum page."""
        head = "OBEN Angaben gemäß § 5 TMG. "
        html = f"<html><body>{head}{'x' * 100_000} UNTEN</body></html>"
        sent, truncated = extract_p2.clean(html)
        self.assertTrue(truncated)
        self.assertIn("OBEN", sent)
        self.assertNotIn("UNTEN", sent)
        self.assertLessEqual(
            len(sent.encode("utf-8")),
            extract_p2.INPUT_CAP_BYTES + len(extract_p2.TRUNCATION_MARKER.encode()),
        )

    def test_the_cap_is_measured_in_bytes_not_characters(self) -> None:
        """The cap bounds what crosses the wire. A page of umlauts is two bytes
        a character, and a character count would send twice the budget."""
        html = f"<html><body>{'ä' * 40_000}</body></html>"
        sent, truncated = extract_p2.clean(html)
        self.assertTrue(truncated)
        self.assertLessEqual(len(sent) * 2, extract_p2.INPUT_CAP_BYTES + 200)


class TestBatchRequests(unittest.TestCase):
    def test_the_custom_id_carries_company_and_artifact(self) -> None:
        """M1.51: results come back in **arbitrary order**, and this is the only
        thing tying a returned legal name to the company it was read for.
        Substring verification does **not** catch a mis-key, because the values
        are genuinely present on the page they came from."""
        page = extract_p2.Prepared(
            7,
            "muster.de",
            42,
            "https://muster.de/impressum",
            "impressum",
            "text",
            False,
        )
        request = extract_p2.build_requests([page])[0]
        self.assertEqual(request.custom_id, "impressum:7:42")
        self.assertEqual(
            extract_p2.parse_custom_id(request.custom_id), ("impressum", 7, 42)
        )

    def test_the_prompt_discipline_is_verbatim_in_the_system_prompt(self) -> None:
        """§5.5b states it *verbatim in the system prompt of the extraction
        call*. It is part of the specification, not a prompt detail."""
        page = extract_p2.Prepared(1, "m.de", 1, "u", "impressum", "t", False)
        request = extract_p2.build_requests([page])[0]
        self.assertIn(extract_p2.PROMPT_DISCIPLINE, request.system)

    def test_the_schema_is_strict(self) -> None:
        """A field the model omits must be an error, not a silent None — §5.5b's
        null is an explicit null."""
        schema = extract_p2.impressum_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("legal_name", schema["required"])
        self.assertIn("managing_directors", schema["required"])

    def test_the_homepage_schema_carries_both_evidence_spans(self) -> None:
        """M1.49. A boolean has no string in it for a substring check to find;
        the span is what gives the check something to test."""
        schema = extract_p2.homepage_schema()
        self.assertIn("own_brand_evidence", schema["properties"])
        self.assertIn("owner_named_evidence", schema["properties"])

    def test_the_output_bound_is_inside_the_models_cap(self) -> None:
        """Haiku 4.5 caps output at 64K, the only current model below the 128K
        ceiling (M1.50), and `reserve_batch` refuses a request over it."""
        limits = llm.limits_for("anthropic", "claude-haiku-4-5")
        self.assertLessEqual(extract_p2.MAX_OUTPUT_TOKENS, limits.max_output_tokens)


class TestVerification(unittest.TestCase):
    """§5.5b's backstop, and the limit of what it buys."""

    PAGE = verify.PageText(
        "Musterhaus GmbH\n  Geschäftsführer: Anna Beispiel\n"
        "Wir führen unsere eigene Marke seit 2011."
    )

    def test_a_value_on_the_page_verifies(self) -> None:
        verdict = self.PAGE.check("legal_name", "Musterhaus GmbH")
        self.assertTrue(verdict.verified)
        self.assertEqual(verdict.confidence, verify.VERIFIED)

    def test_a_value_not_on_the_page_is_rejected_and_kept(self) -> None:
        """A2 §3: the rejected string stays in `value_text`, because a red row
        in §9 with no value tells the operator nothing to check. A person's name
        is the exception and the caller handles it — §8 keeps names in
        `contact`, and an unverified name creates no row."""
        verdict = self.PAGE.check("legal_name", "Erfunden AG")
        self.assertFalse(verdict.verified)
        self.assertEqual(verdict.confidence, verify.REJECTED)
        self.assertEqual(verdict.value, "Erfunden AG")

    def test_wrapped_whitespace_still_counts_as_present(self) -> None:
        self.assertTrue(self.PAGE.contains("Geschäftsführer: Anna Beispiel"))

    def test_nothing_else_is_normalised(self) -> None:
        """Every further normalisation makes the check **more likely to pass**,
        which is the wrong direction for a guard whose failure mode is a wrong
        name in a letter, and none has been measured on this corpus (M1.4)."""
        self.assertFalse(self.PAGE.contains("Musterhaus G.m.b.H."))
        self.assertFalse(self.PAGE.contains("Geschaeftsfuehrer: Anna Beispiel"))

    def test_it_cannot_reach_an_artifact(self) -> None:
        """The structural half of M1.43: there is no constructor that takes a
        path, an artifact id or a connection, so this check cannot be applied to
        a document the model was never shown."""
        self.assertEqual(verify.PageText.__slots__, ("_normalised", "raw"))

    def test_an_absent_value_is_neither_verified_nor_rejected(self) -> None:
        """§5.5b instructs the model to return `null` for a field not on the
        page, so a null is the model obeying. Recording it as a failed
        verification would put a rejection on a field correctly declined."""
        for absent in (None, "", "   "):
            with self.subTest(value=absent), self.assertRaises(ValueError):
                self.PAGE.contains(absent)

    def test_a_boolean_verifies_through_its_span(self) -> None:
        verdict = self.PAGE.check_boolean("own_brand", "unsere eigene Marke")
        self.assertIsNotNone(verdict)
        self.assertTrue(verdict.verified)

    def test_a_boolean_with_no_span_is_an_absence_not_a_rejection(self) -> None:
        """It routes to §6.1's third state — the same place a `null` boolean
        goes — rather than to a `confidence = 0` row."""
        self.assertIsNone(self.PAGE.check_boolean("own_brand", None))

    def test_a_span_that_is_not_on_the_page_is_rejected(self) -> None:
        verdict = self.PAGE.check_boolean("own_brand", "wir stellen selbst her")
        self.assertFalse(verdict.verified)

    def test_the_limit_of_the_guarantee_is_reachable(self) -> None:
        """**The case the guarantee cannot catch, pinned as a test.** The span
        is genuinely on the page and the inference from it may still be wrong: a
        homepage may contain *"unsere eigene Marke"* in a sentence about a brand
        it resells. The check passes. §5.5b says so, and this is the assertion
        that stops the guard being read as stronger than it is."""
        reseller = verify.PageText(
            "Wir vertreiben unsere eigene Marke NICHT – wir sind Händler."
        )
        self.assertTrue(
            reseller.check_boolean("own_brand", "unsere eigene Marke").verified
        )


if __name__ == "__main__":
    unittest.main()


class TestTheGateIsEnforcedWhereMoneyIsCommitted(unittest.TestCase):
    """Audit finding 1. `score --phase 1` wrote `gate.phase2_admitted` and the
    §7.1 spend model assumed it; `prepare` selected every company with a usable
    artifact regardless. The gate now sits on the one path every paid request
    passes through, and the three withheld states are named in the dry run."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from portal import db, migrate

        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "p.db")
        migrate.apply_pending(self.conn)
        self.root = self.tmp / "artifacts"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.conn.close()

    def _company(self, domain: str, *, excluded: bool = False) -> int:
        cur = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at, excluded, "
            "excluded_reason) VALUES (?, 'seed_csv', '2026-08-01T00:00:00Z', ?, ?)",
            (domain, int(excluded), "duplicate_site: x" if excluded else None),
        )
        company_id = int(cur.lastrowid or 0)
        body = self.root / f"{company_id}.html"
        body.write_text("<html><body>Impressum Muster GmbH 12345 Berlin</body></html>")
        for kind in ("homepage", "impressum"):
            self.conn.execute(
                "INSERT INTO artifact (company_id, kind, url, http_status, "
                "content_hash, body_path, fetched_at) VALUES (?,?,?,200,?,?,'x')",
                (
                    company_id,
                    kind,
                    f"https://{domain}/{kind}",
                    f"h-{kind}-{company_id}",
                    body.name,
                ),
            )
        robots = self.root / f"{company_id}-robots.txt"
        robots.write_text("User-agent: *\nAllow: /\n")
        self.conn.execute(
            "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
            "body_path, fetched_at) VALUES (?,'robots',?,200,?,?,'x')",
            (company_id, f"https://{domain}/robots.txt", f"r{company_id}", robots.name),
        )
        return company_id

    def _score(self, company_id: int, admitted: int, *, finished: bool = True) -> None:
        cur = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES "
            "(datetime('now'), ?, 'score-p1')",
            ("2026-08-02T00:00:00Z" if finished else None,),
        )
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, method, "
            "evidence_url, observed_at) VALUES (?,?,'gate.phase2_admitted',?,"
            "'deterministic','',datetime('now'))",
            (company_id, cur.lastrowid, admitted),
        )

    def test_only_admitted_companies_are_prepared_for_either_purpose(self) -> None:
        sent = self._company("sent.de")
        self._score(sent, 1)
        stopped = self._company("stopped.de")
        self._score(stopped, 0)
        self._company("unscored.de")
        excluded = self._company("dup.de", excluded=True)
        self._score(excluded, 1)  # admitted by score, but §6.4 excluded it

        for purpose in extract_p2.PURPOSES:
            prepared, skipped = extract_p2.prepare(
                self.conn, self.root, purpose=purpose
            )
            self.assertEqual([p.company_id for p in prepared], [sent])
            reasons = {s.domain: s.reason for s in skipped}
            self.assertIn("gate", reasons["stopped.de"])
            self.assertIn("not scored", reasons["unscored.de"])
            self.assertIn("excluded", reasons["dup.de"])

    def test_the_latest_finished_verdict_wins_and_a_crashed_run_is_ignored(
        self,
    ) -> None:
        company = self._company("flip.de")
        self._score(company, 0)
        self._score(company, 1)  # re-scored later: admitted now
        self._score(company, 0, finished=False)  # crashed run must not count
        admitted, withheld = extract_p2.eligible_companies(self.conn)
        self.assertEqual(admitted, {company})
        self.assertEqual(withheld, {})

    def test_build_requests_cannot_see_a_withheld_company(self) -> None:
        stopped = self._company("stopped.de")
        self._score(stopped, 0)
        prepared, _ = extract_p2.prepare(self.conn, self.root)
        self.assertEqual(extract_p2.build_requests(prepared), [])
