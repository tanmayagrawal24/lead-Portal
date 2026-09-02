"""Where a URL actually points, and whether we are allowed to go there.

**The threat is a redirect, and it was measured before it was fixed (M1.68).**
`net.Fetcher` follows redirect chains itself, a hop at a time, and until this
module existed the only question asked of a hop target was *does robots.txt
permit it?* — a question the target's own server answers. A shop that redirects
`/` to `http://127.0.0.1:8009/` is redirecting into whatever else is listening
on the operator's machine, and §9's review page is listening there by default,
unauthenticated, on the operator's own database of third-party personal data.
The robots probe against that service 404s, which under RFC 9309 §2.3.1.2 means
*no rules stated* and therefore *everything permitted*. On `d57ea64` the PoC
fetched 9,537 bytes of the portal's own §9 page and filed them in `artifact` as
the shop's `homepage`.

So it is not only an exfiltration route out of a private network, it is a
**corpus integrity** defect: the body stored under a company is not that
company's page, and every downstream stage reads `artifact` by kind.

**Why this is a policy object and not four lines in `Fetcher.get`.** It has to
be switchable, because the entire test suite fetches from loopback. A boolean
buried in the transport would be a switch nobody could see; a named constructor
is a sentence in the diff — the same trade `HostRateLimiter.unthrottled` makes,
and for the same reason. `loopback_permitted()` widens **loopback only**: a test
running against 127.0.0.1 still refuses `169.254.169.254` and `10.0.0.1`, so the
guard stays load-bearing in the suite rather than being switched off by it.

**The address list is explicit rather than `ipaddress.is_private`.** That
property's membership has changed between CPython releases — 100.64.0.0/10 among
them — and CI runs 3.11 and 3.12, so a version-dependent guard would be a guard
whose behaviour nobody could state. These are the networks, written down, with
the reason each is refused.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from typing import Self

from portal.urls import hostname_of

#: The reason string for loopback, carved out by name so `loopback_permitted`
#: can widen exactly one class and not "anything local-ish".
LOOPBACK = "loopback"

#: IPv4 networks we refuse, and why. `169.254.0.0/16` is the one an attacker
#: reaches for: every major cloud serves instance credentials from
#: `169.254.169.254` over plain HTTP with no authentication.
_REFUSED_V4 = (
    ("0.0.0.0/8", "unspecified"),
    ("10.0.0.0/8", "private"),
    ("100.64.0.0/10", "carrier-grade NAT"),
    ("127.0.0.0/8", LOOPBACK),
    ("169.254.0.0/16", "link-local (cloud metadata)"),
    ("172.16.0.0/12", "private"),
    ("192.0.0.0/24", "IETF protocol assignments"),
    ("192.0.2.0/24", "documentation"),
    ("192.168.0.0/16", "private"),
    ("198.18.0.0/15", "benchmarking"),
    ("198.51.100.0/24", "documentation"),
    ("203.0.113.0/24", "documentation"),
    ("224.0.0.0/4", "multicast"),
    ("240.0.0.0/4", "reserved"),
)

_REFUSED_V6 = (
    ("::/128", "unspecified"),
    ("::1/128", LOOPBACK),
    ("100::/64", "discard-only"),
    ("2001:db8::/32", "documentation"),
    ("fc00::/7", "unique-local"),
    ("fe80::/10", "link-local"),
    ("ff00::/8", "multicast"),
)

_NETWORKS = tuple((ip_network(cidr), why) for cidr, why in (*_REFUSED_V4, *_REFUSED_V6))


def classify(address: IPv4Address | IPv6Address) -> str | None:
    """Why this address is refused, or None if it is a public one.

    An IPv4-mapped IPv6 address (`::ffff:127.0.0.1`) is unwrapped and judged as
    the IPv4 address it carries. Without that, the v6 table sees an address in
    no refused v6 network and calls it public — the same bypass in a different
    notation.
    """
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    for network, why in _NETWORKS:
        if address in network:
            return why
    return None


@dataclass(frozen=True)
class Verdict:
    """Three states, not two, and for M1.59's reason.

    `permitted` and refused are the obvious pair. The third is *we could not
    find out* — a name that does not resolve, or a resolver that failed — and
    Unit 4 already settled which way that falls: a policy we could not read
    reports **not verifiable** rather than allowed. Recorded separately from a
    refusal so the audit trail can tell "this pointed somewhere it must not"
    from "DNS was down", which send an operator to different places.
    """

    permitted: bool
    reason: str | None = None

    @property
    def verifiable(self) -> bool:
        return self.permitted or (
            self.reason is not None
            and not self.reason.startswith("address_unverifiable")
        )


PERMITTED = Verdict(permitted=True)


class AddressPolicy:
    """Does this URL's host resolve somewhere we are allowed to fetch from?

    The resolver is **not** captured at import time, and that is load-bearing
    rather than fastidious. `tests/fixture_server.resolves_to_loopback` installs
    its shim by rebinding the `socket.getaddrinfo` *attribute* (M1.64); a
    default argument evaluated at class-definition time would hold the original
    function and this guard would resolve names differently from the client that
    is about to connect. A guard that asks a different resolver than the caller
    uses is not a guard — it is a second opinion about a different question.
    """

    def __init__(
        self,
        *,
        permit_loopback: bool = False,
        resolver: object | None = None,
    ) -> None:
        self.permit_loopback = permit_loopback
        self._resolver = resolver

    @classmethod
    def loopback_permitted(cls, **kwargs: object) -> Self:
        """A policy that allows loopback. **Tests and fixtures only.**

        Named rather than spelled `AddressPolicy(permit_loopback=True)` at each
        call site so that the exemption is one greppable token, in
        `HostRateLimiter.unthrottled`'s idiom. Nothing in `portal` constructs
        it: `cli` and `fetch.run` build the default, and the only way to reach
        this is to type its name.

        It widens **loopback alone**. A fixture server on 127.0.0.1 is reachable
        and `169.254.169.254` still is not, so the tests that assert the guard
        refuses a metadata address run under the same policy as every other test
        rather than under a special one.
        """
        return cls(permit_loopback=True, **kwargs)  # type: ignore[arg-type]

    def resolve(self, host: str, port: int = 0) -> list[str]:
        resolver = self._resolver
        if resolver is None:
            resolver = socket.getaddrinfo  # looked up now, not at import
        return [info[4][0] for info in resolver(host, port, 0, socket.SOCK_STREAM)]

    def verdict_for(self, url: str) -> Verdict:
        """Judge every address `url`'s host resolves to.

        **Every** address, and one bad answer refuses the lot: a resolver
        handing back one public address and one loopback address is the shape of
        a rebinding attack, and picking the reassuring half of an answer is how a
        guard is talked out of firing.

        A literal address in the URL is judged directly and **never resolved** —
        `http://169.254.169.254/` needs no DNS to be refused, so that case
        cannot be defeated by anything a resolver does.
        """
        host = hostname_of(url)
        if not host:
            return Verdict(False, f"address_unverifiable: no host in {url!r}")

        try:
            literal = ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            return self._judge({host: classify(literal)}, host)

        try:
            resolved = self.resolve(host)
        except (OSError, UnicodeError) as exc:
            # `UnicodeError` is what `getaddrinfo` raises for a label IDNA
            # refuses — over 63 characters, or empty. It is not an `OSError`,
            # and uncaught it left this guard, then `Fetcher.get`, then the
            # worker thread, and aborted the whole run on one hostile
            # `Location:` header (audit finding 3).
            return Verdict(
                False,
                f"address_unverifiable: {host} did not resolve "
                f"({type(exc).__name__}: {exc})",
            )
        if not resolved:
            return Verdict(
                False, f"address_unverifiable: {host} resolved to no addresses"
            )
        return self._judge(
            {found: classify(ip_address(found)) for found in resolved}, host
        )

    def _judge(self, classified: dict[str, str | None], host: str) -> Verdict:
        for found, why in classified.items():
            if why is None or (why == LOOPBACK and self.permit_loopback):
                continue
            return Verdict(
                False,
                f"address_refused: {host} → {found} ({why})",
            )
        return PERMITTED


__all__ = ["LOOPBACK", "PERMITTED", "AddressPolicy", "Verdict", "classify"]
