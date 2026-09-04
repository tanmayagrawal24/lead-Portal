"""§5.5c — the AI-visibility check (M6). Dry by default, paid only on `--submit`.

**What it measures, and no more.** For each company §5.4 admits, two German
category queries are derived from what `extract-p2 --purpose homepage` wrote,
each is put to Claude with web search enabled, and the answer is read for the
company's own brand and for the brands that appeared instead. That is one
model, on one date, with one tool, and §8 constrains every sentence built on
it to exactly that — which is why three of the six keys written here carry
the *basis* (query text, date, model) and the brief refuses to render without
them.

**Three decisions taken here rather than left to a reader (M1.105).**

*(a)* **Queries are derived deterministically from the first product category,
and a company with none is withheld, not guessed for.** §5.5c says *"from
`one_line_offer` and `product_categories`"*; the offer line is prose and a
query minted from prose by a heuristic is a query nobody can reproduce — the
literal text is recorded precisely so the finding can be re-run. The category
list is the model's own structured answer, already verified, and its first
entry is the noun the shop leads with. The templates are fixed and the term is
the only variable, so `ai.query_text` is reproducible from the profile.

*(b)* **The reservation is `count_tokens` on the prompt plus a declared,
dated allowance for what the search injects — and the allowance is the one
number in §7 that cannot be measured before the call.** M1.52 forbids
heuristics in the reservation because a model-specific count is available for
free. It is not available here: the search results are not known until the
search runs, and on Haiku 4.5 they land in context in full (M1.54). §5.5c's
own figure is 10–20k input tokens per query; `SEARCH_CONTEXT_TOKENS` takes the
top of that range, is dated, and **errs toward over-reservation**, which is
control 3's stated preference. The measured `usage` replaces it the moment
the response arrives, so the ledger carries the estimate for seconds, not for
a batch's 24 hours.

*(c)* **A balance that runs dry mid-run finishes the run rather than aborting
it.** M1.102 aborts `extract-p2` on `BalanceExhausted` because nothing was
written and the batch never existed. Here every company's six keys are one
transaction, committed before the next company's first call, and the calls
already answered were paid for. An aborted run is a run `company_profile`
refuses to serve (migration 007) — which would throw away signals real money
bought. So the run is **finished with `companies_seen` at what was actually
reached**, the shortfall is printed by name, and `run.est_cost_usd` is
reconciled to the measured actual (control 3). Nothing is lost, and nothing
is claimed for the companies not reached.

Its own `run.stage = 'ai_check'`, for M1.101(a)'s reason exactly.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from portal import extract_p2, ledger, llm
from portal.artifacts import utc_now

STAGE = "ai_check"

#: §5.5c: default 2, hard maximum 3. The MINIMUM is also 2, and it is not
#: configurable: §6.2's `opp.ai_invisible` needs `ai.queries_checked >= 2`, so
#: one query would silently disable a +15 rule (M1.23). If one query is ever
#: wanted the predicate changes in the same commit; the CLI refuses it here.
DEFAULT_QUERIES = 2
MIN_QUERIES = 2
MAX_QUERIES = 3

#: §5.5c's *"10–20k input tokens per query"* on Haiku 4.5 with the basic
#: `web_search_20250305` tool (M1.54: raw results land in context in full).
#: The top of the range, dated, and the only unmeasured number in the
#: reservation — see the module docstring, (b).
SEARCH_CONTEXT_TOKENS = 20_000
SEARCH_CONTEXT_AS_OF = date(2026, 8, 15)

#: One search per query, sent to the provider as `max_uses` so the bound binds.
SEARCHES_PER_QUERY = 1

#: The answer is a short brand list; 800 tokens is generous for it and small
#: enough that a runaway answer cannot cost more than a cent.
MAX_OUTPUT_TOKENS = 800

#: Fixed templates. `{term}` is the only variable, so the literal query text
#: recorded in `ai.query_text` is reproducible from the profile alone.
QUERY_TEMPLATES: tuple[str, ...] = ("beste {term}", "{term} Test", "{term} kaufen")

SYSTEM_PROMPT = (
    "Du bist ein Einkaufsberater für deutschsprachige Verbraucher. Der Nutzer "
    "stellt eine Suchanfrage. Recherchiere einmal im Web und nenne die Marken "
    "oder Online-Shops, die du für diese Anfrage empfehlen würdest — so, wie du "
    "sie einem Freund nennen würdest. Antworte ausschließlich mit einem "
    "JSON-Objekt der Form "
    '{"brands": ["Marke 1", "Marke 2", ...], "note": "ein Satz"} '
    "und nichts sonst. Höchstens acht Marken. Keine Erklärung außerhalb des JSON."
)

#: Legal-form tokens stripped from `company.legal_name` before it is used as a
#: brand term. Removable, capitalised, platform-shipped strings are weak
#: evidence of anything (§10.4), and *"GmbH"* is in every third answer.
_LEGAL_FORM = re.compile(
    # Lookarounds rather than `\b`: a form ending in a dot (`e.K.`, `Inh.`)
    # has no word boundary after it at end of string.
    r"(?<!\w)(GmbH\s*&\s*Co\.?\s*KG|GmbH|UG\s*\(haftungsbeschränkt\)|UG|AG|e\.\s?K\.?|"
    r"eK|KG|OHG|GbR|Einzelunternehmen|Inh\.|Inhaber(in)?|Ltd\.?|Limited)(?!\w)",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^0-9a-zäöüß]+")
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


#: §7 control 3's refusal, imported rather than redefined (M1.109). This module
#: used to declare its own `RunCeilingExceeded` and its own `PER_RUN_CEILING_USD
#: = 5.00` beside `ledger.MONTHLY_CEILING_USD`, which made one control two
#: expressions — M1.42's shape, applied to a policy bound instead of to a
#: corpus. The ceiling is now `ledger.RUN_CEILING_USD` and this name is the same
#: class `charge_run` raises, so `except RunCeilingExceeded` catches control 3
#: whether it fired here (against a whole run's estimate, before the `run` row
#: exists and nothing is reserved) or at `extract_p2`'s reservation write.
RunCeilingExceeded = ledger.RunCeilingExceeded


@dataclass(frozen=True)
class Plan:
    """One company's queries, ready to send. Everything a dry run prints."""

    company_id: int
    domain: str
    term: str
    queries: tuple[str, ...]
    brand_terms: tuple[str, ...]


