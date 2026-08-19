"""§5.2's politeness rules, measured at the server rather than asserted in prose.

These are the slowest tests in the suite by design: proving a 1 req/s floor
takes seconds of wall clock. That is the cost of the guarantee being real.

Everything here runs against loopback fixture servers. `127.0.0.0/8` is all
loopback on Linux, so binding to 127.0.0.1, 127.0.0.2 and 127.0.0.3 gives three
distinct hosts — distinct rate-limiter keys and distinct `Host` headers — with
no third-party domain involved.
"""

from __future__ import annotations

import contextlib
import itertools
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from portal import audit, cli, db, fetch, migrate, net, urls
from portal.addresses import AddressPolicy
from portal.net import Fetcher, HostRateLimiter
from tests import fixture_corpus, shopfixtures
from tests.fixture_server import (
    FIXTURE_APEX,
    FIXTURE_WWW,
    ConcurrencyTracker,
    FixtureServer,
    Site,
    resolves_to_loopback,
)

#: Scheduler jitter can shave a millisecond off a sleep; the rule is 1.0s and
#: this is the tolerance for measuring it, not a relaxation of the rule.
TOLERANCE = 0.02


class TestHostRateLimiter(unittest.TestCase):
    """The limiter in isolation, before it is trusted in the stage."""

    def test_spaces_calls_for_one_host(self) -> None:
        limiter = HostRateLimiter(0.25)
        started = time.monotonic()
        for _ in range(4):
            limiter.wait("example.de")
        self.assertGreaterEqual(time.monotonic() - started, 0.75 - TOLERANCE)

    def test_does_not_space_calls_across_different_hosts(self) -> None:
        limiter = HostRateLimiter(0.25)
        started = time.monotonic()
        for host in ("a.de", "b.de", "c.de", "d.de"):
            limiter.wait(host)
        self.assertLess(time.monotonic() - started, 0.25)

    def test_holds_the_gap_when_two_threads_share_a_host(self) -> None:
        """The per-host lock is held across the sleep; without that, two
        workers could both observe a stale timestamp and fire together."""
        limiter = HostRateLimiter(0.3)
        stamps: list[float] = []
        guard = threading.Lock()

        def hit() -> None:
            limiter.wait("example.de")
            with guard:
                stamps.append(time.monotonic())

        threads = [threading.Thread(target=hit) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        stamps.sort()
        for earlier, later in itertools.pairwise(stamps):
            self.assertGreaterEqual(later - earlier, 0.3 - TOLERANCE)


class TestTheResolverShim(unittest.TestCase):
    """M4 / M1.64 — the suite resolves its own hostnames, and this proves it.

    The three apex→www tests used `localhost` / `www.localhost`, which RFC 6761
    §6.3 makes optional: macOS and systemd-resolved answer for the subdomain,
    most container images do not. The tests therefore passed on the machines the
    project was developed on and failed in exactly the environment CI runs in.
    """

    def test_the_fixture_names_do_not_resolve_without_the_shim(self) -> None:
        """The anti-vacuity test, and the reason the names are `.invalid`.

        RFC 2606 §2 guarantees `.invalid` is never delegated, so this assertion
        holds on **every** machine — including the ones that resolve
        `www.localhost`. Built on `.localhost` this test would pass here while
        proving nothing, and the shim could be deleted without a failure.
        """
        for name in (FIXTURE_APEX, FIXTURE_WWW):
            with self.subTest(name=name), self.assertRaises(OSError):
                socket.getaddrinfo(name, 80)

    def test_the_shim_maps_them_and_passes_everything_else_through(self) -> None:
        with resolves_to_loopback(FIXTURE_APEX, FIXTURE_WWW) as mapped:
            self.assertEqual(mapped, sorted([FIXTURE_APEX, FIXTURE_WWW]))
            for name in (FIXTURE_APEX, FIXTURE_WWW):
                with self.subTest(name=name):
                    addresses = {info[4][0] for info in socket.getaddrinfo(name, 80)}
                    self.assertEqual(addresses, {"127.0.0.1"})
            # Pass-through: an unmapped name is still resolved by the real
            # resolver, so the shim cannot mask a genuine lookup failure.
            self.assertTrue(socket.getaddrinfo("127.0.0.2", 80))
            with self.assertRaises(OSError):
                socket.getaddrinfo("also.invalid", 80)

    def test_it_is_removed_on_exit_including_on_exception(self) -> None:
        """Scoped, not global. A shim that outlived its test would make the next
        one pass for a reason it never declared."""
        original = socket.getaddrinfo
        with (
            contextlib.suppress(RuntimeError),
            resolves_to_loopback(FIXTURE_APEX),
        ):
            self.assertIsNot(socket.getaddrinfo, original)
            raise RuntimeError("boom")
        self.assertIs(socket.getaddrinfo, original)

    def test_the_shim_is_what_makes_the_apex_www_tests_reachable(self) -> None:
        """Ties the shim to the thing it exists for: two **distinct
        authorities** on one machine, which is what M1.8's shared budget is
        about. `host_of` reads the key off the URL authority, so the names have
        to be in the URL — a `Host:` header would leave both keyed on
        `127.0.0.1:PORT` and the test unable to tell them apart."""
        apex_url = f"http://{FIXTURE_APEX}:8000/"
        www_url = f"http://{FIXTURE_WWW}:8000/"
        self.assertNotEqual(urls.authority_of(apex_url), urls.authority_of(www_url))
        self.assertEqual(urls.host_of(apex_url), urls.host_of(www_url))


class TestTheBypassIsExplicit(unittest.TestCase):
    """Switching the floor off must be an act, not a falsy default."""

    def test_a_zero_or_negative_interval_is_refused(self) -> None:
        for value in (0, 0.0, -1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HostRateLimiter(value)

    def test_the_named_bypass_is_the_only_way_through(self) -> None:
        limiter = HostRateLimiter.unthrottled()
        started = time.monotonic()
        for _ in range(5):
            limiter.wait("example.de")
        self.assertLess(time.monotonic() - started, 0.1)


class TestCrawlDelay(unittest.TestCase):
    """§5.2: honour `max(floor, Crawl-delay)`; a delay can only slow us down."""

    def test_a_host_delay_raises_the_gap_but_never_lowers_it(self) -> None:
        limiter = HostRateLimiter(0.25)
        limiter.set_host_interval("slow.de", 0.5)
        limiter.set_host_interval("hasty.de", 0.05)
        self.assertEqual(limiter.interval_for("slow.de"), 0.5)
        self.assertEqual(limiter.interval_for("hasty.de"), 0.25)
        self.assertEqual(limiter.interval_for("silent.de"), 0.25)

    def test_the_wider_gap_is_actually_waited(self) -> None:
        limiter = HostRateLimiter(0.1)
        limiter.set_host_interval("slow.de", 0.4)
        started = time.monotonic()
        limiter.wait("slow.de")
        limiter.wait("slow.de")
        self.assertGreaterEqual(time.monotonic() - started, 0.4 - TOLERANCE)


class TestRedirectsAreRateLimited(unittest.TestCase):
    """The hop is a request. A chain followed inside one `client.get()` would
    put every hop but the first below the limiter — five requests at one host
    inside a second, which is exactly what §5.2 forbids."""

    def test_every_hop_of_a_chain_waits_its_turn(self) -> None:
        site = Site()
        site.add_redirect("/produkt/alpha", "/de/produkt/alpha")
        site.add_redirect("/de/produkt/alpha", "/de/produkt/alpha/")
        site.add("/de/produkt/alpha/", shopfixtures.product_html("alpha"))

        with FixtureServer(site) as server:
            fetcher = Fetcher(
                addresses=AddressPolicy.loopback_permitted(),
                limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS),
            )
            self.addCleanup(fetcher.close)
            response = fetcher.get(
                f"{server.base}/produkt/alpha", hop_allowed=net.RobotsExempt
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.url, f"{server.base}/de/produkt/alpha/")

        arrivals = server.site.arrivals()
        self.assertEqual(
            server.site.paths(),
            ["/produkt/alpha", "/de/produkt/alpha", "/de/produkt/alpha/"],
            "every hop should have arrived at the server as its own request",
        )
        gaps = [later - earlier for earlier, later in itertools.pairwise(arrivals)]
        self.assertGreaterEqual(
            min(gaps),
            net.MIN_INTERVAL_SECONDS - TOLERANCE,
            f"a redirect hop skipped the 1 req/s floor: {gaps}",
        )

    def test_apex_and_www_hops_to_one_server_share_one_budget(self) -> None:
        """`example.de` and `www.example.de` are one machine. Keyed separately,
        the apex→www redirect that nearly every shop has would let each
        back-to-back pair issue two requests to one server inside a second —
        double §5.2's floor, on almost every domain in the corpus.

        The two names are `.invalid` and are mapped to 127.0.0.1 by the suite's
        own resolver shim (M1.64), so the hop really does cross apex→www with
        two distinct authorities in the URLs — and no resolver is asked anything.
        """
        self.enterContext(resolves_to_loopback(FIXTURE_APEX, FIXTURE_WWW))
        server = FixtureServer(Site())
        server.site.add_redirect("/", f"http://{FIXTURE_WWW}:{server.port}/de/")
        server.site.add("/de/", "<html><body>ok</body></html>")

        with server:
            fetcher = Fetcher(
                addresses=AddressPolicy.loopback_permitted(),
                limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS),
            )
            self.addCleanup(fetcher.close)
            response = fetcher.get(
                f"http://{FIXTURE_APEX}:{server.port}/",
                # Vouched for, so this test measures the budget and nothing else.
                hop_allowed=lambda _from, _to: True,
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            server.site.hosts(),
            [f"{FIXTURE_APEX}:{server.port}", f"{FIXTURE_WWW}:{server.port}"],
            "the hop must really have crossed apex→www, or this proves nothing",
        )
        arrivals = server.site.arrivals()
        self.assertEqual(len(arrivals), 2)
        self.assertGreaterEqual(
            arrivals[1] - arrivals[0],
            net.MIN_INTERVAL_SECONDS - TOLERANCE,
            "apex and www were given separate politeness budgets",
        )

    def test_a_chain_longer_than_the_cap_is_abandoned(self) -> None:
        site = Site()
        for hop in range(net.MAX_REDIRECT_HOPS + 2):
            site.add_redirect(f"/hop{hop}", f"/hop{hop + 1}")

        with FixtureServer(site) as server:
            fetcher = Fetcher(
                addresses=AddressPolicy.loopback_permitted(),
                limiter=HostRateLimiter.unthrottled(),
            )
            self.addCleanup(fetcher.close)
            response = fetcher.get(f"{server.base}/hop0", hop_allowed=net.RobotsExempt)

        self.assertIsNone(response.body)
        assert response.error is not None
        self.assertIn("too_many_redirects", response.error)
        self.assertEqual(
            len(server.site.paths()),
            net.MAX_REDIRECT_HOPS + 1,
            "the cap counts hops followed, not requests refused",
        )

    def test_a_host_change_is_refused_under_robots_exempt(self) -> None:
        """`RobotsExempt` is the robots.txt fetch's licence to run before any
        rules exist. It is not a licence to leave the origin: the transport
        cannot know who has read whose robots.txt, so an authority change is
        still refused."""
        elsewhere = Site()
        elsewhere.add("/", "<html><body>somewhere else</body></html>")
        site = Site()

        with FixtureServer(elsewhere, address="127.0.0.2") as other:
            site.add_redirect("/", f"{other.base}/")
            with FixtureServer(site) as server:
                fetcher = Fetcher(
                    addresses=AddressPolicy.loopback_permitted(),
                    limiter=HostRateLimiter.unthrottled(),
                )
                self.addCleanup(fetcher.close)
                response = fetcher.get(f"{server.base}/", hop_allowed=net.RobotsExempt)

            self.assertEqual(
                other.site.paths(), [], "the other host must never be contacted"
            )
        assert response.error is not None
        self.assertIn("redirect_refused", response.error)

    def test_omitting_the_hop_policy_is_a_type_error(self) -> None:
        """The hardening behind M1.12. This subsystem has twice shipped correct
        enforcement that was never invoked, so the policy is a required
        argument: forgetting it fails loudly at the call site instead of
        quietly permitting a hop at runtime. Exempting a fetch has to be
        written down as `RobotsExempt`, where a reviewer can see it.
        """
        fetcher = Fetcher(
            addresses=AddressPolicy.loopback_permitted(),
            limiter=HostRateLimiter.unthrottled(),
        )
        self.addCleanup(fetcher.close)
        with self.assertRaises(TypeError):
            fetcher.get("http://127.0.0.1:1/")  # type: ignore[call-arg]


class PolitenessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conn = db.connect(self.root / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def serve(
        self, address: str, tracker: ConcurrencyTracker, delay: float = 0.0
    ) -> FixtureServer:
        server = FixtureServer(Site(), tracker=tracker, address=address)
        server.site.routes.update(
            shopfixtures.flat_shop(server.base, footer_impressum=True).routes
        )
        server.site.delay = delay
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


class TestOneRequestPerSecondPerHost(PolitenessTestCase):
    def test_arrivals_at_the_server_are_at_least_one_second_apart(self) -> None:
        """The §5.2 floor, at its real value, measured by the server's clock."""
        tracker = ConcurrencyTracker()
        server = self.serve("127.0.0.1", tracker)
        company_id = self.add_company("127.0.0.1")

        fetcher = Fetcher(
            addresses=AddressPolicy.loopback_permitted(),
            limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS),
        )
        self.addCleanup(fetcher.close)
        fetch.run(
            self.conn,
            [(company_id, "127.0.0.1")],
            self.root / "artifacts",
            fetcher=fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )

        arrivals = server.site.arrivals()
        self.assertGreaterEqual(len(arrivals), 4, "too few requests to prove spacing")
        gaps = [later - earlier for earlier, later in itertools.pairwise(arrivals)]
        self.assertGreaterEqual(
            min(gaps),
            net.MIN_INTERVAL_SECONDS - TOLERANCE,
            f"a gap fell below the 1 req/s floor: {sorted(gaps)[:3]}",
        )


