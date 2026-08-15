"""The single live smoke test. Opt-in, first-party only.

This is the **only** code in the repository that touches a network host outside
loopback, and the only domain it will ever contact is `creative-potato.global` —
Creative Potatoes' own site. It is skipped unless `PORTAL_LIVE_SMOKE=1`, so
`pytest` and CI never reach out on their own.

Do not add domains here. A seed list of third-party shops is Tanmay's to
approve; §5.2's politeness rules exist because the far end of every request is
somebody else's server.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import httpx

from portal import db, fetch, migrate, net
from portal.net import Fetcher, HostRateLimiter

LIVE_DOMAIN = "creative-potato.global"
CONTACT_URL = "https://creative-potato.global"

ENABLED = os.environ.get("PORTAL_LIVE_SMOKE") == "1"
SKIP_REASON = "set PORTAL_LIVE_SMOKE=1 to run the live first-party smoke test"


@unittest.skipUnless(ENABLED, SKIP_REASON)
class TestLiveSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conn = db.connect(self.root / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def test_user_agent_contact_url_resolves(self) -> None:
        """§5.2 and §8 promise an identifiable bot with a contact route.

        If this URL does not resolve, the promise is empty and the User-Agent
        needs changing before any real crawl.
        """
        self.assertIn(CONTACT_URL, net.USER_AGENT)
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(CONTACT_URL, headers={"User-Agent": net.USER_AGENT})
        self.assertLess(
            response.status_code,
            400,
            f"the User-Agent's contact URL returned {response.status_code}",
        )

    def test_end_to_end_against_first_party_site(self) -> None:
        cursor = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES (?, 'seed_csv', '2026-08-15T00:00:00Z')",
            (LIVE_DOMAIN,),
        )
        company_id = int(cursor.lastrowid)

        fetcher = Fetcher(limiter=HostRateLimiter(net.MIN_INTERVAL_SECONDS))
        self.addCleanup(fetcher.close)
        _run_id, results = fetch.run(
            self.conn,
            [(company_id, LIVE_DOMAIN)],
            self.root / "artifacts",
            fetcher=fetcher,
            max_hosts=1,
        )
        result = results[0]

        self.assertIn("robots", {a.kind for a in result.artifacts})
        rows = self.conn.execute(
            "SELECT * FROM artifact WHERE company_id = ?", (company_id,)
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            if row["http_status"] == 200:
                self.assertTrue(row["content_hash"])
                self.assertTrue((self.root / "artifacts" / row["body_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
