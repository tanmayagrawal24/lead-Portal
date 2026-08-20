"""§7 control 2 — the rolling ceiling, and the gate that makes it unbypassable.

**What is under test is not "SUM works".** It is the four places this ledger is
the only thing between the tool and spend nobody authorised:

1. **The window boundary is a decision, not an accident.** §7 control 2 keys on
   `run.started_at` and nothing else, which is what makes it agree with B3.1: a
   batch submitted inside the window and reconciled outside it reconciles
   against its *submitting* run, so no money crosses the boundary at
   reconciliation time — and spend correspondingly **ages out** 30 days after
   its run started, reconciled or not (M1.70).
2. **`run` is summed alone.** Control 4 reserves a batch into `llm_batch`
   *and* `run`, so summing both counts every batch twice and halves the
   effective ceiling (M1.69).
3. **The gate cannot be forgotten.** A paid surface that loses its decorator, or
   a new callable nobody classified, must fail at **import** — before there is a
   caller, which is the whole reason this landed before M5 (M1.71).
4. **The gate cannot be talked around.** `clearance=None`, or an object that
   merely looks like a clearance, must be refused; only `check_ceiling`
   constructs the real thing.
"""

from __future__ import annotations

import itertools
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from portal import db, ledger, llm, llm_anthropic, migrate


class LedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = db.connect(Path(self._tmp.name) / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    #: `provider_batch_id` is UNIQUE, and these ids are otherwise identical
    #: whenever two batches carry the same estimate.
    _batch_seq = itertools.count(1)

    def add_run(
        self, *, days_ago: float, cost: float, stage: str = "extract_p2"
    ) -> int:
        """A run that started `days_ago` days ago, carrying `cost` on the ledger."""
        cur = self.conn.execute(
            "INSERT INTO run (started_at, stage, est_cost_usd) "
            "VALUES (datetime('now', ?), ?, ?)",
            (f"-{days_ago} days", stage, cost),
        )
        return int(cur.lastrowid)

    def add_batch(
        self, run_id: int, *, est: float, actual: float | None = None
    ) -> None:
        """A batch as §7 control 4 leaves it: reserved into `llm_batch` too.

        `reserved_at` is migration 014's, and it is separate from `submitted_at`
        because the two are separate moments — the window between them is where
        a crash costs the provider id.
        """
        self.conn.execute(
            "INSERT INTO llm_batch (provider_batch_id, run_id, purpose, "
            "request_count, est_cost_usd, actual_cost_usd, status, reserved_at, "
            "submitted_at, reconciled_at) "
            "VALUES (?,?,?,?,?,?,?,datetime('now'),datetime('now'),?)",
            (
                f"msgbatch_{run_id}_{next(LedgerTestCase._batch_seq)}",
                run_id,
                "impressum",
                100,
                est,
                actual,
                "reconciled" if actual is not None else "submitted",
                "2026-08-18T12:00:00Z" if actual is not None else None,
            ),
        )


class TheWindowBoundary(LedgerTestCase):
    """§7 control 2's 30 rolling days, keyed on `run.started_at` (M1.70)."""

    def test_a_run_inside_the_window_counts(self) -> None:
        self.add_run(days_ago=29, cost=10.0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 10.0)

    def test_a_run_outside_the_window_does_not(self) -> None:
        self.add_run(days_ago=31, cost=10.0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 0.0)

    def test_the_boundary_is_strict_and_sits_where_the_sql_says(self) -> None:
        """`started_at > datetime('now','-30 days')`, so 30 days is already out.

        Pinned rather than left implicit: an off-by-one here is a silent 1/30th
        of the ceiling, in whichever direction nobody checked.
        """
        for days, expected in ((29.99, 10.0), (30.01, 0.0)):
            with self.subTest(days_ago=days):
                self.conn.execute("DELETE FROM run")
                self.add_run(days_ago=days, cost=10.0)
                self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), expected)

    def test_spend_ages_out_and_that_is_what_rolling_means(self) -> None:
        self.add_run(days_ago=45, cost=1_000.0)
        self.add_run(days_ago=1, cost=5.0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 5.0)
        # $1,005 has been spent in this database and the guard clears the call.
        # That is correct for a runaway guard and wrong for an accounting
        # record, which is why §7 control 2 now says which one it is.
        ledger.check_ceiling(self.conn)


