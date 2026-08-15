# M2 handoff — `portal extract-p1`

**Branches:** `m2-extract-p1` → `m2-catalogue-tiers` → `m2-a6-and-diff` (none merged)
**Date:** 2026-08-15
**Spec:** §5.3, plus §10.3's three-state question and M1.24–M1.29, all of which this milestone had to settle to be implementable.

Assumes no prior context beyond §5.3 and `docs/first-crawl-findings.md`. Two companion documents carry detail this one summarises: `docs/catalogue-recount-findings.md` (what the counts became and why) and `docs/blog-article-sample-proposal.md` (A6 as proposed, now ratified and built).

---

## 1. What was built

`portal extract-p1` reads artifacts already on disk and writes deterministic signals. **It makes no HTTP requests at all.** A parser fix is re-applied to the whole corpus by re-running it, with no third-party server involved — which is what makes it safe to iterate on, and what §4's "re-scoring never needs a refetch" depends on.

| Module | Responsibility |
|---|---|
| `portal/parsers.py` | Pure functions over HTML: platform signatures, JSON-LD, hreflang, agency credit, legal form, blog dates and counts |
| `portal/extract.py` | The stage: reads artifacts, applies parsers, writes signals under §4's M1.5 idiom |
| `portal/diff.py` | `portal diff-signals` — what changed, per domain, between two runs (M1.28) |
| `portal/cli.py` | `portal extract-p1`, `portal diff-signals` |
| `portal/fetch.py` | A6's one extra request: a sampled blog article (M1.29) |

**Signals written:** `platform.detected`, `content.blog_exists`, `content.blog_last_post`, `content.blog_post_count`, `catalog.product_url_count`, `catalog.not_measurable` (new — see §2), `schema.article_present` (now from the sampled article, A6), `schema.product_present`, `meta.description_length`, `i18n.hreflang_count`, `agency.footer_credit`, `reviews.trusted_shops`, `reviews.count`. Plus `company.legal_form`, which is a **column**, not a signal, because `company_profile` reads it from the company row.

**Not written, deliberately:** everything Phase 2 owns (`perf.lighthouse_performance`, `impressum.gf_count`, `impressum.owner_named`, `ai.*`), and the two sample URLs — `catalog.product_sample_url` (A5) and `content.blog_sample_url` (A6) — which `fetch` writes because they record fetch-time decisions.

279 tests pass; `ruff check` and `ruff format --check` clean.

---

## 2. The §10.3 question, answered — recommendation ratified and built

**What the stage does now.** When a site has sitemaps but no URL matching any product pattern, it writes **no** `catalog.product_url_count` and instead writes `catalog.not_measurable` (`value_num` 1, `value_text` naming the reason). Three states, distinguishable:

| state | meaning | written |
|---|---|---|
| counted | N product URLs identified | `catalog.product_url_count = N` |
| not measurable | URLs exist, none identifiable as products | `catalog.not_measurable = 1` + reason |
| not measured | no sitemap on disk at all | neither |

The third row matters as much as the second: "we could not tell" and "we never looked" are different, and only the first is a property of the shop.

**My recommendation was to promote this to a review reason, not just a signal** — a signal is read by the scorer and **nobody is shown it**, while the company that needs a human is a shop where three rules went quiet at once. **Ratified, and built:** `catalog_not_measurable` is a §6.4 soft flag as of migration 003, raised wherever the signal is written. The signal stays and carries the reason text, which a flag has no room for; the flag carries the routing. Same division as `blog_date_unparseable`.

**One caveat that changes the shape of the problem, found by running this against the corpus.** The three-state rule catches *zero* matches. It does not catch *few* matches, and few-matches looks exactly like a small catalogue. The case this was raised on was `smile-store.de`, counted at 6 against a real catalogue of ~360.

