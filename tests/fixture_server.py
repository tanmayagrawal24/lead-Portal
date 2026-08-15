"""A local HTTP server standing in for a German shop, built on stdlib only.

Every M1 test except the single live smoke test runs against this. It records
per-request arrival times and in-flight hosts, which is what makes the §5.2
politeness rules observably testable rather than merely asserted.
"""

from __future__ import annotations

import gzip
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self


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
