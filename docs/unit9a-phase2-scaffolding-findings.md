# Unit 9a — the Phase-2 scaffolding, and four blockers that were filed as backlog

Measured 2026-08-20 against `4beefe4` (merged `main`). **No crawl, no API call,
no spend, and no `ANTHROPIC_API_KEY` at any point.** The only network traffic in
this unit was `pip install`, loopback fixture servers, and `git`.

M5 phase 9a. Companion to M1.76–M1.85 in `docs/lead-portal-spec-v0.3.md`.

---

## 0. Baseline, and 9a confirmed unstarted

Every item below was checked against the artefact rather than against the brief.

| claim | how it was checked | result |
|---|---|---|
| `main` is `4beefe4`, PRs #1–#3 merged | `git log --oneline origin/main`, `gh pr list --state all` | **confirmed** — #1 `unit6`, #2 `unit7`, #3 `unit8`, all `MERGED` |
| 589 passed / 2 skipped / 126 subtests | full suite, `data/` aside, no key | **confirmed exactly** |
| lint clean | `ruff check .`, `ruff format --check .` | **confirmed** |
| no migration 011 | `ls portal/migrations/` | **confirmed** — highest is `010_score_evaluated_on.sql` |
| `phase2_input_settled` absent from `portal/` | `grep -rn` over `portal/`, `tests/`, `docs/` | **confirmed** — 2 hits, both prose: `unit5-…-findings.md:42` and `spec:1518` |
| no `docs/unit9a-*-findings.md` | `ls docs/unit9a-*` | **confirmed** — no such file |
| `cli.py` still says `extract-p2` arrives with M5 | `cli.py:6` | **confirmed** verbatim |

**The three M5 preconditions, verified in the code rather than in §10.4b.**

| precondition | verified by | state |
|---|---|---|
| M1.69–M1.71 cost ceiling | `ledger.MONTHLY_CEILING_USD = 45.0`, `check_ceiling` the sole constructor of `LedgerClearance`, `llm.assert_ledger_guarded` called at import in both `llm.py:802` and `llm_anthropic.py:329` | **CLOSED** |
| M1.74 score-date pinning | `010_score_evaluated_on.sql:38` `ALTER TABLE score ADD COLUMN evaluated_on`, and `score.evaluate` sets it from its own `today` | **CLOSED** |
| M1.75 origin-keyed robots | `impressum_audit._policy` keys on `urls.authority_of` and returns `robots.unavailable(...)` where no row matches | **CLOSED** |

**Next defect number: M1.76.** `grep -ohE "M1\.[0-9]+"` over `docs/`, `portal/`
and `tests/` tops out at **M1.75**, and the amendment table's own rows run
M1.70–M1.75 with no gaps. The brief said M1.76 and the brief was right.

**No corpus was needed and none was used for anything but reading.** The stored
database was moved aside for every test run, and the two read-only measurements
below were taken against a `mode=ro` copy in a scratch directory.

---

## 1. A2 — the mapping existed, and existed nowhere the code could reach

The register says *"no field→key mapping table in the spec; `HomepageExtract.own_brand`
still has no view column"*. Re-derived, and the register is right about the
symptom and understates the cause.

**The mapping was ratified on 2026-08-16 — all ten items, `RULED` in the
heading — inside `docs/a2-phase2-signal-mapping-proposal.md`.** The spec was
never amended. So A2 is not an open question; it is a **closed question filed
somewhere the implementation cannot see**, which is a different defect with a
different fix. A proposal is an argument. `extract-p2` needs a table.

**The invisible half is worth more than the visible one.** `qual.own_brand` is a
live +10 rule declared `reads=()` whose predicate returns `declines()`
unconditionally — and the comment said *"Phase 2 only: the judgement needs the
LLM extraction"*, which reads as deliberate dormancy. It is not. **No signal key
existed for it to read.** And `assert_declared` — the one check in this project
built to catch a rule that cannot fire — carried a **named exemption** for
exactly this rule:

```python
if not rule.reads and rule.id != "qual.own_brand":
    raise RulesetError(f"{rule.id} reads no signal and cannot fire")
```

with a test pinning the exemption in place and a docstring calling it *"the
documentation"*. That is B7's shape at one remove: not a rule that reads as
implemented, but a **guard that reads as satisfied**, with the live instance
written into it as a special case and a test defending the special case.

**What it cost, on the corpus.** The rule is `phase2_reachable`, so its +10 sits
in every unbanked company's `remaining_upside`. `germanelectronic.de` scores 5
in Phase 1 and carries 50, so it is admitted at **exactly** the B floor of 55 —
and 45 without `own_brand`'s ten. It advances to **paid** extraction solely on a
rule that would have declined when Phase 2 got there.

### What was done

The ruling is transcribed into §5.5b with **one column the proposal did not
carry: the rule that reads each key.** A field and its reader are only auditable
side by side, and the brief asked for both audits. They were run:

- **Fields with no reader: eleven, all deliberate.** Nine are §8/§9 material —
  a research brief that cannot name an address or a register number is not a
  brief. One (`impressum.owner_name_present`) is unscored **on purpose**, so
  §10.2 becomes decidable by measurement rather than by argument. One
  (`agency_credit`) is demoted, which is A3 below.
