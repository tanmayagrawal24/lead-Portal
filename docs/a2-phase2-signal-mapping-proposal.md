# A2 — signal keys for the Phase-2 extractions

**Status:** proposal. No code. For ratification before M5.
**Resolves:** A2, open since the first review.
**Sections it would change:** §4 (schema — one migration, view rebuild), §5.3, §5.5b, §6.1, §9, §10.4.

§5.5b defines `ImpressumExtract` and `HomepageExtract` as Pydantic models and
stops there. Nothing maps their nineteen fields to `signal.key` values, to
`contact` columns, to `company` columns, or to `company_profile`. This proposes
that mapping in full, and the four decisions it turns out to force.

---

## 1. What the gap costs today, measured

**`qual.own_brand` (+10) is a live scoring rule that cannot be scored.** In
`portal/ruleset.py` it is declared `reads=()` with an evaluate function that
unconditionally returns `declines()` — not because the answer is no, but because
no signal key exists to read. It is `phase2_reachable=True`, so its +10 is in
every company's `remaining_upside`.

That is 10 of the 50-point upside every unbanked company carries, and on the
verified corpus it changes an admission:

| domain | Phase-1 total | upside | admitted | admitted without `own_brand`'s +10 |
|---|---:|---:|---|---|
| germanelectronic.de | 5 | 50 | **yes** (55 ≥ 55) | **no** (45 < 55) |
| propellerdiscount.de | 0 | 50 | no | no |
| *(the other eleven)* | 17–73 | 50 | yes | yes |

`germanelectronic.de` advances to Phase 2 **solely** on a rule that will decline
when Phase 2 gets there, because the field it needs has no destination. §5.4's
gate is meant to be conservative and this is not the gate being wrong — it is
the gate keeping a promise the schema cannot keep. A2 is what makes it good, and
that is the argument for doing it before M5 rather than during.

**`one_line_offer` has the same shape without the points.** §5.5c derives its two
category queries from it and §9 lists it as a table column. It has no key, no
column, and no destination, so M4 substituted a *hook* derived from the
highest-scoring `opp.*` component. That substitute is honest but it is not what
§9 specifies, and §5.5c cannot be built at all until this field lands somewhere.

**Three `company` columns are dead in the UI.** Across all 13 companies:
`legal_name` 0/13, `city` 0/13, `postal_code` 0/13, `country` 0/13 populated. §9's
*Ort* column renders `–` on every row and M4's country filter has an empty option
list, because the only writer today is the optional seed-CSV columns and
`seeds/candidates.csv` carries `domain` alone. `ImpressumExtract` fills exactly
these four.

---

## 2. The rule the mapping follows

**Everything an extraction produces is written as a `signal` first.** The signal
row is the only place that carries `method='llm'`, `confidence`, `evidence_url`
and `artifact_id`. §9 requires LLM-derived fields to be marked and
`confidence = 0` to render red; §5.5b requires substring verification to be
recorded rather than merely applied; M1.42 requires the citation to come off the
artifact the value was read from. **A bare `company.*` column carries none of
those four columns**, so a value that lands only there is a value with no
provenance, no verification state, and no way to be shown as unverified.

This is not hypothetical: `company.legal_form` is written today by `extract-p1`
as a direct `UPDATE` with no signal behind it (`portal/extract.py`,
`_legal_form`). It works because that path is deterministic and its reliability
is documented (§5.3: 7/12, 0 false positives). The moment §5.5b writes the same
column, the value becomes LLM-derived and the UI has no way to know.

So, three destinations rather than the four the question posed, plus a demotion:

| destination | what goes there | why |
|---|---|---|
| **`signal`** | every extracted field, without exception — **except a natural person's name** | provenance, confidence, verification state, M1.42 |
| **`contact`** | natural persons' names, e-mail, phone, full postal address | §8: personal data in one table so `purge` is a single `DELETE` |
| **`company`** | a *projection* of a signal, where a column already exists and the UI or a filter reads it | display and filtering; never the authority |
| *(demoted)* | `HomepageExtract.agency_credit` → an unscored hint | §3 below |

