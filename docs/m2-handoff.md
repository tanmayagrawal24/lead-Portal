# M2 handoff — `portal extract-p1`

**Branch:** `m2-extract-p1` (not merged)
**Date:** 2026-08-15
**Spec:** §5.3, plus §10.3's three-state question, which this milestone had to answer to be implementable.

Assumes no prior context beyond §5.3 and `docs/first-crawl-findings.md`.

---

## 1. What was built

`portal extract-p1` reads artifacts already on disk and writes deterministic signals. **It makes no HTTP requests at all.** A parser fix is re-applied to the whole corpus by re-running it, with no third-party server involved — which is what makes it safe to iterate on, and what §4's "re-scoring never needs a refetch" depends on.

| Module | Responsibility |
|---|---|
| `portal/parsers.py` | Pure functions over HTML: platform signatures, JSON-LD, hreflang, agency credit, legal form, blog dates and counts |
| `portal/extract.py` | The stage: reads artifacts, applies parsers, writes signals under §4's M1.5 idiom |
| `portal/cli.py` | `portal extract-p1` |

**Signals written:** `platform.detected`, `content.blog_exists`, `content.blog_last_post`, `content.blog_post_count`, `catalog.product_url_count`, `catalog.not_measurable` (new — see §2), `schema.article_present`, `schema.product_present`, `meta.description_length`, `i18n.hreflang_count`, `agency.footer_credit`, `reviews.trusted_shops`, `reviews.count`. Plus `company.legal_form`, which is a **column**, not a signal, because `company_profile` reads it from the company row.

**Not written, deliberately:** everything Phase 2 owns (`perf.lighthouse_performance`, `impressum.gf_count`, `impressum.owner_named`, `ai.*`), and `catalog.product_sample_url`, which `fetch` writes because it records a fetch-time decision.

225 tests pass; `ruff check` and `ruff format --check` clean.

---

## 2. The §10.3 question, answered — and my recommendation

**What the stage does now.** When a site has sitemaps but no URL matching any product pattern, it writes **no** `catalog.product_url_count` and instead writes `catalog.not_measurable` (`value_num` 1, `value_text` naming the reason). Three states, distinguishable:

| state | meaning | written |
|---|---|---|
| counted | N product URLs identified | `catalog.product_url_count = N` |
| not measurable | URLs exist, none identifiable as products | `catalog.not_measurable = 1` + reason |
| not measured | no sitemap on disk at all | neither |

The third row matters as much as the second: "we could not tell" and "we never looked" are different, and only the first is a property of the shop.

**My recommendation: promote this to a review reason, not just a signal.** `catalog.not_measurable` is currently a signal, which needs no migration and no §4 change — that is why it is a signal today. But a signal is read by the scorer, and **nobody is shown it**. The company that needs a human is a shop where three rules went quiet at once; §6.4's review queue is the mechanism that exists for exactly that, and this is the same shape as `blog_date_unparseable` — an instrument that could not measure, routed to a person rather than guessed at.

Concretely, for M3: add `catalog_not_measurable` to §6.4's soft flags and raise it wherever this signal is written. It needs a `CHECK` widening (migration 003) and a §6.4 entry. I have not done it here because it is a scoring-model and schema change, and the M1.21/M1.22 precedent is that those get ratified before they are built.

**One caveat that changes the shape of the problem, found by running this against the corpus.** The three-state rule catches *zero* matches. It does not catch *few* matches, and few-matches looks exactly like a small catalogue:

> `smile-store.de` (Shopware 5) has **2,494** sitemap URLs. Twelve contain `/detail/`, six survive the filters, so `catalog.product_url_count = 6`. Its real catalogue is ~360 products, living under category-shaped first segments (`/zahnpasta/…`, `/zahnbuersten/…`, `/zahnpflege/…`). The count is off by roughly fifty times, and it is *written*, so it reads as measured.

That costs it `qual.product_depth` (needs ≥ 20) wrongly, and it passes B7 (needs ≥ 5) by luck rather than by evidence. **An undercount is worse than an unmeasurable, because it does not announce itself.** So the recommendation above should probably become a *measurability* judgement rather than a binary — but what threshold makes a count untrustworthy is not something a 13-shop corpus can answer, and inventing one now would be exactly the plausibility-over-evidence error M1.4 and M1.9 were about. **Flagging it as the open question rather than picking a number.**

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

Two things follow that a reader should not have to infer:

