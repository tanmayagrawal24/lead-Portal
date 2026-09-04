"""§5.6 — reconciliation, restart survival, and §7's other half.

**Nothing here contacts a provider.** `llm.LLMProvider` is a Protocol, so the
fake below is the whole of the API surface these tests see, and the CI M1.65
built — which forbids `ANTHROPIC_API_KEY` outright — runs every one of them.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portal import db, extract_p2, ledger, llm, migrate, reconcile, score, verify

IMPRESSUM_HTML = """<html><body>
<h1>Impressum</h1>
<p>Musterhaus Handels GmbH</p>
<p>Musterstra&szlig;e 1, 40212 D&uuml;sseldorf, Deutschland</p>
<p>Gesch&auml;ftsf&uuml;hrer: Anna Beispiel</p>
<p>Amtsgericht D&uuml;sseldorf, HRB 12345</p>
<p>USt-IdNr.: DE123456789</p>
<script>var vendor = "Erika Unsichtbar";</script>
</body></html>"""

HOMEPAGE_HTML = """<html><body>
<h1>Musterhaus</h1>
<p>Wir f&uuml;hren unsere eigene Marke seit 2011.</p>
<p>Gegr&uuml;ndet von Anna Beispiel.</p>
<p>Kategorien: Lampen, Leuchtmittel</p>
</body></html>"""


def impressum_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "legal_name": "Musterhaus Handels GmbH",
        "legal_form": "GmbH",
        "street": "Musterstraße 1",
        "postal_code": "40212",
        "city": "Düsseldorf",
        "country": "Deutschland",
        "managing_directors": ["Anna Beispiel"],
        "owner_name": None,
        "register_court": "Amtsgericht Düsseldorf",
        "register_number": "HRB 12345",
        "vat_id": "DE123456789",
        "email": None,
        "phone": None,
    }
    payload.update(overrides)
    return payload


def homepage_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "one_line_offer": None,
        "product_categories": ["Lampen", "Leuchtmittel"],
        "audience": None,
        "owner_named_on_site": True,
        "owner_named_evidence": "Gegründet von Anna Beispiel",
        "own_brand": True,
        "own_brand_evidence": "Wir führen unsere eigene Marke",
        "agency_credit": None,
    }
    payload.update(overrides)
    return payload


def _usage() -> llm.Usage:
    return llm.Usage(input_tokens=5_000, output_tokens=400)


class FakeProvider:
    """Unit 2's shape, driven from a test.

    It records what it was handed and replays a scripted `poll_batch`. There is
    nothing in it that could reach a network.
    """

    name = "anthropic"
    model = "claude-haiku-4-5"

    def __init__(self, *, batch_id: str = "msgbatch_fake") -> None:
        self.batch_id = batch_id
        self.submitted: list[llm.BatchRequest] = []
        self.polls: list[str] = []
        self.script: list[llm.BatchResult] = []

    # -- Protocol ------------------------------------------------------
    def limits(self) -> llm.ModelLimits:
        return llm.limits_for("anthropic", "claude-haiku-4-5")

    def count_input_tokens(self, request: llm.BatchRequest) -> int:
        return max(1, len(request.user_text) // 4)

    def token_counter(self) -> llm.TokenCounter:
        def count(*, system: str, user_text: str) -> int:
            return max(1, (len(system) + len(user_text)) // 4)

        return count

    def submit_batch(self, requests, *, clearance: ledger.LedgerClearance) -> str:
        self.submitted = list(requests)
        return self.batch_id

    def poll_batch(self, provider_batch_id: str) -> llm.BatchResult:
        self.polls.append(provider_batch_id)
        if not self.script:
            raise AssertionError("poll_batch called with nothing scripted")
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]

    # -- scripting -----------------------------------------------------
    def will_return(self, *items: llm.BatchResultItem, expected=None) -> None:
        expected = [i.custom_id for i in items] if expected is None else expected
        self.script.append(
            llm.BatchResult(
                self.batch_id,
                llm.resolve_batch_status(list(items), expected=expected),
                tuple(items),
            )
        )

    def will_still_be_processing(self) -> None:
        self.script.append(
            llm.BatchResult(self.batch_id, llm.BatchStatus.SUBMITTED, ())
        )


def succeeded(custom_id: str, payload: dict[str, object]) -> llm.BatchResultItem:
    return llm.BatchResultItem(
        custom_id,
        llm.RequestOutcome.SUCCEEDED,
        extraction=llm.Extraction(
            custom_id=custom_id,
            payload=payload,
            usage=_usage(),
            model="claude-haiku-4-5-20251001",
        ),
    )


class ReconcileTestCase(unittest.TestCase):
    """A database with one company, its two stored pages, and an extract-p2 run.

    Deliberately built through the real writers — `extract_p2.prepare` picks the
    artifacts and `reserve_and_submit` makes the reservation — so a test that
    passes here is a test about the code, not about a fixture that happens to
    look like it.
    """

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
        self.company_id = self._company("muster.de")
        self._artifact(self.company_id, "robots", "https://muster.de/robots.txt", "")
        self._artifact(
            self.company_id,
            "impressum",
            "https://muster.de/impressum",
            IMPRESSUM_HTML,
        )
        self._artifact(self.company_id, "homepage", "https://muster.de/", HOMEPAGE_HTML)

    # -- fixture writers ------------------------------------------------
    def _company(self, domain: str, *, admitted: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES (?, 'seed_csv', '2026-08-01T00:00:00Z')",
            (domain,),
        )
        company_id = int(cur.lastrowid or 0)
        if admitted:
            self._admit(company_id)
        return company_id

    def _admit(self, company_id: int, verdict: int = 1) -> int:
        """§5.4's verdict, as a finished `score-p1` run writes it. `prepare`
        sends nothing the gate has not admitted (audit finding 1), so every
        fixture company that is meant to be sent needs one."""
        run_id = self._run("score-p1")
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, method, "
            "evidence_url, observed_at) VALUES (?,?,'gate.phase2_admitted',?,"
            "'deterministic','',datetime('now'))",
            (company_id, run_id, verdict),
        )
        return run_id

    def _artifact(self, company_id: int, kind: str, url: str, body: str) -> int:
        name = f"{company_id}-{kind}-{abs(hash(url)) % 10_000}.html"
        (self.artifacts / name).write_text(body, encoding="utf-8")
        cur = self.conn.execute(
            "INSERT INTO artifact (company_id, kind, url, http_status, "
            "content_hash, body_path, fetched_at) VALUES (?,?,?,200,?,?,?)",
            (company_id, kind, url, f"h{kind}{company_id}", name, "2026-08-01"),
        )
        return int(cur.lastrowid or 0)

    def _run(self, stage: str = "extract_p2", *, days_ago: int = 0) -> int:
        cur = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) "
            "VALUES (datetime('now', ?), datetime('now'), ?)",
            (f"-{days_ago} days", stage),
        )
        return int(cur.lastrowid or 0)

    # -- the submitting half --------------------------------------------
    def submit(
        self,
        provider: FakeProvider,
        *,
        purpose: str = "impressum",
        run_id: int | None = None,
    ) -> extract_p2.Reservation:
        run_id = self._run() if run_id is None else run_id
        prepared, _ = extract_p2.prepare(self.conn, self.artifacts, purpose=purpose)
        self.assertTrue(prepared, f"no {purpose} page was selected")
        return extract_p2.reserve_and_submit(
            self.conn,
            provider,
            prepared,
            run_id=run_id,
            purpose=purpose,
            clearance=ledger.check_ceiling(self.conn),
        )

    def custom_id(self, purpose: str = "impressum") -> str:
        row = self.conn.execute(
            "SELECT custom_id FROM llm_batch_request ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return str(row["custom_id"])

    def signals(self) -> dict[str, sqlite3.Row]:
        return {
            str(r["key"]): r
            for r in self.conn.execute("SELECT * FROM signal ORDER BY id")
        }


# ══════════════════════════════════════════════════════════════════════
# (b) RESTART SURVIVAL — the done-when that actually bites
# ══════════════════════════════════════════════════════════════════════


class RestartSurvival(ReconcileTestCase):
    """§5.6: a batch takes up to 24 hours and its results stay retrievable for
    29 days, so the process that submitted it is routinely gone.

    **A test that keeps the batch object in a variable proves nothing.** Every
    test in this class throws the submitting process's state away — the
    `Reservation`, the `Prepared` list, the requests, and the CONNECTION — and
    reconciles from a fresh connection that has never seen any of it.
    """

    def _submit_then_discard(self, purpose: str = "impressum") -> None:
        provider = FakeProvider()
        self.submit(provider, purpose=purpose)
        self.conn.commit()
        # Everything the submitting process knew, gone. What survives is on disk.
        self.conn.close()
        del provider

    def _fresh(self) -> sqlite3.Connection:
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def test_reconcile_finds_its_work_with_no_state_carried_from_submit(
        self,
    ) -> None:
        self._submit_then_discard()
        conn = self._fresh()

        open_batches = reconcile.open_batches(conn)
        self.assertEqual(len(open_batches), 1)
        batch = open_batches[0]
        self.assertEqual(batch.status, "submitted")
        self.assertEqual(batch.purpose, "impressum")
        self.assertEqual(batch.provider_batch_id, "msgbatch_fake")

        # And the request SET, which is what §5.6 fact 2 is a rule about.
        requests = reconcile.requests_of(conn, batch.id)
        self.assertEqual(len(requests), batch.request_count)
        self.assertEqual(requests[0].company_id, self.company_id)

    def test_a_full_reconcile_runs_off_the_database_alone(self) -> None:
        self._submit_then_discard()
        conn = self._fresh()
        batch = reconcile.open_batches(conn)[0]
        custom_id = reconcile.requests_of(conn, batch.id)[0].custom_id

        collector = FakeProvider()
        collector.will_return(succeeded(custom_id, impressum_payload()))
        result = reconcile.run(conn, collector, self.artifacts)

        self.assertEqual(len(result.batches), 1)
        report = result.batches[0]
        self.assertEqual(report.status_after, "reconciled")
        self.assertTrue(report.signals_written)
        keys = {str(r["key"]) for r in conn.execute("SELECT key FROM signal")}
        self.assertIn("impressum.legal_name", keys)
        self.assertIn("llm.impressum_extracted", keys)

    def test_the_sent_text_is_reproduced_and_the_digest_proves_it(self) -> None:
        """M1.87. The reconstruction is an argument about today's build; the
        stored digest is what makes it a check."""
        self._submit_then_discard()
        conn = self._fresh()
        batch = reconcile.open_batches(conn)[0]
        request = reconcile.requests_of(conn, batch.id)[0]

        sent = reconcile.sent_text_for(conn, self.artifacts, request)
        self.assertIsNotNone(sent)
        assert sent is not None
        self.assertEqual(extract_p2.sha256_of(sent), request.sent_text_sha256)
        # And it is the CLEANED text, which is what the model was shown: the
        # `<script>` name is not in it, so it cannot verify anything (M1.78).
        self.assertIn("Musterhaus Handels GmbH", sent)
        self.assertNotIn("Erika Unsichtbar", sent)

    def test_an_unreproducible_page_writes_no_value_and_closes_the_request(
        self,
    ) -> None:
        """The digest failing is not a hypothetical: `reconcile` may run under a
        later build than the one that submitted, and `parsers.visible_text` is
        the thing that would move (M1.87). Simulated here by moving it."""
        self._submit_then_discard()
        conn = self._fresh()
        batch = reconcile.open_batches(conn)[0]
        request = reconcile.requests_of(conn, batch.id)[0]

        row = conn.execute(
            "SELECT body_path FROM artifact WHERE id = ?", (request.artifact_id,)
        ).fetchone()
        (self.artifacts / str(row["body_path"])).write_text(
            "<html><body>etwas ganz anderes</body></html>", encoding="utf-8"
        )
        self.assertIsNone(reconcile.sent_text_for(conn, self.artifacts, request))

        collector = FakeProvider()
        collector.will_return(succeeded(request.custom_id, impressum_payload()))
        report = reconcile.run(conn, collector, self.artifacts).batches[0]

        self.assertEqual(report.signals_written, 0)
        self.assertEqual(report.contacts_written, 0)
        self.assertEqual(report.dispositions, {reconcile.TEXT_UNREPRODUCIBLE: 1})
        # Terminal, deliberately: re-running would re-clean the same bytes with
        # the same code and fail the same way, so a retryable disposition would
        # leave the batch open forever.
        self.assertTrue(report.closed)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM signal WHERE method = 'llm'").fetchone()[
                0
            ],
            0,
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM contact").fetchone()[0], 0)

    def test_running_it_twice_writes_nothing_the_second_time(self) -> None:
        """§5.6: *"safe to run repeatedly"*, and it must hold across the process
        boundary as well as within one."""
        self._submit_then_discard()
        conn = self._fresh()
        custom_id = reconcile.requests_of(conn, reconcile.open_batches(conn)[0].id)[
            0
        ].custom_id

        collector = FakeProvider()
        collector.will_return(succeeded(custom_id, impressum_payload()))
        reconcile.run(conn, collector, self.artifacts)
        first_signals = conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0]
        first_spend = ledger.monthly_spend_usd(conn)

        conn.close()
        again = self._fresh()
        second = reconcile.run(again, FakeProvider(), self.artifacts)

        self.assertEqual(second.batches, [])  # closed, so not polled at all
        self.assertEqual(
            again.execute("SELECT COUNT(*) FROM signal").fetchone()[0],
            first_signals,
        )
        self.assertAlmostEqual(ledger.monthly_spend_usd(again), first_spend)


# ══════════════════════════════════════════════════════════════════════
# (a) VERIFICATION AGAINST THE SENT TEXT
# ══════════════════════════════════════════════════════════════════════


class VerificationAgainstTheSentText(ReconcileTestCase):
    def _reconcile(self, payload: dict[str, object], purpose: str = "impressum"):
        provider = FakeProvider()
        self.submit(provider, purpose=purpose)
        custom_id = self.custom_id(purpose)
        provider.will_return(succeeded(custom_id, payload))
        return reconcile.run(self.conn, provider, self.artifacts).batches[0]

    def test_a_value_on_the_page_lands_verified(self) -> None:
        self._reconcile(impressum_payload())
        row = self.signals()["impressum.legal_name"]
        self.assertEqual(row["value_text"], "Musterhaus Handels GmbH")
        self.assertEqual(row["confidence"], verify.VERIFIED)
        self.assertEqual(row["method"], "llm")

    def test_a_value_not_on_the_page_lands_at_confidence_zero(self) -> None:
        """§5.5b: *"a value not literally present on the page is discarded and
        the signal written with `confidence=0` for review"*. The rejected string
        stays in `value_text` (A2 §3) — a red row with no value tells the
        operator nothing to go and check."""
        self._reconcile(impressum_payload(legal_name="Erfundene Handels AG"))
        row = self.signals()["impressum.legal_name"]
        self.assertEqual(row["value_text"], "Erfundene Handels AG")
        self.assertEqual(row["confidence"], verify.REJECTED)

    def test_a_value_only_in_a_script_block_is_rejected(self) -> None:
        """The instrument is the point (M1.78). `visible_text` decomposes
        `<script>`, so a name the model could only have got from JSON-LD is not
        in the sent text and cannot verify against it."""
        self._reconcile(impressum_payload(legal_name="Erika Unsichtbar"))
        self.assertEqual(
            self.signals()["impressum.legal_name"]["confidence"], verify.REJECTED
        )

    def test_verification_cannot_reach_a_different_page(self) -> None:
        """§5.5b's structural rule, restated as a test: the Impressum's own
        legal name does not verify against the HOMEPAGE's text, so a reconcile
        that reached for the wrong artifact would be caught rather than pass
        silently (M1.43)."""
        homepage_text, _ = extract_p2.clean(HOMEPAGE_HTML)
        self.assertFalse(
            verify.PageText(homepage_text).contains("Musterhaus Handels GmbH")
        )

    def test_the_stage_fact_is_written_whatever_came_back(self) -> None:
        """The load-bearing row. Every A7 guard works by declining to write, so
        without a positive fact beside the silence the read model cannot tell
        *the model could not tell* from *Phase 2 never ran here*."""
        self._reconcile(
            impressum_payload(legal_name=None, managing_directors=[], legal_form=None)
        )
        fact = self.signals()["llm.impressum_extracted"]
        self.assertEqual(fact["value_num"], 1)
        self.assertEqual(fact["value_text"], "claude-haiku-4-5-20251001")
        # confidence = 1 always: a stage cannot be wrong about whether it ran,
        # so this row survives migration 012's filter and keeps "rejected"
        # distinguishable from "never ran".
        self.assertEqual(fact["confidence"], verify.VERIFIED)

    def test_a_boolean_is_verified_through_its_evidence_span(self) -> None:
        """M1.47 / M1.49, and the limit of what it buys is in `verify`'s note."""
        self._reconcile(homepage_payload(), purpose="homepage")
        row = self.signals()["brand.own_brand"]
        self.assertEqual(row["value_num"], 1)
        self.assertEqual(row["confidence"], verify.VERIFIED)

    def test_a_boolean_whose_span_is_not_on_the_page_is_rejected(self) -> None:
        self._reconcile(
            homepage_payload(own_brand_evidence="Wir stellen alles selbst her"),
            purpose="homepage",
        )
        self.assertEqual(
            self.signals()["brand.own_brand"]["confidence"], verify.REJECTED
        )

    def test_a_boolean_with_no_span_is_rejected_rather_than_trusted(self) -> None:
        """A judgement with nothing behind it. The one case worth naming."""
        self._reconcile(homepage_payload(own_brand_evidence=None), purpose="homepage")
        self.assertEqual(
            self.signals()["brand.own_brand"]["confidence"], verify.REJECTED
        )

    def test_a_null_boolean_writes_no_row_at_all(self) -> None:
        """§5.5b instructs the model to return `null` for a field it cannot
        find, and a `null` is the model OBEYING. Writing nothing is what makes
        §6.1's third state reachable (M1.81)."""
        self._reconcile(
            homepage_payload(own_brand=None, own_brand_evidence=None),
            purpose="homepage",
        )
        self.assertNotIn("brand.own_brand", self.signals())
        self.assertIn("llm.homepage_extracted", self.signals())

    def test_the_signals_carry_the_artifact_they_were_read_off(self) -> None:
        """M1.42: `evidence_url` and `artifact_id` come out of one expression, so
        they cannot name different documents."""
        self._reconcile(impressum_payload())
        row = self.signals()["impressum.legal_name"]
        artifact = self.conn.execute(
            "SELECT url FROM artifact WHERE id = ?", (row["artifact_id"],)
        ).fetchone()
        self.assertEqual(row["evidence_url"], artifact["url"])
        self.assertEqual(row["evidence_url"], "https://muster.de/impressum")

    def test_signals_are_written_under_the_submitting_runs_id(self) -> None:
        """B4. Under a fresh id the unique index could not dedupe, and *"safe to
        run repeatedly"* would be a claim rather than a property."""
        provider = FakeProvider()
        reservation = self.submit(provider)
        submitting_run = self.conn.execute(
            "SELECT run_id FROM llm_batch WHERE id = ?", (reservation.batch_id,)
        ).fetchone()["run_id"]
        provider.will_return(succeeded(self.custom_id(), impressum_payload()))
        result = reconcile.run(self.conn, provider, self.artifacts)

        self.assertNotEqual(result.run_id, submitting_run)
        for row in self.conn.execute(
            "SELECT DISTINCT run_id FROM signal WHERE method = 'llm'"
        ):
            self.assertEqual(row["run_id"], submitting_run)
        # The reconciling run still gets its own row, for its own timestamps.
        stage = self.conn.execute(
            "SELECT stage FROM run WHERE id = ?", (result.run_id,)
        ).fetchone()["stage"]
        self.assertEqual(stage, "reconcile")

    def test_a_mis_keyed_result_is_refused_rather_than_attributed(self) -> None:
        """M1.51 / M1.17. Substring verification **cannot** catch this, because
        the values genuinely are on the page they came from — so the key is
        checked against the row that stored it, which is the one guard that
        can."""
        provider = FakeProvider()
        self.submit(provider)
        self.conn.execute("UPDATE llm_batch_request SET custom_id = 'impressum-999-1'")
        provider.will_return(succeeded("impressum-999-1", impressum_payload()))
        with self.assertRaises(reconcile.ReconcileError) as caught:
            reconcile.run(self.conn, provider, self.artifacts)
        self.assertIn("does not agree", str(caught.exception))