**A person's name is never written to `signal`.** §8's whole design is that
personal data lives in `contact` "so GDPR purge is a single DELETE". A signal
row holding *Dominik Lindemeier* would sit outside the table `portal purge`
targets and would survive the 12-month expiry. The schema already anticipated
this: the view's Impressum columns are `gf_count` (a number) and `owner_named`
(a boolean), not names. A2's job is to say so explicitly.

---

## 3. `ImpressumExtract` — thirteen fields

`source_url` for every `contact` row is the Impressum URL, per §4. Every signal
below is `method='llm'`, `evidence_url` and `artifact_id` from the Impressum
artifact the text was read from (M1.42).

| field | destination | key / column | scored? | notes |
|---|---|---|---|---|
| `legal_name` | signal **+** `company.legal_name` | `impressum.legal_name` (text) | no | Substring-verified (§5.5b). On failure: signal written with `confidence=0` **and the rejected value in `value_text`**, `company.legal_name` left untouched. A company name is not personal data, so keeping the rejected string is safe and is what makes §9's red row actionable — a red row with no value tells the operator nothing to check. |
| `legal_form` | signal **+** `company.legal_form` | `impressum.legal_form` (text) | **yes**, via `qual.owner_operated` disjunct 1 | Second writer on a column `extract-p1` already writes. See §6 for how precedence is resolved without a race. |
| `street` | **`contact.postal_address`** only | — | no | The identifying part of the address. Assembled with PLZ and city into the one column §4 provides. Never a `company` column. |
| `postal_code` | signal **+** `company.postal_code` | `impressum.postal_code` (text) | no | Company-level per §8. The signal exists so a disagreement with a seeded value is *visible* rather than silently resolved. |
| `city` | signal **+** `company.city` | `impressum.city` (text) | no | Fills §9's dead *Ort* column. |
| `country` | signal **+** `company.country` | `impressum.country` (text) | no | Fills M4's empty country filter. `company.country` has a `CHECK (DE/AT/CH)`, so a fourth value must be written to the signal and **not** to the column, rather than failing the write — `doonails.de` is a Cyprus Ltd (§5.3) and CY is a real answer. |
| `managing_directors` | **`contact`** rows, one per name, `role='Geschäftsführer'` **+** signal (count only) | `impressum.gf_count` (num) — *view column exists* | **yes**, disjunct 2 | Names substring-verified; an unverified name creates **no contact row** and is written nowhere. See §5 for the `gf_count = 0` defect this mapping has to avoid. |
| `owner_name` | **`contact`** row, `role='Inhaber'` **+** signal (presence only) | `impressum.owner_name_present` (num 0/1) | **no — deliberately** | This is §10.2's lever and §10.2 is not mine to close. Extract it, record it, score nothing on it. Recording it is what makes §10.2 decidable by measurement on the next corpus instead of by argument; on this one the `Inh.`/`Inhaber` marker appears on **1 of 12** stored Impressum pages, consistent with §5.3's single observation (`lampenflut.de`). |
| `register_court` | signal | `impressum.register_court` (text) | no | For the brief: evidence the company is a real registered entity. |
| `register_number` | signal | `impressum.register_number` (text) | no | As above. |
| `vat_id` | signal | `impressum.vat_id` (text) | no | **Open question 4** — for an Einzelunternehmen a USt-IdNr is arguably tied to a natural person, and as a signal it sits outside `purge`'s reach. |
| `email` | **`contact.email`** only | — | no | Personal data. |
| `phone` | **`contact.phone`** only | — | no | Personal data. |

Plus one qualifier, which is not a model field:

| | | | | |
|---|---|---|---|---|
| *(stage fact)* | signal | `llm.impressum_extracted` (num `1`, `value_text` = model id) | no | So "the extraction ran and found nothing" is distinguishable from "the extraction never ran". This is `content.blog_search_exhaustive`'s idiom (M1.14) and it is needed for the same reason: every A7 guard works by *not writing*, and the read model cannot tell an absence from a silence without a positive fact beside it. Recording the model id also makes the yield benchmark (multi-provider proposal §7) computable from the database. |

