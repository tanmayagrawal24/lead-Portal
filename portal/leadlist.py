"""The read model behind `portal serve` (§9).

**Every query here is read-only except `resolve_flag`.** The UI does not score,
does not fetch, and does not derive anything the pipeline has not already
written — a page that recomputes is a second scorer, and two scorers disagree.

Three things this module exists to get right, all of them consequences of what
the last three days established:

1. *Only finished runs are authoritative* (M1.39, migration 007). The
   `company_profile` view enforces that for signals. `score` has no view, so the
   same rule is applied here explicitly: a crashed scoring run that reached 10 of
   13 companies must not be the run the list is read from.
2. *An abstention is not a zero.* `score_component.points = 0` is exactly an
   abstention — `evaluate` writes no component at all for a rule that declines,
   and `assert_declared` refuses a rule worth nothing — so the distinction is
   recoverable from the database and the UI must render it (§5.4, A7).
3. *A component's evidence is the signals its rule declared it reads.* Not a
   hand-written mapping from rule to page. `Rule.reads` names profile columns;
   the profile view names the signal key behind each column; the signal row
   carries `evidence_url` and `artifact_id`. Every link in that chain is already
   in the system, so the UI derives it rather than restating it — which is
   M1.42's rule applied to the one place that would otherwise reintroduce it.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from portal.artifacts import utc_now
from portal.ruleset import RULES

#: Profile columns that come off the `company` row rather than out of a signal.
#: A rule reading one of these has no artifact behind it, and says so rather
#: than linking to nothing.
COMPANY_COLUMNS = ("domain", "country", "legal_form")

_PROFILE_COLUMN = re.compile(r"l\.key\s*=\s*'([^']+)'.*?\bAS\s+(\w+)", re.DOTALL)


def evidence_keys(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """Profile column → the signal keys that populate it, read off the view.

    Parsed from `company_profile`'s own definition in `sqlite_master` rather
    than maintained beside it. A hand-kept copy of this mapping is a second
    expression describing what the view does, and M1.40 and M1.42 are both what
    happens when a second expression drifts from the first — here it would
    silently point a reader at the wrong page's evidence, or at none.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='company_profile'"
    ).fetchone()
    if row is None:  # pragma: no cover — `portal init` creates the view
        raise RuntimeError("company_profile view is missing; run `portal init`")

    mapping: dict[str, list[str]] = {}
    for chunk in row["sql"].split("MAX(CASE WHEN")[1:]:
        if match := _PROFILE_COLUMN.search(chunk):
            key, column = match.group(1), match.group(2)
            mapping.setdefault(column, []).append(key)
    return {column: tuple(keys) for column, keys in mapping.items()}


def assert_evidence_reachable(conn: sqlite3.Connection) -> None:
    """Every `Rule.reads` entry resolves to a signal key or a company column.

    The same startup discipline as `ruleset.assert_declared`, for the same
    reason: a rule whose inputs the UI cannot trace renders a component with no
    evidence link, and a missing link looks exactly like a rule that legitimately
    reads nothing. Checked once, loudly, rather than degrading per row.
    """
    known = set(evidence_keys(conn)) | set(COMPANY_COLUMNS)
    unreachable = {
        f"{rule.id}.{column}"
        for rule in RULES
        for column in rule.reads
        if column not in known
    }
    if unreachable:
        raise RuntimeError(
            "these rule inputs resolve to no signal key and no company column, "
            "so their evidence cannot be shown: " + ", ".join(sorted(unreachable))
        )


# ── rows ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Evidence:
    """One signal behind one component."""

    key: str
    value: str
    evidence_url: str
    artifact_id: int | None
    method: str
    confidence: float | None

    @property
    def is_llm(self) -> bool:
        return self.method == "llm"

    @property
    def unverified(self) -> bool:
        """§9: `confidence = 0` is a *failed* substring verification (§5.5b) and
        is rendered in red. `None` is a deterministic signal with no confidence
        to report and must not be painted as a failure — every signal in the
        database is currently `None`, so conflating the two would make the whole
        page red on its first render."""
        return self.confidence is not None and self.confidence == 0


@dataclass(frozen=True)
class Component:
    rule_id: str
    points: int
    reason: str
    section: str
    evidence: tuple[Evidence, ...]

    @property
    def abstained(self) -> bool:
        """A component worth nothing is an abstention, never a rule that scored
        zero: `assert_declared` refuses a rule worth 0 points, and a rule that
        declines writes no component at all (§5.4)."""
        return self.points == 0


