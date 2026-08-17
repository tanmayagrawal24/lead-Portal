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
| M1.14 | **Shopify's blog path is `/blogs/`, plural, and the vocabulary had only `blog`.** Five shops publishing actively — one with 670 blog URLs — reported "no blog path found". `content.blog_exists` would read false and fire `opp.no_blog` **+25**, the largest award in ruleset v3, against shops with live content marketing. | `blogs` added to `BLOG_SEGMENTS`, observed on 5 shops. **Two further real blogs remained undetectable and were deliberately left so**, because a path-segment vocabulary is the wrong instrument for them. *Both are now reached* — see M1.34, which added anchor text and made `opp.no_blog` abstain where the search cannot have been exhaustive. | §5.3 |

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

| M1.30 | **A6 preferred the sampled article's date over the index's, and a sample is not a maximum.** The index's date is the newest over every post it lists; the article's is one post's. Preferring the sample lost 17 months on `bio-fleischer-laden.de` and 4 on `navucko.com` — and it is not simply the other way round, because on `ekomia.de` the sampled article is 4 months *newer* than anything the index dates. | Neither source is preferred. Both are **lower bounds** on the last post date, and `content.blog_last_post` takes the later of the two, with `evidence_url` naming whichever produced it. Never worse than either alone. The remaining gap — that even the maximum is a lower bound — is recorded in §10.5. | §5.3 |
| M1.31 | **Two parsers could not read what real pages carry, and only fetching an article exposed either.** (a) `_ISO_DATE` ended in `\b`, so it never matched a *timestamp*: in `2026-05-29T10:56:32+0200` the `9` and the `T` are both word characters. `datePublished` and `<time datetime>` carry timestamps, so **4 of 7 blogs reported no date at all**. (b) `snocks.com` wraps all three of its JSON-LD blocks in a legacy `// <![CDATA[` guard, which is not JSON — the documents failed to parse entirely and `schema.article_present` read **`0` on a page carrying `BlogPosting`**. | (a) `(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)`, which keeps the guard the boundary was there for — a longer number containing a date shape is still not a date. (b) The CDATA guard is unwrapped before the block is judged; a wrapper is not malformed JSON. Both observed on real pages, both pinned. | §5.3 |

| M1.32 | **The two lower bounds are not the same kind of lower bound, and §6.2 would have read the weaker one as a staleness claim.** M1.30 established that the index's date and the sample's are both lower bounds and took the later. But the index's is a *maximum over the posts it lists*, while a sampled article's is one post's date with nothing behind it. Where the index carries no date at all, `content.blog_last_post` is a floor with no ceiling — it can establish that a blog is **at least** this fresh, and it can never establish that one is stale. `opp.blog_stale` (+20) and `opp.blog_slowing` (+10) are awards for *not* publishing, so both would have fired on evidence that cannot carry them. Measured: **2 of 13 shops** (`doonails.de`, `snocks.com`), and on `snocks.com` it is the difference between +20 and nothing. | An **interim guard**, independent of when §10.5's selector question resolves. `content.blog_last_post_basis` (`index` / `article` / `both`, unscored) travels with the date the way A5's tier travels with `catalog.product_url_count`. Where the basis is `article`, the two staleness rungs do not fire — **in either direction**, which is A5.5's discipline rather than a suppression. `opp.thin_blog` and the is-it-current branch are unaffected: neither asserts staleness, and a lower bound is sound evidence of freshness. Written as an *enabling* fact, so a run predating the guard fails safe. | §5.3, §6.2 |

| M1.33 | **The guard abstained and told nobody, and it was the fourth time this spec had made the same decision from scratch.** M1.32 silenced two rungs on `snocks.com` — newest observed post 2022-08-26 — and left no queue entry, so the pipeline's most conspicuous silence was also its least visible. The wider problem is that A5.5's unfetched sample, §6.2's NULL-date branch, `catalog_not_measurable` and now M1.32 are one decision taken four times, each re-argued from first principles, with the routing part remembered three times out of four. | **A7 (§5)** names it: *where a measurement exists but cannot support the rule that reads it, the rule fires in neither direction, the reason is written, and a human is told* — all three parts required. `blog_date_unbounded` (§6.4, migration 004) supplies the routing for instance 4, raised by `score` where the rung is suppressed, carrying the lower bound in the new `review_flag.raised_note`. It is a distinct reason from `catalog_not_measurable`: *a value that cannot bound the rule* and *no value at all* send a person to different pages. Naming A7 immediately exposed that **instance 1 has no routing at all** — recorded against A5.5, not silently fixed. | §5, §6.2, §6.4, §4 |

| M1.34 | **§10.1's second blocker, ruled on and closed.** `content.blog_exists = 0` was the one place in the pipeline that wrote a confident measurement the spec had documented it could not make, fired the ruleset's largest award (+25) on it, and told nobody — A7 applied to the biggest number in §6.2, and not applied. Two shapes in the corpus were unreachable by any path vocabulary, both publishing: `zecplus.de`'s blog is a host its own sitemap never names, and `lampenflut.de`'s is a root-level slug on a shop that serves no sitemap at all. | **Six parts.** (a) **Anchor text** as a second detector, path vocabulary first — the href taken wherever it points, which is the whole reason it reaches a subdomain. (b) `opp.no_blog` **abstains** where the search cannot have been exhaustive, and the abstention **suppresses the whole ladder** rather than falling through: if `blog_exists` is unknown, `blog_last_post` is not a meaningful question. (c) The exhaustiveness test is **both instruments ran**, not "was there a sitemap" — `zecplus.de` has four shards and is the counter-example. Even both is not proof, and §6.2 says so. (d) `blog_undetectable`, migration 005. (e) A7's table **split** into measurement limits and transient failures, the second retrying and flagging only after **N = 3 runs on distinct days**. (f) The first-match-wins property named in §6.2: every guard below rung 1 raises the cost of rung 1 being wrong. | §5.2, §5.3, §5, §6.2, §6.4, §4, §10.1 |

**M1.34's third part is the one that would have been got wrong.** "Did we have a sitemap to search" is the obvious exhaustiveness test, it is what the analysis proposed, and the counter-example was already on disk: `zecplus.de` serves four sitemap shards and its blog is on `blog.zecplus.de`, a host none of them mentions. Having a sitemap made one of two instruments available; it did not bound the search space. The corrected test costs nothing extra to compute and is the difference between a licence to award +25 and a claim we cannot support.

**And the guard raised the stakes on the gate above it.** M1.32 closed `opp.blog_stale`'s +20 route to a manufactured opportunity, leaving the +25 route as the only one — bigger, and until now the only rung in the ladder with no abstention behind it. Because the ladder is first-match-wins, a false rung 1 also short-circuits everything M1.32 protects: on `zecplus.de` all of that guard's care about lower bounds never executed at all. **Closing the cheaper route raised the expensive route's weight rather than lowering it**, and that is a property of ordered ladders rather than of this bug, so it is written into §6.2 as one.

### Amendments from M3 — 2026-08-16

**Four of these came out of one exercise: auditing every rule for absent-input behaviour *before* writing any of it** (`docs/m3-absent-input-audit.md`, asked for as a precondition of M3). The A7 defect had been found five times by then, every time by accident. Reading all seventeen rules as a table found three more in an afternoon, plus a defect in the read model that was quietly undoing the guards already in place.

