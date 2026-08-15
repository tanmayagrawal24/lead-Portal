# Lead Portal — Technical Specification v0.3

**Owner:** Tanmay Agrawal / Creative Potatoes
**Status:** v0.2 with the v0.3 delta applied. Single source of truth for implementation.
**Supersedes:** v0.2 (retained at `docs/lead-portal-spec-v0.2.md` for provenance)

> If implementation reveals this spec is wrong, change the spec first, then the code. Never let them diverge.

## Changelog v0.2 → v0.3

| # | Defect | Section |
|---|---|---|
| D1 | Phase-1 gate discards recoverable A-band leads | §5.4 — **superseded by M1.22.** D1's fix was a global `PHASE2_MAX_POINTS`; that constant cannot be derived correctly, because a rule belongs in the bound when Phase 2 can still *award* it, not when all its inputs are Phase-2-only. §5.4 is now a per-company gate. |
| D2 | Blog ladder rungs overlap; `thin_blog` predicate undefined | §6.2 |
| D3 | Cost ceiling is per-run, so monthly spend is unbounded; batch reconciliation undefined | §4, §5.7, §7 |
| D4 | AI-visibility token estimate understated ~10×; per-search billing unaccounted | §5.5c |
| D5 | Nondeterministic pivot in `company_profile`; `fetched_at` never updates on unchanged pages | §4 |
| D6 | Idempotency contract overclaims across run boundaries | §5 preamble |
| D7 | Comparative claims in the research brief lack a verifiable basis | §8, §9 |

Also in v0.3: the ruleset version bumps to **v3** (predicate changes in §6.2; no weight changes). v0.2's `§10 Resolved review questions` is deleted, superseded by this changelog. v0.2's `§11 Open decisions` becomes `§10`.

### Amendments after third-pass review — 2026-08-15

Resolutions to findings raised in `v0.3-review-findings.md`. Applied before M0.

| # | Finding | Resolution | Section |
|---|---|---|---|
| B2 | `needs_review_reason` is one column; three soft flags can co-occur | Reasons move to a `review_flag` table, one row per (company, reason). `company.needs_review` survives as a boolean maintained by trigger, so §9's filter and `idx_company_review` still work; `company.needs_review_reason` is dropped. | §4, §6.2, §6.4, §9 |
| B4 | `run_id` for signals written by `reconcile` was undefined | Reconciled signals carry the **submitting** run's id (`llm_batch.run_id`), not the reconciling run's. | §5.6 |
| B3.1 | Cost-ledger ownership across the batch boundary | `actual_cost_usd` reconciles against the submitting run's `est_cost_usd`, where the reservation was made. | §5.6 |
| A5.1 | Product sample must be stable across runs | **Tier 0 reuse:** an existing `product_page` artifact is re-sampled while it still returns HTTP 200; on 404/error it is discarded and selection falls through to Tier 1/2. §4 requires that re-scoring never need a refetch, so the evidence a score points at must not move under it. | §5.2 |
| A5.2 | Candidate sources undefined | Three tiers: platform product sitemap (union of **all** shards, `.xml.gz` decompressed) → path-pattern candidates from fetched sitemaps → product-pattern links on the homepage. | §5.2 |
| A5.3 | Ordering undefined | Lexicographic minimum by **Unicode code point**, never locale collation. Chosen over document order because Shopware re-shards product sitemaps on a schedule, making document order stable only by accident. | §5.2 |
| A5.4 | Non-product URLs contaminate the candidate set | Four filters: reject query strings; require a path segment after the pattern; reject category/listing patterns; reject anything under the detected blog path. | §5.2 |
| A5.5 | Unchecked sites scored as "no schema" | Zero candidates, or a selected sample whose fetch fails, writes **no** `schema.product_present` signal — not `0`. | §5.2 |
| A5.6 | `opp.no_product_schema` could fire unmeasured | **Guard:** the rule fires only when `schema.product_present` was written from a product page fetched with HTTP 200; absent that signal it fires in neither direction. Clarifies what the existing predicate presupposes — no weight or threshold changes. **✅ Ratified 2026-08-15.** | §6.2 |
| A5.7 | Selection was unauditable from the database | New signal key `catalog.product_sample_url` (text, unscored, no schema change); `evidence_url` is the sitemap or homepage it was read from. **✅ Ratified 2026-08-15.** | §5.2, §5.3 |

### Amendments after the M1 review — 2026-08-15

Rulings on the questions M1 parked (`docs/m1-handoff.md` §7) and on one defect the review found. Applied on `m1-fetch`.

| # | Question or defect | Resolution | Section |
|---|---|---|---|
| M1.1 | Redirect hops bypassed the rate limiter and could reach a host whose robots.txt was never read | Redirects are followed one hop at a time, each waiting on the limiter; a hop that changes host is followed only after that host's robots.txt has been fetched and consulted, and is otherwise refused and recorded. | §5.2 |
| M1.2 | `Crawl-delay` was ignored | Honour `max(1.0, Crawl-delay)`, capped at 10 s; above the cap the domain is skipped and the reason recorded. | §5.2 |
| M1.3 | `defusedxml` for third-party sitemap XML | **Declined — no new dependency.** A sitemap whose bytes contain `<!DOCTYPE` or `<!ENTITY` is refused before parsing, which removes the entity-expansion class outright. | §5.2 |
| M1.4 | `/p/` as a Tier 2 product pattern | **Dropped** until observed in the wild. The errors are asymmetric: a false positive wrongly awards +10, a false negative only leaves a signal unwritten, which A5.5 already handles. | §5.2 |
| M1.5 | `signal` writes used `INSERT OR IGNORE`, which swallows CHECK violations | Idiom changes to `ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING`, everywhere signals are written. Same fix `review_flag` already carries. | §4, §5.6 |
| M1.6 | `uvicorn` absent from the named stack | **Approved for M4.** FastAPI cannot serve itself; a gap in the stack list, not scope creep. | §3 |
| M1.7 | Failure-artifact and robots-exclusion policy existed only in code | Both written into the spec: failure rows update in place, and "required paths" is defined for the two paths that are not knowable up front. | §5.2 |
| M1.8 | Apex and www were separate politeness budgets for one server, so an apex→www redirect ran at 2 req/s | The politeness key strips `www.` and keeps the port; other subdomains stay separate, recorded as accepted. The robots.txt key stays origin-based, so each name is still asked for its own file. | §5.2 |

### Amendments after seed verification — 2026-08-15

Two defects in §5.3's `platform.detected` signature list, found by grepping the homepage HTML of all 13 shops in `seeds/candidates.csv` before the first crawl. Both were **assumed** signatures that real German shops do not emit as written; the replacements are **observed** in that HTML. Applied on `first-crawl`.

| # | Defect | Resolution | Section |
|---|---|---|---|
| M1.9 | **JTL was undetectable.** The signature `jtl-shop` is emitted by **none** of the four confirmed JTL shops in the seed list. As written, `platform.detected` misses every JTL shop — a systematic blind spot across a major German SME platform, and one that fails silently: the signal is simply never written, so `qual.ecommerce_platform` (+15) never fires and nothing errors. | Signature becomes any of `jtl-nav-wrapper`, `jtl-validate`, `jtl_token`, `jtlPackFormTranslations` — the JTL 5 markers observed in the seed HTML. `jtl-shop` is removed. | §5.3 |
| M1.10 | **Shopware was too loose.** "`/bundles/storefront/`, `sw-` attributes" was implemented against the bare string `shopware`, which false-positived `germanelectronic.de` — a JTL shop that merely mentions the word. A loose match here awards `qual.ecommerce_platform` (+15) on a mention, and assigns the wrong platform to a shop that also loses its +15 under M1.9. | Anchor on `/bundles/storefront/` alone. The loose `sw-` attribute fallback is dropped: `sw-` is too short to be evidence of anything. | §5.3 |
| M1.11 | **The anchored Shopware signature is Shopware 6 only.** Confirmed in the first crawl (`docs/first-crawl-findings.md`): `smile-store.de` is a Shopware **5** shop and matches no signature at all under M1.10 — `/bundles/storefront/` is a SW6 path. Recording the hole rather than papering over it, because an undetected platform is exactly the M1.9 failure mode. | **Open, deliberately.** SW5 markers `engine/Shopware` and `/themes/Frontend/` were observed, but on **one shop only** (n=1). One observation is not a signature; it goes into §5.3 when a second Shopware 5 shop confirms it. Until then Shopware 5 is knowingly undetected. | §5.3 |

### Amendment after the first crawl — 2026-08-15

One defect, found by reading `run 1`'s artifact rows (`docs/first-crawl-findings.md` §1.1). Applied on `p0-robots-hop-check`.

| # | Defect | Resolution | Section |
|---|---|---|---|
| M1.12 | **The crawler fetched pages robots.txt disallows.** M1.1's rule was written as "a hop that *changes host* is followed only after that host's robots.txt has been consulted", and was implemented exactly as written — so a hop that changed only the **path** was never checked at all. On `snocks.com` (`Disallow: /policies/`) and `smoke2u.de` (`Disallow: /Impressum`) the Impressum probe of an *allowed* path was redirected onto a *disallowed* one and fetched: the artifact table holds both a `robots_disallowed` refusal and a `200` with a stored body for the same URL. 2 of 13 domains, and it recurs every run. | **Every** hop target is checked against the applicable rules, not only cross-authority ones. M1.1's host-change clause was too narrow a statement of the underlying rule: the request that matters is the one that lands, so the target is what robots governs. Pinned by a test in which an allowed path redirects to a disallowed path on the **same** host. | §5.2 |

| M1.13 | **Tier 1 was dead code.** Zero of 143 sitemaps in the first crawl matched a product-sitemap pattern. The filename conventions were right; `is_product_sitemap` matched them against the **whole URL**, and Shopify serves `sitemap_products_1.xml?from=…&to=…`, so the `.xml` the patterns anchor on with `$` is not at the end. Every one of the seven Shopify shops fell through to Tier 2. | Match against `path_of(url)`. A query string is addressing, not identity — and matching it would let any sitemap claim to be a product sitemap. | §5.2 |
| M1.14 | **Shopify's blog path is `/blogs/`, plural, and the vocabulary had only `blog`.** Five shops publishing actively — one with 670 blog URLs — reported "no blog path found". `content.blog_exists` would read false and fire `opp.no_blog` **+25**, the largest award in ruleset v3, against shops with live content marketing. | `blogs` added to `BLOG_SEGMENTS`, observed on 5 shops. **Two further real blogs remain undetectable and are deliberately left so**, because a path-segment vocabulary is the wrong instrument for them — see below. | §5.3 |

| M1.15 | **The blog index was synthesised, not observed**, so M1.14 found seven blogs and then failed to fetch all seven: `base + "/blogs"` 404s on Shopify, where the index is `/blogs/<handle>`, and `/magazin` 404s on `smile-store.de`, which serves `/magazin/<kategorie>/<artikel>`. Without this, M1.14 buys nothing but seven wasted 404 requests per run. | Fetch the **shallowest observed URL** under the blog path — homepage nav links preferred over sitemap URLs at equal depth, code-point minimum breaking ties. The synthesised URL survives only as a fallback when nothing was observed. The path prefix remains what A5 filter 4 excludes candidates under; it is a good filter and a bad address. | §5.2, §5.3 |
| M1.16 | **Reviving Tier 1 (M1.13) made it select a locale storefront root.** Shopify lists `/de-at` inside that locale's *product* sitemap, and Tier 1 waives the path-pattern requirement because membership is the evidence — so `ekomia.de`, `navucko.com` and `snocks.com` would each have sampled `/de-at`, `/en`, `/de-ch`: listing pages, feeding `schema.product_present` a wrong +10. Caught before it reached a run, by comparing Tier 1's would-be choice against Tier 2's on the stored crawl output. | The existing "the homepage is never a product page" guard extends to locale roots (`^/[a-z]{2}(-[a-z]{2})?$`): a multi-locale shop has more than one homepage. With the guard, Tier 1 and Tier 2 agree on all six shops where both can run. | §5.2 |

| M1.18 | **A seeded domain that has moved blinds every `same_site`-anchored parser.** `doonails.de` → `www.doonails.com` and `germanelectronic.de` → `lampenflut.de`: the site's own URLs test as off-site, so five sitemap shards were never expanded and a footer Impressum link was discarded, both silently. Loosening `same_site` is not available — it is what kept us off `propellerdiscount.de`'s placeholder `yoursite.com`. | Adopt the final host as the site identity, **once**, when the homepage redirect resolves and only then, into a new nullable `company.site_domain` (migration 002). `company.domain` keeps the seeded value: it is `UNIQUE`, it is the human key, and it names `data/artifacts/{domain}/`, which a rewrite would orphan silently. Adoption always raises `domain_moved` (§6.4). Collisions resolve as below and never merge automatically. | §5.1, §5.2, §6.4, §4 |

