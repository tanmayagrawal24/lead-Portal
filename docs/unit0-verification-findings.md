# Unit 0 — verifying the inherited state

**Status:** findings. No code. Two new defects filed as M1.44 and M1.45.
**Verified against:** `origin/main` at `f1aed5b`, corpus as stored, run 34/35.

The handoff this unit was given asserted two facts about the state and asked for
both to be checked before anything was assumed. **Both are false**, in the
direction that matters: the previous session's work was committed and pushed, and
the corpus was never lost. Nothing needed rebuilding. What follows is what the
inherited claims look like when re-measured against the data they were drawn
from, plus two defects that verification turned up.

---

## 1. The two inherited facts

| asserted | actual |
|---|---|
| `origin/main` is at `1a08a33` | **`f1aed5b`** — two commits further on |
| The A2 proposal was completed and **never committed**; nothing on disk; its field-by-field table is lost; do not look for `docs/a2-*.md` | **`docs/a2-phase2-signal-mapping-proposal.md` is committed** (`830e2ab`), 438 lines, with the full field-by-field table for all nineteen fields intact |
| M1.43 needs filing | **Already filed** (`f1aed5b`), in the spec's amendment table and in the proposal's §7 |
| `data/` does not exist; the 13-domain corpus was not preserved; rebuild with `portal fetch` | **`data/` is intact** — 137 MB, 13 companies, 556 artifacts, 2 404 signals, 117 scores, 645 score components, 18 review flags, 35 runs |

The handoff was written against a working tree that had lost two commits' worth
of context. The commits themselves were pushed, which is the practice working
exactly as intended — the record survived the session that made it.

Also confirmed as stated: 410 tests pass (plus 2 skipped, 66 subtests); seven
commands; **zero LLM code** — `anthropic` is declared in `pyproject.toml` and
imported nowhere, and `llm_batch` exists only as a table in migration 001 with no
writer and 0 rows.

### Judgement call: I did not re-crawl

The instruction to run `portal fetch` rested entirely on the corpus being gone.
It is not, and it is *the same corpus A2 was measured on*, which makes it a
strictly better basis for verifying A2's claims than a fresh crawl would be. A
fourth crawl would add ~130 artifacts, move every "newest artifact by id"
selection A2 §7 depends on, and re-open the measurements the proposal rests on —
while making 700-odd requests to thirteen live third-party sites for no stated
need. Offered, not taken; it is a one-command decision whenever it is wanted.

Consequence to state plainly: everything below describes the corpus **as of the
third crawl (2026-08-16)**. No claim here is about the sites as they are today.

---

## 2. A2's scoring claims — reproduced exactly

**`qual.own_brand` is as described.** `portal/ruleset.py:492` declares
`Rule("qual.own_brand", 10, "6.1", (), True, _own_brand)` — weight +10,
`reads=()`, `phase2_reachable=True` — and `_own_brand` returns `declines()`
unconditionally. `assert_declared` (`ruleset.py:610`) carries a **named
exemption** for it: every other rule reading no signal is a startup error. The
gap is not merely present, it is already acknowledged in code.

**The admission it changes is real, and the margin is exactly zero.** Run 34,
with `gate.remaining_upside` and `gate.phase2_admitted` read from the signal
table:

| domain | Phase-1 total | upside | ceiling | admitted | ceiling without `own_brand`'s +10 |
|---|---:|---:|---:|:--:|---:|
| germanelectronic.de | 5 | 50 | **55** | yes | **45 — stops** |
| propellerdiscount.de | 0 | 50 | 50 | no | 40 — stops either way |
| *(the other eleven)* | 17–73 | 50 | 67–123 | yes | ≥ 57 — unaffected |

55 is the B floor. `germanelectronic.de` clears the gate by nothing at all, on the
one rule that cannot fire. **12 of 13 admitted** — the figure §10.2 is about.

**The four dead columns are dead.** Across all 13 companies: `legal_name` 0,
`city` 0, `postal_code` 0, `country` 0. `legal_form` is 7/13, written by
`extract-p1`'s regex. §9's *Ort* column and M4's country filter have nothing to
read, as A2 says.

---

## 3. A2 §8's pattern table — 8 of 9 rows reproduce

Re-measured with hand-written patterns over `parsers.visible_text`, newest
200-with-body Impressum per company, 12 companies (`ekomia.de` has no 200
Impressum at all — five 404s and one robots refusal).

