# M1 handoff — `portal fetch`

**Branch:** `m1-fetch` (not merged)
**Date:** 2026-08-15
**Spec:** `docs/lead-portal-spec-v0.3.md` — §5.2 gained the A5 product-sample rule before any code was written.

Assumes no prior context. Read §5.2 of the spec alongside this.

---

## 1. What was built

`portal fetch --seed <csv>` walks each seeded domain in the §5.2 order:

```
robots.txt → homepage → sitemap.xml (+ nested shards) → Impressum
           → blog index if a blog path is found
           → one sample product page if a product path is found
```

| Module | Responsibility |
|---|---|
| `portal/net.py` | httpx client, `HostRateLimiter`, User-Agent, body cap |
| `portal/robots.py` | robots.txt parsing and the §5.2 exclusion policy |
| `portal/sitemap.py` | index expansion, gzip shards, product-sitemap detection |
| `portal/sampling.py` | the A5 selection rule — filters and code-point ordering |
| `portal/impressum.py` | two-step Impressum discovery, blog-path detection |
| `portal/artifacts.py` | bodies to disk, rows via the §4 D5(b) upsert |
| `portal/seeds.py` | seed CSV loading, domain normalisation, company upsert |
| `portal/fetch.py` | stage orchestration, worker pool, review flags |

**What it writes:** `artifact` rows (+ bodies under `data/artifacts/{domain}/`), `company.excluded` on a robots refusal, a `review_flag` row for `no_impressum`, a `run` row, and exactly one signal — `catalog.product_sample_url`.

**What it deliberately does not write:** every other signal. `content.blog_exists`, `platform.detected`, `schema.*` and the rest belong to `extract-p1` (M2). Blog-path detection exists here only to decide what to fetch.

### Test coverage

158 tests, all passing; `ruff check` and `ruff format --check` clean.

- **Politeness is measured, not asserted.** `tests/test_politeness.py` records request arrival times *at the fixture servers* and asserts every same-host gap is ≥ 1.0 s, and that no more than two distinct hosts were ever served at once. It includes an anti-vacuity test proving the pool really does run two hosts concurrently — otherwise the ceiling assertion would pass trivially on a sequential implementation.
- **Redirect hops are measured the same way** (7.1): a chain is counted at the server and every hop's spacing asserted, so "each hop is a request" is a property of the running code rather than a claim in a docstring. The apex→www hop is included, so the shared politeness budget is measured rather than asserted.
- **Cross-host redirects:** the target host's robots.txt is fetched before the hop, a disallowed hop is refused and recorded, and the lookup is memoised per host.
- **`Crawl-delay`:** parsed for our agent and the wildcard group, raises the host's interval but never lowers it, and skips the domain above the 10 s cap.
- **Robots cases:** allow-all, disallow-required-paths (→ hard exclusion, and nothing past robots.txt is requested), disallow-irrelevant-paths-only (→ not a refusal), single disallowed path (→ skipped and recorded, not fetched), missing robots.txt.
- **Impressum two-step:** footer link present; footer link absent so direct probing runs; neither, so `no_impressum` is raised as a *soft* flag with `excluded` still 0.
- **A5:** gzipped multi-shard product sitemap, sitemap mixing content and product URLs, zero candidates, Tier 0 reuse, Tier 0 fall-through on a 404, and stability across shard redistribution.

Fixtures use `Max Mustermann` / `Musterstraße 1` throughout. No real personal data is in the repository.

---

## 2. The live smoke test

`creative-potato.global` is the only non-loopback host this branch has ever contacted. Everything else runs against `tests/fixture_server.py` (stdlib `http.server`).

The live tests are skipped unless opted in:

```bash
PORTAL_LIVE_SMOKE=1 python -m unittest tests.test_live_smoke
```

**Result: both passed.**

### The User-Agent's contact URL resolves — confirmed

`https://creative-potato.global` returned a 2xx. The identifiable-bot promise in §5.2 and §8 holds. Nothing to escalate.