## 3b. `HomepageExtract` — six fields

Evidence is the homepage artifact.

| field | destination | key / column | scored? | notes |
|---|---|---|---|---|
| `one_line_offer` | signal **+** view column | `offer.one_line` (text) | no | §5.5c's query input and §9's table column. Needs a view column despite being unscored — see §4. |
| `product_categories` | signal | `offer.product_categories` (`value_text` pipe-separated, `value_num` = count) | no | Pipe rather than comma: a German category name may contain a comma. Count in `value_num` mirrors `content.blog_post_dates`. §5.5c's second query input. |
| `audience` | signal | `offer.audience` (text: `b2c`/`b2b`/`both`) | no | No rule reads it and none is proposed. Useful in the brief; no view column. |
| `owner_named_on_site` | signal | **`site.owner_named`** (num 0/1) — *view column `owner_named` exists* | **yes**, disjunct 3 | **The existing key name is wrong.** The view names it `impressum.owner_named`, but §6.1's third disjunct is "owner named **on site**" and the value comes from the homepage. A key prefixed `impressum.` whose `evidence_url` is the homepage asserts a provenance the value does not have — M1.42's defect in miniature. The key has never been written, so this is a rename with no data to migrate. The **view column stays `owner_named`**, so no rule changes. |
| `own_brand` | signal **+** view column | `brand.own_brand` (num 0/1) | **yes** — `qual.own_brand` (+10) | Written only when the model returns `true` or `false`. A `null` — the page was read and the answer could not be determined — writes nothing, and `llm.homepage_extracted` is what distinguishes that from Phase 2 never having run. See §5. |
| `agency_credit` | signal, **unscored hint** | `agency.footer_credit_llm` (text) | **no — recommended demotion** | §3c. |

| | | | | |
|---|---|---|---|---|
| *(stage fact)* | signal | `llm.homepage_extracted` (num `1`, `value_text` = model id) | no | As above. Load-bearing for `qual.own_brand`. |

### 3c. Why `agency_credit` is demoted rather than mapped

`agency.footer_credit` already exists, is written deterministically by
`extract-p1`, and feeds `neg.has_agency` at **−20** — the second-largest weight
in ruleset v3. Mapping the LLM field to the same key or the same view column
gives one scored input two writers with no stated precedence, which is the
defect class this project has now found four times.

The specific hazard is §10.4's, already paid for once: *"powered by"* is both an
agency signature and the default JTL footer credit, and reading it wrongly fired
`neg.has_agency` against `smoke2u.de` for its choice of shop system. The
deterministic parser carries an explicit platform-vocabulary exclusion list —
JTL, Shopify, WooCommerce, WordPress, Shopware, Magento, PrestaShop, Gambio,
OXID, plentymarkets — and re-stating that list inside a prompt is neither
verifiable by substring check nor testable without spending money.

**Recommendation:** keep the field in the model (the footer is already in the
input; removing it saves nothing) and write it to `agency.footer_credit_llm`,
which **no §6 rule may read** — the same treatment §5.3 gives
`content.blog_lastmod_hint`. Revisit when the yield benchmark can measure
agreement between the two instruments on the stored corpus, at which point the
choice is a measurement rather than a preference.

---

## 4. `company_profile` — the columns this needs

One migration, rebuilding the view (a view has no state, so DROP and CREATE).

**Required, because a rule reads them:**

| column | source | why |
|---|---|---|
| `own_brand` | `brand.own_brand` → `value_num` | `qual.own_brand` currently reads nothing. |
| `homepage_extracted` | `llm.homepage_extracted` → `value_num` | Lets `qual.own_brand` decline before Phase 2 and abstain after it. |
| `legal_form` | **changed**: `COALESCE(MAX(CASE WHEN l.key='impressum.legal_form' …), c.legal_form)` | §5.5b already rules that the LLM wins on disagreement. §6 explains why the precedence must live here. |

