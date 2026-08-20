"""§5.4's bound, once Phase 2 can answer (M1.82) — and §6.1's three states.

The property under test is that the gate can **tighten**. Without it the term
is decorative: `remaining_upside` already drops a banked rule, so a gate that
never tightens still looks like it is doing arithmetic.
"""

from __future__ import annotations

import unittest
from datetime import date

from portal import ruleset, score
from portal.ruleset import Rule, RulesetError, declines

TODAY = date(2026, 8, 20)


def run(**profile) -> score.ScoreResult:
    return score.evaluate({"company_id": 1, "domain": "muster.de", **profile}, TODAY)


def component(result: score.ScoreResult, rule_id: str) -> score.Component | None:
    return next((c for c in result.components if c.rule_id == rule_id), None)


class TestAssertDeclaredBothWays(unittest.TestCase):
    """`assert_declared` REQUIRES the declaration on a Phase-2-reachable rule
    and REFUSES it on every other. Both directions, because each fails
    differently: a missing one silently inflates the bound, a spurious one
    asserts something about a phase that cannot touch the rule."""

    def test_the_shipped_ruleset_declares_it_exactly_where_it_belongs(self) -> None:
        declared = {r.id for r in ruleset.RULES if r.phase2_input_settled is not None}
        reachable = {r.id for r in ruleset.RULES if r.phase2_reachable}
        self.assertEqual(declared, reachable)

    def test_a_phase2_rule_without_the_declaration_is_refused(self) -> None:
        """**The pre-fix gap, pinned.** Before M1.82 this rule was accepted:
        `assert_declared` had nothing to say about whether a Phase-2-reachable
        rule could report that its input was already answered."""
        undeclared = Rule(
            "qual.invented", 12, "6.1", ("own_brand",), True, lambda p, t: declines()
        )
        with self.assertRaises(RulesetError) as caught:
            ruleset.assert_declared(ruleset.RULES + (undeclared,))
        self.assertIn("phase2_input_settled", str(caught.exception))

    def test_a_phase1_rule_with_the_declaration_is_refused(self) -> None:
        spurious = Rule(
            "qual.invented",
            12,
            "6.1",
            ("platform",),
            False,
            lambda p, t: declines(),
            lambda p: True,
        )
        with self.assertRaises(RulesetError) as caught:
            ruleset.assert_declared(ruleset.RULES + (spurious,))
        self.assertIn("not phase2_reachable", str(caught.exception))

    def test_it_is_not_derived_from_outcomes(self) -> None:
        """The reason it has to be declared at all. A rule that DECLINES writes
        no component, so a Phase-2 `false` — the commonest answer, and the one
        that should tighten the bound — leaves no trace in the score for an
        outcome-based reading to find."""
        result = run(own_brand=0, homepage_extracted=1)
        self.assertIsNone(component(result, "qual.own_brand"))
        self.assertTrue(
            ruleset.RULES[
                [r.id for r in ruleset.RULES].index("qual.own_brand")
            ].phase2_input_settled({"homepage_extracted": 1})
        )


class TestTheGateCanTighten(unittest.TestCase):
    def test_a_settled_negative_answer_removes_its_points_from_the_bound(self) -> None:
        """The measurement this was built on, on §5.4's own worked example.

        `germanelectronic.de` scores 15 in Phase 1 and carries 50 of upside, so
        it is admitted at exactly the B floor. Once its extraction has run and
        answered **no** to both booleans, those 25 points do not exist — and
        before M1.82 the bound went on offering them and re-admitting the
        company on two closed questions.
        """
        before = run(platform="JTL")
        self.assertEqual((before.remaining_upside, before.admitted), (50, True))

        after = run(
            platform="JTL",
            own_brand=0,
            owner_named=0,
            homepage_extracted=1,
            impressum_extracted=1,
        )
        self.assertEqual(after.total, before.total)
        self.assertEqual(after.remaining_upside, 25)
        self.assertFalse(after.admitted)

    def test_a_half_read_company_keeps_the_owner_operated_upside(self) -> None:
        """`qual.owner_operated`'s two Phase-2 disjuncts read different pages.
        One page alone leaves a disjunct that could still award the +15, so the
        rule is not settled and its points stay in the bound."""
        half = run(platform="JTL", homepage_extracted=1, own_brand=0)
        self.assertEqual(half.remaining_upside, 40)  # 50 − own_brand's 10

    def test_a_banked_rule_and_a_settled_one_do_not_double_subtract(self) -> None:
        settled_and_banked = run(
            legal_form="e.K.", homepage_extracted=1, impressum_extracted=1, own_brand=0
        )
        self.assertEqual(settled_and_banked.remaining_upside, 25)

    def test_the_term_is_inert_with_no_phase_2_writer(self) -> None:
        """Nothing writes `llm.*_extracted` yet, so the bound today is what it
        was — which is what makes this safe to land before the writer."""
        self.assertEqual(run(platform="JTL").remaining_upside, 50)


