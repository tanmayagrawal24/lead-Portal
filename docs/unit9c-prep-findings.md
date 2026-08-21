# Unit 9c-prep — the mitigation that became the defect, and a register that was wrong for four units

Measured 2026-08-21 against `650b544` on `claude/unit9c-prep`. **No crawl, no API
call, no key, no spend.** `ANTHROPIC_API_KEY` was confirmed unset before the
first command and never set; the only network traffic was `git` and `gh`.

Companion to **M1.95–M1.100** in `docs/lead-portal-spec-v0.3.md`.

**What this unit is.** Unit 9c was briefed as the first real spend. It stopped 89
seconds in, on two blockers it could verify and not clear. This unit is the
reconciliation of what that stop uncovered: one destroyed artefact, one false
statement in the spec that had reached three shipped migration headers, and two
claims that were about to be recorded as measurements without ever having been
measured. **It builds no Phase-2 behaviour and takes no migration.**

**One thing this unit did not do, stated at the top because it is the largest
open item.** It could not run the batch listing that would establish whether any
money has ever been spent. See §6 and §10.7b.

---

## 0. Baseline

| | |
|---|---|
| Branch point | `650b544`, `origin/main`, clean tree, nothing ahead |
| Full suite before any change | **698 passed, 2 skipped, 139 subtests**, `env -u ANTHROPIC_API_KEY` |
| Highest amendment | **M1.94** — verified by `grep -rhoE 'M1\.[0-9]+' docs/ portal/ tests/ README.md .github/`, not taken from the brief |
| Highest migration | **015**. This unit takes none |
| `data/` | **absent** |

The suite's 698 passed **with no corpus on the machine**. That number is the
first finding: the instruction that destroyed the corpus existed to make this
run possible, and this run did not need it.

---

## 1. M1.95 — the instruction, verified rather than taken

The brief supplied the causal account. It was checked against the transcripts and
the git history rather than accepted, and it holds in every particular. Two
details are tighter than the brief stated.

### 1.1 The defect, and the commit that fixed it

`aaa41bb`, **2026-08-18 20:27:53 +0000**, in its own message:

> CI failed both pytest jobs while the suite was green locally. Real defect, not
> flakiness: `llm-prices --reserve` now opens the database, and the pre-existing
> test used the default path. This machine has data/portal.db from 17 Aug; a CI
> runner has none […]
>
> PricesCommand builds its own database.
>
> Verified the way it should have been the first time -- data/ removed, key
> removed, as CI runs it: 576 passed, 2 skipped.

The defect was **fixed at source**. From that commit forward, no test needed
`data/` to be anywhere in particular.

### 1.2 The instruction outliving it — measured

Seven occurrences across six units, from the session transcripts:

| Session | Timestamp | Form |
|---|---|---|
| `2c7ab981` | 2026-08-18 **21:04:32** | *"with `data/` moved aside and ANTHROPIC_API_KEY unset"* |
| `2c7ab981` | 2026-08-18 21:16:37 | *"BEFORE YOUR FIRST PUSH: move `data/` aside…"* |
| `76543832` | 2026-08-19 04:03:55 | *"a full suite run with `data/` moved aside"* |
| `e82bbfdf` | 2026-08-20 18:46:41 | *"BEFORE EVERY PUSH: `data/` moved aside…"* |
| `f0fd7504` | 2026-08-20 21:13:11 | *"BEFORE EVERY PUSH: `data/` moved aside…"* |
| `5b3575ee` | 2026-08-21 00:08:16 | *"Before pushing: `data/` moved aside…"* |
| `d0771622` | 2026-08-21 04:33:17 | *"Before every push: `data/` moved aside for the test run"* |

**The first re-issue is 37 minutes after the fix landed.** Not months later by an
author who had forgotten — the same evening, in the next brief written.

**No occurrence names a destination, and no occurrence names a restore step.**

### 1.3 The destruction, reconstructed from the transcript

Session `f0fd7504`, 2026-08-20:

```
21:14:53  test -d data && mv data data_aside && echo "moved"
21:19:50  du -sh data_aside && mv data_aside /tmp/claude-1000/.../f0fd7504-.../scratchpad/data_aside
21:20:00  137M	data_aside
...
21:53:52  You've hit your session limit · resets 11:40pm (UTC)
```

No restore command appears anywhere in that session. `/tmp` was subsequently
cleared and `find /` returns no `portal.db`.

