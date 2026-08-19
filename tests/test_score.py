"""`score --phase 1` — §5.4's gate, §6's ruleset, §6.5's bands.

Most of what is asserted here is **what the ruleset refuses to claim**. M3's
audit (`docs/m3-absent-input-audit.md`) went through every rule for absent-input
behaviour before any of it was written, and three of the defects it found —
`opp.no_article_schema`, `opp.slow_site` and `neg.active_content` — were rules
that would have awarded or withheld points on evidence they never had. Those
cases are pinned first, because they are the ones that fail silently.
"""

from __future__ import annotations

import shutil
import socket
import sqlite3
import tempfile
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

from portal import db, migrate, ruleset, score

TODAY = date(2026, 8, 15)


#: Derived from the ruleset rather than typed out, so a rule that starts
#: reading a new field cannot quietly get a fixture where that field is absent
#: for a reason nobody chose.
PROFILE_FIELDS = sorted({field for rule in ruleset.RULES for field in rule.reads})


def profile(**overrides: object) -> dict[str, object]:
    """A company_profile row. Everything absent unless a test says otherwise,
    which is the state the audit cares about."""
    row: dict[str, object] = dict.fromkeys(PROFILE_FIELDS)
    row["company_id"] = 1
    row["domain"] = "muster.de"
    row.update(overrides)
    return row


def run(**overrides: object) -> score.ScoreResult:
    return score.evaluate(profile(**overrides), TODAY)


def points(result: score.ScoreResult, rule_id: str) -> int | None:
    for component in result.components:
        if component.rule_id == rule_id:
            return component.points
    return None


def state(result: score.ScoreResult, rule_id: str) -> str:
    for component in result.components:
        if component.rule_id == rule_id:
            return component.state
    return "declined"


def flags(result: score.ScoreResult) -> list[str]:
    """The §6.4 reasons raised, without their notes."""
    return [flag.reason for flag in result.review_flags]


def note(result: score.ScoreResult, reason: str) -> str:
    return next(flag.note for flag in result.review_flags if flag.reason == reason)


class TestRulesetIsData(unittest.TestCase):
    """B6 / §5.4. The collection is the thing `remaining_upside` and the startup
    assertion both read; neither is expressible over an if/elif chain."""

    def test_every_rule_declares_phase2_reachability(self) -> None:
        ruleset.assert_declared()
        for rule in ruleset.RULES:
            self.assertIsInstance(rule.phase2_reachable, bool, rule.id)

    def test_a_rule_that_forgets_the_declaration_cannot_be_constructed(self) -> None:
        with self.assertRaises(TypeError):
            ruleset.Rule(
                "x", 1, "6.1", ("a",), evaluate=lambda p, t: ruleset.declines()
            )  # type: ignore[call-arg]

    def test_the_assertion_catches_a_rule_that_can_never_fire(self) -> None:
        """§10.4's defect class — B7 and `neg.active_content` both read as
        implemented and could not be computed. This is the check that would
        have caught them as data."""
        broken = (
            ruleset.Rule(
                "qual.x", 5, "6.1", (), False, lambda p, t: ruleset.declines()
            ),
        )
        with self.assertRaises(ruleset.RulesetError):
            ruleset.assert_declared(broken)

    def test_duplicate_ids_are_refused(self) -> None:
        rule = ruleset.RULES[0]
        with self.assertRaises(ruleset.RulesetError):
            ruleset.assert_declared((rule, rule))

    def test_the_phase2_bound_is_fifty_and_derived_not_written_down(self) -> None:
        """M1.21's defect was a hand-maintained constant of 35 against a correct
        50. The number now comes from the ruleset, so it cannot drift from it."""
        upside = sum(r.upside for r in ruleset.RULES if r.phase2_reachable)
        self.assertEqual(upside, 50)

    def test_the_dormant_rules_are_exactly_the_ones_10_6_names(self) -> None:
        """M1.57. §10.6 lists which rules cannot fire until Phase 2, and a prose
        list that can drift from the thing it describes is M1.42's shape — which
        this project does not get to write in a section about avoiding it.

        So the spec's list is pinned against the ruleset here. Adding or removing
        a Phase-2-reachable rule fails this test and names §10.6 as the thing to
        update, which is the whole point: the third re-discovery of "is this dead
        code?" (M1.21, B7, the external audit) is meant to be the last."""
        self.assertEqual(
            {r.id for r in ruleset.RULES if r.phase2_reachable},
            {
                "qual.owner_operated",
                "qual.own_brand",
                "opp.ai_invisible",
                "opp.slow_site",
                "neg.has_agency",
            },
        )

    def test_own_brand_is_the_only_rule_with_no_phase_1_input(self) -> None:
        """§10.6's sharpest row, and the one the auditor read as dead code.
        `assert_declared` carries a named exemption for exactly this rule; the
        exemption is the documentation, and this is what stops a second rule
        quietly acquiring it."""
        self.assertEqual(
            {r.id for r in ruleset.RULES if not r.reads}, {"qual.own_brand"}
        )


