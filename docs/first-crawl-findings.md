# First real crawl — findings

**Run:** `run 1`, 2026-08-15T11:05:49Z, `portal fetch --seed seeds/candidates.csv`
**Corpus:** the 13 German shops in `seeds/candidates.csv`. No domain outside that file was contacted.
**Result:** 13/13 reached, 0 exclusions, 201 artifact rows, 185 stored bodies, 1 review flag, 9 product samples.
**Status:** this is an inspection of the output, not a pass/fail report. Nothing here was fixed; the crawl was run once and read.

The headline: **the crawler is polite and correct about robots.txt in every respect the M1 tests covered, and wrong in three ways they could not cover.** One of those three is a robots violation. Two of them will silently corrupt M2's scoring if M2 is written against the current parsers.

---

## 1. The three findings that matter

### 1.1 The crawler fetched two pages that robots.txt disallows

**This is a compliance defect, not a quality one, and it should be fixed before the next crawl.**

On `snocks.com` and `smoke2u.de` the artifact table contains, for the same URL, both a refusal and a successful fetch:

| domain | robots rule (in the `*` group) | row 1 | row 2 |
|---|---|---|---|
| snocks.com | `Disallow: /policies/` | `/policies/legal-notice` → `robots_disallowed` | `/policies/legal-notice` → **200, body stored** |
| smoke2u.de | `Disallow: /Impressum` | `/Impressum` → `robots_disallowed` | `/Impressum` → **200, body stored** |

The sequence is the same on both. Step 1 of the Impressum two-step finds the footer link, the policy correctly refuses it, and the refusal is recorded. Step 2 then probes `/impressum` — which robots *does* allow — and the site **redirects** that probe onto the disallowed URL. The hop is same-host, so it is followed, and the page is fetched.

