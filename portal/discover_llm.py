"""§5.1's **second** discovery source — `portal discover --source websearch`.

Places (M8, `portal.discover`) stays the primary and is untouched by this
module. This one exists because the Places SKU needs a Google key and a Console
quota cap that no code can confirm, and neither has ever been available on this
machine — so §5.1 has been a stage with a writer and no way to run it. This is
a way to run it, and it is **deliberately the lower-fidelity one** (M1.119).

**The bias, stated at the top because it is the reason to distrust the output
rather than a caveat on it.** A web-search model answers with shops that
already rank — that is what a search index is. §6's whole premise is the
opposite: the lead worth having is the competent shop that is *under-ranked*,
and `opp.ai_invisible` awards +15 for exactly the absence this source selects
against. So a row discovered here is systematically more likely to be a shop
that needs nothing. It is provenance, not a defect: `discovery_source =
'llm_websearch'` is on every row, and a later scoring question about where a
lead came from is answerable without guessing.

What this does NOT get, and Places does: a verified address. `city` and
`postal_code` are left NULL rather than parsed out of a model's prose — a
postal code that a language model produced is an unmeasured value entering a
column that reads as measured, which is M1.52's rule in a different column.

**Cost.** One live call with the web-search tool, at most `MAX_CALLS` of them,
priced with the *same* search allowance as §5.5c — `ai_visibility
.SEARCH_CONTEXT_TOKENS`, imported rather than restated, because two modules
holding two numbers for *"how much context does a web search drag in"* is
M1.42's shape (M1.109's, most recently). §7 control 2 gates it, control 3
bounds the run, and the reservation is reconciled to measured usage.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any

from portal import ai_visibility, countries, ledger, llm
from portal.artifacts import utc_now
from portal.urls import normalise_domain

STAGE = "discover"
SOURCE = "llm_websearch"

#: The cap on one run's calls. Five, not ten: each call is a live web-search
#: message and the marginal call returns mostly what the previous one did —
#: the model is drawing on one index, so breadth comes from a different
#: `--query`, not from asking the same one again.
MAX_CALLS = 5

#: Sent to the provider as `max_uses`, so the bound binds where it is spent
#: rather than only where it is written (M1.103's shape, via `ask_with_search`).
SEARCHES_PER_CALL = 2

#: How many shops one call is asked for. The answer is a domain list; 20 is
#: what a single search-backed answer can carry without the tail turning into
#: invention.
SHOPS_PER_CALL = 20

#: Generous for 20 `{"domain": ..., "name": ...}` pairs and small enough that a
#: runaway answer costs a fraction of a cent.
MAX_OUTPUT_TOKENS = 2_000

#: Tightened by M1.121(a) after 12 of the first 25 real calls returned
#: something the parser could not read. The instructions that changed are the
#: last four sentences: they name the failure modes that actually occurred —
#: a preamble, a fenced code block, and a source list appended after the
#: object — rather than restating "nur JSON" more emphatically. The parser was
#: fixed in the same commit; **the prompt is the cheaper half of the fix and
#: the parser is the half that has to hold**, because an instruction is a
#: request and a parser is a guarantee.
SYSTEM_PROMPT = (
    "Du recherchierst deutschsprachige Online-Shops. Der Nutzer nennt eine "
    "Produkt- oder Branchenbeschreibung und eine Region. Suche im Web und "
    "nenne eigenständige Online-Shops, die dort verkaufen. "
    "**Keine Marktplätze, keine Preisvergleiche, keine Portale, keine "
    "Hersteller ohne eigenen Shop.** "
    "Antworte mit genau einem JSON-Objekt der Form "
    '{"shops": [{"domain": "beispiel.de", "name": "Beispiel Shop"}]}. '
    f"Höchstens {SHOPS_PER_CALL} Shops. "
    "`domain` ist die reine Domain ohne https:// und ohne www. "
    "Deine Antwort beginnt mit { und endet mit }. "
    "Kein einleitender Satz, keine Zusammenfassung, kein abschließender Kommentar. "
    "Keine Code-Fences (kein ```), kein Markdown. "
    "Keine Quellenangaben und keine Fußnoten nach dem JSON — die Suchergebnisse "
    "sind bereits erfasst und dürfen nicht wiederholt werden."
)

#: Marketplaces, price-comparison sites and portals, dropped by name.
#:
#: **Explicit and short on purpose.** The alternative — inferring
#: *"is this a marketplace"* from the page — is a classifier, and a wrong
#: classifier here writes a `company` row that every later stage treats as a
#: lead. A list is auditable: it says exactly what it excludes, a reader can
#: disagree with a specific entry, and adding one is a one-line diff with a
#: reason. It is matched against the registrable domain, so `amazon.de`,
#: `amazon.at` and `www.amazon.de` all fall to the `amazon` entry.
MARKETPLACES: frozenset[str] = frozenset(
    {
        # marketplaces
        "amazon",
        "ebay",
        "ebay-kleinanzeigen",
        "kleinanzeigen",
        "otto",
        "kaufland",
        "hood",
        "etsy",
        "temu",
        "shein",
        "aliexpress",
        "wish",
        "zalando",
        "aboutyou",
        "avocadostore",
        "manomano",
        "realde",
        "real",
        "galaxus",
        "digitec",
        "conrad",
        "mediamarkt",
        "saturn",
        # price comparison and aggregators
        "idealo",
        "geizhals",
        "billiger",
        "guenstiger",
        "preisvergleich",
        "check24",
        "verivox",
        "testberichte",
        "trustedshops",
        "trustpilot",
        # portals, directories and platforms that are not shops
        "google",
        "facebook",
        "instagram",
        "pinterest",
        "youtube",
        "tiktok",
        "wikipedia",
        "shopify",
        "shopware",
        "woocommerce",
        "etracker",
        "gelbeseiten",
        "wlw",
        "europages",
    }
)


@dataclass(frozen=True)
class Found:
    domain: str
    display_name: str
    inserted: bool


@dataclass
class Report:
    run_id: int
    query: str
    reserved_usd: float = 0.0
    actual_usd: float = 0.0
    calls: int = 0
    web_searches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    found: list[Found] = field(default_factory=list)
    marketplaces: list[str] = field(default_factory=list)
    unusable: int = 0
    unparsed_answers: int = 0
    truncated_answers: int = 0
    stopped_by: str = ""

    @property
    def inserted(self) -> int:
        return sum(1 for f in self.found if f.inserted)


def is_marketplace(domain: str) -> bool:
    """True if the normalised domain's registrable label is on the list.

    The label rather than the whole string, so one entry covers every ccTLD a
    marketplace operates (`amazon.de`, `amazon.at`) without listing them, and
    so `amazonas-kaffee.de` — a real shop whose name merely starts the same
    way — is NOT dropped. That second case is why this is an equality test on
    the label and not a substring search.
    """
    label = domain.split(".")[0] if domain else ""
    return label in MARKETPLACES


def parse_shops(text: str) -> list[tuple[str, str]] | None:
    """`{"shops": [...]}` out of the answer, or `None` if it is not there.

    `None` is distinct from `[]`: an unparseable answer is a call that was PAID
    FOR and produced nothing readable, and the report counts those separately
    from a call that searched and honestly found no shop (M1.59's tri-state).

    The object-finding half is `llm.parse_last_json_object`, shared with
    §5.5c (M1.121). This function's own version lacked the balanced-brace trim
    and failed on every answer that appended a source list after the JSON —
    **12 of the first 25 real calls**, which is what made this a measured fix
    rather than a tidy-up.
    """
    payload = llm.parse_last_json_object(text)
    if payload is None:
        return None
    shops = payload.get("shops")
    if not isinstance(shops, list):
        return None
    out: list[tuple[str, str]] = []
    for entry in shops:
        if isinstance(entry, dict):
            domain = str(entry.get("domain", "") or "").strip()
            name = str(entry.get("name", "") or "").strip()
        elif isinstance(entry, str):
            domain, name = entry.strip(), ""
        else:
            continue
        if domain:
            out.append((domain, name))
    return out


def build_prompt(query: str, region: str, *, already: int = 0) -> str:
    text = f"{query} {region}".strip()
    ask = (
        f"Suchbegriff: {text}\n"
        f"Nenne bis zu {SHOPS_PER_CALL} eigenständige deutschsprachige "
        f"Online-Shops dazu."
    )
    if already:
        # The second and later calls say what has already been taken, so the
        # marginal call is asked for something new rather than re-ranking the
        # same list. It is a request, not a guarantee — the dedupe below is
        # what actually holds.
        ask += f"\nNenne andere als die {already} bereits gefundenen Shops."
    return ask


# ── reservation arithmetic (offline) ─────────────────────────────────────


def reservation(
    call_count: int, *, provider: str, model: str, prompt_tokens: int
) -> llm.CostEstimate:
    """§7 control 3's number for `call_count` live search calls.

    The search allowance is `ai_visibility.SEARCH_CONTEXT_TOKENS` — **imported,
    not restated**. It is the same fact about the same tool on the same model
    (M1.54: raw results land in context in full), and a second copy is how the
    two drift. Each call may use up to `SEARCHES_PER_CALL` searches, and the
    reservation prices all of them because that is the number sent as
    `max_uses`: reserving for fewer than the bound permits is reserving for the
    outcome one hopes for.
    """
    return llm.estimate_cost(
        input_tokens=(
            prompt_tokens + ai_visibility.SEARCH_CONTEXT_TOKENS * SEARCHES_PER_CALL
        )
        * call_count,
        output_tokens=MAX_OUTPUT_TOKENS * call_count,
        provider=provider,
        model=model,
        batch=False,
        web_searches=SEARCHES_PER_CALL * call_count,
    )


def unmeasured_floor(call_count: int, *, provider: str, model: str) -> float:
    """What a dry run can price with no key: the searches and the allowance.
    The prompt's own tokens are measured at `--submit` and are the small part."""
    return reservation(
        call_count, provider=provider, model=model, prompt_tokens=0
    ).total_usd


# ── the paid path ────────────────────────────────────────────────────────


@llm.requires_ledger_clearance
def run(
    conn: sqlite3.Connection,
    provider: ai_visibility.SearchProvider,
    query: str,
    *,
    region: str = "",
    country: str | None = None,
    max_calls: int = MAX_CALLS,
    clearance: ledger.LedgerClearance,
    per_run_ceiling_usd: float | None = None,
    say: Any = print,
) -> Report:
    """`max_calls` live search calls, reserved before the first and reconciled
    to measured usage after the last.

    §7's order, the same one `ai_visibility.run` keeps: control 2's clearance is
    already in hand (the caller cannot reach here without one), control 3 is
    checked against the whole run's estimate **before the `run` row exists**, so
    a refused run reserves nothing at all.
    """
    from portal.llm_anthropic import BalanceExhausted  # the seam, not the SDK

    if max_calls < 1 or max_calls > MAX_CALLS:
        raise ValueError(f"max_calls must be 1–{MAX_CALLS}")
    if per_run_ceiling_usd is None:
        per_run_ceiling_usd = ledger.RUN_CEILING_USD

    text = f"{query} {region}".strip()
    prompt_tokens = provider.token_counter()(
        system=SYSTEM_PROMPT, user_text=build_prompt(query, region)
    )
    estimate = reservation(
        max_calls,
        provider=provider.name,
        model=provider.model,
        prompt_tokens=prompt_tokens,
    )
    if estimate.total_usd > per_run_ceiling_usd:
        raise ai_visibility.RunCeilingExceeded(
            f"§7 control 3: this run would reserve ${estimate.total_usd:.2f} "
            f"against a ${per_run_ceiling_usd:.2f} per-run ceiling ({max_calls} "
            f"calls, {estimate.web_searches} searches). Lower --max-calls, or "
            f"raise the ceiling having read this."
        )

    cursor = conn.execute(
        "INSERT INTO run (started_at, stage, est_cost_usd, country) "
        "VALUES (?, ?, ?, ?)",
        (utc_now(), STAGE, estimate.total_usd, country),
    )
    run_id = int(cursor.lastrowid or 0)
    conn.commit()
    report = Report(run_id=run_id, query=text, reserved_usd=estimate.total_usd)
    say(
        f"run {run_id}: reserved ${estimate.total_usd:.4f} for {max_calls} call(s) "
        f"({estimate.web_searches} searches at "
        f"${llm.WEB_SEARCH_PER_SEARCH_USD:.2f}, {prompt_tokens} measured prompt "
        f"tokens + {ai_visibility.SEARCH_CONTEXT_TOKENS:,} search allowance × "
        f"{SEARCHES_PER_CALL} per call)"
    )

    seen: set[str] = set()
    try:
        for _ in range(max_calls):
            answer = provider.ask_with_search(
                system=SYSTEM_PROMPT,
                user_text=build_prompt(query, region, already=len(seen)),
                max_tokens=MAX_OUTPUT_TOKENS,
                max_searches=SEARCHES_PER_CALL,
                clearance=clearance,
            )
            report.calls += 1
            report.web_searches += answer.usage.web_searches
            report.input_tokens += answer.usage.input_tokens
            report.output_tokens += answer.usage.output_tokens
            if answer.stop_reason == "max_tokens":
                report.truncated_answers += 1
            shops = parse_shops(answer.text)
            if shops is None:
                report.unparsed_answers += 1
                say("  (answer not parseable as JSON — the call was still paid for)")
                continue
            for raw_domain, display in shops:
                try:
                    domain = normalise_domain(raw_domain)
                except ValueError:
                    report.unusable += 1
                    continue
                if is_marketplace(domain):
                    report.marketplaces.append(domain)
                    continue
                # Within-run dedupe, so one domain named by three calls is one
                # `Found`. The UNIQUE on `company.domain` is the guard that
                # actually holds across runs; this one keeps the REPORT honest.
                if domain in seen:
                    continue
                seen.add(domain)
                inserted = conn.execute(
                    "INSERT INTO company (domain, legal_name, city, postal_code, "
                    "country, discovery_source, discovery_query, discovered_at) "
                    "VALUES (?,?,NULL,NULL,?,?,?,?) "
                    "ON CONFLICT (domain) DO NOTHING",
                    (
                        domain,
                        display or None,
                        # This source returns a domain and a name and nothing
                        # else — no address, so no measurement to prefer. The
                        # TLD, then the run's tag (M1.128).
                        countries.derive(domain, region=country),
                        SOURCE,
                        text,
                        utc_now(),
                    ),
                )
                report.found.append(Found(domain, display, inserted.rowcount == 1))
            conn.commit()
    except BalanceExhausted as exc:
        report.stopped_by = f"{type(exc).__name__}: {exc}"
        say(f"  ⛔ {exc}")
    except Exception as exc:  # noqa: BLE001 — any provider failure mid-run
        # M1.108's rule: calls already answered were paid for and their rows
        # stand. Finished and reported, not re-raised as a traceback.
        report.stopped_by = f"{type(exc).__name__}: {exc}"[:500]
        say(f"  ⛔ stopped: {report.stopped_by}")
    finally:
        actual = llm.estimate_cost(
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
            provider=provider.name,
            model=provider.model,
            batch=False,
            web_searches=report.web_searches,
        )
        report.actual_usd = actual.total_usd
        # Control 3's reconcile, into the column control 2 sums, plus §7
        # control 8's `web_searches` count.
        conn.execute(
            "UPDATE run SET finished_at = ?, companies_seen = ?, est_cost_usd = ?, "
            "web_searches = COALESCE(web_searches, 0) + ?, llm_input_tokens = ?, "
            "llm_output_tokens = ? WHERE id = ?",
            (
                utc_now(),
                report.inserted,
                actual.total_usd,
                report.web_searches,
                report.input_tokens,
                report.output_tokens,
                run_id,
            ),
        )
        conn.commit()
    return report


#: `run` drives live search calls, so it carries the gate itself and not only
#: through its caller (M1.71's rule, the same one `ask_with_search` follows).
PAID_SURFACES: tuple[str, ...] = ("run",)
FREE_SURFACES: tuple[str, ...] = (
    "build_prompt",
    "is_marketplace",
    "parse_shops",
    "reservation",
    "unmeasured_floor",
)

llm.assert_ledger_guarded(
    sys.modules[__name__],
    paid=PAID_SURFACES,
    free=FREE_SURFACES,
    where="portal.discover_llm",
)

__all__ = [
    "FREE_SURFACES",
    "MARKETPLACES",
    "MAX_CALLS",
    "MAX_OUTPUT_TOKENS",
    "PAID_SURFACES",
    "SEARCHES_PER_CALL",
    "SHOPS_PER_CALL",
    "SOURCE",
    "STAGE",
    "SYSTEM_PROMPT",
    "Found",
    "Report",
    "build_prompt",
    "is_marketplace",
    "parse_shops",
    "reservation",
    "run",
    "unmeasured_floor",
]
