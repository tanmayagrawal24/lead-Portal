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

        fetcher = Fetcher(limiter=HostRateLimiter(0.0))
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

        fetcher = Fetcher(limiter=HostRateLimiter(0.0))
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
