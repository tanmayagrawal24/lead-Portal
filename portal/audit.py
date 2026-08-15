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
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path

from portal.net import MAX_CONCURRENT_HOSTS


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


def report(path: Path, intervals: dict[str, float] | None = None) -> tuple[str, bool]:
    """A human-readable audit and whether §5.2 held. Returns `(text, ok)`."""
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
        f"§5.2: {'HELD' if spacing_ok and hosts_ok else 'BREACHED'}",
    ]
    return "\n".join(lines), spacing_ok and hosts_ok
