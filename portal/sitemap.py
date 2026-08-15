"""Sitemap parsing: index expansion, gzip shards, URL extraction.

Uses stdlib `xml.etree.ElementTree`. That parser is documented as vulnerable to
entity-expansion attacks, and these documents come from third parties, so the
mitigations are: a document declaring a DTD or an entity is refused before it
reaches the parser at all, bodies are capped before they reach here
(`net.MAX_BODY_BYTES`), a shard budget caps how many documents one company can
make us parse, and every parse failure is swallowed into "no URLs" rather than
aborting a run.

The DTD refusal is what closes the entity-expansion class, and it is preferred
over `defusedxml` because the dependency list is deliberately locked: a sitemap
has no legitimate reason to carry a DTD, so refusing one costs nothing.
"""

from __future__ import annotations

import gzip
import re
from xml.etree import ElementTree

from portal.urls import path_of

#: Cap on shards followed per company, so a hostile or broken sitemap index
#: cannot turn one company into thousands of requests.
MAX_SHARDS = 50

_TAG = re.compile(r"^\{[^}]*\}")

#: A sitemap declaring a DTD or an entity is refused unparsed — that is the
#: whole entity-expansion attack surface, and no real sitemap needs either.
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

#: Platform-specific product sitemaps, per §5.2 Tier 1.
_PRODUCT_SITEMAP_PATTERNS = (
    re.compile(r"-product-.*\.xml(\.gz)?$", re.IGNORECASE),  # Shopware 6
    re.compile(r"sitemap_products?[-_0-9]*\.xml(\.gz)?$", re.IGNORECASE),  # Shopify
    re.compile(
        r"product-sitemap[-_0-9]*\.xml(\.gz)?$", re.IGNORECASE
    ),  # WooCommerce / Yoast
    re.compile(r"/sitemap/product", re.IGNORECASE),  # JTL and assorted
)

#: Words in a shard's own filename, read as **semantics** rather than matched
#: against a list of platform filename conventions (M1.24).
#:
#: The convention list above answers "is this Shopify's product sitemap?"; this
#: answers "did this site tell us what is in this shard?". A sitemap index is a
#: labelled table of contents, and a shop that names its shards is describing
#: its own catalogue in a way no convention list can anticipate.
#:
#: Marked by evidence, per M1.9 — a word is `observed` only where a real shop in
#: the corpus was seen serving it:
#:
#:   observed   `articles`  — smile-store.de, Shopware 5 + PixupSitemap (n=1).
#:                            "Artikel" is the ordinary German commerce word for
#:                            a saleable product, and this shard held all 194 of
#:                            the shop's products while the four filename
#:                            conventions matched none of them.
#:   observed   `products`  — Shopify, WooCommerce/Yoast, Shopware 6 (also
#:                            already covered by the patterns above).
#:   UNOBSERVED `artikel`, `produkte` — the German forms of the same two words.
#:                            No shop in the corpus serves either. They are
#:                            listed because a shop that labels its shards in
#:                            German is the same shop that would label them
#:                            `articles`, but nothing here has been seen.
#:
#: **Known ambiguity, unresolved by evidence:** in English-language CMS usage
#: `article` means a *blog post*, not a product. Nothing in the corpus exercises
#: that reading — Yoast names its post shard `post-sitemap.xml`, not
#: `article-sitemap.xml` — so it is recorded as a risk rather than mitigated by
#: a guess. `_BLOG_SHARD_WORDS` is tested first, which catches the compound
#: cases (`blog-articles-…`) but not a bare `articles` shard that means posts.
_PRODUCT_SHARD_WORDS = frozenset(
    {"product", "products", "article", "articles", "artikel", "produkte"}
)

#: The same reading, for content. Used to answer M1.14's third-instrument
#: question and to locate blog article URLs without a path vocabulary.
_BLOG_SHARD_WORDS = frozenset(
    {
        "blog",
        "blogs",
        "magazin",
        "magazine",
        "news",
        "ratgeber",
        "journal",
        "post",
        "posts",
    }
)

_WORD = re.compile(r"[a-z]+", re.IGNORECASE)

#: Filename scaffolding, present in every shard name and meaning nothing.
_NOISE_WORDS = frozenset({"sitemap", "sitemaps", "xml", "gz", "index", "area"})


