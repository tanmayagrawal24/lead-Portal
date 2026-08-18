"""HTTP fetching under the §5.2 politeness rules.

The rules are hard requirements, not options, so they live in the transport
rather than in the callers: nothing in this codebase can issue an HTTP request
that skips the rate limiter, because there is no other way to issue one.

* One request per second **per host**, enforced by `HostRateLimiter`.
* At most two hosts in flight, enforced by the worker-pool size in
  `portal.fetch` — one worker owns one domain end to end.
* An identifiable User-Agent with a contact route.
* Plain `httpx`, no headless browser.

**Redirects are requests.** `httpx`'s own `follow_redirects` would issue every
hop of a chain inside one `client.get()` call, below the limiter — five hops
against one host in well under a second, and a cross-host hop to a host whose
robots.txt was never read. So redirects are followed here, one hop at a time,
each one waiting on the limiter like any other request, and **every** hop target
is put to the caller's `hop_allowed` policy before it is followed.

*Every* hop, not only the ones that change authority (M1.12). Robots permission
does not survive a redirect: an allowed URL may redirect to a disallowed one,
and the request that lands on the disallowed URL is the one robots governs.
Checking only authority changes is what let this transport fetch two
robots-disallowed pages on the first real crawl — via a different path on one
domain, and via a path differing from the `Disallow` rule only in case on the
other. The callback's name says "hop", not "cross host", for that reason.

**And robots is the wrong authority to ask about the address.** `hop_allowed`
asks the *target's own server* whether we may fetch it, which is exactly the
wrong question when the target is `127.0.0.1:8009` or `169.254.169.254`: those
answer 404 to a robots probe, and a 404 means "no rules stated" and therefore
"everything permitted". So a second gate, on the address rather than the path,
is applied to **every** URL this transport is handed — the first one as well as
every hop, since a seed row can name an address just as a `Location` header can.
See `portal.addresses` for the measurement that produced it (M1.68).

Two keys, deliberately: the limiter is keyed on `urls.host_of` (apex and www
share one budget, because they are one machine) and the vouching check on
`urls.authority_of` (apex and www are separate origins, and may serve separate
robots.txt files). Collapsing them either way is a bug — one direction doubles
the request rate, the other skips a robots.txt.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import httpx

from portal.addresses import AddressPolicy
from portal.urls import absolutise, authority_of, host_of

USER_AGENT = "CreativePotatoesBot/1.0 (+https://creative-potato.global)"

#: §5.2 politeness floor. Also the value the timing test asserts against.
MIN_INTERVAL_SECONDS = 1.0

#: Two hosts concurrent, per §5.2.
MAX_CONCURRENT_HOSTS = 2

#: Bodies larger than this are truncated. Guards memory on a multi-megabyte
#: Shopware homepage, and caps what the XML parser is ever handed.
MAX_BODY_BYTES = 8 * 1024 * 1024

DEFAULT_TIMEOUT_SECONDS = 20.0

#: §5.2: hops followed before a chain is abandoned. Each one is a request and
#: waits on the limiter, so this is also a cap on requests-per-`get`.
MAX_REDIRECT_HOPS = 5

#: §5.2 `Crawl-delay` cap. Above this we skip the domain rather than stall a
#: worker slot behind a hostile or broken value.
MAX_CRAWL_DELAY_SECONDS = 10.0

#: `(from_url, to_url) -> may we follow this hop?`, asked for **every** hop
#: (M1.12), not only the ones that change host. See `Fetcher.get`.
HopPolicy = Callable[[str, str], bool]


class _RobotsExempt:
    """The only legitimate reason to fetch without a robots policy: the request
    that *is* the robots.txt fetch, which by definition runs before any rules
    exist (§5.2's stated exception).

    A sentinel rather than a default, because this subsystem has now produced
    two defects of identical shape — enforcement written correctly, invocation
    skipped (M1.1's host-only check, and the `follow_redirects=True` before it).
    A permissive default is how the third one arrives: it makes forgetting the
    policy silent at runtime instead of loud at the call site. Passing this is
    a sentence in the diff that a reviewer can object to.

    It is *not* a licence to follow anything: a hop that changes authority is
    still refused, because a new authority has a robots.txt that provably has
    not been read. It permits only same-authority hops, where there is nothing
    to consult.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RobotsExempt"


#: See `_RobotsExempt`. Import and pass explicitly; never default to it.
RobotsExempt = _RobotsExempt()


class RequestLog:
    """One line of JSON per HTTP request issued, for auditing §5.2 (M1.19).

    M1's done-when is "1 req/s **observed**", and until this existed nothing in
    the output could show it. `artifact` rows record what was *stored*, written
    when a response lands, so the gaps between them measure the server's
    latency variance rather than our spacing — a measurement that appeared to
    show ten violations in the first crawl and showed nothing of the kind.

    What makes this the right place: every hop passes through `Fetcher.get`,
    below which no request can be issued, so the log cannot miss one the way a
    caller-side log would. `issued_at` is taken *after* the limiter returns and
    immediately before the request goes out, which is the moment §5.2 governs.

    Both clocks are recorded. `monotonic` is what gaps are computed from, since
    a wall-clock adjustment mid-run could otherwise manufacture or hide a
    violation; the ISO timestamp is there so a line can be tied to a run.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, **fields: object) -> None:
        line = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        # One line per write, so concurrent workers cannot interleave.
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class HostRateLimiter:
    """Blocks until the host's interval has elapsed since the last call for it.

    Uses `time.monotonic`, so a wall-clock adjustment mid-run cannot shorten
    the gap. Thread-safe: the per-host lock is held across the sleep, which is
    what makes the guarantee hold when two workers touch the same host.

    The interval for a host is `max(min_interval, its Crawl-delay)` — see
    `set_host_interval`. A `Crawl-delay` can only ever slow us down.
    """

    def __init__(self, min_interval: float = MIN_INTERVAL_SECONDS) -> None:
        if min_interval <= 0:
            raise ValueError(
                f"min_interval={min_interval!r} would switch §5.2's politeness "
                "floor off entirely. That is never right against a real host; a "
                "test that genuinely wants no delay must say so out loud with "
                "HostRateLimiter.unthrottled()."
            )
        self.min_interval = min_interval
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._intervals: dict[str, float] = {}
        self._guard = threading.Lock()

    @classmethod
    def unthrottled(cls) -> Self:
        """A limiter that never waits. **Tests only.**

        Named rather than spelled `HostRateLimiter(0)` so that disabling the
        politeness floor is a deliberate and greppable act instead of a falsy
        argument nobody reads. Nothing in `portal` constructs this: `portal.cli`
        clamps `--interval` to the floor and `Fetcher`'s default builds a real
        limiter, so the only way to reach it is to type its name.
        """
        limiter = cls(MIN_INTERVAL_SECONDS)
        limiter.min_interval = 0.0
        return limiter

    def set_host_interval(self, host: str, seconds: float) -> None:
        """Record a host-specific interval, from its robots.txt `Crawl-delay`.

        Stored rather than applied directly: `interval_for` takes the maximum
        against the floor, so this can raise the gap and never lower it.
        """
        with self._guard:
            self._intervals[host] = max(self._intervals.get(host, 0.0), seconds)

    def interval_for(self, host: str) -> float:
        with self._guard:
            return max(self.min_interval, self._intervals.get(host, 0.0))

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:  # the explicit test-only bypass
            return
        interval = self.interval_for(host)
        with self._lock_for(host):
            previous = self._last.get(host)
            now = time.monotonic()
            if previous is not None:
                remaining = interval - (now - previous)
                if remaining > 0:
                    time.sleep(remaining)
            self._last[host] = time.monotonic()


def _redirect_target(response: httpx.Response, current: str) -> str | None:
    """The absolute URL a redirect points at, or None if it is unusable.

    `httpx` has already resolved a relative `Location` for us on
    `next_request`; the fallback covers the case where it did not, and
    `absolutise` is what rejects a `Location` naming a scheme we do not fetch.
    """
    if response.next_request is not None:
        return str(response.next_request.url)
    return absolutise(current, response.headers.get("location", ""))


@dataclass
class Response:
    """What a fetch attempt produced. `error` and `body` are exclusive."""

    url: str
    status: int | None = None
    body: bytes | None = None
    error: str | None = None
    content_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.body is not None

    def text(self) -> str:
        if self.body is None:
            return ""
        return self.body.decode("utf-8", errors="replace")


@dataclass
class Fetcher:
    """A rate-limited HTTP client. One per run; safe to share across workers."""

    limiter: HostRateLimiter = field(default_factory=HostRateLimiter)
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_bytes: int = MAX_BODY_BYTES
    #: Where every issued request is logged (M1.19). None disables logging;
    #: the tests that measure politeness at the fixture server do not need it.
    log: RequestLog | None = None
    #: H2/M1.68. Default refuses loopback, private, link-local and reserved
    #: destinations. The whole test suite fetches from loopback and therefore
    #: passes `AddressPolicy.loopback_permitted()` — one greppable token per
    #: call site, in `HostRateLimiter.unthrottled`'s idiom, rather than a
    #: boolean nobody can find.
    addresses: AddressPolicy = field(default_factory=AddressPolicy)
    _client: httpx.Client | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=self.timeout,
            # Off, deliberately. `get` walks the chain itself so that every hop
            # is rate-limited and every host change is checked. See the module
            # docstring.
            follow_redirects=False,
        )

    def get(self, url: str, *, hop_allowed: HopPolicy | _RobotsExempt) -> Response:
        """Fetch one URL, never raising. Failures come back as `Response.error`.

        Redirects are followed a hop at a time, up to `MAX_REDIRECT_HOPS`, each
        hop waiting on the limiter for *its own* host. **Every** hop target is
        put to `hop_allowed` before it is followed (M1.12) — including hops that
        stay on the same authority, because an allowed URL may redirect to a
        disallowed one and it is the landing request that robots governs.

        `hop_allowed` is **required**. Omitting it is a `TypeError` at the call
        site rather than a silent bypass at runtime — see `_RobotsExempt` for
        why that trade is worth one extra argument at four call sites. The
        robots.txt fetch, which runs before any rules exist, passes
        `RobotsExempt`; it then permits same-authority hops and refuses
        authority changes.

        A refused or over-long chain comes back as an error `Response`, so it is
        recorded like any other failed fetch rather than passing silently.
        """
        assert self._client is not None
        current = url
        #: The status of the redirect that sent us here, so a refusal carries
        #: the hop that produced it exactly as `redirect_refused` does. None on
        #: the first URL, which arrived from a caller and not from a `Location`.
        arrived_by: int | None = None

        for _hop in range(MAX_REDIRECT_HOPS + 1):
            # Before the limiter, because a request we will not issue must not
            # spend a second of the host's politeness budget — and before the
            # request, because the point is that it never goes out. Applied here
            # rather than in the caller's `hop_allowed` for the reason the
            # module docstring gives: robots is the target's own account of
            # itself, and this gate exists precisely for targets whose account
            # of themselves cannot be trusted.
            verdict = self.addresses.verdict_for(current)
            if not verdict.permitted:
                return Response(url=current, status=arrived_by, error=verdict.reason)

            self.limiter.wait(host_of(current))
            issued = time.monotonic()
            issued_at = datetime.now(UTC).isoformat(timespec="milliseconds")
            try:
                response = self._client.get(current)
            except httpx.HTTPError as exc:
                self._log(current, issued, issued_at, None, f"{type(exc).__name__}")
                return Response(url=current, error=f"{type(exc).__name__}: {exc}")
            self._log(
                current,
                issued,
                issued_at,
                response.status_code,
                None,
                str(response.url),
            )

            if not response.has_redirect_location:
                return Response(
                    url=str(response.url),
                    status=response.status_code,
                    body=response.content[: self.max_bytes],
                    content_type=response.headers.get("content-type"),
                )

            target = _redirect_target(response, current)
            if target is None:
                location = response.headers.get("location", "")
                return Response(
                    url=current,
                    status=response.status_code,
                    error=f"redirect_unusable: {location!r}",
                )
            # Asked for every hop (M1.12). Under `RobotsExempt` there are no
            # rules to ask, so the question collapses to "does this hop leave
            # the origin?" — `authority_of`, not `host_of`, because the two
            # differ on `www.`: apex and www share one politeness budget but may
            # serve different robots.txt files.
            if isinstance(hop_allowed, _RobotsExempt):
                permitted = authority_of(target) == authority_of(current)
            else:
                permitted = hop_allowed(current, target)
            if not permitted:
                return Response(
                    url=current,
                    status=response.status_code,
                    error=f"redirect_refused: {target}",
                )
            arrived_by = response.status_code
            current = target

        return Response(
            url=current,
            error=f"too_many_redirects: over {MAX_REDIRECT_HOPS} from {url}",
        )

    def _log(
        self,
        url: str,
        issued: float,
        issued_at: str,
        status: int | None,
        error: str | None,
        final_url: str | None = None,
    ) -> None:
        if self.log is None:
            return
        self.log.record(
            issued_at=issued_at,
            issued_monotonic=round(issued, 6),
            elapsed=round(time.monotonic() - issued, 6),
            host=host_of(url),  # the politeness key: apex and www share it
            authority=authority_of(url),  # the robots key: they do not
            url=url,
            final_url=final_url if final_url != url else None,
            status=status,
            error=error,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
