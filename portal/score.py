"""The `score --phase 1` stage (§5.4, §6).

**A pure function of `company_profile`, and nothing else.** No network, no
filesystem, no artifact bodies — the only inputs are one row of the read model
and the date. That is what makes §4's promise good: re-scoring is free, costs no
API call, and cannot differ between two runs over the same signals. `evaluate`
below takes a mapping and returns a result; everything that touches the database
is in `ScoreStage`, on the other side of that line.

**Three things a score records that a total cannot.**

1. *Every component, with its reason in German.* The reason is the deliverable —
   it is what a person pastes into a letter — so it interpolates the evidence
   and reads as a sentence.
2. *Every abstention, as a component worth 0 points.* A7 (§5) requires that a
   rule which fires in neither direction says so per company rather than leaving
   a gap. Writing it into the score itself puts it where the person reading the
   company actually looks, and it is what stops "this rule did not fire" and
   "this rule could not be evaluated" from looking identical in a UI.
3. *The gate and the number behind it* — `gate.phase2_admitted` and
   `gate.remaining_upside` (§5.4). With a per-company gate, "just under the
   line" means something different for each company, so the threshold it was
   judged against has to be stored rather than reconstructed.

**And one thing a score can withhold.** An abstention that leaves the score too
*high* blocks outbound contact until a human resolves it (A7's third axis,
migration 008). The block lives in the schema, on `outreach`; what this stage
does is raise the flag that causes it and report the state back, so the lead
list says so where a person would otherwise pick up the phone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from portal.artifacts import utc_now
from portal.ruleset import (
    ABSTAINS,
    BAND_FLOOR,
    DECLINES,
    RULES,
    RULESET_VERSION,
    Profile,
    Rule,
    assert_declared,
)


@dataclass(frozen=True)
class Component:
    rule_id: str
    points: int
    reason: str
    state: str


@dataclass(frozen=True)
class ReviewFlag:
    """One §6.4 routing, carrying its own note.

    The note travels with the flag rather than being looked up afterwards. A
    company can abstain on three rules at once, and picking "the first
    abstention's reason" for all three of them sends a person to the wrong page
    — which is the failure `blog_undetectable` was made a distinct reason to
    avoid in the first place (§6.4).
    """

    reason: str
    note: str


@dataclass(frozen=True)
class PendingTransient:
    """An A7b abstention that has not yet earned a flag.

    The rule names where it *would* route; whether it routes yet depends on how
    many consecutive runs it has abstained on, which is history and therefore
    not a question `evaluate` may ask (§5.4: scoring is a pure function of one
    profile row).
    """

    rule_id: str
    reason: str
    note: str


@dataclass
class ScoreResult:
    domain: str
    company_id: int
    #: The date `evaluate` was actually run against (M1.74, closing M1.66).
    #: Set by `evaluate` from its own `today` parameter and by nothing else, so
    #: the stored evaluation date and the date the rules saw are **one
    #: expression**. Writing `self.today` in `_persist` would have been a second
    #: expression for one fact, which is the defect this closes (M1.42's shape).
    #: A band is a function of `(signals, today)`; without this the band is not
    #: reproducible, which is why it precedes any spend against a band.
    evaluated_on: date | None = None
    total: int = 0
    band: str = "D"
    components: list[Component] = field(default_factory=list)
    remaining_upside: int = 0
    admitted: bool = False
    review_flags: list[ReviewFlag] = field(default_factory=list)
    pending_transients: list[PendingTransient] = field(default_factory=list)
    #: §8/A7: an unresolved too-high abstention refuses outbound contact for
    #: this company. Read back from `company.contact_blocked` after the flags
    #: are written, so what is reported is what the database will enforce.
    contact_blocked: bool = False

    @property
    def awarded(self) -> list[Component]:
        return [c for c in self.components if c.points]

    @property
    def abstentions(self) -> list[Component]:
        return [c for c in self.components if c.state == ABSTAINS]


def _evaluated_on_or_fail(result: ScoreResult) -> str:
    """The evaluation date as stored, refusing to write a row without one.

    `evaluated_on` is nullable in the schema **only** so that rows written
    before migration 010 can say "never recorded" (§4). A row written *now*
    with no date would be a new instance of exactly the defect M1.74 closed, so
    it fails loudly here rather than inserting a NULL that reads like history.
    """
    if result.evaluated_on is None:
        raise ValueError(
            f"{result.domain or result.company_id}: refusing to persist a score "
            f"with no evaluation date (M1.74). A band is a function of "
            f"(signals, today); a row without its date cannot be reproduced. "
            f"`evaluate` sets this — construct the result through it."
        )
    return result.evaluated_on.isoformat()


def band_of(total: int) -> str:
    """§6.5. **Not to be tuned on the current corpus** (§10.3) — a third of it is
    systematically ~25 points light on a finding about URL structure, and B7
    changed scores after the crawl was taken. Compute, report, do not calibrate.
    """
    for name, floor in BAND_FLOOR.items():
        if total >= floor:
            return name
    return "D"


def evaluate(
    profile: Profile, today: date, rules: tuple[Rule, ...] = RULES
) -> ScoreResult:
    """Run the ruleset over one profile row. **Pure.**

    Chained rules (§6.2's ladder) are evaluated in declaration order and the
    first that does not decline stops the chain — an abstention stops it too,
    which is M1.34: if `blog_exists` is unknown then `blog_last_post` is not a
    meaningful question, and the rungs below must not be reached.
    """
    result = ScoreResult(
        domain=str(profile.get("domain") or ""),
        company_id=int(profile["company_id"]),  # type: ignore[arg-type]
        # The same object the rules below are handed. Not a copy, not a re-read.
        evaluated_on=today,
    )
    # Renamed from `settled` (M1.82). This tracks which §6.2 ladder chains have
    # stopped; `phase2_input_settled` below asks a completely different
    # question, and two meanings under one name in one function is how the next
    # reader gets it wrong.
    chains_settled: set[str] = set()

    for rule in rules:
        if rule.chain and rule.chain in chains_settled:
            continue
        outcome = rule.evaluate(profile, today)
        if outcome.state == DECLINES:
            continue
        if rule.chain:
            chains_settled.add(rule.chain)
        points = rule.points if outcome.state != ABSTAINS else 0
        result.total += points
        result.components.append(
            Component(rule.id, points, outcome.reason, outcome.state)
        )
        if outcome.review_reason:
            result.review_flags.append(
                ReviewFlag(outcome.review_reason, outcome.reason)
            )
        if outcome.persistent_review_reason:
            result.pending_transients.append(
                PendingTransient(
                    rule.id, outcome.persistent_review_reason, outcome.reason
                )
            )

    result.band = band_of(result.total)

    # §5.4's per-company gate. A rule leaves the bound two ways, and until
    # M1.82 only the first was counted.
    #
    #   BANKED   — Phase 1 already awarded it, so Phase 2 cannot award it again.
    #              This is what makes a per-company gate tighter than any safe
    #              global constant.
    #
    #   SETTLED  — Phase 2 has already ANSWERED it for this company, and the
    #              answer was no. Reproduced before it was fixed: a company
    #              whose extraction had run and returned `false` for both
    #              booleans still carried upside=50 and was still admitted, so
    #              the gate offered 25 points for two closed questions and
    #              re-admitted the company to paid extraction on them. **A gate
    #              that only loosens is a gate that pays twice for one answer.**
    #
    # Settledness is DECLARED, never derived from `result.components`: a rule
    # that declines writes no component at all, so a Phase-2 `false` — the
    # commonest case — is invisible to any outcome-based reading and
    # indistinguishable from "never evaluated".
    banked = {c.rule_id for c in result.components if c.points > 0}
    result.remaining_upside = sum(
        rule.upside
        for rule in rules
        if rule.phase2_reachable
        and rule.id not in banked
        and not (rule.phase2_input_settled and rule.phase2_input_settled(profile))
    )
    result.admitted = result.total + result.remaining_upside >= BAND_FLOOR["B"]
    return result


#: A7b. Three consecutive runs, on three distinct days — the reasoning is in §5
#: and the "distinct days" half is load-bearing: a crash-restart loop inside one
#: afternoon manufactures three runs, and the flag would then be about our own
#: crash rather than about the shop.
PERSISTENT_RUNS = 3


class ScoreStage:
    """Runs `score --phase 1`. Single-threaded; there is no I/O to overlap."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        phase: int = 1,
        today: date | None = None,
    ) -> None:
        assert_declared()
        self.conn = conn
        self.run_id = run_id
        self.phase = phase
        self.today = today or datetime.now(UTC).date()

    def profiles(self) -> list[sqlite3.Row]:
        """Every company that is still a lead. §6.4's hard exclusions are not
        scored: a `duplicate_site` row is the same lead under another id, and
        giving it a band would put one company in the list twice."""
        return self.conn.execute(
            "SELECT * FROM company_profile p JOIN company c ON c.id = p.company_id "
            "WHERE c.excluded = 0 ORDER BY p.company_id"
        ).fetchall()

    def run_company(self, profile: sqlite3.Row) -> ScoreResult:
        result = evaluate(dict(profile), self.today)
        self._route_persistent(result)
        self._persist(result)
        result.contact_blocked = bool(
            self.conn.execute(
                "SELECT contact_blocked FROM company WHERE id = ?",
                (result.company_id,),
            ).fetchone()[0]
        )
        return result

    def _route_persistent(self, result: ScoreResult) -> None:
        """A7b: turn a transient that has stopped being transient into a flag."""
        for pending in result.pending_transients:
            days = self._abstained_days(result.company_id, pending.rule_id)
            if len(days) < PERSISTENT_RUNS:
                continue
            result.review_flags.append(
                ReviewFlag(
                    pending.reason,
                    f"{pending.note} Der Abruf schlägt seit {min(days)} an "
                    f"{len(days)} verschiedenen Tagen in Folge fehl – bitte "
                    "die Seite einmal von Hand aufrufen.",
                )
            )

    def _abstained_days(self, company_id: int, rule_id: str) -> list[str]:
        """The distinct days this rule has abstained on, walking back over
        *consecutive* scoring runs and stopping at the first that did not.

        A component worth 0 points is exactly an abstention: `evaluate` records
        no component at all for a rule that declines, and `assert_declared`
        refuses a rule worth nothing. The day comes from `run.started_at` rather
        than from the number of runs, because `score` is a free recompute and
        may be run five times in an afternoon.

        Every rule that carries a persistent routing has exactly one abstention
        cause today, so "the rule abstained again" and "the same fetch missed
        again" are the same fact. The one rule with two causes, `opp.no_blog`,
        routes its other cause (`blog_undetectable`) immediately anyway.
        """
        days = [self.today.isoformat()]
        rows = self.conn.execute(
            """
            SELECT date(r.started_at) AS day,
                   EXISTS (SELECT 1 FROM score_component sc
                           WHERE sc.score_id = s.id AND sc.rule_id = ?
                             AND sc.points = 0) AS abstained
            FROM score s JOIN run r ON r.id = s.run_id
            WHERE s.company_id = ? AND s.phase = ? AND s.run_id <> ?
            ORDER BY s.run_id DESC
            """,
            (rule_id, company_id, self.phase, self.run_id),
        )
        for row in rows:
            if not row["abstained"]:
                break
            if row["day"] not in days:
                days.append(row["day"])
            if len(days) >= PERSISTENT_RUNS:
                break
        return days

    def _persist(self, result: ScoreResult) -> None:
        """Idempotent within a run (§5, `uq_score_identity`).

        Re-running is a *recompute*, so the row is updated rather than skipped
        and its components are rewritten wholesale. Skipping on conflict would
        leave a stale total next to fresh signals, which is the one thing a
        zero-cost recompute exists to avoid.
        """
        self.conn.execute(
            """
            INSERT INTO score
                (company_id, run_id, phase, total, band, ruleset_version,
                 computed_at, evaluated_on)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT (run_id, company_id, phase) DO UPDATE SET
                total = excluded.total,
                band = excluded.band,
                ruleset_version = excluded.ruleset_version,
                computed_at = excluded.computed_at,
                evaluated_on = excluded.evaluated_on
            """,
            (
                result.company_id,
                self.run_id,
                self.phase,
                result.total,
                result.band,
                RULESET_VERSION,
                # Two different facts, deliberately in two columns: when the row
                # was written, and the day the rules were run against.
                utc_now(),
                _evaluated_on_or_fail(result),
            ),
        )
        score_id = self.conn.execute(
            "SELECT id FROM score WHERE run_id = ? AND company_id = ? AND phase = ?",
            (self.run_id, result.company_id, self.phase),
        ).fetchone()[0]

        self.conn.execute("DELETE FROM score_component WHERE score_id = ?", (score_id,))
        self.conn.executemany(
            "INSERT INTO score_component (score_id, rule_id, points, reason) "
            "VALUES (?,?,?,?)",
            [(score_id, c.rule_id, c.points, c.reason) for c in result.components],
        )

        for key, value in (
            ("gate.phase2_admitted", 1 if result.admitted else 0),
            ("gate.remaining_upside", result.remaining_upside),
        ):
            self.conn.execute(
                """
                INSERT INTO signal
                    (company_id, run_id, key, value_num, method, evidence_url, observed_at)
                VALUES (?,?,?,?,'deterministic','',?)
                ON CONFLICT (run_id, company_id, key, evidence_url) DO UPDATE SET
                    value_num = excluded.value_num, observed_at = excluded.observed_at
                """,
                (result.company_id, self.run_id, key, value, utc_now()),
            )

        # The note is the abstention's own reason — what was seen, and why it
        # could not carry the rule (migration 004) — and it is the reason of the
        # abstention that *raised this flag*, not of whichever abstained first.
        for flag in result.review_flags:
            self.conn.execute(
                "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at, "
                "raised_note) VALUES (?,?,?,?,?) "
                "ON CONFLICT (company_id, reason) DO NOTHING",
                (result.company_id, flag.reason, self.run_id, utc_now(), flag.note),
            )


