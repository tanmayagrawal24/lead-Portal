# Second crawl — verification of P0, and what the P1s actually did

**Run:** `run 2`, 2026-08-15T11:56:08Z, same 13 domains, one pass.
**Code under test:** M1.12 (robots on every hop), M1.13 (Tier 1 path anchor), M1.14 (`/blogs/`).
**Result:** P0 confirmed in the wild. The JTL zero-candidate finding **survives**. Both P1s were incomplete in ways only the run showed, and both are now fixed (M1.15–M1.17).

---

## 1. P0 confirmed on the two domains that produced it

Neither `snocks.com` nor `smoke2u.de` yields a 200 on a disallowed URL in `run 2`.

The evidence is that the run-1 success rows **were not touched**. `artifact` has no `run_id`, but a 200 row's `last_checked_at` only advances when that URL is fetched with 200 again, so a stale timestamp is proof the URL was not re-fetched:

| domain | disallowed URL | run-1 200 row `last_checked_at` | still that value after run 2? |
|---|---|---|---|
| snocks.com | `/policies/legal-notice` | `11:07:18` | **yes** — not re-fetched |
| smoke2u.de | `/Impressum` | `11:07:29` | **yes** — not re-fetched |

And the hops that previously reached them are now recorded refusals:

```
snocks.com   /impressum   301 → redirect_refused: https://snocks.com/policies/legal-notice   11:57:37
snocks.com   /impressum/  301 → redirect_refused: https://snocks.com/policies/legal-notice   11:57:38
smoke2u.de   /impressum   301 → redirect_refused: https://www.smoke2u.de/Impressum           11:57:52
```

with matching run notes (`redirect refused by robots.txt on snocks.com: …`). The `robots_disallowed` rows for the footer links also advanced to run-2 timestamps, so step 1 still refuses them as it did before. Both routes in — different path, and differing only in case — are closed.

**A side effect worth stating:** `smoke2u.de`'s Impressum is now found legitimately. Its robots.txt disallows `/Impressum` but says nothing about `/Imprint`, and the probe list reaches it — a 6.4 KB genuine Imprint page. So on that domain P0 cost nothing at all.

`snocks.com` is the opposite case, and it exposed a new defect — see §4.

## 2. Did the JTL zero-candidate result survive Tier 1 firing? — Yes

**It survives, and the reason is not the anchor bug.** With M1.13 in place, the four JTL shops still match no product sitemap:

| domain | sitemaps fetched | Tier 1 matches |
|---|---|---|
| germanelectronic.de → lampenflut.de | 1 (`/sitemap.xml`, 404) | 0 |
| opulent-wohnen.com | 3 | 0 |
| smoke2u.de | 3 | 0 |
| verpackungskoenig.de | 3 | 0 |

These shops do not ship a product sitemap. Each serves one index naming exactly one shard:

```xml
<sitemapindex>
  <sitemap><loc>https://www.smoke2u.de/export/sitemap_0.xml.gz</loc><lastmod>2026-08-15</lastmod></sitemap>
</sitemapindex>
```

One undifferentiated gzipped shard, products and content mixed, no `/sitemap/product` and no per-type shards. So there is nothing for Tier 1 to select from, the fall-through to Tier 2 is correct rather than a bug, and Tier 2 then fails for the reason already reported: JTL product URLs are root-level SEO slugs, structurally indistinguishable from category URLs.

**This does not prove JTL never ships a product sitemap** — it proves these four installations do not. JTL's sitemap export is configurable. The `/sitemap/product` pattern stays in the Tier 1 list, still unobserved, costing nothing.

### Tier 1 was masked in this run, and that is by design

No `run 2` sample came from Tier 1, because **Tier 0 reuse short-circuited selection on all 8 domains that already had one** — every sample line reads `product sample (reuse)`. A5.1 requires exactly that: the evidence a score points at must not move under it.

So Tier 1's revival could not be observed from run output. It was measured directly instead, by replaying the stored sitemaps through the fixed code — which is what caught §3.

## 3. M1.13 would have shipped a regression: Tier 1 selecting a locale root

Replaying Tier 1 against the stored sitemaps and comparing with Tier 2's actual choice:

| domain | Tier 1 would have picked | Tier 2 actually picked |
|---|---|---|
| ekomia.de | `https://ekomia.de/de-at` | `/de-at/products/alma-erweiterung` |
| navucko.com | `https://navucko.com/en` | `/en/products/bade-strandtuch-amore` |
| snocks.com | `https://snocks.com/de-ch` | `/de-ch/products/1-2-zip-sportshirt-langarm-herren` |
| bio-fleischer-laden.de, blackpolish.de, zecplus.de | same as Tier 2 | — |