- **Rules with no field: none, after this.** Before it, exactly one, and it was
  the +10 above.

`brand.own_brand` and its view column arrive in migration 011,
`qual.own_brand` gains `reads=("own_brand", "homepage_extracted")`, and
**`assert_declared`'s exemption is deleted**. The test that pinned it is
**inverted** rather than removed — it now asserts that *no* rule reads nothing —
because a test that was documenting a gap should say so where the gap was.

**The two stage facts are the load-bearing rows of the whole mapping.**
`llm.impressum_extracted` and `llm.homepage_extracted` are written whenever the
extraction ran, whatever it returned, with `confidence = 1` — they are facts
about the *stage*, not judgements about a page, and a stage cannot be wrong
about whether it ran. Every A7 guard in this project works by declining to
write, so without a positive fact beside the silence there is no way to tell
*the model read the page and could not tell* from *Phase 2 never ran here*. §5.4's
`phase2_input_settled` and §6.1's three states both read them, and nothing else
answers what they answer.

---

## 2. A4 — `discarded` had no implementation, and the register's pointer was stale

The register says *"`company_profile` view (`001_initial_schema.sql:168`) pivots
latest-per-key with **no** confidence predicate"*.

**The claim is true. The citation is wrong**, and re-deriving is what found it:
`001`'s view was dropped and recreated by migration 006 and again by 007, so the
live definition is `007_profile_scoped_to_finished_runs.sql`. The claim holds at
the new address — no revision of the view has ever carried a `confidence`
predicate — but a reader following the pointer lands on a view that has not been
in force since M1.39.

**Measured before the change:** 2,404 signal rows in the stored corpus, **every
one `method='deterministic'` with `confidence IS NULL`**, and **0 rows with
`method='llm'`**. The filter is a no-op on today's data, which is precisely the
argument for landing it now: M5 is what makes `confidence ≠ 1` reachable at all,
so this is the last moment it can be added without a migration that also has to
repair rows.

### Where the predicate goes is the finding, and the obvious place is wrong

The filter is `confidence IS NULL OR confidence > 0`, applied in `latest` —
**after `current_run` has already chosen the authoritative run.** Putting it in
`observed`, which is the natural place and one line earlier, would remove a
Phase-2 run's rejected rows from the set `current_run` takes its `MAX(run_id)`
from. A run whose extractions all failed verification would then stop being its
stage's authoritative run, and an **older** run's values would be served as
current — which is migration 006's defect exactly, re-created by the guard meant
to strengthen it. `test_a_run_of_only_rejected_values_stays_its_stages_authority`
is the pin.

`NULL` passes, or Phase 1 is blanked entirely. `0` is excluded and **no
threshold above 0 is invented** — a plausibility cut chosen on zero observations
is M1.4's error, the one §10.3 already refuses for *"fewer than N products is
suspicious"*.

### Direction of error, checked rather than asserted

**Too strict, and it can only be too strict in the cheap direction.** A filtered
value is a signal scoring does not see, so its rule declines or abstains. That
this is uniformly the too-**low** direction is a property of the mapping, not a
hope: every §5.5b key that any §6 rule reads feeds a rule that **awards** points
(`impressum.legal_form` +15, `impressum.gf_count` +15, `site.owner_named` +15,
`brand.own_brand` +10), and the one Phase-2 key on a rule that **subtracts** is
`agency.footer_credit_llm`, which A3 gives no reader at all. **So the filter
cannot withhold a penalty, and therefore cannot move a company toward a phone
call.** The opposite choice scores a stranger on a value the tool itself
measured and rejected, which is the direction §8 fails an export for.

---

## 3. A3 — two writers on a −20, and the merge rule is that there is no merge

`agency.footer_credit` is written by `parsers.footer_agency_credit` and read by
`_has_agency` at **−20**. `HomepageExtract.agency_credit` is the second writer
and arrives with M5. Left unstated, the winner is whichever stage ran last — so
re-running the **free** `extract-p1` after a **paid** `reconcile` would silently
revert paid work to a regex.

**Ruled: two keys, one reader.** The LLM value goes to
`agency.footer_credit_llm`, which no §6 rule may read and no view column
exposes — `content.blog_lastmod_hint`'s existing treatment.

**The reason it is structural and not semantic is §10.4's exclusion.** The
platform vocabulary — JTL, Shopify, WooCommerce, WordPress, Shopware, Magento,
PrestaShop, Gambio, OXID, plentymarkets — lives in the deterministic parser as
`parsers._PLATFORM_CREDIT`, a regex a test can exercise. **Re-stating that list
inside a system prompt is neither substring-verifiable nor testable without
spending money**, so a prompt-side exclusion is a claim, not a guard.
*"Powered by JTL-Shop"* has already produced two opposite defects from this one
string — as `jtl-shop` it was the platform signature that detected no JTL shop
at all (M1.9), and as *"powered by"* it was an agency credit that detected a
shop system and took −20 off `smoke2u.de` for its choice of software.
**Withholding the reader is the guard**, and it is enforced by a test that no
`Rule.reads` entry and no view column names an unscored hint key.