`gf_count` and `owner_named` already exist and need no change — only their
writers arrive.

**Required for §9, though nothing scores them:**

| column | source |
|---|---|
| `one_line_offer` | `offer.one_line` → `value_text` |

The view's own comment says to add a column "when a new signal key enters the
scoring rules". That is a floor, not a ceiling: M4's `leadlist` already reads
`platform`, `blog_last_post` and `ai_*` off this view for display, and adding a
one-off signal query beside it for a single display field would give the page
two read paths. **Judgement call, flagged for ratification** — the alternative
is that `leadlist` reads `offer.one_line` from the signal table directly.

**Deliberately not added:** `impressum.legal_name`, `impressum.city`,
`impressum.postal_code`, `impressum.country`, `impressum.register_*`,
`impressum.vat_id`, `impressum.owner_name_present`, `offer.audience`,
`offer.product_categories`, `agency.footer_credit_llm`. Nothing scores them, and
the projections that the UI needs land on `company` columns the page already
reads. They stay queryable via the signal table, as the view's comment provides
for.

---

## 5. Two mapping decisions that stop an existing rule from misfiring

Neither is a predicate change. Both are decisions about **what a key means**,
which is exactly what A2 is for — and in both cases the obvious mapping would
make a live rule fire wrongly the first time Phase 2 runs.

### `impressum.gf_count = 0` would fire `qual.owner_operated` (+15) on an absence

`_owner_operated` reads `if directors is not None and directors <= 2: fires`.
Map `len(managing_directors)` straight onto the key and an Impressum that names
no Geschäftsführer at all writes `0`, `0 <= 2` holds, and the rule fires with the
reason *"Das Impressum nennt 0 Geschäftsführende – eine überschaubare
Führungsstruktur"*. §6.1's predicate is "Impressum names **≤ 2 natural-person
Geschäftsführer**", and naming none is not naming ≤ 2.

Measured: a `Geschäftsführer` label appears on **5 of 12** stored Impressum
pages. The other seven would each take a wrong +15 — and `qual.owner_operated`
is one of two rules whose banking raises a company's effective Phase-2 threshold
from 5 to 20 (§7.1), so the error propagates into the spend model.

**Proposed mapping:** `impressum.gf_count` is written **only when the page names
at least one** natural-person Geschäftsführer. An Impressum naming none leaves it
unwritten, and `llm.impressum_extracted` records that the page was nonetheless
read. This keeps §6.1's predicate exactly as ratified, and it is coherent with
the intent: a sole trader has no Geschäftsführer and should be caught by disjunct
1 (`legal_form`) or disjunct 3 (owner named on site), not by counting zero of
something.

*The alternative* — write `0` and change the predicate to `1 <= directors <= 2` —
is a predicate change and therefore not mine. It is the cleaner expression of the
same intent and worth ruling on; I have proposed the mapping-side fix because it
needs no rule edit.

### `own_brand = null` after Phase 2 is an abstention, not a decline

Today `_own_brand` returns `declines()` unconditionally, which is correct while
Phase 2 has not run: a rule whose turn has not come has not abstained, and
abstaining would fill the review queue with "Phase 2 has not run yet" for every
company. `_ai_invisible` and `_slow_site` already establish this convention.

Once the extraction has run and returned `null` — the model read the homepage and
could not tell whether this is a manufacturer or a reseller — that is A7's shape
exactly: a measurement exists, it cannot support the rule, the rule must fire in
neither direction, the reason must be written, and a human must be told.

`llm.homepage_extracted` is what makes the two distinguishable. The direction of
error is **too low** (a withheld +10 award), so it is an ordinary queue item and
blocks nothing — A7's third axis, checked.

**This needs a §6.4 review reason and a migration.** Proposed:
`own_brand_undetermined`. Not folded into an existing reason: it sends a person
to the homepage to answer *"do they make this or resell it"*, which is not the
question any of the nine current reasons asks.

---

## 6. Precedence — who wins when two writers target one field

