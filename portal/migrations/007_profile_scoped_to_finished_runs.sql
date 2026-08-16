-- 007 — the stage-scoped profile trusts only runs that **finished**.
--
-- **006 was half a fix, and the missing half inverts it (M1.39).** 006 scoped
-- latest-per-key to the latest run of that key's own stage, so that a stage
-- which *declines* to write a key retracts the value an earlier run wrote. That
-- is the mechanism every A7 guard depends on. It also trusts the latest run to
-- have written everything it was going to write — and a crashed run has not.
--
-- Per §5's idempotency contract (D6) a crash-then-restart mints a **new
-- `run_id`**. So a run that reached 10 of 13 companies and died becomes the
-- latest run of its stage, and the three it never got to read as *retractions*
-- rather than as *incompleteness*. The direction of the error flips: 006 exists
-- to stop a stale value persisting, and un-narrowed it makes a live value
-- vanish. Worse, it vanishes silently, because absence is exactly what the
-- guards are built to read as "do not fire" — the pipeline would go quiet about
-- a company for no reason it could name.
--
-- **The rule, narrowed: the authoritative run for a (company, stage) is the
-- latest one that ran to completion.** `run.finished_at IS NOT NULL` and no
-- `aborted_reason`. A run still in flight, a run that crashed, and a run that
-- stopped itself against a ceiling are all the same thing here: a set of
-- observations nobody has claimed is complete.
--
-- **A partial run is ignored wholesale, including the keys it did write.** The
-- alternative — take its keys where it has them and fall back per key — is a
-- mixture of two runs' beliefs, and reconstructs precisely the defect 006
-- closed: a key the crashed run would have declined to write, served from an
-- older run that did. Either a run's account of a company is complete and
-- authoritative, or it is not consulted. Nothing is lost: `signal` is still
-- append-only, every observation is still queryable, and the next complete run
-- of that stage supersedes it.
--
-- **This makes `finished_at` load-bearing, so it had to become honest.**
-- `fetch.run` and `score.run` set it from a `finally` block, which marks a
-- crashed run finished — the exact state this migration reads as trustworthy.
-- Both now set it on the success path only and record `aborted_reason`
-- otherwise (`extract.run` was already correct). A migration that depends on a
-- column meaning something must not ship before the column means it.
--
-- Everything else about 006 is unchanged and its note still applies: per stage,
-- per company, by omission as well as by value.
--
-- A view has no state, so this is a DROP and a CREATE.

DROP VIEW company_profile;

CREATE VIEW company_profile AS
WITH observed AS (
    SELECT s.*, r.stage, r.finished_at, r.aborted_reason
    FROM signal s JOIN run r ON r.id = s.run_id
),
-- The authoritative run for each (company, stage): the latest **complete** one.
-- MAX is the latest because `run.id` is monotonic; `--resume` reuses a run id
-- rather than minting a higher one, which is what makes a resumed run supersede
-- correctly — and a resumed run that reaches the end sets `finished_at`, which
-- is what makes it eligible at all.
current_run AS (
    SELECT company_id, stage, MAX(run_id) AS run_id FROM observed
    WHERE finished_at IS NOT NULL AND aborted_reason IS NULL
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
