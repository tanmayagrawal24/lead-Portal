"""`extract-p1` end to end, against a database and artifacts on disk.

The stage makes no HTTP requests at all, so these tests need no fixture server:
they write artifact rows and bodies, then assert what was extracted from them.

Most of what is asserted here is about **what is not written**. Three rules in
§6 turn on the difference between "checked and absent" and "not measured", and
every one of those distinctions is worth points in the wrong direction if the
stage guesses.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from portal import db, extract, migrate

BLOG_INDEX = """<html><body>
  <a href="/blogs/news">Übersicht</a>
  <a href="/blogs/news/erster">Erster</a>
  <a href="/blogs/news/zweiter">Zweiter</a>
  <script type="application/ld+json">
  {"@type":"BlogPosting","datePublished":"2024-02-01"}
  </script>
</body></html>"""

PRODUCT_PAGE = """<html><body><h1>Muster</h1>
  <script type="application/ld+json">{"@type":"Product","name":"Muster"}</script>
</body></html>"""

SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://muster.de/products/alpha</loc></url>
  <url><loc>https://muster.de/products/beta</loc></url>
  <url><loc>https://muster.de/blogs/news/erster</loc></url>
</urlset>"""

#: Root-level SEO slugs, the JTL shape: products indistinguishable from
#: categories by path (findings §4). Four of thirteen real shops look like this.
SITEMAP_ROOT_SLUGS = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://muster.de/</loc></url>
  <url><loc>https://muster.de/luftpolsterfolie-eco-30cm</loc></url>
  <url><loc>https://muster.de/stretchfolie-23-transparent</loc></url>
