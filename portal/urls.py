"""URL and domain normalisation.

`company.domain` is normalised per §4: lowercase, no scheme, no www. Every
comparison in the fetch stage goes through here so that "is this the same
host" has exactly one answer.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit


def normalise_domain(raw: str) -> str:
    """`https://WWW.Example.de/shop?a=1` → `example.de`.

    Raises ValueError on input with no host, so a malformed seed row fails at
    load time rather than becoming a company row that can never be fetched.
    """
    value = raw.strip().rstrip(".")
    if not value:
        raise ValueError("empty domain")
    if "//" not in value:
        value = f"//{value}"
    host = urlsplit(value).hostname
    if not host:
        raise ValueError(f"no host in {raw!r}")
    host = host.lower()
    host = host.removeprefix("www.")
    if "." not in host:
        raise ValueError(f"not a domain: {raw!r}")
    return host


def default_base(domain: str) -> str:
    """How to reach a domain: `https://` with no port, in production.

    Injectable (see `portal.fetch.FetchStage`) so the test suite can point the
    same code at a loopback fixture server over http, without production code
    growing a "is this localhost?" special case.
    """
    return f"https://{domain}"


def homepage_url(base: str) -> str:
    return f"{base}/"


def absolutise(base: str, href: str) -> str | None:
    """Resolve `href` against `base`, dropping fragments and non-HTTP schemes."""
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    joined = urljoin(base, href.strip())
    parts = urlsplit(joined)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def authority_of(url: str) -> str:
    """The netloc — who answers for this URL, and therefore whose robots.txt
    governs it. Lowercased, userinfo dropped, port and `www.` both kept.

    `www.example.de` and `example.de` are separate authorities: RFC 9309 keys
    robots.txt to the origin, and the two may serve different files. Compare
    with `host_of`, which answers a different question.
    """
    netloc = urlsplit(url).netloc.lower()
    _before, _at, hostport = netloc.rpartition("@")  # drop any userinfo
    return hostport


def host_of(url: str) -> str:
    """The rate-limiter and concurrency key: `authority_of` minus a `www.`
    prefix.

    `www.` is stripped because `example.de` and `www.example.de` are one
    machine with one budget. Nearly every shop redirects apex→www, so treating
    them as two budgets would let each back-to-back redirect pair issue two
    requests to one server inside a second — double §5.2's floor, on almost
    every domain in the corpus.

    The port is deliberately kept: `example.de:8001` and `example.de:8002` are
    separate servers and separate budgets. Other subdomains are also kept
    separate — `shop.example.de` and `example.de` are commonly different
    machines, and merging them would slow honest crawling for no gain. §5.2
    records that as accepted.
    """
    return authority_of(url).removeprefix("www.")


def origin_of(url: str) -> str:
    """`scheme://netloc` — the origin whose robots.txt governs `url`."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def same_site(url: str, domain: str) -> bool:
    """True when `url` belongs to `domain` or a subdomain of it."""
    host = urlsplit(url).hostname
    if not host:
        return False
    host = host.lower()
    host = host.removeprefix("www.")
    return host == domain or host.endswith(f".{domain}")


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def has_query(url: str) -> bool:
    return bool(urlsplit(url).query)


def hostname_of(url: str) -> str | None:
    """The bare hostname — no port, no brackets around an IPv6 literal.

    Distinct from `authority_of`, which keeps the port because it answers a
    permission question, and from `host_of`, which strips `www.` because it
    answers a budget question. This one answers *what do we resolve?*, so a
    port would make every lookup fail and IPv6 brackets are not part of the
    name. `urlsplit().hostname` also lowercases, which every comparison here
    already assumes.
    """
    return urlsplit(url).hostname
