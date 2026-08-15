"""robots.txt parsing and the §5.2 exclusion policy.

Two distinct questions, deliberately kept apart:

* *May we fetch this one URL?* — asked before every request.
* *Is this company excluded outright?* — asked once, and only about the paths
  the tool actually needs.

§5.2 is explicit that these are not the same: "A robots.txt that disallows
`/checkout/` or `/account/` is normal and is not a refusal." Conflating them
would throw away most of the corpus.

Uses stdlib `urllib.robotparser`. It is fed text we fetched ourselves rather
than being allowed to fetch — otherwise it would issue an unthrottled request
outside the politeness limiter.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

from portal.urls import path_of

USER_AGENT_TOKEN = "CreativePotatoesBot"

#: Probed in order by the Impressum two-step (§5.2).
IMPRESSUM_PROBE_PATHS = (
    "/impressum",
    "/impressum/",
    "/imprint",
    "/legal",
    "/rechtliches",
)


@dataclass(frozen=True)
class RobotsPolicy:
    """What robots.txt permits for one host."""

    parser: RobotFileParser | None  # None = no usable robots.txt, everything allowed
    raw: str | None

    def allows(self, url: str) -> bool:
        if self.parser is None:
            return True
        # Checked under our own token and under `*`; RobotFileParser already
        # falls back to the wildcard group when the named agent has no group.
        return bool(self.parser.can_fetch(USER_AGENT_TOKEN, url))

    def crawl_delay(self) -> float | None:
        """The host's `Crawl-delay` in seconds, or None if it states none.

        §5.2 honours `max(1.0, crawl_delay)`, so this can only ever slow us
        down. `RobotFileParser` reads the value as an int and ignores a
        fractional one (`Crawl-delay: 0.5` arrives here as None) — which is
        harmless, because any value below the floor changes nothing.
        """
        if self.parser is None:
            return None
        delay = self.parser.crawl_delay(USER_AGENT_TOKEN)
        return None if delay is None else float(delay)

    def blocks_required_paths(self, base: str) -> str | None:
        """The §5.2 hard-exclusion test. Returns a reason, or None to proceed.

        Required paths are the homepage, the sitemap, and the Impressum. The
        blog path is not knowable before the sitemap is parsed, so it is
        checked per-URL later rather than here — a disallowed blog is a missing
        signal, not grounds for exclusion.
        """
        if self.parser is None:
            return None
        if not self.allows(f"{base}/"):
            return "robots_disallowed: / is disallowed"
        if not self.allows(f"{base}/sitemap.xml"):
            return "robots_disallowed: /sitemap.xml is disallowed"
        if all(not self.allows(f"{base}{p}") for p in IMPRESSUM_PROBE_PATHS):
            return "robots_disallowed: every Impressum path is disallowed"
        return None


def parse(text: str | None) -> RobotsPolicy:
    """Build a policy from robots.txt text.

    A missing, empty, or unparseable robots.txt means "no restrictions stated",
    which is the conventional reading and the one every major crawler uses. It
    is not treated as a refusal — a 404 on robots.txt is the common case for
    small shops.
    """
    if not text or not text.strip():
        return RobotsPolicy(parser=None, raw=text)
    parser = RobotFileParser()
    try:
        parser.parse(text.splitlines())
    except Exception:  # noqa: BLE001 — a malformed robots.txt must not abort a run
        return RobotsPolicy(parser=None, raw=text)
    return RobotsPolicy(parser=parser, raw=text)


def sitemap_urls(policy: RobotsPolicy) -> list[str]:
    """`Sitemap:` directives, which are the authoritative pointer when present."""
    if policy.raw is None:
        return []
    found: list[str] = []
    for line in policy.raw.splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() == "sitemap" and value.strip():
            found.append(value.strip())
    return found


def disallowed_reason(url: str) -> str:
    return f"robots_disallowed: {path_of(url)}"