Shopify lists the **locale storefront root** inside that locale's *product* sitemap, and Tier 1 waives the path-pattern requirement (`require_pattern=False`) because membership is supposed to be the evidence. The only guard was "the homepage is never a product page", which tests `/` and misses `/de-at`.

Three of six shops would have sampled a **listing page**, feeding `schema.product_present` a wrong +10 — the exact error M1.4 dropped `/p/` over. Fixed as M1.16: a multi-locale shop has more than one homepage. With the guard, Tier 1 and Tier 2 agree on all six.

**This also corrects a claim in the first findings doc.** I wrote there that reviving Tier 1 "would not change any sample chosen in this run". That was true only by accident — because Tier 1 was dead. Once revived, it changes three of six, and for the worse until M1.16. Run 2 was not affected: Tier 0 reuse meant no locale root was ever fetched.

## 4. M1.14 was inert, and P0 exposed a second Impressum defect

### 4.1 Blog detection now fires — and every index 404'd

`/blogs/` works. Seven domains found a blog path where run 1 found none. Then all seven index fetches failed:

| domain | index fetched | status |
|---|---|---|
| bio-fleischer-laden.de, blackpolish.de, ekomia.de, navucko.com, snocks.com | `…/blogs` | **404** |
| doonails.de | `https://www.doonails.com/blogs` | **404** |
| smile-store.de | `…/magazin` | **404** |

`/blogs` is not a page on Shopify — `/blogs/news` is. Shipping M1.14 alone would have bought seven wasted 404 requests per run and no blog data at all. Fixed as M1.15: fetch the shallowest URL actually **observed** under the blog path. Replayed against the stored crawl output that resolves to `/blogs/rezepte`, `/blogs/news`, `/blogs/inside-ekomia`, `/blogs/lifestyle`, `/magazin/` — real URLs rather than a guess. Whether each returns 200 is unverified until the next crawl; I did not run a third crawl to check.

`zecplus.de` still reports no blog path, correctly for this instrument: its blog is on `blog.zecplus.de`, a subdomain with no path to match (recorded in §5.3 as open).

### 4.2 The homepage was stored as an Impressum

`snocks.com`'s run-2 `impressum` artifact is **1,336,476 bytes, titled "SNOCKS. feel the fit.", with content hash `f28c28ad127b` — byte-identical to its homepage.**

The chain: the real Impressum is robots-disallowed, so P0 refuses it (correctly), probing runs, and `/imprint` redirects to `/#gbaid979323` — the homepage. A 200 came back and was stored as the Impressum.

This is a defect P0 *revealed* rather than caused: before M1.12 the probe chain terminated at the real page, so the soft redirect was never reached. §5.5b would have handed the homepage to the Impressum extraction and received a confident answer about the wrong page. Fixed as M1.17.

## 5. Politeness

Same measurement as run 1 — elapsed span per politeness key against `(rows − 1) × interval`, which is a lower bound, not a proof. Every key passes. `ekomia.de` reads 55 s against a 56 s requirement, the same ±1 s truncation plus non-request rows as last time.

`Crawl-delay` was honoured again where it applies to us: `propellerdiscount.de` 5 rows over **50 s**, `verpackungskoenig.de` 6 over **25 s**, `lampenflut.de` 4 over **21 s**.

**Unchanged and still true:** the crawl records no request-issue timestamp, so this cannot prove the 1 req/s floor. That remains P2, and with a confirmed robots defect now behind us, "correct but undemonstrable" is the right way to describe the current state of the politeness rule.

## 6. Net change, run 1 → run 2

| | run 1 | run 2 |
|---|---|---|
| domains reached | 13 | 13 |
| 200s on robots-disallowed URLs | **2** | **0** |
| blog paths found | 1 | 7 |
| blog indexes successfully fetched | 0 | 0 *(now addressed by M1.15)* |
| product samples | 9 | 9 (all Tier 0 reuse) |
| Impressum: footer / probe / none | 9 / 3 / 1 | 8 / 4 / 1 |
| homepage stored as an Impressum | 0 | **1** *(now addressed by M1.17)* |
| exclusions | 0 | 0 |

The Impressum shift is snocks.com moving from footer to probe, which is P0 working.

## 7. What this second pass says about the method

Three of the four defects fixed in this round — M1.15, M1.16, M1.17 — were found by **reading the output of a change rather than by the tests that accompanied it**. Each change had passing tests written against the shape I expected. M1.16 in particular would have shipped a wrong +10 to three of thirteen companies and no test I wrote would have failed, because I tested that Tier 1 fires, not what it selects.

The pattern is the same one M1.12 came from: correct mechanism, unexamined consequence. Running the thing and reading the rows is what catches that class, and it is worth the second crawl every time a parser changes.
