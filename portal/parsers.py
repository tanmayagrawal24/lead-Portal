"""Deterministic parsers for `extract-p1` (§5.3).

Pure functions over already-fetched HTML. No network, no LLM, no cost, and the
same input always gives the same output — which is what makes re-scoring free
(§4) and what lets every one of these be tested against real markup harvested
from the first crawl.

Everything here answers "what does this page say", never "what should we fetch
next" (that is `portal.impressum`, at fetch time) and never "what is this worth"
(that is §6, at score time).

**The house rule this module exists to serve:** a parser that cannot measure
something returns `None`, never a zero. "Checked and absent" and "not measured"
are different facts, they lead to different scores, and only the caller knows
which rules depend on the difference (§5.2 A5.5, §6.2's blog ladder).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from selectolax.parser import HTMLParser, Node

from portal.urls import path_of, same_site

# ── platform signatures (§5.3, M1.9–M1.11) ──────────────────────────────
#
# Observed in the homepage HTML of all 13 shops in the first crawl, not taken
# from documentation. `jtl-shop` is deliberately absent: it appears only inside
# an operator-removable "powered by JTL-Shop" footer credit, and one of the four
# JTL shops has removed it. The bare string `shopware` is absent for the same
# class of reason — it false-positived a JTL shop and a Shopify shop that merely
# mention the word.
PLATFORM_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Most specific first: a JTL shop can mention "shopware" in prose.
    (
        "JTL",
        ("jtl-nav-wrapper", "jtl-validate", "jtl_token", "jtlPackFormTranslations"),
    ),
    ("Shopify", ("cdn.shopify.com",)),
    ("Shopware", ("/bundles/storefront/",)),
)

#: WooCommerce needs two markers together; one alone is WordPress, not a shop.
_WOO_MARKERS = ("wp-content", "woocommerce")


def detect_platform(html: str) -> str | None:
    """The platform, or `None` when no signature matches.

    `None` is not "no platform" — Shopware 5 emits none of these signatures
    (M1.11) and `smile-store.de` is a real Shopware 5 shop that lands here. The
    caller must not turn this into "not a shop".
    """
    for platform, markers in PLATFORM_SIGNATURES:
        if any(marker in html for marker in markers):
            return platform
    lowered = html.lower()
    if all(marker in lowered for marker in _WOO_MARKERS):
        return "WooCommerce"
    return None


# ── JSON-LD ─────────────────────────────────────────────────────────────


#: A legacy XHTML script guard, still emitted around JSON-LD by real shops
#: (`snocks.com`, on all three of its blocks). It is not JSON, and it made the
#: whole document unparseable — `schema.article_present` read `0` on a page
#: carrying `BlogPosting`, which is a wrong "checked and absent" (M1.31).
_CDATA_OPEN = re.compile(r"^\s*(?://\s*)?<!\[CDATA\[")
_CDATA_CLOSE = re.compile(r"(?://\s*)?\]\]>\s*$")


def _jsonld_documents(html: str) -> list[object]:
    """Every parseable `application/ld+json` block on the page.

    Malformed blocks are skipped rather than raising: a third-party page is
    allowed to ship broken JSON, and one bad block must not hide the good ones.
    A CDATA guard is not "broken", though — it is a wrapper, so it is unwrapped
    before the block is judged.
    """
    documents: list[object] = []
    for block in HTMLParser(html).css('script[type="application/ld+json"]'):
        payload = _CDATA_CLOSE.sub("", _CDATA_OPEN.sub("", block.text())).strip()
        try:
            documents.append(json.loads(payload))
        except (json.JSONDecodeError, ValueError):
            continue
    return documents


def jsonld_types(html: str) -> set[str]:
    """Every `@type` in every `application/ld+json` block, `@graph` included."""
    found: set[str] = set()

    def collect(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            value = node.get("@type")
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, list):
                found.update(item for item in value if isinstance(item, str))
            for key in ("@graph", "mainEntity", "itemListElement"):
                if key in node:
                    collect(node[key])

    for document in _jsonld_documents(html):
        collect(document)
    return found


#: §5.3 checks for `Product` and for article-shaped types. `BlogPosting` and
#: `NewsArticle` are subtypes of `Article` and count as the same evidence.
ARTICLE_TYPES = {"Article", "BlogPosting", "NewsArticle", "TechArticle"}


def has_article_schema(html: str) -> bool:
    return bool(jsonld_types(html) & ARTICLE_TYPES)


def has_product_schema(html: str) -> bool:
    return "Product" in jsonld_types(html)


def aggregate_review_count(html: str) -> int | None:
    """`reviewCount`/`ratingCount` from `AggregateRating`, or `None`.

    `None` rather than 0: a shop with no review markup has not been shown to
    have no reviews, and `qual.product_strength` must not read the two alike.
    """
    best: int | None = None

    def collect(node: object) -> None:
        nonlocal best
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            if node.get("@type") == "AggregateRating" or "aggregateRating" in node:
                rating = node.get("aggregateRating", node)
                if isinstance(rating, dict):
                    for key in ("reviewCount", "ratingCount"):
                        try:
                            value = int(str(rating.get(key, "")).strip() or 0)
                        except (TypeError, ValueError):
                            continue
                        if value > 0:
                            best = value if best is None else max(best, value)
            for value in node.values():
                collect(value)

    for block in HTMLParser(html).css('script[type="application/ld+json"]'):
        try:
            collect(json.loads(block.text()))
        except (json.JSONDecodeError, ValueError):
            continue
    return best


# ── homepage odds and ends ──────────────────────────────────────────────


def meta_description_length(html: str) -> int | None:
    node = HTMLParser(html).css_first('meta[name="description"]')
    if node is None:
        return None
    return len((node.attributes.get("content") or "").strip())


def hreflang_language_count(html: str) -> int | None:
    """Distinct **language** codes, not locale codes (§5.3).

    `de-DE`/`de-AT`/`de-CH` is one language served to three markets, not three
    languages, and counting locales would award `i18n` to every shop with a
    Swiss price list. `x-default` is a routing hint, not a language.
    """
    languages = set()
    for node in HTMLParser(html).css("link[rel=alternate][hreflang]"):
        value = (node.attributes.get("hreflang") or "").strip().lower()
        if not value or value == "x-default":
            continue
        languages.add(value.split("-")[0])
    return len(languages) if languages else None


@dataclass(frozen=True)
class LocalePrefixes:
    """Where a site says its translations live (M1.25).

    `primary` is the path prefix of the storefront the site nominates as its
    default; `secondary` are the prefixes of the declared alternates. Both are
    normalised without a trailing slash, and the site root is `""`.
    """

    primary: str = ""
    secondary: tuple[str, ...] = ()


def hreflang_prefixes(html: str, site: str) -> LocalePrefixes:
    """Read the declared locale storefronts off the homepage (M1.25).

    A multi-locale shop lists **one product sitemap per locale**, each holding
    the same catalogue under a different path prefix, so counting their union
    multiplies a shop's catalogue by its number of markets. `snocks.com` ships
    ten copies of 925 products.

    Only same-site alternates count, and only those with a path: `zecplus.de`
    points its `de-CH` alternate at `zecplus.ch`, a different registrable
    domain that `same_site` already excludes, and an alternate pointing at the
    root is not a prefix at all.

    The `x-default` alternate names the primary storefront, which is the part
    that makes this safe to apply: a shop serving German from `/de/` declares
    `x-default` there too, so the rule excludes its *siblings* rather than its
    catalogue. Absent `x-default`, the primary is the root.
    """
    primary = ""
    declared: list[str] = []
    for node in HTMLParser(html).css("link[rel=alternate][hreflang]"):
        code = (node.attributes.get("hreflang") or "").strip().lower()
        href = (node.attributes.get("href") or "").strip()
        if not code or not href or not same_site(href, site):
            continue
        prefix = path_of(href).rstrip("/")
        if code == "x-default":
            primary = prefix
        elif prefix:
            declared.append(prefix)
    return LocalePrefixes(
        primary=primary,
        secondary=tuple(sorted({p for p in declared if p != primary})),
    )


#: Trusted Shops ships several script hosts; all of them carry this token.
_TRUSTED_SHOPS = re.compile(r"trustedshops|trustbadge", re.IGNORECASE)


def has_trusted_shops(html: str) -> bool:
    return bool(_TRUSTED_SHOPS.search(html))


_AGENCY_CREDIT = re.compile(
    r"realisiert von|umgesetzt von|powered by|webdesign\s*[:by]|"
    r"programmierung\s*:|gestaltung\s*:|entwickelt von",
    re.IGNORECASE,
)
_AGENCY_LINK = re.compile(r"agentur|design|media|digital|webdesign", re.IGNORECASE)

#: "Powered by JTL-Shop" is a *platform* credit, not an agency credit, and it
#: ships by default on every JTL install. Counting it would fire `neg.has_agency`
#: against a shop for choosing a shop system — observed on smoke2u.de.
_PLATFORM_CREDIT = re.compile(
    r"jtl[- ]?shop|shopify|woocommerce|wordpress|shopware|magento|prestashop"
    r"|gambio|oxid|plentymarkets",
    re.IGNORECASE,
)


def footer_agency_credit(html: str, domain: str) -> str | None:
    """An agency credit in the footer, as the matched text (§5.3).

    Under-detects by design — a logo-only credit is invisible here — which is
    why §6.3 treats it as a bonus negative signal and never as a gate.
    """
    tree = HTMLParser(html)
    footers = tree.css("footer") or tree.css('[class*="footer"]')
    for footer in footers:
        text = " ".join((footer.text() or "").split())
        if match := _AGENCY_CREDIT.search(text):
            start = max(0, match.start() - 10)
            credit = text[start : match.end() + 60].strip()
            if not _PLATFORM_CREDIT.search(credit):
                return credit
        for link in footer.css("a[href]"):
            href = link.attributes.get("href") or ""
            if domain in href or href.startswith(("/", "#")):
                continue  # our own pages are not agency credits
            label = f"{link.text() or ''} {link.attributes.get('title') or ''}"
            if _AGENCY_LINK.search(label) and not _PLATFORM_CREDIT.search(label):
                return " ".join(label.split())[:70]
    return None


# ── legal form (A1 / M1.21) ─────────────────────────────────────────────

_BLOCK_ANCHORS = re.compile(
    r"Angaben\s+gem[äa]ß\s+§\s*5|Gesetzliche\s+Anbieterkennung|Anbieterkennzeichnung"
    r"|Verantwortlich\s+f[üu]r\s+den\s+Inhalt|Diensteanbieter|Impressum",
    re.IGNORECASE,
)
#: A DACH postal code followed by a place name. This is what separates the real
#: provider block from a nav link or a cookie banner — without it, a naive
#: first-match found a cookie vendor's `GmbH` on two shops and a trust-seal
#: `e.V.` on a third.
_ADDRESS = re.compile(r"\b(?:[A-Z]{1,2}-)?\d{4,5}\s+[A-ZÄÖÜ][\wäöüß.-]+")
_PROVIDER_WINDOW = 400

#: Longest first: `GmbH & Co. KG` must beat both `GmbH` and `KG`.
LEGAL_FORMS: tuple[tuple[str, str], ...] = (
    ("GmbH & Co. KGaA", r"GmbH\s*&\s*Co\.?\s*KGaA"),
    ("GmbH & Co. KG", r"GmbH\s*&\s*Co\.?\s*KG"),
    ("AG & Co. KG", r"AG\s*&\s*Co\.?\s*KG"),
    ("UG (haftungsbeschränkt)", r"\bUG\b\s*\(?\s*haftungsbeschr[äa]nkt\s*\)?"),
    ("gGmbH", r"\bgGmbH\b"),
    ("GmbH", r"\bGmbH\b"),
    ("KGaA", r"\bKGaA\b"),
    ("e.Kfr.", r"\be\.\s?Kfr\."),
    ("e.K.", r"\be\.\s?K\.(?!\w)"),
    ("Einzelunternehmen", r"\bEinzelunternehmen\b"),
    ("GbR", r"\bGbR\b|\bGesellschaft b[üu]rgerlichen Rechts\b"),
    ("OHG", r"\bOHG\b|\boHG\b"),
    ("Ltd", r"\b(?:Ltd\.?|LTD\.?|Limited)\b"),
    ("AG", r"\bAG\b"),
    ("KG", r"\bKG\b"),
)
_COMPILED_FORMS = tuple((name, re.compile(pattern)) for name, pattern in LEGAL_FORMS)


def visible_text(html: str) -> str:
    """Page text with script/style stripped and whitespace collapsed.

    Stripping `<script>` is load-bearing, not hygiene: JSON-LD blocks contain
    breadcrumb names and vendor identifiers that otherwise land in the middle
    of the provider block and get read as the company's own details.
    """
    tree = HTMLParser(html)
    for tag in ("script", "style", "noscript", "template"):
        for node in tree.css(tag):
            node.decompose()
    return " ".join((tree.text(separator=" ") or "").split())


def provider_block(text: str) -> str | None:
    """The window most likely to hold the provider's own details, or `None`."""
    for match in _BLOCK_ANCHORS.finditer(text):
        window = text[match.end() : match.end() + _PROVIDER_WINDOW]
        if _ADDRESS.search(window):
            return window
    return None


