# M3 — absent-input audit of the whole ruleset

**Asked for before any rule was written.** A7 is the policy; this applies it once
across §6.1–§6.3 rather than discovering it a sixth time. Every rule, every
signal it reads, and what happens when that signal is **missing**, **zero**, or
**present but unable to carry the rule**.

**Measured on** the stored corpus, `data/portal.db`, extract run 19,
2026-08-16. No requests were made.

---

## 0. How to read the verdict column

| verdict | meaning |
|---|---|
| **safe** | The rule awards points for a *presence*. A missing input means it does not fire, which is the direction that costs nothing it has not already lost. |
| **A7 — guarded** | The rule awards points for an *absence*, and a guard already exists. |
| **A7 — NEW** | The rule awards points for an absence and **nothing guards it today**. Found by this audit. |
| **B7 shape** | The rule has no data path to the population it is supposed to fire on. It reads as implemented and cannot fire, or fires on the wrong set. |

Five instances of the A7 defect were found before this audit, each by accident:
A5.5's unfetched sample, §6.2's NULL date, `catalog_not_measurable`, M1.32's
unbounded date, M1.34's rung-1 abstention. This pass found **three more**, plus
one defect in the read model that resurrects two already-fixed bugs.

---

## 1. §6.1 Qualification

| rule | points | reads | missing | zero / below threshold | cannot carry | verdict |
|---|---|---|---|---|---|---|
| `qual.ecommerce_platform` | +15 | `platform.detected` | no fire | n/a (text) | Shopware 5 emits no signature (M1.11), so "absent" and "invisible to us" are indistinguishable — extract records a *note*, not a signal | **safe**, with a known −15 bias against SW5. Not A7: the rule awards for presence. |
| `qual.owner_operated` | +15 | `company.legal_form`, `impressum.gf_count`, `impressum.owner_named` | no fire; **Phase 2 may still award** | form present but outside the set → no fire, correctly | — | **safe** |
| `qual.product_depth` | +10 | `catalog.product_url_count ≥ 20` | no fire | count < 20 → no fire | never written as `0` (§10.3's three-state rule); `catalog_not_measurable` already routes it | **safe**, already routed |
| `qual.own_brand` | +10 | Phase-2 LLM only | never fires in Phase 1 | — | — | **safe** |
| `qual.own_domain_shop` | +5 | `catalog.product_url_count ≥ 5` | no fire, and no `possible_marketplace_only` either (§6.1) | — | — | **safe**, already stated |
| `qual.product_strength` | +10 | `reviews.trusted_shops`, `reviews.count ≥ 50` | `trusted_shops` is always written 0/1 from a fetched homepage; `reviews.count` absent means no `AggregateRating`, which is a disjunct not firing | — | — | **safe** |

## 2. §6.2 Opportunity

| rule | points | reads | missing | zero / below threshold | cannot carry | verdict |
|---|---|---|---|---|---|---|
| `opp.no_blog` | **+25** | `content.blog_exists`, `content.blog_search_exhaustive` | abstain, suppress the ladder | — | search ran one instrument only | **A7 — guarded** (M1.34) |
| `opp.blog_stale` | +20 | `content.blog_last_post`, `..._basis` | rung 2 abstains → `blog_date_unparseable` | — | basis `article` → `blog_date_unbounded` | **A7 — guarded** (§6.2, M1.32) |
| `opp.thin_blog` | +12 | `content.blog_post_count` | does not fire, **falls through** (M1.34) | — | — | **A7 — guarded** |
| `opp.blog_slowing` | +10 | `content.blog_last_post`, `..._basis` | as `blog_stale` | — | as `blog_stale` | **A7 — guarded** |
| `opp.no_article_schema` | +8 | `content.blog_exists`, `schema.article_present` | **fires on an unwritten signal** | 0 → fires, correctly | A6.1 leaves it unwritten when no article was sampled | **A7 — NEW.** See §5. |
| `opp.no_product_schema` | +10 | `schema.product_present` | must not fire | 0 → fires, correctly | A5.5/A5.6 | **A7 — guarded**, routing still open (A7b) |
| `opp.ai_invisible` | +15 | `ai.queries_checked ≥ 2`, `ai.brand_mentions = 0` | no fire — the `queries_checked` guard is already the A7 guard | — | — | **safe** |
| `opp.slow_site` | +10 | `perf.lighthouse_performance < 50` | **NULL must not read as < 50** | — | — | **A7 — NEW**, latent: Phase 2 only, so it cannot bite until M5. Guarded now anyway. |
| `opp.de_only` | +5 | `i18n.hreflang_count` | **never written for a shop with no `hreflang` at all** — which is exactly the de-only population | 1 → fires | — | **B7 shape.** See §6. |

## 3. §6.3 Negative

| rule | points | reads | missing | zero | cannot carry | verdict |
|---|---|---|---|---|---|---|
| `neg.has_agency` | −20 | `agency.footer_credit` | no penalty | — | under-detects (logo-only credits); §6.3 already treats it as a bonus, never a gate | **safe by policy**, but see §4 — the *read model* resurrects §10.4's platform-credit bug |
| `neg.active_content` | **−25** | needs dates for **several** posts | silently never fires | — | one sampled article, one `blog_last_post`, a `post_count` with no recency distribution | **B7 shape, confirmed.** See §7. |

## 4. The read model resurrects fixed bugs — `company_profile` (M1.36)

**Found while auditing, and it is the most serious item here.** `signal` is
append-only and `company_profile` pivots *the latest observation per key across
all runs*. §5 calls that harmless. It stopped being harmless the moment a stage
could **stop** writing a key — which is precisely what every A7 guard added
since M1.32 does.

A signal that is no longer written is **not retracted**. It keeps being served
by the view, from whichever run last wrote it, forever.

Two live instances on the corpus, both re-animating bugs the code already fixed:

| key | shop | stale value | written by | current code |
|---|---|---|---|---|
| `agency.footer_credit` | `smoke2u.de`, `verpackungskoenig.de`, `germanelectronic.de` | `"… Powered by JTL-Shop …"` | **run 5**, before the platform-credit exclusion | correctly writes nothing |
| `content.blog_exists` | `zecplus.de` | `0` | run 18, before M1.34 | correctly writes nothing (its index has never been fetched) |

The first would fire `neg.has_agency` for **−20 on three JTL shops for their
choice of shop system** — §10.4's named defect, arriving through the view
instead of through the parser. The second would fire `opp.no_blog` for **+25 on
`zecplus.de`** — the exact bug M1.34 closed, one week old.

`zecplus.de` survives only because M1.34 writes `content.blog_search_exhaustive`
in the same breath as it declines to write `content.blog_exists`, so rung 1
abstains on the fresh qualifier. That is a paired guard doing its job, not the
read model doing its job — nothing paired protects `agency.footer_credit`, and
nothing would protect `schema.article_present`.

**Fix (migration 006): latest-per-key is scoped to the latest run of that key's
own stage, per company.** A later `extract-p1` run supersedes every earlier
`extract-p1` observation for that company, including by omission; other stages
are untouched, so Phase-2 signals survive a Phase-1 re-run, and `fetch`-written
signals survive both. Scoped **per company** rather than globally, so a partial
or `--resume` run cannot blank the companies it did not touch.

## 5. `opp.no_article_schema` — A7, new

`schema.article_present` is written only from a sampled article (A6.1). Where no
article was obtained the signal is absent, and a rule reading absence as `0`
awards **+8 for missing markup on a page it never fetched** — A5.5's error, one
signal over. It does not bite on this corpus (all seven blogs yielded an
article) which is exactly why it would have shipped.

Guarded here: the rule fires only when the signal was **written**, and abstains
otherwise. It is a transient (A7b) — the article fetch may succeed next run.

## 6. `opp.de_only` — B7 shape

`hreflang_language_count` returns `None` when a page carries no `hreflang`
alternates at all. Extract then writes nothing. So the rule can fire only for a
shop that *declares* `hreflang` and declares exactly one language — and never
for a shop with no `hreflang` at all, which is what "German only" actually
looks like.

On the corpus: **7 of 13 have no `hreflang` and therefore cannot win the rule**;
it fires for the 2 that declare a single language. The rule is inverted against
its own population.

**Data path defined:** a homepage fetched with HTTP 200 and carrying no
`hreflang` is a *measurement* — zero declared alternate languages — not a
failure to measure. Extract writes `i18n.hreflang_count = 0`. The rule fires at
`≤ 1`. Absent the signal (no homepage) it still abstains, so "checked and
monolingual" stays distinct from "never looked".

## 7. `neg.active_content` — B7 shape, confirmed, and worse than the others

§6.3 awards **−25** for "≥ 4 posts in the last 6 months". Evaluating it needs
dates for *several* posts. A6 samples **one** article; `content.blog_last_post`
is one date; `content.blog_post_count` is a total with no recency distribution.

Measured on every stored blog index — distinct parseable post dates against the
number of posts the index actually lists:

| shop | posts listed | dated | coverage | in last 180 d | can the rule decide? |
|---|---|---|---|---|---|
| `navucko.com` | 6 | 6 | 6/6 | **4** | **yes — fires −25**, soundly |
| `blackpolish.de` | 3 | 3 | 3/3 | 0 | yes — declines soundly (3 posts total; ≥ 4 is impossible) |
| `ekomia.de` | 23 | 10 | 10/23 | 0 | **no** — 13 posts undated |
| `bio-fleischer-laden.de` | 22 | 1 | 1/22 | 0 | **no** |
| `snocks.com` | 11 | 0 | 0/11 | 0 | **no** — Shopify index carries no dates |
| `doonails.de` | 26 | 0 | 0/26 | 0 | **no** — and its newest post is 2026-05-29 |
| `smile-store.de` | 15 | 0 | 0/15 | 0 | **no** |

**Confirmed, with one correction to the prediction.** The rule is not
uncomputable everywhere: it is decidable on **2 of 13** shops and undecidable on
**5**. The remaining 6 have no blog at all, where it declines soundly — no blog,
no posts.

**The failure is directional, which is what makes it expensive.** A partial
enumeration can still *establish* activity — if 4 dated posts are recent, there
are at least 4 recent, however many are undated. What it can never establish is
the *absence* of activity. So the rule can only ever fail in one direction: it
under-fires, and every under-fire inflates an active publisher's score by 25.

`doonails.de` is the case. 26 posts, newest 2026-05-29 — 2½ months old — and
**zero** of its posts carry a date the index exposes. It is plainly an active
publisher, it will take no penalty, and it is otherwise the strongest lead in
the corpus. The rule that exists to catch exactly this company cannot see it.

**This is the first A7 instance where abstention is not the conservative
direction.** Every previous one guards a rule that *awards* points for an
absence, so declining to fire loses points and the review queue catches an
under-scored lead. This rule *subtracts* points for a presence, so declining to
fire **over-scores** the lead — and an over-scored lead is not merely mis-ranked,
it gets contacted. A7's shape is the same; the cost of the abstention is not.
The routing therefore matters more here than in any previous instance, not less.

**Data path defined** rather than dropped, because dropping it means no company
can ever be penalised for publishing actively, and "actively publishing" is the
one thing that makes a company *not* an opportunity — the model's stated
purpose:

- Extract writes `content.blog_post_dates` — every distinct parseable post date
  on the index, as sorted ISO text, with the count in `value_num`. Dates rather
  than a pre-computed count, so that §5's "scoring is a pure recompute at zero
  cost" stays true: a stored count of "posts in the last 6 months" silently
  decays as the window moves, and a recompute six months later would be wrong.
- **Fires** at ≥ 4 dates within 180 days — sound on a partial enumeration.
- **Declines** where the enumeration is complete (`dated ≥ listed`) and fewer
  than 4 are recent, or where `opp.no_blog`'s rung established there is no blog.
- **Abstains** otherwise, with the coverage in the reason.

Completeness is `distinct dates ≥ posts listed`, which is deliberately strict:
two posts published on one day count as one date and force an abstention. That
errs toward abstaining, and abstaining is the *visible* error.

**What it does to §6.5:** nothing may be tuned (§10.3), but the effect must be
stated. −25 now fires on 1 of 13 and abstains on 5. Before this audit it would
have fired on 1 and *silently* declined on 5. The scores are identical; what
changes is that the 5 are now visible as abstentions rather than as confident
zeros. §6.5's bands stay exactly where they are.

---

## 8. What needs ratification, and what does not

Implemented under existing authority — data paths, guards, and the read-model
fix are corrections to instruments, not changes to weights or thresholds:

- migration 006's stage-scoped `company_profile`
- `i18n.hreflang_count = 0` on a fetched homepage with no alternates
- `content.blog_post_dates`
- the `opp.no_article_schema` and `opp.slow_site` guards

**Two §6.4 ratifications are outstanding**, both routings for abstentions that
are correct today but silent. Every rule below already abstains in the safe
direction; what is missing is part 3 of A7, the review flag:

| # | abstention | how often on the corpus | recommended reason |
|---|---|---|---|
| 1 | a **persistent** transient — a product page or blog index that has failed for N = 3 runs (A7b, carried over from M1.34) | 2 shops today, at run 1 of 3 | `fetch_persistently_failing` |
| 2 | `neg.active_content` cannot measure cadence | **5 of 13** | `blog_cadence_unmeasurable` |

The second is the larger of the two and is the one I would take first: it is 5
companies whose score is knowingly 25 points high, and unlike every other
abstention in this spec, nobody is currently told.
