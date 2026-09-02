"""The `fetch` stage (§5.2).

Fetch order, per company:

    robots.txt → homepage → sitemap.xml (+ nested) → Impressum
                → blog index if a blog path is found
                → one sample product page if a product path is found

Concurrency is two hosts, enforced structurally: the worker pool has two slots
and one worker owns one domain end to end, so no more than two hosts are ever
in flight. Within a host, `HostRateLimiter` holds the 1 req/s floor.

This stage writes artifacts and exactly one signal — `catalog.product_sample_url`
(§5.2, A5.7), which records a fetch-time decision. Every other signal belongs to
`extract-p1` (M2) and none is written here.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from portal import impressum as impressum_mod
from portal import robots as robots_mod
from portal import sampling, sitemap
from portal.artifacts import ArtifactStore, StoredArtifact, utc_now
from portal.net import (
    MAX_CONCURRENT_HOSTS,
    MAX_CRAWL_DELAY_SECONDS,
    Fetcher,
    Response,
)

# A7b's N, imported rather than restated. The policy is one number and two
# stages read it; a second copy here would be M1.42's shape — a fact described
# by a second expression that can disagree with the first.
from portal.ruleset import PERSISTENT_FETCH
from portal.score import PERSISTENT_RUNS
from portal.sitepolicies import SitePolicies
from portal.urls import (
    authority_of,
    default_base,
    homepage_url,
    host_of,
    normalise_domain,
    origin_of,
    path_of,
    same_site,
)

#: Trailing slashes, for the "did this land on the homepage?" check (M1.17).
_TRAILING_SLASH = re.compile(r"/+$")


@dataclass
class CompanyResult:
    domain: str
    company_id: int
    artifacts: list[StoredArtifact] = field(default_factory=list)
    excluded_reason: str | None = None
    review_flags: list[str] = field(default_factory=list)
    #: Set when `run_company` raised. The exception is recorded here and on
    #: the review queue rather than propagated: one company's crash used to
    #: abort the whole run and discard hours of crawl (audit finding 3).
    failed: str | None = None
    product_sample: str | None = None
    product_sample_tier: str = "none"
    blog_sample: str | None = None
    blog_sample_tier: str = "none"
    notes: list[str] = field(default_factory=list)

    @property
    def kinds(self) -> set[str]:
        return {a.kind for a in self.artifacts if a.ok}


def _sitemap_candidates(
    domain: str, base: str, policy: robots_mod.RobotsPolicy
) -> list[str]:
    """Sitemap entry points: robots.txt `Sitemap:` directives first, then the
    conventional location. Directives win because they are authoritative."""
    declared = [u for u in robots_mod.sitemap_urls(policy) if same_site(u, domain)]
    conventional = f"{base}/sitemap.xml"
    return declared + ([conventional] if conventional not in declared else [])


class FetchStage:
    """Runs the fetch stage. One instance per invocation."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        fetcher: Fetcher,
        store: ArtifactStore,
        run_id: int,
        max_hosts: int = MAX_CONCURRENT_HOSTS,
        base_url: Callable[[str], str] = default_base,
    ) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.store = store
        self.run_id = run_id
        self.max_hosts = max_hosts
        self.base_url = base_url
        # SQLite connections are not safe to share across threads without
        # serialising writes; every DB touch goes through this lock.
        self._db_lock = threading.Lock()

    # ── database helpers ────────────────────────────────────────────────

    def _record(
        self, company_id: int, domain: str, kind: str, response: Response
    ) -> StoredArtifact:
        with self._db_lock:
            return self.store.record(self.conn, company_id, domain, kind, response)

    def _exclude(self, company_id: int, reason: str) -> None:
        with self._db_lock:
            self.conn.execute(
                "UPDATE company SET excluded = 1, excluded_reason = ? WHERE id = ?",
                (reason, company_id),
            )

    def _raise_review_flag(
        self, company_id: int, reason: str, note: str | None = None
    ) -> None:
        """§6.4 soft flag, using the idiom from §4: DO NOTHING on the uniqueness
        conflict only, so a misspelled reason still raises."""
        with self._db_lock:
            self._raise_review_flag_locked(company_id, reason, note)

    def _raise_review_flag_locked(
        self, company_id: int, reason: str, note: str | None = None
    ) -> None:
        """As above, for callers already holding `_db_lock`. `_db_lock` is a
        plain `Lock`, so nesting the public helpers would deadlock."""
        self.conn.execute(
            "INSERT INTO review_flag "
            "(company_id, reason, raised_run_id, raised_at, raised_note) "
            "VALUES (?,?,?,?,?) ON CONFLICT (company_id, reason) DO NOTHING",
            (company_id, reason, self.run_id, utc_now(), note),
        )

    # ── a robots.txt we cannot read, run after run (M1.59, A7b) ─────────

    def _robots_failing_days(
        self, company_id: int, robots_artifact: StoredArtifact
    ) -> list[str]:
        """The distinct days this authority's `robots.txt` has been unreadable,
        as an unbroken streak ending today. Empty when the streak is broken.

        A7b's counting policy, applied to a fetch-stage fact rather than to a
        rule abstention: **3 runs on 3 distinct days**, so a crash-restart loop
        inside one afternoon cannot manufacture a flag about our own crash.

        Two joins do the work, because `artifact` carries no `run_id`:

        * `fetched_at` on the failure row is when this URL first failed —
          `_record_failure` updates in place and never rewrites it (§5.2).
        * A **success** at the same authority since then breaks the streak. A
          200 creates its own row and advances `last_checked_at` on every
          re-fetch of identical content, so `last_checked_at > fetched_at` is
          exactly "it has worked since".

        Two limits, both stated because both are real and neither is measured
        away. A `fetch` run given a company list that omitted this shop still
        counts as a day here; and M1.61's content-hash collapse can file a
        success under a *sibling* origin's row, which an authority match then
        misses. Both err the same way — toward flagging early — and this flag
        raises a queue item and blocks nothing (§6.4, migration 009).
        """
        with self._db_lock:
            row = self.conn.execute(
                "SELECT fetched_at FROM artifact WHERE id = ?",
                (robots_artifact.artifact_id,),
            ).fetchone()
            if row is None:
                return []
            first_failed = str(row["fetched_at"])
            authority = authority_of(robots_artifact.url)
            recovered = [
                r
                for r in self.conn.execute(
                    "SELECT url FROM artifact WHERE company_id = ? AND kind = 'robots' "
                    "AND content_hash IS NOT NULL AND last_checked_at > ?",
                    (company_id, first_failed),
                )
                if authority_of(str(r["url"])) == authority
            ]
            if recovered:
                return []
            days = self.conn.execute(
                "SELECT DISTINCT date(started_at) AS day FROM run "
                "WHERE stage = 'fetch' AND date(started_at) >= date(?) "
                "ORDER BY day",
                (first_failed,),
            ).fetchall()
        return [str(day["day"]) for day in days]

    def _flag_persistent_robots_failure(
        self,
        company_id: int,
        domain: str,
        robots_artifact: StoredArtifact,
        why: str,
        result: CompanyResult,
    ) -> None:
        """Raise `fetch_persistently_failing` once, and only once it persists.

        A single 503 is a transient and A7b says a transient retries before it
        becomes a person's problem. What routes here is M1.34's vocabulary
        because it is M1.34's question: *does this URL load for you?* — the
        same one the product page, the blog index and the sampled article send,
        which is why migration 009 gave all of them one reason.
        """
        days = self._robots_failing_days(company_id, robots_artifact)
        if len(days) < PERSISTENT_RUNS:
            return
        self._raise_review_flag(
            company_id,
            PERSISTENT_FETCH,
            f"{robots_artifact.url}: {why}. Die robots.txt von {domain} ist seit "
            f"{days[0]} an {len(days)} verschiedenen Tagen in Folge nicht lesbar, "
            "daher wurde nichts abgerufen – bitte die Seite einmal von Hand "
            "aufrufen.",
        )
        result.review_flags.append(PERSISTENT_FETCH)

    # ── a seeded domain that has moved (P2, M1.18) ──────────────────────

    def _adopt_moved_site(
        self, company_id: int, domain: str, final_url: str, result: CompanyResult
    ) -> str | None:
        """Adopt the host the homepage actually resolved to, as the site
        identity for the rest of this company's run.

        Returns the domain to use from here on, or `None` when the company was
        excluded as a duplicate. `company.domain` is never written: it is the
        seeded identity, the human key, and the name of the artifacts
        directory. `site_domain` carries the effective host instead.

        Only ever called for the homepage, and only when the final URL is not
        `same_site` with the seeded domain — an apex→www hop is not a move.
        A deeper redirect never adopts: a product page pointing off-site is a
        marketplace or affiliate link, and adopting from one would let a single
        stray link walk the whole crawl onto a third party.

        Collision, per §6.4: a row whose *`domain`* is this host always wins,
        whatever the ids — a seeded identity cannot be taken from a row. Only
        when two rows both claim it as `site_domain` does the lower id win, and
        then it wins regardless of which worker got there first, so the outcome
        does not depend on scheduling.
        """
        moved_to = normalise_domain(final_url)

        with self._db_lock:
            rivals = self.conn.execute(
                "SELECT id, domain, site_domain FROM company "
                "WHERE id != ? AND (domain = ? OR site_domain = ?)",
                (company_id, moved_to, moved_to),
            ).fetchall()

            owner_id: int | None = None
            if seeded := [row for row in rivals if row["domain"] == moved_to]:
                owner_id = int(seeded[0]["id"])
            elif adopters := [int(row["id"]) for row in rivals]:
                if (lowest := min(adopters)) < company_id:
                    owner_id = lowest
                else:
                    # This row has the lower id, so it owns the host — even
                    # though another row got here first. The loser's claim is
                    # withdrawn so exactly one row ever claims a host; its
                    # artifacts stay on disk, which costs nothing and keeps the
                    # record of what was fetched.
                    for rival in adopters:
                        self.conn.execute(
                            "UPDATE company SET site_domain = NULL, excluded = 1, "
                            "excluded_reason = ? WHERE id = ?",
                            (
                                f"duplicate_site: {moved_to} is company #{company_id}",
                                rival,
                            ),
                        )

            if owner_id is not None:
                reason = f"duplicate_site: {moved_to} is company #{owner_id}"
                self.conn.execute(
                    "UPDATE company SET excluded = 1, excluded_reason = ? WHERE id = ?",
                    (reason, company_id),
                )
                self._raise_review_flag_locked(owner_id, "duplicate_site")
                result.excluded_reason = reason
                return None

            self.conn.execute(
                "UPDATE company SET site_domain = ? WHERE id = ?",
                (moved_to, company_id),
            )
            self._raise_review_flag_locked(company_id, "domain_moved")

        result.review_flags.append("domain_moved")
        result.notes.append(f"site moved: {domain} now serves {moved_to}; adopted")
        return moved_to

    def _write_sample_signal(
        self, company_id: int, url: str, source: StoredArtifact, key: str
    ) -> None:
        """A sample URL, recorded because it is a *fetch-time decision*.

        Two keys use this: `catalog.product_sample_url` (A5) and
        `content.blog_sample_url` (A6). Both are unscored and both exist so the
        evidence behind a scored signal is auditable rather than inferred.

        `source` is the stored document the candidate was **listed in**, not a
        URL (M1.42) — the same discipline `extract._write` now holds, and for
        the same reason: `evidence_url` and `artifact_id` come out of one object
        and so cannot name two different pages, or a page that was never stored.
        """
        with self._db_lock:
            self.conn.execute(
                """
                INSERT INTO signal
                    (company_id, run_id, key, value_text, method, evidence_url,
                     artifact_id, observed_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING
                """,
                (
                    company_id,
                    self.run_id,
                    key,
                    url,
                    "deterministic",
                    source.url,
                    source.artifact_id,
                    utc_now(),
                ),
            )

    def _previous_sample(self, company_id: int) -> str | None:
        """The last `catalog.product_sample_url`, which is what Tier 0 reuses.

        Read from the signal rather than from the artifact table on purpose.
        `uq_artifact_identity` is keyed on `(company_id, kind, content_hash)`,
        so two product pages with byte-identical bodies — a soft-404 being the
        realistic case — collapse into one row whose `url` is whichever was
        stored first. Reusing that URL could pin a dead sample forever, which
        is exactly what A5.1's fall-through exists to prevent.
        """
        with self._db_lock:
            row = self.conn.execute(
                """
                SELECT value_text FROM signal
                WHERE company_id = ? AND key = 'catalog.product_sample_url'
                  AND value_text IS NOT NULL
                ORDER BY observed_at DESC, id DESC LIMIT 1
                """,
                (company_id,),
            ).fetchone()
        return str(row["value_text"]) if row else None

    # ── politeness ──────────────────────────────────────────────────────

    def _apply_crawl_delay(
        self, url: str, policy: robots_mod.RobotsPolicy
    ) -> str | None:
        """Honour a `Crawl-delay` (§5.2). Returns a refusal reason if it is above
        the cap, in which case the host must not be fetched at all.

        The delay is stated by an *authority* — apex and www may say different
        things — but applied to a *budget*, which the two of them share. So the
        reason names `authority_of` and the limiter is keyed on `host_of`; where
        both apex and www state a delay, the limiter keeps the larger.

        The limiter takes `max(floor, Crawl-delay)`, so a stated delay can only
        ever slow us down. Above `MAX_CRAWL_DELAY_SECONDS` we stop instead of
        obeying: a worker slot parked for minutes behind one hostile or
        typo'd value is worse for the run than skipping the domain, and the
        skip is recorded rather than silent.
        """
        delay = policy.crawl_delay()
        if delay is None:
            return None
        if delay > MAX_CRAWL_DELAY_SECONDS:
            return (
                f"crawl_delay_too_high: {authority_of(url)} asks for {delay:g}s, "
                f"cap is {MAX_CRAWL_DELAY_SECONDS:g}s"
            )
        self.fetcher.limiter.set_host_interval(host_of(url), delay)
        return None

    # ── per-company pipeline ────────────────────────────────────────────

    def run_company(self, company_id: int, domain: str) -> CompanyResult:
        result = CompanyResult(domain=domain, company_id=company_id)
        base = self.base_url(domain)

        # 1. robots.txt, before anything else.
        #
        # This one fetch may follow a hop off its host, but only within the
        # seeded site — the apex↔www redirect nearly every shop has. Reading
        # `www.example.de/robots.txt` for `example.de` is the conventional
        # behaviour and stays inside the domain we were asked to crawl; a hop
        # anywhere else is refused, because there is no robots.txt yet that
        # could authorise it.
        # Now consulted for every hop, not only host-changing ones (M1.12).
        # There are no robots rules to check a target against yet — this fetch
        # is how we get them — so the only question it can answer is whether the
        # hop stays inside the seeded site.
        def within_site(_from_url: str, to_url: str) -> bool:
            return same_site(to_url, domain)

        robots_response = self.fetcher.get(
            f"{base}/robots.txt", hop_allowed=within_site
        )
        robots_artifact = self._record(company_id, domain, "robots", robots_response)
        result.artifacts.append(robots_artifact)
        policy = robots_mod.for_response(robots_response)

        # M1.59: a robots.txt we could not read stops this company's run here.
        #
        # Not an exclusion. `company.excluded` is a standing verdict about a
        # lead and this is a fact about one afternoon, so nothing is written to
        # `company` — the run simply produces no bodies for this shop, with the
        # reason on the result where an operator reading the run output sees it.
        # Every later request is gated on `SitePolicies.allows`, which an
        # `unavailable` policy already refuses, so returning here is belt and
        # braces; it is here so the shop is not billed 40 refusal rows per run
        # for a state that is about our reading and not about its pages.
        if policy.unavailable is not None:
            result.notes.append(f"{policy.unavailable} — nothing fetched for {domain}")
            self._flag_persistent_robots_failure(
                company_id, domain, robots_artifact, policy.unavailable, result
            )
            return result

        if (reason := policy.blocks_required_paths(base)) is not None:
            self._exclude(company_id, reason)
            result.excluded_reason = reason
            return result

        # Robots policies by authority, so a redirect chain is checked against
        # the robots.txt of the origin it actually lands on. Seeded with both the
        # authority we asked and the one that answered — the same, unless the
        # robots fetch was itself redirected within the site, in which case this
        # policy governs both and its Crawl-delay has to reach both too.
        #
        # The memo and the two policy questions live in `SitePolicies` (M1.67).
        # The seeded fetch stays here because its failure modes are the
        # company's and not an authority's: an unreadable one stops the run
        # above, and a `Crawl-delay` over the cap on the seeded host excludes
        # the company outright, which is a verdict `SitePolicies` may not take.
        seeded = {
            authority_of(base): base,
            authority_of(robots_response.url): robots_response.url,
        }
        site_policies = SitePolicies(
            self.fetcher,
            record_robots=lambda probe: result.artifacts.append(
                self._record(company_id, domain, "robots", probe)
            ),
            crawl_delay_refusal=self._apply_crawl_delay,
            note=result.notes.append,
        )
        for authority in seeded:
            site_policies.seed(authority, policy)
        for url in seeded.values():
            if (reason := self._apply_crawl_delay(url, policy)) is not None:
                self._exclude(company_id, reason)
                result.excluded_reason = reason
                return result

        #: Every document this company yielded, by the URL asked for **and** the
        #: URL it landed on. `_write_sample_signal` resolves its citation
        #: through here rather than assembling one, so a sample's provenance
        #: names a row in `artifact` and not a string that resembles one
        #: (M1.42).
        stored: dict[str, StoredArtifact] = {}

        def get(
            kind: str, url: str, accept: Callable[[Response], str | None] | None = None
        ) -> Response | None:
            """Fetch one URL if robots permits, recording either way.

            `accept` may reject a 200 that is not what it claims to be, giving
            the reason. The row is then stored as a *failure* rather than as a
            body of that kind (M1.17): the request happened and must be
            recorded, but `artifact` is the interface M2 reads by kind, so a
            homepage must not sit in it as an `impressum`.
            """

            def keep(response: Response) -> StoredArtifact:
                record = self._record(company_id, domain, kind, response)
                result.artifacts.append(record)
                stored[url] = record
                stored[record.url] = record
                return record

            if not site_policies.allows(url):
                keep(Response(url=url, error=robots_mod.disallowed_reason(url)))
                return None
            response = self.fetcher.get(url, hop_allowed=site_policies.hop_allowed)
            if response.ok and accept is not None and (why := accept(response)):
                response = Response(url=url, status=response.status, error=why)
            keep(response)
            return response

        # 2. homepage.
        homepage = get("homepage", homepage_url(base))
        homepage_html = homepage.text() if homepage and homepage.ok else ""

        # 2b. Did the homepage land on a different registrable domain? (M1.18)
        #
        # `site` is the identity every later same-site test uses; `domain` stays
        # the seeded value, because `get` records artifacts under it and the
        # bodies on disk are keyed to the company, not to whichever host was
        # serving when they were fetched.
        site = domain
        if homepage is not None and homepage.ok and not same_site(homepage.url, domain):
            adopted = self._adopt_moved_site(company_id, domain, homepage.url, result)
            if adopted is None:
                return result  # excluded as a duplicate of another company
            site = adopted
            base = origin_of(homepage.url)

        # 3. sitemaps, expanding indexes.
        shards = self._walk_sitemaps(site, base, policy, get)
        page_urls = [url for _shard, pages in shards for url in pages]

        # Which document each candidate URL was listed in, built by the same
        # walk that builds the candidate lists (M1.42). A5 and A6 choose from
        # flattened lists, and flattening used to throw the source away — so
        # `catalog.product_sample_url` cited `f"{base}/sitemap.xml"`, a string
        # that on `smile-store.de` and `zecplus.de` names no artifact at all,
        # and on `doonails.de` named the seeded host while the shop serves from
        # `www.doonails.com`. That is M1.18's blinding in a provenance field.
        #
        # One map serves both samplers, and `setdefault` is what makes that
        # sound rather than convenient: sitemap URLs are inserted first, and
        # each sampler's homepage/index tier is reached **only** when no sitemap
        # URL passed the same candidacy filter — so a URL resolved here is
        # resolved to the list the sampler actually drew it from.
        source_of: dict[str, str] = {}
        for shard, pages in shards:
            for page in pages:
                source_of.setdefault(page, shard)

        # 4. Impressum, two-step.
        self._discover_impressum(company_id, site, base, homepage_html, result, get)

        # 5. blog index, if a blog is located — then one article under it (A6).
        located = impressum_mod.locate_blog(
            page_urls, homepage_html, homepage_url(base), site
        )
        blog_path = located.path if located else None
        # M1.27: classification needs the blog path, so it happens here rather
        # than during the walk.
        kinds = sitemap.classify(shards, blog_path)
        product_sitemap_urls = [
            url for shard, pages in shards if kinds[shard] == "product" for url in pages
        ]
        blog_sitemap_urls = [
            url for shard, pages in shards if kinds[shard] == "blog" for url in pages
        ]

        def not_the_homepage(response: Response) -> str | None:
            """A blog-index request that came back on the shop's own front page
            is not a blog index — M1.17's rule, in the place M1.14 made it worth
            asking.

            Taking an anchor's href wherever it points turns *where we landed*
            into a real question, and the answer is host-aware: a root path on
            the blog's own host is `blog.zecplus.de`, the thing we went looking
            for, while a root path on the shop's host is the homepage. Stored as
            a blog index the second would write `content.blog_exists = 1` off
            the homepage and then let A6 sample the catalogue for articles.
            """
            if host_of(response.url) == host_of(base) and not path_of(
                response.url
            ).strip("/"):
                result.notes.append(
                    f"blog index request landed on the homepage: {response.url}"
                )
                return f"soft_redirect_to_homepage: {response.url}"
            return None

        if located is None:
            result.notes.append("no blog path and no blog anchor found")
        else:
            if located.url is not None:
                # Anchor text already names the address; there is nothing
                # shallower to prefer, and the href is the shop's own statement
                # of where its blog is.
                target = located.url
            else:
                # An observed URL, not a synthesised one (M1.15): the bare path
                # prefix 404'd on all seven shops that have a blog.
                observed = impressum_mod.find_blog_index_url(
                    located.path or "",
                    page_urls,
                    homepage_html,
                    homepage_url(base),
                    site,
                )
                if observed is None:
                    result.notes.append(
                        f"blog path {located.path} found but no URL under it"
                    )
                target = observed or impressum_mod.blog_index_url(
                    base, located.path or ""
                )
            result.notes.append(f"blog located by {located.basis}: {target}")
            index = get("blog_index", target, accept=not_the_homepage)
            self._sample_blog_article(
                company_id,
                index,
                blog_sitemap_urls,
                page_urls,
                result,
                get,
                source_of,
                stored,
            )

        # 6. one sample product page (A5).
        self._sample_product_page(
            company_id,
            site,
            base,
            page_urls,
            product_sitemap_urls,
            homepage,
            blog_path,
            result,
            get,
            source_of,
            stored,
        )
        return result

    def _walk_sitemaps(
        self, domain: str, base: str, policy: robots_mod.RobotsPolicy, get
    ) -> list[tuple[str, list[str]]]:
        """Fetch sitemaps breadth-first, returning `(shard_url, page_urls)` pairs.

        Shards are returned unclassified, in the order they were read. M1.27
        decides what a shard holds from its **contents and its siblings**, and
        neither is known until the whole index has been walked — classifying a
        shard as it arrives is what forces the decision back onto its name.
        """
        queue = _sitemap_candidates(domain, base, policy)
        seen: set[str] = set()
        walked: list[tuple[str, list[str]]] = []
        shards = 0

        while queue and shards < sitemap.MAX_SHARDS:
            url = queue.pop(0)
            if url in seen or not same_site(url, domain):
                continue
            seen.add(url)
            shards += 1

            response = get("sitemap", url)
            if response is None or not response.ok:
                continue
            children, pages = sitemap.parse(response.body or b"", url)
            queue.extend(child for child in children if child not in seen)
            walked.append((url, pages))

        return walked

    def _sample_blog_article(
        self,
        company_id: int,
        index: Response | None,
        blog_sitemap_urls: list[str],
        page_urls: list[str],
        result: CompanyResult,
        get,
        source_of: dict[str, str],
        stored: dict[str, StoredArtifact],
    ) -> None:
        """A6: one article under the fetched blog index.

        §5.3 named the blog *index* as the evidence for `content.blog_last_post`
        and `schema.article_present`, and on Shopify the index carries neither —
        no `<time>`, no `datePublished`, no `Article` markup. All three live on
        the article page. Measured: 5 of 7 detected blogs yielded no date and
        `schema.article_present` was `0` on every index in the corpus. That is
        not a parser weakness; the evidence was never on the page we fetched.

        Anchored on the index's **final** URL, because that is the page whose
        children are articles — see `sampling.is_blog_article_candidate` for why
        the blog *path* is the wrong anchor, and why the anchor is the whole URL
        rather than its path.
        """
        if index is None or not index.ok:
            result.notes.append("no blog index fetched; no article sampled (A6.1)")
            return

        index_links = [
            url for url, _text in impressum_mod.links(index.text(), index.url)
        ]
        # Tier 3's source. Tiers 1 and 2 read off sitemap shards and are already
        # in `source_of`; only `index_links` is read off the index itself, so
        # citing the index unconditionally — as this did — named the right page
        # for one tier in three (M1.42).
        for link in index_links:
            source_of.setdefault(link, index.url)
        chosen, tier = sampling.choose_blog_article(
            blog_sitemap_urls, page_urls, index_links, index.url
        )
        if chosen is None:
            # A6.1: no candidate means no article, and downstream neither
            # `content.blog_last_post` nor `schema.article_present` is written.
            # Not a zero, not today's date.
            result.notes.append(
                f"no article candidates under {index.url}; blog date stays unwritten"
            )
            return

        response = get("blog_article", chosen)
        if response is None or not response.ok:
            result.notes.append(
                f"blog article fetch failed, no signal written: {chosen}"
            )
            return

        result.blog_sample = chosen
        result.blog_sample_tier = tier
        if source := _cite(chosen, source_of, stored):
            self._write_sample_signal(
                company_id, chosen, source, "content.blog_sample_url"
            )
        else:
            result.notes.append(
                f"blog sample {chosen} has no stored source document; "
                "content.blog_sample_url not written (M1.42)"
            )

    def _discover_impressum(
        self,
        company_id: int,
        domain: str,
        base: str,
        homepage_html: str,
        result: CompanyResult,
        get,
    ) -> None:
        """§5.2 two-step. `no_impressum` is recorded only after both steps fail."""

        def not_the_homepage(response: Response) -> str | None:
            """An Impressum request that landed on the homepage is not an
            Impressum (M1.17).

            `snocks.com` in `run 2`: its real Impressum is robots-disallowed, so
            probing ran, and `/imprint` redirected to `/#gbaid979323` — the
            homepage. It was stored as the Impressum, carrying the homepage's
            own content hash. §5.5b would then hand the homepage to the
            Impressum extraction and get a confident answer about the wrong
            page. A soft redirect to the root is the site saying "no such
            page"; recording it as an absence routes the company to review,
            which is what §5.2's two-step does with an absence anyway.
            """
            if _TRAILING_SLASH.sub("", path_of(response.url)) in ("", "/"):
                result.notes.append(
                    f"impressum request landed on the homepage: {response.url}"
                )
                return f"soft_redirect_to_homepage: {response.url}"
            return None

        if homepage_html:
            linked = impressum_mod.find_impressum_link(
                homepage_html, homepage_url(base), domain
            )
            if linked:
                response = get("impressum", linked, accept=not_the_homepage)
                if response is not None and response.ok:
                    result.notes.append("impressum found via footer link")
                    return

        for probe in impressum_mod.probe_urls(base):
            response = get("impressum", probe, accept=not_the_homepage)
            if response is not None and response.ok:
                result.notes.append(f"impressum found by probing {path_of(probe)}")
                return

        self._raise_review_flag(company_id, "no_impressum")
        result.review_flags.append("no_impressum")

    def _sample_product_page(
        self,
        company_id: int,
        domain: str,
        base: str,
        page_urls: list[str],
        product_sitemap_urls: list[str],
        homepage: Response | None,
        blog_path: str | None,
        result: CompanyResult,
        get,
        source_of: dict[str, str],
        stored: dict[str, StoredArtifact],
    ) -> None:
        """A5, including Tier 0 reuse with its HTTP-200 fall-through.

        Takes the homepage `Response` rather than its HTML, because A5's Tier 3
        reads candidates off that page and the citation has to be the page as
        **fetched** — `homepage_url(base)` is a reconstruction, and on
        `doonails.de` it reconstructs `https://doonails.de/` for a shop serving
        `https://www.doonails.com/` (M1.42).
        """
        homepage_html = homepage.text() if homepage is not None and homepage.ok else ""
        homepage_links = (
            [url for url, _ in impressum_mod.links(homepage_html, homepage.url)]
            if homepage_html and homepage is not None
            else []
        )
        for link in homepage_links:
            source_of.setdefault(link, homepage.url)  # type: ignore[union-attr]

        # Tier 0: reuse a stored sample while it still returns 200.
        dead: set[str] = set()
        previous = self._previous_sample(company_id)
        if previous and sampling.is_product_candidate(
            previous, domain, blog_path, require_pattern=False
        ):
            response = get("product_page", previous)
            if response is not None and response.ok:
                result.product_sample = previous
                result.product_sample_tier = "reuse"
                # Tier 0 has no listing document: the evidence *is* the page we
                # just re-checked, and `get` has this moment's artifact for it.
                self._write_sample_signal(
                    company_id, previous, stored[previous], "catalog.product_sample_url"
                )
                return
            # Discarded, per A5.1 — and excluded, or it is still the code-point
            # minimum and would simply be chosen again.
            dead.add(previous)
            result.notes.append(
                f"stored product sample no longer 200, re-selecting: {previous}"
            )

        chosen, tier = sampling.choose_product_sample(
            product_sitemap_urls,
            page_urls,
            homepage_links,
            domain,
            blog_path,
            exclude=dead,
        )
        if chosen is None:
            # §5.2: no candidates means no product page and, downstream, no
            # `schema.product_present` signal at all — never a 0.
            result.notes.append(
                "no product candidates; schema.product_present must stay unwritten"
            )
            return

        response = get("product_page", chosen)
        if response is None or not response.ok:
            result.notes.append(
                f"product sample fetch failed, no signal written: {chosen}"
            )
            return

        result.product_sample = chosen
        result.product_sample_tier = tier
        if source := _cite(chosen, source_of, stored):
            self._write_sample_signal(
                company_id, chosen, source, "catalog.product_sample_url"
            )
        else:
            result.notes.append(
                f"product sample {chosen} has no stored source document; "
                "catalog.product_sample_url not written (M1.42)"
            )