def shard_words(url: str) -> frozenset[str]:
    """The meaningful words in a shard's own filename, lowercased.

    `…/area/articles-0-sitemap.xml` → `{"articles"}`;
    `…/sitemap_collections_1.xml` → `{"collections"}`. Digits and the
    scaffolding words are dropped, so a shard is compared on what it says it
    holds rather than on how the platform spells its filenames.
    """
    segment = path_of(url).rsplit("/", 1)[-1]
    return frozenset(
        word for w in _WORD.findall(segment) if (word := w.lower()) not in _NOISE_WORDS
    )


def shard_kind(url: str) -> str | None:
    """`"product"`, `"blog"` or `None`, from the shard's name alone (M1.24).

    Blog is tested first: a shard named `blog-articles-…` is posts, and the
    product vocabulary contains `articles` for the German commerce sense.
    """
    words = shard_words(url)
    if words & _BLOG_SHARD_WORDS:
        return "blog"
    if words & _PRODUCT_SHARD_WORDS:
        return "product"
    return None


def _strip_ns(tag: str) -> str:
    return _TAG.sub("", tag)


#: The sitemaps schema. Extensions — image, video, news, xhtml — declare their
#: own, and their elements are metadata about a page rather than pages.
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _is_sitemap_loc(tag: str) -> bool:
    """A `<loc>` that addresses a page, not an `<image:loc>` that decorates one.

    A namespace-less document is accepted: real sitemaps in the wild omit the
    declaration, and an extension element cannot appear without one.
    """
    if tag.startswith("{"):
        return tag == f"{_SITEMAP_NS}loc"
    return tag == "loc"


def decompress(body: bytes, url: str) -> bytes:
    """Gunzip a `.xml.gz` shard. Shopware ships these by default."""
    looks_gzipped = url.lower().endswith(".gz") or body[:2] == b"\x1f\x8b"
    if not looks_gzipped:
        return body
    try:
        return gzip.decompress(body)
    except (OSError, EOFError):
        return body


def is_product_sitemap(url: str) -> bool:
    """Tier 1 detection, matched against the **path** (M1.13).

    Matching the whole URL is what made Tier 1 dead code: the patterns anchor
    on `$`, and Shopify serves its product sitemap as
    `sitemap_products_1.xml?from=…&to=…`, so the `.xml` is not at the end of
    the URL and every one of the seven Shopify shops in the first crawl fell
    through to Tier 2. The filename convention was right all along; the input
    was wrong. Query strings are addressing, not identity.

    Two instruments, in order of specificity (M1.24). The convention list
    identifies a platform's known filename; `shard_kind` reads whatever name
    the shop chose. `smile-store.de` publishes its whole catalogue as
    `area/articles-0-sitemap.xml`, which no convention list would have
    anticipated and which the shop labelled perfectly clearly.
    """
    if any(pattern.search(path_of(url)) for pattern in _PRODUCT_SITEMAP_PATTERNS):
        return True
    return shard_kind(url) == "product"


def is_blog_sitemap(url: str) -> bool:
    """A shard the site labels as content rather than catalogue (M1.24).

    Not a blog *detector*: §10.1 records that it reaches neither of the two
    shapes `content.blog_exists` misses.
    """
    return shard_kind(url) == "blog"


def parse(body: bytes, url: str) -> tuple[list[str], list[str]]:
    """Return `(child_sitemap_urls, page_urls)` for one sitemap document.

    A `<sitemapindex>` yields children; a `<urlset>` yields pages. Anything
    unparseable — or anything carrying a DTD — yields both empty, because a
    broken or hostile sitemap is a missing signal, not a crash.

    **`<image:loc>` is not a page.** Shopify emits one per product, in the
    image extension's own namespace, and stripping namespaces before comparing
    tag names made every product sitemap look twice its size. The shop's own
    images happen to sit on `cdn.shopify.com`, so `same_site` discarded them
    before anything counted them — but a shop serving its images from
    `/products/…` on its own domain would have had every product counted twice
    by a rule that thought it was looking at pages.
    """
    document = decompress(body, url)
    if _DTD.search(document):
        return [], []
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return [], []

    children: list[str] = []
    pages: list[str] = []
    root_tag = _strip_ns(root.tag)
    for element in root.iter():
        if not _is_sitemap_loc(element.tag) or not (element.text or "").strip():
            continue
        location = element.text.strip()
        if root_tag == "sitemapindex":
            children.append(location)
        else:
            pages.append(location)
    return children, pages