@dataclass(frozen=True)
class Withheld:
    company_id: int
    domain: str
    reason: str


@dataclass(frozen=True)
class QueryResult:
    query: str
    brands: tuple[str, ...]
    brands_parsed: bool
    truncated: bool
    mentioned: bool
    usage: llm.Usage


@dataclass
class Report:
    run_id: int
    model: str
    reserved_usd: float
    actual_usd: float = 0.0
    checked: list[tuple[str, int, int]] = field(default_factory=list)
    not_reached: list[str] = field(default_factory=list)
    web_searches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    balance_exhausted: bool = False
    #: Why the run stopped short, if it did — the exception's class and text.
    #: Carried here because `run.aborted_reason` would hide the paid signals
    #: (007), and a stop that is neither recorded nor printed is a stop the
    #: next person infers from `companies_seen` (M1.39's shape).
    stopped_by: str | None = None


# ── query derivation (offline) ───────────────────────────────────────────


def category_term(product_categories: str | None) -> str | None:
    """The first category, or None. Pipe-separated is §5.5b's own format."""
    if not product_categories:
        return None
    for raw in product_categories.split("|"):
        term = " ".join(raw.split()).strip(" .,;:-")
        if len(term) >= 3:
            return term
    return None


def derive_queries(term: str, count: int = DEFAULT_QUERIES) -> tuple[str, ...]:
    if not MIN_QUERIES <= count <= MAX_QUERIES:
        raise ValueError(
            f"§5.5c allows {MIN_QUERIES}–{MAX_QUERIES} queries, not {count}; below "
            f"{MIN_QUERIES} the +15 rule cannot fire (M1.23)"
        )
    return tuple(template.format(term=term) for template in QUERY_TEMPLATES[:count])


def brand_terms(domain: str, legal_name: str | None) -> tuple[str, ...]:
    """What counts as *the company was named*: its domain, the domain's label,
    and its legal name with the legal form stripped. Short labels are dropped —
    a three-letter label matches inside ordinary words."""
    terms: list[str] = []
    host = domain.lower().strip()
    if host:
        terms.append(host)
        label = host.split(".")[0]
        if len(label) >= 4:
            terms.append(label)
    if legal_name:
        bare = " ".join(_LEGAL_FORM.sub(" ", legal_name).split()).strip(" ,.-&")
        if len(bare) >= 4:
            terms.append(bare)
    seen: dict[str, None] = {}
    for term in terms:
        seen.setdefault(term.lower(), None)
    return tuple(seen)