| M1.19 | **M1's done-when says "1 req/s observed" and nothing observed it.** `artifact` rows are written when a response *lands*, so gaps between them measure the server's latency variance rather than our spacing — reading them that way appeared to show ten violations in the first crawl and showed nothing of the kind. | `net.RequestLog` records every issued request — issue time on both clocks, politeness key, authority, requested and final URL, status — from inside `Fetcher.get`, below which no request can be issued. `portal audit-politeness` reports measured min-gap per key and max hosts in flight, and **exits non-zero on a breach**, so it is an acceptance check rather than something to read. Gaps are computed from `time.monotonic`, so a wall-clock change mid-run cannot manufacture or hide a violation. | §5.2 |
| M1.20 | **`portal fetch` ran against a database older than the code**, and died inside a worker thread on `no such column: site_domain` — after real requests had already gone out to real hosts. | `fetch` compares `PRAGMA user_version` against the highest migration on disk and refuses before the first request, naming `portal init` as the fix. Migrations stay explicit; this only makes the mismatch loud and early. | §5.2 |
| M1.21 | **A1 — the Phase-2 bound is under-counted.** `qual.owner_operated` (+15) was omitted from `PHASE2_MAX_POINTS`, giving 35 where a correct derivation gives 50, `ADVANCE_THRESHOLD = 5`, and a gate that `qual.ecommerce_platform` clears on its own. | Two parts. **(a)** Extract `company.legal_form` deterministically in `extract-p1` (§5.3) — a real Phase-1 signal, 7/12 with 0 false positives on the verified corpus. **(b)** Rewrite the gate itself: see M1.22. **⚠️ Needs Tanmay's ratification** (scoring-model change), same handling as A5.6/A5.7. | §5.3, §5.4 |
| M1.23 | **§7's ceiling would abort every month.** With M1.22's gate admitting ~95%, steady state is $31–36/month against a `$25` default — a ceiling that trips on correct behaviour, which teaches its operator to raise it without reading it. | **Default raised to `$45`**, and reframed as a runaway guard rather than a budget; the "should bite occasionally" job moves to the per-run ceiling. Cutting AI-visibility to one query was rejected: §6.2's `opp.ai_invisible` predicate requires `ai.queries_checked >= 2`, so one query silently disables a **+15** rule. Arithmetic in §7.1. | §7 |
| B7 | **`qual.own_domain_shop` (+5) had no predicate**, in any section — untracked since Task 0. M3 could not implement it, so it could never fire: a permanent −5 on every company, making §6.5's bands stricter than the calibration they came from. | Defined as `catalog.product_url_count >= 5`, the exact inverse of §6.4's `possible_marketplace_only`, so the two cannot disagree. Computable in **Phase 1** from a signal §5.3 already writes. Unwritten count → neither fires (A5.5 discipline), which costs the four JTL shops their +5. | §6.1, §10 |
| M1.22 | **A global `PHASE2_MAX_POINTS` cannot be derived correctly at all**, so extracting `legal_form` does not rescue it. A rule belongs in the bound when Phase 2 can still **award** it — not when all its inputs are Phase-2-only. `qual.owner_operated` has three disjuncts; §5.3 decides the first in Phase 1, but the other two are LLM extractions, so a company failing the legal-form test can still win the full +15 in Phase 2. The rule stays Phase-2-reachable, the constant stays 50, the threshold stays 5. | **§5.4 is rewritten, not amended.** The gate becomes per-company: `advance(c)` iff `phase1_total(c) + remaining_upside(c) >= B_band_floor`, where `remaining_upside(c)` sums the maximum positive points of every rule Phase 2 can still influence for `c` and that Phase 1 has not already awarded. Safe by construction and strictly tighter than any safe global constant — 20 for a company that banked `owner_operated`, 5 for one that did not. Each rule declares its Phase-2 reachability; startup asserts every rule declares it. D1 stays in the changelog as superseded. **⚠️ Needs Tanmay's ratification.** | §5.4 |

**The measurement is what makes M1.22 unavoidable rather than tidy.** The five shops with no legal-form token are exactly the five sole traders — the companies *most* likely to win `qual.owner_operated` in Phase 2 on "owner named on site". The +15 that must stay inside the bound sits on the best leads in the corpus, so dropping it to make the arithmetic come out at 35 would discard precisely the companies the tool exists to find.

