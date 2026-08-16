"""Unit tests for the pure helpers: URL normalisation, robots policy, sitemaps."""

from __future__ import annotations

import gzip
import unittest
from pathlib import Path

from portal import impressum, robots, sitemap, urls


class TestNormaliseDomain(unittest.TestCase):
    def test_strips_scheme_www_path_and_case(self) -> None:
        for raw in (
            "https://WWW.Example.de/shop?a=1",
            "example.de",
            "http://example.de/",
            "  Example.DE  ",
            "www.example.de",
            "example.de.",
        ):
            self.assertEqual(urls.normalise_domain(raw), "example.de", raw)

    def test_keeps_non_www_subdomains(self) -> None:
        self.assertEqual(
            urls.normalise_domain("https://shop.example.de/"), "shop.example.de"
        )

    def test_rejects_unusable_input(self) -> None:
        for raw in ("", "   ", "localhost", "https://"):
            with self.assertRaises(ValueError, msg=raw):
                urls.normalise_domain(raw)


class TestSameSite(unittest.TestCase):
    def test_matches_domain_and_subdomains_and_www(self) -> None:
        self.assertTrue(urls.same_site("https://example.de/x", "example.de"))
        self.assertTrue(urls.same_site("https://www.example.de/x", "example.de"))
        self.assertTrue(urls.same_site("https://shop.example.de/x", "example.de"))

    def test_rejects_other_hosts_including_lookalikes(self) -> None:
        self.assertFalse(urls.same_site("https://example.com/x", "example.de"))
        self.assertFalse(urls.same_site("https://notexample.de/x", "example.de"))

    def test_ignores_port(self) -> None:
        self.assertTrue(urls.same_site("http://127.0.0.1:8123/x", "127.0.0.1"))

    def test_the_apex_www_redirect_shape_in_both_directions(self) -> None:
        """The redirect nearly every shop has. `same_site` is a pure string
        function, so this shape needs no fixture server to pin — only the
        end-to-end loop through it does."""
        self.assertTrue(urls.same_site("https://www.example.de/", "example.de"))
        # And the reverse: seeded as www, redirected to the apex. Seeds are
        # normalised www-off, so the domain side is `example.de` either way.
        self.assertTrue(urls.same_site("https://example.de/", "example.de"))
        self.assertEqual(urls.normalise_domain("https://www.example.de/"), "example.de")

    def test_a_deeper_subdomain_is_still_the_same_site(self) -> None:
        self.assertTrue(urls.same_site("https://shop.eu.example.de/x", "example.de"))
        self.assertTrue(urls.same_site("https://www.shop.example.de/x", "example.de"))

    def test_a_lookalike_prefix_is_not_the_same_site(self) -> None:
        """`notexample.de` shares a suffix with `example.de` as raw text; the
        dot in the comparison is what stops it being a match."""
        for host in ("notexample.de", "myexample.de", "example.de.x"):
            self.assertFalse(urls.same_site(f"https://{host}/x", "example.de"), host)

    def test_a_suffix_attack_domain_is_not_the_same_site(self) -> None:
        """`example.de.evil.com` ends with the seeded domain as a *label*, which
        is exactly the shape an attacker uses to look first-party. It must not
        match: the test is that the domain is a suffix, not that it appears."""
        for host in (
            "example.de.evil.com",
            "www.example.de.evil.com",
            "evil.com/?x=example.de",
        ):
            self.assertFalse(urls.same_site(f"https://{host}", "example.de"), host)


class TestHostOf(unittest.TestCase):
    """The politeness key. Distinct from `authority_of` on exactly one point."""

    def test_apex_and_www_share_one_key(self) -> None:
        self.assertEqual(
            urls.host_of("https://example.de/a"),
            urls.host_of("https://www.example.de/b"),
        )

    def test_but_they_remain_separate_authorities(self) -> None:
        """robots.txt is keyed to the origin, so the two are not interchangeable
        there — that is why there are two functions."""
        self.assertNotEqual(
            urls.authority_of("https://example.de/a"),
            urls.authority_of("https://www.example.de/b"),
        )

    def test_the_port_still_separates_budgets(self) -> None:
        self.assertNotEqual(
            urls.host_of("http://example.de:8001/"),
            urls.host_of("http://example.de:8002/"),
        )

    def test_other_subdomains_stay_separate(self) -> None:
        """§5.2 records this as accepted: `shop.example.de` is commonly a
        different machine, and merging budgets would slow honest crawling."""
        self.assertNotEqual(
            urls.host_of("https://shop.example.de/"),
            urls.host_of("https://example.de/"),
        )

    def test_case_and_userinfo_do_not_split_a_budget(self) -> None:
        self.assertEqual(urls.host_of("https://WWW.Example.DE/x"), "example.de")
        self.assertEqual(urls.host_of("https://user@www.example.de/x"), "example.de")


