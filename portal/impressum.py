"""Impressum two-step discovery and blog-path detection (§5.2).

Both are HTML inspection, not extraction: they decide *what to fetch next*.
The signals read off these pages belong to `extract-p1` (§5.3, M2), and
nothing here writes one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser

from portal.urls import absolutise, host_of, path_of, same_site

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
#:
#: `blogs` is Shopify's, and its absence made every Shopify blog invisible
#: (M1.14): five shops in the first crawl reported "no blog path found" while
#: publishing actively, one of them with 670 blog URLs. Listed before `blog` for
#: readability only — the alternation is followed by `(?:/|$)`, so `/blogs/`
#: could never have matched `blog` whichever way round they sit.
#:
#: Observed in that crawl: `blogs` on 5 Shopify shops, `magazin` on Shopware 5.
#: The rest are still convention. Two real blogs remain undetectable by *any*
#: entry in this tuple, because the vocabulary is the wrong instrument for them
#: — see M1.14.
BLOG_SEGMENTS = ("blogs", "blog", "magazin", "ratgeber", "news", "journal", "tipps")

_BLOG_PATH = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(" + "|".join(BLOG_SEGMENTS) + r")(?:/|$)",
    re.IGNORECASE,
)

#: The same vocabulary, read off the **anchor text** instead of the path (M1.14).
#:
#: Two real blogs in the corpus are unreachable by any path vocabulary, and both
#: are reachable by this one: `zecplus.de` links *"Blog"* at `blog.zecplus.de`, a
#: host its own sitemap never mentions, and `lampenflut.de` links
#: *"Licht-Ratgeber"* at a root-level slug no path pattern can distinguish from a
#: category. Measured across the six shops carrying `content.blog_exists = 0`:
#: 2 true positives, 4 true negatives, on homepages already on disk.
#:
#: The boundaries are letter-class rather than `\b` because German compounds
#: hyphenate: `Licht-Ratgeber` must match on `ratgeber` while `Newsletter` — the
#: single commonest footer link in the corpus — must not match on `news`.
_BLOG_ANCHOR = re.compile(
    r"(?<![^\W\d_])(" + "|".join(BLOG_SEGMENTS) + r")(?![^\W\d_])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BlogLocation:
    """Where a blog is, and how much of that is a path we may filter with.

    `path` and `url` are separate because M1.14's two shapes separate them. A
    blog reached by path vocabulary has a prefix and no address of its own; a
    blog reached by anchor text has an address and, when it lives on the shop's
    own host, a prefix as well. On a *different* host — `blog.zecplus.de` — the
    prefix is `None` and must stay `None`: the only path such a blog has is `/`,
    and handing `/` to §5.2's filter 4 would reject the shop's entire catalogue
    as blog content.
    """

    path: str | None
    url: str | None
    basis: str  # 'path_vocabulary' | 'anchor_text'


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


def find_blog_link(homepage_html: str, base_url: str, domain: str) -> str | None:
    """A homepage link whose **anchor text** is blog vocabulary (M1.14).

    The href is taken **wherever it points**, subdomains and foreign hosts
    included. That is not a loosening to be tightened later — it is the whole
    reason the instrument reaches `zecplus.de`, whose blog is a host rather than
    a path and appears in no sitemap the shop publishes.

    *Ordering*, because a homepage may carry several: same-site links before
    foreign ones, then shallowest path, then the code-point minimum, matching
    `find_blog_index_url` and A5.3. Same-site first is the load-bearing part —
    it is what stops a footer link reading *"News"* and pointing at a press
    portal from outranking the shop's own `/magazin`. Shallowest is next because
    an index sits above its posts: `lampenflut.de` links *"Licht-Ratgeber"* and
    *"Mehr News …"* at the same page, and either would do.

    A link to our **own** front page is skipped: a *"Blog"* in the navigation
    pointing at `/` is navigation, not a blog. The test is host-aware, because a
    root path on the blog's own host is exactly what `blog.zecplus.de` serves.
    """
    candidates = [
        (0 if same_site(url, domain) else 1, _depth(url), url)
        for url, text in links(homepage_html, base_url)
        if _BLOG_ANCHOR.search(text)
        and not (host_of(url) == host_of(base_url) and not path_of(url).strip("/"))
    ]
    return min(candidates)[2] if candidates else None


def locate_blog(
    sitemap_urls: list[str], homepage_html: str, base_url: str, domain: str
) -> BlogLocation | None:
    """Both §5.3 blog instruments, path vocabulary first (M1.14).

    **The order is the precision control**, not a preference. Anchor text is the
    broader instrument and its one measured weakness is a shop with no blog and
    a *"Ratgeber"* link to a static advice page. Running it second means it is
    never consulted for a shop whose blog a path already found — which is
    precisely where its two known loose matches live (`doonails.de`'s *"Tipps &
    Tricks"* → a Shopify page, `ekomia.de`'s *"Ratgeber"* → a tag listing). Both
    shops have a path-detected blog, so neither match is ever reached.
    """
    if path := find_blog_path(sitemap_urls, homepage_html, base_url, domain):
        return BlogLocation(path=path, url=None, basis="path_vocabulary")
    if link := find_blog_link(homepage_html, base_url, domain):
        return BlogLocation(
            path=_own_host_prefix(link, base_url), url=link, basis="anchor_text"
        )
    return None


def _own_host_prefix(url: str, base_url: str) -> str | None:
    """The path prefix a located blog contributes, or `None` off our own host.

    The **full** path, not its first segment: the prefix's job is to keep blog
    URLs out of the catalogue count and the product sample (§5.2 filter 4), and
    a `/service/ratgeber` blog must not take all of `/service` with it.
    """
    if host_of(url) != host_of(base_url):
        return None
    return path_of(url).rstrip("/") or None


def _depth(url: str) -> int:
    return len([segment for segment in path_of(url).strip("/").split("/") if segment])


def find_blog_index_url(
    blog_path: str,
    sitemap_urls: list[str],
    homepage_html: str,
    base_url: str,
    domain: str,
) -> str | None:
    """The shallowest URL actually *observed* under `blog_path` (M1.15).

    Synthesising `base + blog_path` was wrong on every shop that has a blog:
    all seven blog-index fetches in `run 2` returned 404. `/blogs` is not a
    page on Shopify — `/blogs/news` is — and `smile-store.de` serves articles
    at `/magazin/<kategorie>/<artikel>` with nothing at the bare segment. The
    path prefix is a good filter and a bad address.

    Shallowest wins because a blog index sits above its articles; ties break on
    the code-point minimum, for the same reproducibility reason as A5.3.
    Homepage nav links are preferred over sitemap URLs at equal depth: a link a
    human put in the navigation is far likelier to be the index than an
    arbitrary article that happens to sort first.
    """
    prefix = blog_path.rstrip("/") + "/"

    def under(url: str) -> bool:
        path = path_of(url)
        return same_site(url, domain) and (
            path.rstrip("/") == blog_path.rstrip("/") or path.startswith(prefix)
        )

    nav = [url for url, _text in links(homepage_html, base_url) if under(url)]
    observed = [(_depth(u), 0, u) for u in nav] + [
        (_depth(u), 1, u) for u in sitemap_urls if under(u)
    ]
    return min(observed)[2] if observed else None


def blog_index_url(base: str, blog_path: str) -> str:
    """Fallback only: a synthesised index for when nothing was observed."""
    return f"{base}{blog_path}"