**Direction: it under-detects, leaving the lead too high.** A real agency credit
only the model can see — a logo, an unusual phrasing — withholds the −20 that
should have fired. That is the same direction §6.3 already accepts for this rule
(*"under-detects by design … a bonus signal and never a gate"*). What the
demotion adds is that the second instrument's opinion is **stored**, so the two
can be compared on a corpus later and the choice becomes a measurement rather
than a preference.

---

## 4. C4 — decided, and the measurement is why it was decided the other way

The register: *"`uq_signal_identity` includes `evidence_url`, so a redirect
defeats dedup — `001_initial_schema.sql:155`, unchanged"*. The index is indeed
unchanged. **Three things were established before deciding, not after.**

**(1) The column has never disambiguated anything.**

```
signal rows                          2404
distinct (run_id, company_id, key)   2404
groups with >1 distinct evidence_url    0
```

Dropping `evidence_url` from the index today would change no row in the
database.

**(2) `evidence_url` is not a live URL and cannot be redirected.** It is
`artifact.url`, taken off the stored artifact row in the same expression as
`artifact_id` (`extract._write`, M1.42 — the parameter deliberately has no
string form). A redirect happens in `fetch` and is frozen into the artifact row
before any extraction runs. **So the case the finding describes cannot arise in
an extraction stage at all** — including M5's, which is the first stage to write
signals whose evidence is a URL that *was* redirected on the way in. §5.6's
"safe to run repeatedly" depends on the second invocation computing the same
`evidence_url`, and it does, from a row that does not move.

**(3) A duplicate would cost no score.** `company_profile` pivots `rn = 1` on a
deterministic `(observed_at DESC, id DESC)` tiebreak (D5(a)), so exactly one
value surfaces and the same one on every query. It costs §9 a second evidence
link, and it narrows §5's D6 sentence from *"no duplicate observations within
the same run"* to *"no duplicate observations **from the same document** within
the same run"* — which is what the index has always actually guaranteed.

**Rejected on direction of error, not on cost.** Narrowing the key to
`(run_id, company_id, key)` would make the second document's reading vanish
through `ON CONFLICT … DO NOTHING` — written nowhere, visible in nothing,
indistinguishable from never having been observed. **That is M1.75's collapse in
a second table**, and M1.75's own conclusion applies unchanged: the absorbed row
is never written, so nothing on disk can later establish there were two. Keeping
`evidence_url` errs toward recording **too much**, and an over-record is a thing
a person can see and reconcile.

**Labelled unobserved:** no writer has ever produced a same-run, same-key,
two-document signal, so the tiebreak's behaviour in that case is untested
against reality rather than tested and sound.

---

## 5. The scaffolding, with both gaps reproduced first

### 5.1 `assert_declared` accepted a rule that could not answer the question

Run on `4beefe4` before anything was built:

```
fields on Rule: ['id','points','section','reads','phase2_reachable','evaluate','chain']
has phase2_input_settled? False
assert_declared ACCEPTED it -> GAP REPRODUCED
phase2_reachable rules: ['qual.owner_operated','qual.own_brand','opp.ai_invisible','opp.slow_site','neg.has_agency']
any that can report settledness: []
```

Five live rules, none able to say whether Phase 2 had already answered its
input, and a newly invented Phase-2 rule accepted without comment.

**It is declared and not derived, and the reason is A7's own mechanism.** The
tempting fix reads `score_component`: a rule that answered has a component. It
does not work. `evaluate` records **no component at all** for a rule that
DECLINES, and `assert_declared` refuses a rule worth zero — so `score_component`
holds fired rules and abstentions and nothing else. A Phase-2 `false` — *the
commonest outcome, and exactly the one that ought to tighten the bound* — is a
decline, invisible to any outcome-based reading and indistinguishable from
*never evaluated*. `test_it_is_not_derived_from_outcomes` pins that.

`assert_declared` now **requires** it on every Phase-2-reachable rule and
**refuses** it on every other. Both directions, because each fails differently:
a missing one silently inflates the bound; a spurious one asserts something
about a phase that cannot touch the rule.

### 5.2 The gate could only loosen, and a company paid twice for one answer

```
phase 1 only        : total=15 upside=50 admitted=True
phase 2 answered NO : total=15 upside=50 admitted=True
gate tightened? False
```

The second row is a company whose Phase 2 has **already run and answered both
booleans in the negative**. Those 25 points are gone and nothing can award them.
The bound did not know, so it kept offering them and re-admitting the company to
paid extraction on two closed questions.

After:

```
phase 1 only        : total=15 upside=50 admitted=True
phase 2 answered NO : total=15 upside=25 admitted=False
gate tightened? True
```

**Direction: the gate can now only get tighter**, which is the expensive
direction on paper and is safe here for a reason no other tightening would have.
§5.4's property is *no company whose final score could reach B is discarded
without a human being told*. A settled input is the one case where that holds
**without needing the queue at all**: the points are not merely unmeasured, they
have been measured and are not there. Nothing is withheld; the bound is
corrected downward to what is actually still available.