class TestAbsolutise(unittest.TestCase):
    def test_resolves_and_drops_fragments(self) -> None:
        self.assertEqual(
            urls.absolutise("https://example.de/a/b", "../c#frag"),
            "https://example.de/c",
        )

    def test_rejects_non_http_schemes(self) -> None:
        for href in ("mailto:x@y.de", "tel:+49", "javascript:void(0)", "#top", ""):
            self.assertIsNone(urls.absolutise("https://example.de/", href), href)


class TestRobotsPolicy(unittest.TestCase):
    """§5.2: exclusion applies only when the paths the tool needs are blocked."""

    def test_no_robots_allows_everything(self) -> None:
        policy = robots.parse(None)
        self.assertTrue(policy.allows("https://example.de/anything"))
        self.assertIsNone(policy.blocks_required_paths("https://example.de"))

    def test_irrelevant_disallow_is_not_a_refusal(self) -> None:
        policy = robots.parse(
            "User-agent: *\nDisallow: /checkout/\nDisallow: /account/\n"
        )
        self.assertIsNone(policy.blocks_required_paths("https://example.de"))
        self.assertTrue(policy.allows("https://example.de/"))
        self.assertFalse(policy.allows("https://example.de/checkout/cart"))

    def test_disallowing_root_excludes(self) -> None:
        policy = robots.parse("User-agent: *\nDisallow: /\n")
        reason = policy.blocks_required_paths("https://example.de")
        self.assertIsNotNone(reason)
        self.assertIn("robots_disallowed", reason or "")

    def test_disallowing_sitemap_excludes(self) -> None:
        policy = robots.parse("User-agent: *\nDisallow: /sitemap.xml\n")
        self.assertIn(
            "sitemap", policy.blocks_required_paths("https://example.de") or ""
        )

    def test_disallowing_every_impressum_path_excludes(self) -> None:
        rules = "\n".join(f"Disallow: {p}" for p in robots.IMPRESSUM_PROBE_PATHS)
        policy = robots.parse(f"User-agent: *\n{rules}\n")
        self.assertIn(
            "Impressum", policy.blocks_required_paths("https://example.de") or ""
        )

    def test_disallowing_one_impressum_path_does_not_exclude(self) -> None:
        policy = robots.parse("User-agent: *\nDisallow: /legal\n")
        self.assertIsNone(policy.blocks_required_paths("https://example.de"))

    def test_rule_targeting_our_agent_is_honoured(self) -> None:
        policy = robots.parse(f"User-agent: {robots.USER_AGENT_TOKEN}\nDisallow: /\n")
        self.assertIsNotNone(policy.blocks_required_paths("https://example.de"))

    def test_malformed_robots_does_not_abort(self) -> None:
        self.assertTrue(
            robots.parse("\x00\x01 not robots at all").allows("https://example.de/")
        )

    def test_crawl_delay_is_read_for_our_agent(self) -> None:
        policy = robots.parse(
            f"User-agent: {robots.USER_AGENT_TOKEN}\nCrawl-delay: 5\nDisallow:\n\n"
            "User-agent: *\nCrawl-delay: 2\n"
        )
        self.assertEqual(policy.crawl_delay(), 5.0)

    def test_crawl_delay_falls_back_to_the_wildcard_group(self) -> None:
        policy = robots.parse("User-agent: *\nCrawl-delay: 4\nAllow: /\n")
        self.assertEqual(policy.crawl_delay(), 4.0)

    def test_no_crawl_delay_stated_is_none(self) -> None:
        self.assertIsNone(robots.parse("User-agent: *\nAllow: /\n").crawl_delay())
        self.assertIsNone(robots.parse(None).crawl_delay())

    def test_sitemap_directives_are_extracted(self) -> None:
        policy = robots.parse(
            "User-agent: *\nAllow: /\n"
            "Sitemap: https://example.de/sitemap_index.xml\n"
            "sitemap:  https://example.de/sitemap-products.xml \n"
        )
        self.assertEqual(
            robots.sitemap_urls(policy),
            [
                "https://example.de/sitemap_index.xml",
                "https://example.de/sitemap-products.xml",
            ],
        )


URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.de/detail/b-product</loc></url>
  <url><loc>https://example.de/detail/a-product</loc></url>
</urlset>"""

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.de/sitemap-www-product-1.xml.gz</loc></sitemap>
  <sitemap><loc>https://example.de/sitemap-www-content-1.xml.gz</loc></sitemap>
</sitemapindex>"""


class TestSitemapParsing(unittest.TestCase):
    def test_urlset_yields_pages(self) -> None:
        children, pages = sitemap.parse(
            URLSET.encode(), "https://example.de/sitemap.xml"
        )
        self.assertEqual(children, [])
        self.assertEqual(
            pages,
            [
                "https://example.de/detail/b-product",
                "https://example.de/detail/a-product",
            ],
        )

    def test_index_yields_children(self) -> None:
        children, pages = sitemap.parse(
            INDEX.encode(), "https://example.de/sitemap.xml"
        )
        self.assertEqual(len(children), 2)
        self.assertEqual(pages, [])

    def test_gzipped_shard_is_decompressed(self) -> None:
        body = gzip.compress(URLSET.encode())
        _children, pages = sitemap.parse(
            body, "https://example.de/sitemap-product-1.xml.gz"
        )
        self.assertEqual(len(pages), 2)

    def test_gzip_detected_by_magic_bytes_without_extension(self) -> None:
        body = gzip.compress(URLSET.encode())
        _children, pages = sitemap.parse(
            body, "https://example.de/sitemap-product-1.xml"
        )
        self.assertEqual(len(pages), 2)

    def test_unparseable_sitemap_yields_nothing_rather_than_raising(self) -> None:
        self.assertEqual(
            sitemap.parse(b"<not xml", "https://example.de/s.xml"), ([], [])
        )

    def test_a_sitemap_declaring_a_dtd_is_refused_unparsed(self) -> None:
        """The billion-laughs shape. Refusing the document is what removes the
        entity-expansion class without taking on `defusedxml`."""
        bomb = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE urlset [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>\n'
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://example.de/detail/a&lol2;</loc></url></urlset>"
        )
        self.assertEqual(sitemap.parse(bomb, "https://example.de/s.xml"), ([], []))

    def test_the_dtd_refusal_survives_gzip_and_odd_spacing(self) -> None:
        """A `.xml.gz` shard is decompressed before the check, or the check
        would read compressed bytes and pass everything."""
        doctype = (
            b'<!doctype   urlset SYSTEM "x.dtd">'
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://example.de/detail/a</loc></url></urlset>"
        )
        self.assertEqual(
            sitemap.parse(gzip.compress(doctype), "https://example.de/s.xml.gz"),
            ([], []),
        )

    def test_an_ordinary_sitemap_is_not_caught_by_the_dtd_refusal(self) -> None:
        _children, pages = sitemap.parse(URLSET.encode(), "https://example.de/s.xml")
        self.assertEqual(len(pages), 2)

    def test_product_sitemap_detection_per_platform(self) -> None:
        for url in (
            "https://example.de/sitemap-www.example.de-product-1.xml.gz",  # Shopware
            "https://example.de/sitemap_products_1.xml",  # Shopify
            "https://example.de/product-sitemap1.xml",  # WooCommerce
            "https://example.de/sitemap/product/1",  # JTL
        ):
            self.assertTrue(sitemap.is_product_sitemap(url), url)

    def test_a_query_string_does_not_defeat_detection(self) -> None:
        """M1.13, and the exact URLs that made Tier 1 dead code.

        Shopify serves its product sitemap with `?from=…&to=…`, so the `.xml`
        the patterns anchor on with `$` is not at the end of the URL. Matching
        the whole URL meant zero of 143 real sitemaps were recognised in the
        first crawl, on all seven Shopify shops.
        """
        for url in (
            "https://snocks.com/sitemap_products_1.xml?from=1932497715270&to=110104",
            "https://ekomia.de/de-at/sitemap_products_1.xml?from=199692405&to=1215",
            "https://example.de/product-sitemap1.xml?paged=2",
        ):
            self.assertTrue(sitemap.is_product_sitemap(url), url)

    def test_content_sitemaps_are_not_product_sitemaps(self) -> None:
        for url in (
            "https://example.de/sitemap-www.example.de-content-1.xml.gz",
            "https://example.de/sitemap.xml",
            "https://example.de/post-sitemap.xml",
            # Shopify's sibling shards, which carry the same query shape.
            "https://example.de/sitemap_collections_1.xml?from=1&to=2",
            "https://example.de/sitemap_blogs_1.xml",
            # The query is addressing, never identity: matching it would let any
            # sitemap claim to be a product sitemap.
            "https://example.de/sitemap.xml?products=1",
            "https://example.de/sitemap.xml?f=/sitemap_products_1.xml",
        ):
            self.assertFalse(sitemap.is_product_sitemap(url), url)