class TestThreeStates(unittest.TestCase):
    """*Not run* declines, *ran and answered* fires or declines, *ran and could
    not tell* abstains (M1.81)."""

    def test_own_brand_declines_before_phase_2_has_run(self) -> None:
        """Abstaining here would put "Phase 2 has not run yet" in the review
        queue for every company in the corpus."""
        self.assertIsNone(component(run(), "qual.own_brand"))

    def test_own_brand_fires_on_a_written_true(self) -> None:
        result = run(homepage_extracted=1, own_brand=1)
        awarded = component(result, "qual.own_brand")
        self.assertIsNotNone(awarded)
        self.assertEqual(awarded.points, 10)

    def test_own_brand_declines_on_a_written_false(self) -> None:
        self.assertIsNone(
            component(run(homepage_extracted=1, own_brand=0), "qual.own_brand")
        )

    def test_own_brand_abstains_when_the_page_was_read_and_did_not_say(self) -> None:
        result = run(homepage_extracted=1)
        abstention = component(result, "qual.own_brand")
        self.assertIsNotNone(abstention)
        self.assertEqual(abstention.points, 0)
        self.assertEqual(abstention.state, ruleset.ABSTAINS)
        # Membership, not the whole list: a bare profile also abstains the blog
        # ladder, and pinning the full set would make this test about §6.2.
        self.assertIn("own_brand_undetermined", [f.reason for f in result.review_flags])

    def test_owner_operated_abstains_only_on_disjunct_3(self) -> None:
        result = run(homepage_extracted=1, impressum_extracted=1)
        raised = [f.reason for f in result.review_flags]
        self.assertIn("owner_named_undetermined", raised)
        self.assertIn("own_brand_undetermined", raised)

    def test_a_company_that_already_won_the_rule_raises_no_flag(self) -> None:
        """A queue item with no action behind it is exactly what §6.4's
        stickiness argument is protecting against."""
        result = run(legal_form="e.K.", homepage_extracted=1, impressum_extracted=1)
        self.assertNotIn(
            "owner_named_undetermined", [f.reason for f in result.review_flags]
        )

    def test_neither_abstention_blocks_outbound_contact(self) -> None:
        """Both withhold an AWARD, so the lead reads too low — a ranking delay
        the queue repairs, not a phone call to a company on a score the tool
        cannot support. Migration 013 adds no `contact_blocking_reason` row, and
        this is the assertion that says that was a decision."""
        import tempfile
        from pathlib import Path

        from portal import db, migrate

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = db.connect(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        migrate.apply_pending(conn)
        blocking = {
            r[0] for r in conn.execute("SELECT reason FROM contact_blocking_reason")
        }
        self.assertNotIn("own_brand_undetermined", blocking)
        self.assertNotIn("owner_named_undetermined", blocking)
        # …and the schema does accept them as flags, which is the other half.
        for reason in ("own_brand_undetermined", "owner_named_undetermined"):
            with self.subTest(reason=reason):
                conn.execute(
                    "INSERT INTO company (domain, discovery_source, discovered_at) "
                    "VALUES (?,'seed_csv','2026-08-20T00:00:00Z')",
                    (f"{reason}.de",),
                )
                company_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                run_id = conn.execute(
                    "INSERT INTO run (started_at, stage) "
                    "VALUES ('2026-08-20T00:00:00Z','score_p1')"
                ).lastrowid
                conn.execute(
                    "INSERT INTO review_flag (company_id, reason, raised_run_id, "
                    "raised_at) VALUES (?,?,?,'2026-08-20T00:00:00Z')",
                    (company_id, reason, run_id),
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT contact_blocked FROM company WHERE id = ?",
                        (company_id,),
                    ).fetchone()[0],
                    0,
                )


class TestTheDemotedHintHasNoReader(unittest.TestCase):
    """A3/M1.77. The platform-vocabulary exclusion cannot be enforced inside a
    prompt, so the guard is that nothing reads the key."""

    UNSCORED_HINTS = ("agency.footer_credit_llm", "content.blog_lastmod_hint")

    def test_no_rule_reads_an_unscored_hint(self) -> None:
        read = {name for rule in ruleset.RULES for name in rule.reads}
        for hint in self.UNSCORED_HINTS:
            with self.subTest(hint=hint):
                self.assertNotIn(hint, read)
                self.assertNotIn(hint.split(".", 1)[1], read)


if __name__ == "__main__":
    unittest.main()