</urlset>"""


class ExtractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.conn = db.connect(self.root / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.artifacts = self.root / "artifacts"
        self.conn.execute(
            "INSERT INTO run (started_at, stage) VALUES ('2026-08-15T00:00:00Z','fetch')"
        )

    def company(self, domain: str = "muster.de", site_domain: str | None = None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO company (domain, site_domain, discovery_source, discovered_at) "
            "VALUES (?,?, 'seed_csv', '2026-08-15T00:00:00Z')",
            (domain, site_domain),
        )
        return int(cursor.lastrowid)

    def artifact(
        self,
        company_id: int,
        kind: str,
        url: str,
        body: str | bytes,
        domain: str = "muster.de",
        status: int = 200,
    ) -> None:
        directory = self.artifacts / domain
        directory.mkdir(parents=True, exist_ok=True)
        suffix = {"sitemap": "xml", "robots": "txt"}.get(kind, "html")
        name = f"{kind}-{abs(hash(url)) % 10**12}.{suffix}"
        payload = body.encode("utf-8") if isinstance(body, str) else body
        (directory / name).write_bytes(payload)
        self.conn.execute(
            "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
            "body_path, bytes, fetched_at, last_checked_at) "
            "VALUES (?,?,?,?,?,?,?,'2026-08-15T00:00:00Z','2026-08-15T00:00:00Z')",
            (
                company_id,
                kind,
                url,
                status,
                f"h{abs(hash(url)) % 10**12}",
                f"{domain}/{name}",
                len(payload),
            ),
        )

    def extract(self, company_id: int, domain: str = "muster.de"):
        _run_id, results = extract.run(
            self.conn, [(company_id, domain)], self.artifacts, today=date(2026, 8, 15)
        )
        return results[0]

    def signals(self, company_id: int) -> dict[str, sqlite3.Row]:
        return {
            row["key"]: row
            for row in self.conn.execute(
                "SELECT * FROM signal WHERE company_id = ?", (company_id,)
            )
        }

    def signal_text(self, company_id: int, key: str) -> str:
        return self.signals(company_id)[key]["value_text"] or ""


class TestCatalogueMeasurability(ExtractTestCase):
    """§10.3's three states. This is the part of M2 with points riding on it."""

    def test_a_countable_catalogue_is_counted(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP)
        result = self.extract(company_id)

        self.assertEqual(result.signals["catalog.product_url_count"], 2)
        self.assertNotIn("catalog.not_measurable", result.signals)

    def test_root_slugs_are_not_measurable_rather_than_zero(self) -> None:
        """The JTL shape. `0` would be false — these shops sell hundreds of
        products — and would raise `possible_marketplace_only` against a real
        shop while making `qual.product_depth` and `qual.own_domain_shop`
        silently unavailable. Up to 25 points on URL structure alone."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP_ROOT_SLUGS
        )
        result = self.extract(company_id)

        self.assertNotIn("catalog.product_url_count", result.signals)
        self.assertIn("catalog.not_measurable", result.signals)
        row = self.signals(company_id)["catalog.not_measurable"]
        self.assertEqual(row["value_num"], 1)
        self.assertIn("root-level slugs", row["value_text"])

    def test_an_unmeasurable_catalogue_reaches_the_review_queue(self) -> None:
        """Ratified after M2: a signal is read by the scorer and shown to
        nobody, and the company that most needs a human is the one where
        `qual.product_depth`, `qual.own_domain_shop` and `opp.no_product_schema`
        all went quiet at once. Same routing as `blog_date_unparseable`."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP_ROOT_SLUGS
        )
        result = self.extract(company_id)

        self.assertIn("catalog_not_measurable", result.review_flags)
        row = self.conn.execute(
            "SELECT reason, resolved_at FROM review_flag WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        self.assertEqual(
            (row["reason"], row["resolved_at"]), ("catalog_not_measurable", None)
        )
        needs_review = self.conn.execute(
            "SELECT needs_review FROM company WHERE id = ?", (company_id,)
        ).fetchone()["needs_review"]
        self.assertEqual(needs_review, 1)

    def test_a_measured_catalogue_raises_no_flag(self) -> None:
        """Anti-vacuity: the flag has to be capable of not firing."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP)
        self.assertEqual(self.extract(company_id).review_flags, [])

    def test_no_sitemap_writes_neither_signal(self) -> None:
        """Not measurable and not-even-looked-at are different facts too."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        result = self.extract(company_id)

        self.assertNotIn("catalog.product_url_count", result.signals)
        self.assertNotIn("catalog.not_measurable", result.signals)

    def test_a_gzipped_sitemap_is_decompressed(self) -> None:
        """Reading the body as text first destroys the gzip header, which
        silently turned three real JTL catalogues into "0 URLs"."""
        import gzip

        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/export/sitemap_0.xml.gz",
            gzip.compress(SITEMAP.encode("utf-8")),
        )
        self.assertEqual(
            self.extract(company_id).signals["catalog.product_url_count"], 2
        )


