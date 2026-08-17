"""A local HTTP server standing in for a German shop, built on stdlib only.

Every M1 test except the single live smoke test runs against this. It records
per-request arrival times and in-flight hosts, which is what makes the §5.2
politeness rules observably testable rather than merely asserted.

**The suite resolves its own hostnames (M4, M1.64).** The apex→www tests need
two *distinct authorities* answering from one machine, because that is what
M1.8's shared politeness budget is about — and `host_of` derives that key from
the URL, so the two names have to be in the URL and therefore have to resolve.
They used to be `localhost` and `www.localhost`, which resolve on macOS and
under systemd-resolved (RFC 6761 §6.3) and **not** in most container images.
`resolves_to_loopback` maps the names instead, so nothing in the suite asks a
resolver a question it may answer differently on another machine.
"""

from __future__ import annotations

import gzip
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

#: RFC 2606 §2 reserves `.invalid` and guarantees it is never delegated, so
#: these names resolve **nowhere** — which is the point. Built on `.localhost`
#: the shim would be dead weight on every machine that already resolves it, and
#: could be deleted without a single test noticing; the tests would go back to
#: passing by accident, which is how this defect survived to be found by an
#: external reviewer. On `.invalid` the shim is load-bearing everywhere, so
#: removing or breaking it fails the suite on the maintainer's laptop and in CI
#: alike. That property replaces "assert the shim is installed" with "the tests
#: cannot run without it".
FIXTURE_APEX = "shop.invalid"
FIXTURE_WWW = f"www.{FIXTURE_APEX}"


@contextmanager
def resolves_to_loopback(
    *names: str, address: str = "127.0.0.1"
) -> Iterator[list[str]]:
    """Resolve `names` to `address` for the duration of the block.

    **The seam is `socket.getaddrinfo`, and that was verified rather than
    assumed.** `net.Fetcher` is `httpx.Client` over httpcore's sync backend,
    whose `connect_tcp` calls `socket.create_connection((host, port))` — and
    `socket.create_connection` looks `getaddrinfo` up as a module global in
    `socket`, so patching the attribute intercepts it. Traced under httpx
    0.28.1 / httpcore 1.0.9 by wrapping both functions and printing the stack:
    `handle_request → _connect → connect_tcp → create_connection →
    getaddrinfo`. Patching `create_connection` instead would work today and
    would break the moment httpcore used a socket directly; `getaddrinfo` is
    the narrower waist.

    Scoped, not global: the previous function is restored on exit, including on
    exception, and nesting restores in LIFO order. It is process-wide while
    installed — `fetch` runs its workers in a thread pool and they must see it —
    so it is a context manager rather than a fixture that could outlive a test.

    Yields the list of names it is mapping, so a caller can assert on it.
    """
    mapped = {name.lower() for name in names}
    previous = socket.getaddrinfo

    # Signature mirrors `socket.getaddrinfo` exactly, shadowed builtin `type`
    # included: a caller passing `type=SOCK_STREAM` by keyword must reach the
    # real function unchanged, and renaming the parameter would break that.
    def shim(host, port, family=0, type=0, proto=0, flags=0):
        if isinstance(host, str) and host.lower() in mapped:
            host = address
        return previous(host, port, family, type, proto, flags)

    socket.getaddrinfo = shim
    try:
        yield sorted(mapped)
    finally:
        socket.getaddrinfo = previous


@dataclass
class Request:
    path: str
    host: str
    user_agent: str
    started: float
    finished: float = 0.0


@dataclass
class Site:
    """A fixture site: a path→response map plus request bookkeeping."""

    routes: dict[str, tuple[int, bytes, str]] = field(default_factory=dict)
    redirects: dict[str, tuple[int, str, str | None]] = field(default_factory=dict)
    requests: list[Request] = field(default_factory=list)
    delay: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        path: str,
        body: str | bytes,
        status: int = 200,
        content_type: str = "text/html",
    ) -> None:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.routes[path] = (status, payload, content_type)

    def add_gzip(
        self, path: str, body: str, content_type: str = "application/xml"
    ) -> None:
        self.routes[path] = (200, gzip.compress(body.encode("utf-8")), content_type)

    def add_redirect(
        self,
        path: str,
        location: str,
        status: int = 301,
        only_from_host: str | None = None,
    ) -> None:
        """Serve `path` as a redirect to `location`.

        Every hop arrives here as its own request and is recorded like any
        other, which is what lets a test measure whether the politeness floor
        holds *across* a chain rather than only at its head.

        `only_from_host` restricts the redirect to requests carrying that
        `Host` header, which is how a real apex→www server behaves: one machine,
        answering on two names, bouncing one to the other. Without it a
        path-preserving apex→www redirect would bounce forever, since routing
        here is otherwise by path alone.
        """
        self.redirects[path] = (status, location, only_from_host)

    def record_start(self, request: Request) -> None:
        with self._lock:
            self.requests.append(request)

    def paths(self) -> list[str]:
        with self._lock:
            return [r.path for r in self.requests]

    def arrivals(self) -> list[float]:
        with self._lock:
            return sorted(r.started for r in self.requests)

    def hosts(self) -> list[str]:
        """The `Host` header of each request, in arrival order — how a test
        tells an apex→www hop from a plain same-host one."""
        with self._lock:
            return [r.host for r in self.requests]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    site: Site
    tracker: ConcurrencyTracker

    def log_message(self, *args: object) -> None:  # silence stderr noise
        pass

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        request = Request(
            path=self.path,
            host=host,
            user_agent=self.headers.get("User-Agent", ""),
            started=time.monotonic(),
        )
        self.site.record_start(request)
        self.tracker.enter(host)
        try:
            if self.site.delay:
                time.sleep(self.site.delay)
            redirect = self.site.redirects.get(self.path)
            if redirect is not None and redirect[2] in (None, host):
                status, location, _only_from = redirect
                self.send_response(status)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status, body, content_type = self.site.routes.get(
                self.path, (404, b"not found", "text/plain")
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            request.finished = time.monotonic()
            self.tracker.leave(host)


class ConcurrencyTracker:
    """Records the high-water mark of distinct hosts served simultaneously.

    Shared across every fixture server in a test, so "how many hosts were in
    flight at once" is measured at the servers rather than inferred from the
    client's configuration.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: dict[str, int] = {}
        self.max_hosts = 0

    def enter(self, host: str) -> None:
        with self._lock:
            self._in_flight[host] = self._in_flight.get(host, 0) + 1
            self.max_hosts = max(self.max_hosts, len(self._in_flight))

    def leave(self, host: str) -> None:
        with self._lock:
            remaining = self._in_flight.get(host, 1) - 1
            if remaining <= 0:
                self._in_flight.pop(host, None)
            else:
                self._in_flight[host] = remaining


class FixtureServer:
    """A running site. Use as a context manager; `.netloc` is its host:port."""

    def __init__(
        self,
        site: Site,
        tracker: ConcurrencyTracker | None = None,
        address: str = "127.0.0.1",
    ) -> None:
        self.site = site
        self.address = address
        self.tracker = tracker or ConcurrencyTracker()
        handler = type(
            "BoundHandler", (_Handler,), {"site": site, "tracker": self.tracker}
        )
        self._server = ThreadingHTTPServer((address, 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def netloc(self) -> str:
        return f"{self.address}:{self.port}"

    @property
    def base(self) -> str:
        """What `FetchStage(base_url=...)` should return for this site's domain.

        `127.0.0.x` addresses are all loopback on Linux, so several fixture
        sites can look like several distinct hosts to the rate limiter while
        `same_site` still matches on hostname.
        """
        return f"http://{self.netloc}"

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
