-- 011 — the Phase-2 columns A2 ruled and nothing ever built.
--
-- **The number carries no history.** 010 does (see its header: the lost M5
-- stash used 010 for a different `010_phase2_writers.sql`, which survives in
--  `stash@{0}^3` and cannot reclaim the number — M1.98).
-- 011 was left free by M1.75 deliberately — the origin-keyed robots lookup
-- needed no schema change — and nothing has ever used it. Taken here.
--
-- **A2, ratified 2026-08-16, lived only in `docs/a2-phase2-signal-mapping-
-- proposal.md` until M1.76.** A proposal with a RULED heading is not a
-- specification, and the cost of that was not the proposal's tidiness:
--
--   `qual.own_brand` is a LIVE +10 rule declared `reads=()` whose predicate
--   returns `declines()` unconditionally — not because the answer is no, but
--   because NO SIGNAL KEY EXISTED TO READ. `assert_declared`, the one check
--   built to catch a rule that cannot fire, carried a NAMED EXEMPTION for it.
--   It is phase2_reachable, so its +10 sits in every unbanked company's
--   `remaining_upside`, and on the verified corpus that is what admits
--   `germanelectronic.de` to PAID extraction: 5 + 50 = 55, the B floor to the
--   point, and 45 without it. The gate was keeping a promise the schema could
--   not keep.
--
-- **Four changes, three additive and one a precedence rule.**
--
--   `own_brand`            <- brand.own_brand (num 0/1). What qual.own_brand
--                             reads. Written only when the model answers; a
--                             `null` writes nothing, which is what makes the
--                             three-state predicate possible (migration 013).
--
--   `homepage_extracted`   <- llm.homepage_extracted (num 1). THE LOAD-BEARING
--                             ONE. Every A7 guard in this project works by
--                             DECLINING TO WRITE, so the read model cannot
--                             tell "the model read the page and could not
--                             tell" from "Phase 2 never ran here" without a
--                             positive fact beside the silence. This is
--                             `content.blog_search_exhaustive`'s idiom (M1.14)
--                             and it exists for the same reason. §5.4's
--                             `phase2_input_settled` and §6.1's three-state
--                             predicates both read it and nothing else can
--                             answer what it answers.
--
--   `one_line_offer`       <- offer.one_line (text). Unscored, and added
--                             anyway. The view's own comment says to add a
--                             column "when a new signal key enters the scoring
--                             rules"; that is a FLOOR, not a ceiling. M4's
--                             `leadlist` already reads platform, blog_last_post
--                             and ai_* off this view for display, and adding a
--                             one-off signal query beside it for a single
--                             display field would give §9's page two read
--                             paths. A2 item 7, ratified as written.
--
--   `legal_form`           <- CHANGED: COALESCE(impressum.legal_form, c.legal_form).
--                             §5.5b already rules the LLM wins on disagreement.
--                             The MECHANISM is the point: if extract-p2 also
--                             UPDATEd company.legal_form, the ruling would be
--                             implemented as a RACE between two writers, and
--                             re-running the free extract-p1 after a paid
--                             reconcile would silently revert paid work to a
--                             regex. Resolved here instead, in one expression,
--                             in the place that already owns latest-wins — and
--                             extract-p1 cannot revert it because it never
--                             touches the signal. A2 item 8.
--
-- **`impressum.gf_count` and `site.owner_named` need no column**: `gf_count`
-- and `owner_named` already exist and only their writers are missing. Note the
-- KEY behind `owner_named` changes — `impressum.owner_named` -> `site.owner_named`
-- (A2 item 3), because §6.1's third disjunct is "owner named ON SITE" and the
-- value comes off the homepage; a key prefixed `impressum.` whose evidence_url
-- is the homepage asserts a provenance the value does not have (M1.42 in
-- miniature). The key has never been written, so this is a rename with no data
-- to migrate. The COLUMN name is unchanged, so no rule changes.
--
-- **Deliberately NOT added**, per A2 §4: impressum.legal_name, .city,
-- .postal_code, .country, .register_*, .owner_name_present, offer.audience,
-- offer.product_categories, agency.footer_credit_llm. Nothing scores them; the
-- projections §9 needs land on `company` columns the page already reads; and
-- `agency.footer_credit_llm` is EXCLUDED ON PURPOSE (M1.77) — giving it a view
-- column is the first half of giving it a reader on a -20 rule.
--
-- Everything migrations 006 and 007 established is unchanged and their notes
-- still apply: per stage, per company, finished runs only, by omission as well
-- as by value. A view has no state, so this is a DROP and a CREATE.

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
