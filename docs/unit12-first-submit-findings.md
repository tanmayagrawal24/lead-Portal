# Unit 12 — the first `--submit`: refused by the provider, no batch created

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

## 3. The stranded reservation — M1.116, and it is still there

```
run 5        aborted, est_cost_usd = 0.0639135
llm_batch 1  status='reserved', provider_batch_id=NULL, est_cost_usd=0.0639135
llm_batch_request  9 rows
```

Migration 014 reads `reserved` as *"we do not know whether this batch was
submitted"* and therefore as *the money is gone*. **Here we do know.** The 400
was request validation; no batch id was assigned; and `portal llm-batches` —
the account-scoped instrument that closed §10.7b — reports **zero batches at
2026-09-04T12:07:51Z, after the refused submit**.

**It was left in place anyway.** Nothing automatic releases a reservation, and
that rule is not suspended because this instance is provably safe to release: a
rule's value is that it holds when the evidence is thin, and an exception
written the first time the evidence is good is not a rule. Releasing it is an
operator's act.

**Consequences while it stands:** §7 control 2 reads **$0.06 of $45.00**, and a
re-run of `--submit` reserves a *second* time for the same nine pages. Two
reservations for one batch's work — the over-count the design prefers to the
alternative, and the reason the release is worth doing *before* the retry.

## 4. The one number the dry run could never give

The measured reservation is **$0.0639135** for 9 requests — the first time this
project has had that figure at all, because `--dry-run` cannot produce it
(M1.52 forbids a heuristic, and pricing needs `count_tokens`). It sits at
**1.3% of §7 control 3's $5.00 bound**. The pre-run order-of-magnitude estimate
was ~$0.06.

## 5. Verification

- `ruff check` / `ruff format --check`: clean.
- Full suite: **829 passed, 2 skipped** (`--ignore=test_live_smoke.py`).
- Amendment register with docs staged: passes. M1.1–M1.116, no gaps, no
  duplicates.

## 6. Open

1. **The stranded reservation** (M1.116) — an operator decision.
2. **The retry** — not run. `--submit` was authorised once, for a run that has
   now concluded. The fix is unproven against the real API: the next submit is
   still the first real one.

**Next free migration: `018`.** **Next free amendment: `M1.<117>`** (angle
brackets per M1.94, so this line is not a citation).
