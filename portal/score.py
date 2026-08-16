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


@dataclass
class ScoreResult:
    domain: str
    company_id: int
    total: int = 0
    band: str = "D"
    components: list[Component] = field(default_factory=list)
    remaining_upside: int = 0
    admitted: bool = False
    review_flags: list[str] = field(default_factory=list)

    @property
    def awarded(self) -> list[Component]:
        return [c for c in self.components if c.points]

    @property
    def abstentions(self) -> list[Component]:
        return [c for c in self.components if c.state == ABSTAINS]


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
    )
    settled: set[str] = set()

    for rule in rules:
        if rule.chain and rule.chain in settled:
            continue
        outcome = rule.evaluate(profile, today)
        if outcome.state == DECLINES:
            continue
        if rule.chain:
            settled.add(rule.chain)
        points = rule.points if outcome.state != ABSTAINS else 0
        result.total += points
        result.components.append(
            Component(rule.id, points, outcome.reason, outcome.state)
        )
        if outcome.review_reason:
            result.review_flags.append(outcome.review_reason)

    result.band = band_of(result.total)

    # §5.4's per-company gate. A rule already awarded in Phase 1 cannot be
    # awarded again, so it leaves the bound — which is the whole reason a
    # per-company gate is tighter than any safe global constant.
    banked = {c.rule_id for c in result.components if c.points > 0}
    result.remaining_upside = sum(
        rule.upside for rule in rules if rule.phase2_reachable and rule.id not in banked
    )
    result.admitted = result.total + result.remaining_upside >= BAND_FLOOR["B"]
    return result


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
        self._persist(result)
        return result

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
                (company_id, run_id, phase, total, band, ruleset_version, computed_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT (run_id, company_id, phase) DO UPDATE SET
                total = excluded.total,
                band = excluded.band,
                ruleset_version = excluded.ruleset_version,
                computed_at = excluded.computed_at
            """,
            (
                result.company_id,
                self.run_id,
                self.phase,
                result.total,
                result.band,
                RULESET_VERSION,
                utc_now(),
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

        for reason in result.review_flags:
            self.conn.execute(
                "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at, "
                "raised_note) VALUES (?,?,?,?,?) "
                "ON CONFLICT (company_id, reason) DO NOTHING",
                (
                    result.company_id,
                    reason,
                    self.run_id,
                    utc_now(),
                    self._note_for(reason, result),
                ),
            )

    @staticmethod
    def _note_for(reason: str, result: ScoreResult) -> str | None:
        """The one line the person needs at the instant they see the queue
        (migration 004). It is the abstention's own reason, which already says
        what was seen and why it could not carry the rule."""
        for component in result.abstentions:
            if component.reason:
                return component.reason
        return None


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
    try:
        results = [stage.run_company(row) for row in rows]
    finally:
        conn.execute(
            "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
            (utc_now(), len(rows), run_id),
        )
        conn.commit()
    return run_id, results


__all__ = ["Component", "ScoreResult", "ScoreStage", "band_of", "evaluate", "run"]
