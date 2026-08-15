# Lead Portal — Spec v0.3 Delta

**Applies to:** `lead-portal-spec-v0.2.md`
**Nature:** six defect fixes. No new features. Scoring weights unchanged except where noted.

Each block below **replaces** the named section wholesale. Apply in order, save the result as `lead-portal-spec-v0.3.md`, delete the `## 10. Resolved review questions` section (superseded by this changelog), and bump the header to v0.3.

## Changelog v0.2 → v0.3

| # | Defect | Section |
|---|---|---|
| D1 | Phase-1 gate discards recoverable A-band leads | §5.4 |
| D2 | Blog ladder rungs overlap; `thin_blog` predicate undefined | §6.2 |
| D3 | Cost ceiling is per-run, so monthly spend is unbounded; batch reconciliation undefined | §4, §7 |
| D4 | AI-visibility token estimate understated ~10×; per-search billing unaccounted | §5.5c |
| D5 | Nondeterministic pivot in `company_profile`; `fetched_at` never updates on unchanged pages | §4 |
| D6 | Idempotency contract overclaims across run boundaries | §5 preamble |
| D7 | Comparative claims in the research brief lack a verifiable basis | §8, §9 |

---

## D1 — replaces §5.4

### 5.4 score --phase 1

Pure function over `company_profile`. Costs nothing.

**The Phase-2 advance gate is not the B band.** Phase 2 can add points that Phase 1 cannot observe, so gating on the Phase-1 band would permanently discard companies whose final score would have been A. A company scoring 54 in Phase 1 with +35 available in Phase 2 is an 89 — a clear A that would never be looked at.

```
PHASE2_MAX_POINTS = sum of the maximum positive points from all rules
                    whose inputs are Phase-2-only signals.
                    Computed from the ruleset at startup — never hardcoded.
                    Under ruleset v3: qual.own_brand (+10)
                                    + opp.ai_invisible (+15)
                                    + opp.slow_site (+10)  = 35

ADVANCE_THRESHOLD = B_band_floor − PHASE2_MAX_POINTS
                  = 55 − 35 = 20
```

Companies with `phase1_total >= ADVANCE_THRESHOLD` advance to Phase 2. Everything below stops, and its `phase=1` score row is final unless manually promoted in the UI.

A ruleset change that adds a Phase-2 rule automatically lowers the threshold. Assert at startup that `PHASE2_MAX_POINTS` was derived from the live ruleset and not from a stale constant — fail loudly if the two disagree.

**Cost consequence, stated honestly:** this admits substantially more companies to Phase 2 than a band-B gate would. The §7 estimate is revised accordingly. The two-phase split still saves money — it excludes the clear no-hopers — but it is no longer a 60–70% reduction. Expect 55–70% of discovered companies to advance.

**Score direction:** Phase 2 can also *lower* a score (`neg.has_agency` may fire on `HomepageExtract.agency_credit` where the footer regex missed it). The gate concerns maximum upside only; a Phase-2 score below its Phase-1 predecessor is expected and correct.

Record the gate decision per company as a signal (`gate.phase2_admitted`, `value_num` 0/1) so a company that stopped just under the line is auditable rather than invisible.

---

## D2 — replaces the blog ladder table in §6.2

### 6.2 Opportunity (how weak is their content marketing?)

**Blog ladder — evaluated as an ordered chain, first match wins, evaluation stops.** Written as a chain rather than a table because the table format is what allowed overlapping predicates in v0.2.

```
days_since_newest = (today − content.blog_last_post).days      # NULL if no date parsed
post_count        = content.blog_post_count

if not blog_exists:
    → opp.no_blog          +25
elif blog_last_post is NULL:
    → no rung fires; set needs_review = 'blog_date_unparseable'
elif days_since_newest > 365:
    → opp.blog_stale       +20
elif post_count < 10:
    → opp.thin_blog        +12
elif days_since_newest >= 180:
    → opp.blog_slowing     +10
else:
    → no rung fires (blog is current and substantial)
```