class TestAbsentInputs(unittest.TestCase):
    """The audit table, as assertions. Each of these is a rule declining to
    claim something it cannot see."""

    def test_no_article_schema_abstains_without_a_sampled_article(self) -> None:
        """A7, found by M3's audit. A6.1 leaves the signal unwritten when no
        article was fetched, and reading that as `0` awards +8 for missing
        markup on a page we never had — A5.5's error, one signal over."""
        result = run(blog_exists=1, article_schema=None)
        self.assertEqual(state(result, "opp.no_article_schema"), ruleset.ABSTAINS)
        self.assertEqual(points(result, "opp.no_article_schema"), 0)

    def test_no_article_schema_still_fires_on_a_measured_zero(self) -> None:
        """Anti-vacuity: the guard must not have disabled the rule."""
        self.assertEqual(
            points(run(blog_exists=1, article_schema=0), "opp.no_article_schema"), 8
        )

    def test_slow_site_never_reads_a_missing_score_as_slow(self) -> None:
        self.assertIsNone(points(run(lighthouse_perf=None), "opp.slow_site"))
        self.assertEqual(points(run(lighthouse_perf=31), "opp.slow_site"), 10)

    def test_no_product_schema_abstains_without_its_sample(self) -> None:
        self.assertEqual(state(run(), "opp.no_product_schema"), ruleset.ABSTAINS)
        self.assertEqual(points(run(product_schema=0), "opp.no_product_schema"), 10)

    def test_de_only_fires_on_a_homepage_with_no_alternates(self) -> None:
        """B7's shape, found by the audit: the parser returns `None` for "no
        hreflang", so leaving it unwritten made the rule unable to fire on
        exactly the monolingual shops it describes — 7 of 13 in the corpus."""
        self.assertEqual(points(run(hreflang_count=0), "opp.de_only"), 5)
        self.assertEqual(points(run(hreflang_count=1), "opp.de_only"), 5)
        self.assertIsNone(points(run(hreflang_count=4), "opp.de_only"))

    def test_de_only_abstains_when_the_homepage_was_never_read(self) -> None:
        self.assertEqual(
            state(run(hreflang_count=None), "opp.de_only"), ruleset.ABSTAINS
        )

    def test_product_depth_never_reads_an_unmeasurable_catalogue_as_empty(self) -> None:
        self.assertIsNone(points(run(product_url_count=None), "qual.product_depth"))
        self.assertIsNone(points(run(product_url_count=None), "qual.own_domain_shop"))


class TestBlogLadder(unittest.TestCase):
    """§6.2 as an ordered chain: first match wins, evaluation stops."""

    def test_rung_one_abstains_and_suppresses_the_whole_ladder(self) -> None:
        """M1.34. If `blog_exists` is unknown, `blog_last_post` is not a
        meaningful question and the rungs below must not be reached."""
        result = run(
            blog_exists=0,
            blog_search_exhaustive=0,
            blog_search_limit="limit: no sitemap",
            blog_last_post="2019-01-01",
            blog_last_post_basis="both",
            blog_post_count=2,
        )
        self.assertEqual(state(result, "opp.no_blog"), ruleset.ABSTAINS)
        self.assertEqual(result.total, 0)
        for suppressed in ("opp.blog_stale", "opp.thin_blog", "opp.blog_slowing"):
            self.assertIsNone(points(result, suppressed), suppressed)

    def test_an_exhaustive_search_licenses_the_award(self) -> None:
        result = run(blog_exists=0, blog_search_exhaustive=1)
        self.assertEqual(points(result, "opp.no_blog"), 25)

    def test_a_transient_abstention_routes_to_nobody_yet(self) -> None:
        """A7b: the blog was found and its index missed. It retries rather than
        filling the queue on the first miss (N = 3, §5)."""
        result = run(
            blog_exists=None,
            blog_search_exhaustive=0,
            blog_search_limit="transient: blog located by anchor_text at x",
        )
        self.assertEqual(flags(result), [])

    def test_a_measurement_limit_routes_now(self) -> None:
        result = run(
            blog_exists=0,
            blog_search_exhaustive=0,
            blog_search_limit="limit: no homepage links",
        )
        self.assertEqual(flags(result), ["blog_undetectable"])

    def test_stale_fires_only_on_a_bounded_date(self) -> None:
        stale = {"blog_exists": 1, "blog_last_post": "2020-03-24"}
        self.assertEqual(
            points(run(**stale, blog_last_post_basis="both"), "opp.blog_stale"), 20
        )
        unbounded = run(**stale, blog_last_post_basis="article")
        self.assertEqual(state(unbounded, "opp.blog_stale"), ruleset.ABSTAINS)
        self.assertIn("blog_date_unbounded", flags(unbounded))

    def test_an_unbounded_stale_date_does_not_fall_through_to_thin_blog(self) -> None:
        """§6.2: `opp.thin_blog` needs a post *within* 365 days, which is
        exactly what is not known here."""
        result = run(
            blog_exists=1,
            blog_last_post="2020-03-24",
            blog_last_post_basis="article",
            blog_post_count=2,
        )
        self.assertIsNone(points(result, "opp.thin_blog"))

    def test_an_unparseable_date_stops_at_rung_two(self) -> None:
        result = run(blog_exists=1, blog_post_count=2)
        self.assertEqual(state(result, "opp.blog_stale"), ruleset.ABSTAINS)
        self.assertIn("blog_date_unparseable", flags(result))
        self.assertIsNone(points(result, "opp.thin_blog"))

    def test_an_uncounted_blog_does_not_win_thin_blog_but_does_fall_through(
        self,
    ) -> None:
        result = run(
            blog_exists=1,
            blog_last_post="2026-01-05",
            blog_last_post_basis="both",
            blog_post_count=None,
        )
        self.assertIsNone(points(result, "opp.thin_blog"))
        self.assertEqual(points(result, "opp.blog_slowing"), 10)

    def test_a_current_substantial_blog_awards_nothing(self) -> None:
        result = run(
            blog_exists=1,
            blog_last_post="2026-07-01",
            blog_last_post_basis="both",
            blog_post_count=40,
        )
        self.assertEqual(
            [c.rule_id for c in result.components if c.rule_id.startswith("opp.blog")],
            [],
        )