**That case has since been explained, and it was not an instance of the problem.** The shop publishes all 194 of its products in a sitemap shard named `articles`, which matched none of the four filename conventions in the pattern list; the count of 6 came from the path-pattern fallback while the primary instrument sat unread in the index. With M1.24 it reports 194 from Tier 1, `qual.product_depth` fires correctly, and B7 fires on evidence rather than luck. See `docs/catalogue-recount-findings.md`.

**The question stays open with less evidence than it had.** An undercount is still worse than an unmeasurable because it does not announce itself — but the corpus now contains no known instance of a genuinely small catalogue being mistaken for one, which is not the same as there being none. Inventing a threshold on zero observations is the plausibility-over-evidence error M1.4 and M1.9 were about. §10.3 carries the question; the partial answer already shipped is that the tier travels with the count, so a low number from `product_sitemap` is the shop's own statement and a low number from `sitemap_path_pattern` is ours.

---

## 3. Coverage is uneven, and by how much

The corpus is 13 shops: **7 Shopify, 4 JTL, 1 WooCommerce, 1 Shopware 5.** So:

| platform | shops | signature status | parsers exercised against |
|---|---|---|---|
| Shopify | 7 | observed, matched 7/7 | several real pages |
| JTL | 4 | observed, matched 4/4 | several real pages |
| WooCommerce | **1** | observed, matched 1/1 | **one page** |
| Shopware 6 | **0** | **never matched a real shop** | nothing |
| Shopware 5 | 1 | **not detected at all** (M1.11) | one page, as a *negative* |

Three things follow that a reader should not have to infer:

- **WooCommerce is n=1.** One shop matched `wp-content` + `woocommerce`. That is one observation, not a validated signature. **And its catalogue path has never been exercised at all**, because that shop serves no sitemap: `propellerdiscount.de` reaches "not measured" without any tier of A5's hierarchy running. M1.24's Tier 1 has therefore never been tested against WooCommerce on real data, only against the `product-sitemap.xml` filename convention in the test suite. Whether Yoast's `article`/`post` shard naming would collide with M1.27's ambiguity rule is, on this corpus, unanswerable.
- **Shopware is worse than n=1.** The signature in §5.3 is a Shopware **6** path (`/bundles/storefront/`) and no Shopware 6 shop has ever been seen. The one Shopware shop is version 5, matches nothing, and `tests/test_parsers.py::test_shopware_5_is_knowingly_undetected` pins that as expected behaviour so it is visible in the suite rather than looking like an oversight. It will fail loudly the day a signature is added — which is the point.
- **Everything M1.24/M1.27 knows about semantically named shards comes from that same undetected Shopware 5 shop.** `articles` is observed on `smile-store.de` and nowhere else, and the German `artikel`/`produkte` are observed nowhere at all. The rule is n=1 and says so in the code.

`platform.detected` is the input to `qual.ecommerce_platform` (+15), so an undetected platform is −15. On this corpus that is `smile-store.de`, for being on the older version of a supported platform — the same shop whose catalogue the tool could not count until M1.24, which is a coincidence worth noticing: the shop the instruments know least about is the one that most exercises them.

---

## 4. Fixtures: harvested where safe, hand-written where not

`tests/fixtures/extract/` holds two kinds of file, and `README.md` there explains the split.

**Harvested (`platform-*.html`).** Real markup fragments from the crawled homepages, trimmed to the smallest span carrying the signature. Script tags, class names and asset paths — no personal data — so they are committed as served, with provenance in a header comment.

**Hand-written (`impressum-*.html`).** Modelled on the observed structures, with `Max Mustermann` / `Musterstraße 1` / `50667 Musterstadt` throughout.

**They are hand-written because redacting them failed, and the failure is worth recording.** The brief says Impressum content must be redacted before it is committed, and I tried that first: a redactor keyed on the names, streets and towns actually present. It left a real director's name, a real town and a partial VAT number in a file that was one `git add` from being committed — because an Impressum is *made of* personal data and names appear in positions no role-anchored pattern anticipates (`Geschäftsführer: A · B`, a surname trailing a company name). Making the redactor generic caught more and still leaked.

