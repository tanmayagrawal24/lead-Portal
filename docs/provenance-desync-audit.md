# Provenance desync audit (M1.42)

**Question asked.** M1.40 was not a basis bug. `content.blog_last_post_basis` was
a *claim about a value*, computed by a different expression than the value, so
the two could disagree — and did, on 3 of 13. Every signal carries provenance:
`evidence_url`, `method`, `confidence`, `artifact_id`. This audits whether each
is produced by the same code path as the value it describes.

**Method.** Every `INSERT INTO signal` site read (3: `extract._write`,
`fetch._write_sample_signal`, `score._persist`), then every finding checked
against the stored corpus in `data/portal.db` — 2,274 signals, 13 companies —
by re-deriving the value and comparing it to what the citation actually
contains.

**Headline.** `evidence_url` is desynced on the catalogue keys and on both
sample keys. On `catalog.product_url_count` it is desynced on **8 of 8**
companies that have one, and in every case the cited document contains **zero**
of the URLs counted. `signal.artifact_id` — the column that exists to make this
link structural — is written by no code path at all.

---

## The corpus test

For each signal: is `evidence_url` a stored artifact of that company?

| key | rows | `evidence_url=''` | cites no stored artifact | `artifact_id` NULL |
|---|---:|---:|---:|---:|
| `catalog.product_sample_url` | 47 | 0 | **5** | 47 |
| `gate.phase2_admitted` | 117 | **117** | 0 | 117 |
| `gate.remaining_upside` | 117 | **117** | 0 | 117 |
| `content.blog_exists` | 239 | **104** | 0 | 239 |
| `content.blog_search_exhaustive` | 32 | **32** | 0 | 32 |
| every other key | 1,722 | 0 | 0 | 1,722 |
| **total** | **2,274** | **370** | **5** | **2,274** |

"Cites a stored artifact" is the weakest possible test and the catalogue keys
pass it. They pass it while citing the wrong document, which is why the test
below matters more.

---

## F1 — `catalog.product_url_count` cites a document holding none of the URLs it counted

`portal/extract.py:306` — `evidence = sitemaps[0].url`, the first sitemap
artifact by row id. The value comes from somewhere else entirely: shards
classified `product` (tier 1) or pattern-matched catalogue URLs (tier 2), then
locale-filtered. The evidence expression does not read the shards, the
classification, or the filter.

Re-parsing each cited document and counting the page URLs in it:

| domain | count written | page URLs in the cited document | cited |
|---|---:|---:|---|
| bio-fleischer-laden.de | 306 | **0** | `/sitemap.xml` |
| blackpolish.de | 22 | **0** | `/sitemap.xml` |
| doonails.de | 389 | **0** | `www.doonails.com/sitemap.xml` |
| ekomia.de | 335 | **0** | `/sitemap.xml` |
| navucko.com | 72 | **0** | `/sitemap.xml` |
| smile-store.de | 194 | **0** | `www.smile-store.de/shop/en/sitemap.xml` |
| snocks.com | 462 | **0** | `/sitemap.xml` |
| zecplus.de | 242 | **0** | `/sitemap.xml` |

Eight of eight. Every cited document is a sitemap *index* — it lists other
sitemaps and no pages — so the citation is not merely imprecise, it points at a
document in which the number cannot be verified at all.

**`smile-store.de` is the letter case.** Its 194 came from
`/PixupSitemap/…/articles-0-sitemap.xml`, the primary-locale product shard,
*after* M1.25's locale filter deliberately dropped the byte-identical
`/shop/en/` shard as a translation. The evidence cites
`/shop/en/sitemap.xml` — the index of the very locale the count excludes. A
letter would read "Ihr Katalog umfasst 194 Produkte" over a link to the English
subshop, and the operator checking it would find neither the products nor the
number.

This is M1.17's failure — a confident answer read off the wrong page — arriving
through the provenance field rather than through the artifact.

**Contributing shards, so a fix is well-defined.** After the locale filter, the
count rests on one shard for 8 of 8 companies (pre-filter, `ekomia.de` has 9
candidates and `snocks.com` 10, all locale duplicates of one). A single
`evidence_url` can therefore name the shard that produced the number.

## F2 — `catalog.not_measurable` shares the defect and the line

`portal/extract.py:377` writes the §10.3 third state against the same
`sitemaps[0].url`. 58 rows. The claim is about *every* shard — "no product
sitemap and no URL matching a product pattern" — and it cites one document
with no URLs in it. The reader cannot see what was searched.

## F3 — `catalog.product_sample_url`'s evidence is synthesised from the seeded domain

`portal/fetch.py:747`:

```python
def _sample_evidence(base: str, tier: str) -> str:
    """§5.2 requires a real URL here, never a synthesised one …"""
    if tier == "homepage_links":
        return homepage_url(base)
    return f"{base}/sitemap.xml"
```

