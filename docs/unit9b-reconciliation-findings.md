# Unit 9b-close — the register the engineering skipped, and a defect in the register itself

Measured 2026-08-21 against `6f5bc8d` on `claude/unit9b-reconciliation`. **No
crawl, no API call, no spend, and no `ANTHROPIC_API_KEY` at any point** — the
variable was confirmed unset before the first command and the only network
traffic was `git`, `gh`, and loopback fixture servers.

M5 phase 9b, closing unit. Companion to **M1.86–M1.93** in
`docs/lead-portal-spec-v0.3.md`.

**What this unit is.** 9b built `portal/reconcile.py`, migrations `014` and
`015`, and their tests across three commits, and amended **no register at all**.
This unit writes the record, finds that the omission is itself the unit's
largest finding, and merges. It builds no reconciliation behaviour.

---

## 0. Baseline, and the three divergences verified before anything moved

The brief named three divergences and said to verify each rather than take them.
All three are real. **The first is larger than the brief stated.**

| check | result |
|---|---|
| `python -m pytest -q` | 696 passed, 2 skipped, 139 subtests — matches the brief |
| `ruff check .` / `ruff format --check .` | clean / 62 files already formatted |
| `ANTHROPIC_API_KEY` | unset |
| `data/` | does not exist in this workspace; nothing to move aside |

**Divergence 1 — undeclared amendment numbers. The brief said two; there are
five.** The brief's own one-line check (`git grep -c "M1\.8[67]"`) can only find
what it already names, so it was replaced with a derivation that names nothing:

```
declared:  grep -oP '^\| M1\.\d+' docs/lead-portal-spec-v0.3.md   ->  85 numbers
cited:     grep -rhoP 'M1\.\d+'   docs/ portal/ tests/            ->  90 numbers
difference:                                          M1.86 M1.87 M1.88 M1.89 M1.90
```

