# Lead Portal — Technical Specification v0.2

**Owner:** Tanmay Agrawal / Creative Potatoes
**Status:** Revised after external review. Ready for implementation hand-off to Claude Code.
**Supersedes:** v0.1

## Changelog v0.1 → v0.2

1. **ScrapeGraphAI removed.** Both extractions use the Anthropic SDK directly with tool-use structured output. Removes an unstable dependency, gives exact control over prompt and input size, and makes Batch API integration native. (§5.3)
2. **Scoring restructured.** The blog rules are now a mutually exclusive ladder; schema rules are conditional. The 45-point cap is gone because the double-counting it patched no longer exists. (§6.2)
3. **Two-phase pipeline.** Phase 1 = free deterministic signals for everyone. Phase 2 (PageSpeed, LLM Impressum extraction, AI-visibility check) runs only for provisional A/B companies. Cuts LLM volume ~60–70%. (§5)
4. **New signal: AI/GEO visibility.** Category-query checks against an LLM with web search; results feed both the score and the exported research brief, which now doubles as a KI-Sichtbarkeits-Baseline. (§5.5, §6.2)
5. **New signal: review presence** (Trusted Shops badge / on-page review count) as a free product-strength proxy. (§5.3)
6. **Blog date detection fixed.** Sitemap `<lastmod>` demoted to a hint — Shopware and WP regenerate it on deploys. Authoritative date comes from the blog index HTML. (§5.3)
7. **Exclusions softened.** `no_impressum` only after direct-path probing, and downgraded to `needs_review` for CH. `robots_disallowed` only when required paths are disallowed. `too_large` needs two indicators, not just "Vorstand". (§6.4)
8. **Einzelunternehmen fixed.** `qual.owner_operated` now fires on legal form (e.K., Einzelunternehmen, GbR) as well as GF count. (§6.1)
9. **Idempotency hardened.** UNIQUE constraints on `signal` and `score`; cost reserved before LLM calls, reconciled after; extraction keyed to `content_hash`. (§4, §5.4, §7)
10. **`company_profile` SQL view** added: scoring reads a wide pivoted view, provenance stays in EAV. (§4)
11. **`meta.description_length` dropped** from scoring (Shopware auto-generates adequate-length templates; signal is noise). Kept as an informational signal only.

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
                         │              → impressum → blog index
                         ▼
                  ┌──────────────┐
                  │  extract P1  │ ──▶ deterministic signals (free)
                  └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  score  P1   │ ──▶ provisional band
                  └──────────────┘
                         │  band A/B only
                         ▼
                  ┌──────────────┐     PageSpeed API
                  │  extract P2  │ ──▶ Impressum extraction (Anthropic SDK, Batch)
                  └──────────────┘     AI-visibility check (Anthropic SDK + web search)
                         │
                         ▼
                  ┌──────────────┐
                  │  score  P2   │ ──▶ final score + score_component
                  └──────────────┘     (rule_id, points, letter-ready German reason)
                         │
                         ▼
                  FastAPI + HTMX UI  (localhost:8000)
