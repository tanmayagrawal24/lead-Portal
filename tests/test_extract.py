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

    def test_no_blog_index_writes_zero_and_says_why(self) -> None:
        """§10.1: this instrument under-detects — a blog on a subdomain or on
        root-level slugs is invisible to it — so the note records that the `0`
        is vocabulary-limited and §6.2 must not let it carry +25 alone."""
        company_id = self.company()
        self.artifact(company_id, "homepage", "https://muster.de/", "<html></html>")
        result = self.extract(company_id)

        self.assertEqual(result.signals["content.blog_exists"], 0)
        self.assertIn("vocabulary-limited", " ".join(result.notes))

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


if __name__ == "__main__":
    unittest.main()
