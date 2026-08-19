# Unit 8 — the last three M5 prerequisites, and the one that was nearly lost

Measured 2026-08-18/19 against `3f0db48` (merged `main`). **No crawl, no API
call, no spend, and no `ANTHROPIC_API_KEY` at any point.** The only network
traffic in this unit was `pip install`, loopback fixture servers, and `git`.

Three sub-units, one branch: **8a** (M1.73, the counter), **8b** (M1.74, closing
M1.66), **8c** (M1.75, closing M1.61). Companion to M1.73–M1.75 in
`docs/lead-portal-spec-v0.3.md`.

---

## 0. Baseline, taken before anything moved

| | result |
|---|---|
| `3f0db48`, clean tree | **576 passed, 2 skipped, 123 subtests** |
| lint | `ruff check .` and `ruff format --check .` both clean |
| branch tip at hand-off | `641ff23` — **local only** |

## 1. 8b existed in one container and nowhere else

The brief opened by saying `origin/claude/unit8-m5-prerequisites` carried
exactly one commit and that no `010_*.sql` existed on any remote ref. **That was
correct**, verified against `git ls-remote` before anything else was done:

```
ff99e20…  refs/heads/claude/unit8-m5-prerequisites     <- 8a only
```

8b was three commits — `c2de8e9`, `89506bd`, `641ff23` — committed locally and
never pushed. They were pushed before this unit read past Step 0:

```
   ff99e20..641ff23  claude/unit8-m5-prerequisites -> claude/unit8-m5-prerequisites
```

Nothing needed rebuilding. `portal/migrations/010_score_evaluated_on.sql` is
present on the remote ref, verified by `git ls-tree -r origin/...` rather than
by looking at the working copy — the working copy is the thing under suspicion.

**This is the third time. It is not a memory problem and it should stop being
treated as one.** The Unit 5 M5 stash was lost this way, §10.4b still carries
the scar, and `stash@{0}: On main: interrupted-M5-remnant` is *still listed* in
this container's `git stash list` — a stash entry whose contents were lost with
the container that held them, listed on the machine that cannot restore it. The
common factor across all three is that work was considered finished at the point
it passed its tests, and the push was a separate later step that a context
boundary could fall inside. **The push is part of the unit, not after it.**

## 2. 8a (M1.73) — the counter, not the number

Landed before this session, at `ff99e20`, and summarised here because the report
covers all three. The line tracking the untransmitted audit section produced
three incompatible readings in four units, every one after Unit 4 copied from
the previous unit rather than re-derived. The fix replaced a scalar with a named
list, a mechanical grep, and an extension rule, in one place (§Unit 2a).

**8a's lesson is applied to this document's own backlog in §7, and it found a
second instance immediately.** See "Where the instructions were wrong".

## 3. 8b (M1.74) — the date the rules were run against

Landed at `c2de8e9`/`89506bd`/`641ff23`. `score.evaluated_on` (migration 010),
written from the value `evaluate` actually used rather than from a second clock
read in `_persist`. Reproduced before it was fixed on `ff99e20`: a `ScoreStage`
given `today=date(2020, 3, 7)` wrote `computed_at = 2026-08-18T21:17:27Z` with
`2020-03-07` recorded nowhere.

**The negative control caught a vacuous test in 8b's own commit** (`641ff23`):
the "not backfilled" assertion passed whether or not the column was backfilled.
That is recorded here because it is the strongest argument for the negative
control that this project has produced — the test was written *by* the person
running the control, in the same unit, and it still did not measure anything.

## 4. 8c (M1.75) — measured first

### 4.1 The corpus was still on the machine

`data/portal.db` from 17 August survived, so M1.61's measurement was re-run
against current code rather than quoted. **M1.61's account holds, with one
correction and one mechanism it did not name.**

| M1.61 said | Re-measured, 2026-08-19 |
|---|---|
| 31 stored bodies on `zecplus.de` | **29 bodies** — 27 on `www.zecplus.de`, 2 on `blog.zecplus.de`. **31 is the company's `artifact` row count**, which includes its two robots rows. |
| newest robots artifact is 458, `blog.zecplus.de`, `Disallow:`, permissive | **Confirmed.** id 458, 173 bytes. `_ROBOTS_SQL`'s `ORDER BY id DESC LIMIT 1` returns it. |
| `www.zecplus.de`'s own 3,624-byte file went unread | **Confirmed.** id 1, 3,624 bytes, never selected for any body. |
| `www.smoke2u.de` and `www.propellerdiscount.de` each fetched 200 three times with **no artifact row naming either** | **Confirmed, and the count is 26** — 11 and 15 bodies. But "no row" is not "never stored". See below. |

