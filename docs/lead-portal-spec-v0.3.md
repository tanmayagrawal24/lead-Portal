# Lead Portal — Technical Specification v0.3

**Owner:** Tanmay Agrawal / Creative Potatoes
**Status:** v0.2 with the v0.3 delta applied. Single source of truth for implementation.
**Supersedes:** v0.2 (retained at `docs/lead-portal-spec-v0.2.md` for provenance)

> If implementation reveals this spec is wrong, change the spec first, then the code. Never let them diverge.

## Changelog v0.2 → v0.3

| # | Defect | Section |
|---|---|---|
| D1 | Phase-1 gate discards recoverable A-band leads | §5.4 |
| D2 | Blog ladder rungs overlap; `thin_blog` predicate undefined | §6.2 |
| D3 | Cost ceiling is per-run, so monthly spend is unbounded; batch reconciliation undefined | §4, §5.7, §7 |
| D4 | AI-visibility token estimate understated ~10×; per-search billing unaccounted | §5.5c |
| D5 | Nondeterministic pivot in `company_profile`; `fetched_at` never updates on unchanged pages | §4 |
| D6 | Idempotency contract overclaims across run boundaries | §5 preamble |
| D7 | Comparative claims in the research brief lack a verifiable basis | §8, §9 |

Also in v0.3: the ruleset version bumps to **v3** (predicate changes in §6.2; no weight changes). v0.2's `§10 Resolved review questions` is deleted, superseded by this changelog. v0.2's `§11 Open decisions` becomes `§10`.

### Amendments after third-pass review — 2026-08-15

Resolutions to findings raised in `v0.3-review-findings.md`. Applied before M0.

| # | Finding | Resolution | Section |
|---|---|---|---|
| B2 | `needs_review_reason` is one column; three soft flags can co-occur | Reasons move to a `review_flag` table, one row per (company, reason). `company.needs_review` survives as a boolean maintained by trigger, so §9's filter and `idx_company_review` still work; `company.needs_review_reason` is dropped. | §4, §6.2, §6.4, §9 |
| B4 | `run_id` for signals written by `reconcile` was undefined | Reconciled signals carry the **submitting** run's id (`llm_batch.run_id`), not the reconciling run's. | §5.6 |
| B3.1 | Cost-ledger ownership across the batch boundary | `actual_cost_usd` reconciles against the submitting run's `est_cost_usd`, where the reservation was made. | §5.6 |
| A5.1 | Product sample must be stable across runs | **Tier 0 reuse:** an existing `product_page` artifact is re-sampled while it still returns HTTP 200; on 404/error it is discarded and selection falls through to Tier 1/2. §4 requires that re-scoring never need a refetch, so the evidence a score points at must not move under it. | §5.2 |
| A5.2 | Candidate sources undefined | Three tiers: platform product sitemap (union of **all** shards, `.xml.gz` decompressed) → path-pattern candidates from fetched sitemaps → product-pattern links on the homepage. | §5.2 |
| A5.3 | Ordering undefined | Lexicographic minimum by **Unicode code point**, never locale collation. Chosen over document order because Shopware re-shards product sitemaps on a schedule, making document order stable only by accident. | §5.2 |
| A5.4 | Non-product URLs contaminate the candidate set | Four filters: reject query strings; require a path segment after the pattern; reject category/listing patterns; reject anything under the detected blog path. | §5.2 |
| A5.5 | Unchecked sites scored as "no schema" | Zero candidates, or a selected sample whose fetch fails, writes **no** `schema.product_present` signal — not `0`. | §5.2 |
| A5.6 | `opp.no_product_schema` could fire unmeasured | **Guard:** the rule fires only when `schema.product_present` was written from a product page fetched with HTTP 200; absent that signal it fires in neither direction. Clarifies what the existing predicate presupposes — no weight or threshold changes. **✅ Ratified 2026-08-15.** | §6.2 |
| A5.7 | Selection was unauditable from the database | New signal key `catalog.product_sample_url` (text, unscored, no schema change); `evidence_url` is the sitemap or homepage it was read from. **✅ Ratified 2026-08-15.** | §5.2, §5.3 |

### Amendments after the M1 review — 2026-08-15

Rulings on the questions M1 parked (`docs/m1-handoff.md` §7) and on one defect the review found. Applied on `m1-fetch`.

| # | Question or defect | Resolution | Section |
|---|---|---|---|
| M1.1 | Redirect hops bypassed the rate limiter and could reach a host whose robots.txt was never read | Redirects are followed one hop at a time, each waiting on the limiter; a hop that changes host is followed only after that host's robots.txt has been fetched and consulted, and is otherwise refused and recorded. | §5.2 |
| M1.2 | `Crawl-delay` was ignored | Honour `max(1.0, Crawl-delay)`, capped at 10 s; above the cap the domain is skipped and the reason recorded. | §5.2 |
| M1.3 | `defusedxml` for third-party sitemap XML | **Declined — no new dependency.** A sitemap whose bytes contain `<!DOCTYPE` or `<!ENTITY` is refused before parsing, which removes the entity-expansion class outright. | §5.2 |
| M1.4 | `/p/` as a Tier 2 product pattern | **Dropped** until observed in the wild. The errors are asymmetric: a false positive wrongly awards +10, a false negative only leaves a signal unwritten, which A5.5 already handles. | §5.2 |
| M1.5 | `signal` writes used `INSERT OR IGNORE`, which swallows CHECK violations | Idiom changes to `ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING`, everywhere signals are written. Same fix `review_flag` already carries. | §4, §5.6 |
| M1.6 | `uvicorn` absent from the named stack | **Approved for M4.** FastAPI cannot serve itself; a gap in the stack list, not scope creep. | §3 |
| M1.7 | Failure-artifact and robots-exclusion policy existed only in code | Both written into the spec: failure rows update in place, and "required paths" is defined for the two paths that are not knowable up front. | §5.2 |
| M1.8 | Apex and www were separate politeness budgets for one server, so an apex→www redirect ran at 2 req/s | The politeness key strips `www.` and keeps the port; other subdomains stay separate, recorded as accepted. The robots.txt key stays origin-based, so each name is still asked for its own file. | §5.2 |

Remaining findings (A1–A4, B1, B3.2–B3.3, B5–B7, C1–C4) are still open and are not required by M0 or M1.

## Changelog v0.1 → v0.2 (retained for the record)