| candidate | A2 §8 | re-measured | |
|---|---:|---:|---|
| provider block locatable | 10/12 | 10/12 | ✓ |
| PLZ + Ort inside provider block | 9/12 | 9/12 | ✓ |
| USt-IdNr shape | 10/12 | 10/12 | ✓ |
| HRA/HRB number | 4/12 | 4/12 | ✓ |
| e-mail shape | 10/12 | 10/12 | ✓ |
| labelled `Tel`/`Telefon` | 8/12 | 8/12 in-block | ✓ |
| `Geschäftsführer` label | 5/12 | 5/12 | ✓ |
| `Inh.`/`Inhaber` marker | 1/12 | 1/12 | ✓ |
| `Amtsgericht` | 2/12 | **3/12** | ✗ |

`Amtsgericht` is the one row that does not reproduce, by one page. It is a bare
case-insensitive substring in my instrument and hard to get wrong, so the
difference is most likely in A2's — which **cannot be checked, because the script
behind A2 §8 was never committed.** That is the practice's own lesson arriving
one level down: the proposal was preserved as a deliverable and the instrument
that produced its numbers was not, so eight of its rows are reproducible and one
is merely plausible. Worth fixing by habit, not worth re-litigating the row.

### The `Inh.`/`Inhaber` count is 1/12 or 3/12 depending on an M5 decision nobody has made

§10.2's lever deserved a closer look than a single count, and it has a second
number behind it.

