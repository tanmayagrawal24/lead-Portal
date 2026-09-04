"""Regressions for the 2026-09-02 external audit, one class per finding.

Each test reproduces the defect the audit measured — the reproduction is the
test, so a reader can see what went wrong rather than only that it no longer
does.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from portal import cli, db, extract, fetch, llm, llm_anthropic, migrate, reconcile
from portal.addresses import AddressPolicy
from portal.artifacts import utc_now
from portal.net import Fetcher, HostRateLimiter, RobotsExempt
from portal.urls import canonical_host, normalise_domain, same_site


class Finding2_ExtractReadsTheNewestArtifact(unittest.TestCase):
    """`extract-p1` chose the *oldest* 200 artifact per kind, so after the
    second crawl every Phase-1 signal came off the first crawl's bytes, and
    all sitemap shards ever stored were merged into one catalogue count."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "p.db")
        migrate.apply_pending(self.conn)
        self.root = self.tmp / "artifacts"
        (self.root / "shop.de").mkdir(parents=True)
        self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES ('shop.de','seed_csv','2026-01-01')"
        )

    def _store(self, kind: str, name: str, body: str, checked: str, url: str) -> int:
        path = self.root / "shop.de" / name
        path.write_text(body, encoding="utf-8")
        cur = self.conn.execute(
            "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
            "body_path, fetched_at, last_checked_at) VALUES (1,?,?,200,?,?,?,?)",
            (kind, url, name, f"shop.de/{name}", checked, checked),
        )
        return int(cur.lastrowid or 0)

    def test_the_newest_homepage_decides_the_platform(self) -> None:
        self._store(
            "homepage",
            "h-old.html",
            '<script src="https://cdn.shopify.com/x.js"></script>',
            "2026-01-01T00:00:00Z",
            "https://shop.de/",
        )
        self._store(
            "homepage",
            "h-new.html",
            '<script src="/bundles/storefront/x.js"></script>',
            "2026-02-01T00:00:00Z",
            "https://shop.de/",
        )
        _, results = extract.run(self.conn, [(1, "shop.de")], self.root)
        self.assertEqual(results[0].signals["platform.detected"], "Shopware")

    def test_shards_from_an_older_crawl_do_not_inflate_the_count(self) -> None:
        self._store(
            "homepage",
            "h.html",
            "<html></html>",
            "2026-02-01T00:00:00Z",
            "https://shop.de/",
        )
        urlset = "".join(
            f"<url><loc>https://shop.de/products/p{i}</loc></url>" for i in range(30)
        )
        # An old shard listing 30 products the shop no longer serves…
        self._store(
            "sitemap",
            "s-old.xml",
            f"<urlset>{urlset}</urlset>",
            "2026-01-01T00:00:00Z",
            "https://shop.de/sitemap_products_1.xml",
        )
        # …and the current one listing 3.
        current = "".join(
            f"<url><loc>https://shop.de/products/q{i}</loc></url>" for i in range(3)
        )
        self._store(
            "sitemap",
            "s-new.xml",
            f"<urlset>{current}</urlset>",
            "2026-02-01T00:00:01Z",
            "https://shop.de/sitemap_products_1.xml",
        )
        _, results = extract.run(self.conn, [(1, "shop.de")], self.root)
        self.assertEqual(results[0].signals["catalog.product_url_count"], 3)

    def test_an_impressum_that_is_the_homepage_is_not_read_as_one(self) -> None:
        """M1.43, now applied in Phase 1 too."""
        body = "<html>Impressum Muster GmbH 10115 Berlin</html>"
        self._store(
            "homepage", "h.html", body, "2026-02-01T00:00:00Z", "https://shop.de/"
        )
        path = self.root / "shop.de" / "i.html"
        path.write_text(body)
        self.conn.execute(
            "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
            "body_path, fetched_at, last_checked_at) VALUES (1,'impressum',"
            "'https://shop.de/#x',200,'h.html','shop.de/i.html',?,?)",
            ("2026-02-01T00:00:01Z", "2026-02-01T00:00:01Z"),
        )
        _, results = extract.run(self.conn, [(1, "shop.de")], self.root)
        self.assertNotIn("company.legal_form", results[0].signals)
        self.assertTrue(any("no impressum" in n for n in results[0].notes))


