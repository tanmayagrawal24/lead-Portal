# Unit 11 — PR #7 reconciled onto `main`, and the register collision that made it necessary

**DOLLARS SPENT: $0.00.** No batch was submitted, no paid call was made, and
nothing this unit added reads `ANTHROPIC_API_KEY`. The work is a merge, a port
and a renumbering: PR #8 and PR #9 landed on `main`, PR #7's substantive changes
were carried onto a branch off that `main`, and the five amendment numbers two
parallel lines had both claimed were resolved in favour of the line that merged.
Register rows **M1.109–M1.113**; migrations taken **none**, next free `018`.

Measured 2026-09-04 on `claude/unit11-reconcile-9c`, branched from `e6ab5f0`
(`main` after #8 and #9).

---

## 0. What was asked, and the shape of the problem

Three lines of work were open and one of them was orphaned:

| PR | Branch | Numbers | State on arrival |
|---|---|---|---|
| #7 | `claude/unit9c-first-spend` | M1.101–M1.105 | open 2 weeks, 7 commits off `main`, **never merged** |
| #8 | `claude/keen-allen-gtsnrs` | M1.101–M1.103 | draft, green, → `main` |
| #9 | `claude/unit10-completion` | M1.104–M1.108 | draft, green, → #8 |

#8 and #9 are consistent with each other and cite each other across nine files.
#7 is consistent with nothing: it branched from `95d3281`, where the register
ended at M1.100, and so did #8. **Both took M1.101 next, and both were right
to** — see §2.

## 1. The merges, and the one thing GitHub did not do

#8 and #9 were **drafts**, which `gh pr merge` refuses; both were marked ready
first. #9 was based on #8's branch, and the brief expected it to retarget
automatically. **It did not**, because the base branch was not deleted on merge
— GitHub retargets a child PR when its base *branch* disappears, not when its
base PR merges. It was retargeted to `main` explicitly (REST `PATCH /pulls/9`;
`gh pr edit` is a silent no-op in this repository). All four checks green on
both. `main` at `e6ab5f0`.

```
env -u ANTHROPIC_API_KEY python -m pytest -q      812 passed, 2 skipped
```

**A discrepancy worth recording rather than fixing.** The first run reported
**4** skipped, not 2. The cause is an untracked `test_live_smoke.py` sitting in
the repository root — a byte-identical copy of `tests/test_live_smoke.py`,
collected twice, contributing its two skips again. It is untracked, so it is
invisible to M1.91's register check (M1.94(b)) and to CI, and visible only to a
local full run. **It was left in place and not deleted**: it belongs to whoever
put it there, and the number it perturbs is a count, not a result.

## 2. M1.113 — the collision, and why M1.91 could not have caught it

The register is **a file on a branch**, and *"next free"* is a property of the
branch you are standing on. Two units branched from the same `main`, read the
same last row, and took the same next number. Neither could see the other.

**M1.91's check does not catch this, and the reason is structural.** It verifies
that every cited number *resolves to a row*. After both lines merge, every
number still resolves — to **the wrong row**. Merged naively the tree would
carry `M1.101` meaning *§7 control 3 was built* in one document and *`max_uses`
is sent so the bound binds* in another, and nothing anywhere would object. That
is worse than the dangling citation M1.91 refuses, because a dangling citation
announces itself and this one reads as an authority. **Git merges two disjoint
table rows without a word.**

**The later line was not renumbered.** #8 and #9 are merged, green, and cite
each other across nine files; moving them would edit merged history to
accommodate a branch that never landed. So #7's rows moved:

| #7 | Now | Subject |
|---|---|---|
| M1.101 | **M1.109** | §7 control 3 built |
| M1.102 | **M1.110** | reconciliation is never refused by control 3 |
| M1.103 | **— not carried** | M1.75's collapse has a measured recurring price |
| M1.104 | **M1.111** | `contact.purge_after` names a command that did not exist |
| M1.105 | **M1.112** | §10.7b's own procedure could not tell *no batch* from *never asked* |

**#7's M1.103 is not carried as a row, and that is a decision.** It resolved to
*"nothing is changed"*: it recorded that M1.75's robots-collapse over-reporting
has a **measured, recurring** price — `smoke2u.de`, a band-B company at 55
points, loses its paid extraction on every crawl of a shop serving byte-identical
robots.txt from apex and `www.`. That is an update to M1.75, not a decision of
its own. Its content is in `docs/unit9c-first-spend-findings.md` §3.1–3.3 and
§9, and its four citations now point at **M1.75**, the row that owns the
finding. Giving it a sixth number would have been the register inflating itself
to preserve a numbering accident.

**The general rule, which is the half that outlives this merge: a branch holding
register numbers is holding a lock on a shared resource, and an unmerged branch
holds it indefinitely.** Coining a number is cheap and the cost lands on whoever
merges second — as ambiguity, not as a conflict. Long-lived branches take their
numbers **at merge**, or they reserve a block and say so in the register on
`main` when they open.

## 3. What was ported

### 3.1 §7 control 3, whole (M1.109, M1.110)

`portal/ledger.py` gains `RUN_CEILING_USD = 5.0`, `RunCeilingExceeded`,
`run_reserved_usd`, `charge_run` and `reconcile_run`, unchanged from #7 in
substance. `extract_p2._charge_run` — the only path by which a batch reservation
reaches `run.est_cost_usd` — delegates to `charge_run` inside
`_commit_reservation`'s `BEGIN IMMEDIATE`, so **a refusal rolls the batch row
back with it: no batch on the books, no money counted, nothing submitted.**
`reconcile._correct_the_reservation` writes through `ledger.reconcile_run`, which
does **not** consult control 3, and the separate function is what makes that
omission a ruling rather than a property of one call site.

### 3.2 The one change of substance: the second expression is gone

While #7 sat open, PR #9 built M6 and wrote **`ai_visibility.PER_RUN_CEILING_USD
= 5.00`** beside a second `RunCeilingExceeded` class. Landing #7's ledger on top
would have left **one control with two constants and two exception types**.

`ai_visibility` now reads `ledger.RUN_CEILING_USD` and re-exports
`ledger.RunCeilingExceeded`. **Two enforcement points are kept, and they are not
redundant:** M6 can price a whole run before the `run` row exists and refuses
there with nothing reserved; a batch's price is known only per call, so M5
refuses at the write. Two *constants* for one policy bound is M1.42's shape
applied to a policy instead of to a corpus, and its failure mode is a ceiling
raised in one place and not the other.

### 3.2b The port's own defect: a default argument is not one expression

#7 wrote the ceiling into the signatures — `ceiling_usd: float =
ledger.RUN_CEILING_USD`, and the same again on three `extract_p2` functions.
**A default argument is evaluated once, at import.** So the constant was copied
into four signatures at import time and `ledger.RUN_CEILING_USD` became a name
the module reads and nothing obeys: editing it, or patching it, changed
nothing for any caller that did not pass the parameter explicitly.

This is not a style point — it is §3.2's finding again, one layer down. Having
just deleted the *second constant*, the port would have shipped **four frozen
copies of the first**. It was found by the CLI test in §3.3, which patched the
constant, expected a refusal and got a successful submission.

Every parameter is now `None` and resolved at the check. `ai_visibility.run`
already did this — `per_run_ceiling_usd: float | None = None` — which is why
M6's ceiling test worked and M5's did not. **One expression means one expression
*at the moment of the check*.**

### 3.3 The CLI wiring #7 never had

#7 predates `extract-p2 --submit`, which M5 built (M1.102, #8's numbering). So
control 3 was enforced at the write and **unwired at the command**: an
over-ceiling run would have escaped `cmd_extract_p2` as a traceback.

`extract-p2 --submit` now prints control 3's bound immediately after control 2's
reading, **before** `reserve_and_submit`, and catches `RunCeilingExceeded` into
`_abort_run` + exit 2. **The bound cannot be *checked* before the call** — the
reservation is priced from `count_tokens` inside `reserve_and_submit`, so the
number does not exist yet — and the announcement is deliberately not dressed up
as a check. Enforcement stays at the single write, which is what makes it
unbypassable by a future second caller.

The `run` row is aborted on refusal. It was inserted before the reservation and
must not be left open: `company_profile` declines to serve a stage from an
unfinished run (007), so an open run that never spent anything would make a
later, successful run's signals harder to read, not safer (M1.39).

### 3.4 Superseded, and skipped

**#7's hardened §10.7b snippet.** M1.112's finding stands — run as written the
procedure printed **zero bytes on `stdout` and exited 1**, a `TypeError` during
*client construction*, and its stated reading was *"if it prints nothing, no
batch exists"*. But the instrument #7 proposed was built independently while it
sat open: **`portal llm-batches` (M1.104)** ends every path with a printed
statement — exit 2 and *"stays OPEN, not zero"* without a key, *"CLOSED with the
answer ZERO, as of \<date\>"* on an empty listing, every id and *"RESUBMITTING
WOULD DOUBLE THE COST"* otherwise. Two lines reaching the same instrument from
the same defect is evidence about the defect.

**What #9 did not do is the prose**, and that is the half that was ported:
§10.7b still carried the original pasted snippet — the one that manufactures the
unmarked zero — as its closing procedure. **A procedure a reader copies is the
procedure that runs.** §10.7b now names the command first, keeps the
verdict-line rule for the snippet, and keeps *a view that did not render is not
a view showing nothing* for the Console route.

**Nothing else was skipped.** #7 touched six files; `docs/lead-portal-spec-v0.3.md`,
`portal/ledger.py`, `portal/extract_p2.py`, `portal/reconcile.py`,
`tests/test_cost_ledger.py` and `docs/unit9c-first-spend-findings.md` are all
carried.

## 4. The tests

`tests/test_cost_ledger.py` gains #7's seven control-3 tests plus one this
reconciliation required:

- a run refused at the ceiling, with the accumulator **unmoved** after refusal;
- the check is on the **total**, not the increment — a single $9.99 call against
  a $5.00 ceiling is refused outright, because the guard's first call must not
  be free;
- control 2 is **not replaced** — ten runs of $4.50 are each legal under control
  3 and together trip control 2 at $45.00;
- `charge_run` without a clearance is a `TypeError` — composition, not
  replacement;
- a non-existent run is **refused**, not treated as having spent nothing;
- reconciliation to $7.90 against a $5.00 ceiling **lands** (M1.110), and so
  does a downward correction;
- an oversized reservation through `_commit_reservation` leaves **zero**
  `llm_batch` rows and **zero** charged (M1.72's transaction);
- **new:** `ai_visibility` has no `PER_RUN_CEILING_USD`, its `RunCeilingExceeded`
  **is** `ledger.RunCeilingExceeded`, and that class is **not** a
  `CeilingExceeded` — the one-expression property, pinned so a later edit
  restoring the second constant fails;
- **new:** the ceiling is read **at call time**, so patching
  `ledger.RUN_CEILING_USD` refuses a charge the unpatched constant allows
  (§3.2b — this is the test that caught the port's own defect).

`tests/test_extract_p2_cli.py` gains two: control 3 refusing through the command
with **no batch row, nothing charged, the run aborted and exit 2**; and the
bound being printed **before** the reservation it bounds.

`tests/test_ai_visibility.py`'s ceiling test patches `ledger.RUN_CEILING_USD`
rather than the deleted constant, and still asserts the refusal happens with
**no `run` row written and no provider call made**.

## 5. Verification

```
portal init (data/)                      applied 016, 017 — schema version 017,
                                         12 tables, 1 view; the corpus was at 015
ruff check . && ruff format --check .    clean, 78 files
env -u ANTHROPIC_API_KEY python -m pytest -q
                                         823 passed, 2 skipped, 139 subtests
                                         (from 812 — 11 new: 8 ledger, 1 for the
                                         call-time read, 2 for the CLI wiring)
tests/test_amendment_register.py         green WITH docs staged (M1.94(b): an
                                         untracked file is invisible to it)
```

The suite is run with `--ignore=test_live_smoke.py` to exclude the untracked
root-level duplicate described in §1; without it the count is the same 823 with
**4** skipped rather than 2.

The register check was run **after `git add`**, not before. M1.94(b) is the
reason: the scan enumerates with `git ls-files`, so a new findings document that
has not been staged is a document the check cannot see, and four consecutive
local passes once meant nothing.

## 6. CI — recorded after it was observed

*Written after the run, never before it (M1.94).* Run `33822151948` on
`claude/unit11-reconcile-9c` @ `47d237e`, PR #10. **All five checks pass.**

```
assert-no-api-key                 no key, no live-smoke opt-in
pytest (py3.11)   1m31s   823 passed, 2 skipped, 1 warning, 139 subtests
pytest (py3.12)   1m34s   823 passed, 2 skipped
ruff                6s    pass
audit-politeness (fixture corpus)   34s   pass
CodeRabbit                        skipped (manual review, OSS repo)
```

**Two things worth naming.** CI reports **2** skipped where a local full run
reports 4 — CI enumerates a clean checkout, so the untracked duplicate in §1
does not exist there. And `assert-no-api-key` passed, which it must: it fails
the build if `ANTHROPIC_API_KEY` is present (M1.65). It is set in the
*development* container this unit ran in, and nothing here reads it — but that
step is the reason the local suite is run under `env -u` rather than trusting
the environment.

## 7. Open items register — the rows this unit touched

| Item | Was | Now | Why |
|---|---|---|---|
| PR #7 | Open, orphaned, 2 weeks | **CLOSED in favour of `claude/unit11-reconcile-9c`** | M1.113 |
| PR #8, PR #9 | Draft | **MERGED to `main`** (`e6ab5f0`) | §1 |
| §7 control 3 | Specified, not implemented on `main` | **BUILT and merged** — `RUN_CEILING_USD`, `charge_run`, 8 tests | M1.109 (§3.1) |
| The per-run ceiling's expression | Two constants, two exception classes | **ONE of each**, two enforcement points | M1.109 (§3.2) |
| `extract-p2 --submit` vs. control 3 | Enforced at the write, unwired at the CLI | **WIRED** — bound printed, refusal is exit 2 | M1.109 (§3.3) |
| §10.7b's closing procedure | Command hardened (M1.104); **prose still the original snippet** | **Prose amended** — command first, verdict line required | M1.112 (§3.4) |
| The batch question (§10.7b) | OPEN, listing never run | **Still OPEN, still not zero** — `portal llm-batches` has never been run against a key | unchanged |
| §8 erasure path | Recorded by #7, unmerged | **RECORDED on `main`** — M7 built it since; the row stands as the audit | M1.111 |
| M1.75's collapse cost | #7's M1.103, unmerged | **Recorded in the findings, not as a row** | M1.113 |

**Next free migration: `018`.** **Next free amendment: `M1.<114>`** (angle
brackets per M1.94, so this line is not a citation).

## 8. What this unit did not do, stated so it is not inferred

- **Nothing was run against a network, and no `--submit` of any kind was run.**
  The environment holds an `ANTHROPIC_API_KEY`; no path added here reads it, the
  full suite was run under `env -u ANTHROPIC_API_KEY`, and its value appears
  nowhere in this repository or in any output.
- **`data/` was not modified except by `portal init`**, which applies migration
  017 to the existing corpus and is idempotent. No crawl, no re-fetch.
- **The batch question is not closed.** `portal llm-batches` exists on `main`
  and has still never been run with a credential. It precedes any `--submit`.
- **#7's git history is not preserved.** The port is a fresh commit against
  `main`, not a rebase: #7's seven commits describe a tree that no longer exists.
  #7 stays visible on GitHub as a closed PR, which is where its history lives.