```

**Stack:** Python 3.11+, FastAPI, SQLite (WAL mode), `httpx`, `selectolax` for parsing, `anthropic` SDK, Jinja2 + HTMX for the UI. No build step, no Node, no Docker.

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
    needs_review    INTEGER NOT NULL DEFAULT 0,-- soft flag: human decides, machine does not exclude
    needs_review_reason TEXT
);
CREATE INDEX idx_company_excluded ON company(excluded);
CREATE INDEX idx_company_review ON company(needs_review);

-- ─────────────────────────────────────────────────────────────
-- Raw fetched pages. Kept so re-scoring never costs a refetch.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE artifact (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,               -- 'robots'|'homepage'|'sitemap'|'impressum'|'blog_index'|'product_page'
    url           TEXT NOT NULL,
    http_status   INTEGER,
    content_hash  TEXT,                        -- sha256 of body; extraction is keyed to this
    body_path     TEXT,                        -- relative path on disk; bodies are NOT stored in SQLite
    bytes         INTEGER,
    fetched_at    TEXT NOT NULL,
    error         TEXT
);
CREATE INDEX idx_artifact_company_kind ON artifact(company_id, kind);
-- Idempotency: a re-fetch of identical content must not create a second row.
CREATE UNIQUE INDEX uq_artifact_identity ON artifact(company_id, kind, content_hash)
    WHERE content_hash IS NOT NULL;

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
-- Idempotency: re-running a crashed extract stage must not duplicate observations.
-- All writes to signal use INSERT OR IGNORE.
CREATE UNIQUE INDEX uq_signal_identity ON signal(run_id, company_id, key, evidence_url);

-- ─────────────────────────────────────────────────────────────
-- Wide read model for scoring. A VIEW, not a table: no sync, no
-- second write path. Pivots the LATEST observation per key.
-- Add a column here when a new signal key enters the scoring rules;
-- keys not listed remain queryable via the signal table.
-- ─────────────────────────────────────────────────────────────
CREATE VIEW company_profile AS
WITH latest AS (
    SELECT s.*,
           ROW_NUMBER() OVER (PARTITION BY company_id, key ORDER BY observed_at DESC) AS rn
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
    purge_after       TEXT NOT NULL            -- collected_at + 12 months, enforced by a cron task
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
    stage          TEXT NOT NULL,              -- 'discover'|'fetch'|'extract_p1'|'score_p1'|'extract_p2'|'score_p2'
    companies_seen INTEGER DEFAULT 0,
    places_calls   INTEGER DEFAULT 0,
    llm_input_tokens  INTEGER DEFAULT 0,
    llm_output_tokens INTEGER DEFAULT 0,
    est_cost_usd   REAL DEFAULT 0,             -- reserved BEFORE each LLM call, reconciled after
    aborted_reason TEXT
);
```

## 5. Pipeline stages

Each stage is a separate CLI command, independently re-runnable. `python -m portal discover`, `… fetch`, `… extract-p1`, `… score --phase 1`, `… extract-p2`, `… score --phase 2`, `… serve`.

**Idempotency contract (applies to every stage):** re-running a stage after a mid-run crash must produce the same database state as a clean run, with no duplicate rows and no repeated paid API calls. Mechanisms: the UNIQUE indexes in §4 with `INSERT OR IGNORE`; extraction keyed to `artifact.content_hash` (an artifact whose hash already has signals for the current ruleset is skipped, even within the same run); cost reservation before each LLM call (§7).

### 5.1 discover
Input: a category + region (`"Zahnpflege Onlineshop"`, `"NRW"`) or a seed CSV of domains.
Places API call with a strict field mask — `displayName`, `websiteUri`, `formattedAddress` only. Requesting `rating` or `reviews` moves the call to a more expensive SKU tier. Deduplicate on normalised domain. Write `company` rows.

### 5.2 fetch
Politeness rules are **hard requirements**, not options:
- Fetch and honour `robots.txt` before anything else. Exclusion applies **only if the paths this tool needs** (`/`, `/sitemap.xml`, the Impressum path, the blog path) are disallowed for our User-Agent or `*`. A robots.txt that disallows `/checkout/` or `/account/` is normal and is not a refusal.
- One request per second per host, max 2 concurrent hosts.
- `User-Agent: CreativePotatoesBot/1.0 (+https://creative-potato.global)` — identifiable, with a contact route.
- Plain `httpx` only. No headless browser unless a site returns an empty `<body>`, and then only as a per-domain opt-in flag.

Fetch order: `robots.txt` → homepage → `sitemap.xml` (and any nested sitemaps) → Impressum → blog index if a blog path is found.

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
| `i18n.hreflang_count` | Count of distinct `hreflang` values | Note: `de-DE`/`de-AT`/`de-CH` variants are not real i18n; count distinct language codes, not locale codes |
| `perf.lighthouse_performance` | PageSpeed Insights API — **Phase 2 only** (slow: 15–30 s/site) | Cache by content_hash age; do not re-run within 30 days |
| `agency.footer_credit` | Regex for `realisiert von|umgesetzt von|powered by|Webdesign:` in footer, plus outbound footer links whose anchor/title contains `agentur|design|media|digital` | Under-detects (logo-only credits). Treated as bonus negative signal, never as a gate |
| `reviews.trusted_shops`, `reviews.count` | Trusted Shops badge script detection; visible aggregate review count in `AggregateRating` JSON-LD | Free product-strength proxy |

