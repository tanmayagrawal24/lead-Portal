-- 020 — the `extract-p2` run stage names its PURPOSE, so one purpose's signals
--       cannot mask the other's.
--
-- **THIS MIGRATION SHIPS IN THE SAME COMMIT AS ITS WRITER** (M1.45(c)).
--
-- ─────────────────────────────────────────────────────────────────────────
-- Measured, not predicted (M1.124).
-- ─────────────────────────────────────────────────────────────────────────
--
-- `company_profile` chooses ONE authoritative run per `(company_id, stage)`,
-- taking `MAX(run_id)` over finished, un-aborted runs. That is A4's rule and it
-- is right: a re-run that fails to write a key must not leave an older run's
-- value standing as current, because a stale value served as fresh is the
-- failure the whole view exists to prevent.
--
-- The rule assumes **one stage writes one vocabulary.** `extract-p2` broke that
-- assumption the first time both of its purposes were used:
--
--   run 26  --purpose homepage   -> offer.*, brand.*, site.*, llm.homepage_*
--   run 27  --purpose impressum  -> impressum.*, llm.impressum_extracted
--
-- Both wrote `stage = 'extract-p2'`. `MAX(run_id)` is 27, so for the 36
-- companies present in both batches the view served run 27 alone and every
-- `offer.*` signal from run 26 became invisible — **38 companies had
-- `offer.product_categories` in `signal` and 2 had it in `company_profile`.**
-- `ai-check` reads the view, so it offered 2 companies instead of 38, and the
-- money for the homepage batch had already been spent.
--
-- Nothing was lost: the rows are in `signal` and always were. The view could
-- not see them.
--
-- ─────────────────────────────────────────────────────────────────────────
-- Why a distinct stage rather than a cleverer view.
-- ─────────────────────────────────────────────────────────────────────────
--
-- The alternative is to choose the authoritative run per `(company, stage,
-- key)` instead of per `(company, stage)`. That fixes this case and breaks A4:
-- a key a re-run stopped writing would resurrect its value from an older run,
-- which is precisely the stale-as-fresh reading the current grouping forbids.
--
-- So the stage becomes what it always meant. `impressum` keeps the original
-- name — it is the default purpose and every historical row already carries it
-- correctly — and `homepage` gets its own. Only rows that are demonstrably a
-- homepage run are relabelled, and the evidence is `llm_batch.purpose`, not an
-- id typed into a migration.

UPDATE run
SET stage = 'extract-p2-homepage'
WHERE stage = 'extract-p2'
  AND id IN (SELECT run_id FROM llm_batch WHERE purpose = 'homepage');