### 4.2 The mechanism M1.61 did not name

The two hosts with no row of their own are **not** a missing fetch. The request
log records *both* apex and `www.` robots.txt returning 200 three times each.
Both origins served byte-identical files, so:

* `uq_artifact_identity` is `(company_id, kind, content_hash)` — one row survives;
* `ON CONFLICT … DO UPDATE` sets `last_checked_at` and `http_status` **and not
  `url`** — so the surviving row keeps the **first** origin written.

`smoke2u.de` id 173 and `propellerdiscount.de` id 196 are apex rows. The `www.`
fetches were absorbed into them and left no trace at all.

**A collapse is invisible in the table by construction.** A corpus-wide query
for robots rows sharing a `content_hash` within a company returns **0 groups** —
because after a collapse there is only ever one row to find. There is no query
that recovers the count of collapses from the artifact table. That fact is what
decided the shape below.

Corpus-wide: **507 stored bodies, 26 on an authority naming no robots row**, and
**3 of 13 companies have more than one robots origin** (`zecplus.de`,
`doonails.de`, `germanelectronic.de`).

### 4.3 Two shapes, weighed on recovery and not on elegance

**(i) Add `origin` to `artifact` and change `uq_artifact_identity`. REJECTED.**
It prevents *future* collapses. It un-collapses nothing, because the absorbed
row was never written — and its `origin` could only ever be backfilled as
`authority_of(url)`, which is what the lookup computes anyway. So it buys **no
recovery for rows already on disk** while rewriting the identity of every stored
row: the dedup guarantee that index exists to provide is the thing being
altered, for a benefit the measurement says is zero on the half that matters.

**(ii) Leave the index alone and key the lookup on `artifact.url`. TAKEN.**
`url` already names the origin that served the bytes (`net.py` sets it from the
final response), `urls.authority_of` is already the project's single expression
for *whose robots.txt governs this URL*, and `fetch.py:181` **already filters
this way** — this unit applies an in-repo pattern to two consumers that predate
it, rather than inventing one.

**(iii) A side table recording absorbed origins on conflict. REJECTED, and it is
the one worth recording.** It would genuinely reduce over-reporting *going
forward*: on conflict, note that a second origin served these bytes. It recovers
nothing for the corpus either, it needs a migration and a writer, and it would
put a schema change and a behaviour change in one unit — which is the exact
combination §10.4b's Unit 6 ruling (L1 strictly before H2) exists to prevent.
Take it when over-reporting is measured to cost something.

**What neither shape recovers, plainly:** for a body on origin B whose company's
only robots row names origin A, **nothing on disk can establish what B served**.
The bytes were never stored under B. Only a re-fetch answers it. That is
precisely the case now reported as not verifiable.

### 4.4 No migration was taken

**`011` is free and M5 may use it.** A stored `origin` column would be a second
expression for a fact `url` already determines — M1.42's shape — and the
`ON CONFLICT` column list in §4.2 is exactly how such a pair drifts in this
schema. §10.4b records this so M5 does not collide.

### 4.5 The two consumers, both named in the brief, both changed

**`portal/impressum_audit.py:133`** — `_policy` now takes the body's URL and
matches on `authority_of`. It never returns `None` and never falls back to a
sibling. Absent a robots.txt for that origin it returns
`robots.unavailable("no robots.txt stored for origin …")`.

**`portal/audit.py` `robots_coverage`** — two changes. The breach denominator was
`b.company_id = a.company_id`, so a 503 on one origin counted bodies a *different*
origin's file governs; it is now `bodies_for_origin`. And a new
`unverifiable_origins` reports the class `robots_coverage` **cannot see**: an
origin with stored bodies and no robots row at all. That is where the 26 live —
`robots_coverage` returned nothing for either shop, because nothing had failed.

### 4.6 NOT VERIFIABLE is a different object from "no rules stated"

No new vocabulary was needed. M1.59's tri-state already carries both, as
distinct objects with distinct reason strings:

| state | constructed by | `allows()` | means |
|---|---|---|---|
| no rules stated | `robots.unrestricted()` | **True** | the shop declared nothing |
| not verifiable | `robots.unavailable(reason)` | **False** | we cannot tell whose file this was |

`test_not_verifiable_is_distinguishable_from_no_rules_stated` pins that a
robots.txt read-and-empty still measures its page, while an origin with nothing
on disk is refused with `robots_unavailable` in the reason. They send an
operator to different places: one is "this shop has no policy", the other is
"go and fetch this host's policy".