### 5.4 score --phase 1
Pure function over `company_profile`. Costs nothing. Companies reaching provisional band A or B advance to Phase 2; C/D stop here (final band = provisional band, `phase=1` score row is their final score unless manually promoted in the UI).

### 5.5 extract-p2 — paid signals, A/B candidates only

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

For each Phase-2 company, derive up to 3 German category queries from `one_line_offer` and `product_categories` (e.g. *"beste Ultraschallzahnbürste"*, *"Ultraschallzahnbürste Test"*, *"Ultraschallzahnbürste kaufen empfehlung"*). Run each against Claude with web search enabled and a fixed prompt asking which brands/shops it would recommend. Record:

- `ai.queries_checked` — how many queries ran
- `ai.brand_mentions` — in how many the company's brand or domain appeared
- `ai.competitors_mentioned` — value_text: comma-separated brands that did appear (goes straight into the brief)

This is stored evidence for the sentence that opens the pitch: *"Bei X von Y KI-Anfragen zu Ihrer Produktkategorie werden Sie nicht genannt — genannt werden stattdessen: …"* Budgeted like every other LLM call (§7); roughly 3 calls × ~2k tokens per A/B company.

### 5.6 score --phase 2
Recomputes the full score including Phase-2 signals. Writes a `phase=2` score row; the UI shows the latest phase available per company.

## 6. Scoring model — ruleset v2

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

**Blog ladder — mutually exclusive; exactly one rung fires (the applicable one):**

| rule_id | Condition | Points |
|---|---|---|
| `opp.no_blog` | `blog_exists = 0` | +25 |
| `opp.blog_stale` | Blog exists, newest post > 12 months old | +20 |
| `opp.thin_blog` | Blog exists, active-ish, but < 10 posts total | +12 |
| `opp.blog_slowing` | Newest post 6–12 months old, ≥ 10 posts | +10 |

**Conditional and independent rules:**

| rule_id | Condition | Points |
|---|---|---|
| `opp.no_article_schema` | Blog **exists** and no `Article`/`BlogPosting` in JSON-LD on blog pages. Never fires together with `opp.no_blog`. | +8 |
| `opp.no_product_schema` | No `Product` in JSON-LD on a product page | +10 |
| `opp.ai_invisible` | `ai.queries_checked ≥ 2` and `ai.brand_mentions = 0` (Phase 2) | +15 |
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

**Soft (`needs_review = 1`, surfaced in a dedicated UI filter, human decides):**
- `no_impressum` — after the two-step discovery in §5.2 fails. For DE/AT this usually means not a real trading business, but it can be a footer-parsing miss, so a human glances before it dies. For **CH** companies this is always soft: the Swiss disclosure duty (UWG) is structured differently from §5 DDG and legitimate Swiss shops may present the information under "Kontakt".
- `possible_marketplace_only` — shop platform detected but < 5 product URLs on own domain

### 6.5 Bands

`A ≥ 75` · `B 55–74` · `C 30–54` · `D < 30`

(Thresholds raised slightly vs. v0.1 because ruleset v2 adds up to +35 new available points — `qual.own_brand`, `qual.product_strength`, `opp.ai_invisible`. Re-tune after the first 100 scored companies; `ruleset_version` makes this a zero-cost recompute.)

## 7. Cost controls

Non-negotiable, implemented as code not as discipline:

1. **Google Cloud Console quota cap** set below the free SKU threshold for every Places SKU. Make it physically impossible to be billed.
2. **Per-run token budget with pre-call reservation.** Before every LLM call (or batch submission), the *estimated* cost of that call is added to `run.est_cost_usd` and the ceiling (default `$5.00`) is checked **against the reserved total**. After the response, the estimate is reconciled to actual usage. A crash between call and write can therefore only over-count, never under-count — the failure mode is a conservatively aborted run, not silent overspend. Batch submissions are estimated as a whole batch before submission, because a submitted batch is committed spend.
3. **Input size cap.** LLM inputs are cleaned and capped at 60 KB (§5.5b). This closes the unbounded-spend path of multi-megabyte Shopware homepages.
4. **Content-hash short-circuit.** Unchanged page → no LLM call. Extraction keyed to `content_hash`, effective across runs *and* within a resumed run.
5. **Two-phase gating.** All LLM and PageSpeed spend is restricted to provisional A/B companies (§5.4) — roughly the top 30–40% of discovered companies.
6. **Batch API** for the extraction pass — 50% off.
7. Every API key from environment variables. `.env` in `.gitignore`. No keys in the repo, ever.