### What the live crawl found

```
run 1:
  creative-potato.global: homepage, robots, sitemap
      review flag: no_impressum
      note: no blog path found
      note: no product candidates; schema.product_present must stay unwritten
```

Three artifacts stored (robots 1,860 B; homepage 29,230 B; sitemap 980 B), five Impressum probes each 404, zero signals written — correct, since there were no product candidates.

**Worth your attention, and not a tool bug: `creative-potato.global` has no Impressum page.** I checked the stored homepage by hand. The footer links to `/ueber/` and `/datenschutz/` only; the single occurrence of "Rechtliches" is an `aria-label` on a `<nav>`, not a link. All five probe paths (`/impressum`, `/impressum/`, `/imprint`, `/legal`, `/rechtliches`) return 404, and the sitemap lists four URLs, none of them an Impressum. So the `no_impressum` flag is a true positive, not a parser miss.

I'm flagging it as an observation about your own site, not as legal advice: §5 DDG generally requires a Impressum for a German-facing business site, and the tool's whole pitch rests on the operator's own house being in order. Your call entirely.

---

## 3. Running the real seed crawl

**Once you have approved a seed list.** Nothing in this branch may crawl a third-party domain before that.

```bash
# 1. Write the approved list. `domain` is the only required column.
cat > seeds/nrw-shops.csv <<'CSV'
domain,legal_name,city,postal_code,country
beispiel-shop.de,Beispiel GmbH,Köln,50667,DE
CSV

# 2. Create the database if it does not exist (idempotent).
portal init

# 3. Crawl.
portal fetch --seed seeds/nrw-shops.csv
```

Roughly 8–10 requests per domain at 1 req/s, two domains in flight — about 5 s per domain, so 20 domains ≈ 2 minutes.

Useful to know:

- `--db PATH` or `PORTAL_DB` moves the database; artifacts land in `artifacts/` beside it.
- `--interval` cannot go below 1.0 s and `--max-hosts` cannot exceed 2. The CLI exits 2 rather than accepting a value that would breach §5.2 — including by typo.
- Re-running is safe. Unchanged pages do not create new `artifact` rows, and a repeated `no_impressum` does not re-raise a resolved flag.
- `seeds/example.csv` holds only `creative-potato.global` and exists for smoke-testing.

---

## 4. Decisions I made that the instructions did not cover

Each of these is a judgment call. Flagging them so they get ratified rather than absorbed.

### 4.1 Tier 0 reuse reads the signal, not the artifact table

A5.7's rationale said the sample signal "makes Tier 0 reuse a simple lookup rather than an artifact-table scan", and implementing it revealed a stronger reason. `uq_artifact_identity` is keyed on `(company_id, kind, content_hash)`, so two product pages with byte-identical bodies — a soft-404 being the realistic case — collapse into a single row whose `url` is whichever was stored first. Reusing that URL could pin a dead sample forever, defeating A5.1's fall-through. Tier 0 therefore reads the last `catalog.product_sample_url`.

### 4.2 "Discard" means excluding the dead URL from re-selection

Caught by a test that failed. After a Tier 0 sample 404s, the dead URL is *still* the code-point minimum of its candidate set, so the fall-through re-chose the exact URL that had just failed — burning a second request and ending with no sample at all, even though a live product was available. `choose_product_sample` now takes an `exclude` set. I wrote this into §5.2 along with a **cap of two product requests per company per run** (the Tier 0 probe plus one fresh selection).

### 4.3 Failure artifacts update in place rather than appending

`uq_artifact_identity` is a partial index over non-NULL hashes, so it does not constrain failure rows. Left to a plain INSERT, every re-run would append another row for the same dead URL and `artifact` would grow without bound. A failure row for the same `(company_id, kind, url)` is updated in place instead, advancing `last_checked_at`. *When* a URL last failed is worth keeping; a row per attempt is not. **Now stated in §5.2 (M1.7).**

