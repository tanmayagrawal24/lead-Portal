"""Impressum two-step discovery and blog-path detection (§5.2).

Both are HTML inspection, not extraction: they decide *what to fetch next*.
The signals read off these pages belong to `extract-p1` (§5.3, M2), and
nothing here writes one.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from portal.urls import absolutise, path_of, same_site

#: §5.2 step 1 — footer link text or href.
IMPRESSUM_LINK = re.compile(
    r"impressum|imprint|legal[\s_-]*notice|rechtliches", re.IGNORECASE
)

#: §5.2 step 2 — direct probes, in order.
IMPRESSUM_PROBE_PATHS = (
    "/impressum",
    "/impressum/",
    "/imprint",
    "/legal",
    "/rechtliches",
)

#: §5.3 `content.blog_exists` vocabulary. Used here only to decide what to fetch.
BLOG_SEGMENTS = ("blog", "magazin", "ratgeber", "news", "journal", "tipps")

_BLOG_PATH = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(" + "|".join(BLOG_SEGMENTS) + r")(?:/|$)",
    re.IGNORECASE,
)


def links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Every resolvable link on the page, as `(absolute_url, anchor_text)`."""
    found: list[tuple[str, str]] = []
    for node in HTMLParser(html).css("a[href]"):
        url = absolutise(base_url, node.attributes.get("href") or "")
        if url:
            found.append((url, (node.text() or "").strip()))
    return found


def footer_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Links inside `<footer>`, falling back to the whole document.

    The fallback is deliberate: plenty of small German shops mark up the footer
    as a plain `<div class="footer">`, and §5.2's two-step exists to avoid
    recording `no_impressum` for a site that plainly has one. A false positive
    here costs one extra request; a false negative costs a lead.
    """
    tree = HTMLParser(html)
    footers = tree.css("footer")
    if footers:
        collected: list[tuple[str, str]] = []
        for footer in footers:
            for node in footer.css("a[href]"):
                url = absolutise(base_url, node.attributes.get("href") or "")
                if url:
                    collected.append((url, (node.text() or "").strip()))
        if collected:
            return collected
    return links(html, base_url)


def find_impressum_link(html: str, base_url: str, domain: str) -> str | None:
    """Step 1: a footer link that looks like an Impressum, on our own domain."""
    for url, text in footer_links(html, base_url):
        if not same_site(url, domain):
            continue
        if IMPRESSUM_LINK.search(text) or IMPRESSUM_LINK.search(path_of(url)):
            return url
    return None


def probe_urls(base: str) -> list[str]:
    """Step 2: direct paths to try before concluding absence."""
    return [f"{base}{path}" for path in IMPRESSUM_PROBE_PATHS]


def find_blog_path(
    sitemap_urls: list[str], homepage_html: str, base_url: str, domain: str
) -> str | None:
    """The blog path, from sitemap URLs or homepage nav links (§5.3).

    Returns the path prefix (`/magazin`), not a full URL, because §5.2's A5
    filter 4 needs a prefix to exclude candidates under it.
    """
    for url in sorted(sitemap_urls):
        if same_site(url, domain) and (match := _BLOG_PATH.match(path_of(url))):
            return match.group(0).rstrip("/")
    for url, _text in links(homepage_html, base_url):
        if same_site(url, domain) and (match := _BLOG_PATH.match(path_of(url))):
            return match.group(0).rstrip("/")
    return None


def blog_index_url(base: str, blog_path: str) -> str:
    return f"{base}{blog_path}"
