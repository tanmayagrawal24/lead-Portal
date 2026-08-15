# Proposal — a provider-agnostic LLM layer

**Status:** proposal. No code. Requested by the operator; needs ratification before build.
**Sections it would change:** §2 (non-goals), §3 (stack), §4 (schema, migration 00N), §5.5, §7, §10.
**Motivation:** run the paid extractions against cheaper API keys instead of being fixed to Claude Haiku 4.5.

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

At §7.1's 475 advancing companies/month:

| Call site | Per company | Monthly | Share |
|---|---|---|---|
| AI-visibility — per-search fee, 2 × $0.01 | $0.020 | $9.50 | 29% |
| AI-visibility — ~30k live tokens @ $1/MTok | $0.030 | $14.25 | 46% |
| Extractions 1+2 — ~30k batch tokens @ $0.50/MTok | $0.015 | $7.13 | 23% |
| **Total** | **$0.065** | **$30.88** | |

**The safely-portable share is 23% of the bill.** A provider at half the price saves
~$3.50/month. Stated here so the abstraction is built for the reason that actually
motivates it — lock-in, existing credits, resilience — rather than on an assumed
saving it cannot deliver.

The per-search fee is charged by whoever runs the search and does not move.

## 3. The AI-visibility check is a separate decision, and the default is no

§8 requires every AI-visibility claim in an exported brief to carry its basis:
the literal query, the date, the model, and that web search was enabled. That is
not documentation — it is what makes a comparative claim about a named
competitor lawful under §6 UWG, and §8 makes the export *fail* without it.

So the model is not an implementation detail of this signal. It is part of the
claim. Three consequences:

- **The pitch is "we asked a model your customers might ask."** Swapping to a
  cheaper model to save $0.03 per company weakens the evidence behind the
  sentence the outreach rests on.
- **Search corpora differ between providers.** A different provider is a
  different measurement, not the same measurement more cheaply.
- **`ai.model_used` already records this**, so the schema is ready either way.

**Recommendation:** build the abstraction for call sites 1 and 2. Leave 3 on the
model the brief will name, and treat changing it as a separate, deliberate
decision — with the note that running *several* providers is a defensible
product improvement ("not named by any of three models") whereas running the
cheapest is a weakening.

## 4. The abstraction — a Protocol, not a framework

§2 rules out an LLM framework and that stands. Two fixed schemas do not justify
LangChain any more than they justified ScrapeGraphAI. What is needed is an
interface with one implementation per provider:

```python
class LLMProvider(Protocol):
    name: str                       # 'anthropic' | 'openai' | …
    model: str                      # recorded on every signal and batch row

    def estimate_cost(self, input_tokens: int, output_tokens: int, *, batch: bool) -> float: ...
    def extract(self, schema: type[BaseModel], text: str, system: str) -> Extraction: ...
    def submit_batch(self, requests: list[BatchRequest]) -> str: ...
    def poll_batch(self, provider_batch_id: str) -> BatchResult: ...
```

`Extraction` carries the parsed model plus real token counts, so §7's reservation
and reconciliation work unchanged.

**Selected by configuration, never inferred.** `PORTAL_LLM_PROVIDER` +
`PORTAL_LLM_MODEL`, read at startup, failing loudly on an unknown pair — same
discipline as every other config in this tool.

### What must not change

- **Substring verification (§5.5b) is provider-independent and stays exactly as
  is.** It is what makes swapping safe: a weaker model degrades to more
  `confidence=0` rows, not to confident wrong names. This is the single most
  important sentence in this proposal.
- **`method='llm'`** stays. The UI distinction is about provenance, not vendor.
- **The 60 KB input cap (§7.5)** is a spend bound and applies to every provider.
- **Pre-call reservation (§7.3)** stays; only the price table becomes per-provider.

## 5. Schema

Migration 00N, additive:

```sql
ALTER TABLE llm_batch ADD COLUMN provider TEXT;   -- NULL = pre-migration Anthropic
ALTER TABLE llm_batch ADD COLUMN model    TEXT;
```

`provider_batch_id` is already provider-neutral. Signals need no change:
`ai.model_used` exists, and an extraction's provider belongs on the batch row
that produced it, not repeated per signal.

**A price table per (provider, model, batch?)**, in config and asserted at
startup against a recorded date, so a silent upstream price change cannot make
§7's ceiling meaningless. Prices are facts with expiry dates; treat them as data,
not constants.

## 6. Batch APIs are where this will actually hurt

Not the extractions — the batch plumbing. Providers differ in submission format,
polling semantics, expiry windows and partial-failure behaviour, and §7.4
reserves the whole batch at submission because *a submitted batch is committed
spend*. That invariant must hold per provider and is the thing most likely to be
got wrong.

If a provider has no batch API, `submit_batch` must fall back to serial calls and
**say so in the cost estimate**, since the 50% discount silently disappearing is
a §7 defect, not a performance note.

## 7. How to choose a provider — measure yield, not price

The corpus is already the benchmark. Twelve Impressum pages and thirteen
homepages are on disk, and §5.5b's substring verification gives an objective
score without any human judging German prose:

> **verified-field yield** = fields extracted **and** literally present in the
> cleaned page text ÷ fields a human confirms are on the page

A cheaper model that yields more `confidence=0` rows has moved cost from the API
bill to the operator's review queue. Compare `$ per verified field`, not
`$ per million tokens`.

Run it offline against stored artifacts. Nothing is committed, so the redaction
constraint that forced hand-written fixtures does not apply here.

§10.5 already carries the Ollama-vs-Haiku decision with the stated tradeoff
"German-language extraction quality". **This benchmark is how that open item gets
settled** rather than argued — it applies to Ollama exactly as it applies to a
cheap hosted key.

## 8. Decisions needed before build

1. **Scope.** Extractions only (recommended), or the AI-visibility check too?
2. **Which provider and model, exactly.** "OpenAI Luna" is not a model this
   project can confirm; the plan is model-agnostic, but the config needs a real
   identifier and the price table needs real figures with a date.
3. **§2 and §3 change.** The dependency list is a design decision and this adds
   to it. Record it as a deliberate amendment with its reason, not a quiet
   import.
4. **Does the fallback exist?** If provider A fails mid-run, does the run abort
   or retry on B? Cross-provider fallback multiplies the cost model and the
   evidence story — recommend **abort**, at least for v1.

## 9. Recommended build order

| Step | Deliverable |
|---|---|
| 1 | Protocol + Anthropic implementation, extracted from existing code. **No behaviour change; tests prove it.** |
| 2 | Price table as data, per (provider, model, batch), asserted at startup |
| 3 | Second provider implementation, extractions only |
| 4 | Yield benchmark over the stored corpus; publish the table |
| 5 | Operator picks on the numbers; `PORTAL_LLM_PROVIDER` documented |

Step 1 is worth doing whatever is decided in §8 — it is the seam, and it is
where the Anthropic-specific assumptions currently baked into the extraction path
become visible.
