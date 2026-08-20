-- 012 — `company_profile` stops serving values the tool does not believe.
--
-- The number carries no history: nothing has ever used 012.
--
-- **A4 (M1.79).** §5.5b has ruled since v0.3 that an LLM value whose evidence
-- is not literally on the page is *"discarded and the signal written with
-- `confidence=0` for review"*. **`discarded` had no implementation.** This view
-- pivots latest-per-key with no `confidence` predicate anywhere in it, at any
-- of its three revisions — so the row §5.5b writes to say *the tool checked
-- this and does not believe it* would have been handed to `evaluate` as though
-- it did.
--
-- The register cited `001_initial_schema.sql:168` for this. **That pointer is
-- stale** — the live view is migration 007's — and the claim was true at the
-- new address, which is why the row was re-derived rather than trusted.
--
-- **Measured before the change: 2,404 signal rows in the stored corpus, every
-- one `method=deterministic` with `confidence IS NULL`.** The filter is a no-op
-- on today's data, and that is the argument for landing it now: M5 is what
-- makes `confidence != 1` reachable at all, so this is the last moment it can
-- be added without a migration that also has to repair rows.
--
-- **DIRECTION OF ERROR: too strict, and checkably so.** A filtered value is a
-- signal scoring does not see, so its rule declines or abstains. That is
-- uniformly the too-LOW direction, and it is not a hope — it is a property of
-- the mapping: every §5.5b key that any §6 rule reads feeds a rule that AWARDS
-- points (`impressum.legal_form` +15, `impressum.gf_count` +15,
-- `site.owner_named` +15, `brand.own_brand` +10), and the one Phase-2 key on a
-- rule that SUBTRACTS is `agency.footer_credit_llm`, which M1.77 gives no
-- reader at all. So this filter cannot withhold a penalty, and therefore cannot
-- move a company toward outbound contact. Too loose is the direction that
-- scores a stranger on a value the tool itself rejected.
--
-- **And the loss is visible, which is A7's condition on every abstention.** The
-- rejected row stays in `signal` and stays queryable; §9 renders `confidence=0`
-- red on the row expansion; and `llm.impressum_extracted` /
-- `llm.homepage_extracted` are written with `confidence = 1` — they are facts
-- about the STAGE, not judgements about a page, and a stage cannot be wrong
-- about whether it ran — so they survive the filter and keep "the extraction
-- ran and its answer was rejected" distinguishable from "Phase 2 never ran
-- here". Without them this filter would convert a rejection into a silence and
-- §6.1's three-state predicates would have nothing to read.
--
-- A view has no state, so this is a DROP and a CREATE. Everything migrations
-- 006, 007 and 011 established is unchanged.

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
    MAX(CASE WHEN l.key='ai.brand_mentions'          THEN l.value_num  END) AS ai_brand_mentions,
    MAX(CASE WHEN l.key='ai.queries_checked'         THEN l.value_num  END) AS ai_queries_checked
FROM company c
LEFT JOIN latest l ON l.company_id = c.id AND l.rn = 1
GROUP BY c.id;