### 4.7 Direction of error: it over-reports, deliberately

A collapsed row that genuinely *did* govern both origins is now called not
verifiable. That costs a company a run and says exactly why. The alternative —
under-reporting — means bodies fetched under a policy nobody read, which is H1's
whole family and the reason M1.59, M1.61 and M1.68(e) all exist. **This fails
toward refusing to measure.**

### 4.8 The corpus after the change

| | before | after |
|---|---|---|
| impressum candidates chosen | 11 | **9** |
| skipped | 2 | **4** |
| `zecplus.de`'s governing robots | artifact **458** (`blog.`, 173 B, permissive) | artifact **1** (`www.`, 3,624 B, its own) |
| `unverifiable_origins()` | *did not exist* | `www.propellerdiscount.de` 15 bodies, `www.smoke2u.de` 11 bodies |

```
propellerdiscount.de  robots_unavailable: no robots.txt stored for origin www.propellerdiscount.de (M1.75)
smoke2u.de            robots_unavailable: no robots.txt stored for origin www.smoke2u.de (M1.75)
```

`zecplus.de` is still measured — correctly, and that is the point: it is now
tested against its own 3,624-byte file instead of a sibling's permissive one,
and it passes that test. The fix changed *which file governed it*, which is what
M1.61 asked for; it did not merely reject more shops.

**`OBSERVED_PAGES = 11` was left at its measured value, not moved to 9.** It is a
record of what was observed on 2026-08-16, and the report prints `<-- was n/11`
so an operator sees the divergence. Moving a baseline to match a new result is
how a baseline stops being evidence.

## 5. Negative control

The guarantee was removed — `_policy`'s authority filter deleted and its
`unavailable` swapped back to `unrestricted`, and `unverifiable_origins`'
reporting reverted — and the suite re-run.

```
6 failed, 583 passed, 2 skipped, 126 subtests
```

**All six failures are 8c's own tests. 0 of 581 pre-existing tests could see the
absence.** The series is now:

| unit | pre-existing tests that could see it |
|---|---|
| Unit 6 | 0 of 537 |
| Unit 7 | 0 of 551 |
| **Unit 8c** | **0 of 581** |

The denominator re-key was controlled separately, since the first control did
not touch it: reverting `bodies_for_origin` to the company-wide count fails
`test_the_breach_denominator_is_the_origin_not_the_company` and nothing else.

**A pre-existing test did catch a real defect in 8c's first draft**, which is
worth more than the zero above. `unverifiable_origins` initially required a
**200** to consider an origin covered, which reddened every 4xx shop —
`test_a_404_robots_is_reported_and_does_not_fail` failed immediately. A 404 is
RFC 9309 §2.3.1.2's "no rules stated" and *is* a definitive answer; a 5xx is
already reported by `robots_coverage` as unavailable. The rule is now "any
robots row covers its origin", and the class left for this function is the one
with no row at all.

## 6. Still open

- **The untransmitted audit section** — headed *"LLM-generated/hallucination
  signals"*, missing and not empty. Unchanged by this unit. The canonical
  membership list and its derivation live in §Unit 2a's amendment (M1.73), and
  **this document is now a member**. The count is not carried forward and not
  computed as "four plus this one" — that self-reference is how Unit 5 reached
  its figure. This section was written first, then the grep was re-run, and the
  number below is what it returned:

  ```
  $ grep -rlniE "LLM-generated ?/ ?hallucination" docs/unit*-findings.md | sort
  docs/unit4-robots-tristate-findings.md
  docs/unit5-portability-and-ci-findings.md
  docs/unit6-address-guard-findings.md
  docs/unit7-cost-ceiling-findings.md
  docs/unit8-m5-prerequisites-findings.md
  ```

  **Five.** Pre-existing membership was confirmed as Units 4–7 before this
  document was added, as the extension rule requires.
- **M3 — the repository is still public.** Verified this unit via
  `gh repo view --json visibility` → `PUBLIC`, not taken from a previous report.
- **M1.72 — the batch reservation's two writes are not one transaction.** M5's,
  by §10.4b's own sequencing.
- The full register, derived rather than remembered, is §7.

## 7. Open items register — derived, not remembered

**How this was derived.** M1.73's lesson is that a backlog carried forward
verbatim rots. So every row below was re-checked this unit against the artefact
that decides it — the code, the schema, the GitHub API, or the spec section that
owns it — and the method is named in each row. **No row was taken from a
previous unit's report, including the brief's own list.**