**A correction to what this entry said in its first form.** It claimed that moving one disjunct into Phase 1 removed the rule from the Phase-2-only set and returned the threshold to 20. That was wrong: it confused "Phase 1 can decide this rule for *some* companies" with "Phase 2 can no longer award it". The legal-form extractor is kept — 7/12 with 0 false positives is a real Phase-1 signal, the provider-block anchoring is load-bearing (a naive first-match found a cookie vendor's `GmbH` on two shops and a trust-seal `e.V.` on a third), and `doonails.de` being a **Cyprus** Ltd is a lead-quality fact worth having. It simply does not do the job A1 needed doing.

**M1.18's collision rule, since it is the part that can go wrong quietly.** A row whose **`domain`** equals the contested host **always wins, whatever the ids** — a seeded identity cannot be taken from a row by something that merely redirected onto it. Id ordering breaks ties only between two rows both claiming the host as `site_domain`, and there the lower id wins *regardless of which worker got there first*, so the outcome does not depend on thread scheduling; a higher-id row that adopted earlier has its claim withdrawn. Exactly one row ever claims a host. The loser is excluded with `duplicate_site` and its `site_domain` cleared; the winner gets a `duplicate_site` review flag so the merge target is visible. Because `site_domain` is a separate column and `company.domain` is never written after insert, **a `UNIQUE` violation is structurally impossible** — the collision is resolved deliberately, with a recorded reason, rather than surfacing as an `IntegrityError` from inside a worker thread.

One consequence of adopting only from the homepage: `allowed()` must consult the robots policy of the **authority each URL is on**, not the seeded one. After a move, every later request goes to a host the seeded `robots.txt` says nothing about, and the redirect hop has already fetched the target's own rules.

| M1.17 | **The homepage was stored as an Impressum.** `snocks.com` in `run 2`: with its real Impressum robots-disallowed (M1.12 now refuses it correctly), probing ran and `/imprint` redirected to `/#gbaid979323` — the homepage. It was stored as the `impressum` artifact, carrying the **homepage's own content hash**, which is how it was caught. §5.5b would have handed the homepage to the Impressum extraction and got a confident answer about the wrong page. | An Impressum request whose **final** URL is the site root is not an Impressum. The request is still recorded — it happened — but as a failure row (`soft_redirect_to_homepage: …`) with no body, because `artifact` is the interface M2 reads by kind. Absence then routes to `no_impressum` review, which is what §5.2's two-step does with an absence anyway. | §5.2 |

**M1.14's limits, stated rather than implied.** Adding `blogs` fixes the shape a vocabulary can fix. Two others in the same 13-shop corpus it cannot:

- **A blog on a subdomain.** `zecplus.de` links "Blog" to `https://blog.zecplus.de/`, whose *path* is `/`. `same_site` accepts it, but there is no path segment to match, so no vocabulary entry can ever reach it. The first-crawl findings recorded `zecplus.de` as having no blog; that was **wrong**, and the error was mine rather than the crawler's — its sitemap genuinely contains no blog URLs, because the blog is a different host.
- **A blog served as root-level slugs.** `lampenflut.de` (JTL) publishes guides at `/Lampenflut-Licht-Ratgeber`, `/Sollux-Lampen-Ratgeber-…`. The segment *contains* a vocabulary word but is not equal to one, and widening to substring matching would match product slugs — `verpackungskoenig.de` sells `/pressengarn-…`, which contains "presse". This is the same root cause as JTL product sampling (findings §4): on JTL, both catalog and content are root-level slugs, so path shape carries no type information at all.

Both are **open**, and both need a different instrument — a link whose anchor text is "Blog" is evidence the path is not; so is a page carrying `Article` JSON-LD. Neither is a vocabulary entry, so neither goes in on the M1.4 rule. `content.blog_exists` therefore still under-detects, and a `false` reading from it is not yet strong enough to carry +25 on its own.

The narrowness was the whole defect. M1.1 correctly identified that a redirect hop is a request and that the transport must enforce robots on it; it then described the enforcement in terms of the case that had prompted it. Two of the three ways a hop can reach a forbidden URL were left open — a different path, and (on `smoke2u.de`) a path differing from the `Disallow` rule only in **case**, so the probe is genuinely allowed and the redirect target genuinely is not.

M1.9 and M1.10 are the same class of error as M1.4 (`/p/`): a pattern admitted on plausibility rather than observation. The rule stands — **a signature goes into this list on evidence, not on convention**, and M1.11 is that rule applied to a hole this crawl opened rather than closed.

On the strength of the M1.9 markers, since "observed" is not one bit: across the four JTL shops, `jtl-validate` and `jtl_token` appear on **4 of 4**, `jtl-nav-wrapper` on 3, `jtlPackFormTranslations` on 2. The any-of union covers all four. The discarded `jtl-shop` string does occur on 3 of the 4 — but only inside an operator-removable "powered by JTL-Shop" footer credit, capitalised as `JTL-Shop`, which is both why a case-sensitive match found nothing and why the string must not be reinstated case-insensitively: `opulent-wohnen.com` has removed the credit and would still be missed.

Signatures for platforms not represented in the seed list remain unverified and are marked as such in §6.1 below. Shopify (7 shops) and WooCommerce (1) *are* represented and both matched — `cdn.shopify.com` on 7 of 7, `woocommerce` on 1 of 1.

Remaining findings (A1–A4, B1, B3.2–B3.3, B5–B7, C1–C4) are still open and are not required by M0 or M1.

### Amendments after the M2 run — 2026-08-15

| # | Finding | Resolution | Sections |
|---|---|---|---|
| M1.24 | **`catalog.product_url_count` had no tier hierarchy, and no way to read a shard's own name.** A5 defines Tier 1 (product sitemap) before Tier 2 (path patterns) for *sampling*, and the count ignored the order: it fell to path patterns whenever the four filename conventions missed. `smile-store.de` publishes all 194 of its products in `PixupSitemap/sitemap/area/articles-0-sitemap.xml` — the shard the shop labelled, in German commerce's own word for a saleable product — and was counted at **6**, from a `/detail/` pattern scraping stragglers off the rest of the site while the shard sat unread in the index. A 32× undercount, *written*, so it read as measured. | Two parts. **(a)** The count uses A5's tiers in A5's order, and records which tier answered in `value_text`. **(b)** A shard's own filename is read as **semantics**, not matched against a convention list: `articles`, `products` and their German forms mean catalogue; `blog`, `magazin`, `ratgeber`, `news`, `journal`, `post` mean content, and are tested first. Per M1.9, `articles` is marked **observed on one shop** and the German `artikel`/`produkte` are marked **unobserved**. The generalisable part is reading the label, not the string. | §5.2, §5.3 |
| M1.25 | **A multi-locale shop's catalogue was counted once per market.** Shopify lists one product sitemap per locale, each holding the same catalogue under a different path prefix. With M1.24's Tier 1 anchoring, `snocks.com` would have reported **4,620** products against a real 462 and `ekomia.de` 2,861 against 335. Fixing the undercount would have shipped a tenfold overcount in the same commit. | A URL under a **secondary locale prefix** is a translation, not a second product. Two instruments, because neither covers the corpus alone: prefixes *declared* in an `hreflang` alternate (`smile-store.de` serves its English subshop from `/shop/en/`, which no path-shape rule would see), and prefixes visible only by their *shape* (`snocks.com` declares three of its ten markets; the other seven appear as a leading `^/[a-z]{2}(-[a-z]{2})?$` segment — the same shape M1.16 already treats as a storefront root). `x-default` names the primary storefront, so a shop serving its default from `/de/` has its siblings excluded rather than its catalogue. **Safety valve:** an exclusion that would empty a catalogue is evidence about the exclusion, not the shop — it is not applied, and the fallback is recorded. | §5.3 |
| M1.26 | **`<image:loc>` was read as a page.** Namespaces were stripped before tag names were compared, so every Shopify product sitemap parsed as twice its length. It reached no count on this corpus only by luck: Shopify serves images from `cdn.shopify.com` and `same_site` discarded them. A shop hosting its own images under `/products/…` would have had every product counted twice by a rule that believed it was counting pages. | A `<loc>` counts when its namespace is the sitemaps schema or absent. Extension namespaces — image, video, news, xhtml — describe a page; they are not pages. | §5.2 |

**M1.24's ambiguity, unresolved rather than guessed at.** In English-language CMS usage `article` means a *blog post*; in German commerce `Artikel` means a product. The corpus exercises only the second reading, and Yoast — the likeliest source of the first — names its post shard `post-sitemap.xml`. Testing the content vocabulary first catches the compound case (`blog-articles-…`) and does **not** catch a bare `articles` shard that means posts. Recorded as a live risk on the M1.4 rule: it is mitigated when a shop is observed doing it, not before.

**What M1.24 changed on the stored corpus.** Every one of the eight measurable shops is now counted from Tier 1; **Tier 2 answers for none of them**. That is worth stating plainly, because before this amendment Tier 2 answered for all eight — the fallback was doing the entire job, and its numbers were wrong on five of them.

**The blog-shard reading is not a blog detector.** `is_blog_sitemap` exists to keep content out of the catalogue count, and it reaches **neither** of M1.14's two unreachable shapes: `zecplus.de` serves no blog shard at all (its blog is a different host) and `lampenflut.de` serves no sitemap at all. See §10.1.

| # | Finding | Resolution | Sections |
|---|---|---|---|
| M1.27 | **M1.24's `articles` ambiguity was mitigated by word order, which is not evidence.** `article` means a saleable product in German commerce and a blog post in English CMS usage. Testing the content vocabulary first catches `blog-articles-…` and does nothing at all for a bare `articles` shard that holds posts — which would write a confident, wrong product count that nothing downstream has reason to doubt. | Disambiguate by **content**, not by name, using what a sitemap index already contains — in order of strength: (1) a shard whose URLs are also in a content shard is content, whatever it is called; (2) an index naming both `articles-*` and `blogs-*` has said which one holds its posts, so the other is catalogue (the `smile-store.de` shape); (3) a lone ambiguous shard whose URLs all sit under the detected blog path is content. Nothing resolving it toward content leaves it catalogue. **No vocabulary is added** — `artikel` and `produkte` stay recorded as unobserved. | §5.2, §5.3 |
| M1.28 | **Three defects in two milestones passed the suite and were caught by reading a run against the previous one** — the gzip `str` round-trip, `<image:loc>` as a page, and blog-shard URLs dropped from blog *path* detection. Two of the three were **hidden behind a plausible state**: the gzip defect surfaced as a correct-looking "not measurable", and halved counts looked like counts. Reading line by line works at 13 domains and cannot work at 500, which is the target. | `portal diff-signals` — every key whose value changed, appeared or disappeared between two runs, grouped by domain. Reads only; costs nothing; touches no third party. Defaults to the last two runs of a stage, and refuses rather than guesses when there is only one. A run that wrote no signals is not a comparison point. | §5.3, §9 |
| M1.29 | **§5.3 named the blog index as the evidence for `content.blog_last_post` and `schema.article_present`, and the index carries neither.** Shopify blog indexes emit no `<time>`, no `datePublished` and no `Article` markup — all three live on the post. Measured: 5 of 7 detected blogs yielded no date, and `schema.article_present` was `0` on **every** blog index in the corpus. Not a parser weakness: the evidence was never on the page that was fetched. | **A6** — sample one article under the fetched index, symmetric with A5, anchored on the **index path** rather than the blog path. New artifact kind `blog_article`, new unscored signal `content.blog_sample_url`, one extra request per company with a blog. A6.1 writes nothing where no article is obtained. | §5.2, §5.3 |

**M1.27's failure direction is chosen, not incidental.** Reading a product shard as content costs its count, and §10.3's three-state rule then reports *not measurable* — visible, flagged, recoverable. Reading a content shard as products writes a number. The rule resolves toward the recoverable error every time, which is the same asymmetry argument that dropped `/p/` in M1.4.

**M1.29's cheaper alternative was assessed and rejected as the primary instrument.** Blog sitemap shards carry a `<lastmod>` per article, on both platforms that serve one, and it is already on disk. It measures modification, not publication — and the corpus contains a measured instance of it lying by three years. The numbers are in §5.3; the consequence is that a freshness proxy erring only *fresh* suppresses `opp.blog_stale` on exactly the stale blogs the rule exists to find. It is admissible as a hint for a human, never as the value.

## Changelog v0.1 → v0.2 (retained for the record)

1. **ScrapeGraphAI removed.** Both extractions use the Anthropic SDK directly with tool-use structured output. (§5.5)
2. **Scoring restructured.** Blog rules became a mutually exclusive ladder; schema rules conditional; the 45-point cap removed. (§6.2)
3. **Two-phase pipeline.** Phase 1 = free deterministic signals for everyone; Phase 2 = paid signals for advancing companies. (§5)
4. **New signal: AI/GEO visibility.** (§5.5c, §6.2)
5. **New signal: review presence** as a free product-strength proxy. (§5.3)
6. **Blog date detection fixed.** Sitemap `<lastmod>` demoted to a hint. (§5.3)
7. **Exclusions softened.** Two-tier exclusion, CH-aware. (§6.4)
8. **Einzelunternehmen fixed.** `qual.owner_operated` fires on legal form as well as GF count. (§6.1)
9. **Idempotency hardened.** UNIQUE constraints, pre-call cost reservation, hash-keyed extraction. (§4, §5, §7)
10. **`company_profile` SQL view** added. (§4)
11. **`meta.description_length` dropped** from scoring. (§5.3)

---

## 1. Purpose

A locally-run tool that discovers candidate companies in the DACH region, gathers **evidence** about the state of their content marketing from public sources, assigns a reproducible priority score, and presents a reviewable list.

The output of a good run is not "here are 200 emails." It is: *"Schulz Manufaktur GmbH, Shopware shop, owner-operated, last blog post March 2023, no Article schema, invisible in AI answers for its own category — priority A."*

Every number in the score must trace back to a stored artifact with a URL and a fetch timestamp.

The exported research brief per company is a first-contact asset: it must read like the KI-Sichtbarkeits-Baseline-Report format that has already worked in live pitches — concrete findings, evidenced, in German.

## 2. Non-goals

Explicitly out of scope. Do not implement these, and do not let scope creep add them:

- **No email sending.** Not a feature, not a stub, not "for later." See §8.
- **No LinkedIn, Xing, or Instagram scraping.** ToS violation, actively blocked, not worth the engineering.
- **No multi-user support, auth, or deployment.** Single operator, localhost, SQLite.
- **No CRM.** Outreach tracking is a single flat table, nothing more.
- **No paid contact database integration** (Apollo, ZoomInfo, Cognism). The Impressum is a better and cheaper source for DACH.
- **No ScrapeGraphAI or other LLM-framework dependency.** Two fixed-schema extractions do not justify a framework. Direct SDK calls only.

## 3. Architecture

```
                  ┌──────────────┐
  Places API ───▶ │  discovery   │ ──▶ company (domain, name, city)
  Seed CSV   ───▶ └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │    fetch     │ ──▶ artifact (raw HTML/XML, hashed)
                  └──────────────┘     robots.txt → homepage → sitemap.xml
                         │              → impressum → blog index → product page
                         ▼
                  ┌──────────────┐
                  │  extract P1  │ ──▶ deterministic signals (free)
                  └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  score  P1   │ ──▶ provisional band + gate decision
                  └──────────────┘
                         │  phase1_total + remaining_upside >= B floor
                         ▼
                  ┌──────────────┐     PageSpeed API
                  │  extract P2  │ ──▶ Impressum + homepage extraction (Anthropic SDK, Batch)
                  └──────────────┘     AI-visibility check (Anthropic SDK + web search)
                         │
                         ▼
                  ┌──────────────┐
                  │  reconcile   │ ──▶ batch results → signals, actual cost
                  └──────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  score  P2   │ ──▶ final score + score_component
                  └──────────────┘     (rule_id, points, letter-ready German reason)
                         │
                         ▼
                  FastAPI + HTMX UI  (localhost:8000)
```

**Stack:** Python 3.11+, FastAPI, SQLite (WAL mode), `httpx`, `selectolax` for parsing, `anthropic` SDK, Jinja2 + HTMX for the UI, and `uvicorn` **from M4 only** — FastAPI is a framework and cannot serve itself, so `portal serve` (§9) needs an ASGI server. It is listed here so it is not mistaken for scope creep later. No build step, no Node, no Docker.

The list is closed. Anything not named here is a decision to be taken deliberately, not a convenience import — see M1.3 above, where a security concern was answered inside the stack rather than by adding to it.

**Repo layout:** single self-contained repository. No fork of any scraping framework exists or is needed.

## 4. Database schema

SQLite. Migrations via plain numbered `.sql` files applied in order; no ORM migration framework.

```sql
-- ─────────────────────────────────────────────────────────────
-- Core entity
-- ─────────────────────────────────────────────────────────────
CREATE TABLE company (
    id              INTEGER PRIMARY KEY,
    domain          TEXT NOT NULL UNIQUE,      -- normalised: lowercase, no scheme, no www
    legal_name      TEXT,
    legal_form      TEXT,                      -- 'GmbH'|'GmbH & Co. KG'|'e.K.'|'Einzelunternehmen'|'GbR'|'AG'|'UG'|…
    city            TEXT,
    postal_code     TEXT,
    country         TEXT CHECK (country IN ('DE','AT','CH')),
    discovery_source TEXT NOT NULL,            -- 'places' | 'seed_csv' | 'manual'
    discovery_query TEXT,                      -- the query that surfaced it, for provenance
    discovered_at   TEXT NOT NULL,             -- ISO8601 UTC
    excluded        INTEGER NOT NULL DEFAULT 0,
    excluded_reason TEXT,                      -- never exclude silently; always record why
    needs_review    INTEGER NOT NULL DEFAULT 0 -- derived: 1 iff an unresolved review_flag exists.
                                               -- Maintained by trigger, never written directly.
);
CREATE INDEX idx_company_excluded ON company(excluded);
CREATE INDEX idx_company_review ON company(needs_review);

-- ─────────────────────────────────────────────────────────────
-- B2: soft review flags. §6.4's three reasons are independent and
-- can co-occur, so one row per (company, reason) rather than one
-- TEXT column shared by three writers in three different stages.
--
-- Raising a flag is idempotent, so `score --phase 1` stays a zero-cost
-- repeatable recompute (§5.4). The idiom is a targeted DO NOTHING:
--
--   INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at)
--   VALUES (?,?,?,?) ON CONFLICT (company_id, reason) DO NOTHING;
--
-- NOT `INSERT OR IGNORE`, which suppresses CHECK violations as well as
-- uniqueness conflicts — a renamed or misspelled reason would then be
-- dropped silently rather than raising, which is the opposite of the
-- fail-loudly rule the CHECK is there to enforce.
--
-- Resolution is sticky — see §6.4, which states the rule and the
-- reasoning. The unique index is what implements it; §6.4 is what
-- decides it.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE review_flag (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL CHECK (reason IN (
                      'no_impressum','possible_marketplace_only','blog_date_unparseable')),
    raised_run_id INTEGER NOT NULL REFERENCES run(id),
    raised_at     TEXT NOT NULL,
    resolved_at   TEXT,                        -- NULL = not yet reviewed
    resolved_by_human INTEGER CHECK (resolved_by_human IN (0,1)),
                                               -- 1 = a human dismissed it; 0 = the pipeline cleared it.
                                               -- NULL passes: a SQLite CHECK holds unless it evaluates
                                               -- false, and the paired CHECK below governs the NULL case.
    resolved_note TEXT,
    CHECK ((resolved_at IS NULL     AND resolved_by_human IS NULL)
        OR (resolved_at IS NOT NULL AND resolved_by_human IS NOT NULL))
);
CREATE UNIQUE INDEX uq_review_flag ON review_flag(company_id, reason);
CREATE INDEX idx_review_flag_open ON review_flag(company_id) WHERE resolved_at IS NULL;

-- `company.needs_review` is a cache of "has an unresolved flag", kept
-- correct by trigger so there is exactly one write path: write the flag,
-- the boolean follows. §9's filter stays an indexed column lookup rather
-- than an EXISTS subquery on every page load.
-- All three correlate the subquery to `company.id` rather than to NEW/OLD, so
-- the UPDATE trigger can recompute both sides with one statement: an update
-- that moved a flag between companies would otherwise leave the company it
-- left behind still marked.
CREATE TRIGGER trg_review_flag_after_insert AFTER INSERT ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ) WHERE id = NEW.company_id;
END;

CREATE TRIGGER trg_review_flag_after_update AFTER UPDATE ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ) WHERE id IN (OLD.company_id, NEW.company_id);
END;

CREATE TRIGGER trg_review_flag_after_delete AFTER DELETE ON review_flag
BEGIN
    UPDATE company SET needs_review = EXISTS (
        SELECT 1 FROM review_flag WHERE company_id = company.id AND resolved_at IS NULL
    ) WHERE id = OLD.company_id;
END;

-- ─────────────────────────────────────────────────────────────
-- Raw fetched pages. Kept so re-scoring never costs a refetch.
-- D5(b): fetched_at = first seen; last_checked_at = most recent verification.
-- On a pre-existing database this column arrives as:
--   ALTER TABLE artifact ADD COLUMN last_checked_at TEXT;
-- ─────────────────────────────────────────────────────────────
CREATE TABLE artifact (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,             -- 'robots'|'homepage'|'sitemap'|'impressum'|'blog_index'|'product_page'
    url             TEXT NOT NULL,
    http_status     INTEGER,
    content_hash    TEXT,                      -- sha256 of body; extraction is keyed to this
    body_path       TEXT,                      -- relative path on disk; bodies are NOT stored in SQLite
    bytes           INTEGER,
    fetched_at      TEXT NOT NULL,             -- first time this exact content was seen
    last_checked_at TEXT,                      -- most recent time this URL was verified
    error           TEXT
);
CREATE INDEX idx_artifact_company_kind ON artifact(company_id, kind);
-- Idempotency: a re-fetch of identical content must not create a second row.
CREATE UNIQUE INDEX uq_artifact_identity ON artifact(company_id, kind, content_hash)
    WHERE content_hash IS NOT NULL;
```

**D5(b) — all artifact writes use this upsert.** With `INSERT OR IGNORE`, an unchanged page never updated `fetched_at`, so "when did I last check this" was unanswerable — and the 30-day PageSpeed cache rule in §5.3 depends on exactly that.

```sql
INSERT INTO artifact (company_id, kind, url, http_status, content_hash, body_path, bytes, fetched_at, last_checked_at)
VALUES (?,?,?,?,?,?,?,?,?)
ON CONFLICT (company_id, kind, content_hash) WHERE content_hash IS NOT NULL DO UPDATE
SET last_checked_at = excluded.last_checked_at,
    http_status     = excluded.http_status;
```

> The `WHERE content_hash IS NOT NULL` on the conflict target is not optional and is not decoration. `uq_artifact_identity` is a **partial** index, and SQLite matches a conflict target to a partial index only when the predicate is repeated verbatim. Without it every artifact write raises `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint`. Found in M1; v0.3's original snippet omitted it and could never have run.

```sql
-- ─────────────────────────────────────────────────────────────
-- Signals: append-only, one row per observation, always evidenced.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE signal (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    run_id        INTEGER NOT NULL REFERENCES run(id),
    key           TEXT NOT NULL,               -- see §6 for the controlled vocabulary
    value_text    TEXT,
    value_num     REAL,
    value_date    TEXT,
    method        TEXT NOT NULL CHECK (method IN ('deterministic','llm')),
    confidence    REAL,                        -- NULL for deterministic; 0-1 for llm
    evidence_url  TEXT NOT NULL,               -- the page this was read off
    artifact_id   INTEGER REFERENCES artifact(id),
    observed_at   TEXT NOT NULL
);
CREATE INDEX idx_signal_company_key ON signal(company_id, key);
-- Idempotency: re-running a crashed extract stage must not duplicate observations
-- within the same run. See §5 (D6) for what this does and does not guarantee
-- across run boundaries.
--
-- M1.5 — every write to signal uses this idiom:
--
--   INSERT INTO signal (…) VALUES (…)
--   ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING;
--
-- NOT `INSERT OR IGNORE`. `OR IGNORE` suppresses the CHECK on `method` as well
-- as the uniqueness conflict, so a typo'd or renamed method would be dropped in
-- silence — a signal that was never written, indistinguishable from one that
-- was never observed. The targeted DO NOTHING dedupes and nothing more. This is
-- the same trap review_flag avoids, for the same reason.
CREATE UNIQUE INDEX uq_signal_identity ON signal(run_id, company_id, key, evidence_url);

-- ─────────────────────────────────────────────────────────────
-- Wide read model for scoring. A VIEW, not a table: no sync, no
-- second write path. Pivots the LATEST observation per key.
-- Add a column here when a new signal key enters the scoring rules;
-- keys not listed remain queryable via the signal table.
--
-- D5(a): the ORDER BY carries an `id DESC` tiebreaker. Signals written
-- within the same second share an observed_at; without the tiebreaker,
-- which one the view surfaces is arbitrary and can differ between
-- queries — contradicting the reproducibility guarantee the tool rests on.
-- ─────────────────────────────────────────────────────────────
CREATE VIEW company_profile AS
WITH latest AS (
    SELECT s.*,
           ROW_NUMBER() OVER (PARTITION BY company_id, key
                              ORDER BY observed_at DESC, id DESC) AS rn
    FROM signal s
)
SELECT
    c.id AS company_id,
    c.domain,
    c.country,
    c.legal_form,
    MAX(CASE WHEN l.key='platform.detected'          THEN l.value_text END) AS platform,
    MAX(CASE WHEN l.key='content.blog_last_post'     THEN l.value_date END) AS blog_last_post,
    MAX(CASE WHEN l.key='content.blog_post_count'    THEN l.value_num  END) AS blog_post_count,
    MAX(CASE WHEN l.key='content.blog_exists'        THEN l.value_num  END) AS blog_exists,
    MAX(CASE WHEN l.key='schema.article_present'     THEN l.value_num  END) AS article_schema,
    MAX(CASE WHEN l.key='schema.product_present'     THEN l.value_num  END) AS product_schema,
    MAX(CASE WHEN l.key='i18n.hreflang_count'        THEN l.value_num  END) AS hreflang_count,
    MAX(CASE WHEN l.key='perf.lighthouse_performance' THEN l.value_num END) AS lighthouse_perf,
    MAX(CASE WHEN l.key='agency.footer_credit'       THEN l.value_text END) AS agency_credit,
    MAX(CASE WHEN l.key='catalog.product_url_count'  THEN l.value_num  END) AS product_url_count,
    MAX(CASE WHEN l.key='reviews.count'              THEN l.value_num  END) AS review_count,
    MAX(CASE WHEN l.key='reviews.trusted_shops'      THEN l.value_num  END) AS trusted_shops,
    MAX(CASE WHEN l.key='impressum.gf_count'         THEN l.value_num  END) AS gf_count,
    MAX(CASE WHEN l.key='impressum.owner_named'      THEN l.value_num  END) AS owner_named,
    MAX(CASE WHEN l.key='ai.brand_mentions'          THEN l.value_num  END) AS ai_brand_mentions,
    MAX(CASE WHEN l.key='ai.queries_checked'         THEN l.value_num  END) AS ai_queries_checked
FROM company c
LEFT JOIN latest l ON l.company_id = c.id AND l.rn = 1
GROUP BY c.id;

-- ─────────────────────────────────────────────────────────────
-- Scoring. Recomputable from signals at zero API cost.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE score (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    run_id          INTEGER NOT NULL REFERENCES run(id),
    phase           INTEGER NOT NULL DEFAULT 1 CHECK (phase IN (1,2)),
    total           INTEGER NOT NULL,
    band            TEXT NOT NULL CHECK (band IN ('A','B','C','D')),
    ruleset_version TEXT NOT NULL,             -- bump when weights change; old scores stay readable
    computed_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_score_identity ON score(run_id, company_id, phase);

CREATE TABLE score_component (
    id        INTEGER PRIMARY KEY,
    score_id  INTEGER NOT NULL REFERENCES score(id) ON DELETE CASCADE,
    rule_id   TEXT NOT NULL,                   -- e.g. 'opp.blog_stale'
    points    INTEGER NOT NULL,
    reason    TEXT NOT NULL                    -- German, letter-ready: "Letzter Blogbeitrag: März 2023."
);
```

> `score_component.reason` is the whole point of the tool. It should be written so it can be pasted, near-verbatim, into a Brief. Not `blog_stale=true`.

```sql
-- ─────────────────────────────────────────────────────────────
-- Contacts. Separate table so GDPR purge is a single DELETE.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE contact (
    id                INTEGER PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    full_name         TEXT,
    role              TEXT,                    -- 'Geschäftsführer', 'Inhaber', …
    email             TEXT,
    phone             TEXT,
    postal_address    TEXT,
    source_url        TEXT NOT NULL,           -- must be the Impressum URL
    collected_at      TEXT NOT NULL,
    art14_notice_sent_at TEXT,                 -- GDPR Art. 14 information duty
    purge_after       TEXT NOT NULL            -- collected_at + 12 months; enforced by `portal purge` (§8)
);

CREATE TABLE outreach (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL CHECK (channel IN ('post','phone')),  -- see §8
    occurred_at TEXT NOT NULL,
    notes       TEXT,
    outcome     TEXT CHECK (outcome IN ('no_response','interested','declined','meeting','client'))
);

CREATE TABLE run (
    id             INTEGER PRIMARY KEY,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    stage          TEXT NOT NULL,              -- 'discover'|'fetch'|'extract_p1'|'score_p1'|'extract_p2'|'reconcile'|'score_p2'
    companies_seen INTEGER DEFAULT 0,
    places_calls   INTEGER DEFAULT 0,
    web_searches   INTEGER DEFAULT 0,          -- searches issued; billed separately from tokens (§7.8)
    llm_input_tokens  INTEGER DEFAULT 0,
    llm_output_tokens INTEGER DEFAULT 0,
    est_cost_usd   REAL DEFAULT 0,             -- reserved BEFORE each LLM call, reconciled after
    aborted_reason TEXT
);

-- ─────────────────────────────────────────────────────────────
-- D3: Batch API submissions. A submitted batch is committed spend
-- that may return after the submitting run has closed.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE llm_batch (
    id                INTEGER PRIMARY KEY,
    provider_batch_id TEXT NOT NULL UNIQUE,
    run_id            INTEGER NOT NULL REFERENCES run(id),
    purpose           TEXT NOT NULL,   -- 'impressum' | 'homepage'
    request_count     INTEGER NOT NULL,
    est_cost_usd      REAL NOT NULL,   -- reserved at submission
    actual_cost_usd   REAL,            -- written at reconciliation
    status            TEXT NOT NULL DEFAULT 'submitted'
                      CHECK (status IN ('submitted','completed','reconciled','failed','expired')),
    submitted_at      TEXT NOT NULL,
    reconciled_at     TEXT
);
CREATE INDEX idx_batch_status ON llm_batch(status);
```

## 5. Pipeline stages

Each stage is a separate CLI command, independently re-runnable: `python -m portal discover`, `… fetch`, `… extract-p1`, `… score --phase 1`, `… extract-p2`, `… reconcile`, `… score --phase 2`, `… serve`.

**Idempotency contract (D6).** Re-running a stage after a mid-run crash must not repeat any paid API call and must not create duplicate artifacts. It does **not** guarantee a byte-identical database, and v0.2 overstated this.

What is guaranteed:

- **No duplicate paid calls.** Extraction is keyed to `artifact.content_hash`; a hash with signals for the current `ruleset_version` is skipped. Batch submissions are recorded in `llm_batch` before the provider call returns.
- **No duplicate artifacts.** `uq_artifact_identity` plus the upsert in §4.
- **Scoring is a pure recompute.** `score --phase N` can be re-run any number of times at zero cost; `uq_score_identity` makes the write idempotent within a run.

What is not guaranteed: a crashed-then-restarted run gets a **new `run_id`**, so `uq_signal_identity` (which includes `run_id`) does not deduplicate across the restart. Deterministic signals will be re-observed under the new run. This is harmless — `signal` is append-only by design and `company_profile` resolves to the latest observation — but it means the database is not byte-identical to a clean run.

To resume under the original run instead, use `python -m portal <stage> --resume <run_id>`. This reuses the run row, so the unique index applies and the cost ledger stays in one place. Prefer `--resume` after a crash; use a fresh run for a genuine re-scrape.

### 5.1 discover

Input: a category + region (`"Zahnpflege Onlineshop"`, `"NRW"`) or a seed CSV of domains.
Places API call with a strict field mask — `displayName`, `websiteUri`, `formattedAddress` only. Requesting `rating` or `reviews` moves the call to a more expensive SKU tier. Deduplicate on normalised domain. Write `company` rows.

### 5.2 fetch

Politeness rules are **hard requirements**, not options:

- Fetch and honour `robots.txt` before anything else. Exclusion applies **only if the paths this tool needs** (`/`, `/sitemap.xml`, the Impressum path, the blog path) are disallowed for our User-Agent or `*`. A robots.txt that disallows `/checkout/` or `/account/` is normal and is not a refusal.
- One request per second per host, max 2 concurrent hosts.
- **"Host" for the politeness budget means the authority with any `www.` prefix removed.** `example.de` and `www.example.de` are one machine and get one budget. Keyed separately, the apex→www redirect that nearly every shop has would let each back-to-back pair issue two requests to one server inside a second — double this floor, on almost every domain in the corpus. **The port is kept**: `example.de:8001` and `example.de:8002` are separate servers. **Other subdomains are kept separate** — `shop.example.de` is commonly a different machine, and merging its budget with the apex would slow honest crawling for no gain. *Accepted, with the risk named:* where a shop does serve both names off one machine, we allow up to 2 req/s across the pair.
- This is **not** the same key as the one that decides which `robots.txt` applies. Robots is keyed to the origin (RFC 9309), so `example.de` and `www.example.de` are separate there and each is asked for its own file. Two questions, two keys: collapsing them one way doubles the request rate, the other way skips a robots.txt.
- `User-Agent: CreativePotatoesBot/1.0 (+https://creative-potato.global)` — identifiable, with a contact route.
- Plain `httpx` only. No headless browser unless a site returns an empty `<body>`, and then only as a per-domain opt-in flag.

**Which paths are "required" (M1.7).** Two of the four are not knowable before fetching, so the test is defined on what is:

- Hard-exclude when `/` is disallowed, **or** `/sitemap.xml` is disallowed, **or** *every* Impressum probe path (`/impressum`, `/impressum/`, `/imprint`, `/legal`, `/rechtliches`) is disallowed.
- A *single* disallowed path is never an exclusion. It is skipped per-URL and recorded with `error='robots_disallowed: …'`, so the skip is visible in the artifact table rather than silent.
- The blog path is checked per-URL once the sitemap reveals it. **A disallowed blog is a missing signal, not grounds for exclusion** — treating it as a refusal would discard exactly the leads whose weak blogs this tool exists to find.

**`Crawl-delay` (M1.2).** Honour `max(1.0, Crawl-delay)` for the stating host: our floor already exceeds a `Crawl-delay: 1`, and a larger value is a request to go slower, which we grant. A delay is read for our own agent token first and the `*` group otherwise.

Above **10 s**, do not obey — **skip the domain** and record `excluded_reason = 'crawl_delay_too_high: …'`. A value that large is a hostile or broken file, and one of two worker slots parked behind it for minutes costs the run more than the domain is worth. The delay is necessarily read from the robots.txt response itself, so that one request goes out at the floor; every subsequent request to that host respects the stated delay.

**Redirects are requests (M1.1).** A redirect chain must not be followed inside a single client call: that issues every hop below the rate limiter, so a site redirecting `http → https → /slash/` fires three requests at one host inside a second, and a cross-host hop reaches a host whose robots.txt was never read. Both breach the two rules above. Therefore:

- Follow at most **5 hops**, one at a time, each waiting on the limiter for **its own** host.
- **Every hop's target is checked against the applicable robots rules before it is followed (M1.12).** A hop that stays on the same authority is checked against the rules already loaded for it; a hop that **changes authority** is followed only after that authority's `robots.txt` has been fetched and consulted. Either way, a target the rules disallow is **refused and recorded** as `error='redirect_refused: …'`, never fetched. A new authority's `Crawl-delay` applies too, cap included.
- **The check is on the target, not on what was requested.** Robots permission is not transitive across a redirect: an allowed URL may redirect to a disallowed one, and the request that lands on the disallowed URL is the one robots governs. See M1.12 — this is not hypothetical, it happened on two of thirteen domains in the first crawl.
- The one exception is the `robots.txt` fetch itself, which may follow a hop within the seeded site (the apex↔www redirect nearly every shop has) — there is no earlier robots.txt that could authorise it, and the hop stays inside the domain we were asked to crawl. A hop off the seeded site during a robots fetch is refused.
- `artifact.url` records the **final** URL, so the evidence link points where the content actually came from.

`same_site` checks in the callers answer *attribution* — is this our page? They do not answer politeness, because by the time a caller sees the response the request has already gone out. The enforcement belongs in the transport.

**Failure artifacts update in place (M1.7).** `uq_artifact_identity` is a partial index over non-NULL hashes, so it does not constrain failure rows. Left to a plain INSERT, every re-run would append another row for the same dead URL and `artifact` would grow without bound. A failure row for the same `(company_id, kind, url)` is therefore **updated in place**, advancing `last_checked_at`. *When* a URL last failed is worth keeping; a row per attempt is not.

**Third-party XML (M1.3).** Sitemaps are attacker-controlled input parsed by stdlib `ElementTree`. A sitemap whose bytes contain `<!DOCTYPE` or `<!ENTITY` is **refused before parsing** — no real sitemap needs either, and refusing removes the entity-expansion class without adding a dependency. Bodies are capped at 8 MB before parsing and shards at 50 per company; a parse failure yields "no URLs" rather than aborting a run.

Fetch order: `robots.txt` → homepage → `sitemap.xml` (and any nested sitemaps) → Impressum → blog index if a blog path is found → one sample blog article under it (A6) → one sample product page if a product path is found.

**Product sample selection (A5).** `opp.no_product_schema` (+10) reads a single sampled product page, so which page is sampled has to be a stated rule. "Deterministic" here cannot mean "the same URL forever" — a catalog changes, and any rule keyed to the catalog picks differently once it does. The guarantee is **same inputs → same choice**, with the chosen URL recorded so the score traces to a specific stored artifact.

*Tier 0 — reuse.* If a `product_page` artifact already exists for this company and its URL is still a valid candidate, re-sample **that** URL and consider nothing else. This is what keeps `schema.product_present` from flipping between runs for reasons unrelated to the site, and it lets the content-hash short-circuit do its job.

Reuse holds only while the stored sample still returns **HTTP 200**. On a 404 or a fetch error the stored sample is discarded and selection falls through to Tier 1/2, so a dead sample is never pinned forever.

"Discarded" means **excluded from the re-selection**, not merely un-reused: a dead URL is still the code-point minimum of its candidate set, so without the exclusion the fall-through would re-choose the very URL that just failed. At most **two** product requests are made per company per run — the Tier 0 probe and one fresh selection. If the fresh selection also fails, no sample is recorded and no `schema.product_present` is written.

*Tier 1 — platform product sitemap.* When a platform-specific product sitemap is detected (Shopware `…-product-….xml(.gz)`, Shopify `sitemap_products_*.xml`, WooCommerce `product-sitemap.xml`), candidates are the **union of all its shards**, not the first shard. `.xml.gz` shards are decompressed before parsing.

*Tier 2 — path patterns.* With no product sitemap, candidates are URLs from the fetched sitemaps matching `/detail/`, `/products/`, `/produkt/`. With no sitemap at all, fall back to product-pattern links on the homepage.

`/p/` was in this list and is **dropped (M1.4)** until it is observed on a real shop. It is a product prefix on some platforms and a *pagination* prefix on others, and the two errors are not equally bad: a false positive feeds a listing page to `schema.product_present` and wrongly awards +10, while a false negative merely leaves the signal unwritten — which the zero-candidates rule below already handles correctly. When the failure modes are asymmetric, take the safe side. A pattern goes back into this list on evidence, not on plausibility.

*Ordering, in every tier:* the **lexicographically smallest** URL, compared by **Unicode code point**. Never locale collation — a locale-dependent sort over a corpus full of umlauts is a reproducibility bug. Code-point ordering over the union is invariant to both intra-file reordering and inter-shard redistribution, which document order is not: Shopware regenerates and re-shards product sitemaps on a schedule, so document order is stable only by accident.

*Filters, applied before ordering.* The ordering barely matters; which URLs qualify matters a great deal, because a non-product URL that reaches the candidate set produces a false `Product`-absent reading and wrongly awards +10. §5.3 already warns that Shopware sitemaps mix content and product URLs.

- Reject URLs carrying a query string — variant and filter permutations, not canonical pages.
- Require a path segment **after** the pattern. Bare `/products/` is Shopify's collection listing; `/products/<handle>` is the product.
- Reject URLs also matching category/listing patterns (`/kategorie/`, `/collections/`, `/c/`).
- Reject anything under the blog path detected for `content.blog_exists`.

*Zero candidates.* No product page is fetched and **no `schema.product_present` signal is written — not `0`.** A `0` there means "checked, absent", which fires `opp.no_product_schema` for +10 against a site whose product pages were never retrieved. That is the same error as the blog ladder's `NULL` branch (§6.2), and it is refused for the same reason. The same applies when a sample is selected but its fetch fails — 404, timeout, robots-disallowed.

No new review reason is needed: zero product candidates on a detected shop platform already satisfies `possible_marketplace_only` (§6.4).

*Auditability.* The chosen URL is recorded as `catalog.product_sample_url` (text, unscored), with `evidence_url` set to the sitemap or homepage it was read from. Without it, `schema.product_present` points at a product page with no record of *why that page*, and the selection rule is unauditable from the database.

**Blog article sampling (A6, M1.29).** `content.blog_last_post` and `schema.article_present` are read from **one sampled article**, not from the blog index — the index carries neither on the platform that is most of the corpus. Same shape as A5, same guarantee (*same inputs → same choice*), one additional request per company that has a blog.

*The anchor is the **index path**, not the blog path.* On Shopify the hierarchy is `/blogs/<blog-handle>/<article-handle>`, so a URL one level under `/blogs` is *another blog index*. "The shallowest URL under the blog path" — the obvious phrasing — selects `/blogs/karriere` on `bio-fleischer-laden.de` and hands a listing page to an `Article` parser, which is M1.16's error in a new place. Anchoring on the index M1.15 actually fetched makes the level unambiguous.

*Candidates* are same-site URLs, without a query string, strictly under the fetched index's path. *Tiers:* (1) a blog sitemap shard (M1.24 — membership is the evidence, no path shape required); (2) sitemap URLs under the index path; (3) links on the index page itself. *Ordering:* shallowest first, code-point minimum breaking ties.

No secondary-locale filter is needed here: `/de-ch/blogs/lifestyle/x` does not start with `/blogs/lifestyle/`, and M1.15 already prefers the shallowest index, which is the primary storefront's. The anchoring subsumes M1.25.

**A6.1 — zero candidates, or a failed article fetch, writes neither `content.blog_last_post` nor `schema.article_present`.** Not a `0`, not today's date. This is A5.5 applied to the same shape of absence, and it is why `schema.article_present = 0` no longer appears for shops whose article pages were never retrieved.

*Auditability.* The chosen URL is recorded as `content.blog_sample_url` (text, unscored), `evidence_url` being the blog index that fixed the anchor. `blog_article` joins the artifact kinds.

**Impressum discovery** is two-step: (1) footer links matching `impressum|imprint|legal notice|rechtliches`; (2) if none, probe direct paths `/impressum`, `/impressum/`, `/imprint`, `/legal`, `/rechtliches` before concluding absence. Only after both steps fail is `no_impressum` recorded — and for CH companies it sets `needs_review`, not `excluded` (§6.4).

Store bodies on disk under `data/artifacts/{domain}/{kind}-{timestamp}.html`, path recorded in `artifact.body_path`. Skip re-extraction when `content_hash` is unchanged from the previous run.

### 5.3 extract-p1 — deterministic parsers (no LLM, no cost, fully reproducible)

| Signal key | Method | Reliability note |
|---|---|---|
| `platform.detected` | HTML signature match on **anchored strings only** — Shopware: `/bundles/storefront/`; Shopify: `cdn.shopify.com`; WooCommerce: `wp-content` **and** `woocommerce`; JTL: any of `jtl-nav-wrapper`, `jtl-validate`, `jtl_token`, `jtlPackFormTranslations` | Signatures **observed**, not assumed — see M1.9/M1.10. The bare string `shopware` and bare `sw-` attributes are **not** signatures; `jtl-shop` is **not** a signature and never was one in the wild. |
| `content.blog_exists` | Blog/magazin/ratgeber/news path found in sitemap **or** homepage nav links | Good |
| `content.blog_last_post` | **Authoritative:** newest date parsed from the **sampled blog article** (A6, M1.29) — JSON-LD `datePublished`, `<time datetime>`, or German visible-date patterns (`12. März 2023`). Falls back to the blog index where the index itself carries dates. Sitemap `<lastmod>` is a hint only and is **never** used alone. | The index was the wrong page: Shopify blog indexes carry no `<time>` and no `datePublished` at all, and 5 of 7 detected blogs yielded no date from one. **The lastmod warning is now measured, not asserted — see below.** |
| `content.blog_post_count` | Count of post links on the blog index (paginated: first page count × page count if pagination is visible), cross-checked against sitemap URL count under the blog path | Sitemap counts include tag/category noise; index count wins on conflict |
| `catalog.product_url_count` | **A5's tier hierarchy, in A5's order (M1.24):** Tier 1 the product sitemap — recognised either by a platform filename convention *or* by the shard's own name (M1.24); Tier 2 product-typical paths (`/detail/`, `/products/`, `/produkt/`); otherwise not measurable (§10.3). Translations are excluded (M1.25). The tier is written to `value_text` alongside the count. | Path patterns are the **fallback**, not the default: reading them first counted `smile-store.de` at 6 against a catalogue of 194. A count of 6 from a path pattern and a count of 6 from a product sitemap are different claims, so the tier travels with the number. |
| `schema.article_present`, `schema.product_present` | Parse all `application/ld+json` blocks and collect `@type`. `article_present` is read from the **sampled blog article** (A6), `product_present` from the sampled product page (A5) plus the homepage. **Neither is ever written without its sample** (A5.5, A6.1). | Checking only the homepage under-detects; checking the *index* under-detects to zero. `Article`/`BlogPosting` lives on the post, and `schema.article_present` was `0` on **every** blog index in the corpus — a wrong "checked and absent" for shops whose posts all carry it. |
| `meta.description_length` | Homepage `<meta name="description">` length | **Informational only, not scored** — platforms auto-generate adequate-length templates |
| `i18n.hreflang_count` | Count of distinct `hreflang` values | `de-DE`/`de-AT`/`de-CH` variants are not real i18n; count distinct language codes, not locale codes |
| `perf.lighthouse_performance` | PageSpeed Insights API — **Phase 2 only** (slow: 15–30 s/site) | Cache by `artifact.last_checked_at` age; do not re-run within 30 days |
| `company.legal_form` | **Regex over the already-fetched Impressum HTML** (A1). Strip `<script>`/`<style>` first, then take the *provider block*: the text after an anchor (`Angaben gemäß § 5`, `Gesetzliche Anbieterkennung`, `Anbieterkennzeichnung`, `Verantwortlich für den Inhalt`, `Diensteanbieter`, `Impressum`) whose following ~400 characters contain a postal-code-and-place. Match longest form first — `GmbH & Co. KG` before both `GmbH` and `KG`. | **Measured 7/12 on the stored corpus, 0 false positives** — see below. Anchoring is load-bearing, not tidiness: a naive first-match-in-page found a cookie-consent vendor's `GmbH` on two shops and a trust-seal `e.V.` on a third. |
| `agency.footer_credit` | Regex for `realisiert von\|umgesetzt von\|powered by\|Webdesign:` in footer, plus outbound footer links whose anchor/title contains `agentur\|design\|media\|digital` | Under-detects (logo-only credits). Treated as bonus negative signal, never as a gate |
| `reviews.trusted_shops`, `reviews.count` | Trusted Shops badge script detection; visible aggregate review count in `AggregateRating` JSON-LD | Free product-strength proxy |
| `catalog.product_sample_url` | The product URL selected by the A5 rule in §5.2. Written by `fetch`, not by an extractor — it records a fetch-time decision. | **Unscored.** Exists so the sample behind `schema.product_present` is auditable |

**The `lastmod` warning, measured (M1.29).** This spec has said since v0.1 that sitemap `<lastmod>` "is regenerated on deploys and systematically lies fresh". That was an assertion. It is now a measurement, taken on the exact signal the rule protects — `bio-fleischer-laden.de`, whose blog sitemap shard is on disk:

| what was read | date |
|---|---|
| newest post date parsed from the blog **index** | **2022-12-01** |
| newest `<lastmod>` on an **article** URL in the blog shard | 2025-01-23 |
| newest `<lastmod>` on a **listing** URL in the blog shard | 2026-02-25 |

**The error is directional, which is what makes it expensive.** `opp.blog_stale` is an award for *not publishing*. A shop whose newest post is from 2022 presents, via lastmod, as active in 2026 — so the instrument suppresses the award on precisely the stale blogs the rule exists to find, and does it silently. A freshness proxy that only ever errs fresh is not a weak instrument; it is an instrument pointed the wrong way.

`lastmod` may still be recorded as a separate hint (`content.blog_lastmod_hint`) for a human resolving `blog_date_unparseable` — "has this blog been touched at all" is a real question. **No §6 rule may read it.**

**Why `legal_form` is extracted here rather than in §5.5b (A1).** It makes `qual.owner_operated`'s first disjunct decidable in Phase 1, which is worth having on its own: a company whose Impressum says `e.K.` banks the +15 before any LLM is called, and its `remaining_upside` under §5.4's gate drops from 50 to 35 accordingly.

**It does not, by itself, fix the gate** — the claim first written here, that this returns `PHASE2_MAX_POINTS` to 35, was wrong. The other two disjuncts are LLM extractions, so Phase 2 can still award the rule to a company Phase 1 could not; the +15 stays reachable and stays in the bound. See M1.22, which replaces the global constant with a per-company gate.

The regex is not a cheaper `ImpressumExtract.legal_form`; it is a *different* signal with a *different* reliability, and §5.5b still extracts the full legal identity for advancing companies. Where both exist and disagree, the LLM extraction wins — it reads the whole page, this reads one window.

**What it actually found, on all 12 stored Impressum pages (2026-08-15):**

| result | n | domains |
|---|---|---|
| `GmbH` | 4 | bio-fleischer-laden.de, propellerdiscount.de, snocks.com, verpackungskoenig.de |
| `GmbH & Co. KG` | 2 | smoke2u.de, zecplus.de |
| `Ltd` | 1 | doonails.de — a **Cyprus** Ltd, which is a lead-quality fact in its own right |
| no form stated | 5 | blackpolish.de, germanelectronic.de, navucko.com, opulent-wohnen.com, smile-store.de |

**7 of 12, with no false positives.** The five misses were checked by hand and are all correct: each names a natural person and an address with no legal-form token at all (`Benjamin Luzolo BLACKPOLISH`, `NAVUCKO Nataša Vučković`, `Christian Riedel OPULENT Wohnen`, `Kay Link`, `Lampenflut.de Inh. Dominik Lindemeier`). German law does not require a form token from a sole trader, so "absent" is the page being accurate, not the parser failing.

**A consequence that must be ruled on with the rest (see M1.21).** The disjunct is `legal_form ∈ {e.K., Einzelunternehmen, GbR}`, and **not one of the 12 satisfies it** — the seven found are GmbH, GmbH & Co. KG and Ltd. The five that *are* owner-operated sole traders are exactly the five with no token to match. So the arithmetic fix works, and the predicate as written would award `qual.owner_operated` to **zero** of this corpus while the ideal leads sit in the unmatched group. The marker that identifies them is `Inh.`/`Inhaber` (found on `lampenflut.de`) or simply a personal name standing where a company name would be — the latter being a judgement, not a regex.

### 5.4 score --phase 1

Pure function over `company_profile`. Costs nothing.

**The Phase-2 advance gate is not the B band.** Phase 2 can add points Phase 1 cannot observe, so gating on the Phase-1 band would permanently discard companies whose final score would have been A. A company scoring 54 in Phase 1 with +35 still available is an 89 — a clear A that would never be looked at.

**The gate is per-company, not a global constant (M1.22).** This *replaces* D1's `PHASE2_MAX_POINTS`; it is not an amendment to it. D1 remains in the changelog as superseded.

```
remaining_upside(c) = sum of the maximum positive points of every rule that
                      Phase 2 can still influence for c, and that Phase 1 has
                      not already awarded to c.

advance(c)          if phase1_total(c) + remaining_upside(c) >= B_band_floor
```

**Why a global constant cannot work here.** A rule belongs in the bound if Phase 2 can still *add* its points — not if all its inputs are Phase-2-only. `qual.owner_operated` (+15) has three disjuncts, and §5.3 now decides the first one in Phase 1 (`company.legal_form`). The other two — ≤ 2 natural-person Geschäftsführer, owner named on site — are LLM extractions, so a company that fails the legal-form test **can still win the full +15 in Phase 2**. The rule stays Phase-2-reachable, the bound must still contain it, and any global constant is therefore stuck at 50, giving `ADVANCE_THRESHOLD = 5` — at which `qual.ecommerce_platform` alone clears the gate and the gate stops gating.

The first-crawl measurement makes this sharper rather than softer. The five shops with no legal-form token are exactly the five sole traders (§5.3) — the companies *most* likely to win the rule in Phase 2 on "owner named". **The +15 that has to stay inside the bound sits on the best leads**, which is precisely why it cannot be dropped from a global constant to make the arithmetic pleasant.

**Why per-company is strictly better than any safe global constant.** It is safe by construction — no company whose final score could reach B is discarded, since `remaining_upside` bounds what Phase 2 can add for *that* company — and it is tighter, because a company that has already been awarded a rule in Phase 1 cannot be awarded it again:

| company | won `qual.owner_operated` in Phase 1? | `remaining_upside` | effective threshold |
|---|---|---|---|
| legal form is `e.K.` | yes, +15 already banked | 35 (`own_brand` 10 + `ai_invisible` 15 + `slow_site` 10) | 20 |
| legal form is `GmbH`, or absent | no — Phase 2 may still award it | 50 (35 + `owner_operated` 15) | 5 |

A global constant must use the worst case for every company; this uses each company's own.

**Each rule declares whether Phase 2 can still change its outcome**, and the declaration is part of the ruleset rather than inferred from the signal names — inference is what produced the wrong answer in the first place. **Assert at startup that every rule carries the declaration**, and fail loudly on any that does not, so adding a rule cannot silently widen or narrow the gate. A new Phase-2-reachable rule automatically raises `remaining_upside` for the companies it applies to.

**Cost consequence, stated honestly and now worse than D1 claimed.** Under D1's arithmetic the threshold was 20; under a correct per-company gate it is 20 only for companies that already banked `owner_operated`, and 5 for the rest — and on the verified corpus *none* of the twelve banks it (§5.3), so in practice nearly everything advances today. The two-phase split still excludes clear no-hopers, but the saving is smaller than §7 assumes and shrinks further the worse `legal_form` coverage is. **The honest reading: the gate's value now depends on the `qual.owner_operated` predicate (§10.1), not on the gate's arithmetic.**

**Score direction:** Phase 2 can also *lower* a score (`neg.has_agency` may fire on `HomepageExtract.agency_credit` where the footer regex missed it). The gate concerns maximum upside only; a Phase-2 score below its Phase-1 predecessor is expected and correct.

Record per company, as signals, both the decision and the number behind it — `gate.phase2_admitted` (`value_num` 0/1) and `gate.remaining_upside` (`value_num`). A company that stopped just under the line must be auditable, and with a per-company gate "just under the line" now means something different for each company, so the threshold it was actually judged against has to be recorded rather than reconstructed.

### 5.5 extract-p2 — paid signals, advancing companies only

**(a) PageSpeed Insights** — as in §5.3, run here.

**(b) Impressum extraction — Anthropic SDK, structured output via tool use. No framework.**

```python
from pydantic import BaseModel
from typing import Optional, List

class ImpressumExtract(BaseModel):
    legal_name: Optional[str]
    legal_form: Optional[str]          # GmbH, GmbH & Co. KG, e.K., Einzelunternehmen, …
    street: Optional[str]
    postal_code: Optional[str]
    city: Optional[str]
    country: Optional[str]
    managing_directors: List[str]      # [] if none named — do NOT infer from elsewhere
    owner_name: Optional[str]          # Inhaber/in for e.K./Einzelunternehmen — distinct from GF
    register_court: Optional[str]      # Amtsgericht …
    register_number: Optional[str]     # HRB … — absent for Einzelunternehmen, that is valid
    vat_id: Optional[str]              # USt-IdNr.
    email: Optional[str]
    phone: Optional[str]

class HomepageExtract(BaseModel):
    one_line_offer: Optional[str]      # German, max 20 words, what they actually sell
    product_categories: List[str]
    audience: Optional[str]            # 'b2c' | 'b2b' | 'both' | None
    owner_named_on_site: bool          # is a founder/owner presented by name?
    own_brand: Optional[bool]          # manufacturer/own-brand vs pure reseller — best-fit segment marker
    agency_credit: Optional[str]
```

Input preparation is a hard requirement, not an optimisation: strip `<script>`, `<style>`, `<svg>`, `<nav>`, comments; reduce to text + structural tags; **cap at 60 KB**. Pages exceeding the cap after cleaning are truncated from the end (Impressum content is near the top of an Impressum page). This is the primary defence against unbounded token spend (§7).

Prompt discipline — stated verbatim in the system prompt of the extraction call:

> Return `null` for any field not present on the page. Do not infer, do not guess, do not fill from general knowledge. If the page is not an Impressum, return all nulls.

Hallucinated Impressum data is the single worst failure mode here: it produces a confident wrong name in a letter to a stranger. Every LLM-derived `signal` row carries `method='llm'` and must be visually distinguished in the UI. Additionally: `legal_name` and `managing_directors`/`owner_name` values are verified by exact substring presence in the cleaned page text; a value not literally present on the page is discarded and the signal written with `confidence=0` for review.

**Model:** Claude Haiku 4.5 via the Batch API (50% off; latency is irrelevant here). Extraction requests are keyed to `artifact.content_hash` so a resumed run never re-submits an already-extracted page.

**(c) AI-visibility check — the differentiating signal.**

For each Phase-2 company, derive **2** German category queries (configurable, default 2, hard maximum 3) from `one_line_offer` and `product_categories` — e.g. *"beste Ultraschallzahnbürste"*, *"Ultraschallzahnbürste Test"*. Run each against Claude with web search enabled and a fixed prompt asking which brands or shops it would recommend.

Record as signals:

| key | type | content |
|---|---|---|
| `ai.queries_checked` | num | how many queries actually completed |
| `ai.brand_mentions` | num | in how many the company's brand or domain appeared |
| `ai.competitors_mentioned` | text | comma-separated brands that did appear |
| `ai.query_text` | text | the literal queries run, pipe-separated |
| `ai.checked_at` | date | date of the check |
| `ai.model_used` | text | model ID, e.g. `claude-haiku-4-5-20251001` |

The last three exist solely so the research brief can state its basis (§8). They are not optional and not for debugging — without them the finding is an unverifiable comparative claim about a named third party.

**Cost — corrected.** v0.2 estimated ~2k tokens per query. That was wrong by roughly an order of magnitude: a web-search-enabled call injects search results into context, realistically **10–20k input tokens per query**. Two queries per company is ~30k input tokens, roughly **$0.03–0.04 per company on Haiku 4.5**, before the separate per-search charge.

**Web search billing — confirmed 2026-08-15.** Web search is charged **$10 per 1,000 searches ($0.01 per search)** on the Claude API, in addition to token costs. Each search counts as one use regardless of result count; searches that error are not billed. The count is reported per response in `usage.server_tool_use.web_search_requests` and must be accumulated into `run.web_searches`. The Batch API does **not** discount the per-search charge (batch web-search calls are priced the same as regular ones), so at 2 searches per company the search fee is **$0.02 per company**, bringing the AI-visibility sub-stage to roughly **$0.05–0.06 per company**. The per-search charge must be included in the pre-call reservation of §7.3.

**Methodological constraint.** This measures one model, on one date, with web search enabled. It is a defensible *baseline*, not a statement about AI systems in general. §8 and §9 govern how it may be worded in anything sent to a prospect.

### 5.6 reconcile

`python -m portal reconcile` — polls every `llm_batch` row with status `submitted`, writes returned extractions as signals, sets `actual_cost_usd`, moves status to `reconciled`. Safe to run repeatedly. Must be run before `score --phase 2` produces trustworthy output; `score --phase 2` warns loudly if unreconciled batches exist for the companies being scored.

**B4 — reconciled signals carry the submitting run's `run_id`**, i.e. `llm_batch.run_id`, not the id of the run doing the reconciling.

`uq_signal_identity` is `(run_id, company_id, key, evidence_url)`. Writing under a fresh `run_id` on each invocation would mean the unique index cannot dedupe, so a `reconcile` that writes 40 of 60 companies and then dies would have the next invocation re-insert all 60. Under the submitting run's id, the §4 `ON CONFLICT … DO NOTHING` idiom behaves as it does everywhere else and "safe to run repeatedly" actually holds. It also keeps the reserved spend (§7 control 4) and the resulting evidence on the same `run` row.

Consequences to expect rather than treat as bugs:

- `observed_at` is reconcile wall-clock time, not the submitting run's. So `signal.observed_at > run.finished_at` is normal, and `company_profile`'s latest-wins resolution is unaffected — the batch result genuinely is the newest thing known.
- A run's signal set can therefore grow after its `finished_at`. A finished run is not a closed one until its batches have reconciled.
- The reconciling run still gets its own `run` row with `stage='reconcile'`, for started/finished timestamps and batches polled. It just does not own the signals.

**B3.1 — the cost ledger reconciles against the submitting run.** `llm_batch.actual_cost_usd` is written at reconciliation, and the estimate-to-actual correction is applied to the *submitting* run's `run.est_cost_usd`, because that is where §7 control 4 made the reservation. The reconciling run does not absorb the delta.

### 5.7 score --phase 2

Recomputes the full score including Phase-2 signals. Writes a `phase=2` score row; the UI shows the latest phase available per company.

## 6. Scoring model — ruleset v3

**The score measures opportunity size, not company quality.** A high score means "this company has a strong product and visibly weak content marketing" — i.e. a good fit for the offer. State this in the UI so it is never misread as a quality ranking.

### 6.1 Qualification (is this a real, fitting business?)

| rule_id | Condition | Points |
|---|---|---|
| `qual.ecommerce_platform` | Shopware / Shopify / WooCommerce / JTL detected | +15 |
| `qual.owner_operated` | `legal_form ∈ {e.K., Einzelunternehmen, GbR}` **or** Impressum names ≤ 2 natural-person Geschäftsführer **or** owner named on site | +15 |
| `qual.product_depth` | ≥ 20 product URLs | +10 |
| `qual.own_brand` | Sells own-brand/manufactured products, not pure reselling | +10 |
| `qual.own_domain_shop` | `catalog.product_url_count >= 5` — the exact inverse of §6.4's `possible_marketplace_only` (B7) | +5 |
| `qual.product_strength` | Trusted Shops badge present or ≥ 50 aggregate reviews | +10 |

**`qual.own_domain_shop` had no predicate at all until now (B7).** It existed only as a row in this table with a prose gloss, so M3 could not implement it and it could never fire — a permanent −5 on every company, which makes §6.5's bands stricter than the calibration they were set from. Defined here as the inverse of the soft flag that already covers the same ground, so the two can never disagree: `possible_marketplace_only` is raised when a platform is detected and `catalog.product_url_count < 5`; this awards +5 when the count is ≥ 5. It is computable in **Phase 1**, from a signal §5.3 already writes.

Two consequences to state rather than discover later:

- **When `catalog.product_url_count` is unwritten, the rule does not fire** — no +5, and no `possible_marketplace_only` either. That is the same "checked and absent ≠ never checked" discipline as A5.5, and it is not hypothetical: on the four JTL shops in the verified corpus no product URLs are identifiable at all (findings §4), so all four forgo this +5 for a reason that has nothing to do with their business.
- **The bands in §6.5 were calibrated with this rule assumed live.** Now that it can actually fire, scores rise by 5 for most qualifying companies — which restores the calibration rather than shifting it, but it does mean §6.5 should not be re-tuned on data gathered before this change.

`qual.ecommerce_platform` is only as good as the §5.3 signatures behind it, and it is the single largest false-positive risk in this table: it is +15 on a string match. As of the first crawl, JTL (4 shops), Shopify (7) and WooCommerce (1) are all **observed** against real homepage HTML (M1.9, M1.10). **Shopware is only half-observed:** the SW6 signature has never matched a real shop, and Shopware 5 is knowingly undetected (M1.11). A Shopware 5 shop therefore scores 15 points lower than an identical Shopware 6 one, for no reason that has anything to do with the business.

### 6.2 Opportunity (how weak is their content marketing?)

**Blog ladder — evaluated as an ordered chain, first match wins, evaluation stops.** Written as a chain rather than a table because the table format is what allowed overlapping predicates in v0.2.

```
days_since_newest = (today − content.blog_last_post).days      # NULL if no date parsed
post_count        = content.blog_post_count

if not blog_exists:
    → opp.no_blog          +25
elif blog_last_post is NULL:
    → no rung fires; raise review_flag 'blog_date_unparseable' (§4, §6.4)
elif days_since_newest > 365:
    → opp.blog_stale       +20
elif post_count < 10:
    → opp.thin_blog        +12
elif days_since_newest >= 180:
    → opp.blog_slowing     +10
else:
    → no rung fires (blog is current and substantial)
```

The `blog_last_post is NULL` branch is new and deliberate. A blog index whose dates cannot be parsed is an unknown, not a stale blog. Guessing here would put a false claim into a letter. Route it to human review instead — this is the same principle as `confidence=0` on unverified LLM extractions (§5.5b).

`opp.thin_blog` now has a precise predicate: fewer than 10 posts, newest post within the last 365 days. The undefined term "active-ish" is removed.

**Conditional and independent rules — unchanged from v0.2:**

| rule_id | Condition | Points |
|---|---|---|
| `opp.no_article_schema` | Blog **exists** and no `Article`/`BlogPosting` in JSON-LD on blog pages. Never fires together with `opp.no_blog`. | +8 |
| `opp.no_product_schema` | No `Product` in JSON-LD on a product page. **Fires only when `schema.product_present` was written from a product page fetched with HTTP 200** (§5.2, A5) — absent that signal the rule fires in neither direction. | +10 |
| `opp.ai_invisible` | `ai.queries_checked >= 2` and `ai.brand_mentions = 0` (Phase 2) | +15 |
| `opp.slow_site` | Lighthouse performance < 50 (Phase 2) | +10 |
| `opp.de_only` | Single distinct language (locale variants don't count), expansion angle | +5 |

The old 45-point cap is removed: mutual exclusivity in the ladder plus the conditional schema rule eliminate the double-counting structurally.

### 6.3 Negative signals

| rule_id | Condition | Points |
|---|---|---|
| `neg.has_agency` | Footer names an agency (text or linked credit) | −20 |
| `neg.active_content` | ≥ 4 posts in the last 6 months | −25 |

### 6.4 Hard exclusions and soft review flags

Never delete, never silently drop. Two tiers:

**Hard (`excluded = 1`, reason recorded):**

*Rejections — "not a viable lead":*

- `robots_disallowed` — only when required paths (§5.2) are disallowed
- `competitor` — site is itself a marketing/web agency
- `too_large` — requires **two** independent indicators from: Konzern structure, > 250 employees stated, > 5 named Geschäftsführer, "Vorstand" **together with** register type AG and multi-location footprint. A lone "Vorstand" mention never excludes (small AGs and Vereine have one).
- `unreachable` — after 2 attempts on different days

*Merged away — "the same lead as #N", a distinct class (M1.18):*

- `duplicate_site` — this company's site resolves to a host another company row already owns. Recorded as `excluded_reason = 'duplicate_site: <host> is company #<id>'`.

**These two classes must not be presented alike.** A rejection means we looked and this is not a prospect; a merge-away means the prospect is real and lives under another row — the company is not gone, its evidence is somewhere else. A UI that lists them together tells the reader a live lead was rejected. The discriminator is the stable `duplicate_site:` prefix on `excluded_reason`, and it must stay stable for exactly that reason; the referenced `#<id>` is the row to follow. Everything else about a merged-away row — artifacts, signals, review flags — is left in place and never merged automatically, because choosing which legal name, contact, score and outreach history survives is a decision with a letter at the end of it.

**Soft (one `review_flag` row per reason, surfaced in a dedicated UI filter, human decides):**

These are independent and can all apply to one company, which is why they are rows rather than a shared column. Raising one sets `company.needs_review` by trigger; resolving the last open one clears it.

- `no_impressum` — after the two-step discovery in §5.2 fails. For DE/AT this usually means not a real trading business, but it can be a footer-parsing miss, so a human glances before it dies. For **CH** companies this is always soft: the Swiss disclosure duty (UWG) is structured differently from §5 DDG and legitimate Swiss shops may present the information under "Kontakt".
- `possible_marketplace_only` — shop platform detected but < 5 product URLs on own domain
- `blog_date_unparseable` — the blog index exists but no post date could be parsed (§6.2)
- `catalog_not_measurable` — the site serves sitemaps, and no tier of A5's hierarchy can identify a product in them (§5.3, §10.3). Ratified after M2 and added in migration 003. Three rules go quiet at once when this fires — `qual.product_depth` (+10), `qual.own_domain_shop` (+5), `opp.no_product_schema` (+10) — so the company most in need of a human is precisely the one about which the pipeline says least. The **signal** `catalog.not_measurable` carries the reason text, which a flag has no room for; the flag carries the routing. Same division of labour, and the same principle, as `blog_date_unparseable`: where the pipeline cannot measure, route to a person rather than guess a number.
- `domain_moved` — the seeded domain now serves a different registrable domain and the new host was adopted (§5.1, §5.2). Whether the shop behind the new host is still the lead that was intended is a judgement about the lead list, not one the crawler may make: `germanelectronic.de` now serves `lampenflut.de`, a different brand with a different catalogue.
- `duplicate_site` — raised on the company that **owns** a host another row tried to adopt, so a merge target is visible rather than silently accumulating duplicates pointed at it. Distinct from the exclusion of the same name, which sits on the row that lost.

**Resolution is sticky.** Once a reason has been resolved for a company, that same reason is never raised for that company again — a later run that re-detects the condition writes nothing. This is a policy decision, not an artefact of the schema, and it is what `uq_review_flag` plus `ON CONFLICT DO NOTHING` implement.

The trade: a genuinely changed situation will not re-surface. Accepted deliberately, because the alternative re-adjudicates the same judgment on every run — the CH shop whose Impressum lives under "Kontakt" would return to the review queue monthly, forever, and a queue that refills itself stops being read. The human decision is the more durable fact.

Consequences to hold onto:

- Resolving is a considered act, not a way to clear the screen. The UI should present it as such.
- Re-raising, if it is ever wanted, is an explicit operator action (deleting the flag row), not something a pipeline stage does on its own.
- `resolved_by_human` distinguishes the two ways a flag closes: `1` for a human dismissal, `0` for a pipeline clear. Only the human case is a judgment; the distinction exists so the two are never conflated when reviewing what was skipped.

### 6.5 Bands

`A ≥ 75` · `B 55–74` · `C 30–54` · `D < 30`

(Thresholds raised slightly vs. v0.1 because ruleset v2 added up to +35 new available points — `qual.own_brand`, `qual.product_strength`, `opp.ai_invisible`. Re-tune after the first 100 scored companies; `ruleset_version` makes this a zero-cost recompute.)

## 7. Cost controls

Non-negotiable, implemented as code, not as discipline.

1. **Google Cloud Console quota cap** set below the free SKU threshold for every Places SKU. Make it physically impossible to be billed.

2. **Rolling 30-day ceiling — the outer bound.** Before any paid call:

   ```sql
   SELECT COALESCE(SUM(est_cost_usd), 0) FROM run
   WHERE started_at > datetime('now','-30 days');
   ```

   Abort if this exceeds `MONTHLY_CEILING_USD` (**default `$45`** — raised from `$25` by M1.23; the arithmetic is below). This is the control that actually bounds spend. The per-run ceiling below does not: `run.est_cost_usd` resets on every invocation, so ten aborted-and-retried runs cost ten times the per-run limit. v0.2 claimed runaway spend was impossible; without this check it was not.

3. **Per-run ceiling with pre-call reservation.** Before every LLM call, the *estimated* cost is added to `run.est_cost_usd` and checked against the per-run ceiling (default `$5.00`). After the response, the estimate is reconciled to actual usage. A crash between call and write can only over-count, never under-count — the failure mode is a conservatively aborted run, not silent overspend.

4. **Batch submissions reserve the whole batch at submission time.** A submitted batch is committed spend regardless of whether the process survives to read the result. Reserve into both `llm_batch.est_cost_usd` and `run.est_cost_usd` before the submit call returns.

5. **Input size cap.** LLM inputs are cleaned and capped at 60 KB (§5.5b). Closes the unbounded-spend path of multi-megabyte Shopware homepages.

6. **Content-hash short-circuit.** Unchanged page → no LLM call. Extraction keyed to `artifact.content_hash`, effective across runs *and* within a resumed run.

7. **Two-phase gating** (§5.4) — restricts paid signals to companies whose Phase-1 total plus their own `remaining_upside` could still reach the B floor. Per-company since M1.22, so the effective threshold varies (20 for a company that already banked `qual.owner_operated`, 5 for one that has not). **This saves less than the flat-threshold model assumed**, and on the verified corpus, where no company banks that rule, it currently admits nearly everything — see §5.4.

8. **Web search accounting.** `run.web_searches` counts searches issued, read from `usage.server_tool_use.web_search_requests` on each response. The per-search charge is **$10 per 1,000 searches**, billed separately from tokens and not discounted by the Batch API (§5.5c). Include it in the pre-call reservation at `$0.01 × planned_queries` per company.

9. Every API key from environment variables. `.env` in `.gitignore`. No keys in the repo, ever.

### 7.1 Steady-state cost, and why the ceiling moved to $45 (M1.23)

M1.22's per-company gate admits roughly **95%** of discovered companies today, because no company in the verified corpus banks `qual.owner_operated` in Phase 1 and everything else clears an effective threshold of 5 (§5.4). At ~500 discovered companies/month that is ~475 advancing, and the per-advancing-company cost is:

| item | basis | cost |
|---|---|---|
| AI-visibility searches | 2 queries × $0.01/search (§5.5c, not batch-discounted) | $0.020 |
| AI-visibility tokens | ~30k input tokens on Haiku 4.5, live (§5.5c) | $0.030–0.040 |
| Impressum + homepage extraction | ~30k tokens, Batch API (50% off) | $0.015 |
| PageSpeed Insights | free tier | $0.000 |
| **total per advancing company** | | **$0.065–0.075** |

```
475 advancing × $0.065  =  $30.88 / month
475 advancing × $0.075  =  $35.63 / month
```

**So the old $25 default would abort every month, not occasionally.** A ceiling that trips on correct, expected behaviour teaches its operator to raise it without reading it, which is worse than having no ceiling.

**Decision: raise the default to `$45` rather than cut the AI-visibility default to one query.** Both were considered:

- *One query instead of two* would give ~$0.04–0.045 per company and ~$21/month, comfortably under `$25`. It is rejected because **§6.2's predicate for `opp.ai_invisible` is `ai.queries_checked >= 2`**: at one query the rule can never fire, in either direction. That is not a $9/month saving — it silently removes a **+15** rule, the joint-largest opportunity signal in ruleset v3, and the one the outreach pitch actually rests on ("invisible in AI answers for its own category"). Trading the measurement to fit the budget inverts the point of measuring. If one query is ever wanted, the §6.2 predicate must change in the same commit and §6.5's bands must be re-tuned.
- *$45* leaves ~25% headroom over the $35.63 worst case. It is a **runaway guard** — a bug, a redirect loop, a pathological catalogue — and no longer a budget. That reframing is the substantive change: the previous text wanted the ceiling "to bite occasionally, which is how you find out the model is wrong", and that job now belongs to the per-run ceiling (control 3, `$5.00`) and to `run.est_cost_usd` reconciliation, both of which sit closer to the spend and fire without aborting a month.

**Expected steady-state: $31–36/month at ~500 discovered companies/month**, up from v0.3's $20–35 and v0.2's $15. The increase is not drift; it is three corrected errors compounding — D4's ten-times token estimate, the confirmed per-search charge (§5.5c), and M1.22's finding that no safe gate can be as tight as D1 assumed.

**This figure falls if `qual.owner_operated` becomes Phase-1-identifiable** (§10.2). Every sole trader recognised in Phase 1 banks +15, raising that company's effective threshold from 5 to 20 — the single largest lever on this number.

## 8. Compliance requirements

These are requirements, not recommendations. They shape the schema, so they cannot be bolted on later.

**No outbound email capability.** B2B cold email in Germany is restricted under §7 UWG; prior consent is required in practice and the "mutmaßliche Einwilligung" exception is narrow. The `outreach.channel` enum permits only `post` and `phone`. The application must contain no SMTP client, no mail API dependency, and no send button. This is why "no email sending" is a non-goal in §2 rather than a backlog item.

**GDPR.** Named individuals in the `contact` table are personal data, processed under legitimate interest (Art. 6(1)(f)). Consequences for the build:

- Art. 14 imposes an information duty when data is collected from a source other than the data subject — the notice goes out with the first postal contact. `contact.art14_notice_sent_at` tracks it.
- `contact.purge_after` defaults to collected_at + 12 months. A `python -m portal purge` command deletes expired rows and must actually be run.
- A `python -m portal forget --domain X` command hard-deletes all rows for one company across every table, for erasure requests.
- Company-level data (domain, platform, blog cadence) is not personal data and is not subject to the above. Keeping the two in separate tables is what makes this tractable.

**Crawling conduct.** robots.txt honoured, identifiable User-Agent, 1 req/s. This is partly legal hygiene and partly commercial: the pitch involves telling a prospect you analysed their site, and their server logs should support that story.

**Comparative claims in outbound material.** The research brief names competitors — *"genannt werden stattdessen: …"*. That is comparative advertising, lawful under §6 UWG only where the comparison is objective and verifiable. Two consequences, both binding on the export:

Every AI-visibility statement in an exported brief must carry its basis inline: the literal query text, the date of the check, the model used, and the fact that web search was enabled. These come from `ai.query_text`, `ai.checked_at`, `ai.model_used`. An export missing any of them must fail, not degrade gracefully — a brief that asserts a competitor comparison without its basis is the failure this rule exists to prevent.

Wording is constrained to what was measured. Permitted: *"Bei 2 von 2 geprüften KI-Abfragen wurden Sie nicht genannt."* Not permitted: *"Sie sind in KI-Systemen unsichtbar."* One model checked once does not support a general claim, and the gap between the two is the same category of error as a Heilversprechen — asserting more than the evidence carries.

## 9. UI

Single page, server-rendered, HTMX for interactions. No SPA.

- Table: company, band (with phase indicator), score, city, platform, one-line offer, last blog post, AI-visibility (e.g. `0/2`).
- Filter by band, platform, country, excluded status, **needs_review**.
- Row expands to show every `score_component` with its reason and a link to the evidence artifact.
- LLM-derived fields visually marked (e.g. a dotted underline) and hoverable to show `confidence` and `evidence_url`. Fields with `confidence=0` (failed substring verification, §5.5b) rendered in red — never trust these in a letter without checking the source.
- Actions per row: mark excluded (with reason), clear/confirm needs_review, log an outreach attempt, export the research brief.
- An expanded row lists its open `review_flag` reasons individually, each independently clearable — clearing writes `resolved_at`, `resolved_by_human = 1` and an optional note, so "not yet reviewed" and "reviewed and dismissed" stay distinguishable. The row-level `needs_review` marker clears when the last open flag does.

**Research brief export** (per company, German, Markdown). Findings section built from `score_component.reason` sentences. KI-Sichtbarkeit section built from the `ai.*` signals, in the format proven in live pitches, with a mandatory basis line:

> **KI-Sichtbarkeit**
> Geprüft am 15.08.2026 über Claude (`claude-haiku-4-5`) mit aktivierter Websuche.
> Abfragen: „beste Ultraschallzahnbürste" · „Ultraschallzahnbürste Test"
> Ergebnis: Bei 2 von 2 Abfragen wurde Ihre Marke nicht genannt.
> Genannt wurden stattdessen: Emmi-Dent, Philips Sonicare, Curaprox.

The export function asserts the presence of `ai.query_text`, `ai.checked_at` and `ai.model_used` before writing, and raises if any is missing. Briefs for companies that did not reach Phase 2 omit the KI-Sichtbarkeit section entirely rather than rendering it empty.

## 10. Open decisions

### 10.1 Blockers — M3 may not start until these are addressed

| # | Blocker | Why it blocks |
|---|---|---|
| A1 / M1.21–M1.22 | The Phase-2 advance gate. A global `PHASE2_MAX_POINTS` cannot be derived correctly, so §5.4 is rewritten as a per-company gate. | M3 scores, and the gate decides what Phase 2 costs. **Awaiting ratification** — it is a scoring-model change (§5.4). |
| M1.14 | **`content.blog_exists` under-detects, and a `false` reading is not strong enough to carry `opp.no_blog`'s +25 on its own.** Two shapes in a 13-shop corpus are unreachable by any path vocabulary: a blog on a subdomain (`blog.zecplus.de`) and a blog served as root-level slugs (`lampenflut.de`). | M3 scores, and `opp.no_blog` is the **largest single award in ruleset v3**. Firing it on a shop that publishes weekly is the worst error the model can make: it manufactures the exact opportunity the outreach letter is about. The right instrument is anchor text or `Article` JSON-LD, both of which are M2 territory — so this is recorded here, not fixed in M1. |

*(§5.3 naming the wrong page for `content.blog_last_post` and `schema.article_present` was the third blocker here. **Closed by M1.29 / A6**, ratified and implemented: the signals now read from a sampled article and A6.1 writes nothing where none is obtained. It stays visible in the amendment log rather than here.)*

Neither of the two above is a coding task. Both are decisions about what a signal is allowed to assert when it did not find something, which is the same question `A5.5` and the `blog_last_post is NULL` branch already answer with "write nothing rather than write zero".

**A third instrument for M1.14, assessed and reported negative.** A semantically named sitemap shard (`blogs-0-sitemap.xml`, `sitemap_blogs_1.xml`) is cheaper than both anchor text and `Article` JSON-LD, and is not a path-vocabulary entry, so it does not fall under M1.4's rule. It was measured against both unreachable shapes and **reaches neither**:

| shape | shop | why the shard does not reach it |
|---|---|---|
| blog on a subdomain | `zecplus.de` | Its sitemap index lists four shards — products, pages, collections, discovery — and no blog shard. The blog is on `blog.zecplus.de`, a different host with its own sitemap that this shop's index never mentions. |
| blog as root-level slugs | `lampenflut.de` | Serves no sitemap at all: `/sitemap.xml` is a 404 and `robots.txt` declares no `Sitemap:`. There is no index to read labels from. |

So M1.14 stays open on the instruments already named. The shard reading earns its place for a different reason (M1.24: keeping content out of the catalogue count) and as the cheapest available source of **article URLs and per-article `lastmod`** — which is what makes the blog-date proposal above cost one request per shop rather than several.

### 10.2 The Phase-2 cost lever — not a correctness blocker

**Should `qual.owner_operated` admit an `Inh.`/`Inhaber` marker, or a personal name standing where a company name would be?**

This is **the primary lever on Phase-2 spend**, not a ranking refinement, and §7.1 is where its effect shows up. §5.4's gate is safe either way — a company that cannot bank the rule simply carries its +15 in `remaining_upside` and is admitted more readily — so nothing recoverable is ever discarded. But that is exactly the mechanism: **every sole trader made Phase-1-identifiable moves one company from an effective threshold of 5 to 20**, and thereby out of Phase 2 unless it earns its way in on other signals.

On the verified corpus the predicate (`legal_form ∈ {e.K., Einzelunternehmen, GbR}`) matches **none** of the twelve, while five are plainly owner-operated sole traders whose form is simply unstated — `Lampenflut.de Inh. Dominik Lindemeier`, `NAVUCKO Nataša Vučković`, `Benjamin Luzolo BLACKPOLISH`, `Christian Riedel OPULENT Wohnen`, `Kay Link`. That is 5 of 12 (~42%) whose effective threshold is 5 when it arguably should be 20, and it is why §7.1's steady-state reads $31–36/month rather than something lower.

It stays out of §10.1 because it does not block correctness, and it is not settled here because a personal name standing where a company name would be is a judgement rather than a regex, and twelve shops is not the sample to decide it on. **Settle it on a larger corpus, and re-derive §7.1 when it is settled.**

### 10.3 §6.5's bands must not be calibrated on the current corpus

**A JTL shop cannot earn up to 25 points, on URL structure alone.** Every rule downstream of `catalog.product_url_count` is affected by the root-slug shape (findings §4), not only B7:

| rule | predicate | why it is unavailable |
|---|---|---|
| `qual.product_depth` | ≥ 20 product URLs | +10 — no URL is identifiable as a product |
| `qual.own_domain_shop` | ≥ 5 product URLs (B7) | +5 — same count, unwritten |
| `opp.no_product_schema` | needs a sampled product page (A5.5, A5.6) | +10 — no sample can be selected |

§6.5's bands are **20 points wide**, so this moves a shop a full band — silently, and in a model whose stated purpose is to measure *opportunity* rather than *platform*. It affects **3 of 13** shops in the verified corpus: `opulent-wohnen.com`, `smoke2u.de`, `verpackungskoenig.de`. (It was four. `smile-store.de` left this group when M1.24 found its product sitemap — see below.)

Two consequences:

1. **Do not calibrate or re-tune §6.5 on this corpus.** Roughly a quarter of it is systematically ~25 points light for reasons that have nothing to do with the businesses. Calibrating here would bake the instrument's blind spot into the band thresholds, and thereafter the blind spot would look like a property of German SME shops. This compounds with B7's own warning (§6.1) about not re-tuning on data gathered before B7 could fire — the two must be satisfied together, which in practice means calibration waits for a corpus gathered after both are settled.
2. **The under-measurement is visible per company**, not inferred from an absent signal: the `catalog.not_measurable` signal carries the reason and, since migration 003, `catalog_not_measurable` routes the company to §6.4's review queue.

**When is a written count untrustworthy? Still open, and with one fewer piece of evidence than it had.** The three-state rule catches *zero* matches; it does not catch *few*, and few-matches is indistinguishable from a small catalogue. The case this was raised on was `smile-store.de` — 6 counted against ~360 real products — and it turned out **not** to be an instance of the problem but a bug: the shop had published a perfectly good product sitemap under a name the tool could not read (M1.24). It now reports 194 from Tier 1, `qual.product_depth` fires on evidence, and B7 fires on evidence rather than by luck.

That resolution is the reason to keep the question open rather than close it. A count that is wrong because an instrument was misread looks exactly like a count that is wrong because the catalogue is small, and the corpus now contains **no** known instance of the latter — which is not the same as there being none. Inventing a plausibility threshold ("fewer than N is suspicious") on zero observations is the M1.4 error. **Settle it on a corpus where a genuine few-product shop has been seen**, and until then note that Tier 1's provenance in `value_text` is the cheap partial answer: a low count from `product_sitemap` is the shop's own statement, and a low count from `sitemap_path_pattern` is ours.

### 10.4 Tracked so it stops disappearing

- **B7 — `qual.own_domain_shop` (+5).** Raised at Task 0, then untracked through three reviews: it appeared only as a row in §6.1 with a prose gloss and no predicate anywhere, so it was invisible to every subsequent pass. **Now defined** as `catalog.product_url_count >= 5` (§6.1). Recorded here rather than closed silently, because "a rule that cannot fire" is a defect class that hides in exactly this way — a table row reads as implemented. The remaining question is whether the threshold should be 5 or higher; 5 is chosen only because §6.4's `possible_marketplace_only` already uses it and two thresholds for one distinction is worse than a debatable one.

- **`neg.has_agency` must never match a platform credit.** `agency.footer_credit` reads "powered by" as an agency signature, and *"Powered by JTL-Shop"* ships by default on every JTL install — so on `smoke2u.de` it fired `neg.has_agency` against a shop for its choice of shop system. Named here as an explicit exclusion rather than left as a fixed bug, because **the same string has now caused two opposite defects**: as `jtl-shop` it was the §5.3 platform signature that detected no JTL shop at all (M1.9, because the credit is capitalised and removable), and as "powered by" it was an agency credit that detected a shop system. A removable, capitalised, platform-shipped string is weak evidence of a platform and *no* evidence of an agency, and any future rule reading footer credits must exclude the platform vocabulary — currently JTL, Shopify, WooCommerce, WordPress, Shopware, Magento, PrestaShop, Gambio, OXID, plentymarkets — before it reads anything else.

### 10.5 Undecided, not blocking

- Ollama for local extraction instead of Haiku — saves ~$10/month at Phase-2 volumes, costs German-language extraction quality and the substring-verification simplicity. Currently: use Haiku.
- Whether to store artifact bodies compressed (gzip) — likely yes above a few hundred companies.
- Research brief export stays Markdown for v1; DOCX (letter-ready) is a candidate for v1.1 once the brief content has stabilised against real outreach feedback.
- Band thresholds (§6.5) are provisional pending the first 100-company calibration run.
