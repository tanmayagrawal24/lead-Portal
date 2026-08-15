-- Migration 001 — initial schema.
-- Transcribes §4 of docs/lead-portal-spec-v0.3.md, including the B2 amendment
-- (review_flag replaces company.needs_review_reason).
--
-- Applied inside a transaction by portal/migrate.py. Do not put PRAGMA
-- journal_mode or PRAGMA foreign_keys here: neither takes effect inside a
-- transaction. Connection pragmas live in portal/db.py.

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
-- within the same run. All writes to signal use INSERT OR IGNORE. See §5 (D6)
-- for what this does and does not guarantee across run boundaries.
-- B4: signals written by `reconcile` carry the SUBMITTING run's run_id, which is
-- what makes repeated reconciliation dedupe here rather than duplicate (§5.6).
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
