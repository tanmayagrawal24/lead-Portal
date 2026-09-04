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

**Control 3 lives here too, as of Unit 11 (M1.109).** It was specified in §7
from v0.3 and existed nowhere for four units; `MONTHLY_CEILING_USD` was the only
ceiling constant in the tree. It is here rather than beside its callers for
M1.70's reason — the decision and its expression in one place — and because
there is now more than one caller: `extract_p2` charges a batch reservation
against it, and `ai_visibility` checks a whole run's estimate against it before
any row exists. **One constant, one exception class, two enforcement points**;
`ai_visibility.PER_RUN_CEILING_USD` was the second expression and is gone
(M1.109).

**Nothing wrote `run.est_cost_usd` until M5.** The gate shipped four units
before its caller, which was the intended order (M1.69–M1.72): the reader
exists *before* the writer, so the writer is built against its presence rather
than its absence. In production this ledger still reads `$0.00` until a batch
is actually submitted, which is §10.7b's precondition.
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

#: §7 control 3's default. **This is the one meant to bite occasionally.**
#: Control 2 is a runaway guard whose job is to be silent — §7.1's expected
#: steady state is $31–36/month against a $45 ceiling, so reaching it means
#: something is wrong. This one bounds a *single invocation*, where the normal
#: reason to hit it is a legitimately large batch, and the operator's normal
#: response is to look at the reservation and raise the cap for that run. On
#: M6's arithmetic (~$0.06 a company) it bites at ~80 companies, which is a run
#: worth a second look.
#:
#: Control 2's own text is explicit that this ceiling does not bound total
#: spend — `run.est_cost_usd` resets on every invocation, so ten aborted-and-
#: retried runs cost ten times this number. The two controls bound different
#: things and neither substitutes for the other.
RUN_CEILING_USD = 5.0

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