| item | state | derived from |
|---|---|---|
| **M3 repository visibility** | **OPEN** | `gh repo view --json visibility` → `PUBLIC` |
| **M1.72 transactional reservation** | **OPEN, M5's** | spec M1.72 row; no `BEGIN`/`SAVEPOINT` around the pair exists because the caller does not exist |
| **Untransmitted audit section** | **OPEN** | `grep -rlniE "LLM-generated ?/ ?hallucination" docs/unit*-findings.md` |
| **§10.5 DNS-rebinding residual** | **OPEN, UNOBSERVED, labelled not fixed** | §10.5; closing it needs a pinning `httpx` transport, refused under M1.4 |
| **§10.5 address guard's architecture limit** | **OPEN and uncloseable by design** | §10.5 — a public address proxying to something internal is not visible to an address classifier |
| **§10.3 ban on calibrating §6.5 on this corpus** | **STANDING** | §10.3; 3 of 13 shops are ~25 points light for platform reasons |
| **§10.3 "when is a written count untrustworthy"** | **OPEN, with one fewer piece of evidence** | §10.3's closing paragraph |
| **§10.2 `owner_operated` lever** | **OPEN, not a correctness blocker** | §10.2; predicate matches 0 of 12, marker appears on 1 of 11 |
| **§10.1 blockers** | **EMPTY — all three closed** | §10.1 renders an empty table: *"M3 may start, and has."* |
| **A1** `PHASE2_MAX_POINTS` / gate no-op | **CLOSED** | M1.21→M1.22, per-company gate; `PHASE2_MAX_POINTS` no longer exists in `portal/`, only in a `ruleset.py` comment recording it |
| **A2** Phase-2 outputs have no signal keys | **OPEN** | no field→key mapping table in the spec; `HomepageExtract.own_brand` still has no view column |
| **A3** `agency.footer_credit` two writers, no merge rule | **OPEN** | `ruleset.py:414` reads one `agency_credit` key; the second writer is M5's |
| **A4** no confidence filter into scoring | **OPEN** | `company_profile` view (`001_initial_schema.sql:168`) pivots latest-per-key with **no** `confidence` predicate |
| **B1** brief export fail-loudly vs omit | **OPEN** | M7 not started; predicate still unstated |
| **B2** `needs_review_reason` is one column | **CLOSED** | migrations 003–005, 008, 009 add distinct reasons; §6.4 vocabulary |
| **B3.1** reconcile vs submitting run | **CLOSED** | M1.69/M1.70 — the `run` row is the ledger, keyed on `run.started_at` |
| **B3.2** ceiling sums estimates, never actuals | **OPEN** | §7 control 2 sums `run.est_cost_usd`; `ledger.monthly_spend_usd` does the same |
| **B3.3** reconciliation cost-ledger rule | **OPEN** | M5's reconcile path does not exist |
| **B4** `run_id` for reconciled signals | **CLOSED** | settled with B3.1 — the submitting run |
| **B5** ruleset version inconsistent | **CLOSED in the code** | `ruleset.RULESET_VERSION = "v3"` is the single source; the one surviving `ruleset v2` (spec:1319) is a historical parenthetical about v0.1→v0.2, not a live claim |
| **B6** ruleset representation undefined | **CLOSED** | `ruleset.Rule` carries `reads`, `points` and `phase2_reachable`; `assert_declared` enforces it |
| **B7** `own_domain_shop` has no predicate | **CLOSED; residual is §10.3's** | `ruleset.py:218 _own_domain_shop`, ≥5 product URLs, comment cites B7. It cannot *fire* on 3 shops — that is §10.3's measurement limit, not a missing predicate |
| **C1** blog ladder scores a healthy new blog | **OPEN** | chain order unchanged in `ruleset.py` |
| **C2** one failed search costs `opp.ai_invisible` | **OPEN, untestable** | `ruleset.py:371` reads `ai_queries_checked`; M6 not started, so no failure has ever been observed |
| **C3** M7 blocked transitively on M6 | **PREMISE DISSOLVED** | §10.5: *"M6 is unblocked, not started"* (M1.54). M7's remaining blocker is **B1**, independently |
| **C4** `uq_signal_identity` includes `evidence_url` | **OPEN** | `001_initial_schema.sql:155`, unchanged |

**Four rows disagree with the brief's list**, which is the register's whole
point — see below.

## 8. Where the instructions were wrong

