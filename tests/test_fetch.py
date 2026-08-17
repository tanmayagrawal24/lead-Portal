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

from portal import db, fetch, migrate, net, sitemap
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
        self.fetcher = Fetcher(limiter=HostRateLimiter.unthrottled())
        self.addCleanup(self.fetcher.close)

    def serve(self, build, address: str = "127.0.0.1") -> FixtureServer:
        """Start a fixture server whose site is built from its own base URL.

        Sitemaps contain absolute URLs, so the builder needs the port — which
        only exists once the socket is bound. `FixtureServer` binds in its
        constructor, so the site is populated after construction and before
        serving starts.

        `address` exists for the cross-host redirect tests: `127.0.0.0/8` is
        all loopback, so a second server on 127.0.0.2 is a genuinely different
        host to the limiter, to `same_site`, and to robots.txt — with no
        third-party domain involved.
        """
        server = FixtureServer(Site(), address=address)
        site = build(server.base)
        server.site.routes.update(site.routes)
        server.site.redirects.update(site.redirects)
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
                "blog_article",  # A6
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


class TestRobotsUnavailable(FetchTestCase):
    """M1.59 — RFC 9309 §2.3.1's third case, which used to be the first.

    Every one of these responses used to reach `robots_mod.parse(None)` and come
    back as an unrestricted policy, so a shop whose robots.txt was 503-ing had
    its Impressum, its catalogue and its blog fetched under rules nobody read.
    """

    def _shop_with_broken_robots(self, status: int):
        def build(base: str) -> Site:
            site = shopfixtures.shopware_shop(base)
            site.add("/robots.txt", "server error", status=status)
            return site

        return build

    def _assert_nothing_but_robots_was_fetched(self, server, result) -> None:
        self.assertEqual(
            server.site.paths(),
            ["/robots.txt"],
            "no page may be requested under rules we never read",
        )
        self.assertIsNone(
            result.excluded_reason,
            "a transient is not a standing verdict about the lead",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT excluded FROM company WHERE id = ?", (result.company_id,)
            ).fetchone()["excluded"],
            0,
        )
        stored = [
            row
            for row in self.artifact_rows(result.company_id)
            if row["content_hash"] is not None
        ]
        self.assertEqual(
            [row["kind"] for row in stored], [], "no body may be stored at all"
        )

    def test_a_503_robots_stops_the_run_and_stores_no_impressum(self) -> None:
        """The instruction's minimum case: /robots.txt 503s while /impressum
        would happily return 200."""
        server = self.serve(self._shop_with_broken_robots(503))
        result = self.run_fetch(server)
        self._assert_nothing_but_robots_was_fetched(server, result)
        self.assertIn("robots_unavailable", " ".join(result.notes))
        self.assertIn("HTTP 503", " ".join(result.notes))

    def test_a_429_robots_stops_the_run(self) -> None:
        server = self.serve(self._shop_with_broken_robots(429))
        result = self.run_fetch(server)
        self._assert_nothing_but_robots_was_fetched(server, result)
        self.assertIn("HTTP 429", " ".join(result.notes))

    def test_a_transport_failure_stops_the_run(self) -> None:
        """Nothing is listening on the port, so the connection is refused before
        any status exists."""
        server = self.serve(shopfixtures.shopware_shop)
        dead = f"http://{server.address}:{_closed_port()}"
        company_id = self.add_company(server.address)
        _run_id, results = fetch.run(
            self.conn,
            [(company_id, server.address)],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: dead,
        )
        result = results[0]
        self.assertIsNone(result.excluded_reason)
        self.assertIn("robots_unavailable", " ".join(result.notes))
        self.assertEqual(
            [
                row["kind"]
                for row in self.artifact_rows(company_id)
                if row["content_hash"] is not None
            ],
            [],
        )

    def test_a_404_robots_still_allows_the_crawl(self) -> None:
        """The other side of the tri-state, kept: `parse(None)`'s reasoning is
        right for an absent file and this is the case it is about."""
        server = self.serve(self._shop_with_broken_robots(404))
        result = self.run_fetch(server)
        self.assertIsNone(result.excluded_reason)
        self.assertIn("impressum", result.kinds)
        self.assertIn("homepage", result.kinds)

    def test_the_failure_is_recorded_as_unavailable_not_as_disallowed(self) -> None:
        """A `robots_disallowed` row means the shop said so. Nothing here did."""
        server = self.serve(self._shop_with_broken_robots(503))
        result = self.run_fetch(server)
        errors = [row["error"] for row in self.artifact_rows(result.company_id)]
        self.assertNotIn(
            "robots_disallowed",
            " ".join(e for e in errors if e),
            "the shop stated no rules; it must not be recorded as if it had",
        )

    def _repeat_run(self, server, company_id: int) -> fetch.CompanyResult:
        _run_id, results = fetch.run(
            self.conn,
            [(company_id, server.address)],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )
        return results[0]

    def _backdate_last_run(self, day: str) -> None:
        self.conn.execute(
            "UPDATE run SET started_at = ? WHERE id = (SELECT max(id) FROM run)",
            (f"{day}T09:00:00Z",),
        )

    def _backdate_first_failure(self, company_id: int, day: str) -> None:
        """`_record_failure` updates in place and never rewrites `fetched_at`,
        so this is the column that carries "since when" (§5.2)."""
        self.conn.execute(
            "UPDATE artifact SET fetched_at = ? WHERE company_id = ? AND kind = 'robots'",
            (f"{day}T09:00:00Z", company_id),
        )

    def test_one_occurrence_raises_no_flag_and_three_distinct_days_do(self) -> None:
        """A7b's counting policy (M1.34), applied to a fetch-stage fact.

        The runs are backdated onto three distinct days because that is the half
        of the policy that stops a crash-restart loop inside one afternoon from
        manufacturing a flag about our own crash.
        """
        server = self.serve(self._shop_with_broken_robots(503))
        company_id = self.add_company(server.address)

        first = self._repeat_run(server, company_id)
        self.assertEqual(first.review_flags, [], "one 503 is a transient")
        self.assertEqual(self.review_flags(company_id), [])
        self._backdate_last_run("2026-08-10")
        self._backdate_first_failure(company_id, "2026-08-10")

        second = self._repeat_run(server, company_id)
        self.assertEqual(second.review_flags, [], "two days is still not three")
        self.assertEqual(self.review_flags(company_id), [])
        self._backdate_last_run("2026-08-11")
        self._backdate_first_failure(company_id, "2026-08-10")

        third = self._repeat_run(server, company_id)
        self.assertEqual(third.review_flags, ["fetch_persistently_failing"])
        row = self.conn.execute(
            "SELECT raised_note FROM review_flag WHERE company_id = ? AND reason = ?",
            (company_id, "fetch_persistently_failing"),
        ).fetchone()
        self.assertIn("robots.txt", row["raised_note"])
        self.assertIn("HTTP 503", row["raised_note"])

    def test_a_success_since_the_first_failure_breaks_the_streak(self) -> None:
        """However many runs the failure row has seen, a robots.txt that has
        worked since is not persistently unreadable."""
        server = self.serve(self._shop_with_broken_robots(503))
        company_id = self.add_company(server.address)
        self._repeat_run(server, company_id)
        self._backdate_last_run("2026-08-10")
        self._repeat_run(server, company_id)
        self._backdate_last_run("2026-08-11")
        self._backdate_first_failure(company_id, "2026-08-10")

        self.conn.execute(
            "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
            "body_path, bytes, fetched_at, last_checked_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                company_id,
                "robots",
                f"{server.base}/robots.txt",
                200,
                "deadbeef",
                "x/robots.txt",
                10,
                "2026-08-11T09:00:00Z",
                "2026-08-11T09:00:00Z",
            ),
        )

        third = self._repeat_run(server, company_id)
        self.assertEqual(third.review_flags, [])
        self.assertEqual(self.review_flags(company_id), [])


