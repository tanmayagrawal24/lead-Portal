"""Regressions for the 2026-09-02 external audit, one class per finding.

Each test reproduces the defect the audit measured — the reproduction is the
test, so a reader can see what went wrong rather than only that it no longer
does.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portal import db, extract, fetch, migrate
from portal.addresses import AddressPolicy
from portal.artifacts import utc_now
from portal.net import Fetcher, HostRateLimiter
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
