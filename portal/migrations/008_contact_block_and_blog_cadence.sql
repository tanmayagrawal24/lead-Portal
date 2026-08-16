-- 008 — `blog_cadence_unmeasurable`, and the first abstention that stops a
-- phone call.
--
-- **Ratified 2026-08-16 (M1.37).** `neg.active_content` abstains where the
-- blog index dates fewer posts than it lists, on **5 of 13** shops, and until
-- now told nobody. A7's third part, supplied.
--
-- **A sixth reason rather than a reuse**, on the rule this spec has applied
-- three times already — a different thing is known, so a different resolution
-- applies:
--
--   `blog_date_unparseable`      no date at all. Open the index and read it.
--   `blog_date_unbounded`        a date that cannot bound staleness. Is this
--                                blog actually dead?
--   `blog_cadence_unmeasurable`  dates for some posts and not others. A third
--                                question: **how often does this shop publish?**
--                                Answered by opening the blog and looking at
--                                the dates on the first page.
--
-- ─────────────────────────────────────────────────────────────
-- **The third axis: which direction the abstention errs in.**
-- ─────────────────────────────────────────────────────────────
--
-- A7's table records what could not be measured and what routes it. It did not
-- record the one thing that decides how much an abstention costs: **which way
-- the score is wrong while it waits.**
--
-- Every prior instance guards a rule that *awards* points for an absence. The
-- abstention withholds an award, the lead is scored too **low**, it ranks below
-- where it belongs, and the review queue eventually reaches it. The cost is a
-- delay.
--
-- `neg.active_content` subtracts 25 for a *presence*. Abstaining withholds a
-- penalty, so the lead is scored too **high** — and a lead that is too high is
-- not mis-ranked, it is called. `doonails.de` publishes actively, cannot be
-- measured to be doing so, and sits in the list as a strong prospect on a score
-- that is knowingly up to 25 points generous. The failure mode is not a worse
-- ordering; it is a phone call to a company that is already doing the thing we
-- are selling.
--
-- **So the consequence is not only a queue entry.** §8 already holds that an
-- export whose comparative claim lacks its basis must **fail** rather than
-- degrade — because the failure is outward-facing and lands on a third party.
-- A too-high abstention is the same shape: the error leaves the building. It is
-- therefore given the same treatment.
--
-- > **An unresolved too-high abstention blocks outbound contact for that
-- > company until a human resolves it.** Not a warning next to the row. The
-- > `outreach` insert is refused.
--
-- Too-low abstentions are ordinary queue items and block nothing; the queue is
-- the whole remedy, because the error is a delay in ranking.
--
-- **Which reasons block is data, not a branch**, so that adding the next one is
-- an INSERT and cannot be half-applied across four triggers. `contact_blocked`
-- on `company` is a **cache for the UI**, maintained exactly as `needs_review`
-- is; the block itself is enforced live in the `outreach` trigger against
-- `contact_blocking_reason`, so a reason added after a flag was raised still
-- blocks, and no code path reaches `outreach` around it.
--
-- **What resolution means here.** Resolving the flag is a human saying they
-- looked at the blog and know the cadence. §6.4's stickiness applies unchanged:
-- resolved once, never re-raised. That is deliberate — the human's answer to
-- "how often does this shop publish" is the more durable fact — and it is worth
-- naming that the block lifts on that judgement and not on any measurement.
--
-- SQLite cannot alter a CHECK constraint, so `review_flag` is rebuilt: the same
-- dance as 002, 003, 004 and 005.

-- ─────────────────────────────────────────────────────────────
-- The blocking set.
-- ─────────────────────────────────────────────────────────────

CREATE TABLE contact_blocking_reason (
    reason TEXT PRIMARY KEY,
    -- Why this reason leaves the score too high, in one line, for whoever adds
    -- the next one and has to decide whether it belongs here.
    rationale TEXT NOT NULL
);

INSERT INTO contact_blocking_reason (reason, rationale) VALUES (
    'blog_cadence_unmeasurable',
    'neg.active_content (-25) cannot fire: the score omits a penalty this shop may be owed, so the lead reads stronger than the evidence supports.'
);

-- ─────────────────────────────────────────────────────────────
-- `review_flag`, rebuilt with the sixth reason.
-- ─────────────────────────────────────────────────────────────

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
                      'blog_cadence_unmeasurable')),
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

-- ─────────────────────────────────────────────────────────────
-- `company.contact_blocked` — derived, like `needs_review`, never written
-- directly. A UI cache: the enforcement is on `outreach`.
-- ─────────────────────────────────────────────────────────────

ALTER TABLE company ADD COLUMN contact_blocked INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_company_contact_blocked ON company(contact_blocked);

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

-- The column is new, so bring it into agreement with the flags already stored
-- rather than trusting the DEFAULT to be right for existing rows.
UPDATE company SET contact_blocked = EXISTS (
    SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
    WHERE f.company_id = company.id AND f.resolved_at IS NULL
);

-- ─────────────────────────────────────────────────────────────
-- The block itself. Checked live against the flags, not against the cache, so
-- that a reason added to `contact_blocking_reason` after the fact still blocks.
-- ─────────────────────────────────────────────────────────────

CREATE TRIGGER trg_outreach_blocked_before_insert BEFORE INSERT ON outreach
WHEN EXISTS (
    SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
    WHERE f.company_id = NEW.company_id AND f.resolved_at IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'outreach blocked: an unresolved review flag leaves this company''s score too high (A7, §6.4) — resolve it first');
END;

-- Moving a recorded contact onto a blocked company is the same act by another
-- route, so it is refused by the same rule.
CREATE TRIGGER trg_outreach_blocked_before_update BEFORE UPDATE OF company_id ON outreach
WHEN EXISTS (
    SELECT 1 FROM review_flag f JOIN contact_blocking_reason b ON b.reason = f.reason
    WHERE f.company_id = NEW.company_id AND f.resolved_at IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'outreach blocked: an unresolved review flag leaves this company''s score too high (A7, §6.4) — resolve it first');
END;
