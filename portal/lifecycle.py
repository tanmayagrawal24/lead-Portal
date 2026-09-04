"""§8's lifecycle: `purge`, `forget`, and the two §9 writes that are not flags (M7).

**Every function here is a deletion or a write a human decided on**, and none
of them is reachable from a pipeline stage. That is the division §8 draws:
the stages *collect*, and what they collected about a named person is deleted
by a command that must actually be run.

Four things, and the reasoning for each is the one §8 already gives:

- **`purge`** deletes `contact` rows past `purge_after`. Twelve months from
  `collected_at`, set by `reconcile`, and *"must actually be run"* — so it
  prints what it deleted and returns the count.
- **`forget --domain X`** hard-deletes a company from every table. The schema
  carries `ON DELETE CASCADE` on every table with a `company_id` (§4, and
  migration 015 denormalised one out of a text key for exactly this), so it is
  one `DELETE` — but the artifact **bodies are files**, and a file does not
  cascade. Both halves are done and both are verified afterwards: the count of
  rows that still name the company across every table, and whether the
  domain's directory still exists. Two things are deliberately kept: `run`
  rows, because they are the §7 ledger and a deletion must never release
  spend (control 12c); and `llm_batch`, which names no company.
- **`exclude`** writes `excluded = 1` **with its reason** — §4's column
  comment: *never exclude silently*. Reversible with `--lift`, which is the
  operator's act M1.103(10) says a stage must not perform.
- **`log_outreach`** inserts the row migration 008's trigger guards, and turns
  the trigger's refusal into a named exception with the flags that caused it —
  because the trigger's message is the schema's and the reason is a person's.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from portal.artifacts import _UNSAFE, utc_now

CHANNELS: tuple[str, ...] = ("post", "phone")
OUTCOMES: tuple[str, ...] = (
    "no_response",
    "interested",
    "declined",
    "meeting",
    "client",
)


class UnknownCompany(LookupError):
    """No `company` row with that domain."""


class OutreachBlocked(RuntimeError):
    """Migration 008's trigger refused the insert; `flags` names why."""

    def __init__(self, domain: str, flags: tuple[str, ...]) -> None:
        self.flags = flags
        super().__init__(
            f"outreach to {domain} is blocked: an unresolved review flag leaves its "
            f"score too high (A7, §6.4) — {', '.join(flags)}. Resolve it in `portal "
            f"serve` first; the block is the schema's, not this command's."
        )


@dataclass(frozen=True)
class ForgetReport:
    domain: str
    rows_deleted: dict[str, int]
    bodies_removed: bool
    residue: dict[str, int]

    @property
    def clean(self) -> bool:
        return not self.residue


def company_id_for(conn: sqlite3.Connection, domain: str) -> int:
    row = conn.execute("SELECT id FROM company WHERE domain = ?", (domain,)).fetchone()
    if row is None:
        raise UnknownCompany(f"no company with domain {domain!r}")
    return int(row["id"])


# ── purge ────────────────────────────────────────────────────────────────


def expired_contacts(
    conn: sqlite3.Connection, *, now: str | None = None
) -> list[sqlite3.Row]:
    """Contacts whose `purge_after` has passed. Read separately so `--dry-run`
    can show them, and so the delete below can be matched against this list."""
    return conn.execute(
        "SELECT c.id, c.full_name, c.role, c.collected_at, c.purge_after, co.domain "
        "FROM contact c JOIN company co ON co.id = c.company_id "
        "WHERE c.purge_after < ? ORDER BY c.purge_after, c.id",
        (now or utc_now(),),
    ).fetchall()


def purge(conn: sqlite3.Connection, *, now: str | None = None) -> int:
    """Delete every expired contact. Returns how many. One statement, so a
    crash deletes all of them or none."""
    when = now or utc_now()
    cursor = conn.execute("DELETE FROM contact WHERE purge_after < ?", (when,))
    conn.commit()
    return int(cursor.rowcount)


# ── forget ───────────────────────────────────────────────────────────────


def _company_tables(conn: sqlite3.Connection) -> list[str]:
    """Every table with a `company_id` column, read from the schema rather
    than listed — a table added later must not be missed by a deletion that
    claims to reach everywhere."""
    tables = [
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return sorted(
        table
        for table in tables
        if any(
            col["name"] == "company_id"
            for col in conn.execute(f"PRAGMA table_info({table})")
        )
    )


def residue(conn: sqlite3.Connection, company_id: int) -> dict[str, int]:
    """Rows anywhere that still name the company. Empty is the only acceptable
    answer after `forget`, and it is measured rather than assumed."""
    found: dict[str, int] = {}
    for table in _company_tables(conn):
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE company_id = ?", (company_id,)
            ).fetchone()[0]
        )
        if count:
            found[table] = count
    company = int(
        conn.execute(
            "SELECT COUNT(*) FROM company WHERE id = ?", (company_id,)
        ).fetchone()[0]
    )
    if company:
        found["company"] = company
    # `score_component` hangs off `score`, not off `company`, so it is checked
    # through its parent: a component whose score row is gone is unreachable
    # and a component whose score row remains is residue.
    orphans = int(
        conn.execute(
            "SELECT COUNT(*) FROM score_component sc JOIN score s ON s.id = sc.score_id "
            "WHERE s.company_id = ?",
            (company_id,),
        ).fetchone()[0]
    )
    if orphans:
        found["score_component"] = orphans
    return found