**The next defect number was M1.75, not M1.74.** The brief said M1.74 and told
me to verify before claiming, citing M1.49's double-claim. Verified:
`grep -o "^| M1\.7[0-9]"` returns M1.70–M1.74, so **8b already took M1.74** and
8c is M1.75. The instruction to verify was right; the number in it was not.

**The brief's open-items list is a carried-forward scalar of exactly the kind
8a fixed.** The list *"A1–A4, B1, B3.2–B3.3, B5–B7, C1–C4"* is a verbatim quote
of `docs/lead-portal-spec-v0.3.md:113` — *"Remaining findings … are still open
and are not required by M0 or M1"* — a sentence written at M0/M1 time and never
re-derived since. Four of its members have closed underneath it:

* **A1** — closed by M1.21→M1.22 and ratified 2026-08-16, *in the same document*
  that still lists it as open at line 113.
* **B6** — closed as a byproduct of M1.22: the per-company gate required rules to
  exist as data, which is what B6 asked for.
* **B7** — the predicate exists and its comment cites B7 by name.
* **C3** — its premise is gone; §10.5 records M6 as unblocked.

That is M1.73's defect a second time, in the backlog rather than in the counter,
found only because the register was derived. **Line 113 should be replaced by a
pointer to §7's method rather than corrected in place**, since correcting the
membership leaves the mechanism that rots it. Not done in this unit: it is a
spec-structure change and 8c was already at its stop condition's edge.

**M1.61's "31 bodies" was a row count.** 29 bodies plus 2 robots rows. Minor,
but the brief asked for the numbers beside M1.61's and this is the difference.

**M1.61's "NO artifact row naming either" was true but incomplete**, and the
incompleteness mattered: it reads as a missing fetch, and it is a *collapse*.
The rows exist under the apex sibling. A reader who took the brief literally
would look for a fetcher bug and find none.

**Step 3 presumed a migration.** It said *"Migration 011 — 010 is 8b's"*, and
011 was not needed. The requirement §10.4b actually ratified is *"an origin-keyed
robots **lookup**"* — a lookup, not an index — and the measurement in §4.2 is
what shows the schema change buys nothing for the corpus. The number is
recorded as **not taken**, which is the thing that prevents M5 colliding.

## 9. Gate verification, 2026-08-18 — the record of the stack landing

Derived from `gh pr list --state merged` and `git`, this unit, not from Unit 7's
report.

| PR | branch | merged | method | merge commit |
|---|---|---|---|---|
| **#1** | `claude/lead-portal-unit5-m4-1henyf` | 2026-08-18 21:05:35Z | **merge commit** | `77d4bc6` |
| **#2** | `claude/unit7-cost-ceiling` | 2026-08-18 21:07:12Z | **merge commit** | `3f0db48` |

Both merged by merge commit, in stack order, two minutes apart. **Both branches
are still on the remote** (`git ls-remote --heads` matches 2), as required.

**The faithfulness diff is empty and the tree hashes are identical.**

```
$ git log -1 --format=%T origin/main    c58b270508f18b1a06569a464ec254b6e705c4e0
$ git log -1 --format=%T aaa41bb        c58b270508f18b1a06569a464ec254b6e705c4e0
$ git diff --stat aaa41bb origin/main   (empty)
```

`aaa41bb` is Unit 7's branch tip. Merged `main` and the tip of the stack are the
same tree, `c58b2705…`, so the two merge commits added no content and dropped
none: the stack landed exactly as reviewed.

**Post-merge counts, run on merged `main` itself** (`3f0db48`, `data/` moved
aside, no key) rather than inferred from the branches:

```
576 passed, 2 skipped, 123 subtests passed
```

## 10. Verification

All runs with `data/` moved aside and `ANTHROPIC_API_KEY` unset.

| check | result |
|---|---|
| `3f0db48` merged main, baseline | 576 passed, 2 skipped, 123 subtests |
| `641ff23` (8a+8b), pre-8c | 581 passed, 2 skipped, 126 subtests |
| 8c complete | **589 passed, 2 skipped, 126 subtests** |
| negative control (guarantee removed) | 6 failed — all six 8c's own |
| negative control (denominator only) | 1 failed — the denominator test alone |
| `ruff check .` | clean |
| `ruff format --check .` | clean |
| `audit-politeness` fixture corpora | both green — exit 0 and exit 1 |

**Size against the stop condition.** Unit 7 was 1,244 insertions across 9 files.
8c is well inside that, and the reason is §4.3: the shape that fit the
measurement was also the small one. Had the identity-index change been the only
shape that answered M1.61, this unit would have stopped and shipped 8b alone.