# ══════════════════════════════════════════════════════════════════════
# (c) THE `contact` WRITER
# ══════════════════════════════════════════════════════════════════════


class TheContactWriter(ReconcileTestCase):
    """§10.6: *"`contact` — ahead of its writer; M5 writes it from verified
    Impressum names (§5.5b)"*. Re-derived before it was built: `grep -rn
    "INSERT INTO contact" portal/` returned nothing, so the row was accurate."""

    def _reconcile(self, payload: dict[str, object]) -> reconcile.BatchReport:
        provider = FakeProvider()
        self.submit(provider)
        provider.will_return(succeeded(self.custom_id(), payload))
        return reconcile.run(self.conn, provider, self.artifacts).batches[0]

    def contacts(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM contact ORDER BY id"))

    def test_a_verified_director_becomes_a_contact_row(self) -> None:
        report = self._reconcile(impressum_payload())
        self.assertEqual(report.contacts_written, 1)
        row = self.contacts()[0]
        self.assertEqual(row["full_name"], "Anna Beispiel")
        self.assertEqual(row["role"], "Geschäftsführer")
        # §4: *"must be the Impressum URL"*. A synthesised one would make the
        # Art. 14 notice unanswerable.
        self.assertEqual(row["source_url"], "https://muster.de/impressum")
        self.assertIsNotNone(row["purge_after"])
        self.assertGreater(row["purge_after"], row["collected_at"])

    def test_an_unverified_name_creates_no_contact_row_anywhere(self) -> None:
        """`verify`'s own note, and it is the one place §5.5b's *"write it with
        `confidence = 0`"* does NOT apply: a person's name the tool does not
        believe is not a measurement of a company, it is personal data about
        someone who may not exist (§8)."""
        report = self._reconcile(
            impressum_payload(managing_directors=["Erika Erfunden"])
        )
        self.assertEqual(report.contacts_written, 0)
        self.assertEqual(self.contacts(), [])
        # And the loss is visible as a smaller number rather than as silence:
        # the count is over names that PASSED, so no key is written at all.
        self.assertNotIn("impressum.gf_count", self.signals())

    def test_the_gf_count_counts_only_verified_names(self) -> None:
        """M1.46's invariant where it is produced. `qual.owner_operated`
        disjunct 2 is +15 and requires `1 <= n <= 2`; a hallucinated third
        director would push a real company out of its own rule."""
        self._reconcile(
            impressum_payload(managing_directors=["Anna Beispiel", "Erika Erfunden"])
        )
        self.assertEqual(self.signals()["impressum.gf_count"]["value_num"], 1)
        self.assertEqual([r["full_name"] for r in self.contacts()], ["Anna Beispiel"])

    def test_an_owner_is_written_with_the_inhaber_role(self) -> None:
        self._reconcile(
            impressum_payload(managing_directors=[], owner_name="Anna Beispiel")
        )
        row = self.contacts()[0]
        self.assertEqual(row["role"], "Inhaber")
        # §5.5b maps the field to a 0/1 PRESENCE marker, not to the name —
        # §10.2's lever, deliberately read by no rule.
        marker = self.signals()["impressum.owner_name_present"]
        self.assertEqual(marker["value_num"], 1)
        self.assertIsNone(marker["value_text"])

    def test_reconciling_twice_does_not_duplicate_a_person(self) -> None:
        self._reconcile(impressum_payload())
        reconcile.run(self.conn, FakeProvider(), self.artifacts)
        self.assertEqual(len(self.contacts()), 1)

    def test_a_verified_impressum_fills_empty_company_columns_only(self) -> None:
        """A2's fill-if-NULL: the LLM does not overwrite what `fetch` or a seed
        established. The one field where it wins on disagreement is
        `legal_form`, resolved inside `company_profile`'s COALESCE rather than
        by an UPDATE racing the view."""
        self.conn.execute(
            "UPDATE company SET city = 'Köln' WHERE id = ?", (self.company_id,)
        )
        self._reconcile(impressum_payload())
        row = self.conn.execute(
            "SELECT city, postal_code, legal_name FROM company WHERE id = ?",
            (self.company_id,),
        ).fetchone()
        self.assertEqual(row["city"], "Köln")  # not overwritten
        self.assertEqual(row["postal_code"], "40212")  # was NULL, filled
        self.assertEqual(row["legal_name"], "Musterhaus Handels GmbH")

    def test_a_rejected_city_does_not_reach_the_company_row(self) -> None:
        self._reconcile(impressum_payload(city="Erfundenstadt", postal_code=None))
        row = self.conn.execute(
            "SELECT city FROM company WHERE id = ?", (self.company_id,)
        ).fetchone()
        self.assertIsNone(row["city"])
        self.assertEqual(
            self.signals()["impressum.city"]["confidence"], verify.REJECTED
        )


# ══════════════════════════════════════════════════════════════════════
# (e) EXPIRY, PARTIAL RESULTS, AND THE RESERVATION'S FATE
# ══════════════════════════════════════════════════════════════════════


class ExpiryAndPartialResults(ReconcileTestCase):
    """§5.6: *"a batch can END NORMALLY while carrying requests that were never
    processed"*, and M1.86's addition — it can also return fewer results than it
    was sent. The ordinary case, not the exotic one."""

    def _two_companies(self) -> tuple[FakeProvider, list[str]]:
        second = self._company("zweite.de")
        self._artifact(second, "robots", "https://zweite.de/robots.txt", "")
        self._artifact(
            second, "impressum", "https://zweite.de/impressum", IMPRESSUM_HTML
        )
        provider = FakeProvider()
        self.submit(provider)
        ids = [
            str(r["custom_id"])
            for r in self.conn.execute(
                "SELECT custom_id FROM llm_batch_request ORDER BY id"
            )
        ]
        self.assertEqual(len(ids), 2)
        return provider, ids

    def test_an_expired_member_closes_the_batch_as_expired(self) -> None:
        provider, ids = self._two_companies()
        provider.will_return(
            succeeded(ids[0], impressum_payload()),
            llm.BatchResultItem(ids[1], llm.RequestOutcome.EXPIRED),
        )
        report = reconcile.run(self.conn, provider, self.artifacts).batches[0]

        self.assertEqual(report.status_after, "expired")
        self.assertEqual(report.dispositions, {"succeeded": 1, "expired": 1})
        # §5.6: expired members are re-submittable as exactly those members, and
        # re-submission is NEW spend that §7 reserves like any other.
        self.assertEqual(report.resubmittable, (ids[1],))

    def test_the_expired_members_reservation_comes_back_by_arithmetic(
        self,
    ) -> None:
        """(e)'s answer, and it needed no special case. An expired request
        consumed nothing, so it contributes nothing to the measured actual, and
        §7 control 12's correction hands its share back. **Nothing releases a
        reservation by rule** — only a measured actual (migration 014)."""
        provider, ids = self._two_companies()
        reserved = ledger.monthly_spend_usd(self.conn)
        self.assertGreater(reserved, 0)

        provider.will_return(
            succeeded(ids[0], impressum_payload()),
            llm.BatchResultItem(ids[1], llm.RequestOutcome.EXPIRED),
        )
        report = reconcile.run(self.conn, provider, self.artifacts).batches[0]

        self.assertLess(report.ledger_delta_usd, 0)
        self.assertAlmostEqual(
            ledger.monthly_spend_usd(self.conn),
            reserved + report.ledger_delta_usd,
        )
        self.assertAlmostEqual(
            report.actual_cost_usd or 0.0,
            reserved + report.ledger_delta_usd,
        )

    def test_a_short_result_set_keeps_the_batch_open_and_names_who_is_missing(
        self,
    ) -> None:
        """**M1.86.** The version that resolved against the returned items said
        `reconciled` here, and `resubmittable` said nothing: one company
        silently unextracted, named nowhere."""
        provider, ids = self._two_companies()
        provider.will_return(succeeded(ids[0], impressum_payload()), expected=ids)
        report = reconcile.run(self.conn, provider, self.artifacts).batches[0]

        self.assertEqual(report.status_after, "completed")
        self.assertFalse(report.closed)
        self.assertEqual(report.still_owed, (ids[1],))
        # The reservation is NOT released while anything is owed.
        self.assertEqual(report.ledger_delta_usd, 0.0)

    def test_a_batch_that_is_still_processing_is_left_alone(self) -> None:
        provider = FakeProvider()
        self.submit(provider)
        before = ledger.monthly_spend_usd(self.conn)
        provider.will_still_be_processing()
        report = reconcile.run(self.conn, provider, self.artifacts).batches[0]

        self.assertEqual(report.status_after, "submitted")
        self.assertEqual(report.signals_written, 0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), before)

    def test_a_short_batch_closes_on_the_second_poll_and_corrects_once(
        self,
    ) -> None:
        """The whole point of `reconciled_at` and of the running total: the
        correction is applied exactly once, when the batch closes."""
        provider, ids = self._two_companies()
        reserved = ledger.monthly_spend_usd(self.conn)
        provider.will_return(succeeded(ids[0], impressum_payload()), expected=ids)
        provider.will_return(
            succeeded(ids[0], impressum_payload()),
            succeeded(ids[1], impressum_payload()),
        )
        first = reconcile.run(self.conn, provider, self.artifacts).batches[0]
        self.assertEqual(first.ledger_delta_usd, 0.0)

        second = reconcile.run(self.conn, provider, self.artifacts).batches[0]
        self.assertEqual(second.status_after, "reconciled")
        self.assertNotEqual(second.ledger_delta_usd, 0.0)
        self.assertAlmostEqual(
            ledger.monthly_spend_usd(self.conn), reserved + second.ledger_delta_usd
        )
        # Tokens are a running SUM on the run, so the first pass's usage must
        # not be added twice — M1.69's double-count, one table over.
        tokens = self.conn.execute(
            "SELECT llm_input_tokens FROM run WHERE id = "
            "(SELECT run_id FROM llm_batch LIMIT 1)"
        ).fetchone()[0]
        self.assertEqual(tokens, 2 * _usage().input_tokens)

    def test_a_dry_key_is_its_own_status_and_not_a_failure(self) -> None:
        """§7 control 11, end to end. *"The provider failed"* and *"we ran out
        of money"* need different operator responses, and one of them is not an
        engineering task. Before migration 014 this INSERT was a CHECK
        violation."""
        provider, ids = self._two_companies()
        provider.will_return(
            succeeded(ids[0], impressum_payload()),
            llm.BatchResultItem(ids[1], llm.RequestOutcome.BALANCE_EXHAUSTED),
        )
        report = reconcile.run(self.conn, provider, self.artifacts).batches[0]

        self.assertEqual(report.status_after, "balance_exhausted")
        stored = self.conn.execute(
            "SELECT status FROM llm_batch WHERE id = ?", (report.batch_id,)
        ).fetchone()["status"]
        self.assertEqual(stored, "balance_exhausted")
        # Whatever succeeded before the key ran dry is written and paid for.
        self.assertIn("impressum.legal_name", self.signals())

    def test_an_invalid_request_closes_without_being_retried(self) -> None:
        """M1.51 fact 3: malformed will be malformed again, so a retry is spend
        with a known outcome."""
        provider, ids = self._two_companies()
        provider.will_return(
            succeeded(ids[0], impressum_payload()),
            llm.BatchResultItem(
                ids[1], llm.RequestOutcome.INVALID_REQUEST, error_message="bad"
            ),
        )
        report = reconcile.run(self.conn, provider, self.artifacts).batches[0]
        self.assertEqual(report.status_after, "reconciled")
        self.assertEqual(report.resubmittable, ())