class ABatchThatCrossesTheBoundary(LedgerTestCase):
    """B3.1: `actual_cost_usd` reconciles against the **submitting** run."""

    def test_a_batch_reconciled_now_counts_against_its_submitting_run(self) -> None:
        """Submitted 3 days ago, reconciled today: still inside, once."""
        run_id = self.add_run(days_ago=3, cost=20.0)
        self.add_batch(run_id, est=20.0, actual=18.0)
        # The run's column is the ledger; reconciliation writes back into it
        # (control 3), so the reading is the run's, not the batch's.
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 20.0)

    def test_a_batch_whose_run_aged_out_is_not_pulled_back_in(self) -> None:
        """Submitted 40 days ago, reconciled today. It does **not** re-enter.

        This is the consequence §7 control 2 states rather than leaves to be
        discovered. The alternative — keying the window on `reconciled_at` —
        would contradict B3.1 by attributing a batch to a run that never
        reserved it.
        """
        run_id = self.add_run(days_ago=40, cost=20.0)
        self.add_batch(run_id, est=20.0, actual=18.0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 0.0)


class TheDoubleCountThatWasSpecified(LedgerTestCase):
    """M1.69. §10.4b said to sum both tables; control 4 puts a batch in both."""

    def test_a_batch_reserved_into_both_tables_is_counted_once(self) -> None:
        run_id = self.add_run(days_ago=1, cost=30.0)
        self.add_batch(run_id, est=30.0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 30.0)

        # What §10.4b asked for, computed here so the defect is a number and not
        # an argument: it is exactly double, and it would trip a $45 ceiling on
        # $22.50 of real spend — M1.23's failure one level down.
        both = self.conn.execute(
            "SELECT (SELECT COALESCE(SUM(est_cost_usd),0) FROM run"
            "        WHERE started_at > datetime('now','-30 days'))"
            "     + (SELECT COALESCE(SUM(est_cost_usd),0) FROM llm_batch)"
        ).fetchone()[0]
        self.assertAlmostEqual(both, 60.0)

    def test_the_ledger_survives_many_batches_under_one_run(self) -> None:
        run_id = self.add_run(days_ago=1, cost=12.0)
        for i in range(4):
            self.add_batch(run_id, est=3.0 + i * 0.0)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 12.0)


class TheCeilingItself(LedgerTestCase):
    def test_an_empty_ledger_clears_and_reports_full_headroom(self) -> None:
        clearance = ledger.check_ceiling(self.conn)
        self.assertIsInstance(clearance, ledger.LedgerClearance)
        self.assertAlmostEqual(clearance.spend_usd, 0.0)
        self.assertAlmostEqual(clearance.headroom_usd, ledger.MONTHLY_CEILING_USD)
        self.assertEqual(clearance.window_days, 30)

    def test_over_the_ceiling_refuses(self) -> None:
        self.add_run(days_ago=2, cost=ledger.MONTHLY_CEILING_USD + 0.01)
        with self.assertRaises(ledger.CeilingExceeded) as caught:
            ledger.check_ceiling(self.conn)
        # The reading is in the message: an abort that cannot say how much is an
        # abort its operator raises the ceiling to silence.
        self.assertIn("45.00", str(caught.exception))

    def test_exactly_at_the_ceiling_still_clears(self) -> None:
        self.add_run(days_ago=2, cost=ledger.MONTHLY_CEILING_USD)
        self.assertAlmostEqual(ledger.check_ceiling(self.conn).headroom_usd, 0.0)

    def test_an_unreadable_ledger_raises_rather_than_reading_as_zero(self) -> None:
        """Fails **closed**. An empty ledger and an unreadable one look alike,
        and treating the second as the first is how an unmeasured number gets to
        authorise spend (M1.52's argument, one layer out)."""
        empty = sqlite3.connect(":memory:")
        self.addCleanup(empty.close)
        with self.assertRaises(sqlite3.Error):
            ledger.check_ceiling(empty)