def _normalise(text: str) -> str:
    return _NON_WORD.sub(" ", text.lower())


def mentioned(text: str, terms: Sequence[str]) -> bool:
    """Case-insensitive, punctuation-insensitive containment. `zecplus.de`,
    `ZecPlus` and `Zec-Plus` all count as the company being named."""
    haystack = f" {_normalise(text)} "
    compact = haystack.replace(" ", "")
    for term in terms:
        needle = _normalise(term).strip()
        if not needle:
            continue
        if f" {needle} " in haystack or needle.replace(" ", "") in compact:
            return True
    return False


def parse_brands(text: str) -> tuple[tuple[str, ...], bool]:
    """The `brands` list out of the answer, and whether it parsed at all.

    Lenient on purpose: a model with a search tool sometimes narrates before
    the JSON. The *last* object in the text is taken. Where nothing parses the
    query still ran and is still paid for; the competitor list is simply empty
    and `brands_parsed` says so."""
    match = None
    for match in _JSON_OBJECT.finditer(text):
        pass
    if match is None:
        return (), False
    candidate = match.group(0)
    # Trim to the last balanced object if the greedy match overshot.
    depth, end = 0, None
    for index, char in enumerate(candidate):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is not None:
        candidate = candidate[:end]
    try:
        payload = json.loads(candidate)
    except ValueError:
        return (), False
    brands = payload.get("brands") if isinstance(payload, dict) else None
    if not isinstance(brands, list):
        return (), False
    cleaned: list[str] = []
    for item in brands:
        name = " ".join(str(item).split()).strip()
        if name and name.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(name[:80])
    return tuple(cleaned[:8]), True


# ── eligibility ──────────────────────────────────────────────────────────

_PROFILE_SQL = """
SELECT c.id AS company_id, c.domain, c.legal_name,
       p.product_categories, p.one_line_offer, p.ai_checked_at, p.homepage_extracted
FROM company c LEFT JOIN company_profile p ON p.company_id = c.id
ORDER BY c.domain
"""


def prepare(
    conn: sqlite3.Connection, *, queries: int = DEFAULT_QUERIES, recheck: bool = False
) -> tuple[list[Plan], list[Withheld]]:
    """Who gets checked, and why everyone else does not — printed by the dry run.

    The §5.4 gate is `extract_p2.eligible_companies`, the same predicate the
    other paid stage consults (audit finding 1); this module does not have a
    second copy of it. On top of it: no category term means no query (a), and
    an already-checked company is withheld unless `recheck` is passed — a
    re-check is new spend and §7 treats a re-run as one.
    """
    admitted, withheld_gate = extract_p2.eligible_companies(conn)
    plans: list[Plan] = []
    withheld: list[Withheld] = []
    for row in conn.execute(_PROFILE_SQL):
        company_id = int(row["company_id"])
        domain = str(row["domain"])
        if company_id in withheld_gate:
            withheld.append(Withheld(company_id, domain, withheld_gate[company_id]))
            continue
        if company_id not in admitted:
            withheld.append(Withheld(company_id, domain, "not admitted by §5.4"))
            continue
        if row["ai_checked_at"] and not recheck:
            withheld.append(
                Withheld(
                    company_id,
                    domain,
                    f"already checked on {row['ai_checked_at']} — pass --recheck "
                    f"to spend again",
                )
            )
            continue
        term = category_term(row["product_categories"])
        if term is None:
            why = (
                "no product category — `offer.product_categories` is absent; run "
                "`extract-p2 --purpose homepage --submit` and `reconcile` first"
                if not row["homepage_extracted"]
                else "no product category — the homepage extraction ran and "
                "returned none (§5.5c (a): no query is minted from prose)"
            )
            withheld.append(Withheld(company_id, domain, why))
            continue
        plans.append(
            Plan(
                company_id,
                domain,
                term,
                derive_queries(term, queries),
                brand_terms(domain, row["legal_name"]),
            )
        )
    return plans, withheld


# ── reservation arithmetic (offline) ─────────────────────────────────────


def reservation(
    plans: Sequence[Plan], *, provider: str, model: str, prompt_tokens: int
) -> llm.CostEstimate:
    """§7 control 3's number, with its arithmetic kept. `prompt_tokens` is the
    measured count for ONE query's prompt; the search allowance is added per
    query (b). Live prices — this is not a batch."""
    query_count = sum(len(plan.queries) for plan in plans)
    return llm.estimate_cost(
        input_tokens=(prompt_tokens + SEARCH_CONTEXT_TOKENS) * query_count,
        output_tokens=MAX_OUTPUT_TOKENS * query_count,
        provider=provider,
        model=model,
        batch=False,
        web_searches=SEARCHES_PER_QUERY * query_count,
    )