class TestShardSemantics(unittest.TestCase):
    """M1.24. A sitemap index is a labelled table of contents; read the labels.

    The convention list this backs up answers "is this Shopify's product
    sitemap?" and cannot answer "what did this shop call its catalogue?".
    `smile-store.de` calls it `articles`, published all 194 of its products
    there, and was counted at **6** — from a `/detail/` pattern scraping
    stragglers off the rest of the site while the shard sat unread.
    """

    def test_a_semantically_named_shard_is_a_product_sitemap(self) -> None:
        """Observed on one shop (Shopware 5 + PixupSitemap), per M1.9."""
        self.assertTrue(
            sitemap.is_product_sitemap(
                "https://www.smile-store.de/PixupSitemap/sitemap/area/articles-0-sitemap.xml"
            )
        )

    def test_the_german_forms_are_carried_but_unobserved(self) -> None:
        """`artikel` and `produkte` are in the vocabulary and no shop in the
        corpus serves either. The test pins the behaviour; the docstring is the
        only place that records the evidence is missing."""
        for url in (
            "https://example.de/sitemap/artikel-0.xml",
            "https://example.de/produkte-sitemap.xml",
        ):
            self.assertTrue(sitemap.is_product_sitemap(url), url)

    def test_a_blog_shard_is_content_and_not_catalogue(self) -> None:
        for url in (
            "https://www.smile-store.de/PixupSitemap/sitemap/area/blogs-0-sitemap.xml",
            "https://example.de/magazin-sitemap.xml",
            "https://example.de/sitemap-ratgeber-1.xml",
        ):
            self.assertTrue(sitemap.is_blog_sitemap(url), url)
            self.assertFalse(sitemap.is_product_sitemap(url), url)

    def test_the_blog_reading_wins_where_both_words_appear(self) -> None:
        """`article` is a product in German commerce and a post in English CMS
        usage. Where a shard says both, the content reading is taken."""
        self.assertEqual(
            sitemap.shard_kind("https://example.de/blog-articles-0-sitemap.xml"), "blog"
        )

    def test_the_siblings_of_a_product_shard_are_not_products(self) -> None:
        """Every one of these sits beside `articles-0-sitemap.xml` in a real
        index, and counting any of them would inflate the catalogue."""
        for url in (
            "https://www.smile-store.de/PixupSitemap/sitemap/area/categories-0-sitemap.xml",
            "https://www.smile-store.de/PixupSitemap/sitemap/area/customPages-0-sitemap.xml",
            "https://www.smile-store.de/PixupSitemap/sitemap/area/suppliers-0-sitemap.xml",
            "https://www.smile-store.de/PixupSitemap/sitemap/area/pictures-0-sitemap.xml",
            "https://www.smile-store.de/PixupSitemap/sitemap/area/landingPages-0-sitemap.xml",
            "https://verpackungskoenig.de/export/sitemap_0.xml.gz",
        ):
            self.assertIsNone(sitemap.shard_kind(url), url)
            self.assertFalse(sitemap.is_product_sitemap(url), url)

    def test_scaffolding_words_carry_no_meaning(self) -> None:
        self.assertEqual(
            sitemap.shard_words("https://example.de/area/sitemap_index.xml.gz"),
            frozenset(),
        )