Four fields now have more than one possible writer, and none has a stated rule.
Left unstated, the winner is whichever stage ran last, which means **re-running
`extract-p1` after `reconcile` would silently revert an LLM value to a regex
one** — a free, idempotent, encouraged operation quietly undoing paid work.

Two different situations, two different answers:

**Automated versus automated → the better instrument wins, resolved in the view.**
`legal_form` has both `extract-p1`'s regex (§5.3, A1) and `ImpressumExtract`.
§5.5b already rules the LLM wins. The mechanism matters: if extract-p2 also
`UPDATE`s `company.legal_form`, the ruling is implemented as a race. Instead the
LLM value is a **signal**, and the view resolves precedence in one expression:

```sql
COALESCE(MAX(CASE WHEN l.key='impressum.legal_form' THEN l.value_text END),
         c.legal_form) AS legal_form
```

One expression, in the place that already owns latest-wins resolution, and
`extract-p1` re-running cannot revert it because it never touches the signal.

**Human versus automated → the human wins, fill-if-NULL.**
`legal_name`, `city`, `postal_code`, `country` can come from the seed CSV
(operator-typed, first-write-wins today via `ON CONFLICT DO NOTHING`), later from
`discover`'s Places `formattedAddress` (§5.1), and from `ImpressumExtract`. An
operator typing a value into a seed file has made a decision; an extraction
should not overwrite it. So: **seed > Impressum > Places**, and the extraction
fills the `company` column only where it is `NULL`. The extracted value is
written to its signal regardless, so a disagreement is visible in the row
expansion rather than resolved out of sight.

*Places last, not first:* the Impressum is the company's own legally-required
statement of its address; `formattedAddress` is Google's record of a place.

---

## 7. Which artifact the extraction reads, and cites