class TheImportTimeAssertion(unittest.TestCase):
    """M1.71. The checks must FIRE, or they are decoration."""

    @staticmethod
    def _module(**members: object) -> types.ModuleType:
        mod = types.ModuleType("fake_paid_module")
        for name, obj in members.items():
            if callable(obj):
                obj.__module__ = "fake_paid_module"
                obj.__qualname__ = name
            setattr(mod, name, obj)
        return mod

    def test_the_real_modules_pass(self) -> None:
        llm.assert_ledger_guarded(
            llm, paid=llm.PAID_SURFACES, free=llm.FREE_SURFACES, where="portal.llm"
        )
        llm_anthropic.llm.assert_ledger_guarded(
            llm_anthropic.AnthropicProvider,
            paid=llm_anthropic.PAID_SURFACES,
            free=llm_anthropic.FREE_SURFACES,
            where="provider",
        )

    def test_a_paid_surface_without_the_decorator_is_refused(self) -> None:
        def spend(**_: object) -> None: ...

        mod = self._module(spend=spend)
        with self.assertRaises(llm.LLMConfigError) as caught:
            llm.assert_ledger_guarded(mod, paid=("spend",), free=(), where="fake")
        self.assertIn("no §7 control 2 gate", str(caught.exception))

    def test_a_callable_classified_as_neither_is_refused(self) -> None:
        """The one that matters: the new paid path nobody declared."""

        def newly_added(**_: object) -> None: ...

        mod = self._module(newly_added=newly_added)
        with self.assertRaises(llm.LLMConfigError) as caught:
            llm.assert_ledger_guarded(mod, paid=(), free=(), where="fake")
        self.assertIn("neither paid nor free", str(caught.exception))

    def test_a_registered_name_that_does_not_exist_is_refused(self) -> None:
        mod = self._module()
        with self.assertRaises(llm.LLMConfigError) as caught:
            llm.assert_ledger_guarded(mod, paid=(), free=("deleted",), where="fake")
        self.assertIn("no such callable exists", str(caught.exception))

    def test_a_surface_listed_as_both_is_refused(self) -> None:
        def thing(**_: object) -> None: ...

        mod = self._module(thing=thing)
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_ledger_guarded(
                mod, paid=("thing",), free=("thing",), where="fake"
            )

    def test_imported_names_are_not_mistaken_for_undeclared_surfaces(self) -> None:
        """`_module_callables` filters on `__module__`, or every `import` in
        `llm.py` would read as an unclassified paid path and the assertion
        would be unusable — which is how a check gets deleted."""
        mod = types.ModuleType("borrower")
        mod.borrowed = ledger.check_ceiling  # defined elsewhere
        llm.assert_ledger_guarded(mod, paid=(), free=(), where="fake")


class TheGateAtTheCallSite(LedgerTestCase):
    def _requests(self) -> list[llm.BatchRequest]:
        return [llm.BatchRequest("a", "sys", "page", {}, 2048)]

    def test_reserve_batch_without_a_clearance_is_refused_by_name(self) -> None:
        """The wrapper runs before Python binds the signature, so an omitted
        clearance raises `LedgerBypass` and not the bare `TypeError` the
        keyword-only parameter would give. That is the better error — it names
        the control and says what to call — and it is pinned here because it is
        the message whoever writes M5 will actually read."""
        with self.assertRaises(ledger.LedgerBypass) as caught:
            llm.reserve_batch(
                self._requests(),
                provider="anthropic",
                model="claude-haiku-4-5",
                count_tokens=lambda **_: 10,
            )
        self.assertIn("§7 control 2", str(caught.exception))

    def test_reserve_batch_with_none_is_refused(self) -> None:
        with self.assertRaises(ledger.LedgerBypass):
            llm.reserve_batch(
                self._requests(),
                provider="anthropic",
                model="claude-haiku-4-5",
                count_tokens=lambda **_: 10,
                clearance=None,
            )

    def test_a_look_alike_clearance_is_refused(self) -> None:
        """Duck typing is the obvious way past this gate, so it is closed."""

        class NotAClearance:
            spend_usd = 0.0
            ceiling_usd = 45.0
            window_days = 30
            taken_at = "2026-08-18T00:00:00Z"

        with self.assertRaises(ledger.LedgerBypass):
            llm.reserve_batch(
                self._requests(),
                provider="anthropic",
                model="claude-haiku-4-5",
                count_tokens=lambda **_: 10,
                clearance=NotAClearance(),
            )

    def test_a_real_clearance_lets_the_reservation_through(self) -> None:
        est = llm.reserve_batch(
            self._requests(),
            provider="anthropic",
            model="claude-haiku-4-5",
            count_tokens=lambda **_: 10_000,
            clearance=ledger.check_ceiling(self.conn),
        )
        self.assertEqual(est.input_tokens, 10_000)

    def test_submit_batch_is_gated_too_and_not_only_the_reservation(self) -> None:
        """`reserve_batch` spends nothing — it is arithmetic. `submit_batch` is
        the irrevocable call, so gating only the first would be decorative."""
        provider = llm_anthropic.AnthropicProvider(client=object())
        with self.assertRaises(ledger.LedgerBypass):
            provider.submit_batch(self._requests(), clearance=None)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
