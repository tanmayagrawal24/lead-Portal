"""Releasing a §7 control 4 reservation — the only operation that makes the
ledger smaller, and the three conditions that are the only way in.

Migration 014 said it plainly: *"Nothing releases it automatically. A
reservation released by a rule is how real spend leaves the ledger."* That is
still the governing sentence, and this module does not weaken it. It draws the
distinction 014 could not, because in the crash 014 was written for the
distinction does not exist (M1.117):

* **The outcome is unknowable.** The process died between `messages.batches
  .create` and the row write. Whether a batch exists cannot be established from
  here, so the reservation stands. 014's reading, unchanged.
* **The outcome is known, and the account can be asked.** The provider refused
  the request and said why; no batch id was ever assigned. This is M1.116's
  case, met on the first real submit.

**The rule is not "an operator may clear a row".** It is: *a reservation may be
released only when the account itself says the batch does not exist.* Three
conditions, all required, and the third is a live network read — because a
cached answer, or one inferred from `llm_batch`, is the local record vouching
for itself, which is the thing §10.7b spent four units refusing to accept
(M1.100, M1.114).

Every refusal below is a refusal to release. There is no flag that overrides
them: a condition that can be waived is not a condition, and this is the one
direction §7 says must not fail.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from portal import llm


class ReleaseRefused(RuntimeError):
    """One of the three conditions did not hold, so nothing was written.

    Deliberately not a subclass of anything §7 already catches: a release that
    is refused is not an error in a paid path, it is the mechanism working.
    """


@dataclass(frozen=True)
class Release:
    """What was released, so the caller can report it without re-reading."""

    batch_id: int
    run_id: int
    released_usd: float
    run_est_cost_usd_after: float
    reason: str
    released_at: str
    batches_listed: int


def _parse_created_at(raw: str) -> datetime:
    """The provider's `created_at`, as a comparable instant.

    Raises on anything it cannot read. **Unreadable is not empty** (M1.52): a
    listing whose timestamps cannot be parsed must refuse the release, not be
    treated as an account with nothing in it — that is the one misreading that
    releases money which was actually spent.
    """
    text = raw.strip()
    if not text:
        raise ValueError("a listed batch has no `created_at`")
    # `str()` of the SDK's datetime gives "2026-09-04 12:06:49+00:00"; the API's
    # JSON gives "2026-09-04T12:06:49Z". `fromisoformat` reads both in 3.11+.
    parsed = datetime.fromisoformat(text)
    # Naive and aware instants do not compare, and a naive `created_at` from the
    # provider is UTC by the API's own contract. Anchored rather than assumed
    # at the comparison, so the two sides cannot drift apart.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def release_reservation(
    conn: sqlite3.Connection,
    provider: llm.BatchLister,
    *,
    batch_id: int,
    reason: str,
    now: str,
    limit: int = 20,
) -> Release:
    """Release one reservation, or refuse and write nothing. **Free.**

    Makes one read-only account-scoped call and, only if all three conditions
    hold, one transaction: the batch row moves to `released` with its reason
    and clock, and the submitting run's `est_cost_usd` is **decremented by this
    batch's reservation** — not assigned zero, so a run carrying two batches
    loses only the one that was released.
    """
    if not reason.strip():
        raise ReleaseRefused(
            "a release needs a stated reason: this is the only operation that "
            "makes §7 control 2 smaller, and an unexplained one is "
            "indistinguishable from a mistake later (migration 018)"
        )

    row = conn.execute(
        "SELECT id, run_id, provider_batch_id, status, est_cost_usd, reserved_at "
        "FROM llm_batch WHERE id = ?",
        (batch_id,),
    ).fetchone()
    if row is None:
        raise ReleaseRefused(f"no llm_batch row with id {batch_id}")

    _, run_id, provider_batch_id, status, est_cost_usd, reserved_at = row

    # Condition 1. A row that learned an id was submitted, whatever else is
    # true of it — the id IS the evidence that `create` returned.
    if provider_batch_id is not None:
        raise ReleaseRefused(
            f"batch {batch_id} carries provider_batch_id {provider_batch_id!r}, "
            f"so `messages.batches.create` returned and the batch exists. "
            f"Committed spend is corrected by a MEASURED actual through "
            f"`portal reconcile` (§7 control 12), never by release"
        )

    # Condition 2. Any other status is a batch with a known history; only
    # `reserved` means the outcome was never learned.
    if status != "reserved":
        raise ReleaseRefused(
            f"batch {batch_id} has status {status!r}, not 'reserved'. Only a "
            f"reservation whose submit outcome was never learned can be "
            f"released; anything else has a history that release would erase"
        )

    try:
        boundary = _parse_created_at(reserved_at)
    except ValueError as exc:
        raise ReleaseRefused(
            f"batch {batch_id} has an unreadable reserved_at {reserved_at!r} "
            f"({exc}), so there is no instant to compare the account's batches "
            f"against. Unreadable is not empty (M1.52)"
        ) from None

    # Condition 3, the one that carries the weight: ask the ACCOUNT, live.
    listed = provider.list_batches(limit=limit)
    for batch in listed:
        try:
            created = _parse_created_at(batch.created_at)
        except ValueError as exc:
            raise ReleaseRefused(
                f"the account listing includes batch "
                f"{batch.provider_batch_id!r} with an unreadable created_at "
                f"{batch.created_at!r} ({exc}) — refusing to release against a "
                f"listing that cannot be read. Unreadable is not empty (M1.52)"
            ) from None
        if created >= boundary:
            raise ReleaseRefused(
                f"the account holds batch {batch.provider_batch_id!r} created "
                f"{batch.created_at} — at or after batch {batch_id}'s "
                f"reservation at {reserved_at}. It may be the batch this "
                f"reservation paid for, so the money is not free to release. "
                f"Account for it with `portal reconcile` first (§5.6)"
            )

    # All three hold. One transaction, both writes (M1.72's shape, inverted).
    with conn:
        conn.execute(
            "UPDATE llm_batch SET status = 'released', released_at = ?, "
            "release_reason = ? WHERE id = ?",
            (now, reason.strip(), batch_id),
        )
        conn.execute(
            "UPDATE run SET est_cost_usd = est_cost_usd - ? WHERE id = ?",
            (est_cost_usd, run_id),
        )
    after = float(
        conn.execute("SELECT est_cost_usd FROM run WHERE id = ?", (run_id,)).fetchone()[
            0
        ]
    )
    return Release(
        batch_id=batch_id,
        run_id=int(run_id),
        released_usd=float(est_cost_usd),
        run_est_cost_usd_after=after,
        reason=reason.strip(),
        released_at=now,
        batches_listed=len(listed),
    )


PAID_SURFACES: tuple[str, ...] = ()
FREE_SURFACES: tuple[str, ...] = ("release_reservation",)

llm.assert_ledger_guarded(
    sys.modules[__name__],
    paid=PAID_SURFACES,
    free=FREE_SURFACES,
    where="portal.reservations",
)

__all__ = [
    "FREE_SURFACES",
    "PAID_SURFACES",
    "Release",
    "ReleaseRefused",
    "release_reservation",
]