**The session immediately before it did the same thing and survived** —
`e82bbfdf` at 18:48:32 moved `data` to its scratchpad and at **19:35:10** ran
`mv .../data-aside data`. So the practice was not obviously fatal; it was fatal
exactly once, when a session ended between the move and the move back. **An
instruction whose safety depends on the session not being interrupted is an
instruction with a failure mode, not a precaution.**

### 1.4 The class, and why nothing caught it

The finding is not that a file was lost.

> **A mitigation is a liability with no owner and no expiry.** The defect it
> names is fixed, testable, and has a commit. The mitigation is prose, is copied
> forward by whoever writes the next brief, and is never re-derived against the
> thing it mitigates.

It is M1.73's copied scalar with a destructive side effect — with one difference
that matters more than the similarity. **M1.73's counters were in the repository,
so a unit could grep them.** This lived only in prompts. Six units of review
passed over it because review reads the repository, and this was never in it.

**CI had been falsifying it on every run since Unit 5.** M1.65's runners have no
`data/` at all — there is nothing there to move aside. Every green CI run was a
proof that the instruction was unnecessary, and no unit read it as one, because
nobody was looking for evidence about an instruction that was not written down
anywhere a unit would look.

### 1.5 What was deleted from the repository: nothing

Per the brief, every doc, brief and comment was swept:

```
$ git grep -n -iE "data/? (moved|aside)|move .{0,10}data.{0,4} aside|mv data|data_aside|data\.aside|data-aside"
(no matches)

$ git grep -n -iE "before (every|your first) push|unset ANTHROPIC|ANTHROPIC_API_KEY unset|corpus aside|set aside"
.github/workflows/ci.yml:114:        run: env -u ANTHROPIC_API_KEY python -m pytest -q
docs/lead-portal-spec-v0.3.md:234: (M1.65, describing that CI line)
docs/unit5-portability-and-ci-findings.md:228,236 (the same, historically)
docs/unit7-cost-ceiling-findings.md:348 (a recorded run command)
```

**Nothing to delete.** All four surviving hits are `env -u ANTHROPIC_API_KEY`,
which is the *correct* practice — the variable removed for the duration of one
command, nothing moved, nothing to restore — and is CI's own line.

**The absence is the finding, not a clean bill of health.** The instruction that
destroyed the corpus was never reviewable, because it was never in the artefact
that gets reviewed.

### 1.6 The replacement: a test, because a convention asks and a test refuses

`tests/conftest.py`. `db.connect` calls `path.parent.mkdir(parents=True,
exist_ok=True)`, so anything opening `config.DEFAULT_DB_PATH` creates `data/` as
a side effect. The fixture records whether `data/` existed at session start and
fails the session if it exists at the end and did not at the start.

**Negative control, run before it was trusted:**

```python
class BorrowsTheMachinesDatabase(unittest.TestCase):
    def test_opens_the_default_path(self) -> None:
        conn = db.connect(config.DEFAULT_DB_PATH)   # the aaa41bb defect
        conn.close()
```

```
.E                                                                       [100%]
E   the test suite created /workspaces/lead-Portal/data — something opened
    config.DEFAULT_DB_PATH instead of a temporary database. […] do not move the
    corpus out of the way to make this pass (M1.95).
1 passed, 1 error in 0.03s
```

**The test passes and the session fails**, which is the shape required: the
defect is not a property of any single assertion, so no single assertion can be
about it.

**What it cannot do is in its docstring rather than left to be discovered.** On a
machine that already holds `data/`, before and after are both *present* and the
guard is silent. That is not a hole to close here — it is M1.64 and M1.19's
standing ruling that the authority is the run that gates the merge. CI runners
never have `data/`, so CI is where this check has teeth. Closing the gap properly
would mean intercepting `sqlite3.connect` to guard against a defect CI already
fails on.

**Options rejected.** *(a)* A static scan of test sources for `DEFAULT_DB_PATH` —
rejected because the Unit 7 defect was the **CLI** opening the default path from
inside a test that never named it, which a grep cannot see. *(b)* Deleting the
instruction from the briefs — not available; there is nothing in the repository
to delete, and the briefs are not this project's artefact.

---

## 2. M1.96 — the corpus ruling

§10.4b carried the scar of the lost M5 stash and said nothing about the corpus. A
resource with no stated durability is defended by whatever habit the current
brief happens to carry, which is exactly how §1 happened.

**RULED: DISPOSABLE.**