**A naming collision was in the way and is worth one line.** `evaluate` already
had a local `settled: set[str]` tracking which §6.2 ladder chains had stopped —
a completely different question. Renamed `chains_settled`.

### 5.3 Three states, and the two review reasons with their writer

*Not run* declines, *ran and answered* fires or declines, *ran and could not
tell* abstains. Three collapses were available and all three are wrong:

| collapse | cost |
|---|---|
| `null → false` | *unverified* read as *absent*, on a rule that awards points — the defect §6.1 exists to refuse |
| `null →` decline | right until Phase 2 runs, wrong the moment it has: a decline records no component, so the company shows nothing and nobody is told |
| decline `→` abstain | *"Phase 2 has not run yet"* in the queue for every company — §6.4's *"a queue that refills itself stops being read"*, manufactured |

The third state is reached **two ways** — the model returned `null` (§5.5b
instructs exactly that), or its `_evidence` span failed verification and
migration 012's filter removed the row — and both take the same review reason
**deliberately**, because both send a person to the same page to answer the same
question. A7's one-question test.

`qual.owner_operated` abstains **only where disjuncts 1 and 2 have both
declined**. A company that already won its +15 on `legal_form` or `gf_count` has
nothing to abstain about, and flagging it would be a queue item with no action
behind it.

**Migration 013 adds both reasons in the same commit as the code that raises
them** (M1.45(c)), and adds **no** `contact_blocking_reason` row — both
abstentions withhold an *award*, so the lead reads too low, which the queue
repairs. A test asserts the absence so it reads as a decision.

### 5.4 `extract-p2`'s entry point, and the first time the ledger gate engaged

`portal/extract_p2.py` builds the seam and spends nothing. `submit` is the one
paid surface: registered in `PAID_SURFACES`, decorated with
`@requires_ledger_clearance`, and refusing a truthy stand-in for the clearance —
only `ledger.check_ceiling` constructs one. The provider is injected
(`llm.LLMProvider` is a Protocol), so the module names no vendor, imports no
SDK, and **every test drives a fake**.

Selection reuses `impressum_audit.select_inputs` rather than re-expressing A2 §7
— it is already the project's single expression for *which stored Impressum a
company is measured on*, and it already excludes a homepage filed as an
Impressum (M1.43) and any body the origin's own robots.txt disallows (M1.44).

`portal/verify.py` takes the **sent text** and has no constructor that accepts a
path, an artifact id or a connection, so it structurally cannot repeat M1.43's
*verified against a different page*. **The limit of the guarantee is pinned by a
test that passes**: a span genuinely on the page, an inference from it that is
wrong — a homepage containing *"unsere eigene Marke"* in a sentence saying the
shop is a reseller. That is the case the backstop cannot catch, and §5.5b says
so; the test exists so nobody reads the guard as stronger than it is.

**`extract-p2 --dry-run` against the stored corpus** — free, no key, read-only:

```
9 of 13 companies would be sent; 4 skipped, for three distinct reasons
  ekomia.de             no 200 Impressum artifact with a body
  propellerdiscount.de  robots_unavailable: no robots.txt stored for origin
                        www.propellerdiscount.de (M1.75)
  smoke2u.de            robots_unavailable: … www.smoke2u.de (M1.75)
  snocks.com            robots-disallowed body (M1.44)
92,203 bytes of cleaned visible text total; largest page 21,181 bytes
```

Two things worth naming. **M1.75 is visibly doing work here**: two companies are
withheld from paid extraction because the tool cannot establish whose robots.txt
governed their bodies, rather than being sent under a permissive default — that
is Unit 8c's over-reporting direction, costing a run and saying why. And **the
60 KB cap is unobserved on this corpus**: the largest cleaned page is a third of
it, so the truncation path is covered by tests only and has never run on real
bytes.

---

## 6. The missing audit section: LLM-generated / hallucination signals

**This does not close the audit.** The original section was never transmitted
and cannot be recovered — the branch that carried the review is byte-identical
to `main` and holds no audit document, so the text exists nowhere. What follows
is the next best thing and is labelled as such: **what that section would have
had to cover for this code**, written at the stage that generates the content it
is about. A reconstruction is not the artefact. The register row stays open.

### 6.1 Where an LLM-extracted value can be plausible and wrong

The dangerous failure here is not a nonsense answer. It is a **confident, well-
formed, page-shaped answer that is false**, because that is the one that survives
every cheap check and ends up in a letter to a stranger.

| shape | what it looks like | what catches it |
|---|---|---|
| **Fabrication** — a name, court or register number the page does not contain | indistinguishable from a correct answer at the API boundary | substring verification (§5.5b). This is the one the backstop is *for*, and the only one it fully answers |
| **Displacement** — a value genuinely on the page, belonging to someone else | a payment provider's USt-IdNr; a hosting company's address in a privacy notice; a JSON-LD vendor identifier | **partly.** The substring check *passes* — the string is there. §5.3's `provider_block` anchor and M1.78's visible-text ruling reduce the surface; neither eliminates it |
| **Mis-attribution across companies** — a correct value assigned to the wrong company | batch results return in **arbitrary order** (M1.51) | `custom_id`, and **only** `custom_id`. Substring verification is blind here: the values are genuinely present on the page they came from |
| **Wrong document** — the right extraction from the wrong page | `snocks.com` artifact 265 is the homepage, stored as `kind='impressum'` | the selection rule (A2 item 9, M1.43/M1.44), not the verification. A name in the homepage footer would verify |
| **Inference error** — the page read correctly, the conclusion wrong | *"unsere eigene Marke"* in a sentence about a resold brand | **nothing.** See 6.3 |
| **Silent truncation** — the answer is absent from the *sent* text, not the page | 60 KB cap, cut from the end | nothing, and it is why the cap cuts from the end: Impressum content is near the top. Unobserved on this corpus (largest page 21 KB) |