class TestHelpersAgainstClassify(unittest.TestCase):
    """M1.55 — do the zero-caller helpers still agree with the live path?

    The external audit found `is_product_sitemap` and `is_blog_sitemap` have no
    production callers: `classify` (M1.27) is what `fetch` and `extract` use, and
    the helpers survive only in the assertions above. The question asked before
    deleting them was not *should they go* but **do those assertions still agree
    with the code that runs** — a green suite defending an obsolete definition of
    "product sitemap" is this project's signature failure wearing a disguise.

    Measured: **25 of 26 URLs agree; one disagrees.** This class is that
    measurement, committed rather than transcribed (M1.48's standing rule), so
    the disagreement cannot quietly widen or quietly heal while M1.55 is open.
    Nothing is deleted until it is ruled on.
    """

    #: A single shard with no page URLs and no blog path: the closest analogue
    #: of the name-only judgement the helpers make. `classify`'s index-level
    #: evidence rules cannot apply to one shard, which is the point — that is
    #: exactly the information the helpers never had either.
    @staticmethod
    def solo(url: str) -> str | None:
        return sitemap.classify([(url, [])])[url]

    def test_every_convention_but_one_is_subsumed_by_the_word_reading(self) -> None:
        """Patterns 1–3 of `_PRODUCT_SITEMAP_PATTERNS` add nothing `shard_kind`
        does not already reach: the platform filename carries the word."""
        for url in (
            "https://example.de/sitemap-www.example.de-product-1.xml.gz",  # Shopware
            "https://example.de/sitemap_products_1.xml",  # Shopify
            "https://example.de/product-sitemap1.xml",  # WooCommerce
            "https://snocks.com/sitemap_products_1.xml?from=1932497715270&to=110104",
            "https://www.smile-store.de/PixupSitemap/sitemap/area/articles-0-sitemap.xml",
        ):
            self.assertTrue(sitemap.is_product_sitemap(url), url)
            self.assertEqual(self.solo(url), "product", url)

    def test_the_negatives_agree_everywhere(self) -> None:
        for url in (
            "https://example.de/sitemap-www.example.de-content-1.xml.gz",
            "https://example.de/sitemap.xml",
            "https://example.de/sitemap_collections_1.xml?from=1&to=2",
            "https://example.de/sitemap.xml?products=1",
            "https://www.smile-store.de/PixupSitemap/sitemap/area/categories-0-sitemap.xml",
            "https://verpackungskoenig.de/export/sitemap_0.xml.gz",
        ):
            self.assertFalse(sitemap.is_product_sitemap(url), url)
            self.assertNotEqual(self.solo(url), "product", url)

    def test_the_blog_verdicts_agree_everywhere(self) -> None:
        for url in (
            "https://www.smile-store.de/PixupSitemap/sitemap/area/blogs-0-sitemap.xml",
            "https://example.de/magazin-sitemap.xml",
            "https://example.de/sitemap-ratgeber-1.xml",
        ):
            self.assertTrue(sitemap.is_blog_sitemap(url), url)
            self.assertEqual(self.solo(url), "blog", url)

    def test_the_one_disagreement_is_the_jtl_path_form(self) -> None:
        """**M1.55, unruled.** `/sitemap/product/N` names its contents in a
        *parent* path segment, and `shard_words` reads only the last one — so
        the helper says product (via the convention list) and the live path says
        nothing at all.

        Two facts decide how much this matters, and both are measured elsewhere:
        the shape appears on **0 of 307** stored sitemap artifacts, and the four
        JTL shops in the corpus serve `/export/sitemap_0.xml.gz` instead — an
        undifferentiated shard that names nothing, which is a different problem
        (§10.3). So this is §10.4's case: a pattern carried forward on
        convention rather than observation. It is pinned here, not fixed and not
        deleted, because the direction of the error is the safe one — `None`
        withholds a count, and §10.3's three-state rule then reports *not
        measurable* rather than a wrong number (M1.4).
        """
        url = "https://example.de/sitemap/product/1"
        self.assertTrue(sitemap.is_product_sitemap(url))
        self.assertIsNone(self.solo(url))
        self.assertEqual(sitemap.shard_words(url), frozenset())

    def test_the_helpers_still_have_no_production_callers(self) -> None:
        """Pins the audit's own finding, so "dead" cannot silently become "live"
        without someone reading this test."""
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(Path(sitemap.__file__).parent.glob("*.py"))
            if path.name != "sitemap.py"
        )
        self.assertNotIn("is_product_sitemap", source)
        self.assertNotIn("is_blog_sitemap", source)


