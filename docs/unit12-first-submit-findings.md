# Unit 12 — the first `--submit`: refused by the provider, and the release it forced

**2026-09-04.** Authorised with the word *submit*. One command was run against
the real key. **No batch was created, no money was spent, and the account still
holds zero message batches.** The submission was refused by
`messages.batches.create` with a 400 before a batch id was ever assigned.

A reservation of **$0.0639135** is on the books for that non-existent batch and
**has not been released** — see §3.

---

## 1. What was run

```
python -m portal.cli extract-p2 --submit
```

Preflight printed what the dry run had printed: 9 companies sent, 4 withheld
(2 for no 200 Impressum artifact, 2 for M1.75's robots collapse), §7 control 2
at $0.00 of $45.00, §7 control 3 bounding the run at $5.00. Then:

```
anthropic.BadRequestError: Error code: 400 - {'type': 'error', 'error':
{'type': 'invalid_request_error', 'message': "requests.0.custom_id: String
should match pattern '^[a-zA-Z0-9_-]{1,64}$'"}, 'request_id': 'req_011CeiNU4RswdFtH41oSWujU'}
```

Exit 1. `_abort_run` marked the run, as designed for an unclassified exception.

## 2. The defect — M1.115

`build_requests` had emitted `f"{kind}:{company_id}:{artifact_id}"` since Unit
9b. **A colon is not in the batch API's `custom_id` pattern.** Every request
this pipeline has ever built was unsendable.

**The interesting part is not the typo, it is why 829 tests were compatible with
it.** Every fake provider's `submit_batch` accepted any string, so nothing in
the suite modelled the constraint — and three tests *asserted* the colon form:

| file | assertion |
|---|---|
| `tests/test_extract_p2.py` | `custom_id == "impressum:7:42"` |
| `tests/test_extract_p2_cli.py` | `startswith("impressum:")` |
| `tests/test_extract_p2_cli.py` | `startswith("homepage:")` |

A fake that accepts what the real surface refuses does not merely fail to catch
a defect — it certifies it. The suite was pinning the bug in place.

Two aggravating factors, both already-named shapes:

- **The failure lands after the reservation.** Migration 014 puts
  `create` *after* the §7 control 4 write, on purpose. So a request-shape error
  is discovered with money already counted.
- **A second hand-written copy.** `_commit_reservation` built the same key
  independently. That is M1.109's *one expression, two copies* again; it would
  have surfaced only at reconciliation, via the cross-check in
  `reconcile._apply_extraction`.

### The fix

- Separator `:` → `-`.
- The pattern is stated **once**, as `llm.CUSTOM_ID_PATTERN`.
- It is enforced in **`BatchRequest.__post_init__`** — the frozen dataclass every
  request passes through. Not in `extract_p2`: a check the fakes can go around
  is precisely what failed here. No caller, fake included, can now build a
  request the provider would refuse.
- `extract_p2.format_custom_id` is the single builder for both call sites, and
  raises where it can name the company rather than letting `create` fail on an
  index.
- `parse_custom_id` is strict on the new separator. No `:` batch was ever
  submitted (M1.114, re-measured after the refusal), so there is no in-flight
  key to stay compatible with.

Six regression tests, including one that reads the module source to assert the
format string has exactly one expression, and one that proves the old form no
longer parses.

## 3. The stranded reservation — M1.116, released under a rule

The refusal left this:

```
run 5        aborted, est_cost_usd = 0.0639135
llm_batch 1  status='reserved', provider_batch_id=NULL, 9 requests
```

Migration 014 reads `reserved` as *"we do not know whether this batch was
submitted"* and therefore as *the money is gone*. **Here we did know**, and the
difference was measurable: request validation, no id assigned, and
`portal llm-batches` reporting zero *after* the refusal.

014 could not draw that distinction because in the crash it was written for it
does not exist. So rather than editing the row, the distinction was built:

### `portal release-reservation --batch <id> --reason "..."` (M1.117, migration 018)

**The rule is not "an operator may clear a row". It is that a reservation may
be released only when the account itself says the batch does not exist.** All
three conditions are required, and there is no override flag — a condition that
can be waived is not a condition:

1. `provider_batch_id IS NULL` — the row never learned an id.
2. `status = 'reserved'` — the outcome was never learned.
3. a **live** `messages.batches.list` shows no batch created at or after the
   row's `reserved_at`.

Condition 3 is deliberately a network read. A cached answer, or one inferred
from `llm_batch`, is the local record vouching for itself — the thing §10.7b
spent four units refusing to accept. An unparseable or missing `created_at` on
any listed batch **refuses** the release: M1.52's rule, in the one place where
reading *unreadable* as *empty* releases money that was actually spent.

Migration 018 makes the reason a `NOT NULL` CHECK for a released row, not a
convention. The run is **decremented by the batch's reservation, not assigned
zero**, so a run carrying two batches loses only the one released; the batch
keeps its `est_cost_usd` as the record of the amount released.

A new narrow `llm.BatchLister` protocol carries only `list_batches`, so the
release path cannot submit — and its fake models a listing and nothing more,
which is §2's lesson applied at the point of writing the next fake.

**14 tests, 11 of them refusals**, because a release path is only worth having
if it declines.

### Run

```
released batch 1: $0.0639135 off run 5, which now stands at $0.0000000
  reason: 400 invalid_request_error on custom_id pattern; no batch created; account listing zero
  evidence: 0 batch(es) on the account, none created at or after the reservation,
            checked live at 2026-09-04T12:30:30Z
```

**§7 control 2 reads $0.00 of $45.00 again.** The batch row stands as
`released`, with its reason and clock.

## 4. The one number the dry run could never give

The measured reservation is **$0.0639135** for 9 requests — the first time this
project has had that figure at all, because `--dry-run` cannot produce it
(M1.52 forbids a heuristic, and pricing needs `count_tokens`). It sits at
**1.3% of §7 control 3's $5.00 bound**. The pre-run order-of-magnitude estimate
was ~$0.06.

## 5. Verification

- `ruff check` / `ruff format --check`: clean.
- Full suite: **843 passed, 2 skipped** (`--ignore=test_live_smoke.py`).
- Amendment register with docs staged: passes. M1.1–M1.117, no gaps, no
  duplicates. Schema at **018**.

## 6. Open

**The retry.** Not run. `--submit` was authorised once, for a run that has now
concluded; the fix is unproven against the real API, so the next submit is
still the first real one. The ledger is clean and nothing is double-counted.

**Next free migration: `021`.** **Next free amendment: `M1.<127>`** (angle
brackets per M1.94, so this line is not a citation).
