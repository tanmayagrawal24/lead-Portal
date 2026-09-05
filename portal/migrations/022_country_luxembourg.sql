-- pragma: table-rebuild
--
-- 022 — Luxembourg joins the country set.
--
-- **THIS MIGRATION SHIPS IN THE SAME COMMIT AS ITS WRITER** (M1.45(c)).
-- `portal/countries.py` is the rule. See M1.129.
--
-- ─────────────────────────────────────────────────────────────────────────
-- What changed, and it is not a defect in the code.
-- ─────────────────────────────────────────────────────────────────────────
--
-- `company.country`'s `CHECK (country IN ('DE','AT','CH'))` dates to migration
-- 001, where it encodes §5.1's market as DACH. M1.128 did not introduce it and
-- did not overlook it: it named `.lu` in `countries.OUT_OF_SCOPE_TLD`, refused
-- 'LU' by name in `countries.normalise`, and wrote down what widening would
-- cost. What it could not know is a fact about the BUSINESS rather than the
-- schema — that Luxembourg is in scope for creative-potato.global outreach.
--
-- So this is a scope change arriving from outside the code, which is the only
-- kind of change that can justify what it costs. `.li` stays out: nobody has
-- said Liechtenstein is in scope, and widening a constraint on the chance that
-- it might be is how a market definition stops meaning anything.
--
-- ─────────────────────────────────────────────────────────────────────────
-- Why this file is a table rebuild and why it carries a pragma line.
-- ─────────────────────────────────────────────────────────────────────────
--
-- SQLite cannot relax an inline CHECK. The only route is the 12-step procedure
-- from its own documentation — create, copy, drop, rename — and step 3's
-- `DROP TABLE` performs an implicit DELETE that fires every `ON DELETE
-- CASCADE` hanging off `company` while `foreign_keys` is ON. FIFTEEN tables
-- reference `company(id)`. Run with the pragma on, this file would empty the
-- corpus rather than widen it.
--
-- `PRAGMA foreign_keys` is a no-op inside a transaction, so the runner turns
-- it off around the whole file and runs `PRAGMA foreign_key_check` BEFORE the
-- commit, rolling back if the rebuild left anything dangling. It also turns on
-- `legacy_alter_table`, because the RENAME below otherwise reparses every view
-- to rewrite references and `company_profile` cannot be parsed while `company`
-- is dropped — measured, not predicted: the first draft of this file failed on
-- exactly that, and the runner rolled it back with the corpus intact. The
-- declaration on line 1 is what asks for both; `migrate._apply_table_rebuild`
-- is what does it.
--
-- Nothing else here is clever, and that is deliberate. The new table is the
-- old one with four characters added to one CHECK; `INSERT INTO … SELECT` is
-- column-for-column with `id` preserved, so every foreign key that pointed at
-- a row still points at the same row. The `foreign_key_check` is what proves
-- that rather than asserts it.
--
-- No trigger is ON `company` (the three that maintain `needs_review` are ON
-- `review_flag` and the two contact-block ones are ON `outreach`), and none is
-- dropped by this file. They store SQL text and re-resolve `company` by name
-- after the rename — as does the `company_profile` view. That is the whole
-- reason this rebuild is affordable and 019's was not: there, five triggers
-- rode on the table being replaced.

CREATE TABLE company_new (
    id              INTEGER PRIMARY KEY,
    domain          TEXT NOT NULL UNIQUE,      -- normalised: lowercase, no scheme, no www
    legal_name      TEXT,
    legal_form      TEXT,                      -- 'GmbH'|'GmbH & Co. KG'|'e.K.'|'Einzelunternehmen'|'GbR'|'AG'|'UG'|…
    city            TEXT,
    postal_code     TEXT,
    country         TEXT CHECK (country IN ('DE','AT','CH','LU')),
    discovery_source TEXT NOT NULL,            -- 'places' | 'seed_csv' | 'manual' | 'llm_websearch'
    discovery_query TEXT,                      -- the query that surfaced it, for provenance
    discovered_at   TEXT NOT NULL,             -- ISO8601 UTC
    excluded        INTEGER NOT NULL DEFAULT 0,
    excluded_reason TEXT,                      -- never exclude silently; always record why
    needs_review    INTEGER NOT NULL DEFAULT 0,-- derived: 1 iff an unresolved review_flag exists.
                                               -- Maintained by trigger, never written directly.
    site_domain     TEXT,                      -- migration 002
    contact_blocked INTEGER NOT NULL DEFAULT 0 -- migration 008
);