class TestConcurrencyCeiling(PolitenessTestCase):
    def test_never_more_than_two_hosts_in_flight(self) -> None:
        """§5.2's ceiling, measured at the servers.

        Three domains and a server-side delay, so the pool is saturated and a
        third host would overlap if the ceiling were not enforced.
        """
        tracker = ConcurrencyTracker()
        addresses = ["127.0.0.1", "127.0.0.2", "127.0.0.3"]
        servers = {a: self.serve(a, tracker, delay=0.15) for a in addresses}
        targets = [(self.add_company(a), a) for a in addresses]

        fetcher = Fetcher(
            addresses=AddressPolicy.loopback_permitted(),
            limiter=HostRateLimiter.unthrottled(),
        )
        self.addCleanup(fetcher.close)
        fetch.run(
            self.conn,
            targets,
            self.root / "artifacts",
            fetcher=fetcher,
            max_hosts=net.MAX_CONCURRENT_HOSTS,
            base_url=lambda domain: servers[domain].base,
        )

        self.assertGreater(tracker.max_hosts, 0, "no requests were observed")
        self.assertLessEqual(
            tracker.max_hosts,
            net.MAX_CONCURRENT_HOSTS,
            f"{tracker.max_hosts} hosts were served at once",
        )

    def test_the_pool_really_does_run_two_hosts_at_once(self) -> None:
        """Guards the test above from passing vacuously: if the stage were
        sequential, `max_hosts` would be 1 and the ceiling assertion would be
        meaningless."""
        tracker = ConcurrencyTracker()
        addresses = ["127.0.0.1", "127.0.0.2"]
        servers = {a: self.serve(a, tracker, delay=0.3) for a in addresses}
        targets = [(self.add_company(a), a) for a in addresses]

        fetcher = Fetcher(
            addresses=AddressPolicy.loopback_permitted(),
            limiter=HostRateLimiter.unthrottled(),
        )
        self.addCleanup(fetcher.close)
        fetch.run(
            self.conn,
            targets,
            self.root / "artifacts",
            fetcher=fetcher,
            max_hosts=2,
            base_url=lambda domain: servers[domain].base,
        )
        self.assertEqual(tracker.max_hosts, 2)