class TestActiveContent(unittest.TestCase):
    """§6.3's −25, and the data path M3's audit had to define for it.

    The asymmetry is the rule: a partial enumeration can establish activity and
    can never establish its absence.
    """

    def test_it_fires_on_a_lower_bound(self) -> None:
        """Four dated posts inside six months means at least four, however many
        posts carry no date. Measured on navucko.com."""
        result = run(
            blog_exists=1,
            blog_post_count=6,
            blog_post_dates="2026-03-02,2026-04-08,2026-05-19,2026-06-20",
        )
        self.assertEqual(points(result, "neg.active_content"), -25)

    def test_it_declines_only_on_a_complete_enumeration(self) -> None:
        """blackpolish.de: 3 posts, 3 dated, so ≥ 4 is impossible."""
        result = run(
            blog_exists=1,
            blog_post_count=3,
            blog_post_dates="2020-01-02,2020-02-11,2020-03-24",
        )
        self.assertIsNone(points(result, "neg.active_content"))

    def test_it_abstains_on_a_partial_enumeration(self) -> None:
        """doonails.de: 26 posts, none dated, newest post 2½ months old. The
        rule that exists to catch exactly this company cannot see it."""
        result = run(blog_exists=1, blog_post_count=26, blog_post_dates="")
        self.assertEqual(state(result, "neg.active_content"), ruleset.ABSTAINS)
        reason = next(
            c.reason for c in result.components if c.rule_id == "neg.active_content"
        )
        self.assertIn("26", reason)

    def test_no_blog_means_no_posts(self) -> None:
        result = run(blog_exists=0, blog_search_exhaustive=1)
        self.assertIsNone(points(result, "neg.active_content"))

    def test_an_unconfirmed_blog_is_not_this_rules_problem(self) -> None:
        """The ladder already abstained; a second abstention on the same
        ignorance would double-count the silence."""
        result = run(blog_exists=None)
        self.assertIsNone(points(result, "neg.active_content"))


class TestAgencyCredit(unittest.TestCase):
    def test_a_platform_credit_never_fires_it(self) -> None:
        """§10.4. The parser excludes platform vocabulary, and migration 006
        stops the *read model* re-serving a pre-fix observation — which is how
        three JTL shops were still carrying −20 for their choice of shop
        system."""
        self.assertIsNone(points(run(agency_credit=None), "neg.has_agency"))

    def test_a_real_credit_fires_with_the_credit_quoted(self) -> None:
        result = run(agency_credit="cubecom Webdesign")
        self.assertEqual(points(result, "neg.has_agency"), -20)
        reason = next(
            c.reason for c in result.components if c.rule_id == "neg.has_agency"
        )
        self.assertIn("cubecom Webdesign", reason)


class TestOwnerOperatedDirectorCount(unittest.TestCase):
    """§6.1 disjunct 2, as amended by M1.46: `1 <= directors <= 2`.

    The predicate is the invariant behind A2's mapping convention, not a
    duplicate of it. A2 says `impressum.gf_count` is written only when the page
    names at least one Geschäftsführer — but that holds only while every future
    writer remembers it, and this rule is +15 and raises the company's effective
    Phase-2 threshold from 5 to 20 (§7.1). So the zero case is pinned here, on
    the rule, where a mapping mistake cannot reach it.
    """

    def test_zero_directors_does_not_fire(self) -> None:
        """Naming none is not naming ≤ 2. Before M1.46 this took +15 with the
        reason 'Das Impressum nennt 0 Geschäftsführende'."""
        self.assertIsNone(points(run(gf_count=0), "qual.owner_operated"))

    def test_one_and_two_fire(self) -> None:
        for directors in (1, 2):
            with self.subTest(directors=directors):
                self.assertEqual(
                    points(run(gf_count=directors), "qual.owner_operated"), 15
                )

    def test_three_does_not_fire(self) -> None:
        self.assertIsNone(points(run(gf_count=3), "qual.owner_operated"))

    def test_zero_still_reaches_the_other_disjuncts(self) -> None:
        """M1.46 narrows disjunct 2 only. A sole trader with no Geschäftsführer
        is meant to be caught by legal_form or by being named on the site —
        which is the coherence argument for the narrowing, so it is tested."""
        self.assertEqual(
            points(run(gf_count=0, legal_form="e.K."), "qual.owner_operated"), 15
        )
        self.assertEqual(
            points(run(gf_count=0, owner_named=1), "qual.owner_operated"), 15
        )


class TestGateAndBands(unittest.TestCase):
    def test_bands(self) -> None:
        for total, band in (
            (80, "A"),
            (75, "A"),
            (74, "B"),
            (55, "B"),
            (54, "C"),
            (30, "C"),
            (29, "D"),
            (-5, "D"),
        ):
            self.assertEqual(score.band_of(total), band, total)

    def test_a_banked_rule_leaves_the_bound(self) -> None:
        """§5.4's table: 35 for a company that banked `owner_operated`, 50 for
        one that did not. This is why per-company beats any global constant."""
        self.assertEqual(run(legal_form="e.K.").remaining_upside, 35)
        self.assertEqual(run(legal_form="GmbH").remaining_upside, 50)

    def test_the_gate_admits_on_upside_not_on_band(self) -> None:
        """A company scoring 54 in Phase 1 with 35 still available is an 89 — a
        clear A that a band gate would never have looked at."""
        low = run(platform="Shopify", legal_form="e.K.")
        self.assertEqual(low.total, 30)
        self.assertTrue(low.admitted)

    def test_a_company_whose_ceiling_is_below_b_is_stopped(self) -> None:
        stopped = run(agency_credit="SK Design", legal_form="e.K.")
        self.assertEqual(stopped.total, -5)
        self.assertFalse(stopped.admitted)

    def test_a_negative_rule_adds_nothing_to_the_bound(self) -> None:
        """§5.4 bounds the *maximum positive* points Phase 2 can add, and
        `neg.has_agency` can only subtract."""
        by_id = {r.id: r for r in ruleset.RULES}
        self.assertTrue(by_id["neg.has_agency"].phase2_reachable)
        self.assertEqual(by_id["neg.has_agency"].upside, 0)