M1.88 (`reconcile.py:383`), M1.89 (`014_batch_reservation_states.sql:53`) and
M1.90 (cited in the working tree's §7 draft) were **not in the brief's list**.
The brief's closing instruction — *"continue numbering at M1.88 after that,
verify the highest in use first"* — was therefore an instruction to collide with
two live numbers, and the verification it asked for is what caught it. New
findings in this unit start at **M1.91**.

**Divergence 2 — spec line 113.** Confirmed verbatim: *"Remaining findings
(A1–A4, B1, B3.2–B3.3, B5–B7, C1–C4) are still open and are not required by M0
or M1."* B3.2 is implemented at `reconcile.py:520` (`actual_cost_usd`) with the
correction at `reconcile.py:563` (`_correct_the_reservation`); B3.3 is what
M1.72 and B3.2 amount to once the rule is stated. See §4 — the fix is not the
one the brief asked for, and the reason is Unit 8's.

**Divergence 3 — `docs/unit9b-reconciliation-findings.md`.** Confirmed absent.
`ls docs/ | grep -i finding` returned twelve documents, none of them 9b's. This
is that document.

**A fourth divergence the brief did not name, found while verifying the first.**
The working tree was **not clean**: `docs/lead-portal-spec-v0.3.md` carried an
uncommitted addition — a drafted §7 control 12 citing `B3.3, M1.90` — left by
the session that wrote 9b's code. It is 9b's own work, unpushed and unrecorded.
Its four clauses were checked against `_correct_the_reservation` line by line
before any of it was kept (§1, M1.90), and all four hold. Had this unit run
`git add -A` without looking, it would have committed a spec change it had not
read as though it were its own.

---

## 1. The register entries, written from what the code does

Each row was sourced from the code's own comments — migration headers,
docstrings, module preambles — rather than from a reconstruction of intent. Full
text is in the spec's `### Amendments from Unit 9b` table; what follows is where
each came from and what checking it produced.

| # | source of truth | verified against |
|---|---|---|
| **M1.86** | `015_batch_requests.sql:13–36`, `llm.py:717–728`, `reconcile.py:35–38` | `resolve_batch_status`'s `expected` is keyword-only and **required** (`llm.py:708`); `_disposition` returns `None` for an absent request and the batch stays open (`reconcile.py:620`) |
| **M1.87** | `015_batch_requests.sql:38–60`, `reconcile.py:14–22` | `TEXT_UNREPRODUCIBLE` is defined outside `llm.RequestOutcome` (`reconcile.py:66`); `reconcile.py:952` writes no value on mismatch |
| **M1.88** | `reconcile.py:381–390` (`_impressum_signals` docstring) | every text field of `ImpressumExtract` passes through `_SignalWriter`, which refuses an undeclared key |
| **M1.89** | `014_batch_reservation_states.sql:40–75` | the CHECK at `014:121`; `OPEN_STATUSES` excludes `reserved` (`reconcile.py:71`) |
| **M1.90** | `reconcile.py:39–44` and `563–588`, plus the uncommitted §7 draft | all four clauses, below |

**No cited number turned out to describe something the code does not do.** That
was the thing worth checking, and it came back clean — which is why M1.91 is a
finding about the register rather than about the engineering.

**M1.90's four clauses, checked individually.** The draft was not taken on
trust:

* **(a) the correction goes to the submitting run.** `_correct_the_reservation`
  reads and updates `run WHERE id = batch.run_id` (`reconcile.py:591`, `:612`) —
  the submitting run, never the reconciling one. It raises rather than falls
  back if that run is missing.
* **(b) applied once, at a terminal state only.** `reconcile.py:831` returns
  early on `SUBMITTED` before any correction; the delta is `actual -
  batch.est_cost_usd` against a **running** actual, so a second poll of the same
  batch moves the ledger by zero. There is a floor at `reconcile.py:604`: a
  correction that would take the run's reservation below zero raises, because a
  ledger that can go negative is one that can be talked below a real number.
* **(c) nothing else releases a reservation.** Derived, not read:
  `grep -rn "UPDATE run" portal/*.py` returns nine writes, of which exactly
  **two** touch `est_cost_usd` — `extract_p2.py:503` (the reservation) and
  `reconcile.py:612` (the correction). No status transition anywhere releases
  one.
* **(d) the window does not move with it.** `ledger.py:52` sums
  `est_cost_usd FROM run` keyed on `started_at`, and nothing in the correction
  path touches `started_at`.

The draft's closing note — that the actual is never *added* to the ceiling query
— is also correct: `ledger.py:52` is the only `SUM(` in the file and it names
one column.

---

## 2. M1.91 — the pattern, measured

**The finding is not that five rows were missing. It is that the rate is
rising, in the instrument the project uses to measure itself.**

Unit 9a coined **M1.85**, cited it three times in its own findings document and
once in a test docstring, and did not amend the register. Unit 9b then coined
**five** numbers and cited them **seventeen times across seven files**, and the
files are the difference:

| file | citations |
|---|---|
| `portal/reconcile.py` | 9 |
| `portal/extract_p2.py` | 2 |
| `portal/migrations/015_batch_requests.sql` | 2 |
| `portal/cli.py` | 1 |
| `portal/llm.py` | 1 |
| `portal/llm_anthropic.py` | 1 |
| `portal/migrations/014_batch_reservation_states.sql` | 1 |
| `docs/lead-portal-spec-v0.3.md` (the uncommitted §7 draft) | 1 |

9a's citations were in a **report**. 9b's are in **production code and schema**,
where they read as authority. Three in six files went in as load-bearing
justification for a schema decision.

**What a reader of `reconcile.py` on 2026-08-20 could and could not do.** They
could read `reconcile.py:625` — *"M1.86's whole finding, and the reason this
takes the stored request rather than the count"* — and understand the local
argument, because the comment restates it. They could **not** look up M1.86:
`grep "M1\.86" docs/lead-portal-spec-v0.3.md` returned nothing but the
uncommitted draft's passing mention. Nine such pointers in one file. The comment
convention M1.58 defended — *historical* comments carrying their measurement —
depends entirely on the measurement being retrievable, and for five findings it
was not.

**Why this is a numbered finding rather than an erratum.** It is **M1.71
inverted**: M1.71 was a spec line naming an `assert_prices()` that no code
implemented, and this is implemented, tested, cited code naming rows the spec
does not have. And it is **M1.73's class one level out** — M1.73 was a number
miscounted *inside* a report; this is findings that never reached the register
at all. A defect in the measuring apparatus is worth more than the thing it was
measuring.

### 2.1 The mechanism, built rather than proposed

`tests/test_amendment_register.py`. It enumerates the tree with `git ls-files`
— a directory walk would let `.mypy_cache` and untracked scratch influence a
check about what the **repository** says — greps every tracked file for
`M1\.\d+`, parses the declared set from row starts (`^\| M1\.\d+ \|`) in the
spec, and fails naming every undeclared number **and the files that cite it**.

It is a test rather than a convention because every comparable rule here is
enforced by something that fails: `ruleset.assert_declared`,
`assert_ledger_guarded`, M1.19's exit code. It runs in the existing `test` job
on both 3.11 and 3.12, adds no dependency, and takes 0.14 s.

It carries a second test guarding itself — if the table's row format ever
changes, the first test would pass by finding nothing declared and nothing
cited, which is the failure mode a parser-based check has. The guard asserts
both sets are non-trivially populated.

**Two things it deliberately does not do**, stated because the omissions are
choices: it does not require a declared row to be **cited** anywhere (a finding
may be recorded and never referenced again), and it does not check the
numbering's **contiguity** — a different rule, never violated, and not this one.

**A note on what this cannot catch.** It enforces that a cited number resolves
to a row. It cannot enforce that the row *says what the citation means*. That is
§1's manual check, and this unit did it by hand for all five.

---

## 3. M1.92 — §7's controls have rendered under the wrong numbers since Unit 2

Found while placing control 12, not looked for. The §7 list ran `1, 2, 3, 4,
10, 11, 5, 6, 7, 8, 9` in the source. Markdown numbers an ordered list **by
position**, so the rendered document disagreed with the written one:

```
written:  1  2  3  4  10  11  12   5   6   7   8   9
renders:  1  2  3  4   5   6   7   8   9  10  11  12
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^  8 of 12 mismatched
```

`git log -S` puts the misordering in `601b955`, **Unit 2** — it predates 9b,
and 9b's draft control 12 inherited the position and widened it by one item.

**Measured, across `docs/ portal/ tests/`:**

| citation | count | resolves to |
|---|---|---|
| `control 2` | 74 | correct |
| `control 3` | 11 | correct |
| `control 4` | 55 | correct |
| `control 6` | 2 | wrong paragraph |
| `control 8` | 7 | wrong paragraph |
| `control 9` | 4 | wrong paragraph |
| `control 10` | 9 | wrong paragraph |
| `control 11` | 19 | wrong paragraph |
| `control 12` | 9 | wrong paragraph |

**50 citations** resolve to the wrong paragraph for anyone reading the rendered
spec. A reader following `llm_anthropic.py` to *"§7 control 11"* — the prepaid
balance rule, the one that keeps `billing_error` out of `failed` — lands on
*"Content-hash short-circuit"*.

**Fixed, not just recorded.** The three misplaced items were moved into
position; no control text changed and nothing was renumbered, so all 50
citations became correct without one of them being edited. It qualifies as
small and local under the brief's rule: a pure reordering of whole list items,
verified by re-parsing the list and comparing written labels to positional
index. Leaving it would have meant shipping control 12 into a list that
mis-renders it.

This is **M1.84's class at the level of the document** — a passage that reads as
one thing and is another — and like M1.84 it is cosmetic in neither a renderer
nor a grep.

---

## 4. M1.93 — line 113, and why the brief's fix was declined

The brief asked to *"move B3.2 and B3.3 out of still open"*. That was not done,
and the reason is that **Unit 8 already ruled on it and the ruling is better**.

Unit 8 derived the register against the artefacts and found four members of line
113's list already closed underneath it — **A1** (closed by M1.21→M1.22 and
ratified 2026-08-16 *in the same document that still listed it as open*),
**B6**, **B7**, and **C3** (premise dissolved). It recorded the fix and deferred
it as out of scope:

