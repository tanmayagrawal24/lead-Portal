"""§5.2's politeness rules, measured at the server rather than asserted in prose.

These are the slowest tests in the suite by design: proving a 1 req/s floor
takes seconds of wall clock. That is the cost of the guarantee being real.

Everything here runs against loopback fixture servers. `127.0.0.0/8` is all
loopback on Linux, so binding to 127.0.0.1, 127.0.0.2 and 127.0.0.3 gives three
distinct hosts — distinct rate-limiter keys and distinct `Host` headers — with
no third-party domain involved.
"""

from __future__ import annotations

import itertools
import tempfile
import threading
import time
import unittest
from pathlib import Path

from portal import db, fetch, migrate, net
from portal.net import Fetcher, HostRateLimiter
from tests import shopfixtures
from tests.fixture_server import ConcurrencyTracker, FixtureServer, Site

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
            fetcher = Fetcher(limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS))
            self.addCleanup(fetcher.close)
            response = fetcher.get(f"{server.base}/produkt/alpha")

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

        `www.localhost` and `localhost` both resolve to 127.0.0.1, so the shape
        is reachable end to end without leaving loopback.
        """
        server = FixtureServer(Site())
        server.site.add_redirect("/", f"http://www.localhost:{server.port}/de/")
        server.site.add("/de/", "<html><body>ok</body></html>")

        with server:
            fetcher = Fetcher(limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS))
            self.addCleanup(fetcher.close)
            response = fetcher.get(
                f"http://localhost:{server.port}/",
                # Vouched for, so this test measures the budget and nothing else.
                hop_allowed=lambda _from, _to: True,
            )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            server.site.hosts(),
            [f"localhost:{server.port}", f"www.localhost:{server.port}"],
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
            fetcher = Fetcher(limiter=HostRateLimiter.unthrottled())
            self.addCleanup(fetcher.close)
            response = fetcher.get(f"{server.base}/hop0")

        self.assertIsNone(response.body)
        assert response.error is not None
        self.assertIn("too_many_redirects", response.error)
        self.assertEqual(
            len(server.site.paths()),
            net.MAX_REDIRECT_HOPS + 1,
            "the cap counts hops followed, not requests refused",
        )

    def test_a_host_change_is_refused_when_the_caller_vouches_for_nothing(
        self,
    ) -> None:
        """The transport's default. It cannot know who has read whose
        robots.txt, so with no policy supplied the answer is no."""
        elsewhere = Site()
        elsewhere.add("/", "<html><body>somewhere else</body></html>")
        site = Site()

        with FixtureServer(elsewhere, address="127.0.0.2") as other:
            site.add_redirect("/", f"{other.base}/")
            with FixtureServer(site) as server:
                fetcher = Fetcher(limiter=HostRateLimiter.unthrottled())
                self.addCleanup(fetcher.close)
                response = fetcher.get(f"{server.base}/")

            self.assertEqual(
                other.site.paths(), [], "the other host must never be contacted"
            )
        assert response.error is not None
        self.assertIn("redirect_refused", response.error)


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

        fetcher = Fetcher(limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS))
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

        fetcher = Fetcher(limiter=HostRateLimiter.unthrottled())
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

        fetcher = Fetcher(limiter=HostRateLimiter.unthrottled())
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
