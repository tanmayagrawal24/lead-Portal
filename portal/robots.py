"""robots.txt parsing and the §5.2 exclusion policy.

Two distinct questions, deliberately kept apart:

* *May we fetch this one URL?* — asked before every request.
* *Is this company excluded outright?* — asked once, and only about the paths
  the tool actually needs.

§5.2 is explicit that these are not the same: "A robots.txt that disallows
`/checkout/` or `/account/` is normal and is not a refusal." Conflating them
would throw away most of the corpus.

A third question was missing entirely until M1.59: *did we read a robots.txt at
all?* Every non-200 response used to collapse into "no rules stated", so a 503,
a 429 and a connection timeout each granted the same unrestricted permission a
404 does. `for_response` is where that question is now answered, and
`RobotsPolicy.unavailable` is the state the answer can be.

Uses stdlib `urllib.robotparser`. It is fed text we fetched ourselves rather
than being allowed to fetch — otherwise it would issue an unthrottled request
outside the politeness limiter.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

from portal.net import Response
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
    """What robots.txt permits for one host.

    Three states, not two (M1.59, RFC 9309 §2.3.1):

    * `parser` set — rules were read, and they decide.
    * `parser` None, `unavailable` None — the host answered that it states no
      rules (a 404). Everything is allowed, which is the conventional reading.
    * `unavailable` set — **we never read this host's rules.** Nothing is
      allowed for the rest of this run, and the string says why.
    """

    parser: RobotFileParser | None  # None = no rules to apply; see `unavailable`
    raw: str | None
    #: Why no rules were read, or None. Non-None means *disallow all* — the
    #: opposite of `parser is None`, which is why the two cannot be one field.
    unavailable: str | None = None

    def allows(self, url: str) -> bool:
        if self.unavailable is not None:
            return False
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

        **The delay is lost whenever the policy is (M1.59).** There is one
        parser behind both `allows` and this, so a response that yields no
        parser yields no delay either — and before the tri-state that included
        a 503 and a 429, where the tool fell back to its own default and
        crawled a failing or explicitly back-off-ing server at a rate it chose
        for itself. §5.2 treats this value as load-bearing in the other
        direction already (`crawl_delay_too_high` skips the domain outright),
        so discarding it silently was the same number read two ways.

        Under the tri-state the question cannot arise: an `unavailable` policy
        allows nothing, so there is no request left for a delay to pace. That
        is a structural property and `TestUnavailablePolicy` pins it.
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

        **An `unavailable` policy is not an exclusion** and must not reach
        here as one. `company.excluded` is a standing verdict about a lead;
        a 503 is a fact about one afternoon. The caller stops this run without
        writing one — see `fetch.run_company`.
        """
        if self.unavailable is not None:
            return None
        if self.parser is None:
            return None
        if not self.allows(f"{base}/"):
            return "robots_disallowed: / is disallowed"
        if not self.allows(f"{base}/sitemap.xml"):
            return "robots_disallowed: /sitemap.xml is disallowed"
        if all(not self.allows(f"{base}{p}") for p in IMPRESSUM_PROBE_PATHS):
            return "robots_disallowed: every Impressum path is disallowed"
        return None


def unrestricted(raw: str | None = None) -> RobotsPolicy:
    """ "This host states no rules." Everything is allowed."""
    return RobotsPolicy(parser=None, raw=raw)


def unavailable(reason: str) -> RobotsPolicy:
    """ "We did not read this host's rules." Nothing is allowed (M1.59)."""
    return RobotsPolicy(
        parser=None, raw=None, unavailable=f"robots_unavailable: {reason}"
    )