def unmeasured_floor(plans: Sequence[Plan], *, provider: str, model: str) -> float:
    """What a dry run can price without a key: the searches and the allowance.
    The prompt's own tokens are measured at `--submit` and are the smaller part."""
    return reservation(plans, provider=provider, model=model, prompt_tokens=0).total_usd


# ── the paid path ────────────────────────────────────────────────────────


class SearchProvider:
    """The two methods this stage needs. `AnthropicProvider` satisfies it."""

    name: str
    model: str

    def token_counter(self) -> llm.TokenCounter: ...

    def ask_with_search(
        self,
        *,
        system: str,
        user_text: str,
        max_tokens: int,
        max_searches: int,
        clearance: ledger.LedgerClearance,
    ) -> llm.SearchAnswer: ...


def _ask(
    provider: SearchProvider, query: str, plan: Plan, clearance: ledger.LedgerClearance
) -> QueryResult:
    answer = provider.ask_with_search(
        system=SYSTEM_PROMPT,
        user_text=query,
        max_tokens=MAX_OUTPUT_TOKENS,
        max_searches=SEARCHES_PER_QUERY,
        clearance=clearance,
    )
    brands, parsed = parse_brands(answer.text)
    return QueryResult(
        query=query,
        brands=brands,
        brands_parsed=parsed,
        truncated=answer.stop_reason == "max_tokens",
        mentioned=mentioned(answer.text, plan.brand_terms),
        usage=answer.usage,
    )


