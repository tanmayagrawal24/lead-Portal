# Proposal — A6's selector: a tier hierarchy, not an arbitrary article

**Status:** proposal. Nothing here is implemented.
**Changes:** A6's ordering, which was **ratified** as shallowest-first. That is why this is a proposal rather than a branch.
**Depends on:** nothing. **Depended on by:** `content.blog_last_post`, and therefore `opp.blog_stale` and `opp.blog_slowing` in M3.
**Does not depend on, and does not resolve:** M1.32's interim guard (§6.2). The two are independent; see §6.

---

## 1. The defect

A6 picks the **shallowest** article under the fetched blog index, code-point minimum breaking ties. That ordering was chosen for determinism — *same inputs → same choice* — and it delivers determinism. It was never chosen to pick the **newest** post, and it does not.

`content.blog_last_post` is therefore a lower bound (M1.30, §10.5), and the rung that reads it is an award for *not* publishing. An under-estimate of freshness fires `opp.blog_stale` (+20) wrongly, which is the expensive direction.

The shape of the miss is measurable without fetching anything: on **7 of 7** blogs in the corpus, the article A6 selects today is not the article the shop's own sitemap says was touched most recently.

---

## 2. The proposal

> **A6.2. Sample selection is tiered**, in the same shape A5 uses for products and M1.24 uses for the catalogue count — each tier a different instrument with a different reliability, the first that yields a candidate answering, and the tier that answered travelling with the choice in `content.blog_sample_url`'s `value_text`.
>
> - **Tier 1 — newest `<lastmod>` in the blog sitemap shard.** Among candidates (A6's filter, unchanged), the one whose `<lastmod>` is greatest. Ties broken by code-point minimum, as everywhere else.
> - **Tier 2 — shallowest under the index path.** A6 as ratified, unchanged, as the terminal tier.
>
> **Two tiers, not three.** The brief that produced this proposal specified a middle tier — first article in index document order — and it is **not proposed**. It is written up in §4 and parked. The reason is not that the evidence for it is thin; it is that the evidence for it cannot be gathered from the cases that would use it.
>
> **`<lastmod>` is the selector and never the value.** The date written to `content.blog_last_post` is still `datePublished` / `<time datetime>` / a German visible date, read off the fetched page. §5.3's rule — `lastmod` is a hint, no §6 rule may read it — is untouched, because no §6 rule reads it here either.

### Why the bias cannot reach the signal

`lastmod` "systematically lies fresh": it is regenerated on deploys, and the corpus holds a measured instance of it lying by three years (§5.3, `bio-fleischer-laden.de`). As a **value** that is disqualifying, and §5.3 disqualified it.

As a **selector** the same bias is harmless, because the worst it can do is pick the wrong post, and every post is a real post with a real `datePublished`:

| what lastmod does | which article gets picked | what gets written |
|---|---|---|
| tells the truth | the most recently touched post | its real publication date |
| lies fresh on a deploy | an old post that was edited | **that old post's real publication date** |

Both rows write a genuine `datePublished` off a genuine page. The failure mode is "we sampled an unhelpful article", which is exactly the failure mode of the ratified rule — so Tier 1 is bounded below by today's behaviour and cannot be worse than it. It just has a much better expected case.

---

## 3. What it selects on the corpus

Measured on the stored artifacts, 2026-08-15. No requests. Paths abbreviated; `doonails.de` resolves under its adopted host (M1.18).

| shop | Tier 1 — newest lastmod | Tier 2 — A6 today | differs |
|---|---|---|---|
| bio-fleischer-laden.de | `…/putenschnitzel-mit-kerbelschmand-…` (2025-01-13) | `…/bbq-schweinenacken` | yes |
| blackpolish.de | `…/blackpolish-x-afew` (2023-12-26) | `…/blackpolish-is-live` | yes |
| doonails.de | `…/remover-pen` (2026-07-25) | `…/pedicure-press-ons-instruction` | yes |
| ekomia.de | `…/duda-komposter-fur-zuhause-kompostieren` (2026-06-15) | `…/arbeiten-rueckenuebungen-fuer-das-buero` | yes |
| navucko.com | `…/nyc-juni-2026` (2026-07-03) | `…/broome-street-temple-x-navucko` | yes |
| smile-store.de | `…/zahnpflege-infos/die-besten-ultraschallzahnbuersten-2026-…` (2026-06-15) | `…/auszeichnung-dental-champions-in-der-apotheke` | yes |
| snocks.com | `…/sieben-stoffe-die-uns-im-winter-warm-halten-…` (2026-06-10) | `…/das-poloshirt-und-was-man-daruber-wissen-muss` | yes |

**Tier 1 resolves on all 7**, including both shops the M1.32 guard currently silences. **Tier 2 answers for none of them** — the same shape M1.24 found, where the fallback had been doing the entire job.

**What this table does not show is the effect on the dates**, and it cannot: the new articles have not been fetched, so their `datePublished` is unknown. Two of the current samples are already suggestive — `snocks.com`'s sample dates to 2022-08-26 while its shard has an article touched in 2026-06, and `blackpolish.de`'s to 2019-08-25 against 2023-12 — but a lastmod is not a publication date and treating it as directional evidence here would be the exact error §5.3 warns against. **The date effect is measured by running it, on the branch that implements it, and not before.**

---

## 4. The parked tier: document order, and why it is not "unobserved"

The parked tier rests on a convention: *blog indexes render newest-first.* The brief asked for it to be stated as unobserved unless the corpus confirmed it. The corpus was checked and the answer is worse than unobserved, in a way worth being precise about, because it changes what would have to happen for the tier to become adoptable.

**The ordering claim is confirmed, 3 for 3 — where it is testable.** Three of the seven indexes date their posts on the index page. On all three, the dates appear in descending document order and the first is the newest:

| shop | dates in index document order | first is newest |
|---|---|---|
| blackpolish.de | 2020-03-24, 2019-08-26, 2019-08-25 | yes |
| navucko.com | 2026-06-20, 2026-06-07, 2026-04-11, 2026-02-17, … | yes |
| ekomia.de | 2025-12-08, 2025-07-10, 2025-06-06, 2025-02-28, … | yes |

**Every one of those confirmations comes from an index that dates its posts — and an index that dates its posts is exactly the case where no sample is needed.** It already yields `content.blog_last_post` directly, and M1.32 marks the basis `index` or `both`. The other four indexes carry no date at all, which is the population the tier would actually serve, and on all four the convention is untested.

**That is selection bias, not small n, and the distinction decides what to do about it.** The observable population and the served population are disjoint *by construction*: the only thing that lets us verify document order on an index is the index carrying dates, and the only thing that makes the tier load-bearing is the index carrying none. Twenty more dated indexes would raise the confirmation count to 23 for 23 and say nothing whatever about the four. **The tier is not unobserved; on the cases that matter it is unfalsifiable by observation** — no amount of the evidence this pipeline gathers can confirm or refute it there.

What *would* settle it is an experiment rather than an observation: on an undated index, fetch two or more candidates and compare their `datePublished`. That is a deliberate, budgeted measurement — several requests per shop against A6's one — and it is not something a crawl produces as a by-product. Recorded as the concrete thing to do if the tier is ever wanted, so the next reader does not re-derive the same dead end from the same three confirmations.

**And on the one shop where it was tested against something other than ordering, it failed differently and worse.** `smile-store.de`'s first three candidates in document order are its magazine's own category pages:

```
/magazin/zahnaufhellung/        "Zahnaufhellung"
/magazin/zahnpflege-infos/      "Zahnpflege Infos"
/magazin/kundenerfahrung/       "Kundenerfahrung"
```

They pass A6's filter — they are under the index path — and they are listings. The parked tier would hand a listing page to an Article parser: **M1.16's error, a third time, in the tier introduced to be the safe one.** Navigation precedes content in document order on essentially every template, so this is a structural property of the tier, not a quirk of one shop.

**So the tier is dropped, on three independent grounds, any one of which would be enough.** It is unfalsifiable on the population it serves; it selects navigation on the one shop where anything beyond ordering could be checked; and it would never fire here at all, because Tier 1 resolves all seven. Adding a convention to a hierarchy in a position where it has never once executed, on evidence that cannot be gathered where it would execute, is the M1.4 error with a different string. This project has taken that error at least three times already — `/p/` as a product prefix (M1.4), `jtl-shop` as a JTL signature (M1.9), "shallowest under the blog path is a post" (M1.16) — and each time the convention was plausible, widespread in the wild, and wrong here.

It is written up rather than deleted because the case it was meant to serve is real: a shop whose blog index is fetchable but whose shard is missing or unreadable. `zecplus.de` and `lampenflut.de` are that shape for other signals (§10.1), and this corpus simply contains no shop in that state for blogs. **If it is ever taken, it needs both** the experiment above and a guard requiring the candidate to be dated by the index itself — the latter being what would have excluded `smile-store.de`'s categories.

---

## 5. Cost

**Zero additional requests.** The blog shard is already fetched and on disk for all seven shops; this changes which single URL the existing per-blog request asks for. It is a change to `portal/sampling.py` and to what `fetch` passes it — `choose_blog_article` currently receives shard URLs as a bare list and would need their `<lastmod>` alongside, which `portal/sitemap.py::parse` does not currently return.

---

## 6. What this does not do

**It does not retire M1.32's guard, and does not claim to.** A lastmod-chosen sample is still one post's `datePublished` — a better lower bound, not a maximum. `basis = article` stays unbounded whatever selects the article, so §6.2's staleness rungs stay silent on it. The two changes are independent: the guard is correct with or without this, and this is an improvement with or without the guard.

**It does not make the index exhaustive.** `ekomia.de`'s sampled article is four months newer than anything its index dates (M1.30), so the index lists a subset. Tier 1 widens the population from what the index links to what the shard lists — 24 URLs against 21 candidates on `ekomia.de` — which is better, and still not the blog.

**The obvious next step is deliberately not taken.** Since `lastmod ≥ datePublished` per URL, the newest `lastmod` in a shard is an **upper** bound on the newest publication date — and an upper bound is precisely what a staleness claim needs. It is not proposed, because its error direction is the one §5.3 rules out: a deploy that refreshes every `lastmod` produces a recent upper bound for a dead blog, and the rule suppresses `opp.blog_stale` on exactly the lead it exists to find. Recorded here so the idea gets an answer instead of being raised again.
