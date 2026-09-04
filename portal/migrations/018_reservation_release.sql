-- 018 — `released`: the one state that takes money OUT of §7 control 2, and
--       the three conditions that are the only way in.
--
-- **THIS MIGRATION SHIPS IN THE SAME COMMIT AS ITS WRITER** (M1.45(c)).
-- `portal release-reservation` is the only thing that writes `released`.
--
-- ─────────────────────────────────────────────────────────────────────────
-- Why this exists at all, given that 014 forbids exactly this.
-- ─────────────────────────────────────────────────────────────────────────
--
-- 014: *"**Nothing releases it automatically.** A reservation released by a
-- rule is how real spend leaves the ledger."* That sentence is still true and
-- this migration does not weaken it. What 014 could not distinguish — because
-- in the crash it was written for the distinction does not exist — is:
--
--   (a) the process died between `create` and the row write, so whether the
--       batch exists is genuinely UNKNOWABLE from here; and
--   (b) the provider refused the request and said so, so no batch was ever
--       created, and the account can be ASKED whether that is true.
--
-- 014 reads both as *the money is gone*, which is correct for (a) and merely
-- expensive for (b). M1.116 met (b) on the first real submit: a 400 on the
-- `custom_id` pattern (M1.115), no batch id assigned, and `messages.batches
-- .list` empty afterwards.
--
-- **The rule this migration encodes is not "an operator may clear a row".**
-- It is: a reservation may be released only when the account itself says the
-- batch does not exist. The evidence is a live listing, not a judgement, and
-- the command refuses without it — which is why this is still a measurement
-- authorising a ledger write, in the direction §7 says is dangerous, rather
-- than a convenience.
--
-- The three conditions, all required, checked at release time:
--
--   1. `provider_batch_id IS NULL`  — this row never learned an id.
--   2. `status = 'reserved'`        — it is in the unknown-outcome state.
--   3. a LIVE `messages.batches.list` shows NO batch created at or after this
--      row's `reserved_at` — the account has nothing this row could have made.
--
-- Condition 3 is the one that carries the weight, and it is deliberately a
-- network read: a stale answer, a cached answer, or an answer inferred from
-- `llm_batch` would all be the local record vouching for itself, which is the
-- thing §10.7b spent four units refusing to accept (M1.100, M1.114).
--
-- An unparseable or missing `created_at` on any listed batch REFUSES the
-- release. Unreadable is not empty — M1.52's rule, in the one place where
-- reading it as empty would release money that was spent.
--
-- ─────────────────────────────────────────────────────────────────────────
-- What is written, and what is deliberately NOT written.
-- ─────────────────────────────────────────────────────────────────────────
--
-- `release_reason` is NOT NULL for a released row, as a CHECK rather than as a
-- convention: a release with no stated cause is indistinguishable from a
-- mistake six months later, and this is the only operation in the project that
-- makes the ledger smaller.
--
-- `est_cost_usd` on the batch row is LEFT AT ITS RESERVED VALUE. It is the
-- record of *what was released*, and zeroing it would erase the amount at the
-- same moment as the obligation. §7 control 2 sums `run.est_cost_usd`, so it
-- is the RUN that is decremented — by exactly this batch's reservation, not by
-- assignment to zero, so a run carrying two batches loses only the one that
-- was released.

CREATE TABLE llm_batch_new (
    id                INTEGER PRIMARY KEY,
    provider_batch_id TEXT UNIQUE,
    run_id            INTEGER NOT NULL REFERENCES run(id),
    purpose           TEXT NOT NULL,
    request_count     INTEGER NOT NULL,
    -- Kept at its reserved value after a release: what was released, not what
    -- is owed. `status` is what says whether it is still counted.
    est_cost_usd      REAL NOT NULL,
    actual_cost_usd   REAL,
    status            TEXT NOT NULL DEFAULT 'submitted'
                      CHECK (status IN ('reserved','released','submitted',
                                        'completed','reconciled','failed',
                                        'expired','balance_exhausted')),
    reserved_at       TEXT NOT NULL,
    submitted_at      TEXT,
    reconciled_at     TEXT,
    -- New in 018. Both NULL unless `status = 'released'`.
    released_at       TEXT,
    release_reason    TEXT,
    -- 014's invariant, widened by exactly one state: a `released` row is one
    -- that never had a provider id, so it cannot be asked to produce one.
    CHECK (status IN ('reserved', 'released')
           OR (provider_batch_id IS NOT NULL AND submitted_at IS NOT NULL)),
    -- The reason is part of the state, not commentary beside it.
    CHECK (status <> 'released'
           OR (released_at IS NOT NULL AND release_reason IS NOT NULL
               AND provider_batch_id IS NULL))
);

INSERT INTO llm_batch_new
    (id, provider_batch_id, run_id, purpose, request_count, est_cost_usd,
     actual_cost_usd, status, reserved_at, submitted_at, reconciled_at,
     released_at, release_reason)
SELECT id, provider_batch_id, run_id, purpose, request_count, est_cost_usd,
       actual_cost_usd, status, reserved_at, submitted_at, reconciled_at,
       NULL, NULL
FROM llm_batch;

DROP TABLE llm_batch;
ALTER TABLE llm_batch_new RENAME TO llm_batch;

CREATE INDEX idx_batch_status ON llm_batch(status);
