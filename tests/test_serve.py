"""`portal serve` — the §9 page, driven through the app.

These tests exist because the page is the first and only way to read three
things the pipeline has been writing for three days and showing nobody: an
abstention, a review flag's note, and a contact block. §5.4's narrowed safety
claim (M1.41) is *nothing recoverable is discarded without a human being told*,
and it holds only if the queue can be read — so "the page renders" is not the
property under test. The properties are that it renders the states that would
otherwise be invisible, and that it does not flatten them into each other.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from selectolax.parser import HTMLParser

from portal import db, leadlist, migrate, score, serve

TODAY = date(2026, 8, 15)


class ServeTestCase(unittest.TestCase):
    """A database with one fetch run, one scoring run, and artifacts on disk."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "portal.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.run_id = int(
            self.conn.execute(
                "INSERT INTO run (started_at, finished_at, stage, companies_seen) "
                "VALUES ('2026-08-15T00:00:00Z','2026-08-15T00:01:00Z','extract-p1',1)"
            ).lastrowid
        )

    # ── fixture builders ────────────────────────────────────────────────

    def company(self, domain: str = "muster.de", **columns) -> int:
        keys = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)
        return int(
            self.conn.execute(
                f"INSERT INTO company (domain, discovery_source, discovered_at"
                f"{', ' + keys if keys else ''}) "
                f"VALUES (?, 'seed_csv', '2026-08-15T00:00:00Z'"
                f"{', ' + marks if marks else ''})",
                (domain, *columns.values()),
            ).lastrowid
        )

    def artifact(self, company_id: int, kind: str, url: str, body: str) -> int:
        name = f"{kind}-{abs(hash(url)) % 10**9}.html"
        (self.artifacts / name).write_text(body, encoding="utf-8")
        return int(
            self.conn.execute(
                "INSERT INTO artifact (company_id, kind, url, http_status, content_hash, "
                "body_path, bytes, fetched_at, last_checked_at) "
                "VALUES (?,?,?,200,?,?,?,'2026-08-15T00:00:00Z','2026-08-15T00:00:00Z')",
                (company_id, kind, url, f"h{abs(hash(url)) % 10**9}", name, len(body)),
            ).lastrowid
        )

    def signal(
        self,
        company_id: int,
        key: str,
        *,
        num=None,
        text=None,
        day=None,
        artifact_id: int | None = None,
        url: str = "https://muster.de/",
        method: str = "deterministic",
        confidence: float | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, value_text, "
            "value_date, method, confidence, evidence_url, artifact_id, observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'2026-08-15T00:00:00Z')",
            (
                company_id,
                self.run_id,
                key,
                num,
                text,
                day,
                method,
                confidence,
                url,
                artifact_id,
            ),
        )

    def score_now(self) -> int:
        run_id, _ = score.run(self.conn, today=TODAY)
        return run_id

    def client(self) -> TestClient:
        app = serve.create_app(self.db_path, self.artifacts)
        return TestClient(app)

    def page(self, path: str = "/", **params) -> HTMLParser:
        """Parsed. `/rows` returns bare `<tr>` elements, which a parser drops
        unless they are inside a table — htmx puts them in one, so the test
        does too."""
        response = self.client().get(path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        html = response.text
        if path == "/rows":
            html = f"<table><tbody>{html}</tbody></table>"
        return HTMLParser(html)

    # ── a shop with everything worth rendering ──────────────────────────

    def full_company(self) -> int:
        """Shopify shop, a stale blog, and an abstention that blocks contact.

        `neg.active_content` abstains where the index dates fewer posts than it
        lists (migration 008), which is the one abstention that leaves the score
        too **high** — so this fixture carries a real block rather than a
        simulated one.
        """
        company_id = self.company("muster.de", city="Köln", country="DE")
        homepage = self.artifact(
            company_id, "homepage", "https://muster.de/", "<html>Startseite</html>"
        )
        index = self.artifact(
            company_id, "blog_index", "https://muster.de/blog", "<html>Blog</html>"
        )
        self.signal(
            company_id, "platform.detected", text="Shopify", artifact_id=homepage
        )
        self.signal(company_id, "content.blog_exists", num=1, artifact_id=index)
        self.signal(
            company_id,
            "content.blog_last_post",
            day="2023-03-01",
            artifact_id=index,
            url="https://muster.de/blog",
        )
        self.signal(
            company_id, "content.blog_last_post_basis", text="index", artifact_id=index
        )
        self.signal(company_id, "content.blog_post_count", num=20, artifact_id=index)
        # Two dated posts against twenty listed: cadence unmeasurable, which is
        # the too-high abstention and therefore the contact block.
        self.signal(
            company_id,
            "content.blog_post_dates",
            num=2,
            text="2023-03-01,2023-01-04",
            artifact_id=index,
        )
        self.score_now()
        return company_id


class TestTheTable(ServeTestCase):
    def test_the_ranked_table_renders_every_company(self) -> None:
        self.full_company()
        self.company("zweite.de")
        tree = self.page()
        rows = tree.css("tr.summary")
        self.assertEqual(len(rows), 2)
        # Every row has as many cells as the header has columns; a table that
        # silently drops a cell misaligns every column to its right.
        self.assertEqual(
            {len(row.css("td")) for row in rows}, {len(tree.css("thead th"))}
        )

    def test_an_unscored_company_is_shown_as_unscored_not_as_a_zero(self) -> None:
        """A company `score` never reached is the row an operator most needs to
        see, and a `0` in the score column is a claim nobody made."""
        self.company("ungescored.de")
        tree = self.page()
        row = tree.css_first("tr.summary")
        self.assertIn("band-none", row.html)
        self.assertNotIn(">0<", row.css("td")[3].html)

    def test_the_band_and_the_gate_decision_are_both_shown(self) -> None:
        self.full_company()
        row = self.page().css_first("tr.summary")
        self.assertTrue(row.css_first("span.band"))
        gate = row.css("td")[4].text().strip()
        self.assertIn(gate, ("P2", "Stopp"))


class TestAbstentions(ServeTestCase):
    """The requirement with the sharpest edge. A rule that declined to fire is
    the difference between "no blog" and "we could not tell", and the two must
    not look the same — as an absent row, or as a zero."""

    def _detail(self, company_id: int) -> HTMLParser:
        response = self.client().get(f"/company/{company_id}/detail")
        self.assertEqual(response.status_code, 200)
        return HTMLParser(response.text)

    def test_an_abstention_is_rendered_as_an_abstention(self) -> None:
        company_id = self.full_company()
        tree = self._detail(company_id)
        abstained = tree.css("li.abstained")
        self.assertTrue(abstained, "no abstention rendered")
        for item in abstained:
            points = item.css_first(".pts").text().strip()
            self.assertEqual(points, "Enthaltung")
            self.assertNotEqual(points, "0")
            self.assertIn("nicht bewertbar", item.text())

    def test_an_abstention_is_not_an_absent_row(self) -> None:
        """It appears in the component list with its reason, not merely as a
        count somewhere — the reason is what tells a person what to go and do."""
        company_id = self.full_company()
        tree = self._detail(company_id)
        rules = [
            item.css_first("code").text() for item in tree.css("ul.components > li")
        ]
        self.assertIn("neg.active_content", rules)
        abstention = next(
            item
            for item in tree.css("ul.components > li")
            if item.css_first("code").text() == "neg.active_content"
        )
        self.assertTrue(abstention.css_first("p.reason").text().strip())

    def test_the_row_advertises_its_abstentions(self) -> None:
        self.full_company()
        row = self.page().css_first("tr.summary")
        self.assertIn("Enthaltung", row.css("td")[-1].text())


class TestEvidence(ServeTestCase):
    def test_every_component_links_to_the_artifact_behind_it(self) -> None:
        company_id = self.full_company()
        tree = HTMLParser(self.client().get(f"/company/{company_id}/detail").text)
        components = tree.css("ul.components > li")
        self.assertTrue(components)
        linked = [item for item in components if item.css('a[href^="/artifact/"]')]
        self.assertTrue(linked, "no component carries an evidence link")
        # Every link resolves to a stored body, not a 404 — the M1.42 property,
        # end to end through the UI.
        client = self.client()
        for item in linked:
            for anchor in item.css('a[href^="/artifact/"]'):
                response = client.get(anchor.attributes["href"])
                self.assertEqual(response.status_code, 200, anchor.attributes["href"])
                self.assertIn("# artifact", response.text)

    def test_the_artifact_route_serves_the_stored_bytes_as_text(self) -> None:
        """Not as HTML. The operator is checking a citation against what was
        scored, and rendering a third party's page would run their scripts on
        this origin and show something other than the bytes we parsed."""
        company_id = self.company()
        artifact_id = self.artifact(
            company_id,
            "homepage",
            "https://muster.de/",
            "<h1>Hallo</h1><script>x</script>",
        )
        response = self.client().get(f"/artifact/{artifact_id}")
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("<h1>Hallo</h1>", response.text)
        self.assertIn("https://muster.de/", response.text)

    def test_a_missing_artifact_is_a_404_not_a_blank_page(self) -> None:
        self.assertEqual(self.client().get("/artifact/9999").status_code, 404)


class TestLlmMarking(ServeTestCase):
    """§9: LLM-derived fields marked, `confidence = 0` in red.

    **These tests were rewritten in 9a, and the reason is a finding (M1.85).**
    They used to give `platform.detected` — a deterministic Phase-1 key — a
    synthetic `method='llm', confidence=0`, because when they were written no
    writer produced an LLM signal at all and any key would do. Migration 012's
    confidence filter turned that combination into a value the read model does
    not serve, so the rule declined, no `score_component` was written, and there
    was no component for the red evidence to render *under*. The test went red
    on a behaviour change it was not about.

    That exposed the real dependency, which the placeholder had hidden: **§9
    renders evidence beneath components, so a rejected value is only visible if
    its rule still produces one.** For every §5.5b key that any rule reads, it
    does — the three-state predicates abstain, and an abstention *is* a
    component worth 0 points (M1.81). The visibility A4's direction-of-error
    argument rests on therefore holds **through the abstention**, not
    independently of it, and these tests now exercise that path on a key that
    will really carry `confidence = 0`.
    """

    def _own_brand_company(self, **signal_args) -> HTMLParser:
        """A company whose homepage extraction ran, on `brand.own_brand`."""
        company_id = self.company()
        homepage = self.artifact(
            company_id, "homepage", "https://muster.de/", "<html></html>"
        )
        self.signal(
            company_id,
            "llm.homepage_extracted",
            num=1,
            artifact_id=homepage,
            method="llm",
            confidence=1.0,
        )
        self.signal(
            company_id,
            "brand.own_brand",
            num=1,
            artifact_id=homepage,
            **signal_args,
        )
        self.score_now()
        return HTMLParser(self.client().get(f"/company/{company_id}/detail").text)

    def _company_with(self, **signal_args) -> HTMLParser:
        company_id = self.company()
        homepage = self.artifact(
            company_id, "homepage", "https://muster.de/", "<html></html>"
        )
        self.signal(
            company_id,
            "platform.detected",
            text="Shopify",
            artifact_id=homepage,
            **signal_args,
        )
        self.score_now()
        return HTMLParser(self.client().get(f"/company/{company_id}/detail").text)

    def test_a_deterministic_signal_is_neither_marked_nor_red(self) -> None:
        tree = self._company_with()
        self.assertEqual(len(tree.css(".v.llm")), 0)
        self.assertEqual(len(tree.css(".unverified")), 0)

    def test_an_llm_signal_is_marked(self) -> None:
        tree = self._own_brand_company(method="llm", confidence=0.9)
        self.assertTrue(tree.css(".v.llm"))
        self.assertEqual(len(tree.css(".unverified")), 0)

    def test_confidence_zero_is_red_under_the_abstention_it_causes(self) -> None:
        """The rejected value is not scored (migration 012) **and is still on
        the page** — under `qual.own_brand`'s abstention, which is what carries
        it there. Both halves matter: a value the tool rejected must not score,
        and a rejection nobody can see is a silence."""
        tree = self._own_brand_company(method="llm", confidence=0.0)
        self.assertTrue(tree.css(".v.unverified"))
        self.assertIn("nicht verifiziert", tree.text())
        self.assertIn("Nicht bewertbar", tree.text())

    def test_a_rejected_value_scores_nothing(self) -> None:
        tree = self._own_brand_company(method="llm", confidence=0.0)
        self.assertNotIn("+10", tree.text())


class TestContactBlock(ServeTestCase):
    """A block a human cannot see is a block that gets worked around by hand."""

    def test_the_block_is_visible_on_the_row(self) -> None:
        self.full_company()
        row = self.page().css_first("tr.summary")
        self.assertIn("Kontakt gesperrt", row.text())

    def test_the_block_is_explained_with_the_reason_that_causes_it(self) -> None:
        company_id = self.full_company()
        tree = HTMLParser(self.client().get(f"/company/{company_id}/detail").text)
        box = tree.css_first("div.blockbox")
        self.assertIsNotNone(box, "contact block rendered without an explanation")
        text = box.text()
        self.assertIn("outreach", text)
        # The rationale comes out of `contact_blocking_reason`, which migration
        # 008 made data precisely so the next reason is an INSERT. A UI that
        # restated it here would put the branch back.
        rationale = self.conn.execute(
            "SELECT rationale FROM contact_blocking_reason "
            "WHERE reason = 'blog_cadence_unmeasurable'"
        ).fetchone()[0]
        self.assertIn(rationale.split(":")[0], text)

    def test_the_database_agrees_with_the_page(self) -> None:
        """Anti-vacuity: the badge tracks `company.contact_blocked`, which the
        trigger maintains, rather than a second opinion computed here."""
        company_id = self.full_company()
        blocked = self.conn.execute(
            "SELECT contact_blocked FROM company WHERE id = ?", (company_id,)
        ).fetchone()[0]
        self.assertEqual(blocked, 1)


class TestFlagResolution(ServeTestCase):
    def _flags(self, company_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM review_flag WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()

    def test_resolving_writes_the_three_columns_ss9_names(self) -> None:
        company_id = self.full_company()
        flag = self._flags(company_id)[0]
        response = self.client().post(
            f"/company/{company_id}/flag/{flag['id']}/resolve",
            data={"note": "Blog aufgerufen, ein Beitrag pro Monat."},
        )
        self.assertEqual(response.status_code, 200)
        after = self.conn.execute(
            "SELECT * FROM review_flag WHERE id = ?", (flag["id"],)
        ).fetchone()
        self.assertIsNotNone(after["resolved_at"])
        self.assertEqual(after["resolved_by_human"], 1)
        self.assertEqual(
            after["resolved_note"], "Blog aufgerufen, ein Beitrag pro Monat."
        )

    def test_each_flag_clears_independently(self) -> None:
        """§6.4 made these distinct reasons because they send a person to
        different pages. Clearing one must not clear the others."""
        company_id = self.full_company()
        self.conn.execute(
            "INSERT INTO review_flag (company_id, reason, raised_run_id, raised_at) "
            "VALUES (?, 'no_impressum', ?, '2026-08-15T00:00:00Z')",
            (company_id, self.run_id),
        )
        self.conn.commit()
        flags = self._flags(company_id)
        self.assertGreaterEqual(len(flags), 2)

        self.client().post(
            f"/company/{company_id}/flag/{flags[0]['id']}/resolve", data={"note": ""}
        )
        still_open = self.conn.execute(
            "SELECT COUNT(*) FROM review_flag WHERE company_id = ? AND resolved_at IS NULL",
            (company_id,),
        ).fetchone()[0]
        self.assertEqual(still_open, len(flags) - 1)

    def test_clearing_the_blocking_flag_lifts_the_block_in_the_same_response(
        self,
    ) -> None:
        """The out-of-band swap. A block that has lifted must stop showing as
        one now, not on the next reload — otherwise the operator's next action
        is taken against a stale badge."""
        company_id = self.full_company()
        blocking = self.conn.execute(
            "SELECT f.id FROM review_flag f JOIN contact_blocking_reason b "
            "ON b.reason = f.reason WHERE f.company_id = ?",
            (company_id,),
        ).fetchone()
        self.assertIsNotNone(blocking)

        response = self.client().post(
            f"/company/{company_id}/flag/{blocking['id']}/resolve",
            data={"note": "geprüft"},
        )
        self.assertNotIn("Kontaktaufnahme gesperrt", response.text)
        self.assertIn('hx-swap-oob="true"', response.text)
        self.assertEqual(
            self.conn.execute(
                "SELECT contact_blocked FROM company WHERE id = ?", (company_id,)
            ).fetchone()[0],
            0,
        )

    def test_the_lifted_block_lets_outreach_through(self) -> None:
        """The block's whole point is the `outreach` trigger. Asserting the badge
        without asserting the trigger would test the paint, not the door."""
        company_id = self.full_company()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO outreach (company_id, channel, occurred_at) "
                "VALUES (?, 'phone', '2026-08-16T10:00:00Z')",
                (company_id,),
            )
        for flag in self._flags(company_id):
            self.client().post(
                f"/company/{company_id}/flag/{flag['id']}/resolve", data={"note": "ok"}
            )
        self.conn.execute(
            "INSERT INTO outreach (company_id, channel, occurred_at) "
            "VALUES (?, 'phone', '2026-08-16T10:00:00Z')",
            (company_id,),
        )

    def test_resolving_twice_does_not_move_the_timestamp(self) -> None:
        """`resolved_at` records when a person actually looked. A second write
        would overwrite that with when they clicked again."""
        company_id = self.full_company()
        flag = self._flags(company_id)[0]
        client = self.client()
        client.post(
            f"/company/{company_id}/flag/{flag['id']}/resolve", data={"note": "erste"}
        )
        first = self.conn.execute(
            "SELECT resolved_at, resolved_note FROM review_flag WHERE id = ?",
            (flag["id"],),
        ).fetchone()
        client.post(
            f"/company/{company_id}/flag/{flag['id']}/resolve", data={"note": "zweite"}
        )
        second = self.conn.execute(
            "SELECT resolved_at, resolved_note FROM review_flag WHERE id = ?",
            (flag["id"],),
        ).fetchone()
        self.assertEqual(tuple(first), tuple(second))


class TestFilters(ServeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.blocked = self.full_company()
        self.plain = self.company("plain.de", city="Wien", country="AT")
        homepage = self.artifact(
            self.plain, "homepage", "https://plain.de/", "<html></html>"
        )
        self.signal(
            self.plain,
            "platform.detected",
            text="JTL",
            artifact_id=homepage,
            url="https://plain.de/",
        )
        self.excluded = self.company(
            "raus.de", excluded=1, excluded_reason="duplicate_site"
        )
        self.score_now()

    def _needing_review(self) -> set[str]:
        return {
            row[0]
            for row in self.conn.execute(
                "SELECT domain FROM company WHERE needs_review = 1"
            )
        }

    def domains(self, **params) -> set[str]:
        tree = self.page("/rows", **params)
        return {row.css("td")[1].text().strip() for row in tree.css("tr.summary")}

    def test_unfiltered_shows_everything_including_excluded(self) -> None:
        """Hiding excluded rows by default would make a hard exclusion
        invisible, and §6.4 requires that we never exclude silently."""
        self.assertEqual(self.domains(), {"muster.de", "plain.de", "raus.de"})

    def test_each_filter_narrows(self) -> None:
        cases = {
            "band": ("C", None),
            "platform": ("JTL", {"plain.de"}),
            "country": ("AT", {"plain.de"}),
            "excluded": ("yes", {"raus.de"}),
            # Both scored companies carry a flag: `plain.de` has no blog signal
            # at all, so `opp.no_blog` abstains and raises `blog_undetectable`.
            # Taken from the database rather than typed, so the case cannot pass
            # by agreeing with a stale guess.
            "needs_review": ("yes", self._needing_review()),
            "contact_blocked": ("yes", {"muster.de"}),
        }
        for name, (value, expected) in cases.items():
            with self.subTest(filter=name):
                got = self.domains(**{name: value})
                self.assertLess(len(got), 3, f"{name} did not narrow")
                if expected is not None:
                    self.assertEqual(got, expected)

    def test_filters_compose(self) -> None:
        self.assertEqual(self.domains(country="AT", excluded="no"), {"plain.de"})
        self.assertEqual(self.domains(country="AT", excluded="yes"), set())

    def test_the_negative_side_of_every_boolean_filter_works(self) -> None:
        self.assertNotIn("raus.de", self.domains(excluded="no"))
        self.assertNotIn("muster.de", self.domains(contact_blocked="no"))
        self.assertNotIn("muster.de", self.domains(needs_review="no"))


class TestEvidenceReachability(unittest.TestCase):
    """`assert_evidence_reachable` — the startup check, in the same spirit as
    `ruleset.assert_declared`. A rule whose inputs the UI cannot trace renders a
    component with no link, which looks exactly like a rule that reads nothing.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = db.connect(Path(tmp.name) / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def test_the_live_ruleset_is_fully_reachable(self) -> None:
        leadlist.assert_evidence_reachable(self.conn)

    def test_the_mapping_is_read_off_the_view_not_hardcoded(self) -> None:
        mapping = leadlist.evidence_keys(self.conn)
        self.assertEqual(mapping["platform"], ("platform.detected",))
        self.assertEqual(
            mapping["blog_search_limit"], ("content.blog_search_exhaustive",)
        )

    def test_an_untraceable_rule_input_raises(self) -> None:
        from portal.ruleset import Rule, fires

        rogue = Rule(
            id="opp.erfunden",
            points=5,
            section="6.2",
            reads=("kein_solches_feld",),
            phase2_reachable=False,
            evaluate=lambda profile, today: fires("—"),
        )
        with (
            unittest.mock.patch.object(leadlist, "RULES", (rogue,)),
            self.assertRaises(RuntimeError) as caught,
        ):
            leadlist.assert_evidence_reachable(self.conn)
        self.assertIn("kein_solches_feld", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
