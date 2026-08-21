-- 015 — `llm_batch_request`: what was sent, so `reconcile` can survive a restart
--       and can answer a question `llm_batch` cannot.
--
-- The number carries no history: nothing has ever used 015. **The lost M5
-- stash's `010_phase2_writers.sql` created a table of this name and §10.4b's
-- standing instruction is *"rebuild it in M5 with its writer, or register it in
-- §10.6 as ahead-of-writer deliberately"*.** It is rebuilt here, with its
-- writer, from the description rather than from the file — the stash is
-- unavailable to it and this schema was derived from §5.6's requirements, not
-- remembered. Whatever the stash's columns were, these are the ones §5.6 needs.
--
-- ─────────────────────────────────────────────────────────────────────────
-- (1) THE DEFECT THIS CLOSES, measured before it was reasoned about (M1.86).
-- ─────────────────────────────────────────────────────────────────────────
--
-- §5.6 fact 2 is a rule about a SET: *"A batch moves to `reconciled` only when
-- EVERY ONE of its requests has a terminal disposition."* `llm_batch` records
-- `request_count`, which is a NUMBER. A number cannot answer *which* requests
-- are still owed, and `llm.resolve_batch_status` reads the RETURNED items and
-- nothing else. Ten requests sent, eight results returned:
--
--     resolve_batch_status(8 succeeded)  ->  BatchStatus.RECONCILED
--     resubmittable(8 succeeded)         ->  ()
--
-- The batch is marked done and the two missing companies are silently
-- unextracted, with nothing anywhere naming them. **That is M1.51 fact 2's own
-- failure arriving through a different door** — M1.51 caught partial-ness
-- arriving as an `expired` MEMBER, and this is partial-ness arriving as an
-- ABSENT member, which no guard built for the first one can see. The guard was
-- correct and its input was incomplete.
--
-- So the request set is stored at reservation time, and `reconcile` resolves
-- the batch against the STORED set rather than against the returned one. A
-- request with no result is a request with no terminal disposition, and the
-- batch stays open, which is what §5.6 has said since v0.3.
--
-- ─────────────────────────────────────────────────────────────────────────
-- (2) `sent_text_sha256` — §5.5b's verification, made checkable across a
--     process boundary (M1.87).
-- ─────────────────────────────────────────────────────────────────────────
--
-- §5.5b verifies an extracted value by substring presence **in the text that
-- was sent to the model**, and `portal/verify.py` takes that text as an
-- argument specifically so it cannot reach for a different rendering of the
-- same page (M1.43). `reconcile` runs in a DIFFERENT PROCESS, hours or days
-- later, and the sent text is not in it.
--
-- Reconstructing it is the only option that does not put 60 KB per company in
-- the database, and it is sound as far as it goes: `custom_id` names the
-- artifact, artifact bodies are content-addressed and immutable, and
-- `extract_p2.clean` is one expression. **But "sound as far as it goes" is
-- precisely the shape M1.43 was**, and the assumption has a real way to fail:
-- `reconcile` may run under a LATER BUILD than the one that submitted — results
-- stay retrievable for 29 days (§5.6 fact 4) — and a change to
-- `parsers.visible_text` between the two would have `verify` checking against a
-- page the model was never shown, silently, with every test still passing.
--
-- The digest turns that assumption into a CHECK. Reconstruct, hash, compare; a
-- mismatch is `text_unreproducible` and NO VALUE IS WRITTEN. **Direction: it
-- errs low.** The extraction is lost and paid for, the stage fact still records
-- that the extraction ran, and §6.1's three-state predicates therefore abstain
-- and send a person to the page — so the loss is visible and no unverified name
-- can reach a letter. The opposite direction would be a name verified against
-- text nobody sent, which is the failure this whole backstop exists for.
--
-- `text_unreproducible` is TERMINAL, and that is deliberate rather than
-- convenient: re-running `reconcile` re-cleans the same bytes with the same
-- code and fails the same way, so treating it as retryable would leave the
-- batch open forever — §7 control 11's *"a batch sitting in `submitted`
-- forever"*, manufactured by the guard meant to prevent silent loss.
--
-- ─────────────────────────────────────────────────────────────────────────
-- (3) The vocabulary, and why it is a superset of `llm.RequestOutcome`.
-- ─────────────────────────────────────────────────────────────────────────
--
-- Six of the seven are `llm.RequestOutcome`'s members — the four the API
-- returns plus the two this tool has to distinguish that the API's vocabulary
-- does not (M1.51's `errored` split, and control 11's balance case).
-- `text_unreproducible` is the seventh and it is LOCAL: it is not the
-- provider's opinion about a request, it is this tool's opinion about its own
-- ability to check one. Keeping it out of `RequestOutcome` keeps that
-- distinction where it belongs — `portal/llm.py` holds facts about a vendor.

CREATE TABLE llm_batch_request (
    id             INTEGER PRIMARY KEY,
    batch_id       INTEGER NOT NULL REFERENCES llm_batch(id) ON DELETE CASCADE,
    -- `build_requests`' key, stored verbatim. `parse_custom_id` is the single
    -- expression that reads it (M1.42); the two columns below are denormalised
    -- out of it so that `portal forget --domain X` (§8) can CASCADE, which a
    -- company id buried in a text key could not do.
    custom_id      TEXT NOT NULL,
    company_id     INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    artifact_id    INTEGER NOT NULL REFERENCES artifact(id),
    -- SHA-256 of the exact string handed to the provider. See (2).
    sent_text_sha256 TEXT NOT NULL,
    sent_bytes     INTEGER NOT NULL,
    -- NULL means: no terminal disposition yet. That is the state §5.6 fact 2
    -- asks about, and it is the reason this table exists.
    outcome        TEXT CHECK (outcome IN ('succeeded','invalid_request',
                                           'server_error','canceled','expired',
                                           'balance_exhausted',
                                           'text_unreproducible')),
    error_message  TEXT,
    settled_at     TEXT
);

-- One row per request per batch. `reconcile` writes dispositions with
-- `ON CONFLICT ... DO NOTHING` on this target, the §4 M1.5 idiom, so a repeated
-- run neither duplicates nor silently swallows a CHECK violation on `outcome`.
CREATE UNIQUE INDEX uq_batch_request ON llm_batch_request(batch_id, custom_id);

-- The open set, which is the question §5.6 fact 2 actually asks.
CREATE INDEX idx_batch_request_open
    ON llm_batch_request(batch_id) WHERE outcome IS NULL;