- In **visible text**, the marker appears on **1 of 12** — `germanelectronic.de`.
- In **raw HTML**, it appears on **3 of 12** — `blackpolish.de`, `snocks.com` and
  `germanelectronic.de`. On the two extra domains it occurs *only inside a
  `<script>` block*, which `visible_text` decomposes deliberately and for a
  documented reason (JSON-LD vendor identifiers otherwise land inside the
  provider block and are read as the company's own details).

Neither number is wrong; they answer different questions. Which one §10.2 should
be decided on depends on **what text `extract-p2` actually sends the model**, and
that is an open M5 design decision. If it sends `visible_text` — the natural
choice, and the one every deterministic parser already uses — then the model
cannot see what a human reading the page cannot see either, and **1/12 is the
right base rate**. If it sends raw HTML, 3/12 is, and the model inherits the
JSON-LD contamination §5.3 strips on purpose. Flagged now because §10.2 is meant
to be settled by measurement on the next corpus, and the measurement is
instrument-dependent in a way nobody has recorded.

*One attribution reconciled:* A2 §8 calls the single observation `lampenflut.de`,
citing §5.3. `lampenflut.de` is the host `germanelectronic.de` now redirects to
(M1.18, `docs/p2-moved-domain-proposal.md`) — the seeded domain and the served
host of one shop. Same observation, two names. No discrepancy.

### The guarded selection moves six of these counts up by one

A2 §7, as amended by M1.43, excludes any artifact whose `content_hash` matches a
homepage artifact of the same company. Applied to the corpus, **exactly one
company's selection changes**: `snocks.com`, 265 → 171. M1.43's "exactly one row"
is confirmed independently.

But that one row is a whole company's Impressum, so the §8 table shifts under it:

| candidate | naive newest | M1.43-guarded |
|---|---:|---:|
| provider block locatable | 10/12 | **11/12** |
| PLZ + Ort inside provider block | 9/12 | **10/12** |
| USt-IdNr shape | 10/12 | **11/12** |
| HRA/HRB number | 4/12 | **5/12** |
| `Amtsgericht` | 3/12 | **4/12** |
| e-mail shape | 10/12 | **11/12** |
| `Geschäftsführer` label | 5/12 | **6/12** |
| labelled `Tel`/`Telefon` (in block) | 8/12 | 8/12 |
| `Inh.`/`Inhaber` marker | 1/12 | 1/12 |

**A2 §8's table was measured through the poisoned row**, so its Phase-1 case is
understated everywhere. The PLZ + Ort candidate — A2 §10 item 10, the one it asks
to measure before M5 — is **10/12, not 9/12**, on the selection M5 will actually
implement. And A2 §5's `gf_count` measurement becomes **6 of 12 name at least one
Geschäftsführer, 6 of 12 name none**: the argument for not writing `0` is
unchanged in shape and applies to half the corpus rather than seven twelfths.

Recommendation: **restate A2 §8 and §5's counts on the guarded selection**, since
that is the input M5 is being ratified to use. No conclusion of A2 reverses; every
one of them gets stronger.

---

## 4. M1.44 — the writer was fixed and the bodies stayed (new)

M1.43's general lesson is *a fix to a writer does not repair what the writer
already wrote, and no stage re-reads old artifacts to check*. Checking whether it
had happened more than once, rather than assuming it had not:

**M1.12** records that the crawler fetched pages `robots.txt` disallows — an
allowed Impressum probe redirected onto a disallowed path, on `snocks.com`
(`Disallow: /policies/`) and `smoke2u.de` (`Disallow: /Impressum`), 2 of 13
domains. The guard is correct today: run 2 onward records
`redirect_refused: …` for exactly those URLs. **M1.12's amendment does not say
that the bodies fetched before the fix are still on disk.** They are.

Measured over **all 521 stored 200-with-body artifacts**, each URL tested against
the newest `robots.txt` stored for its own company:

| artifact | domain | kind | bytes | fetched | URL |
|---:|---|---|---:|---|---|
| **171** | snocks.com | impressum | 635 458 | 2026-08-15T11:07:18Z | `/policies/legal-notice` |
| **186** | smoke2u.de | impressum | 366 649 | 2026-08-15T11:07:29Z | `/Impressum` |

Exactly two, both from run 1, both `kind='impressum'`, both HTTP 200 with a
stored body. No artifact fetched after the fix is disallowed. `snocks.com`'s own
`robots.txt` carries `Disallow: /policies/` under `User-agent: *`; the artifact
table holds refusal row 168 and body row 171 for the same URL, three seconds
apart — M1.12's signature verbatim, still sitting there.

**Why this is material now, and not before.** M1.43's guard, applied to
`snocks.com`, selects **171**. So the fix for M1.43 hands M5 a page the tool was
not permitted to fetch, as that company's Impressum, to be read for a legal name
and directors. `smoke2u.de` is not affected — its newest allowed Impressum (274)
is newer than 186, so 186 is never selected — which leaves exactly one material
case and no reason to expect it to be the last.

This is the *third* consecutive selection defect on the same company's Impressum,
each caught only by checking the previous fix against the data: wrong page
(M1.17 → the row it left, M1.43) → a page we may not use (M1.44). The pattern is
not snocks.com being unusual; it is that **no stage validates a stored artifact
against anything, ever** — the artifact table is treated as a record of what was
fetched, and read as if it were a record of what may be used.

**Proposed resolution, for ratification, both in M5:**

1. **Selection excludes any artifact whose URL the company's stored robots
   policy disallows**, checked at selection time against the newest stored
   `robots.txt` — the same structural shape as M1.43's hash check, and for the
   same reason: it catches the class however the row was created.
2. **A one-off repair of artifacts 171 and 186** — bodies that should never have
   been stored. Deleting the body while keeping the row and its `error` is the
   conservative form, since the *request* genuinely happened and §5.2 wants that
   recorded.

Consequence to accept openly: with 265 repaired (M1.43) and 171 repaired
(M1.44), **`snocks.com` has no usable Impressum artifact at all** and routes to
`no_impressum` review — which, per M1.17's own reasoning, is what §5.2's two-step
does with an absence anyway.

---

## 5. M1.45 — `no_impressum` on snocks.com: the premise is wrong, the defect is real (new)

The handoff described this as *an open `no_impressum` flag while two real
Impressum artifacts sat on disk*, and asked for it to be filed as a
queue-correctness item. Checked:

**The premise does not survive.** `snocks.com`'s two 200-with-body Impressum
artifacts are **265** — the homepage, filed as an Impressum (M1.43) — and **171**
— robots-disallowed (M1.44). Neither is a real, usable Impressum. Once both are
repaired the flag is **correct**, and the queue is right for the first time. That
is worth stating plainly rather than filing a defect against a conclusion that
happens to be true.

**Two genuine defects sit underneath it, and neither is what was described.**

**(a) A 429 was read as an absence.** The flag was raised at
`2026-08-15T12:42:11Z` — the same second artifact 361 (`/rechtliches`) returned
**429**, the last of three rate-limit responses in run 4 (`/imprint`, `/legal`,
`/rechtliches`, all 429). `_discover_impressum` (`portal/fetch.py:728-735`) tests
`response is not None and response.ok`; a 429 is not ok, the probe loop falls
through, and `no_impressum` is raised. A 429 is **not** a page that does not
exist — it is a measurement that could not be made, which is A7's shape exactly:
a rule fired in one direction on evidence that cannot support either. 429s are
confined to `snocks.com` (impressum 3, product_page 2, blog_index 1) and
`ekomia.de` (product_page 2), so the corpus has one affected flag — but the
reasoning is wrong everywhere, not just where it changed an answer.

*Direction of error (A7's third axis):* `no_impressum` raises a review flag and
does **not** exclude — verified, `company.excluded = 0` on all 13. So it is a
queue item, blocks nothing, and errs toward more human attention rather than
less. That is the safe direction, and it is why this is a correctness item rather
than an urgent one.

*Contrast that confirms the reading:* `ekomia.de`'s `no_impressum` is **correct** —
five genuine 404s and one robots refusal, no 429 anywhere. The two flags look
identical in the queue and were reached by completely different reasoning.

**(b) §6.4's pipeline clear is specified and unimplemented.** §6.4 says
`resolved_by_human` distinguishes *"`1` for a human dismissal, `0` for a pipeline
clear"*, and migration 001's CHECK admits both. **No code path writes `0`.** The
only writers are `leadlist.resolve_flag` and `serve`, both writing
`resolved_by_human = 1`. So an open flag can only ever be closed by a person.

This is *not* §6.4's stickiness, which the handoff named as the cause. Stickiness
governs **resolved** reasons — *"once a reason has been resolved for a company,
that same reason is never raised for that company again"* — and it is doing its
job. What makes an open flag permanent is that half of §6.4's own resolution
model was never built.

Whether the pipeline *should* clear `no_impressum` on a later successful fetch is
a real question with a real argument on each side, and §6.4's stickiness argument
— *a queue that refills itself stops being read* — cuts against it. **Not decided
here.** What is filed is that the spec describes a mechanism the code does not
have.

**Proposed resolution, for ratification:**

1. **A rate-limited or transport-failed probe is not an absence.** `no_impressum`
   is raised only when the two-step completes with responses that actually
   answer — 404, or a refusal establishing the page is not available to us. Where
   probes were inconclusive, the correct outcome is an abstention with its reason
   written and a human told (§5, A7), not a conclusion.
2. **Absence is judged against the `artifact` table, not against one run's
   responses.** M1.17's amendment already states the principle — *`artifact` is
   the interface M2 reads by kind* — and `_discover_impressum` is the one place
   that reasons about presence without consulting it. Note this interacts with
   M1.44: "does a usable Impressum artifact exist" must mean *usable*, or the
   query re-admits exactly the two rows §4 above removes.
3. **Either implement §6.4's pipeline clear or delete it from §6.4.** A documented
   resolution path with no writer is a claim the tool does not keep.

---

## 6. For ratification

Nothing below is implemented. Items 1–3 amend a proposal already before you;
4–5 are the new defects.

1. **Restate A2 §8's pattern table on the M1.43-guarded selection** (§3 above).
   Six counts rise by one; no conclusion reverses; A2 §10 item 10's PLZ + Ort
   candidate is 10/12, not 9/12.
2. **Restate A2 §5's `Geschäftsführer` measurement as 6/12, not 5/12** (§3 above).
   The `gf_count` argument is unchanged in shape.
3. **Record that §10.2's base rate is instrument-dependent** — 1/12 in visible
   text, 3/12 in raw HTML — and that the M5 decision about *what text is sent to
   the model* determines which one settles §10.2 (§3 above).
4. **M1.44** — robots-disallowed bodies survive the fix that stopped them being
   written; selection must exclude them, and artifacts 171 and 186 need a one-off
   repair (§4 above). Accept that this leaves `snocks.com` with no Impressum.
5. **M1.45** — a 429 is being read as an absence; absence is judged from one run
   rather than from the artifact table; and §6.4's pipeline clear has no writer
   (§5 above). The handoff's stated premise for this item does not hold.

Unchanged and still open, untouched here: §6.1's qualification block, §10.2,
§10.3, §10.5, the Shopware/WooCommerce n=1 seed gap, and the unmeasured LLM
yield.