def forget(conn: sqlite3.Connection, artifacts_root: Path, domain: str) -> ForgetReport:
    """§8's erasure: every row and every stored body for one company."""
    company_id = company_id_for(conn, domain)
    before = {table: 0 for table in _company_tables(conn)}
    for table in before:
        before[table] = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE company_id = ?", (company_id,)
            ).fetchone()[0]
        )
    before["score_component"] = int(
        conn.execute(
            "SELECT COUNT(*) FROM score_component sc JOIN score s ON s.id = sc.score_id "
            "WHERE s.company_id = ?",
            (company_id,),
        ).fetchone()[0]
    )
    # `foreign_keys = ON` is a property of every connection `db.connect`
    # opens, and CASCADE does nothing without it; asserted rather than trusted.
    if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise RuntimeError(
            "foreign_keys is OFF on this connection; CASCADE would not run"
        )
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM company WHERE id = ?", (company_id,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    directory = artifacts_root / (_UNSAFE.sub("-", domain.lower()) or "unknown")
    bodies_removed = False
    if directory.is_dir():
        shutil.rmtree(directory)
        bodies_removed = True

    return ForgetReport(
        domain=domain,
        rows_deleted={table: n for table, n in before.items() if n} | {"company": 1},
        bodies_removed=bodies_removed,
        residue=residue(conn, company_id)
        | ({"bodies": 1} if directory.exists() else {}),
    )


# ── exclude ──────────────────────────────────────────────────────────────


def exclude(conn: sqlite3.Connection, domain: str, reason: str) -> int:
    reason = " ".join(reason.split())
    if not reason:
        raise ValueError("an exclusion needs a reason (§4: never exclude silently)")
    company_id = company_id_for(conn, domain)
    conn.execute(
        "UPDATE company SET excluded = 1, excluded_reason = ? WHERE id = ?",
        (reason, company_id),
    )
    conn.commit()
    return company_id


def lift_exclusion(conn: sqlite3.Connection, domain: str) -> int:
    company_id = company_id_for(conn, domain)
    conn.execute(
        "UPDATE company SET excluded = 0, excluded_reason = NULL WHERE id = ?",
        (company_id,),
    )
    conn.commit()
    return company_id


# ── outreach ─────────────────────────────────────────────────────────────


def log_outreach(
    conn: sqlite3.Connection,
    domain: str,
    *,
    channel: str,
    occurred_at: str | None = None,
    notes: str = "",
    outcome: str | None = None,
) -> int:
    """Insert one `outreach` row, or raise `OutreachBlocked` with the flags.

    The channel CHECK is the schema's (§8: `post` and `phone` only, no email);
    it is repeated here only so the error names the rule rather than the
    constraint. `occurred_at` defaults to now and is normalised to the same
    UTC format every other timestamp in the database uses.
    """
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS} (§8: no email, ever)")
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    company_id = company_id_for(conn, domain)
    when = occurred_at or utc_now()
    try:
        datetime.fromisoformat(when).astimezone(UTC)
    except ValueError as exc:
        raise ValueError(f"occurred_at {when!r} is not an ISO 8601 timestamp") from exc
    try:
        cursor = conn.execute(
            "INSERT INTO outreach (company_id, channel, occurred_at, notes, outcome) "
            "VALUES (?,?,?,?,?)",
            (company_id, channel, when, notes.strip() or None, outcome),
        )
    except sqlite3.IntegrityError as exc:
        if "outreach blocked" not in str(exc):
            raise
        flags = tuple(
            str(row["reason"])
            for row in conn.execute(
                "SELECT f.reason FROM review_flag f JOIN contact_blocking_reason b "
                "ON b.reason = f.reason WHERE f.company_id = ? AND f.resolved_at IS NULL "
                "ORDER BY f.reason",
                (company_id,),
            )
        )
        raise OutreachBlocked(domain, flags) from exc
    conn.commit()
    return int(cursor.lastrowid or 0)


__all__ = [
    "CHANNELS",
    "OUTCOMES",
    "ForgetReport",
    "OutreachBlocked",
    "UnknownCompany",
    "company_id_for",
    "exclude",
    "expired_contacts",
    "forget",
    "lift_exclusion",
    "log_outreach",
    "purge",
    "residue",
]