class TestCatalogueTierHierarchy(ExtractTestCase):
    """M1.24: the count uses A5's tiers, product sitemap before path patterns.

    Reading them in the other order is what produced the corpus's worst number:
    `smile-store.de` counted at 6 against a catalogue of 194, because a
    `/detail/` pattern found six stragglers on the rest of the site while the
    shard holding every product sat unread in the index.
    """

    #: The real shape: a semantically named shard, and a handful of URLs
    #: elsewhere on the site that happen to match a path pattern.
    ARTICLES_SHARD = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://muster.de/stoebern/marke/produkt-eins</loc></url>
      <url><loc>https://muster.de/kosmetik/produkt-zwei</loc></url>
      <url><loc>https://muster.de/geschenke/produkt-drei</loc></url>
    </urlset>"""

    STRAGGLERS = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://muster.de/detail/uebriggebliebenes</loc></url>
    </urlset>"""

    def stocked(self) -> int:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/PixupSitemap/sitemap/area/articles-0-sitemap.xml",
            self.ARTICLES_SHARD,
        )
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/PixupSitemap/sitemap/area/customPages-0-sitemap.xml",
            self.STRAGGLERS,
        )
        return company_id

    def test_the_named_shard_is_counted_not_the_path_patterns(self) -> None:
        company_id = self.stocked()
        result = self.extract(company_id)
        self.assertEqual(result.signals["catalog.product_url_count"], 3)

    def test_the_tier_travels_with_the_count(self) -> None:
        """A count of 3 from a product sitemap and a count of 3 from a path
        pattern are different claims. Only one is the shop's own statement."""
        company_id = self.stocked()
        self.extract(company_id)
        row = self.signals(company_id)["catalog.product_url_count"]
        self.assertEqual(row["value_text"], "product_sitemap")

    def test_path_patterns_still_answer_when_no_shard_is_named(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP)
        self.extract(company_id)
        row = self.signals(company_id)["catalog.product_url_count"]
        self.assertEqual(
            (row["value_num"], row["value_text"]), (2, "sitemap_path_pattern")
        )

    def test_a_content_shard_is_not_catalogue(self) -> None:
        """A shard the shop labels `blogs` holds posts, whatever their paths
        look like — but its URLs stay available to blog *path* detection, which
        is where dropping them turned snocks.com into `blog_exists = 0`."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/sitemap_blogs_1.xml",
            """<?xml version="1.0"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://muster.de/blogs/news</loc></url>
              <url><loc>https://muster.de/blogs/news/erster</loc></url>
            </urlset>""",
        )
        self.artifact(
            company_id, "blog_index", "https://muster.de/blogs/news", BLOG_INDEX
        )
        result = self.extract(company_id)
        self.assertNotIn("catalog.product_url_count", result.signals)
        self.assertEqual(result.signals["content.blog_exists"], 1)


class TestMultiLocaleCatalogues(ExtractTestCase):
    """M1.25: ten storefronts are one catalogue."""

    def locale_shop(self) -> int:
        company_id = self.company()
        self.artifact(
            company_id,
            "homepage",
            "https://muster.de/",
            """<html><head>
            <link rel="alternate" hreflang="x-default" href="https://muster.de/">
            <link rel="alternate" hreflang="en" href="https://muster.de/en">
            </head></html>""",
        )
        for prefix in ("", "/en", "/fr-ch"):
            self.artifact(
                company_id,
                "sitemap",
                f"https://muster.de{prefix}/sitemap_products_1.xml",
                f"""<?xml version="1.0"?>
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://muster.de{prefix}/products/alpha</loc></url>
                  <url><loc>https://muster.de{prefix}/products/beta</loc></url>
                </urlset>""",
            )
        return company_id

    def test_translations_are_not_counted_twice(self) -> None:
        """Declared (`/en`) and undeclared (`/fr-ch`) alike: `snocks.com` names
        three of its ten markets and serves all ten."""
        result = self.extract(self.locale_shop())
        self.assertEqual(result.signals["catalog.product_url_count"], 2)
        self.assertIn("locale filter dropped 4", " ".join(result.notes))

    def test_a_filter_that_would_empty_the_catalogue_is_not_applied(self) -> None:
        """An exclusion that removes everything is evidence about the exclusion,
        not about the shop. A shop serving its whole catalogue from a two-letter
        first segment must not become unmeasurable on a path-shape guess."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/sitemap_products_1.xml",
            """<?xml version="1.0"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://muster.de/de/products/alpha</loc></url>
              <url><loc>https://muster.de/de/products/beta</loc></url>
            </urlset>""",
        )
        result = self.extract(company_id)
        self.assertEqual(result.signals["catalog.product_url_count"], 2)
        self.assertIn("not applied", " ".join(result.notes))