class TestShardClassificationByContent(unittest.TestCase):
    """M1.27. `article` is a product in German commerce and a post in English
    CMS usage, so the word is not asked to decide — the index is."""

    ARTICLES = "https://muster.de/area/articles-0-sitemap.xml"
    BLOGS = "https://muster.de/area/blogs-0-sitemap.xml"

    def test_an_index_naming_both_says_articles_are_products(self) -> None:
        """The smile-store.de shape: a shop listing `articles-*` and `blogs-*`
        has told us which one holds its posts."""
        kinds = sitemap.classify(
            [
                (self.ARTICLES, ["https://muster.de/stoebern/marke/produkt"]),
                (self.BLOGS, ["https://muster.de/magazin/ein-beitrag"]),
            ]
        )
        self.assertEqual(kinds[self.ARTICLES], "product")
        self.assertEqual(kinds[self.BLOGS], "blog")

    def test_a_lone_articles_shard_under_the_blog_path_is_content(self) -> None:
        kinds = sitemap.classify(
            [
                (
                    self.ARTICLES,
                    ["https://muster.de/blog/erster", "https://muster.de/blog/zweiter"],
                )
            ],
            blog_path="/blog",
        )
        self.assertEqual(kinds[self.ARTICLES], "blog")

    def test_a_shard_republishing_the_blogs_urls_is_content(self) -> None:
        """Strongest of the three, and it overrides the sibling reading: whatever
        it is called, a shard holding the blog's own URLs is the blog."""
        posts = ["https://muster.de/magazin/a", "https://muster.de/magazin/b"]
        kinds = sitemap.classify([(self.ARTICLES, posts), (self.BLOGS, posts)])
        self.assertEqual(kinds[self.ARTICLES], "blog")

    def test_a_lone_articles_shard_elsewhere_is_catalogue(self) -> None:
        """Nothing resolves it toward content, so it means what it meant on the
        one shop observed serving it."""
        kinds = sitemap.classify(
            [(self.ARTICLES, ["https://muster.de/kosmetik/produkt"])], blog_path="/blog"
        )
        self.assertEqual(kinds[self.ARTICLES], "product")

    def test_misreading_costs_a_count_and_never_invents_one(self) -> None:
        """The failure direction, pinned. A product shard read as content leaves
        no product sitemap, and §10.3's three-state rule then reports *not
        measurable* — recoverable. The reverse writes a confident wrong count."""
        kinds = sitemap.classify(
            [(self.ARTICLES, ["https://muster.de/blog/a"])], blog_path="/blog"
        )
        self.assertNotEqual(kinds[self.ARTICLES], "product")

    def test_an_unambiguous_name_is_taken_at_its_word(self) -> None:
        products = "https://muster.de/sitemap_products_1.xml"
        kinds = sitemap.classify(
            [(products, ["https://muster.de/blogs/news/a"]), (self.BLOGS, [])],
            blog_path="/blogs",
        )
        self.assertEqual(kinds[products], "product")


