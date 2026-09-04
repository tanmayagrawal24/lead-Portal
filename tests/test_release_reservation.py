"""M1.117. `portal release-reservation` — the only operation that makes §7
control 2 smaller, and the three conditions that are the only way in.

Migration 014's rule stands: nothing releases a reservation automatically.
What 018 adds is a rule with evidence attached — the account itself saying the
batch does not exist — so the tests that matter are the REFUSALS. A release
path is only worth having if it declines.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portal import cli, db, llm, migrate, reservations

RESERVED_AT = "2026-09-04T12:06:49Z"


def _listing(provider_batch_id: str, created_at: str) -> llm.BatchListing:
    return llm.BatchListing(
        provider_batch_id=provider_batch_id,
        processing_status="ended",
        created_at=created_at,
        expires_at="",
        succeeded=9,
        errored=0,
        expired=0,
        canceled=0,
        processing=0,
    )


class FakeLister:
    """Only `list_batches` — the protocol is narrow so the fake cannot
    accidentally model a submitting provider (M1.115's lesson)."""

    def __init__(self, *batches: llm.BatchListing) -> None:
        self.batches = batches
        self.calls: list[int] = []

    def list_batches(self, *, limit: int = 20) -> tuple[llm.BatchListing, ...]:
        self.calls.append(limit)
        return self.batches


class ReleaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "portal.db"
        self.conn = db.connect(self.path)
        migrate.apply_pending(self.conn)
        self.addCleanup(self.conn.close)
        self.conn.execute(
            "INSERT INTO run (id, started_at, stage, est_cost_usd) "
            "VALUES (5, ?, 'extract-p2', 0.0639135)",
            (RESERVED_AT,),
        )
        self.conn.execute(
            "INSERT INTO llm_batch (id, provider_batch_id, run_id, purpose, "
            "request_count, est_cost_usd, status, reserved_at) "
            "VALUES (1, NULL, 5, 'impressum', 9, 0.0639135, 'reserved', ?)",
            (RESERVED_AT,),
        )
        self.conn.commit()

    def run_est(self) -> float:
        return float(
            self.conn.execute("SELECT est_cost_usd FROM run WHERE id = 5").fetchone()[0]
        )

    def status(self) -> str:
        return str(
            self.conn.execute("SELECT status FROM llm_batch WHERE id = 1").fetchone()[0]
        )


class TheAcceptPath(ReleaseTestCase):
    def test_an_empty_account_releases_the_reservation(self) -> None:
        lister = FakeLister()
        release = reservations.release_reservation(
            self.conn,
            lister,
            batch_id=1,
            reason="400 on custom_id; no batch created",
            now="2026-09-04T13:00:00Z",
        )
        self.assertEqual(self.status(), "released")
        self.assertAlmostEqual(release.released_usd, 0.0639135)
        self.assertAlmostEqual(self.run_est(), 0.0, places=9)
        self.assertEqual(lister.calls, [20], "the account must be asked, live")

    def test_a_batch_created_before_the_reservation_does_not_block_it(self) -> None:
        """The boundary is the reservation's own clock: an older batch belongs
        to some earlier run and says nothing about this one."""
        lister = FakeLister(_listing("msgbatch_old", "2026-09-01T00:00:00Z"))
        reservations.release_reservation(
            self.conn, lister, batch_id=1, reason="r", now="2026-09-04T13:00:00Z"
        )
        self.assertEqual(self.status(), "released")

    def test_the_reason_and_clock_are_stored_on_the_row(self) -> None:
        reservations.release_reservation(
            self.conn,
            FakeLister(),
            batch_id=1,
            reason="  400 invalid_request_error  ",
            now="2026-09-04T13:00:00Z",
        )
        row = self.conn.execute(
            "SELECT release_reason, released_at, est_cost_usd FROM llm_batch "
            "WHERE id = 1"
        ).fetchone()
        self.assertEqual(row[0], "400 invalid_request_error")
        self.assertEqual(row[1], "2026-09-04T13:00:00Z")
        self.assertAlmostEqual(
            row[2], 0.0639135, msg="the batch keeps the record of what was released"
        )

    def test_only_the_released_batch_leaves_a_shared_run(self) -> None:
        """Decremented, not assigned zero — a run carrying two batches must
        lose only the one that was released."""
        self.conn.execute(
            "INSERT INTO llm_batch (id, provider_batch_id, run_id, purpose, "
            "request_count, est_cost_usd, status, reserved_at, submitted_at) "
            "VALUES (2, 'msgbatch_real', 5, 'impressum', 3, 0.02, 'submitted', "
            "?, ?)",
            (RESERVED_AT, RESERVED_AT),
        )
        self.conn.execute("UPDATE run SET est_cost_usd = 0.0839135 WHERE id = 5")
        self.conn.commit()
        reservations.release_reservation(
            self.conn, FakeLister(), batch_id=1, reason="r", now="now"
        )
        self.assertAlmostEqual(self.run_est(), 0.02, places=9)


class TheRefusePath(ReleaseTestCase):
    def assert_untouched(self) -> None:
        self.assertEqual(self.status(), "reserved")
        self.assertAlmostEqual(self.run_est(), 0.0639135)

    def test_a_batch_created_after_the_reservation_refuses(self) -> None:
        """The condition that carries the weight: the account holds something
        this reservation might have paid for."""
        lister = FakeLister(_listing("msgbatch_maybe", "2026-09-04T12:06:50Z"))
        with self.assertRaises(reservations.ReleaseRefused) as caught:
            reservations.release_reservation(
                self.conn, lister, batch_id=1, reason="r", now="now"
            )
        self.assertIn("msgbatch_maybe", str(caught.exception))
        self.assert_untouched()

    def test_a_row_with_a_provider_id_refuses(self) -> None:
        self.conn.execute(
            "UPDATE llm_batch SET provider_batch_id = 'msgbatch_x', "
            "submitted_at = ?, status = 'submitted' WHERE id = 1",
            (RESERVED_AT,),
        )
        self.conn.commit()
        with self.assertRaises(reservations.ReleaseRefused) as caught:
            reservations.release_reservation(
                self.conn, FakeLister(), batch_id=1, reason="r", now="now"
            )
        self.assertIn("reconcile", str(caught.exception))

    def test_a_row_that_is_not_reserved_refuses(self) -> None:
        self.conn.execute(
            "UPDATE llm_batch SET status = 'released', released_at = 'x', "
            "release_reason = 'already done' WHERE id = 1"
        )
        self.conn.commit()
        with self.assertRaises(reservations.ReleaseRefused):
            reservations.release_reservation(
                self.conn, FakeLister(), batch_id=1, reason="r", now="now"
            )
        self.assertAlmostEqual(
            self.run_est(), 0.0639135, msg="a second release must not double-credit"
        )

    def test_an_empty_reason_refuses(self) -> None:
        with self.assertRaises(reservations.ReleaseRefused) as caught:
            reservations.release_reservation(
                self.conn, FakeLister(), batch_id=1, reason="   ", now="now"
            )
        self.assertIn("reason", str(caught.exception))
        self.assert_untouched()

    def test_an_unknown_batch_refuses(self) -> None:
        with self.assertRaises(reservations.ReleaseRefused):
            reservations.release_reservation(
                self.conn, FakeLister(), batch_id=99, reason="r", now="now"
            )

    def test_an_unreadable_created_at_refuses_rather_than_reading_it_as_empty(
        self,
    ) -> None:
        """M1.52 in the one place where the wrong reading releases money that
        was actually spent."""
        lister = FakeLister(_listing("msgbatch_bad", "not-a-timestamp"))
        with self.assertRaises(reservations.ReleaseRefused) as caught:
            reservations.release_reservation(
                self.conn, lister, batch_id=1, reason="r", now="now"
            )
        self.assertIn("Unreadable is not empty", str(caught.exception))
        self.assert_untouched()

    def test_a_naive_created_at_is_compared_rather_than_crashing(self) -> None:
        lister = FakeLister(_listing("msgbatch_naive", "2026-09-04 12:06:50"))
        with self.assertRaises(reservations.ReleaseRefused) as caught:
            reservations.release_reservation(
                self.conn, lister, batch_id=1, reason="r", now="now"
            )
        self.assertIn("msgbatch_naive", str(caught.exception))


class TheCommandSurface(ReleaseTestCase):
    def test_the_cli_releases_and_reports(self) -> None:
        code = cli.cmd_release_reservation(
            self.path, 1, "400 on custom_id", provider=FakeLister()
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.status(), "released")

    def test_the_cli_exits_2_on_a_refusal_and_writes_nothing(self) -> None:
        lister = FakeLister(_listing("msgbatch_maybe", "2026-09-05T00:00:00Z"))
        code = cli.cmd_release_reservation(self.path, 1, "r", provider=lister)
        self.assertEqual(code, 2)
        self.assertEqual(self.status(), "reserved")
        self.assertAlmostEqual(self.run_est(), 0.0639135)

    def test_the_schema_refuses_a_released_row_with_no_reason(self) -> None:
        """The reason is a CHECK, not a convention (018)."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE llm_batch SET status = 'released', released_at = 'x' "
                "WHERE id = 1"
            )