def run(
    conn: sqlite3.Connection,
    phase: int = 1,
    today: date | None = None,
    run_id: int | None = None,
) -> tuple[int, list[ScoreResult]]:
    """Score every non-excluded company. Returns `(run_id, results)`."""
    if run_id is None:
        cursor = conn.execute(
            "INSERT INTO run (started_at, stage) VALUES (?, ?)",
            (utc_now(), f"score-p{phase}"),
        )
        run_id = int(cursor.lastrowid)

    stage = ScoreStage(conn, run_id, phase, today)
    rows = stage.profiles()
    # `finished_at` marks a run that reached the end, and nothing else (M1.39):
    # `company_profile` reads it to decide which run's account of a company to
    # trust, so a crashed run that claimed to be finished would let a partial
    # pass retract signals it never got to write (migration 007).
    try:
        results = [stage.run_company(row) for row in rows]
    except BaseException as exc:
        conn.execute(
            "UPDATE run SET aborted_reason = ? WHERE id = ?",
            (f"{type(exc).__name__}: {exc}"[:500], run_id),
        )
        conn.commit()
        raise
    conn.execute(
        "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
        (utc_now(), len(results), run_id),
    )
    conn.commit()
    return run_id, results


__all__ = [
    "Component",
    "PendingTransient",
    "ReviewFlag",
    "ScoreResult",
    "ScoreStage",
    "band_of",
    "evaluate",
    "run",
]