class TestPurity(unittest.TestCase):
    def test_scoring_issues_no_network_call(self) -> None:
        """§5.4: "pure function over company_profile, costs nothing". Asserted
        rather than assumed, because a rule reaching for a page later would be
        invisible until the bill arrived."""
        real = socket.socket

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("scoring opened a socket")

        socket.socket = refuse  # type: ignore[assignment]
        try:
            result = run(
                platform="Shopify",
                blog_exists=0,
                blog_search_exhaustive=1,
                hreflang_count=0,
            )
        finally:
            socket.socket = real  # type: ignore[assignment]
        self.assertEqual(result.total, 45)

    def test_the_same_profile_always_scores_the_same(self) -> None:
        first, second = run(platform="Shopify"), run(platform="Shopify")
        self.assertEqual(
            [(c.rule_id, c.points, c.reason) for c in first.components],
            [(c.rule_id, c.points, c.reason) for c in second.components],
        )


class TestReasonsAreLetterReady(unittest.TestCase):
    """`score_component.reason` is the deliverable, not a label."""

    ALL = (
        {
            "platform": "Shopify",
            "legal_form": "e.K.",
            "product_url_count": 306,
            "trusted_shops": 1,
            "blog_exists": 1,
            "blog_last_post": "2023-03-12",
            "blog_last_post_basis": "both",
            "blog_post_count": 4,
            "article_schema": 0,
            "product_schema": 0,
            "hreflang_count": 0,
            "lighthouse_perf": 31,
            "agency_credit": "cubecom Webdesign",
            "ai_queries_checked": 2,
            "ai_brand_mentions": 0,
        },
        {
            "blog_exists": 1,
            "blog_post_count": 26,
            "blog_post_dates": "",
            "gf_count": 1,
            "review_count": 851,
            "blog_last_post": "2026-06-20",
            "blog_last_post_basis": "article",
            "hreflang_count": 9,
        },
        {
            "blog_exists": 0,
            "blog_search_exhaustive": 0,
            "blog_search_limit": "limit: no sitemap",
        },
        {
            "blog_exists": 1,
            "blog_post_count": 6,
            "owner_named": 1,
            "blog_post_dates": "2026-03-02,2026-04-08,2026-05-19,2026-06-20",
            "blog_last_post": "2026-06-20",
            "blog_last_post_basis": "both",
        },
    )

    def components(self) -> list[score.Component]:
        found: list[score.Component] = []
        for case in self.ALL:
            found.extend(run(**case).components)
        return found

    def test_every_reason_is_a_complete_german_sentence(self) -> None:
        seen = set()
        for component in self.components():
            seen.add(component.rule_id)
            with self.subTest(rule=component.rule_id):
                self.assertTrue(component.reason, "empty reason")
                self.assertTrue(component.reason[0].isupper(), component.reason)
                self.assertTrue(
                    component.reason.rstrip().endswith("."), component.reason
                )
                self.assertNotIn("=", component.reason)
                self.assertNotIn("_", component.reason)
        # Anti-vacuity: the fixtures must actually exercise most of the ruleset.
        self.assertGreaterEqual(len(seen), 12)

    def test_a_date_is_named_the_way_a_letter_names_it(self) -> None:
        result = run(
            blog_exists=1, blog_last_post="2023-03-12", blog_last_post_basis="both"
        )
        reason = next(
            c.reason for c in result.components if c.rule_id == "opp.blog_stale"
        )
        self.assertIn("März 2023", reason)
        self.assertNotIn("2023-03-12", reason)

    def test_an_abstention_says_it_is_not_assessable(self) -> None:
        for component in self.components():
            if component.state == ruleset.ABSTAINS:
                self.assertTrue(
                    component.reason.startswith("Nicht bewertbar:"), component.reason
                )


class ScoreStageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = db.connect(Path(tmp.name) / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.run_id = self.add_run("extract-p1", "2026-08-15T00:00:00Z")

    def add_run(self, stage: str, started_at: str, *, finished: bool = True) -> int:
        """A run row. `finished` is explicit because migration 007 makes it
        load-bearing: only a run that reached the end is authoritative for the
        signals it wrote, and for the ones it did not."""
        return int(
            self.conn.execute(
                "INSERT INTO run (started_at, stage, finished_at) VALUES (?,?,?)",
                (started_at, stage, started_at if finished else None),
            ).lastrowid
        )

    def company(self, domain: str = "muster.de", excluded: int = 0) -> int:
        return int(
            self.conn.execute(
                "INSERT INTO company (domain, discovery_source, discovered_at, excluded) "
                "VALUES (?,'seed_csv','2026-08-15T00:00:00Z',?)",
                (domain, excluded),
            ).lastrowid
        )

    def signal(
        self, company_id: int, key: str, *, num=None, text=None, run_id=None
    ) -> None:
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, value_text, method, "
            "evidence_url, observed_at) VALUES (?,?,?,?,?,'deterministic','','2026-08-15T00:00:00Z')",
            (company_id, run_id or self.run_id, key, num, text),
        )

    def components(self, run_id: int, company_id: int) -> list[tuple[str, int, str]]:
        return [
            (r["rule_id"], r["points"], r["reason"])
            for r in self.conn.execute(
                "SELECT sc.* FROM score_component sc JOIN score s ON s.id = sc.score_id "
                "WHERE s.run_id = ? AND s.company_id = ? ORDER BY sc.id",
                (run_id, company_id),
            )
        ]