**The ruling rests on a fact rather than on a preference: the irreplaceable input
is already durable.** `seeds/candidates.csv` — 13 domains, platform verified by
hand — is committed. What was lost is *derived*: fetched bodies and the signals
computed from them, which `fetch` and `extract-p1` recompute from the seed list.

**Recovery procedure:**

```
portal init
portal fetch --seed seeds/candidates.csv
portal extract-p1
portal score --phase 1
```

**Accepted cost, written down:** wall-clock at the §5.2 politeness floor (1 req/s
per host, 2 hosts in flight) and **zero LLM spend** — Phase 2 has never run
against this corpus.

**DURABLE was weighed and rejected on §8, not on convenience.** A durable home
means a second copy of a database holding the names and addresses of 13 real
businesses — and, once M5 runs, their contact details — in a location outside the
container and therefore outside the ruling M3 already took when it made this
repository private. The two error directions are not comparable:

- **Disposable, and wrong** → this project pays for a re-crawl and loses the
  measurements in §10.7a.
- **Durable, and wrong** → third-party personal data sits somewhere §8's handling
  rules do not reach, on behalf of people who never consented to the first copy.

§8 is the section that decides which of those weighs more, and it already has.

**What the ruling does not buy, and this half must travel with it: a re-crawl
produces A corpus, not THE corpus.** The sites have moved on. That is §3.

---

## 3. M1.97 / §10.7a — fifteen measurements that lost their evidence

Found by reading the spec, not from the brief's list. The brief named four
(M1.61's zecplus figures, §10.2's 1/11, §10.3's 3-of-13, M1.63's navucko); **all
four are present and eleven more were found.**

Full table in **§10.7a**. The marker is the literal greppable string
`MEASURED <date>, NOT REPRODUCIBLE`, and each row carries **its own** date —
they span 2026-08-15 to 2026-08-20, and one "the lost corpus" date would itself
be the copied scalar M1.73 is about.

Summary of what is now marked:

| Taken | Rows |
|---|---|
| 2026-08-15 | §10.3's 3-of-13; `smile-store.de`'s 194; §10.2's 5-of-12 and §7.1's $31–36/month |
| 2026-08-16 | §10.2's 1/11 and 3/12; M1.48's `Amtsgericht` 3/12-vs-2/12; feed autodiscovery 4-of-6; M1.37's 6-of-13; M1.36's three JTL shops at −20; `propellerdiscount.de` at 0+50 |
| 2026-08-17 | M1.55's 0-of-307; the disallow count of exactly two (artifacts 171, 186); M1.63's `navucko.com` D→C |
| 2026-08-18 | M1.61→M1.75's zecplus 29 bodies, artifact 458 over 1, and 26 bodies on two `www.` hosts |
| 2026-08-20 | M1.79's 2,404 signal rows; M1.76's `germanelectronic.de` at 5+50=55 |

**Nothing was deleted and nothing was restated as checkable**, per the brief. The
reason deletion would be wrong is worth stating: **in every row above it was the
reasoning that changed the code, not the count.** M1.75 re-keyed the robots
lookup because a permissive file from another origin governing a shop's stored
bodies is wrong on its face — and it would still be wrong if the byte counts had
been different. A finding does not become false when its evidence expires.

What the marking removes is these numbers from the set a later unit may treat as
*checkable*. **A re-measurement after a re-crawl is a new measurement and takes a
new date and a new row.** Two numbers describing different corpora under one
citation is M1.42's two-expressions defect with a week between the expressions.

**Direction of error, since it is not symmetric.** A row marked unreproducible
that could in fact be re-measured costs a later unit one re-measurement. An
unmarked row that cannot be re-measured costs it a false correction that it will
believe, and that will then propagate — which is §4's failure exactly.

---

## 4. M1.98 — the stash, and the register that hardened a conditional into a fact

### 4.1 What is in it

`stash@{0}`, `interrupted-M5-remnant`, base commit **`6a5e266`** — the exact
commit §10.4b names — taken **2026-08-17 16:17:17 +0000**. **Read with
`git stash show -p` and `git ls-tree`. Not popped, not applied.**

```
$ git rev-parse -q --verify stash@{0}^3    # a third parent ⇒ stashed with -u
YES - untracked included
```

**Tracked half** (`git stash show --stat`):

