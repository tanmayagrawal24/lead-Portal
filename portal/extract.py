"""The `extract-p1` stage (§5.3) — deterministic signals, no LLM, no cost.

Reads artifacts `fetch` already stored and writes signals. It makes no HTTP
requests at all: if a page is not on disk, its signals are not written. That is
what makes this stage free to re-run and what lets §4 promise that re-scoring
never needs a refetch.

**Two rules govern every write here**, and both are about the difference
between an absence and an ignorance:

1. *Checked-and-absent is written; not-measured is not.* `schema.product_present
   = 0` is only ever written from a product page fetched with HTTP 200 (A5.5,
   A5.6). Without the page, nothing is written — a `0` there would fire
   `opp.no_product_schema` for +10 against a shop whose product pages were
   never retrieved.
2. *An instrument that does not apply says so.* When no URL on a site matches
   any product pattern, `catalog.product_url_count` is left unwritten and
   `catalog.not_measurable` records why (§10.3). Writing `0` would claim a real
   shop has no catalogue and raise `possible_marketplace_only` against it;
   writing nothing at all would silence three rules with no record of the
   reason. This is the same third state §6.2's blog ladder uses for an
   unparseable date.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from portal import parsers, sampling, sitemap
from portal.artifacts import ArtifactStore, utc_now
from portal.urls import path_of


@dataclass
class ExtractResult:
    domain: str
    company_id: int
    signals: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Artifact:
    kind: str
    url: str
    body_path: str | None
    http_status: int | None


class ExtractStage:
    """Runs `extract-p1`. One instance per invocation; single-threaded.

    No thread pool here, unlike `fetch`: there is no network to overlap and the
    work is milliseconds per company. The lock discipline `fetch` needs does
    not apply, and adding threads would buy nothing but a way to corrupt the
    database.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        store: ArtifactStore,
        run_id: int,
        today: date | None = None,
    ) -> None:
        self.conn = conn
        self.store = store
        self.run_id = run_id
        self.today = today or datetime.now(UTC).date()

    # ── database ────────────────────────────────────────────────────────

    def _artifacts(self, company_id: int) -> list[_Artifact]:
        rows = self.conn.execute(
            "SELECT kind, url, body_path, http_status FROM artifact "
            "WHERE company_id = ? ORDER BY id",
            (company_id,),
        ).fetchall()
        return [
            _Artifact(r["kind"], r["url"], r["body_path"], r["http_status"])
            for r in rows
        ]

    def _body_bytes(self, artifact: _Artifact) -> bytes:
        """Raw bytes. Sitemaps must not be round-tripped through `str`: a
        `.xml.gz` shard is binary, and decoding it as UTF-8 with replacement
        destroys the gzip header — which silently turned three JTL catalogues
        into "0 URLs" on the first run of this stage."""
        if not artifact.body_path:
            return b""
        path = self.store.root / artifact.body_path
        return path.read_bytes() if path.is_file() else b""

    def _body(self, artifact: _Artifact) -> str:
        return self._body_bytes(artifact).decode("utf-8", errors="replace")

    def _write(
        self,
        result: ExtractResult,
        key: str,
        evidence_url: str,
        *,
        num: float | None = None,
        text: str | None = None,
        day: date | None = None,
    ) -> None:
        """One signal, using §4's M1.5 idiom.

        `ON CONFLICT ... DO NOTHING` on the uniqueness target only — never
        `INSERT OR IGNORE`, which would also swallow a CHECK violation on
        `method` and turn a typo into a signal that silently never existed.
        """
        self.conn.execute(
            """
            INSERT INTO signal
                (company_id, run_id, key, value_num, value_text, value_date,
                 method, evidence_url, observed_at)
            VALUES (?,?,?,?,?,?,'deterministic',?,?)
            ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING
            """,
            (
                result.company_id,
                self.run_id,
                key,
                num,
                text,
                day.isoformat() if day else None,
                evidence_url,
                utc_now(),
            ),
        )
        result.signals[key] = num if num is not None else (text or day)

    def _raise_review_flag(self, result: ExtractResult, reason: str) -> None:
        """§6.4 soft flag. Same idiom as `fetch`: `DO NOTHING` on the uniqueness
        conflict only, so a misspelled reason still raises a CHECK violation
        rather than becoming a flag that silently never existed.

        No lock here, unlike `fetch` — this stage is single-threaded.
        """
        self.conn.execute(
            "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at) "
            "VALUES (?,?,?,?) ON CONFLICT (company_id, reason) DO NOTHING",
            (result.company_id, reason, self.run_id, utc_now()),
        )
        result.review_flags.append(reason)

    # ── per-company ─────────────────────────────────────────────────────

    def run_company(self, company_id: int, domain: str) -> ExtractResult:
        result = ExtractResult(domain=domain, company_id=company_id)
        artifacts = self._artifacts(company_id)
        # M1.18: every same-site test must use the host the site actually
        # serves from. Anchored on the seeded domain instead, doonails.de's
        # 1,319 catalogue URLs all read as off-site and the shop came out as
        # "catalogue not measurable" — the same silent blinding M1.18 fixed in
        # `fetch`, reappearing one stage later.
        row = self.conn.execute(
            "SELECT site_domain FROM company WHERE id = ?", (company_id,)
        ).fetchone()
        site = (row["site_domain"] if row else None) or domain

        def first(kind: str) -> _Artifact | None:
            return next(
                (
                    a
                    for a in artifacts
                    if a.kind == kind and a.body_path and a.http_status == 200
                ),
                None,
            )

        homepage = first("homepage")
        if homepage is None:
            result.notes.append("no homepage on disk — nothing to extract")
            return result

        homepage_html = self._body(homepage)
        self._homepage_signals(result, homepage.url, homepage_html)

        impressum = first("impressum")
        if impressum is not None:
            self._legal_form(result, impressum.url, self._body(impressum))
        else:
            result.notes.append("no impressum on disk — legal_form not extracted")

        blog_path = self._catalog_and_blog(result, site, artifacts, homepage_html)
        self._blog_signals(result, artifacts, blog_path, homepage_html)
        self._product_schema(result, artifacts, homepage_html)
        return result

    # ── homepage ────────────────────────────────────────────────────────

    def _homepage_signals(self, result: ExtractResult, url: str, html: str) -> None:
        if platform := parsers.detect_platform(html):
            self._write(result, "platform.detected", url, text=platform)
        else:
            # Not "no platform": Shopware 5 emits none of the §5.3 signatures
            # (M1.11), and a real SW5 shop lands here. Recorded so a missing
            # `qual.ecommerce_platform` is distinguishable from an unchecked one.
            result.notes.append(
                "no platform signature matched (M1.11: SW5 is known undetected)"
            )

        if (length := parsers.meta_description_length(html)) is not None:
            self._write(result, "meta.description_length", url, num=length)
        if (languages := parsers.hreflang_language_count(html)) is not None:
            self._write(result, "i18n.hreflang_count", url, num=languages)
        if credit := parsers.footer_agency_credit(html, result.domain):
            self._write(result, "agency.footer_credit", url, text=credit)

        self._write(
            result,
            "reviews.trusted_shops",
            url,
            num=1 if parsers.has_trusted_shops(html) else 0,
        )
        if (reviews := parsers.aggregate_review_count(html)) is not None:
            self._write(result, "reviews.count", url, num=reviews)

    def _legal_form(self, result: ExtractResult, url: str, html: str) -> None:
        """A1: writes `company.legal_form`, a column rather than a signal.

        `company_profile` reads `legal_form` off the company row, so this is
        where it has to land. Absence is left as NULL and is not an error: five
        of twelve Impressum pages in the first crawl state no legal form, and
        all five are sole traders whose pages are simply accurate (§10.2).
        """
        form = parsers.legal_form(html)
        if form is None:
            result.notes.append(f"no legal form stated in {path_of(url)}")
            return
        self.conn.execute(
            "UPDATE company SET legal_form = ? WHERE id = ?", (form, result.company_id)
        )
        result.signals["company.legal_form"] = form

    # ── catalogue (§10.3) ───────────────────────────────────────────────

    def _catalog_and_blog(
        self,
        result: ExtractResult,
        domain: str,
        artifacts: list[_Artifact],
        homepage_html: str,
    ) -> str | None:
        """`catalog.product_url_count`, plus the blog path the count excludes.

        The three-state rule lives here. A site with sitemaps but no URL
        matching any product pattern is *not* a site with no products — on JTL
        every product is a root-level slug indistinguishable from a category
        (findings §4) — so the count is left unwritten and the reason recorded.
        """
        sitemaps = [a for a in artifacts if a.kind == "sitemap" and a.body_path]
        shards = [
            (
                artifact.url,
                sitemap.parse(self._body_bytes(artifact), artifact.url)[1],
            )
            for artifact in sitemaps
        ]
        page_urls = [url for _shard, pages in shards for url in pages]

        blog_path = self._blog_path(page_urls, homepage_html, domain)
        # M1.27: a shard named `articles` is decided on its contents and its
        # siblings, so classification waits until every shard has been read and
        # the blog path is known.
        kinds = sitemap.classify(shards, blog_path)
        product_sitemap_urls = [
            url for shard, pages in shards if kinds[shard] == "product" for url in pages
        ]
        # A shard the shop itself labels as content is not catalogue, whatever
        # its URLs look like. It stays in `page_urls` regardless: dropping it
        # there took `/blogs/…` away from blog *path* detection and silently
        # turned `snocks.com` — 107 blog URLs, a post from July — into
        # `blog_exists = 0`, which is `opp.no_blog`'s +25 against a shop that
        # publishes weekly.
        catalogue_urls = [
            url for shard, pages in shards if kinds[shard] != "blog" for url in pages
        ]

        evidence = sitemaps[0].url if sitemaps else ""
        locales = parsers.hreflang_prefixes(homepage_html, domain)

        def candidates(urls: list[str], require_pattern: bool) -> set[str]:
            """Filtered to one locale, because a translation is not a product."""
            found = {
                url
                for url in urls
                if sampling.is_product_candidate(
                    url, domain, blog_path=blog_path, require_pattern=require_pattern
                )
            }
            primary = {
                url
                for url in found
                if not sampling.is_secondary_locale(
                    url, locales.primary, locales.secondary
                )
            }
            if found and not primary:
                # An exclusion that empties a catalogue is evidence about the
                # exclusion, not about the shop. Fall back rather than convert a
                # measured shop into an unmeasurable one on a path-shape guess.
                result.notes.append(
                    "locale filter would have excluded every candidate — not applied"
                )
                return found
            if len(found) != len(primary):
                result.notes.append(
                    f"locale filter dropped {len(found) - len(primary)} translated URLs"
                )
            return primary

        # A5's tier hierarchy, now shared with the count (M1.24). The primary
        # instrument is the shop's own product sitemap; path patterns are the
        # fallback, not the default. Reading them in the other order is what
        # made `smile-store.de` report 6 products against a catalogue of 194:
        # the shard holding all of them sat unread in the index while a
        # `/detail/` pattern scraped six stragglers off the rest of the site.
        if counted := candidates(product_sitemap_urls, require_pattern=False):
            # Tier 1's own membership is the evidence, so the path need not
            # match a pattern — SEO-rewritten catalogues would otherwise count
            # as empty.
            tier = "product_sitemap"
        elif counted := candidates(catalogue_urls, require_pattern=True):
            tier = "sitemap_path_pattern"
        else:
            tier = ""

        if counted:
            # The tier travels with the number: a count of 6 from a path
            # pattern and a count of 6 from a product sitemap are different
            # claims, and only one of them is the shop's own statement.
            self._write(
                result,
                "catalog.product_url_count",
                evidence,
                num=len(counted),
                text=tier,
            )
            return blog_path

        if not sitemaps:
            result.notes.append("no sitemap on disk — catalogue not measured")
            return blog_path

        # §10.3: the instrument does not apply. Not a zero, and not silence.
        reason = (
            "no product sitemap and no URL matching a product pattern; on this "
            "platform products are root-level slugs indistinguishable from categories"
        )
        self._write(result, "catalog.not_measurable", evidence, num=1, text=reason)
        self._raise_review_flag(result, "catalog_not_measurable")
        result.notes.append(
            f"catalog not measurable: {len(page_urls)} URLs, none identifiable as products"
        )
        return blog_path

    def _blog_path(
        self, page_urls: list[str], homepage_html: str, domain: str
    ) -> str | None:
        from portal import impressum as impressum_mod

        return impressum_mod.find_blog_path(
            page_urls, homepage_html, f"https://{domain}/", domain
        )

    # ── blog (§6.2's inputs) ────────────────────────────────────────────

    def _blog_signals(
        self,
        result: ExtractResult,
        artifacts: list[_Artifact],
        blog_path: str | None,
        homepage_html: str,
    ) -> None:
        index = next(
            (
                a
                for a in artifacts
                if a.kind == "blog_index" and a.body_path and a.http_status == 200
            ),
            None,
        )
        if index is None or blog_path is None:
            # `0` is written only because both the sitemaps and the homepage
            # were searched. §10.1 records that this instrument under-detects —
            # a blog on a subdomain or served as root-level slugs is invisible
            # to it — so §6.2 must not let a `0` carry `opp.no_blog`'s +25 alone.
            if homepage_html:
                self._write(
                    result,
                    "content.blog_exists",
                    "",
                    num=0,
                )
                result.notes.append(
                    "no blog index on disk; blog_exists=0 is vocabulary-limited (§10.1)"
                )
            return

        html = self._body(index)
        self._write(result, "content.blog_exists", index.url, num=1)
        read = parsers.read_blog_index(
            html, blog_path, index_path=path_of(index.url), today=self.today
        )
        article = next(
            (
                a
                for a in artifacts
                if a.kind == "blog_article" and a.body_path and a.http_status == 200
            ),
            None,
        )

        # A6, corrected by running it (M1.30): **the later of the two sources
        # wins, and neither is preferred.**
        #
        # The proposal read the sample first and fell back to the index. On the
        # corpus that lost two months on `navucko.com` and seventeen on
        # `bio-fleischer-laden.de`, because a *sampled* article is one post's
        # date while the index's is a maximum over every post it lists — and it
        # is not simply the other way round either: on `ekomia.de` the sampled
        # article is four months *newer* than anything the index dates. Neither
        # source is reliably the newest post, so both are lower bounds, and the
        # later of two lower bounds is the better one and never the worse.
        dated: list[tuple[date, str]] = []
        if read.newest_post is not None:
            dated.append((read.newest_post, index.url))
        article_html = self._body(article) if article is not None else ""
        if article is not None:
            if (
                published := parsers.newest_post_date(article_html, self.today)
            ) is not None:
                dated.append((published, article.url))
            # A6: the markup lives on the post too. `Article`/`BlogPosting` is
            # never on the listing — `schema.article_present` was `0` on every
            # blog index in the corpus, a wrong "checked and absent" for the
            # four shops whose posts do carry it.
            self._write(
                result,
                "schema.article_present",
                article.url,
                num=1 if parsers.has_article_schema(article_html) else 0,
            )
        else:
            # A6.1: no article, no claim about article markup. A `0` from the
            # index is a fact about the wrong page.
            result.notes.append(
                "no blog article on disk — schema.article_present stays unwritten (A6.1)"
            )

        if dated:
            newest, evidence = max(dated)
            self._write(result, "content.blog_last_post", evidence, day=newest)
        else:
            # §6.2's NULL branch: a blog whose dates cannot be parsed is an
            # unknown, not a stale blog. The flag is raised by `score`, not
            # here; this stage only declines to invent a date.
            result.notes.append("no parseable post date on the index or the sample")

        if read.post_count is not None:
            self._write(
                result, "content.blog_post_count", index.url, num=read.post_count
            )

    # ── product schema (A5.5/A5.6) ──────────────────────────────────────

    def _product_schema(
        self, result: ExtractResult, artifacts: list[_Artifact], homepage_html: str
    ) -> None:
        """Written **only** from a product page fetched with HTTP 200.

        The guard is the whole point: absent the page, `opp.no_product_schema`
        must fire in neither direction. A `0` here against a shop whose product
        pages were never retrieved is worth +10 to it, wrongly.
        """
        sample = next(
            (
                a
                for a in artifacts
                if a.kind == "product_page" and a.body_path and a.http_status == 200
            ),
            None,
        )
        if sample is None:
            result.notes.append(
                "no product page fetched — schema.product_present stays unwritten (A5.5)"
            )
            return
        html = self._body(sample)
        present = parsers.has_product_schema(html) or parsers.has_product_schema(
            homepage_html
        )
        self._write(
            result, "schema.product_present", sample.url, num=1 if present else 0
        )


def run(
    conn: sqlite3.Connection,
    company_rows: list[tuple[int, str]],
    artifacts_root: Path,
    today: date | None = None,
) -> tuple[int, list[ExtractResult]]:
    """Extract every company. Returns `(run_id, results)`."""
    cursor = conn.execute(
        "INSERT INTO run (started_at, stage) VALUES (?, 'extract-p1')", (utc_now(),)
    )
    run_id = int(cursor.lastrowid)
    stage = ExtractStage(conn, ArtifactStore(artifacts_root), run_id, today=today)

    results = [
        stage.run_company(company_id, domain) for company_id, domain in company_rows
    ]

    conn.execute(
        "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
        (utc_now(), len(results), run_id),
    )
    conn.commit()
    return run_id, results


__all__ = ["ExtractResult", "ExtractStage", "run"]