if __name__ == "__main__":
    unittest.main()


class TestRequestLogAndAudit(unittest.TestCase):
    """M1.19. The log exists so §5.2's floor is *observed* rather than argued
    about; these pin that it observes the right thing."""

    def _log_path(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name) / "requests.jsonl"

    def test_every_hop_of_a_chain_is_logged_with_its_issue_time(self) -> None:
        """A caller-side log would miss redirect hops. They are requests."""
        site = Site()
        site.add_redirect("/a", "/b")
        site.add_redirect("/b", "/c")
        site.add("/c", "<html><body>ok</body></html>")
        path = self._log_path()

        with FixtureServer(site) as server:
            fetcher = Fetcher(
                addresses=AddressPolicy.loopback_permitted(),
                limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS),
                log=net.RequestLog(path),
            )
            self.addCleanup(fetcher.close)
            fetcher.get(f"{server.base}/a", hop_allowed=net.RobotsExempt)

        entries = audit.load(path)
        self.assertEqual([e["url"].rsplit("/", 1)[1] for e in entries], ["a", "b", "c"])
        report = audit.spacing(entries)
        self.assertEqual(len(report), 1)
        assert report[0].min_gap is not None
        self.assertGreaterEqual(report[0].min_gap, net.MIN_INTERVAL_SECONDS - TOLERANCE)
        self.assertTrue(report[0].ok)

    def test_the_audit_reports_held_for_a_real_run(self) -> None:
        site = Site()
        site.add("/", "<html><body>ok</body></html>")
        path = self._log_path()

        with FixtureServer(site) as server:
            fetcher = Fetcher(
                addresses=AddressPolicy.loopback_permitted(),
                limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS),
                log=net.RequestLog(path),
            )
            self.addCleanup(fetcher.close)
            for _ in range(3):
                fetcher.get(f"{server.base}/", hop_allowed=net.RobotsExempt)

        text, ok = audit.report(path)
        self.assertTrue(ok, text)
        self.assertIn("§5.2: HELD", text)

    def test_the_audit_would_actually_catch_a_breach(self) -> None:
        """The anti-vacuity test. A report that only ever says HELD proves
        nothing, so this feeds it a log that breaches both rules."""
        path = self._log_path()
        log = net.RequestLog(path)
        # Two requests 0.2s apart on one host, and three hosts overlapping.
        log.record(
            issued_at="x", issued_monotonic=100.0, elapsed=1.0, host="a.de", url="u"
        )
        log.record(
            issued_at="x", issued_monotonic=100.2, elapsed=1.0, host="a.de", url="u"
        )
        log.record(
            issued_at="x", issued_monotonic=100.1, elapsed=1.0, host="b.de", url="u"
        )
        log.record(
            issued_at="x", issued_monotonic=100.1, elapsed=1.0, host="c.de", url="u"
        )

        entries = audit.load(path)
        self.assertEqual(audit.max_hosts_in_flight(entries), 3)
        text, ok = audit.report(path)
        self.assertFalse(ok)
        self.assertIn("BREACHED", text)
        self.assertIn("*** UNDER ***", text)
        self.assertIn("*** OVER ***", text)

    def test_a_report_without_a_database_says_so_rather_than_looking_green(
        self,
    ) -> None:
        """M1.62: spacing alone is not a politeness verdict."""
        site = Site()
        site.add("/", "<html><body>ok</body></html>")
        path = self._log_path()
        with FixtureServer(site) as server:
            fetcher = Fetcher(
                addresses=AddressPolicy.loopback_permitted(),
                limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS),
                log=net.RequestLog(path),
            )
            self.addCleanup(fetcher.close)
            fetcher.get(f"{server.base}/", hop_allowed=net.RobotsExempt)

        text, _ok = audit.report(path)
        self.assertIn("NOT CHECKED", text)

    def test_crawl_delay_hosts_are_judged_against_their_own_interval(self) -> None:
        """1.0s is fine for most hosts and a breach for one that asked for 5s."""
        path = self._log_path()
        log = net.RequestLog(path)
        log.record(
            issued_at="x", issued_monotonic=10.0, elapsed=0.1, host="slow.de", url="u"
        )
        log.record(
            issued_at="x", issued_monotonic=11.0, elapsed=0.1, host="slow.de", url="u"
        )

        entries = audit.load(path)
        self.assertTrue(audit.spacing(entries)[0].ok)
        self.assertFalse(audit.spacing(entries, {"slow.de": 5.0})[0].ok)