The `blog_last_post is NULL` branch is new and deliberate. A blog index whose dates cannot be parsed is an unknown, not a stale blog. Guessing here would put a false claim into a letter. Route it to human review instead — this is the same principle as `confidence=0` on unverified LLM extractions (§5.5b).

`opp.thin_blog` now has a precise predicate: fewer than 10 posts, newest post within the last 365 days. The undefined term "active-ish" is removed.

**Conditional and independent rules — unchanged from v0.2:**

| rule_id | Condition | Points |
|---|---|---|
| `opp.no_article_schema` | Blog **exists** and no `Article`/`BlogPosting` in JSON-LD on blog pages. Never fires together with `opp.no_blog`. | +8 |
| `opp.no_product_schema` | No `Product` in JSON-LD on a product page | +10 |
| `opp.ai_invisible` | `ai.queries_checked >= 2` and `ai.brand_mentions = 0` (Phase 2) | +15 |
| `opp.slow_site` | Lighthouse performance < 50 (Phase 2) | +10 |
| `opp.de_only` | Single distinct language (locale variants don't count), expansion angle | +5 |

---

## D3 — replaces §7, and adds one table to §4

### New table for §4

```sql
-- ─────────────────────────────────────────────────────────────
-- Batch API submissions. A submitted batch is committed spend
-- that may return after the submitting run has closed.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE llm_batch (
    id                INTEGER PRIMARY KEY,
    provider_batch_id TEXT NOT NULL UNIQUE,
    run_id            INTEGER NOT NULL REFERENCES run(id),
    purpose           TEXT NOT NULL,   -- 'impressum' | 'homepage'
    request_count     INTEGER NOT NULL,
    est_cost_usd      REAL NOT NULL,   -- reserved at submission
    actual_cost_usd   REAL,            -- written at reconciliation
    status            TEXT NOT NULL DEFAULT 'submitted'
                      CHECK (status IN ('submitted','completed','reconciled','failed','expired')),
    submitted_at      TEXT NOT NULL,
    reconciled_at     TEXT
);
CREATE INDEX idx_batch_status ON llm_batch(status);
```

New CLI command: `python -m portal reconcile` — polls every `llm_batch` row with status `submitted`, writes returned extractions as signals, sets `actual_cost_usd`, moves status to `reconciled`. Safe to run repeatedly. Must be run before `score --phase 2` produces trustworthy output; `score --phase 2` warns loudly if unreconciled batches exist for the companies being scored.

### 7. Cost controls

Non-negotiable, implemented as code, not as discipline.

1. **Google Cloud Console quota cap** set below the free SKU threshold for every Places SKU. Make it physically impossible to be billed.

2. **Rolling 30-day ceiling — the outer bound.** Before any paid call:
   ```sql
   SELECT COALESCE(SUM(est_cost_usd), 0) FROM run
   WHERE started_at > datetime('now','-30 days');
   ```
   Abort if this exceeds `MONTHLY_CEILING_USD` (default `$25`). This is the control that actually bounds spend. The per-run ceiling below does not: `run.est_cost_usd` resets on every invocation, so ten aborted-and-retried runs cost ten times the per-run limit. v0.2 claimed runaway spend was impossible; without this check it was not.

3. **Per-run ceiling with pre-call reservation.** Before every LLM call, the *estimated* cost is added to `run.est_cost_usd` and checked against the per-run ceiling (default `$5.00`). After the response, the estimate is reconciled to actual usage. A crash between call and write can only over-count, never under-count — the failure mode is a conservatively aborted run, not silent overspend.

4. **Batch submissions reserve the whole batch at submission time.** A submitted batch is committed spend regardless of whether the process survives to read the result. Reserve into both `llm_batch.est_cost_usd` and `run.est_cost_usd` before the submit call returns.

5. **Input size cap.** LLM inputs are cleaned and capped at 60 KB (§5.5b). Closes the unbounded-spend path of multi-megabyte Shopware homepages.

6. **Content-hash short-circuit.** Unchanged page → no LLM call. Extraction keyed to `artifact.content_hash`, effective across runs *and* within a resumed run.

7. **Two-phase gating** (§5.4) — restricts paid signals to companies above `ADVANCE_THRESHOLD`.

8. **Web search accounting.** `run.web_searches` counts searches issued. See §5.5c: the per-search charge is billed separately from tokens and must be included in the reservation once the rate is confirmed.

9. Every API key from environment variables. `.env` in `.gitignore`. No keys in the repo, ever.

**Expected steady-state: $20–35/month at ~500 discovered companies/month.** Revised upward from v0.2's $15 for two reasons: the corrected advance threshold (D1) admits more companies to Phase 2, and the corrected AI-visibility token estimate (D4) is roughly ten times v0.2's. The `$25` default ceiling in control 2 is deliberately close to this figure — it should bite occasionally, which is how you find out the model is wrong.

---

## D4 — replaces §5.5(c)

**(c) AI-visibility check — the differentiating signal.**

For each Phase-2 company, derive **2** German category queries (configurable, default 2, hard maximum 3) from `one_line_offer` and `product_categories` — e.g. *"beste Ultraschallzahnbürste"*, *"Ultraschallzahnbürste Test"*. Run each against Claude with web search enabled and a fixed prompt asking which brands or shops it would recommend.

Record as signals:

| key | type | content |
|---|---|---|
| `ai.queries_checked` | num | how many queries actually completed |
| `ai.brand_mentions` | num | in how many the company's brand or domain appeared |
| `ai.competitors_mentioned` | text | comma-separated brands that did appear |
| `ai.query_text` | text | the literal queries run, pipe-separated |
| `ai.checked_at` | date | date of the check |
| `ai.model_used` | text | model ID, e.g. `claude-haiku-4-5-20251001` |

The last three exist solely so the research brief can state its basis (§8). They are not optional and not for debugging — without them the finding is an unverifiable comparative claim about a named third party.

**Cost — corrected.** v0.2 estimated ~2k tokens per query. That was wrong by roughly an order of magnitude: a web-search-enabled call injects search results into context, realistically **10–20k input tokens per query**. Two queries per company is ~30k input tokens, roughly **$0.03–0.04 per company on Haiku 4.5**, before the separate per-search charge.

**Before implementing, confirm the web search tool's billing rate** in the current Anthropic API pricing documentation. It is charged per search in addition to tokens and is not in the token-based reservation model. Until confirmed, treat it as unknown, count searches in `run.web_searches`, and set `MONTHLY_CEILING_USD` conservatively. Do not ship this sub-stage with an unverified cost.

**Methodological constraint.** This measures one model, on one date, with web search enabled. It is a defensible *baseline*, not a statement about AI systems in general. §8 and §9 govern how it may be worded in anything sent to a prospect.

---

## D5 — two corrections within §4

**(a) `company_profile` VIEW — deterministic ordering.** Replace the window function line:

```sql
-- was:  ROW_NUMBER() OVER (PARTITION BY company_id, key ORDER BY observed_at DESC) AS rn
           ROW_NUMBER() OVER (PARTITION BY company_id, key
                              ORDER BY observed_at DESC, id DESC) AS rn
```

Signals written within the same second share an `observed_at`. Without the `id` tiebreaker, which one the view surfaces is arbitrary and can differ between queries — which contradicts the reproducibility guarantee the whole tool rests on.

**(b) `artifact` — preserve freshness on unchanged content.** Add a column and change the write path:

```sql
ALTER TABLE artifact ADD COLUMN last_checked_at TEXT;   -- fetched_at = first seen; last_checked_at = most recent verification
```

All artifact writes use:

```sql
INSERT INTO artifact (company_id, kind, url, http_status, content_hash, body_path, bytes, fetched_at, last_checked_at)
VALUES (?,?,?,?,?,?,?,?,?)
ON CONFLICT (company_id, kind, content_hash) DO UPDATE
SET last_checked_at = excluded.last_checked_at,
    http_status     = excluded.http_status;
```

With `INSERT OR IGNORE`, an unchanged page never updated `fetched_at`, so "when did I last check this" was unanswerable — and the 30-day PageSpeed cache rule in §5.3 depends on exactly that.

---

## D6 — replaces the idempotency paragraph in §5

**Idempotency contract.** Re-running a stage after a mid-run crash must not repeat any paid API call and must not create duplicate artifacts. It does **not** guarantee a byte-identical database, and v0.2 overstated this.

What is guaranteed:

- **No duplicate paid calls.** Extraction is keyed to `artifact.content_hash`; a hash with signals for the current `ruleset_version` is skipped. Batch submissions are recorded in `llm_batch` before the provider call returns.
- **No duplicate artifacts.** `uq_artifact_identity` plus the upsert in D5(b).
- **Scoring is a pure recompute.** `score --phase N` can be re-run any number of times at zero cost; `uq_score_identity` makes the write idempotent within a run.

What is not guaranteed: a crashed-then-restarted run gets a **new `run_id`**, so `uq_signal_identity` (which includes `run_id`) does not deduplicate across the restart. Deterministic signals will be re-observed under the new run. This is harmless — `signal` is append-only by design and `company_profile` resolves to the latest observation — but it means the database is not byte-identical to a clean run.

To resume under the original run instead, use `python -m portal <stage> --resume <run_id>`. This reuses the run row, so the unique index applies and the cost ledger stays in one place. Prefer `--resume` after a crash; use a fresh run for a genuine re-scrape.

---

## D7 — adds to §8, replaces the brief paragraph in §9

### Addition to §8. Compliance requirements

**Comparative claims in outbound material.** The research brief names competitors — *"genannt werden stattdessen: …"*. That is comparative advertising, lawful under §6 UWG only where the comparison is objective and verifiable. Two consequences, both binding on the export:

Every AI-visibility statement in an exported brief must carry its basis inline: the literal query text, the date of the check, the model used, and the fact that web search was enabled. These come from `ai.query_text`, `ai.checked_at`, `ai.model_used`. An export missing any of them must fail, not degrade gracefully — a brief that asserts a competitor comparison without its basis is the failure this rule exists to prevent.

Wording is constrained to what was measured. Permitted: *"Bei 2 von 2 geprüften KI-Abfragen wurden Sie nicht genannt."* Not permitted: *"Sie sind in KI-Systemen unsichtbar."* One model checked once does not support a general claim, and the gap between the two is the same category of error as a Heilversprechen — asserting more than the evidence carries.

### Replaces the "Research brief export" paragraph in §9

**Research brief export** (per company, German, Markdown). Findings section built from `score_component.reason` sentences. KI-Sichtbarkeit section built from the `ai.*` signals, in the format proven in live pitches, with a mandatory basis line:

> **KI-Sichtbarkeit**
> Geprüft am 15.08.2026 über Claude (`claude-haiku-4-5`) mit aktivierter Websuche.
> Abfragen: „beste Ultraschallzahnbürste" · „Ultraschallzahnbürste Test"
> Ergebnis: Bei 2 von 2 Abfragen wurde Ihre Marke nicht genannt.
> Genannt wurden stattdessen: Emmi-Dent, Philips Sonicare, Curaprox.

The export function asserts the presence of `ai.query_text`, `ai.checked_at` and `ai.model_used` before writing, and raises if any is missing. Briefs for companies that did not reach Phase 2 omit the KI-Sichtbarkeit section entirely rather than rendering it empty.
