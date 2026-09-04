"""§9's research brief export — per company, German, Markdown (M7).

**The export fails rather than degrades.** §8: every AI-visibility statement
must carry its basis inline — the literal queries, the date, the model, and
that web search was on — and *"an export missing any of them must fail, not
degrade gracefully"*. So `render` raises `MissingBasis` if a company has an
`ai.*` result without all three basis fields, and it omits the KI-Sichtbarkeit
section entirely for a company that never reached the check. Those are the two
states; there is no third in which a section renders with a blank where the
date should be.

**Wording is constrained to what was measured** (§8). The result line is the
count — *"Bei 2 von 2 geprüften KI-Abfragen wurden Sie nicht genannt"* — and
never a general claim. The competitor line is comparative advertising under
§6 UWG and renders only from `ai.competitors_mentioned`, which is what the
model actually said, on the date the basis line states.

**Findings are `score_component.reason` sentences**, verbatim, because M3 made
each one a complete German sentence for exactly this use. Abstentions are
listed under their own heading as *nicht bewertbar* rather than dropped: a
brief that hides what the pipeline could not measure asserts more than it
knows, which is the same category of error §8 names.

**A blocked company cannot be exported.** `contact_blocked` is A7's third
axis — the score is knowingly too high — and §8 refuses the outreach row for
it; a brief is the document that row would accompany. `render` raises
`ContactBlocked`, and the reason renders in `portal serve` rather than here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from portal.leadlist import Lead, LeadList

BASIS_KEYS: tuple[str, ...] = ("ai_query_text", "ai_checked_at", "ai_model_used")


class MissingBasis(RuntimeError):
    """An `ai.*` result exists and one of §8's three basis fields does not."""


class ContactBlocked(RuntimeError):
    """A7's third axis: the score is knowingly too high, so no brief."""


class NotScored(RuntimeError):
    """No finished scoring run has reached this company."""


@dataclass(frozen=True)
class AiBasis:
    queries: tuple[str, ...]
    checked_on: str
    model: str
    queries_checked: int
    brand_mentions: int
    competitors: tuple[str, ...]


def _german_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return iso


def ai_basis(conn: sqlite3.Connection, company_id: int) -> AiBasis | None:
    """None where the check never ran; raises where it ran without a basis."""
    row = conn.execute(
        "SELECT ai_queries_checked, ai_brand_mentions, ai_competitors_mentioned, "
        "ai_query_text, ai_checked_at, ai_model_used FROM company_profile "
        "WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if row is None or row["ai_queries_checked"] is None:
        return None
    missing = [key for key in BASIS_KEYS if not row[key]]
    if missing:
        raise MissingBasis(
            f"company {company_id} has an AI-visibility result and no "
            f"{', '.join(missing)} — §8 forbids exporting a comparative claim "
            f"without its basis. Re-run `portal ai-check --recheck` for it."
        )
    competitors = tuple(
        part.strip()
        for part in str(row["ai_competitors_mentioned"] or "").split(",")
        if part.strip()
    )
    return AiBasis(
        queries=tuple(
            q.strip() for q in str(row["ai_query_text"]).split("|") if q.strip()
        ),
        checked_on=str(row["ai_checked_at"]),
        model=str(row["ai_model_used"]),
        queries_checked=int(row["ai_queries_checked"]),
        brand_mentions=int(row["ai_brand_mentions"] or 0),
        competitors=competitors,
    )


def _findings(lead: Lead) -> tuple[list[str], list[str], list[str]]:
    opportunities = [
        c.reason
        for c in lead.components
        if c.points > 0 and c.rule_id.startswith("opp.")
    ]
    strengths = [
        c.reason
        for c in lead.components
        if c.points > 0 and not c.rule_id.startswith("opp.")
    ] + [c.reason for c in lead.components if c.points < 0]
    unmeasurable = [f"{c.rule_id}: {c.reason}" for c in lead.components if c.abstained]
    return opportunities, strengths, unmeasurable


def render(conn: sqlite3.Connection, company_id: int) -> str:
    """The brief, as Markdown. Raises rather than rendering a hollow one."""
    lead = LeadList(conn).lead(company_id)
    if lead is None:
        raise LookupError(f"no company {company_id}")
    if not lead.scored:
        raise NotScored(
            f"{lead.domain} has no finished score; run `portal score` first"
        )
    if lead.contact_blocked:
        raise ContactBlocked(
            f"{lead.domain} is contact-blocked: {lead.open_flags} open review flag(s) "
            f"leave its score too high (A7, §8). Resolve them in `portal serve` first."
        )
    basis = ai_basis(conn, company_id)
    company = conn.execute(
        "SELECT legal_name, legal_form, city, postal_code, country FROM company WHERE id = ?",
        (company_id,),
    ).fetchone()
    opportunities, strengths, unmeasurable = _findings(lead)

    name = company["legal_name"] or lead.domain
    lines = [
        f"# Research-Brief: {name}",
        "",
        f"- **Domain:** {lead.domain}",
    ]
    if company["city"]:
        lines.append(
            f"- **Ort:** {company['postal_code'] or ''} {company['city']}"
            f"{' (' + company['country'] + ')' if company['country'] else ''}".replace(
                "  ", " "
            )
        )
    if lead.platform:
        lines.append(f"- **Shopsystem:** {lead.platform}")
    lines += [
        (
            f"- **Bewertung:** {lead.total} Punkte, Band {lead.band} "
            f"(Phase {lead.phase}, Regelwerk {lead.ruleset_version}, berechnet {lead.computed_at})"
        ),
        "",
        "## Befunde",
        "",
    ]
    if opportunities:
        lines.append("**Ansatzpunkte**")
        lines += [f"- {reason}" for reason in opportunities]
        lines.append("")
    if strengths:
        lines.append("**Was bereits da ist**")
        lines += [f"- {reason}" for reason in strengths]
        lines.append("")
    if unmeasurable:
        lines.append(
            "**Nicht bewertbar** — die Regel hat in keine Richtung gefeuert (A7):"
        )
        lines += [f"- {entry}" for entry in unmeasurable]
        lines.append("")

    if basis is not None:
        quoted = " · ".join(f"„{q}“" for q in basis.queries)
        not_named = basis.queries_checked - basis.brand_mentions
        lines += [
            "## KI-Sichtbarkeit",
            "",
            (
                f"Geprüft am {_german_date(basis.checked_on)} über Claude (`{basis.model}`) "
                f"mit aktivierter Websuche.  "
            ),
            f"Abfragen: {quoted}  ",
            (
                f"Ergebnis: Bei {not_named} von {basis.queries_checked} Abfragen wurde "
                f"Ihre Marke nicht genannt."
                if not_named
                else f"Ergebnis: Bei allen {basis.queries_checked} Abfragen wurde Ihre Marke genannt."
            ),
        ]
        if basis.competitors and not_named:
            lines.append(f"Genannt wurden stattdessen: {', '.join(basis.competitors)}.")
        lines += [
            "",
            (
                "_Eine Messung: ein Modell, ein Datum, Websuche aktiviert. Keine Aussage "
                "über KI-Systeme im Allgemeinen (§8)._"
            ),
            "",
        ]

    lines += [
        "---",
        (
            f"_Erstellt aus `score_component`-Sätzen des Regelwerks {lead.ruleset_version}; "
            f"jede Angabe ist im Portal mit ihrem Beleg hinterlegt._"
        ),
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "BASIS_KEYS",
    "AiBasis",
    "ContactBlocked",
    "MissingBasis",
    "NotScored",
    "ai_basis",
    "render",
]