class RunCeilingExceeded(RuntimeError):
    """§7 control 3: one run's reservations exceed the per-run ceiling.

    **Deliberately not a subclass of `CeilingExceeded`, because the two send an
    operator to different places.** Control 2 firing means something is wrong —
    a loop, a pathological catalogue, a bug — and the response is to
    investigate. Control 3 firing is an ordinary operating condition: this run
    is bigger than the default cap, and the response is to read the reservation
    and decide. A shared type would let an `except CeilingExceeded` written for
    the runaway case silently swallow the routine one, which is the direction
    that spends money.

    **One class for both enforcement points (M1.109).** `charge_run` raises it
    at the reservation write, where a batch's price is known only per call;
    `ai_visibility.run` raises it against a whole run's estimate before the
    `run` row exists, because there the total *is* knowable up front. Same
    control, same refusal, two places it can be measured — so a single
    `except RunCeilingExceeded` catches control 3 wherever it fires.
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


def run_reserved_usd(conn: sqlite3.Connection, run_id: int) -> float:
    """What this one run has already reserved. §7 control 3's accumulator.

    `COALESCE` because `run.est_cost_usd` is NULL until the first reservation
    lands, and a run that has reserved nothing has reserved zero — not
    "unknown". The distinction matters in the other direction only: an
    unreadable ledger raises out of `sqlite3`, as it does in control 2.
    """
    row = conn.execute(
        "SELECT COALESCE(est_cost_usd, 0) FROM run WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise RunCeilingExceeded(
            f"§7 control 3: run {run_id} does not exist, so its reservations "
            f"cannot be bounded. Refusing rather than treating an absent run "
            f"as one that has spent nothing."
        )
    return float(row[0])


def charge_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    usd: float,
    clearance: LedgerClearance,
    ceiling_usd: float | None = None,
) -> float:
    """§7 control 3: check this run's total against the per-run ceiling, then charge.

    Returns the run's new reserved total.

    **It takes a `LedgerClearance` so that control 3 composes with control 2
    rather than replacing it.** The clearance is proof that the rolling window
    was consulted, and it is unforgeable — `check_ceiling` is the only thing
    that constructs one. Requiring it here means the per-run ceiling cannot be
    applied *instead of* the outer bound by a caller who found the smaller
    number more convenient. The two bound different things: control 2 bounds
    thirty days across every run, control 3 bounds one invocation, and control
    2's own text says so.

    **The check is on the post-charge total, not on the increment.** A single
    reservation larger than the whole ceiling is refused outright rather than
    allowed because it is the first one — otherwise the guard's first call is
    free, which is the call most likely to be the pathological one.

    **Caller must hold the write transaction.** This reads the accumulator and
    writes it back, and the two must not be separable — `extract_p2` calls it
    inside the `BEGIN IMMEDIATE` that M1.72 requires for the pair of writes, so
    the read-modify-write is already serialised against another connection.
    Nothing here opens a transaction of its own, because a nested one would
    break that guarantee rather than add to it.

    **Direction of error: it fails closed, and it over-counts while doing so.**
    The estimate is written *before* the call (control 3's own wording), so a
    crash between reservation and submission leaves money reserved that was
    never spent — a conservatively aborted run rather than silent overspend.
    A wrongly-refused run costs the operator one retry with a raised cap; a
    wrongly-cleared one costs money that is already gone.
    """
    # Resolved at call time, not bound as a default. A default argument is
    # evaluated once at import, which would freeze `RUN_CEILING_USD` into every
    # signature that names it and make the module-level constant unpatchable and
    # — worse — unchangeable by anything that edits it after import. One
    # expression means one expression *at the moment of the check* (M1.109).
    if ceiling_usd is None:
        ceiling_usd = RUN_CEILING_USD
    already = run_reserved_usd(conn, run_id)
    proposed = already + usd
    if proposed > ceiling_usd:
        raise RunCeilingExceeded(
            f"§7 control 3: run {run_id} has reserved ${already:.4f} and this "
            f"call would take it to ${proposed:.4f}, over the ${ceiling_usd:.2f} "
            f"per-run ceiling. This control is expected to bite occasionally — "
            f"unlike control 2, being here does not by itself mean something is "
            f"wrong. Read the reservation, and raise the cap for this run only "
            f"if the number is one you meant to spend. Control 2 is unaffected "
            f"and still reads ${clearance.spend_usd:.2f} of "
            f"${clearance.ceiling_usd:.2f} over {clearance.window_days} days."
        )
    conn.execute(
        "UPDATE run SET est_cost_usd = COALESCE(est_cost_usd, 0) + ? WHERE id = ?",
        (usd, run_id),
    )
    return proposed


def reconcile_run(conn: sqlite3.Connection, *, run_id: int, delta_usd: float) -> None:
    """Apply a measured actual to a run's reservation. **Never refused (M1.90).**

    **This deliberately does not consult control 3, and the omission is the
    ruling (M1.110).** A reconciliation is not a request to spend — the money is
    already gone, and this is the correction that makes the ledger true.
    Refusing it because the corrected total lands above the per-run ceiling
    would leave `run.est_cost_usd` holding a number known to be wrong, which
    makes control 2 — the guard that actually bounds spend — read a falsehood.
    **A ceiling that can block its own bookkeeping is a ceiling that degrades
    the ledger it exists to protect.**

    Separate from `charge_run` for the same reason `_charge_run` is separate
    from `_write_batch_row` in `extract_p2`: *reserve* and *reconcile* are
    different operations with different rules, and one function taking a flag
    would be one place for a later edit to conflate them. The delta may be
    negative — B3.1 sends a batch's actual back to the submitting run, and an
    over-estimate corrects downward.
    """
    conn.execute(
        "UPDATE run SET est_cost_usd = COALESCE(est_cost_usd, 0) + ? WHERE id = ?",
        (delta_usd, run_id),
    )


__all__ = [
    "MONTHLY_CEILING_USD",
    "RUN_CEILING_USD",
    "WINDOW_DAYS",
    "CeilingExceeded",
    "LedgerBypass",
    "LedgerClearance",
    "RunCeilingExceeded",
    "charge_run",
    "check_ceiling",
    "monthly_spend_usd",
    "reconcile_run",
    "run_reserved_usd",
]
