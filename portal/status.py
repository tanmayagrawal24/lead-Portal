"""`/status` — what the database says about itself. **Read-only, and that is
the whole design constraint** (M1.120).

Three things this page must not become, each the UI form of a mistake this
project has already made once:

1. **A second source of truth.** Every number here is SELECTed. Nothing is
   derived that a stage has not already stored, and nothing is recomputed —
   `serve`'s own docstring says a page that scores is a second scorer, and a
   page that prices is a second ledger.
2. **A live dashboard.** No provider call, ever. `llm-batches` asks the account
   and costs nothing, and it is still not called from here: a page that makes a
   network call on render is a page that makes one when a browser reloads it,
   and §7 control 9's rule about credentials is easier to keep when the web
   process never needs one.
3. **A control panel.** The "next step" section is **sentences with commands in
   them, never buttons** (M1.102). A button that runs `extract-p2 --submit` is
   a paid call one accidental click away, and the whole `--dry-run`-by-default
   design exists so that spending is something a person types. The commands are
   rendered as `<code>` for exactly that reason, and the row records it as a
   decision rather than a styling choice.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from portal import ledger

#: §5.6 fact 4: a batch's results are retrievable for 29 days from creation.
RETRIEVAL_DAYS = 29


@dataclass(frozen=True)
class RunRow:
    id: int
    stage: str
    started_at: str
    finished_at: str | None
    aborted_reason: str | None
    companies_seen: int | None
    web_searches: int | None
    places_calls: int | None
    pagespeed_calls: int | None
    est_cost_usd: float

    @property
    def state(self) -> str:
        if self.aborted_reason:
            return "abgebrochen"
        return "abgeschlossen" if self.finished_at else "offen"


@dataclass(frozen=True)
class BatchRow:
    id: int
    provider_batch_id: str | None
    purpose: str
    status: str
    request_count: int
    est_cost_usd: float
    actual_cost_usd: float | None
    reserved_at: str
    submitted_at: str | None
    reconciled_at: str | None
    release_reason: str | None

    @property
    def needs_reconcile(self) -> bool:
        """Submitted, not yet reconciled. A `reserved` row has nothing to
        collect and a `released` one never will have."""
        return self.submitted_at is not None and self.reconciled_at is None

    @property
    def retrieval_deadline(self) -> str | None:
        """Creation + 29 days, or `None` if there is no submission clock to
        count from. Not a guess: an unparseable timestamp returns `None`
        rather than a date that looks measured (M1.52)."""
        if self.submitted_at is None:
            return None
        try:
            start = datetime.fromisoformat(self.submitted_at)
        except ValueError:
            return None
        return (start + timedelta(days=RETRIEVAL_DAYS)).date().isoformat()


@dataclass(frozen=True)
class Count:
    label: str
    n: int
    href: str = ""


@dataclass(frozen=True)
class NextStep:
    """One sentence and one command. The command is text (M1.102)."""

    sentence: str
    command: str


@dataclass
class Status:
    spend_usd: float = 0.0
    ceiling_usd: float = 0.0
    window_days: int = 0
    headroom_usd: float = 0.0
    run_ceiling_usd: float = 0.0
    runs: list[RunRow] = field(default_factory=list)
    batches: list[BatchRow] = field(default_factory=list)
    by_source: list[Count] = field(default_factory=list)
    by_query: list[Count] = field(default_factory=list)
    coverage: list[Count] = field(default_factory=list)
    flags: list[Count] = field(default_factory=list)
    next_steps: list[NextStep] = field(default_factory=list)
    companies: int = 0
    excluded: int = 0


def _f(value: object) -> float:
    return float(value or 0.0)


def read(conn: sqlite3.Connection) -> Status:
    """One read of everything the page shows. **No writes, no network.**"""
    status = Status(run_ceiling_usd=ledger.RUN_CEILING_USD)

    # ── §7 control 2, through the ledger's own function ──────────────────
    # Not a hand-written SUM here: control 2's window and ceiling are
    # `ledger`'s to define, and a page with its own copy of the arithmetic is
    # a second ledger that can disagree with the one that refuses spending
    # (M1.42). `check_ceiling` is free and read-only.
    clearance = ledger.check_ceiling(conn)
    status.spend_usd = clearance.spend_usd
    status.ceiling_usd = clearance.ceiling_usd
    status.window_days = clearance.window_days
    status.headroom_usd = clearance.headroom_usd

    status.runs = [
        RunRow(
            id=int(r["id"]),
            stage=str(r["stage"]),
            started_at=str(r["started_at"]),
            finished_at=r["finished_at"],
            aborted_reason=r["aborted_reason"],
            companies_seen=r["companies_seen"],
            web_searches=r["web_searches"],
            places_calls=r["places_calls"],
            pagespeed_calls=r["pagespeed_calls"],
            est_cost_usd=_f(r["est_cost_usd"]),
        )
        for r in conn.execute(
            "SELECT id, stage, started_at, finished_at, aborted_reason, "
            "companies_seen, web_searches, places_calls, pagespeed_calls, "
            "est_cost_usd FROM run ORDER BY id DESC"
        )
    ]

    status.batches = [
        BatchRow(
            id=int(r["id"]),
            provider_batch_id=r["provider_batch_id"],
            purpose=str(r["purpose"]),
            status=str(r["status"]),
            request_count=int(r["request_count"]),
            est_cost_usd=_f(r["est_cost_usd"]),
            actual_cost_usd=(
                None if r["actual_cost_usd"] is None else float(r["actual_cost_usd"])
            ),
            reserved_at=str(r["reserved_at"]),
            submitted_at=r["submitted_at"],
            reconciled_at=r["reconciled_at"],
            release_reason=r["release_reason"],
        )
        for r in conn.execute("SELECT * FROM llm_batch ORDER BY id DESC")
    ]

    # ── corpus ───────────────────────────────────────────────────────────
    status.companies = int(
        conn.execute("SELECT COUNT(*) FROM company").fetchone()[0] or 0
    )
    status.excluded = int(
        conn.execute("SELECT COUNT(*) FROM company WHERE excluded = 1").fetchone()[0]
        or 0
    )
    status.by_source = [
        Count(str(r[0]), int(r[1]))
        for r in conn.execute(
            "SELECT discovery_source, COUNT(*) FROM company "
            "GROUP BY 1 ORDER BY 2 DESC, 1"
        )
    ]
    status.by_query = [
        Count(str(r[0] or "—"), int(r[1]))
        for r in conn.execute(
            "SELECT discovery_query, COUNT(*) FROM company "
            "GROUP BY 1 ORDER BY 2 DESC, 1"
        )
    ]

    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0] or 0)

    status.coverage = [
        Count(
            "mit Homepage-Artefakt",
            one(
                "SELECT COUNT(DISTINCT company_id) FROM artifact "
                "WHERE kind = 'homepage' AND http_status = 200"
            ),
        ),
        Count(
            "mit Impressum-Artefakt",
            one(
                "SELECT COUNT(DISTINCT company_id) FROM artifact "
                "WHERE kind = 'impressum' AND http_status = 200"
            ),
        ),
        Count(
            "mit Phase-1-Score",
            one("SELECT COUNT(DISTINCT company_id) FROM score WHERE phase = 1"),
        ),
        Count(
            "mit Phase-2-Score",
            one("SELECT COUNT(DISTINCT company_id) FROM score WHERE phase = 2"),
        ),
        Count(
            "mit ai.checked_at",
            one(
                "SELECT COUNT(DISTINCT company_id) FROM signal "
                "WHERE key = 'ai.checked_at'"
            ),
        ),
    ]

    status.flags = [
        Count(str(r[0]), int(r[1]), href=f"/?needs_review=1#{r[0]}")
        for r in conn.execute(
            "SELECT reason, COUNT(*) FROM review_flag WHERE resolved_at IS NULL "
            "GROUP BY 1 ORDER BY 2 DESC, 1"
        )
    ]

    status.next_steps = _next_steps(conn, status)
    return status


def _next_steps(conn: sqlite3.Connection, status: Status) -> list[NextStep]:
    """What to run next, derived from the rows above and said in sentences.

    **Every entry is a sentence and a command, and none of them is a button**
    (M1.102). The order is the pipeline's, so a reader working top to bottom
    does the cheap and reversible things first: collecting a batch already paid
    for, then the dry runs that price the next spend, then the queue a human
    has to read. Nothing here is offered as a click.
    """
    steps: list[NextStep] = []

    pending = [b for b in status.batches if b.needs_reconcile]
    for batch in pending:
        deadline = batch.retrieval_deadline
        when = f" Die Ergebnisse sind bis {deadline} abrufbar." if deadline else ""
        steps.append(
            NextStep(
                sentence=(
                    f"Batch {batch.id} ({batch.provider_batch_id}) ist abgeschickt "
                    f"und noch nicht eingesammelt — {batch.request_count} Anfragen, "
                    f"${batch.est_cost_usd:.4f} reserviert. Das Geld ist bereits "
                    f"ausgegeben, ob die Ergebnisse gelesen werden oder nicht."
                    f"{when}"
                ),
                command="portal reconcile",
            )
        )

    # Admitted by §5.4 and with an Impressum on disk, but no Phase-2 score:
    # the set `extract-p2` would send. Counted, not listed — the command
    # prints the list itself, and a page that previews it is a second
    # implementation of the gate.
    awaiting_p2 = int(
        conn.execute(
            "SELECT COUNT(*) FROM company c "
            "WHERE c.excluded = 0 "
            "AND EXISTS (SELECT 1 FROM artifact a WHERE a.company_id = c.id "
            "            AND a.kind = 'impressum' AND a.http_status = 200) "
            "AND NOT EXISTS (SELECT 1 FROM score s WHERE s.company_id = c.id "
            "                AND s.phase = 2)"
        ).fetchone()[0]
        or 0
    )
    if awaiting_p2:
        steps.append(
            NextStep(
                sentence=(
                    f"{awaiting_p2} zugelassene Firmen haben ein Impressum auf "
                    f"der Platte, aber keinen Phase-2-Score. Der Trockenlauf "
                    f"zeigt, wer gesendet würde und wer nicht — er kostet nichts "
                    f"und reserviert nichts."
                ),
                command="portal extract-p2 --dry-run",
            )
        )

    awaiting_ai = int(
        conn.execute(
            "SELECT COUNT(*) FROM company c "
            "WHERE c.excluded = 0 "
            "AND EXISTS (SELECT 1 FROM signal g WHERE g.company_id = c.id "
            "            AND g.key = 'catalog.product_url_count') "
            "AND NOT EXISTS (SELECT 1 FROM signal s WHERE s.company_id = c.id "
            "                AND s.key = 'ai.checked_at')"
        ).fetchone()[0]
        or 0
    )
    if awaiting_ai:
        steps.append(
            NextStep(
                sentence=(
                    f"{awaiting_ai} Firmen haben einen messbaren Katalog, aber "
                    f"noch keine KI-Sichtbarkeitsprüfung. Auch hier zeigt der "
                    f"Trockenlauf zuerst, was er kosten würde."
                ),
                command="portal ai-check --dry-run",
            )
        )

    open_flags = sum(flag.n for flag in status.flags)
    if open_flags:
        steps.append(
            NextStep(
                sentence=(
                    f"{open_flags} offene Prüfmarkierungen warten auf eine "
                    f"Entscheidung. Sie sind der Grund, aus dem §5.4 sagen darf, "
                    f"dass nichts Verwertbares verworfen wird, ohne dass ein "
                    f"Mensch es erfährt — die Aussage hält nur, solange die "
                    f"Liste gelesen wird. Sie lassen sich direkt in der "
                    f"Leadliste auflösen."
                ),
                command="portal serve",
            )
        )

    if not steps:
        steps.append(
            NextStep(
                sentence=(
                    "Nichts steht an: kein offener Batch, keine unbewertete "
                    "Firma, keine offene Markierung."
                ),
                command="portal score",
            )
        )
    return steps


def utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


__all__ = [
    "RETRIEVAL_DAYS",
    "BatchRow",
    "Count",
    "NextStep",
    "RunRow",
    "Status",
    "read",
    "utc_today",
]
