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


def _strip_ns(tag: str) -> str:
    return _TAG.sub("", tag)


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
    return any(pattern.search(url) for pattern in _PRODUCT_SITEMAP_PATTERNS)


def parse(body: bytes, url: str) -> tuple[list[str], list[str]]:
    """Return `(child_sitemap_urls, page_urls)` for one sitemap document.

    A `<sitemapindex>` yields children; a `<urlset>` yields pages. Anything
    unparseable — or anything carrying a DTD — yields both empty, because a
    broken or hostile sitemap is a missing signal, not a crash.
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
        if _strip_ns(element.tag) != "loc" or not (element.text or "").strip():
            continue
        location = element.text.strip()
        if root_tag == "sitemapindex":
            children.append(location)
        else:
            pages.append(location)
    return children, pages
