# Unit 9c — the corpus rebuilt, two guards met a real server, and the per-run ceiling

**DOLLARS SPENT: $0.00.** No batch was submitted. The corpus was rebuilt (free
by construction — Phase 1 is deterministic and makes no paid call), §7 control 3
was built and proved to refuse, and both gates were run — and the unit stopped at
the submit call because **`ANTHROPIC_API_KEY` is unset on this machine and the
two available workarounds are both forbidden by rulings this project wrote one
unit ago** (M1.52, M1.99). §7.4 is the full account.

**What is done:** the prep merged, the corpus rebuilt and its deltas reported,
both live guards measured, control 3 built with seven tests, Gate A run, Gate B's
structural checks passed. **What is not:** steps 4 and 5 — submit, restart
survival against a real batch, reconcile, and the hallucination evidence that
only a real run produces.

Measured 2026-08-21 on `claude/unit9c-first-spend`, branched from `95d3281`
(merged `main`, carrying 9c-prep's M1.95–M1.100).

Migrations taken: none. Amendments: **M1.101–M1.104**.

---

## 1. The corpus, rebuilt

§10.4b's ruling is DISPOSABLE and this is its recovery procedure, executed for
the first time. It ran exactly as written:

```
portal init                                    # 15 migrations, schema 015
portal fetch --seed seeds/candidates.csv       # 13 real hosts, §5.2 floor
portal extract-p1
portal score --phase 1
```

**It worked with no edits.** The procedure written in 9c-prep from reading the
CLI, without a corpus to test against, turned out to be correct.

| | New corpus (2026-08-21) |
|---|---|
| companies | 13 |
| artifact rows | 228 |
| stored bodies | 201 |
| signal rows | 161 |
| requests issued | 249 |
| distinct hosts contacted | 16 |
| distinct authorities | 21 |
| clear §5.4's gate | **12 of 13** |

---

## 2. M1.68's address guard — first contact with real servers

**The guard judged 249 addresses and refused none.**

It runs inside `net.py`'s redirect loop **before every request**, not only on
redirect hops (`net.py:308`), so every one of the 249 logged requests was
preceded by a `verdict_for` call. Of those, **23 were redirect responses**,
each of which produced a hop the guard judged before the next request went out.

| | |
|---|---|
| addresses judged | 249 |
| redirect hops among them | 23 |
| refused by the address guard | **0** |
| false positives on a legitimate host | **0** |

Verified two ways, because "no refusals" is exactly the result that could also
mean "the guard never ran": `SELECT COUNT(*) FROM artifact WHERE error LIKE
'address_%'` returns **0**, and `grep -c "address_refused\|address_unverifiable"`
over the 249-line request log returns **0**. The guard's own vocabulary
(`addresses.py:180-214`) is `address_unverifiable:` and `address_refused:`, and
neither string appears anywhere in the run.

**Three redirects WERE refused, and none of them was this guard** — which is
the distinction that makes the zero meaningful:

| Refusal | Refused by |
|---|---|
| `doonails.de/robots.txt` → `www.doonails.com/robots.txt` | registrable-domain change (P2/M1.18) |
| `germanelectronic.de/robots.txt` → `lampenflut.de/robots.txt` | registrable-domain change (P2/M1.18) |
| `smoke2u.de/impressum` → `www.smoke2u.de/Impressum` | robots.txt (P0/M1.12) |

**What this measures and what it does not.** The false-positive rate on this
population is **0/249**, which is the number the brief asked for and it is a
real answer: the guard has now seen 13 production shops, two live domain moves
and 23 redirect hops without once refusing a legitimate host. **What remains
entirely unobserved is its true-positive rate.** It has still never refused a
real server, so this run is evidence that it does not fire wrongly and no
evidence at all that it fires when it should. Those are different claims and
only the first one is now measured. M1.68's own residual — the resolve-then-
reconnect DNS gap — is untouched by this and remains as recorded in §10.5.

---

## 3. M1.75's origin-keyed robots lookup — the finding reproduces

Measured with `authority_of` over every stored body, the same expression the
production lookup uses.

| domain | bodies | origins | robots rows | covered | NOT VERIFIABLE |
|---|---|---|---|---|---|
| bio-fleischer-laden.de | 11 | 1 | 1 | 11 | 0 |
| blackpolish.de | 11 | 1 | 1 | 11 | 0 |
| doonails.de | 11 | 1 | 2 | 11 | 0 |
| ekomia.de | 57 | 1 | 1 | 57 | 0 |
| germanelectronic.de | 4 | 1 | 2 | 4 | 0 |
| navucko.com | 15 | 1 | 1 | 15 | 0 |
| opulent-wohnen.com | 5 | 1 | 1 | 5 | 0 |
| **propellerdiscount.de** | 4 | **2** | 1 | 1 | **3** |
| smile-store.de | 20 | 1 | 1 | 20 | 0 |
| **smoke2u.de** | 8 | 1 | 1 | 0 | **8** |
| snocks.com | 51 | 1 | 1 | 51 | 0 |
| verpackungskoenig.de | 5 | 1 | 1 | 5 | 0 |
| **zecplus.de** | 10 | **2** | **2** | **10** | **0** |

### 3.1 zecplus.de — M1.61's case is clean

This is the domain M1.61 was measured on, and it now does the right thing.

```
robots id=1   https://www.zecplus.de/robots.txt    status=200 bytes=3624
robots id=20  https://blog.zecplus.de/robots.txt   status=200 bytes=173
body origins: {'blog.zecplus.de': 2, 'www.zecplus.de': 8}
```

**Two origins, two robots rows, every body covered by its own origin's file.**
Under M1.44's pre-M1.75 rule — *the newest stored robots.txt for the company* —
the 173-byte permissive file from `blog.zecplus.de` would have been selected by
`ORDER BY id DESC LIMIT 1` and applied to all ten bodies, including the eight on
`www.zecplus.de` whose own 3,624-byte file has rules in it. That is precisely
M1.61's vacuity, and origin-keying eliminates it here without a schema change,
exactly as M1.75 claimed.

### 3.2 The collapse mechanism reproduces on two companies

**11 bodies across 2 companies report NOT VERIFIABLE**, and the cause is the one
M1.75 named rather than a missing fetch:

```
smoke2u.de            robots id=202  https://smoke2u.de/robots.txt   (authority smoke2u.de)
                      body origins:  {'www.smoke2u.de': 8}
propellerdiscount.de  robots id=222  https://propellerdiscount.de/robots.txt
                      body origins:  {'www.propellerdiscount.de': 3, 'propellerdiscount.de': 1}
```

The request log shows **both** origins' robots.txt fetched and both returning
200:

```
https://smoke2u.de/robots.txt                 status=200
https://www.smoke2u.de/robots.txt             status=200
https://propellerdiscount.de/robots.txt       status=200
https://www.propellerdiscount.de/robots.txt   status=200
```

Both origins served byte-identical files; `uq_artifact_identity` is `(company_id,
kind, content_hash)` so the second collapsed into the first; `ON CONFLICT DO
UPDATE` sets `last_checked_at` and `http_status` but **not `url`**, so the
surviving row keeps the apex origin and the `www.` bodies match no row.

**This is the same two companies as §10.7a row 13, on an independently rebuilt
corpus five days later.** M1.75 described the mechanism from one corpus; it now
has a second, and the mechanism is not an artefact of the first.

### 3.3 It has a live cost, and this is the first time that has been visible

`extract-p2 --dry-run` refuses both companies:

```
propellerdiscount.de  SKIPPED — robots_unavailable: no robots.txt stored for
                                origin www.propellerdiscount.de (M1.75)
smoke2u.de            SKIPPED — robots_unavailable: no robots.txt stored for
                                origin www.smoke2u.de (M1.75)
```

**M1.75's stated direction of error — *it over-reports* — now has a price:
`smoke2u.de` is a band-B company at 55 points, and it is excluded from paid
extraction because of a robots row that collapsed.** That is the correct
direction (bodies read under a policy nobody can show was read is H1's family),
and it is worth recording that the cost is not hypothetical. See §7.

---

## 4. audit-politeness on the new corpus

```
§5.2 spacing:  all 16 hosts ok, min gap ≥ 1.000s against 1.0s owed
max hosts in flight: 2 (ceiling 2) — ok
robots.txt coverage: 2 stored artifacts are not 200
  no file        doonails.de           HTTP 301 redirect_refused — RFC 9309 §2.3.1.2, not a breach
  no file        germanelectronic.de   HTTP 301 redirect_refused — RFC 9309 §2.3.1.2, not a breach
  NOT VERIFIABLE www.propellerdiscount.de   3 bodies under rules we cannot show were read
  NOT VERIFIABLE www.smoke2u.de             4 bodies under rules we cannot show were read
§5.2 robots: BREACHED — 0 unread, 2 not verifiable, 2 stating no file
§5.2: BREACHED
```

**Exit code 1**, so M1.19's rule holds: the gate is wired to something.

**Spacing HELD; robots BREACHED.** The two halves disagree and the audit is
right to report them separately. Nothing was fetched too fast and no host saw
more than the ceiling of 2 in flight; the breach is entirely §3.2's collapse.
**0 bodies were fetched under rules that went unread** — the failing category is
*not verifiable*, which is M1.59's tri-state doing its job: we can show that we
read *a* robots.txt for those hosts (the log proves the fetch), and we cannot
show *which stored row* governed them.

---

## 5. Deltas against §10.7a — differences are expected, not regressions

§10.7a is the register of measurements whose evidence was destroyed. This is the
first re-measurement, and per M1.97 **these are new rows, not corrections of old
ones.**

| §10.7a | Old value | New value (2026-08-21) | Read |
|---|---|---|---|
| 13 | zecplus 29 bodies, artifact 458 chosen over 1 | **10 bodies, both origins covered, 0 not verifiable** | The *defect* is gone (§3.1); the *counts* are simply a different crawl |
| 13 | 26 bodies on `www.smoke2u.de` / `www.propellerdiscount.de` naming no robots row | **11 bodies, same two companies** | Mechanism reproduces; magnitude differs |
| 14 | 2,404 signal rows, all `deterministic` / `confidence IS NULL` | **161 rows, all `deterministic` / `confidence IS NULL`** | The *invariant* holds exactly. The count differed because 2,404 accumulated across many runs and this is one |
| 9 | `propellerdiscount.de` stopped at **0 + 50** against a floor of 55 | **0 + 50 against 55** — identical | Reproduced to the point |
| 15 | `germanelectronic.de` admitted at **5 + 50 = 55** | **5 + 50 = 55** — identical | Reproduced to the point |

**Two rows came out identical and that does not make them reproducible.** It
makes them *lucky*. §10.7a's ruling stands unchanged: the evidence for the
original measurement is gone, and what is recorded above is a **new measurement
taken on 2026-08-21**, which happens to agree. If it had disagreed, the correct
action would have been identical — record the new number, leave the old row
marked, change nothing.

**Nothing was adjusted to make an old number reappear.** No weight, no band, no
threshold, no selector was touched during this step.

### 5.1 The current scores

| domain | total | band |
|---|---|---|
| blackpolish.de | 73 | B |
| smoke2u.de | 55 | B |
| bio-fleischer-laden.de | 45 | C |
| opulent-wohnen.com | 45 | C |
| verpackungskoenig.de | 45 | C |
| zecplus.de | 45 | C |
| navucko.com | 42 | C |
| doonails.de | 40 | C |
| ekomia.de | 40 | C |
| snocks.com | 40 | C |
| smile-store.de | 18 | D |
| germanelectronic.de | 5 | D |
| propellerdiscount.de | 0 | D |

Two live domain moves were adopted per P2/M1.18 and raised `domain_moved`:
`doonails.de → doonails.com` and `germanelectronic.de → lampenflut.de`.

Review flags raised: `blog_cadence_unmeasurable` 5, `catalog_not_measurable` 3,
`no_impressum` 2, `domain_moved` 2, `blog_date_unbounded` 2, `blog_undetectable`
1, `blog_date_unparseable` 1.

`snocks.com` returned **429** on nine requests including every Impressum
candidate — recorded as observed behaviour of a real host at the §5.2 floor, not
as a defect in the crawler.

---

## 6. §7 control 3 — built, and proved to refuse (M1.101, M1.102)

### 6.1 The gap, reproduced first

On `95d3281`, through the real reservation write (`extract_p2._charge_run`):

```
§7 control 3 says: per-run ceiling, default $5.00, checked before every call.
The only ceiling constant in the tree: MONTHLY_CEILING_USD = 45.0
grep for a per-run constant: NOTHING

Charging one run, $2.00 at a time:
  call 1: run.est_cost_usd = $ 2.00
  call 2: run.est_cost_usd = $ 4.00
  call 3: run.est_cost_usd = $ 6.00  <-- PAST $5.00
  call 4: run.est_cost_usd = $ 8.00  <-- PAST $5.00
  call 5: run.est_cost_usd = $10.00  <-- PAST $5.00
  call 6: run.est_cost_usd = $12.00  <-- PAST $5.00

Nothing raised. Control 2 is consulted and is content:
  monthly_spend_usd = $12.00  headroom = $33.00  (ceiling $45.00)
```

**One run reserved $12.00 against a stated per-run ceiling of $5.00, and the
only guard in the tree cleared it — correctly, because it is the outer bound.**

Control 2's own text names the gap: *"`run.est_cost_usd` resets on every
invocation, so ten aborted-and-retried runs cost ten times the per-run limit."*
That is an argument for why control 2 is needed **as well**. It had been
available to read for two units as though it made control 3 optional.

### 6.2 What was built

`ledger.RUN_CEILING_USD = 5.0`, `ledger.RunCeilingExceeded`,
`ledger.charge_run`, `ledger.run_reserved_usd`, `ledger.reconcile_run`.

**It composes with `LedgerClearance` rather than replacing it.** `charge_run`
takes a clearance, and a clearance is unforgeable — `check_ceiling` is the only
thing that constructs one. So control 3 cannot be applied *instead of* control
2 by a caller who preferred the smaller number. `test_it_cannot_be_applied_
without_consulting_control_2` asserts the `TypeError`.

**Enforced at the single write.** `_charge_run` is the only path by which a
reservation reaches `run.est_cost_usd`, and it now delegates to `charge_run`.
That is what makes it unbypassable: a future second caller gets the ceiling by
construction instead of by remembering.

### 6.3 Direction of error, stated

- **It fails closed, and over-counts while doing so.** The estimate is written
  *before* the call, so a crash between reservation and submission leaves money
  reserved that was never spent — a conservatively aborted run, not silent
  overspend. Control 3's own wording requires this ordering.
- **A wrongly-refused run costs one retry with a raised cap. A wrongly-cleared
  one costs money that is already gone.**
- **The check is on the post-charge total, not the increment**, so a single
  reservation larger than the whole ceiling is refused outright. Otherwise the
  guard's first call is free — and the first call is the one most likely to be
  the pathological one.
- **`RunCeilingExceeded` is not a `CeilingExceeded`.** Control 2 firing means
  something is wrong; this firing is ordinary. A shared type lets an `except
  CeilingExceeded` written for the runaway case swallow the routine one, which
  is the direction that spends money.

### 6.4 The finding the build produced (M1.102)

`run.est_cost_usd` has **two** writers, and control 3's wording covers only one.

The second is `reconcile`, applying the estimate-to-actual delta to the
**submitting** run (B3.1, M1.90). Applied there, a per-run ceiling inverts: the
money is already spent, and refusing the correction leaves the column holding a
number known to be wrong. Control 2 sums that column — so **a per-run guard that
blocks its own bookkeeping makes the guard that actually bounds spend read a
falsehood**, in the under-counting direction, which is M1.69's argument arriving
through a different door.

`ledger.reconcile_run` exists so that this is a **ruling and not an accident of
one call site**. `reconcile` previously wrote the column with an inline
`UPDATE`; control 3 not applying to it would have been true by omission and
undocumented. A test drives an actual that takes a run to **$7.90**, past the
$5.00 ceiling, and asserts it lands.

### 6.5 The tests

Seven, in `tests/test_cost_ledger.py::ThePerRunCeiling` and
`::TheReservationPathEnforcesControl3`:

| Test | Proves |
|---|---|
| `test_a_run_is_refused_at_the_per_run_ceiling` | four charges of $1.20 pass, the fifth is refused, **and the accumulator has not moved** |
| `test_the_check_is_on_the_total_not_the_increment` | a single $9.99 call is refused outright |
| `test_control_2_is_not_replaced_by_control_3` | ten runs of $4.50 are each legal under control 3 and together trip control 2 |
| `test_it_cannot_be_applied_without_consulting_control_2` | the clearance is required |
| `test_a_run_that_does_not_exist_is_refused_not_treated_as_empty` | an absent run is not read as having spent nothing |
| `test_reconciliation_is_never_refused_by_the_per_run_ceiling` | M1.102's ruling, both directions of delta |
| `test_an_oversized_reservation_leaves_no_batch_row_and_no_charge` | the refusal rolls back M1.72's transaction — **nothing on the books** |

Suite: **705 passed, 2 skipped, 139 subtests** (was 698 before this unit).

---

## 7. GATE A — the dry run, and where this unit stopped

### 7.1 What the gate shows

**9 of 13 companies would be sent. 12 of 13 clear §5.4's gate**; the difference
is four companies that clear the gate and are then refused for a reason that is
not about their score.

| # | company | artifact | source | sent bytes |
|---|---|---|---|---|
| 1 | smile-store.de | 31 | `https://www.smile-store.de/impressum` | 7,288 |
| 2 | zecplus.de | 17 | `https://www.zecplus.de/policies/legal-notice` | 5,970 |
| 3 | doonails.de | 49 | `https://www.doonails.com/policies/legal-notice` | 12,656 |
| 4 | navucko.com | 63 | `https://navucko.com/pages/impressum` | 17,962 |
| 5 | blackpolish.de | 72 | `https://blackpolish.de/policies/legal-notice` | 2,363 |
| 7 | bio-fleischer-laden.de | 96 | `https://bio-fleischer-laden.de/policies/legal-notice` | 3,657 |
| 9 | opulent-wohnen.com | 203 | `https://www.opulent-wohnen.com/Impressum` | 11,665 |
| 11 | verpackungskoenig.de | 221 | `https://verpackungskoenig.de/Impressum` | 21,187 |
| 12 | germanelectronic.de | 223 | `https://lampenflut.de/Impressum` | 11,521 |
| | **9 requests** | | | **94,269** |

**Why the other four are not sent:**

| company | reason | clears §5.4? |
|---|---|---|
| `propellerdiscount.de` | `robots_unavailable` — origin `www.propellerdiscount.de` (M1.75) | **no** — 0 + 50 against a floor of 55 |
| `smoke2u.de` | `robots_unavailable` — origin `www.smoke2u.de` (M1.75) | yes — band B at 55 |
| `ekomia.de` | no 200 Impressum artifact with a body | yes |
| `snocks.com` | no 200 Impressum artifact with a body | yes |

`ekomia.de` and `snocks.com` both returned **429** or **404** on every Impressum
candidate — real host behaviour at the §5.2 floor, recorded as observed. Note
that `germanelectronic.de` is sent, and the artifact it reads is on
`lampenflut.de`: the adopted moved domain (P2/M1.18) carrying through to Phase 2.

### 7.2 The ledger before — its first production read

```
monthly_spend_usd = $0.0000
clearance: spend=$0.0000  ceiling=$45.00  headroom=$45.0000  window=30d
RUN_CEILING_USD = $5.00
llm_batch rows: 0
run rows: 3 (fetch, extract-p1, score-p1), all est_cost_usd = 0.0
```

**`ledger.monthly_spend_usd` has now returned a number in production, and the
number is `$0.0000`.** That is the "before" the brief asked for. It has still
never returned a non-zero one, because nothing has ever been reserved.

### 7.3 GATE B — the two structural checks pass; the priced check is blocked

Gate B names three things a large reservation would mean. Two are checkable
without pricing, and both pass:

- **The 60 KB cap is applying.** `extract_p2.INPUT_CAP_BYTES = 61,440`, and the
  largest single request is **21,187 bytes** — 34% of the cap. No request
  approaches it, so a runaway input is not present.
- **No company is prepared twice.** Nine requests carry nine distinct
  `company_id`s.
- **`count_tokens` reading the wrong text** — not checkable here; see below.

**Sanity bound, and it is NOT the reservation.** 94,269 bytes of German visible
text is on the order of 25–30k input tokens in total; at the declared batch rate
of **$0.50/MTok** that is roughly **$0.015**, and 9 × 2,048 max output tokens at
**$2.50/MTok** bounds output at **$0.046**. So an upper bound in the region of
**$0.06**, comfortably inside the brief's $0.05–$0.30 expectation and far below
Gate B's $1.00 stop. §7.1's own check is consistent: it prices *30k tokens per
advancing company* at $0.0150, and this batch is ~30k tokens for **all nine**.

**This arithmetic may not be used as the reservation, and is not.** M1.52 is
explicit: the estimate comes from `count_tokens` for the model actually being
called, never from a heuristic, because a character-length rule of thumb is a
second expression describing what the first one does (M1.42's shape). The
paragraph above exists to answer *"is this obviously wrong?"* — it is not — and
for nothing else.

### 7.4 Where this unit stopped, and why

**`ANTHROPIC_API_KEY` is UNSET on this machine.** Confirmed before the first
command of this unit and unchanged since; there is no `ant` CLI, no credential
file, and no `ANTHROPIC` name in any of the five Codespaces secret stores
(checked names only, values never read). §7 control 9 as amended by M1.99
forbids substituting the CLI subscription OAuth token that is present, and that
ruling was written by this project one unit ago for exactly this moment.

**Three things follow, and none of them is a judgement call:**

1. **`count_tokens` is a network call** (`llm_anthropic.py:137-150`) and it needs
   a client, which needs a key. It is free, but it is not local.
2. **M1.52 forbids the fallback.** *"A failure propagates and aborts the
   submission rather than falling back to an estimate."* So there is no
   compliant way to produce the reservation figure without the key.
3. **§10.7b's precondition is also unmet.** The open batch question — *was a
   batch ever submitted?* — closes with `messages.batches.list` on a machine
   with a key, and 9c-prep ruled that listing must **precede** any work that
   could submit, because a batch that exists is committed spend and resubmitting
   doubles it. That listing still cannot be run.

**Steps 4 and 5 of the brief are therefore not done: no batch was submitted, no
`provider_batch_id` exists, restart survival was not exercised against a real
batch, and nothing was reconciled. Dollars spent by this unit: $0.00.**

This is reported rather than worked around. The alternatives available were to
price the batch with a heuristic (forbidden by M1.52), or to use the OAuth token
(forbidden by M1.99, written one unit ago). Both would have produced a number
and neither would have produced a measurement.

---

## 8. §8 — what Phase 2 would store, and what erasure would take today

Step 5's last item, and it is answerable from the schema without the run.

**`contact` is the table Phase 2 writes, and every column but two is personal
data:**

```sql
CREATE TABLE contact (
    id, company_id,
    full_name         TEXT,   -- a natural person's name
    role              TEXT,   -- 'Geschäftsführer', 'Inhaber', …
    email             TEXT,
    phone             TEXT,
    postal_address    TEXT,
    source_url        TEXT NOT NULL,   -- must be the Impressum URL
    collected_at      TEXT NOT NULL,
    art14_notice_sent_at TEXT,         -- GDPR Art. 14 information duty
    purge_after       TEXT NOT NULL    -- collected_at + 12 months;
                                       -- enforced by `portal purge` (§8)
);
```

**`portal purge` does not exist. Neither does `portal forget`.** `git grep` for
either returns nothing in `portal/`, and the CLI's subcommand list is `init,
fetch, extract-p1, score, diff-signals, serve, audit-politeness,
audit-impressum-candidates, extract-p2, reconcile, llm-prices`.

**This is M1.45(c)'s shape in the schema itself**: `purge_after` is `NOT NULL`,
so every contact row this project ever writes will carry a deletion deadline,
and the column's own comment names a command that has never been written. A
reader of the schema would conclude erasure is handled. It is not.

**What honouring a deletion request would take today**, written out because
"M7 is unbuilt" is not an answer to a person who asks:

1. **Find the subject.** There is no index on `full_name` or `email`, and no
   command that searches them. It is a manual `SELECT` against `contact`.
2. **Delete the contact row.** `ON DELETE CASCADE` appears 9 times in the schema
   and every one hangs off `company(id)` — so deleting a *company* cascades, and
   deleting a *contact* does not cascade to anything, because nothing references
   `contact`. A single `DELETE FROM contact WHERE id = ?` is sufficient for that
   row.
3. **The copies the cascade does not reach, and this is the part that matters.**
   The person's name is also in:
   - **`signal.value_text`** — §5.5b writes the extracted value as a signal with
     its confidence, and `signal` references `company`, not `contact`.
   - **`signal.evidence_url`** and `contact.source_url` — the Impressum URL,
     which for a sole trader is frequently identifying on its own.
   - **the stored artifact body on disk** under `data/artifacts/`, which is the
     Impressum page in full and is what §5.5b verifies against.
   - **`llm_batch_request.sent_text_sha256`** — a hash, not the text, so this one
     is arguably fine; it is listed because "we kept a digest" is a question a
     regulator asks and the answer should not have to be re-derived.
   - **the provider's side.** A submitted batch's inputs and results sit with
     Anthropic for **29 days** (§5.6), and nothing in this project can delete
     them.
4. **The request log.** `data/requests.jsonl` records every URL fetched,
   appended across runs and never truncated by design (`config.py`).

**So: today, honouring an erasure request means a hand-written multi-table
`DELETE`, a file deletion under `data/artifacts/`, an edit to an append-only log
that was deliberately made append-only, and an unaddressable 29-day window at
the provider.** None of it is impossible; none of it is one command; and the
schema currently claims otherwise.

**This is recorded and not fixed**, because M7 is a milestone and this unit's
scope is the first spend — but it is recorded *now* rather than when M7 starts,
because the first `contact` row is what makes it real, and this unit came within
one API key of writing nine of them. See M1.104.

---

## 9. Still open

- **Was a batch ever submitted? (§10.7b, M1.100.)** Unchanged and still OPEN.
  This unit could not run `messages.batches.list` for the same reason it could
  not submit. **It is still not zero.**
- **The first real spend.** Steps 4 and 5 of the brief. Everything up to the
  submit call is built, tested and dry-run; what is missing is a credential.
- **`count_tokens` against a live model**, and with it §7 control 4's reservation
  arithmetic end to end. The seam is exercised only by fakes.
- **M1.53's prepaid-balance assumption** — `llm-prices` still prints
  *"UNVERIFIED — it needs a live key."*
- **M1.68's true-positive rate.** The address guard has now refused 0 of 249
  real addresses. It has still never refused a real server, so nothing here says
  it fires when it should.
- **M1.68's DNS residual** (resolve, then httpx resolves again). Untouched.
- **M1.103's recurring cost** — every crawl of a shop serving identical
  robots.txt from apex and `www.` loses that shop to `robots_unavailable`.
- **M1.104** — no erasure path. M7.
- **§10.2's lever.** Not observable in this unit: it needs what the model reads
  about owner-operation, and the model was not called. The deterministic
  predicate's side is measurable and unchanged — `legal_form ∈ {e.K.,
  Einzelunternehmen, GbR}` still matches **none** of the corpus.
- **§10.3's calibration block.** Still binding. This unit re-scored and changed
  no weight, band, threshold or selector.

## 10. Where the instructions were wrong

**The brief said "make the first real spend" and it was not possible.** Not
because of a defect — because there is no API credential on this machine, and
the two ways to proceed anyway are both forbidden by rulings this project wrote
one unit ago: M1.52 (no heuristic estimate) and M1.99 (the OAuth token is not an
API key). **The rulings did their job.** M1.99 was written on 2026-08-21 with
the sentence *"written now, before 9c needs a real key, because the moment
someone wants one the convenient credential will be sitting right there"* — and
that is exactly the position this unit reached.

**The brief said "Do not split it further" and "the default is finish it".**
Steps 0–3 are complete and pushed; 4–5 are blocked on a credential rather than
on work. Per the brief's own escape clause — *"If something genuinely blocks,
push what works and say what blocked"* — that is what happened.

**The brief said `extract-p2 --dry-run` would report the reservation in
dollars.** It cannot, and that is correct behaviour rather than a gap: the
reservation is `count_tokens`-derived by M1.52, `count_tokens` is a network
call, and the dry run deliberately makes none.

**The brief said `ledger.monthly_spend_usd` "has never returned a non-zero
number in production".** True, and it still has not — but it has now returned
`$0.0000` in production, against a real database, which it had not done before.

**The brief predicted the crawl's numbers would differ from §10.7a and said not
to treat differences as regressions.** Correct — and two rows came out
*identical* (`propellerdiscount.de` 0+50, `germanelectronic.de` 5+50=55), which
the brief did not anticipate. Handled the same way: recorded as new measurements
dated today, with §10.7a's rows left marked.

## 11. Open items register — only the rows this unit touched

Per the brief: not re-derived in full. Four rows changed.

| Item | Was | Now | Why |
|---|---|---|---|
| The 13-company corpus | GONE; ruled disposable | **REBUILT** 2026-08-21 — 13 companies, 228 artifacts, 201 bodies | §10.4b's recovery procedure executed, unmodified (§1) |
| §7 control 3 | Specified, not implemented | **BUILT** — `RUN_CEILING_USD`, `charge_run`, 7 tests | M1.101 (§6) |
| `run.est_cost_usd`'s second writer | Unstated | **RULED** — reconciliation is never refused by control 3 | M1.102, `ledger.reconcile_run` (§6.4) |
| §8 erasure path | Not previously registered | **OPEN, with the manual procedure written out** | M1.104 (§8) |

**Unchanged and explicitly re-checked:** the batch question (§10.7b) is still
open and still not zero; `interrupted-M5-remnant` is still stashed and
unapplied; `portal/pagespeed.py` is still unbuilt; `run.pagespeed_calls` still
exists nowhere; `llm_batch.status = 'balance_exhausted'` is still ahead of its
writer. **Next free migration: `016`. Next free amendment: `M1.<105>`** (angle
brackets per M1.94, so this line is not a citation).

## 12. The counter (M1.73)

Re-run after this document existed, as the convention requires:

```
$ git grep -ohE 'M1\.[0-9]+' -- docs/ portal/ tests/ README.md .github/ \
    | sed 's/M1\.//' | sort -n | uniq | tail -1
104
$ grep -c '^| M1\.' docs/lead-portal-spec-v0.3.md
104
```

**Cited maximum and declared row count agree at 104.** M1.91's check passes,
which is the mechanism rather than the assertion.