# ══════════════════════════════════════════════════════════════════════
# §5.5b's MAPPING, DECLARED
# ══════════════════════════════════════════════════════════════════════


class TheMappingIsDeclared(unittest.TestCase):
    """A2's table, and the two audits M1.76 ran on it, kept honest by a test."""

    def test_no_key_outside_the_declaration_can_be_written(self) -> None:
        writer = reconcile._SignalWriter(
            verify.PageText("text"), reconcile.IMPRESSUM_KEYS
        )
        with self.assertRaises(reconcile.ReconcileError):
            writer.emit("impressum.invented", num=1.0, text=None, confidence=1.0)

    def test_the_two_declarations_do_not_overlap(self) -> None:
        """Each key has one writer. Two would be A3's shape (M1.77) on a key
        with no merge rule stated."""
        self.assertFalse(set(reconcile.IMPRESSUM_KEYS) & set(reconcile.HOMEPAGE_KEYS))

    def test_the_unscored_hint_key_is_declared_and_read_by_nothing(self) -> None:
        """A3 / M1.77: `agency.footer_credit_llm` is written and **no §6 rule may
        read it**. Withholding the reader IS the guard — §10.4's platform
        vocabulary is a testable regex and is not restatable in a prompt."""
        self.assertIn("agency.footer_credit_llm", reconcile.HOMEPAGE_KEYS)
        for rule in __import__("portal.ruleset", fromlist=["RULES"]).RULES:
            self.assertNotIn("agency.footer_credit_llm", rule.reads)

    def test_the_contact_only_fields_are_never_signal_keys(self) -> None:
        """§5.5b's *"never a signal"* column. A street and a VAT id are
        registration data about a named business, not measurements of it."""
        declared = set(reconcile.IMPRESSUM_KEYS) | set(reconcile.HOMEPAGE_KEYS)
        for field_name in reconcile.CONTACT_ONLY_FIELDS:
            self.assertFalse(
                any(key.endswith(f".{field_name}") for key in declared),
                f"{field_name} reached the signal vocabulary",
            )