1. **ScrapeGraphAI removed.** Both extractions use the Anthropic SDK directly with tool-use structured output. (§5.5)
2. **Scoring restructured.** Blog rules became a mutually exclusive ladder; schema rules conditional; the 45-point cap removed. (§6.2)
3. **Two-phase pipeline.** Phase 1 = free deterministic signals for everyone; Phase 2 = paid signals for advancing companies. (§5)
4. **New signal: AI/GEO visibility.** (§5.5c, §6.2)
5. **New signal: review presence** as a free product-strength proxy. (§5.3)
6. **Blog date detection fixed.** Sitemap `<lastmod>` demoted to a hint. (§5.3)
7. **Exclusions softened.** Two-tier exclusion, CH-aware. (§6.4)
8. **Einzelunternehmen fixed.** `qual.owner_operated` fires on legal form as well as GF count. (§6.1)
9. **Idempotency hardened.** UNIQUE constraints, pre-call cost reservation, hash-keyed extraction. (§4, §5, §7)
10. **`company_profile` SQL view** added. (§4)
11. **`meta.description_length` dropped** from scoring. (§5.3)

---

## 1. Purpose

A locally-run tool that discovers candidate companies in the DACH region, gathers **evidence** about the state of their content marketing from public sources, assigns a reproducible priority score, and presents a reviewable list.

The output of a good run is not "here are 200 emails." It is: *"Schulz Manufaktur GmbH, Shopware shop, owner-operated, last blog post March 2023, no Article schema, invisible in AI answers for its own category — priority A."*

Every number in the score must trace back to a stored artifact with a URL and a fetch timestamp.

The exported research brief per company is a first-contact asset: it must read like the KI-Sichtbarkeits-Baseline-Report format that has already worked in live pitches — concrete findings, evidenced, in German.

## 2. Non-goals

Explicitly out of scope. Do not implement these, and do not let scope creep add them:

- **No email sending.** Not a feature, not a stub, not "for later." See §8.
- **No LinkedIn, Xing, or Instagram scraping.** ToS violation, actively blocked, not worth the engineering.
- **No multi-user support, auth, or deployment.** Single operator, localhost, SQLite.
- **No CRM.** Outreach tracking is a single flat table, nothing more.
- **No paid contact database integration** (Apollo, ZoomInfo, Cognism). The Impressum is a better and cheaper source for DACH.
- **No ScrapeGraphAI or other LLM-framework dependency.** Two fixed-schema extractions do not justify a framework. Direct SDK calls only.

## 3. Architecture

```
                  ┌──────────────┐
  Places API ───▶ │  discovery   │ ──▶ company (domain, name, city)
  Seed CSV   ───▶ └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │    fetch     │ ──▶ artifact (raw HTML/XML, hashed)
                  └──────────────┘     robots.txt → homepage → sitemap.xml
                         │              → impressum → blog index → product page
                         ▼
                  ┌──────────────┐
                  │  extract P1  │ ──▶ deterministic signals (free)
                  └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  score  P1   │ ──▶ provisional band + gate decision
                  └──────────────┘
                         │  phase1_total >= ADVANCE_THRESHOLD
                         ▼
                  ┌──────────────┐     PageSpeed API
                  │  extract P2  │ ──▶ Impressum + homepage extraction (Anthropic SDK, Batch)
                  └──────────────┘     AI-visibility check (Anthropic SDK + web search)
                         │
                         ▼
                  ┌──────────────┐
                  │  reconcile   │ ──▶ batch results → signals, actual cost
                  └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  score  P2   │ ──▶ final score + score_component
                  └──────────────┘     (rule_id, points, letter-ready German reason)
                         │
                         ▼
                  FastAPI + HTMX UI  (localhost:8000)
```

**Stack:** Python 3.11+, FastAPI, SQLite (WAL mode), `httpx`, `selectolax` for parsing, `anthropic` SDK, Jinja2 + HTMX for the UI, and `uvicorn` **from M4 only** — FastAPI is a framework and cannot serve itself, so `portal serve` (§9) needs an ASGI server. It is listed here so it is not mistaken for scope creep later. No build step, no Node, no Docker.

The list is closed. Anything not named here is a decision to be taken deliberately, not a convenience import — see M1.3 above, where a security concern was answered inside the stack rather than by adding to it.

**Repo layout:** single self-contained repository. No fork of any scraping framework exists or is needed.

## 4. Database schema

SQLite. Migrations via plain numbered `.sql` files applied in order; no ORM migration framework.

```sql
-- ─────────────────────────────────────────────────────────────
-- Core entity
-- ─────────────────────────────────────────────────────────────
CREATE TABLE company (
    id              INTEGER PRIMARY KEY,
    domain          TEXT NOT NULL UNIQUE,      -- normalised: lowercase, no scheme, no www
    legal_name      TEXT,
    legal_form      TEXT,                      -- 'GmbH'|'GmbH & Co. KG'|'e.K.'|'Einzelunternehmen'|'GbR'|'AG'|'UG'|…
    city            TEXT,
    postal_code     TEXT,
    country         TEXT CHECK (country IN ('DE','AT','CH')),
    discovery_source TEXT NOT NULL,            -- 'places' | 'seed_csv' | 'manual'
    discovery_query TEXT,                      -- the query that surfaced it, for provenance
    discovered_at   TEXT NOT NULL,             -- ISO8601 UTC
    excluded        INTEGER NOT NULL DEFAULT 0,
    excluded_reason TEXT,                      -- never exclude silently; always record why
    needs_review    INTEGER NOT NULL DEFAULT 0 -- derived: 1 iff an unresolved review_flag exists.
                                               -- Maintained by trigger, never written directly.
);
CREATE INDEX idx_company_excluded ON company(excluded);
CREATE INDEX idx_company_review ON company(needs_review);

-- ─────────────────────────────────────────────────────────────
-- B2: soft review flags. §6.4's three reasons are independent and
-- can co-occur, so one row per (company, reason) rather than one
-- TEXT column shared by three writers in three different stages.
--
-- Raising a flag is idempotent, so `score --phase 1` stays a zero-cost
-- repeatable recompute (§5.4). The idiom is a targeted DO NOTHING:
--
--   INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at)
--   VALUES (?,?,?,?) ON CONFLICT (company_id, reason) DO NOTHING;
--
-- NOT `INSERT OR IGNORE`, which suppresses CHECK violations as well as
-- uniqueness conflicts — a renamed or misspelled reason would then be
-- dropped silently rather than raising, which is the opposite of the
-- fail-loudly rule the CHECK is there to enforce.
--
-- Resolution is sticky — see §6.4, which states the rule and the
-- reasoning. The unique index is what implements it; §6.4 is what
-- decides it.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE review_flag (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL CHECK (reason IN (
                      'no_impressum','possible_marketplace_only','blog_date_unparseable')),
    raised_run_id INTEGER NOT NULL REFERENCES run(id),
    raised_at     TEXT NOT NULL,
    resolved_at   TEXT,                        -- NULL = not yet reviewed
    resolved_by_human INTEGER CHECK (resolved_by_human IN (0,1)),
                                               -- 1 = a human dismissed it; 0 = the pipeline cleared it.
                                               -- NULL passes: a SQLite CHECK holds unless it evaluates
                                               -- false, and the paired CHECK below governs the NULL case.
    resolved_note TEXT,
    CHECK ((resolved_at IS NULL     AND resolved_by_human IS NULL)
        OR (resolved_at IS NOT NULL AND resolved_by_human IS NOT NULL))
);
CREATE UNIQUE INDEX uq_review_flag ON review_flag(company_id, reason);
CREATE INDEX idx_review_flag_open ON review_flag(company_id) WHERE resolved_at IS NULL;

-- `company.needs_review` is a cache of "has an unresolved flag", kept
-- correct by trigger so there is exactly one write path: write the flag,
-- the boolean follows. §9's filter stays an indexed column lookup rather
-- than an EXISTS subquery on every page load.
-- All three correlate the subquery to `company.id` rather than to NEW/OLD, so
-- the UPDATE trigger can recompute both sides with one statement: an update
-- that moved a flag between companies would otherwise leave the company it
-- left behind still marked.
CREATE TRIGGER trg_review_flag_after_insert AFTER INSERT ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ) WHERE id = NEW.company_id;
END;

CREATE TRIGGER trg_review_flag_after_update AFTER UPDATE ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ) WHERE id IN (OLD.company_id, NEW.company_id);
END;

CREATE TRIGGER trg_review_flag_after_delete AFTER DELETE ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ) WHERE id = OLD.company_id;
END;

-- ─────────────────────────────────────────────────────────────
-- Raw fetched pages. Kept so re-scoring never costs a refetch.
-- D5(b): fetched_at = first seen; last_checked_at = most recent verification.
-- On a pre-existing database this column arrives as:
--   ALTER TABLE artifact ADD COLUMN last_checked_at TEXT;
-- ─────────────────────────────────────────────────────────────
CREATE TABLE artifact (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,             -- 'robots'|'homepage'|'sitemap'|'impressum'|'blog_index'|'product_page'
    url             TEXT NOT NULL,
    http_status     INTEGER,
    content_hash    TEXT,                      -- sha256 of body; extraction is keyed to this
    body_path       TEXT,                      -- relative path on disk; bodies are NOT stored in SQLite
    bytes           INTEGER,
    fetched_at      TEXT NOT NULL,             -- first time this exact content was seen
    last_checked_at TEXT,                      -- most recent time this URL was verified
    error           TEXT
);
CREATE INDEX idx_artifact_company_kind ON artifact(company_id, kind);
-- Idempotency: a re-fetch of identical content must not create a second row.
CREATE UNIQUE INDEX uq_artifact_identity ON artifact(company_id, kind, content_hash)
    WHERE content_hash IS NOT NULL;
```

