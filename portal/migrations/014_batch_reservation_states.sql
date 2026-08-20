-- 014 — the two `llm_batch.status` values §7 needs before a reservation exists,
--       and the nullability that §7 control 4's ORDERING forces.
--
-- The number carries no history: nothing has ever used 014. §10.4b named it as
-- the next free number and named this row as what would take it.
--
-- **THIS MIGRATION SHIPS IN THE SAME COMMIT AS ITS WRITER** (M1.45(c)).
-- `extract_p2.reserve_and_submit` writes `reserved`, and `reconcile` writes
-- `balance_exhausted`. §10.6 has listed control 11's status as *"not in the
-- schema yet — ships with M5's writer"* for five units, correctly.
--
-- ─────────────────────────────────────────────────────────────────────────
-- (1) `balance_exhausted` — and it is NOT a new idea, it is a LIVE DEFECT.
-- ─────────────────────────────────────────────────────────────────────────
--
-- §7 control 11: a prepaid key can empty between reserve and result, and
-- `billing_error` is a 403 that shares its status code with `permission_error`,
-- so it is distinguishable only through the error object's `.type`. A balance
-- error is its own status and is never folded into `failed`, because *"the
-- provider failed"* and *"we ran out of money"* need different operator
-- responses and one of them is not an engineering task.
--
-- **Measured before the change rather than argued** — `llm.resolve_batch_status`
-- has returned `BatchStatus.BALANCE_EXHAUSTED` since Unit 2, and the CHECK
-- below rejected it:
--
--     resolve_batch_status([BALANCE_EXHAUSTED]) -> BatchStatus.BALANCE_EXHAUSTED
--     INSERT ... status='balance_exhausted'
--       -> IntegrityError: CHECK constraint failed:
--          status IN ('submitted','completed','reconciled','failed','expired')
--
-- So the first dry key would have crashed `reconcile` with an IntegrityError
-- instead of reporting *"this batch stopped because the key ran dry"* in those
-- words — which is the one thing control 11 exists to make it able to say. The
-- taxonomy shipped in Unit 2 and the schema value did not, and the gap was
-- invisible for exactly as long as there was no caller. That is why this is not
-- the deferrable half of control 11: the code that needs the value already
-- exists and already produces it.
--
-- ─────────────────────────────────────────────────────────────────────────
-- (2) `reserved` — the state §7 control 4's own ordering creates.
-- ─────────────────────────────────────────────────────────────────────────
--
-- §7 control 4: *"Reserve into both `llm_batch.est_cost_usd` and
-- `run.est_cost_usd` **before the submit call returns**."* Taken literally —
-- and it must be taken literally, because the whole point is that a submitted
-- batch is committed spend whether or not the process survives to read it —
-- the reservation is written BEFORE `messages.batches.create` returns, and
-- therefore before `provider_batch_id` exists. The column was `TEXT NOT NULL
-- UNIQUE`, so the reservation could not be written at all.
--
-- **The alternative orderings were weighed and both fail in the forbidden
-- direction** (M1.89):
--
--   submit, then write both rows in one transaction
--       — a crash in the gap leaves money spent, the ledger blind AND no
--         `llm_batch` row at all, so `reconcile` cannot even find the batch to
--         collect the results that were paid for. That is M1.72's defect made
--         worse, not fixed: it under-counts §7 control 2, the one direction
--         §7 must not fail in.
--
--   write `run.est_cost_usd` first, submit, write `llm_batch` after
--       — control 2 sums `run` alone, so the LEDGER stays right. But a crash in
--         the gap leaves money in the ledger attributable to nothing and a
--         submitted batch with no local row, so its results are never collected.
--         Spend with zero return, invisibly. It also violates M1.72 by
--         construction, which is the finding it would be implementing.
--
-- So: `provider_batch_id` becomes nullable, NULL exactly while `status =
-- 'reserved'`, and the CHECK below enforces that rather than leaving it to a
-- convention. `submitted_at` goes with it for the same reason — a batch that
-- has not been submitted has no submission time, and writing the reservation's
-- clock into that column would be a second meaning for one word.
--
-- **What `reserved` MEANS, stated because the direction of error depends on
-- it: we do not know whether this batch was submitted.** A crash before
-- `create` and a crash after it are indistinguishable from here, so the row is
-- read as *the money is gone* — over-counting the ledger, which is §7 control
-- 3's own stated preference (*"can only over-count, never under-count"*).
-- **Nothing releases it automatically.** A reservation released by a rule is
-- how real spend leaves the ledger; `reconcile` reports a `reserved` batch to
-- the operator and stops there, and the only thing that ever corrects a
-- reservation is a MEASURED actual (§7 control 12, B3.3).
--
-- `reserved_at` is added rather than reusing `submitted_at`, so the two clocks
-- stay separable — a batch reserved at 10:00 and submitted at 10:02 has spent
-- two minutes in the window where a crash costs the provider id.
--
-- SQLite cannot alter a CHECK constraint or drop a NOT NULL, so the table is
-- rebuilt. **The table is empty in every real database** — `llm_batch` has had
-- no writer since §4 declared it (§10.6: *"ahead of its writer — M5"*) — so the
-- copy below moves nothing today. It is written correctly anyway, because a
-- migration that assumes an empty table is a migration that cannot be re-read.