class TestPersistence(ScoreStageTestCase):
    def test_a_score_row_carries_the_ruleset_version(self) -> None:
        company_id = self.company()
        self.signal(company_id, "platform.detected", text="Shopify")
        run_id, _ = score.run(self.conn, today=TODAY)
        row = self.conn.execute(
            "SELECT total, band, ruleset_version FROM score WHERE run_id = ?", (run_id,)
        ).fetchone()
        self.assertEqual(row["ruleset_version"], "v3")
        self.assertEqual((row["total"], row["band"]), (15, "D"))

    def test_re_running_the_same_run_id_is_a_recompute_not_a_duplicate(self) -> None:
        company_id = self.company()
        self.signal(company_id, "platform.detected", text="Shopify")
        run_id, _ = score.run(self.conn, today=TODAY)
        before = self.components(run_id, company_id)
        score.run(self.conn, today=TODAY, run_id=run_id)
        self.assertEqual(self.components(run_id, company_id), before)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM score").fetchone()[0], 1
        )

    def test_component_ordering_is_stable_across_runs(self) -> None:
        company_id = self.company()
        for key, num, text in (
            ("platform.detected", None, "Shopify"),
            ("catalog.product_url_count", 306, "product_sitemap"),
            ("content.blog_exists", 0, None),
            ("content.blog_search_exhaustive", 1, "sitemap enumerated"),
            ("i18n.hreflang_count", 0, None),
        ):
            self.signal(company_id, key, num=num, text=text)
        first, _ = score.run(self.conn, today=TODAY)
        second, _ = score.run(self.conn, today=TODAY)
        self.assertEqual(
            self.components(first, company_id), self.components(second, company_id)
        )
        self.assertEqual(
            [c[0] for c in self.components(first, company_id)],
            [
                "qual.ecommerce_platform",
                "qual.product_depth",
                "qual.own_domain_shop",
                "opp.no_blog",
                "opp.no_product_schema",
                "opp.de_only",
            ],
        )

    def test_the_gate_is_recorded_as_signals(self) -> None:
        company_id = self.company()
        self.signal(company_id, "platform.detected", text="Shopify")
        run_id, _ = score.run(self.conn, today=TODAY)
        written = dict(
            self.conn.execute(
                "SELECT key, value_num FROM signal WHERE run_id = ? AND key LIKE 'gate.%'",
                (run_id,),
            ).fetchall()
        )
        self.assertEqual(
            written, {"gate.phase2_admitted": 1, "gate.remaining_upside": 50}
        )

    def test_excluded_companies_are_not_scored(self) -> None:
        """§6.4: a `duplicate_site` row is the same lead under another id, and
        banding it would put one company in the list twice."""
        self.company("gone.de", excluded=1)
        kept = self.company("kept.de")
        self.signal(kept, "platform.detected", text="Shopify")
        _run_id, results = score.run(self.conn, today=TODAY)
        self.assertEqual([r.domain for r in results], ["kept.de"])

    def test_an_abstention_raises_its_flag_with_the_reason_as_the_note(self) -> None:
        company_id = self.company()
        self.signal(company_id, "content.blog_exists", num=0)
        self.signal(
            company_id,
            "content.blog_search_exhaustive",
            num=0,
            text="limit: no homepage links",
        )
        score.run(self.conn, today=TODAY)
        row = self.conn.execute(
            "SELECT reason, raised_note FROM review_flag WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        self.assertEqual(row["reason"], "blog_undetectable")
        self.assertIn("Nicht bewertbar", row["raised_note"])


class TestScoreDateIsPinned(ScoreStageTestCase):
    """M1.74, closing M1.66.

    A band is a function of `(signals, today)`. Before this, `score` recorded
    only `computed_at` — a clock read taken in `_persist` — while the rules ran
    against `ScoreStage.today`, and a stage given `today=2020-03-07` wrote
    `computed_at=2026-08-18` with the evaluation date **recorded nowhere**.

    **The test that would have caught it** is the first one below: inject a
    `today` far from the wall clock and assert the *stored* value is the
    injected one. Everything else here defends the property that they are one
    expression rather than two that happen to agree.
    """

    #: Deliberately six years from any wall clock this will ever run on. A date
    #: near today would pass against a second clock read and prove nothing.
    INJECTED = date(2020, 3, 7)

    def score_row(self, company_id: int):
        return self.conn.execute(
            "SELECT * FROM score WHERE run_id = ? AND company_id = ?",
            (self.run_id, company_id),
        ).fetchone()

    def test_the_stored_date_is_the_injected_one_not_the_wall_clock(self) -> None:
        company_id = self.company()
        stage = score.ScoreStage(self.conn, self.run_id, 1, self.INJECTED)
        stage.run_company({"company_id": company_id, "domain": "muster.de"})
        row = self.score_row(company_id)

        self.assertEqual(row["evaluated_on"], "2020-03-07")
        # And the two columns are genuinely different facts, not a copy: the row
        # was written now, and evaluated against 2020.
        self.assertTrue(row["computed_at"].startswith("20"))
        self.assertNotEqual(row["computed_at"][:10], row["evaluated_on"])

    def test_the_stored_date_is_the_one_the_rules_were_given(self) -> None:
        """The property, stated directly: whatever `evaluate` saw is what is
        stored. Asserted against `evaluate`'s own return rather than against a
        constant, so it holds for any date without the test being rewritten."""
        for injected in (date(2020, 3, 7), date(2031, 12, 31), date(2026, 8, 18)):
            with self.subTest(today=injected):
                company_id = self.company(f"d{injected.year}.de")
                profile = {"company_id": company_id, "domain": f"d{injected.year}.de"}
                result = score.evaluate(profile, injected)
                self.assertEqual(result.evaluated_on, injected)

                stage = score.ScoreStage(self.conn, self.run_id, 1, injected)
                stage.run_company(profile)
                self.assertEqual(
                    self.score_row(company_id)["evaluated_on"],
                    result.evaluated_on.isoformat(),
                )

    def test_a_recompute_updates_the_date_rather_than_keeping_the_first(
        self,
    ) -> None:
        """`_persist` is an upsert (§5, `uq_score_identity`). Re-scoring the
        same run against a different date must move the stored date with it, or
        the row would claim a band was evaluated on a day it was not."""
        company_id = self.company()
        profile = {"company_id": company_id, "domain": "muster.de"}
        score.ScoreStage(self.conn, self.run_id, 1, date(2020, 3, 7)).run_company(
            profile
        )
        self.assertEqual(self.score_row(company_id)["evaluated_on"], "2020-03-07")

        score.ScoreStage(self.conn, self.run_id, 1, date(2024, 1, 1)).run_company(
            profile
        )
        self.assertEqual(self.score_row(company_id)["evaluated_on"], "2024-01-01")

    def test_persisting_a_result_with_no_date_is_refused(self) -> None:
        """The schema column is nullable **only** so pre-010 rows can say "never
        recorded". A row written now with no date would be a fresh instance of
        the defect M1.74 closed, so it fails loudly instead of inserting a NULL
        that reads like history."""
        company_id = self.company()
        stage = score.ScoreStage(self.conn, self.run_id, 1, self.INJECTED)
        orphan = score.ScoreResult(domain="muster.de", company_id=company_id)
        self.assertIsNone(orphan.evaluated_on)
        with self.assertRaises(ValueError) as caught:
            stage._persist(orphan)
        self.assertIn("M1.74", str(caught.exception))

    def test_a_row_that_predates_migration_010_is_not_backfilled(self) -> None:
        """Migration 010 adds no UPDATE, and this proves it **on a row that
        existed before 010 ran**.

        The obvious version of this test is vacuous and was written first: if
        the row is inserted after `apply_pending` has already run, the migration
        updated an empty table and a backfilling 010 passes it. Caught by the
        negative control, not by review. So the database here is taken to 009,
        given a score row, and only then advanced to 010.

        The ruling under test: inferring `date(computed_at)` would be right
        almost always — nothing injects `today` outside tests — and wrong
        exactly at the midnight-UTC boundary the column exists for. A fabricated
        measurement that is usually right is worse than a NULL, because a NULL
        can be seen.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        upto_009 = Path(tmp.name) / "migrations"
        upto_009.mkdir()
        for number, path in migrate.discover():
            if number < 10:
                shutil.copy(path, upto_009 / path.name)

        old_db = db.connect(Path(tmp.name) / "pre010.db")
        self.addCleanup(old_db.close)
        migrate.apply_pending(old_db, upto_009)
        self.assertEqual(migrate.current_version(old_db), 9)
        self.assertNotIn(
            "evaluated_on", [r[1] for r in old_db.execute("PRAGMA table_info(score)")]
        )

        run_id = int(
            old_db.execute(
                "INSERT INTO run (started_at, stage, finished_at) "
                "VALUES ('2026-08-17T23:59:00Z','score_p1','2026-08-17T23:59:00Z')"
            ).lastrowid
        )
        company_id = int(
            old_db.execute(
                "INSERT INTO company (domain, discovery_source, discovered_at) "
                "VALUES ('alt.de','seed_csv','2026-08-17T00:00:00Z')"
            ).lastrowid
        )
        # 23:59 UTC: the exact case a backfill from `computed_at` gets wrong.
        old_db.execute(
            "INSERT INTO score (company_id, run_id, phase, total, band, "
            "ruleset_version, computed_at) VALUES (?,?,1,0,'D','v3',?)",
            (company_id, run_id, "2026-08-17T23:59:00Z"),
        )
        old_db.commit()

        self.assertEqual(migrate.apply_pending(old_db), [10])
        row = old_db.execute(
            "SELECT evaluated_on FROM score WHERE company_id = ?", (company_id,)
        ).fetchone()
        self.assertIsNone(
            row["evaluated_on"],
            "migration 010 backfilled a date nobody observed — see §4's ruling",
        )


class TestContactBlock(ScoreStageTestCase):
    """Migration 008. A7's third axis: an abstention that leaves the score too
    *high* refuses outbound contact until a human resolves it."""

    def cadence_unmeasurable(self, domain: str = "muster.de") -> int:
        """`doonails.de`'s shape: an index listing posts, none of them dated."""
        company_id = self.company(domain)
        self.signal(company_id, "content.blog_exists", num=1)
        self.signal(company_id, "content.blog_post_count", num=26)
        self.signal(company_id, "content.blog_last_post", text=None)
        return company_id

    def test_the_abstention_now_routes(self) -> None:
        company_id = self.cadence_unmeasurable()
        score.run(self.conn, today=TODAY)
        reasons = {
            row["reason"]
            for row in self.conn.execute(
                "SELECT reason FROM review_flag WHERE company_id = ?", (company_id,)
            )
        }
        self.assertIn("blog_cadence_unmeasurable", reasons)

    def test_it_blocks_outbound_contact(self) -> None:
        """The consequence, not just the queue entry. §8 fails an export that
        cannot state its basis; this fails a contact the score cannot support."""
        company_id = self.cadence_unmeasurable()
        _run_id, results = score.run(self.conn, today=TODAY)
        self.assertTrue(results[0].contact_blocked)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO outreach (company_id, channel, occurred_at) "
                "VALUES (?, 'phone', ?)",
                (company_id, "2026-08-16T09:00:00Z"),
            )

    def test_resolving_the_flag_lifts_the_block(self) -> None:
        company_id = self.cadence_unmeasurable()
        score.run(self.conn, today=TODAY)
        self.conn.execute(
            "UPDATE review_flag SET resolved_at = ?, resolved_by_human = 1 "
            "WHERE company_id = ? AND reason = 'blog_cadence_unmeasurable'",
            ("2026-08-16T10:00:00Z", company_id),
        )
        self.conn.execute(
            "INSERT INTO outreach (company_id, channel, occurred_at) "
            "VALUES (?, 'post', ?)",
            (company_id, "2026-08-16T11:00:00Z"),
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0], 1
        )

    def test_a_too_low_abstention_blocks_nothing(self) -> None:
        """The axis has to discriminate, or it is just a second word for
        `needs_review`. `blog_undetectable` withholds +25 — the lead reads too
        weak, which is a queue item and not a phone call."""
        company_id = self.company()
        self.signal(company_id, "content.blog_exists", num=0)
        self.signal(
            company_id,
            "content.blog_search_exhaustive",
            num=0,
            text="limit: no sitemap",
        )
        _run_id, results = score.run(self.conn, today=TODAY)
        self.assertTrue(results[0].review_flags)
        self.assertFalse(results[0].contact_blocked)
        self.conn.execute(
            "INSERT INTO outreach (company_id, channel, occurred_at) "
            "VALUES (?, 'phone', ?)",
            (company_id, "2026-08-16T09:00:00Z"),
        )

    def test_each_flag_carries_its_own_abstentions_note(self) -> None:
        """Two rules abstain for different reasons; the notes must not cross.
        Sent to the wrong page, a person answers the wrong question (§6.4)."""
        company_id = self.company()
        self.signal(company_id, "content.blog_exists", num=1)
        self.signal(company_id, "content.blog_post_count", num=9)
        notes = dict(
            self.conn.execute(
                "SELECT reason, raised_note FROM review_flag WHERE company_id = ?",
                (company_id,),
            ).fetchall()
        )
        score.run(self.conn, today=TODAY)
        notes = dict(
            self.conn.execute(
                "SELECT reason, raised_note FROM review_flag WHERE company_id = ?",
                (company_id,),
            ).fetchall()
        )
        self.assertIn("kein Datum", notes["blog_date_unparseable"])
        self.assertIn("lesbares Datum", notes["blog_cadence_unmeasurable"])


