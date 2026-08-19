"""Politeness auditing from the request log (§5.2, M1.19).

M1's done-when is "robots respected; 1 req/s **observed**". Robots is provable
from `artifact` rows. Spacing is not: those rows are written when a response
*lands*, so the gaps between them measure the server's latency variance, not
our request spacing. Reading them that way appeared to show ten violations in
the first crawl and showed nothing of the kind.

This reads `net.RequestLog` instead, which records the moment each request was
issued — after the limiter returned, immediately before the request went out.
Gaps come from the monotonic clock, so a wall-clock adjustment mid-run cannot
manufacture or hide a violation.

Concurrency is measured as *hosts in flight*, which is what §5.2 caps: a
request occupies its host from `issued` until `issued + elapsed`, and the
answer is the largest number of distinct hosts whose intervals overlap at any
instant. Counting requests rather than hosts would be a different and much
weaker claim.

**Spacing is not the whole of politeness, and this file used to behave as
though it were (M1.62).** Under H1 a robots.txt that 503'd produced an
unrestricted policy, so the pages that followed were spaced against the
*default* interval and this audit passed — the instrument built to verify
politeness could not see the one failure that makes politeness wrong, because
the failure changes which pages may be fetched and not how fast they arrive.
`robots_coverage` reads the artifact table for that, and `report` fails on it.
`unverifiable_origins` reports the class it cannot see (M1.75): an origin with
stored bodies and no robots.txt filed under it at all
the way it already fails on a spacing breach (M1.19's instinct: a rule should
be enforceable, not stated).
"""

from __future__ import annotations

import itertools
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portal.net import MAX_CONCURRENT_HOSTS
from portal.urls import authority_of


@dataclass(frozen=True)
class HostSpacing:
    host: str
    requests: int
    min_gap: float | None
    interval: float  # what this host was owed: 1.0s, or its Crawl-delay

    @property
    def ok(self) -> bool:
        return self.min_gap is None or self.min_gap >= self.interval - TOLERANCE


#: Scheduler jitter can shave a fraction of a millisecond off a sleep. The rule
#: is the interval; this is the tolerance for measuring it, matching the value
#: `tests/test_politeness.py` asserts with.
TOLERANCE = 0.02


def load(path: Path) -> list[dict]:
    """Every logged request, oldest first. Malformed lines fail loudly."""
    entries: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: malformed log line: {exc}") from exc
    return sorted(entries, key=lambda e: e["issued_monotonic"])


def spacing(
    entries: list[dict], intervals: dict[str, float] | None = None
) -> list[HostSpacing]:
    """Smallest gap between consecutive requests to each politeness key."""
    intervals = intervals or {}
    per_host: dict[str, list[float]] = {}
    for entry in entries:
        per_host.setdefault(entry["host"], []).append(entry["issued_monotonic"])

    report = []
    for host, issued in sorted(per_host.items()):
        issued.sort()
        gaps = [b - a for a, b in itertools.pairwise(issued)]
        report.append(
            HostSpacing(
                host=host,
                requests=len(issued),
                min_gap=min(gaps) if gaps else None,
                interval=intervals.get(host, 1.0),
            )
        )
    return report


def max_hosts_in_flight(entries: list[dict]) -> int:
    """The most distinct hosts occupied at any one instant.

    A sweep over interval endpoints. Distinct *hosts*, not requests: two
    sequential requests to one host are one host in flight, and §5.2's ceiling
    is on hosts.
    """
    events: list[tuple[float, int, str]] = []
    for entry in entries:
        start = entry["issued_monotonic"]
        events.append((start, 1, entry["host"]))
        events.append((start + entry.get("elapsed", 0.0), -1, entry["host"]))
    events.sort(key=lambda e: (e[0], e[1]))  # ends before starts at equal time

    live: dict[str, int] = {}
    highest = 0
    for _when, delta, host in events:
        live[host] = live.get(host, 0) + delta
        if live[host] <= 0:
            live.pop(host, None)
        highest = max(highest, len(live))
    return highest


@dataclass(frozen=True)
class RobotsArtifact:
    """One stored `robots.txt` fetch, classified the way `robots.for_response`
    classifies a live one."""

    domain: str
    url: str
    status: int | None
    has_body: bool
    error: str | None
    #: Bodies stored on **this row's own authority** (M1.75). Company-wide was
    #: the wrong denominator: it counted pages a sibling origin's robots.txt
    #: governs, which is the same key error M1.61 found in `impressum_audit`.
    bodies_for_origin: int

    @property
    def readable(self) -> bool:
        return self.status == 200 and self.has_body

    @property
    def unavailable(self) -> bool:
        """5xx, 429, or no status at all — the states under which M1.59 refuses
        to fetch. A stored row in this state means bodies alongside it were
        fetched under rules nobody read."""
        if self.readable:
            return False
        if self.status is None:
            return True
        return self.status == 429 or 500 <= self.status < 600

    @property
    def breach(self) -> bool:
        return self.unavailable and self.bodies_for_origin > 0


