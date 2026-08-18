"""Which robots.txt governs a URL, for the length of one company's run.

§5.2 asks two questions with two different keys, and this module owns the
second of them. The politeness budget is keyed on `urls.host_of` — apex and
www are one machine and share one request-per-second — while permission is
keyed on `urls.authority_of`, because apex and www are separate origins and
may serve separate robots.txt files. Collapsing them either way is a bug: one
direction doubles the request rate, the other applies a file its origin never
served.

**Why this is a class and not four closures.** It was four closures over three
mutable dicts inside `FetchStage.run_company`, which is the longest method in
the project. Extraction changes nothing about what they do — it gives the
memo, the refusal set and the two policy questions one name, so the address
guard (H2) has a seam to land in and a defect in either is attributable to one
of them rather than to both (M1.67).

The lifetime is deliberate: **one instance per company run**, never shared.
`policies` is a memo of files read during this run, and a robots.txt is a
statement about one afternoon (M1.59). Carrying it across companies would let
one shop's 503 silence another's, and would make the memo a cache with no
expiry rule anybody had written down.
"""

from __future__ import annotations

from collections.abc import Callable

from portal import robots as robots_mod
from portal.net import Fetcher, Response, RobotsExempt
from portal.urls import authority_of, origin_of


class SitePolicies:
    """The robots.txt of every authority this run has touched.

    Four collaborators, all injected, because this object issues requests and
    records artifacts and must not know how either is done:

    * `fetcher` — the rate-limited transport. Robots probes go through it like
      everything else; there is no way to issue an unthrottled request.
    * `record_robots` — hands a probe response back to the caller to store. The
      probe is a fetch that happened and §5.2's audit trail has to show it,
      including when it failed.
    * `crawl_delay_refusal` — applies a stated `Crawl-delay` to the limiter,
      returning a reason when the value is above the cap. Injected rather than
      imported because the limiter is keyed on the politeness key and this
      class only knows about the permission key.
    * `note` — a line for the operator reading the run output.
    """

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        record_robots: Callable[[Response], None],
        crawl_delay_refusal: Callable[[str, robots_mod.RobotsPolicy], str | None],
        note: Callable[[str], None],
    ) -> None:
        self.fetcher = fetcher
        self._record_robots = record_robots
        self._crawl_delay_refusal = crawl_delay_refusal
        self._note = note
        #: authority → the rules it served. A memo, so a chain that keeps
        #: landing on one origin costs one robots.txt fetch.
        self.policies: dict[str, robots_mod.RobotsPolicy] = {}
        #: authority → why nothing more will be fetched from it this run.
        #: `crawl_delay_too_high` and M1.59's `robots_unavailable` share this
        #: dict because they mean the same thing to every later request: this
        #: authority is off limits, and the reason is recorded rather than
        #: inferred from an absence.
        self.unfetchable: dict[str, str] = {}

    def seed(self, authority: str, policy: robots_mod.RobotsPolicy) -> None:
        """Record a policy already fetched, so `policy_for` does not re-probe.

        The seeded robots.txt is fetched by `run_company` itself, before this
        object exists, because its failure modes are the company's rather than
        an authority's: an unreadable one stops the run and a `Disallow: /` on
        a required path excludes the company outright. Neither is a decision
        this class is allowed to take, so both stay with the caller and only
        the result arrives here.
        """
        self.policies[authority] = policy

    def policy_for(self, url: str) -> robots_mod.RobotsPolicy:
        """The rules of the authority that answers for `url`, read on first
        sight of that authority.

        **Fetching here, rather than only on a redirect hop, is M1.14's
        doing.** Until anchor text could hand the fetch stage a URL on a host
        nothing had visited — `blog.zecplus.de` — every request was either on
        the seeded authority or arrived through a hop, which loaded the file
        itself. A first request to an unvisited authority used to fall back to
        the seeded policy, which is `zecplus.de`'s file applied to somebody
        else's origin: the one thing §5.2 says twice not to do.
        """
        authority = authority_of(url)
        if authority in self.policies:
            return self.policies[authority]

        probe = self.fetcher.get(
            f"{origin_of(url)}/robots.txt", hop_allowed=RobotsExempt
        )
        self._record_robots(probe)
        self.policies[authority] = robots_mod.for_response(probe)

        # M1.59, one authority out. A blog host or a redirect target whose
        # robots.txt 503s is off limits for the rest of this run. §5.2's rule
        # that a blog host refusing us is a missing signal and never an
        # exclusion holds unchanged: nothing is written to `company`.
        if (why := self.policies[authority].unavailable) is not None:
            self._refuse(authority, why)
            return self.policies[authority]

        if (
            refusal := self._crawl_delay_refusal(url, self.policies[authority])
        ) is not None:
            self._refuse(authority, refusal)
        return self.policies[authority]

    def _refuse(self, authority: str, why: str) -> None:
        self.unfetchable[authority] = why
        self._note(f"{authority} not fetched — {why}")

    def allows(self, url: str) -> bool:
        """May we fetch this URL, under the policy of the authority it is on?

        The authority's own policy, not the seeded one. After a move is adopted
        (M1.18) every later request goes to a host the seeded robots.txt says
        nothing about; checking `lampenflut.de` URLs against
        `germanelectronic.de`'s rules would be applying the wrong file.
        """
        rules = self.policy_for(url)
        return authority_of(url) not in self.unfetchable and rules.allows(url)

    def hop_allowed(self, _from_url: str, to_url: str) -> bool:
        """§5.2: "fetch and honour robots.txt before anything else" applies to a
        redirect hop too, because the hop is itself a request.

        Asked for **every** hop, not only those that change authority (M1.12).
        A same-authority hop takes the fast path — its rules are already
        memoised — and is then checked against them exactly like any other
        target. That check is the whole fix: `/impressum` being allowed says
        nothing about the `/policies/legal-notice` it redirects to, and it was
        the unchecked same-host hop that fetched two disallowed pages on the
        first crawl.
        """
        rules = self.policy_for(to_url)
        if authority_of(to_url) in self.unfetchable:
            return False
        if rules.allows(to_url):
            return True
        self._note(
            f"redirect refused by robots.txt on {authority_of(to_url)}: {to_url}"
        )
        return False


__all__ = ["SitePolicies"]
