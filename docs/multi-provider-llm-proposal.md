# Proposal v2 — a provider-agnostic LLM layer

**Status:** proposal. No code. Supersedes v1 (same file, git history).
**Sections it would change:** §2 (non-goals), §3 (stack), §4 (schema, migration 0NN), §5.5, §7, §10.
**Motivation (revised):** run the portal against a **prepaid key from any of Anthropic,
OpenAI or Google**, chosen on measured cost per useful result rather than fixed to one vendor.

**What changed from v1:** three named providers rather than one alternative; prepaid
balance treated as a ceiling the tool does not control (§6); and the AI-visibility check
reconsidered — with three providers it becomes a *stronger* claim, not a cheaper one (§3).

---

## 1. What is actually LLM-dependent

Three call sites, and they are not equivalent.

| # | Call site | Spec | Mechanical? | Portable? |
|---|---|---|---|---|
| 1 | `ImpressumExtract` — fixed schema, structured output | §5.5b | yes | **yes** |
| 2 | `HomepageExtract` — fixed schema, structured output | §5.5b | yes | **yes** |
| 3 | AI-visibility check — web search enabled, free-text answer | §5.5c | **no** | **see §3** |

1 and 2 are schema-constrained extractions from a page already on disk, guarded by
substring verification (§5.5b). 3 is evidence quoted in a letter to a stranger.

## 2. What it costs today, and therefore what a swap is worth

At §7.1's 475 advancing companies/month, on Haiku 4.5:

| Call site | Per company | Monthly | Share |
|---|---|---|---|
| AI-visibility — per-search fee, 2 × $0.01 | $0.020 | $9.50 | 29% |
| AI-visibility — ~30k live tokens | $0.030 | $14.25 | 46% |
| Extractions 1+2 — ~30k batch tokens | $0.015 | $7.13 | 23% |
| **Total** | **$0.065** | **$30.88** | |

**The safely-portable share is 23% of the bill.** A provider at half the price saves
~$3.50/month. Stated plainly so the work is justified by what actually motivates it —
prepaid keys, no vendor commitment, resilience, and the §3 upside — rather than by a
saving it cannot deliver.

The per-search fee is charged by whoever runs the search and does not go away.

## 3. The AI-visibility check — three providers makes it *better*, not cheaper

v1 recommended leaving this on one vendor because §8 makes the model part of the claim:
an exported brief must state the query, the date, the model, and that web search was on,
because that is what makes a comparative claim about a named competitor defensible.

**That reasoning inverts once three providers exist.** Today's claim is *"Claude did not
name you."* The available claim becomes:

> Bei 2 von 2 Abfragen wurde Ihre Marke von keinem der drei geprüften KI-Systeme genannt.

That is a materially stronger statement, and §8 is satisfied by naming three models rather
than one. Two consequences:

- **Cost goes up, not down.** Three models × 2 queries = 6 searches = **$0.06/company** in
  search fees alone, before tokens. Roughly triple today's AI-visibility line.
- **Partial results need a rule.** If two models answer and one errors, the brief must say
  *two*, not three. `ai.queries_checked` already counts completed queries; this needs the
  same discipline per model, and §6.2's `opp.ai_invisible` predicate
  (`ai.queries_checked >= 2`) needs restating in terms of *model × query* pairs.

**Recommendation:** build the Protocol for call sites 1 and 2 first, ship it, then treat
multi-model AI-visibility as its own change with its own §6.2 and §8 amendments. Do not
fold it into the extraction work — the extraction swap is a cost decision, this is a
product decision, and merging them means neither gets judged on its own merits.

**Never do:** pick the cheapest single model for call site 3. It weakens the sentence the
outreach rests on to save $0.03.

## 4. The abstraction — a Protocol, not a framework

§2 rules out an LLM framework and that stands. Two fixed schemas do not justify one, and
three providers do not either — a framework's abstraction is not the same as this one's,
and adopting it would mean inheriting its opinions about retries, streaming and tools.

```python
class LLMProvider(Protocol):
    name: str                       # 'anthropic' | 'openai' | 'google'
    model: str                      # recorded on every batch row

    def estimate_cost(self, input_tokens: int, output_tokens: int, *, batch: bool) -> float: ...
    def extract(self, schema: type[BaseModel], text: str, system: str) -> Extraction: ...
    def supports_batch(self) -> bool: ...
    def submit_batch(self, requests: list[BatchRequest]) -> str: ...
    def poll_batch(self, provider_batch_id: str) -> BatchResult: ...
```

`Extraction` carries the parsed model plus real token counts, so §7's reservation and
reconciliation work unchanged.

**Selected by configuration, never inferred.** `PORTAL_LLM_PROVIDER` + `PORTAL_LLM_MODEL`,
read at startup, failing loudly on an unknown pair.

### What must not change

