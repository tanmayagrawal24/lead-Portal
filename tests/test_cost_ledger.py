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

from portal import db, extract_p2, ledger, llm, llm_anthropic, migrate


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


class ThePerRunCeiling(LedgerTestCase):
    """§7 control 3 — the control §7 says should bite occasionally (M1.101).

    **Reproduced before it was built.** On `95d3281`, one run charged $2.00 six
    times through the real reservation write reached `run.est_cost_usd = 12.00`
    against a stated per-run ceiling of `$5.00`, and `check_ceiling` cleared it
    — correctly, because control 2 is the outer bound and $12 is well under $45.
    `ledger.MONTHLY_CEILING_USD` was the only ceiling constant in the tree.
    """

    def clearance(self) -> ledger.LedgerClearance:
        """A real one. `check_ceiling` is the only thing that builds these."""
        return ledger.check_ceiling(self.conn)

    def test_a_run_is_refused_at_the_per_run_ceiling(self) -> None:
        run_id = self.add_run(days_ago=0, cost=0.0)
        cl = self.clearance()

        # Four charges of $1.20 → $4.80, all under $5.00.
        for _ in range(4):
            ledger.charge_run(self.conn, run_id=run_id, usd=1.20, clearance=cl)
        self.assertAlmostEqual(ledger.run_reserved_usd(self.conn, run_id), 4.80)

        # The fifth would reach $6.00 and must be refused.
        with self.assertRaises(ledger.RunCeilingExceeded) as caught:
            ledger.charge_run(self.conn, run_id=run_id, usd=1.20, clearance=cl)

        message = str(caught.exception)
        self.assertIn("§7 control 3", message)
        self.assertIn("6.0000", message)
        self.assertIn("$5.00", message)

        # And it refused *before* writing: the accumulator has not moved.
        self.assertAlmostEqual(ledger.run_reserved_usd(self.conn, run_id), 4.80)

    def test_the_check_is_on_the_total_not_the_increment(self) -> None:
        """A single call larger than the whole ceiling is refused outright.

        Otherwise the guard's first call is free — and the first call is the one
        most likely to be the pathological one.
        """
        run_id = self.add_run(days_ago=0, cost=0.0)
        with self.assertRaises(ledger.RunCeilingExceeded):
            ledger.charge_run(
                self.conn, run_id=run_id, usd=9.99, clearance=self.clearance()
            )
        self.assertEqual(ledger.run_reserved_usd(self.conn, run_id), 0.0)

    def test_control_2_is_not_replaced_by_control_3(self) -> None:
        """The two bound different things and neither substitutes for the other.

        Ten runs of $4.50 each are all individually legal under control 3 and
        together are $45.00 — which is control 2's whole point about
        `run.est_cost_usd` resetting on every invocation.
        """
        cl = self.clearance()
        for _ in range(10):
            run_id = self.add_run(days_ago=0, cost=0.0)
            ledger.charge_run(self.conn, run_id=run_id, usd=4.50, clearance=cl)
        self.assertAlmostEqual(ledger.monthly_spend_usd(self.conn), 45.00)
        with self.assertRaises(ledger.CeilingExceeded):
            self.add_run(days_ago=0, cost=0.01)
            ledger.check_ceiling(self.conn)

    def test_it_cannot_be_applied_without_consulting_control_2(self) -> None:
        """Composition, not replacement: the clearance is required and unforgeable."""
        run_id = self.add_run(days_ago=0, cost=0.0)
        with self.assertRaises(TypeError):
            ledger.charge_run(self.conn, run_id=run_id, usd=1.00)  # type: ignore[call-arg]

    def test_a_run_that_does_not_exist_is_refused_not_treated_as_empty(self) -> None:
        with self.assertRaises(ledger.RunCeilingExceeded):
            ledger.charge_run(
                self.conn, run_id=9999, usd=0.01, clearance=self.clearance()
            )

    def test_reconciliation_is_never_refused_by_the_per_run_ceiling(self) -> None:
        """M1.101's ruling: a ceiling may not block its own bookkeeping.

        The money is already spent. Refusing the correction would leave
        `run.est_cost_usd` holding a number known to be wrong, and control 2 —
        the guard that actually bounds spend — reads that column.
        """
        run_id = self.add_run(days_ago=0, cost=4.90)
        # An actual that lands well above the per-run ceiling still applies.
        ledger.reconcile_run(self.conn, run_id=run_id, delta_usd=+3.00)
        self.assertAlmostEqual(ledger.run_reserved_usd(self.conn, run_id), 7.90)
        # And a downward correction, which is the ordinary case.
        ledger.reconcile_run(self.conn, run_id=run_id, delta_usd=-1.40)
        self.assertAlmostEqual(ledger.run_reserved_usd(self.conn, run_id), 6.50)


class TheReservationPathEnforcesControl3(LedgerTestCase):
    """The ceiling is at the single write, so a second caller gets it for free."""

    def test_an_oversized_reservation_leaves_no_batch_row_and_no_charge(self) -> None:
        """The refusal rolls back `_write_batch_row` with it (M1.72's transaction).

        This is the property that matters operationally: a refused reservation
        must leave **nothing** — no batch on the books for `reconcile` to find,
        and no money counted against a run that never submitted.
        """
        run_id = self.add_run(days_ago=0, cost=0.0)
        cl = ledger.check_ceiling(self.conn)
        page = types.SimpleNamespace(
            kind="impressum",
            company_id=1,
            artifact_id=1,
            sent_text="x",
            sent_sha256="0" * 64,
        )
        self.conn.execute(
            "INSERT INTO company (id, domain, discovery_source, discovered_at) "
            "VALUES (1,'x.de','seed_csv','2026-08-21T00:00:00Z')"
        )
        self.conn.execute(
            "INSERT INTO artifact (id, company_id, kind, url, fetched_at) "
            "VALUES (1,1,'impressum','https://x.de/impressum','2026-08-21T00:00:00Z')"
        )

        with self.assertRaises(ledger.RunCeilingExceeded):
            extract_p2._commit_reservation(
                self.conn,
                [page],  # type: ignore[list-item]
                [object()],  # type: ignore[list-item]
                run_id=run_id,
                purpose="impressum",
                total_usd=6.00,
                now="2026-08-21T00:00:00Z",
                clearance=cl,
            )

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM llm_batch").fetchone()[0], 0
        )
        self.assertEqual(ledger.run_reserved_usd(self.conn, run_id), 0.0)
