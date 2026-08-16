-- 009 — `fetch_persistently_failing`: where A7b's retry policy runs out.
--
-- **Ratified 2026-08-16 (M1.34, A7b).** A7b says a transient abstention retries
-- and is flagged only once it persists — **N = 3 consecutive runs on 3 distinct
-- days**, reasoned out in §5. The counting policy has been binding since M1.34.
-- The *reason it routes to* was the part left open, and it has waited a
-- milestone: for three runs a company can have a rule going quiet with nobody
-- told, because by run 3 the retry policy is exhausted and what is left is a
-- measurement limit wearing a transient's clothes.
--
-- **One reason for all three instances, and that is the discriminator working,
-- not being ignored.** The blog trio are three reasons because they send a
-- person to answer three different questions. These three —
--
--   `schema.product_present`  a product page was selected and never returned 200
--   `content.blog_exists`     a blog was located and its index never returned 200
--   `schema.article_present`  an article was selected and never returned 200
--
-- — send a person to answer **one** question, and it is the same question every
-- time: *does this URL load for you?* Same page in a browser, same answer, same
-- next step. `raised_note` carries which URL and how many runs, which is the
-- part that differs.
--
-- The trade, stated: `uq_review_flag` is per (company, reason), so a company
-- whose product page and blog index both go persistently missing gets one row,
-- noted for whichever tripped first. Accepted — the resolution is a person
-- opening that shop's pages, and they will see both.
--
-- **This one does not block outbound contact (008's third axis).** All three
-- rules *award* points for an absence — +10, +25, +8 — so the abstention
-- withholds an award and the score is too **low**. That is a ranking delay the
-- queue fixes, not an error that leaves the building. Only a too-high
-- abstention gets 008's treatment, and this is why the axis is recorded per
-- reason rather than assumed per abstention.
--
-- SQLite cannot alter a CHECK constraint, so `review_flag` is rebuilt.
--
-- **008's outreach triggers have to come down with it, and that is a feature.**
-- They read `review_flag` by name, so SQLite refuses to drop the table while
-- they stand. Every future migration that adds a reason therefore has to look
-- at the block and put it back deliberately — it cannot be left behind by
-- accident, because the migration will not apply. A trigger reading the cached
-- `company.contact_blocked` instead would have survived this rebuild silently,
-- which is the worse trade: a block that quietly stops blocking.

DROP TRIGGER trg_outreach_blocked_before_insert;
DROP TRIGGER trg_outreach_blocked_before_update;
DROP TRIGGER trg_review_flag_after_insert;
DROP TRIGGER trg_review_flag_after_update;
DROP TRIGGER trg_review_flag_after_delete;

CREATE TABLE review_flag_new (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL CHECK (reason IN (
                      'no_impressum','possible_marketplace_only','blog_date_unparseable',
                      'domain_moved','duplicate_site','catalog_not_measurable',
                      'blog_date_unbounded','blog_undetectable',
                      'blog_cadence_unmeasurable','fetch_persistently_failing')),
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
    (id, company_id, reason, raised_run_id, raised_at, raised_note, resolved_at,
     resolved_by_human, resolved_note)
SELECT id, company_id, reason, raised_run_id, raised_at, raised_note, resolved_at,
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

-- 008's block, restored unchanged. Live against the flags, not against the
-- cache, so a reason added to `contact_blocking_reason` later still blocks.

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
