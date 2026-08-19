# Unit 7 — the cost ceiling stops being a paragraph

Measured 2026-08-18 against `98015f0` (PR #1's head, Unit 6). **No crawl, no API
call, no spend, and no `ANTHROPIC_API_KEY` at any point.** The only network
traffic in this unit was `pip install -e ".[dev]"` and loopback fixture servers.

Companion to M1.69–M1.72 in `docs/lead-portal-spec-v0.3.md`.

---

## 0. Baseline, taken before anything moved

| | result |
|---|---|
| `98015f0`, clean tree | **551 passed, 2 skipped, 121 subtests** |
| lint | `ruff check .` and `ruff format --check .` both clean |
| PR #1 | **OPEN, DRAFT, `MERGEABLE`/`CLEAN`**, CI green on all four jobs |

**Unit 7 is stacked on `98015f0`, not on `main`.** `origin/main` is still at
`d57ea64` and does not carry Unit 6's address guard. The instruction to stop was
honoured: the stack was authorised explicitly before this branch was created,
and PR #1 remains independently mergeable — nothing here touches
`portal/addresses.py`, `portal/sitepolicies.py` or `portal/net.py`.

CI on `98015f0` was re-checked rather than taken from the brief: run
`32176182691`, all four jobs pass — `ruff` 6s, `pytest (py3.11)` 1m29s,
`pytest (py3.12)` 1m28s, `audit-politeness (fixture corpus)` 25s.

## 1. M3 — one line, as instructed

`gh repo view --json visibility,isPrivate` returns
`{"isPrivate": false, "visibility": "PUBLIC"}`. **Still public**, verified a
fourth time; nothing in this session can change it.

## 2. The defect, measured before anything was written

The brief's measurement does not reproduce as stated, and the difference
matters. `grep -rn est_cost_usd portal/` returns **two hits**, not nothing:

```
portal/migrations/001_initial_schema.sql:259:    est_cost_usd   REAL DEFAULT 0,   -- run
portal/migrations/001_initial_schema.sql:273:    est_cost_usd      REAL NOT NULL, -- llm_batch
```

The columns exist; nothing reads them. The measurement that shows the actual
defect is `grep -rn est_cost_usd portal/ --include=*.py`, which returns empty.

**The demonstration, run on `98015f0` before a line was changed.** A database
was built with `portal init`, loaded with twenty runs of `$45` each inside the
30-day window and one unreconciled `$120` batch, and then §7 control 4's own
reservation function was called:

```
portal init -> 0
ledger says, rolling 30 days: $900.00   (ceiling is $45)
reserve_batch returned  $0.0201  (30,000 in / 2,048 out)

VERDICT: paid path reachable with ledger at $900 against a $45 ceiling = True
```

Twenty times over the ceiling, and the function whose docstring cites §7 control
4 hands back a fresh reservation without looking. `count_tokens` was stubbed, so
the question under test is the ledger and not the tokenizer.

## 3. The month boundary — reconciled, not invented (M1.70)

**The brief asked me to check its own premise, and the premise held.** §10.4b
said the schema "defines no month boundary and no query". §7 control 2 has
defined both since v0.3: a **rolling 30-day** window, with the SQL written out
in full. Both readings stood in one document. That is the finding.

**§7 wins and nothing new was chosen.** Picking a fresh window would have been
re-deciding a question §7 had already answered — the exact error §10.4b exists
to prevent, committed inside §10.4b itself. §10.4b now points at §7.

**The window is keyed on `run.started_at` and on nothing else**, which is what
keeps it consistent with **B3.1**. `llm_batch.run_id INTEGER NOT NULL REFERENCES
run(id)` gives every batch exactly one submitting run; B3.1 sends
`actual_cost_usd` back to that run; the run's `started_at` does not move. So a
batch **submitted in one window and reconciled in the next** never moves money
across the boundary — it was counted where it was reserved, and stays there.

**The consequence, stated rather than discovered:** spend **ages out** 30 days
after its run *started*, whether or not that run's batches ever reconciled. A
run 40 days old whose batch reconciles today does not re-enter the window. That
is correct for a rolling runaway guard and would be wrong for an accounting
record, so §7 control 2 now says which one it is. Both directions are tested.

**Rejected:** a calendar month. It makes the guard's strictness depend on the
day of the month — an identical bug costs a full ceiling starting on the 2nd and
almost nothing starting on the 30th. **Also rejected:** keying the window on
`llm_batch.reconciled_at`, which would contradict B3.1 by attributing a batch to
a run that never reserved it.

## 4. The double-count — the brief was right, and it is M1.69

§10.4b instructs the query over `run.est_cost_usd` **and**
`llm_batch.est_cost_usd`/`actual_cost_usd`. §7 control 4 says a batch reserves
into **both** tables. Read together, every batch is counted twice — and §7
control 2's own draft query, which sums `run` alone, contradicts §10.4b outright.

**Settled from the schema, not from either prose.** `llm_batch.run_id` is
`NOT NULL REFERENCES run(id)`: the `run` row is the ledger, and the `llm_batch`
row is the per-batch record of a line already in it. `run` is summed alone.

The defect is a number rather than an argument, and it is asserted in
`tests/test_cost_ledger.py`:

| | reading |
|---|---|
| one run at `$30`, one batch of `$30` reserved into both | |
| `run` alone (implemented) | **$30.00** |
| both tables (as §10.4b specified) | **$60.00** |

**Direction of error: it fails CLOSED**, which is the safe one, and is why this
is a defect rather than an incident. But the effective ceiling would have been
**$22.50 against a stated $45** on all-batch spend, and M1.23 is the finding
that raised the ceiling to `$45` *precisely* so it would stop tripping on
correct behaviour — "a ceiling that trips on correct, expected behaviour teaches
its operator to raise it without reading it, which is worse than having no
ceiling." A guard that trips at half its stated value is that failure one level
down.

## 5. A fail-open path nobody had noticed (M1.72)

Control 4 requires two writes — `llm_batch.est_cost_usd` and
`run.est_cost_usd` — and does not say they are one transaction. Control 2 reads
only the second. **A crash between the two writes leaves the batch on the books
and the ledger blind to it: an under-count.**

Every other failure mode in §7 is deliberately biased towards aborting. Control
3's crash window "can only over-count, never under-count". M1.52 refuses a
fallback estimate outright. This is the one path in §7 that fails **open**, and
it was created by the interaction of two controls that are each correct alone.

**Written into control 4 as a requirement, not built.** It needs the reservation
caller, which is M5, and Unit 7's fence is control 2 plus the assertion. Named
so that the single-table query it depends on cannot be quietly invalidated later.

## 6. `assert_prices()` does not exist and never did (M1.71)

The brief said to model the new assertion on `llm.py`'s `assert_declared` "and
`assert_prices` beside it". There is no `assert_prices`. `grep -rn assert_prices
portal/` returns nothing; the two hits in the repository are both in the spec —
§7 control 10 and M1.52's own resolution column — each asserting that
`portal/llm.py` carries it at import.

`portal/llm.py` has **one** assertion, `assert_declared` (`llm.py:205`, called
at import on `llm.py:659` before this unit), covering `PRICES` **and** `LIMITS`
together. The reason they share one assertion is now written down rather than
left as an accident: a priced model with no declared limits is callable at an
assumed parameter surface, which is M1.50's whole finding, and splitting the
assertion would have hidden the check that catches it.

Recorded rather than corrected silently, because *"a function the spec describes
and the code does not have"* is the same defect class as B7 in §10.4 — a table
row that reads as implemented. It survived two units and was inherited by this
brief.

## 7. Where `MONTHLY_CEILING_USD` lives, and why one home

**`portal/ledger.py`**, beside the window and the query that enforce it.

`llm.py` holds **prices** — dated facts about a vendor, inputs to arithmetic.
The ceiling is an **output constraint**: a policy bound on the total those
prices produce. Different kinds of number. The concrete reason, though, is
M1.70: §7 stated the window and the SQL, §10.4b said neither existed, and the
two drifted for two units *because the decision and its expression lived in
different places*. One home, so M5 does not invent a second.

**Rejected: `portal/config.py`.** It holds paths and the §5.2 politeness floor,
and its docstring says it holds things that are not secrets — a cost bound with
no query beside it is exactly the split that produced M1.70.

**Rejected: `portal/llm.py`.** It would have put the bound next to
`WEB_SEARCH_PER_SEARCH_USD`, which is superficially attractive since both are §7
constants. But the query needs `sqlite3`, and `llm.py` is deliberately pure
today — importing the database into the provider-agnostic layer to reach one
`SUM` inverts the dependency. `ledger.py` imports nothing from `portal`, and
`llm.py` imports `ledger`. One direction.

## 8. The gate — three pieces, and why each is there

`@requires_ledger_clearance` refuses a call whose `clearance` is not a real
`LedgerClearance`; `check_ceiling` is the only thing that constructs one. So
*"did anyone consult the ledger?"* is answered at the call site by the type,
not by a convention someone has to remember.

`PAID_SURFACES` / `FREE_SURFACES` classify **every** callable in `portal/llm.py`
and on `AnthropicProvider`. The free list is written out longhand on purpose:
it makes a **new, unclassified callable a build failure**, which is the only
check here that can catch the failure that actually threatens M5 — a paid path
nobody thought to register.

`assert_ledger_guarded` runs all of it **at import**, in `assert_declared`'s
shape and beside it.

**`submit_batch` is gated as well as `reserve_batch`, and that is not
belt-and-braces.** `reserve_batch` spends nothing — it is arithmetic over the
price table. `client.messages.batches.create` is irrevocable the moment it
returns. Gating only the reservation would have produced an assertion that
reads like a guarantee and stops nothing, which is M1.49's lesson about a guard
believed to be stronger than it is.

`LLMProvider.submit_batch`'s **Protocol signature** takes the clearance too, so
a second provider cannot satisfy the interface without it.

**Order of operations:** `portal llm-prices --reserve` consults control 2
**before** pricing anything. A runaway that is allowed to price itself first has
already made the call the ceiling exists to refuse.

```
$ PORTAL_DB=…/poc.db portal llm-prices --reserve 40      # the $900 ledger
§7 control 4 reservation over a 40 KB page:
  refused: §7 control 2: $900.00 reserved or spent in the last 30 days exceeds
  the $45.00 ceiling. This is a runaway guard, not a budget (M1.23) — expected
  steady state is $31–36/month, so being here means something is wrong, not that
  the month was busy.
EXIT=2
```

On a healthy ledger, and with no key, the same command reports
`§7 control 2: $0.00 of $45.00 used over 30 rolling days; $45.00 headroom` and
*then* declines to guess at `count_tokens` (M1.52), which is the pre-existing
behaviour unchanged.

## 9. Direction of error, stated for each new decision

| decision | fails | why that direction |
|---|---|---|
| ledger unreadable (`sqlite3.Error`) | **closed** — raises | an empty ledger and an unreadable one look alike; treating the second as the first is how an unmeasured number authorises spend |
| spend exactly at the ceiling | **open** — clears | `>` not `>=`, matching §7's SQL as written; the ceiling is the bound, not the first refused value |
| clearance missing or a look-alike | **closed** — `LedgerBypass` | a gate with an opt-out is the convention §7's preamble refuses |
| unclassified new callable | **closed** — import fails | the whole point is that M5 cannot add a paid path silently |
| batch reserved but run row not updated (M1.72) | **open** — under-counts | not closed here; needs the caller. Written into control 4 as an M5 obligation |

## 10. Negative control — every mechanism broken on purpose

Each break was applied to a green tree, the suite run, and the tree restored.
The second column is the suite **with `tests/test_cost_ledger.py` excluded** —
i.e. what the 551 tests that existed at `98015f0` can see.

| break | new tests | pre-existing (551) |
|---|---|---|
| **A.** M1.69's double-count reintroduced (sum both tables) | **4 failed** | **551 passed** |
| **B.** `@requires_ledger_clearance` removed from both surfaces | **9 collection errors** | **8 collection errors** |
| **C.** window widened 30 → 365 days | **5 failed** | **551 passed** |
| **D.** ceiling comparison neutered (`if False and spend > …`) | **1 failed** | **551 passed** |

**The pre-existing-test answer is zero of 551**, for every break except B — the
same shape as Unit 6's zero of 537 and M1.65's finding one level in. Nothing in
the suite could see the absence of a cost ledger, because nothing in the suite
had ever mentioned one.

**B is different, and it is the strongest result here.** Removing the decorator
does not fail a test — it fails the **import**, so the suite cannot be collected
at all:

```
portal.llm.LLMConfigError: portal.llm: 'reserve_batch' is a paid surface with no
§7 control 2 gate. Decorate it with @requires_ledger_clearance — the ledger is a
mechanism only while every paid path is obliged to consult it.
```

The same for the provider, and for the case that matters most — a new callable
nobody classified:

```
portal.llm.LLMConfigError: portal.llm: 'spend_money_somehow' is classified as
neither paid nor free. Add it to PAID_SURFACES (and decorate it with
@requires_ledger_clearance) or to FREE_SURFACES. §7 control 2 cannot gate a path
nobody declared.
```

**A second, unplanned negative control ran by itself.** Adding the gate turned
**10 of the 551 pre-existing tests red** — every test that reached
`reserve_batch` or `submit_batch` — with `LedgerBypass`. That is the gate
proving it covers the real call sites and not only the ones written for it. All
10 are now threaded through a clearance.

## 11. What was NOT built, and the seam

Controls **3** (per-run reservation) and **4** (batch reservation) need a caller
and remain M5's, per the fence. Nothing here reserves, reconciles, or writes
`run.est_cost_usd`.

**The seam is `LedgerClearance` itself, and its limit is stated rather than
overclaimed.** It is a frozen dataclass, so M5 *could* construct one by hand
with `spend_usd=0.0` and bypass the ledger. That is deliberate: the tests do
exactly this (`_cleared()` in `tests/test_llm.py` and
`tests/test_llm_anthropic.py`, documented where it is defined). A private
sentinel was considered and **rejected** — it would raise the bar from "call the
right function" to "import a private and lie", which is security theatre against
a co-operating author and makes every test harder to read for nothing. The gate's
job is to make a paid path unreachable **without anyone deciding**; writing
`spend_usd=0.0` by hand is a decision, and a visible one in review.

**§10.6 records the honest state:** `run.est_cost_usd` is now
*read-live, written-by-nobody*. The ledger reads `$0.00` in production today and
every gate passes. That is the intended order and the reason this landed before
M5 rather than with it — the gate exists **before** the caller, so M5 is written
against its presence.

## 12. Verification run locally

| gate | result |
|---|---|
| `ruff check .` | clean |
| `ruff format --check .` | clean |
| full suite | **576 passed, 2 skipped, 123 subtests** |
| `audit-politeness`, healthy corpus | `§5.2: HELD`, **exit 0** |
| `audit-politeness`, `--breached` corpus | `§5.2: BREACHED`, **exit 1** |

No `ANTHROPIC_API_KEY` was set at any point, which is what CI enforces and what
made this unit separable from M5 in the first place.

## 12b. CI caught a defect the local suite could not — and it is M1.64's shape

**The first CI run on this branch failed both pytest jobs while the same suite
was green locally.** Not flakiness — a real defect I introduced:

```
sqlite3.OperationalError: no such table: run
FAILED tests/test_llm_anthropic.py::PricesCommand::test_the_reservation_refuses_to_guess_without_a_key
```

`llm-prices --reserve` now opens the database, and the pre-existing test invoked
it on the **default path**. This machine has a `data/portal.db` from 17 August;
a CI runner has none, so `db.connect` created an empty file and the ledger query
hit a schema that was not there. **The local pass was an artifact of the
developer's environment**, which is exactly M1.64 — the defect Unit 5 fixed —
one level down, and it is the second time this project has produced it.

Two things came out of it, both kept:

- **The ledger was right and the CLI was wrong.** `check_ceiling` deliberately
  lets `sqlite3.Error` propagate rather than reading a missing table as `$0`
  (§9). But the CLI let it out as a traceback. It now refuses with exit 2 and
  names the fix — *"the §7 control 2 ledger is not readable … run `portal init`
  … an unreadable ledger and an empty one look alike"*. Fail-closed either way;
  the difference is whether an operator can act on it.
- **The test now builds its own database** instead of borrowing whatever the
  machine has, and two tests were added: one proving the ledger is consulted
  **before** the key is even looked for, one proving an uninitialised database
  refuses rather than pricing a call.

**Reproduced locally after the fix by removing `data/` and running as CI does**
(`env -u ANTHROPIC_API_KEY python -m pytest -q`): **576 passed, 2 skipped**. That
check is the one I should have run before the first push, and not running it is
the process error in this unit.

## 13. Still open

- **M3 — the repository is still PUBLIC.** Fourth unit to record it. The
  operator's to make; no tool in this session can perform it.
- **The first external audit's "LLM-generated / hallucination signals" section
  is still missing — missing, not empty — and that audit is not closed.** It
  belongs with M5, the stage that generates the content the section is about.
  Not closed here.

  > **Corrected by Unit 8 (M1.73).** This line originally read *"THREE units have
  > now passed it over: Unit 5, Unit 6 and Unit 7"*. Both halves were wrong: the
  > membership omitted **Unit 4**, which also passed it over, and the count was
  > recounted on a definition different from the one Units 5 and 6 had used. The
  > count is no longer stated here. **It is defined once, in the spec's Unit 2a
  > amendment, as a named list with the grep that derives it** — currently Units
  > 4, 5, 6 and 7, four. Restating the number in a report is the mechanism that
  > broke it.
- **M1.72** — control 4's two writes must commit together. Specified, not built;
  needs M5's caller.
- **M1.66** (score-date pinning) and **M1.61** (origin-keyed robots lookup)
  remain M5 preconditions, untouched by this unit.
- **The ceiling has never been exercised against real spend.** Every number in
  §7.1 is arithmetic over the price table; no run has ever written
  `run.est_cost_usd`. Whether `$45` is the right bound is unobserved and stays
  labelled unobserved until M5 produces a month of data.
- **`LedgerClearance` is forgeable by a co-operating author** (§11 above).
  Recorded as a stated limit, not a defect.

## 14. Where the instructions were wrong

Four things, none of them fatal, all of them worth recording rather than
working around.

1. **`grep -rn est_cost_usd portal/` "should return nothing". It returns two
   hits.** Both are schema DDL. The substantive claim — no code reads either
   column — is correct, and the measurement that shows it is
   `grep -rn est_cost_usd portal/ --include=*.py`. As written, the check reads
   as a failed one, which is how a real absence gets argued away.

2. **`assert_prices` does not exist** (§6 above). The brief said to model the new
   assertion on it "beside" `assert_declared`. It inherited the error from the
   spec, which has carried it for two units. This is now M1.71.

3. **Step 0 contradicts itself.** "If it has not [merged]: … STOP and tell me"
   and "Unit 7 branches from PR #1's head or from a merged main" cannot both be
   acted on: the second pre-authorises exactly what the first forbids. I honoured
   the STOP and asked. Worth fixing, because the next unit will hit the same
   branch state — PR #1 is still unmerged as this is written.

4. **§10.4b's two errors were the brief's own premises, and both were real.**
   The brief was right to ask for them to be verified rather than accepted, and
   right on both counts (M1.69, M1.70). Recorded here as the one place where
   "check my reading" was the instruction that found the defect.

**One error of mine, not the brief's**, recorded because §12b is where it
belongs and this section is where a reader looks for it: I ran the full suite
locally and called it green without reproducing CI's environment, and the
project's own `data/` directory hid the defect. See §12b.

**And one judgement call the brief did not cover.** `ledger.py` needs a UTC
timestamp, and `artifacts.utc_now()` already produces exactly the format
required. It is **not** imported: `portal/artifacts.py` pulls in `portal/net.py`
and therefore `httpx`, and the cost ledger must not depend on the HTTP transport
to know what time it is. The four-token duplication is noted in the code with
its reason. This cuts against M1.42's standing objection to a second expression
for one fact, so it is flagged rather than buried — if `utc_now` is ever moved
to a leaf module, `ledger.py` should import it.