CREATE TABLE llm_batch_new (
    id                INTEGER PRIMARY KEY,
    -- Nullable as of 014, and NULL means one specific thing: the reservation
    -- was committed and the submit call's outcome is unknown. UNIQUE still
    -- holds — SQLite admits many NULLs in a UNIQUE index, which is what lets
    -- more than one batch sit in that window at once.
    provider_batch_id TEXT UNIQUE,
    run_id            INTEGER NOT NULL REFERENCES run(id),
    purpose           TEXT NOT NULL,   -- 'impressum' | 'homepage'
    request_count     INTEGER NOT NULL,
    est_cost_usd      REAL NOT NULL,   -- reserved BEFORE the submit call returns
    -- Written at reconciliation, from MEASURED usage. It is a running total:
    -- a batch polled twice writes the sum over everything terminal so far, and
    -- §7 control 12 applies the DIFFERENCE to the submitting run, so a repeated
    -- `reconcile` moves the ledger by zero.
    actual_cost_usd   REAL,
    status            TEXT NOT NULL DEFAULT 'submitted'
                      CHECK (status IN ('reserved','submitted','completed',
                                        'reconciled','failed','expired',
                                        'balance_exhausted')),
    reserved_at       TEXT NOT NULL,
    submitted_at      TEXT,
    reconciled_at     TEXT,
    -- The invariant, as a constraint rather than as a convention. A row that
    -- claims to have been submitted must be able to say WHERE and WHEN, or
    -- `reconcile` has a batch it can neither poll nor explain.
    CHECK (status = 'reserved'
           OR (provider_batch_id IS NOT NULL AND submitted_at IS NOT NULL))
);

INSERT INTO llm_batch_new
    (id, provider_batch_id, run_id, purpose, request_count, est_cost_usd,
     actual_cost_usd, status, reserved_at, submitted_at, reconciled_at)
SELECT id, provider_batch_id, run_id, purpose, request_count, est_cost_usd,
       actual_cost_usd, status,
       -- A row written before 014 was written at submission time and there is
       -- no earlier clock to recover, so its reservation is dated to its
       -- submission. Correct rather than convenient: those two moments were the
       -- same event for every row this can apply to.
       submitted_at, submitted_at, reconciled_at
FROM llm_batch;

DROP TABLE llm_batch;
ALTER TABLE llm_batch_new RENAME TO llm_batch;

CREATE INDEX idx_batch_status ON llm_batch(status);