class TestPersistentTransient(ScoreStageTestCase):
    """Migration 009 / A7b. A transient retries; on the third consecutive run
    over three distinct days it has stopped being transient."""

    def scored_on(self, company_id: int, day: str) -> score.ScoreResult:
        run_id = self.add_run("score-p1", f"{day}T09:00:00Z")
        _run_id, results = score.run(
            self.conn, today=date.fromisoformat(day), run_id=run_id
        )
        return next(r for r in results if r.company_id == company_id)

    def raised(self, company_id: int) -> list[str]:
        return [
            row["reason"]
            for row in self.conn.execute(
                "SELECT reason FROM review_flag WHERE company_id = ?", (company_id,)
            )
        ]

    def test_two_runs_are_not_enough(self) -> None:
        company_id = self.company()  # no product sample: opp.no_product_schema abstains
        self.scored_on(company_id, "2026-08-14")
        self.scored_on(company_id, "2026-08-15")
        self.assertNotIn("fetch_persistently_failing", self.raised(company_id))

    def test_the_third_day_routes_it(self) -> None:
        company_id = self.company()
        for day in ("2026-08-14", "2026-08-15", "2026-08-16"):
            self.scored_on(company_id, day)
        self.assertIn("fetch_persistently_failing", self.raised(company_id))
        note = self.conn.execute(
            "SELECT raised_note FROM review_flag WHERE company_id = ? "
            "AND reason = 'fetch_persistently_failing'",
            (company_id,),
        ).fetchone()[0]
        self.assertIn("2026-08-14", note)
        self.assertIn("Produktseite", note)

    def test_three_runs_in_one_day_are_one_day(self) -> None:
        """§5: without the distinct-day requirement a crash-restart loop inside
        one afternoon manufactures the flag, and the flag would then be about
        our own crash rather than about the shop."""
        company_id = self.company()
        for _ in range(3):
            self.scored_on(company_id, "2026-08-16")
        self.assertNotIn("fetch_persistently_failing", self.raised(company_id))

    def test_a_run_where_it_measured_resets_the_count(self) -> None:
        """Consecutive means consecutive: one successful fetch in the middle
        and the retry policy starts again."""
        company_id = self.company()
        self.scored_on(company_id, "2026-08-14")
        self.signal(company_id, "schema.product_present", num=0)
        self.scored_on(company_id, "2026-08-15")
        self.conn.execute(
            "DELETE FROM signal WHERE company_id = ? AND key = 'schema.product_present'",
            (company_id,),
        )
        self.scored_on(company_id, "2026-08-16")
        self.assertNotIn("fetch_persistently_failing", self.raised(company_id))


