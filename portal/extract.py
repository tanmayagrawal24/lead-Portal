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
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from portal import impressum as impressum_mod
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
    """One stored document. **Provenance travels on this object, not beside it**
    (M1.42).

    `id` is here so `_write` can take the artifact a value was read off and
    derive both `evidence_url` and `signal.artifact_id` from it. Passing a URL
    string instead is what let `catalog.product_url_count` cite a sitemap index
    holding none of the URLs it counted, on 8 of 8 shops in the corpus: the
    citation was a second expression describing what the first one had done, and
    a second expression can be wrong.
    """

    id: int
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
            "SELECT id, kind, url, body_path, http_status FROM artifact "
            "WHERE company_id = ? ORDER BY id",
            (company_id,),
        ).fetchall()
        return [
            _Artifact(r["id"], r["kind"], r["url"], r["body_path"], r["http_status"])
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
        source: _Artifact,
        *,
        num: float | None = None,
        text: str | None = None,
        day: date | None = None,
    ) -> None:
        """One signal, using §4's M1.5 idiom.

        `ON CONFLICT ... DO NOTHING` on the uniqueness target only — never
        `INSERT OR IGNORE`, which would also swallow a CHECK violation on
        `method` and turn a typo into a signal that silently never existed.

        **`source` is the artifact the value was read off, not a URL** (M1.42).
        Both provenance columns come out of it in one expression, so
        `evidence_url` and `artifact_id` cannot name different documents and
        neither can name a document the value did not come from. The parameter
        has no string form on purpose: a caller that cannot name an artifact has
        not established where its number came from, and §1's guarantee is
        exactly that it can.
        """
        self.conn.execute(
            """
            INSERT INTO signal
                (company_id, run_id, key, value_num, value_text, value_date,
                 method, evidence_url, artifact_id, observed_at)
            VALUES (?,?,?,?,?,?,'deterministic',?,?,?)
            ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING
            """,
            (
                result.company_id,
                self.run_id,
                key,
                num,
                text,
                day.isoformat() if day else None,
                source.url,
                source.id,
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
        self._homepage_signals(result, homepage, homepage_html)

        impressum = first("impressum")
        if impressum is not None:
            self._legal_form(result, impressum.url, self._body(impressum))
        else:
            result.notes.append("no impressum on disk — legal_form not extracted")

        located = self._catalog_and_blog(result, site, artifacts, homepage)
        self._blog_signals(result, artifacts, located, homepage)
        self._product_schema(result, artifacts, homepage)
        return result

    # ── homepage ────────────────────────────────────────────────────────

    def _homepage_signals(
        self, result: ExtractResult, page: _Artifact, html: str
    ) -> None:
        """Everything read off the homepage cites the homepage artifact `html`
        came out of — `page`, not a URL reconstructed from the seeded domain."""
        if platform := parsers.detect_platform(html):
            self._write(result, "platform.detected", page, text=platform)
        else:
            # Not "no platform": Shopware 5 emits none of the §5.3 signatures
            # (M1.11), and a real SW5 shop lands here. Recorded so a missing
            # `qual.ecommerce_platform` is distinguishable from an unchecked one.
            result.notes.append(
                "no platform signature matched (M1.11: SW5 is known undetected)"
            )

        if (length := parsers.meta_description_length(html)) is not None:
            self._write(result, "meta.description_length", page, num=length)
        # `0`, not silence, when a fetched homepage declares no alternates at
        # all (M3 audit §6). `opp.de_only` (+5) awards points for *being*
        # monolingual, and the parser returns `None` for "no hreflang links" —
        # so leaving it unwritten made the rule unable to fire on exactly the
        # population it describes, and able to fire only on shops that declare
        # hreflang and declare one language. 7 of 13 in the corpus. A homepage
        # we have with no alternates in it is a measurement, not a gap; a
        # homepage we do not have still writes nothing, so "checked and
        # monolingual" stays distinct from "never looked".
        self._write(
            result,
            "i18n.hreflang_count",
            page,
            num=parsers.hreflang_language_count(html) or 0,
        )
        if credit := parsers.footer_agency_credit(html, result.domain):
            self._write(result, "agency.footer_credit", page, text=credit)

        self._write(
            result,
            "reviews.trusted_shops",
            page,
            num=1 if parsers.has_trusted_shops(html) else 0,
        )
        if (reviews := parsers.aggregate_review_count(html)) is not None:
            self._write(result, "reviews.count", page, num=reviews)

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
        homepage: _Artifact,
    ) -> impressum_mod.BlogLocation | None:
        """`catalog.product_url_count`, plus the blog location the count excludes.

        The three-state rule lives here. A site with sitemaps but no URL
        matching any product pattern is *not* a site with no products — on JTL
        every product is a root-level slug indistinguishable from a category
        (findings §4) — so the count is left unwritten and the reason recorded.

        **Every URL carries the shard it was read off, from here down** (M1.42).
        The count used to be evidenced by `sitemaps[0]` — the first sitemap row
        by id, which on all 8 shops with a count was the *index*: a document
        listing other sitemaps and no pages at all. So the citation named a page
        in which the number could not be checked, and on `smile-store.de` it
        named `/shop/en/sitemap.xml`, the English subshop, while the 194 came
        from the primary-locale shard *after* M1.25's filter dropped `/shop/en/`
        as a translation. Pairing each URL with its shard makes the citation
        fall out of the same comprehension as the value.
        """
        sitemaps = [a for a in artifacts if a.kind == "sitemap" and a.body_path]
        shard_artifact = {a.url: a for a in sitemaps}
        shards = [
            (
                artifact.url,
                sitemap.parse(self._body_bytes(artifact), artifact.url)[1],
            )
            for artifact in sitemaps
        ]
        page_urls = [url for _shard, pages in shards for url in pages]

        located = impressum_mod.locate_blog(
            page_urls, self._body(homepage), homepage.url, domain
        )
        blog_path = located.path if located else None
        # M1.27: a shard named `articles` is decided on its contents and its
        # siblings, so classification waits until every shard has been read and
        # the blog path is known.
        kinds = sitemap.classify(shards, blog_path)
        product_sitemap_pairs = [
            (shard, url)
            for shard, pages in shards
            if kinds[shard] == "product"
            for url in pages
        ]
        # A shard the shop itself labels as content is not catalogue, whatever
        # its URLs look like. It stays in `page_urls` regardless: dropping it
        # there took `/blogs/…` away from blog *path* detection and silently
        # turned `snocks.com` — 107 blog URLs, a post from July — into
        # `blog_exists = 0`, which is `opp.no_blog`'s +25 against a shop that
        # publishes weekly.
        catalogue_pairs = [
            (shard, url)
            for shard, pages in shards
            if kinds[shard] != "blog"
            for url in pages
        ]

        homepage_html = self._body(homepage)
        locales = parsers.hreflang_prefixes(homepage_html, domain)

        def candidates(
            pairs: list[tuple[str, str]], require_pattern: bool
        ) -> dict[str, str]:
            """Surviving URLs, each mapped to the shard it was read off.

            A dict rather than a set because the count and its citation must
            come out of the same filter: `len()` is the number and the values
            are where the number is verifiable. Filtered to one locale, because
            a translation is not a product.
            """
            found = {
                url: shard
                for shard, url in pairs
                if sampling.is_product_candidate(
                    url, domain, blog_path=blog_path, require_pattern=require_pattern
                )
            }
            primary = {
                url: shard
                for url, shard in found.items()
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
        if counted := candidates(product_sitemap_pairs, require_pattern=False):
            # Tier 1's own membership is the evidence, so the path need not
            # match a pattern — SEO-rewritten catalogues would otherwise count
            # as empty.
            tier = "product_sitemap"
        elif counted := candidates(catalogue_pairs, require_pattern=True):
            tier = "sitemap_path_pattern"
        else:
            tier = ""

        if counted:
            # The tier travels with the number: a count of 6 from a path
            # pattern and a count of 6 from a product sitemap are different
            # claims, and only one of them is the shop's own statement.
            #
            # And so does the shard. One `evidence_url` cannot name a set, so it
            # names the shard that contributed the most of the counted URLs —
            # the document where the largest checkable part of the number
            # actually is. On all 8 shops in the corpus the locale filter leaves
            # a single contributing shard, so "most" is "all"; the tie-break is
            # the code-point minimum, for the same reason A5's is (§5.2).
            contributed = Counter(counted.values())
            top = min(contributed.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            self._write(
                result,
                "catalog.product_url_count",
                shard_artifact[top],
                num=len(counted),
                text=tier,
            )
            return located

        if not sitemaps:
            result.notes.append("no sitemap on disk — catalogue not measured")
            return located

        # §10.3: the instrument does not apply. Not a zero, and not silence.
        #
        # The citation is the largest shard searched, and the reason states the
        # extent — because this claim is about *every* shard and one URL cannot
        # name them all. Citing the index instead, as this did, pointed the
        # reader at a document with no URLs in it at all: nothing to see, and no
        # way to tell whether 12 URLs were searched or 12,000.
        searched, _pages = min(shards, key=lambda shard: (-len(shard[1]), shard[0]))
        reason = (
            "no product sitemap and no URL matching a product pattern in "
            f"{len(page_urls)} URLs across {len(shards)} sitemap shards; on this "
            "platform products are root-level slugs indistinguishable from categories"
        )
        self._write(
            result,
            "catalog.not_measurable",
            shard_artifact[searched],
            num=1,
            text=reason,
        )
        self._raise_review_flag(result, "catalog_not_measurable")
        result.notes.append(
            f"catalog not measurable: {len(page_urls)} URLs, none identifiable as products"
        )
        return located

    # ── blog (§6.2's inputs) ────────────────────────────────────────────

    def _blog_signals(
        self,
        result: ExtractResult,
        artifacts: list[_Artifact],
        located: impressum_mod.BlogLocation | None,
        homepage: _Artifact,
    ) -> None:
        index = next(
            (
                a
                for a in artifacts
                if a.kind == "blog_index" and a.body_path and a.http_status == 200
            ),
            None,
        )
        if index is None:
            self._no_blog_index(result, artifacts, located, homepage)
            return

        html = self._body(index)
        self._write(result, "content.blog_exists", index, num=1)
        read = parsers.read_blog_index(
            html,
            located.path if located else None,
            index_path=path_of(index.url),
            today=self.today,
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
        # The artifact travels in the tuple, not its URL: `max` then hands back
        # the document the winning date was parsed out of, and the citation is
        # the same object the value came from rather than a second lookup.
        dated: list[tuple[date, _Artifact]] = []
        index_dated = article_dated = False
        if read.newest_post is not None:
            dated.append((read.newest_post, index))
            index_dated = True
        article_html = self._body(article) if article is not None else ""
        if article is not None:
            if (
                published := parsers.newest_post_date(article_html, self.today)
            ) is not None:
                dated.append((published, article))
                article_dated = True
            # A6: the markup lives on the post too. `Article`/`BlogPosting` is
            # never on the listing — `schema.article_present` was `0` on every
            # blog index in the corpus, a wrong "checked and absent" for the
            # four shops whose posts do carry it.
            self._write(
                result,
                "schema.article_present",
                article,
                num=1 if parsers.has_article_schema(article_html) else 0,
            )
        else:
            # A6.1: no article, no claim about article markup. A `0` from the
            # index is a fact about the wrong page.
            result.notes.append(
                "no blog article on disk — schema.article_present stays unwritten (A6.1)"
            )

        if dated:
            # Keyed on the date alone: `_Artifact` is not orderable, and on a
            # tie the index — appended first — is the citation to keep, since a
            # tie is precisely the case where its maximum does bound the value.
            newest, evidence = max(dated, key=lambda pair: pair[0])
            self._write(result, "content.blog_last_post", evidence, day=newest)
            # The interim guard (§6.2). Both sources are lower bounds, but they
            # are not lower bounds of the same kind: the index's date is a
            # *maximum* over the posts it lists, while a sampled article's is
            # one post's and has nothing behind it. Where the index carries no
            # date at all, the value is a floor with no ceiling — it can show a
            # blog is at least this fresh, and it cannot show one is stale.
            #
            # So the basis travels with the date, exactly as A5's tier travels
            # with `catalog.product_url_count`. §6.2 reads it to decide whether
            # `opp.blog_stale` and `opp.blog_slowing` may fire at all. It is
            # written as an *enabling* fact rather than a suppressing one so
            # that a run predating this guard — where the signal is simply
            # absent — fails to the safe side, which is A5.5's discipline.
            # **The basis describes the date that was written, not the sources
            # that had one (M1.40).** It said `both` whenever each source
            # produced a date, and §6.2 reads `both` as "the index bounds this
            # from above". On `zecplus.de` the index's newest was 2021-03-10,
            # the sampled article was 2025-09-03, the article won the max — and
            # the basis still claimed a bound the index plainly does not
            # supply, since it failed to date the newest post we are holding.
            # `opp.blog_slowing` took +10 on that, which is M1.32's defect
            # arriving through the basis instead of through its absence.
            #
            # The index bounds the value only when the index's own maximum *is*
            # the value. Otherwise the date rests on the sample: a floor with
            # no ceiling, and §6.2's staleness rungs must not fire on it.
            index_bounds = index_dated and newest == read.newest_post
            basis = (
                ("both" if article_dated else "index") if index_bounds else "article"
            )
            self._write(result, "content.blog_last_post_basis", evidence, text=basis)
            if basis == "article":
                result.notes.append(
                    "blog_last_post is sample-only ("
                    + (
                        "the sampled post is newer than anything the index dates, "
                        "so the index's maximum does not bound it"
                        if index_dated
                        else "the index carries no date"
                    )
                    + "): a lower bound with no maximum behind it, so §6.2's "
                    "staleness rungs must not fire on it"
                )
        else:
            # §6.2's NULL branch: a blog whose dates cannot be parsed is an
            # unknown, not a stale blog. The flag is raised by `score`, not
            # here; this stage only declines to invent a date.
            result.notes.append("no parseable post date on the index or the sample")

        if read.post_count is not None:
            self._write(result, "content.blog_post_count", index, num=read.post_count)

        # §6.3's `neg.active_content` (−25) asks for four posts in six months and
        # had no data path at all until M3 measured for one: `blog_last_post` is
        # one date and `blog_post_count` is a total with no recency in it. The
        # dates travel rather than a pre-computed "posts in the last 180 days",
        # because §5 promises scoring is a pure recompute at zero cost — and a
        # stored recency count decays silently as the window moves.
        #
        # Written whenever an index was read, empty list included: "this index
        # dates nothing" is a measurement §6.3 needs, and it is the state 3 of
        # the 7 blogs in the corpus are in.
        self._write(
            result,
            "content.blog_post_dates",
            index,
            num=len(read.post_dates),
            text=",".join(day.isoformat() for day in read.post_dates),
        )

    def _no_blog_index(
        self,
        result: ExtractResult,
        artifacts: list[_Artifact],
        located: impressum_mod.BlogLocation | None,
        homepage: _Artifact,
    ) -> None:
        """No blog index on disk. **What may be claimed about that is M1.14.**

        `opp.no_blog` is +25, the largest award in ruleset v3, and it is an award
        for an *absence* — so by A7's one-question test it abstains wherever the
        absence was not established. Two different things can put us here, they
        are not equally recoverable, and the signal says which:

        - **A blog was located and its index is not on disk.** The fetch failed:
          a 404, a timeout, a momentarily disallowed path. `content.blog_exists`
          is then written **at all** — a `0` would award +25 against a shop whose
          blog we found and then failed to retrieve. It is a *transient* (A7's
          second table): it usually fixes itself on the next run, so it retries
          rather than filling the review queue on the first miss.
        - **Nothing was located.** `0` is written, and `content.blog_search_
          exhaustive` records whether both §5.3 instruments actually ran.

        **What "exhaustive" means, exactly, and what it does not.** It means a
        sitemap was enumerated *and* a homepage yielded parseable links — both
        instruments, not one. "Did we have a sitemap" was the tempting test and
        it is wrong, with the counter-example in the corpus: `zecplus.de` serves
        four sitemap shards and its blog lives on `blog.zecplus.de`, a host none
        of them names. A sitemap made one instrument available; it did not make
        the search complete.

        It does **not** mean the blog is not there. A blog on an unlinked
        subdomain is undetectable by construction, so an exhaustive search is
        always "we looked everywhere we can look" and never "it is not there".
        The `1` licenses the award; it does not certify the absence.

        **All three signals cite the homepage** (M1.42). They used to cite the
        empty string, which `docs/implementation-brief.md` forbids outright, and
        the homepage is not a placeholder standing in for a missing citation: it
        is the document the search actually read. Both §5.3 instruments run off
        it — `impressum.links` parses its anchors, and the sitemap inventory is
        the walk it seeded. A person reading a `no blog` verdict needs to see
        the page we looked at, and that page is this one.
        """
        homepage_html = self._body(homepage)
        if located is not None:
            where = located.url or located.path
            self._write(
                result,
                "content.blog_search_exhaustive",
                homepage,
                num=0,
                text=(
                    f"transient: blog located by {located.basis} at {where}, "
                    "its index is not on disk"
                ),
            )
            result.notes.append(
                f"blog located at {where} but no index fetched — blog_exists stays "
                "unwritten, retry next run (A7, transient)"
            )
            return

        self._write(result, "content.blog_exists", homepage, num=0)

        enumerated = any(a.kind == "sitemap" and a.body_path for a in artifacts)
        parsed = bool(impressum_mod.links(homepage_html, homepage.url))
        missing = [
            what
            for what, ok in (("sitemap", enumerated), ("homepage links", parsed))
            if not ok
        ]
        if missing:
            self._write(
                result,
                "content.blog_search_exhaustive",
                homepage,
                num=0,
                text="limit: no " + " and no ".join(missing),
            )
            result.notes.append(
                f"blog search not exhaustive (no {' and no '.join(missing)}) — "
                "opp.no_blog must not fire on this 0 (M1.14, A7)"
            )
        else:
            self._write(
                result,
                "content.blog_search_exhaustive",
                homepage,
                num=1,
                text="sitemap enumerated and homepage links parsed",
            )

    # ── product schema (A5.5/A5.6) ──────────────────────────────────────

    def _product_schema(
        self, result: ExtractResult, artifacts: list[_Artifact], homepage: _Artifact
    ) -> None:
        """Written **only** from a product page fetched with HTTP 200.

        The guard is the whole point: absent the page, `opp.no_product_schema`
        must fire in neither direction. A `0` here against a shop whose product
        pages were never retrieved is worth +10 to it, wrongly.

        **The citation follows whichever page decided the value** (M1.42). Two
        pages are read and either can produce the `1`, so citing the product
        page unconditionally would let a `1` decided by the homepage send a
        reader to a page that does not contain the markup. It has not happened
        in the corpus — on all 9 shops with the signal the product page carries
        it and no homepage does — but `opp.no_product_schema` is +10, and a
        latent desync is the one M1.40 was.
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
        if parsers.has_product_schema(self._body(sample)):
            found: _Artifact | None = sample
        elif parsers.has_product_schema(self._body(homepage)):
            found = homepage
            result.notes.append(
                "Product markup is on the homepage, not on the sampled product "
                f"page — schema.product_present cites {path_of(homepage.url)}"
            )
        else:
            found = None
        self._write(
            result,
            "schema.product_present",
            found or sample,
            num=1 if found else 0,
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
