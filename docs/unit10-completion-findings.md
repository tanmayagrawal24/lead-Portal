# Unit 10 — completing the build without the first spend

> **No crawl, no API call, no key, no spend.** Written 2026-09-03 in a container
> holding no credential of any kind, branched from `518aee4`
> (`claude/keen-allen-gtsnrs`, M5 complete). Every paid path built here was
> exercised against a fake provider under `env -u ANTHROPIC_API_KEY`.
> Register rows **M1.104–M1.108**; migration **017** taken; **018** next free.

## 0. What this unit was asked, and what it found

The instruction was *"complete the entire build, apart from the one where I
give my API key"*. Read against `docs/implementation-brief.md`'s build order,
that is a list, and the list was checked against the tree rather than against
the handoff:

| Milestone | Handoff said | Tree said (`518aee4`) |
|---|---|---|
| M0–M5 | complete | complete; 760 passed |
| §5.7 `score --phase 2` | — | `--phase 2` accepted; the *"warns loudly if unreconciled batches exist"* sentence read by nothing |
| §10.7b closing | *"run `messages.batches.list()` with a real key"* | a Python snippet in the spec; no command |
| M6 | *unblocked, not started* (M1.54) | not started; `_never_settled` still in `ruleset.py` |
| M7 | — | none of `purge`, `forget`, `brief`, `outreach`, `exclude` existed; §9's three writing actions were prose |
| M8 | *deliberately last* | not started; `run.places_calls` had no writer; `fetch` required `--seed` |

Everything in the right-hand column is now built. The two things that remain
are the two the instruction excluded, and they are in order: §10.7b's listing,
then 9c.

## 1. M1.104 — a sentence nothing read, and a procedure nobody could run

`score.unreconciled_batches` answers on `reconciled_at IS NULL` and on
nothing else. The alternative — a status predicate — was rejected because §7
control 12(b) already makes `reconciled_at` the one column that means *the
measured actual has been written*, and a `reserved` batch with no provider id
(migration 014) is exactly the case a status list would get wrong. The
warning goes to stderr **before** the score is written, names the companies,
and does not refuse: the score is the best reading available and `reconcile`
supersedes it under the submitting run's own id (B4). Phase 1 never warns.

`portal llm-batches` is the §10.7b procedure as a command. It is classified
**free** in `llm_anthropic.FREE_SURFACES`, so `assert_ledger_guarded` accepts
it at import; it needs a key and makes no paid call. Three exits, and the
test pins that they are distinct: no key → 2, *"stays OPEN, not zero"*; empty
listing → 0, *"CLOSED at zero, as of \<date\>"*; non-empty → 0, every id
printed and *"RESUBMITTING WOULD DOUBLE THE COST"*.

## 2. M1.105 — M6, and the one number §7 cannot measure

Three decisions, each written in `ai_visibility.py`'s docstring where a later
reader would otherwise "fix" it:

**(a) Queries.** First product category, fixed templates, withheld where
there is none. The dry run distinguishes *the homepage extraction never ran*
from *it ran and returned no category* — different next steps.