class Finding3_OneBadDomainDoesNotAbortTheRun(unittest.TestCase):
    def test_a_label_idna_refuses_is_a_verdict_not_an_exception(self) -> None:
        verdict = AddressPolicy().verdict_for("http://" + "a" * 70 + ".example.com/")
        self.assertFalse(verdict.permitted)
        self.assertFalse(verdict.verifiable)

    def test_the_transport_never_raises_on_it(self) -> None:
        with Fetcher(limiter=HostRateLimiter.unthrottled()) as fetcher:
            response = fetcher.get(
                "http://" + "a" * 70 + ".example.com/", hop_allowed=lambda a, b: True
            )
        self.assertIsNotNone(response.error)

    def test_a_crash_in_one_company_is_recorded_and_the_run_finishes(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        conn = db.connect(tmp / "p.db")
        migrate.apply_pending(conn)
        for domain in ("a.invalid", "b.invalid"):
            conn.execute(
                "INSERT INTO company (domain, discovery_source, discovered_at) "
                "VALUES (?,'seed_csv',?)",
                (domain, utc_now()),
            )
        original = fetch.FetchStage.run_company

        def crashy(self, company_id, domain):
            if domain == "a.invalid":
                raise RuntimeError("boom")
            return fetch.CompanyResult(domain=domain, company_id=company_id)

        fetch.FetchStage.run_company = crashy
        try:
            run_id, results = fetch.run(
                conn, [(1, "a.invalid"), (2, "b.invalid")], tmp / "artifacts"
            )
        finally:
            fetch.FetchStage.run_company = original
        self.assertEqual([r.failed is not None for r in results], [True, False])
        row = conn.execute(
            "SELECT finished_at, aborted_reason FROM run WHERE id = ?", (run_id,)
        ).fetchone()
        self.assertIsNotNone(row["finished_at"])
        self.assertIsNone(row["aborted_reason"])
        flag = conn.execute(
            "SELECT reason FROM review_flag WHERE company_id = 1"
        ).fetchone()
        self.assertEqual(flag["reason"], "fetch_persistently_failing")


class Finding4_UmlautDomainsAreOneHost(unittest.TestCase):
    def test_seed_and_wire_spelling_agree(self) -> None:
        self.assertEqual(normalise_domain("Müller.de"), "xn--mller-kva.de")
        self.assertEqual(normalise_domain("www.XN--MLLER-KVA.de"), "xn--mller-kva.de")
        self.assertEqual(canonical_host("straße.de"), canonical_host("STRASSE.de"))

    def test_same_site_survives_httpx_normalisation(self) -> None:
        for seeded in ("müller.de", "xn--mller-kva.de"):
            self.assertTrue(same_site("https://xn--mller-kva.de/impressum", seeded))
            self.assertTrue(same_site("https://www.müller.de/blog/", seeded))
            self.assertFalse(same_site("https://mueller.de/", seeded))


if __name__ == "__main__":
    unittest.main()


# ── findings 8–10 ────────────────────────────────────────────────────────


def _message(text: str | None, *, stop_reason: str = "end_turn") -> SimpleNamespace:
    """A batch result's `message`, in the shape `_result_item` reads."""
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        model="claude-haiku-4-5",
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
    )


def _succeeded(custom_id: str, message: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message)
    )


class _FakeBatches:
    def __init__(self, results: list[SimpleNamespace]) -> None:
        self._results = results

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id: str):
        return iter(self._results)


class _FakeClient:
    def __init__(self, results: list[SimpleNamespace]) -> None:
        self.messages = SimpleNamespace(batches=_FakeBatches(results))