class TestProductSchemaGuard(ExtractTestCase):
    """A5.5/A5.6: `0` only ever from a product page fetched with HTTP 200."""

    def test_no_product_page_writes_nothing(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        result = self.extract(company_id)
        self.assertNotIn("schema.product_present", result.signals)

    def test_a_failed_product_fetch_writes_nothing(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "product_page", "https://muster.de/products/x", "", status=404
        )
        self.assertNotIn("schema.product_present", self.extract(company_id).signals)

    def test_a_fetched_product_page_without_schema_writes_zero(self) -> None:
        """Here `0` is correct: we looked at the page and there is no Product."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "product_page", "https://muster.de/products/x", "<html></html>"
        )
        self.assertEqual(self.extract(company_id).signals["schema.product_present"], 0)

    def test_a_fetched_product_page_with_schema_writes_one(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "product_page", "https://muster.de/products/x", PRODUCT_PAGE
        )
        self.assertEqual(self.extract(company_id).signals["schema.product_present"], 1)


BLOG_ARTICLE = """<html><body><h1>Ein Beitrag</h1>
  <script type="application/ld+json">
  {"@type":"BlogPosting","datePublished":"2025-06-30"}
  </script>
</body></html>"""


class TestBlogSignals(ExtractTestCase):
    def test_reads_existence_and_count_from_the_index(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP)
        self.artifact(
            company_id, "blog_index", "https://muster.de/blogs/news", BLOG_INDEX
        )
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 1)
        self.assertEqual(result.signals["content.blog_post_count"], 2)


class TestBlogArticleSample(ExtractTestCase):
    """A6. §5.3 named the index; on Shopify the index carries neither the date
    nor the markup, and both live on the post."""

    def stocked(self, index_html: str = BLOG_INDEX) -> int:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP)
        self.artifact(
            company_id, "blog_index", "https://muster.de/blogs/news", index_html
        )
        return company_id

    def test_the_later_of_the_two_dates_wins(self) -> None:
        """M1.30, found by running A6 rather than by testing it. A sampled
        article carries one post's date; the index carries a maximum over the
        posts it lists. Neither is reliably the newest — preferring the sample
        lost 17 months on bio-fleischer-laden.de, and preferring the index would
        have lost 4 on ekomia.de — so both are lower bounds and the later one
        is taken."""
        company_id = self.stocked()  # index dates 2024-02-01
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            BLOG_ARTICLE,  # 2025-06-30
        )
        result = self.extract(company_id)
        self.assertEqual(str(result.signals["content.blog_last_post"]), "2025-06-30")

    def test_an_older_article_does_not_overwrite_a_newer_index(self) -> None:
        company_id = self.stocked()  # index dates 2024-02-01
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            '<html><body><time datetime="2021-07-22">alt</time></body></html>',
        )
        result = self.extract(company_id)
        self.assertEqual(str(result.signals["content.blog_last_post"]), "2024-02-01")

    def test_article_markup_is_read_from_the_article(self) -> None:
        company_id = self.stocked()
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            BLOG_ARTICLE,
        )
        self.assertEqual(self.extract(company_id).signals["schema.article_present"], 1)

    def test_no_article_leaves_article_markup_unwritten(self) -> None:
        """A6.1. `0` from the index is a fact about the wrong page — it was `0`
        on every blog index in the corpus, for shops whose posts all carry
        `BlogPosting`."""
        result = self.extract(self.stocked())
        self.assertNotIn("schema.article_present", result.signals)
        self.assertIn("stays unwritten (A6.1)", " ".join(result.notes))

    def test_a_fetched_article_without_markup_writes_zero(self) -> None:
        """Here `0` is correct: we looked at the post itself."""
        company_id = self.stocked()
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            "<html><body><h1>Ein Beitrag</h1></body></html>",
        )
        self.assertEqual(self.extract(company_id).signals["schema.article_present"], 0)

    def test_an_undated_article_falls_back_to_the_index(self) -> None:
        """An index that carries dates is already an answer."""
        company_id = self.stocked()
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            "<html><body><h1>Ein Beitrag</h1></body></html>",
        )
        result = self.extract(company_id)
        self.assertEqual(str(result.signals["content.blog_last_post"]), "2024-02-01")

    def test_neither_dated_writes_no_date(self) -> None:
        company_id = self.stocked(
            index_html='<html><body><a href="/blogs/news/x">Ein Beitrag</a></body></html>'
        )
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/x",
            "<html></html>",
        )
        result = self.extract(company_id)
        self.assertNotIn("content.blog_last_post", result.signals)
        self.assertIn("no parseable post date", " ".join(result.notes))

    def test_a_sample_newer_than_the_index_is_not_bounded_by_it(self) -> None:
        """M1.40, found by running the third crawl. The basis describes **the
        date that was written**, not which sources produced one.

        `zecplus.de` is the case: the index's newest date was 2021-03-10, the
        sampled article was 2025-09-03, the article won the maximum — and the
        basis said `both`, which §6.2 reads as "the index bounds this from
        above". An index that failed to date the newest post we are holding
        bounds nothing, and `opp.blog_slowing` took +10 on that. M1.32's defect,
        arriving through the basis instead of through its absence.
        """
        company_id = self.stocked()  # index dates 2024-02-01
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            BLOG_ARTICLE,  # 2025-06-30 — newer than anything the index dates
        )
        result = self.extract(company_id)
        self.assertEqual(str(result.signals["content.blog_last_post"]), "2025-06-30")
        self.assertEqual(result.signals["content.blog_last_post_basis"], "article")
        self.assertIn("does not bound it", " ".join(result.notes))

    def test_two_sources_record_a_basis_of_both_when_the_index_wins(self) -> None:
        """§6.2's interim guard needs to know what the date rests on, so the
        basis travels with it the way A5's tier travels with the count. Where
        the index's own maximum *is* the value written, it bounds it — and the
        sample agreeing changes nothing about that."""
        company_id = self.stocked()  # index dates 2024-02-01
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            '<html><body><time datetime="2023-05-04">4. Mai 2023</time></body></html>',
        )
        result = self.extract(company_id)
        self.assertEqual(str(result.signals["content.blog_last_post"]), "2024-02-01")
        self.assertEqual(result.signals["content.blog_last_post_basis"], "both")

    def test_an_index_date_alone_records_a_basis_of_index(self) -> None:
        result = self.extract(self.stocked())
        self.assertEqual(result.signals["content.blog_last_post_basis"], "index")

    def test_a_sample_only_date_is_marked_as_such(self) -> None:
        """The whole point of the guard. An index with no date at all leaves
        the sampled article's date a lower bound with **no maximum behind
        it** — it can show a blog is at least this fresh and can never show one
        is stale, so §6.2 must not let it carry `opp.blog_stale` (+20) or
        `opp.blog_slowing` (+10). Observed on doonails.de and snocks.com."""
        company_id = self.stocked(
            index_html='<html><body><a href="/blogs/news/erster">Ein Beitrag</a></body></html>'
        )
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/erster",
            BLOG_ARTICLE,  # 2025-06-30
        )
        result = self.extract(company_id)

        self.assertEqual(str(result.signals["content.blog_last_post"]), "2025-06-30")
        self.assertEqual(result.signals["content.blog_last_post_basis"], "article")
        self.assertIn("sample-only", " ".join(result.notes))

    def test_no_date_records_no_basis(self) -> None:
        """The basis is an enabling fact, never a suppressing one: absent a
        date there is nothing to qualify, and absent the signal §6.2 fires in
        neither direction — which is what makes a pre-guard run safe."""
        company_id = self.stocked(
            index_html='<html><body><a href="/blogs/news/x">Ein Beitrag</a></body></html>'
        )
        self.artifact(
            company_id,
            "blog_article",
            "https://muster.de/blogs/news/x",
            "<html></html>",
        )
        result = self.extract(company_id)
        self.assertNotIn("content.blog_last_post_basis", result.signals)

    def test_no_blog_index_writes_zero_and_qualifies_it(self) -> None:
        """M1.14: the `0` is written and its licence to fire +25 is written with
        it. A homepage with no links and no sitemap is a search that ran neither
        instrument, so `opp.no_blog` must not read this `0` as an absence."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 0)
        self.assertEqual(result.signals["content.blog_search_exhaustive"], 0)
        self.assertIn(
            "limit: no sitemap and no homepage links",
            self.signal_text(company_id, "content.blog_search_exhaustive"),
        )

    def test_both_instruments_running_licenses_the_award(self) -> None:
        """A sitemap enumerated *and* a homepage that yielded links. Only then
        may §6.2 read `blog_exists = 0` as an absence and fire +25."""
        company_id = self.company()
        self.artifact(
            company_id,
            "homepage",
            "https://muster.de/",
            '<html><body><a href="/kontakt">Kontakt</a></body></html>',
        )
        self.artifact(
            company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP_ROOT_SLUGS
        )
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 0)
        self.assertEqual(result.signals["content.blog_search_exhaustive"], 1)

    def test_a_sitemap_alone_is_not_an_exhaustive_search(self) -> None:
        """§5 of the M1.14 read proposed "did we have a sitemap to search" and
        the counter-example was already in the corpus: `zecplus.de` serves four
        shards and its blog is on a host none of them names. A sitemap makes one
        instrument available; it does not make the search complete."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP_ROOT_SLUGS
        )
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 0)
        self.assertEqual(result.signals["content.blog_search_exhaustive"], 0)
        self.assertEqual(
            self.signal_text(company_id, "content.blog_search_exhaustive"),
            "limit: no homepage links",
        )

    def test_a_located_blog_whose_index_failed_writes_no_zero(self) -> None:
        """A7's transient half. The blog was found and the fetch missed it — a
        `0` here would award +25 against a shop whose blog we had in hand. The
        reason is written so the abstention is visible per company, and it
        retries next run rather than filling the queue on the first miss."""
        company_id = self.company()
        self.artifact(
            company_id,
            "homepage",
            "https://muster.de/",
            '<html><body><a href="https://blog.muster.de/">Blog</a></body></html>',
        )
        self.artifact(
            company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP_ROOT_SLUGS
        )
        result = self.extract(company_id)

        self.assertNotIn("content.blog_exists", result.signals)
        self.assertEqual(result.signals["content.blog_search_exhaustive"], 0)
        self.assertTrue(
            self.signal_text(company_id, "content.blog_search_exhaustive").startswith(
                "transient:"
            )
        )

    def test_a_blog_on_a_subdomain_is_detected_and_dated(self) -> None:
        """M1.14 end to end on `zecplus.de`'s shape: the +25 does not fire, and
        A6 samples the blog like any other."""
        company_id = self.company()
        self.artifact(
            company_id,
            "homepage",
            "https://muster.de/",
            '<html><body><a href="https://blog.muster.de/">Blog</a></body></html>',
        )
        self.artifact(company_id, "blog_index", "https://blog.muster.de/", BLOG_INDEX)
        self.artifact(
            company_id, "blog_article", "https://blog.muster.de/erster", BLOG_ARTICLE
        )
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 1)
        self.assertEqual(str(result.signals["content.blog_last_post"]), "2025-06-30")
        self.assertNotIn("content.blog_search_exhaustive", result.signals)

    def test_a_blog_on_a_subdomain_has_no_countable_posts(self) -> None:
        """No path prefix separates its posts from its navigation, so counting
        every same-host link would write a number made of menus. `None` means
        not counted, and §6.2 reads that as unknown rather than as few."""
        company_id = self.company()
        self.artifact(
            company_id,
            "homepage",
            "https://muster.de/",
            '<html><body><a href="https://blog.muster.de/">Blog</a></body></html>',
        )
        self.artifact(company_id, "blog_index", "https://blog.muster.de/", BLOG_INDEX)
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 1)
        self.assertNotIn("content.blog_post_count", result.signals)

    def test_an_unparseable_date_is_left_unwritten(self) -> None:
        """§6.2's NULL branch: an undated blog is an unknown, not a stale one."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP)
        self.artifact(
            company_id,
            "blog_index",
            "https://muster.de/blogs/news",
            '<html><body><a href="/blogs/news/x">Ein Beitrag</a></body></html>',
        )
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 1)
        self.assertNotIn("content.blog_last_post", result.signals)
        self.assertIn("no parseable post date", " ".join(result.notes))


