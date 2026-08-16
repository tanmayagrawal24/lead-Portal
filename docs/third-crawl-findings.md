# Third crawl — 2026-08-16

**Why it ran.** Two shops abstained on §6.2's rung 1 for a reason that was not
about them: M1.34's anchor-text detector is newer than the second crawl, so
`zecplus.de` and `germanelectronic.de` had blogs the pipeline could now *find*
and had never *fetched*. The abstention was correct and the ignorance behind it
was stale. It is also the first exercise of migration 006/007 across runs — a
read model whose whole job is deciding which run to believe, tested by giving it
two runs to choose between.

**Conduct.** 13 domains, 738 requests, 16 politeness keys, min gap 1.000 s
everywhere, max 2 hosts in flight against a ceiling of 2. `portal
audit-politeness` reports **§5.2: HELD** and exits 0. Three hosts were served
slower than the floor because they asked to be: `lampenflut.de` and
`verpackungskoenig.de` at 5 s, `propellerdiscount.de` at 10 s, all from
`Crawl-delay`.

---

## 1. What the crawl was for: both blogs landed

| shop | before | after |
|---|---|---|
| `zecplus.de` | rung 1 abstained, `transient:` — blog located by anchor text at `blog.zecplus.de`, index never fetched | index fetched. `blog_exists = 1`, 2 dated posts, an article sampled, `schema.article_present = 1` |
| `germanelectronic.de` | rung 1 abstained, `transient:` — blog located at `lampenflut.de/Lampenflut-Licht-Ratgeber` | index fetched. `blog_exists = 1`, **11 dated posts**, newest 2026-07-29 |

`zecplus.de` is the shape M1.34 was built for and could not previously reach:
the blog is a **subdomain its own four sitemap shards never mention**, and the
anchor href is the only instrument that gets there. It took three crawls before
the detector and the fetch were in the same run.

## 2. `neg.active_content` fired for the first time in the project's history

`germanelectronic.de` → `lampenflut.de` publishes **11 posts in six months**.
The rule that M3's audit found had no data path at all — B7's shape on the
largest negative in the ruleset — took its −25, and the score fell **30 → 5**.

This is the rule working exactly as §6.3 argues it should. A shop publishing
twice a month is not a content-marketing opportunity, and every previous run
ranked it as one because the pipeline had no way to see the posts. The first
thing the new data path did was **remove** a lead, which is the direction that
never feels like progress and is the reason the rule exists.

## 3. Two defects, both found by reading the output rather than by a test

### M1.40 — the basis claimed a bound the index did not supply

`zecplus.de` scored **55 and entered band B** on `opp.blog_slowing` (+10),
justified by:

> Letzter Blogbeitrag: September 2025 – seit gut einem halben Jahr ist die
> Veröffentlichung eingeschlafen.

The date is real. What is not real is the bound behind it:

| | |
|---|---|
| `content.blog_last_post` | **2025-09-03**, evidence URL `…/die-3-postworkout-basics/` — the **sampled article** |
| the index's own newest date | **2021-03-10** |
| `content.blog_last_post_basis` | `both` — which §6.2 reads as *the index bounds this from above* |

An index whose newest dated post is four years older than a post we are holding
in our hand has demonstrably failed to date the newest post. It bounds nothing.
The basis was computed from *which sources produced a date*, not from *which
date won*, so the guard M1.32 added to stop exactly this reported that it was
satisfied.

Fixed: the index bounds the value only where the index's own maximum **is** the
value written. Live on **3 of 13** shops — `zecplus.de`, `bio-fleischer-laden.de`
and `ekomia.de` all moved `both → article`. The score consequences are in §4.

### M1.40b — an abstention reason that printed a missing total as a zero

`zecplus.de` again, this time in the queue note a person acts on:

> Nicht bewertbar: Von **0** Beiträgen auf der Übersichtsseite tragen nur **2**
> ein lesbares Datum

`content.blog_post_count` was never written for that index, and the reason
rendered the absent total as `0`. The abstention itself is right — without a
total there is nothing for the dates to be complete *against* — but the sentence
is incoherent, and it is a sentence that goes into a review queue and can go
into a letter. It now says the index cannot be counted, because that is what
happened.

## 4. The score delta

Runs compared: **28** (this morning's code, before the crawl) → **31** (after
the crawl) → **34** (after the two fixes above). Ten of thirteen scores are
unchanged.

| shop | 28 | 31 | 34 | what moved |
|---|---|---|---|---|
| `germanelectronic.de` | 30 | **5** | 5 | `neg.active_content` −25, on 11 posts in 180 days |
| `zecplus.de` | 45 | **55** | **45** | crawl added `opp.blog_slowing` +10; M1.40 took it back — the date rests on the sample |
| `bio-fleischer-laden.de` | 65 | 65 | **45** | M1.40: `opp.blog_stale` +20 → abstains. Leaves band B |

The two movements that matter are both **downward**, and both are the pipeline
declining to claim something it cannot support. `bio-fleischer-laden.de` was the
number-two lead in the list this morning on a staleness claim whose upper bound
did not exist.

## 5. The read model, exercised

Migration 006/007's first real test: 13 companies, an `extract-p1` run
superseding another, `fetch`-written signals from a third stage, and the score
runs writing their own gate signals alongside. `diff-signals` between the two
extract runs reports **11 changes across 2 domains** — the two blogs — and
nothing else moved. In particular the `agency.footer_credit` values from run 5
that M1.36 found being re-served stayed retracted, and no company was blanked by
a run that touched a different set of keys.

## 6. Review queue after the crawl

18 open flags across 11 companies. Six are `blog_cadence_unmeasurable`, and all
six **block outbound contact** (migration 008):

| reason | companies |
|---|---|
| `blog_cadence_unmeasurable` ⛔ | `bio-fleischer-laden.de`, `doonails.de`, `ekomia.de`, `smile-store.de`, `snocks.com`, `zecplus.de` |
| `blog_date_unbounded` | `bio-fleischer-laden.de`, `snocks.com`, `zecplus.de` |
| `catalog_not_measurable` | `opulent-wohnen.com`, `smoke2u.de`, `verpackungskoenig.de` |
| `domain_moved` | `doonails.de`, `germanelectronic.de` |
| `no_impressum` | `ekomia.de`, `snocks.com` |
| `blog_date_unparseable` | `smile-store.de` |
| `blog_undetectable` | `propellerdiscount.de` |

`zecplus.de` is now carrying three of them at once, which is the correct
picture: the blog was found, its dates do not bound staleness, and its cadence
cannot be measured. This morning it carried none of that and a confident 45.

`no blog index fetched` on `snocks.com` remains a **robots-respecting** refusal,
not a failure — its legal-notice redirect is disallowed, and §5.2 declines it.

## 7. What did not happen

- No new `fetch_persistently_failing` flags. Two measurements are counting
  (`ekomia.de`'s product sample and article, `snocks.com`'s product sample and
  blog index) but the counter needs 3 consecutive scoring runs on 3 **distinct
  days**, and this is day 1.
- No band recalibration. §10.3 still forbids it, and the two shops that moved
  band moved because their evidence changed, not because a threshold did.
- `propellerdiscount.de` is still the only company the Phase-2 gate stops, at
  0 + 50 against a floor of 55 — and it carries `blog_undetectable`, which is
  the whole of §5.4's narrowed safety claim (M1.41).