@dataclass(frozen=True)
class Flag:
    id: int
    reason: str
    raised_note: str
    raised_at: str
    blocks_contact: bool
    block_rationale: str


@dataclass(frozen=True)
class Lead:
    company_id: int
    domain: str
    city: str
    country: str
    platform: str
    band: str
    phase: int
    total: int
    ruleset_version: str
    computed_at: str
    admitted: bool | None
    remaining_upside: int | None
    blog_last_post: str
    blog_last_post_basis: str
    ai_mentions: float | None
    ai_queries: float | None
    excluded: bool
    excluded_reason: str
    needs_review: bool
    contact_blocked: bool
    open_flags: int
    components: tuple[Component, ...] = ()
    flags: tuple[Flag, ...] = ()
    #: Every `confidence = 0` signal for this company, **whether or not any rule
    #: reads its key** (M1.85, settled in 9b). See `rejected_values` below.
    rejected: tuple[Evidence, ...] = ()

    @property
    def scored(self) -> bool:
        return self.band != ""

    @property
    def abstentions(self) -> tuple[Component, ...]:
        return tuple(c for c in self.components if c.abstained)

    @property
    def rejected_unread(self) -> tuple[Evidence, ...]:
        """Rejected values that **no component renders** — M1.85's recorded gap.

        §9 renders a `confidence = 0` value in red *beneath its
        `score_component`*, and `evaluate` writes no component for a rule that
        declines (A7). So the red rendering was only ever reachable where the
        value's rule still produced one. For every §5.5b key a §6 rule reads it
        does, because M1.81's three-state predicates abstain on a rejected value
        and an abstention **is** a component worth 0 points.

        **For the keys no rule reads, it did not.** Nine §8/§9-only Impressum
        fields and `agency.footer_credit_llm` produce no component on any path,
        so a value the tool had checked and disbelieved stayed queryable in
        `signal` and was invisible on the page — including
        `impressum.legal_name`, which is what goes in the letter and is the
        single worst thing to be wrong about (§5.5b). That is what 9b settles:
        this is the second render path, and it does **not** hang off
        `score_component`.
        """
        rendered = {
            evidence.key
            for component in self.components
            for evidence in component.evidence
        }
        return tuple(item for item in self.rejected if item.key not in rendered)

    @property
    def hook(self) -> str:
        """§9's one-line offer: the largest opportunity actually awarded.

        Empty where nothing was awarded — including where the only §6.2 rules
        that ran abstained, which is the case the column must not paper over.
        """
        opportunities = [
            component
            for component in self.components
            if component.points > 0 and component.rule_id.startswith("opp.")
        ]
        if not opportunities:
            return ""
        return max(opportunities, key=lambda c: c.points).reason

    @property
    def ai_visibility(self) -> str:
        """`0/2`, or `—` where Phase 2 has not run. Never `0/0`."""
        if not self.ai_queries:
            return "—"
        return f"{int(self.ai_mentions or 0)}/{int(self.ai_queries)}"


#: The authoritative score per company: the latest **finished** scoring run,
#: highest phase first. Migration 007's rule, applied to a table that has no
#: view to enforce it — a run that died at company 10 of 13 is not the account
#: of the corpus this list is read from (M1.39).
_CURRENT_SCORE = """
WITH complete AS (
    SELECT s.*, ROW_NUMBER() OVER (
               PARTITION BY s.company_id ORDER BY s.phase DESC, s.run_id DESC
           ) AS rn
    FROM score s JOIN run r ON r.id = s.run_id
    WHERE r.finished_at IS NOT NULL AND r.aborted_reason IS NULL
)
SELECT * FROM complete WHERE rn = 1
"""


@dataclass(frozen=True)
class Filters:
    """§9's filter set. Every field is "no opinion" by default, so an unfiltered
    page shows the whole corpus including the rows a filter would hide."""

    band: str = ""
    platform: str = ""
    country: str = ""
    excluded: str = ""
    needs_review: str = ""
    contact_blocked: str = ""

    @property
    def active(self) -> bool:
        return any(
            (
                self.band,
                self.platform,
                self.country,
                self.excluded,
                self.needs_review,
                self.contact_blocked,
            )
        )


def _flag_of(row: sqlite3.Row) -> Flag:
    return Flag(
        id=int(row["id"]),
        reason=row["reason"],
        raised_note=row["raised_note"] or "",
        raised_at=row["raised_at"] or "",
        blocks_contact=bool(row["rationale"]),
        block_rationale=row["rationale"] or "",
    )