Root cause: `get()` in [fetch.py:278-285](portal/fetch.py#L278-L285) evaluates `policy.allows(url)` on the URL it is *asked* for. M1.1 made redirect hops re-check robots **when the host changes** ([fetch.py:210](portal/fetch.py#L210) tests `same_site`). A hop that changes only the *path* is not re-checked. The M1 handoff describes the fix as "a host change followed only after that host's robots.txt has been consulted" — that is exactly what was implemented, and the path case was never in scope.

Note the two routes in, because a fix must close both:

- **snocks.com** — `/impressum` and `/policies/legal-notice` are simply different paths; the redirect crosses a `Disallow` boundary.
- **smoke2u.de** — the probe differs from the rule *only in case*. `Disallow: /Impressum` does not match `/impressum`, correctly (robots paths are case-sensitive), so the probe is allowed; the server then 301s it to the capitalised path, which is disallowed.

Neither is exotic. Both would recur on the next run.

**Recommendation:** re-evaluate `policy.allows()` on every hop target, not only on host changes. The policy object is already in hand at that point, so this is a check in the hop loop, not new plumbing. A refused hop already has a recording idiom (`redirect_refused`) — reuse it.

### 1.2 `/blogs/` — Shopify's blog path — matches nothing, on 5 of 7 Shopify shops

Every domain in this crawl reported `no blog path found` except `smile-store.de`. Five of them have substantial, live blogs:

| domain | blog URLs in its sitemaps | detected? |
|---|---|---|
| snocks.com | 670 | **no** |
| ekomia.de | 396 | **no** |
| bio-fleischer-laden.de | 26 | **no** |
| navucko.com | 18 | **no** |
| blackpolish.de | 4 | **no** |
| zecplus.de | 0 | no — **correct**, it has no blog |
| doonails.de | not measurable | no — its shards were never expanded (§6) |
| smile-store.de | 61 | yes (`/magazin`) — but the index 404s (§9) |

`BLOG_SEGMENTS` in [impressum.py:31](portal/impressum.py#L31) contains `blog`; Shopify serves `/blogs/` — plural — as in `https://blackpolish.de/blogs/news`. `_BLOG_PATH` anchors on the whole segment, so `blogs` never matches `blog`. The locale prefix is not the problem: the regex already allows `/nl-be/blogs/…`, and it is the plural alone that fails.

The consequence lands in M2, not here. §6.2's ladder reads `content.blog_exists`, and its first rung is `opp.no_blog` **+25** — the largest single award in ruleset v3. On this corpus it would fire on five shops that publish actively, including one with 670 blog URLs. That is a 25-point error in the wrong direction on 38% of the sample, and it is invisible: no error, no flag, just a note saying no blog was found.

**Recommendation:** add `blogs` to `BLOG_SEGMENTS` (§5.3's vocabulary). Observed on 5 shops, so it clears the evidence bar M1.4/M1.9 set.

### 1.3 Tier 1 never fired — and the reason is a regex anchor, not a wrong filename

Zero of 143 sitemap artifacts matched a Tier 1 product-sitemap pattern. Every product sample came from Tier 2 (7) or the homepage-links fallback (2). The handoff predicted Tier 1 might be "silently dead code"; it is, but not for the reason it guessed.

Shopify's real filename is exactly what §5.2 assumed — `sitemap_products_1.xml`. What was not assumed is that Shopify appends a query string:

```
https://snocks.com/sitemap_products_1.xml?from=1932497715270&to=11010499674379
```

`_PRODUCT_SITEMAP_PATTERNS` ([sitemap.py:33-39](portal/sitemap.py#L33-L39)) anchors with `$`, and `is_product_sitemap` runs it against the **whole URL**. The `.xml` is no longer at the end, so it fails:

```
is_product_sitemap(<the URL above>)                  -> False
same patterns against path_of(<the URL above>)       -> True
is_product_sitemap(".../sitemap_products_1.xml")     -> True
```

So the pattern was right and the input was wrong. Matching on `path_of(url)` instead of the raw URL fixes all 7 Shopify shops at once.

Two things this does **not** fix, worth knowing before anyone assumes Tier 1 is now healthy:

- **JTL has no product sitemap at all.** All four JTL shops serve one undifferentiated `/export/sitemap_0.xml.gz`. Tier 1 is legitimately inapplicable there; see §4.
- **Fixing Tier 1 would not change any sample chosen in this run.** For every Shopify shop the Tier 2 candidate set already contained the same product URLs, and code-point ordering picks the same minimum. The gain is precision — Tier 1 sets `require_pattern=False`, which matters for SEO-rewritten catalogs — not a different answer today.

---

## 2. Per-domain table

`sample tier`: `T2` = sitemap path pattern, `HP` = homepage links, `—` = no candidates. `Impressum`: how it was found.

| # | domain | platform | final host | rows | sitemaps | product sample | tier | Impressum | notable |
|---|---|---|---|---|---|---|---|---|---|
| 1 | bio-fleischer-laden.de | Shopify | *(no redirect)* | 10 | 6 | `/products/aika-geflugel-gemuse-300g-tk` | T2 | footer | clean run; blog missed (26 URLs) |
| 2 | blackpolish.de | Shopify | *(no redirect)* | 10 | 6 | `/products/abgabe-reinigung-im-store-in-dusseldorf` | T2 | footer | sample is a **service SKU**, no LD `Product` |
| 3 | doonails.de | Shopify | **www.doonails.com** | 6 | 1 | `/products/babyboomer-gel-strips` | HP | footer | cross-domain: **5 sitemap shards never expanded** |
| 4 | ekomia.de | Shopify | *(no redirect)* | 56 | 47 | `/de-at/products/alma-erweiterung` | T2 | **none** | `no_impressum` flag is a **false positive**; 9 locales |
| 5 | germanelectronic.de | JTL | **lampenflut.de** | 5 | 1 (404) | — | — | probe | rebranded domain; sitemap 404; no sample |
| 6 | navucko.com | Shopify | *(no redirect)* | 14 | 10 | `/en/products/bade-strandtuch-amore` | T2 | footer | sample is the **English** locale |
| 7 | opulent-wohnen.com | JTL | www. (apex→www) | 6 | 3 | — | — | footer | gz shard, 682 URLs; **no sample** |
| 8 | propellerdiscount.de | WooCommerce | www. (apex→www) | 5 | 1 (404) | `/produkt/6-tlg-instrumentenset-…-kronos/` | HP | footer | `Crawl-delay: 10` honoured; placeholder sitemap ignored |
| 9 | smile-store.de | Shopware **5** | www. (apex→www) | 20 | 15 | `/detail/index/sArticle/1213` | T2 | footer | **platform undetected**; blog index 404 |
| 10 | smoke2u.de | JTL | www. (apex→www) | 7 | 3 | — | — | probe | **robots violation** (§1.1); gz shard, 4,296 URLs |
| 11 | snocks.com | Shopify | *(no redirect)* | 47 | 42 | `/de-ch/products/1-2-zip-sportshirt-langarm-herren` | T2 | probe | **robots violation** (§1.1); sample is **Swiss** locale |
| 12 | verpackungskoenig.de | JTL | *(no redirect)* | 6 | 3 | — | — | footer | `Crawl-delay: 5` honoured; gz shard, 2,075 URLs; **no sample** |
| 13 | zecplus.de | Shopify | www. (apex→www) | 9 | 5 | `/products/fight-post-fight` | T2 | footer | clean run; genuinely no blog |

---

## 3. The §6.3 handoff question: apex→www under real DNS and TLS

**It held.** The prediction that it would "appear on nearly every domain" was too pessimistic — it appeared on 5 of 13.

| shape | n | domains |
|---|---|---|
| apex → `www.` | 5 | opulent-wohnen.com, propellerdiscount.de, smile-store.de, smoke2u.de, zecplus.de |
| apex served directly, no redirect | 6 | bio-fleischer-laden.de, blackpolish.de, ekomia.de, navucko.com, snocks.com, verpackungskoenig.de |
| **cross-domain** redirect | 2 | doonails.de → www.doonails.com, germanelectronic.de → lampenflut.de |

**Did `artifact.url` record the final host?** Yes, on all 13 — verified by comparing every `homepage` row's URL host against its seed domain. The evidence link points where the content came from, including for both cross-domain moves. `body_path` stays under the *seed* domain's directory, which is right: the directory is keyed to the company, not the host.

**Did the shared politeness budget hold?** Honest answer: **the run is consistent with it, and the crawl output cannot prove it.** Nothing records a request-issue time. Two weaker measurements, and what each is worth:

- *Rejected as invalid:* gaps between stored-body write timestamps. Ten keys show sub-1s write gaps — but a write happens when a response *lands*, and response latency varies, so two requests exactly 1.0 s apart routinely produce a 0.7 s write gap. This measures the server's variance, not our spacing. I nearly filed it as a violation; it is not one.
- *Valid as a lower bound:* elapsed span per politeness key against `(requests − 1) × interval`. Every key passes. The apex/www keys — where M1.8's merge is the thing under test — pass with the apex `robots.txt` and the `www` pages counted against one budget: e.g. `zecplus.de` 9 rows over 12 s, `smile-store.de` 20 over 23 s, `smoke2u.de` 7 over 8 s. `ekomia.de` reads 54 s against a 55 s requirement, which is the ±1 s timestamp truncation plus one recorded row that involved no request at all (the `robots_disallowed` refusal).

The strongest *positive* evidence that the limiter is genuinely in the redirect path is the `Crawl-delay` hosts, where the interval is far enough above 1 s to survive the measurement noise: `propellerdiscount.de` 5 rows over **51 s** at a declared 10 s, `verpackungskoenig.de` 6 over **25 s** and `lampenflut.de` 4 over **20 s** at a declared 5 s.

**What remains unproven:** per-request spacing at sub-second resolution, under real DNS. The fixture-server tests remain the only measurement of that, and this crawl did not weaken them. If it matters more than that, `artifact` needs a request-issue timestamp; nothing else will answer it.

---

## 4. Product sampling: what Tier 2 actually selected

### Every sample is a real product page. None is a listing.

All 9 were re-parsed from the stored bodies:

| domain | LD-JSON `Product` | add-to-cart | price | title |
|---|---|---|---|---|
| bio-fleischer-laden.de | yes | yes | yes | AIKA Geflügel/Gemüse 300g TK |
| blackpolish.de | **no** | yes | yes | Abgabe & Reinigung im Store Düsseldorf |
| doonails.de | yes | yes | yes | Doonails – Gel Strips – Babyboomer |
| ekomia.de | **no** | yes | yes | Regalmodul Alma (2 Ebenen) |
| navucko.com | yes | yes | yes | Buy Beach Towel AMORE White Red |
| propellerdiscount.de | yes | yes | yes | Instrumentenset von FARIA "Kronos" |
| smile-store.de | **no** | yes | yes | Ultraschallzahnbürste Megasonex M8 |
| snocks.com | yes | yes | yes | 1/2 Zip Sportshirt langarm Herren |
| zecplus.de | yes | yes | yes | FIGHT+ POST FIGHT, 80 Kapseln |

The four §5.2 filters did their job: no category page, no query-string variant, no bare `/products/` collection listing reached the candidate set. The three `Product`-absent readings are **genuine absences**, so M2 will correctly award `opp.no_product_schema` on those three — the A5.6 guard is satisfied, since all three were fetched with HTTP 200.

Two quality problems, though, both caused by the *ordering* rule rather than the filters:

**(a) Code-point minimum systematically prefers a foreign locale.** Three of nine samples are not the German page:

- `ekomia.de` → `/de-at/products/…` (Austrian)
- `snocks.com` → `/de-ch/products/…` (Swiss)
- `navucko.com` → `/en/products/…` (English — the stored title is literally "Buy Beach Towel AMORE White Red")

This is structural, not luck. Comparing `https://ekomia.de/de-at/…` against `https://ekomia.de/products/…`, the first difference is `d` < `p`, so the locale-prefixed path always wins. Every multi-locale Shopify shop will sample a non-primary locale. It survives A5.3's reproducibility requirement — the choice is stable — but §5.5b's LLM extraction reads this page for the German one-line offer, and on `navucko.com` it would read English.

**(b) Alphabetical-first selects unrepresentative SKUs.** `blackpolish.de`'s sample is "Abgabe & Reinigung im Store Düsseldorf" — a drop-off *service*, first because it starts with "A". It is a legitimate product page by every filter, and a poor basis for "what do they sell".

Neither is a bug against the spec as written. Both are the spec working exactly as specified and producing a worse sample than a naive crawler would. **Recommendation:** treat as an open A5 question — prefer a URL with no locale prefix when one exists, before applying the code-point minimum. Do not touch the ordering rule itself; its determinism is load-bearing.

### The four domains with no sample are all JTL, and the cause is structural

`opulent-wohnen.com`, `smoke2u.de`, `verpackungskoenig.de`, `germanelectronic.de` — zero product candidates, no `schema.product_present` written, which is A5.5 behaving correctly.

It is **not** a parse failure. Their gzipped sitemaps decompress and parse cleanly (682 / 4,296 / 2,075 URLs). The problem is that JTL product URLs are root-level SEO slugs with no path prefix whatsoever:

```
https://www.smoke2u.de/adalya-shisha-tabak-200g-love66
https://verpackungskoenig.de/luftpolsterfolie-eco-30cm-100m-transparent
https://www.opulent-wohnen.com/Linari-Rubino-Raumduft-Diffusor-Linari-Rubino-Diffuser
```

No `/detail/`, `/products/` or `/produkt/` can ever match, and there is no product-specific sitemap for Tier 1 to fall back on. **On JTL, product sampling is currently impossible.** Combined with the JTL signature defect (M1.9), JTL shops were near-invisible to the scoring model: no platform detection (+15 lost), no product sample, and `catalog.product_url_count` unwritten (`qual.product_depth` +10 lost).

The obvious fix is the wrong one. A JTL category page is *also* a root-level slug — the URL shape carries no information distinguishing product from category, so no path pattern can separate them, and a pattern that admits both would feed listing pages to `schema.product_present` and wrongly award +10. That is precisely the asymmetry M1.4 dropped `/p/` over. **This needs a different discriminator (JSON-LD `@type` on fetch, or the JTL sitemap's own structure), and it is an open M2 design question, not a pattern to add.**

---

## 5. Impressum two-step: the footer parser is not the weak link

| route | n | domains |
|---|---|---|
| footer link | 9 | bio-fleischer-laden, blackpolish, doonails, navucko, opulent-wohnen, propellerdiscount, smile-store, verpackungskoenig, zecplus |
| direct-path probe | 3 | snocks.com, smoke2u.de, germanelectronic.de |
| `no_impressum` | 1 | ekomia.de |

A 3/13 probe rate would suggest a weak footer parser. It is not: re-running `find_impressum_link` over all 13 stored homepages finds the link on **12 of 13**, and on **13 of 13** when anchored on the post-redirect host. Every probe was caused by something downstream of the parser:

- **snocks.com, smoke2u.de** — footer link found, then refused by robots. Probing was the correct next step; it then reached the same page anyway via redirect (§1.1).
- **germanelectronic.de** — footer link found and then **discarded by `same_site`**, because after the redirect to `lampenflut.de` the link is `https://lampenflut.de/Impressum` while `company.domain` is still `germanelectronic.de`. See §6.
- **ekomia.de** — footer link found, refused by robots (`Disallow: /policies/`), then all five probe paths 404. So the one `no_impressum` flag in the run is a **false positive**: the Impressum exists at `/policies/legal-notice`, we located it, and robots forbade us to read it.

`needs_review` is arguably still the right destination for ekomia, but the reason recorded is wrong, and "we were told not to look" is a different fact from "it isn't there" — the §6.4 CH/DE handling turns on that distinction. **Recommendation:** a distinct `impressum_robots_disallowed` reason. That is a §4 CHECK-constraint change and a §6.4 entry, so it is a spec decision, not a code tweak.

---

## 6. A defect this crawl exposed by accident: cross-domain redirects blind three parsers

Two domains have moved: `doonails.de` → `www.doonails.com`, `germanelectronic.de` → `lampenflut.de`. Both were crawled successfully, and `artifact.url` correctly records the new host. But `company.domain` stays the seed value, and `same_site(url, domain)` ([urls.py:97-104](portal/urls.py#L97-L104)) is anchored on it — so after such a redirect, **the site's own URLs test as off-site**:

- **Sitemap shards were never expanded.** `doonails.de`'s index lists 5 shards on `www.doonails.com`; the `same_site` guard at [fetch.py:332](portal/fetch.py#L332) rejected all 5. The shop's entire catalog was invisible, and the sample fell through to the homepage-links tier. `doonails.de` has 6 artifact rows where a comparable Shopify shop has 10.
- **The Impressum footer link was rejected**, forcing a probe (§5).
- **The base URL stays stale.** Homepage links are absolutised against `https://{seed_domain}`, which is why `catalog.product_sample_url` for `doonails.de` records `https://doonails.de/products/babyboomer-gel-strips` while the artifact it was fetched from is `https://www.doonails.com/products/…`. It worked only because the old domain still redirects.

This is a design gap, not a coding error — the guard is what stopped `propellerdiscount.de`'s placeholder sitemap from dragging us to a third-party host (§7). The two needs are in genuine tension and the resolution is a spec question: **should a seed domain that redirects entirely to another registrable domain adopt the new one as the site boundary for the rest of the run?** My view is yes, once, on the homepage redirect only, recorded on the company row — but that is a §5.2 amendment to rule on, not something to slip into the code.

It also raises a seed-quality question worth answering separately: `germanelectronic.de` now serves **lampenflut.de**, a different brand. Whether that is still the intended lead is a judgment about the seed list, not about the crawler.

---

## 7. robots.txt: every shape was handled correctly

Thirteen files fetched, 13 parsed, 0 exclusions, and no misattributed rule. This is the part of M1 that real data validated rather than broke.

**`Crawl-delay` — declared on 6 domains, honoured on exactly the 3 where it applied to us.**

| domain | declared | in group | honoured? |
|---|---|---|---|
| propellerdiscount.de | 10 s | `*` | **yes** — 5 rows over 51 s |
| verpackungskoenig.de | 5 s | `*` | **yes** — 6 rows over 25 s |
| germanelectronic.de | 5 s | `*` | **yes** — 4 rows over 20 s on lampenflut.de |
| opulent-wohnen.com | 10 s | `Bingbot` | correctly ignored — ran at ~1 s |
| ekomia.de | 10 s, 1 s | `AhrefsBot`, `MJ12bot`, `Pinterest`, … | correctly ignored |
| snocks.com | 10 s, 1 s | `AhrefsBot`, `MJ12bot`, `Pinterest`, … | correctly ignored |

`propellerdiscount.de` sits exactly at the 10 s cap, so it was slowed rather than skipped — correct per M1.2, and it is why the run took ~2.5 minutes rather than ~1.

**Exclusions: none, and that is the right answer.** Two files contain `Disallow: /` — `ekomia.de` and `snocks.com` — both inside a `User-agent: Nutch` group. Group attribution was correct: neither was excluded, and both were crawled normally. This is the highest-stakes robots case in §5.2 (a misread here hard-excludes a live lead) and it was handled correctly on the first real file that presented it.

**Per-URL disallows were applied and recorded, not silently skipped:** 3 rows carry `robots_disallowed`, each naming the path. The refusals were right; §1.1 is about what happened *next*, not about these.

**One unexpectedly good behaviour.** `propellerdiscount.de`'s robots.txt ships a shipped-default placeholder:

```
Sitemap: https://www.yoursite.com/sitemap_index.xml
```

The `same_site` filter at [fetch.py:69](portal/fetch.py#L69) refused it, and `yoursite.com` was **never contacted** (0 artifact rows). A crawler that trusted robots-declared sitemap URLs would have made an unsolicited request to an unrelated third party on its first production run.

---

## 8. Sitemaps: gzip, sharding, and one thing nobody predicted

**Gzipped shards: yes, and they parse.** Three JTL shops served `.xml.gz`, all decompressed and parsed cleanly:

| domain | shard | gz | decompressed | `<loc>` |
|---|---|---|---|---|
| opulent-wohnen.com | `/export/sitemap_0.xml.gz` | 8 KB | 145 KB | 682 |
| smoke2u.de | `/export/sitemap_0.xml.gz` | 89 KB | 1,497 KB | 4,296 |
| verpackungskoenig.de | `/export/sitemap_0.xml.gz` | 58 KB | 784 KB | 2,075 |

**Gzipped *multi-shard*, specifically, did not occur** and remains untested against reality. The two properties came apart: the gzipped sitemaps are single-shard (JTL), and the multi-shard sitemaps are uncompressed (Shopify). The handoff's fixture covers the combination; no real shop in this corpus does.

**Multi-shard is real and much bigger than the fixtures.** `ekomia.de` fetched 47 sitemaps and `snocks.com` 42 — because each serves a full shard set *per locale* (9 and 10 locales). Their sitemap fetches alone are 89 of the run's 201 artifact rows — 44% of the crawl spent on two domains.

**`MAX_SHARDS = 50` came within 3 of binding.** `ekomia.de` used 47. On a slightly larger multi-locale shop the cap will truncate, and it truncates **silently** — no note, no flag, and a partial candidate set feeding `catalog.product_url_count` and the A5 selection. Not a defect today; it is a defect the moment it fires. **Recommendation:** record a note or flag when the shard cap is hit, so a truncated catalog is distinguishable from a small one.

**Nobody predicted this one:** all 7 Shopify shops now serve a `sitemap_agentic_discovery.xml`, ~210 bytes, whose sole content is a pointer to `/agents.md`:

```xml
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://blackpolish.de/agents.md</loc><changefreq>weekly</changefreq></url>
</urlset>
```

Harmless here — it costs one request per Shopify shop and contributes no product candidates. Noted because it is a new platform-level convention aimed squarely at agents like this one, and because `/agents.md` is now sitting in `page_urls` on 7 domains. Whether the pipeline should *read* it is a question for later, not M1.

---

## 9. Errors, 404s and small responses — all accounted for

201 rows: 185 with a stored body, 11 HTTP 404s, 3 `robots_disallowed` (no request made), 2 `redirect_refused`. **No timeouts, no connection failures, no truncated bodies, no unparseable XML, no domain that failed outright.** Every 404 is explained:

| what | n | explanation |
|---|---|---|
| `/sitemap.xml` 404 | 4 | opulent-wohnen, smoke2u, verpackungskoenig, propellerdiscount — the probe of the conventional path; 3 then found the real sitemap via the robots `Sitemap:` directive, and propellerdiscount's directive is the placeholder (§7) |
| ekomia Impressum probes | 5 | all five §5.2 direct paths genuinely absent (§5) |
| lampenflut.de `/sitemap.xml` | 1 | genuinely absent; no robots `Sitemap:` directive either — hence no sample |
| smile-store.de `/magazin` | 1 | see below |
| `redirect_refused` on robots.txt | 2 | doonails.de, germanelectronic.de — the cross-domain hop during the *robots* fetch, correctly refused; both hosts' robots.txt were then fetched properly on the homepage hop |

The smallest bodies are the 210-byte agentic-discovery sitemaps (§8) — small, but exactly the right size for what they contain. Nothing came back suspiciously small.

**One 404 is a real parser finding.** `smile-store.de`'s blog was detected correctly from 61 sitemap URLs under `/magazin/…`, but the index fetch of `https://www.smile-store.de/magazin` **404s**: on this shop the articles live at `/magazin/<kategorie>/<artikel>` and the bare segment is not a page. `blog_index_url` ([impressum.py:103-104](portal/impressum.py#L103-L104)) *synthesises* `base + blog_path` rather than using an observed URL. So the one domain where blog detection worked is also the one where M2 will get no blog index — and §6.2's ladder will raise `blog_date_unparseable` for a blog whose 61 URLs we already hold. **Recommendation:** prefer the shortest observed URL under the blog path; synthesise only as a fallback.

---

## 10. Which §6.1 unknowns are now known

| handoff §6.1 unknown | verdict |
|---|---|
| Shopware product-sitemap regex | **Still unknown.** No Shopware 6 shop in the corpus; the one Shopware shop is SW5 and serves a plugin sitemap (`articles-0-sitemap.xml`) that matches nothing. |
| Shopify product-sitemap regex | **Known — filename right, matcher wrong.** Query string defeats the `$` anchor (§1.3). |
| WooCommerce `product-sitemap.xml` | **Still unknown.** The one WooCommerce shop serves no sitemap at all (404 + placeholder directive). |
| JTL `/sitemap/product` | **Known — wrong.** JTL serves one undifferentiated `/export/sitemap_0.xml.gz`; no product sitemap exists to match (§4). |
| Tier 2 `/detail/`, `/products/`, `/produkt/` | **Known — all three observed and all three correct.** `/products/` on 6 Shopify shops, `/detail/` on Shopware 5, `/produkt/` on WooCommerce. Zero false positives; every selected page is a genuine product page. Their weakness is coverage, not precision: they match nothing on JTL. |
| `/p/` (dropped, M1.4) | **Still unobserved.** Nothing in this corpus uses it. Stays dropped. |
| Real robots.txt variety | **Known — handled.** 13 files, including two `Disallow: /` in named groups and six `Crawl-delay` declarations across five different agent groups. No misattribution (§7). |
| Gzipped multi-shard sitemaps | **Half known.** Gzip parses on 3 real shops; multi-shard works up to 47 shards; the *combination* still has no real-world instance (§8). |
| apex→www under real DNS/TLS | **Known — holds.** 5 of 13, final host recorded on all (§3). |
| Concurrency ceiling under real DNS | **Still unproven**, and the crawl output cannot prove it — no request-issue timestamps exist (§3). |

## 11. Recommended order of work, before anything M2

1. **Robots re-check on every redirect hop** (§1.1) — compliance; the only item that affects third parties.
2. **`blogs` in `BLOG_SEGMENTS`** (§1.2) — one word; prevents a wrong +25 on 5 of 13.
3. **`is_product_sitemap` matches on `path_of(url)`** (§1.3) — one call; revives Tier 1 on 7 of 13.
4. **Blog index from an observed URL, not synthesised** (§9).
5. **Spec rulings needed** — cross-domain redirect and the site boundary (§6); `impressum_robots_disallowed` as a review reason (§5); locale preference ahead of the code-point minimum (§4); shard-cap truncation notice (§8). Each changes §5.2/§6.4 and should be ruled on before code, per the spec's own preamble.
6. **JTL product identification** (§4) — open design question. Do not solve it with a path pattern.

Items 1–4 are mechanical and each is directly evidenced above. Item 5 is a decision, not a task. Item 6 is research.

**Not done here, deliberately:** no fixture was harvested into `tests/`. The Impressum bodies in `data/artifacts/` contain real names and addresses and must be redacted to Max Mustermann / Musterstraße 1 before anything is committed. `data/` is gitignored, so all 185 bodies stay local to this Codespace.