### 4.4 How "required paths" is interpreted for robots exclusion

§5.2 names `/`, `/sitemap.xml`, the Impressum path and the blog path. The last two are not knowable before fetching. Implemented as: hard-exclude when `/` is disallowed, **or** `/sitemap.xml` is disallowed, **or** *every* one of the five Impressum probe paths is disallowed. A single disallowed path is skipped per-URL and recorded with `error='robots_disallowed: …'`, never an exclusion. The blog path is checked per-URL once discovered — a disallowed blog is a missing signal, not grounds for exclusion. **Now stated in §5.2 (M1.7).**

### 4.5 A missing or unparseable robots.txt is not a refusal

404 on robots.txt is the common case for small shops. Treated as "no restrictions stated", which is the conventional reading. A robots.txt that fails to parse is treated the same way rather than aborting the run.

### 4.6 Footer-link discovery falls back to the whole document

Step 1 looks inside `<footer>`; if there is no `<footer>` element, or it contains no links, the search widens to every `<a>` on the page. Plenty of small German shops mark the footer up as a plain `<div class="footer">`. A false positive costs one extra request; a false negative costs a lead.

### 4.7 Smaller ones, for completeness

- **Sitemap shard cap of 50 per company**, so a broken or hostile sitemap index cannot turn one company into thousands of requests.
- **Response bodies truncated at 8 MB.**
- **Redirects followed, max 5** — one hop at a time, each rate-limited, host changes robots-checked (see 7.1). `artifact.url` records the *final* URL, so the evidence link points where the content actually came from.
- **`check_same_thread=False`** on the SQLite connection, because two host-workers share it. Every cross-thread database touch is serialised behind `FetchStage._db_lock`. **Any new threaded stage must take the same lock.**
- **A `base_url` seam** on `FetchStage`, defaulting to `https://{domain}`, so tests can point the same code at a loopback fixture server. Production code contains no "is this localhost?" special case.
- **Domain normalisation keeps non-`www` subdomains** — `shop.example.de` stays distinct from `example.de`, and so do their politeness budgets (§5.2, M1.8). Apex and www share one budget but remain separate robots.txt authorities.
- **Seed loading fails loudly** on a malformed row, with a line number, rather than skipping it.

---

## 5. Bug found in the spec

**§4's artifact upsert could never have run.** The snippet was:

```sql
ON CONFLICT (company_id, kind, content_hash) DO UPDATE
```

`uq_artifact_identity` is a *partial* index (`WHERE content_hash IS NOT NULL`), and SQLite matches a conflict target to a partial index only when the predicate is repeated verbatim. Every artifact write raised `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint`. Fixed in both the spec and the code; §4 now carries a note explaining why the `WHERE` is load-bearing.

---

## 6. What I could not verify

**This is the section that matters most.**

### 6.1 No parser here has ever seen real German shop HTML

The hard boundary against third-party crawling means every fixture is one I wrote. Specifically unverified:

- **The product-sitemap regexes.** Shopware's `…-product-….xml.gz`, Shopify's `sitemap_products_*.xml`, WooCommerce's `product-sitemap.xml` and JTL's `/sitemap/product` come from documentation and convention, not from observation. If any is wrong, Tier 1 silently falls through to Tier 2 and the sample gets picked by path pattern instead — a quality regression that produces no error.
- **The Tier 2 path patterns** `/detail/`, `/products/`, `/produkt/`. `/p/` was here and has since been **dropped** (7.3): it is a real product prefix on some shops and a *pagination* prefix on others, and a false match feeds a listing page to `schema.product_present` and wrongly awards +10.
- **Real robots.txt variety** — the fixtures cover the shapes §5.2 names, not the mess of production files.
- **Gzipped multi-shard sitemaps** work against my fixtures. Shopware's real output is untested.

The first approved seed crawl is the real test of M1. I would treat its output as data to inspect by hand rather than as a passing run.

### 6.2 The concurrency ceiling is tested on loopback

