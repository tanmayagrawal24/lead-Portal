"""H2/M1.68 — the second gate, on where a URL points rather than what it says.

`hop_allowed` asks the redirect target's own server for permission. These tests
exist because that is the wrong authority to ask when the target is on the
operator's own machine: a service that has no `robots.txt` answers 404, and a
404 means *no rules stated* and therefore *everything permitted*.

**The measurement this replaces.** Against `d57ea64`, a fixture shop redirecting
`/` to the portal's own §9 page on `127.0.0.1` caused 9,537 bytes of that page —
naming a real prospect out of the operator's database — to be fetched and stored
in `artifact` as the shop's `homepage`. `test_a_redirect_into_the_operators_own_
machine_is_refused` is that PoC, kept as a test.

Nothing here resolves a public name. Every case is either an IP literal (no DNS
at all), a name under an injected resolver, or `.invalid` under the suite's own
shim — so this file leaves the machine exactly as often as the rest of the
suite does, which is never.
"""

from __future__ import annotations

import socket
import sqlite3
import tempfile
import unittest
from ipaddress import ip_address
from pathlib import Path

from portal import db, fetch, migrate, net
from portal.addresses import LOOPBACK, AddressPolicy, classify
from portal.net import Fetcher, HostRateLimiter
from tests.fixture_server import FixtureServer, Site, resolves_to_loopback

#: Reachable from most clouds, unauthenticated, and the first thing an SSRF
#: goes for. Never contacted here — the guard refuses it before a socket opens.
METADATA = "169.254.169.254"


class TestClassification(unittest.TestCase):
    """The table, in isolation. No resolver is involved in any of these."""

    def test_refused_ranges(self) -> None:
        cases = {
            "127.0.0.1": LOOPBACK,
            "127.1.2.3": LOOPBACK,
            "::1": LOOPBACK,
            "0.0.0.0": "unspecified",
            "10.1.2.3": "private",
            "172.16.0.1": "private",
            "172.31.255.255": "private",
            "192.168.1.1": "private",
            "100.64.0.1": "carrier-grade NAT",
            METADATA: "link-local (cloud metadata)",
            "fe80::1": "link-local",
            "fd00::1": "unique-local",
            "224.0.0.1": "multicast",
            "255.255.255.255": "reserved",
            "192.0.2.1": "documentation",
            "2001:db8::1": "documentation",
        }
        for literal, why in cases.items():
            with self.subTest(address=literal):
                self.assertEqual(classify(ip_address(literal)), why)

    def test_public_addresses_are_not_refused(self) -> None:
        for literal in ("93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"):
            with self.subTest(address=literal):
                self.assertIsNone(classify(ip_address(literal)))

    def test_an_ipv4_mapped_v6_address_is_judged_as_its_ipv4(self) -> None:
        """`::ffff:127.0.0.1` is loopback written in another notation. Judged as
        v6 alone it sits in no refused v6 network and reads as public, which is
        the same bypass with a colon in it."""
        self.assertEqual(classify(ip_address("::ffff:127.0.0.1")), LOOPBACK)
        self.assertEqual(
            classify(ip_address("::ffff:169.254.169.254")),
            "link-local (cloud metadata)",
        )