| # | Finding | Resolution | Sections |
|---|---|---|---|
| M1.35 | **The anchor-text tie-break picked a leaf post as the index.** `lampenflut.de` carries three vocabulary anchors, all at depth 1: the nav label *"Licht-Ratgeber"* and two article headlines. Code-point order alone chose `Kinderzimmerleuchten…` — a post — and handed it to A6 as an index. Nothing is nested under a post, so no article could be sampled and the blog dated nothing: M1.14's instrument would have detected the blog and then learned nothing about it. | Order by same-site, then depth, then **shortest anchor text**, then code point. An index link is a *label*; a post link is a *headline*. Measured on the one shop in the corpus that carries both, and it is M1.15's own argument about nav links applied one level down. | §5.3 |
| M1.36 | **The read model resurrected fixed bugs.** `company_profile` pivoted the latest observation per key across *all* runs. That was harmless until abstention became the mechanism — A5.5, A6.1 and M1.34 all work by **declining to write**, and a view keyed on the all-time latest never sees a retraction. Two live instances on the corpus: `agency.footer_credit` still serving `"Powered by JTL-Shop"` from a pre-fix run (**−20 on three JTL shops for their choice of shop system**, §10.4's named defect arriving through the view), and `content.blog_exists = 0` on `zecplus.de` from a pre-M1.34 run (**+25**, the bug M1.34 closed, one week old). | Latest-per-key scoped to the **latest run of that key's own stage, per company** (migration 006). Per stage, so a Phase-1 re-extract cannot blank what Phase 2 was paid for. Per company, so a partial or `--resume` run cannot blank the companies it skipped. By omission as well as by value, which is the whole point. | §4, §5 |
| M1.37 | **`neg.active_content` (−25) had no data path.** "≥ 4 posts in the last 6 months" needs dates for several posts; A6 samples one article, `blog_last_post` is one date, `blog_post_count` has no recency in it. B7's shape on the largest negative in the ruleset — and worse, because a negative that never fires inflates every active publisher's score. Measured: decidable on **2 of 13**. `doonails.de` lists 26 posts, published one 2½ months ago, and dates **none** of them. | `content.blog_post_dates` (§5.3) carries every distinct post date off the index. The rule **fires** on ≥ 4 recent — sound on a lower bound — **declines** only on a complete enumeration, and **abstains** in between. Dates rather than a recency count, so §5's zero-cost recompute cannot decay. | §5.3, §6.3 |
| M1.38 | **`opp.de_only` (+5) was inverted against its own population.** The parser returns `None` for "no `hreflang` at all", extract wrote nothing, so the rule could fire only for shops that *declare* `hreflang` with one language — and never for a shop with no `hreflang`, which is what German-only looks like. 7 of 13. | A fetched homepage with no alternates writes `i18n.hreflang_count = 0`. The rule fires at `≤ 1` and abstains where no homepage was read. | §5.3, §6.2 |

**Two guards were added to rules that had not fired wrongly yet**, `opp.no_article_schema` (A6.1's unwritten signal read as a `0`, +8 on a page never fetched) and `opp.slow_site` (a NULL read as `< 50`). Neither bites on this corpus — all seven blogs yielded an article, and Phase 2 has not run — which is precisely why they would have shipped. They are in A7's tables as instances 9 and 8.

**M1.36 is the one to remember.** The other three are rules that could not reach their population; that class is now checked at startup. M1.36 is different in kind: **every** abstention guard in this spec works by not writing a signal, and the read model was serving the last value anyway. The guards were correct and the plumbing under them was not, and no test could have caught it because every test builds its signals in one run.

**Neither M1.31 defect was reachable before A6.** Both live on article pages, and until A6 no article page was ever fetched — the ISO-timestamp bug had been in `content.blog_last_post` since the parser was written and could not be seen from an index that carries no timestamps at all. Fetching the right page is what made two silent parsers testable.

**M1.29's cheaper alternative was assessed and rejected as the primary instrument.** Blog sitemap shards carry a `<lastmod>` per article, on both platforms that serve one, and it is already on disk. It measures modification, not publication — and the corpus contains a measured instance of it lying by three years. The numbers are in §5.3; the consequence is that a freshness proxy erring only *fresh* suppresses `opp.blog_stale` on exactly the stale blogs the rule exists to find. It is admissible as a hint for a human, never as the value.

### Amendments from M3, second pass — 2026-08-16

**These came from accepting M3 and then running it.** Two are corrections to work landed the same day, and both were found by the same move: taking a claim the code makes and asking what it rests on.

| # | Finding | Resolution | Sections |
|---|---|---|---|
| M1.39 | **Migration 006 trusted the latest run of a stage to be complete, and a crashed run is not.** Per §5 (D6) a crash-then-restart mints a **new `run_id`**, so a run that wrote 10 of 13 companies and died becomes the latest run of its stage — and the 3 it never reached read as *retractions* rather than as *incompleteness*. That inverts 006: it exists to stop a stale value persisting, and un-narrowed it makes a live value **vanish**, silently, because absence is exactly what every A7 guard reads as "do not fire". | The authoritative run for a (company, stage) is the latest one that **finished** — `finished_at IS NOT NULL` and no `aborted_reason` (migration 007). A partial run is ignored **wholesale**, keys it did write included: falling back per key mixes two runs' beliefs and rebuilds the defect 006 closed. `fetch.run` and `score.run` set `finished_at` from the success path only and record an abort otherwise; they wrote it from a `finally`, which marked a crashed run finished — a column cannot be made load-bearing while it lies. | §4, §5 |
| M1.40 | **`content.blog_last_post_basis` described which sources had a date, not which date was written.** It said `both` whenever the index and the sampled article each produced one — and §6.2 reads `both` as *the index bounds this from above*. On `zecplus.de` the index's newest was 2021-03-10, the sampled article was 2025-09-03, the article won the maximum, and the basis still claimed a bound from an index that had plainly failed to date the newest post we were holding. `opp.blog_slowing` took **+10** on it. M1.32's defect, arriving through the basis instead of through its absence — live on **3 of 13** shops. | The index bounds the value only where the index's own maximum **is** the value written; otherwise the basis is `article` and §6.2's staleness rungs stay silent. Found by re-scoring after the crawl and reading the reason text, not by a test. | §5.3, §6.2 |
| M1.41 | **§5.4's safety claim was stronger than the gate.** "No company whose final score could reach B is discarded" counts only Phase-2 reachability, so a Phase-1 rule that **abstained** contributes nothing — and an abstained rung is not a rung that scored zero. `propellerdiscount.de` is stopped at 0 + 50 against a floor of 55 with a blog ladder worth up to +25 abstaining. | The claim is narrowed, not the gate widened: **nothing whose final score could reach B is discarded without a human being told.** Counting abstained rungs as upside was refused — it buys the guarantee with Phase-2 spend on points that may not exist. The property depends on the review queue actually being read, and that dependency is now written down next to it. | §5.4 |
| M1.42 | **M1.40 generalised: a provenance field computed by a different expression than the value it describes can disagree with it, and `evidence_url` did — worse than the basis had.** `catalog.product_url_count` cited `sitemaps[0]`, the first sitemap row by id, while the number came from classified, locale-filtered shards. On **8 of 8** shops with a count, the cited document contained **zero** of the URLs counted: every one was a sitemap *index*. On `smile-store.de` it named `/shop/en/sitemap.xml` — the English subshop — while the 194 came from the primary-locale shard *after* M1.25's filter dropped `/shop/en/` as a translation. `catalog.product_sample_url` was worse still: `f"{base}/sitemap.xml"`, synthesised under a docstring forbidding synthesis, naming no artifact at all on 2 of 13 and the seeded host rather than the served one on 2 more (M1.18's blinding, in a provenance field). And 370 signals carried `evidence_url = ''`, which the implementation brief forbids outright. This is M1.17 — a confident answer read off the wrong page — arriving through provenance instead of through the artifact, and §8's export asserts on the field. | **The citation is taken off the object the value was read from, and has no string form.** `extract._write` and `fetch._write_sample_signal` take an artifact, not a URL, and write `evidence_url` and `signal.artifact_id` — declared since 001 and until now **NULL on all 2,274 rows** — out of one expression, so the two cannot name different documents and neither can name a document that was never stored. Where one `evidence_url` cannot name a set, it names the largest contributing member and the extent goes in the value text. Where no stored source exists, **no signal is written** rather than a plausible-looking string. `schema.product_present` follows whichever of its two pages carried the markup (latent, 0 of 9, but +10 rides on it); the blog-absence signals cite the homepage, which is the page both §5.3 instruments actually read. Audit in `docs/provenance-desync-audit.md`. | §1, §4, §5.2, §5.3, §8 |

| M1.43 | **M1.17 fixed the code and left the row it had written.** `snocks.com` artifact **265** is stored as `kind='impressum'`, HTTP 200, with a body whose `content_hash` is byte-identical to homepage artifact 82 — the soft redirect to `/#gbaid979323` that M1.17 is named for, filed as an Impressum by run 2 before the guard existed. The guard is correct today and rejects that URL. What nobody recorded is that **the poisoned row is still there and is that company's highest `artifact.id` of its kind**, so "the newest Impressum" selects the homepage. Latent until §5.5b, then material: the page would be sent to the LLM to be read for a legal name and directors, and a name in the homepage footer would pass substring verification because it is genuinely on the page that was sent — the backstop catches a hallucination, not a wrong page. Blast radius measured across every artifact of every kind: **exactly one row.** | Two parts, both in M5. (a) Artifact selection for extraction **excludes any artifact whose `content_hash` matches a `homepage` artifact of the same company** — structural, so it catches a mis-filed page however it was mis-filed, rather than re-testing the URL shape the guard already tests. (b) A one-off repair of the row, which is safe to delete: its body duplicates a homepage artifact that still exists. ~~and artifact 171 is a real Impressum for the same company~~ — **corrected by M1.44: artifact 171 is robots-disallowed and is not usable either, so this repair leaves the company with no Impressum.** **The general lesson is the one to keep: a fix to a writer does not repair what the writer already wrote, and no stage re-reads old artifacts to check.** | §4, §5.2, §5.5b |

| M1.44 | **M1.43's lesson, a second time: the robots fix left its bodies on disk.** M1.12 records that an allowed Impressum probe was redirected onto a `robots.txt`-disallowed path and fetched, on 2 of 13 domains, and the guard is correct today — run 2 onward records `redirect_refused: …` for exactly those URLs. What M1.12 does not say is that **the bodies fetched before the fix are still stored**. Measured over all **521** stored 200-with-body artifacts, each URL tested against the newest `robots.txt` stored for its own company: **exactly two**, both from run 1, both `kind='impressum'` — `snocks.com` **171** (`/policies/legal-notice`, 635 KB, under `Disallow: /policies/`) and `smoke2u.de` **186** (`/Impressum`, 367 KB). Nothing fetched after the fix is disallowed. Material because **M1.43's own guard selects 171** for `snocks.com`: the fix for one selection defect hands §5.5b a page the tool was not permitted to fetch, to be read for a legal name and directors. `smoke2u.de` is unaffected — its newest allowed Impressum (274) is newer than 186. Third consecutive selection defect on one company's Impressum, each found only by checking the previous fix against the data. | Two parts, both in M5, the same shape as M1.43's. (a) **Selection excludes any artifact whose URL the company's stored robots policy disallows**, checked at selection time against the newest stored `robots.txt` — structural, so it catches the class however the row was created. (b) **A one-off repair of 171 and 186**: the body is deleted, the row and its `error` are kept, because the request genuinely happened and §5.2 wants that recorded. Accepted consequence: `snocks.com` is then left with **no usable Impressum artifact** and routes to `no_impressum`, which is what §5.2's two-step does with an absence anyway (M1.17). **The root cause under both: no stage validates a stored artifact against anything. The `artifact` table records what was fetched and is read as if it recorded what may be used.** | §4, §5.2, §5.5b |

| M1.45 | **A rate-limit response was read as an absence, and half of §6.4's resolution model has no writer.** `snocks.com` carries an open `no_impressum` flag raised at `2026-08-15T12:42:11Z` — the same second artifact 361 returned **429**, the last of three rate-limit responses in run 4 (`/imprint`, `/legal`, `/rechtliches`, all 429). `_discover_impressum` tests `response.ok`; a 429 is not ok, the probe loop falls through, and absence is recorded. **A 429 is not a page that does not exist — it is a measurement that could not be made**, which is A7's shape exactly: the rule fired in one direction on evidence supporting neither. Two further problems behind it: absence is judged from **one run's responses** rather than from the `artifact` table, though M1.17 already states that *`artifact` is the interface M2 reads by kind*; and §6.4 defines `resolved_by_human = 0` as *a pipeline clear* while **no code path ever writes `0`** — the only writers are `leadlist.resolve_flag` and `serve`, both writing `1`. So an open flag can only ever be closed by a person. This is **not** §6.4's stickiness, which governs *resolved* reasons and is working. Direction of error: `no_impressum` raises a flag and does not exclude (`company.excluded = 0` on all 13), so it errs toward more human attention — a queue item, not a blocker. `ekomia.de`'s identical-looking flag is **correct** (five 404s, one robots refusal, no 429), which is the contrast that isolates the defect. | Proposed, not yet ratified. (a) `no_impressum` is raised only when the two-step completes with responses that **answer** — a 404, or a refusal establishing the page is unavailable to us. An inconclusive probe is an **abstention** with its reason written and a human told (§5), not a conclusion. (b) Absence is judged against the `artifact` table, where *usable* must carry M1.44's meaning or the query re-admits the two rows M1.44 removes. (c) **Amended on ratification — the hazard was stated backwards, and neither option is built yet.** §6.4's stickiness rule is *"once a reason has been resolved for a company, that same reason is never raised for that company again"*, enforced by `uq_review_flag` + `ON CONFLICT DO NOTHING` — **and a pipeline clear is a resolution.** So implementing `resolved_by_human = 0` exactly as §6.4 specifies would mean **one successful Impressum fetch suppresses `no_impressum` for that company permanently, including after the page later disappears.** The risk is not a queue that refills itself; it is one that **seals silently** — and a queue nobody can re-raise into is worse than a noisy one, because §5.4's whole safety property depends on the queue being read. The choice is therefore explicit and **not yet taken**: either (i) stickiness applies to **human dismissals only** (`resolved_by_human = 1`) and pipeline clears are re-raisable, which keeps both halves of §6.4 honest; or (ii) the clear stays unbuilt and **the line comes out of §6.4**, because a documented resolution path with no writer is a claim the tool does not keep. **Recommendation: (i)** — it is the reading under which `resolved_by_human` means what §6.4 says it means, and the stickiness argument (*a queue that refills itself stops being read*) is about a **human** having already looked, which a pipeline clear is not. Build neither until ruled. **Note the premise this arrived under was wrong**: the flag was reported as firing while two real Impressum artifacts sat on disk. Both are unusable — 265 is the homepage (M1.43), 171 is robots-disallowed (M1.44) — so after both repairs **the flag is correct**, reached by reasoning that is not. | §5.2, §6.4 |

| M1.46 | **`impressum.gf_count` could fire a +15 rule on an absence — and, once guarded, could not tell a verification failure from one.** `_owner_operated` reads `if directors is not None and directors <= 2`. Mapping `len(managing_directors)` straight onto the key makes an Impressum that names no Geschäftsführer write `0`, `0 <= 2` holds, and the rule takes **+15 for naming nobody** — on **6 of 11** stored Impressum pages (measured on the ratified M1.43+M1.44 selection, where `snocks.com` has no usable Impressum at all; 7 of 12 on the naive one). `qual.owner_operated` is one of two rules whose banking raises a company's effective Phase-2 threshold from 5 to 20, so the error propagates into §7.1's spend model. The guard against it opened a second hole: if the model returns two directors and **both fail substring verification**, §5.5b writes no `contact` rows, the count goes unwritten, and `llm.impressum_extracted = 1` sits beside it — *exactly* the state that means "the page was read and names none". A verification failure and a genuine absence become indistinguishable, on the input to a +15 rule. | **Both halves, because they fail differently.** (a) **Mapping:** `impressum.gf_count` is written only when the page names ≥ 1 natural-person Geschäftsführer. (b) **Predicate:** §6.1 disjunct 2 becomes **`1 <= directors <= 2`**. *The mapping is a convention that holds while every writer remembers it; the predicate is an invariant that holds when one doesn't* — on a +15 rule, both. (c) **The count counts what the model returned, with `confidence = 0` when any returned name failed verification: verification governs `contact` rows, not the count.** The two questions differ — *is this name real enough to put in a letter* versus *how many people does this page name* — and §9 renders `confidence = 0` red, so an unverified count reads as unverified rather than as nothing. The same question was then asked of every other field whose absence-signature is load-bearing: `impressum.owner_name_present` takes the same treatment (**`1` with `confidence = 0`** on a failed verification, because a `0` there would corrupt the very base rate §10.2 is to be decided on), and `impressum.legal_name` already had it. | §5.5b, §6.1 |

| M1.47 | **The two scored fields Phase 2 adds to §6.1 cannot be substring-verified at all.** §5.5b's verification list is `legal_name`, `managing_directors`, `owner_name` — values *quoted from the page*, which is the only thing a substring check can test. `HomepageExtract.owner_named_on_site` and `own_brand` are **booleans**: judgements about a page, with no string to find in it. So `site.owner_named` (**+15**, §6.1 disjunct 3) and `brand.own_brand` (**+10**) carry **no verification of any kind** — **25 points of ruleset v3 riding on unverifiable model output**, and the backstop the whole §5.5b design leans on does not reach either. Found by asking M1.46's question of every field rather than only the ones it named. Direction of error is **too high** in both cases (an award taken that was not earned), which by M1.37's third axis is the direction that blocks outbound contact rather than filling a queue. | **Not resolved — recorded so it is decided rather than discovered on a paid run.** Substring verification is the wrong instrument for a judgement, so the options are different in kind: (i) accept the exposure and mark both as unverifiable in §9 rather than silently rendering them like verified values; (ii) require a *quoted span* alongside each boolean (`owner_named_on_site` returns the name it saw, `own_brand` the phrase it read it from) so the substring check has something to test — a §5.5b model change that makes the guard reach them; (iii) drop both from scoring and keep them as unscored hints, as `agency_credit` already is. **(ii) is the recommendation**: it is the only option that makes the backstop actually cover a scored Phase-2 field, and it costs a schema field rather than a rule. Ruling belongs with §6.1's qualification block, which is with the operator. | §5.5b, §6.1, §9 |

| M1.48 | **A2 §8's nine measurements shipped without the instrument that produced them.** The A2 proposal was preserved as a deliverable and committed; the script behind its pattern-presence table was not. Re-measurement reproduced **eight of nine** rows exactly and came out one page apart on `Amtsgericht` (3/12 vs the recorded 2/12) — and the disagreement **cannot be adjudicated**, because there is nothing to compare instruments with. A number whose basis cannot be re-read is M1.42 one level up: the citation and the value computed by different expressions, where here the "expression" is a throwaway script in a transcript. | **`portal audit-impressum-candidates`** — a committed, tested command, following `audit-politeness` and `diff-signals`. Counts-only output by default; **no extracted personal value is printed or stored**. Selection is the guarded one (M1.43 + M1.44) expressed **once** and shared by every mode, so counts and values can never be measured over different pages. `--show-values` prints the PLZ + Ort candidates to the terminal for the operator's accuracy check and **writes nothing** — the check needs the values, and the values may not enter the repo or a transcript. **The standing rule: any future measurement that reaches a proposal ships with its instrument.** | §5.3, §10.4 |

**M1.42's root cause is a column nobody wrote.** `signal.artifact_id` has been a foreign key to `artifact(id)` since migration 001 and no code path ever set it, so the link from a number to the document behind it was carried entirely by an unconstrained TEXT column that the value never passed through. Every one of the five desyncs was a place where a *second* expression had been written to describe what the first one did. Nothing was added to the schema to fix it — the structural link was already there, unused, and the fix is to make the citation impossible to state except by naming an artifact.

**A7 gained a third axis (M1.37), and it is the one that has a consequence.** *Would running again help* decides the routing; **which way the score is wrong while it waits** decides what else must happen. Every instance but one errs too **low** — an award withheld, a ranking delay the queue repairs. `neg.active_content` errs too **high**: a penalty withheld, and an over-scored lead is not mis-ranked, it is called. So a too-high abstention now **blocks outbound contact** until a human resolves it, refused in the schema on `outreach` (migration 008), which is §8's rule about exports applied to the same failure one step further out. Two routings were ratified with it: `blog_cadence_unmeasurable` (008) and `fetch_persistently_failing` (009, closing A7b's open question from M1.34).

**The third crawl ran on 2026-08-16** — 13 domains, 738 requests, §5.2 held. `zecplus.de` and `germanelectronic.de` had their blog indexes fetched for the first time, and `neg.active_content` **fired for the first time in the project's history**: `lampenflut.de` publishes 11 posts in six months, which is not an opportunity, and the score fell 30 → 5. Findings in `docs/third-crawl-findings.md`.

### Amendments from Unit 2 — the provider layer — 2026-08-16

**One ratified ruling, and five facts about the API this spec has been designing against from memory.** Every fact below was verified against current Anthropic documentation before it was written down, and the two that could not be verified are marked as such rather than assumed. The through-line is the same one M1.48 named: *a number whose basis cannot be re-read is not a measurement*, applied to a vendor's behaviour rather than to our own corpus.

| # | Finding | Resolution | Sections |
|---|---|---|---|
| M1.49 | **M1.47 ratified as option (ii) — with the amendment that a quoted span is a weaker guarantee for a boolean than substring verification is for a name.** The two instruments look alike and are not. For `legal_name`, the substring check tests *the thing that gets scored*: the name is the value, and a name absent from the page is the failure mode the check exists to catch. For a boolean, the span tests something adjacent — it proves the model did not fabricate its evidence, and it **cannot catch the model reading the page correctly and inferring wrongly**. `own_brand` is a judgement over a page that may genuinely contain the phrase *"unsere eigene Marke"* in a sentence about somebody else's. **The failure mode for a boolean is bad inference, not invented text, and the backstop does not reach it.** Recording this is the point: (ii) was recommended as *"the only option that makes the backstop actually cover a scored Phase-2 field"*, and it does not cover it to the same depth. A guard believed to be stronger than it is, is how a rule ends up trusted. | **The span is required, and its limit is written beside it.** §5.5b gains `owner_named_evidence` and `own_brand_evidence`; §9 renders a span-verified boolean as *verified-by-span* and never as *verified*, because the operator's judgement of the value is the remaining guard and it has to know that it is. **The firing rule follows from A7's direction-of-error axis** — see §6.1 and A7a items 10 and 11. | §5.5b, §6.1, §9 |
| M1.50 | **Claude Haiku 4.5's parameter surface is not the common one, on three axes, and a provider interface that assumes the common one is wrong at the first call.** Verified 2026-08-16. (a) `output_config.effort` **errors** on Haiku 4.5 and adaptive thinking is unavailable; it takes the older `thinking: {type: "enabled", budget_tokens: N}`, `budget_tokens < max_tokens`, minimum 1024. (b) **200K context, 64K max output** — the only current model below the 128K output ceiling, so batch sizing cannot assume the common maximum. (c) **The prompt-cache minimum is 4096 tokens**, the highest of any current model. A shorter extraction prompt silently does not cache: no error, just `cache_creation_input_tokens: 0`. §5.5b's system prompt is a few hundred tokens. Structured outputs **are** supported, so §5.5b's contract is fine. | All three are encoded as **data next to the price table** (`portal/llm.py`, `ModelLimits`) and asserted at import, in the same shape as `ruleset.assert_declared`: a model whose limits are not declared cannot be used. The interface exposes `thinking` as a per-model concern rather than a universal parameter. **Caching the extraction prompt is not attempted until it is over the minimum, and if it is attempted the observed `cache_creation_input_tokens` is asserted rather than assumed** — an unasserted cache write is a saving that silently is not happening. | §3, §5.5b, §7 |
| M1.51 | **A batch can end normally with some requests never processed, and a reconcile that handles partial-ness only on the error path will mark that batch complete.** Verified 2026-08-16. `expired` is a **per-request result type** alongside `succeeded` / `errored` / `canceled`: a batch that exceeds the 24-hour maximum ends with `processing_status: ended` carrying a mix of succeeded and expired results. So §6-of-the-proposal's partially-processed case **arrives through the success path**, which is not where anyone looks for it. Two more: results are returned in **arbitrary order** — keyed by position they belong to the wrong companies, which on a page read for a legal name is M1.17's failure with a new cause — and `errored` splits into `invalid_request` (never retry; the request is malformed and will be malformed again) and a server error (safe to retry). Results stay retrievable for **29 days**. | §5.6 states all four. Reconciliation is **per result, not per batch**: `llm_batch` moves to `reconciled` only when every request in it has a terminal disposition, and a batch with expired members is re-submittable for exactly those members. The 29-day window bounds any re-read: a batch older than that cannot be recovered and must be re-run as new spend, which is a fact §7's ledger has to be able to say out loud. | §4, §5.6, §7 |
| M1.52 | **§7 control 4 reserves "the estimated cost" of a batch without saying what estimates it, and the price table was going to be constants.** Two corrections, both cheap now and expensive later. (a) The pre-submit input estimate uses **`count_tokens`**, which is model-specific and therefore the only thing that answers the question asked; a character-count heuristic calibrated on one tokenizer is the M1.42 shape one layer out — a second expression describing what the first one does. (b) Prices are **dated data, not constants**: Haiku 4.5 lists at $1/$5 per MTok and the Batch API is 50% off list, so the batch row is **$0.50 / $2.50 per MTok, as-of 2026-06-24**, and the date is part of the row. | `portal/llm.py` carries `PRICES` as `(provider, model, batch) → (input, output, as_of)` with `assert_prices()` at import. **A consequence to state rather than discover: `count_tokens` is a network call, so §7's reservation now depends on the API being reachable before any spend is committed.** It is free and it is not a paid call, but it is a call, and what a `count_tokens` failure should do — abort the submission, or fall back to a stated over-estimate — is recorded in §7 as taken (abort), because an under-estimated reservation is the one failure §7 exists to prevent. | §7 |
| M1.53 | **Where a prepaid balance failure surfaces in the batch lifecycle is unverified, and the two possibilities are different designs.** `billing_error` is real, maps to **403**, shares that code with `permission_error`, and is distinguishable only through the error object's `.type` — not through the status code, which is the thing most code branches on. What is **not** established is whether an exhausted prepaid balance surfaces **on the submit call** (the batch is rejected; the dry-key status is set at submission) or **per request inside the results** (the batch is accepted, some requests fail, and the dry key is discovered at reconcile). It needs a real key to settle and could not be settled here. | **The seam represents both, and the assumption is recorded rather than buried.** `BatchDisposition.BALANCE_EXHAUSTED` exists at batch level *and* `RequestOutcome.BALANCE_EXHAUSTED` at per-request level; the classifier that maps an SDK error to a disposition is one function used by both call sites. **Assumed until verified: submit-time.** That is the safer assumption — it is the one under which the tool stops before committing spend it cannot pay for — and it is the one the code takes when the evidence is ambiguous. §6-of-the-proposal's rule 1 stands: a balance error is **its own status**, never folded into `failed`, because *"the provider failed"* and *"we ran out of money"* need different operator responses. | §4, §5.6, §7 |
| M1.54 | **M6 is not blocked. The capability is confirmed, and confirming it moved the cost the other way.** `docs/implementation-brief.md` blocks M6 on the D4 pricing confirmation, and `docs/v0.3-review-findings.md` adds *"still to verify: that Claude Haiku 4.5 supports the web search tool at all"*. Both are now answered: the per-search rate is $10/1,000 (§5.5c, confirmed 2026-08-15) and **Haiku 4.5 does support web search, via the basic `web_search_20250305` variant.** The newer `web_search_20260209` — which runs code execution to filter results *before* they reach the context window — requires Opus 4.6+ / Sonnet 4.6+. | **The block is lifted; the consequence is priced.** Without dynamic filtering, raw search results land in context in full, so **tokens per search exceed what the $10/1,000-searches figure implies** — the fee is the visible cost and the context is the larger one. §5.5c's 10–20k input tokens per query was estimated on exactly this behaviour and is not invalidated; what changes is that the estimate can no longer be assumed to fall when the tool is upgraded, because the upgrade is not available on this model. Price it into §7 when M6 is scheduled. **M6 is unblocked, not started.** | §5.5c, §7, §10 |

