-- 002 — a seeded domain that has moved to another registrable domain (P2).
--
-- Two changes, both required by the same finding: two of thirteen domains in
-- the first crawl now serve a different registrable domain, and every parser
-- anchored on `same_site(url, company.domain)` went quiet without erroring.
--
-- See docs/p2-moved-domain-proposal.md and §5.1/§5.2/§6.4.

-- ─────────────────────────────────────────────────────────────
-- 1. The effective site host, when it differs from the seeded one.
--
-- A separate nullable column rather than a mutated `company.domain`, because
-- `domain` is UNIQUE (so mutation can collide), is the human key across the UI
-- and outreach, and names the `data/artifacts/{domain}/` directory — mutating
-- it would orphan every body already stored, silently. `domain` keeps the
-- seeded value permanently; provenance is a fact about the lead list that a
-- rewrite would destroy.
-- ─────────────────────────────────────────────────────────────
ALTER TABLE company ADD COLUMN site_domain TEXT;

-- Not UNIQUE: the collision it would raise is real but is resolved in the
-- fetch stage, deliberately and with a recorded reason, rather than by an
-- IntegrityError from inside a worker thread (§6.4, `duplicate_site`).
CREATE INDEX idx_company_site_domain ON company(site_domain)
    WHERE site_domain IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- 2. Two new review reasons. SQLite cannot alter a CHECK constraint, so the
--    table is rebuilt: the 12-step ALTER dance, minus the steps that do not
--    apply (no views select from it, and its only foreign keys are outbound).
--
--    `domain_moved`  — adoption always raises it. Whether a moved shop is still
--                      the lead we meant to approach is a judgement about the
--                      lead list, not one the crawler may make. Same principle
--                      as `blog_date_unparseable`: where the pipeline cannot
--                      know, route to a human rather than guess.
--    `duplicate_site`— raised on the row that OWNS a host another row tried to
--                      adopt, so a merge target is visible rather than silently
--                      accumulating duplicates pointed at it.
-- ─────────────────────────────────────────────────────────────
DROP TRIGGER trg_review_flag_after_insert;
DROP TRIGGER trg_review_flag_after_update;
DROP TRIGGER trg_review_flag_after_delete;

CREATE TABLE review_flag_new (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL CHECK (reason IN (
                      'no_impressum','possible_marketplace_only','blog_date_unparseable',
                      'domain_moved','duplicate_site')),
    raised_run_id INTEGER NOT NULL REFERENCES run(id),
    raised_at     TEXT NOT NULL,
    resolved_at   TEXT,
    resolved_by_human INTEGER CHECK (resolved_by_human IN (0,1)),
    resolved_note TEXT,
    CHECK ((resolved_at IS NULL     AND resolved_by_human IS NULL)
        OR (resolved_at IS NOT NULL AND resolved_by_human IS NOT NULL))
);

INSERT INTO review_flag_new
    (id, company_id, reason, raised_run_id, raised_at, resolved_at,
     resolved_by_human, resolved_note)
SELECT id, company_id, reason, raised_run_id, raised_at, resolved_at,
       resolved_by_human, resolved_note
FROM review_flag;

DROP TABLE review_flag;
ALTER TABLE review_flag_new RENAME TO review_flag;

CREATE UNIQUE INDEX uq_review_flag ON review_flag(company_id, reason);
CREATE INDEX idx_review_flag_open ON review_flag(company_id) WHERE resolved_at IS NULL;

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