- **Substring verification (§5.5b) is provider-independent and stays exactly as is.** It is
  what makes swapping safe: a weaker model degrades to more `confidence=0` rows, not to
  confident wrong names. This is the most important sentence in this proposal.
- **`method='llm'`** stays — provenance, not vendor.
- **The 60 KB input cap (§7.5)** applies to every provider.
- **Pre-call reservation (§7.3)** stays; only the price table becomes per-provider.
- **M1.42's rule.** Every signal an extraction produces names the artifact it was read
  from. That is provider-independent and non-negotiable.

## 5. Verify each provider's API before writing bindings

**Do not write OpenAI or Google SDK code from memory or by analogy with Anthropic's.**
Each of the following differs per provider and must be confirmed against that provider's
current documentation at implementation time:

| Capability | Why it matters here |
|---|---|
| Structured-output mechanism | Tool-use vs a JSON-schema response format vs something else. `ImpressumExtract` must come back validated, not parsed from prose. |
| Batch API — exists? shape? discount? | §7.4 reserves the whole batch at submission. No batch means the 50% discount silently disappears, which is a §7 defect, not a performance note. |
| Token accounting field names | `run.llm_input_tokens` needs a real number from every provider. |
| Current model IDs and prices | Data with an as-of date, never constants. |
| Web search availability | Only relevant if §3's multi-model check is later approved. |

If a provider has no batch API, `submit_batch` falls back to serial calls and **says so in
the cost estimate**.

## 6. Prepaid changes the failure model — this is new in v2

§7's controls assume the tool is the thing that stops spending: reserve, check the ceiling,
abort. **A prepaid balance is a second ceiling, owned by the provider, invisible to the
tool and reachable at any moment.**

The dangerous interaction is §7.4. A batch is reserved and submitted as *committed spend*.
If the balance empties between submission and reconciliation:

- the batch may be partially processed, or rejected outright after acceptance;
- `llm_batch.status` has `failed` and `expired`, so the states exist — but nothing
  currently distinguishes *"the provider failed"* from *"we ran out of money"*, and they
  need different operator responses;
- §7.2's rolling 30-day ceiling keeps counting an estimate for spend that never happened.

Four rules:

1. **A balance error is its own status**, not folded into `failed`. `portal reconcile` must
   be able to report *"this batch stopped because the key ran dry"* in those words.
2. **Reconciliation must handle a partially-processed batch** — write the extractions that
   returned, leave the rest unreconciled, do not mark the batch complete.
3. **Where a provider exposes remaining balance, read it and surface it** in `portal
   status` alongside the 30-day figure. Where it does not, say so rather than implying the
   number is known.
4. **Prefer smaller batches on a prepaid key.** A batch is the unit of committed spend, so
   it is also the unit of loss. This is a real trade against the per-call overhead and
   belongs in §7 as a stated one.

## 7. How to choose a provider — measure yield, not price

The corpus is the benchmark. Twelve Impressum pages and thirteen homepages are on disk, and
§5.5b's substring verification gives an objective score with no human judging German prose:

> **verified-field yield** = fields extracted **and** literally present in the cleaned page
> text ÷ fields a human confirms are on the page

A cheaper model that yields more `confidence=0` rows has moved cost from the API bill to
your review queue. **Compare `$ per verified field`, not `$ per million tokens`** — and
publish the table, because it is also the artefact that settles §10.5's open Ollama
question by measurement instead of argument.

Run it offline against stored artifacts. Nothing is committed, so the redaction constraint
that forced hand-written Impressum fixtures does not apply.

## 8. Decisions still needed

1. **Which model per provider**, once §7's benchmark has run. The benchmark answers this;
   do not pre-commit.
2. **§2 and §3 amendment.** The dependency list is a design decision and this adds three
   SDKs. Record it as deliberate, with its reason.
3. **Cross-provider fallback.** If provider A fails mid-run, abort or retry on B?
   **Recommend abort for v1** — a fallback multiplies both the cost model and the evidence
   story, and on a prepaid key "A ran dry so we spent B's balance" is a surprise, not a
   feature.
4. **Multi-model AI-visibility (§3)** — separate change, separate ratification.

## 9. Build order

| Step | Deliverable |
|---|---|
| 1 | Protocol + Anthropic implementation, extracted from existing code. **No behaviour change; existing tests prove it.** |
| 2 | Price table as dated data, per (provider, model, batch), asserted at startup |
| 3 | Prepaid failure handling (§6) — balance status, partial reconciliation |
| 4 | Second and third provider implementations, extractions only |
| 5 | Yield benchmark over the stored corpus; publish the table |
| 6 | Operator picks on the numbers; `PORTAL_LLM_PROVIDER` documented |

Steps 1 and 2 are worth doing whatever is decided in §8 — and they are worth doing
**before M5**, because M5 writes the extraction call sites and building them behind the
Protocol is cheaper than retrofitting a week-old implementation.