def legal_form(html: str) -> str | None:
    """The provider's legal form from Impressum HTML, or `None` if unstated.

    `None` is common and correct: a German sole trader is not required to name
    a legal form, and five of the twelve Impressum pages in the first crawl
    state none. It must not be read as "not a real business" — those five are
    the owner-operated shops the tool is looking for (§10.2).
    """
    block = provider_block(visible_text(html))
    if block is None:
        return None
    for name, pattern in _COMPILED_FORMS:
        if pattern.search(block):
            return name
    return None


# ── blog index ──────────────────────────────────────────────────────────

_GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
    "jan": 1,
    "feb": 2,
    "mär": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "nov": 11,
    "dez": 12,
}
_GERMAN_DATE = re.compile(
    r"\b(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\.?\s+(\d{4})\b", re.IGNORECASE
)
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
#: An ISO date, **including one that opens a timestamp** (M1.31).
#:
#: This was `\b(\d{4})-(\d{2})-(\d{2})\b`, and the trailing `\b` is the bug: in
#: `2026-05-29T10:56:32+0200` the `9` and the `T` are both word characters, so
#: there is no boundary between them and the match failed. A timestamp is what
#: `datePublished` and `<time datetime>` actually carry — 4 of 7 blogs in the
#: corpus reported no date at all because of it, and it had been unmeasurable
#: from the index alone, so nothing else exposed it.
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def _parse_iso(value: str) -> date | None:
    match = _ISO_DATE.search(value)
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def parse_german_date(text: str) -> date | None:
    """`12. März 2023`, `12.03.2023` or an ISO date — whichever appears first."""
    if match := _GERMAN_DATE.search(text):
        month = _GERMAN_MONTHS.get(match[2].lower().rstrip("."))
        if month:
            try:
                return date(int(match[3]), month, int(match[1]))
            except ValueError:
                return None
    if match := _NUMERIC_DATE.search(text):
        try:
            return date(int(match[3]), int(match[2]), int(match[1]))
        except ValueError:
            return None
    return _parse_iso(text)