class TestMovedDomain(ExtractTestCase):
    """M1.18 reaches this stage too."""

    def test_the_adopted_host_is_used_for_same_site_tests(self) -> None:
        """Anchored on the seeded domain instead, a moved shop's whole
        catalogue reads as off-site — which is exactly what happened to
        doonails.de's 1,319 URLs on the first run of this stage."""
        company_id = self.company(domain="alt.de", site_domain="neu.de")
        self.artifact(
            company_id, "homepage", "https://neu.de/", "<html></html>", domain="alt.de"
        )
        self.artifact(
            company_id,
            "sitemap",
            "https://neu.de/sitemap.xml",
            SITEMAP.replace("muster.de", "neu.de"),
            domain="alt.de",
        )
        result = self.extract(company_id, domain="alt.de")
        self.assertEqual(result.signals["catalog.product_url_count"], 2)


class TestStageContract(ExtractTestCase):
    def test_no_homepage_extracts_nothing(self) -> None:
        company_id = self.company()
        self.assertEqual(self.extract(company_id).signals, {})

    def test_legal_form_lands_on_the_company_row(self) -> None:
        """`company_profile` reads `legal_form` off the company row, not from a
        signal, so that is where it has to be written."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id,
            "impressum",
            "https://muster.de/impressum",
            "<html><body><p>Impressum Muster Handel GmbH Musterstraße 1 "
            "50667 Musterstadt</p></body></html>",
        )
        self.extract(company_id)
        row = self.conn.execute(
            "SELECT legal_form FROM company WHERE id = ?", (company_id,)
        ).fetchone()
        self.assertEqual(row["legal_form"], "GmbH")

    def test_re_running_does_not_duplicate_signals_within_a_run(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        stage = extract.ExtractStage(
            self.conn,
            extract.ArtifactStore(self.artifacts),
            run_id=1,
            today=date(2026, 8, 15),
        )
        stage.run_company(company_id, "muster.de")
        stage.run_company(company_id, "muster.de")
        count = self.conn.execute(
            "SELECT COUNT(*) c FROM signal WHERE company_id = ? AND key = 'reviews.trusted_shops'",
            (company_id,),
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_every_signal_is_evidenced_and_deterministic(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP)
        self.extract(company_id)
        for key, row in self.signals(company_id).items():
            with self.subTest(signal=key):
                self.assertEqual(row["method"], "deterministic")
                self.assertIsNone(row["confidence"])
                self.assertIsNotNone(row["evidence_url"])


#: Sitemap index → two shards, one of which holds the products. The index is the
#: **first** artifact row, so it is what `sitemaps[0]` used to cite.
SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://muster.de/sitemap_pages_1.xml</loc></sitemap>
  <sitemap><loc>https://muster.de/sitemap_products_1.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP_PAGES = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://muster.de/ueber-uns</loc></url>
</urlset>"""