def _cite(url: str, source_of: dict[str, str], stored: dict[str, StoredArtifact]):
    """The stored document `url` was listed in, or `None`.

    §5.2 requires a real URL here and never a synthesised one. The function this
    replaced returned `f"{base}/sitemap.xml"` under a docstring saying exactly
    that, and two of thirteen shops cited a sitemap that was never fetched.
    Resolving through `stored` makes the requirement structural: a citation that
    is not a row in `artifact` cannot be produced, and where none exists the
    caller writes no signal rather than a plausible-looking string.
    """
    source = source_of.get(url)
    return stored.get(source) if source else None


def run(
    conn: sqlite3.Connection,
    company_rows: list[tuple[int, str]],
    artifacts_root: Path,
    fetcher: Fetcher | None = None,
    max_hosts: int = MAX_CONCURRENT_HOSTS,
    base_url: Callable[[str], str] = default_base,
) -> tuple[int, list[CompanyResult]]:
    """Fetch every company. Returns `(run_id, results)`."""
    cursor = conn.execute(
        "INSERT INTO run (started_at, stage) VALUES (?, 'fetch')", (utc_now(),)
    )
    run_id = int(cursor.lastrowid)

    owns_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    stage = FetchStage(
        conn, fetcher, ArtifactStore(artifacts_root), run_id, max_hosts, base_url
    )

    # `finished_at` is set on the success path **only**, and an abort is
    # recorded instead (M1.39). It used to be written from the `finally`, which
    # marked a crashed run finished — and `company_profile` now reads exactly
    # that column to decide which run's account of a company to trust
    # (migration 007). A run that died at company 10 of 13 must not be able to
    # retract the three it never reached.
    def guarded(row: tuple[int, str]) -> CompanyResult:
        """Isolate one company. A crash inside `run_company` is recorded on
        its result and as a `fetch_persistently_failing` note, and the pool
        moves on. Only an interrupt (`BaseException` that is not an
        `Exception`) still stops the run, because that one is the operator's.
        """
        company_id, domain = row
        try:
            return stage.run_company(company_id, domain)
        except Exception as exc:  # noqa: BLE001 — recorded, never swallowed
            result = CompanyResult(domain=domain, company_id=company_id)
            result.failed = f"{type(exc).__name__}: {exc}"[:500]
            result.notes.append(f"crashed — nothing further fetched: {result.failed}")
            stage._raise_review_flag(
                company_id,
                PERSISTENT_FETCH,
                f"Der Abruf von {domain} ist mit einem internen Fehler abgebrochen "
                f"({type(exc).__name__}); bitte die Seite einmal von Hand aufrufen.",
            )
            result.review_flags.append(PERSISTENT_FETCH)
            return result

    try:
        with ThreadPoolExecutor(max_workers=max_hosts) as pool:
            results = list(pool.map(guarded, company_rows))
    except BaseException as exc:
        conn.execute(
            "UPDATE run SET aborted_reason = ? WHERE id = ?",
            (f"{type(exc).__name__}: {exc}"[:500], run_id),
        )
        raise
    finally:
        if owns_fetcher:
            fetcher.close()
    conn.execute(
        "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
        (utc_now(), len(results), run_id),
    )
    return run_id, results


__all__ = ["CompanyResult", "FetchStage", "run"]