Expected steady-state: **under $15/month at ~500 leads/month** (two-phase gating more than offsets the added AI-visibility calls).

## 8. Compliance requirements

These are requirements, not recommendations. They shape the schema, so they cannot be bolted on later.

**No outbound email capability.** B2B cold email in Germany is restricted under §7 UWG; prior consent is required in practice and the "mutmaßliche Einwilligung" exception is narrow. The `outreach.channel` enum permits only `post` and `phone`. The application must contain no SMTP client, no mail API dependency, and no send button. This is why "no email sending" is a non-goal in §2 rather than a backlog item.

**GDPR.** Named individuals in the `contact` table are personal data, processed under legitimate interest (Art. 6(1)(f)). Consequences for the build:
- Art. 14 imposes an information duty when data is collected from a source other than the data subject — the notice goes out with the first postal contact. `contact.art14_notice_sent_at` tracks it.
- `contact.purge_after` defaults to collected_at + 12 months. A `python -m portal purge` command deletes expired rows and must actually be run.
- A `python -m portal forget --domain X` command hard-deletes all rows for one company across every table, for erasure requests.
- Company-level data (domain, platform, blog cadence) is not personal data and is not subject to the above. Keeping the two in separate tables is what makes this tractable.

**Crawling conduct.** robots.txt honoured, identifiable User-Agent, 1 req/s. This is partly legal hygiene and partly commercial: the pitch involves telling a prospect you analysed their site, and their server logs should support that story.

## 9. UI

Single page, server-rendered, HTMX for interactions. No SPA.

- Table: company, band (with phase indicator), score, city, platform, one-line offer, last blog post, AI-visibility (e.g. `0/3`).
- Filter by band, platform, country, excluded status, **needs_review**.
- Row expands to show every `score_component` with its reason and a link to the evidence artifact.
- LLM-derived fields visually marked (e.g. a dotted underline) and hoverable to show `confidence` and `evidence_url`. Fields with `confidence=0` (failed substring verification, §5.5b) rendered in red — never trust these in a letter without checking the source.
- Actions per row: mark excluded (with reason), clear/confirm needs_review, log an outreach attempt, export the research brief.

**Research brief export** (per company, German, Markdown): findings section built from `score_component.reason` sentences, plus a KI-Sichtbarkeit section built from the `ai.*` signals in the format already proven in live pitches: queries run, who was mentioned, that the prospect was not. This makes every A-band row a ready first-contact asset, not just a score.

## 10. Resolved review questions (was: review brief)

Kept for the record; all eight v0.1 review questions are resolved in this revision:

1. Scoring coherence → blog ladder + conditional schema rule (§6.2), cap removed.
2. EAV trade-off → EAV retained for provenance, `company_profile` VIEW for scoring (§4).
3. Over-exclusion → two-tier exclusion, CH-aware, robots path-scoped (§6.4, §5.2).
4. Einzelunternehmen → `qual.owner_operated` rewritten; `owner_name` added to extraction (§6.1, §5.5b).
5. Idempotency → UNIQUE indexes, hash-keyed extraction, pre-call cost reservation (§4, §5, §7).
6. Signal quality → blog dates from index HTML not sitemap lastmod; meta length descored; schema checked on the right page types (§5.3).
7. Missing signal → AI/GEO visibility (§5.5c) + review-count product-strength proxy (§5.3).
8. Cost model → input cap, pre-submission batch estimation, two-phase gating (§7).

## 11. Open decisions

- Ollama for local extraction instead of Haiku — saves ~$10/month at Phase-2 volumes, costs German-language extraction quality and the substring-verification simplicity. Currently: use Haiku.
- Whether to store artifact bodies compressed (gzip) — likely yes above a few hundred companies.
- Research brief export stays Markdown for v1; DOCX (letter-ready) is a candidate for v1.1 once the brief content has stabilised against real outreach feedback.
- Band thresholds (§6.5) are provisional pending the first 100-company calibration run.