A redactor that must be perfect on adversarial input is the wrong tool when the alternative is under our control. The real names were never what the parser needed: it needs the *structure* — which anchor phrase introduces the block, what noise precedes it, whether a form is stated at all — and that is reproduced exactly. **No content from a real Impressum is committed anywhere in this branch.**

`impressum-vendor-noise-first.html` carries the case that matters most: a cookie-consent vendor's `GmbH` appearing before the operator's details, which a naive first-match returned on two real shops.

---

## 5. What running it against the corpus showed

All 13 companies, from `portal extract-p1`. Catalogue counts are **post-M1.24/M1.25/M1.26** — five of the eight measurable counts changed, and `docs/catalogue-recount-findings.md` is the account of why:

| domain | platform | catalogue | tier | blog | last post | product schema | legal form |
|---|---|---|---|---|---|---|---|
| bio-fleischer-laden.de | Shopify | 306 | product_sitemap | yes | 2022-12-01 | 1 | GmbH |
| blackpolish.de | Shopify | 22 | product_sitemap | yes | — | 0 | — |
| doonails.de | Shopify | 389 | product_sitemap | yes | — | 1 | Ltd |
| ekomia.de | Shopify | 335 | product_sitemap | yes | 2025-12-08 | 0 | — |
| germanelectronic.de | JTL | *not measured* | — | no | — | — | — |
| navucko.com | Shopify | 72 | product_sitemap | yes | 2026-06-21 | 1 | — |
| opulent-wohnen.com | JTL | **not measurable** | — | no | — | — | — |
| propellerdiscount.de | WooCommerce | *not measured* | — | no | — | 1 | GmbH |
| smile-store.de | *(undetected)* | 194 | product_sitemap | yes | — | 0 | — |
| smoke2u.de | JTL | **not measurable** | — | no | — | — | GmbH & Co. KG |
| snocks.com | Shopify | 462 | product_sitemap | yes | — | 1 | GmbH |
| verpackungskoenig.de | JTL | **not measurable** | — | no | — | — | GmbH |
| zecplus.de | Shopify | 242 | product_sitemap | no | — | 1 | GmbH & Co. KG |

Three findings that need M3's attention:

**(a) and (b) — the blog date and the Article markup — are closed by A6 (M1.29), and the table above still shows the state before it runs.** Five of the seven detected blogs yielded no date, and it was never a parser weakness: Shopify blog *indexes* carry no `<time>`, no `datePublished` and no `Article` markup at all. All three live on the post. `schema.article_present` was `0` on **every** blog index in the corpus — a wrong "checked and absent" — and §6.2's ladder was raising `blog_date_unparseable` for the majority platform on missing evidence that was never on the page we fetched.

A6 samples one article under the fetched index and reads both from there; A6.1 writes neither where no article is obtained, which is why the `last post` and `product schema` columns above have *fewer* entries than they did before — **the signals are now correctly silent, pending a fetch that stores the articles**. See §8.

**(c) Three shops have no legal form because their Impressum is not on disk or states none** — `ekomia.de`'s is robots-disallowed (M1.12, correctly refused), and `blackpolish.de`/`navucko.com`/`smile-store.de` state none. This is §10.2's open question showing up as data: 4 of 13 are sole traders the predicate cannot see.

---

## 6. Bugs found by running it, not by testing it — and the command that now looks for them

Four, all found by reading a run's output against the previous run's, all fixed and pinned:

1. **Gzipped sitemaps were destroyed before parsing.** The stage read bodies as `str` and re-encoded them; a `.xml.gz` shard is binary, and decoding with `errors="replace"` corrupts the gzip header. Three real JTL catalogues silently became "0 URLs" — which the three-state rule then faithfully recorded as *not measurable*, so the bug hid behind a correct-looking answer.
2. **The moved-domain adoption (M1.18) did not reach this stage.** `same_site` was anchored on `company.domain`, so `doonails.de`'s 1,319 catalogue URLs all read as off-site and the shop came out as "not measurable". The same silent blinding M1.18 fixed in `fetch`, reappearing one stage later; it now reads `site_domain`.
3. **"Powered by JTL-Shop" was being read as an agency credit** on `smoke2u.de`, which would fire `neg.has_agency` against a shop for choosing a shop system. Platform credits are now excluded from `agency.footer_credit`, and §10 names the exclusion explicitly rather than leaving it as a fixed bug — the same string had already caused the opposite defect as a platform signature (M1.9).
4. **`<image:loc>` was read as a page.** Namespaces were stripped before tag names were compared, so every Shopify product sitemap parsed as twice its length. It reached no count only because Shopify serves images from a CDN and `same_site` discarded them.

