# P2 proposal — a seed domain that has moved to another registrable domain

**Status:** proposal. No code written. Requested before implementation.
**Sections it would change:** §5.1 (discovery/identity), §5.2 (fetch), §6.4 (review reasons), §4 (schema, migration 002).
**Evidence:** `docs/first-crawl-findings.md` §6 — two of thirteen seeded domains have moved.

---

## 1. The problem, precisely

`company.domain` holds the seeded value forever, and `same_site(url, domain)` is anchored on it. When a shop has moved to a different registrable domain, the site's own URLs then test as **off-site**, and three parsers go quiet without erroring:

| domain | moved to | consequence in `run 1` |
|---|---|---|
| doonails.de | www.doonails.com | 5 sitemap shards never expanded → catalog invisible → sample fell to homepage links |
| germanelectronic.de | lampenflut.de | footer Impressum link rejected → forced to probe; sitemap 404; no product sample |

The same guard is load-bearing elsewhere: it is what stopped `propellerdiscount.de`'s placeholder `Sitemap: https://www.yoursite.com/…` from dragging us onto a third-party host. So this cannot be fixed by loosening `same_site`. The two needs are in genuine tension, which is why it is a spec decision rather than a patch.

Note what is **not** broken: `artifact.url` already records the final host on all 13 domains, and the new host's robots.txt is already fetched and honoured (the hop policy does that). The defect is confined to *what we look for next*.

## 2. Recommendation

**Adopt the final host as the company's site identity, once, when the homepage redirect resolves — and keep the seeded domain as the discovery value.**

Four rules:

1. **Trigger.** After the homepage fetch, if the final URL is not `same_site` with the seeded domain, the site has moved. An apex→www redirect is *not* a move — `same_site` already accepts it, and treating it as one would rewrite identity on five of thirteen domains for no reason.
2. **Once, and only from the homepage.** A deeper redirect never adopts. A product page redirecting off-site is a marketplace or affiliate link, not a move, and adopting from it would let one stray link redirect the whole crawl onto a third party.
3. **Scope.** After adoption, `same_site`, the base URL for absolutising links, and sitemap-shard expansion all anchor on the new host for the rest of that company's run. Robots continues to be keyed per authority, as now.
4. **Both facts are recorded.** The seeded domain is provenance and must survive; the effective host is what the crawl used. A run must be auditable as "we seeded X, we crawled Y".

### Schema: a new column, not a mutated one

Add to `company` (migration 002):

```sql
site_domain TEXT   -- normalised effective host after a homepage redirect off the
                   -- seeded registrable domain. NULL = never moved, the common case.
```

`company.domain` keeps the seeded value permanently. `site_domain` is what the fetch stage anchors on when set. A helper — `company.effective_domain = site_domain or domain` — is the single read path.

**Why not mutate `company.domain`:**

- It is `UNIQUE`, so mutation can collide. See §3.
- It is the human key across the UI, the outreach table and every hand-written query.
- `data/artifacts/{domain}/` is keyed on it. Mutating it orphans every body already stored under the old name, and silently: nothing errors, the old directory simply stops being written to.
- Provenance is lost. "We seeded germanelectronic.de" is a fact about the lead list that a rewrite destroys, and it is exactly the fact a human needs to judge whether the lead is still the intended one.

## 3. Collision behaviour — the part that needs a ruling

With `site_domain` as a separate nullable column, **a UNIQUE violation is structurally impossible**: nothing writes to `domain` after insert. That is the main reason to prefer it. But the *underlying* collision is still real, and there are three shapes:

**(a) The final host is already another company's `domain`.** Both `germanelectronic.de` and `lampenflut.de` are in the seed list, or a later discovery run adds the target. Two company rows now describe one business.

**(b) Two seeded domains move to the same target.** Two brands consolidated onto one shop. Same end state as (a), reached differently.

**(c) The final host collides with another row's `site_domain`, not its `domain`.** Same as (b) but neither row is seeded as the target.

**Recommended handling, identical for all three: detect, record, do not merge, do not double-crawl.**

1. Before adopting, look up whether any *other* company row has this host as its `domain` or `site_domain`.
2. If one exists, **do not adopt on the later row.** Record `excluded_reason = 'duplicate_site: <host> is already company #<id>'` and stop that company's run there. Exclusion, not a review flag, because there is nothing further to fetch — continuing would re-crawl a site we already have.
3. Raise a review flag on the row that *does* own the host, so a human sees that something merged into it. This needs a new §6.4 reason (`duplicate_site`), which means a `CHECK` constraint change in the same migration.
4. **Never merge automatically.** Merging two companies means choosing which legal name, which contact, which score and which outreach history survives. That is an outreach decision with a letter at the end of it, and a crawler must not make it.

Ordering is deterministic by `company.id` — the lower id owns the host — so two workers racing on the same target resolve the same way regardless of scheduling. The lookup and the adoption must happen under `_db_lock`, since two host-workers can hit this concurrently.

**Politeness is unaffected either way**, worth stating explicitly: the rate limiter is keyed on host, not on company, so even two rows crawling one site could never exceed 1 req/s against it. The cost of a collision is duplicated work and a duplicated lead, not a breach.

## 4. Is the moved lead still the intended lead? — a separate ruling

`germanelectronic.de` now serves **lampenflut.de**, a different brand with a different catalog. Whether that is still the company we meant to approach is a judgment about the lead list, not about the crawler.

**Recommendation:** adoption always raises a review flag (`domain_moved`, new §6.4 reason) with `excluded = 0`. The crawl proceeds and the data is collected; a human confirms the lead is still the one intended before anything is sent. This is the same principle as `blog_date_unparseable` — where the pipeline cannot know, route to review rather than guess.

## 5. What I would *not* do

- **Loosen `same_site`.** It is the control that kept us off `yoursite.com`.
- **Follow the move across more than one registrable domain in a run.** One adoption per company per run; a second move is a review flag, not a second adoption. Chained redirects across domains are as often a parked-domain chain as a real move.
- **Adopt on a `www.` change.** Already handled; adopting there would churn identity on 38% of the corpus for nothing.
- **Backfill `site_domain` for the two known cases by hand.** They will be set on the next crawl by the same code path everything else uses, and a hand-written row is a row no test covers.

## 6. Cost

One migration (a column plus one `CHECK` widened for two new reasons), one lookup under the existing lock, and the fetch stage reading `effective_domain` instead of `domain` in four places. The tests it needs are the two shapes already observed — a move with no collision, and a move onto a seeded row — plus the negative that apex→www does not trigger it.

## 7. Open question for you

Should a moved domain's **artifacts** continue to be stored under the seeded domain's directory (`data/artifacts/germanelectronic.de/`), or move to the new host's?

My recommendation is **keep the seeded directory**: it is keyed to the company, not the host, so one company's evidence stays in one place across a move, and nothing already on disk orphans. But it does mean a directory named `germanelectronic.de` full of `lampenflut.de` pages, which is mildly confusing to anyone reading the filesystem directly. The alternative — moving them — is worse for a different reason: it makes the on-disk layout depend on when a site moved.