def _closed_port() -> int:
    """A port with nothing listening on it, for the transport-failure case."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class TestCrawlDelay(FetchTestCase):
    """§5.2: honour `max(floor, Crawl-delay)`, and skip above the cap."""

    def test_a_stated_delay_raises_the_interval_for_that_host(self) -> None:
        server = self.serve(
            lambda base: shopfixtures.shopware_shop(
                base, robots_txt="User-agent: *\nCrawl-delay: 3\nAllow: /\n"
            )
        )
        result = self.run_fetch(server)

        self.assertIsNone(result.excluded_reason)
        self.assertEqual(self.fetcher.limiter.interval_for(server.netloc), 3.0)

    def test_a_delay_below_the_floor_changes_nothing(self) -> None:
        """A `Crawl-delay: 1` is not permission to speed up — the floor holds."""
        server = self.serve(
            lambda base: shopfixtures.shopware_shop(
                base, robots_txt="User-agent: *\nCrawl-delay: 1\nAllow: /\n"
            )
        )
        limiter = HostRateLimiter(net.MIN_INTERVAL_SECONDS)
        self.assertEqual(limiter.interval_for(server.netloc), net.MIN_INTERVAL_SECONDS)
        limiter.set_host_interval(server.netloc, 1.0)
        self.assertEqual(limiter.interval_for(server.netloc), net.MIN_INTERVAL_SECONDS)

    def test_a_delay_above_the_cap_skips_the_domain_and_records_why(self) -> None:
        server = self.serve(
            lambda base: shopfixtures.shopware_shop(
                base, robots_txt="User-agent: *\nCrawl-delay: 60\nAllow: /\n"
            )
        )
        result = self.run_fetch(server)

        assert result.excluded_reason is not None
        self.assertIn("crawl_delay_too_high", result.excluded_reason)
        row = self.conn.execute(
            "SELECT excluded, excluded_reason FROM company WHERE id = ?",
            (result.company_id,),
        ).fetchone()
        self.assertEqual(row["excluded"], 1)
        self.assertIn("crawl_delay_too_high", row["excluded_reason"])
        self.assertEqual(
            server.site.paths(),
            ["/robots.txt"],
            "we must stop rather than stall behind the delay",
        )


class TestCrossHostRedirects(FetchTestCase):
    """A redirect hop is a request, so it is subject to the robots.txt of the
    host it lands on — which, on a host change, we have not read yet."""

    def _shop_redirecting_a_product_to(self, other_base: str):
        def build(base: str) -> Site:
            site = shopfixtures.shopware_shop(base)
            del site.routes["/detail/alpha-buerste"]
            site.add_redirect(
                "/detail/alpha-buerste", f"{other_base}/detail/alpha-buerste"
            )
            return site

        return build

    def test_the_new_hosts_robots_is_read_before_the_hop_is_followed(self) -> None:
        other = self.serve(
            lambda base: shopfixtures.shopware_shop(base), address="127.0.0.2"
        )
        server = self.serve(self._shop_redirecting_a_product_to(other.base))
        result = self.run_fetch(server)

        paths = other.site.paths()
        self.assertIn("/robots.txt", paths)
        self.assertLess(
            paths.index("/robots.txt"),
            paths.index("/detail/alpha-buerste"),
            "robots.txt must be read before anything else on a host, redirect "
            "targets included",
        )
        self.assertEqual(result.product_sample, f"{server.base}/detail/alpha-buerste")

    def test_a_hop_the_new_host_disallows_is_refused_and_recorded(self) -> None:
        other = self.serve(
            lambda base: shopfixtures.shopware_shop(
                base, robots_txt="User-agent: *\nDisallow: /detail/\n"
            ),
            address="127.0.0.2",
        )
        server = self.serve(self._shop_redirecting_a_product_to(other.base))
        result = self.run_fetch(server)

        self.assertEqual(
            other.site.paths(),
            ["/robots.txt"],
            "the disallowed page must never be requested on the new host",
        )
        refused = [
            row
            for row in self.artifact_rows(result.company_id)
            if row["error"] and "redirect_refused" in row["error"]
        ]
        self.assertTrue(refused, "a refused hop must be recorded, not silent")
        self.assertIn("refused by robots.txt", " ".join(result.notes))
        self.assertIsNone(result.product_sample)

    def test_the_new_hosts_robots_is_read_once_however_many_hops_land_on_it(
        self,
    ) -> None:
        def build(base: str) -> Site:
            site = shopfixtures.shopware_shop(base)
            for handle in ("alpha-buerste", "beta-buerste"):
                del site.routes[f"/detail/{handle}"]
                site.add_redirect(f"/detail/{handle}", f"{other.base}/detail/{handle}")
            return site

        other = self.serve(
            lambda base: shopfixtures.shopware_shop(base), address="127.0.0.2"
        )
        server = self.serve(build)
        self.run_fetch(server)

        self.assertEqual(
            other.site.paths().count("/robots.txt"),
            1,
            "the per-company robots lookup should be memoised by host",
        )


class TestBlogOnAnotherHost(FetchTestCase):
    """M1.14: anchor text takes the href wherever it points, so `fetch` can now
    make a *first* request to a host nothing has visited."""

    def _shop_linking_its_blog_at(self, other_base: str):
        def build(base: str) -> Site:
            site = shopfixtures.shopware_shop(base)
            # The path vocabulary must find nothing, or it answers first.
            del site.routes["/magazin"]
            del site.routes["/magazin/zahnpflege"]
            site.add("/", shopfixtures.homepage_without_blog_path(other_base))
            site.add_gzip(
                "/sitemap-muster-content-1.xml.gz",
                shopfixtures.urlset([f"{base}/ueber-uns", f"{base}/impressum"]),
            )
            return site

        return build

    def _blog_host(self, robots_txt: str = "User-agent: *\nAllow: /\n"):
        def build(_base: str) -> Site:
            site = Site()
            site.add("/robots.txt", robots_txt, content_type="text/plain")
            site.add(
                "/",
                '<!doctype html><html lang="de"><body><h1>Blog</h1>'
                '<a href="/erster-beitrag">Erster Beitrag</a></body></html>',
            )
            site.add("/erster-beitrag", shopfixtures.BLOG_HTML)
            return site

        return build

    def test_the_blog_host_robots_is_read_before_its_index(self) -> None:
        """Robots is keyed to the origin (§5.2). Falling back to the seeded
        policy would apply the shop's robots.txt to somebody else's host, which
        is the one thing §5.2 says twice not to do."""
        other = self.serve(self._blog_host(), address="127.0.0.2")
        server = self.serve(self._shop_linking_its_blog_at(other.base))
        self.run_fetch(server)

        paths = other.site.paths()
        self.assertIn("/robots.txt", paths)
        self.assertLess(paths.index("/robots.txt"), paths.index("/"))

    def test_the_index_is_fetched_and_an_article_sampled_under_it(self) -> None:
        """The zecplus.de shape end to end. A6 samples it like any other blog —
        the index is its host's front page, so the anchor is the whole URL."""
        other = self.serve(self._blog_host(), address="127.0.0.2")
        server = self.serve(self._shop_linking_its_blog_at(other.base))
        result = self.run_fetch(server)

        self.assertIn("blog_index", result.kinds)
        self.assertIn("blog_article", result.kinds)
        self.assertEqual(result.blog_sample, f"{other.base}/erster-beitrag")
        self.assertIn("blog located by anchor_text", " ".join(result.notes))

    def test_the_blog_hosts_own_robots_can_refuse_it(self) -> None:
        other = self.serve(
            self._blog_host(robots_txt="User-agent: *\nDisallow: /\n"),
            address="127.0.0.2",
        )
        server = self.serve(self._shop_linking_its_blog_at(other.base))
        result = self.run_fetch(server)

        self.assertEqual(other.site.paths(), ["/robots.txt"])
        self.assertNotIn("blog_index", result.kinds)