And one I introduced while fixing the others: excluding blog-shard URLs from the catalogue count is right, but I excluded them from `page_urls` entirely, which took `/blogs/…` away from blog *path* detection and flipped `snocks.com` — 107 blog URLs, a July post — to `content.blog_exists = 0`. That is `opp.no_blog`'s **+25** against a shop that publishes weekly, the single worst error §6.2 can make.

**Bugs 1, 4 and that last one share a shape, and it is the shape a test suite cannot catch:** each produced a *plausible* output. The gzip defect surfaced as a correct-looking "not measurable"; halved counts looked like counts; `blog_exists = 0` looks like a shop with no blog. A test asserts what its author expected, and in all three cases the author of the test was the author of the defect. What caught them was comparing a run against the previous one, line by line.

That works at 13 domains and cannot work at 500, which is the target — so it is now `portal diff-signals` (M1.28). It reports every key whose value changed, appeared or disappeared between two runs, grouped by domain, because a defect shows as a *pattern* across domains: the gzip defect was three JTL shops losing the same key at once, the `<image:loc>` defect was seven Shopify shops halving the same one. Either reads as noise in a flat list and as a shape when grouped. It reads only, costs nothing, and refuses rather than guesses when there is only one run to compare.

---

## 7. Not built, on purpose

No scoring — `score --phase 1` is M3 and nothing here computes a band. No Phase-2 signals.

---

## 8. The one thing that is built and not yet exercised against real sites

**A6 changes the fetch stage, and no fetch has run since.** It is covered end to end against the loopback fixture server — the `blog_article` artifact is fetched, the sample is selected by the stated rule, and the extract side reads the date and the markup off it. What has *not* happened is a `portal fetch` against the 13 real domains, so no `blog_article` body is on disk for any of them.

The visible consequence is in §5's table: `content.blog_last_post` and `schema.article_present` are now **unwritten** for the seven shops with blogs. That is A6.1 behaving correctly — the article was never retrieved, so nothing is claimed — but it means the corpus currently carries *less* blog evidence than it did before A6, not more, and it stays that way until a fetch runs.

The selection rule itself was replayed against the sitemaps already on disk, which needs no requests. It resolves on all seven blogs, every one from Tier 1:

| shop | index (M1.15) | selected article |
|---|---|---|
| bio-fleischer-laden.de | `/blogs/rezepte` | `/blogs/rezepte/bbq-schweinenacken` |
| blackpolish.de | `/blogs/news` | `/blogs/news/blackpolish-is-live` |
| doonails.de | `/blogs/press-ons-instructions` | `/blogs/press-ons-instructions/pedicure-press-ons-instruction` |
| ekomia.de | `/blogs/inside-ekomia` | `/blogs/inside-ekomia/arbeiten-rueckenuebungen-fuer-das-buero` |
| navucko.com | `/blogs/news` | `/blogs/news/broome-street-temple-x-navucko` |
| smile-store.de | `/magazin` | `/magazin/auszeichnung-dental-champions-in-der-apotheke` |
| snocks.com | `/blogs/lifestyle` | `/blogs/lifestyle/das-poloshirt-und-was-man-daruber-wissen-muss` |

**Cost of the run that would close this:** seven extra requests over the whole corpus, subject to §5.2's limiter and robots rules like any other. A crawl touches third-party servers, so it waits on a decision rather than being taken as implied by "build it".
