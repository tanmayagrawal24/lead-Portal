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

    def test_p_slash_is_not_a_tier_2_pattern(self) -> None:
        """Dropped until observed in the wild: `/p/` is a product prefix on some
        shops and a *pagination* prefix on others, and the two errors are not
        equally bad. A false positive hands a listing page to
        `schema.product_present` and wrongly awards +10; a false negative just
        leaves the signal unwritten, which A5.5 already handles.
        """
        self.assertNotIn("/p/", sampling.PRODUCT_PATH_PATTERNS)
        self.assertFalse(
            sampling.is_product_candidate("https://example.de/p/2", DOMAIN)
        )

    def test_a_product_sitemap_url_still_needs_no_known_pattern(self) -> None:
        """Dropping `/p/` costs nothing on a platform product sitemap, where
        membership is the evidence and the path shape is not consulted."""
        self.assertTrue(
            sampling.is_product_candidate(
                "https://example.de/p/schallbuerste", DOMAIN, require_pattern=False
            )
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

    def test_a_locale_storefront_root_is_never_a_product(self) -> None:
        """A multi-locale shop has more than one homepage, and Shopify lists
        each locale root inside that locale's *product* sitemap. Reviving Tier 1
        (M1.13) made this reachable: without the guard, ekomia.de, navucko.com
        and snocks.com would each have sampled `/de-at`, `/en` and `/de-ch` —
        listing pages feeding `schema.product_present` a wrong +10, which is the
        exact error M1.4 dropped `/p/` over.
        """
        for path in ("/de-at", "/en", "/fr-ch", "/de-at/", "/NL-BE"):
            self.assertFalse(
                sampling.is_product_candidate(
                    f"https://example.de{path}", DOMAIN, require_pattern=False
                ),
                path,
            )

    def test_the_guard_does_not_swallow_real_products_under_a_locale(self) -> None:
        """The anti-vacuity case: only the bare locale root is rejected."""
        for path in ("/de-at/products/alma", "/en/products/x", "/de-at/schallbuerste"):
            self.assertTrue(
                sampling.is_product_candidate(
                    f"https://example.de{path}", DOMAIN, require_pattern=False
                ),
                path,
            )


class TestSecondaryLocales(unittest.TestCase):
    """M1.25. A translation of a product is not a second product.

    A multi-locale shop lists one product sitemap per market, each holding the
    same catalogue under a different prefix. Counting their union multiplies a
    shop's catalogue by its number of markets: `snocks.com` ships ten copies of
    462 products, `ekomia.de` nine copies of 335.
    """

    def test_a_declared_prefix_is_secondary(self) -> None:
        """`smile-store.de` serves its English subshop from `/shop/en/` and says
        so in an `hreflang` alternate. No path-shape rule would see that."""
        self.assertTrue(
            sampling.is_secondary_locale(
                "https://www.smile-store.de/shop/en/brands/prevdent/plaque-detector",
                primary="",
                secondary=("/shop/en",),
            )
        )
        self.assertFalse(
            sampling.is_secondary_locale(
                "https://www.smile-store.de/stoebern/prevdent/plaque-detector",
                primary="",
                secondary=("/shop/en",),
            )
        )

    def test_an_undeclared_locale_is_caught_by_its_shape(self) -> None:
        """`snocks.com` declares three of its ten markets. The other seven are
        visible only as a leading segment of locale-code shape."""
        for path in ("/fr-fr/products/x", "/pl-pl/products/x", "/en-es/products/x"):
            self.assertTrue(
                sampling.is_secondary_locale(f"https://snocks.com{path}"), path
            )

    def test_the_primary_storefront_is_never_secondary(self) -> None:
        """A shop serving its default from `/de/` declares `x-default` there.
        Excluding it would empty the catalogue rather than deduplicate it."""
        self.assertFalse(
            sampling.is_secondary_locale(
                "https://example.de/de/products/x", primary="/de"
            )
        )
        self.assertTrue(
            sampling.is_secondary_locale(
                "https://example.de/en/products/x", primary="/de"
            )
        )

    def test_ordinary_paths_are_not_locales(self) -> None:
        for url in (
            "https://example.de/products/alpha",
            "https://example.de/detail/beta",
            "https://example.de/",
        ):
            self.assertFalse(sampling.is_secondary_locale(url), url)


class TestBlogArticleSelection(unittest.TestCase):
    """A6, and the one detail that decides whether it is correct at all."""

    def test_the_index_path_is_the_anchor_not_the_blog_path(self) -> None:
        """On Shopify the hierarchy is `/blogs/<handle>/<article>`, so a URL one
        level under `/blogs` is *another index*. "Shallowest under the blog
        path" selects `/blogs/karriere` on bio-fleischer-laden.de and hands a
        listing page to an Article parser — M1.16's error in a new place."""
        urls = [
            "https://muster.de/blogs/karriere",  # another blog index
            "https://muster.de/blogs/rezepte",  # the index we fetched
            "https://muster.de/blogs/rezepte/bbq-schweinenacken",
        ]
        chosen, tier = sampling.choose_blog_article(
            blog_sitemap_urls=urls,
            sitemap_urls=[],
            index_links=[],
            index_url="https://muster.de/blogs/rezepte",
        )
        self.assertEqual(chosen, "https://muster.de/blogs/rezepte/bbq-schweinenacken")
        self.assertEqual(tier, "blog_sitemap")

    def test_the_index_itself_is_never_the_sample(self) -> None:
        chosen, _tier = sampling.choose_blog_article(
            ["https://muster.de/blogs/rezepte", "https://muster.de/blogs/rezepte/"],
            [],
            [],
            "https://muster.de/blogs/rezepte",
        )
        self.assertIsNone(chosen)

    def test_tiers_fall_through_in_order(self) -> None:
        chosen, tier = sampling.choose_blog_article(
            blog_sitemap_urls=[],
            sitemap_urls=["https://muster.de/blogs/news/zweiter"],
            index_links=["https://muster.de/blogs/news/erster"],
            index_url="https://muster.de/blogs/news",
        )
        self.assertEqual(
            (chosen, tier),
            ("https://muster.de/blogs/news/zweiter", "sitemap_under_index"),
        )

    def test_shallowest_wins_then_code_point(self) -> None:
        chosen, _tier = sampling.choose_blog_article(
            [
                "https://muster.de/blogs/news/tag/x/tief",
                "https://muster.de/blogs/news/zebra",
                "https://muster.de/blogs/news/Mango",
            ],
            [],
            [],
            "https://muster.de/blogs/news",
        )
        self.assertEqual(chosen, "https://muster.de/blogs/news/Mango")

    def test_no_candidates_returns_none_so_nothing_is_written(self) -> None:
        """A6.1: `content.blog_last_post` and `schema.article_present` then stay
        unwritten. Not a zero, not today's date."""
        chosen, tier = sampling.choose_blog_article(
            [], [], [], "https://muster.de/blogs/news"
        )
        self.assertEqual((chosen, tier), (None, "none"))

    def test_off_site_and_query_urls_are_rejected(self) -> None:
        chosen, _tier = sampling.choose_blog_article(
            [
                "https://fremd.de/blogs/news/x",
                "https://muster.de/blogs/news/x?page=2",
            ],
            [],
            [],
            "https://muster.de/blogs/news",
        )
        self.assertIsNone(chosen)


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