# ══════════════════════════════════════════════════════════════════════
# WHAT §6 DOES WITH WHAT WAS WRITTEN
# ══════════════════════════════════════════════════════════════════════


class ScoringReadsTheResult(ReconcileTestCase):
    """The end of the chain: a reconciled value reaching (or not reaching) a
    score, through migration 012's filter and M1.81's three states."""

    def _reconcile_homepage(self, **overrides: object) -> None:
        provider = FakeProvider()
        self.submit(provider, purpose="homepage")
        provider.will_return(
            succeeded(self.custom_id("homepage"), homepage_payload(**overrides))
        )
        reconcile.run(self.conn, provider, self.artifacts)

    def _profile(self) -> dict[str, object]:
        """`company_profile` as a plain mapping — `score.evaluate`'s `Profile`.

        The view is read here rather than a dict being assembled, because the
        thing under test is that migration 012's filter and M1.81's predicates
        agree about a value `reconcile` actually wrote.
        """
        row = self.conn.execute(
            "SELECT * FROM company_profile WHERE company_id = ?",
            (self.company_id,),
        ).fetchone()
        return dict(row)

    def test_a_verified_boolean_reaches_the_view_and_fires_its_rule(self) -> None:
        self._reconcile_homepage()
        profile = self._profile()
        self.assertEqual(profile["own_brand"], 1)
        self.assertEqual(profile["homepage_extracted"], 1)
        result = score.evaluate(profile, dt.date(2026, 8, 20))
        fired = {c.rule_id for c in result.awarded}
        self.assertIn("qual.own_brand", fired)

    def test_a_rejected_boolean_is_filtered_out_and_the_rule_abstains(
        self,
    ) -> None:
        """A4 (M1.79) and M1.81 together. The rejected row is removed from the
        read model, the stage fact survives because it carries `confidence = 1`,
        and the rule therefore reaches its THIRD state — abstain, with a review
        reason — rather than reading the rejection as *absent*."""
        self._reconcile_homepage(own_brand_evidence="frei erfunden")
        profile = self._profile()
        self.assertIsNone(profile["own_brand"])  # filtered by migration 012
        self.assertEqual(profile["homepage_extracted"], 1)  # survived

        result = score.evaluate(profile, dt.date(2026, 8, 20))
        abstained = {c.rule_id for c in result.abstentions}
        self.assertIn("qual.own_brand", abstained)
        self.assertIn("own_brand_undetermined", {f.reason for f in result.review_flags})
        # The row is still in `signal`, which is what makes the loss visible.
        self.assertEqual(
            self.signals()["brand.own_brand"]["confidence"], verify.REJECTED
        )