class Finding8_ATruncatedOrRefusedResultIsADispositionNotACrash(unittest.TestCase):
    """`_json_payload` raised on a `succeeded` result whose message was cut off
    at `max_tokens`, refused, or otherwise not JSON — and one such company took
    the whole poll down with it, so nine good extractions were never written.
    It is now a terminal disposition on that one request, with its reason on
    the row, and — because the response was paid for — with its usage."""

    def _poll(self, *results: SimpleNamespace) -> dict[str, llm.BatchResultItem]:
        provider = llm_anthropic.AnthropicProvider(client=_FakeClient(list(results)))
        return llm.index_by_custom_id(provider.poll_batch("msgbatch_fake").items)

    def test_a_truncated_response_settles_the_request_and_spares_the_batch(self):
        by_id = self._poll(
            _succeeded("good", _message('{"city": "Bonn"}')),
            _succeeded("cut", _message('{"city": "Ber', stop_reason="max_tokens")),
        )
        self.assertIs(by_id["good"].outcome, llm.RequestOutcome.SUCCEEDED)
        self.assertIs(by_id["cut"].outcome, llm.RequestOutcome.INVALID_REQUEST)
        self.assertIsNone(by_id["cut"].extraction)
        self.assertIn("truncated", by_id["cut"].error_message)
        # Not retryable: the same request at the same bound truncates again.
        self.assertEqual(llm.resubmittable(list(by_id.values())), ())

    def test_a_truncated_prefix_that_happens_to_parse_is_still_not_a_value(self):
        """`{"city": "Bonn"}` cut at max_tokens may still be a JSON object —
        the closing brace can land inside the bound. It is not read."""
        by_id = self._poll(
            _succeeded("cut", _message('{"city": "Bonn"}', stop_reason="max_tokens"))
        )
        self.assertIs(by_id["cut"].outcome, llm.RequestOutcome.INVALID_REQUEST)

    def test_a_refusal_and_a_non_json_body_are_dispositions_too(self):
        by_id = self._poll(
            _succeeded("no", _message("I cannot help.", stop_reason="refusal")),
            _succeeded("prose", _message("Here is the Impressum: ...")),
            _succeeded("empty", _message(None)),
        )
        for custom_id in ("no", "prose", "empty"):
            self.assertIs(by_id[custom_id].outcome, llm.RequestOutcome.INVALID_REQUEST)
        self.assertIn("refused", by_id["no"].error_message)
        self.assertIn("not a JSON object", by_id["prose"].error_message)
        self.assertIn("no text block", by_id["empty"].error_message)

    def test_the_paid_tokens_still_reach_the_ledger(self):
        """§7's direction. The truncated response consumed 1,000 input and 50
        output tokens; counting only `extraction.usage` would hand that share
        of the reservation back as though nothing had been spent."""
        by_id = self._poll(
            _succeeded("cut", _message('{"city": "Ber', stop_reason="max_tokens"))
        )
        item = by_id["cut"]
        self.assertEqual(item.usage, llm.Usage(1000, 50))
        cost, usage = reconcile.actual_cost_usd(
            [item], provider="anthropic", model="claude-haiku-4-5"
        )
        self.assertGreater(cost, 0.0)
        self.assertEqual((usage.input_tokens, usage.output_tokens), (1000, 50))

    def test_an_expired_request_still_costs_nothing(self):
        """The other side of the same rule: `usage=None` means consumed
        nothing, and that path is unchanged."""
        cost, _ = reconcile.actual_cost_usd(
            [llm.BatchResultItem("x", llm.RequestOutcome.EXPIRED)],
            provider="anthropic",
            model="claude-haiku-4-5",
        )
        self.assertEqual(cost, 0.0)


class _CountingGzipStream(httpx.SyncByteStream):
    """A gzip body served in raw chunks, counting how many the client took."""

    def __init__(self, decoded_size: int, chunk_size: int) -> None:
        raw = gzip.compress(b"\0" * decoded_size, compresslevel=9)
        self.chunks = [raw[i : i + chunk_size] for i in range(0, len(raw), chunk_size)]
        self.yielded = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