**Two of the six are corrections to reasoning that reached a conclusion the right way by accident**, which is the pattern Unit 0 named and this project keeps producing. M1.49's ruling stands but its stated justification was too strong. And the proposal's fallback recommendation (`docs/multi-provider-llm-proposal.md` §8.3, *abort rather than fail over*) is **right for a reason it did not give**: the Anthropic `fallbacks` parameter was never a cross-provider mechanism at all. It routes only to other **Anthropic** models drawn from the requested model's `allowed_fallback_models`, and it triggers on **policy refusals only** — not rate limits, not overloads, not billing. It would never have rescued a dry key at any layer, so there is no cross-provider capability being given up. It is also rejected outright on the Batches API, which is where §5.5b's spend lives.

### Amendments from the external audit — Unit 2a — 2026-08-16

**An external review ran against `f1f9732` on branch `copilot/research-high-level-review`.** Six findings, all six independently verified as factually true, and the branch confirmed at **zero divergence** from `main` — it is the same commit, and carries no committed audit document, so the findings exist only in the channel that transmitted them. Two are acted on, two recorded, two declined with the reason written here so a future audit does not re-raise them.

**The instruction that arrived with this unit said to file the sitemap finding as M1.49. That number was taken by Unit 2 (M1.49–M1.54), so it is M1.55.** Recorded rather than silently renumbered, because a defect referred to by two numbers is how a finding gets closed twice and fixed never.

| # | Finding | Resolution | Sections |
|---|---|---|---|
| M1.55 | **Two sitemap helpers have no production callers, and one of them disagrees with the code that does run.** `sitemap.is_product_sitemap` and `is_blog_sitemap` are called by nothing in `portal/`; `fetch` and `extract` both use `classify` (M1.27). The question asked before deleting them was not *should they go* but **do their nine surviving assertions still agree with the live path** — a green suite defending an obsolete definition of "product sitemap" is this project's signature failure wearing a disguise. Measured across all 26 URLs those assertions carry: **25 agree, 1 disagrees.** The disagreement is the JTL form `https://example.de/sitemap/product/1`, where the helper says product (via `_PRODUCT_SITEMAP_PATTERNS`) and `classify` says **nothing at all** — because `shard_words` reads only the *last* path segment, and here the word `product` sits in a parent segment. The larger fact behind it: **the platform convention list is unreachable as a positive signal in production.** `_matches_convention` is called only from `_is_ambiguous`, where it *removes* ambiguity; it never asserts product-ness on the live path. Patterns 1–3 (Shopware, Shopify, WooCommerce) are fully subsumed by `shard_kind`'s word reading — the platform filename carries the word — so **pattern 4, the JTL one, is the list's only unique contribution.** | **Nothing deleted; the disagreement is pinned and the question is open.** `tests/test_urls_robots_sitemap.py::TestHelpersAgainstClassify` is the measurement, committed rather than transcribed (M1.48), so the disagreement can neither widen nor heal unnoticed. Two facts bound how much it matters, both measured on the stored corpus: the `/sitemap/product/N` shape appears on **0 of 307** stored sitemap artifacts, and the four JTL shops serve `/export/sitemap_0.xml.gz` instead — an undifferentiated shard naming nothing, which is a **different** problem and the one §10.3 is actually about. So this is §10.4's case: a pattern carried on convention rather than observation, now labelled unobserved rather than ported forward. **Direction of error is the safe one** — `classify` returning `None` withholds a count and §10.3's three-state rule reports *not measurable* rather than a wrong number (M1.4) — which is why this is filed rather than fixed tonight. | §5.2, §10.3, §10.4 |
| M1.56 | **`portal serve` warned about a non-loopback bind in prose and enforced nothing.** `--host` defaulted to `127.0.0.1` and its help text said binding elsewhere "publishes an unauthenticated database". That warning is read when someone reads `--help`, not when they type `--host 0.0.0.0` — and §1 (single operator, no authentication) and §8 (the rows are third-party personal data) were both stated only as prose. Highest risk-per-line in the audit. | **The code says it now.** A non-loopback bind address exits non-zero unless `--allow-public-bind` is passed, and says what it costs and how to proceed. Every loopback spelling still works with no flag — `127.0.0.1`, the whole `127/8` block, `::1`, `[::1]`, `localhost`. **Wildcards are classified public, not unspecified**: `0.0.0.0`, `::` and the empty string bind every interface the machine has, so they are the *most* exposed address rather than an unknown one, and reading "unspecified" as "probably fine" is how this mistake is usually made. **An unresolvable hostname is refused rather than resolved** — a name lookup would make the guard depend on what the machine's resolver currently believes. Landed **before M5**, deliberately: M5 is when the database starts holding LLM-extracted personal data rather than page bytes. | §1, §8, §9 |
| M1.57 | **"Dormant Phase-2 surface" has now been re-discovered three times under three names** — as `PHASE2_MAX_POINTS` inflation (M1.21), as B7's unfirable rule, and now as an external auditor reading `_own_brand`'s unconditional `declines()` and the `llm_batch` table as dead code. Each time it was correct-as-observed and wrong-as-diagnosed: the surfaces are deliberate, and the deliberateness lives in a code comment rather than anywhere a reader of the spec would find it. | **§10.6 — a status marker per schema object and per rule**, so the ambiguity has a documented answer instead of being re-derived by whoever looks next. The rule half is **checked against `ruleset.RULES` by a test** rather than maintained by hand: a status list that can drift from the thing it describes is M1.42's shape, and this project does not get to write one of those in a section about avoiding them. | §4, §6, §10.6 |
| M1.58 | **Two audit recommendations are declined, and the reasons are recorded here so they are not re-raised.** (a) *Defer the `anthropic` and `pydantic` imports / treat them as unused.* Accurate against an earlier tree and **wrong from `f1f9732` onward**: Unit 2 imports `anthropic` in `portal/llm_anthropic.py` and §5.5b's extraction models are `pydantic`. Both are live. (b) *Periodic comment and documentation pruning.* **Rejected.** The historical narrative in `fetch.py`, `extract.py` and the migrations **is** the amendment-table discipline that has produced 58 numbered defects each carrying its measurement; an audit optimising for comment density would delete the audit trail, and the trail is the thing that catches the next M1.43. | The *other* half of (b) is sound and **adopted as a convention**: comments are marked **normative** (what the code must do, and why) or **historical** (what was measured, when, and what it cost). Pruning is refused; the split is what makes the volume navigable. | §3, and the conventions in `docs/implementation-brief.md` |

| M1.59 | **`robots.txt` failed open on every response that was not a 200, so a 503, a 429 and a connection timeout each granted the same unrestricted permission a 404 does.** `Response.ok` is `status == 200 and body is not None`, and both consumption sites read `robots.parse(resp.text() if resp.ok else None)` — where `parse(None)` returns a policy that allows everything. The rule §5.2 states for one case (*"a 404 on robots.txt is the common case for small shops"*) was implemented as though it covered all of them, which is **M1.12's shape one level up**: a correct sentence about a narrow case, generalised by the code that implemented it. RFC 9309 §2.3.1 separates three cases the code merged into two. **The crawl-delay is the worse half.** `RobotsPolicy.crawl_delay()` returns None whenever there is no parser, so a 503 or a 429 dropped the host's requested pacing *along with* its rules and the tool fell back to a default it chose for itself — crawling a server that was failing, or explicitly asking us to back off, at our own rate. §5.2 already refuses a domain outright when `crawl_delay` is too high (`crawl_delay_too_high`), so the project treated that number as load-bearing in one direction and discarded it in silence in the other. **The trigger condition is in the corpus.** The request log for run 29 records `snocks.com` returning **429 to six requests** — `/imprint`, `/legal`, `/rechtliches`, `/blogs/lifestyle` and two product pages — and `ekomia.de` returning **500 and two more 429s** (M1.45's storm, seen from the log side). Robots requests over the three logged runs are 46 × 200 and 15 × 301 with no 5xx and no 429, so the branch never fired; but a host rate-limiting our pages is exactly the host whose `robots.txt` 429s next, and under H1 that response granted permission to crawl everything at our own pacing on a server that had just said stop eight times. Only the ordering spared it. | **The tri-state, in one classifier that is a pure function of status and body** (`robots.for_response`), so it cannot drift from the error strings `net.get` happens to write (M1.42). **2xx** → the rules. **4xx** → unrestricted; that branch and the reasoning in its docstring are *kept*, because they are right about the case they are about. **5xx, 429, transport failure, redirect loop** → `unavailable`: **disallow all for this run**, recorded as a `robots_unavailable` note carrying the status or the transport error, so an operator can see why a domain produced nothing. A **refused redirect** stays unrestricted — it is M1.18's moved-domain shape (`doonails.de`, `germanelectronic.de`, 2 of 13), where the host actually fetched has its own `robots.txt` read before its first request, and RFC 9309 §2.3.1.1 says so too. **Not an exclusion**: `company.excluded` is a standing verdict about a lead and a 503 is a fact about one afternoon, so nothing is written to `company`. Persistence routes to `fetch_persistently_failing` at A7b's N — **3 runs on 3 distinct days** (M1.34, migration 009) — and never on a single occurrence. **The crawl-delay loss is now structurally impossible rather than fixed**: an `unavailable` policy allows nothing, so no request survives for a delay to pace, and `TestUnavailablePolicy` pins the pair together. | §5.2 |
| M1.60 | **A `robots.txt` that was retrieved and would not parse also fell open** — `parse` caught the exception and returned the same unrestricted policy an absent file returns. Same class as M1.59, lower exposure: stdlib `RobotFileParser.parse` **skips** lines it cannot read rather than raising, which is measured rather than assumed (`test_malformed_robots_does_not_abort` feeds it `\x00\x01 not robots at all` and it parses to no rules). | **`unavailable`, not unrestricted — the unreachable side of the tri-state**, and the reasoning is recorded because either side was arguable. It is **not** the 4xx case: the server has a file and served it, so "no rules stated" is contradicted by the bytes in hand, and the honest reading is *rules stated, not understood*. The errors are asymmetric in M1.4's sense — falling open crawls pages the shop may have forbidden, falling closed costs one company one run and says so in a note — and because the branch is close to unreachable, the safe side is very nearly free. | §5.2 |
| M1.61 | **M1.44's method selected the wrong `robots.txt` on one company, and the count survived anyway.** M1.44 tested each stored URL against *"the newest stored robots.txt for that company"*. Measured over the corpus: on `zecplus.de` the newest robots artifact is **`blog.zecplus.de`'s** (id 458, `Disallow:` — fully permissive), so all **31** of that shop's stored bodies were tested against a permissive file from a **different origin**, while `www.zecplus.de`'s own 3,624-byte file (id 1) went unread. Per-company-newest is the wrong key: §5.2 says twice that robots is keyed to the **origin**. Behind it, a second fact with wider reach — **two origins serving byte-identical `robots.txt` collapse into one `artifact` row.** `uq_artifact_identity` is `(company_id, kind, content_hash)`, so the row names whichever origin was recorded first: the request log shows `www.smoke2u.de/robots.txt` and `www.propellerdiscount.de/robots.txt` were each fetched **200, three times**, and **no artifact row names either** — 26 stored bodies sit on authorities the artifact table appears to have no policy for, when in fact it read one and filed it under a sibling. | **Measured, not repaired — the repair is M5's and this changes its shape.** M1.44's *conclusion* is confirmed four ways: **0 of 13** companies' newest robots artifact is anything but a 200-with-body (so H1's undercount hazard did not materialise on this corpus), and the disallow count is **exactly two** — artifacts 171 and 186 — under per-company-newest, under newest-200-with-body, under strict authority matching (which finds 171 and calls 186 *undecidable*, the apex file having been applied to a `www.` URL), and under authority-matching with a `www.`/apex sibling fallback (2, 0 undecidable). **M1.44's repair (a) must not be implemented as written**: "the newest stored `robots.txt`" reproduces the zecplus vacuity, and an origin-keyed lookup must treat a collapsed row as *the policy of every origin that served those bytes* — or, where that cannot be established, report **not verifiable** rather than *allowed*. Measurement in `docs/unit4-robots-tristate-findings.md`. | §4, §5.2, §5.5b |
| M1.62 | **`audit-politeness` was blind to M1.59 by construction, and would have passed over it.** The external review said the audit "won't catch it because the requests were correctly spaced". The sharper statement is the one that matters: if the policy was unrestricted, the audit measures the run against the **default** interval — the interval an unread policy leaves in place — and reports HELD. The instrument built to verify politeness could not see the one failure that makes politeness wrong, because that failure changes **which pages may be fetched**, not how fast they arrive. | **The audit reads the artifact table too, and fails on it**, following M1.19's instinct that a rule should be enforceable rather than stated. Two classes, reported and failing **separately**, because collapsing them would make the check cry wolf and a check nobody trusts is worse than none: **unavailable** (5xx, 429, no status) **fails**, and fails as a *breach* when bodies are stored for that company — pages fetched under a policy never read; **no file** (4xx, refused redirect) is **reported and does not fail**, per RFC 9309 §2.3.1.2 and because the only instance of the second in this corpus is M1.18's moved domain. The database is now **required**: an audit run without one prints `NOT CHECKED` rather than looking green. It reads stored *state*, not a run — `artifact` carries no `run_id` and failure rows update in place — which is a narrower claim than the one asked for, and is the one the table can support. On the corpus at `5f56560`: **0 unread, 2 stating no file, §5.2 HELD.** | §5.2 |

| M1.63 | **A lead moved a band overnight on the clock alone, and it was found by re-running `score` after a change that touches neither scoring nor the corpus.** Unit 4's mandated re-run moved `navucko.com` **17 (D) → 42 (C)**. Nothing was crawled, no signal changed, and runs computed with the working tree and with `5f56560`'s ruleset are byte-identical — so the movement is not the code's. It is `neg.active_content` (**−25**, §6.3's largest) ceasing to fire between 2026-08-16 and 2026-08-17: the shop's dated posts are `2025-12-01, 2026-01-06, 2026-02-17, 2026-04-11, 2026-06-07, 2026-06-20`, and `(2026-08-17 − 2026-02-17).days == 181`. One post crossed `SIX_MONTHS = 180` overnight, `recent` fell **4 → 3**, and the rule's `>= 4` threshold is on the other side of that boundary. **Two hard thresholds compounding, with a 25-point step between them and a band boundary underneath.** The rule is doing exactly what §6.3 says; what had never been observed is how little has to change for the largest number in the ruleset to switch. | **Recorded, not fixed. No threshold is touched** — §6.5's bands and §6.3's weights are the spec's, and §10.3 already forbids calibrating them on this corpus. Two things follow instead. **(a)** §5.4's "score is a free recompute" is true of *cost* and not of *result*: a recompute is a function of `(signals, today)`, and the second argument moves on its own. Any claim of the form "the score did not change" must therefore name the date it was computed on, and a diff between two score runs on different days is not evidence about the code. **(b)** Direction of error: this instance moves the score **up** (a penalty withdrawn), which is the direction §6.4 treats as expensive — an over-scored lead is not mis-ranked, it is called. It raised no flag, because with `blog_post_count = 6` and 6 dated posts the enumeration is complete and the rule *declines* rather than abstaining, so A7's guard is not engaged and nothing blocks contact. Whether a −25 that can be withdrawn by one day's clock advance should decay rather than step is a §6.3 question and is **open**, filed in §10.5. | §5.4, §6.3, §6.5, §10.3 |

