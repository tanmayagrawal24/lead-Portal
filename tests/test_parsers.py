"""§5.3's deterministic parsers, against real markup where it is safe to keep it.

Fixture provenance and why the Impressum files are hand-written rather than
redacted: `tests/fixtures/extract/README.md`.

**Coverage is uneven and this file says so out loud.** The corpus behind these
tests is 13 shops: 7 Shopify, 4 JTL, 1 WooCommerce, 1 Shopware 5. So Shopify and
JTL are tested against several real pages each, WooCommerce and Shopware against
exactly one — and the Shopware one is a shop the platform signatures do not
detect at all (M1.11). A suite that covers four platforms this unevenly should
not read as though it covers them equally.
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from portal import parsers

FIXTURES = Path(__file__).parent / "fixtures" / "extract"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestPlatformDetection(unittest.TestCase):
    """M1.9–M1.11. Every positive here is real markup from a real shop."""

    def test_detects_each_platform_present_in_the_corpus(self) -> None:
        for name, expected in [
            ("platform-jtl.html", "JTL"),  # n=4 real shops
            ("platform-shopify.html", "Shopify"),  # n=7 real shops
            ("platform-woocommerce.html", "WooCommerce"),  # n=1
        ]:
            with self.subTest(platform=expected):
                self.assertEqual(parsers.detect_platform(fixture(name)), expected)

    def test_shopware_5_is_knowingly_undetected(self) -> None:
        """M1.11, pinned rather than papered over. `smile-store.de` is a real
        Shopware 5 shop; `/bundles/storefront/` is a Shopware **6** path, and no
        second SW5 shop exists in the corpus to confirm a signature on. This
        test exists so the gap is visible in the suite instead of looking like
        an oversight, and so it fails loudly the day a signature is added."""
        self.assertIsNone(
            parsers.detect_platform(fixture("platform-shopware5-undetected.html"))
        )

    def test_the_jtl_footer_credit_is_not_a_signature(self) -> None:
        """`jtl-shop` appears only in an operator-removable "powered by"
        credit, and one of the four real JTL shops has removed it."""
        self.assertIsNone(
            parsers.detect_platform(
                '<a title="JTL-Shop" href="https://jtl-url.de/jtlshop">x</a>'
            )
        )

    def test_a_bare_platform_mention_is_not_a_signature(self) -> None:
        """M1.10: the loose match false-positived a JTL shop and a Shopify shop
        that merely mention the word."""
        self.assertIsNone(
            parsers.detect_platform(
                "<p>Wir sind von Shopware zu einem anderen System gewechselt.</p>"
            )
        )

    def test_wordpress_alone_is_not_woocommerce(self) -> None:
        self.assertIsNone(
            parsers.detect_platform('<link href="/wp-content/themes/x.css">')
        )


class TestLegalForm(unittest.TestCase):
    """A1 / M1.21. Measured 7/12 with 0 false positives on the real corpus."""

    def test_reads_the_form_from_the_provider_block(self) -> None:
        for name, expected in [
            ("impressum-gmbh-co-kg.html", "GmbH & Co. KG"),
            ("impressum-gmbh.html", "GmbH"),
            ("impressum-foreign-ltd.html", "Ltd"),
        ]:
            with self.subTest(fixture=name):
                self.assertEqual(parsers.legal_form(fixture(name)), expected)

    def test_the_longest_form_wins_over_its_own_substrings(self) -> None:
        """`GmbH & Co. KG` contains both `GmbH` and `KG`, and the real page also
        names a second `GmbH` as Komplementärin."""
        self.assertEqual(
            parsers.legal_form(fixture("impressum-gmbh-co-kg.html")), "GmbH & Co. KG"
        )

    def test_a_sole_trader_stating_no_form_returns_none(self) -> None:
        """Not a parse failure. German law does not require a form token from a
        sole trader, and five of twelve real pages state none — all five being
        the owner-operated shops the tool wants (§10.2)."""
        for name in (
            "impressum-sole-trader-no-form.html",
            "impressum-inhaber-marker.html",
        ):
            with self.subTest(fixture=name):
                self.assertIsNone(parsers.legal_form(fixture(name)))

    def test_a_vendor_gmbh_before_the_operator_is_not_the_operators_form(self) -> None:
        """The false positive provider-block anchoring exists to prevent: a
        cookie-consent vendor's `GmbH` earlier in the page. A naive
        first-match-in-page returned the vendor on two real shops."""
        self.assertIsNone(
            parsers.legal_form(fixture("impressum-vendor-noise-first.html"))
        )

    def test_script_contents_cannot_supply_a_provider_block(self) -> None:
        """JSON-LD breadcrumbs sit before the visible Impressum on real pages
        and would otherwise be read as the company's own details."""
        html = """<html><body>
          <script type="application/ld+json">
          {"@type":"Organization","name":"Fremd Vendor GmbH",
           "address":"Fremdweg 2, 99999 Fremdstadt"}
          </script>
          <main><p>Impressum Anbieterkennzeichnung: Max Mustermann
          Musterstraße 1 50667 Musterstadt</p></main></body></html>"""
        self.assertIsNone(parsers.legal_form(html))


