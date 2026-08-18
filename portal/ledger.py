"""§7 control 2 — the rolling 30-day cost ceiling, and the only query that reads it.

This module is the outer bound on spend. §7 control 3's per-run ceiling is the
one meant to bite occasionally; this one is a **runaway guard** — a bug, a
redirect loop, a pathological catalogue — and M1.23 reframed it as such when it
raised the default from `$25` to `$45`.

**Why the ceiling lives here and not in `llm.py`.** `llm.py` holds *prices*:
inputs to the arithmetic, dated facts about a vendor. `MONTHLY_CEILING_USD` is
an *output constraint* — a policy bound on the total those prices produce. They
are different kinds of number, and the concrete reason to keep the bound beside
the query that enforces it is M1.70: §7 stated the window and the SQL, §10.4b
said neither existed, and the two drifted apart for two units precisely because
the decision and its expression were written in different places. One home, so
M5 does not invent a second.

**Reserved and reconciled are one column.** The query sums `run.est_cost_usd`
alone. It is *not* summed together with `llm_batch.est_cost_usd`, because §7
control 4 reserves a batch into both tables and adding them counts every batch
twice (M1.69). `run` is the ledger; `llm_batch` is the per-batch record of a
line already in it.

**Nothing writes `run.est_cost_usd` yet.** Controls 3 and 4 need a caller and
belong to M5, so in production this ledger currently reads `$0.00` and every
gate below passes. That is the intended order: the gate exists *before* the
caller, so M5 is written against its presence rather than its absence.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

#: §7 control 2. Rolling days, counted back from now, keyed on `run.started_at`.
#: A calendar month was rejected: it makes the guard's strictness depend on the
#: day of the month, so an identical bug costs a full ceiling starting on the
#: 2nd and almost nothing starting on the 30th.
WINDOW_DAYS = 30

#: §7 control 2's default, raised from `$25` by M1.23 — see §7.1's arithmetic.
#: Expected steady state is $31–36/month, so this is ~25% headroom over the
#: worst case and is deliberately *not* a budget.
MONTHLY_CEILING_USD = 45.0

#: The §7 control 2 query, written once. `run.started_at` is the only date this
#: window is keyed on, which is what keeps it consistent with B3.1: a batch
#: reconciles against its *submitting* run, whose `started_at` does not move, so
#: a batch submitted in one window and reconciled in the next never moves money
#: across the boundary.
_SPEND_SQL = (
    "SELECT COALESCE(SUM(est_cost_usd), 0) FROM run "
    f"WHERE started_at > datetime('now','-{WINDOW_DAYS} days')"
)


class LedgerBypass(RuntimeError):
    """A paid surface was called without a `LedgerClearance`.

    This is a programming error, not a runtime condition — it means a paid path
    was reached without §7 control 2 being consulted, which is the exact failure
    the import-time assertion in `llm.py` exists to make impossible to ship. It
    is raised rather than warned because a guard that can be ignored is the
    convention §7's preamble refuses: *"implemented as code, not as discipline"*.
    """


class CeilingExceeded(RuntimeError):
    """The rolling window is over budget, so no paid path may proceed.

    Distinct from `llm.LLMConfigError`: that one means *this call is not
    describable*, this one means *this call is not affordable*. They send an
    operator to different places, which is the same argument §7 control 11
    makes for keeping `billing_error` out of `failed`.
    """


@dataclass(frozen=True)
class LedgerClearance:
    """Proof that the §7 control 2 ledger was consulted and permitted a call.

    **This is the mechanism, and its unforgeability is the whole point.** Every
    paid surface requires one of these, and `check_ceiling` is the only thing
    that constructs one — so "did anyone check the ledger?" is answered by the
    type system at the call site rather than by a convention someone has to
    remember. A reading is kept rather than a bare boolean because an abort
    that cannot say *how much* and *against what* is an abort its operator will
    raise the ceiling to silence.
    """

    spend_usd: float
    ceiling_usd: float
    window_days: int
    taken_at: str

    @property
    def headroom_usd(self) -> float:
        """What is left before the guard trips."""
        return self.ceiling_usd - self.spend_usd


def monthly_spend_usd(conn: sqlite3.Connection) -> float:
    """§7 control 2's ledger: reserved plus reconciled, over the rolling window.

    One table, deliberately (M1.69). Control 3 reconciles an estimate to actual
    usage *in this same column*, and B3.1 sends a batch's `actual_cost_usd` back
    to the submitting run's reservation — so "reserved plus reconciled" is one
    sum over `run`, not a union of two tables.
    """
    return float(conn.execute(_SPEND_SQL).fetchone()[0])


def check_ceiling(
    conn: sqlite3.Connection, *, ceiling_usd: float = MONTHLY_CEILING_USD
) -> LedgerClearance:
    """Consult the ledger and either clear the call or refuse it.

    **Direction of error, stated because §7's controls are chosen for it:** this
    fails **closed**. A ledger that cannot be read raises rather than returning
    zero, and `sqlite3.Error` is deliberately not caught here — an unreadable
    ledger is indistinguishable from an empty one, and treating it as empty is
    exactly how an unmeasured number gets to authorise spend (M1.52's argument,
    one layer out). A wrongly-refused run costs an operator one retry; a wrongly
    -cleared one costs money that is already gone.
    """
    spend = monthly_spend_usd(conn)
    if spend > ceiling_usd:
        raise CeilingExceeded(
            f"§7 control 2: ${spend:.2f} reserved or spent in the last "
            f"{WINDOW_DAYS} days exceeds the ${ceiling_usd:.2f} ceiling. "
            f"This is a runaway guard, not a budget (M1.23) — expected steady "
            f"state is $31–36/month, so being here means something is wrong, "
            f"not that the month was busy."
        )
    return LedgerClearance(
        spend_usd=spend,
        ceiling_usd=ceiling_usd,
        window_days=WINDOW_DAYS,
        # Deliberately the same format as `artifacts.utc_now`, and deliberately
        # not an import of it: that helper sits in a module that pulls in the
        # HTTP transport, and the cost ledger must not depend on the fetcher to
        # know what time it is. The duplication is four tokens; the coupling
        # would be permanent.
        taken_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


__all__ = [
    "MONTHLY_CEILING_USD",
    "WINDOW_DAYS",
    "CeilingExceeded",
    "LedgerBypass",
    "LedgerClearance",
    "check_ceiling",
    "monthly_spend_usd",
]
