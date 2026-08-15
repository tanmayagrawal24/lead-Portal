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
    RobotsExempt,
)
from portal.urls import (
    authority_of,
    default_base,
    homepage_url,
    host_of,
    origin_of,
    path_of,
    same_site,
)


@dataclass
class CompanyResult:
    domain: str
    company_id: int
    artifacts: list[StoredArtifact] = field(default_factory=list)
    excluded_reason: str | None = None
    review_flags: list[str] = field(default_factory=list)
    product_sample: str | None = None
    product_sample_tier: str = "none"
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

    def _raise_review_flag(self, company_id: int, reason: str) -> None:
        """§6.4 soft flag, using the idiom from §4: DO NOTHING on the uniqueness
        conflict only, so a misspelled reason still raises."""
        with self._db_lock:
            self.conn.execute(
                "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at) "
                "VALUES (?,?,?,?) ON CONFLICT (company_id, reason) DO NOTHING",
                (company_id, reason, self.run_id, utc_now()),
            )

    def _write_sample_signal(
        self, company_id: int, url: str, evidence_url: str
    ) -> None:
        with self._db_lock:
            self.conn.execute(
                """
                INSERT INTO signal
                    (company_id, run_id, key, value_text, method, evidence_url, observed_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING
                """,
                (
                    company_id,
                    self.run_id,
                    "catalog.product_sample_url",
                    url,
                    "deterministic",
                    evidence_url,
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
        result.artifacts.append(
            self._record(company_id, domain, "robots", robots_response)
        )
        policy = robots_mod.parse(
            robots_response.text() if robots_response.ok else None
        )

        if (reason := policy.blocks_required_paths(base)) is not None:
            self._exclude(company_id, reason)
            result.excluded_reason = reason
            return result

        # Robots policies by authority, so a redirect chain is checked against
        # the robots.txt of the origin it actually lands on. Seeded with both the
        # authority we asked and the one that answered — the same, unless the
        # robots fetch was itself redirected within the site, in which case this
        # policy governs both and its Crawl-delay has to reach both too.
        seeded = {
            authority_of(base): base,
            authority_of(robots_response.url): robots_response.url,
        }
        policies: dict[str, robots_mod.RobotsPolicy] = dict.fromkeys(seeded, policy)
        for url in seeded.values():
            if (reason := self._apply_crawl_delay(url, policy)) is not None:
                self._exclude(company_id, reason)
                result.excluded_reason = reason
                return result

        unfetchable: dict[str, str] = {}  # authority → why we will not fetch it

        def hop_allowed(_from_url: str, to_url: str) -> bool:
            """§5.2: "fetch and honour robots.txt before anything else" applies
            to a redirect hop too, because the hop is itself a request.

            Asked for **every** hop, not only those that change authority
            (M1.12). A same-authority hop takes the fast path — its rules are
            already in `policies` — and is then checked against them exactly
            like any other target. That check is the whole fix: `/impressum`
            being allowed says nothing about the `/policies/legal-notice` it
            redirects to, and it was the unchecked same-host hop that fetched
            two disallowed pages on the first crawl.

            Keyed on authority rather than on the politeness key: apex and www
            share a budget but not necessarily a robots.txt, so each is asked
            for its own. Both the lookup and its verdict are memoised, so a
            chain that keeps landing on one origin costs one robots.txt fetch.
            """
            authority = authority_of(to_url)
            if authority not in policies:
                probe = self.fetcher.get(
                    f"{origin_of(to_url)}/robots.txt", hop_allowed=RobotsExempt
                )
                result.artifacts.append(
                    self._record(company_id, domain, "robots", probe)
                )
                policies[authority] = robots_mod.parse(
                    probe.text() if probe.ok else None
                )
                refusal = self._apply_crawl_delay(to_url, policies[authority])
                if refusal is not None:
                    unfetchable[authority] = refusal
                    result.notes.append(f"redirect not followed — {refusal}")
            if authority in unfetchable:
                return False
            if policies[authority].allows(to_url):
                return True
            result.notes.append(
                f"redirect refused by robots.txt on {authority}: {to_url}"
            )
            return False

        def allowed(url: str) -> bool:
            return policy.allows(url)

        def get(kind: str, url: str) -> Response | None:
            """Fetch one URL if robots permits, recording either way."""
            if not allowed(url):
                skipped = Response(url=url, error=robots_mod.disallowed_reason(url))
                result.artifacts.append(self._record(company_id, domain, kind, skipped))
                return None
            response = self.fetcher.get(url, hop_allowed=hop_allowed)
            result.artifacts.append(self._record(company_id, domain, kind, response))
            return response

        # 2. homepage.
        homepage = get("homepage", homepage_url(base))
        homepage_html = homepage.text() if homepage and homepage.ok else ""

        # 3. sitemaps, expanding indexes.
        page_urls, product_sitemap_urls = self._walk_sitemaps(domain, base, policy, get)

        # 4. Impressum, two-step.
        self._discover_impressum(company_id, domain, base, homepage_html, result, get)

        # 5. blog index, if a blog path is found.
        blog_path = impressum_mod.find_blog_path(
            page_urls, homepage_html, homepage_url(base), domain
        )
        if blog_path:
            get("blog_index", impressum_mod.blog_index_url(base, blog_path))
        else:
            result.notes.append("no blog path found")

        # 6. one sample product page (A5).
        self._sample_product_page(
            company_id,
            domain,
            base,
            page_urls,
            product_sitemap_urls,
            homepage_html,
            blog_path,
            result,
            get,
        )
        return result

    def _walk_sitemaps(
        self, domain: str, base: str, policy: robots_mod.RobotsPolicy, get
    ) -> tuple[list[str], list[str]]:
        """Fetch sitemaps breadth-first, returning `(all_page_urls, product_sitemap_page_urls)`."""
        queue = _sitemap_candidates(domain, base, policy)
        seen: set[str] = set()
        page_urls: list[str] = []
        product_urls: list[str] = []
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
            page_urls.extend(pages)
            if sitemap.is_product_sitemap(url):
                product_urls.extend(pages)

        return page_urls, product_urls

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
        if homepage_html:
            linked = impressum_mod.find_impressum_link(
                homepage_html, homepage_url(base), domain
            )
            if linked:
                response = get("impressum", linked)
                if response is not None and response.ok:
                    result.notes.append("impressum found via footer link")
                    return

        for probe in impressum_mod.probe_urls(base):
            response = get("impressum", probe)
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
        homepage_html: str,
        blog_path: str | None,
        result: CompanyResult,
        get,
    ) -> None:
        """A5, including Tier 0 reuse with its HTTP-200 fall-through."""
        homepage_links = (
            [url for url, _ in impressum_mod.links(homepage_html, homepage_url(base))]
            if homepage_html
            else []
        )

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
                self._write_sample_signal(company_id, previous, previous)
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
        self._write_sample_signal(company_id, chosen, _sample_evidence(base, tier))


def _sample_evidence(base: str, tier: str) -> str:
    """`evidence_url` for `catalog.product_sample_url`: the source it was read off.

    §5.2 requires a real URL here, never a synthesised one — so this names the
    document the candidate list came from, not the product page itself.
    """
    if tier == "homepage_links":
        return homepage_url(base)
    return f"{base}/sitemap.xml"


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

    try:
        with ThreadPoolExecutor(max_workers=max_hosts) as pool:
            results = list(pool.map(lambda row: stage.run_company(*row), company_rows))
    finally:
        if owns_fetcher:
            fetcher.close()
        conn.execute(
            "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
            (utc_now(), len(company_rows), run_id),
        )
    return run_id, results


__all__ = ["CompanyResult", "FetchStage", "run"]