```
 portal/ruleset.py   | 168 ++++++++++++++++++++++++++++++++++++++++++++++++----
 portal/score.py     |  18 +++++-
 tests/test_score.py |  28 ++++++---
 3 files changed, 194 insertions(+), 20 deletions(-)
```

**Untracked half** (`git ls-tree -r -l stash@{0}^3`):

```
19440  portal/migrations/010_phase2_writers.sql
 8983  portal/pagespeed.py
 5846  portal/verify.py
```

**Against Unit 5's inventory, item by item — all six present:**

| Unit 5's inventory | In the stash |
|---|---|
| `phase2_input_settled` on `Rule` with `assert_declared` enforcing it | ✅ 15 hits in the tracked diff |
| the `settled` term | ✅ 23 hits |
| three-state `_own_brand` | ✅ 5 hits |
| three-state `_owner_operated` | ✅ 4 hits |
| `portal/pagespeed.py` | ✅ 8,983 bytes |
| `portal/verify.py` | ✅ 5,846 bytes |
| `migrations/010_phase2_writers.sql` | ✅ 19,440 bytes |

**§10.4b is wrong, and is corrected.**

### 4.2 Unit 6 was right. The register dropped the qualifier

This is the finding, and it is not the one the brief expected.
`docs/unit6-address-guard-findings.md:21-31` states it **conditionally and
correctly**:

> The work is not lost *if* the machine Unit 5 ran on still exists — and it is
> unrecoverable from the repository alone, because nothing was committed.

§10.4b rendered that as:

> **The interrupted M5 work is GONE and cannot be recovered.**

**The lost `if` is the entire defect, and the condition was true the whole
time.** Unit 5's session ran at `/workspaces/lead-Portal` in the codespace
created `2026-08-15T01:37:43Z` — the one this was verified on, still `Available`.

> **A negative observation made in one working copy was written into the register
> as a property of the world.** A stash is local to a working copy *by
> definition* — which is precisely why *"it is not here"* is evidence about a
> container and never about the artefact, and why that sentence needed its `if`
> more than any other sentence in §10.4b.

### 4.3 The propagation — seven sites, three of them shipped schema

| Site | Was |
|---|---|
| `docs/…spec-v0.3.md` §10.4b | *"is GONE and cannot be recovered"* |
| `docs/…spec-v0.3.md` §10.6 | *"the lost stash's `010` is unrecoverable"* |
| `docs/…spec-v0.3.md` M1.74 | *"a different, unrecoverable file"* |
| `portal/migrations/010_score_evaluated_on.sql` | *"UNRECOVERABLE FILE"* (capitals in the original) |
| `portal/migrations/011_phase2_view_columns.sql` | *"a different, unrecoverable `010_phase2_writers.sql`"* |
| `portal/migrations/015_batch_requests.sql` | *"unrecoverable and this schema was derived from §5.6's requirements"* |
| `docs/unit9a-phase2-scaffolding-findings.md` | *"the lost stash's `010` is unrecoverable"* |

All corrected. The migration headers carry no checksum — `migrate.py` tracks
applied state in `PRAGMA user_version` alone — so editing comments is safe and
was verified before editing.

`unit9a`'s line is a **dated note appended rather than a rewrite**: what it
recorded faithfully is what the register said at the time, and the record of that
is worth keeping. `unit6`'s line needs no correction; it was right.

**Nobody re-ran `git stash list`, in any container, for four units.** One word,
zero cost, available everywhere the repository was.

### 4.4 What this is worth, given 9a rebuilt most of it

**The comparison is worth more than the file.** `verify.py` exists in the tree
today at 6,053 bytes, written independently by 9a. Against the stashed 5,846-byte
original: the same structural ruling, different prose.

> *Stashed:* "**Verify against what was sent, never against the page.** The
> verifier takes the sent text as an argument and has no way to reach an
> artifact."
>
> *Rebuilt:* "**It takes the SENT TEXT as an argument and cannot reach an
> artifact.** That is a structural choice, not an ergonomic one."

Two independent derivations of the same ruling is the strongest evidence
available that the ruling is right.

**And what is genuinely still unbuilt is now knowable rather than guessed:**

| Stashed `010`'s five changes | Today |
|---|---|
| `contact.vat_id` | has a writer (`extract_p2`, `reconcile`) |
| `llm_batch.status = 'balance_exhausted'` | still §10.6's *ahead of its writer* |
| two review reasons | landed as migration `013` |
| `company_profile` rebuilt | landed as migration `011` |
| `run.pagespeed_calls` | **exists nowhere in the tree** |