class TestStageScopedProfile(ScoreStageTestCase):
    """Migration 006. A signal a later run of the same stage stopped writing is
    superseded — otherwise every A7 guard is undone by the read model."""

    def test_a_later_run_of_the_same_stage_supersedes_by_omission(self) -> None:
        company_id = self.company()
        self.signal(company_id, "agency.footer_credit", text="Powered by JTL-Shop")
        later = self.add_run("extract-p1", "2026-08-16T00:00:00Z")
        self.signal(company_id, "platform.detected", text="JTL", run_id=later)
        credit = self.conn.execute(
            "SELECT agency_credit FROM company_profile WHERE company_id = ?",
            (company_id,),
        ).fetchone()[0]
        self.assertIsNone(credit, "a pre-fix observation must not be re-served")

    def test_another_stage_is_untouched(self) -> None:
        """A Phase-1 re-extract must not blank Phase-2 signals, or every
        re-score would discard what Phase 2 was paid for."""
        company_id = self.company()
        paid = self.add_run("extract-p2", "2026-08-15T01:00:00Z")
        self.signal(company_id, "ai.queries_checked", num=2, run_id=paid)
        self.signal(company_id, "ai.brand_mentions", num=0, run_id=paid)
        later = self.add_run("extract-p1", "2026-08-16T00:00:00Z")
        self.signal(company_id, "platform.detected", text="Shopify", run_id=later)
        row = self.conn.execute(
            "SELECT ai_queries_checked, ai_brand_mentions FROM company_profile "
            "WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        self.assertEqual((row[0], row[1]), (2, 0))

    def test_a_partial_run_does_not_blank_the_companies_it_skipped(self) -> None:
        """Scoped per company, not globally — otherwise `--resume` over three
        companies would supersede the other ten into nothing."""
        touched, skipped = self.company("a.de"), self.company("b.de")
        self.signal(touched, "platform.detected", text="Shopify")
        self.signal(skipped, "platform.detected", text="JTL")
        later = self.add_run("extract-p1", "2026-08-16T00:00:00Z")
        self.signal(touched, "platform.detected", text="Shopify", run_id=later)
        rows = dict(
            self.conn.execute(
                "SELECT domain, platform FROM company_profile ORDER BY domain"
            ).fetchall()
        )
        self.assertEqual(rows, {"a.de": "Shopify", "b.de": "JTL"})


class TestOnlyFinishedRunsAreAuthoritative(ScoreStageTestCase):
    """Migration 007. 006 read the latest run of a stage as authoritative,
    including by omission — which is right only if that run finished.

    Per §5 (D6) a crash-then-restart mints a **new** run_id, so a run that
    wrote 10 of 15 keys and died would otherwise become the latest, and the 5
    it never reached would read as retractions rather than as incompleteness.
    That inverts 006: instead of a stale value persisting, a live value
    vanishes. Nothing in the suite before this could reach the case, because
    every fixture run was finished by construction.
    """

    def written(self, company_id: int) -> tuple[str | None, str | None]:
        row = self.conn.execute(
            "SELECT platform, agency_credit FROM company_profile WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        return row["platform"], row["agency_credit"]

    def test_a_partial_run_does_not_retract_the_previous_runs_keys(self) -> None:
        company_id = self.company()
        self.signal(company_id, "platform.detected", text="Shopify")
        self.signal(company_id, "agency.footer_credit", text="Umsetzung: Agentur X")

        crashed = self.add_run("extract-p1", "2026-08-16T00:00:00Z", finished=False)
        self.signal(company_id, "platform.detected", text="Shopware", run_id=crashed)

        self.assertEqual(
            self.written(company_id),
            ("Shopify", "Umsetzung: Agentur X"),
            "an unfinished run must retract nothing, and supersede nothing",
        )

    def test_an_aborted_run_is_not_authoritative_either(self) -> None:
        """A run that stopped itself against a ceiling wrote a partial account
        of the corpus just as surely as one that crashed."""
        company_id = self.company()
        self.signal(company_id, "agency.footer_credit", text="Umsetzung: Agentur X")
        aborted = self.add_run("extract-p1", "2026-08-16T00:00:00Z")
        self.conn.execute(
            "UPDATE run SET aborted_reason = 'cost ceiling' WHERE id = ?", (aborted,)
        )
        self.signal(company_id, "platform.detected", text="Shopware", run_id=aborted)
        self.assertEqual(self.written(company_id), (None, "Umsetzung: Agentur X"))

    def test_the_partial_runs_own_keys_are_ignored_too(self) -> None:
        """Wholesale, not per key. Taking the crashed run's keys where it has
        them and falling back per key rebuilds the exact defect 006 closed: a
        key it would have declined to write, served from an older run that
        did."""
        company_id = self.company()
        self.signal(company_id, "agency.footer_credit", text="Powered by JTL-Shop")
        crashed = self.add_run("extract-p1", "2026-08-16T00:00:00Z", finished=False)
        self.signal(company_id, "platform.detected", text="JTL", run_id=crashed)
        self.assertEqual(self.written(company_id), (None, "Powered by JTL-Shop"))

    def test_the_restarted_run_supersedes_once_it_finishes(self) -> None:
        """Anti-vacuity: the narrowing must not have disabled 006. A complete
        run still retracts by omission — that is the whole mechanism."""
        company_id = self.company()
        self.signal(company_id, "agency.footer_credit", text="Powered by JTL-Shop")
        restarted = self.add_run("extract-p1", "2026-08-16T01:00:00Z")
        self.signal(company_id, "platform.detected", text="JTL", run_id=restarted)
        self.assertEqual(self.written(company_id), ("JTL", None))

    def test_score_marks_its_own_run_finished_only_on_success(self) -> None:
        """`finished_at` is now load-bearing, so the stages had to stop writing
        it from a `finally` — which marked a crashed run finished."""
        self.company()
        run_id, _ = score.run(self.conn, today=TODAY)
        row = self.conn.execute(
            "SELECT finished_at, aborted_reason FROM run WHERE id = ?", (run_id,)
        ).fetchone()
        self.assertIsNotNone(row["finished_at"])
        self.assertIsNone(row["aborted_reason"])

    def test_a_crashed_scoring_run_records_the_abort_and_no_finish(self) -> None:
        self.company()
        run_id = self.add_run("score-p1", "2026-08-16T02:00:00Z", finished=False)
        stage = score.ScoreStage(self.conn, run_id, 1, TODAY)
        rows = stage.profiles()
        with (
            unittest.mock.patch.object(
                score.ScoreStage, "run_company", side_effect=RuntimeError("boom")
            ),
            self.assertRaises(RuntimeError),
        ):
            score.run(self.conn, run_id=run_id, today=TODAY)
        self.assertTrue(rows)
        row = self.conn.execute(
            "SELECT finished_at, aborted_reason FROM run WHERE id = ?", (run_id,)
        ).fetchone()
        self.assertIsNone(row["finished_at"])
        self.assertIn("boom", row["aborted_reason"])


if __name__ == "__main__":
    unittest.main()
