-- 005 — `blog_undetectable` becomes a §6.4 review reason, and `company_profile`
-- gains the two columns §6.2's blog ladder now reads.
--
-- **The reason.** M1.14 held that `content.blog_exists = 0` is the one place in
-- the ruleset where the pipeline writes a confident measurement it has
-- documented that it cannot make, fires the largest award in ruleset v3 (+25)
-- on it, and tells nobody. §6.2 now abstains: where the search that produced
-- the `0` could not have been exhaustive, `opp.no_blog` fires in neither
-- direction and the whole ladder below it is suppressed. A7 (§5) says that is
-- two thirds of the rule — the third part is that a person is told, and this is
-- the routing.
--
-- **The asymmetry that makes it worth the points.** A false `opp.no_blog` tells
-- a shop that publishes weekly that it has no blog, and thereby manufactures
-- the exact opportunity the outreach letter is about. A false negative ranks a
-- lead lower. Same shape as M1.4's `/p/` and A5.5, at its most extreme, and it
-- resolves the same way: where the two errors are not equally bad, take the
-- safe side.
--
-- **It is a fifth reason and not a reuse of either existing blog reason, for
-- the same purpose `blog_date_unbounded` was distinct from
-- `blog_date_unparseable`: a different thing is known, so a different
-- resolution applies.**
--
--   `blog_date_unparseable` — there is an index, its dates are unreadable.
--                             Open it and read the top of it.
--   `blog_date_unbounded`   — there is a date, it cannot bound the rule.
--                             One question: is this blog actually dead?
--   `blog_undetectable`     — we do not know whether there is a blog at all.
--                             A different question, answered somewhere else:
--                             does this shop publish, anywhere, under any name?
--
-- Sent to the wrong one of these a human opens the wrong page. `raised_note`
-- (004) carries the one line that makes the difference visible in the queue —
-- which instrument was missing, and therefore where to look instead.
--
-- **What the reason may never claim.** Not "this shop has no blog", and not
-- "we proved nothing is there". A blog on an unlinked subdomain is undetectable
-- by construction — `zecplus.de` serves four sitemap shards, none of which
-- mentions `blog.zecplus.de`. The reason means *the search could not have been
-- exhaustive*, and even an exhaustive one means only "we looked everywhere we
-- can look".
--
-- **The view columns.** `content.blog_last_post_basis` has been read by §6.2
-- since M1.32 and was never added to `company_profile`, so the guard it
-- implements was unreachable from the read model scoring uses — §10.4's
-- "a rule that cannot fire reads as implemented" defect, one layer down.
-- `content.blog_search_exhaustive` would have joined it. Both are added here,
-- with the view's own instruction followed: a new key that enters the scoring
-- rules gets a column.
--
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt — the same
-- dance as 002, 003 and 004, and for the same reason.

DROP TRIGGER trg_review_flag_after_insert;
DROP TRIGGER trg_review_flag_after_update;
DROP TRIGGER trg_review_flag_after_delete;

CREATE TABLE review_flag_new (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL CHECK (reason IN (
                      'no_impressum','possible_marketplace_only','blog_date_unparseable',
                      'domain_moved','duplicate_site','catalog_not_measurable',
                      'blog_date_unbounded','blog_undetectable')),
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
-- `company_profile`, rebuilt with the two guard columns. A view has no
-- state, so this is a DROP and a CREATE rather than a migration of data.
-- ─────────────────────────────────────────────────────────────

DROP VIEW company_profile;

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
    -- M1.32's guard: 'index' or 'both' bounds the date from above, 'article'
    -- does not, and an absent value must read as unbounded so a run written
    -- before the guard fails safe.
    MAX(CASE WHEN l.key='content.blog_last_post_basis' THEN l.value_text END) AS blog_last_post_basis,
    MAX(CASE WHEN l.key='content.blog_post_count'    THEN l.value_num  END) AS blog_post_count,
    MAX(CASE WHEN l.key='content.blog_exists'        THEN l.value_num  END) AS blog_exists,
    -- M1.14's guard: 1 licenses `opp.no_blog`, and anything else — 0, or the
    -- signal missing entirely — suppresses the whole blog ladder. `value_text`
    -- carries the reason, prefixed `limit:` or `transient:` (§5.3).
    MAX(CASE WHEN l.key='content.blog_search_exhaustive' THEN l.value_num END) AS blog_search_exhaustive,
    MAX(CASE WHEN l.key='content.blog_search_exhaustive' THEN l.value_text END) AS blog_search_limit,
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