def _write_signals(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    plan: Plan,
    results: Sequence[QueryResult],
    model: str,
    checked_on: str,
    observed_at: str,
) -> None:
    """The six §5.5c keys for one company, in one transaction.

    `evidence_url` names the instrument rather than a page, because no page
    was read: the check's evidence is its own basis triple, which is why the
    triple is written as signals and not as a note.
    """
    competitors: list[str] = []
    for result in results:
        for brand in result.brands:
            if not mentioned(brand, plan.brand_terms) and brand.lower() not in {
                c.lower() for c in competitors
            }:
                competitors.append(brand)
    evidence = f"ai-check:{model}:{checked_on}"
    rows: list[tuple[str, str | None, float | None, str | None]] = [
        ("ai.queries_checked", None, float(len(results)), None),
        (
            "ai.brand_mentions",
            None,
            float(sum(1 for r in results if r.mentioned)),
            None,
        ),
        (
            "ai.competitors_mentioned",
            ", ".join(competitors),
            float(len(competitors)),
            None,
        ),
        ("ai.query_text", " | ".join(r.query for r in results), None, None),
        ("ai.checked_at", None, None, checked_on),
        ("ai.model_used", model, None, None),
    ]
    conn.execute("BEGIN")
    try:
        for key, text, num, when in rows:
            conn.execute(
                "INSERT INTO signal (company_id, run_id, key, value_text, value_num, "
                "value_date, method, confidence, evidence_url, observed_at) "
                "VALUES (?,?,?,?,?,?,'llm',1.0,?,?) "
                "ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING",
                (plan.company_id, run_id, key, text, num, when, evidence, observed_at),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def run(
    conn: sqlite3.Connection,
    provider: SearchProvider,
    plans: Sequence[Plan],
    *,
    clearance: ledger.LedgerClearance,
    per_run_ceiling_usd: float | None = None,
    say: Callable[[str], None] = print,
) -> Report:
    """Reserve, ask, write, reconcile — §7 controls 2, 3 and 8 in that order.

    `clearance` is control 2's, taken by the caller before anything else so a
    window already over budget never counts a token. Control 3's reservation
    is the `run` row's `est_cost_usd`, written before the first paid call and
    replaced by the measured actual at the end. The balance seam is (c).
    """
    from portal.llm_anthropic import BalanceExhausted  # the seam, not the SDK

    if not plans:
        raise ValueError("nothing to check — every company was withheld")
    if per_run_ceiling_usd is None:
        per_run_ceiling_usd = ledger.RUN_CEILING_USD

    prompt_tokens = provider.token_counter()(
        system=SYSTEM_PROMPT, user_text=plans[0].queries[0]
    )
    estimate = reservation(
        plans, provider=provider.name, model=provider.model, prompt_tokens=prompt_tokens
    )
    if estimate.total_usd > per_run_ceiling_usd:
        raise RunCeilingExceeded(
            f"§7 control 3: this run would reserve ${estimate.total_usd:.2f} against "
            f"a ${per_run_ceiling_usd:.2f} per-run ceiling ({len(plans)} companies, "
            f"{estimate.web_searches} searches). Split it, or raise the ceiling "
            f"having read this."
        )

    now = utc_now()
    checked_on = datetime.now(UTC).date().isoformat()
    cursor = conn.execute(
        "INSERT INTO run (started_at, stage, est_cost_usd) VALUES (?, ?, ?)",
        (now, STAGE, estimate.total_usd),
    )
    run_id = int(cursor.lastrowid or 0)
    conn.commit()
    report = Report(
        run_id=run_id, model=provider.model, reserved_usd=estimate.total_usd
    )
    say(
        f"run {run_id}: reserved ${estimate.total_usd:.4f} for {len(plans)} companies "
        f"({estimate.web_searches} searches at ${llm.WEB_SEARCH_PER_SEARCH_USD:.2f}, "
        f"{prompt_tokens} measured prompt tokens + {SEARCH_CONTEXT_TOKENS:,} search "
        f"allowance per query)"
    )

    remaining = list(plans)
    try:
        while remaining:
            plan = remaining[0]
            results = [_ask(provider, query, plan, clearance) for query in plan.queries]
            _write_signals(
                conn,
                run_id=run_id,
                plan=plan,
                results=results,
                model=report.model,
                checked_on=checked_on,
                observed_at=utc_now(),
            )
            for result in results:
                report.web_searches += result.usage.web_searches
                report.input_tokens += result.usage.input_tokens
                report.output_tokens += result.usage.output_tokens
            hits = sum(1 for r in results if r.mentioned)
            report.checked.append((plan.domain, hits, len(results)))
            say(
                f"  {plan.domain:28} {hits}/{len(results)} named"
                + (
                    ""
                    if all(r.brands_parsed for r in results)
                    else "  (brand list unparsed)"
                )
                + ("  (answer truncated)" if any(r.truncated for r in results) else "")
            )
            remaining.pop(0)
    except BalanceExhausted as exc:
        report.balance_exhausted = True
        report.stopped_by = f"{type(exc).__name__}: {exc}"
        say(f"  ⛔ {exc}")
    except Exception as exc:  # noqa: BLE001 — a provider failure mid-run, any kind
        # Unit 10 audit (M1.108): a rate limit or a 5xx between two companies
        # is the same case as a dry balance for everything that matters —
        # the calls already answered were paid for and their signals stand.
        # It is finished, reported, and NOT re-raised as a traceback: the
        # caller reads `stopped_by` and exits 2.
        report.stopped_by = f"{type(exc).__name__}: {exc}"[:500]
        say(f"  ⛔ stopped: {report.stopped_by}")
    finally:
        report.not_reached = [plan.domain for plan in remaining]
        actual = llm.estimate_cost(
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
            provider=provider.name,
            model=provider.model,
            batch=False,
            web_searches=report.web_searches,
        )
        report.actual_usd = actual.total_usd
        # Control 3's reconcile: the reservation is replaced by the measured
        # actual, in the same column control 2 sums. The run is FINISHED even
        # when the balance stopped it (c) — its signals were paid for.
        conn.execute(
            "UPDATE run SET finished_at = ?, companies_seen = ?, est_cost_usd = ?, "
            "web_searches = ?, llm_input_tokens = ?, llm_output_tokens = ? WHERE id = ?",
            (
                utc_now(),
                len(report.checked),
                actual.total_usd,
                report.web_searches,
                report.input_tokens,
                report.output_tokens,
                run_id,
            ),
        )
        conn.commit()
    return report


__all__ = [
    "DEFAULT_QUERIES",
    "MAX_OUTPUT_TOKENS",
    "MAX_QUERIES",
    "MIN_QUERIES",
    "QUERY_TEMPLATES",
    "SEARCHES_PER_QUERY",
    "SEARCH_CONTEXT_AS_OF",
    "SEARCH_CONTEXT_TOKENS",
    "STAGE",
    "SYSTEM_PROMPT",
    "Plan",
    "Report",
    "RunCeilingExceeded",
    "SearchProvider",
    "Withheld",
    "brand_terms",
    "category_term",
    "derive_queries",
    "mentioned",
    "parse_brands",
    "prepare",
    "reservation",
    "run",
    "unmeasured_floor",
]