def robots_coverage(conn: sqlite3.Connection) -> list[RobotsArtifact]:
    """Every stored `robots.txt` artifact that is not a 200 with a body.

    Reads stored *state*, not a run: `artifact` carries no `run_id`, and failure
    rows update in place (§5.2), so "which run" is not a question this table can
    answer. What it can answer is the one that matters — is there a policy on
    disk that was never read, next to pages that were fetched anyway.
    """
    rows = conn.execute(
        """
        SELECT c.domain               AS domain,
               a.company_id           AS company_id,
               a.url                  AS url,
               a.http_status          AS status,
               a.content_hash IS NOT NULL AS has_body,
               a.error                AS error
        FROM artifact a JOIN company c ON c.id = a.company_id
        WHERE a.kind = 'robots'
          AND NOT (a.http_status = 200 AND a.content_hash IS NOT NULL)
        ORDER BY c.domain, a.id
        """
    ).fetchall()
    return [
        RobotsArtifact(
            domain=str(row["domain"]),
            url=str(row["url"]),
            status=row["status"],
            has_body=bool(row["has_body"]),
            error=row["error"],
            bodies_for_origin=len(
                _body_urls(conn, int(row["company_id"]), authority_of(str(row["url"])))
            ),
        )
        for row in rows
    ]


def _body_urls(conn: sqlite3.Connection, company_id: int, authority: str) -> list[str]:
    """Stored body URLs for one company on one authority.

    Filtered in Python on `authority_of` rather than by SQL on `url`, for the
    reason `impressum_audit` gives: it is the project's one expression for
    "whose robots.txt governs this", and a `LIKE` pattern beside it would be a
    second one (M1.42).
    """
    return [
        str(row["url"])
        for row in conn.execute(
            "SELECT url FROM artifact WHERE company_id = ? AND kind <> 'robots' "
            "AND content_hash IS NOT NULL",
            (company_id,),
        )
        if authority_of(str(row["url"])) == authority
    ]


@dataclass(frozen=True)
class UnverifiableOrigin:
    """An authority with stored bodies and **no robots.txt row of its own at all**.

    The class `robots_coverage` cannot see (M1.75). That function reports stored
    robots rows that failed; this one reports an origin with no row at all — and
    on the 17-August corpus that was the larger finding: 26 bodies across
    `www.smoke2u.de` and `www.propellerdiscount.de`, neither of which appears in
    the table, while `robots_coverage` returned nothing for either.

    **"No row" is not "never fetched."** Both of those hosts *were* fetched, 200,
    three times each; they served bytes identical to their apex sibling and
    `uq_artifact_identity` collapsed the pair into one row keyed on the apex. So
    what this reports is exactly what it says: the table cannot establish whose
    file governed these bodies. Only a re-fetch can.
    """

    domain: str
    authority: str
    bodies: int


def unverifiable_origins(conn: sqlite3.Connection) -> list[UnverifiableOrigin]:
    """Every authority carrying stored bodies with no robots artifact of its own.

    This is the audit-side statement of the same rule `impressum_audit._policy`
    applies per body: where the origin cannot be established the answer is *not
    verifiable*, never *allowed*. Reported separately from `robots_coverage`'s
    two classes because it sends a person somewhere else — not "this host failed
    us" but "we never filed this host's rules under this host".
    """
    # Any robots row at all covers its origin, whatever it says. A 200 was read;
    # a 404 is RFC 9309 §2.3.1.2's "no rules stated"; a 503 is unread and fails —
    # but it fails as `robots_coverage`'s *unavailable* class, which already
    # reports it. Requiring a 200 here would redden every 404 shop and double-
    # report every 5xx, and a check that cries wolf is one nobody reads. What is
    # left for this function is the case with no row of any kind.
    covered: dict[int, set[str]] = {}
    for row in conn.execute(
        "SELECT company_id, url FROM artifact WHERE kind = 'robots'"
    ):
        covered.setdefault(int(row["company_id"]), set()).add(
            authority_of(str(row["url"]))
        )

    counts: dict[tuple[str, str], int] = {}
    for row in conn.execute(
        "SELECT c.domain AS domain, a.company_id AS company_id, a.url AS url "
        "FROM artifact a JOIN company c ON c.id = a.company_id "
        "WHERE a.kind <> 'robots' AND a.content_hash IS NOT NULL"
    ):
        authority = authority_of(str(row["url"]))
        if authority in covered.get(int(row["company_id"]), set()):
            continue
        key = (str(row["domain"]), authority)
        counts[key] = counts.get(key, 0) + 1
    return [
        UnverifiableOrigin(domain=domain, authority=authority, bodies=bodies)
        for (domain, authority), bodies in sorted(counts.items())
    ]