- **WooCommerce is n=1.** One shop matched `wp-content` + `woocommerce`. That is one observation, not a validated signature, and the one WooCommerce shop in the corpus also serves no sitemap — so the WooCommerce *catalogue* path has never been exercised at all.
- **Shopware is worse than n=1.** The signature in §5.3 is a Shopware **6** path (`/bundles/storefront/`) and no Shopware 6 shop has ever been seen. The one Shopware shop is version 5, matches nothing, and `tests/test_parsers.py::test_shopware_5_is_knowingly_undetected` pins that as expected behaviour so it is visible in the suite rather than looking like an oversight. It will fail loudly the day a signature is added — which is the point.

`platform.detected` is the input to `qual.ecommerce_platform` (+15), so an undetected platform is −15. On this corpus that is `smile-store.de`, for being on the older version of a supported platform.

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

All 13 companies, from `portal extract-p1`:

| domain | platform | catalogue | blog | last post | product schema | legal form |
|---|---|---|---|---|---|---|
| bio-fleischer-laden.de | Shopify | 306 | yes | 2022-12-01 | 1 | GmbH |
| blackpolish.de | Shopify | 22 | yes | — | 0 | — |
| doonails.de | Shopify | 389 | yes | — | 1 | Ltd |
| ekomia.de | Shopify | 2861 | yes | 2025-12-08 | 0 | — |
| germanelectronic.de | JTL | *not measured* | no | — | — | — |
| navucko.com | Shopify | 144 | yes | 2026-06-21 | 1 | — |
| opulent-wohnen.com | JTL | **not measurable** | no | — | — | — |
| propellerdiscount.de | WooCommerce | *not measured* | no | — | 1 | GmbH |
| smile-store.de | *(undetected)* | 6 ⚠ | yes | — | 0 | — |
| smoke2u.de | JTL | **not measurable** | no | — | — | GmbH & Co. KG |
| snocks.com | Shopify | 4620 | yes | — | 1 | GmbH |
| verpackungskoenig.de | JTL | **not measurable** | no | — | — | GmbH |
| zecplus.de | Shopify | 242 | no | — | 1 | GmbH & Co. KG |

Three findings that need M3's attention:

**(a) `content.blog_last_post` is unobtainable for most Shopify blogs.** Five of the nine detected blogs yield no date, and it is not a parser weakness: Shopify's blog *index* pages carry **no `<time>` element and no `datePublished`** at all — those live on the article pages, which we do not fetch. §6.2's ladder therefore hits its `blog_last_post is NULL` branch and raises `blog_date_unparseable` for the majority platform in the corpus. That is the ladder behaving correctly on missing evidence, but a review queue that fills with most of the corpus is not a review queue. Fixing it means fetching one article per blog — a §5.2 change and one more request per company, not an M2 change.

**(b) `schema.article_present` is 0 on every blog index.** Same cause: `Article`/`BlogPosting` markup lives on the post, not the listing. §5.3 says to check "the blog index", and on real shops that is the wrong page for this signal. Worth a §5.3 correction.

**(c) Three shops have no legal form because their Impressum is not on disk or states none** — `ekomia.de`'s is robots-disallowed (M1.12, correctly refused), and `blackpolish.de`/`navucko.com`/`smile-store.de` state none. This is §10.2's open question showing up as data: 4 of 13 are sole traders the predicate cannot see.

---

## 6. Bugs found by running it, not by testing it

Three, all found by reading the first output and all now fixed and pinned:

1. **Gzipped sitemaps were destroyed before parsing.** The stage read bodies as `str` and re-encoded them; a `.xml.gz` shard is binary, and decoding with `errors="replace"` corrupts the gzip header. Three real JTL catalogues silently became "0 URLs" — which the three-state rule then faithfully recorded as *not measurable*, so the bug hid behind a correct-looking answer.
2. **The moved-domain adoption (M1.18) did not reach this stage.** `same_site` was anchored on `company.domain`, so `doonails.de`'s 1,319 catalogue URLs all read as off-site and the shop came out as "not measurable". The same silent blinding M1.18 fixed in `fetch`, reappearing one stage later; it now reads `site_domain`.
3. **"Powered by JTL-Shop" was being read as an agency credit** on `smoke2u.de`, which would fire `neg.has_agency` against a shop for choosing a shop system. Platform credits are now excluded from `agency.footer_credit`.

Bug 1 is the one worth remembering: a defect that produces a *plausible* output is invisible to a test suite written by the same person who wrote the defect.

---

## 7. Not built, on purpose

No scoring — `score --phase 1` is M3 and nothing here computes a band. No Phase-2 signals. No migration: `catalog.not_measurable` is a signal precisely so that M2 needs no schema change, and the recommendation to promote it (§2) is left for ratification.