class LeadList:
    """Everything the page reads, on one connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.evidence_keys = evidence_keys(conn)
        self.rules = {rule.id: rule for rule in RULES}

    # ── the table ───────────────────────────────────────────────────────

    _SUMMARY = f"""
        WITH current AS ({_CURRENT_SCORE})
        SELECT c.id AS company_id, c.domain, c.city, c.country,
               c.excluded, c.excluded_reason, c.needs_review, c.contact_blocked,
               p.platform, p.blog_last_post, p.blog_last_post_basis,
               p.ai_brand_mentions, p.ai_queries_checked,
               s.id AS score_id, s.total, s.band, s.phase,
               s.ruleset_version, s.computed_at, s.run_id,
               (SELECT COUNT(*) FROM review_flag f
                 WHERE f.company_id = c.id AND f.resolved_at IS NULL) AS open_flags,
               (SELECT value_num FROM signal g
                 WHERE g.company_id = c.id AND g.run_id = s.run_id
                   AND g.key = 'gate.phase2_admitted') AS admitted,
               (SELECT value_num FROM signal g
                 WHERE g.company_id = c.id AND g.run_id = s.run_id
                   AND g.key = 'gate.remaining_upside') AS upside
        FROM company c
        LEFT JOIN company_profile p ON p.company_id = c.id
        LEFT JOIN current s ON s.company_id = c.id
    """

    def _summary(self, row: sqlite3.Row) -> Lead:
        return Lead(
            company_id=int(row["company_id"]),
            domain=row["domain"],
            city=row["city"] or "",
            country=row["country"] or "",
            platform=row["platform"] or "",
            band=row["band"] or "",
            phase=int(row["phase"] or 1),
            total=int(row["total"]) if row["total"] is not None else 0,
            ruleset_version=row["ruleset_version"] or "",
            computed_at=row["computed_at"] or "",
            admitted=None if row["admitted"] is None else bool(row["admitted"]),
            remaining_upside=None if row["upside"] is None else int(row["upside"]),
            blog_last_post=row["blog_last_post"] or "",
            blog_last_post_basis=row["blog_last_post_basis"] or "",
            ai_mentions=row["ai_brand_mentions"],
            ai_queries=row["ai_queries_checked"],
            excluded=bool(row["excluded"]),
            excluded_reason=row["excluded_reason"] or "",
            needs_review=bool(row["needs_review"]),
            contact_blocked=bool(row["contact_blocked"]),
            open_flags=int(row["open_flags"]),
        )

    def leads(self, filters: Filters | None = None) -> list[Lead]:
        """The ranked list, with each row's components already attached.

        Ranked by score descending, domain breaking ties. **Unscored companies
        are kept and shown as unscored, never dropped** — a company `score`
        could not reach is exactly the row an operator needs to see, and it
        sorts last rather than as a zero.

        Components are loaded for every row rather than on expansion because the
        hook column and the abstention count are computed from them, and 500
        companies is the design target (§1) — a join, not a per-row query.
        """
        filters = filters or Filters()
        rows = self.conn.execute(self._SUMMARY).fetchall()
        components = self._components_by_company()
        flags = self._flags_by_company()
        rejected = self._rejected_by_company()

        leads = []
        for row in rows:
            lead = self._summary(row)
            company_id = lead.company_id
            leads.append(
                Lead(
                    **{
                        **lead.__dict__,
                        "components": components.get(company_id, ()),
                        "flags": flags.get(company_id, ()),
                        "rejected": rejected.get(company_id, ()),
                    }
                )
            )

        leads = [lead for lead in leads if _matches(lead, filters)]
        # Unscored last: `scored` is False for them, and a missing score is not
        # a low one.
        leads.sort(key=lambda lead: (not lead.scored, -lead.total, lead.domain))
        return leads

    def facets(self) -> dict[str, list[str]]:
        """The values the filter selects offer, taken from the data rather than
        hardcoded — a platform the corpus does not contain is not a filter."""
        platforms = [
            row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT platform FROM company_profile "
                "WHERE platform IS NOT NULL AND platform <> '' ORDER BY platform"
            )
        ]
        countries = [
            row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT country FROM company "
                "WHERE country IS NOT NULL AND country <> '' ORDER BY country"
            )
        ]
        return {"platform": platforms, "country": countries}

    # ── components and their evidence ───────────────────────────────────

    def _components_by_company(self) -> dict[int, tuple[Component, ...]]:
        rows = self.conn.execute(f"""
            WITH current AS ({_CURRENT_SCORE})
            SELECT s.company_id, sc.rule_id, sc.points, sc.reason
            FROM current s JOIN score_component sc ON sc.score_id = s.id
            ORDER BY s.company_id, sc.id
        """).fetchall()
        evidence = self._evidence_by_company()

        out: dict[int, list[Component]] = {}
        for row in rows:
            rule = self.rules.get(row["rule_id"])
            reads = rule.reads if rule else ()
            available = evidence.get(int(row["company_id"]), {})
            out.setdefault(int(row["company_id"]), []).append(
                Component(
                    rule_id=row["rule_id"],
                    points=int(row["points"]),
                    reason=row["reason"],
                    section=rule.section if rule else "",
                    # The rule's declared inputs, resolved through the profile
                    # view to the signals that fed them. A rule input with no
                    # signal — `legal_form`, off the company row — contributes
                    # nothing here rather than a dead link.
                    evidence=tuple(
                        signal
                        for column in reads
                        for key in self.evidence_keys.get(column, ())
                        if (signal := available.get(key)) is not None
                    ),
                )
            )
        return {company: tuple(items) for company, items in out.items()}

    def _evidence_by_company(self) -> dict[int, dict[str, Evidence]]:
        """The signal behind each key, from the same authoritative run
        `company_profile` reads — so the evidence shown under a component is the
        evidence the rule was actually evaluated against, not a later or a
        partial observation (M1.39).

        **It differs from the view in exactly one way, deliberately: there is no
        `confidence` predicate here** (migration 012, M1.79). The view drops
        `confidence = 0` because scoring must not read a value the tool verified
        and rejected. §9 must still *show* it — that is what makes the rejection
        visible rather than silent, and A4's whole direction-of-error argument
        rests on the operator being able to see it. Adding the filter here would
        close the loop the filter was designed to leave open.

        **That this is a second expression for the view's resolution is a known
        cost, recorded rather than hidden.** The CTEs below are a hand-copy of
        `company_profile`'s, and a hand-copy drifts — this one already has, in
        the one place it is supposed to. Parsing the view's own SQL is what
        `_profile_columns` does above for a different question; doing it here
        would mean parsing a query in order to *remove* a clause from it, which
        is worse. Named so the next reader knows the difference is a decision.
        """
        rows = self.conn.execute("""
            WITH observed AS (
                SELECT s.*, r.stage, r.finished_at, r.aborted_reason
                FROM signal s JOIN run r ON r.id = s.run_id
            ),
            current_run AS (
                SELECT company_id, stage, MAX(run_id) AS run_id FROM observed
                WHERE finished_at IS NOT NULL AND aborted_reason IS NULL
                GROUP BY company_id, stage
            ),
            latest AS (
                SELECT o.*, ROW_NUMBER() OVER (
                           PARTITION BY o.company_id, o.key
                           ORDER BY o.observed_at DESC, o.id DESC) AS rn
                FROM observed o JOIN current_run c
                  ON c.company_id = o.company_id AND c.stage = o.stage
                 AND c.run_id = o.run_id
            )
            SELECT company_id, key, value_num, value_text, value_date,
                   method, confidence, evidence_url, artifact_id
            FROM latest WHERE rn = 1
        """).fetchall()

        out: dict[int, dict[str, Evidence]] = {}
        for row in rows:
            value = row["value_text"] or row["value_date"]
            if value is None and row["value_num"] is not None:
                number = float(row["value_num"])
                value = str(int(number)) if number.is_integer() else str(number)
            out.setdefault(int(row["company_id"]), {})[row["key"]] = Evidence(
                key=row["key"],
                value=str(value) if value is not None else "",
                evidence_url=row["evidence_url"] or "",
                artifact_id=row["artifact_id"],
                method=row["method"],
                confidence=row["confidence"],
            )
        return out

    def _rejected_by_company(self) -> dict[int, tuple[Evidence, ...]]:
        """Every `confidence = 0` signal, keyed by company (M1.85).

        **Deliberately not scoped to the authoritative run, and deliberately not
        keyed on whether a rule reads it.** Both of those filters are correct
        for `_evidence_by_company`, whose job is to show what a component was
        evaluated against; this one's job is the opposite — *what did the tool
        check and disbelieve?* — and a rejected value that a later run
        superseded is still a value that was rejected, still queryable, and
        still the thing an operator wants to know about before writing to a
        stranger.

        It reuses `Evidence` rather than a second shape, so the template renders
        both paths with one partial and `unverified` means one thing in the
        codebase.
        """
        out: dict[int, tuple[Evidence, ...]] = {}
        rows = self.conn.execute("""
            SELECT s.company_id, s.key, s.value_text, s.value_num, s.value_date,
                   s.method, s.confidence, s.evidence_url, s.artifact_id
            FROM signal s
            WHERE s.method = 'llm' AND s.confidence = 0
            ORDER BY s.company_id, s.key, s.id DESC
        """).fetchall()
        for row in rows:
            value = row["value_text"] or row["value_date"]
            if value is None and row["value_num"] is not None:
                number = float(row["value_num"])
                value = str(int(number)) if number.is_integer() else str(number)
            company_id = int(row["company_id"])
            items = out.get(company_id, ())
            if any(item.key == row["key"] for item in items):
                continue  # newest per key; ORDER BY id DESC put it first
            out[company_id] = items + (
                Evidence(
                    key=row["key"],
                    value=str(value) if value is not None else "",
                    evidence_url=row["evidence_url"] or "",
                    artifact_id=row["artifact_id"],
                    method=row["method"],
                    confidence=row["confidence"],
                ),
            )
        return out

    # ── the review queue ────────────────────────────────────────────────

    def _flags_by_company(self) -> dict[int, tuple[Flag, ...]]:
        """Open flags only, each with the rationale that makes it a block.

        The blocking set is read from `contact_blocking_reason` rather than
        listed here — migration 008 made which reasons block into data
        precisely so that adding the next one is an INSERT, and a UI that
        hardcodes the set would put that back.
        """
        rows = self.conn.execute(
            "SELECT f.id, f.company_id, f.reason, f.raised_note, f.raised_at, "
            "       b.rationale "
            "FROM review_flag f "
            "LEFT JOIN contact_blocking_reason b ON b.reason = f.reason "
            "WHERE f.resolved_at IS NULL ORDER BY f.company_id, f.id"
        ).fetchall()
        out: dict[int, list[Flag]] = {}
        for row in rows:
            out.setdefault(int(row["company_id"]), []).append(_flag_of(row))
        return {company: tuple(items) for company, items in out.items()}

    # ── one company ─────────────────────────────────────────────────────

    def lead(self, company_id: int) -> Lead | None:
        for lead in self.leads():
            if lead.company_id == company_id:
                return lead
        return None

    def resolve_flag(self, flag_id: int, note: str) -> int | None:
        """§9: clearing writes `resolved_at`, `resolved_by_human = 1` and an
        optional note, so *not yet reviewed* and *reviewed and dismissed* stay
        distinguishable. Returns the company, or `None` if the flag was already
        resolved — resolution is not idempotent-by-overwrite, because the second
        write would move a timestamp recording when a person actually looked.

        `needs_review` and `contact_blocked` are maintained by the triggers, not
        here; this writes the one row and lets the schema derive the rest.
        """
        row = self.conn.execute(
            "SELECT company_id FROM review_flag WHERE id = ? AND resolved_at IS NULL",
            (flag_id,),
        ).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE review_flag SET resolved_at = ?, resolved_by_human = 1, "
            "resolved_note = ? WHERE id = ?",
            (utc_now(), note or None, flag_id),
        )
        self.conn.commit()
        return int(row["company_id"])

    def artifact(self, artifact_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM artifact WHERE id = ?", (artifact_id,)
        ).fetchone()


def _matches(lead: Lead, filters: Filters) -> bool:
    """§9's filters. **Every one defaults to showing the row**, including
    `excluded` — hiding excluded companies by default would make a hard
    exclusion invisible, and §6.4 requires that we never exclude silently."""
    if filters.band and lead.band != filters.band:
        return False
    if filters.platform and lead.platform != filters.platform:
        return False
    if filters.country and lead.country != filters.country:
        return False
    for value, actual in (
        (filters.excluded, lead.excluded),
        (filters.needs_review, lead.needs_review),
        (filters.contact_blocked, lead.contact_blocked),
    ):
        if value == "yes" and not actual:
            return False
        if value == "no" and actual:
            return False
    return True


__all__ = [
    "COMPANY_COLUMNS",
    "Component",
    "Evidence",
    "Filters",
    "Flag",
    "Lead",
    "LeadList",
    "assert_evidence_reachable",
    "evidence_keys",
]