`portal/pagespeed.py` was **never rebuilt** — `pagespeed` appears in no tracked
file. Migration `010`'s number is taken by `score.evaluated_on` (M1.74) and the
stashed file cannot reclaim it; its content, if wanted, arrives on `016` or later
with its writers, per M1.45(c).

---

## 5. M1.99 — a CLI OAuth token is not an API key

A triage session on 2026-08-21 was authorised for exactly one read-only
`messages.batches.list`. It found:

```
ANTHROPIC_API_KEY: UNSET
ant CLI:           not on PATH
~/.anthropic, ~/.config/anthropic, ~/.ant:  absent
Codespaces secret stores (5 files, names only): ANTHROPIC match -> NONE
~/.claude/.credentials.json:  exists
  claudeAiOauth subkeys: accessToken, expiresAt, rateLimitTier, refreshToken, …
```

It declined to use the OAuth token and reported no credential.

**§7 control 9 did not tell it to decline.** The control governs where an *API
key* comes from; this was not an API key. The next session in that position has a
brief that wants a listing, a control that is silent, and a working token one
`Authorization` header away.

Control 9 now carries the distinction, with three reasons of which the third
generalises past this credential:

- **(a) Different credential, different surface.** It authenticates a *person* to
  their CLI session, not this application to the Anthropic API.
- **(b) It is invisible to every control in §7.** Controls 2–4 reserve and sum
  against `run.est_cost_usd`. Consumption against a subscription token writes no
  row anywhere, so `ledger.monthly_spend_usd` would report **`$0.00` and be
  correct** — a ledger that is right about the wrong universe, which is worse
  than one that is wrong.
- **(c) Authorisation to read is not authorisation to reach for a different
  credential to read with.** A permission's scope is the action *and* the means.
  A session that substitutes the means has exceeded the permission **while
  believing it complied**.

**Recorded as the ruling rather than as a fact about one session's judgement**,
which is the difference between a good instinct and a control.

---

## 6. M1.100 / §10.7b — the batch question is OPEN, not zero

**This unit could not answer it, and that is recorded rather than rounded.**

Every local instrument is gone or was never written. `llm_batch` lived only in
`data/portal.db`; no code has ever written a row to it, because `extract-p2`
stops short of §7 control 4's reservation by design (M1.83).

**The evidence against submission is strong and is all of one kind:**

- `ANTHROPIC_API_KEY` unset in every session of every unit.
- CI's `assert-no-api-key` step fails the build if it is present (M1.65).
- The Unit 9c transcript records `ANTHROPIC_API_KEY: NOT SET` at **04:34:20** on
  2026-08-21, 89 seconds in, before it stopped.

**But *no key on this machine* is a statement about this machine** — which is
§4's defect exactly, one day later and pointing the other way. This unit is not
going to make it twice in the same document.

**Why it may not be recorded as zero:** it is M1.59's tri-state applied to our own
audit trail. *We cannot tell* and *there is nothing* are different states that
send a reader to different places.

Closing procedure and the interpretation of the answer are in **§10.7b**. The
operative constraint for whoever runs 9c: **a batch that exists means the spend is
already committed, results stay retrievable for 29 days, retrieval costs nothing
extra, and resubmitting would double the cost** — so the listing precedes any
work that could submit.

---

## 7. Where the instructions were wrong

**The brief said "M1.94 was the last I know of" and asked me to verify.** It was
correct — `grep -rhoE 'M1\.[0-9]+'` over `docs/ portal/ tests/ README.md
.github/` tops out at 94. Next was 95, and this unit took 95–100.

**The brief said to "grep every doc, brief and comment … delete it, and say what
you deleted."** Nothing was deleted, because nothing was there. The instruction
never entered the repository — and that is §1.5's finding, which is worth more
than a deletion would have been.

**The brief said §10.4b's stash conclusion was "a conclusion Unit 6 reached from a
DIFFERENT container and wrote into the spec as fact."** Half right, and the other
half is better. Unit 6 *did* reach it from a different container — but it wrote
the **conditional** into its own findings document, correctly. The unconditional
form appears first in §10.4b. **Unit 6 is owed the credit and the register takes
the defect.**

**The brief listed four unreproducible measurements and said to find the rest by
reading.** All four are real; eleven more were found.

---

## 8. Provenance — what this unit measured, and what it did not

**Measured here, on this machine, today:**