class TestSameHostRedirectsAreRobotsChecked(FetchTestCase):
    """M1.12: robots permission does not survive a redirect.

    Both shapes here are copied from `run 1` (see `docs/first-crawl-findings.md`
    §1.1), where an *allowed* Impressum probe was redirected onto a *disallowed*
    URL on the same host and fetched — two 200s on pages robots.txt forbids.
    The host never changes, so the cross-host check these tests sit beside never
    ran. The assertion that matters is made at the fixture server: the forbidden
    path must never be requested at all.
    """

    def _shop_whose_impressum_probe_redirects(
        self, *, disallow: str, target: str
    ) -> tuple:
        def build(base: str) -> Site:
            site = shopfixtures.flat_shop(base, with_impressum=False)
            site.add(
                "/robots.txt",
                f"User-agent: *\nDisallow: {disallow}\n",
                content_type="text/plain",
            )
            site.add(
                "/",
                shopfixtures.homepage(
                    footer_impressum=False,
                    extra_links=f'<footer><a href="{target}">Impressum</a></footer>',
                ),
            )
            # The probe path robots allows, bouncing to the one it does not.
            site.add_redirect("/impressum", f"{base}{target}")
            site.add(target, shopfixtures.IMPRESSUM_HTML)
            return site

        return build

    def _assert_never_fetched(self, disallow: str, target: str) -> None:
        server = self.serve(
            self._shop_whose_impressum_probe_redirects(disallow=disallow, target=target)
        )
        result = self.run_fetch(server)

        self.assertNotIn(
            target,
            server.site.paths(),
            f"robots.txt disallows {disallow}; the redirect target {target} "
            "must never be requested, however we arrived at it",
        )
        refused = [
            row
            for row in self.artifact_rows(result.company_id)
            if row["error"] and "redirect_refused" in row["error"]
        ]
        self.assertTrue(refused, "a hop refused by robots must be recorded, not silent")
        self.assertTrue(
            any(target in (row["error"] or "") for row in refused),
            f"the recorded refusal must name {target}: {[r['error'] for r in refused]}",
        )

    def test_a_hop_to_a_disallowed_path_on_the_same_host_is_refused(self) -> None:
        """snocks.com: `Disallow: /policies/`, and `/impressum` 301s into it."""
        self._assert_never_fetched("/policies/", "/policies/legal-notice")

    def test_a_hop_differing_from_the_rule_only_in_case_is_refused(self) -> None:
        """smoke2u.de: `Disallow: /Impressum` does not match the lowercase probe
        — correctly, robots paths are case-sensitive — so the probe is genuinely
        allowed and its target genuinely is not."""
        self._assert_never_fetched("/Impressum", "/Impressum")

    def test_a_probe_that_lands_on_the_homepage_is_not_an_impressum(self) -> None:
        """M1.17, from `run 2`. With the real Impressum robots-disallowed,
        snocks.com's `/imprint` probe 302'd to `/#gbaid979323` — the homepage —
        and it was stored as the Impressum, with the homepage's own content
        hash. A soft redirect to the root means "no such page"."""

        def build(base: str) -> Site:
            site = shopfixtures.flat_shop(base, with_impressum=False)
            site.add(
                "/robots.txt",
                "User-agent: *\nDisallow: /policies/\n",
                content_type="text/plain",
            )
            site.add(
                "/",
                shopfixtures.homepage(
                    footer_impressum=False,
                    extra_links='<footer><a href="/policies/legal-notice">'
                    "Impressum</a></footer>",
                ),
            )
            site.add("/policies/legal-notice", shopfixtures.IMPRESSUM_HTML)
            for probe in ("/impressum", "/impressum/", "/imprint", "/legal"):
                site.add_redirect(probe, f"{base}/")
            return site

        server = self.serve(build)
        result = self.run_fetch(server)

        impressum_rows = [
            row
            for row in self.artifact_rows(result.company_id)
            if row["kind"] == "impressum"
        ]
        self.assertTrue(impressum_rows, "the probes themselves must still be recorded")
        for row in impressum_rows:
            self.assertIsNone(
                row["body_path"],
                "the homepage must never be stored as an Impressum body",
            )
        self.assertTrue(
            any(
                "soft_redirect_to_homepage" in (row["error"] or "")
                for row in impressum_rows
            ),
            "the rejection must be recorded with its reason, not silently dropped",
        )

        homepage_hash = next(
            row["content_hash"]
            for row in self.artifact_rows(result.company_id)
            if row["kind"] == "homepage"
        )
        self.assertNotIn(
            homepage_hash,
            [row["content_hash"] for row in impressum_rows],
            "the giveaway in run 2: the impressum row carried the homepage's hash",
        )
        self.assertIn("no_impressum", self.review_flags(result.company_id))
        self.assertIn("landed on the homepage", " ".join(result.notes))

    def test_an_allowed_same_host_hop_is_still_followed(self) -> None:
        """The anti-vacuity case. If the new check refused same-host hops
        wholesale the two tests above would pass for the wrong reason, and every
        shop with a trailing-slash redirect would break."""
        server = self.serve(
            self._shop_whose_impressum_probe_redirects(
                disallow="/nichts-hiervon/", target="/rechtliches"
            )
        )
        result = self.run_fetch(server)

        self.assertIn(
            "/rechtliches",
            server.site.paths(),
            "a hop robots permits must still be followed",
        )
        self.assertEqual([], self.review_flags(result.company_id))


