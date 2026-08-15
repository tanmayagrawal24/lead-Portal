"""The A5 product-sample selection rule (§5.2).

Tier 0 reuse needs the database and a live fetch, so it is covered end to end
in `test_fetch.py`. Everything here is the pure part: filters and ordering.
"""

from __future__ import annotations

import unittest

from portal import sampling

DOMAIN = "example.de"


class TestFilters(unittest.TestCase):
    def test_accepts_a_plain_product_url(self) -> None:
        self.assertTrue(
            sampling.is_product_candidate(
                "https://example.de/detail/schallbuerste", DOMAIN
            )
        )

    def test_rejects_query_strings(self) -> None:
        """Filter 1 — variant and filter permutations, not canonical pages."""
        self.assertFalse(
            sampling.is_product_candidate(
                "https://example.de/detail/x?color=red", DOMAIN
            )
        )

    def test_requires_a_segment_after_the_pattern(self) -> None:
        """Filter 2 — bare /products/ is Shopify's collection listing."""
        self.assertFalse(
            sampling.is_product_candidate("https://example.de/products/", DOMAIN)
        )
        self.assertFalse(
            sampling.is_product_candidate("https://example.de/products", DOMAIN)
        )
        self.assertTrue(
            sampling.is_product_candidate("https://example.de/products/handle", DOMAIN)
        )

    def test_rejects_category_and_listing_patterns(self) -> None:
        """Filter 3."""
        for url in (
            "https://example.de/kategorie/zahnpflege",
            "https://example.de/collections/all",
            "https://example.de/c/zahnbuersten",
        ):
            self.assertFalse(sampling.is_product_candidate(url, DOMAIN), url)

    def test_rejects_urls_under_the_blog_path(self) -> None:
        """Filter 4."""
        self.assertFalse(
            sampling.is_product_candidate(
                "https://example.de/magazin/products/test", DOMAIN, blog_path="/magazin"
            )
        )
        self.assertTrue(
            sampling.is_product_candidate(
                "https://example.de/detail/x", DOMAIN, blog_path="/magazin"
            )
        )

    def test_rejects_other_domains(self) -> None:
        self.assertFalse(
            sampling.is_product_candidate("https://other.de/detail/x", DOMAIN)
        )

    def test_product_sitemap_membership_waives_the_pattern_requirement(self) -> None:
        """Shopware SEO-rewritten URLs carry no /detail/ segment; membership of
        a product sitemap is itself the evidence."""
        url = "https://example.de/schallzahnbuerste-pro/"
        self.assertFalse(sampling.is_product_candidate(url, DOMAIN))
        self.assertTrue(
            sampling.is_product_candidate(url, DOMAIN, require_pattern=False)
        )

    def test_homepage_is_never_a_product_even_without_the_pattern(self) -> None:
        for url in ("https://example.de/", "https://example.de"):
            self.assertFalse(
                sampling.is_product_candidate(url, DOMAIN, require_pattern=False), url
            )


class TestOrdering(unittest.TestCase):
    def test_picks_the_code_point_minimum(self) -> None:
        candidates = [
            "https://example.de/detail/zebra",
            "https://example.de/detail/apfel",
            "https://example.de/detail/Mango",
        ]
        # Capital M sorts before lowercase a by code point. Locale collation
        # would fold case and pick "apfel" — the bug this test exists to catch.
        self.assertEqual(sampling.select(candidates), "https://example.de/detail/Mango")

    def test_is_invariant_to_input_order(self) -> None:
        candidates = [f"https://example.de/detail/{n}" for n in ("c", "a", "b")]
        self.assertEqual(
            sampling.select(candidates), sampling.select(list(reversed(candidates)))
        )

    def test_umlauts_order_by_code_point_not_locale(self) -> None:
        """`ä` (U+00E4) sorts after `z` by code point; a German locale would
        sort it next to `a`. The rule says code point."""
        candidates = [
            "https://example.de/detail/zahn",
            "https://example.de/detail/ärzte",
        ]
        self.assertEqual(sampling.select(candidates), "https://example.de/detail/zahn")

    def test_empty_candidates_select_nothing(self) -> None:
        self.assertIsNone(sampling.select([]))


class TestTierPrecedence(unittest.TestCase):
    def test_product_sitemap_wins_over_path_patterns(self) -> None:
        chosen, tier = sampling.choose_product_sample(
            product_sitemap_urls=["https://example.de/zzz-product/"],
            sitemap_urls=["https://example.de/detail/aaa"],
            homepage_links=["https://example.de/detail/000"],
            domain=DOMAIN,
        )
        self.assertEqual(
            (chosen, tier), ("https://example.de/zzz-product/", "product_sitemap")
        )

    def test_falls_through_to_path_patterns(self) -> None:
        chosen, tier = sampling.choose_product_sample(
            product_sitemap_urls=[],
            sitemap_urls=["https://example.de/detail/b", "https://example.de/detail/a"],
            homepage_links=["https://example.de/detail/000"],
            domain=DOMAIN,
        )
        self.assertEqual(
            (chosen, tier), ("https://example.de/detail/a", "sitemap_path_pattern")
        )

    def test_falls_through_to_homepage_links(self) -> None:
        chosen, tier = sampling.choose_product_sample(
            product_sitemap_urls=[],
            sitemap_urls=["https://example.de/ueber-uns"],
            homepage_links=[
                "https://example.de/produkt/eins",
                "https://example.de/kontakt",
            ],
            domain=DOMAIN,
        )
        self.assertEqual(
            (chosen, tier), ("https://example.de/produkt/eins", "homepage_links")
        )

    def test_a_content_only_sitemap_yields_no_candidate(self) -> None:
        """The mixed-sitemap case: content URLs must not become the sample."""
        chosen, tier = sampling.choose_product_sample(
            product_sitemap_urls=[],
            sitemap_urls=[
                "https://example.de/magazin/zahnpflege-tipps",
                "https://example.de/ueber-uns",
                "https://example.de/kategorie/buersten",
            ],
            homepage_links=[],
            domain=DOMAIN,
            blog_path="/magazin",
        )
        self.assertEqual((chosen, tier), (None, "none"))

    def test_selection_is_stable_across_shard_redistribution(self) -> None:
        """The argument for code-point minimum over document order: Shopware
        re-shards product sitemaps, so the same catalog arrives in a different
        order and split. The choice must not move."""
        shard_a = ["https://example.de/p/delta", "https://example.de/p/alpha"]
        shard_b = ["https://example.de/p/charlie", "https://example.de/p/bravo"]
        first, _ = sampling.choose_product_sample(shard_a + shard_b, [], [], DOMAIN)
        # Same catalog, re-sharded and reordered.
        second, _ = sampling.choose_product_sample(
            ["https://example.de/p/charlie"]
            + ["https://example.de/p/delta", "https://example.de/p/bravo"]
            + ["https://example.de/p/alpha"],
            [],
            [],
            DOMAIN,
        )
        self.assertEqual(first, second)
        self.assertEqual(first, "https://example.de/p/alpha")


if __name__ == "__main__":
    unittest.main()