- the suite baseline (698/2/139) with no corpus present
- the stash's full contents, both halves, against Unit 5's inventory
- the seven brief occurrences of the instruction, from transcript JSON
- `aaa41bb`'s content and timestamp, from git
- the seven propagation sites, from `git grep`
- the guard's negative control
- the absence of any API credential, names-only, values never read

**Reconstructed from transcripts, and labelled as reconstruction:** the
destruction sequence in §1.3. The commands and their outputs are quoted from the
session log; no filesystem evidence of the move survives, because `/tmp` was
cleared. The 137 MB figure is that session's own `du -sh`.

**Not measured, and not asserted:** whether any message batch exists (§6).

**Unobserved, labelled unobserved:** whether a re-crawl today would reproduce any
of §10.7a's fifteen rows. No re-crawl was run — this unit is `NO CRAWL` — so the
claim is that they are *not reproducible in principle*, from the nature of the
sites, not that any specific one has been shown to have changed.

---

## 9. Verification

```
$ ruff check .                     All checks passed!
$ ruff format --check .            64 files already formatted
$ env -u ANTHROPIC_API_KEY python -m pytest -q
```

Full-suite result and CI status recorded in §10 **after** they were observed, per
M1.19's rule that the authority is the run that gates the merge.

---

## 10. Open items register — derived, not remembered

Re-derived row by row against the artefact that decides each row, per M1.93.

| Item | Decided by | State |
|---|---|---|
| Was a batch ever submitted? | `messages.batches.list`, or the Console | **OPEN** — §10.7b. Not zero |
| The 13-company corpus | `ls data/portal.db` | **GONE.** Ruled disposable (M1.96); recovery procedure in §10.4b |
| `interrupted-M5-remnant` | `git stash list` | **PRESENT**, complete, base `6a5e266`. Not applied |
| `portal/pagespeed.py` | `git grep pagespeed` | **Never rebuilt.** In the stash only |
| `run.pagespeed_calls` | `git grep pagespeed_calls` | **Exists nowhere.** Needed by §5.5a's quota model |
| `llm_batch.status = 'balance_exhausted'` | §10.6 | Still ahead of its writer |
| §10.2's cost lever | §10.2 | Open, and its base rates are now unreproducible (§10.7a rows 3–4) |
| §6.5 band calibration | §10.3 | Blocked, and now blocked on a corpus that no longer exists |
| Next free migration | `ls portal/migrations/` | **016** |
| Next free amendment | this document + the register | **`M1.<101>`** — written with angle brackets so it is not a citation; see §11 |


---

## 11. M1.94, a second time, on this document

M1.91's check refused this findings document on its first run:

```
M1.<101> — cited in docs/unit9c-prep-findings.md      [number substituted]
A citation that cannot be looked up is worse than no citation: it reads as an
authority. Write the row, or drop the number (M1.91).
```

**The row above said the next free amendment number was `M1.<101>` — and naming an
unused number is indistinguishable, to a grep, from citing a row that does not
exist.** This is M1.94's exact finding recurring: *a document that quotes a
citation is a citation*, and the check is right both times.

**Fixed the way M1.94 ruled, not by widening the check.** The number is written
`M1.<101>` — angle brackets, which do not match `M1\.\d+` — with the
substitution stated at the site. An exclusion for lines containing "next free"
was considered and rejected for M1.94's reason: a rule that can be opted out of
by writing a phrase is the convention this test exists to replace.

**And it took two passes, which is the part worth recording.** Substituting the
number in the handoff row was not enough: the paragraph above **quotes the
failure message**, and the quoted message contains the number, so the check
refused the document a second time for reporting the first refusal. The fix is
the same substitution applied inside the quotation, marked `[number
substituted]`. **A document that describes this check cannot quote its output
verbatim** — which is not a defect in the check, but it is a property anyone
writing the third instance should know before they spend a run finding it.

**What is new is the trigger.** M1.94's instance was a *quoted negative control*;
this one is a **forward reference** — a unit naming the number the next unit
should take, which is a thing every findings document in this project has reason
to do. It is recorded because the third instance should be recognised rather than
re-derived, and because it is now clear the check will refuse any handoff line
that names an unused number.

**Caught locally this time, not by CI.** M1.94 recorded that the check is more
permissive before a commit than after one, because `git ls-files` cannot see an
untracked file. This document was staged with `git add -A` before the suite was
run — which is the working practice that closes that gap, and it is worth naming
as such rather than leaving to chance.