**D5(b) — all artifact writes use this upsert.** With `INSERT OR IGNORE`, an unchanged page never updated `fetched_at`, so "when did I last check this" was unanswerable — and the 30-day PageSpeed cache rule in §5.3 depends on exactly that.

```sql
INSERT INTO artifact (company_id, kind, url, http_status, content_hash, body_path, bytes, fetched_at, last_checked_at)
VALUES (?,?,?,?,?,?,?,?,?)
ON CONFLICT (company_id, kind, content_hash) WHERE content_hash IS NOT NULL DO UPDATE
SET last_checked_at = excluded.last_checked_at,
    http_status     = excluded.http_status;
```

> The `WHERE content_hash IS NOT NULL` on the conflict target is not optional and is not decoration. `uq_artifact_identity` is a **partial** index, and SQLite matches a conflict target to a partial index only when the predicate is repeated verbatim. Without it every artifact write raises `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint`. Found in M1; v0.3's original snippet omitted it and could never have run.

```sql
-- ─────────────────────────────────────────────────────────────
-- Signals: append-only, one row per observation, always evidenced.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE signal (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    run_id        INTEGER NOT NULL REFERENCES run(id),
    key           TEXT NOT NULL,               -- see §6 for the controlled vocabulary
    value_text    TEXT,
    value_num     REAL,
    value_date    TEXT,
    method        TEXT NOT NULL CHECK (method IN ('deterministic','llm')),
    confidence    REAL,                        -- NULL for deterministic; 0-1 for llm
    evidence_url  TEXT NOT NULL,               -- the page this was read off
    artifact_id   INTEGER REFERENCES artifact(id),
    observed_at   TEXT NOT NULL
);
CREATE INDEX idx_signal_company_key ON signal(company_id, key);
-- Idempotency: re-running a crashed extract stage must not duplicate observations
-- within the same run. See §5 (D6) for what this does and does not guarantee
-- across run boundaries.
--
-- M1.5 — every write to signal uses this idiom:
--
--   INSERT INTO signal (…) VALUES (…)
--   ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING;
--
-- NOT `INSERT OR IGNORE`. `OR IGNORE` suppresses the CHECK on `method` as well
-- as the uniqueness conflict, so a typo'd or renamed method would be dropped in
-- silence — a signal that was never written, indistinguishable from one that
-- was never observed. The targeted DO NOTHING dedupes and nothing more. This is
-- the same trap review_flag avoids, for the same reason.
CREATE UNIQUE INDEX uq_signal_identity ON signal(run_id, company_id, key, evidence_url);

-- ─────────────────────────────────────────────────────────────
-- Wide read model for scoring. A VIEW, not a table: no sync, no
-- second write path. Pivots the LATEST observation per key.
-- Add a column here when a new signal key enters the scoring rules;
-- keys not listed remain queryable via the signal table.
--
-- D5(a): the ORDER BY carries an `id DESC` tiebreaker. Signals written
-- within the same second share an observed_at; without the tiebreaker,
-- which one the view surfaces is arbitrary and can differ between
-- queries — contradicting the reproducibility guarantee the tool rests on.
-- ─────────────────────────────────────────────────────────────
CREATE VIEW company_profile AS
WITH latest AS (
    SELECT s.*,
           ROW_NUMBER() OVER (PARTITION BY company_id, key
                              ORDER BY observed_at DESC, id DESC) AS rn
    FROM signal s
)
SELECT
    c.id AS company_id,
    c.domain,
    c.country,
    c.legal_form,
    MAX(CASE WHEN l.key='platform.detected'          THEN l.value_text END) AS platform,
    MAX(CASE WHEN l.key='content.blog_last_post'     THEN l.value_date END) AS blog_last_post,
    MAX(CASE WHEN l.key='content.blog_post_count'    THEN l.value_num  END) AS blog_post_count,
    MAX(CASE WHEN l.key='content.blog_exists'        THEN l.value_num  END) AS blog_exists,
    MAX(CASE WHEN l.key='schema.article_present'     THEN l.value_num  END) AS article_schema,
    MAX(CASE WHEN l.key='schema.product_present'     THEN l.value_num  END) AS product_schema,
    MAX(CASE WHEN l.key='i18n.hreflang_count'        THEN l.value_num  END) AS hreflang_count,
    MAX(CASE WHEN l.key='perf.lighthouse_performance' THEN l.value_num END) AS lighthouse_perf,
    MAX(CASE WHEN l.key='agency.footer_credit'       THEN l.value_text END) AS agency_credit,
    MAX(CASE WHEN l.key='catalog.product_url_count'  THEN l.value_num  END) AS product_url_count,
    MAX(CASE WHEN l.key='reviews.count'              THEN l.value_num  END) AS review_count,
    MAX(CASE WHEN l.key='reviews.trusted_shops'      THEN l.value_num  END) AS trusted_shops,
    MAX(CASE WHEN l.key='impressum.gf_count'         THEN l.value_num  END) AS gf_count,
    MAX(CASE WHEN l.key='impressum.owner_named'      THEN l.value_num  END) AS owner_named,
    MAX(CASE WHEN l.key='ai.brand_mentions'          THEN l.value_num  END) AS ai_brand_mentions,
    MAX(CASE WHEN l.key='ai.queries_checked'         THEN l.value_num  END) AS ai_queries_checked
FROM company c
LEFT JOIN latest l ON l.company_id = c.id AND l.rn = 1
GROUP BY c.id;

-- ─────────────────────────────────────────────────────────────
-- Scoring. Recomputable from signals at zero API cost.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE score (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    run_id          INTEGER NOT NULL REFERENCES run(id),
    phase           INTEGER NOT NULL DEFAULT 1 CHECK (phase IN (1,2)),
    total           INTEGER NOT NULL,
    band            TEXT NOT NULL CHECK (band IN ('A','B','C','D')),
    ruleset_version TEXT NOT NULL,             -- bump when weights change; old scores stay readable
    computed_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_score_identity ON score(run_id, company_id, phase);

CREATE TABLE score_component (
    id        INTEGER PRIMARY KEY,
    score_id  INTEGER NOT NULL REFERENCES score(id) ON DELETE CASCADE,
    rule_id   TEXT NOT NULL,                   -- e.g. 'opp.blog_stale'
    points    INTEGER NOT NULL,
    reason    TEXT NOT NULL                    -- German, letter-ready: "Letzter Blogbeitrag: März 2023."
);
```