class TestJsonLd(unittest.TestCase):
    def test_finds_types_including_inside_graph(self) -> None:
        html = """<script type="application/ld+json">
        {"@graph":[{"@type":"Product","name":"x"},{"@type":["BreadcrumbList"]}]}
        </script>"""
        self.assertEqual(parsers.jsonld_types(html), {"Product", "BreadcrumbList"})
        self.assertTrue(parsers.has_product_schema(html))

    def test_one_malformed_block_does_not_hide_the_others(self) -> None:
        html = """
        <script type="application/ld+json">{ this is not json }</script>
        <script type="application/ld+json">{"@type":"BlogPosting"}</script>"""
        self.assertTrue(parsers.has_article_schema(html))

    def test_article_subtypes_count_as_article(self) -> None:
        for value in ("Article", "BlogPosting", "NewsArticle"):
            with self.subTest(type=value):
                self.assertTrue(
                    parsers.has_article_schema(
                        f'<script type="application/ld+json">{{"@type":"{value}"}}</script>'
                    )
                )

    def test_review_count_is_none_rather_than_zero_when_absent(self) -> None:
        """`qual.product_strength` must not read "no markup" as "no reviews"."""
        self.assertIsNone(parsers.aggregate_review_count("<html></html>"))

    def test_review_count_is_read_from_aggregate_rating(self) -> None:
        html = """<script type="application/ld+json">
        {"@type":"Product","aggregateRating":{"@type":"AggregateRating","reviewCount":"851"}}
        </script>"""
        self.assertEqual(parsers.aggregate_review_count(html), 851)


class TestHreflang(unittest.TestCase):
    def test_counts_languages_not_locales(self) -> None:
        """§5.3: `de-DE`/`de-AT`/`de-CH` is one language served to three
        markets. Counting locales would award i18n for a Swiss price list."""
        html = """
        <link rel="alternate" hreflang="de-DE" href="/">
        <link rel="alternate" hreflang="de-AT" href="/at">
        <link rel="alternate" hreflang="de-CH" href="/ch">
        <link rel="alternate" hreflang="x-default" href="/">"""
        self.assertEqual(parsers.hreflang_language_count(html), 1)

    def test_distinct_languages_are_counted(self) -> None:
        html = """
        <link rel="alternate" hreflang="de-DE" href="/">
        <link rel="alternate" hreflang="fr-CH" href="/fr">
        <link rel="alternate" hreflang="nl-BE" href="/nl">"""
        self.assertEqual(parsers.hreflang_language_count(html), 3)

    def test_absent_hreflang_is_none_not_zero(self) -> None:
        self.assertIsNone(parsers.hreflang_language_count("<html></html>"))


class TestAgencyCredit(unittest.TestCase):
    def test_reads_a_real_footer_credit(self) -> None:
        html = "<footer><p>Realisiert von Musteragentur Webdesign Köln</p></footer>"
        credit = parsers.footer_agency_credit(html, "muster.de")
        assert credit is not None
        self.assertIn("Musteragentur", credit)

    def test_a_platform_credit_is_not_an_agency_credit(self) -> None:
        """Observed on smoke2u.de: "Powered by JTL-Shop" ships by default on
        every JTL install. Counting it fires `neg.has_agency` against a shop
        for choosing a shop system."""
        html = "<footer><p>Powered by JTL-Shop</p></footer>"
        self.assertIsNone(parsers.footer_agency_credit(html, "muster.de"))

    def test_our_own_links_are_not_agency_credits(self) -> None:
        html = '<footer><a href="https://muster.de/design">Design</a></footer>'
        self.assertIsNone(parsers.footer_agency_credit(html, "muster.de"))


class TestBlogDates(unittest.TestCase):
    TODAY = date(2026, 8, 15)

    def test_reads_german_visible_dates(self) -> None:
        self.assertEqual(parsers.parse_german_date("12. März 2023"), date(2023, 3, 12))
        self.assertEqual(parsers.parse_german_date("01.02.2024"), date(2024, 2, 1))

    def test_jsonld_date_wins_and_newest_is_taken(self) -> None:
        html = """
        <script type="application/ld+json">{"@type":"BlogPosting","datePublished":"2024-03-01"}</script>
        <script type="application/ld+json">{"@type":"BlogPosting","datePublished":"2025-07-19"}</script>"""
        self.assertEqual(parsers.newest_post_date(html, self.TODAY), date(2025, 7, 19))

    def test_a_future_date_is_discarded(self) -> None:
        """A scheduled post would otherwise make a dead blog look current —
        the exact error `opp.blog_stale` exists to catch."""
        html = '<time datetime="2027-01-01">bald</time><time datetime="2022-05-04">alt</time>'
        self.assertEqual(parsers.newest_post_date(html, self.TODAY), date(2022, 5, 4))

    def test_no_date_returns_none_rather_than_today(self) -> None:
        """§6.2's NULL branch depends on this: an unparseable blog is an
        unknown, not a fresh one."""
        self.assertIsNone(parsers.newest_post_date("<p>Unser Magazin</p>", self.TODAY))

    def test_post_count_excludes_the_index_itself(self) -> None:
        html = """
        <a href="/blogs/news">Übersicht</a>
        <a href="/blogs/news/erster-artikel">Erster</a>
        <a href="/blogs/news/zweiter-artikel">Zweiter</a>
        <a href="/blogs/news/zweiter-artikel">Zweiter nochmal</a>"""
        read = parsers.read_blog_index(
            html, "/blogs", index_path="/blogs/news", today=self.TODAY
        )
        self.assertEqual(read.post_count, 2)


if __name__ == "__main__":
    unittest.main()