-- Column-for-column and id-preserving. Named rather than `SELECT *`, so a
-- column added to one side and not the other fails here instead of silently
-- shifting values one place to the left.
INSERT INTO company_new
    (id, domain, legal_name, legal_form, city, postal_code, country,
     discovery_source, discovery_query, discovered_at, excluded,
     excluded_reason, needs_review, site_domain, contact_blocked)
SELECT
     id, domain, legal_name, legal_form, city, postal_code, country,
     discovery_source, discovery_query, discovered_at, excluded,
     excluded_reason, needs_review, site_domain, contact_blocked
FROM company;

DROP TABLE company;
ALTER TABLE company_new RENAME TO company;

-- Every index from 001, 002 and 008, recreated verbatim. `sqlite_autoindex`
-- comes back on its own with the UNIQUE on `domain`.
CREATE INDEX idx_company_excluded ON company(excluded);
CREATE INDEX idx_company_review ON company(needs_review);
CREATE INDEX idx_company_site_domain ON company(site_domain)
    WHERE site_domain IS NOT NULL;
CREATE INDEX idx_company_contact_blocked ON company(contact_blocked);

-- The same widening on the run tag, so `--country LU` can be stored on the run
-- that applies it. `run` is referenced by foreign keys too, and the rebuild is
-- identical in shape; it is cheap because `run` has no indexes of its own.
CREATE TABLE run_new (
    id             INTEGER PRIMARY KEY,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    stage          TEXT NOT NULL,
    companies_seen INTEGER DEFAULT 0,
    places_calls   INTEGER DEFAULT 0,
    web_searches   INTEGER DEFAULT 0,          -- billed separately from tokens (§7.8)
    llm_input_tokens  INTEGER DEFAULT 0,
    llm_output_tokens INTEGER DEFAULT 0,
    est_cost_usd   REAL DEFAULT 0,             -- reserved BEFORE each LLM call, reconciled after
    aborted_reason TEXT,
    pagespeed_calls INTEGER DEFAULT 0,         -- migration 016
    country        TEXT CHECK (country IS NULL OR country IN ('DE','AT','CH','LU'))
);
INSERT INTO run_new
    (id, started_at, finished_at, stage, companies_seen, places_calls,
     web_searches, llm_input_tokens, llm_output_tokens, est_cost_usd,
     aborted_reason, pagespeed_calls, country)
SELECT
     id, started_at, finished_at, stage, companies_seen, places_calls,
     web_searches, llm_input_tokens, llm_output_tokens, est_cost_usd,
     aborted_reason, pagespeed_calls, country
FROM run;
DROP TABLE run;
ALTER TABLE run_new RENAME TO run;

-- ─────────────────────────────────────────────────────────────────────────
-- The backfill, on the same terms as 021's.
-- ─────────────────────────────────────────────────────────────────────────
--
-- `WHERE country IS NULL` for the same reason: a derived value is a
-- placeholder for a measured one and never overwrites `reconcile`'s
-- `impressum.country`. The corpus holds no `.lu` domain today, so this writes
-- nothing — it is here because the NEXT `portal init` after a discovery run
-- must not leave a `.lu` row untagged just because 022 already ran.
UPDATE company SET country = 'LU' WHERE country IS NULL AND domain LIKE '%.lu';