def parse(text: str | None) -> RobotsPolicy:
    """Build a policy from robots.txt text we successfully retrieved.

    A missing or empty robots.txt means "no restrictions stated", which is the
    conventional reading and the one every major crawler uses. It is not
    treated as a refusal — a 404 on robots.txt is the common case for small
    shops. **That reasoning is right for an absent file and only for an absent
    file**; which responses reach this function at all is `for_response`'s
    question, not this one's (M1.59).

    **A body that was retrieved and will not parse is `unavailable`, not
    unrestricted (M1.60).** It is not the 4xx case: the server has a file and
    served it, so "no rules stated" is contradicted by the bytes in hand. The
    honest reading is *rules stated, not understood*, and the errors are
    asymmetric in M1.4's sense — falling open crawls pages the shop may have
    forbidden, while falling closed costs one company one run and says so in a
    note. Exposure is low either way: stdlib `RobotFileParser.parse` skips
    lines it cannot read rather than raising, so this branch is close to
    unreachable, which is exactly why taking the safe side is nearly free.
    """
    if text is None or not text.strip():
        return unrestricted(text)
    parser = RobotFileParser()
    try:
        parser.parse(text.splitlines())
    except Exception as exc:  # noqa: BLE001 — must not abort a run; see above
        return unavailable(
            f"robots.txt retrieved but unparseable: {type(exc).__name__}"
        )
    return RobotsPolicy(parser=parser, raw=text)


#: RFC 9309 §2.3.1.3 "Unreachable status". 429 is grouped with 5xx deliberately:
#: `Too Many Requests` is the server asking us to stop, and reading it as "no
#: rules stated" answers a request to back off by crawling the whole site.
def _is_unreachable(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def for_stored(status: int, body: str | None) -> RobotsPolicy:
    """The policy a **stored** robots.txt row establishes (M1.122).

    The same rule as `for_response`, applied to a row on disk instead of a live
    response, and it exists because the two had drifted: `for_response` reads a
    4xx as *"an answer: there is no file"* and returns `unrestricted`, while
    `impressum_audit.policy_for` selected on `http_status = 200` and therefore
    could not see a 4xx row at all — so the same 404, read live, allowed the
    fetch, and read back from the table, forbade it.

    Three companies in the corpus sat behind that disagreement with a perfectly
    good 404 on file. **One expression, both directions** (M1.42's shape).
    """
    if status == 200:
        return parse(body)
    if _is_unreachable(status):
        return unavailable(f"HTTP {status}")
    return unrestricted()


def for_response(response: Response) -> RobotsPolicy:
    """The §5.2 policy a `robots.txt` fetch attempt establishes (M1.59).

    RFC 9309 §2.3.1 separates three cases that `Response.ok` merges into two,
    and the merge fails open. The question this asks is **did the server give
    us an answer we can act on?**

    * **200 with a body** — the rules. An empty body is "no rules stated".
    * **4xx** — an answer: there is no file. Unrestricted, per §2.3.1.2 and per
      this module's original docstring, which is right about this case.
    * **3xx we did not follow** — no answer, but §2.3.1.1 permits treating an
      unresolved redirect as unavailable-meaning-allow, and here it is also the
      safe reading: the only shape in the corpus is M1.18's moved domain
      (`doonails.de`, `germanelectronic.de` — 2 of 13), where the hop leaves the
      seeded site, is refused, and the host we then actually fetch has its own
      `robots.txt` read before its first request by `policy_for`. `redirect_
      unusable` — a `Location` naming a scheme we do not fetch — is classified
      here too and is **unobserved**; it is grouped by status class rather than
      by error string on purpose, so this stays a pure function of the status
      and cannot drift from the strings `net.get` happens to write (M1.42).
    * **5xx, 429, transport failure, redirect loop** — no answer at all, or an
      explicit "not now". **Disallow all for this run.**

    `too_many_redirects` reaches the last group because it carries no status: a
    chain that never terminated is a broken or hostile file, not a stated
    absence. §2.3.1.1's allowance there is a MAY, and this project does not
    convert an absence of evidence into permission.
    """
    if response.ok:
        return parse(response.text())
    if response.status is None:
        return unavailable(response.error or "no response")
    if _is_unreachable(response.status):
        return unavailable(f"HTTP {response.status}")
    return unrestricted()


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
