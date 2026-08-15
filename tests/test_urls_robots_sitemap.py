"""Unit tests for the pure helpers: URL normalisation, robots policy, sitemaps."""

from __future__ import annotations

import gzip
import unittest

from portal import robots, sitemap, urls


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

    def test_content_sitemaps_are_not_product_sitemaps(self) -> None:
        for url in (
            "https://example.de/sitemap-www.example.de-content-1.xml.gz",
            "https://example.de/sitemap.xml",
            "https://example.de/post-sitemap.xml",
        ):
            self.assertFalse(sitemap.is_product_sitemap(url), url)


if __name__ == "__main__":
    unittest.main()