> `score_component.reason` is the whole point of the tool. It should be written so it can be pasted, near-verbatim, into a Brief. Not `blog_stale=true`.

```sql
-- ─────────────────────────────────────────────────────────────
-- Contacts. Separate table so GDPR purge is a single DELETE.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE contact (
    id                INTEGER PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    full_name         TEXT,
    role              TEXT,                    -- 'Geschäftsführer', 'Inhaber', …
    email             TEXT,
    phone             TEXT,
    postal_address    TEXT,
    source_url        TEXT NOT NULL,           -- must be the Impressum URL
    collected_at      TEXT NOT NULL,
    art14_notice_sent_at TEXT,                 -- GDPR Art. 14 information duty
    purge_after       TEXT NOT NULL            -- collected_at + 12 months; enforced by `portal purge` (§8)
);

CREATE TABLE outreach (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL CHECK (channel IN ('post','phone')),  -- see §8
    occurred_at TEXT NOT NULL,
    notes       TEXT,
    outcome     TEXT CHECK (outcome IN ('no_response','interested','declined','meeting','client'))
);

CREATE TABLE run (
    id             INTEGER PRIMARY KEY,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    stage          TEXT NOT NULL,              -- 'discover'|'fetch'|'extract_p1'|'score_p1'|'extract_p2'|'reconcile'|'score_p2'
    companies_seen INTEGER DEFAULT 0,
    places_calls   INTEGER DEFAULT 0,
    web_searches   INTEGER DEFAULT 0,          -- searches issued; billed separately from tokens (§7.8)
    llm_input_tokens  INTEGER DEFAULT 0,
    llm_output_tokens INTEGER DEFAULT 0,
    est_cost_usd   REAL DEFAULT 0,             -- reserved BEFORE each LLM call, reconciled after
    aborted_reason TEXT
);

-- ─────────────────────────────────────────────────────────────
-- D3: Batch API submissions. A submitted batch is committed spend
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

## 5. Pipeline stages

Each stage is a separate CLI command, independently re-runnable: `python -m portal discover`, `… fetch`, `… extract-p1`, `… score --phase 1`, `… extract-p2`, `… reconcile`, `… score --phase 2`, `… serve`.

**Idempotency contract (D6).** Re-running a stage after a mid-run crash must not repeat any paid API call and must not create duplicate artifacts. It does **not** guarantee a byte-identical database, and v0.2 overstated this.

What is guaranteed:

- **No duplicate paid calls.** Extraction is keyed to `artifact.content_hash`; a hash with signals for the current `ruleset_version` is skipped. Batch submissions are recorded in `llm_batch` before the provider call returns.
- **No duplicate artifacts.** `uq_artifact_identity` plus the upsert in §4.
- **Scoring is a pure recompute.** `score --phase N` can be re-run any number of times at zero cost; `uq_score_identity` makes the write idempotent within a run.

What is not guaranteed: a crashed-then-restarted run gets a **new `run_id`**, so `uq_signal_identity` (which includes `run_id`) does not deduplicate across the restart. Deterministic signals will be re-observed under the new run. This is harmless — `signal` is append-only by design and `company_profile` resolves to the latest observation — but it means the database is not byte-identical to a clean run.

To resume under the original run instead, use `python -m portal <stage> --resume <run_id>`. This reuses the run row, so the unique index applies and the cost ledger stays in one place. Prefer `--resume` after a crash; use a fresh run for a genuine re-scrape.

### 5.1 discover

Input: a category + region (`"Zahnpflege Onlineshop"`, `"NRW"`) or a seed CSV of domains.
Places API call with a strict field mask — `displayName`, `websiteUri`, `formattedAddress` only. Requesting `rating` or `reviews` moves the call to a more expensive SKU tier. Deduplicate on normalised domain. Write `company` rows.

### 5.2 fetch

Politeness rules are **hard requirements**, not options:

- Fetch and honour `robots.txt` before anything else. Exclusion applies **only if the paths this tool needs** (`/`, `/sitemap.xml`, the Impressum path, the blog path) are disallowed for our User-Agent or `*`. A robots.txt that disallows `/checkout/` or `/account/` is normal and is not a refusal.
- One request per second per host, max 2 concurrent hosts.
- **"Host" for the politeness budget means the authority with any `www.` prefix removed.** `example.de` and `www.example.de` are one machine and get one budget. Keyed separately, the apex→www redirect that nearly every shop has would let each back-to-back pair issue two requests to one server inside a second — double this floor, on almost every domain in the corpus. **The port is kept**: `example.de:8001` and `example.de:8002` are separate servers. **Other subdomains are kept separate** — `shop.example.de` is commonly a different machine, and merging its budget with the apex would slow honest crawling for no gain. *Accepted, with the risk named:* where a shop does serve both names off one machine, we allow up to 2 req/s across the pair.
- This is **not** the same key as the one that decides which `robots.txt` applies. Robots is keyed to the origin (RFC 9309), so `example.de` and `www.example.de` are separate there and each is asked for its own file. Two questions, two keys: collapsing them one way doubles the request rate, the other way skips a robots.txt.
- `User-Agent: CreativePotatoesBot/1.0 (+https://creative-potato.global)` — identifiable, with a contact route.
- Plain `httpx` only. No headless browser unless a site returns an empty `<body>`, and then only as a per-domain opt-in flag.

**Which paths are "required" (M1.7).** Two of the four are not knowable before fetching, so the test is defined on what is:

- Hard-exclude when `/` is disallowed, **or** `/sitemap.xml` is disallowed, **or** *every* Impressum probe path (`/impressum`, `/impressum/`, `/imprint`, `/legal`, `/rechtliches`) is disallowed.
- A *single* disallowed path is never an exclusion. It is skipped per-URL and recorded with `error='robots_disallowed: …'`, so the skip is visible in the artifact table rather than silent.
- The blog path is checked per-URL once the sitemap reveals it. **A disallowed blog is a missing signal, not grounds for exclusion** — treating it as a refusal would discard exactly the leads whose weak blogs this tool exists to find.

**`Crawl-delay` (M1.2).** Honour `max(1.0, Crawl-delay)` for the stating host: our floor already exceeds a `Crawl-delay: 1`, and a larger value is a request to go slower, which we grant. A delay is read for our own agent token first and the `*` group otherwise.

Above **10 s**, do not obey — **skip the domain** and record `excluded_reason = 'crawl_delay_too_high: …'`. A value that large is a hostile or broken file, and one of two worker slots parked behind it for minutes costs the run more than the domain is worth. The delay is necessarily read from the robots.txt response itself, so that one request goes out at the floor; every subsequent request to that host respects the stated delay.

**Redirects are requests (M1.1).** A redirect chain must not be followed inside a single client call: that issues every hop below the rate limiter, so a site redirecting `http → https → /slash/` fires three requests at one host inside a second, and a cross-host hop reaches a host whose robots.txt was never read. Both breach the two rules above. Therefore:

- Follow at most **5 hops**, one at a time, each waiting on the limiter for **its own** host.
- A hop that **changes host** is followed only after that host's `robots.txt` has been fetched and consulted, and only if it permits the target. Otherwise the hop is refused and recorded as `error='redirect_refused: …'`. The new host's `Crawl-delay` applies too, cap included.
- The one exception is the `robots.txt` fetch itself, which may follow a hop within the seeded site (the apex↔www redirect nearly every shop has) — there is no earlier robots.txt that could authorise it, and the hop stays inside the domain we were asked to crawl. A hop off the seeded site during a robots fetch is refused.
- `artifact.url` records the **final** URL, so the evidence link points where the content actually came from.

`same_site` checks in the callers answer *attribution* — is this our page? They do not answer politeness, because by the time a caller sees the response the request has already gone out. The enforcement belongs in the transport.

**Failure artifacts update in place (M1.7).** `uq_artifact_identity` is a partial index over non-NULL hashes, so it does not constrain failure rows. Left to a plain INSERT, every re-run would append another row for the same dead URL and `artifact` would grow without bound. A failure row for the same `(company_id, kind, url)` is therefore **updated in place**, advancing `last_checked_at`. *When* a URL last failed is worth keeping; a row per attempt is not.

**Third-party XML (M1.3).** Sitemaps are attacker-controlled input parsed by stdlib `ElementTree`. A sitemap whose bytes contain `<!DOCTYPE` or `<!ENTITY` is **refused before parsing** — no real sitemap needs either, and refusing removes the entity-expansion class without adding a dependency. Bodies are capped at 8 MB before parsing and shards at 50 per company; a parse failure yields "no URLs" rather than aborting a run.

Fetch order: `robots.txt` → homepage → `sitemap.xml` (and any nested sitemaps) → Impressum → blog index if a blog path is found → one sample product page if a product path is found.

**Product sample selection (A5).** `opp.no_product_schema` (+10) reads a single sampled product page, so which page is sampled has to be a stated rule. "Deterministic" here cannot mean "the same URL forever" — a catalog changes, and any rule keyed to the catalog picks differently once it does. The guarantee is **same inputs → same choice**, with the chosen URL recorded so the score traces to a specific stored artifact.

*Tier 0 — reuse.* If a `product_page` artifact already exists for this company and its URL is still a valid candidate, re-sample **that** URL and consider nothing else. This is what keeps `schema.product_present` from flipping between runs for reasons unrelated to the site, and it lets the content-hash short-circuit do its job.

Reuse holds only while the stored sample still returns **HTTP 200**. On a 404 or a fetch error the stored sample is discarded and selection falls through to Tier 1/2, so a dead sample is never pinned forever.

"Discarded" means **excluded from the re-selection**, not merely un-reused: a dead URL is still the code-point minimum of its candidate set, so without the exclusion the fall-through would re-choose the very URL that just failed. At most **two** product requests are made per company per run — the Tier 0 probe and one fresh selection. If the fresh selection also fails, no sample is recorded and no `schema.product_present` is written.

*Tier 1 — platform product sitemap.* When a platform-specific product sitemap is detected (Shopware `…-product-….xml(.gz)`, Shopify `sitemap_products_*.xml`, WooCommerce `product-sitemap.xml`), candidates are the **union of all its shards**, not the first shard. `.xml.gz` shards are decompressed before parsing.

*Tier 2 — path patterns.* With no product sitemap, candidates are URLs from the fetched sitemaps matching `/detail/`, `/products/`, `/produkt/`. With no sitemap at all, fall back to product-pattern links on the homepage.

`/p/` was in this list and is **dropped (M1.4)** until it is observed on a real shop. It is a product prefix on some platforms and a *pagination* prefix on others, and the two errors are not equally bad: a false positive feeds a listing page to `schema.product_present` and wrongly awards +10, while a false negative merely leaves the signal unwritten — which the zero-candidates rule below already handles correctly. When the failure modes are asymmetric, take the safe side. A pattern goes back into this list on evidence, not on plausibility.

*Ordering, in every tier:* the **lexicographically smallest** URL, compared by **Unicode code point**. Never locale collation — a locale-dependent sort over a corpus full of umlauts is a reproducibility bug. Code-point ordering over the union is invariant to both intra-file reordering and inter-shard redistribution, which document order is not: Shopware regenerates and re-shards product sitemaps on a schedule, so document order is stable only by accident.

*Filters, applied before ordering.* The ordering barely matters; which URLs qualify matters a great deal, because a non-product URL that reaches the candidate set produces a false `Product`-absent reading and wrongly awards +10. §5.3 already warns that Shopware sitemaps mix content and product URLs.

- Reject URLs carrying a query string — variant and filter permutations, not canonical pages.
- Require a path segment **after** the pattern. Bare `/products/` is Shopify's collection listing; `/products/<handle>` is the product.
- Reject URLs also matching category/listing patterns (`/kategorie/`, `/collections/`, `/c/`).
- Reject anything under the blog path detected for `content.blog_exists`.

*Zero candidates.* No product page is fetched and **no `schema.product_present` signal is written — not `0`.** A `0` there means "checked, absent", which fires `opp.no_product_schema` for +10 against a site whose product pages were never retrieved. That is the same error as the blog ladder's `NULL` branch (§6.2), and it is refused for the same reason. The same applies when a sample is selected but its fetch fails — 404, timeout, robots-disallowed.

No new review reason is needed: zero product candidates on a detected shop platform already satisfies `possible_marketplace_only` (§6.4).

*Auditability.* The chosen URL is recorded as `catalog.product_sample_url` (text, unscored), with `evidence_url` set to the sitemap or homepage it was read from. Without it, `schema.product_present` points at a product page with no record of *why that page*, and the selection rule is unauditable from the database.

**Impressum discovery** is two-step: (1) footer links matching `impressum|imprint|legal notice|rechtliches`; (2) if none, probe direct paths `/impressum`, `/impressum/`, `/imprint`, `/legal`, `/rechtliches` before concluding absence. Only after both steps fail is `no_impressum` recorded — and for CH companies it sets `needs_review`, not `excluded` (§6.4).

Store bodies on disk under `data/artifacts/{domain}/{kind}-{timestamp}.html`, path recorded in `artifact.body_path`. Skip re-extraction when `content_hash` is unchanged from the previous run.

### 5.3 extract-p1 — deterministic parsers (no LLM, no cost, fully reproducible)

| Signal key | Method | Reliability note |
|---|---|---|
| `platform.detected` | HTML signature match: Shopware (incl. SW6 `/bundles/storefront/`, `sw-` attributes), `cdn.shopify.com`, `wp-content` + `woocommerce`, `jtl-shop` | Good |
| `content.blog_exists` | Blog/magazin/ratgeber/news path found in sitemap **or** homepage nav links | Good |
| `content.blog_last_post` | **Authoritative:** newest date parsed from the blog index HTML — JSON-LD `datePublished`, `<time datetime>`, or German visible-date patterns (`12. März 2023`). Sitemap `<lastmod>` is a hint only and is never used alone. | Sitemap lastmod is regenerated on deploys by Shopware/WP and systematically lies fresh; this rule exists because of that. |
| `content.blog_post_count` | Count of post links on the blog index (paginated: first page count × page count if pagination is visible), cross-checked against sitemap URL count under the blog path | Sitemap counts include tag/category noise; index count wins on conflict |
| `catalog.product_url_count` | URLs under product-typical paths (`/detail/`, `/products/`, `/produkt/`) or, for Shopware, the product sitemap | Shopware sitemaps often mix content and product URLs — prefer the platform-specific product sitemap when detected |
| `schema.article_present`, `schema.product_present` | Parse all `application/ld+json` blocks on homepage **and** blog index / a sample product page, collect `@type` | Checking only the homepage under-detects; check the page type where the schema would live |
| `meta.description_length` | Homepage `<meta name="description">` length | **Informational only, not scored** — platforms auto-generate adequate-length templates |
| `i18n.hreflang_count` | Count of distinct `hreflang` values | `de-DE`/`de-AT`/`de-CH` variants are not real i18n; count distinct language codes, not locale codes |
| `perf.lighthouse_performance` | PageSpeed Insights API — **Phase 2 only** (slow: 15–30 s/site) | Cache by `artifact.last_checked_at` age; do not re-run within 30 days |
| `agency.footer_credit` | Regex for `realisiert von\|umgesetzt von\|powered by\|Webdesign:` in footer, plus outbound footer links whose anchor/title contains `agentur\|design\|media\|digital` | Under-detects (logo-only credits). Treated as bonus negative signal, never as a gate |
| `reviews.trusted_shops`, `reviews.count` | Trusted Shops badge script detection; visible aggregate review count in `AggregateRating` JSON-LD | Free product-strength proxy |
| `catalog.product_sample_url` | The product URL selected by the A5 rule in §5.2. Written by `fetch`, not by an extractor — it records a fetch-time decision. | **Unscored.** Exists so the sample behind `schema.product_present` is auditable |

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

### 5.5 extract-p2 — paid signals, advancing companies only

**(a) PageSpeed Insights** — as in §5.3, run here.

**(b) Impressum extraction — Anthropic SDK, structured output via tool use. No framework.**

```python
from pydantic import BaseModel
from typing import Optional, List

class ImpressumExtract(BaseModel):
    legal_name: Optional[str]
    legal_form: Optional[str]          # GmbH, GmbH & Co. KG, e.K., Einzelunternehmen, …
    street: Optional[str]
    postal_code: Optional[str]
    city: Optional[str]
    country: Optional[str]
    managing_directors: List[str]      # [] if none named — do NOT infer from elsewhere
    owner_name: Optional[str]          # Inhaber/in for e.K./Einzelunternehmen — distinct from GF
    register_court: Optional[str]      # Amtsgericht …
    register_number: Optional[str]     # HRB … — absent for Einzelunternehmen, that is valid
    vat_id: Optional[str]              # USt-IdNr.
    email: Optional[str]
    phone: Optional[str]

class HomepageExtract(BaseModel):
    one_line_offer: Optional[str]      # German, max 20 words, what they actually sell
    product_categories: List[str]
    audience: Optional[str]            # 'b2c' | 'b2b' | 'both' | None
    owner_named_on_site: bool          # is a founder/owner presented by name?
    own_brand: Optional[bool]          # manufacturer/own-brand vs pure reseller — best-fit segment marker
    agency_credit: Optional[str]
```

Input preparation is a hard requirement, not an optimisation: strip `<script>`, `<style>`, `<svg>`, `<nav>`, comments; reduce to text + structural tags; **cap at 60 KB**. Pages exceeding the cap after cleaning are truncated from the end (Impressum content is near the top of an Impressum page). This is the primary defence against unbounded token spend (§7).

Prompt discipline — stated verbatim in the system prompt of the extraction call:

> Return `null` for any field not present on the page. Do not infer, do not guess, do not fill from general knowledge. If the page is not an Impressum, return all nulls.

Hallucinated Impressum data is the single worst failure mode here: it produces a confident wrong name in a letter to a stranger. Every LLM-derived `signal` row carries `method='llm'` and must be visually distinguished in the UI. Additionally: `legal_name` and `managing_directors`/`owner_name` values are verified by exact substring presence in the cleaned page text; a value not literally present on the page is discarded and the signal written with `confidence=0` for review.

**Model:** Claude Haiku 4.5 via the Batch API (50% off; latency is irrelevant here). Extraction requests are keyed to `artifact.content_hash` so a resumed run never re-submits an already-extracted page.

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

**Web search billing — confirmed 2026-08-15.** Web search is charged **$10 per 1,000 searches ($0.01 per search)** on the Claude API, in addition to token costs. Each search counts as one use regardless of result count; searches that error are not billed. The count is reported per response in `usage.server_tool_use.web_search_requests` and must be accumulated into `run.web_searches`. The Batch API does **not** discount the per-search charge (batch web-search calls are priced the same as regular ones), so at 2 searches per company the search fee is **$0.02 per company**, bringing the AI-visibility sub-stage to roughly **$0.05–0.06 per company**. The per-search charge must be included in the pre-call reservation of §7.3.

**Methodological constraint.** This measures one model, on one date, with web search enabled. It is a defensible *baseline*, not a statement about AI systems in general. §8 and §9 govern how it may be worded in anything sent to a prospect.

### 5.6 reconcile

`python -m portal reconcile` — polls every `llm_batch` row with status `submitted`, writes returned extractions as signals, sets `actual_cost_usd`, moves status to `reconciled`. Safe to run repeatedly. Must be run before `score --phase 2` produces trustworthy output; `score --phase 2` warns loudly if unreconciled batches exist for the companies being scored.

**B4 — reconciled signals carry the submitting run's `run_id`**, i.e. `llm_batch.run_id`, not the id of the run doing the reconciling.

`uq_signal_identity` is `(run_id, company_id, key, evidence_url)`. Writing under a fresh `run_id` on each invocation would mean the unique index cannot dedupe, so a `reconcile` that writes 40 of 60 companies and then dies would have the next invocation re-insert all 60. Under the submitting run's id, the §4 `ON CONFLICT … DO NOTHING` idiom behaves as it does everywhere else and "safe to run repeatedly" actually holds. It also keeps the reserved spend (§7 control 4) and the resulting evidence on the same `run` row.

Consequences to expect rather than treat as bugs:

- `observed_at` is reconcile wall-clock time, not the submitting run's. So `signal.observed_at > run.finished_at` is normal, and `company_profile`'s latest-wins resolution is unaffected — the batch result genuinely is the newest thing known.
- A run's signal set can therefore grow after its `finished_at`. A finished run is not a closed one until its batches have reconciled.
- The reconciling run still gets its own `run` row with `stage='reconcile'`, for started/finished timestamps and batches polled. It just does not own the signals.

**B3.1 — the cost ledger reconciles against the submitting run.** `llm_batch.actual_cost_usd` is written at reconciliation, and the estimate-to-actual correction is applied to the *submitting* run's `run.est_cost_usd`, because that is where §7 control 4 made the reservation. The reconciling run does not absorb the delta.

### 5.7 score --phase 2

Recomputes the full score including Phase-2 signals. Writes a `phase=2` score row; the UI shows the latest phase available per company.

## 6. Scoring model — ruleset v3

**The score measures opportunity size, not company quality.** A high score means "this company has a strong product and visibly weak content marketing" — i.e. a good fit for the offer. State this in the UI so it is never misread as a quality ranking.

### 6.1 Qualification (is this a real, fitting business?)

| rule_id | Condition | Points |
|---|---|---|
| `qual.ecommerce_platform` | Shopware / Shopify / WooCommerce / JTL detected | +15 |
| `qual.owner_operated` | `legal_form ∈ {e.K., Einzelunternehmen, GbR}` **or** Impressum names ≤ 2 natural-person Geschäftsführer **or** owner named on site | +15 |
| `qual.product_depth` | ≥ 20 product URLs | +10 |
| `qual.own_brand` | Sells own-brand/manufactured products, not pure reselling | +10 |
| `qual.own_domain_shop` | Sells on own domain, not marketplace-only | +5 |
| `qual.product_strength` | Trusted Shops badge present or ≥ 50 aggregate reviews | +10 |

### 6.2 Opportunity (how weak is their content marketing?)

**Blog ladder — evaluated as an ordered chain, first match wins, evaluation stops.** Written as a chain rather than a table because the table format is what allowed overlapping predicates in v0.2.

```
days_since_newest = (today − content.blog_last_post).days      # NULL if no date parsed
post_count        = content.blog_post_count

if not blog_exists:
    → opp.no_blog          +25
elif blog_last_post is NULL:
    → no rung fires; raise review_flag 'blog_date_unparseable' (§4, §6.4)
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
| `opp.no_product_schema` | No `Product` in JSON-LD on a product page. **Fires only when `schema.product_present` was written from a product page fetched with HTTP 200** (§5.2, A5) — absent that signal the rule fires in neither direction. | +10 |
| `opp.ai_invisible` | `ai.queries_checked >= 2` and `ai.brand_mentions = 0` (Phase 2) | +15 |
| `opp.slow_site` | Lighthouse performance < 50 (Phase 2) | +10 |
| `opp.de_only` | Single distinct language (locale variants don't count), expansion angle | +5 |

The old 45-point cap is removed: mutual exclusivity in the ladder plus the conditional schema rule eliminate the double-counting structurally.

### 6.3 Negative signals

| rule_id | Condition | Points |
|---|---|---|
| `neg.has_agency` | Footer names an agency (text or linked credit) | −20 |
| `neg.active_content` | ≥ 4 posts in the last 6 months | −25 |

### 6.4 Hard exclusions and soft review flags

Never delete, never silently drop. Two tiers:

**Hard (`excluded = 1`, reason recorded):**

- `robots_disallowed` — only when required paths (§5.2) are disallowed
- `competitor` — site is itself a marketing/web agency
- `too_large` — requires **two** independent indicators from: Konzern structure, > 250 employees stated, > 5 named Geschäftsführer, "Vorstand" **together with** register type AG and multi-location footprint. A lone "Vorstand" mention never excludes (small AGs and Vereine have one).
- `unreachable` — after 2 attempts on different days

**Soft (one `review_flag` row per reason, surfaced in a dedicated UI filter, human decides):**

These three are independent and can all apply to one company, which is why they are rows rather than a shared column. Raising one sets `company.needs_review` by trigger; resolving the last open one clears it.

- `no_impressum` — after the two-step discovery in §5.2 fails. For DE/AT this usually means not a real trading business, but it can be a footer-parsing miss, so a human glances before it dies. For **CH** companies this is always soft: the Swiss disclosure duty (UWG) is structured differently from §5 DDG and legitimate Swiss shops may present the information under "Kontakt".
- `possible_marketplace_only` — shop platform detected but < 5 product URLs on own domain
- `blog_date_unparseable` — the blog index exists but no post date could be parsed (§6.2)

**Resolution is sticky.** Once a reason has been resolved for a company, that same reason is never raised for that company again — a later run that re-detects the condition writes nothing. This is a policy decision, not an artefact of the schema, and it is what `uq_review_flag` plus `ON CONFLICT DO NOTHING` implement.

The trade: a genuinely changed situation will not re-surface. Accepted deliberately, because the alternative re-adjudicates the same judgment on every run — the CH shop whose Impressum lives under "Kontakt" would return to the review queue monthly, forever, and a queue that refills itself stops being read. The human decision is the more durable fact.

Consequences to hold onto:

- Resolving is a considered act, not a way to clear the screen. The UI should present it as such.
- Re-raising, if it is ever wanted, is an explicit operator action (deleting the flag row), not something a pipeline stage does on its own.
- `resolved_by_human` distinguishes the two ways a flag closes: `1` for a human dismissal, `0` for a pipeline clear. Only the human case is a judgment; the distinction exists so the two are never conflated when reviewing what was skipped.

### 6.5 Bands

`A ≥ 75` · `B 55–74` · `C 30–54` · `D < 30`

(Thresholds raised slightly vs. v0.1 because ruleset v2 added up to +35 new available points — `qual.own_brand`, `qual.product_strength`, `opp.ai_invisible`. Re-tune after the first 100 scored companies; `ruleset_version` makes this a zero-cost recompute.)

## 7. Cost controls

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

8. **Web search accounting.** `run.web_searches` counts searches issued, read from `usage.server_tool_use.web_search_requests` on each response. The per-search charge is **$10 per 1,000 searches**, billed separately from tokens and not discounted by the Batch API (§5.5c). Include it in the pre-call reservation at `$0.01 × planned_queries` per company.

9. Every API key from environment variables. `.env` in `.gitignore`. No keys in the repo, ever.

**Expected steady-state: $20–35/month at ~500 discovered companies/month.** Revised upward from v0.2's $15 for two reasons: the corrected advance threshold (D1) admits more companies to Phase 2, and the corrected AI-visibility token estimate (D4) is roughly ten times v0.2's. The `$25` default ceiling in control 2 is deliberately close to this figure — it should bite occasionally, which is how you find out the model is wrong.

## 8. Compliance requirements

These are requirements, not recommendations. They shape the schema, so they cannot be bolted on later.

**No outbound email capability.** B2B cold email in Germany is restricted under §7 UWG; prior consent is required in practice and the "mutmaßliche Einwilligung" exception is narrow. The `outreach.channel` enum permits only `post` and `phone`. The application must contain no SMTP client, no mail API dependency, and no send button. This is why "no email sending" is a non-goal in §2 rather than a backlog item.

**GDPR.** Named individuals in the `contact` table are personal data, processed under legitimate interest (Art. 6(1)(f)). Consequences for the build:

- Art. 14 imposes an information duty when data is collected from a source other than the data subject — the notice goes out with the first postal contact. `contact.art14_notice_sent_at` tracks it.
- `contact.purge_after` defaults to collected_at + 12 months. A `python -m portal purge` command deletes expired rows and must actually be run.
- A `python -m portal forget --domain X` command hard-deletes all rows for one company across every table, for erasure requests.
- Company-level data (domain, platform, blog cadence) is not personal data and is not subject to the above. Keeping the two in separate tables is what makes this tractable.

**Crawling conduct.** robots.txt honoured, identifiable User-Agent, 1 req/s. This is partly legal hygiene and partly commercial: the pitch involves telling a prospect you analysed their site, and their server logs should support that story.

**Comparative claims in outbound material.** The research brief names competitors — *"genannt werden stattdessen: …"*. That is comparative advertising, lawful under §6 UWG only where the comparison is objective and verifiable. Two consequences, both binding on the export:

Every AI-visibility statement in an exported brief must carry its basis inline: the literal query text, the date of the check, the model used, and the fact that web search was enabled. These come from `ai.query_text`, `ai.checked_at`, `ai.model_used`. An export missing any of them must fail, not degrade gracefully — a brief that asserts a competitor comparison without its basis is the failure this rule exists to prevent.

Wording is constrained to what was measured. Permitted: *"Bei 2 von 2 geprüften KI-Abfragen wurden Sie nicht genannt."* Not permitted: *"Sie sind in KI-Systemen unsichtbar."* One model checked once does not support a general claim, and the gap between the two is the same category of error as a Heilversprechen — asserting more than the evidence carries.

## 9. UI

Single page, server-rendered, HTMX for interactions. No SPA.

- Table: company, band (with phase indicator), score, city, platform, one-line offer, last blog post, AI-visibility (e.g. `0/2`).
- Filter by band, platform, country, excluded status, **needs_review**.
- Row expands to show every `score_component` with its reason and a link to the evidence artifact.
- LLM-derived fields visually marked (e.g. a dotted underline) and hoverable to show `confidence` and `evidence_url`. Fields with `confidence=0` (failed substring verification, §5.5b) rendered in red — never trust these in a letter without checking the source.
- Actions per row: mark excluded (with reason), clear/confirm needs_review, log an outreach attempt, export the research brief.
- An expanded row lists its open `review_flag` reasons individually, each independently clearable — clearing writes `resolved_at`, `resolved_by_human = 1` and an optional note, so "not yet reviewed" and "reviewed and dismissed" stay distinguishable. The row-level `needs_review` marker clears when the last open flag does.

**Research brief export** (per company, German, Markdown). Findings section built from `score_component.reason` sentences. KI-Sichtbarkeit section built from the `ai.*` signals, in the format proven in live pitches, with a mandatory basis line:

> **KI-Sichtbarkeit**
> Geprüft am 15.08.2026 über Claude (`claude-haiku-4-5`) mit aktivierter Websuche.
> Abfragen: „beste Ultraschallzahnbürste" · „Ultraschallzahnbürste Test"
> Ergebnis: Bei 2 von 2 Abfragen wurde Ihre Marke nicht genannt.
> Genannt wurden stattdessen: Emmi-Dent, Philips Sonicare, Curaprox.

The export function asserts the presence of `ai.query_text`, `ai.checked_at` and `ai.model_used` before writing, and raises if any is missing. Briefs for companies that did not reach Phase 2 omit the KI-Sichtbarkeit section entirely rather than rendering it empty.

## 10. Open decisions

- Ollama for local extraction instead of Haiku — saves ~$10/month at Phase-2 volumes, costs German-language extraction quality and the substring-verification simplicity. Currently: use Haiku.
- Whether to store artifact bodies compressed (gzip) — likely yes above a few hundred companies.
- Research brief export stays Markdown for v1; DOCX (letter-ready) is a candidate for v1.1 once the brief content has stabilised against real outreach feedback.
- Band thresholds (§6.5) are provisional pending the first 100-company calibration run.
