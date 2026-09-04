-- 019 — `manufacturer_not_shop`: the false-positive class the second discovery
--       source produces, as a REVIEW FLAG and deliberately not as an exclusion.
--
-- **THIS MIGRATION SHIPS IN THE SAME COMMIT AS ITS WRITER** (M1.45(c)).
-- `extract.Extractor._shop_signals` raises it.
--
-- ─────────────────────────────────────────────────────────────────────────
-- Measured before the change rather than argued (M1.119's first live run).
-- ─────────────────────────────────────────────────────────────────────────
--
-- Five `discover --source websearch` runs added 69 companies. Scored, the
-- rubric put `bosch-professional.com`, `makita.de` and `metabo.com` in band C
-- at 30 points, most of it `opp.no_blog +25` — a rule written to find a small
-- shop with no content strategy, firing on global manufacturers whose
-- marketing simply does not live on that domain. They are not leads. They are
-- a class the rubric was never designed against, and M1.119 predicted the
-- class in general terms one unit before it appeared in the table.
--
-- ─────────────────────────────────────────────────────────────────────────
-- Why a flag and not an exclusion, and why not a rubric change.
-- ─────────────────────────────────────────────────────────────────────────
--
-- **A flag is reversible and a rubric decision is not.** `excluded` removes a
-- row from the lead list; a scoring rule changes every score in the corpus,
-- including the ones already reviewed by a human. Both would be acting on ONE
-- run's evidence from a source that has existed for a day. A flag says *"look
-- at this"* to a person who can disagree in one click, and §9's queue already
-- exists to be read (M1.41).
--
-- **And the detector cannot be trusted to exclude.** `detect_platform` returns
-- `None` for Shopware 5 and says in its own docstring that the caller must not
-- read that as *"not a shop"* (M1.11). So the flag is raised on a POSITIVE
-- absence — no cart or checkout marker anywhere on the homepage AND no product
-- URL located in the catalogue — and even then only for a row whose
-- `discovery_source` is `llm_websearch`. A seeded or Places-discovered company
-- with a quiet homepage is not this class and is not flagged.
--
-- The origin condition is part of the RULE, not a filter on it: the claim is
-- *"this source returns manufacturers"*, and a flag that fired on seed rows
-- would be making a different, unmeasured claim about the corpus at large.

-- ─────────────────────────────────────────────────────────────────────────
-- Five triggers ride on this table and all five must be rebuilt with it.
-- ─────────────────────────────────────────────────────────────────────────
--
-- Three are ON `review_flag` and go with the DROP. The other two are ON
-- `outreach` and merely REFERENCE `review_flag` — they survive the drop as
-- schema text pointing at a table that momentarily does not exist, and the
-- next statement that touches `outreach` fails with *"no such table:
-- main.review_flag"*. Measured, not predicted: the first draft of this
-- migration omitted them and 23 tests failed in exactly that way.
--
-- `manufacturer_not_shop` is deliberately NOT added to
-- `contact_blocking_reason`. It says *"this may not be a shop"*, which is a
-- reason to look before writing, not a reason the score is too high — and that
-- table's one existing member (`blog_cadence_unmeasurable`) is there because
-- the SCORE is unsafe, which is a different claim. §9 shows the flag either
-- way.

DROP TRIGGER IF EXISTS trg_review_flag_after_insert;
DROP TRIGGER IF EXISTS trg_review_flag_after_update;
DROP TRIGGER IF EXISTS trg_review_flag_after_delete;
DROP TRIGGER IF EXISTS trg_outreach_blocked_before_insert;
DROP TRIGGER IF EXISTS trg_outreach_blocked_before_update;

CREATE TABLE review_flag_new (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL CHECK (reason IN (
                      'no_impressum','possible_marketplace_only','blog_date_unparseable',
                      'domain_moved','duplicate_site','catalog_not_measurable',
                      'blog_date_unbounded','blog_undetectable',
                      'blog_cadence_unmeasurable','fetch_persistently_failing',
                      'own_brand_undetermined','owner_named_undetermined',
                      'manufacturer_not_shop')),
    raised_run_id INTEGER NOT NULL REFERENCES run(id),
    raised_at     TEXT NOT NULL,
    raised_note   TEXT,
    resolved_at   TEXT,
    resolved_by_human INTEGER CHECK (resolved_by_human IN (0,1)),
    resolved_note TEXT,
    CHECK ((resolved_at IS NULL     AND resolved_by_human IS NULL)
        OR (resolved_at IS NOT NULL AND resolved_by_human IS NOT NULL))
);

INSERT INTO review_flag_new
    (id, company_id, reason, raised_run_id, raised_at, raised_note,
     resolved_at, resolved_by_human, resolved_note)
SELECT id, company_id, reason, raised_run_id, raised_at, raised_note,
       resolved_at, resolved_by_human, resolved_note
FROM review_flag;

DROP TABLE review_flag;
ALTER TABLE review_flag_new RENAME TO review_flag;

CREATE UNIQUE INDEX uq_review_flag ON review_flag(company_id, reason);
CREATE INDEX idx_review_flag_open ON review_flag(company_id) WHERE resolved_at IS NULL;

-- Recreated verbatim from 001 — the bodies are unchanged; only the table they
-- sit on was rebuilt.
CREATE TRIGGER trg_review_flag_after_insert AFTER INSERT ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ), contact_blocked = EXISTS (
        SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
        WHERE f.company_id = company.id AND f.resolved_at IS NULL
    ) WHERE id = NEW.company_id;
END;

CREATE TRIGGER trg_review_flag_after_update AFTER UPDATE ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ), contact_blocked = EXISTS (
        SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
        WHERE f.company_id = company.id AND f.resolved_at IS NULL
    ) WHERE id IN (OLD.company_id, NEW.company_id);
END;

CREATE TRIGGER trg_review_flag_after_delete AFTER DELETE ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ), contact_blocked = EXISTS (
        SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
        WHERE f.company_id = company.id AND f.resolved_at IS NULL
    ) WHERE id = OLD.company_id;
END;

CREATE TRIGGER trg_outreach_blocked_before_insert BEFORE INSERT ON outreach
WHEN EXISTS (
    SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
    WHERE f.company_id = NEW.company_id AND f.resolved_at IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'outreach blocked: an unresolved review flag leaves this company''s score too high (A7, §6.4) — resolve it first');
END;

CREATE TRIGGER trg_outreach_blocked_before_update BEFORE UPDATE OF company_id ON outreach
WHEN EXISTS (
    SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
    WHERE f.company_id = NEW.company_id AND f.resolved_at IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'outreach blocked: an unresolved review flag leaves this company''s score too high (A7, §6.4) — resolve it first');
END;