def _jsonld_dates(html: str) -> list[date]:
    found: list[date] = []

    def collect(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            for key in ("datePublished", "dateCreated", "dateModified"):
                value = node.get(key)
                if isinstance(value, str) and (parsed := _parse_iso(value)):
                    found.append(parsed)
            for value in node.values():
                collect(value)

    for document in _jsonld_documents(html):
        collect(document)
    return found


def newest_post_date(html: str, today: date | None = None) -> date | None:
    """The newest post date on a blog index, by §5.3's precedence.

    JSON-LD `datePublished` first, then `<time datetime>`, then German visible
    dates. Sitemap `<lastmod>` is **never** consulted here — it is regenerated
    on deploys by Shopware and WordPress and systematically lies fresh, which
    is the whole reason this parser reads the index HTML instead.

    Future dates are discarded rather than trusted: a scheduled post dated next
    month would otherwise make a dead blog look current, which is the exact
    error `opp.blog_stale` exists to catch.
    """
    horizon = today or datetime.now(UTC).date()
    candidates = [d for d in _jsonld_dates(html) if d <= horizon]

    tree = HTMLParser(html)
    for node in tree.css("time[datetime]"):
        parsed = _parse_iso(node.attributes.get("datetime") or "")
        if parsed is not None and parsed <= horizon:
            candidates.append(parsed)

    if not candidates:
        text = visible_text(html)
        for chunk in text.split(" | "):
            parsed = parse_german_date(chunk)
            if parsed is not None and parsed <= horizon:
                candidates.append(parsed)
        if not candidates:
            parsed = parse_german_date(text)
            if parsed is not None and parsed <= horizon:
                candidates.append(parsed)

    return max(candidates) if candidates else None


@dataclass(frozen=True)
class BlogIndex:
    """What a blog index page yields. `post_count is None` means not counted."""

    post_count: int | None
    newest_post: date | None
    article_schema: bool


def _is_post_link(node: Node, blog_path: str, index_path: str | None) -> bool:
    """A link to a post under `blog_path`, excluding the index page itself.

    Excluding `index_path` is what stops the index counting its own "back to
    overview" link and its pagination. It cannot be inferred from `blog_path`:
    on Shopify the path prefix is `/blogs` while the index is `/blogs/news`, so
    a rule like "one segment deeper than the prefix" counts the index as a post.
    """
    href = (node.attributes.get("href") or "").split("#")[0].split("?")[0]
    if blog_path not in href:
        return False
    if index_path and href.rstrip("/").endswith(index_path.rstrip("/")):
        return False
    remainder = href.split(blog_path, 1)[1].strip("/")
    return bool(remainder)


def read_blog_index(
    html: str,
    blog_path: str,
    index_path: str | None = None,
    today: date | None = None,
) -> BlogIndex:
    """Post count, newest date and article schema from a blog index page."""
    tree = HTMLParser(html)
    links = {
        (node.attributes.get("href") or "").split("#")[0].split("?")[0]
        for node in tree.css("a[href]")
        if _is_post_link(node, blog_path, index_path)
    }
    return BlogIndex(
        post_count=len(links) or None,
        newest_post=newest_post_date(html, today),
        article_schema=has_article_schema(html),
    )