# ══════════════════════════════════════════════════════════════════════
# M1.72 — §7 control 4's TWO WRITES, and the one fail-open path in §7
# ══════════════════════════════════════════════════════════════════════


class TheReservationIsOneTransaction(ReconcileTestCase):
    """§7 control 4 reserves into `llm_batch.est_cost_usd` **and**
    `run.est_cost_usd`; control 2's ledger sums `run` alone (M1.69 — summing
    both halves the ceiling). A crash between them leaves the batch committed
    and the ledger blind to it, which **under-counts** the rolling total.

    That is the one fail-OPEN path in a section where every other failure is
    biased to abort, which is why M1.72 calls committing them together a
    correctness requirement rather than an optimisation.
    """

    def test_both_writes_land(self) -> None:
        provider = FakeProvider()
        reservation = self.submit(provider)
        batch = self.conn.execute(
            "SELECT est_cost_usd FROM llm_batch WHERE id = ?",
            (reservation.batch_id,),
        ).fetchone()
        self.assertAlmostEqual(
            float(batch["est_cost_usd"]), reservation.estimate.total_usd
        )
        self.assertAlmostEqual(
            ledger.monthly_spend_usd(self.conn), reservation.estimate.total_usd
        )

    def test_a_failure_between_the_two_writes_leaves_neither(self) -> None:
        """**The negative control for M1.72, as a test.**

        `_charge_run` is patched to raise, so the failure lands *between* the
        batch row and the ledger — the one place a test can distinguish "these
        commit together" from "these both happen to run". A test that could only
        fail before or after the pair would prove nothing about the transaction.

        Afterwards the ledger and the batch table must AGREE, and the agreement
        that matters is that both are empty: nothing was submitted, so nothing
        may be on the books.
        """
        original = reconcile_target = extract_p2._charge_run

        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("crash between §7 control 4's two writes")

        extract_p2._charge_run = explode  # type: ignore[assignment]
        self.addCleanup(setattr, extract_p2, "_charge_run", original)
        try:
            with self.assertRaises(RuntimeError):
                self.submit(FakeProvider())
        finally:
            extract_p2._charge_run = reconcile_target  # type: ignore[assignment]

        batches = self.conn.execute("SELECT COUNT(*) FROM llm_batch").fetchone()[0]
        requests = self.conn.execute(
            "SELECT COUNT(*) FROM llm_batch_request"
        ).fetchone()[0]
        self.assertEqual(batches, 0)
        self.assertEqual(requests, 0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 0.0)

    def test_the_ledger_and_the_batch_table_agree_after_the_rollback(
        self,
    ) -> None:
        """The same control with money already on the books, so "both empty" is
        not the only way to pass. A prior batch's reservation must survive the
        failed one untouched — a rollback that took the whole ledger with it
        would fail in the *other* direction and be just as wrong."""
        first = self.submit(FakeProvider())
        before = ledger.monthly_spend_usd(self.conn)
        self.assertAlmostEqual(before, first.estimate.total_usd)

        original = extract_p2._charge_run
        extract_p2._charge_run = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("crash")
        )
        self.addCleanup(setattr, extract_p2, "_charge_run", original)
        with self.assertRaises(RuntimeError):
            self.submit(FakeProvider(batch_id="msgbatch_second"), purpose="homepage")
        extract_p2._charge_run = original

        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), before)
        rows = list(self.conn.execute("SELECT id, est_cost_usd FROM llm_batch"))
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(
            sum(float(r["est_cost_usd"]) for r in rows),
            ledger.monthly_spend_usd(self.conn),
        )

    def test_a_reservation_is_made_before_the_provider_is_called(self) -> None:
        """§7 control 4 says *"before the submit call returns"* and means it: a
        submitted batch is committed spend whether or not the process survives
        to read the result. So the ledger already knows when `create` is called
        — which is the whole reason migration 014 made `provider_batch_id`
        nullable."""
        seen: list[float] = []

        class Watching(FakeProvider):
            def submit_batch(inner, requests, *, clearance):
                seen.append(ledger.monthly_spend_usd(self.conn))
                return super().submit_batch(requests, clearance=clearance)

        reservation = self.submit(Watching())
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0], reservation.estimate.total_usd)

    def test_a_lost_provider_id_leaves_the_money_counted(self) -> None:
        """Migration 014's `reserved`. A crash before `create` and a crash after
        it are indistinguishable from here, so the row is read as *the money is
        gone* — over-counting, which is control 3's own stated preference.
        **Nothing releases it automatically**, and `reconcile` reports it."""

        class Failing(FakeProvider):
            def submit_batch(self, requests, *, clearance):
                raise RuntimeError("the process died inside messages.batches.create")

        with self.assertRaises(RuntimeError):
            self.submit(Failing())

        row = self.conn.execute("SELECT * FROM llm_batch").fetchone()
        self.assertEqual(row["status"], "reserved")
        self.assertIsNone(row["provider_batch_id"])
        self.assertIsNone(row["submitted_at"])
        self.assertIsNotNone(row["reserved_at"])
        self.assertGreater(ledger.monthly_spend_usd(self.conn), 0)

        result = reconcile.run(self.conn, FakeProvider(), self.artifacts)
        self.assertEqual(result.batches, [])  # not polled: there is nothing to poll
        self.assertEqual([b.id for b in result.reserved_unknown], [row["id"]])
        # And it is still counted afterwards.
        self.assertGreater(ledger.monthly_spend_usd(self.conn), 0)

    def test_the_paid_surface_still_refuses_without_a_clearance(self) -> None:
        """M1.71/M1.83, on the new caller. `reserve_and_submit` is registered in
        `PAID_SURFACES` and decorated, so it cannot run without a
        `LedgerClearance` — which only `ledger.check_ceiling` constructs."""
        prepared, _ = extract_p2.prepare(self.conn, self.artifacts)
        with self.assertRaises(ledger.LedgerBypass):
            extract_p2.reserve_and_submit(
                self.conn,
                FakeProvider(),
                prepared,
                run_id=self._run(),
                purpose="impressum",
            )
        with self.assertRaises(ledger.LedgerBypass):
            extract_p2.reserve_and_submit(
                self.conn,
                FakeProvider(),
                prepared,
                run_id=self._run(),
                purpose="impressum",
                clearance=object(),  # type: ignore[arg-type]
            )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM llm_batch").fetchone()[0], 0
        )

    def test_an_empty_batch_is_refused(self) -> None:
        """A ledger entry for work that was never requested."""
        with self.assertRaises(ValueError):
            extract_p2.reserve_and_submit(
                self.conn,
                FakeProvider(),
                [],
                run_id=self._run(),
                purpose="impressum",
                clearance=ledger.check_ceiling(self.conn),
            )