> *"Line 113 should be replaced by a pointer to §7's method rather than
> corrected in place, since correcting the membership leaves the mechanism that
> rots it. Not done in this unit: it is a spec-structure change and 8c was
> already at its stop condition's edge."*

9b closes B3.2 and B3.3, which takes the sentence to **six of twelve members
wrong**. Editing two names out of it would have made this the eighth unit to
copy a frozen list forward — M1.73's scalar exactly. The membership list is
therefore **deleted** and replaced by a pointer to the per-unit
`## Open items register — derived, not remembered`, which is the artefact that
actually decides each row.

Unit 8's recommendation is implemented as written and credited rather than
re-discovered. **This is a judgement call against the brief's literal
instruction**, and it is recorded here as one.

---

## 5. Restart survival — the brief was wrong, and the test was already there

The brief said: *"`reconcile.py`'s docstring claims restart survival; no test
name evidences it. Find the test or write it."*

**The test exists and predates this unit.** `tests/test_reconcile.py:238`,
`class RestartSurvival`, five tests. Its `_submit_then_discard` helper does
exactly what the brief demanded and says so:

```python
self.conn.commit()
# Everything the submitting process knew, gone. What survives is on disk.
self.conn.close()
del provider
```

`_fresh()` then opens a **new** `db.connect(self.db_path)`. The `Reservation`,
the `Prepared` list, the request objects and the connection are all discarded.
The class docstring anticipates the brief's objection verbatim — *"a test that
keeps the batch object in a variable proves nothing"*.