The docstring forbids a synthesised URL and the next line synthesises one. Two
consequences, both in the corpus — these are the 5 dangling rows in the table
above:

- **The seeded domain is not the site.** `doonails.de` cites
  `https://doonails.de/`; its stored homepage is `https://www.doonails.com/` — a
  different TLD. `propellerdiscount.de` cites the bare host where the artifact
  is `www.`. This is exactly M1.18's blinding — anchoring on the seed instead of
  `site_domain` — reappearing one field over.
- **`{base}/sitemap.xml` need not exist.** `smile-store.de` and `zecplus.de`
  cite it; neither has an artifact at that URL. The candidate list for both came
  from `sitemap_products_1.xml?from=…` shards.

The chosen URL is picked from a specific list; which document that list came
from is known at the call site and is thrown away in favour of a string built
from the tier label.

## F4 — `content.blog_sample_url` cites the index whichever document supplied the candidate

`portal/fetch.py:621` passes `index.url` unconditionally. `choose_blog_article`
has three tiers and only the third, `index_links`, reads off the index;
`blog_sitemap` and `sitemap_under_index` both read off sitemap shards. Same
shape as F3, opposite direction — here the evidence is a real artifact that
simply did not supply the value.

Not measurable from the database, because of F6.

## F5 — `schema.product_present` can be decided by the homepage and cites the product page

`portal/extract.py:648`:

```python
present = parsers.has_product_schema(html) or parsers.has_product_schema(homepage_html)
self._write(result, "schema.product_present", sample.url, num=1 if present else 0)
```

A `1` decided by the second operand cites a page that does not contain the
markup. **Latent, not observed**: on all 9 companies with the signal, the cited
product page carries the schema and no homepage does, so the fallback has never
decided a value here. It is a desync by construction, and `opp.no_product_schema`
is a +10 rule.

## F6 — neither sample key stores its tier, so F3 and F4 cannot be audited from the database

`catalog.product_url_count` puts its tier in `value_text` next to the number, so
"6 from a path pattern" and "6 from a product sitemap" stay distinguishable
(M1.24). `_write_sample_signal` stores the chosen URL and nothing else — yet
`_sample_evidence` *derives the evidence from the tier*. The provenance depends
on a fact that is not recorded, so the only way to check a citation is to re-run
the selection.

## F7 — `signal.artifact_id` is declared, foreign-keyed, and never written

`portal/migrations/001_initial_schema.sql:135`:

```sql
artifact_id   INTEGER REFERENCES artifact(id),
```

2,274 of 2,274 rows NULL. **This is the root cause of F1–F5.** The link from a
number to the document it was read off is carried entirely by an unconstrained
TEXT column, computed by an expression the value never passes through. A string
assembled separately from the value can disagree with it; an id taken off the
same object the body was read from cannot. Every one of the five findings above
is a place where a second expression was written to describe what the first
expression did.

## F8 — 370 signals carry `evidence_url = ''`

`docs/implementation-brief.md:59` — "Every signal write carries a real
`evidence_url`. No placeholder, no empty string, no synthesised URL." Two
distinct classes, and only one of them is a bug:

- **`content.blog_exists = 0` (104) and `content.blog_search_exhaustive` (32).**
  There *is* evidence: the search read the homepage links and the sitemap
  inventory, and `_no_blog_index` already receives `homepage_url` as a
  parameter. It writes `""` anyway. The reader of a "no blog" verdict cannot see
  what was looked at — which is M1.14's whole subject.
- **`gate.phase2_admitted` and `gate.remaining_upside` (234).** Derived from the
  score, not read off any page. No artifact exists and none should be invented.
  These are computed values recorded in the signal table, and §1's guarantee
  does not apply to them — but nothing in the schema or the column comment says
  so, and §8's export asserts on `evidence_url` uniformly.

---

## `method` and `confidence`

- **`method`** — the literal `'deterministic'` at all three write sites. It
  cannot desync today because no LLM path exists. It is worth noting that it is
  hardcoded at the call site rather than derived from the writer, so M5 adding
  an LLM write through a shared helper is the moment it becomes desyncable.
- **`confidence`** — NULL on all 2,274 rows. Correct for deterministic signals
  per the schema comment. §9's "`confidence=0` in red" has nothing to render
  yet; M4 must handle the column being NULL everywhere without treating NULL as
  0, or every deterministic signal turns red.

## Clean

Read off the same artifact whose URL is cited, verified against the corpus:
`platform.detected`, `meta.description_length`, `i18n.hreflang_count`,
`agency.footer_credit`, `reviews.trusted_shops`, `reviews.count` (homepage);
`content.blog_exists = 1`, `content.blog_post_count`, `content.blog_post_dates`
(blog index); `schema.article_present` (the article, per A6);
`content.blog_last_post` and `content.blog_last_post_basis` — the last two
because M1.30 made the evidence travel out of `max(dated)` with the value and
M1.40 made the basis describe the value rather than the sources. That is the
shape the rest of these keys need.