| M1.64 | **Three tests depended on a resolver behaviour RFC 6761 makes optional, and passed on every machine the project was developed on.** `TestApexToWwwWithinTheSeededSite` (×2) and `TestRedirectsAreRateLimited::test_apex_and_www_hops_to_one_server_share_one_budget` built their URLs from `localhost` and `www.localhost`. §6.3 of RFC 6761 requires `localhost` to resolve and leaves **subdomains** to the implementation: macOS and systemd-resolved answer, most container images do not. Reproduced rather than assumed — a `socket.getaddrinfo` shim that fails `*.localhost` was applied to a worktree at `6a5e266`, and exactly those three failed with `AssertionError: None != 200`. **A fourth test was passing vacuously in that condition**: `test_an_apex_to_www_redirect_is_not_a_move` asserts `site_domain IS NULL` and no `domain_moved`, and a redirect whose target never resolves satisfies both — an absence assertion met by the request failing. | **The suite resolves its own hostnames, and the names are chosen so the shim cannot be dead weight.** `tests/fixture_server.resolves_to_loopback` maps names to 127.0.0.1 at `socket.getaddrinfo` — **the seam was verified, not assumed**: `net.Fetcher` is httpx over httpcore's sync backend, whose `connect_tcp` calls `socket.create_connection`, which looks `getaddrinfo` up as a module global; traced under httpx 0.28.1 / httpcore 1.0.9 by wrapping both and printing the stack. Scoped by context manager with LIFO restore, never a bare monkeypatch. **The names are `shop.invalid` / `www.shop.invalid`, not `.localhost`** — RFC 2606 §2 guarantees `.invalid` is never delegated, so the shim is load-bearing on *every* machine and deleting it fails the suite on the maintainer's laptop as well as in CI. Built on `.localhost` the tests would go back to passing by accident wherever the resolver was generous, which is how this survived to be found by an external reviewer. **A `@skipUnless` was refused**: a skipped politeness test is worse than a failing one, because it goes quiet on exactly the machines where it would run most. **A `Host:`-header fixture was refused too, and the reason was checked in the code rather than taken on the review's word**: `net.Fetcher.get` calls `limiter.wait(host_of(current))` where `current` is the URL, and `host_of` is `authority_of(url).removeprefix("www.")` — derived from the URL and never from a header, so both cases would key on `127.0.0.1:PORT` and the test could no longer tell apex from www. It would pass while measuring nothing, which is the defect it exists to catch. | §5.2, and `tests/fixture_server.py` |
| M1.65 | **8,255 lines of tests, a ruff config, and nothing that ran either.** Every finding in the 17 Aug external review was one the reviewer had to know to run — including M1.64, which had been failing in container environments for the life of the project. This is the project's own standard not applied to itself: M1.19 made `audit-politeness` exit non-zero precisely so politeness is *enforced* rather than stated, and an unenforced suite is an assertion by the same argument. | **`.github/workflows/ci.yml`**, three jobs, `permissions: contents: read`. **lint** — `ruff check .` and `ruff format --check .`, both, since the project treats them as separate gates. **test** — pytest on the `pyproject.toml` floor (3.11) and the development version (3.12); 3.13 is deliberately absent until an interpreter above 3.12 has actually run this suite, because an unverified version in a first CI is how a green light gets ignored the week it goes red. The suite runs under `env -u ANTHROPIC_API_KEY` — *removed*, not blanked — and a preceding step **fails the build** if either `ANTHROPIC_API_KEY` or `PORTAL_LIVE_SMOKE` is present, so no job can reach a paid endpoint or the one live host, whoever adds a secret later. That is what turns Unit 2's injected-client seam from a claim into a measurement, and it closes **before** M5 rather than after. **The migration runner from empty to head is not a fourth job**: `test_migrate.py::TestApplyPending` already applies every migration to a fresh database, checks `user_version` against the highest on disk, and checks that a second run is a no-op — a separate job would re-run that path with weaker assertions. **politeness** — `data/` is gitignored and absent, so `tests/fixture_corpus` builds one from a loopback fixture server at the real 1 req/s floor (anything faster fails its own spacing audit), and the audit runs **twice**: green on a healthy corpus, and required to **exit non-zero** on the same corpus with `robots.txt` returning 503. A gate that can only go green is a green light wired to nothing, and M1.62 is exactly the case where spacing still measures fine. | §5.2, §7, §3 |
| M1.66 | **`score` records when a row was written, not the date it was scored against — and M1.63 made that the difference between reproducible and not.** M1.63 established that `evaluate` is a function of `(signals, today)` and that a band therefore has no meaning without its evaluation date. The `score` table's only temporal column is `computed_at TEXT NOT NULL`, written from `utc_now()` inside `_persist`, while `today` comes from `ScoreStage.today` — **two expressions for what is assumed to be one fact**, which is M1.42's shape. They agree in production only because nothing injects `today` outside tests, and nothing enforces that. Measured, and the gap is not subtle: a stage constructed with `today=date(2020, 3, 7)` writes a row reading `computed_at = 2026-08-17T16:32:15Z`, and **2020-03-07 is recorded nowhere at all**. A run straddling midnight UTC produces the same divergence with no injection involved. | **Not built here — it is an M5 prerequisite and it precedes any spend against a band.** The fix is a column recording the date `evaluate` was actually given, written from the same expression that feeds the rules rather than from a second clock read, so the two cannot name different days. Registered in §10.4b alongside the other two M5 preconditions. Filed rather than fixed because a schema change belongs with the milestone that has a migration in it, and because Unit 5's scope is M4 and M2. | §4, §5.4, §6.5 |

**One section of the audit was not transmitted and is not in the repository.** It is headed *"LLM-generated/hallucination signals"* and it is **missing, not empty** — the branch is byte-identical to `main` and carries no audit artifact, and there are no pull requests or issues on the remote to recover it from. On this project that heading most plausibly lands on §10.4: patterns that look plausible and were never observed. **Unit 2a is therefore complete but the audit is not closed**, and the conclusions above that would need revisiting if that section turns out to be substantive are named in the unit's report rather than assumed benign.

### 10.6 Status of each surface — live, dormant-by-design, or ahead of its writer

**Written because the same ambiguity has now been re-discovered three times under three names (M1.57).** A schema object with no writer and a rule that always declines both read as dead code, and both are deliberate here — the spec commits to the whole pipeline and the milestones land it in order. What was missing is a place to look that up.

**Schema objects** (§4), by whether anything writes them today:

| Object | Status | Decided by |
|---|---|---|
| `company`, `artifact`, `signal`, `run`, `sitemap`, `review_flag`, `score`, `score_component`, `company_profile` | **live** | `fetch`, `extract`, `score` |
| `contact_blocking_reason`, `company.contact_blocked` | **live** | migration 008, `score`, `serve` |
| `llm_batch` | **ahead of its writer** — M5 | §5.5b, §5.6 |
| `contact` | **ahead of its writer** — M5 writes it from verified Impressum names (§5.5b) | §5.5b |
| `outreach` | **ahead of its writer** — M7 | §9 |
| `llm_batch.status = 'balance_exhausted'` | **not in the schema yet** — migration 010 ships with M5's writer, per M1.45(c) | §7 control 11 |
| review reasons `own_brand_undetermined`, `owner_named_undetermined` | **not in the schema yet** — same rule, same reason | M1.49, A7a items 10–11 |

**Rules** (§6). *Dormant* here means the rule is correct, declared, and cannot fire until Phase 2 supplies its input — which is exactly what `Rule.phase2_reachable` records, so this column is **derived from `ruleset.RULES` and checked by a test** rather than maintained beside it:

| Rule | Status |
|---|---|
| `qual.own_brand` (+10) | **dormant until M5.** The only rule with no Phase-1 input at all — `reads=()`, and `assert_declared` carries a named exemption for exactly that. Its `_own_brand` predicate declines unconditionally by design. |
| `qual.owner_operated` (+15) | **partly dormant.** Disjuncts 1 and 2 fire in Phase 1 from `legal_form` and `gf_count`; disjunct 3 (`site.owner_named`) is Phase 2. |
| `opp.ai_invisible` (+15) | **dormant until M6.** §6.2's predicate needs `ai.queries_checked >= 2`. |
| `opp.slow_site` (+10) | **dormant until M5.** PageSpeed is §5.5a. |
| `neg.has_agency` (−20) | **dormant until M5** for the LLM-read `agency_credit`; the deterministic footer credit already fires in Phase 1. |
| every other rule | **live in Phase 1.** |

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
    needs_review    INTEGER NOT NULL DEFAULT 0, -- derived: 1 iff an unresolved review_flag exists.
                                                -- Maintained by trigger, never written directly.
    contact_blocked INTEGER NOT NULL DEFAULT 0 -- derived: 1 iff an unresolved review_flag names a
                                               -- reason in contact_blocking_reason (migration 008).
                                               -- A cache for the UI; the refusal itself is a trigger
                                               -- on `outreach` reading the flags live. See §8, A7.
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
-- M1.36: "latest per key" is scoped to the latest run of that key's OWN
-- STAGE, per company — see migration 006, which supersedes the definition
-- below. A later run of a stage is authoritative for everything that stage
-- owns, INCLUDING the keys it deliberately did not write. Without that,
-- a signal a stage stops writing is never retracted, and every A7 guard —
-- all of which work by declining to write — is undone by the read model.
--
-- M1.39: and the run must have FINISHED (migration 007). Authority by
-- omission is only sound from a run that reached the end: a crash mints a
-- new run_id (§5, D6), so a partial pass would otherwise retract every key
-- it never got to. A run still in flight, one that crashed, and one that
-- aborted are all the same thing here — an account nobody has claimed is
-- complete — and each is ignored WHOLESALE, keys it did write included.
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