Run individually and observed this session:

```
RestartSurvival::test_reconcile_finds_its_work_with_no_state_carried_from_submit PASSED
RestartSurvival::test_a_full_reconcile_runs_off_the_database_alone             PASSED
RestartSurvival::test_the_sent_text_is_reproduced_and_the_digest_proves_it     PASSED
RestartSurvival::test_an_unreproducible_page_writes_no_value_and_closes_the_request PASSED
RestartSurvival::test_running_it_twice_writes_nothing_the_second_time          PASSED
```

Nothing was written. The brief's premise — *"no test name evidences it"* — is
false; the class name is `RestartSurvival` and the first test's name is
`test_reconcile_finds_its_work_with_no_state_carried_from_submit`. What the
brief could not see from a diff, it asserted from one.

---

## 6. `expired` — the code and the schema agree, and the word does not

The brief asked whether `reconcile.py:29` (*"`expired` is a **per-request**
result type"*) contradicts migration 001's `llm_batch` CHECK, which lists
`'expired'` as a batch **status**. **Settled: they agree, and there is no
numbered finding.** The word names two members of two different enums:

| | `llm.RequestOutcome.EXPIRED` | `llm.BatchStatus.EXPIRED` |
|---|---|---|
| defined | `llm.py:409` | `llm.py:444` |
| what it describes | one request the provider did not process in 24 h | this tool's conclusion about the batch that carried it |
| origin | the provider returns it | **derived** by `resolve_batch_status` |
| stored in | `llm_batch_request.outcome` | `llm_batch.status` |
| schema | migration `015` | migration `001` CHECK, widened by `014` |

The provider has **no** `expired` batch state — that is precisely M1.51's fact,
and why `resolve_batch_status` exists. So `reconcile.py:29` is correct about the
provider and `TERMINAL_STATUSES` twelve lines below is correct about this tool,
and a reader meeting both in one docstring has no way to know that.

**Fixed as documentation, which is the whole defect.** One clause added to fact
2 in `reconcile.py`'s module docstring naming both enums and the derivation
between them. No behaviour changed; no reconcile feature added.

---

## 7. Negative control

**One control was run in this session, on the one thing this session built.**
`tests/test_amendment_register.py` is a gate, and an ungated gate is a green
light wired to nothing (M1.62).

An undeclared citation was appended to `portal/reconcile.py`:

```python
# negative control: a number with no row (M1.994).
```

Observed:

```
AssertionError: 1 amendment number(s) are cited in the tree and have no row in
the amendment table of docs/lead-portal-spec-v0.3.md:
  M1.994 — cited in portal/reconcile.py
1 failed, 1 passed
```

The file was restored from a byte copy taken before the edit; `git diff --stat
portal/reconcile.py` returned empty, and the check went back to 2 passed. The
gate fails on the defect it was built for, and it **names the file**, which is
what makes the failure actionable rather than merely red.

**No other negative control was run in this unit, and none is claimed** — see
§8.

---

## 8. Provenance — what this session measured, and what it did not

**This section exists because the brief was right to demand it**, and because
the temptation it names is real: this unit wrote the record for code it did not
write, and every measurement in that code's comments is available to be repeated
as though it had been taken here.

**Measured in this session, first-hand:**

* the declared/cited derivation — 85 vs 90, difference of exactly five
* the 17 citations across 7 files, per-file
* the §7 written-vs-rendered parse, 8 of 12, and the 50 affected citations
* the two writers of `run.est_cost_usd`
* the baseline suite, and every suite run after each change
* the negative control in §7, observed failing and observed restored
* the five `RestartSurvival` tests, run individually
* both `audit-politeness` corpora and the `extract-p2` exit code
* the M1.73 counter grep, re-run against this document after it existed
* `M1.90`'s four clauses, read against `_correct_the_reservation`

**NOT measured in this session — reconstructed from the code's own comments and
labelled as such:**

* **M1.86's ten-sent/eight-returned measurement.** `015_batch_requests.sql:29`
  records `resolve_batch_status(8 succeeded) -> RECONCILED` and
  `resubmittable(8 succeeded) -> ()` on the pre-fix version. That version no
  longer exists in the tree — `expected` is required, so the old call cannot be
  made — and **this session did not reproduce it**. The register row states it
  as the measurement the fixing session took, which is what it is.
* **M1.72's between-the-writes control.** Commit `e08518f` claims a control that
  fails *between* the two reservation writes. This session ran
  `test_a_failure_between_the_two_writes_leaves_neither` and observed it
  **PASSED**; it did **not** re-run it against the pre-fix code, so what is
  evidenced here is that the guard is present and green, not that it was ever
  observed red. **A reconstructed negative control is not a negative control**,
  and this one is not claimed as one.
* **M1.85's `test_confidence_zero_is_red` reproduction.** 9a/9b's, not this
  unit's. Quoted in the spec row that already existed.
* **M1.88's and M1.89's reasoning.** Both rows are transcriptions of arguments
  made in `reconcile.py:381` and `014:40–75`. The arguments were checked for
  internal consistency and against the code they describe; the **weighing of
  the alternatives** in M1.89 was done by the session that wrote migration 014,
  not here.

**Where the line falls.** A comment's measurement is evidence about what a past
session observed. Repeating it in the register is correct — that is what the
register is for — and presenting it as this session's observation would not be.
Every row above is attributed.

---

## 9. Still open

* **The untransmitted audit section: LLM-generated / hallucination signals.**
  Still outstanding. 9a reconstructed what such a section would have to cover
  and was explicit that *"a reconstruction is not the artefact"*; the original
  was never transmitted and the branch that carried it is byte-identical to
  `main`. Nothing in this unit recovers it. **This document is the seventh to
  pass it over** — see below; the number was re-derived, not incremented.
* **B1** — brief export, fail-loudly vs omit. M7 not started.
* **C1** — the blog ladder scores a healthy new blog. Unchanged.
* **C2** — one failed search costs `opp.ai_invisible`. Untestable until M6.
* **9c — first real spend.** Not started, needs written authorisation.
  `extract-p2` without `--dry-run` exits **2** (observed).
* **M1.91's residual.** The check enforces that a citation resolves to a row. It
  cannot enforce that the row means what the citation means, and it cannot see a
  finding that was never numbered at all.
* **Inference error in a verified boolean** (M1.49). Unguarded, bounded
  arithmetically, unchanged by this unit.

### The counter, re-derived

`## Still open` above was written first. The grep was then re-run against a tree
containing this document:

```
$ grep -rlniE "LLM-generated ?/ ?hallucination" docs/unit*-findings.md
docs/unit4-robots-tristate-findings.md
docs/unit5-portability-and-ci-findings.md
docs/unit6-address-guard-findings.md
docs/unit7-cost-ceiling-findings.md
docs/unit8-m5-prerequisites-findings.md
docs/unit9a-phase2-scaffolding-findings.md
docs/unit9b-reconciliation-findings.md

```

**Seven.** Units 4, 5, 6, 7, 8, 9a and 9b. Derived from the grep, not from
9a's six-plus-one — which is M1.73's entire instruction, and which would have
given the same answer this time and is still not how it was obtained.

---

## 10. Where the instructions were wrong

**The brief named two undeclared numbers; there were five.** M1.88, M1.89 and
M1.90 were live in the tree and unnamed by the brief. Its closing instruction —
*"continue numbering at M1.88"* — would have collided with two of them. The
instruction to *"verify the highest in use first"* is what caught it, and it was
right to be there.

**The brief's own check could not have found them.** `git grep -c "M1\.8[67]"`
searches for the two numbers it already knows. The derivation that found five
(`cited − declared`) names nothing in advance, which is the same distinction the
brief draws elsewhere between a rule that asks and a check that fails.

**Restart survival was already tested.** *"No test name evidences it"* is false;
`class RestartSurvival` with five tests has been in `tests/test_reconcile.py`
since commit `e08518f`. The brief asked to *"find the test or write it"* and the
correct answer was to find it, but the assertion preceding the instruction was
wrong and would have justified writing a duplicate.

**The `expired` question presumed a conflict that is not there.** Two enums, one
word. The question was worth asking — the collision is genuinely confusing in
one docstring — but the answer is documentation, not a numbered finding, and the
brief pre-committed it to being *"a numbered finding"* if the two disagreed.

**The brief's line-113 fix was declined in favour of Unit 8's.** §4. Correcting
the membership is the thing seven units have now declined to do.

**The brief did not mention the dirty working tree.** An uncommitted §7 draft
was sitting in `docs/lead-portal-spec-v0.3.md`. The brief's *"check `git status`
before any `git add -A`"* covered the consequence and not the cause; the draft
had to be read and verified clause by clause before it could be kept.

**"Six-plus-one" was pre-empted, correctly.** The brief anticipated the exact
failure M1.73 records and forbade it in advance. Followed literally: §9's list
was written, then the grep was run, then the number was written.

---

## 11. Open items register — derived, not remembered

**How this was derived.** Every row re-checked **this unit** against the
artefact that decides it, with the method named per row. **No row was taken from
9a's §10 or Unit 8's §7.**

| item | state | derived from |
|---|---|---|
| **M3 repository visibility** | **CLOSED** | `gh repo view --json visibility,isPrivate` → `{"isPrivate":true,"visibility":"PRIVATE"}`, re-run this unit |
| **M1.72 transactional reservation** | **CLOSED this unit's branch** | `extract_p2.py` opens `BEGIN IMMEDIATE` around the pair; `test_a_failure_between_the_two_writes_leaves_neither` PASSED. Guard present and green; **not** observed red against pre-fix code (§8) |
| **B3.2** ceiling sums estimates, never actuals | **CLOSED (M1.90)** | `reconcile.actual_cost_usd` at `:520` is the first measured number; `_correct_the_reservation` at `:563` writes it back |
| **B3.3** reconciliation cost-ledger rule | **CLOSED (M1.90)** | §7 control 12, four clauses, each verified against `_correct_the_reservation` (§1) |
| **Amendment numbers cited but undeclared** | **CLOSED this unit (M1.91)** | 90 cited vs 85 declared before; `tests/test_amendment_register.py` green after, and observed red on an injected citation |
| **§7 control numbering** | **CLOSED this unit (M1.92)** | written order re-parsed and compared to positional index: `1`–`12`, equal |
| **Spec line 113's frozen list** | **CLOSED this unit (M1.93)** | membership deleted; replaced by a pointer to this register's method, per Unit 8 |
| **9c — first real spend** | **NOT STARTED, needs written authorisation** | `portal extract-p2` without `--dry-run` → exit **2**, observed |
| **Untransmitted audit section** | **OPEN — reconstructed by 9a, not closed** | grep → **seven** files (§9) |
| **A1** gate no-op | **CLOSED** | per-company gate in `score.py`; line 113's claim it is open is deleted by M1.93 |
| **A2** Phase-2 outputs have no signal keys | **CLOSED (M1.76)** | `_SignalWriter` refuses an undeclared key (`reconcile.py:368`); `test_no_key_outside_the_declaration_can_be_written` PASSED |
| **A3** `agency.footer_credit` two writers | **CLOSED (M1.77)** | `test_the_unscored_hint_key_is_declared_and_read_by_nothing` PASSED |
| **A4** no confidence filter into scoring | **CLOSED (M1.79)** | migration 012's filter; `test_a_rejected_boolean_is_filtered_out_and_the_rule_abstains` PASSED |
| **B1** brief export fail-loudly vs omit | **OPEN** | `grep -rn "def export_brief" portal/` empty; M7 not started |
| **B2** `needs_review_reason` | **CLOSED** | `review_flag` with CHECK vocabulary |
| **B3.1** reconcile vs submitting run | **CLOSED** | `_correct_the_reservation` updates `run WHERE id = batch.run_id`, and raises if absent |
| **B4** `run_id` for reconciled signals | **CLOSED** | `test_signals_are_written_under_the_submitting_runs_id` PASSED |
| **B5** ruleset version | **CLOSED in the code** | `ruleset.RULESET_VERSION = "v3"` |
| **B6** ruleset representation | **CLOSED** | `Rule` carries `reads`/`points`/`phase2_reachable` |
| **B7** `own_domain_shop` predicate | **CLOSED; residual is §10.3's** | `ruleset._own_domain_shop` |
| **C1** blog ladder | **OPEN** | chain order unchanged in `ruleset.RULES` |
| **C2** `opp.ai_invisible` | **OPEN, untestable** | M6 not started |
| **C3** M7 blocked on M6 | **PREMISE DISSOLVED** | §10.5; M7's remaining blocker is B1 |
| **C4** `uq_signal_identity` | **DECIDED (M1.80) — unchanged** | 9a's measurement; nothing this unit touches it |
| **§10.5 DNS-rebinding residual** | **OPEN, UNOBSERVED, labelled** | §10.5; closing it needs a pinning transport, refused under M1.4 |
| **§10.5 address guard's architecture limit** | **OPEN, uncloseable by design** | §10.5 |
| **§10.3 ban on calibrating §6.5** | **STANDING** | nothing in this unit touches a weight or band |
| **§10.2 `owner_operated` lever** | **OPEN, no new evidence** | base rate still 1 of 11; no LLM has run |
| **60 KB input cap** | **UNOBSERVED on real bytes** | tests cover it; no real page has been sent |
| **Inference error in a verified boolean** | **OPEN, unguarded, bounded** | M1.49; `test_a_boolean_is_verified_through_its_evidence_span` PASSED |
| **`expired`, two enums one word** | **SETTLED, not a finding** | §6; documentation fixed, no behaviour change |

**Three rows moved that no previous register had at all** — M1.91, M1.92 and
M1.93 — and all three are defects in the project's own instrumentation rather
than in the crawler, the ledger, or the model. That is the shape of this unit.

---

## 12. Verification

All runs with `ANTHROPIC_API_KEY` unset; `data/` does not exist in this
workspace.

| check | result |
|---|---|
| `6f5bc8d` baseline | 696 passed, 2 skipped, 139 subtests |
| after this unit | **698 passed, 2 skipped, 139 subtests** (+2, both the new register check) |
| `tests/test_amendment_register.py` | 2 passed in 0.14 s |
| negative control (injected `M1.994`) | **1 failed, 1 passed**, naming the file; restored, `git diff --stat` empty |
| declared vs cited, before | 85 declared / 90 cited / 5 undeclared |
| declared vs cited, after | **93 declared / 93 cited / 0 undeclared** |
| §7 written order vs rendered order | `1`–`12`, equal |
| `ruff check .` | clean |
| `ruff format --check .` | clean |
| `audit-politeness` healthy corpus | exit **0**, §5.2 HELD |
| `audit-politeness` breached corpus | exit **1**, §5.2 BREACHED, 1 unread |
| `extract-p2` without `--dry-run` | exit **2**, naming 9b and 9c |
| M1.73 counter grep | **seven** files — Units 4, 5, 6, 7, 8, 9a, 9b |
| CI on the PR | CI_PLACEHOLDER |

**Size.** **704 insertions across 4 files** for this closing unit, of which 563 lines are this findings document, 98 are `tests/test_amendment_register.py`, and the remainder is spec prose and one docstring clause. **No production behaviour changed**: the only non-comment edit outside `docs/` and `tests/` is zero lines. The 9b branch as a whole is 3,309 insertions across 16 files against `main`.

**What this unit did not build.** No reconciliation behaviour, no API call, no
key, no spend. `extract-p2` stays exit-2 without `--dry-run` until 9c is
authorised in writing.
