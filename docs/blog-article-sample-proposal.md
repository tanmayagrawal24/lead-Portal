# Proposal — A6: sample one blog article, the way A5 samples one product

**Status:** proposal. Nothing here is implemented.
**Changes:** §5.2 (a new fetch target), §5.3 (two signals change their evidence page).
**Depends on:** nothing. **Depended on by:** `content.blog_last_post`, `schema.article_present`, and therefore `opp.blog_stale` and `opp.no_blog_schema` in M3.

---

## 1. The defect this exists to fix

§5.3 says to read `content.blog_last_post` and `schema.article_present` from **the blog index**. On the platform that is 7 of 13 shops in the corpus, the blog index carries neither.

Measured on the stored corpus, 2026-08-15:

| signal | §5.3 says | what the index actually holds | result |
|---|---|---|---|
| `content.blog_last_post` | newest `datePublished` / `<time>` / German visible date on the index | Shopify blog indexes emit **no `<time>` element and no `datePublished`** — both live on the article page | **5 of 7** detected blogs yield no date |
| `schema.article_present` | `Article`/`BlogPosting` JSON-LD on the index | `Article` markup lives on the post, not the listing | **0 on every blog index in the corpus** |

Neither failure is a parser weakness, and this is the reason the fix cannot be an M2 fix: **the evidence is not on the page we fetched.** §5.3 names the wrong page.

Both consequences are wrong in the expensive direction:

- §6.2's ladder hits its `blog_last_post is NULL` branch and raises `blog_date_unparseable` for the majority platform. A review queue that fills with most of the corpus is not a review queue; it is a way of teaching its reader to close the tab.
- `schema.article_present = 0` is written as a *checked-and-absent* fact when it is a not-measured one. It is the same class of error A5.5 already guards `schema.product_present` against — and the guard exists there because we fetch a product page. The asymmetry is the bug.

---

## 2. The proposal — A6, symmetric with A5

> **A6. Blog article sampling.** Where a blog index is fetched (M1.15), one **article** under it is also fetched. The choice is a stated rule, not whatever the crawler reached first, and obeys the same guarantee A5 does: *same inputs → same choice.*
>
> Let `index_path` be the path of the blog index artifact. A candidate is a URL that
>
> 1. is `same_site` (M1.18: against `site_domain`),
> 2. lies strictly under `index_path` — `path.startswith(index_path + "/")`,
> 3. is not itself the index,
> 4. carries no query string,
> 5. is not under a secondary locale prefix (M1.25).
>
> Candidates are taken from the first tier that yields any:
>
> - **Tier 1 — the blog sitemap shard.** URLs from a shard the site labels as content (M1.24). Membership is the evidence; no path shape is required.
> - **Tier 2 — sitemap URLs under the blog path.** The general sitemap, filtered as above.
> - **Tier 3 — links on the blog index page itself.** Same filter, applied to observed anchors.
>
> Within the chosen tier: **shallowest first, code-point minimum breaking ties** — the same ordering M1.15 uses to pick the index and A5 uses to pick a product, and for the same reason (a locale-collating sort makes the choice depend on the machine).
>
> The chosen URL is recorded as `content.blog_sample_url`, **unscored**, exactly as `catalog.product_sample_url` records A5's choice. It exists so that the evidence behind `content.blog_last_post` and `schema.article_present` is auditable rather than inferred.
>
> **A6.1.** Where no tier yields a candidate, no article is fetched, and `content.blog_last_post` and `schema.article_present` are **left unwritten**. Not zero, not today's date. This is A5.5's rule applied to the same shape of absence.

### What it selects on the real corpus

Every blog in the corpus resolves, and every one resolves from Tier 1:

| shop | index (M1.15) | tier | selected article |
|---|---|---|---|
| bio-fleischer-laden.de | `/blogs/rezepte` | 1 | `/blogs/rezepte/bbq-schweinenacken` |
| blackpolish.de | `/blogs/news` | 1 | `/blogs/news/blackpolish-is-live` |
| doonails.de | `/blogs/press-ons-instructions` | 1 | `/blogs/press-ons-instructions/pedicure-press-ons-instruction` |
| ekomia.de | `/blogs/inside-ekomia` | 1 | `/blogs/inside-ekomia/arbeiten-rueckenuebungen-fuer-das-buero` |
| navucko.com | `/blogs/news` | 1 | `/blogs/news/broome-street-temple-x-navucko` |
| smile-store.de | `/magazin` | 1 | `/magazin/auszeichnung-dental-champions-in-der-apotheke` |
| snocks.com | `/blogs/lifestyle` | 1 | `/blogs/lifestyle/das-poloshirt-und-was-man-daruber-wissen-muss` |

**Filter 2 is load-bearing and is the part most likely to be got wrong.** On Shopify the hierarchy is `/blogs/<blog-handle>/<article-handle>`, so a URL at depth 2 under `/blogs` is *another blog index*, not a post. Selecting "the shallowest URL under the blog path" — the obvious phrasing — picks `/blogs/karriere` on `bio-fleischer-laden.de`: a listing page, fed to an Article parser, which is precisely the M1.16 error in a new place. Anchoring on the **index path** rather than the blog path is what avoids it.

### Cost

**One request per company that has a blog** — 7 on this corpus, 7 more on a 13-shop run, ~+3% on a full crawl's request count. It is subject to §5.2's limiter and robots rules like any other request, and it happens in the same pass as the index fetch, so it adds no new host to the concurrency budget.

---

## 3. The cheaper instrument, assessed and rejected as the primary

Blog sitemap shards carry a `<lastmod>` per article, on **both** platforms that serve one — Shopify's `sitemap_blogs_1.xml` and Shopware/Pixup's `blogs-0-sitemap.xml`. That is already on disk. It costs nothing. It is worth stating plainly why it does not answer the question:

**`lastmod` is modification, not publication, and the corpus contains a measured instance of it lying by three years.**

| `bio-fleischer-laden.de` | date |
|---|---|
| newest post date parsed from the blog index | **2022-12-01** |
| newest `lastmod` on an **article** URL | 2025-01-23 |
| newest `lastmod` on a **listing** URL | 2026-02-25 |

§5.3 already warns that sitemap `lastmod` "is regenerated on deploys by Shopware/WP and systematically lies fresh". This is that warning, measured: a shop whose newest post is from 2022 presents as active in 2026. `opp.blog_stale` is an award for *not publishing*, so an instrument that lies fresh suppresses the award on exactly the leads it should fire for — the failure is silent and in the direction that costs money.

**Recommended use, therefore: corroboration, never the value.** Where the sampled article gives a `datePublished`, that is the signal. Where A6.1 leaves it unwritten, a `lastmod` may be recorded separately (`content.blog_lastmod_hint`) so a human reviewing `blog_date_unparseable` can see whether the blog looks touched at all — but it must not be read by any §6 rule. That keeps §5.3's existing "hint only, never used alone" rule intact rather than quietly relaxing it.

---

## 4. What §5.3 must say afterwards

Two rows change their *evidence page*, not their method:

- `content.blog_last_post` — "newest date parsed from the blog index HTML" becomes "…from the **sampled article** (A6), falling back to the index where an index date exists". The index remains a legitimate source where it has dates: `smile-store.de` and the German-visible-date shops do carry them, and a date already parsed is not worth a request.
- `schema.article_present` — evidence page becomes the sampled article. Absent a sample, **unwritten**, per A6.1.

And §5.2 gains `blog_article` as a fetch kind, alongside `blog_index`.

---

## 5. Open question this proposal does *not* answer

`content.blog_post_count` is still read off the index, and on Shopify the index is one blog handle among several — `bio-fleischer-laden.de` publishes under `rezepte`, `karriere` **and** `tiere-bio-wissenswertes`, of which the count sees one. Its shard holds 26 URLs: 3 listings and **23 articles**, against the 22 counted from the index. The blog shard gives the whole-shop figure for free. That is a separate change to a separate signal, with its own scoring consequence, and it is not bundled in here.