-- ─────────────────────────────────────────────────────────────
-- Migration 008. WHICH review reasons refuse outbound contact, as data:
-- an abstention that leaves a score too HIGH is an outward-facing error,
-- and §8's rule for exports applies to it. Classifying the next one is an
-- INSERT here, not a branch repeated across four triggers.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE contact_blocking_reason (
    reason    TEXT PRIMARY KEY,
    rationale TEXT NOT NULL                    -- why this reason leaves the score too high
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

What is not guaranteed: a crashed-then-restarted run gets a **new `run_id`**, so `uq_signal_identity` (which includes `run_id`) does not deduplicate across the restart. Deterministic signals will be re-observed under the new run. This is harmless — `signal` is append-only by design and `company_profile` resolves to the latest observation of each key **for that key's own stage** (M1.36, migration 006) — but it means the database is not byte-identical to a clean run.

**"Latest observation" had to be qualified, and the qualification is load-bearing (M1.36).** Until M1.32 every stage wrote the same keys on every run, so "the latest observation" and "what the pipeline currently believes" were the same thing. They stopped being the same thing the moment abstention became the mechanism: A5.5, A6.1 and M1.34 all work by **declining to write**, and a view keyed on the all-time latest observation never sees a retraction. Two live instances were found on the stored corpus while M3's audit was running — `agency.footer_credit` still serving `"Powered by JTL-Shop"` from a run predating the platform-credit exclusion, and `content.blog_exists = 0` still serving the value M1.34 exists to stop writing. Both would have scored: −20 on three JTL shops for their choice of shop system, and +25 on `zecplus.de`. **Every abstention guard in this spec depends on the fix**, so it belongs here rather than in a migration note.

To resume under the original run instead, use `python -m portal <stage> --resume <run_id>`. This reuses the run row, so the unique index applies and the cost ledger stays in one place. Prefer `--resume` after a crash; use a fresh run for a genuine re-scrape.

### A7 — Abstention. The rule this spec has now made four times

> **A7.** Where a measurement exists but cannot support the rule that reads it, the rule fires **in neither direction**, and a human is told. Not a zero, not a default, not the rule's absence recorded as its negation.
>
> Three parts, and all three are required:
>
> 1. **The rule does not fire.** Not "fires as false" — a rule that awards points for an absence must not read *unmeasured* as *absent*.
> 2. **The reason is written**, as a signal or as `value_text` on the measurement it qualifies, so the abstention is visible per company rather than inferred from a gap.
> 3. **A `review_flag` routes it to a person**, because a company the pipeline has gone quiet about is the company least likely to be looked at, and the quiet is not visible in a score.

This is written down because it is **the single most repeated design decision in this specification**, and each instance so far was re-derived from scratch.

**The table is split in two, because abstentions are not all the same kind of thing (M1.34).** Naming A7 conflated them, and the conflation has a cost: one class is permanent until the instrument changes, the other usually resolves itself on the next run, and giving them one resolution policy either fills the review queue with things that fix themselves or leaves permanent gaps waiting for a retry that will never help.

**And the tables carry a third axis: the direction of the error (M1.37).** *Whether* running again helps decides the routing. *Which way the score is wrong while it waits* decides what else has to happen, and until M3 nothing recorded it.

Every instance but one guards a rule that **awards** points for an absence. The abstention withholds an award, the lead reads too **low**, it ranks below where it belongs, and the queue reaches it eventually. The cost is a delay in ranking, and the queue is the whole remedy.

`neg.active_content` subtracts 25 for a **presence**. Abstaining withholds a *penalty*, so the lead reads too **high** — and a lead that reads too high is not mis-ranked, it is **called**. `doonails.de` publishes actively, cannot be measured to be doing so, and sits in the list as a strong prospect on a score that is knowingly up to 25 points generous. The failure is not a worse ordering; it is a phone call to a company already doing the thing the letter offers to sell them.

> **A too-high abstention blocks outbound contact for that company until a human resolves it.** Not a warning beside the row: the `outreach` insert is refused, in the schema (migration 008).

This is §8's rule applied to a second outward-facing failure. §8 already holds that an export whose comparative claim cannot state its basis must **fail rather than degrade**, because the failure leaves the building and lands on a third party. A contact made on a score the pipeline cannot support is the same shape and gets the same treatment. Too-low abstentions block nothing.

Which reasons block is **data** — the `contact_blocking_reason` table — so that classifying the next one is an INSERT and not a branch repeated across four triggers, and so that the axis is recorded per reason rather than re-derived per abstention. `company.contact_blocked` is a cache for the UI, maintained exactly as `needs_review` is; the refusal itself reads the flags live.

**A7a — measurement limits.** The instrument cannot reach this shop. Nothing about running again changes that, so the routing is immediate.

| # | The measurement | Why it cannot carry the rule | Rule that abstains | Error direction | Routed by |
|---|---|---|---|---|---|
| 2 | `content.blog_last_post`, unparseable (§6.2) | An undated index is an unknown, not a stale blog | the blog ladder below `no_blog` | too low | `blog_date_unparseable` |
| 3 | `catalog.product_url_count`, no tier identifying a product (§10.3) | A `0` would claim a real shop has no catalogue | `qual.product_depth`, `qual.own_domain_shop`, `opp.no_product_schema` | too low | `catalog_not_measurable` |
| 4 | `content.blog_last_post` not bounded from above by the index (M1.32, M1.40) | A lower bound with no maximum behind it cannot show staleness | `opp.blog_stale` (+20), `opp.blog_slowing` (+10) | too low | `blog_date_unbounded` |
| 5 | `content.blog_exists = 0` from a search that ran only one of the two instruments (M1.14) | A vocabulary that did not run is not an absence | **the whole ladder**, `opp.no_blog` (+25) included | too low | `blog_undetectable` |
| 7 | `content.blog_post_dates` covering fewer posts than the index lists, or an index that cannot be counted (M1.37) | A partial enumeration can establish activity and never its absence | `neg.active_content` (−25) | **too high** | `blog_cadence_unmeasurable` — **and outbound contact is blocked** (migration 008) |
| 8 | `i18n.hreflang_count` absent because no homepage was read (M1.38) | An unread page is not a monolingual one | `opp.de_only` (+5) | too low | nothing — the rule simply does not fire, and no points are at stake in the expensive direction |
| 10 | `brand.own_brand`, whose `own_brand_evidence` span was not found in the cleaned page text (M1.47, M1.49) | A judgement whose quoted evidence is not on the page is not evidence of anything | `qual.own_brand` (+10) | too low **(the abstention)** — see below | `own_brand_undetermined` (M5) |
| 11 | `site.owner_named`, whose `owner_named_evidence` span was not found in the cleaned page text (M1.47, M1.49) | Same — and a name not on the page is the failure §5.5b's whole backstop exists for | `qual.owner_operated` **disjunct 3 only** (+15); the rule still fires on disjunct 1 or 2 | too low **(the abstention)** — see below | `owner_named_undetermined` (M5) |

**Items 10 and 11 are the first instances where the axis was applied to the *input* rather than to the abstention, and the two directions point opposite ways.** The unverified value errs **too high** — an award taken that was not earned — which is why verification gates firing at all (§6.1). The abstention that gate produces errs **too low** — an award withheld — which is why neither blocks outbound contact. Both are true at once and they are about different things: the first is a property of trusting the model, the second a property of declining to. Conflating them would block contact on 25 points of missing evidence, which is the queue-noise failure §6.4 warns about rather than the phone-call failure item 7 prevents.

**A7b — transient failures.** The instrument applies and the request missed. A 404, a timeout, a momentarily disallowed path. These **retry on the next run** and are flagged only once they persist; the abstention itself is immediate and identical to A7a, because a rule must not fire on a page we do not have whatever the reason.

| # | The measurement | What failed | Rule that abstains | Error direction | Routed by |
|---|---|---|---|---|---|
| 1 | `schema.product_present`, absent a product page fetched with HTTP 200 (A5.5, A5.6) | A product was identified and its page did not return 200 | `opp.no_product_schema` (+10) | too low | `fetch_persistently_failing`, after **N** runs |
| 6 | `content.blog_exists`, where a blog was located and its index did not return 200 (M1.14) | The blog was found; the index fetch missed | **the whole ladder** — `content.blog_exists` is not written at all | too low | `fetch_persistently_failing`, after **N** runs |
| 9 | `schema.article_present`, absent an article fetched with HTTP 200 (A6.1, M3 audit) | An article was identified and its page did not return 200 | `opp.no_article_schema` (+8) | too low | `fetch_persistently_failing`, after **N** runs |

All three are too-low, so none blocks contact. That is not a coincidence and it is worth naming: a transient is a *page we did not get*, and a page we did not get can only withhold an award. The one abstention that errs upward is the one where the page loaded fine and said too little.

**N = 3 consecutive runs, on 3 distinct days.** The reasoning, because the number is otherwise arbitrary:

- **One run is already two attempts.** A5.1 discards a dead Tier 0 sample and re-selects within the same run, and §5.2 caps that at two product requests per company per run. A single run therefore already buys what `unreachable`'s "2 attempts" buys for a hard exclusion — so N counts *runs*, and the effective attempt count is up to six.
- **The asymmetry points late, not early.** A premature flag costs queue credibility, and §6.4 has already written down what that costs: *a queue that refills itself stops being read*. A late flag costs one run's delay on a company whose points are merely unawarded, which is the safe direction A5.5 already chose. When the two errors are unequal, take the safe side — M1.4's rule.
- **"On distinct days" is load-bearing, not decoration.** §5's idempotency contract gives a crashed-then-restarted run a **new `run_id`**. Without the day requirement a single crash-restart loop inside one afternoon manufactures three runs and therefore a flag, and the flag would be about our own crash rather than about the shop.

**Which reason a persistent transient routes to was settled with M3: `fetch_persistently_failing`** (§6.4, migration 009). By the third run the retry policy is exhausted and what is left is a measurement limit wearing a transient's clothes, so it routes like one. **One reason for all three instances**, because unlike the blog trio they send a person to answer a single question — *does this URL load for you?* — and `raised_note` carries which URL and since when. `possible_marketplace_only` was never a candidate (§5.2 assigns it to *zero candidates*, not to a candidate that will not load) and neither was `catalog_not_measurable` (*nothing was measured*).

**The point of naming A7 is that the next instance is applied rather than argued.** The recurring shape is a rule that awards points for an *absence* — no schema, no posts, no catalogue, no recent post, no blog — reading a failure to measure as the absence it rewards. Every such rule is a candidate, and the test is one question: *if this signal is missing or weak, does the rule award points?* If yes, A7 applies. Then the second question decides the routing: *would running again plausibly fix it?*

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
- The blog path is checked per-URL once the sitemap reveals it. **A disallowed blog is a missing signal, not grounds for exclusion** — treating it as a refusal would discard exactly the leads whose weak blogs this tool exists to find. Since M1.14 the blog may be on a host of its own, and then it is that host's `robots.txt` that governs it; a refusal there is still a missing signal and never an exclusion of the shop.

**Three answers a `robots.txt` fetch can give, not two (M1.59).** RFC 9309 §2.3.1 separates cases that "did it return 200?" merges, and the merge falls open:

- **2xx with a body** — these are the rules. An empty body states no rules, which is a real answer.
- **4xx** — the host has answered that there is no file. **Unrestricted.** This is the common case for a small shop and the sentence above about `/checkout/` is about this case.
- **5xx, 429, a transport failure, or a redirect chain that never resolved** — we did **not read** this host's rules. **Nothing may be fetched from it for this run.** A 429 is grouped here deliberately: it is the server asking us to stop, and reading it as "no rules stated" answers a request to back off by crawling the whole site.
- **A body we retrieved and could not parse** belongs on the unreachable side (M1.60), not the 4xx side: the server has a file and served it.
- **A redirect we declined to follow** stays *unrestricted*, per §2.3.1.1 — the only shape in this corpus is M1.18's moved domain, where the host actually fetched has its own `robots.txt` read before its first request.

The refusal is **recorded, never silent**: a `robots_unavailable` note carries the status or the transport error, so a domain that produced nothing says why. It is **not an exclusion** — `company.excluded` is a standing verdict about a lead and a 5xx is a fact about one afternoon — and it raises no flag on a single occurrence. Once it persists it routes to `fetch_persistently_failing` at A7b's N, **3 runs on 3 distinct days** (M1.34, migration 009), because it sends a person to that reason's one question: *does this URL load for you?*

**The `Crawl-delay` is lost with the policy, and that is now structurally impossible (M1.59a).** One parser answers both *may we fetch this?* and *how fast?*, so before the tri-state a 503 or a 429 discarded the host's requested pacing along with its rules and the tool fell back to a default it chose itself — on a server that was failing or explicitly asking for back-off. Note the inconsistency that made this worth stating rather than merely fixing: this section already refuses a domain outright when the delay is *too high*, so the value was load-bearing in one direction and discarded in silence in the other. Under the tri-state the question cannot arise — an unavailable policy allows nothing, so no request survives for a delay to pace — and it is written down here so a future reader does not have to re-derive it.

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

Fetch order: `robots.txt` → homepage → `sitemap.xml` (and any nested sitemaps) → Impressum → blog index if a blog is **located** (§5.3: a path, or an anchor pointing anywhere) → one sample blog article under it (A6) → one sample product page if a product path is found.

**A first request to an unvisited authority reads that authority's `robots.txt` first (M1.14).** Until anchor text could hand this stage a URL on a host nothing had touched, every request was either on the seeded authority or arrived through a redirect hop — and the hop rule above already loads the file. A first request to a new authority used to fall back to the seeded policy, which is `zecplus.de`'s robots.txt applied to `blog.zecplus.de`: exactly the collapse this section forbids twice. The lookup is memoised per authority, so a blog host costs one extra `robots.txt` and no more, and its `Crawl-delay` and cap apply as they would to any other host. A blog host that disallows us is a **missing signal, not an exclusion** — the same rule the blog path already has.

**A blog-index request that lands back on the shop's own front page is not a blog index.** M1.17's rule, in the place M1.14 made it reachable: taking an href wherever it points makes *where we landed* a real question, and the answer is host-aware. A root path on the blog's own host is the thing we went looking for; a root path on the shop's host is the homepage, and stored as an index it would write `content.blog_exists = 1` off the homepage and then let A6 sample the catalogue for articles. Recorded as a failure, exactly as the Impressum case is.

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
- Reject anything under the blog path detected for `content.blog_exists`. **A blog on a host of its own contributes no path here** (M1.14): its only path is `/`, and read as a prefix that would reject the entire catalogue as blog content. Where an anchor-detected blog *is* on our own host, the prefix is its **full** path rather than its first segment, so a `/service/ratgeber` blog does not take all of `/service` with it.

*Zero candidates.* No product page is fetched and **no `schema.product_present` signal is written — not `0`.** A `0` there means "checked, absent", which fires `opp.no_product_schema` for +10 against a site whose product pages were never retrieved. That is the same error as the blog ladder's `NULL` branch (§6.2), and it is refused for the same reason. The same applies when a sample is selected but its fetch fails — 404, timeout, robots-disallowed.

No new review reason is needed: zero product candidates on a detected shop platform already satisfies `possible_marketplace_only` (§6.4).

*Auditability.* The chosen URL is recorded as `catalog.product_sample_url` (text, unscored), with `evidence_url` set to the sitemap or homepage it was read from. Without it, `schema.product_present` points at a product page with no record of *why that page*, and the selection rule is unauditable from the database.

**Blog article sampling (A6, M1.29).** `content.blog_last_post` and `schema.article_present` are read from **one sampled article**, not from the blog index — the index carries neither on the platform that is most of the corpus. Same shape as A5, same guarantee (*same inputs → same choice*), one additional request per company that has a blog.

*The anchor is the **index path**, not the blog path.* On Shopify the hierarchy is `/blogs/<blog-handle>/<article-handle>`, so a URL one level under `/blogs` is *another blog index*. "The shallowest URL under the blog path" — the obvious phrasing — selects `/blogs/karriere` on `bio-fleischer-laden.de` and hands a listing page to an `Article` parser, which is M1.16's error in a new place. Anchoring on the index M1.15 actually fetched makes the level unambiguous.

*Candidates* are URLs without a query string, on the **index's own host**, strictly below the index's path. *Tiers:* (1) a blog sitemap shard (M1.24 — membership is the evidence, no path shape required); (2) sitemap URLs under the index path; (3) links on the index page itself. *Ordering:* shallowest first, code-point minimum breaking ties.

**The anchor is the index's whole URL, and its host replaces the `same_site` test (M1.14).** Two changes, one reason. A blog can be a *host*: `blog.zecplus.de` serves its index at `/`, where a path anchor is empty and "strictly below it" degenerates either to nothing or to the shop's entire catalogue. And a blog the shop links at a host of its own is still the shop's blog, so asking `same_site` against the **seeded** domain is the wrong question — it would detect the shape anchor text exists to reach and then refuse to sample it. Host equality with the index is *stricter* than `same_site` wherever the index is on our own domain, which is every path-detected blog in the corpus, so nothing already working changes. One rule covers both shapes: **an article is a page below the index, on the index's host.**

No secondary-locale filter is needed here: `/de-ch/blogs/lifestyle/x` does not start with `/blogs/lifestyle/`, and M1.15 already prefers the shallowest index, which is the primary storefront's. The anchoring subsumes M1.25.

**A6.1 — zero candidates, or a failed article fetch, writes neither `content.blog_last_post` nor `schema.article_present`.** Not a `0`, not today's date. This is A5.5 applied to the same shape of absence, and it is why `schema.article_present = 0` no longer appears for shops whose article pages were never retrieved.

*Auditability.* The chosen URL is recorded as `content.blog_sample_url` (text, unscored), `evidence_url` being the blog index that fixed the anchor. `blog_article` joins the artifact kinds.

**Impressum discovery** is two-step: (1) footer links matching `impressum|imprint|legal notice|rechtliches`; (2) if none, probe direct paths `/impressum`, `/impressum/`, `/imprint`, `/legal`, `/rechtliches` before concluding absence. Only after both steps fail is `no_impressum` recorded — and for CH companies it sets `needs_review`, not `excluded` (§6.4).

Store bodies on disk under `data/artifacts/{domain}/{kind}-{timestamp}.html`, path recorded in `artifact.body_path`. Skip re-extraction when `content_hash` is unchanged from the previous run.

### 5.3 extract-p1 — deterministic parsers (no LLM, no cost, fully reproducible)

| Signal key | Method | Reliability note |
|---|---|---|
| `platform.detected` | HTML signature match on **anchored strings only** — Shopware: `/bundles/storefront/`; Shopify: `cdn.shopify.com`; WooCommerce: `wp-content` **and** `woocommerce`; JTL: any of `jtl-nav-wrapper`, `jtl-validate`, `jtl_token`, `jtlPackFormTranslations` | Signatures **observed**, not assumed — see M1.9/M1.10. The bare string `shopware` and bare `sw-` attributes are **not** signatures; `jtl-shop` is **not** a signature and never was one in the wild. |
| `content.blog_exists` | **Two instruments, path vocabulary first (M1.14).** (1) A blog/magazin/ratgeber/news **path** in a sitemap or a homepage link; (2) failing that, a homepage link whose **anchor text** is that vocabulary, with the href taken **wherever it points** — subdomains and foreign hosts included. `1` is written only from a blog index fetched with HTTP 200. | The path vocabulary alone missed 2 of 13 shops, both of them publishing. Anchor text reaches both — 2 true positives, 4 true negatives across the six shops carrying a `0`. **Order is the precision control**: anchor text's two loose matches sit on shops a path already resolves, so running it second means neither is reached. |
| `content.blog_search_exhaustive` | Written **only alongside `content.blog_exists = 0`**, as the qualifier of that `0`: `1` when a sitemap was enumerated **and** the homepage yielded parseable links, `0` otherwise. `value_text` carries the reason with a stable prefix — `limit:` for a measurement limit, `transient:` for a fetch that missed. | **Unscored qualifier, read by §6.2's rung 1 (M1.14, A7).** An *enabling* fact, so a run predating it fails safe. `1` licenses `opp.no_blog` (+25); it does **not** certify that no blog exists — a blog on an unlinked subdomain is undetectable by construction. Where a blog *was* located and its index did not load, `content.blog_exists` is not written at all and this signal carries `transient:`. |
| `content.blog_last_post` | **Authoritative:** the **later** of the date parsed from the sampled blog article (A6, M1.29) and the newest date on the blog index (M1.30) — JSON-LD `datePublished`, `<time datetime>`, or German visible-date patterns (`12. März 2023`), in that precedence. Both sources are lower bounds; neither is preferred. Sitemap `<lastmod>` is a hint only and is **never** used alone. | The index was the wrong page: Shopify blog indexes carry no `<time>` and no `datePublished` at all, and 5 of 7 detected blogs yielded no date from one. **The lastmod warning is now measured, not asserted — see below.** |
| `content.blog_last_post_basis` | **What bounds the value above** (M1.40): `index` or `both` where the index's own newest date *is* the value written, `article` otherwise — including where the index dated something older and the sample won. Written whenever `content.blog_last_post` is written, with the same `evidence_url`. | **Unscored provenance, read by §6.2 (M1.32).** The two sources are lower bounds of different strength — the index's date is a maximum over the posts it *lists*, a sampled article's is one post's with nothing behind it. `article` is a floor with no ceiling, and §6.2's staleness rungs must not fire on it. **It describes the date written, not the sources that had one**: an index whose newest is older than the sample has demonstrably failed to date the newest post we hold, and bounds nothing. Absent the signal the same guard applies, so a run predating it fails safe. |
| `content.blog_post_count` | Count of post links on the blog index (paginated: first page count × page count if pagination is visible), cross-checked against sitemap URL count under the blog path. **Not written for a blog on its own host** (M1.14). | Sitemap counts include tag/category noise; index count wins on conflict. A host-based blog has no path prefix separating its posts from its navigation, so "every same-host link" would be a count made of menus — the parser returns `None`, and §6.2 reads that as *not counted* rather than as *few*. |
| `catalog.product_url_count` | **A5's tier hierarchy, in A5's order (M1.24):** Tier 1 the product sitemap — recognised either by a platform filename convention *or* by the shard's own name (M1.24); Tier 2 product-typical paths (`/detail/`, `/products/`, `/produkt/`); otherwise not measurable (§10.3). Translations are excluded (M1.25). The tier is written to `value_text` alongside the count. | Path patterns are the **fallback**, not the default: reading them first counted `smile-store.de` at 6 against a catalogue of 194. A count of 6 from a path pattern and a count of 6 from a product sitemap are different claims, so the tier travels with the number. |
| `schema.article_present`, `schema.product_present` | Parse all `application/ld+json` blocks and collect `@type`. `article_present` is read from the **sampled blog article** (A6), `product_present` from the sampled product page (A5) plus the homepage. **Neither is ever written without its sample** (A5.5, A6.1). | Checking only the homepage under-detects; checking the *index* under-detects to zero. `Article`/`BlogPosting` lives on the post, and `schema.article_present` was `0` on **every** blog index in the corpus — a wrong "checked and absent" for shops whose posts all carry it. |
| `meta.description_length` | Homepage `<meta name="description">` length | **Informational only, not scored** — platforms auto-generate adequate-length templates |
| `i18n.hreflang_count` | Count of distinct `hreflang` **language** codes on the homepage. **Written as `0`, not omitted, when a fetched homepage declares none** (M1.38). | `de-DE`/`de-AT`/`de-CH` variants are not real i18n; count distinct language codes, not locale codes. A page we have with no alternates is a measurement; omitting it made `opp.de_only` unable to fire on the 7 of 13 monolingual shops it describes. |
| `content.blog_post_dates` | Every **distinct** parseable post date on the blog index, oldest first, as ISO text in `value_text`, count in `value_num`. Written whenever an index was read, the empty list included. | **Unscored evidence, read by §6.3's `neg.active_content` (M1.37)**, which had no data path before it. Dates rather than a recency count so a recompute cannot silently decay. Compared against `content.blog_post_count` it says whether the enumeration is *complete*, which is what the rule needs before it may decline. Distinct dates under-count two posts published on one day — deliberately, since that errs toward abstaining. |
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

**Why per-company is strictly better than any safe global constant.** It is tighter than any global constant, because a company that has already been awarded a rule in Phase 1 cannot be awarded it again:

| company | won `qual.owner_operated` in Phase 1? | `remaining_upside` | effective threshold |
|---|---|---|---|
| legal form is `e.K.` | yes, +15 already banked | 35 (`own_brand` 10 + `ai_invisible` 15 + `slow_site` 10) | 20 |
| legal form is `GmbH`, or absent | no — Phase 2 may still award it | 50 (35 + `owner_operated` 15) | 5 |

A global constant must use the worst case for every company; this uses each company's own.

**The safety property, stated at the strength it actually holds (M1.41).** This section used to claim the gate is *safe by construction* — "no company whose final score could reach B is discarded, since `remaining_upside` bounds what Phase 2 can add". That claim is too strong, and running the gate found the counter-example. `remaining_upside` sums Phase-2 reachability only, so a **Phase-1 rule that abstained contributes nothing** — and an abstained rung is not a rung that scored zero, it is a rung nobody has measured. `propellerdiscount.de` is stopped at 0 + 50 = 50 against a B floor of 55, with §6.2's blog ladder abstaining and worth up to +25 if its blog index were ever fetched. So the number the gate compared against was one the pipeline already knew was incomplete, and "nothing recoverable is discarded" rested on an unmeasured rung.

The fix is **not** to count abstained rungs as upside. That inflates every gate decision to cover a measurement nobody has made, and it buys the guarantee with Phase-2 spend on companies whose points may not exist. What holds instead is this, and it is what the section now claims:

> **No company whose final score could reach B is discarded without a human being told.**

An abstention is not silence: A7 requires it to route to a `review_flag` (§6.4), and `propellerdiscount.de` carries `blog_undetectable` for exactly the rung the gate could not count. The gate may stop a company on an incomplete number; it may not stop one *quietly*. Scoring is a free recompute, so a resolved flag or a later fetch re-gates the company at no cost.

**The property therefore has a dependency, and it is a human one:** it holds only where the review queue is actually read. §6.4 already spends its stickiness argument on keeping the queue readable — *a queue that refills itself stops being read* — and this is what that argument is protecting. If the queue goes unread, the gate's guarantee degrades to the arithmetic one, which is: a company stopped below the floor stays stopped.

**Each rule declares whether Phase 2 can still change its outcome**, and the declaration is part of the ruleset rather than inferred from the signal names — inference is what produced the wrong answer in the first place. **Assert at startup that every rule carries the declaration**, and fail loudly on any that does not, so adding a rule cannot silently widen or narrow the gate. A new Phase-2-reachable rule automatically raises `remaining_upside` for the companies it applies to.

**Cost consequence, stated honestly and now worse than D1 claimed.** Under D1's arithmetic the threshold was 20; under a correct per-company gate it is 20 only for companies that already banked `owner_operated`, and 5 for the rest — and on the verified corpus *none* of the twelve banks it (§5.3), so in practice nearly everything advances today. The two-phase split still excludes clear no-hopers, but the saving is smaller than §7 assumes and shrinks further the worse `legal_form` coverage is. **The honest reading: the gate's value now depends on the `qual.owner_operated` predicate (§10.1), not on the gate's arithmetic.**

**Score direction:** Phase 2 can also *lower* a score (`neg.has_agency` may fire on `HomepageExtract.agency_credit` where the footer regex missed it). The gate concerns maximum upside only; a Phase-2 score below its Phase-1 predecessor is expected and correct.

Record per company, as signals, both the decision and the number behind it — `gate.phase2_admitted` (`value_num` 0/1) and `gate.remaining_upside` (`value_num`). A company that stopped just under the line must be auditable, and with a per-company gate "just under the line" now means something different for each company, so the threshold it was actually judged against has to be recorded rather than reconstructed.

**Ratified 2026-08-16 and implemented (M3).** `remaining_upside` is a sum over `portal/ruleset.RULES` — the declaration is the source, so the number cannot drift from the rules the way D1's hand-maintained `PHASE2_MAX_POINTS = 35` drifted from a correct 50. §5.4's startup assertion is `ruleset.assert_declared()`, which refuses a ruleset containing a rule that omits `phase2_reachable`, duplicates an id, is worth nothing, or reads no signal at all. That last check is not decoration: "a rule that reads as implemented and cannot fire" is now a defect class this project has hit **three** times — B7, `opp.de_only` and `neg.active_content` — and every instance was invisible because nothing ever inspected the rules *as data*.

A negative rule contributes `max(0, points)`, i.e. nothing: the bound is on what Phase 2 could still **add**, and `neg.has_agency` can only subtract. It is still declared Phase-2-reachable, because §5.4 says a Phase-2 score below its Phase-1 predecessor is expected and correct.

**The finding that forced the narrowing above, kept here as the measurement (M1.41).** `propellerdiscount.de` scores 0 with §6.2's ladder abstaining; 0 + 50 = 50, five points below the B floor, and it is the only company the gate stops. Had its blog index been reachable, rung 1 could have awarded +25. The stop is not permanent — scoring is a free recompute and the next run re-gates it — and it is not silent, which is the part that does the work: the company carries `blog_undetectable`. Whether an abstained rung should carry its points into the bound stays **refused**, for the reason above: it would buy the guarantee with Phase-2 spend on points that may not exist.

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
    owner_named_evidence: Optional[str]  # M1.49: the name, quoted from the page
    own_brand: Optional[bool]          # manufacturer/own-brand vs pure reseller — best-fit segment marker
    own_brand_evidence: Optional[str]    # M1.49: the phrase it read that from, quoted
    agency_credit: Optional[str]
```

**The two `_evidence` fields are M1.47's ruling (ratified as option (ii), M1.49), and the limit of what they buy is part of the ruling.** Both booleans are scored — `site.owner_named` is §6.1 disjunct 3 (+15) and `brand.own_brand` is +10 — and a boolean has no string in it for a substring check to find, so before this they carried no verification of any kind. A quoted span gives the check something to test. **It is a weaker guarantee than the one `legal_name` gets, and it is weaker in a specific way: it proves the model did not fabricate its evidence, and it cannot catch the model reading the page correctly and inferring wrongly.** A homepage may genuinely contain *"unsere eigene Marke"* in a sentence about a brand it resells. For a name, the verified string **is** the scored value; for a boolean, the verified string is adjacent to it. Write this next to the fields rather than in a commit message, because a guard believed to be stronger than it is, is how a rule ends up trusted.

Input preparation is a hard requirement, not an optimisation: strip `<script>`, `<style>`, `<svg>`, `<nav>`, comments; reduce to text + structural tags; **cap at 60 KB**. Pages exceeding the cap after cleaning are truncated from the end (Impressum content is near the top of an Impressum page). This is the primary defence against unbounded token spend (§7).

Prompt discipline — stated verbatim in the system prompt of the extraction call:

> Return `null` for any field not present on the page. Do not infer, do not guess, do not fill from general knowledge. If the page is not an Impressum, return all nulls.

Hallucinated Impressum data is the single worst failure mode here: it produces a confident wrong name in a letter to a stranger. Every LLM-derived `signal` row carries `method='llm'` and must be visually distinguished in the UI. Additionally: `legal_name` and `managing_directors`/`owner_name` values are verified by exact substring presence in the cleaned page text; a value not literally present on the page is discarded and the signal written with `confidence=0` for review. **The two booleans are verified against their `_evidence` span by the same substring check** — with the weaker guarantee stated above, and the firing rule §6.1 derives from it.

**Model:** Claude Haiku 4.5 via the Batch API (50% off; latency is irrelevant here). Extraction requests are keyed to `artifact.content_hash` so a resumed run never re-submits an already-extracted page.

**Haiku 4.5's parameter surface is not the common one (M1.50, verified 2026-08-16), and each difference bites a different part of this stage:**

| Fact | Consequence here |
|---|---|
| `output_config.effort` **errors**; adaptive thinking unavailable. Thinking is the older `thinking: {type: "enabled", budget_tokens: N}`, `budget_tokens < max_tokens`, minimum 1024 | A provider interface that treats `effort` as universal fails on the first call. Declared per model, never per provider (`portal/llm.py`). |
| 200K context, **64K max output** — the only current model below the 128K ceiling | Batch sizing cannot assume the common maximum. |
| Prompt-cache minimum **4096 tokens**, the highest of any current model | §5.5b's system prompt is a few hundred tokens: caching it silently does nothing — no error, just `cache_creation_input_tokens: 0`. **If the extraction prompt is cached, assert the observed value; do not assume the write happened.** |
| Structured outputs **are** supported | The `ImpressumExtract` / `HomepageExtract` contract above is fine as written. |

**What text is sent to the model is still unstated, and §10.2's base rate depends on it.** Recorded here so the two do not drift: whichever of `parsers.visible_text` or raw HTML this stage sends decides whether §10.2 is settled on 1/12 or 3/12, and the choice belongs to M5.

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

**Four facts about how batch results actually arrive (M1.51, verified 2026-08-16). All four are load-bearing and none is visible from the happy path.**

1. **Results are returned in arbitrary order. Key by `custom_id`, never by position.** Reading result *n* as belonging to request *n* attributes a legal name and a set of Geschäftsführer to the wrong company — M1.17's failure with a new cause, and the substring check does not catch it, because the values are genuinely present on the page they came from.
2. **`expired` is a per-request result type, alongside `succeeded` / `errored` / `canceled` — so a batch can END NORMALLY while carrying requests that were never processed.** Past the 24-hour maximum a batch reaches `processing_status: ended` with a mix of succeeded and expired. This is §7's partially-processed case **arriving through the success path**, which is not where a reconcile looks for it: *"the batch ended, therefore the batch is done"* marks it `reconciled` with companies silently unextracted. **A batch moves to `reconciled` only when every one of its requests has a terminal disposition**; expired members are re-submittable as exactly those members, and re-submission is new spend that §7 reserves like any other.
3. **`errored` splits.** `invalid_request` is never retried — the request was malformed and will be malformed again, so a retry is spend with a known outcome. A server error is safe to retry.
4. **Results stay retrievable for 29 days.** Any re-read window is sized against that, and a batch older than it cannot be recovered at all — it must be re-run as new spend. The ledger has to be able to say that out loud rather than leave a batch sitting in `submitted` forever.

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
| `qual.owner_operated` | `legal_form ∈ {e.K., Einzelunternehmen, GbR}` **or** Impressum names **1–2** natural-person Geschäftsführer (`1 <= directors <= 2`, M1.46) **or** owner named on site | +15 |
| `qual.product_depth` | ≥ 20 product URLs | +10 |
| `qual.own_brand` | Sells own-brand/manufactured products, not pure reselling | +10 |
| `qual.own_domain_shop` | `catalog.product_url_count >= 5` — the exact inverse of §6.4's `possible_marketplace_only` (B7) | +5 |
| `qual.product_strength` | Trusted Shops badge present or ≥ 50 aggregate reviews | +10 |

**`qual.own_domain_shop` had no predicate at all until now (B7).** It existed only as a row in this table with a prose gloss, so M3 could not implement it and it could never fire — a permanent −5 on every company, which makes §6.5's bands stricter than the calibration they were set from. Defined here as the inverse of the soft flag that already covers the same ground, so the two can never disagree: `possible_marketplace_only` is raised when a platform is detected and `catalog.product_url_count < 5`; this awards +5 when the count is ≥ 5. It is computable in **Phase 1**, from a signal §5.3 already writes.

Two consequences to state rather than discover later:

- **When `catalog.product_url_count` is unwritten, the rule does not fire** — no +5, and no `possible_marketplace_only` either. That is the same "checked and absent ≠ never checked" discipline as A5.5, and it is not hypothetical: on the four JTL shops in the verified corpus no product URLs are identifiable at all (findings §4), so all four forgo this +5 for a reason that has nothing to do with their business.
- **The bands in §6.5 were calibrated with this rule assumed live.** Now that it can actually fire, scores rise by 5 for most qualifying companies — which restores the calibration rather than shifting it, but it does mean §6.5 should not be re-tuned on data gathered before this change.

**The two Phase-2 boolean-backed rules fire only on a span-verified value (M1.47 ratified, M1.49).** `qual.own_brand` (+10) reads `brand.own_brand`, and disjunct 3 of `qual.owner_operated` (+15) reads `site.owner_named`; both come from booleans that no substring check could reach until §5.5b gained the `_evidence` spans. The firing rule follows from **A7's third axis, used one step earlier than A7 usually uses it** — and the distinction is worth being precise about, because it is easy to read this as the `neg.active_content` case and it is not:

- **The axis usually classifies the abstention** — *which way is the score wrong while it waits?* — and that is what decides whether outbound contact is blocked.
- **Here it classifies the unverified value**, and that is what decides whether verification gates firing at all. A boolean that is wrongly `true` **awards points that were not earned**: the score reads too **high**, and too high is the direction that moves a company toward outbound contact rather than merely mis-ranking it. That asymmetry is the whole argument for a gate: an award taken on unverified model output is not a ranking error, it is a letter.

So: **each rule fires only on a value whose `_evidence` span was found in the cleaned page text. A span that fails verification abstains** — no points in either direction, no default to `false`, the reason written, and a `review_flag` raised (A7a items 10 and 11). Defaulting to `false` would be the same defect in the opposite direction and is refused for the same reason `gf_count` is not written as `0` on an Impressum that names nobody (M1.46): *unverified* is not *absent*.

**The resulting abstention errs too low, and therefore blocks nothing.** Withholding an award leaves the company ranked below where it belongs, which the queue repairs — the same class as every A7a instance but item 7. It is only the *unverified value* that errs high, and the gate is what stops that value ever reaching a score. Reading the gate's motivation as the abstention's direction would wrongly block contact on 25 points of merely-missing evidence.

**Neither review reason exists yet, deliberately.** `own_brand_undetermined` and `owner_named_undetermined` are named here so the ruling is complete, and the migration that creates them ships with the writer that raises them, in M5. Adding the rows now would be M1.45(c)'s defect exactly — *a documented resolution path with no writer is a claim the tool does not keep* — and this project has already paid for that once with §6.4's pipeline clear.

`qual.ecommerce_platform` is only as good as the §5.3 signatures behind it, and it is the single largest false-positive risk in this table: it is +15 on a string match. As of the first crawl, JTL (4 shops), Shopify (7) and WooCommerce (1) are all **observed** against real homepage HTML (M1.9, M1.10). **Shopware is only half-observed:** the SW6 signature has never matched a real shop, and Shopware 5 is knowingly undetected (M1.11). A Shopware 5 shop therefore scores 15 points lower than an identical Shopware 6 one, for no reason that has anything to do with the business.

### 6.2 Opportunity (how weak is their content marketing?)

**Blog ladder — evaluated as an ordered chain, first match wins, evaluation stops.** Written as a chain rather than a table because the table format is what allowed overlapping predicates in v0.2.

```
days_since_newest = (today − content.blog_last_post).days      # NULL if no date parsed
post_count        = content.blog_post_count                    # NULL if not counted
bounded           = content.blog_last_post_basis ∈ {'index', 'both'}   # false if unwritten
                    # i.e. the index's own newest date IS the value (M1.40)
searched          = content.blog_search_exhaustive = 1         # false if unwritten

if blog_exists is NULL or (not blog_exists and not searched):
    → NO RUNG FIRES AND THE LADDER STOPS. Raise review_flag 'blog_undetectable'
      where the reason is a measurement limit; where it is transient, retry and
      count toward 'fetch_persistently_failing' instead (A7b, migration 009).
      Never fall through to the rungs below.
elif not blog_exists:
    → opp.no_blog          +25
elif blog_last_post is NULL:
    → no rung fires; raise review_flag 'blog_date_unparseable' (§4, §6.4)
elif days_since_newest > 365:
    → if bounded: opp.blog_stale    +20
      else:       no rung fires; raise review_flag 'blog_date_unbounded'
elif post_count is not NULL and post_count < 10:
    → opp.thin_blog        +12
elif days_since_newest >= 180:
    → if bounded: opp.blog_slowing  +10
      else:       no rung fires; raise review_flag 'blog_date_unbounded'
else:
    → no rung fires (blog is current and substantial)
```

The `blog_last_post is NULL` branch is new and deliberate. A blog index whose dates cannot be parsed is an unknown, not a stale blog. Guessing here would put a false claim into a letter. Route it to human review instead — this is the same principle as `confidence=0` on unverified LLM extractions (§5.5b).

**Rung 1 abstains, and the abstention suppresses the whole ladder (M1.14, A7).** `opp.no_blog` is +25, the largest award in ruleset v3, and it is an award for an *absence* — so it is A7's shape at its most extreme. A false `opp.no_blog` tells a shop that publishes weekly that it has no blog, and thereby **manufactures the exact opportunity the outreach letter is about**; a false negative ranks a lead lower. Same asymmetry that dropped `/p/` (M1.4) and shaped A5.5, and it resolves the same way.

**Suppressing the whole ladder is not incidental — it is the only coherent reading.** If `blog_exists` is unknown then `blog_last_post` is not a meaningful question. Falling through to rung 2 would evaluate the freshness of a blog we cannot confirm exists, which produces nonsense with a confident number on it, and rung 2's own resolution (`blog_date_unparseable` — *open the blog and read the top of it*) sends a human to a page nobody can name.

**What `searched` means, and what it must never be read as.** `content.blog_search_exhaustive = 1` means both §5.3 instruments actually ran: a sitemap was enumerated **and** a homepage yielded parseable links. It does **not** mean the blog is not there. A blog on an unlinked subdomain is undetectable by construction, so `opp.no_blog` is always *"we looked everywhere we can look"* and never *"it is not there"*. The `1` licenses the award; it does not certify the absence.

*The test that was proposed and rejected*, because it is the tempting one and the counter-example is already in the corpus: **"did we have a sitemap to search" does not bound the search space.** `zecplus.de` has a sitemap — four shards — and its blog lives on `blog.zecplus.de`, a host that sitemap never mentions. Having a sitemap made one of two instruments available; it did not make the search exhaustive.

**A property of the structure, not of this bug (M1.14).** In a first-match-wins ladder, **every guard added below rung 1 increases the cost of rung 1 being wrong.** `zecplus.de` is the worked example: a false `blog_exists = 0` does not merely add a wrong +25, it short-circuits at rung 1 and makes rungs 2 through 5 unreachable, so all of M1.32's care about what a lower bound can and cannot support **never executes**. Closing the +20 route therefore raised the +25 route's weight rather than lowering it — the guard is downstream of a gate that can be wrong, and the gate bounds the value of the guard. Anything added below rung 1 in future should be read as also raising the stakes on rung 1.

**`post_count is NULL` does not fire `opp.thin_blog`, and does fall through.** Unlike rung 1, this one *is* a fall-through, and for the reason rung 1 is not: `opp.thin_blog` awards +12 for *few posts*, so an uncounted blog must not win it (A7's one-question test), while the rung below asks a different and separately measured question — is the newest post more than 180 days old — which an absent count does not touch. A host-based blog reaches this: no path prefix separates its posts from its navigation, so §5.3 declines to count rather than counting the menu.

**`bounded` is M1.32's interim guard, and it is asymmetric on purpose.** `content.blog_last_post` is a lower bound (§10.5). A lower bound is *sound* evidence of freshness — a blog with a post dated May cannot be less fresh than May — and *no* evidence of staleness, because there is nothing above it. Where the date rests on a sampled article alone, with no index date behind it as a maximum over listed posts, the two rungs that award points for **not** publishing have no basis to fire and do not fire. The other two rungs are untouched: `opp.thin_blog` requires a post within 365 days, which a lower bound inside 365 days establishes outright, and the final branch awards nothing.

Two consequences worth stating rather than leaving to be rediscovered:

- **It does not fall through.** Where `days_since_newest > 365` on an unbounded date, evaluation stops with no rung, rather than continuing to `opp.thin_blog` — that rung's own predicate needs a post *within* 365 days, which is exactly what is not known here. The rule declines in both directions, as `opp.no_product_schema` does absent its sample (A5.5).
- **`bounded` is false when the signal is absent**, not only when it reads `article`. Signals are per-run, and a run written before M1.32 carries no basis at all; reading absence as permission would silently un-guard every historical run the moment §5.4 is implemented.

**The flag is raised only where a rung was actually suppressed**, not on every `basis = article`. `doonails.de` is sample-only and its date is recent enough that the current-and-substantial branch fires either way — nothing was lost, so nobody is called. `snocks.com`, whose newest observed post is 2022-08-26, is the case: the ladder would have awarded +20 and now says nothing, and A7's third part says someone must be told. As with `blog_date_unparseable`, **the flag is raised by `score`, not by `extract-p1`** — the abstention happens where the rung is evaluated, and extract's job ends at writing the basis honestly.

The guard is interim by design, and the two changes are independent in the right direction: it closes the expensive direction now, at the cost of two rungs going quiet on 2 of 13 shops, and it constrains nothing about how the sample is chosen later. **It is not, however, retired by the selector fix.** A sample chosen by newest `<lastmod>` is a much better lower bound than an arbitrary one, and it is still one post's date rather than a maximum over the population — so `basis = article` stays unbounded whatever selects the sample. What would retire the guard is a source that bounds the newest post from *above*, and §10.5's proposal deliberately does not claim to be one.

`opp.thin_blog` now has a precise predicate: fewer than 10 posts, newest post within the last 365 days. The undefined term "active-ish" is removed.

**Conditional and independent rules — unchanged from v0.2:**

| rule_id | Condition | Points |
|---|---|---|
| `opp.no_article_schema` | Blog **exists** and no `Article`/`BlogPosting` in JSON-LD on blog pages. Never fires together with `opp.no_blog`, nor on a ladder that abstained. **Fires only when `schema.article_present` was written from an article fetched with HTTP 200** (A6.1) — absent that signal it fires in neither direction (M3 audit). | +8 |
| `opp.no_product_schema` | No `Product` in JSON-LD on a product page. **Fires only when `schema.product_present` was written from a product page fetched with HTTP 200** (§5.2, A5) — absent that signal the rule fires in neither direction. | +10 |
| `opp.ai_invisible` | `ai.queries_checked >= 2` and `ai.brand_mentions = 0` (Phase 2). The `queries_checked` clause is itself the A7 guard: an unrun check cannot award +15. | +15 |
| `opp.slow_site` | Lighthouse performance < 50 (Phase 2). **A NULL is not a low score** — the rule fires only on a written measurement (M3 audit). | +10 |
| `opp.de_only` | `i18n.hreflang_count <= 1` — at most one distinct declared language; locale variants don't count. Expansion angle. **A fetched homepage with no `hreflang` at all writes `0`, not silence** (§5.3, M3 audit); absent the signal the rule abstains. | +5 |

**`opp.de_only`'s predicate was inverted against its own population until M3's audit (M1.38).** `hreflang_language_count` returns `None` when a page carries no alternates, extract wrote nothing, and the rule could therefore fire only for a shop that *declares* `hreflang` and declares exactly one language — never for a shop with no `hreflang` at all, which is what "German only" actually looks like. **7 of 13 in the corpus.** It is B7's shape a second time: a rule that reads as implemented and cannot reach the companies it describes. A homepage we have, with no alternates in it, is a measurement; a homepage we do not have is still silence.

The old 45-point cap is removed: mutual exclusivity in the ladder plus the conditional schema rule eliminate the double-counting structurally.

### 6.3 Negative signals

| rule_id | Condition | Points |
|---|---|---|
| `neg.has_agency` | Footer names an agency (text or linked credit), platform credits excluded (§10.4) | −20 |
| `neg.active_content` | ≥ 4 dated posts within 180 days, from `content.blog_post_dates` (M1.37) | −25 |

**`neg.active_content` had no data path at all, and M3's audit had to define one (M1.37).** The predicate needs dates for *several* posts. A6 samples **one** article; `content.blog_last_post` is one date; `content.blog_post_count` is a total with no recency in it. It is B7's shape on the **largest negative in the ruleset**, and worse than B7 was, because a rule that silently never fires inflates every active publisher's score by 25 — and an over-scored lead does not merely rank wrongly, it gets contacted.

*Measured before it was fixed*, over every stored blog index: decidable on **2 of 13** shops, undecidable on 5, and vacuously sound on the 6 with no blog. `doonails.de` is the case that matters — 26 posts, newest 2026-05-29, and **not one of them carries a date the index exposes**. It is plainly an active publisher; the rule that exists to catch exactly this company could not see it.

*The data path.* `content.blog_post_dates` carries every distinct parseable post date off the index (§5.3). **Dates, not a pre-computed recency count**, because §5 promises scoring is a pure recompute at zero cost and a stored "posts in the last six months" decays silently as the window moves.

*The asymmetry is the rule.* A partial enumeration can **establish** activity — four dated posts inside 180 days means at least four, however many are undated — and can never establish its **absence**. So:

- **fires** at ≥ 4 recent dates, sound on a lower bound;
- **declines** where the enumeration is complete (`distinct dates ≥ posts listed`) and fewer than four are recent, or where rung 1 soundly established there is no blog;
- **abstains** otherwise, carrying the coverage in its reason.

Completeness is deliberately strict — two posts on one day count as one date and force an abstention — because abstaining is the *visible* error.

**This is the first A7 instance where abstention is not the conservative direction, and that is worth stating plainly.** Every previous instance guards a rule that *awards* points for an absence, so declining to fire loses points and the queue catches an under-scored lead. This rule *subtracts* points for a presence, so declining to fire leaves the score too **high**. A7's shape is identical; the cost of the abstention is inverted.

**Ratified 2026-08-16 (M1.37).** It routes to `blog_cadence_unmeasurable` (§6.4, migration 008), and because it is the too-high direction it also **blocks outbound contact** for that company until the flag is resolved — A7's third axis, argued in §5. On the corpus it abstains on **6 of 13** and blocks all six.

**An index that cannot be counted abstains here too (M1.40).** Completeness is `distinct dates ≥ posts listed`, and where `content.blog_post_count` was never written there is no total for the dates to be complete against. `zecplus.de` is the case: two dated posts, no count. The reason must say that rather than printing the missing total as a zero — *"von 0 Beiträgen tragen nur 2 ein lesbares Datum"* is a sentence that goes into a letter and into the queue note a person acts on.

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
- `catalog_not_measurable` — the site serves sitemaps, and no tier of A5's hierarchy can identify a product in them (§5.3, §10.3). Ratified after M2 and added in migration 003. Three rules go quiet at once when this fires — `qual.product_depth` (+10), `qual.own_domain_shop` (+5), `opp.no_product_schema` (+10) — so the company most in need of a human is precisely the one about which the pipeline says least. The **signal** `catalog.not_measurable` carries the reason text; the flag carries the routing. Same division of labour, and the same principle, as `blog_date_unparseable`: where the pipeline cannot measure, route to a person rather than guess a number. (Migration 004 added `review_flag.raised_note`, so a flag now *can* hold a line of text. This reason's text stays on the signal, where it already is and where the diff can see it move.)
- `blog_date_unbounded` — `content.blog_last_post` rests on a sampled article alone (`content.blog_last_post_basis = 'article'`), and §6.2's staleness rung declined to fire on it (M1.32, A7). Added in migration 004. **Deliberately not folded into `catalog_not_measurable`**, whose meaning is *nothing was measured*; this one means *a value exists and cannot bound the rule that reads it*. There is a real date, off a real post, and it is sound evidence of freshness and none of staleness — so the human is not being asked whether the shop has a blog, they are being asked one narrow question, *is this blog actually dead*, which is answered by opening it and looking at the top. Conflating the two would send them to the wrong page.

  This is the reason `review_flag` gained **`raised_note`** (migration 004). §6.4 previously held that the signal carries the text and the flag carries the routing, on the grounds that a flag has no room for text — a statement about the schema rather than a design commitment, and the schema was being rebuilt anyway. The person triaging this queue needs one fact at the instant they see the row — *newest post we could see: 2022-08-26* — and making them join `content.blog_last_post`, `content.blog_last_post_basis` and `content.blog_sample_url` to learn it is how a queue stops being read. The division of labour is unchanged in substance: signals remain the machine-readable evidence, scored and diffable; `raised_note` is the one line a person needs in order to act. Nothing was relocated — `catalog.not_measurable` keeps its reason text — and the column is nullable because most reasons say it all.

- `blog_undetectable` — `content.blog_exists = 0` rests on a search that ran only one of §5.3's two instruments, so §6.2's rung 1 declined to fire `opp.no_blog` (+25) and suppressed the whole ladder with it (M1.14, A7). Added in migration 005. Raised by `score`, where the rung is evaluated, as `blog_date_unparseable` and `blog_date_unbounded` are; `raised_note` carries which instrument was missing, because that is what tells the person where to look instead.

  **A fifth reason rather than a reuse, for the same purpose `blog_date_unbounded` was distinct from `blog_date_unparseable`: a different thing is known, so a different resolution applies.** `blog_date_unparseable` says *there is an index and its dates are unreadable* — open it and read the top. `blog_date_unbounded` says *there is a date and it cannot bound the rule* — one narrow question, is this blog dead. `blog_undetectable` says *we do not know whether there is a blog at all* — a different question again, answered somewhere else entirely: does this shop publish, anywhere, under any name? Sent to the wrong one of the three, a human opens the wrong page.

  **What it may never be presented as.** Not "this shop has no blog". The reason means the search could not have been exhaustive — and per §6.2 even an exhaustive one means only that we looked everywhere we can look.

- `blog_cadence_unmeasurable` — `neg.active_content` (−25) cannot decide how often the shop publishes: the index dates fewer posts than it lists, or lists posts it does not count (§6.3, M1.37). Ratified 2026-08-16, added in migration 008. Raised by `score`, where the rule is evaluated.

  **A sixth reason rather than a reuse**, on the discriminator the blog reasons have now been split by three times — a different thing is known, so a different resolution applies. `blog_date_unparseable` says *there is an index and its dates are unreadable*. `blog_date_unbounded` says *there is a date and it cannot bound the rule*. This one says *there are dates, for some of the posts* — and the question it asks a human is neither of the others: **how often does this shop publish?** Answered by opening the blog and reading the dates down the first page.

  **It is the first reason that blocks outbound contact** (§5's third axis, §8's rule). Every other abstention in this spec leaves the lead scored too low, which is a ranking delay the queue repairs. This one withholds a *penalty*, so the lead reads stronger than the evidence supports — and the failure mode is not a bad ordering, it is a call to a company that already publishes weekly. Until a human resolves the flag, `outreach` refuses the row.

  **What resolution means here, and what it does not.** Resolving is a person saying they looked at the blog and know the cadence. Per the stickiness rule below it is never re-raised, so the block lifts on that judgement and not on any later measurement — which is the intended trade, because the human's answer is the more durable fact.

- `fetch_persistently_failing` — a page that was identified and has not returned 200 for **3 consecutive runs on 3 distinct days** (A7b, §5). Ratified 2026-08-16, added in migration 009, closing the routing M1.34 left open. Three measurements share it — the product sample, the blog index, and the sampled article — because all three send a person to the same question, *does this URL load for you?*, with `raised_note` carrying which URL and since when. Counted by `score` over consecutive scoring runs, which is why the days matter: a crash-restart loop inside one afternoon must not manufacture a flag about our own crash. Too-low in every instance, so it blocks nothing.

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

   **The estimate comes from `count_tokens`, not from a heuristic (M1.52).** Token counts are model-specific, so a character-length rule of thumb calibrated on one tokenizer is a second expression describing what the first one does — M1.42's shape, one layer out from the corpus. `count_tokens` answers the question actually asked, for the model actually being called, and it is free.

   Two consequences, both taken rather than left to be discovered:

   - **The reservation now depends on a network call before any spend is committed.** `count_tokens` is not a paid call, but it is a call, and it can fail.
   - **A failed `count_tokens` aborts the submission.** It does not fall back to an over-estimate. §7 exists to make an under-reserved batch impossible, and a fallback estimate is exactly the path by which an unmeasured number enters the ledger looking like a measured one. An aborted run is the cheap failure; this whole section is built on preferring it.

10. **The price table is dated data, asserted at startup (M1.52).** Prices are `(provider, model, batch) → (input per MTok, output per MTok, as-of date)` in `portal/llm.py`, with `assert_prices()` at import in the same shape as `ruleset.assert_declared` — a model with no declared price cannot be called, rather than being called at an assumed one. Haiku 4.5 lists at **$1.00 / $5.00 per MTok**; the Batch API is 50% off list, so the batch row is **$0.50 / $2.50 per MTok, as-of 2026-06-24**. The date is part of the row and not a comment beside it: a price without one is a constant that was true once, and §7.1's whole arithmetic rests on these two numbers.

11. **A prepaid balance is a second ceiling this tool does not own.** §7's controls all assume the tool is the thing that stops spending. A prepaid key can empty at any moment, invisibly, between reserve and result. `billing_error` is a real API error type, maps to **403**, and shares that code with `permission_error` — so it is distinguishable **only** through the error object's `.type`, never through the status code, which is what most code branches on. **A balance error is its own status, never folded into `failed`**: `portal reconcile` must be able to report *"this batch stopped because the key ran dry"* in those words, because *"the provider failed"* and *"we ran out of money"* need different operator responses. **Where in the batch lifecycle it surfaces — on submit, or per request inside the results — is unverified (M1.53); the seam represents both and the code assumes submit-time**, which is the assumption under which the tool stops before committing spend it cannot pay for.

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
| Impressum + homepage extraction | ~30k tokens, Batch API at $0.50/$2.50 per MTok (Haiku 4.5 list $1/$5, 50% off; as-of **2026-06-24**, M1.52) | $0.015 |
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

**No outbound contact on a score the pipeline cannot support (M1.37).** Where a rule abstains in the direction that leaves the score too *high*, the affected company's `outreach` rows are refused until a human resolves the flag — enforced in the schema (migration 008), not in a UI warning. The reasoning is this section's own: an export that cannot state its basis must fail rather than degrade, because the failure leaves the building. A contact placed on a score that is knowingly up to 25 points generous is the same category of error, one step further out. See §5's A7, third axis.

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
**Empty. M3 may start, and has.**

*(All three blockers are closed. §5.3 naming the wrong page for `content.blog_last_post` and `schema.article_present` — closed by **M1.29 / A6**. **M1.14**, `content.blog_exists` under-detecting and carrying +25 anyway — closed by **M1.34**, summarised below. **A1 / M1.21–M1.22**, the per-company Phase-2 advance gate — **ratified 2026-08-16** and implemented in `portal/score.py`; `remaining_upside` is derived from the live ruleset rather than from a constant, and §5.4's startup assertion is `ruleset.assert_declared`. All three stay visible in the amendment log rather than here.)*

**A table that empties is worth a note about what filled it.** All three blockers were the same species: a rule or a bound asserting something the instrument behind it could not support — the wrong page, the wrong vocabulary, a hand-maintained constant. None was found by a test. Each was found by reading a run, or by reading the rules as data. M3's own audit (`docs/m3-absent-input-audit.md`) found three more of the same species before a line of scoring was written, which is the first time that has happened in advance rather than in arrears.

**M1.14, closed by M1.34 — kept here in summary because the negative results are the expensive part.** `content.blog_exists` under-detected two shapes in a 13-shop corpus, both unreachable by any path vocabulary: a blog on a subdomain (`blog.zecplus.de`) and a blog served as root-level slugs (`lampenflut.de`). Four candidate instruments were measured on stored homepages; **two of the four were rejected on the measurement**, and they are recorded so they are not re-proposed:

| instrument | verdict |
|---|---|
| **Anchor text**, href taken wherever it points | **Adopted.** Reaches both shapes — `zecplus.de`'s *"Blog"* → `blog.zecplus.de`, `lampenflut.de`'s *"Licht-Ratgeber"* → a root-level slug. 2 true positives, 4 true negatives across the six shops carrying a `0`. |
| `Article` JSON-LD on the homepage | Not adopted. Reaches the root-slug shape only; the subdomain homepage links out and carries no post markup. Admissible later as corroboration, insufficient alone. |
| **Semantically named sitemap shard** (`blogs-0-sitemap.xml`) | **Rejected — reaches neither shape.** `zecplus.de`'s index lists four shards and no blog shard, because the blog is a different host with its own sitemap. `lampenflut.de` serves no sitemap at all: `/sitemap.xml` 404s and `robots.txt` declares none. (The shard reading still earns its place under M1.24, for keeping content out of the catalogue count.) |
| **Feed autodiscovery** | **Rejected, and the measurement is why.** It looked like the cheapest instrument available — one `<link>` in a `<head>` already on disk. It fires on **4 of the 6**, and on every one of those four it is a **platform default**: JTL ships `/rss.xml` on every install, WordPress ships `/feed/` and `/comments/feed/`. All four are shops with no blog. §10.4's rule about removable, platform-shipped strings, in a new place — it would have converted four correct negatives into four false positives while looking like a fix. |

**And no instrument closed it, which was the real finding.** "We searched with a better vocabulary and found nothing" is still a vocabulary claim, and `opp.no_blog` is an award for an absence — so M1.14 was **A7 applied to the largest award in the ruleset, and not applied**. The resolution is §6.2's rung-1 abstention plus `blog_undetectable` (§6.4, migration 005), not the detector alone. See `docs/m114-blog-detection-read.md` for the corpus measurements and M1.34 for what was ruled.

### 10.2 The Phase-2 cost lever — not a correctness blocker

**Should `qual.owner_operated` admit an `Inh.`/`Inhaber` marker, or a personal name standing where a company name would be?**

This is **the primary lever on Phase-2 spend**, not a ranking refinement, and §7.1 is where its effect shows up. §5.4's gate is safe either way — a company that cannot bank the rule simply carries its +15 in `remaining_upside` and is admitted more readily — so nothing recoverable is ever discarded. But that is exactly the mechanism: **every sole trader made Phase-1-identifiable moves one company from an effective threshold of 5 to 20**, and thereby out of Phase 2 unless it earns its way in on other signals.

On the verified corpus the predicate (`legal_form ∈ {e.K., Einzelunternehmen, GbR}`) matches **none** of the twelve, while five are plainly owner-operated sole traders whose form is simply unstated — `Lampenflut.de Inh. Dominik Lindemeier`, `NAVUCKO Nataša Vučković`, `Benjamin Luzolo BLACKPOLISH`, `Christian Riedel OPULENT Wohnen`, `Kay Link`. That is 5 of 12 (~42%) whose effective threshold is 5 when it arguably should be 20, and it is why §7.1's steady-state reads $31–36/month rather than something lower.

It stays out of §10.1 because it does not block correctness, and it is not settled here because a personal name standing where a company name would be is a judgement rather than a regex, and twelve shops is not the sample to decide it on. **Settle it on a larger corpus, and re-derive §7.1 when it is settled.**

**The base rate this will be settled on is instrument-dependent, and that must be fixed before the measurement is taken (Unit 0 item 3, ratified 2026-08-16).** On the guarded selection (M1.43 + M1.44) the `Inh.`/`Inhaber` marker appears on **1 of 11** stored Impressum pages in `parsers.visible_text`, and on **3 of 12** across all stored Impressum artifacts in the raw HTML (different denominators: the raw-HTML scan is not restricted to the selected page) — on the two extra domains the token occurs *only inside a `<script>` block*, which `visible_text` decomposes deliberately, because JSON-LD vendor identifiers otherwise land inside the provider block and are read as the company's own details (§5.3). Neither number is wrong; they answer different questions. **Which one settles §10.2 depends on what text `extract-p2` sends the model**, which §5.5b does not yet state. If it sends `visible_text` — the natural choice, and what every deterministic parser already reads — the model cannot see what a human reading the page cannot see either, and **1/12 is the right base rate**. If it sends raw HTML, 3/12 is. A larger corpus measured with an unstated instrument would settle §10.2 to a number nobody could reproduce, which is M1.48's lesson one level up. Reproducible via `portal audit-impressum-candidates`.

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

### 10.4b Decisions taken on the external review at `5f56560` — recorded so they are not re-derived

Four rulings, applied rather than re-argued. The review that prompted them is the same one that produced M1.59–M1.62.

- **M4 (DNS-independent fixtures) comes before M2 (CI). ORDER CORRECTED.** The review lists M2 first while stating in its own body that M4 is what makes M2 green. Recorded here because the sequence would otherwise be re-derived from the review as written, and re-derived wrongly. **Both landed in Unit 5, in that order** — M1.64 then M1.65.

- **M5 has three preconditions, not one, and every one of them came out of verifying a previous fix rather than out of planning.** Named here and in M5's definition of done, so M5 cannot merge over any of them:

  1. **§7's monthly-rolling cost ceiling, as a startup assertion** (M1 from the review). Three things, not one: define the month boundary, write the ledger query over `run.est_cost_usd` and `llm_batch.est_cost_usd`/`actual_cost_usd`, and assert in `assert_declared`'s shape that no paid path is reachable without a ledger check. §7 states the ceiling and no code reads it.
  2. **An origin-keyed robots lookup that reports *not verifiable* rather than *allowed*** (M1.61). `uq_artifact_identity` is `(company_id, kind, content_hash)`, so a robots row does not name its origin and two origins serving identical bytes collapse into one. M1.44's repair (a) as ratified — *"the newest stored robots.txt"* for the company — reproduces the `zecplus.de` vacuity, where a 173-byte permissive file from `blog.zecplus.de` was applied to all 31 of the shop's stored bodies.
  3. **Score-date pinning** (M1.63 → **M1.66, checked in Unit 5 and NOT closed**). The question asked was whether `score` rows already record the `today` they were evaluated against. They do not: `computed_at` is a second clock read taken at persist time, and a stage given `today=2020-03-07` writes `computed_at=2026-08-17` with the evaluation date recorded nowhere. This one **precedes any spend against a band**, because a band that cannot be reproduced is not a basis for buying anything.

- **The interrupted M5 work is stashed, not deleted** (`git stash` entry `interrupted-M5-remnant`, taken 2026-08-17 at `6a5e266`). It is worth salvaging and is inventoried in the Unit 5 report: `phase2_input_settled` on `Rule` with `assert_declared` enforcing it, the `settled` term that lets §5.4's gate *tighten* rather than only loosen, three-state `_own_brand` and `_owner_operated` abstentions, and untracked `portal/pagespeed.py`, `portal/verify.py` and `migrations/010_phase2_writers.sql`. **Migration 010 is why it could not simply be committed**: it creates `llm_batch_request`, which has **no writer in `portal/` and no registration in this spec** — verified by grep, not assumed — which is M1.45(c)'s shape and exactly what 010's own header comment says a migration must not do. It is also what failed `test_schema.py::test_every_spec_table_exists`. Rebuild it in M5 with its writer, or register it in §10.6 as ahead-of-writer deliberately; do not let it arrive as a side effect of unstashing.

- **M3 — the repository names 13 real prospects. The decision is to MAKE THE REPOSITORY PRIVATE**: not pseudonymise, not gitignore the seeds. The review's own analysis rules out both alternatives — the findings documents' worth is inseparable from the named sites, so pseudonymising destroys the asset, and gitignoring `candidates.csv` while leaving the named documents in place is the wrong half. **One fact the review did not state:** the repository publishes §6's **scoring weights and band thresholds alongside the named prospects and assessments of their marketing**, so any named company can compute exactly why it scored low. That is the difference between a list of leads and a published critique with the marking scheme attached. **The visibility change is the operator's to make and is STILL PENDING.** Verified twice, both on 2026-08-17: at Unit 4 and again at Unit 5, `gh repo view tanmayagrawal24/lead-Portal --json visibility,isPrivate` returns **`{"isPrivate": false, "visibility": "PUBLIC"}`**. The exposure is live, not hypothetical, and **a decision recorded is not a change made** — this line stays until the command returns `PRIVATE`.

- **M1 (§7's cost ceiling exists in the spec and nowhere in the code) is correct, and lands WITH M5, not after it.** Not Unit 4's work. When it does land it is **three things, not one**, and this is why it cannot be a line in a checklist: §7's ceiling is a **monthly rolling** total, and the schema carries `run.est_cost_usd` and `llm_batch.est_cost_usd` / `actual_cost_usd` while defining **no month boundary and no query**. So: (a) define the boundary; (b) write the ledger query; (c) assert at import, in `assert_declared`'s shape, that **no paid path is reachable without a ledger check**. **Added to M5's definition of done, so M5 cannot merge without it.**

- **H2 (the redirect SSRF guard) and L1 (extracting `SitePolicies`) are Unit 6, in that order.** The review is right that the address guard belongs in that seam. Combining a security behaviour change with a 286-line extraction means a defect in either is attributable to both — and M1.42 is already this project's lesson about a second expression describing what the first one did. Extraction lands first as a pure no-behaviour-change commit; the guard lands on top of it.

### 10.5 Undecided, not blocking

- **`content.blog_last_post` is a lower bound, not the last post date (M1.30).** The index's date is a maximum over the posts it *lists*, the sampled article's is one post's, and the later of the two is better than either — but neither is guaranteed to be the newest post on the blog. `opp.blog_stale` awards **+20** for *not* publishing, so an under-estimate of freshness fires it wrongly, which is the expensive direction. (`+25` is `opp.no_blog`; the paragraph's argument is about the size of the expensive direction, so the number matters.) The available fix is to select the sample by the **newest `<lastmod>` in the blog sitemap shard** and still read `datePublished` off the page — lastmod as the *selector*, never as the *value*, so its known freshness bias cannot reach the signal (at worst it picks a recently edited old post). That changes A6's ordering, which was ratified as shallowest-first, so it is recorded here rather than taken. **Now written up as a tier hierarchy — see `docs/blog-article-selector-proposal.md`, which measures it on the corpus and is the thing to rule on.** It proposes two tiers, newest-`lastmod` then A6-as-ratified. A third — first article in index document order — was measured and dropped: every confirmation of the newest-first convention comes from an index that dates its posts, which is precisely the population where no sample is needed, so on the cases that matter the convention is unfalsifiable by observation rather than merely unobserved.

  **What has been taken, meanwhile, is the interim guard (M1.32).** It is deliberately independent of the question above: `content.blog_last_post_basis` records whether the date rests on the index (a maximum over listed posts) or on the sample alone (a floor with nothing above it), and §6.2's two staleness rungs decline to fire on the latter. That closes the expensive direction on the 2 shops where it is open today and constrains nothing about how the sample is later chosen. It is **not** retired by the selector fix: a lastmod-chosen sample is a better lower bound, not a maximum, so `basis = article` stays unbounded whatever picks the article. Retiring it needs a source that bounds the newest post from above, which the proposal is explicit about not being.

  **Resolved: a suppressed rung routes to a human.** `blog_date_unbounded` (§6.4, migration 004) is raised by `score` wherever the guard silences a staleness rung, carrying the lower bound in `raised_note`. It is a distinct reason rather than a reuse of `catalog_not_measurable`, because *a value that cannot bound the rule* and *no value at all* send a person to do different things. The general form is now A7 (§5), which this was the fourth instance of.
- **`neg.active_content`'s abstention had no routing, and it was the one that most needed it (M1.37, A7 instance 7). Resolved 2026-08-16.** `blog_cadence_unmeasurable` (§6.4, migration 008), plus the consequence the direction of the error demands: a too-high abstention **blocks outbound contact** until a human resolves it, the same way §8 fails an export that cannot state its basis. The general form is A7's **third axis** (§5), which this instance forced into the tables: *which way the score is wrong while it waits* is not the same question as *would running again help*, and only the first of the two had ever been recorded. On the corpus it now abstains on 6 of 13 and blocks all six.
- **The gate treats an abstained Phase-1 rule as zero upside (M3).** `remaining_upside` counts Phase-2 reachability only, so a company can be stopped on a total the pipeline knows is incomplete — `propellerdiscount.de`, at 0 + 50 against a B floor of 55, with an abstained blog ladder worth up to +25. §5.4's safety claim is that nothing whose final score could reach B is discarded, and for one run that claim rests on an unmeasured rung. It self-corrects on the next run, since scoring is a free recompute and no Phase-2 money has been spent. Whether an abstained **transient** should carry its points into the bound is a change to a gate ratified on 2026-08-16, so it is recorded here rather than taken.
- **A persistent transient needed a routing (M1.34, A7b). Resolved 2026-08-16.** `fetch_persistently_failing` (§6.4, migration 009), counted by `score` over consecutive scoring runs at the N = 3 / 3-distinct-days policy A7 already bound. One reason for all three instances — the product sample, the blog index and the sampled article — because they send a person to one question, and `raised_note` carries which URL. It had waited a milestone: for three runs a company could have a rule going quiet with nobody told.
- **Should `neg.active_content`'s −25 step, or decay? (M1.63, opened 2026-08-17.)** Observed once, on `navucko.com`: a single post crossing the 180-day line took the largest penalty in §6.3 off a lead overnight and moved it D → C, with no change to the site and none to the code. The rule is behaving as specified — `>= 4` dated posts inside six months, both numbers hard — and the question is whether a step function is the right shape for "publishes regularly" when the input is a lower bound that decays continuously. **Not taken**, because it is a §6.3 weight question and §10.3 already forbids calibrating on this corpus; recorded so the next instance is recognised rather than re-derived. The measurement is in `docs/unit4-robots-tristate-findings.md` §7.
- **Timestamps are read as UTC dates.** `navucko.com`'s newest post moved 2026-06-21 → 2026-06-20 when M1.31 made timestamps parseable: `…T22:00:00Z` is the 21st in CEST. Immaterial at §6.2's month-scale thresholds and recorded so it is not later mistaken for drift.
- **M6 is unblocked, and the thing that unblocked it also moved the cost (M1.54).** The brief blocked M6 on the D4 pricing confirmation and `docs/v0.3-review-findings.md` added a second condition — *does Haiku 4.5 support web search at all?* Both are now answered: $10/1,000 searches (§5.5c, 2026-08-15) and yes, via the basic `web_search_20250305` variant. The consequence to price into §7 when M6 is scheduled is that the **newer** `web_search_20260209` — which filters results with code execution before they reach the context window — requires Opus 4.6+ / Sonnet 4.6+, so on Haiku 4.5 raw results land in context in full and tokens per search exceed what the $10/1,000 figure implies. §5.5c's 10–20k input tokens per query was estimated on exactly this behaviour; what changes is that it cannot be assumed to fall later, because the cheaper-in-context tool is not available on this model. **Unblocked, not started.**
- Ollama for local extraction instead of Haiku — saves ~$10/month at Phase-2 volumes, costs German-language extraction quality and the substring-verification simplicity. Currently: use Haiku.
- Whether to store artifact bodies compressed (gzip) — likely yes above a few hundred companies.
- Research brief export stays Markdown for v1; DOCX (letter-ready) is a candidate for v1.1 once the brief content has stabilised against real outreach feedback.
- Band thresholds (§6.5) are provisional pending the first 100-company calibration run.
