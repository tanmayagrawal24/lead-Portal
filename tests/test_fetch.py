"""End-to-end M1 tests against the local fixture server.

No third-party domain is contacted by anything in this file. The one live
request the M1 work makes is in `test_live_smoke.py`, is opt-in, and targets
creative-potato.global only.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portal import db, fetch, migrate
from portal.artifacts import ArtifactStore
from portal.net import Fetcher, HostRateLimiter
from tests import shopfixtures
from tests.fixture_server import FixtureServer, Site


class FetchTestCase(unittest.TestCase):
    """Each test gets a database, an artifacts root, and an unthrottled fetcher.

    The politeness interval is 0 here so the suite stays fast; the 1 req/s floor
    is asserted for real in `test_politeness.py`.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conn = db.connect(self.root / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.artifacts = self.root / "artifacts"
        self.fetcher = Fetcher(limiter=HostRateLimiter(0.0))
        self.addCleanup(self.fetcher.close)

    def serve(self, build) -> FixtureServer:
        """Start a fixture server whose site is built from its own base URL.

        Sitemaps contain absolute URLs, so the builder needs the port — which
        only exists once the socket is bound. `FixtureServer` binds in its
        constructor, so the site is populated after construction and before
        serving starts.
        """
        server = FixtureServer(Site())
        server.site.routes.update(build(server.base).routes)
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        return server

    def add_company(self, domain: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES (?, 'seed_csv', '2026-08-15T00:00:00Z')",
            (domain,),
        )
        return int(cursor.lastrowid)

    def run_fetch(
        self, server: FixtureServer, domain: str | None = None
    ) -> fetch.CompanyResult:
        domain = domain or server.address
        company_id = self.add_company(domain)
        _run_id, results = fetch.run(
            self.conn,
            [(company_id, domain)],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )
        return results[0]

    def artifact_rows(self, company_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM artifact WHERE company_id = ? ORDER BY kind, id",
            (company_id,),
        ).fetchall()

    def review_flags(self, company_id: int) -> list[str]:
        return [
            row["reason"]
            for row in self.conn.execute(
                "SELECT reason FROM review_flag WHERE company_id = ? AND resolved_at IS NULL",
                (company_id,),
            )
        ]


class TestFullPipeline(FetchTestCase):
    def test_fetches_every_kind_and_stores_bodies_on_disk(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        result = self.run_fetch(server)

        self.assertIsNone(result.excluded_reason)
        self.assertEqual(
            result.kinds,
            {
                "robots",
                "homepage",
                "sitemap",
                "impressum",
                "blog_index",
                "product_page",
            },
        )

        store = ArtifactStore(self.artifacts)
        for artifact in result.artifacts:
            if not artifact.ok:
                continue
            self.assertTrue(artifact.body_path, artifact.kind)
            self.assertTrue(
                (self.artifacts / artifact.body_path).is_file(), artifact.kind
            )
            self.assertGreater(len(store.body_of(artifact)), 0, artifact.kind)

    def test_every_stored_row_has_a_hash_and_a_real_url(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        result = self.run_fetch(server)
        for row in self.artifact_rows(result.company_id):
            self.assertTrue(row["url"].startswith("http"), dict(row))
            self.assertTrue(row["fetched_at"])
            self.assertTrue(row["last_checked_at"])
            if row["http_status"] == 200:
                self.assertTrue(row["content_hash"])
                self.assertGreater(row["bytes"], 0)

    def test_identical_content_does_not_create_a_second_row(self) -> None:
        """§4's uq_artifact_identity plus the D5(b) upsert."""
        server = self.serve(shopfixtures.shopware_shop)
        result = self.run_fetch(server)
        before = self.artifact_rows(result.company_id)
        first_checked = {row["id"]: row["last_checked_at"] for row in before}

        _run_id, _results = fetch.run(
            self.conn,
            [(result.company_id, result.domain)],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )
        after = self.artifact_rows(result.company_id)
        self.assertEqual(len(after), len(before))
        self.assertEqual({r["id"] for r in after}, set(first_checked))

    def test_user_agent_is_identifiable_on_every_request(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        self.run_fetch(server)
        agents = {r.user_agent for r in server.site.requests}
        self.assertEqual(len(agents), 1)
        agent = agents.pop()
        self.assertIn("CreativePotatoesBot/1.0", agent)
        self.assertIn("+https://creative-potato.global", agent)

    def test_robots_is_fetched_before_anything_else(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        self.run_fetch(server)
        self.assertEqual(server.site.paths()[0], "/robots.txt")

    def test_a_run_row_is_recorded_and_closed(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        self.run_fetch(server)
        row = self.conn.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["stage"], "fetch")
        self.assertEqual(row["companies_seen"], 1)
        self.assertIsNotNone(row["finished_at"])


class TestRobotsHandling(FetchTestCase):
    def test_allow_all_fetches_normally(self) -> None:
        server = self.serve(lambda base: shopfixtures.shopware_shop(base))
        result = self.run_fetch(server)
        self.assertIsNone(result.excluded_reason)
        self.assertIn("homepage", result.kinds)

    def test_disallowing_required_paths_excludes_and_stops(self) -> None:
        server = self.serve(
            lambda base: shopfixtures.shopware_shop(
                base, robots_txt="User-agent: *\nDisallow: /\n"
            )
        )
        result = self.run_fetch(server)

        self.assertIsNotNone(result.excluded_reason)
        row = self.conn.execute(
            "SELECT excluded, excluded_reason FROM company WHERE id = ?",
            (result.company_id,),
        ).fetchone()
        self.assertEqual(row["excluded"], 1)
        self.assertIn("robots_disallowed", row["excluded_reason"])
        # Nothing beyond robots.txt may have been requested.
        self.assertEqual(server.site.paths(), ["/robots.txt"])

    def test_disallowing_irrelevant_paths_only_is_not_a_refusal(self) -> None:
        """§5.2: a robots.txt blocking /checkout/ is normal, not a refusal."""
        robots_txt = "User-agent: *\nDisallow: /checkout/\nDisallow: /account/\n"
        server = self.serve(
            lambda base: shopfixtures.shopware_shop(base, robots_txt=robots_txt)
        )
        result = self.run_fetch(server)

        self.assertIsNone(result.excluded_reason)
        self.assertIn("homepage", result.kinds)
        self.assertIn("impressum", result.kinds)

    def test_a_disallowed_single_path_is_skipped_not_fetched(self) -> None:
        robots_txt = "User-agent: *\nDisallow: /magazin\n"
        server = self.serve(
            lambda base: shopfixtures.shopware_shop(base, robots_txt=robots_txt)
        )
        result = self.run_fetch(server)

        self.assertIsNone(result.excluded_reason)
        self.assertNotIn("/magazin", server.site.paths())
        blocked = [
            row
            for row in self.artifact_rows(result.company_id)
            if row["error"] and "robots_disallowed" in row["error"]
        ]
        self.assertTrue(blocked, "the skip should be recorded, not silent")

    def test_missing_robots_is_not_a_refusal(self) -> None:
        def build(base: str) -> Site:
            site = shopfixtures.shopware_shop(base)
            del site.routes["/robots.txt"]
            return site

        result = self.run_fetch(self.serve(build))
        self.assertIsNone(result.excluded_reason)
        self.assertIn("homepage", result.kinds)


class TestImpressumTwoStep(FetchTestCase):
    def test_step_one_follows_a_footer_link(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        result = self.run_fetch(server)
        self.assertIn("impressum", result.kinds)
        self.assertIn("footer link", " ".join(result.notes))
        self.assertEqual(self.review_flags(result.company_id), [])

    def test_step_two_probes_direct_paths_when_no_footer_link(self) -> None:
        server = self.serve(
            lambda base: shopfixtures.flat_shop(base, footer_impressum=False)
        )
        result = self.run_fetch(server)
        self.assertIn("impressum", result.kinds)
        self.assertIn("probing", " ".join(result.notes))
        self.assertIn("/impressum", server.site.paths())
        self.assertEqual(self.review_flags(result.company_id), [])

    def test_neither_step_finds_one_raises_the_soft_flag(self) -> None:
        server = self.serve(
            lambda base: shopfixtures.flat_shop(
                base, footer_impressum=False, with_impressum=False
            )
        )
        result = self.run_fetch(server)

        self.assertNotIn("impressum", result.kinds)
        self.assertEqual(self.review_flags(result.company_id), ["no_impressum"])
        # Soft, never hard: §6.4.
        row = self.conn.execute(
            "SELECT excluded, needs_review FROM company WHERE id = ?",
            (result.company_id,),
        ).fetchone()
        self.assertEqual((row["excluded"], row["needs_review"]), (0, 1))

    def test_every_probe_path_is_tried_before_concluding_absence(self) -> None:
        server = self.serve(
            lambda base: shopfixtures.flat_shop(
                base, footer_impressum=False, with_impressum=False
            )
        )
        self.run_fetch(server)
        paths = server.site.paths()
        for probe in (
            "/impressum",
            "/impressum/",
            "/imprint",
            "/legal",
            "/rechtliches",
        ):
            self.assertIn(probe, paths)


class TestProductSampleSelection(FetchTestCase):
    def sample_signal(self, company_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM signal WHERE company_id = ? AND key = 'catalog.product_sample_url'",
            (company_id,),
        ).fetchone()

    def test_gzipped_multi_shard_product_sitemap_yields_the_code_point_minimum(
        self,
    ) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        result = self.run_fetch(server)

        self.assertEqual(result.product_sample_tier, "product_sitemap")
        self.assertTrue(
            result.product_sample.endswith("/detail/alpha-buerste"),
            result.product_sample,
        )
        self.assertIn("product_page", result.kinds)

    def test_mixed_sitemap_never_samples_a_content_url(self) -> None:
        server = self.serve(
            lambda base: shopfixtures.flat_shop(base, footer_impressum=True)
        )
        result = self.run_fetch(server)

        self.assertEqual(result.product_sample_tier, "sitemap_path_pattern")
        self.assertTrue(
            result.product_sample.endswith("/produkt/alpha"), result.product_sample
        )
        for fragment in ("/magazin/", "/kategorie/", "/ueber-uns"):
            self.assertNotIn(fragment, result.product_sample)

    def test_the_choice_is_recorded_as_a_signal_with_real_evidence(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        result = self.run_fetch(server)

        row = self.sample_signal(result.company_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["value_text"], result.product_sample)
        self.assertEqual(row["method"], "deterministic")
        self.assertTrue(row["evidence_url"].startswith("http"))

    def test_zero_candidates_fetches_no_product_page_and_writes_no_signal(self) -> None:
        """§5.2/A5.5: never a 0 — the absence of the signal is the point."""
        server = self.serve(shopfixtures.catalogue_free_shop)
        result = self.run_fetch(server)

        self.assertIsNone(result.product_sample)
        self.assertNotIn("product_page", result.kinds)
        self.assertIsNone(self.sample_signal(result.company_id))
        self.assertIn("must stay unwritten", " ".join(result.notes))

    def test_selection_is_identical_across_runs(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        first = self.run_fetch(server)
        second_conn_result = fetch.run(
            self.conn,
            [(first.company_id, first.domain)],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )[1][0]
        self.assertEqual(second_conn_result.product_sample, first.product_sample)


class TestTierZeroReuse(FetchTestCase):
    def test_a_second_run_reuses_the_stored_sample(self) -> None:
        server = self.serve(shopfixtures.shopware_shop)
        first = self.run_fetch(server)

        second = fetch.run(
            self.conn,
            [(first.company_id, first.domain)],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )[1][0]
        self.assertEqual(second.product_sample_tier, "reuse")
        self.assertEqual(second.product_sample, first.product_sample)

    def test_reuse_falls_through_when_the_stored_sample_stops_returning_200(
        self,
    ) -> None:
        """A5.1's fall-through: a dead sample must not be pinned forever."""
        server = self.serve(shopfixtures.shopware_shop)
        first = self.run_fetch(server)
        self.assertTrue(first.product_sample.endswith("/detail/alpha-buerste"))

        # The sampled product is discontinued.
        server.site.add("/detail/alpha-buerste", "gone", status=404)

        second = fetch.run(
            self.conn,
            [(first.company_id, first.domain)],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )[1][0]
        self.assertEqual(second.product_sample_tier, "product_sitemap")
        self.assertTrue(
            second.product_sample.endswith("/detail/beta-buerste"),
            second.product_sample,
        )
        self.assertIn("re-selecting", " ".join(second.notes))


if __name__ == "__main__":
    unittest.main()
