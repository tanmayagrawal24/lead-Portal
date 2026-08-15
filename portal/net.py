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
each one waiting on the limiter like any other request, and a hop that changes
host is refused unless the caller vouches for the new host (`cross_host`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Self

import httpx

from portal.urls import absolutise, host_of

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

#: `(from_url, to_url) -> may we follow this hop?`, asked only when the hop
#: changes host. See `Fetcher.get`.
CrossHostPolicy = Callable[[str, str], bool]


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

    def get(self, url: str, *, cross_host: CrossHostPolicy | None = None) -> Response:
        """Fetch one URL, never raising. Failures come back as `Response.error`.

        Redirects are followed a hop at a time, up to `MAX_REDIRECT_HOPS`, each
        hop waiting on the limiter for *its own* host. A hop that leaves the
        current host is put to `cross_host` first; with no policy supplied the
        answer is no, because the transport cannot know whether anyone has read
        that host's robots.txt. A refused or over-long chain comes back as an
        error `Response`, so it is recorded like any other failed fetch rather
        than passing silently.
        """
        assert self._client is not None
        current = url

        for _hop in range(MAX_REDIRECT_HOPS + 1):
            self.limiter.wait(host_of(current))
            try:
                response = self._client.get(current)
            except httpx.HTTPError as exc:
                return Response(url=current, error=f"{type(exc).__name__}: {exc}")

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
            if host_of(target) != host_of(current) and not (
                cross_host is not None and cross_host(current, target)
            ):
                return Response(
                    url=current,
                    status=response.status_code,
                    error=f"redirect_refused: {target}",
                )
            current = target

        return Response(
            url=current,
            error=f"too_many_redirects: over {MAX_REDIRECT_HOPS} from {url}",
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