class Finding9_TheBodyCapIsAppliedDuringDecompression(unittest.TestCase):
    """`response.content[:max_bytes]` sliced a body httpx had already inflated
    in full: a gzip bomb was in memory before the cap ran. The cap now applies
    to decoded output as it streams, and the transport is not drained past it."""

    #: 64 MiB of zeros gzips to ~64 KiB; served in 4 KiB raw chunks, each of
    #: which decodes to ~4 MiB. A 1 MiB cap must stop inside the first chunk.
    DECODED = 64 * 1024 * 1024
    CHUNK = 4 * 1024
    CAP = 1024 * 1024

    def _fetch(self, stream: _CountingGzipStream, *, max_bytes: int):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-encoding": "gzip", "content-type": "text/html"},
                stream=stream,
            )

        fetcher = Fetcher(
            limiter=HostRateLimiter.unthrottled(),
            addresses=AddressPolicy.loopback_permitted(),
            transport=httpx.MockTransport(handler),
            max_bytes=max_bytes,
        )
        self.addCleanup(fetcher.close)
        return fetcher.get("http://127.0.0.1:1/", hop_allowed=RobotsExempt)

    def test_the_transport_is_not_drained_past_the_cap(self):
        stream = _CountingGzipStream(self.DECODED, self.CHUNK)
        response = self._fetch(stream, max_bytes=self.CAP)
        self.assertEqual(response.status, 200)
        self.assertIsNotNone(response.body)
        self.assertEqual(len(response.body), self.CAP)
        # The measurement: of the raw chunks the server had, how many did the
        # client take? Before the fix, all of them; now, the first.
        self.assertGreater(len(stream.chunks), 8)
        self.assertEqual(stream.yielded, 1)

    def test_a_body_under_the_cap_is_read_whole_and_decoded(self):
        stream = _CountingGzipStream(10_000, self.CHUNK)
        response = self._fetch(stream, max_bytes=self.CAP)
        self.assertEqual(response.body, b"\0" * 10_000)
        self.assertEqual(stream.yielded, len(stream.chunks))

    def test_a_corrupt_stream_is_a_failed_fetch_not_an_exception(self):
        stream = _CountingGzipStream(10_000, self.CHUNK)
        stream.chunks = [b"\x1f\x8b" + b"garbage" * 40]
        response = self._fetch(stream, max_bytes=self.CAP)
        self.assertIsNone(response.body)
        self.assertIn("DecodingError", response.error or "")


class Finding10_FetchSkipsExcludedCompanies(unittest.TestCase):
    """`cmd_fetch` built its targets from the seed rows and never asked
    `company.excluded`, so a `duplicate_site` row — the same lead as another
    row, per §6.4 — was re-crawled on every run. `extract-p1` reads
    `WHERE excluded = 0`; `fetch` now applies the same verdict."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "p.db"
        conn = db.connect(self.db_path)
        migrate.apply_pending(conn)
        conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at, excluded, "
            "excluded_reason) VALUES ('a.de','seed_csv','2026-01-01',1,"
            "'duplicate_site: b.de is company #2')"
        )
        conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at, excluded, "
            "excluded_reason) VALUES ('b.de','seed_csv','2026-01-01',1,"
            "'robots_disallowed: /impressum')"
        )
        conn.close()
        self.seed = self.tmp / "seed.csv"
        self.seed.write_text("domain\na.de\nb.de\n", encoding="utf-8")

    def test_excluded_seed_rows_are_reported_and_not_fetched(self) -> None:
        """Both seeded companies are excluded, so a correct `fetch` issues no
        request at all — which is also what makes this test safe to run
        without a fixture server: a regression here would reach the network."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(
                ["--db", str(self.db_path), "fetch", "--seed", str(self.seed)]
            )
        text = out.getvalue() + err.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("Fetching 0 domain(s)", text)
        self.assertIn("a.de: SKIPPED — excluded (§6.4): duplicate_site", text)
        self.assertIn("b.de: SKIPPED — excluded (§6.4): robots_disallowed", text)
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0], 0)
        run = conn.execute("SELECT stage, companies_seen FROM run").fetchone()
        self.assertEqual((run["stage"], run["companies_seen"]), ("fetch", 0))
        # The verdicts themselves are untouched: the seed upsert leaves an
        # existing row as-is, and nothing here lifts an exclusion.
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM company WHERE excluded = 1").fetchone()[
                0
            ],
            2,
        )