class TestAMovedRegistrableDomain(FetchTestCase):
    """M1.18. Two of thirteen seeded domains had moved, and every parser
    anchored on `same_site(url, company.domain)` went quiet without erroring:
    doonails.de's five sitemap shards were never expanded, and
    germanelectronic.de's footer Impressum link was discarded as off-site."""

    def _moved_site(self, new_base: str):
        """A seeded host that redirects everything to another registrable one."""

        def build(_base: str) -> Site:
            site = Site()
            site.add(
                "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain"
            )
            site.add_redirect("/", f"{new_base}/")
            return site

        return build

    def test_the_new_host_is_adopted_and_the_seeded_domain_is_kept(self) -> None:
        new = self.serve(shopfixtures.shopware_shop, address="127.0.0.2")
        server = self.serve(self._moved_site(new.base))
        result = self.run_fetch(server, domain="seeded.de")

        row = self.conn.execute(
            "SELECT domain, site_domain, excluded FROM company WHERE id = ?",
            (result.company_id,),
        ).fetchone()
        self.assertEqual(row["domain"], "seeded.de", "the seeded identity must survive")
        self.assertEqual(row["site_domain"], new.address)
        self.assertEqual(row["excluded"], 0)
        self.assertIn("domain_moved", self.review_flags(result.company_id))

    def test_adoption_unblocks_the_parsers_that_were_going_quiet(self) -> None:
        """The point of the change: sitemaps expand and the Impressum link is
        followed on the new host, rather than being discarded as off-site."""
        new = self.serve(shopfixtures.shopware_shop, address="127.0.0.2")
        server = self.serve(self._moved_site(new.base))
        result = self.run_fetch(server, domain="seeded.de")

        self.assertIn("sitemap", result.kinds)
        self.assertIn("impressum", result.kinds)
        self.assertIn("product_page", result.kinds)
        assert result.product_sample is not None
        self.assertTrue(
            result.product_sample.startswith(new.base), result.product_sample
        )

    def test_artifacts_stay_under_the_seeded_domain_directory(self) -> None:
        """One company's evidence stays in one place across a move, and nothing
        already on disk orphans."""
        new = self.serve(shopfixtures.shopware_shop, address="127.0.0.2")
        server = self.serve(self._moved_site(new.base))
        result = self.run_fetch(server, domain="seeded.de")

        for row in self.artifact_rows(result.company_id):
            if row["body_path"]:
                self.assertTrue(
                    row["body_path"].startswith("seeded.de/"), row["body_path"]
                )

    def test_a_seeded_row_owning_the_host_always_wins_whatever_the_ids(self) -> None:
        """§6.4's rule: a seeded identity cannot be taken from a row. The rival
        here has the HIGHER id, so an id-ordering rule alone would get it
        wrong."""
        new = self.serve(shopfixtures.shopware_shop, address="127.0.0.2")
        server = self.serve(self._moved_site(new.base))

        mover = self.add_company("seeded.de")
        owner = self.add_company(new.address)  # higher id, but seeded as the host
        _run_id, results = fetch.run(
            self.conn,
            [(mover, "seeded.de")],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )

        assert results[0].excluded_reason is not None
        self.assertIn(
            f"duplicate_site: {new.address} is company #{owner}",
            results[0].excluded_reason,
        )
        moved = self.conn.execute(
            "SELECT excluded, site_domain FROM company WHERE id = ?", (mover,)
        ).fetchone()
        self.assertEqual(moved["excluded"], 1)
        self.assertIsNone(moved["site_domain"], "a loser must not claim the host")
        self.assertIn("duplicate_site", self.review_flags(owner))

    def test_between_two_adopters_the_lower_id_owns_it(self) -> None:
        """And it wins regardless of which worker got there first: here the
        HIGHER id adopts first, and is withdrawn when the lower id arrives."""
        new = self.serve(shopfixtures.shopware_shop, address="127.0.0.2")
        server = self.serve(self._moved_site(new.base))

        lower = self.add_company("first.de")
        higher = self.add_company("second.de")
        self.conn.execute(
            "UPDATE company SET site_domain = ? WHERE id = ?", (new.address, higher)
        )

        _run_id, results = fetch.run(
            self.conn,
            [(lower, "first.de")],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )

        self.assertIsNone(results[0].excluded_reason, "the lower id owns the host")
        rows = {
            row["id"]: row
            for row in self.conn.execute(
                "SELECT id, excluded, site_domain FROM company"
            )
        }
        self.assertEqual(rows[lower]["site_domain"], new.address)
        self.assertEqual(rows[higher]["excluded"], 1)
        self.assertIsNone(
            rows[higher]["site_domain"], "exactly one row may claim a host"
        )

    def test_an_apex_to_www_redirect_is_not_a_move(self) -> None:
        """Five of thirteen domains do this. Adopting there would churn
        identity for nothing."""
        server = FixtureServer(Site())
        server.site.add(
            "/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain"
        )
        server.site.add_redirect(
            "/",
            f"http://www.localhost:{server.port}/",
            only_from_host=f"localhost:{server.port}",
        )
        server.site.routes.update(shopfixtures.shopware_shop(server.base).routes)
        with server:
            company_id = self.add_company("localhost")
            _run_id, results = fetch.run(
                self.conn,
                [(company_id, "localhost")],
                self.artifacts,
                fetcher=self.fetcher,
                max_hosts=1,
                base_url=lambda _d: f"http://localhost:{server.port}",
            )

        row = self.conn.execute(
            "SELECT site_domain FROM company WHERE id = ?", (company_id,)
        ).fetchone()
        self.assertIsNone(row["site_domain"])
        self.assertNotIn("domain_moved", self.review_flags(company_id))
        self.assertEqual(results[0].excluded_reason, None)


