-- 006 — `company_profile` resolves per stage, and gains the blog-cadence keys.
--
-- **The defect (M1.36).** `signal` is append-only and this view pivoted the
-- latest observation per key across *all* runs. §5 calls that harmless. It
-- stopped being harmless the moment a stage could **stop** writing a key —
-- which is exactly what every A7 guard added since M1.32 does: A5.5 declines to
-- write `schema.product_present`, A6.1 declines `schema.article_present`, M1.34
-- declines `content.blog_exists`. Declining to write is the whole mechanism.
--
-- A signal that is no longer written was **not retracted**. The view kept
-- serving it, from whichever run last wrote it, forever. Two live instances on
-- the stored corpus, both re-animating bugs the code had already fixed:
--
--   `agency.footer_credit`  smoke2u.de, verpackungskoenig.de, germanelectronic.de
--                           still served "… Powered by JTL-Shop …" from run 5,
--                           written before the platform-credit exclusion landed.
--                           §6.3 would have fired neg.has_agency for -20 on
--                           three JTL shops for their choice of shop system —
--                           §10.4's named defect, arriving through the read
--                           model instead of through the parser.
--
--   `content.blog_exists`   zecplus.de still served `0` from run 18, written
--                           before M1.34. opp.no_blog would have taken +25 on a
--                           shop whose blog we now detect and simply have not
--                           fetched yet. That is the bug M1.34 closed, one week
--                           old, resurrected by a view.
--
-- zecplus.de survives only because M1.34 writes `content.blog_search_exhaustive`
-- in the same breath as it declines `content.blog_exists`, so rung 1 abstains on
-- the fresh qualifier. That is a paired guard working, not the read model
-- working. Nothing pairs `agency.footer_credit`, and nothing would pair
-- `schema.article_present`.
--
-- **The rule.** Latest-per-key is scoped to the **latest run of that key's own
-- stage, per company**. Three properties, each load-bearing:
--
--   *Per stage*, so a Phase-1 re-extract cannot blank Phase-2 signals, and
--   neither touches `catalog.product_sample_url`, which `fetch` writes.
--
--   *Per company*, so a partial run or a `--resume` over three companies cannot
--   supersede the other ten into NULL. Without this the fix would be worse than
--   the defect.
--
--   *By omission as well as by value*, which is the point: a later run of a
--   stage is authoritative for everything that stage owns, including the keys it
--   deliberately did not write.
--
-- Nothing about the append-only guarantee changes. Every observation stays in
-- `signal` and stays queryable; the view stops presenting superseded ones as
-- current, which is all it was ever meant to do.
--
-- **The new columns** carry M3's audit findings: `content.blog_post_dates`
-- feeds §6.3's `neg.active_content`, which until now had no data path at all
-- (B7's shape — a rule in the table that cannot be computed). Dates rather than
-- a pre-computed recency count, so §5's "scoring is a pure recompute" stays
-- true: a stored count of "posts in the last six months" decays as the window
-- moves, and a recompute later would be quietly wrong.
--
-- A view has no state, so this is a DROP and a CREATE.

DROP VIEW company_profile;

CREATE VIEW company_profile AS
WITH observed AS (
    SELECT s.*, r.stage FROM signal s JOIN run r ON r.id = s.run_id
),
-- The authoritative run for each (company, stage). MAX is the latest because
-- `run.id` is monotonic; `--resume` reuses a run id rather than minting a
-- higher one, which is what makes a resumed run supersede correctly.
current_run AS (
    SELECT company_id, stage, MAX(run_id) AS run_id FROM observed
    GROUP BY company_id, stage
),
latest AS (
    SELECT o.*,
           ROW_NUMBER() OVER (PARTITION BY o.company_id, o.key
                              ORDER BY o.observed_at DESC, o.id DESC) AS rn
    FROM observed o
    JOIN current_run c
      ON  c.company_id = o.company_id
      AND c.stage      = o.stage
      AND c.run_id     = o.run_id
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
    -- M3: every distinct post date the index exposes, sorted ISO text, count in
    -- value_num. `neg.active_content` (-25) had no data path before this.
    MAX(CASE WHEN l.key='content.blog_post_dates'    THEN l.value_text END) AS blog_post_dates,
    MAX(CASE WHEN l.key='content.blog_post_dates'    THEN l.value_num  END) AS blog_dated_posts,
    MAX(CASE WHEN l.key='schema.article_present'     THEN l.value_num  END) AS article_schema,
    MAX(CASE WHEN l.key='schema.product_present'     THEN l.value_num  END) AS product_schema,
    MAX(CASE WHEN l.key='i18n.hreflang_count'        THEN l.value_num  END) AS hreflang_count,
    MAX(CASE WHEN l.key='perf.lighthouse_performance' THEN l.value_num END) AS lighthouse_perf,
    MAX(CASE WHEN l.key='agency.footer_credit'       THEN l.value_text END) AS agency_credit,
    MAX(CASE WHEN l.key='catalog.product_url_count'  THEN l.value_num  END) AS product_url_count,
    MAX(CASE WHEN l.key='catalog.not_measurable'     THEN l.value_num  END) AS catalog_not_measurable,
    MAX(CASE WHEN l.key='reviews.count'              THEN l.value_num  END) AS review_count,
    MAX(CASE WHEN l.key='reviews.trusted_shops'      THEN l.value_num  END) AS trusted_shops,
    MAX(CASE WHEN l.key='impressum.gf_count'         THEN l.value_num  END) AS gf_count,
    MAX(CASE WHEN l.key='impressum.owner_named'      THEN l.value_num  END) AS owner_named,
    MAX(CASE WHEN l.key='ai.brand_mentions'          THEN l.value_num  END) AS ai_brand_mentions,
    MAX(CASE WHEN l.key='ai.queries_checked'         THEN l.value_num  END) AS ai_queries_checked
FROM company c
LEFT JOIN latest l ON l.company_id = c.id AND l.rn = 1
GROUP BY c.id;
