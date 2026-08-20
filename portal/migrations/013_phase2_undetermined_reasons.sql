-- 013 — `own_brand_undetermined` and `owner_named_undetermined`: the two
--       reasons A7a items 10 and 11 have been naming since M1.49.
--
-- The number carries no history: nothing has ever used 013.
--
-- **THIS MIGRATION SHIPS IN THE SAME COMMIT AS THE CODE THAT RAISES THESE
-- FLAGS.** That is M1.45(c), and it is why §6.1 said explicitly that the rows
-- must not be added early: *a documented resolution path with no writer is a
-- claim the tool does not keep*, and this project has already paid for that
-- once with §6.4's pipeline clear. §10.6 listed both as "not in the schema
-- yet — same rule, same reason" for four units, correctly.
--
-- **What they route (M1.81).** §5.5b's two booleans — `own_brand` (+10 via
-- `qual.own_brand`) and `owner_named_on_site` (+15 via `qual.owner_operated`
-- disjunct 3) — are judgements about a page with no string in them for a
-- substring check to find. M1.47/M1.49 gave each an `_evidence` span so there
-- is something to verify, and stated the limit of what that buys: it proves the
-- model did not fabricate its evidence, and it cannot catch the model reading
-- the page correctly and inferring wrongly.
--
-- A rule reading one of them has THREE states, not two:
--
--   stage fact absent            -> DECLINE. Phase 2's turn has not come, and a
--                                   rule whose turn has not come has not
--                                   abstained. (`_ai_invisible`'s convention.)
--   boolean written              -> FIRE or DECLINE on its value.
--   stage fact written,          -> ABSTAIN, reason written, THESE FLAGS.
--   boolean absent
--
-- The third state is reached two ways — the model returned `null` (§5.5b
-- instructs exactly that for a field it cannot find), or its `_evidence` span
-- failed verification and migration 012's filter removed the row. **Both take
-- the same reason on purpose**: they send a person to the same page to answer
-- the same question, which is A7's one-question test. `raised_note` carries
-- which of the two it was.
--
-- **NEITHER BLOCKS OUTBOUND CONTACT, and the axis is why (008's third axis).**
-- Both rules AWARD points, so the abstention withholds an award and the lead
-- reads too **low** — a ranking delay the queue repairs. It is only the
-- *unverified value* that errs too high, and §6.1's verification gate is what
-- stops that value ever reaching a score. Conflating the two would block
-- contact on 25 points of merely-missing evidence, which is §6.4's queue-noise
-- failure rather than item 7's phone-call failure. So no row is added to
-- `contact_blocking_reason`, deliberately, and this comment is the record of
-- that being a decision rather than an omission.
--
-- SQLite cannot alter a CHECK constraint, so `review_flag` is rebuilt — and
-- 008's outreach triggers must come down with it, which 009's header explains
-- is a feature: they read `review_flag` by name, so SQLite refuses to drop the
-- table while they stand, and every migration adding a reason therefore has to
-- look at the block and put it back deliberately.

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
                      'blog_cadence_unmeasurable','fetch_persistently_failing',
                      'own_brand_undetermined','owner_named_undetermined')),
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
