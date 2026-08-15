"""Builders for the fixture shop sites used by the M1 tests.

Structural markup is realistic; every name, address and phone number is
`Max Mustermann` / `Musterstraße 1`. Nothing here is copied from a real site,
so there is no personal data in the repo (see the implementation brief).
"""

from __future__ import annotations

from tests.fixture_server import Site

IMPRESSUM_HTML = """<!doctype html><html lang="de"><body>
<h1>Impressum</h1>
<p>Muster Handel GmbH<br>Musterstraße 1<br>50667 Musterstadt</p>
<p>Vertreten durch: Max Mustermann</p>
<p>Telefon: 0221 1234567<br>E-Mail: info@muster.example</p>
<p>USt-IdNr.: DE123456789</p>
</body></html>"""


def product_html(handle: str) -> str:
    """Distinct per product — two pages with byte-identical bodies would share a
    `content_hash` and collapse into one `artifact` row (§4)."""
    return (
        '<!doctype html><html lang="de"><body>'
        f"<h1>Schallzahnbürste {handle}</h1><p>149,00 €</p>"
        "</body></html>"
    )


BLOG_HTML = """<!doctype html><html lang="de"><body>
<h1>Magazin</h1><article><time datetime="2023-03-12">12. März 2023</time></article>
</body></html>"""


def homepage(footer_impressum: bool = True, extra_links: str = "") -> str:
    footer = (
        '<footer><a href="/impressum">Impressum</a><a href="/agb">AGB</a></footer>'
        if footer_impressum
        else '<footer><a href="/agb">AGB</a></footer>'
    )
    return f"""<!doctype html><html lang="de"><head><title>Muster Shop</title></head>
<body>
<nav><a href="/magazin">Magazin</a><a href="/kategorie/buersten">Bürsten</a></nav>
{extra_links}
{footer}
</body></html>"""


def urlset(locations: list[str]) -> str:
    entries = "".join(f"<url><loc>{loc}</loc></url>" for loc in locations)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


def sitemapindex(locations: list[str]) -> str:
    entries = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locations)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</sitemapindex>"
    )


def shopware_shop(base: str, robots_txt: str = "User-agent: *\nAllow: /\n") -> Site:
    """A Shopware-shaped shop: gzipped multi-shard product sitemap, a content
    sitemap mixed into the same index, a footer Impressum link, and a blog."""
    site = Site()
    site.add("/robots.txt", robots_txt, content_type="text/plain")
    site.add("/", homepage())
    site.add("/impressum", IMPRESSUM_HTML)
    site.add("/magazin", BLOG_HTML)

    site.add(
        "/sitemap.xml",
        sitemapindex(
            [
                f"{base}/sitemap-muster-product-1.xml.gz",
                f"{base}/sitemap-muster-product-2.xml.gz",
                f"{base}/sitemap-muster-content-1.xml.gz",
            ]
        ),
        content_type="application/xml",
    )
    # Shard 2 holds the code-point minimum, so a rule that read only the first
    # shard, or trusted document order, would pick differently.
    site.add_gzip(
        "/sitemap-muster-product-1.xml.gz",
        urlset([f"{base}/detail/zebra-buerste", f"{base}/detail/mango-buerste"]),
    )
    site.add_gzip(
        "/sitemap-muster-product-2.xml.gz",
        urlset([f"{base}/detail/alpha-buerste", f"{base}/detail/beta-buerste"]),
    )
    site.add_gzip(
        "/sitemap-muster-content-1.xml.gz",
        urlset(
            [f"{base}/magazin/zahnpflege", f"{base}/ueber-uns", f"{base}/impressum"]
        ),
    )

    for handle in ("zebra-buerste", "mango-buerste", "alpha-buerste", "beta-buerste"):
        site.add(f"/detail/{handle}", product_html(handle))
    site.add("/magazin/zahnpflege", BLOG_HTML)
    site.add("/ueber-uns", "<html><body>Über uns</body></html>")
    site.add("/agb", "<html><body>AGB</body></html>")
    return site


def flat_shop(
    base: str, footer_impressum: bool = False, with_impressum: bool = True
) -> Site:
    """A shop with one plain sitemap mixing content and product URLs, and no
    footer Impressum link — so the two-step's direct probing has to run."""
    site = Site()
    site.add("/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain")
    site.add("/", homepage(footer_impressum=footer_impressum))
    site.add(
        "/sitemap.xml",
        urlset(
            [
                f"{base}/ueber-uns",
                f"{base}/kategorie/buersten",
                f"{base}/produkt/beta",
                f"{base}/produkt/alpha",
                f"{base}/magazin/tipps",
            ]
        ),
        content_type="application/xml",
    )
    site.add("/produkt/alpha", product_html("alpha"))
    site.add("/produkt/beta", product_html("beta"))
    site.add("/magazin", BLOG_HTML)
    site.add("/magazin/tipps", BLOG_HTML)
    site.add("/ueber-uns", "<html><body>Über uns</body></html>")
    if with_impressum:
        site.add("/impressum", IMPRESSUM_HTML)
    return site


def catalogue_free_shop(base: str) -> Site:
    """A site with no product URLs at all — the zero-candidates case."""
    site = Site()
    site.add("/robots.txt", "User-agent: *\nAllow: /\n", content_type="text/plain")
    site.add("/", homepage())
    site.add("/impressum", IMPRESSUM_HTML)
    site.add(
        "/sitemap.xml",
        urlset([f"{base}/ueber-uns", f"{base}/kontakt"]),
        content_type="application/xml",
    )
    site.add("/ueber-uns", "<html><body>Über uns</body></html>")
    site.add("/kontakt", "<html><body>Kontakt</body></html>")
    return site