# ══════════════════════════════════════════════════════════════════════
# B3.2 / B3.3 — the estimate-to-actual correction (§7 control 12)
# ══════════════════════════════════════════════════════════════════════


class TheEstimateToActualCorrection(ReconcileTestCase):
    """B3.2: *"the ceiling sums estimates and never actuals"*. Re-derived before
    it was built — `grep -rn actual_cost_usd portal/` returned `ledger.py`'s
    docstring and nothing else, so no code had ever read one.

    B3.1 is already ratified: the correction applies to the **submitting** run,
    where the reservation was made, and the reconciling run does not absorb it.
    """

    def _reconcile(self, run_id: int | None = None) -> reconcile.BatchReport:
        provider = FakeProvider()
        self.submit(provider, run_id=run_id)
        provider.will_return(succeeded(self.custom_id(), impressum_payload()))
        return reconcile.run(self.conn, provider, self.artifacts).batches[0]

    def test_the_measured_actual_replaces_the_estimate_on_the_ledger(
        self,
    ) -> None:
        report = self._reconcile()
        self.assertIsNotNone(report.actual_cost_usd)
        self.assertAlmostEqual(
            report.ledger_delta_usd,
            (report.actual_cost_usd or 0) - report.est_cost_usd,
        )
        self.assertAlmostEqual(
            ledger.monthly_spend_usd(self.conn), report.actual_cost_usd or 0.0
        )
        stored = self.conn.execute(
            "SELECT actual_cost_usd, reconciled_at FROM llm_batch WHERE id = ?",
            (report.batch_id,),
        ).fetchone()
        self.assertAlmostEqual(
            float(stored["actual_cost_usd"]), report.actual_cost_usd or 0.0
        )
        self.assertIsNotNone(stored["reconciled_at"])

    def test_the_correction_lands_on_the_submitting_run_not_the_reconciling_one(
        self,
    ) -> None:
        """B3.1, as an assertion about which row moved."""
        submitting = self._run()
        report = self._reconcile(run_id=submitting)
        reconciling = self.conn.execute(
            "SELECT id FROM run WHERE stage = 'reconcile' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        self.assertAlmostEqual(
            float(
                self.conn.execute(
                    "SELECT est_cost_usd FROM run WHERE id = ?", (submitting,)
                ).fetchone()[0]
            ),
            report.actual_cost_usd or 0.0,
        )
        self.assertAlmostEqual(
            float(
                self.conn.execute(
                    "SELECT est_cost_usd FROM run WHERE id = ?", (reconciling,)
                ).fetchone()[0]
            ),
            0.0,
        )

    def test_a_batch_that_reconciles_in_a_later_window_moves_this_one_by_zero(
        self,
    ) -> None:
        """**The cross-window case, stated rather than left to be discovered.**

        The window is keyed on `run.started_at` and nothing else (M1.70). A run
        that started 40 days ago is already outside it, so a correction written
        onto that run today changes the current window's total by **zero** —
        headroom over-reserved in a past window is never returned to this one,
        and spend under-reserved in a past window is never charged to it. That
        is what makes the window rolling rather than cumulative; a runaway guard
        and an accounting record want different behaviour at the boundary, and
        §7 control 2 says which one this is.
        """
        old_run = self._run(days_ago=40)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 0.0)
        report = self._reconcile(run_id=old_run)

        # The correction really happened, on the row that made the reservation…
        self.assertNotEqual(report.ledger_delta_usd, 0.0)
        self.assertAlmostEqual(
            float(
                self.conn.execute(
                    "SELECT est_cost_usd FROM run WHERE id = ?", (old_run,)
                ).fetchone()[0]
            ),
            report.actual_cost_usd or 0.0,
        )
        # …and the rolling window never saw either the reservation or the
        # correction, because the run aged out on `started_at`.
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 0.0)

    def test_the_actual_is_priced_off_the_declared_model_not_the_returned_one(
        self,
    ) -> None:
        """A response says `claude-haiku-4-5-20251001`; §7 control 10's table
        says `claude-haiku-4-5`. `price_for` refuses an undeclared model on
        purpose, so the price comes from what was CALLED. The response's exact
        model id is not discarded — §5.5b's stage fact carries it."""
        with self.assertRaises(llm.LLMConfigError):
            llm.price_for("anthropic", "claude-haiku-4-5-20251001", batch=True)
        report = self._reconcile()
        self.assertIsNotNone(report.actual_cost_usd)
        self.assertEqual(
            self.signals()["llm.impressum_extracted"]["value_text"],
            "claude-haiku-4-5-20251001",
        )

    def test_no_actual_is_ever_added_to_the_ceiling_query(self) -> None:
        """M1.69's double-count, restated now that actuals exist. §7 control 2
        sums `run` **alone**; `llm_batch` is the per-batch record of a line
        already in it. Summing both would halve the effective ceiling."""
        report = self._reconcile()
        both = self.conn.execute(
            "SELECT (SELECT COALESCE(SUM(est_cost_usd),0) FROM run"
            "        WHERE started_at > datetime('now','-30 days'))"
            "     + (SELECT COALESCE(SUM(actual_cost_usd),0) FROM llm_batch)"
        ).fetchone()[0]
        self.assertAlmostEqual(both, 2 * (report.actual_cost_usd or 0.0))
        self.assertAlmostEqual(
            ledger.monthly_spend_usd(self.conn), report.actual_cost_usd or 0.0
        )

    def test_a_correction_can_never_take_the_ledger_negative(self) -> None:
        """Only reachable by correcting a reservation twice, and a ledger that
        can go negative is one that can be talked below a real number. §7 fails
        closed."""
        provider = FakeProvider()
        reservation = self.submit(provider)
        batch = reconcile.open_batches(self.conn)[0]
        self.conn.execute("UPDATE run SET est_cost_usd = 0")
        with self.assertRaises(reconcile.ReconcileError) as caught:
            reconcile._correct_the_reservation(self.conn, batch, actual=0.0)
        self.assertIn("below zero", str(caught.exception))
        self.assertGreater(reservation.estimate.total_usd, 0)


