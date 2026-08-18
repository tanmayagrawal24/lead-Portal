"""Build a throwaway corpus from the fixture server, for CI (M2, M1.65).

`data/` is gitignored and is not in the repository, so any job that needs a
database has to build one — and it must build it **from fixtures only**. No
third-party host is contacted by anything here; the whole corpus comes from a
loopback `FixtureServer`, which is the same guarantee the rest of the suite
gives.

**Why this exists rather than a test.** `portal audit-politeness` is an
acceptance check whose product is an **exit code** (M1.19, and M1.62 gave it a
second thing to fail on). `audit.report` and `audit.robots_report` are unit
tested; the CLI that turns them into a process exit status is not, and the
review asked for the audit as a CI job. That job needs a database and a request
log, produced by the real `fetch.run` at the real 1 req/s floor — a faster
limiter would make `audit.spacing` report a breach against its own 1.0s
default, so the corpus genuinely has to take its time.

Two corpora, because a gate that can only ever go green is not a gate:

* `healthy` — a shop that serves a 200 `robots.txt`. The audit must exit 0.
* `breached` — the same shop with `robots.txt` returning 503. Under M1.59 the
  fetch stops there and stores no bodies, so the audit sees an unread policy
  and must exit 1.

Run as `python -m tests.fixture_corpus <directory> [--breached]`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from portal import config, db, fetch, migrate
from portal.addresses import AddressPolicy
from portal.net import Fetcher, HostRateLimiter, RequestLog
from tests import shopfixtures
from tests.fixture_server import FixtureServer, Site


def _seed_company(conn: sqlite3.Connection, domain: str) -> int:
    cursor = conn.execute(
        "INSERT INTO company (domain, discovery_source, discovered_at) "
        "VALUES (?, 'seed_csv', '2026-08-17T00:00:00Z')",
        (domain,),
    )
    return int(cursor.lastrowid)


def build(destination: Path, *, breached: bool = False) -> Path:
    """Fetch one fixture shop into `destination`. Returns the database path."""
    destination.mkdir(parents=True, exist_ok=True)
    database = destination / "portal.db"
    conn = db.connect(database)
    try:
        migrate.apply_pending(conn)

        def site(base: str) -> Site:
            shop = shopfixtures.shopware_shop(base)
            if breached:
                shop.add("/robots.txt", "unavailable", status=503)
            return shop

        server = FixtureServer(Site())
        server.site.routes.update(site(server.base).routes)
        with server:
            company_id = _seed_company(conn, server.address)
            fetcher = Fetcher(
                # The corpus is a loopback fixture server, so it needs the
                # same named exemption every test uses (M1.68). It widens
                # loopback alone: a redirect to 169.254.169.254 is refused
                # here exactly as it is in production.
                addresses=AddressPolicy.loopback_permitted(),
                # The real floor. `audit.spacing` judges every host against
                # 1.0s unless it stated a Crawl-delay, so a corpus built any
                # faster would fail its own audit.
                limiter=HostRateLimiter(config.POLITENESS_INTERVAL),
                log=RequestLog(config.request_log_path(database)),
            )
            run_id, results = fetch.run(
                conn,
                [(company_id, server.address)],
                config.artifacts_root(database),
                fetcher=fetcher,
                max_hosts=1,
                base_url=lambda _domain: server.base,
            )
    finally:
        conn.close()

    kinds = sorted(results[0].kinds)
    print(
        f"corpus at {database}: run {run_id}, "
        f"{len(results[0].artifacts)} artifacts, kinds={kinds or '(none)'}"
    )
    return database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--breached",
        action="store_true",
        help="serve robots.txt as 503, so the audit has something to fail on",
    )
    args = parser.parse_args(argv)
    build(args.destination, breached=args.breached)
    return 0


if __name__ == "__main__":
    sys.exit(main())