class TestImageLocationsAreNotPages(unittest.TestCase):
    """`<image:loc>` is metadata about a page, not another page.

    Stripping namespaces before comparing tag names made every Shopify product
    sitemap read as twice its size. It never reached a count on this corpus —
    Shopify serves images from `cdn.shopify.com` and `same_site` discarded them
    — but a shop hosting its own images under `/products/…` would have had every
    product counted twice by a rule that believed it was counting pages.
    """

    WITH_IMAGES = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url>
        <loc>https://example.de/products/alpha</loc>
        <image:image><image:loc>https://example.de/products/alpha.jpg</image:loc></image:image>
      </url>
    </urlset>"""

    def test_only_the_page_location_is_returned(self) -> None:
        _children, pages = sitemap.parse(
            self.WITH_IMAGES.encode(), "https://example.de/sitemap_products_1.xml"
        )
        self.assertEqual(pages, ["https://example.de/products/alpha"])

    def test_a_namespaceless_sitemap_still_parses(self) -> None:
        """Real sitemaps omit the declaration; an extension element cannot."""
        body = b"<urlset><url><loc>https://example.de/detail/a</loc></url></urlset>"
        _children, pages = sitemap.parse(body, "https://example.de/s.xml")
        self.assertEqual(pages, ["https://example.de/detail/a"])


class TestBlogPathDetection(unittest.TestCase):
    """§5.3's blog vocabulary, against the paths real shops actually serve."""

    def test_shopify_plural_blogs_is_detected(self) -> None:
        """M1.14. Five shops in the first crawl published actively under
        `/blogs/` and every one reported "no blog path found"."""
        for path in (
            "/blogs/news",
            "/blogs/news/blackpolish-is-live",
            "/blogs/rezepte",
            "/nl-be/blogs/ekomia-magazine",  # locale-prefixed, as Shopify serves it
        ):
            self.assertTrue(
                impressum.find_blog_path(
                    [f"https://x.de{path}"], "", "https://x.de/", "x.de"
                ),
                path,
            )

    def test_the_shapes_already_covered_still_are(self) -> None:
        for path in ("/blog", "/magazin/zahnpflege", "/ratgeber/", "/de/news"):
            self.assertTrue(
                impressum.find_blog_path(
                    [f"https://x.de{path}"], "", "https://x.de/", "x.de"
                ),
                path,
            )

    def test_the_index_url_is_observed_rather_than_synthesised(self) -> None:
        """M1.15. `/blogs` is not a page on Shopify; `/blogs/news` is. All seven
        blog-index fetches in `run 2` 404'd on the synthesised bare path."""
        pages = [
            "https://x.de/blogs/news/zweiter-artikel",
            "https://x.de/blogs/rezepte",
            "https://x.de/blogs/news",
        ]
        self.assertEqual(
            impressum.find_blog_index_url("/blogs", pages, "", "https://x.de/", "x.de"),
            "https://x.de/blogs/news",
            "shallowest wins, code-point minimum breaks the tie",
        )

    def test_a_nav_link_beats_a_sitemap_url_at_the_same_depth(self) -> None:
        """A link a human put in the navigation is likelier to be the index
        than whichever article happens to sort first."""
        html = (
            '<html><body><nav><a href="/blogs/magazin">Magazin</a></nav></body></html>'
        )
        pages = ["https://x.de/blogs/aaa-artikel"]
        self.assertEqual(
            impressum.find_blog_index_url(
                "/blogs", pages, html, "https://x.de/", "x.de"
            ),
            "https://x.de/blogs/magazin",
        )

    def test_no_observed_url_falls_back_to_the_synthesised_one(self) -> None:
        self.assertIsNone(
            impressum.find_blog_index_url("/blogs", [], "", "https://x.de/", "x.de")
        )
        self.assertEqual(
            impressum.blog_index_url("https://x.de", "/blogs"), "https://x.de/blogs"
        )

    def test_a_segment_that_merely_starts_with_a_blog_word_is_not_a_blog(self) -> None:
        """The alternation is anchored by `(?:/|$)`, so adding `blogs` cannot
        widen `blog` into a prefix match."""
        for path in ("/blogsammlung", "/newsletter", "/magazinhalter-stahl"):
            self.assertIsNone(
                impressum.find_blog_path(
                    [f"https://x.de{path}"], "", "https://x.de/", "x.de"
                ),
                path,
            )


