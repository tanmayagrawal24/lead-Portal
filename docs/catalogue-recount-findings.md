# `catalog.product_url_count` after M1.24–M1.26 — what the counts became

**Date:** 2026-08-15 · **Branch:** `m2-catalogue-tiers` · **Stage:** `portal extract-p1` (no HTTP requests; the corpus on disk is unchanged since the third crawl)

---

## 1. The table

All 13 companies. **`tier` is now written to the signal's `value_text`**, so a count carries the provenance of the instrument that produced it.

| domain | platform | before | after | tier | why it moved |
|---|---|---:|---:|---|---|
| bio-fleischer-laden.de | Shopify | 306 | **306** | `product_sitemap` | unchanged; single locale |
| blackpolish.de | Shopify | 22 | **22** | `product_sitemap` | unchanged; single locale |
| doonails.de | Shopify | 389 | **389** | `product_sitemap` | unchanged; locales are on subdomains, absent from the sitemap |
| ekomia.de | Shopify | 2 861 | **335** | `product_sitemap` | M1.25 — nine locale storefronts were being counted as nine catalogues |
| germanelectronic.de → lampenflut.de | JTL | *not measured* | *not measured* | — | no sitemap at all (`/sitemap.xml` 404, no `Sitemap:` directive) |
| navucko.com | Shopify | 144 | **72** | `product_sitemap` | M1.25 — `/en` counted twice |
| opulent-wohnen.com | JTL | *not measurable* | *not measurable* | — | genuine root-slug shape; **now flagged** |
| propellerdiscount.de | WooCommerce | *not measured* | *not measured* | — | serves no sitemap |
| **smile-store.de** | *(SW5, undetected)* | **6** | **194** | `product_sitemap` | **M1.24** — its product sitemap is named `articles` |
| smoke2u.de | JTL | *not measurable* | *not measurable* | — | genuine root-slug shape; **now flagged** |
| snocks.com | Shopify | 4 620 | **462** | `product_sitemap` | M1.25 — ten locale storefronts |
| verpackungskoenig.de | JTL | *not measurable* | *not measurable* | — | genuine root-slug shape; **now flagged** |
| zecplus.de | Shopify | 242 | **242** | `product_sitemap` | unchanged; the `de-CH` alternate is on `zecplus.ch`, a different site |

**Five of the eight measurable counts were wrong**, and only one of them was wrong in the direction that had been noticed. Four were inflated between 2× and 10×.

**Every one of the eight now comes from Tier 1. Tier 2 answers for none of them.** Before this change Tier 2 answered for all eight — the fallback was doing the entire job.

---

## 2. `smile-store.de`, which was the case this started from

Its sitemap index is a labelled table of contents:

```
area/categories-0-sitemap.xml       65
area/articles-0-sitemap.xml        194   <- the catalogue
area/customPages-0-sitemap.xml      17
area/suppliers-0-sitemap.xml        27
area/landingPages-0-sitemap.xml      1
area/blogs-0-sitemap.xml            57
area/pictures-0-sitemap.xml        887
```

The old count of **6** came from `/detail/` matching six stragglers elsewhere on the site while the shard holding all 194 products sat unread. The shop had told us exactly where its catalogue was, in German commerce's own word for a saleable product, and the tool was matching filenames against four platform conventions instead of reading the label.

**Consequences of the correction, in points:** `qual.product_depth` (needs ≥ 20) now fires **correctly** rather than being missed, and `qual.own_domain_shop` (B7, needs ≥ 5) now fires **on evidence** rather than passing by luck on six accidental matches. +10 recovered, +5 made sound.

---

## 3. `snocks.com` and `ekomia.de`, which were about to get worse

M1.24's Tier 1 anchoring alone would have made these two much wronger. A Shopify shop serves one product sitemap **per locale**, each holding the same catalogue under a different prefix:

```
snocks.com/sitemap_products_1.xml            925 <loc> elements
snocks.com/de-ch/sitemap_products_1.xml      925   (same products, translated)
snocks.com/it-ch/… fr-ch/… fr-fr/… pl-pl/…
nl-nl/… fr-nl/… it-it/… en-es/…             ten storefronts in total
```

Counting their union gives 4 620 for a shop with 462 products. **Fixing the undercount would have shipped a tenfold overcount in the same commit** — the reason M1.25 is not a separate later improvement.

The two-instrument rule is needed because neither instrument covers the corpus:

- `snocks.com` declares **three** of its ten markets in `hreflang`. The other seven are visible only by the *shape* of the leading path segment.
- `smile-store.de`'s English subshop is at `/shop/en/`, which no path-shape rule would ever recognise as a locale. It is declared, and only declared.

---

## 4. `<image:loc>`, found while checking the arithmetic

The counts came out at exactly half the `<loc>` elements in every Shopify product sitemap, which is what prompted looking. Shopify emits one `<image:loc>` per product, in the image extension's namespace — and `parse` stripped namespaces before comparing tag names, so every product sitemap read as twice its length.

It reached no count on this corpus **only by luck**: Shopify serves its images from `cdn.shopify.com`, and `same_site` discarded them. A shop hosting its own images under `/products/…` would have had every product counted twice by a rule that believed it was counting pages. Fixed (M1.26) and pinned.

---

## 5. Review flags

`catalog_not_measurable` (migration 003) is raised on exactly the three shops whose catalogues genuinely cannot be measured:

| company | open flags |
|---|---|
| opulent-wohnen.com | `catalog_not_measurable` |
| smoke2u.de | `catalog_not_measurable` |
| verpackungskoenig.de | `catalog_not_measurable` |
| doonails.de | `domain_moved` |
| germanelectronic.de | `domain_moved` |
| ekomia.de, snocks.com | `no_impressum` |

**It was four shops before this change.** `smile-store.de` left the group by being measured, which is the outcome that says the flag is measuring the instrument's reach rather than the platform's name.

---

## 6. A regression I introduced and caught by re-running

Excluding blog-shard URLs from the catalogue count is right; I excluded them from `page_urls` *entirely*, which also took `/blogs/…` away from blog **path** detection. `snocks.com` — 107 blog URLs, a post from July — came out as `content.blog_exists = 0`, which is `opp.no_blog`'s **+25** against a shop that publishes weekly, the single worst error §6.2 can make.

It did not fail a test. It failed the run, and only because the output was read line by line against the previous run's. The fix separates the two collections: `page_urls` stays complete for detection, `catalogue_urls` excludes content shards for counting.

Same lesson as M2's gzip defect, in a new place: **a change that produces a plausible output is invisible to a test suite written by whoever made the change.**

---

## 7. What is still open

- **"When is a written count untrustworthy?"** — still open, and now with *less* evidence than it had. `smile-store.de` was the case it was raised on, and it turned out to be a bug rather than an instance. The corpus contains no known example of a genuinely small catalogue being mistaken for a measurement failure, which is not the same as there being none. §10.3 records why inventing a threshold now would be the M1.4 error. The cheap partial answer is already in place: a low count from `product_sitemap` is the shop's own statement, a low count from `sitemap_path_pattern` is ours.
- **`articles` is ambiguous** — a product in German commerce, a blog post in English CMS usage. Recorded as a live risk in M1.24 rather than mitigated by a guess. The content vocabulary is tested first, which catches `blog-articles-…` and not a bare `articles` shard that means posts.
- **The German shard words `artikel` and `produkte` are unobserved.** They are in the vocabulary and no shop in the corpus serves either.
- **M1.14 is unchanged.** The blog shard was assessed as a third instrument and reaches neither unreachable shape — see §10.1.