### 6.2 Which §5.5b outputs are substring-verifiable, and which are not

**Verifiable — the value is a string quoted from the page:**
`legal_name`, `managing_directors`, `owner_name`, and the two `_evidence` spans.
For these the verified string **is** (or directly contains) the scored value.

**Not verifiable — the value is a judgement with no string in it:**
`owner_named_on_site` and `own_brand`. Also, in a weaker sense,
`one_line_offer`, `audience` and `product_categories`, which are *summaries*: a
summary is not on the page by construction, and no substring check applies to
one at all.

**The arithmetic that makes this matter.** The two booleans carry
`site.owner_named` at **+15** and `brand.own_brand` at **+10** — **25 points of
ruleset v3**, on a scale whose bands are 20 points wide. Until M1.47/M1.49 they
carried *no verification of any kind*, and the whole design leans on a backstop
that does not reach them.

### 6.3 What the tool does with the ones that are not verifiable

Four mechanisms, in the order they engage. None of them is *trusting the model*.

1. **Verify the adjacent string instead of the value** (M1.49). Each boolean
   carries an `_evidence` span, and the span is substring-checked. **This is a
   strictly weaker guarantee and the spec says so in the same paragraph that
   introduces it**: it proves the model did not fabricate its evidence; it
   cannot catch the model reading the page correctly and inferring wrongly. For
   a name the verified string *is* the scored value; for a boolean it is merely
   *adjacent* to it. `test_the_limit_of_the_guarantee_is_reachable` is that gap
   as a passing test.

2. **Gate firing on verification, not scoring on confidence** (§6.1). A rule
   fires **only** on a value whose span was found. This is A7's third axis used
   one step earlier than A7 usually uses it — classifying the *unverified value*
   rather than the abstention — and the asymmetry is the whole argument: a
   boolean wrongly `true` **awards points that were not earned**, the score reads
   too **high**, and too high is the direction that moves a company toward a
   letter rather than merely mis-ranking it.

