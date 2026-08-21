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
