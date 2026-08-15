"""Unit tests for the pure helpers: URL normalisation, robots policy, sitemaps."""

from __future__ import annotations

import gzip
import unittest

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


if __name__ == "__main__":
    unittest.main()