Three fixture servers on `127.0.0.1/2/3` are three hosts to the rate limiter and to the `Host` header, which is what §5.2 constrains. It is not a test of behaviour under real DNS, connection pooling, or a host that resolves to several addresses.

### 6.3 The apex→www loop is covered on loopback, but not under real DNS

I said in the first pass that the apex↔www redirect could not be tested. That was wrong: `localhost` and `www.localhost` both resolve to 127.0.0.1, so one fixture server answering on both names is a faithful apex→www host with nothing but loopback involved. It is now tested end to end — the robots hop is followed and its policy seeded for both names, an off-site hop during the robots fetch is refused and recorded, and the shared politeness budget is measured at the server across the hop.

What remains genuinely unverified is narrower: **the loop under real DNS and TLS** — a hostname resolving to several addresses, connection reuse across a scheme change, a redirect chain that mixes both. The live smoke against `creative-potato.global` exercises the loop over real HTTPS but that host does not redirect. Only the first approved seed crawl will cover the rest, and apex→www will appear on nearly every domain in it. Worth reading those artifact rows by hand.

---

## 7. Questions I parked — all now ruled on

Ruled 2026-08-15, after review. Each is recorded in the spec's M1 amendments table and implemented on this branch; nothing below is still open.

1. **`Crawl-delay` — added.** Honour `max(1.0, crawl_delay)`, capped at 10 s; above the cap the domain is skipped with `excluded_reason = 'crawl_delay_too_high: …'` rather than stalling a worker slot. Written into §5.2.
2. **`defusedxml` — declined, no new dependency.** Instead a sitemap whose bytes contain `<!DOCTYPE` or `<!ENTITY` is refused before parsing. Two lines, and it eliminates the entity-expansion class outright while the dependency list stays locked.
3. **`/p/` — dropped** until observed in the wild, on the asymmetry argument in 6.1: a false positive wrongly awards +10, a false negative only leaves a signal unwritten, which A5.5 already handles.
4. **A5.6 and A5.7 — both ratified.** Marked approved in the amendments table.
5. **`signal` idiom — changed.** `ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING` replaces `INSERT OR IGNORE` in §4, in the migration, and everywhere signals are written. `test_schema.py` now pins that a bad `method` **raises** rather than vanishing, and that `OR IGNORE` would have neither raised nor written.
6. **`uvicorn` — approved for M4.** Added to §3's stack list, marked M4-only, so it is not re-litigated later.

### 7.1 The defect this review found: redirects bypassed the limiter

`Fetcher.get` rate-limited only the original URL and then handed the chain to `httpx` with `follow_redirects=True, max_redirects=5`. Every hop after the first was an unthrottled request: a same-host chain could fire up to five requests at one host inside a second, breaking §5.2's hard 1 req/s rule on any redirecting domain, and a cross-host hop fetched from a host whose robots.txt had never been read.

The old comment — "callers check `same_site`" — answered *attribution*, not politeness. By the time a caller inspects the response the request is already out.

Fixed: `follow_redirects=False`, hops walked one at a time with `limiter.wait()` per hop, and a host change followed only after that host's robots.txt has been fetched and consulted (its `Crawl-delay` included). A refused hop is recorded as `error='redirect_refused: …'`, never silent. The one exception is the robots.txt fetch itself, which may follow a hop **within the seeded site** — the apex↔www redirect nearly every shop has, where no earlier robots.txt exists to authorise it.

`tests/test_politeness.py` now counts hop arrivals *at the fixture server* and asserts the spacing holds across a chain; `tests/test_fetch.py` asserts that a redirect target's `/robots.txt` is requested before anything else on that host, that a disallowed hop is refused and recorded, and that the lookup is memoised per host.

---

## 8. Not built, on purpose

No parsers beyond what fetch order and A5 selection need. No `extract-p1`, no scoring beyond the §6.2 guard sentence, no signals other than `catalog.product_sample_url`. M2 starts from a clean slate.
