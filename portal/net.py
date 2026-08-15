"""HTTP fetching under the §5.2 politeness rules.

The rules are hard requirements, not options, so they live in the transport
rather than in the callers: nothing in this codebase can issue an HTTP request
that skips the rate limiter, because there is no other way to issue one.

* One request per second **per host**, enforced by `HostRateLimiter`.
* At most two hosts in flight, enforced by the worker-pool size in
  `portal.fetch` — one worker owns one domain end to end.
* An identifiable User-Agent with a contact route.
* Plain `httpx`, no headless browser.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Self

import httpx

from portal.urls import host_of

USER_AGENT = "CreativePotatoesBot/1.0 (+https://creative-potato.global)"

#: §5.2 politeness floor. Also the value the timing test asserts against.
MIN_INTERVAL_SECONDS = 1.0

#: Two hosts concurrent, per §5.2.
MAX_CONCURRENT_HOSTS = 2

#: Bodies larger than this are truncated. Guards memory on a multi-megabyte
#: Shopware homepage, and caps what the XML parser is ever handed.
MAX_BODY_BYTES = 8 * 1024 * 1024

DEFAULT_TIMEOUT_SECONDS = 20.0


class HostRateLimiter:
    """Blocks until `min_interval` has elapsed since the last call for a host.

    Uses `time.monotonic`, so a wall-clock adjustment mid-run cannot shorten
    the gap. Thread-safe: the per-host lock is held across the sleep, which is
    what makes the guarantee hold when two workers touch the same host.
    """

    def __init__(self, min_interval: float = MIN_INTERVAL_SECONDS) -> None:
        self.min_interval = min_interval
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        with self._lock_for(host):
            previous = self._last.get(host)
            now = time.monotonic()
            if previous is not None:
                remaining = self.min_interval - (now - previous)
                if remaining > 0:
                    time.sleep(remaining)
            self._last[host] = time.monotonic()


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
            follow_redirects=True,
            # A redirect off-site is not our page; callers check same_site.
            max_redirects=5,
        )

    def get(self, url: str) -> Response:
        """Fetch one URL, never raising. Failures come back as `Response.error`."""
        assert self._client is not None
        self.limiter.wait(host_of(url))
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            return Response(url=url, error=f"{type(exc).__name__}: {exc}")

        body = response.content[: self.max_bytes]
        return Response(
            url=str(response.url),
            status=response.status_code,
            body=body,
            content_type=response.headers.get("content-type"),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