Not a detail. There are **five or six stored `impressum` artifacts with HTTP 200
per company** — one content hash per crawl, all on the same URL for 10 of 12
companies, two distinct URLs for the other two (`smoke2u.de`, `snocks.com`, where
different probes in §5.2's order succeeded on different runs).

§5.5b keys extraction to `artifact.content_hash`, which implies per-snapshot, and
M1.42 requires the signal to cite the artifact the value was read from. So
extract-p2 must **state which snapshot it sends** rather than letting a
`LIMIT 1` decide.

**Proposed:** the newest 200-with-body artifact of that kind, by `artifact.id`.
Rationale: `id` is monotonic, so it is the most recent successful fetch; the
content-hash short-circuit (§7 control 6) then means an unchanged page is never
re-sent. Where two URLs exist, the newest still wins — the alternative, preferring
a particular probe path, would re-open §5.2's ordering for no measured gain.

I found this while measuring §5 above: parsing the arbitrary `GROUP BY` pick
versus the newest artifact gave different results on four of the twelve pages
(HRB 6→4, Amtsgericht 4→2, USt-IdNr 12→10, e-mail 12→10). Which snapshot is read
changes the answer, so the choice has to be written down.

---

## 8. Phase-1-derivable, versus what genuinely needs the LLM

Measured over the twelve stored Impressum pages, newest snapshot each, using
`parsers.visible_text` and the existing `parsers.provider_block`. **These are
pattern-presence counts, not extraction accuracy** — a USt-IdNr shape in the page
may belong to a payment provider, an e-mail may be in a cookie policy. Labelled
as observations, per §10.4, and not as a claim that a parser would work.

| candidate | pattern present | verdict |
|---|---:|---|
| provider block locatable | 10/12 | the anchor §5.3 already relies on |
| `legal_form` | — | **already Phase 1** (§5.3/A1: 7/12, 0 false positives) |
| `agency_credit` | — | **already Phase 1** (`agency.footer_credit`) |
| PLZ + Ort inside the provider block | 9/12 | **strong Phase-1 candidate, unmeasured for accuracy.** Would fill §9's dead *Ort* column and M4's empty country filter at zero cost. Worth measuring before M5 — see §10. |
| USt-IdNr shape | 10/12 | Phase-1 candidate, unmeasured. Highly regular format. |
| HRA/HRB number | 4/12 | Phase-1 candidate. Low base rate — most of the corpus is not a registered *Handelsgesellschaft*. |
| Amtsgericht | 2/12 | as above. |
| labelled `Tel`/`Telefon` | 8/12 | Phase-1 candidate, but obfuscation (`info [at] …`, image phone numbers) is common and unmeasured here. |
| e-mail shape | 10/12 | as above, and prone to false positives from privacy-policy contacts. |
| `Inh.`/`Inhaber` marker | **1/12** | §10.2's lever. Consistent with §5.3's single observation. Not enough to decide anything on, which is what §10.2 already says. |
| `Geschäftsführer` label | 5/12 | the measurement behind §5's `gf_count` decision. |

**Genuinely needs the LLM — five fields:**

- `legal_name` — §5.3 states the reason: a personal name standing where a company
  name would be is a judgement, not a regex.
- `managing_directors`, `owner_name` — name extraction from free-form German
  prose. These are exactly the fields substring verification exists to guard.
- `one_line_offer`, `audience`, `product_categories`, `own_brand` — judgements
  about what a shop sells and to whom, from a homepage.

**The framing worth keeping:** if the Phase-1 candidates above hold up, the
irreducible LLM job in `ImpressumExtract` is precisely its three
personal-data fields — the three that are also the dangerous ones, and the three
§5.5b's substring verification already names. That is a tidy alignment and it
argues for measuring the deterministic candidates before M5 rather than after:
every field moved to Phase 1 is a field the LLM cannot hallucinate.

It does **not** reduce the number of LLM calls or the token count — the same two
pages are sent either way — so this is a correctness argument, not a cost one.

---

## 9. What this proposal deliberately does not decide

- **§10.2** — whether `qual.owner_operated` should admit an `Inh.`/`Inhaber`
  marker. `impressum.owner_name_present` is written and scored by nothing,
  specifically so the decision can be made on measurement later.
- **§6.1's qualification block** — untouched. No weight, threshold or predicate
  is changed by anything above; §5's two items are mapping decisions chosen
  because they need no predicate edit.
- **§5.5c / AI-visibility** — out of scope per the multi-provider proposal §3.
  `offer.one_line` and `offer.product_categories` are mapped because §5.5c needs
  them, not because any of §5.5c is being designed here.
- **Model choice** — the yield benchmark answers it (multi-provider proposal §7).

## 10. For ratification

1. **The mapping in §3 and §3b as written**, including the three-destination rule
   and the prohibition on a person's name in `signal`.
2. **`impressum.gf_count` written only when ≥ 1 Geschäftsführer is named** (§5) —
   or, if preferred, `0` is written and §6.1's predicate becomes
   `1 <= directors <= 2`, which is the cleaner expression and is a predicate
   change the spec owns.
3. **`impressum.owner_named` renamed to `site.owner_named`** (§3b). No data
   exists; the view column name is unchanged.
4. **`impressum.vat_id` as a signal** — accepted, or moved to a new
   `contact.vat_id`? For an Einzelunternehmen the number is arguably tied to a
   natural person, and a signal is outside `portal purge`'s 12-month expiry
   (though `portal forget --domain X` reaches it either way, §8). My weak
   recommendation is `contact.vat_id`, on the same conservative reading that put
   the street address there.
5. **`HomepageExtract.agency_credit` demoted to an unscored hint** (§3c).
6. **`own_brand_undetermined` as a tenth §6.4 review reason** (§5), non-blocking
   for contact.
7. **`one_line_offer` as a view column** despite being unscored (§4) — or read
   from the signal table by `leadlist` instead.
8. **Precedence: seed > Impressum > Places for address fields; LLM > regex for
   `legal_form`, resolved by `COALESCE` in the view** (§6).
9. **Newest-artifact-by-id as the extraction input** (§7).
10. **Measure the PLZ + Ort candidate before M5** (§8). It is free, it fills two
    dead UI affordances, and it would be the second field after `legal_form` that
    Phase 1 takes off the LLM's plate. Not a blocker for the mapping above.