def robots_report(conn: sqlite3.Connection) -> tuple[str, bool]:
    """The robots half of the audit, and whether it held.

    Two classes, reported separately and failing separately, because collapsing
    them would make this instrument cry wolf and a check nobody trusts is worse
    than no check:

    * **unavailable** — 5xx, 429, a transport failure. Fails. With bodies stored
      for the company it is a breach outright: pages were fetched under a policy
      that was never read. With none it still fails, because a policy the tool
      could not read is not a policy it may proceed under.
    * **no file** — 4xx, or a redirect we declined to follow. **Reported and not
      failed.** RFC 9309 §2.3.1.2 makes a 4xx "no rules stated", and the only
      shape of the second in this corpus is M1.18's moved domain, where the host
      actually fetched has its own robots.txt read before its first request.
    """
    findings = robots_coverage(conn)
    unavailable = [f for f in findings if f.unavailable]
    absent = [f for f in findings if not f.unavailable]
    unverifiable = unverifiable_origins(conn)

    lines = ["", f"robots.txt coverage: {len(findings)} stored artifacts are not 200"]
    if not findings:
        lines[-1] = (
            "robots.txt coverage: every stored robots artifact is a 200 with a body"
        )
    for finding in unavailable:
        lines.append(
            f"  *** UNREAD *** {finding.domain:28} HTTP {finding.status} "
            f"{finding.error or ''} — {finding.bodies_for_origin} bodies stored "
            f"on this origin{' (BREACH)' if finding.breach else ''}"
        )
    for finding in absent:
        lines.append(
            f"  no file       {finding.domain:28} HTTP {finding.status} "
            f"{finding.error or ''} — RFC 9309 §2.3.1.2, not a breach"
        )
    for origin in unverifiable:
        lines.append(
            f"  NOT VERIFIABLE {origin.authority:27} no robots.txt stored for this "
            f"origin — {origin.bodies} bodies under rules we cannot show were read"
        )
    ok = not unavailable and not unverifiable
    lines.append(
        f"§5.2 robots: {'HELD' if ok else 'BREACHED'} — "
        f"{len(unavailable)} unread, {len(unverifiable)} not verifiable, "
        f"{len(absent)} stating no file"
    )
    return "\n".join(lines), ok


def report(
    path: Path,
    intervals: dict[str, float] | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, bool]:
    """A human-readable audit and whether §5.2 held. Returns `(text, ok)`.

    `conn` adds the robots half (M1.62). It is optional so the spacing
    measurement stays usable against a bare log, and the CLI always passes one
    — an audit run without it says so in its output rather than looking green.
    """
    entries = load(path)
    if not entries:
        return f"{path}: no requests logged", False

    rows = spacing(entries, intervals)
    concurrent = max_hosts_in_flight(entries)

    lines = [
        f"{len(entries)} requests logged, {len(rows)} politeness keys",
        "",
        f"{'host':32} {'reqs':>5} {'min gap':>9} {'owed':>7}  verdict",
    ]
    for row in sorted(rows, key=lambda r: (r.ok, r.host)):
        gap = "n/a" if row.min_gap is None else f"{row.min_gap:.3f}s"
        lines.append(
            f"{row.host:32} {row.requests:5} {gap:>9} {row.interval:6.1f}s  "
            f"{'ok' if row.ok else '*** UNDER ***'}"
        )

    spacing_ok = all(row.ok for row in rows)
    hosts_ok = concurrent <= MAX_CONCURRENT_HOSTS
    lines += [
        "",
        (
            f"max hosts in flight: {concurrent} (ceiling {MAX_CONCURRENT_HOSTS}) — "
            f"{'ok' if hosts_ok else '*** OVER ***'}"
        ),
    ]

    robots_ok = True
    if conn is None:
        lines.append(
            "robots.txt coverage: NOT CHECKED — no database given, so this audit "
            "says nothing about whether a policy was read (M1.62)"
        )
    else:
        text, robots_ok = robots_report(conn)
        lines.append(text)

    lines.append(
        f"§5.2: {'HELD' if spacing_ok and hosts_ok and robots_ok else 'BREACHED'}"
    )
    return "\n".join(lines), spacing_ok and hosts_ok and robots_ok