class _Resolver:
    """A resolver that records its calls, so a test can assert one never happened."""

    def __init__(
        self,
        answers: dict[str, list[str]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.answers = answers or {}
        self.error = error
        self.calls: list[str] = []

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.calls.append(host)
        if self.error is not None:
            raise self.error
        return [
            (0, 0, 0, "", (address, port)) for address in self.answers.get(host, [])
        ]


class TestAddressPolicy(unittest.TestCase):
    def test_a_literal_address_is_never_resolved(self) -> None:
        """The metadata case must not be defeatable by anything DNS does, so it
        must not consult DNS. Asserted by refusing to answer."""
        resolver = _Resolver(error=AssertionError("the guard resolved a literal"))
        verdict = AddressPolicy(resolver=resolver).verdict_for(f"http://{METADATA}/")
        self.assertFalse(verdict.permitted)
        self.assertEqual(resolver.calls, [])
        assert verdict.reason is not None
        self.assertIn("link-local", verdict.reason)

    def test_one_bad_answer_refuses_the_whole_name(self) -> None:
        """A name answering with one public and one loopback address is the
        shape of a rebinding attack. Picking the reassuring half is how a guard
        is talked out of firing."""
        resolver = _Resolver({"mixed.example": ["93.184.216.34", "127.0.0.1"]})
        verdict = AddressPolicy(resolver=resolver).verdict_for("http://mixed.example/")
        self.assertFalse(verdict.permitted)

    def test_a_public_name_is_permitted(self) -> None:
        resolver = _Resolver({"shop.example": ["93.184.216.34"]})
        verdict = AddressPolicy(resolver=resolver).verdict_for("http://shop.example/")
        self.assertTrue(verdict.permitted)
        self.assertIsNone(verdict.reason)

    def test_a_name_that_does_not_resolve_is_unverifiable_and_not_allowed(self) -> None:
        """M1.59's ruling, one layer down: a thing we could not check reports
        *not verifiable* rather than *allowed*, and says which it was."""
        resolver = _Resolver(error=socket.gaierror("Name or service not known"))
        verdict = AddressPolicy(resolver=resolver).verdict_for(
            "http://nowhere.example/"
        )
        self.assertFalse(verdict.permitted)
        assert verdict.reason is not None
        self.assertTrue(verdict.reason.startswith("address_unverifiable"))

    def test_loopback_permitted_widens_loopback_and_nothing_else(self) -> None:
        """The seam the whole suite runs under. If it widened more than
        loopback, every test below would be running with the guard off."""
        policy = AddressPolicy.loopback_permitted()
        self.assertTrue(policy.verdict_for("http://127.0.0.1:8000/").permitted)
        self.assertTrue(policy.verdict_for("http://[::1]:8000/").permitted)
        for refused in (METADATA, "10.0.0.1", "192.168.0.1", "172.20.0.1"):
            with self.subTest(address=refused):
                self.assertFalse(policy.verdict_for(f"http://{refused}/").permitted)

    def test_the_resolver_is_looked_up_at_call_time_not_import_time(self) -> None:
        """**The guard must ask the same resolver the client will ask.**

        `resolves_to_loopback` installs its shim by rebinding the
        `socket.getaddrinfo` attribute (M1.64). A default argument bound at
        class-definition time would hold the original function, and the guard
        would then judge a name the client is about to resolve differently —
        `address_unverifiable` here, while httpcore connects to 127.0.0.1.

        The two outcomes are distinguishable, which is what makes this a test
        rather than a comment: bound early the reason says *did not resolve*;
        looked up late it says *loopback*.
        """
        with resolves_to_loopback("late-binding.invalid"):
            verdict = AddressPolicy().verdict_for("http://late-binding.invalid/")
        self.assertFalse(verdict.permitted)
        assert verdict.reason is not None
        self.assertIn(LOOPBACK, verdict.reason)
        self.assertNotIn("did not resolve", verdict.reason)


class TestTheGuardInTheTransport(unittest.TestCase):
    """`Fetcher.get`, end to end against a real socket."""

    def test_the_production_default_refuses_the_fixture_server(self) -> None:
        """The anti-vacuity test for the seam. Everything else in the suite
        passes `loopback_permitted`, so without this nothing would establish
        that the *default* — the one production runs under — refuses at all.

        The fixture server's own request list is the measurement: the guard's
        job is that no socket opens, not that the response is discarded.
        """
        site = Site()
        site.add("/", "<html>should never be served</html>")
        with FixtureServer(site) as server:
            fetcher = Fetcher(limiter=HostRateLimiter.unthrottled())
            self.addCleanup(fetcher.close)
            response = fetcher.get(f"{server.base}/", hop_allowed=net.RobotsExempt)

        assert response.error is not None
        self.assertTrue(response.error.startswith("address_refused"))
        self.assertIn(LOOPBACK, response.error)
        self.assertIsNone(response.body)
        self.assertEqual(site.paths(), [], "the request must not have been issued")

    def test_a_redirect_into_a_private_address_is_refused(self) -> None:
        """Runs under the same `loopback_permitted` policy as every other test,
        which is the point: the exemption widens loopback and this hop is
        link-local, so the guard is live in the suite rather than switched off
        by it."""
        site = Site()
        site.add_redirect("/", f"http://{METADATA}/latest/meta-data/", status=302)
        with FixtureServer(site) as server:
            fetcher = Fetcher(
                addresses=AddressPolicy.loopback_permitted(),
                limiter=HostRateLimiter.unthrottled(),
            )
            self.addCleanup(fetcher.close)
            response = fetcher.get(f"{server.base}/", hop_allowed=lambda _f, _t: True)

        assert response.error is not None
        self.assertTrue(response.error.startswith("address_refused"))
        self.assertIn(METADATA, response.error)
        # The hop that produced the refusal is carried, exactly as
        # `redirect_refused` carries it, so the artifact row shows the 302.
        self.assertEqual(response.status, 302)
        self.assertEqual(site.paths(), ["/"], "only the first hop was issued")

    def test_a_refused_request_is_not_written_to_the_request_log(self) -> None:
        """M1.19's log is one line per request **issued**, and `audit.spacing`
        computes gaps from it. A refusal that never opened a socket would show
        up there as a request that happened, and could manufacture a spacing
        violation out of a request nobody made."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.jsonl"
            fetcher = Fetcher(
                limiter=HostRateLimiter.unthrottled(), log=net.RequestLog(path)
            )
            self.addCleanup(fetcher.close)
            fetcher.get(f"http://{METADATA}/", hop_allowed=net.RobotsExempt)
            self.assertFalse(
                path.exists() and path.read_text().strip(),
                "a request that was never issued must not appear in the log",
            )

    def test_a_refusal_does_not_spend_the_politeness_budget(self) -> None:
        """The guard runs before the limiter. A host we will not talk to must
        not cost a second of the budget of the host we will."""
        fetcher = Fetcher(limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS))
        self.addCleanup(fetcher.close)
        import time

        started = time.monotonic()
        for _ in range(3):
            fetcher.get(f"http://{METADATA}/", hop_allowed=net.RobotsExempt)
        self.assertLess(time.monotonic() - started, net.MIN_INTERVAL_SECONDS)


class TestTheOriginalProofOfConcept(unittest.TestCase):
    """The measured defect, kept as a regression test at the stage level."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.conn: sqlite3.Connection = db.connect(self.root / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def _seed(self, domain: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES (?, 'seed_csv', '2026-08-18T00:00:00Z')",
            (domain,),
        )
        return int(cursor.lastrowid)

    def test_a_redirect_into_the_operators_own_machine_is_refused(self) -> None:
        """The PoC from the unit report, as a test.

        The shop allows everything in `robots.txt` and bounces its homepage to
        another service. On `d57ea64` that service's body was stored under this
        company as its `homepage`; the robots probe against it 404'd, which
        reads as *no rules stated*.

        The internal service here is a second fixture server bound to
        `127.0.0.2` and the policy is the strict one, so the hop is refused as
        loopback. What is asserted is not only the refusal but that **no body
        from the internal service was stored** — the original defect was a
        corpus-integrity defect as much as an exfiltration one.
        """
        internal = Site()
        internal.add("/robots.txt", "not found", status=404)
        internal.add("/", "<html><title>Lead Portal</title>a-real-prospect.de</html>")

        with FixtureServer(internal, address="127.0.0.2") as victim:
            shop = Site()
            shop.add("/robots.txt", "User-agent: *\nAllow: /\n")
            shop.add_redirect("/", f"{victim.base}/", status=302)
            with FixtureServer(shop) as server:
                company_id = self._seed(server.address)
                fetcher = Fetcher(limiter=HostRateLimiter.unthrottled())
                self.addCleanup(fetcher.close)
                _run_id, results = fetch.run(
                    self.conn,
                    [(company_id, server.address)],
                    self.root / "artifacts",
                    fetcher=fetcher,
                    max_hosts=1,
                    base_url=lambda _domain: server.base,
                )

            self.assertEqual(internal.paths(), [], "the internal service was contacted")

        stored = self.conn.execute(
            "SELECT url, error, body_path FROM artifact WHERE company_id = ?",
            (company_id,),
        ).fetchall()
        self.assertTrue(stored, "the refusal must still be recorded")
        for row in stored:
            self.assertIsNone(row["body_path"], f"a body was stored for {row['url']}")
        self.assertTrue(
            any("address_refused" in (row["error"] or "") for row in stored),
            f"no refusal recorded: {[dict(r) for r in stored]}",
        )
        self.assertEqual(results[0].kinds, set())


if __name__ == "__main__":
    unittest.main()
