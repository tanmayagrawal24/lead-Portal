-- 004 — `blog_date_unbounded` becomes a §6.4 review reason, and flags gain a
-- note written at raise time.
--
-- **The reason.** M1.32 established that `content.blog_last_post` resting on a
-- sampled article alone is a lower bound with no maximum behind it, and §6.2
-- now declines to fire `opp.blog_stale` (+20) and `opp.blog_slowing` (+10) on
-- it. That closes the expensive direction and leaves a company nobody is told
-- about: `snocks.com`'s newest observed post is 2022-08-26 and the ladder says
-- nothing at all. A7 (§5) is explicit that abstention without routing is only
-- two thirds of the rule.
--
-- **It is not `catalog_not_measurable`, and must not be folded into it.** That
-- reason means *nothing was measured* — no tier of A5's hierarchy could
-- identify a product. This one means *a value exists and cannot bound the rule
-- that reads it*: there is a real date, parsed from a real post, and it is
-- sound evidence of freshness and no evidence of staleness. The two send a
-- human to do different things. A `catalog_not_measurable` asks whether the
-- shop has a catalogue at all; a `blog_date_unbounded` asks one narrow
-- question — is this blog actually dead? — that is answered by opening the
-- blog and looking at the top of it.
--
-- **`raised_note`, and why the table gains a column rather than the signal
-- gaining text.** §6.4 has said twice that the signal carries the reason and
-- the flag carries the routing, on the grounds that a flag "has no room" for
-- text. That was a statement about the schema, not a design commitment, and
-- the schema is being rebuilt here anyway. The human resolving this flag needs
-- one fact at the moment they see the queue — *the newest post we could see
-- was 2022-08-26* — and making them join three signals to learn it is how a
-- queue stops being read. The division of labour is unchanged in substance:
-- signals stay the machine-readable evidence, scored and diffable; the note is
-- the one line the person needs to act. `catalog.not_measurable` keeps its
-- reason text — nothing is relocated.
--
-- The column is deliberately not NOT NULL. Four of the six existing reasons
-- are self-describing and want no note, and a required column would invite
-- restating the reason in prose on every raise.
--
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt — the same
-- dance as 002 and 003, and for the same reason.

DROP TRIGGER trg_review_flag_after_insert;
DROP TRIGGER trg_review_flag_after_update;
DROP TRIGGER trg_review_flag_after_delete;

CREATE TABLE review_flag_new (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL CHECK (reason IN (
                      'no_impressum','possible_marketplace_only','blog_date_unparseable',
                      'domain_moved','duplicate_site','catalog_not_measurable',
                      'blog_date_unbounded')),
    raised_run_id INTEGER NOT NULL REFERENCES run(id),
    raised_at     TEXT NOT NULL,
    raised_note   TEXT,                        -- what we saw, for the person who
                                               -- will act on it. NULL where the
                                               -- reason says it all.
    resolved_at   TEXT,
    resolved_by_human INTEGER CHECK (resolved_by_human IN (0,1)),
    resolved_note TEXT,
    CHECK ((resolved_at IS NULL     AND resolved_by_human IS NULL)
        OR (resolved_at IS NOT NULL AND resolved_by_human IS NOT NULL))
);

INSERT INTO review_flag_new
    (id, company_id, reason, raised_run_id, raised_at, raised_note, resolved_at,
     resolved_by_human, resolved_note)
SELECT id, company_id, reason, raised_run_id, raised_at, NULL, resolved_at,
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