class TestRobotsCoverageAudit(unittest.TestCase):
    """M1.62 — the half of `audit-politeness` that was blind by construction.

    Spacing measures how fast requests arrived. H1 changed *which pages may be
    fetched at all*, and left the spacing correct, so the audit passed over a
    policy that was never read. These pin that it no longer can.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.conn = db.connect(self.root / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.conn.execute(
            "INSERT INTO run (started_at, stage) VALUES ('2026-08-17T00:00:00Z','fetch')"
        )
        self.company_id = int(
            self.conn.execute(
                "INSERT INTO company (domain, discovery_source, discovered_at) "
                "VALUES ('muster.example','seed_csv','2026-08-17T00:00:00Z')"
            ).lastrowid
        )

    def _artifact(
        self,
        kind: str,
        url: str,
        status: int | None,
        content_hash: str | None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
            "error, fetched_at, last_checked_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                self.company_id,
                kind,
                url,
                status,
                content_hash,
                error,
                "2026-08-17T00:00:00Z",
                "2026-08-17T00:00:00Z",
            ),
        )

    def test_a_readable_robots_is_not_reported(self) -> None:
        self._artifact("robots", "https://muster.example/robots.txt", 200, "aaa")
        self._artifact("homepage", "https://muster.example/", 200, "bbb")
        text, ok = audit.robots_report(self.conn)
        self.assertTrue(ok, text)
        self.assertIn("every stored robots artifact is a 200", text)

    def test_a_503_robots_with_bodies_beside_it_is_a_breach(self) -> None:
        """The exact state H1 produced: a policy nobody read, and pages stored
        under it anyway."""
        self._artifact(
            "robots", "https://muster.example/robots.txt", 503, None, "http_503"
        )
        self._artifact("impressum", "https://muster.example/impressum", 200, "bbb")
        findings = audit.robots_coverage(self.conn)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].unavailable)
        self.assertTrue(findings[0].breach)
        text, ok = audit.robots_report(self.conn)
        self.assertFalse(ok)
        self.assertIn("*** UNREAD ***", text)
        self.assertIn("BREACH", text)

    # ---- M1.75: the origin key -----------------------------------------

    def test_bodies_on_an_origin_with_no_robots_row_are_not_verifiable(self) -> None:
        """M1.61's second finding, and the one `robots_coverage` could not see.

        On the 17-August corpus this was 26 bodies on `www.smoke2u.de` and
        `www.propellerdiscount.de`: both were fetched 200, both served bytes
        identical to their apex sibling, and `uq_artifact_identity` collapsed
        the pair into a single row keyed on the apex. The audit reported
        nothing, because nothing had failed — the row was a healthy 200.
        """
        self._artifact("robots", "https://muster.example/robots.txt", 200, "aaa")
        self._artifact("impressum", "https://www.muster.example/impressum", 200, "bbb")

        # Nothing failed, so the class that reports failures stays empty.
        self.assertEqual(audit.robots_coverage(self.conn), [])

        origins = audit.unverifiable_origins(self.conn)
        self.assertEqual([o.authority for o in origins], ["www.muster.example"])
        self.assertEqual(origins[0].bodies, 1)

        text, ok = audit.robots_report(self.conn)
        self.assertFalse(ok, text)
        self.assertIn("NOT VERIFIABLE", text)
        self.assertIn("www.muster.example", text)

    def test_apex_and_www_are_separate_authorities(self) -> None:
        """RFC 9309 keys robots.txt to the origin. A file served by the apex
        does not speak for `www.`, which is the whole of M1.61 — and is why
        this cannot be settled by a suffix match."""
        self._artifact("robots", "https://muster.example/robots.txt", 200, "aaa")
        self._artifact("robots", "https://www.muster.example/robots.txt", 200, "ccc")
        self._artifact("impressum", "https://www.muster.example/impressum", 200, "bbb")
        self.assertEqual(audit.unverifiable_origins(self.conn), [])
        _, ok = audit.robots_report(self.conn)
        self.assertTrue(ok)

    def test_a_404_covers_its_origin_but_a_sibling_does_not(self) -> None:
        """The two negatives are different. A 404 is RFC 9309 §2.3.1.2 — the
        shop stated no rules, and that origin is answered for. A sibling's file
        answers for nothing, however permissive it is."""
        self._artifact(
            "robots", "https://muster.example/robots.txt", 404, None, "http_404"
        )
        self._artifact("impressum", "https://muster.example/impressum", 200, "bbb")
        self.assertEqual(audit.unverifiable_origins(self.conn), [])

        # Same company, a body on a sibling origin the 404 does not answer for.
        self._artifact("blog_index", "https://shop.muster.example/blog", 200, "ddd")
        self.assertEqual(
            [o.authority for o in audit.unverifiable_origins(self.conn)],
            ["shop.muster.example"],
        )

    def test_the_breach_denominator_is_the_origin_not_the_company(self) -> None:
        """`audit.py` joined `b.company_id = a.company_id`, so a 503 on one
        origin counted bodies a *different* origin's robots.txt governs. The
        row is still a breach here — but on its own two bodies, not on four."""
        self._artifact(
            "robots", "https://muster.example/robots.txt", 503, None, "http_503"
        )
        self._artifact("impressum", "https://muster.example/impressum", 200, "b1")
        self._artifact("homepage", "https://muster.example/", 200, "b2")
        self._artifact("robots", "https://blog.muster.example/robots.txt", 200, "ccc")
        self._artifact("blog_index", "https://blog.muster.example/blog", 200, "b3")
        self._artifact("product_page", "https://blog.muster.example/p", 200, "b4")

        findings = audit.robots_coverage(self.conn)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].breach)
        self.assertEqual(findings[0].bodies_for_origin, 2)

    def test_a_429_and_a_transport_failure_both_fail(self) -> None:
        for status, error in ((429, "http_429"), (None, "ConnectTimeout: x")):
            with self.subTest(status=status):
                self.conn.execute("DELETE FROM artifact")
                self._artifact(
                    "robots", "https://muster.example/robots.txt", status, None, error
                )
                _text, ok = audit.robots_report(self.conn)
                self.assertFalse(ok)

    def test_a_404_robots_is_reported_and_does_not_fail(self) -> None:
        """RFC 9309 §2.3.1.2 — a shop with no robots.txt is not a breach, and a
        check that reddened on it would be a check nobody reads."""
        self._artifact(
            "robots", "https://muster.example/robots.txt", 404, None, "http_404"
        )
        self._artifact("homepage", "https://muster.example/", 200, "bbb")
        text, ok = audit.robots_report(self.conn)
        self.assertTrue(ok, text)
        self.assertIn("no file", text)

    def test_a_refused_redirect_is_reported_and_does_not_fail(self) -> None:
        """M1.18's moved-domain shape, 2 of 13 in the real corpus."""
        self._artifact(
            "robots",
            "https://muster.example/robots.txt",
            301,
            None,
            "redirect_refused: https://elsewhere.example/robots.txt",
        )
        text, ok = audit.robots_report(self.conn)
        self.assertTrue(ok, text)
        self.assertIn("not a breach", text)

    def test_the_full_report_fails_on_robots_even_when_spacing_held(self) -> None:
        """The anti-vacuity test for M1.62: a log with perfect spacing must not
        be able to produce a green audit over an unread policy."""
        path = self.root / "requests.jsonl"
        log = net.RequestLog(path)
        for offset in range(3):
            log.record(
                issued_at="x",
                issued_monotonic=100.0 + offset * 2,
                elapsed=0.1,
                host="muster.example",
                url="https://muster.example/",
            )
        self._artifact(
            "robots", "https://muster.example/robots.txt", 503, None, "http_503"
        )
        self._artifact("impressum", "https://muster.example/impressum", 200, "bbb")

        text, ok = audit.report(path, conn=self.conn)
        self.assertIn("ok", text)  # spacing held
        self.assertFalse(ok, text)
        self.assertIn("§5.2: BREACHED", text)


class TestTheAuditCliExitCode(unittest.TestCase):
    """M2 / M1.65 — `audit-politeness` is a gate, and a gate is its exit code.

    `audit.report` is unit-tested above; the CLI that turns it into a process
    status was not tested at all, and that status is the entire point of M1.19's
    "an acceptance check rather than something to read". CI runs this same path
    against a fixture-built corpus.

    The breached corpus is the one exercised here because it is the fast one:
    under M1.59 a 503 `robots.txt` stops the run at one request, so no politeness
    floor has to be waited out. The healthy corpus takes ~10s at the real 1 req/s
    and is left to the CI job.
    """

    def test_a_corpus_with_an_unread_policy_exits_non_zero(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        database = fixture_corpus.build(Path(tmp.name), breached=True)

        code = cli.main(["--db", str(database), "audit-politeness"])
        self.assertEqual(code, 1, "an unread robots.txt must fail the gate")

    def test_the_gate_refuses_to_pass_without_a_database(self) -> None:
        """M1.62: spacing alone is not a politeness verdict, so a missing
        database is an error rather than a narrower audit."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        database = fixture_corpus.build(Path(tmp.name), breached=True)
        database.unlink()

        self.assertEqual(cli.main(["--db", str(database), "audit-politeness"]), 2)
