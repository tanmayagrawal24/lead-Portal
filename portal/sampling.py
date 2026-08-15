"""The A5 product-sample selection rule (§5.2).

Which product page is sampled decides a +10 rule, so it is a stated rule rather
than whatever the crawler happened to hit first. The guarantee is *same inputs
→ same choice*: a catalog that changes may legitimately change the sample, but
two runs over identical inputs must not differ.

Tier 0 (reuse) lives in `portal.fetch`, because it needs the database and a
live HTTP check. Everything here is pure.
"""

from __future__ import annotations

import re

from portal.urls import has_query, path_of, same_site

#: §5.2 Tier 2 path patterns.
#:
#: `/p/` is deliberately absent. It is a product prefix on some shops and a
#: *pagination* prefix on others, and the two failure modes are not symmetric:
#: a false positive feeds a listing page to `schema.product_present` and wrongly
#: awards +10, while a false negative just leaves the signal unwritten, which
#: A5.5 already handles correctly. It goes back in when it is observed in the
#: wild, not before.
PRODUCT_PATH_PATTERNS = ("/detail/", "/products/", "/produkt/")

#: Rejected outright — listing pages, not products.
CATEGORY_PATH_PATTERNS = ("/kategorie/", "/collections/", "/c/", "/categories/")

_TRAILING_SLASHES = re.compile(r"/+$")

#: A locale storefront root: `/de-at`, `/en`, `/fr-ch`. Shopify lists it inside
#: the *product* sitemap of each locale, so Tier 1 — which trusts membership
#: rather than the path (`require_pattern=False`) — would otherwise select it as
#: a product. It is a storefront home page: real listing, no product.
#:
#: Observed on ekomia.de (`/de-at`), navucko.com (`/en`) and snocks.com
#: (`/de-ch`) once M1.13 revived Tier 1 — a listing page reaching
#: `schema.product_present` is precisely the +10 error M1.4 dropped `/p/` over.
_LOCALE_ROOT = re.compile(r"^/[a-z]{2}(-[a-z]{2})?$", re.IGNORECASE)


def is_secondary_locale(
    url: str, primary: str = "", secondary: tuple[str, ...] = ()
) -> bool:
    """True when `url` is a translation of something counted elsewhere (M1.25).

    Two instruments, because neither alone covers the corpus:

    1. **Declared.** `smile-store.de` serves its English subshop from
       `/shop/en/` and says so in an `hreflang` alternate. No path-shape rule
       would recognise `/shop/en` as a locale.
    2. **Shaped.** `snocks.com` serves ten locales and declares only three of
       them; the other seven (`/fr-fr`, `/pl-pl`, `/en-es`, …) are visible only
       as a leading segment of locale-code shape — the same shape M1.16 already
       treats as a storefront root rather than a product.

    Where the primary storefront *is* under a prefix, it is never secondary:
    the point is to drop a shop's siblings, not its own catalogue.
    """
    path = path_of(url).rstrip("/") or "/"
    for prefix in secondary:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    first = f"/{path.split('/')[1]}" if path.count("/") >= 1 and len(path) > 1 else ""
    return bool(first and first != primary and _LOCALE_ROOT.match(first))


def _segment_after_pattern(path: str, pattern: str) -> str:
    """The path remainder after `pattern`, with trailing slashes removed."""
    index = path.lower().find(pattern)
    if index < 0:
        return ""
    return _TRAILING_SLASHES.sub("", path[index + len(pattern) :])


def is_product_candidate(
    url: str, domain: str, blog_path: str | None = None, require_pattern: bool = True
) -> bool:
    """Apply the four §5.2 filters.

    `require_pattern=False` is used for URLs that already came from a
    platform-specific *product* sitemap, where membership is itself the
    evidence and the path need not match a known pattern — Shopware shops with
    rewritten SEO URLs (`/schallzahnbuerste-pro/`) would otherwise yield no
    candidates at all.
    """
    if not same_site(url, domain):
        return False
    if has_query(url):  # filter 1: variant and filter permutations
        return False

    path = path_of(url)
    lowered = path.lower()

    # filter 3: category and listing patterns
    if any(pattern in lowered for pattern in CATEGORY_PATH_PATTERNS):
        return False

    # filter 4: anything under the detected blog path
    if blog_path and lowered.startswith(blog_path.lower().rstrip("/") + "/"):
        return False

    if require_pattern:
        # filter 2: a path segment must follow the pattern; bare `/products/`
        # is Shopify's collection listing, not a product.
        matched = [p for p in PRODUCT_PATH_PATTERNS if p in lowered]
        if not matched:
            return False
        if not any(_segment_after_pattern(path, p) for p in matched):
            return False
    else:
        # The homepage is never a product page — and a multi-locale shop has
        # more than one homepage. `/de-at` is the German-Austrian storefront
        # root, not a product, however it got into a product sitemap.
        bare = _TRAILING_SLASHES.sub("", path)
        if bare in ("", "/") or _LOCALE_ROOT.match(bare):
            return False

    return True


def select(candidates: list[str]) -> str | None:
    """The lexicographically smallest candidate, by Unicode code point.

    `min` on `str` compares code points, which is the point: locale collation
    would make the choice depend on the machine's locale, and a corpus full of
    umlauts is exactly where that bites. Chosen over document order because
    Shopware re-shards product sitemaps on a schedule — document order is
    stable only by accident, while a code-point minimum over the union is
    invariant to reordering and to redistribution between shards.
    """
    return min(candidates) if candidates else None


def choose_product_sample(
    product_sitemap_urls: list[str],
    sitemap_urls: list[str],
    homepage_links: list[str],
    domain: str,
    blog_path: str | None = None,
    exclude: set[str] | None = None,
) -> tuple[str | None, str]:
    """Run Tiers 1→2→3 and return `(chosen_url, tier_label)`.

    The label names the tier that produced the choice, for the handoff note and
    for `catalog.product_sample_url`'s provenance. Returns `(None, "none")`
    when no tier yields a candidate — the caller must then write no
    `schema.product_present` signal at all, not a `0` (§5.2).

    `exclude` carries URLs already known dead this run, which is what makes
    A5.1's "discard it and re-select" mean something: without it, a Tier 0
    sample that just 404'd is still the code-point minimum and would simply be
    chosen again.
    """
    dead = exclude or set()

    def usable(urls: list[str], require_pattern: bool) -> list[str]:
        return [
            url
            for url in urls
            if url not in dead
            and is_product_candidate(
                url, domain, blog_path, require_pattern=require_pattern
            )
        ]

    if tier_1 := usable(product_sitemap_urls, require_pattern=False):
        return select(tier_1), "product_sitemap"
    if tier_2 := usable(sitemap_urls, require_pattern=True):
        return select(tier_2), "sitemap_path_pattern"
    if tier_3 := usable(homepage_links, require_pattern=True):
        return select(tier_3), "homepage_links"
    return None, "none"