class TestBlogAnchorDetection(unittest.TestCase):
    """M1.14's anchor-text instrument, on the two shapes no path vocabulary
    reaches and the four shops it must stay silent on."""

    def locate(self, html: str, domain: str = "zecplus.de"):
        return impressum.locate_blog([], html, f"https://{domain}/", domain)

    def test_a_blog_on_a_subdomain_is_reached(self) -> None:
        """`zecplus.de`. Its sitemap index lists four shards and none of them
        mentions `blog.zecplus.de`, so the href must be taken wherever it points
        — that is the whole reason the instrument works."""
        located = self.locate('<a href="https://blog.zecplus.de/">Blog</a>')
        self.assertEqual(located.url, "https://blog.zecplus.de/")
        self.assertEqual(located.basis, "anchor_text")

    def test_a_blog_on_another_host_contributes_no_path_prefix(self) -> None:
        """`/` is the only path such a blog has, and §5.2's filter 4 would read
        it as "every URL on this shop is blog content" and empty the catalogue."""
        self.assertIsNone(
            self.locate('<a href="https://blog.zecplus.de/">Blog</a>').path
        )

    def test_a_root_level_slug_is_reached_and_keeps_its_prefix(self) -> None:
        """`lampenflut.de`. No sitemap at all and a path no pattern can tell
        from a category — but the anchor a human clicks says `Licht-Ratgeber`."""
        located = self.locate(
            '<a href="/Lampenflut-Licht-Ratgeber">Licht-Ratgeber</a>', "lampenflut.de"
        )
        self.assertEqual(located.url, "https://lampenflut.de/Lampenflut-Licht-Ratgeber")
        self.assertEqual(located.path, "/Lampenflut-Licht-Ratgeber")

    def test_the_full_path_is_the_prefix_not_its_first_segment(self) -> None:
        """The prefix keeps blog URLs out of the catalogue count, so a
        `/service/ratgeber` blog must not take all of `/service` with it."""
        located = self.locate('<a href="/service/ratgeber">Ratgeber</a>', "x.de")
        self.assertEqual(located.path, "/service/ratgeber")

    def test_the_four_true_negatives_stay_negative(self) -> None:
        """The other four shops carrying `blog_exists = 0`. Their homepages link
        no blog vocabulary at all, and `Newsletter` — the commonest footer link
        in the corpus — must not match on `news`."""
        for html in (
            '<a href="/newsletter">Newsletter</a>',
            '<a href="/kontakt">Kontakt</a><a href="/agb">AGB</a>',
            '<a href="/magazinhalter-stahl">Magazinhalter Stahl</a>',
            "",
        ):
            self.assertIsNone(self.locate(html, "x.de"), html)

    def test_path_vocabulary_is_consulted_first(self) -> None:
        """The order is the precision control. Anchor text's two known loose
        matches — doonails.de's *Tipps & Tricks* → a Shopify page, ekomia.de's
        *Ratgeber* → a tag listing — sit on shops whose blog a path already
        finds, so running it second means neither is ever reached."""
        located = impressum.locate_blog(
            ["https://x.de/blogs/news"],
            '<a href="/pages/tips-tricks">Tipps &amp; Tricks</a>',
            "https://x.de/",
            "x.de",
        )
        self.assertEqual((located.path, located.url), ("/blogs", None))
        self.assertEqual(located.basis, "path_vocabulary")

    def test_a_blog_link_to_our_own_front_page_is_navigation(self) -> None:
        self.assertIsNone(self.locate('<a href="/">Blog</a>', "x.de"))

    def test_same_site_links_outrank_foreign_ones(self) -> None:
        """A footer `News` pointing at a press portal must not outrank the
        shop's own magazine, however much shallower the foreign URL is."""
        self.assertEqual(
            impressum.find_blog_link(
                '<a href="https://presse.fremd.de/">News</a>'
                '<a href="/Kunden-Magazin">Magazin</a>',
                "https://x.de/",
                "x.de",
            ),
            "https://x.de/Kunden-Magazin",
        )

    def test_shallowest_wins_because_an_index_sits_above_its_posts(self) -> None:
        self.assertEqual(
            impressum.find_blog_link(
                '<a href="/Ratgeber-Welt/2024/ein-post">Ratgeber</a>'
                '<a href="/Ratgeber-Welt">Zum Ratgeber</a>',
                "https://x.de/",
                "x.de",
            ),
            "https://x.de/Ratgeber-Welt",
        )

    def test_the_shortest_anchor_text_wins_at_equal_depth(self) -> None:
        """M1.35, measured on `lampenflut.de`: three vocabulary anchors, all at
        depth 1 — one nav label and two article headlines. Code-point order
        alone picked `Kinderzimmerleuchten…`, a leaf post, and handed it to A6
        as the index; nothing is nested under a post, so no article could be
        sampled and the blog dated nothing. An index link is a label, a post
        link is a headline."""
        self.assertEqual(
            impressum.find_blog_link(
                '<a href="/Kinderzimmerleuchten-Grosser-Ratgeber-fuer-Eltern">'
                "Kinderzimmerleuchten richtig auswählen: Der große Ratgeber</a>"
                '<a href="/Lampenflut-Licht-Ratgeber">Licht-Ratgeber</a>'
                '<a href="/Sollux-Lampen-Ratgeber-Moderne-Leuchten">'
                "Sollux Lampen Ratgeber: Moderne Leuchten für ein Zuhause</a>",
                "https://lampenflut.de/",
                "lampenflut.de",
            ),
            "https://lampenflut.de/Lampenflut-Licht-Ratgeber",
        )


if __name__ == "__main__":
    unittest.main()
