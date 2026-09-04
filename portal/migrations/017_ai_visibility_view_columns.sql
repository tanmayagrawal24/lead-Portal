-- 017 — the four `ai.*` columns §8's brief reads, and `company_profile`
--       learns them in the same commit as their writer (M1.45(c)).
--
-- The number carries no history: nothing has ever used 017. M1.101 named it
-- as the next free number after 016.
--
-- **THIS MIGRATION SHIPS IN THE SAME COMMIT AS ITS WRITER.** `portal ai-check`
-- (§5.5c, M6) writes six keys. Two — `ai.queries_checked` and
-- `ai.brand_mentions` — have been projected since migration 001 because
-- `opp.ai_invisible` reads them, and that rule has been dormant behind them
-- for six milestones (§10.6). The other four were never projected, because
-- nothing scored them; §5.5c says they *"exist solely so the research brief
-- can state its basis (§8)"*, and §8 says an export missing any of the three
-- basis fields *"must fail, not degrade gracefully"*. A brief cannot fail on
-- a column it cannot read, so the reader lands here with the writer.
--
-- **The view's scoping is unchanged and it is why `ai-check` is its own
-- `run.stage`.** Migration 006 serves each key from the latest finished run
-- per (company, stage); M1.101(a) already ruled that a measurement sharing
-- `extract_p2`'s stage would retract the LLM extraction's signals per company
-- whichever finished second. The same argument, the same answer: the stage is
-- `'ai_check'`, and a re-check supersedes only `ai.*`.
--
-- **`ai.checked_at` is projected from `value_date`**, the column §4 gives a
-- `date`-typed signal; the other three are `value_text`. The competitor list
-- is comma-separated as §5.5c specifies and the query list pipe-separated,
-- because a query can contain a comma and a brand name cannot sensibly
-- contain a pipe.
--
-- A view has no state, so this is a DROP and a CREATE. Everything migrations
-- 006, 007, 011 and 012 established is unchanged.

DROP VIEW company_profile;

CREATE VIEW company_profile AS
WITH observed AS (
    SELECT s.*, r.stage, r.finished_at, r.aborted_reason
    FROM signal s JOIN run r ON r.id = s.run_id
),
current_run AS (
    SELECT company_id, stage, MAX(run_id) AS run_id FROM observed
    WHERE finished_at IS NOT NULL AND aborted_reason IS NULL
    GROUP BY company_id, stage
),
-- **A4, and WHERE the predicate goes is the whole finding.** It is applied
-- HERE, after `current_run` has already chosen the authoritative run — never
-- inside `observed`. Filtering earlier would remove a Phase-2 run's rejected
-- rows from the set `current_run` takes its MAX(run_id) from, so a run whose
-- extractions all failed verification would stop being its stage's authority
-- and an OLDER run's values would be served as current. That is migration
-- 006's defect exactly, re-created by the guard meant to strengthen it.
latest AS (
    SELECT o.*,
           ROW_NUMBER() OVER (PARTITION BY o.company_id, o.key
                              ORDER BY o.observed_at DESC, o.id DESC) AS rn
    FROM observed o
    -- §4: `confidence` is NULL for deterministic and 0-1 for llm. NULL passes,
    -- or Phase 1 is blanked entirely. `0` is §5.5b's record of a value that was
    -- verified and REJECTED, and no threshold above 0 is invented — a
    -- plausibility cut chosen on zero observations is M1.4's error.
    JOIN current_run c
      ON  c.company_id = o.company_id
      AND c.stage      = o.stage
      AND c.run_id     = o.run_id
    WHERE o.confidence IS NULL OR o.confidence > 0
)
SELECT
    c.id AS company_id,
    c.domain,
    c.country,
    -- A2 item 8: the LLM wins on disagreement, resolved here rather than by a
    -- second UPDATE racing the first.
    COALESCE(MAX(CASE WHEN l.key='impressum.legal_form' THEN l.value_text END),
             c.legal_form) AS legal_form,
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
    -- A2 item 3: `site.owner_named`, not `impressum.owner_named` — the value is
    -- read off the HOMEPAGE and the key must not claim otherwise. Column name
    -- unchanged, so §6.1 disjunct 3 is untouched.
    MAX(CASE WHEN l.key='site.owner_named'           THEN l.value_num  END) AS owner_named,
    -- A2: the Phase-2 columns.
    MAX(CASE WHEN l.key='brand.own_brand'            THEN l.value_num  END) AS own_brand,
    MAX(CASE WHEN l.key='llm.homepage_extracted'     THEN l.value_num  END) AS homepage_extracted,
    MAX(CASE WHEN l.key='llm.impressum_extracted'    THEN l.value_num  END) AS impressum_extracted,
    MAX(CASE WHEN l.key='offer.one_line'             THEN l.value_text END) AS one_line_offer,
    -- 017: §5.5c's second query input. Migration 011 left it out under A2 §4
    -- ("nothing scores them") — a ruling about readers, and `ai-check` is now
    -- one. Still unscored. `impressum.legal_name` stays OUT: A2 §4 routes it
    -- to `company.legal_name`, which `reconcile` writes and the brand match
    -- reads from there.
    MAX(CASE WHEN l.key='offer.product_categories'   THEN l.value_text END) AS product_categories,
    MAX(CASE WHEN l.key='ai.brand_mentions'          THEN l.value_num  END) AS ai_brand_mentions,
    MAX(CASE WHEN l.key='ai.queries_checked'         THEN l.value_num  END) AS ai_queries_checked,
    -- 017: §5.5c's three basis fields plus the competitor list. Unscored; they
    -- exist so §8's brief can state its basis and refuse to ship without one.
    MAX(CASE WHEN l.key='ai.competitors_mentioned'   THEN l.value_text END) AS ai_competitors_mentioned,
    MAX(CASE WHEN l.key='ai.query_text'              THEN l.value_text END) AS ai_query_text,
    MAX(CASE WHEN l.key='ai.checked_at'              THEN l.value_date END) AS ai_checked_at,
    MAX(CASE WHEN l.key='ai.model_used'              THEN l.value_text END) AS ai_model_used
FROM company c
LEFT JOIN latest l ON l.company_id = c.id AND l.rn = 1
GROUP BY c.id;
