# Unit 9c — the corpus rebuilt, two guards met a real server, and the per-run ceiling

**Dollars spent: $0.00 so far.** The crawl is free by construction — Phase 1 is
deterministic and makes no paid call. Steps 4 and 5 are reported in §6.

Measured 2026-08-21 on `claude/unit9c-first-spend`, branched from `95d3281`
(merged `main`, carrying 9c-prep's M1.95–M1.100).

Migration taken: none yet. Amendments: see §7.

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