SITEMAP_PRODUCTS = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://muster.de/products/alpha</loc></url>
  <url><loc>https://muster.de/products/beta</loc></url>
  <url><loc>https://muster.de/products/gamma</loc></url>
</urlset>"""


class TestSignalProvenance(ExtractTestCase):
    """M1.42. **A provenance field must be produced by the code path that
    produced the value it describes**, which is the general form of M1.40.

    `content.blog_last_post_basis` was a claim *about* a value, computed by a
    different expression than the value, so the two could disagree — and did, on
    3 of 13. The audit found the same shape in `evidence_url`, where it matters
    more: §1's guarantee is that every number traces to a stored artifact and
    §8's export asserts on it, so a desynced citation puts the wrong page in a
    letter as proof.
    """

    def _index_and_shards(self, company_id: int) -> None:
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP_INDEX
        )
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/sitemap_pages_1.xml",
            SITEMAP_PAGES,
        )
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/sitemap_products_1.xml",
            SITEMAP_PRODUCTS,
        )

    def test_every_signal_names_a_stored_artifact_of_this_company(self) -> None:
        """The invariant, stated once. No empty string, no synthesised URL, and
        `artifact_id` agreeing with `evidence_url` on every row."""
        company_id = self.company()
        self._index_and_shards(company_id)
        self.artifact(
            company_id, "blog_index", "https://muster.de/blogs/news", BLOG_INDEX
        )
        self.artifact(
            company_id, "product_page", "https://muster.de/products/alpha", PRODUCT_PAGE
        )
        self.extract(company_id)

        rows = self.conn.execute(
            "SELECT s.key, s.evidence_url, s.artifact_id, a.url AS artifact_url "
            "FROM signal s LEFT JOIN artifact a "
            "  ON a.id = s.artifact_id AND a.company_id = s.company_id "
            "WHERE s.company_id = ?",
            (company_id,),
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(signal=row["key"]):
                self.assertNotEqual(row["evidence_url"], "")
                self.assertIsNotNone(row["artifact_id"])
                # The two columns are one fact. They can only disagree if they
                # were computed by two expressions, which is the bug.
                self.assertEqual(row["artifact_url"], row["evidence_url"])

    def test_catalogue_count_cites_the_shard_it_counted_not_the_index(self) -> None:
        """The corpus failure: 8 of 8 shops cited a document holding **zero** of
        the URLs counted, because the citation was `sitemaps[0]` — the index."""
        company_id = self.company()
        self._index_and_shards(company_id)
        self.extract(company_id)

        row = self.signals(company_id)["catalog.product_url_count"]
        self.assertEqual(row["value_num"], 3)
        self.assertEqual(
            row["evidence_url"], "https://muster.de/sitemap_products_1.xml"
        )
        # And not merely "some shard": the cited document must actually contain
        # the URLs. That is the property the old citation failed.
        body = (self.artifacts / "muster.de").glob("sitemap-*.xml")
        cited = next(
            path for path in body if "products/gamma" in path.read_text("utf-8")
        )
        self.assertIn("products/gamma", cited.read_text("utf-8"))

    def test_not_measurable_cites_the_largest_shard_searched(self) -> None:
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id, "sitemap", "https://muster.de/sitemap.xml", SITEMAP_INDEX
        )
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/sitemap_pages_1.xml",
            SITEMAP_ROOT_SLUGS,
        )
        self.extract(company_id)

        row = self.signals(company_id)["catalog.not_measurable"]
        self.assertEqual(row["evidence_url"], "https://muster.de/sitemap_pages_1.xml")
        # The claim is about every shard, so the extent is recorded with it —
        # citing the index said nothing about whether 3 URLs were searched or
        # 3,000.
        self.assertIn("3 URLs across 2 sitemap shards", row["value_text"])

    def test_blog_absence_signals_cite_the_homepage_not_an_empty_string(self) -> None:
        """370 rows carried `evidence_url = ''`, which the implementation brief
        forbids. For the blog-absence signals there was always a real citation
        available: both §5.3 instruments read the homepage."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id,
            "sitemap",
            "https://muster.de/sitemap_pages_1.xml",
            SITEMAP_PAGES,
        )
        self.extract(company_id)

        signals = self.signals(company_id)
        for key in ("content.blog_exists", "content.blog_search_exhaustive"):
            with self.subTest(signal=key):
                self.assertEqual(signals[key]["evidence_url"], "https://muster.de/")
                self.assertIsNotNone(signals[key]["artifact_id"])
        self.assertEqual(signals["content.blog_exists"]["value_num"], 0)

    def test_product_schema_cites_the_page_that_carries_the_markup(self) -> None:
        """Latent in the corpus, fatal in a letter: the value is decided by
        either of two pages and the citation followed only one of them."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", PRODUCT_PAGE)
        self.artifact(
            company_id,
            "product_page",
            "https://muster.de/products/alpha",
            "<html><body>kein Markup</body></html>",
        )
        result = self.extract(company_id)

        row = self.signals(company_id)["schema.product_present"]
        self.assertEqual(row["value_num"], 1)
        self.assertEqual(row["evidence_url"], "https://muster.de/")
        self.assertTrue(any("on the homepage" in note for note in result.notes))

    def test_absent_markup_still_cites_the_product_page(self) -> None:
        """A `0` is a statement about the sampled product page (A5.5), so it
        cites that page even though the homepage was read too."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        self.artifact(
            company_id,
            "product_page",
            "https://muster.de/products/alpha",
            "<html><body>kein Markup</body></html>",
        )
        self.extract(company_id)

        row = self.signals(company_id)["schema.product_present"]
        self.assertEqual(row["value_num"], 0)
        self.assertEqual(row["evidence_url"], "https://muster.de/products/alpha")


if __name__ == "__main__":
    unittest.main()