# ══════════════════════════════════════════════════════════════════════
# (d) M1.85 — confidence = 0 IN §9, settled
# ══════════════════════════════════════════════════════════════════════


class ARejectedValueIsVisible(ReconcileTestCase):
    """**M1.85, and 9b's settlement of it.**

    9a found that §9 renders a rejected value's red evidence *beneath its
    `score_component`*, and that `evaluate` writes no component for a rule that
    declines (A7) — so A4's *"the loss is visible"* argument holds **through**
    M1.81's abstention rather than independently of it. What 9a did not settle
    is the other half: for a key **no rule reads**, no component is produced on
    any path, so the row stayed queryable in `signal` and invisible on the page.

    Nine §8/§9-only Impressum fields are in that class and `impressum.legal_name`
    is one of them — the value that goes in the letter, and the single worst
    thing to be wrong about (§5.5b). So it is closed with a second render path
    rather than recorded as a gap.
    """

    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        from portal import serve as serve_mod

        self.serve_mod = serve_mod
        self.client = TestClient(serve_mod.create_app(self.db_path, self.artifacts))

    def _detail(self) -> str:
        return self.client.get(f"/company/{self.company_id}/detail").text

    def _reconcile(self, payload: dict[str, object], purpose="impressum") -> None:
        provider = FakeProvider()
        self.submit(provider, purpose=purpose)
        provider.will_return(succeeded(self.custom_id(purpose), payload))
        reconcile.run(self.conn, provider, self.artifacts)
        self.conn.commit()

    def _score(self) -> None:
        score.run(self.conn, phase=2, today=dt.date(2026, 8, 20))
        self.conn.commit()

    def test_a_rejected_value_on_a_key_no_rule_reads_is_rendered(self) -> None:
        """The gap 9a left, closed. `impressum.legal_name` is read by **no §6
        rule** — §5.5b's mapping says so, in the *"the rule that reads it"*
        column — so before this it could not appear on the page at all."""
        self._reconcile(impressum_payload(legal_name="Erfundene Handels AG"))
        self._score()

        html = self._detail()
        self.assertIn("Verworfene LLM-Werte", html)
        self.assertIn("Erfundene Handels AG", html)
        self.assertIn("impressum.legal_name", html)
        self.assertIn("nicht verifiziert", html)

    def test_it_renders_even_where_no_score_exists_at_all(self) -> None:
        """The render path does not hang off `score_component`, and this is the
        assertion that says so: with no scoring run at all there are no
        components, and the rejected value is still on the page."""
        self._reconcile(impressum_payload(legal_name="Erfundene Handels AG"))
        html = self._detail()
        self.assertNotIn("<h3>Bewertung</h3>", html.replace("\n", ""))
        self.assertIn("Erfundene Handels AG", html)

    def test_a_value_a_rule_reads_is_rendered_once_under_its_component(
        self,
    ) -> None:
        """The two paths must not both fire. A scored key renders beneath its
        rule's abstention — which is M1.81's component — and is excluded from
        the second panel, so the panel above stays the place a scored value is
        explained."""
        self._reconcile(
            homepage_payload(own_brand_evidence="frei erfunden"), purpose="homepage"
        )
        self._score()
        html = self._detail()

        self.assertIn("qual.own_brand", html)
        self.assertIn("Enthaltung", html)
        # `brand.own_brand` renders under the abstention, not in the second
        # panel — the reason M1.85's guarantee holds THROUGH the abstention.
        self.assertNotIn("Verworfene LLM-Werte", html)

    def test_what_a_reviewer_still_cannot_see_is_written_down(self) -> None:
        """The residual, named rather than left implicit: a rejected value is
        visible, and **which of the two routes it arrived by is not**. M1.81
        already rules that the model returning `null` and its span failing
        verification take the same review reason deliberately, because both send
        a person to the same page; the difference lives in `signal` and in
        `raised_note`, not on the row."""
        self._reconcile(
            homepage_payload(own_brand_evidence="frei erfunden"), purpose="homepage"
        )
        self._score()
        flags = {
            str(r["reason"])
            for r in self.conn.execute(
                "SELECT reason FROM review_flag WHERE company_id = ?",
                (self.company_id,),
            )
        }
        self.assertIn("own_brand_undetermined", flags)
        # The rejected row is what distinguishes the two, and it is queryable.
        rejected = self.conn.execute(
            "SELECT value_text FROM signal WHERE key = 'brand.own_brand' "
            "AND confidence = 0"
        ).fetchone()
        self.assertEqual(rejected["value_text"], "frei erfunden")
