"""Per-domain signal diff between two runs (M1.28).

**Why this exists, stated as evidence rather than as principle.** Three defects
in two milestones passed the test suite and were caught by reading a run's
output line by line against the previous run's:

1. sitemap bodies round-tripped through `str`, which destroyed the gzip header
   and turned three real JTL catalogues into "0 URLs";
2. `<image:loc>` counted as a page, which made every Shopify product sitemap
   read as twice its length;
3. blog-shard URLs dropped from blog *path* detection, which flipped
   `snocks.com` to `content.blog_exists = 0` — `opp.no_blog`'s +25 against a
   shop that publishes weekly.

Two of the three were **hidden behind a plausible state**: the gzip defect
surfaced as a correct-looking "not measurable", and the halved counts looked
like counts. That is the case a diff catches and a test suite structurally
cannot — a test asserts what its author expected, and all three defects were
introduced by the author of the tests around them.

Reading line by line works at 13 domains and cannot work at 500, which is the
target. So it becomes a command.

Nothing here writes. It reads two runs and reports.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: What a key did between two runs.
APPEARED = "appeared"
DISAPPEARED = "disappeared"
CHANGED = "changed"


@dataclass(frozen=True)
class Change:
    domain: str
    key: str
    before: str | None
    after: str | None

    @property
    def kind(self) -> str:
        if self.before is None:
            return APPEARED
        if self.after is None:
            return DISAPPEARED
        return CHANGED


def _render(num: float | None, text: str | None, day: str | None) -> str:
    """One signal row as a comparable, readable string.

    Integral numbers lose their `.0`, so a count that did not move does not
    show up as a change because the storage type shifted.
    """
    parts: list[str] = []
    if num is not None:
        parts.append(str(int(num)) if float(num).is_integer() else str(num))
    if day:
        parts.append(day)
    if text:
        parts.append(f"({text})" if parts else text)
    return " ".join(parts) if parts else "∅"


def runs(conn: sqlite3.Connection, stage: str | None = None) -> list[sqlite3.Row]:
    """Runs that wrote at least one signal, newest first.

    A run that wrote none is not a comparison point — it is a run that did not
    happen, as far as this command is concerned, and silently diffing against
    one would report every signal as having disappeared.
    """
    query = """
        SELECT r.id, r.stage, r.started_at, COUNT(s.id) AS signals
        FROM run r JOIN signal s ON s.run_id = r.id
        {where}
        GROUP BY r.id ORDER BY r.id DESC
    """.format(where="WHERE r.stage = ?" if stage else "")
    return list(conn.execute(query, (stage,) if stage else ()))


def snapshot(conn: sqlite3.Connection, run_id: int) -> dict[tuple[str, str], str]:
    """`{(domain, key): rendered value}` for one run.

    Keyed on `company.domain` rather than on the id, because the diff is read
    by a person and the seeded domain is the human key (M1.18 keeps it stable
    across a move, which is the other reason to use it here).

    A key written more than once in a run — the same signal from two evidence
    URLs — is joined, so the multiplicity itself shows up as a change rather
    than one row silently winning.
    """
    rows = conn.execute(
        """
        SELECT c.domain, s.key, s.value_num, s.value_text, s.value_date
        FROM signal s JOIN company c ON c.id = s.company_id
        WHERE s.run_id = ? ORDER BY c.domain, s.key, s.id
        """,
        (run_id,),
    )
    collected: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        rendered = _render(row["value_num"], row["value_text"], row["value_date"])
        collected.setdefault((row["domain"], row["key"]), []).append(rendered)
    return {
        identity: values[0] if len(values) == 1 else "; ".join(sorted(set(values)))
        for identity, values in collected.items()
    }


def compare(before: dict[tuple[str, str], str], after: dict[tuple[str, str], str]):
    """Every key that changed, appeared or disappeared, ordered for reading."""
    changes = [
        Change(domain, key, before.get((domain, key)), after.get((domain, key)))
        for domain, key in sorted(set(before) | set(after))
        if before.get((domain, key)) != after.get((domain, key))
    ]
    return changes


_MARK = {APPEARED: "+", DISAPPEARED: "−", CHANGED: "~"}


def report(changes: list[Change], before_run: int, after_run: int) -> str:
    """Grouped by domain, because a defect shows as a *pattern* across domains.

    The gzip defect was three JTL shops losing the same key at once; the
    `<image:loc>` defect was seven Shopify shops halving the same one. Either
    reads as noise in a flat list and as a shape when grouped.
    """
    lines = [f"signal diff: run {before_run} → run {after_run}"]
    if not changes:
        lines.append("  no differences")
        return "\n".join(lines)

    current = ""
    for change in changes:
        if change.domain != current:
            current = change.domain
            lines.append(f"\n  {current}")
        mark = _MARK[change.kind]
        if change.kind == CHANGED:
            lines.append(f"    {mark} {change.key}: {change.before} → {change.after}")
        else:
            lines.append(f"    {mark} {change.key}: {change.before or change.after}")

    domains = len({change.domain for change in changes})
    keys = len({change.key for change in changes})
    counts = {kind: sum(1 for c in changes if c.kind == kind) for kind in _MARK}
    lines.append(
        f"\n  {len(changes)} change(s) across {domains} domain(s), {keys} key(s): "
        f"{counts[CHANGED]} changed, {counts[APPEARED]} appeared, "
        f"{counts[DISAPPEARED]} disappeared"
    )
    return "\n".join(lines)


__all__ = ["Change", "compare", "report", "runs", "snapshot"]