3. **Refuse to default** (§6.1, M1.46's shape). A failed verification abstains.
   It does not fall back to `false`, because that is the same defect in the
   opposite direction: *unverified* is not *absent*. Migration 012 is what makes
   this real rather than aspirational — before it, the rejected value was still
   in the read model.

4. **Route it to a person, and say which way the score is wrong while it waits**
   (A7, migration 013). `own_brand_undetermined` and `owner_named_undetermined`.
   Neither blocks contact, because both withhold an *award*.

**What none of this reaches, stated plainly.** Inference error survives all four.
A model that reads the page correctly and concludes wrongly produces a verified
span, a fired rule and a scored company, and nothing in this design will notice.
The bound on the damage is arithmetic rather than logical: 25 points, on rules
that *award*, into a ranking a human reads before anyone is contacted. **The
instrument that would actually measure it is a yield benchmark against
hand-labelled pages, which does not exist and is not this unit's.**

**And one exposure that is not the model's fault at all.** `custom_id` is the
only thing tying a returned legal name to a company (M1.51), and the substring
check cannot see a mis-key, because the value really is on the page it came
from. That failure produces a *correct extraction attributed to the wrong
company* — M1.17's shape — and it would pass every guard above.

---

## 7. Negative control

**Denominator: 589** — the suite on `4beefe4` before this unit. This unit's own
tests are `tests/test_phase2_profile.py`, `tests/test_phase2_gate.py` and
`tests/test_extract_p2.py` (51 tests), plus four test methods rewritten or added
inside `test_score.py` and `test_serve.py`, which are 9a's and are counted as
9a's below.

| guarantee removed | pre-existing tests that could see it | what did fail |
|---|---|---|
| A4's confidence predicate (012) | **0 of 589** | 3 of 9a's own, plus `test_a_rejected_value_scores_nothing` — a method 9a added to a pre-existing file |
| `phase2_input_settled` enforcement, both arms | **0 of 589** | 2 of 9a's own |
| the `settled` term in `evaluate` | **0 of 589** | 3 of 9a's own |
| the three-state predicates | **0 of 589** | 3 of 9a's own, plus the 2 llm-marking tests 9a rewrote (M1.85) |
| `submit` unclassified in `PAID_SURFACES` | **the import fails; the suite cannot collect** | 7 collection errors |
| `@requires_ledger_clearance` removed, classification kept | **the import fails** | — |
| A2's `own_brand` view column (011/012) | **30 of 589 — a pre-existing test caught it** | `test_the_live_ruleset_is_fully_reachable`, and 29 others |

**The series, and the first non-zero in it:**

| unit | pre-existing tests that could see it |
|---|---|
| Unit 6 | 0 of 537 |
| Unit 7 | 0 of 551 |
| Unit 8c | 0 of 581 |
| **Unit 9a** | **0 of 589 for five of six guarantees; 30 of 589 for the sixth** |

**The sixth is the interesting one and it is not an accident.**
`leadlist.assert_evidence_reachable` parses `company_profile`'s own SQL out of
`sqlite_master` and asserts that every `Rule.reads` entry resolves to a real view
column. It is pre-existing, unmodified by this unit, and it went red the moment
`brand.own_brand`'s column was pulled — **because 9a gave `qual.own_brand` a
`reads` tuple.** A rule declaring what it reads is what made an unrelated
pre-existing check able to see a schema regression. That is the payoff of B6's
*rules as data* arriving three units later, and it is the argument for the
declaration style over inference restated as a measurement.

**The two ledger controls are the strongest result here** and they are not a
count: removing the classification, or the decorator, fails the **import**, so
no test runs at all. That is the property Unit 7 built (M1.71) and 9a is the
first unit with a real caller to exercise it on.

**One control found a real defect in this unit's own first draft**, which is
worth more than the zeroes. `test_confidence_zero_is_red` went red on migration
012 for a reason it was not about: it gave `platform.detected` a synthetic
`method='llm', confidence=0`, the filter removed the value, the rule declined,
and there was no `score_component` for the red evidence to render under. That
exposed a dependency the placeholder had hidden — **§9 renders evidence beneath
components, so a rejected value is visible only if its rule still produces
one** — which is the thing A4's "the loss is visible" argument rests on. It
holds, but *through the abstention*, not independently of it. Filed as M1.85 and
the tests moved onto that path.

---

## 8. Still open

- **The untransmitted audit section** — headed *"LLM-generated/hallucination
  signals"*, missing and not empty. §6 above reconstructs **what it would have
  had to cover for this code**, at the stage that generates the content it is
  about, and that is explicitly **not a closure**: the original text is
  unrecoverable, and a reconstruction written by the implementer is not an
  independent audit of the implementer's work. The canonical membership list and
  its derivation live in §Unit 2a's amendment (M1.73), and **this document is
  now a member**. The count is not carried forward and not computed as
  "five plus one". This section was written first, then the grep was re-run, and
  the number below is what it returned:

  ```
  $ grep -rlniE "LLM-generated ?/ ?hallucination" docs/unit*-findings.md | sort
  docs/unit4-robots-tristate-findings.md
  docs/unit5-portability-and-ci-findings.md
  docs/unit6-address-guard-findings.md
  docs/unit7-cost-ceiling-findings.md
  docs/unit8-m5-prerequisites-findings.md
  docs/unit9a-phase2-scaffolding-findings.md
  ```

  **Six.** Pre-existing membership was confirmed as Units 4–8 before this
  document was added, as the extension rule requires.
- **M1.72 — the batch reservation's two writes are not one transaction.** 9b's,
  by §10.4b's sequencing. `extract_p2.submit` stops immediately before it.
- **B3.2 — the ceiling sums estimates and never actuals.** 9b's; it needs
  `reconcile`.
- **B3.3 — the reconciliation cost-ledger rule.** 9b's; same reason.
- **9c — the first real spend, ≤ 3 companies, only on written authorisation.**
  Not started, and `extract-p2` refuses without `--dry-run` today so it cannot
  start by accident.
- **Inference error in a verified boolean is unguarded** (§6.3). No mechanism in
  this design reaches it; the bound is arithmetic (25 points, on award rules,
  into a list a human reads). The instrument that would measure it is a yield
  benchmark on hand-labelled pages.
- The full register, derived rather than remembered, is §10.

---

## 9. Where the instructions were wrong

**A2 was not open. It was ruled, and filed where the code could not see it.**
The brief says *"Write the mapping … into the spec BEFORE any extraction code"*,
which is the right instruction. What it does not say — and what changes the
work — is that the mapping already existed, ratified on 2026-08-16, all ten
items closed, in `docs/a2-phase2-signal-mapping-proposal.md`. Writing one from
scratch would have produced a second, differently-argued mapping beside a
ratified one: M1.42's shape at the level of a specification. **The work was
transcription plus the one column the proposal lacked (the reader), not
authorship.** Recorded because the difference is between honouring a ruling and
silently re-deciding it.

**A4's citation is stale.** The register points at
`001_initial_schema.sql:168`. The view has been dropped and recreated twice
since — migrations 006 and 007 — so the live definition is 007's. The *claim* is
true at the new address, and would have been true at any of the three. A reader
following the pointer lands on a definition that has not been in force since
M1.39.

**C4's premise does not hold for the stage the brief attaches it to.** The brief
says *"M5 is the first stage to write signals whose evidence is a URL that may
have been redirected"*. `evidence_url` is `artifact.url` — a stored value, taken
off the row in the same expression as `artifact_id`. The redirect happens in
`fetch` and is frozen before extraction runs, so the described failure cannot
occur in an extraction stage. That does not make the decision unnecessary; it
changes what the decision is about, and it is why the answer is *keep it* rather
than *change it*.

**M3 has closed underneath the brief, and underneath the register.** Both treat
the repository's visibility as an open item. Re-derived this unit:
`gh repo view tanmayagrawal24/lead-Portal --json visibility,isPrivate` returns
**`{"isPrivate": true, "visibility": "PRIVATE"}`**. It had returned `PUBLIC` at
every check from Unit 4 through Unit 8. **The row survived five units without
drifting for exactly one reason: its state was a command, not a sentence** — and
that is M1.73's fix arrived at independently, in the one row nobody could report
on without measuring.

**§10.6's `balance_exhausted` row named a migration number that had been taken.**
It read *"migration 010 ships with M5's writer"*. `010` went to
`score.evaluated_on` in Unit 8b, and the lost stash's `010` is unrecoverable.
The row's claim — *not in the schema yet* — was still true; its number was not.
Corrected, and reassigned to 9b with `014` or later.

**Two prose passages were sitting inside §4's `sql` code fences**, one of them
added by M1.74's own amendment in Unit 8b. Cosmetic in a renderer, not cosmetic
in §4, which is the section every schema question gets answered from. Both
fences closed and reopened (M1.84).

**A pre-existing test pinned a migration count.**
`test_a_row_that_predates_migration_010_is_not_backfilled` asserted
`apply_pending(old_db) == [10]`, so it went red on migration 011 for a reason
unrelated to backfilling. Narrowed to `assertIn(10, applied)` — the assertion
under test is that 010 ran over a pre-existing row.

**A stray `package.json` was in the working tree before this unit started** —
`@github/copilot-sdk`, untracked, not this project's — and a `git add -A` swept
it into a commit. Removed in a follow-up commit and both paths gitignored, since
the unit was told to add no dependency and this would have added one in the most
literal sense.

**One judgement call the brief did not name, taken and flagged.** §5.5b left
*what text is sent to the model* explicitly unstated, and §10.2's base rate
depends on it. The entry point cannot be written without answering it, so it is
answered: **cleaned visible text**, which is what §5.5b's own input-preparation
requirement already describes. §10.2's `Inh.`/`Inhaber` base rate is therefore
**1 of 11**, not 3 of 12. §10.2 itself stays open. Filed as M1.78 so it reads as
a decision taken rather than a detail assumed.

---

## 10. Open items register — derived, not remembered

**How this was derived.** M1.73's lesson is that a backlog carried forward
verbatim rots, and Unit 8 proved it by finding four dead rows in the brief's own
list. So every row below was re-checked **this unit** against the artefact that
decides it, and the method is named per row. **No row was taken from a previous
unit's report, including Unit 8's §7.**

| item | state | derived from |
|---|---|---|
| **M3 repository visibility** | **CLOSED — 2026-08-20** | `gh repo view --json visibility,isPrivate` → `{"isPrivate":true,"visibility":"PRIVATE"}`. Open at every check from Unit 4 to Unit 8 |
| **M1.72 transactional reservation** | **OPEN, 9b's** | `grep -rn "BEGIN\|SAVEPOINT" portal/` — no transaction wraps the pair, because the caller still does not exist. `extract_p2.submit` stops immediately before it and its docstring says why |
| **B3.2** ceiling sums estimates, never actuals | **OPEN, 9b's** | `ledger.monthly_spend_usd` sums `run.est_cost_usd` alone; `actual_cost_usd` is written by `reconcile`, which does not exist |
| **B3.3** reconciliation cost-ledger rule | **OPEN, 9b's** | no `reconcile` in `portal/`; `grep -rn "def reconcile" portal/` is empty |
| **9c — first real spend** | **NOT STARTED, needs written authorisation** | `portal extract-p2` without `--dry-run` exits 2 and names 9b and 9c |
| **Untransmitted audit section** | **OPEN — reconstructed here, not closed** | `grep -rlniE "LLM-generated ?/ ?hallucination" docs/unit*-findings.md` → six files. §6 above is a reconstruction by the implementer, not the transmitted text |
| **A1** `PHASE2_MAX_POINTS` / gate no-op | **CLOSED** | `grep -rn PHASE2_MAX_POINTS portal/` → one comment in `ruleset.py` recording it; the per-company gate is `score.evaluate` |
| **A2** Phase-2 outputs have no signal keys | **CLOSED this unit (M1.76)** | mapping table now in §5.5b; `brand.own_brand` → migration 011 view column; `qual.own_brand.reads` is non-empty and `assert_declared`'s exemption is deleted |
| **A3** `agency.footer_credit` two writers | **CLOSED this unit (M1.77)** | ruled as two keys with one reader; `test_no_rule_reads_an_unscored_hint` enforces it against `Rule.reads` |
| **A4** no confidence filter into scoring | **CLOSED this unit (M1.79)** | migration 012's `WHERE o.confidence IS NULL OR o.confidence > 0`, applied after `current_run`; the register's `001:168` pointer was stale, the live view was 007's |
| **C4** `uq_signal_identity` includes `evidence_url` | **DECIDED this unit — unchanged, with its cost recorded (M1.80)** | 2,404 rows / 2,404 distinct triples / 0 multi-URL groups; `evidence_url` is `artifact.url`, frozen before extraction |
| **B1** brief export fail-loudly vs omit | **OPEN** | M7 not started; §8's predicate still unstated |
| **B2** `needs_review_reason` is one column | **CLOSED** | `review_flag` with a `CHECK` vocabulary; migration 013 takes it to twelve reasons |
| **B3.1** reconcile vs submitting run | **CLOSED** | B4's ruling; `run.started_at` keys the window |
| **B4** `run_id` for reconciled signals | **CLOSED** | settled with B3.1 — the submitting run |
| **B5** ruleset version inconsistent | **CLOSED in the code** | `ruleset.RULESET_VERSION = "v3"`, single source |
| **B6** ruleset representation undefined | **CLOSED, and it paid a dividend this unit** | `Rule` carries `reads`, `points`, `phase2_reachable` and now `phase2_input_settled`. §7's control 6 is the dividend: a pre-existing reachability test caught a schema regression *because* rules declare what they read |
| **B7** `own_domain_shop` has no predicate | **CLOSED; residual is §10.3's** | `ruleset._own_domain_shop`, ≥ 5 product URLs |
| **C1** blog ladder scores a healthy new blog | **OPEN** | chain order unchanged in `ruleset.RULES`; declaration order is evaluation order |
| **C2** one failed search costs `opp.ai_invisible` | **OPEN, untestable** | `_ai_invisible` reads `ai_queries_checked`; M6 not started, so no failure has been observed |
| **C3** M7 blocked transitively on M6 | **PREMISE DISSOLVED** | §10.5: M6 unblocked (M1.54). M7's remaining blocker is **B1** |
| **§10.5 DNS-rebinding residual** | **OPEN, UNOBSERVED, labelled not fixed** | §10.5; closing it needs a pinning `httpx` transport, refused under M1.4 |
| **§10.5 address guard's architecture limit** | **OPEN and uncloseable by design** | §10.5 — a public address proxying to something internal is invisible to an address classifier |
| **§10.3 ban on calibrating §6.5** | **STANDING** | §10.3; nothing in this unit touches a weight or a band. `band_of`'s docstring still carries the ban |
| **§10.3 "when is a written count untrustworthy"** | **OPEN, no new evidence** | §10.3's closing paragraph; no few-product shop has been observed |
| **§10.2 `owner_operated` lever** | **OPEN — but its instrument is now fixed (M1.78)** | §10.2; the base rate is **1 of 11** on visible text, which is what `extract-p2` sends. Settling it still needs a larger corpus |
| **§10.1 blockers** | **EMPTY** | §10.1 renders an empty table |
| **60 KB input cap** | **UNOBSERVED on this corpus** | `extract-p2 --dry-run`: largest cleaned page 21,181 bytes against 61,440. Tests cover it; real bytes have not |
| **Same-run two-document signal** | **UNOBSERVED, labelled** | 0 groups in 2,404 rows; the `(observed_at DESC, id DESC)` tiebreak is untested against reality |
| **Inference error in a verified boolean** | **OPEN, unguarded, bounded arithmetically** | §6.3 above; `test_the_limit_of_the_guarantee_is_reachable` passes, which is the point |

**Two rows moved that Unit 8's register had as OPEN**, and neither moved because
someone remembered: **M3** closed because the command was re-run, and **A2/A3/A4**
closed because this unit did the work. **C4 is decided rather than closed**, which
is a third state the register did not previously have and now does.

---

## 11. Verification

All runs with `data/` moved aside and `ANTHROPIC_API_KEY` unset.

| check | result |
|---|---|
| `4beefe4` merged main, baseline | 589 passed, 2 skipped, 126 subtests |
| 9a complete | **641 passed, 2 skipped, 139 subtests** |
| negative control × 4 (filter, assertion, term, three states) | 0 of 589 pre-existing in each |
| negative control × 2 (ledger classification, decorator) | **import fails; 7 collection errors** |
| negative control (A2 view column) | 30 of 589 pre-existing, led by `test_the_live_ruleset_is_fully_reachable` |
| `ruff check .` | clean |
| `ruff format --check .` | clean |
| `audit-politeness` healthy corpus | exit **0**, §5.2 HELD |
| `audit-politeness` breached corpus | exit **1** |
| `extract-p2 --dry-run` on the stored corpus | 9 sent, 4 skipped, 0 requests, 0 spend |
| `extract-p2` without `--dry-run` | exit 2, naming 9b and 9c |

**Size against the stop condition.** 2,110 insertions across 16 files, of which
182 are spec prose and 688 are this unit's three new test files. Unit 7 was 1,244
across 9. This is larger, and the reason is that A2 and A4 were **blocking** —
neither could be deferred without leaving M5 to write signals with no keys and
score values the tool had rejected — so the brief's own ordering ("A2 and A4
come first") was followed and the scaffolding was sized to fit around them
rather than the other way round. **What was deliberately not built, with its
measurement, is §8 and §10**: the reservation caller (M1.72), reconcile (B3.2,
B3.3), and any real spend (9c).