**(b) The reservation.** `count_tokens` on the prompt (measured, free) plus
`SEARCH_CONTEXT_TOKENS = 20,000` per query. This is the only unmeasured number
in any §7 reservation and it is unmeasurable in principle: the search results
are not known until the search runs. It is the top of §5.5c's own range, it
is dated, and it errs toward over-reservation. The measured `usage` replaces
it the moment the response arrives. `max_uses = 1` goes to the provider so
the bound binds (M1.103's rule).

**(c) A dry balance.** The run is *finished*, not aborted. `extract-p2` aborts
because nothing was written; here each company's six keys are committed
before the next company's first call, and an aborted run is one
`company_profile` refuses to serve (007). The test pins that company A's
signals are served and company B's are absent after the balance dies between
them.

Migration 017 projects the four `ai.*` basis fields and
`offer.product_categories`. The latter reverses one line of A2 §4's
*"deliberately not added"* — a ruling about readers, and M6 is the reader.
`impressum.legal_name` is **not** added; A2 §4 routes it to
`company.legal_name`, and the brand match reads it there.

`opp.ai_invisible` fires at +15 off the written signals — pinned end to end
through `score.run(phase=2)` in `test_ai_visibility.py`.

## 3. M1.106 — M7, and `forget` as a measurement

The brief says `forget --domain X` *"leaves zero rows anywhere"*. That is
checked, not assumed: `lifecycle.residue` reads every table with a
`company_id` column from `sqlite_master`, adds `score_component` through its
parent, and the command adds the directory on disk. It asserts
`PRAGMA foreign_keys = 1` before deleting, because CASCADE is silent without
it. The test seeds a company across `artifact`, `signal`, `contact`,
`review_flag`, `score`, `score_component` and `llm_batch_request`, forgets
it, and asserts residue `{}` — while the sibling company, the `run` ledger
and the `llm_batch` row are untouched. **Two things kept on purpose:** `run`
rows (a deletion must never release spend, control 12c) and `llm_batch`
(names no company).

`brief.render` fails in three named ways rather than rendering hollow:
`MissingBasis` (an `ai.*` result without all three basis fields — §8's rule),
`ContactBlocked` (A7's third axis), `NotScored`. The KI-Sichtbarkeit section
renders in the format §9 quotes, and the result line is the count.

## 4. M1.107 — M8, and the mask on the wire

The field mask is asserted on the `X-Goog-FieldMask` header httpx actually
sends, with `rating` asserted absent. The key rides in a header, never in a
URL; an HTTP error re-raises with the class name only (the test passes a key
named `sekrit` and asserts it is not in the exception). `run.places_calls`
counts issued requests. §7 control 1 lives in Cloud Console and no code can
confirm it; the dry run prints the sentence.

Building M8 exposed that `cmd_fetch` required `--seed` and built its targets
from the file — a discovered company would never have been crawled. `--seed`
is now optional; without it, every company row is a target, which is the set
`extract-p1` and `score` already read.

## 4b. M1.108 — the audit, before handover

Read back the day it was written, looking for the register's own defect
classes. Found: a mid-run provider failure that finished the run silently
and escaped as a traceback (M1.39's shape); a `--limit` that the SDK's
auto-paginating page object ignored (a bound that did not bind); one missing
`__all__` entry. Each fixed with the test that would have caught it. Not
fixed, recorded: M6/M8 write non-URL `evidence_url`s, which render as dead
links on the §9 page.

## 5. Verification

```
env -u ANTHROPIC_API_KEY python -m pytest -q
  812 passed, 2 skipped, 139 subtests    (from 760)
ruff check . && ruff format --check .    clean
tests/test_amendment_register.py         green (M1.91 refused the tree twice
                                          mid-unit — once for M1.104/105,
                                          once for M1.106 — and was obeyed)
audit-politeness on fixture corpus       §5.2: HELD; breached corpus refused
```

New test files: `test_score_phase2.py` (7), `test_llm_batches_cli.py` (5),
`test_ai_visibility.py` (16), `test_m7_lifecycle.py` (13), `test_discover.py`
(9). New modules: `ai_visibility.py`, `lifecycle.py`, `brief.py`,
`discover.py`. New commands: `llm-batches`, `ai-check`, `purge`, `forget`,
`exclude`, `outreach`, `brief`, `discover`.

## 6. What this unit did not do, stated so it is not inferred

- **Nothing was run against a network.** `ask_with_search`'s request shape
  (tool block, `max_uses`) and `HttpPlacesClient`'s body follow the vendors'
  documentation as of this container's knowledge; the first `--submit` of
  either is the first time the wire shape is exercised, and a 400 there is a
  request-shape defect, not a spend.
- **`extract-p2 --purpose homepage` has never run**, so
  `offer.product_categories` is empty on every corpus; `ai-check`'s dry run
  will withhold everyone until it has. That is the order §5.5 gives.
- **§10.7b is not closed.** The command exists; running it is the operator's.

## 7. Open items register — derived, not remembered

| Item | Decided by | State |
|---|---|---|
| Was a batch ever submitted? | `portal llm-batches` with a real key | **CLOSED 2026-09-04 — the answer is ZERO** (M1.114). The command this unit built was run against a real key, listed nothing, and printed its verdict line at exit 0 |
| 9c — first real spend | operator, `extract-p2 --submit` | **Not started; no longer gated** — the row above closed at zero, so there is no committed spend to account for and nothing a first `--submit` would double |
| PR #8 (`claude/keen-allen-gtsnrs` → `main`) | GitHub | **Draft**; this unit branches from its head |
| M6 first run | `ai-check --submit` | never run; needs `homepage` extraction reconciled first |
| M8 first run | `discover --submit` | never run; needs §7 control 1 in Cloud Console first |
| §10.2's cost lever | §10.2 | open; base rates unreproducible (§10.7a) |
| §6.5 band calibration | §10.3 | blocked on a corpus gathered after B7 and the root-slug fix |
| `interrupted-M5-remnant` stash | `git stash list` on the 2026-08-15 codespace | unread by this unit; nothing here was built from it |
| Next free migration | `ls portal/migrations/` | **019** — *was `018` when written; the reservation-release states took 018 (M1.117)* |
| Next free amendment | the register | **`M1.<119>`** — angle brackets so it is not a citation; M1.91 refused this document on its first run for writing the bare number, exactly as it refused 9c-prep (its §11). *Was `M1.<109>` when written; Unit 11 took M1.109–M1.113 reconciling PR #7 (M1.113), and the §10.7b closing took M1.114* |