class TestApexToWwwWithinTheSeededSite(FetchTestCase):
    """The redirect nearly every German shop has, end to end.

    `localhost` and `www.localhost` both resolve to 127.0.0.1, so one fixture
    server answering on both names is a faithful apex→www host — same machine,
    two names, one bouncing to the other — with nothing but loopback involved.
    """

    def _serve_apex_redirecting_to_www(self) -> tuple[FixtureServer, str]:
        server = FixtureServer(Site())
        apex = f"localhost:{server.port}"
        base = f"http://{apex}"
        server.site.routes.update(shopfixtures.shopware_shop(base).routes)
        # Path-preserving, and only for requests arriving on the apex name —
        # exactly how such a server behaves.
        for path in ("/robots.txt", "/"):
            server.site.add_redirect(
                path, f"http://www.{apex}{path}", only_from_host=apex
            )
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        return server, base

    def _run(self, base: str) -> fetch.CompanyResult:
        company_id = self.add_company("localhost")
        _run_id, results = fetch.run(
            self.conn,
            [(company_id, "localhost")],
            self.artifacts,
            fetcher=self.fetcher,
            max_hosts=1,
            base_url=lambda _d: base,
        )
        return results[0]

    def test_the_robots_fetch_follows_the_hop_and_the_run_proceeds(self) -> None:
        """There is no earlier robots.txt that could authorise this hop, so the
        rule is that it must stay within the seeded site — which apex→www does.
        """
        server, base = self._serve_apex_redirecting_to_www()
        result = self._run(base)

        apex = f"localhost:{server.port}"
        robots_hosts = [
            host
            for path, host in zip(server.site.paths(), server.site.hosts(), strict=True)
            if path == "/robots.txt"
        ]
        self.assertEqual(
            robots_hosts,
            [apex, f"www.{apex}"],
            "robots.txt should be asked of the apex, then followed to www",
        )
        self.assertIsNone(result.excluded_reason)
        self.assertIn("homepage", result.kinds)

    def test_the_www_policy_is_seeded_so_the_homepage_hop_costs_no_second_fetch(
        self,
    ) -> None:
        """The robots fetch already landed on www, so its policy governs www
        too. Re-deriving it on the first page hop would be a wasted request
        against a host we have already read."""
        server, base = self._serve_apex_redirecting_to_www()
        self._run(base)

        self.assertEqual(
            server.site.paths().count("/robots.txt"),
            2,
            "one apex request and the hop it redirected to — no re-derivation",
        )

    def test_a_hop_off_the_seeded_site_during_the_robots_fetch_is_refused(
        self,
    ) -> None:
        """The other half of the rule. Within the site is conventional; anywhere
        else there is nothing that could have authorised it."""
        elsewhere = self.serve(
            lambda base: shopfixtures.shopware_shop(base), address="127.0.0.2"
        )
        server = FixtureServer(Site())
        base = f"http://localhost:{server.port}"
        server.site.routes.update(shopfixtures.shopware_shop(base).routes)
        server.site.add_redirect("/robots.txt", f"{elsewhere.base}/robots.txt")
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)

        result = self._run(base)

        self.assertEqual(
            elsewhere.site.paths(), [], "the off-site host must never be contacted"
        )
        refused = [
            row
            for row in self.artifact_rows(result.company_id)
            if row["error"] and "redirect_refused" in row["error"]
        ]
        self.assertTrue(refused, "the refusal must be recorded, not silent")
        # No usable robots.txt means "no restrictions stated", so the run goes on.
        self.assertIsNone(result.excluded_reason)


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
        """ "Real evidence" means a stored document that **lists the chosen URL**
        (M1.42), not a string beginning `http`.

        The weaker assertion is what this test used to make, and it passed for
        as long as the evidence was `f"{base}/sitemap.xml"` — a URL that on 2 of
        13 real shops was never fetched, and on 2 more named the seeded host
        while the shop served from `www.` or another TLD entirely.
        """
        server = self.serve(shopfixtures.shopware_shop)
        result = self.run_fetch(server)

        row = self.sample_signal(result.company_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["value_text"], result.product_sample)
        self.assertEqual(row["method"], "deterministic")

        cited = self.conn.execute(
            "SELECT url, body_path FROM artifact WHERE id = ?", (row["artifact_id"],)
        ).fetchone()
        self.assertIsNotNone(cited, "evidence names no artifact row")
        self.assertEqual(cited["url"], row["evidence_url"])
        body = (self.artifacts / cited["body_path"]).read_bytes()
        _children, listed = sitemap.parse(body, cited["url"])
        self.assertIn(result.product_sample, listed)

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
