"""§5.6 — poll a submitted batch, verify what came back, and write it down.

**The half of M5 that survives a restart.** `extract_p2` submits; this collects.
The two are deliberately not one command and not one process, because a batch
takes up to 24 hours and its results stay retrievable for 29 days (§5.6 fact 4).
Everything this stage needs is therefore read **out of the database**: the batch
rows, the request set that was sent, and the artifacts the text was read off.
There is no object handed over from submit, no cache, and no state that a
process boundary could lose. A test that keeps the batch in a variable would
prove nothing about that, so `tests/test_reconcile.py` throws the process state
away and opens a fresh connection.

**Verification is §5.5b's, and it is the reason this file is shaped as it is.**
`verify.PageText` takes the SENT TEXT as an argument and cannot reach an
artifact (M1.43): checking an extraction against a document the model was never
shown would pass or fail for reasons unrelated to what the model saw. Across a
process boundary the sent text is gone, so it is **reconstructed** — same
artifact id off the `custom_id`, same immutable bytes, same `extract_p2.clean`
— and then **checked** against the SHA-256 the reservation stored (M1.87).
Reconstruction alone would be an assumption; the digest makes it a test. A
mismatch writes no value at all.

**Four things about how results arrive, none visible from the happy path**
(§5.6, M1.51), and a fifth this unit added:

1. Results come back in **arbitrary order**; `custom_id` is the only thing
   tying a returned legal name to a company. Substring verification cannot
   catch a mis-key, because the value really is on the page it came from.
2. `expired` is a **per-request** result type, so a batch ends *normally*
   carrying requests that were never processed.
3. `errored` splits: `invalid_request` will be malformed again, a server error
   will not.
4. Results stay retrievable for 29 days; past that a batch is unrecoverable and
   must be re-run as new spend.
5. **A batch can return fewer results than it was sent** (M1.86). The stored
   request set is what makes that visible; `request_count` is a number and
   cannot name the two companies that went missing.

**The cost ledger's other half (§7 control 12, B3.2/B3.3).** `reconcile` is the
first code in this project that ever learns an *actual*. What it does with one
is stated in §7 as a rule rather than only here: the measured cost corrects the
reservation on the **submitting** run (B3.1), it is applied once and only when
the batch reaches a terminal state, and nothing else ever releases a
reservation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from portal import extract_p2, llm, verify

#: The one disposition in `llm_batch_request.outcome` that is **not** a member
#: of `llm.RequestOutcome`, and the separation is deliberate (migration 015).
#: `portal/llm.py` holds facts about a vendor; this is this tool's opinion about
#: its own ability to check a result, which is not the provider's business.
TEXT_UNREPRODUCIBLE = "text_unreproducible"

#: Batch statuses from which more work can still arrive. `reserved` is
#: deliberately **not** here: a reserved batch has no provider id to poll, and
#: it is reported to a human rather than retried (migration 014).
OPEN_STATUSES = ("submitted", "completed")

#: Batch statuses that close a batch. `expired` and `failed` were declared in §4
#: and reachable from nothing until M1.86 gave `resolve_batch_status` the request
#: set it needed to tell them apart from *still owed*.
TERMINAL_STATUSES = ("reconciled", "expired", "failed", "balance_exhausted")


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReconcileError(RuntimeError):
    """A batch cannot be reconciled and continuing would write something false.

    Raised rather than warned for the reason §7's preamble gives about its own
    controls: a guard that can be skipped is a convention. Every instance below
    is a state where the alternative is writing a signal whose provenance the
    tool cannot establish.
    """


# ── what the database already knows ─────────────────────────────────────


@dataclass(frozen=True)
class BatchRow:
    """One `llm_batch` row. **The whole of reconcile's starting state.**"""

    id: int
    provider_batch_id: str | None
    run_id: int
    purpose: str
    request_count: int
    est_cost_usd: float
    actual_cost_usd: float | None
    status: str


@dataclass(frozen=True)
class RequestRow:
    """One `llm_batch_request` row — a request that was sent, and its fate."""

    id: int
    custom_id: str
    company_id: int
    artifact_id: int
    sent_text_sha256: str
    outcome: str | None


@dataclass
class BatchReport:
    """What one batch's reconciliation did, in the words an operator needs."""

    batch_id: int
    provider_batch_id: str | None
    purpose: str
    status_before: str
    status_after: str
    est_cost_usd: float
    actual_cost_usd: float | None = None
    ledger_delta_usd: float = 0.0
    signals_written: int = 0
    contacts_written: int = 0
    dispositions: dict[str, int] = field(default_factory=dict)
    still_owed: tuple[str, ...] = ()
    resubmittable: tuple[str, ...] = ()
    note: str = ""

    @property
    def closed(self) -> bool:
        return self.status_after in TERMINAL_STATUSES


@dataclass
class ReconcileResult:
    run_id: int
    batches: list[BatchReport] = field(default_factory=list)
    #: Batches whose submit outcome is unknown (migration 014). Reported, never
    #: retried and never released — only a human can say what happened to them.
    reserved_unknown: list[BatchRow] = field(default_factory=list)


def _batch_rows(conn: sqlite3.Connection, statuses: tuple[str, ...]) -> list[BatchRow]:
    marks = ",".join("?" for _ in statuses)
    return [
        BatchRow(
            id=int(r["id"]),
            provider_batch_id=r["provider_batch_id"],
            run_id=int(r["run_id"]),
            purpose=str(r["purpose"]),
            request_count=int(r["request_count"]),
            est_cost_usd=float(r["est_cost_usd"]),
            actual_cost_usd=(
                None if r["actual_cost_usd"] is None else float(r["actual_cost_usd"])
            ),
            status=str(r["status"]),
        )
        for r in conn.execute(
            f"SELECT * FROM llm_batch WHERE status IN ({marks}) ORDER BY id", statuses
        )
    ]


def open_batches(conn: sqlite3.Connection) -> list[BatchRow]:
    """Every batch with work still owed. **This is the restart-survival seam.**

    It reads `llm_batch` and nothing else — no argument, no handover, no memory
    of a `submit` that may have happened in a process that no longer exists.
    §5.6's *"polls every `llm_batch` row with status `submitted`"* is this query,
    widened by one status: a batch that ended short is `completed` and still
    owed, and leaving it out would make M1.86's whole finding unactionable.
    """
    return _batch_rows(conn, OPEN_STATUSES)


def reserved_batches(conn: sqlite3.Connection) -> list[BatchRow]:
    """Batches reserved whose submit outcome is unknown (migration 014)."""
    return _batch_rows(conn, ("reserved",))


def requests_of(conn: sqlite3.Connection, batch_id: int) -> list[RequestRow]:
    """The set that was sent. §5.6 fact 2's *"every one of its requests"*."""
    return [
        RequestRow(
            id=int(r["id"]),
            custom_id=str(r["custom_id"]),
            company_id=int(r["company_id"]),
            artifact_id=int(r["artifact_id"]),
            sent_text_sha256=str(r["sent_text_sha256"]),
            outcome=r["outcome"],
        )
        for r in conn.execute(
            "SELECT * FROM llm_batch_request WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        )
    ]


# ── the sent text, reconstructed and then checked ───────────────────────


def sent_text_for(
    conn: sqlite3.Connection, root: Path, request: RequestRow
) -> str | None:
    """The exact string that was sent, or `None` if it cannot be reproduced.

    Three things make the reconstruction sound, and the fourth is what makes it
    *checked* rather than merely argued (M1.87):

    1. `custom_id` names the artifact, so the page is the one that was read —
       not "the company's Impressum", which is a lookup that could move.
    2. Artifact bodies are content-addressed and never rewritten in place.
    3. `extract_p2.clean` is one expression, shared with the submitting half.
    4. The reservation stored the SHA-256 of what it sent, and this compares.

    Point 4 exists because points 1–3 are an argument about today's build, and
    `reconcile` may run under a later one — results stay retrievable for 29
    days. A change to `parsers.visible_text` in that window would silently move
    the text `verify` checks against, which is M1.43's shape with every test
    still green.

    Returns `None` rather than raising, because an unreproducible page is one
    request's problem and the rest of the batch is still worth collecting.
    """
    row = conn.execute(
        "SELECT body_path FROM artifact WHERE id = ?", (request.artifact_id,)
    ).fetchone()
    if row is None or not row["body_path"]:
        return None
    path = root / str(row["body_path"])
    if not path.is_file():
        return None
    sent, _ = extract_p2.clean(path.read_text(encoding="utf-8", errors="replace"))
    if extract_p2.sha256_of(sent) != request.sent_text_sha256:
        return None
    return sent


# ── §5.5b's mapping, written down ───────────────────────────────────────

#: Every signal key `_impressum_signals` can write, declared rather than
#: inferred — `ruleset.Rule.reads`' idiom, and a test asserts the two agree in
#: both directions. A key written but not declared is a signal §5.5b's mapping
#: table does not know about; a key declared but never written is B7's shape.
IMPRESSUM_KEYS: tuple[str, ...] = (
    "impressum.legal_name",
    "impressum.legal_form",
    "impressum.postal_code",
    "impressum.city",
    "impressum.country",
    "impressum.gf_count",
    "impressum.owner_name_present",
    "impressum.register_court",
    "impressum.register_number",
    "llm.impressum_extracted",
)

HOMEPAGE_KEYS: tuple[str, ...] = (
    "offer.one_line",
    "offer.product_categories",
    "offer.audience",
    "site.owner_named",
    "brand.own_brand",
    "agency.footer_credit_llm",
    "llm.homepage_extracted",
)

#: §5.5b's *"never a signal"* column, kept as data so the reason is checkable.
#: These reach `contact` (§8) and stop there — a street and a VAT id are
#: personal or registration data about a named business, not measurements of it.
CONTACT_ONLY_FIELDS: tuple[str, ...] = ("street", "vat_id", "email", "phone")


def _text_or_none(payload: dict[str, object], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class _SignalWriter:
    """One company's signal writes for one extraction, with its verdicts.

    Collected rather than executed as it goes, so the whole extraction is
    written inside the batch's transaction or not at all.
    """

    def __init__(self, page: verify.PageText, declared: tuple[str, ...]) -> None:
        self.page = page
        self.declared = declared
        self.rows: list[tuple[str, float | None, str | None, float]] = []
        self.verdicts: list[verify.Verdict] = []

    def fact(self, key: str, model: str) -> None:
        """A stage fact: `confidence = 1`, always (§5.5b, migration 012).

        *A stage cannot be wrong about whether it ran.* These are the rows that
        keep "the extraction ran and its answer was rejected" distinguishable
        from "Phase 2 never ran here", which every A7 guard downstream depends
        on because they all work by declining to write.
        """
        self.emit(key, num=1.0, text=model, confidence=verify.VERIFIED)

    def verified_text(self, key: str, value: str | None) -> verify.Verdict | None:
        """A quoted value: written either way, with the verdict as confidence.

        A rejected value keeps its string in `value_text` deliberately (A2 §3):
        a red row in §9 with no value tells the operator nothing to check.
        """
        if value is None:
            return None
        verdict = self.page.check(key, value)
        self.emit(key, text=value, confidence=verdict.confidence)
        self.verdicts.append(verdict)
        return verdict

    def verified_boolean(
        self, key: str, value: bool | None, evidence: str | None
    ) -> None:
        """A boolean verified through its `_evidence` span (M1.47, M1.49).

        Weaker than a quoted value and weaker in a specific way: it proves the
        model did not fabricate its evidence, and it cannot catch the model
        reading the page correctly and inferring wrongly.

        **A `null` writes nothing at all**, which is what makes §6.1's third
        state reachable: the stage fact says the extraction ran and the absent
        boolean says it could not tell, so the rule abstains and a person is
        told. **A boolean with no span is rejected rather than trusted** — a
        judgement with nothing behind it — and a span that fails the check is
        rejected too. Both land at `confidence = 0`, whose row migration 012
        removes from the read model, so §6.1 reaches the same abstention by the
        other of its two routes.
        """
        if value is None:
            return
        verdict = self.page.check_boolean(key, evidence)
        confidence = verify.REJECTED if verdict is None else verdict.confidence
        if verdict is not None:
            self.verdicts.append(verdict)
        self.emit(key, num=1.0 if value else 0.0, text=evidence, confidence=confidence)

    def unverified_num(self, key: str, value: float) -> None:
        """A number derived from values that were each verified individually.

        `impressum.gf_count` is the case: the count is over the names that
        PASSED, so the number carries its inputs' verification rather than
        needing one of its own — there is no string in a count for a substring
        check to find, and inventing one would be a guard that cannot fail.
        """
        self.emit(key, num=value, confidence=verify.VERIFIED)

    def emit(
        self,
        key: str,
        *,
        num: float | None = None,
        text: str | None = None,
        confidence: float,
    ) -> None:
        if key not in self.declared:
            raise ReconcileError(
                f"{key!r} is not in §5.5b's mapping table for this purpose. "
                f"A signal key that no declaration names is a key no rule, view "
                f"column or audit can be checked against (A2, M1.76)."
            )
        self.rows.append((key, num, text, confidence))


def _impressum_signals(
    payload: dict[str, object], page: verify.PageText, model: str
) -> tuple[_SignalWriter, list[tuple[str, str | None]]]:
    """`ImpressumExtract` → §5.5b's keys, plus the verified names for `contact`.

    **Every text field is substring-verified, which is wider than §5.5b's
    original sentence and narrower than it sounds (M1.88).** §5.5b named
    `legal_name` and `managing_directors`/`owner_name`, because those are what
    goes in a letter. A2's mapping then showed the rest have destinations too:
    `legal_form` is `qual.owner_operated` disjunct 1 at **+15**, `city` and
    `postal_code` are written to `company` and rendered in §9, and the register
    fields are §8's brief. A field that reaches a person unverified is the
    failure this backstop exists for, whichever column it arrived in.
    """
    writer = _SignalWriter(page, IMPRESSUM_KEYS)
    writer.fact("llm.impressum_extracted", model)

    for field_name, key in (
        ("legal_name", "impressum.legal_name"),
        ("legal_form", "impressum.legal_form"),
        ("postal_code", "impressum.postal_code"),
        ("city", "impressum.city"),
        ("country", "impressum.country"),
        ("register_court", "impressum.register_court"),
        ("register_number", "impressum.register_number"),
    ):
        writer.verified_text(key, _text_or_none(payload, field_name))

    # §8 keeps personal data in `contact`, and `verify`'s own note says an
    # unverified name creates no row and is written nowhere. So a director's
    # name is verified here and never becomes a signal in its own right; what
    # becomes a signal is how many of them survived.
    raw_directors = payload.get("managing_directors") or []
    directors = [
        name
        for item in raw_directors
        if isinstance(item, str) and (name := item.strip())
    ]
    verified_directors = [name for name in directors if page.contains(name)]
    # M1.46's invariant, restated where it is produced: the count is over names
    # that PASSED, so an Impressum naming nobody — or naming only people who are
    # not on the page — writes no key rather than writing 0.
    # `qual.owner_operated` disjunct 2 requires `1 <= n <= 2`, and "naming none
    # is not naming <= 2"; a written 0 would be a measurement where there is
    # none, on a rule worth +15.
    if verified_directors:
        writer.unverified_num("impressum.gf_count", float(len(verified_directors)))

    owner = _text_or_none(payload, "owner_name")
    owner_verified = owner is not None and page.contains(owner)
    if owner is not None:
        # §5.5b maps this to a 0/1 presence marker, not to the name — §10.2's
        # lever, deliberately unread by any rule. `confidence` carries the
        # verdict so migration 012's filter applies to it like anything else.
        writer.emit(
            "impressum.owner_name_present",
            num=1.0 if owner_verified else 0.0,
            text=None,
            confidence=verify.VERIFIED if owner_verified else verify.REJECTED,
        )

    contacts: list[tuple[str, str | None]] = [
        (name, "Geschäftsführer") for name in verified_directors
    ]
    if owner is not None and owner_verified:
        contacts.append((owner, "Inhaber"))
    return writer, contacts


def _homepage_signals(
    payload: dict[str, object], page: verify.PageText, model: str
) -> _SignalWriter:
    """`HomepageExtract` → §5.5b's keys.

    `agency_credit` goes to `agency.footer_credit_llm`, which **no §6 rule may
    read and no view column exposes** (A3, M1.77). Withholding the reader *is*
    the guard: §10.4's platform-vocabulary exclusion is a testable regex in
    `parsers._PLATFORM_CREDIT` and is not restatable in a prompt, so a platform
    string arriving here cannot cost a company −20 because nothing reads it.
    """
    writer = _SignalWriter(page, HOMEPAGE_KEYS)
    writer.fact("llm.homepage_extracted", model)

    writer.verified_text("offer.one_line", _text_or_none(payload, "one_line_offer"))
    writer.verified_text("offer.audience", _text_or_none(payload, "audience"))
    writer.verified_text(
        "agency.footer_credit_llm", _text_or_none(payload, "agency_credit")
    )

    raw_categories = payload.get("product_categories") or []
    categories = [
        name
        for item in raw_categories
        if isinstance(item, str) and (name := item.strip())
    ]
    if categories:
        # Each category is verified on its own; the row carries the ones that
        # passed and the count of them, so a hallucinated category cannot ride
        # into §5.5c's query text on the back of a real one.
        kept = [name for name in categories if page.contains(name)]
        if kept:
            writer.emit(
                "offer.product_categories",
                num=float(len(kept)),
                text="|".join(kept),
                confidence=verify.VERIFIED,
            )

    owner_named = payload.get("owner_named_on_site")
    writer.verified_boolean(
        "site.owner_named",
        bool(owner_named) if isinstance(owner_named, bool) else None,
        _text_or_none(payload, "owner_named_evidence"),
    )
    own_brand = payload.get("own_brand")
    writer.verified_boolean(
        "brand.own_brand",
        own_brand if isinstance(own_brand, bool) else None,
        _text_or_none(payload, "own_brand_evidence"),
    )
    return writer


#: `company` columns filled from a verified Impressum, and **fill-if-NULL only**
#: (A2). The LLM does not overwrite what `fetch` or a seed established; the one
#: place it wins on disagreement is `legal_form`, and that is resolved inside
#: `company_profile`'s COALESCE rather than by an UPDATE racing the view.
COMPANY_FILL: tuple[tuple[str, str], ...] = (
    ("impressum.legal_name", "legal_name"),
    ("impressum.postal_code", "postal_code"),
    ("impressum.city", "city"),
)


# ── the cost ledger's other half (§7 control 12, B3.2 / B3.3) ───────────


def actual_cost_usd(
    items: list[llm.BatchResultItem], *, provider: str, model: str
) -> tuple[float, llm.Usage]:
    """What the batch really cost, over the results that really arrived.

    **The first measured number this project has ever had.** `ledger.
    monthly_spend_usd` sums `run.est_cost_usd`, which until now held only
    estimates (B3.2), and `estimate_cost` is reused here rather than a second
    arithmetic being written beside it — the price table is the same table and
    the batch discount is the same discount (M1.42).

    Priced at the **declared** model rather than at the one the response names.
    They differ in practice — a response says `claude-haiku-4-5-20251001` where
    §7 control 10's table says `claude-haiku-4-5` — and `price_for` refuses an
    undeclared model on purpose (a price that was assumed is worse than a call
    that aborted). The response's exact model id is not discarded: §5.5b's two
    stage facts carry it in `value_text`, which is where a question about which
    build answered belongs.

    Requests that expired or errored contribute nothing, because they consumed
    nothing — which is what makes the reservation's release fall out of the
    arithmetic rather than needing a rule of its own (§7 control 12).
    """
    total = llm.Usage(0, 0)
    for item in items:
        if item.extraction is None:
            continue
        usage = item.extraction.usage
        total = llm.Usage(
            input_tokens=total.input_tokens + usage.input_tokens,
            output_tokens=total.output_tokens + usage.output_tokens,
            cache_creation_input_tokens=(
                total.cache_creation_input_tokens + usage.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                total.cache_read_input_tokens + usage.cache_read_input_tokens
            ),
            web_searches=total.web_searches + usage.web_searches,
        )
    estimate = llm.estimate_cost(
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        provider=provider,
        model=model,
        batch=True,
        web_searches=total.web_searches,
    )
    return estimate.total_usd, total


def _correct_the_reservation(
    conn: sqlite3.Connection, batch: BatchRow, *, actual: float
) -> float:
    """§7 control 12 / B3.2 — the estimate-to-actual correction, applied once.

    **On the SUBMITTING run** (B3.1), because that is where control 4 made the
    reservation. The reconciling run gets its own `run` row for its own
    timestamps and does not absorb the delta.

    **Only when the batch closes.** While a batch is still owed anything, its
    reservation stands at full value — over-counting the ledger, which is
    control 3's own stated preference (*"can only over-count, never
    under-count"*). Releasing a reservation for work that has not come back yet
    would be exactly the automatic release migration 014 refuses.

    **The cross-window case, stated rather than left to be discovered.** The
    window is keyed on `run.started_at` and on nothing else (M1.70), so a batch
    submitted 40 days ago and reconciled today writes its correction onto a run
    that is already outside the window — and therefore moves the current
    window's total by **zero**. Headroom over-reserved in a past window is never
    returned to this one, and spend under-reserved in a past window is never
    charged to it. That is what makes the window rolling rather than cumulative,
    and §7 control 2 already says so for the reservation; this is the same
    sentence about the correction. A runaway guard and an accounting record want
    different behaviour at the boundary, and this is the guard.
    """
    delta = actual - batch.est_cost_usd
    row = conn.execute(
        "SELECT COALESCE(est_cost_usd, 0) AS spend FROM run WHERE id = ?",
        (batch.run_id,),
    ).fetchone()
    if row is None:
        raise ReconcileError(
            f"batch {batch.id} names run {batch.run_id}, which does not exist — "
            f"B3.1 sends the correction to the submitting run and there is no "
            f"other run it may be charged to"
        )
    if float(row["spend"]) + delta < -1e-9:
        # Not defensiveness: this is only reachable if a reservation was
        # corrected twice, and a ledger that can go negative is one that can be
        # talked below a real number. §7 fails closed.
        raise ReconcileError(
            f"correcting batch {batch.id} by {delta:+.6f} would take run "
            f"{batch.run_id}'s reservation below zero (currently "
            f"{float(row['spend']):.6f}). §7 control 2's ledger is not an "
            f"accounting record and must never read less than what is owed."
        )
    conn.execute(
        "UPDATE run SET est_cost_usd = COALESCE(est_cost_usd, 0) + ? WHERE id = ?",
        (delta, batch.run_id),
    )
    return delta


# ── one batch ───────────────────────────────────────────────────────────


def _disposition(item: llm.BatchResultItem | None) -> str | None:
    """A returned item's terminal disposition, or `None` if it is still owed.

    A request with **no result at all** is `None` and keeps the batch open —
    M1.86's whole finding, and the reason this takes the stored request rather
    than iterating the results.
    """
    return None if item is None else item.outcome.value


def _write_contacts(
    conn: sqlite3.Connection,
    company_id: int,
    source_url: str,
    contacts: list[tuple[str, str | None]],
    *,
    now: str,
) -> int:
    """§10.6's `contact` row, from **verified** Impressum names only (§5.5b).

    **The whole of the GDPR shape is in the arguments.** `source_url` must be
    the Impressum URL and comes off the artifact the name was read from, in the
    same expression as everything else about that page (M1.42) — §4's column
    comment says *"must be the Impressum URL"* and a synthesised one would make
    the Art. 14 notice unanswerable. `purge_after` is `collected_at` + 12 months
    (§8), set here rather than defaulted in the schema so that the row and its
    expiry are written by the same statement.

    **An unverified name creates no row and is written nowhere**, which is
    `verify`'s own note and is the one place where §5.5b's *"written with
    `confidence = 0` for review"* does not apply: a signal is a measurement of a
    company, and a person's name that the tool does not believe is not a
    measurement of anything. It is personal data about someone who may not
    exist. The count of verified names still becomes `impressum.gf_count`, so
    the loss is visible as a smaller number rather than as silence.

    There is no unique index on `contact` in §4, so re-running would duplicate.
    `NOT EXISTS` on (company, name, role) is the dedupe, in the same spirit as
    the M1.5 idiom: narrow, and it swallows nothing else.
    """
    written = 0
    for full_name, role in contacts:
        existing = conn.execute(
            "SELECT 1 FROM contact WHERE company_id = ? AND full_name = ? "
            "AND role IS ?",
            (company_id, full_name, role),
        ).fetchone()
        if existing is not None:
            continue
        conn.execute(
            "INSERT INTO contact (company_id, full_name, role, source_url, "
            "collected_at, purge_after) VALUES (?,?,?,?,?, "
            "datetime(?, '+12 months'))",
            (company_id, full_name, role, source_url, now, now),
        )
        written += 1
    return written


def _fill_company(
    conn: sqlite3.Connection,
    company_id: int,
    writer: _SignalWriter,
) -> None:
    """A2's *fill-if-NULL* destinations on `company`, and only where verified.

    `COALESCE`-on-write rather than an overwrite: the LLM does not replace what
    `fetch` or a seed established. The one field where the LLM wins on
    disagreement is `legal_form`, and that is resolved inside `company_profile`'s
    own `COALESCE` (migration 012) rather than by an UPDATE racing the view.
    """
    values = {key: (num, text, conf) for key, num, text, conf in writer.rows}
    for key, column in COMPANY_FILL:
        if key not in values:
            continue
        _num, text, confidence = values[key]
        if text is None or confidence != verify.VERIFIED:
            continue
        conn.execute(
            f"UPDATE company SET {column} = ? WHERE id = ? AND {column} IS NULL",
            (text, company_id),
        )


def _write_signals(
    conn: sqlite3.Connection,
    writer: _SignalWriter,
    *,
    company_id: int,
    run_id: int,
    evidence_url: str,
    artifact_id: int,
    now: str,
) -> int:
    """§4's M1.5 idiom, with `method='llm'` and a real confidence.

    **`run_id` is the SUBMITTING run's** (B4), not the reconciling one. Under a
    fresh id the unique index could not dedupe, so a `reconcile` that wrote 40
    of 60 companies and died would have the next invocation re-insert all 60 —
    and *"safe to run repeatedly"* would be a claim rather than a property. It
    also keeps the reserved spend (§7 control 4) and the resulting evidence on
    one `run` row.

    `evidence_url` and `artifact_id` come off the artifact the text was read
    from, **in one expression** (M1.42), so they cannot name different
    documents and neither can name a document the value did not come from.

    `ON CONFLICT ... DO NOTHING` on the uniqueness target only — never `INSERT
    OR IGNORE`, which would also swallow a CHECK violation on `method` and turn
    a typo into a signal that silently never existed.
    """
    written = 0
    for key, num, text, confidence in writer.rows:
        conn.execute(
            """
            INSERT INTO signal
                (company_id, run_id, key, value_num, value_text, value_date,
                 method, confidence, evidence_url, artifact_id, observed_at)
            VALUES (?,?,?,?,?,NULL,'llm',?,?,?,?)
            ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING
            """,
            (
                company_id,
                run_id,
                key,
                num,
                text,
                confidence,
                evidence_url,
                artifact_id,
                now,
            ),
        )
        written += 1
    return written


def _settle(
    conn: sqlite3.Connection, request: RequestRow, outcome: str, *, now: str, note: str
) -> None:
    """Record one request's terminal disposition. Idempotent by the unique index."""
    conn.execute(
        "UPDATE llm_batch_request SET outcome = ?, error_message = ?, "
        "settled_at = ? WHERE id = ? AND outcome IS NULL",
        (outcome, note or None, now, request.id),
    )


def reconcile_batch(
    conn: sqlite3.Connection,
    provider: llm.LLMProvider,
    root: Path,
    batch: BatchRow,
) -> BatchReport:
    """Poll one batch, verify what came back, and write it — all or nothing.

    **The whole per-batch effect is one transaction.** Signals, contacts,
    dispositions, the batch's status and the ledger correction commit together
    or not at all, for M1.72's reason one stage further on: a partial commit
    here would leave a batch marked `reconciled` with half its companies
    unwritten, and *"safe to run repeatedly"* would then re-poll a batch that
    says it is done.

    **`expired` and `balance_exhausted` are ordinary, not exotic** (§5.6, §7
    control 11), and the reservation's fate in each falls out of the arithmetic
    rather than needing a case:

    * **expired members** — the batch closes as `expired`. Those requests
      consumed nothing, so they contribute nothing to the measured actual, and
      the correction hands their share of the reservation back to the submitting
      run. Re-submitting them is a **new batch** with its own reservation, which
      is exactly what §5.6 says: *"re-submission is new spend that §7 reserves
      like any other."*
    * **a short result set** — some request has no disposition at all. The batch
      stays `completed`, **no correction is applied**, and the full reservation
      stands. The requests still owed are named in the report (M1.86).
    * **balance exhausted** — the batch closes as `balance_exhausted`, never as
      `failed`, because *"the provider failed"* and *"we ran out of money"* need
      different operator responses (§7 control 11). Whatever succeeded before
      the key ran dry is written and paid for, and the correction releases the
      rest.
    """
    if batch.provider_batch_id is None:
        raise ReconcileError(
            f"batch {batch.id} has no provider id and status {batch.status!r}; "
            f"only a 'reserved' batch may lack one (migration 014)"
        )
    requests = requests_of(conn, batch.id)
    if not requests:
        raise ReconcileError(
            f"batch {batch.id} has no stored requests. §5.6 fact 2 is a rule "
            f"about a set, and `request_count` = {batch.request_count} is a "
            f"number: without the set this cannot tell 'every request settled' "
            f"from 'nothing came back' (M1.86)."
        )

    result = provider.poll_batch(batch.provider_batch_id)
    by_id = llm.index_by_custom_id(result.items)
    status = llm.resolve_batch_status(
        result.items, expected=[r.custom_id for r in requests]
    )

    report = BatchReport(
        batch_id=batch.id,
        provider_batch_id=batch.provider_batch_id,
        purpose=batch.purpose,
        status_before=batch.status,
        status_after=status.value,
        est_cost_usd=batch.est_cost_usd,
    )
    if status is llm.BatchStatus.SUBMITTED:
        report.status_after = batch.status
        report.note = "still processing"
        report.still_owed = tuple(r.custom_id for r in requests if r.outcome is None)
        return report

    now = _utc_now()
    # The batch's running total, over everything that has ever come back. It is
    # written to `llm_batch.actual_cost_usd`, and the correction is taken
    # against it once, at close — so polling twice moves the ledger by zero.
    actual, _ = actual_cost_usd(
        result.items, provider=provider.name, model=provider.model
    )
    # Tokens are ACCUMULATED onto the run, which is a running sum rather than a
    # total, so only what settles in THIS pass may be added. A batch polled
    # twice would otherwise count its first eight results' tokens twice — the
    # same double-count M1.69 found one table over.
    newly_settled: list[llm.BatchResultItem] = []

    conn.execute("BEGIN IMMEDIATE")
    try:
        for request in requests:
            if request.outcome is not None:
                report.dispositions[request.outcome] = (
                    report.dispositions.get(request.outcome, 0) + 1
                )
                continue
            item = by_id.get(request.custom_id)
            disposition = _disposition(item)
            if disposition is None:
                continue  # still owed; nothing to record and the batch stays open
            note = "" if item is None else item.error_message
            if item is not None and item.extraction is not None:
                disposition, note = _apply_extraction(
                    conn,
                    root,
                    batch,
                    request,
                    item.extraction,
                    report=report,
                    now=now,
                )
            _settle(conn, request, disposition, now=now, note=note)
            if item is not None:
                newly_settled.append(item)
            report.dispositions[disposition] = (
                report.dispositions.get(disposition, 0) + 1
            )

        settled = {
            r.custom_id
            for r in requests_of(conn, batch.id)  # re-read: dispositions just landed
            if r.outcome is not None
        }
        report.still_owed = tuple(
            r.custom_id for r in requests if r.custom_id not in settled
        )
        report.resubmittable = llm.resubmittable(result.items)

        _, new_usage = actual_cost_usd(
            newly_settled, provider=provider.name, model=provider.model
        )
        _record_usage(conn, batch.run_id, new_usage)
        report.actual_cost_usd = actual
        if report.status_after in TERMINAL_STATUSES:
            report.ledger_delta_usd = _correct_the_reservation(
                conn, batch, actual=actual
            )
            conn.execute(
                "UPDATE llm_batch SET status = ?, actual_cost_usd = ?, "
                "reconciled_at = ? WHERE id = ?",
                (report.status_after, actual, now, batch.id),
            )
        else:
            # Still owed: the actual is recorded, the reservation is NOT
            # released, and `reconciled_at` stays NULL so a later poll can close
            # the batch and correct it exactly once.
            conn.execute(
                "UPDATE llm_batch SET status = ?, actual_cost_usd = ? WHERE id = ?",
                (report.status_after, actual, batch.id),
            )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return report


def _apply_extraction(
    conn: sqlite3.Connection,
    root: Path,
    batch: BatchRow,
    request: RequestRow,
    extraction: llm.Extraction,
    *,
    report: BatchReport,
    now: str,
) -> tuple[str, str]:
    """Verify one succeeded result against the sent text and write it.

    Returns the request's disposition, which is `succeeded` unless the sent text
    could not be reproduced — see `sent_text_for` and migration 015 for why that
    is terminal and why it errs low.
    """
    kind, company_id, artifact_id = extract_p2.parse_custom_id(request.custom_id)
    if kind != batch.purpose or company_id != request.company_id:
        # `custom_id` is the only thing tying a returned legal name to a company
        # (M1.51), and substring verification cannot catch a mis-key because the
        # value really is on the page it came from. So the key is checked
        # against the row that stored it, which is the one guard that can.
        raise ReconcileError(
            f"custom_id {request.custom_id!r} does not agree with the batch row "
            f"that stored it (purpose {batch.purpose!r}, company "
            f"{request.company_id}) — refusing to attribute an extraction to a "
            f"company it may not belong to (M1.17's failure, M1.51's cause)"
        )

    sent = sent_text_for(conn, root, request)
    if sent is None:
        return TEXT_UNREPRODUCIBLE, (
            "the text sent to the model could not be reproduced from the stored "
            "artifact; no value was written (M1.87)"
        )

    row = conn.execute(
        "SELECT url FROM artifact WHERE id = ?", (artifact_id,)
    ).fetchone()
    if row is None:
        return TEXT_UNREPRODUCIBLE, f"artifact {artifact_id} is gone"
    evidence_url = str(row["url"])

    page = verify.PageText(sent)
    contacts: list[tuple[str, str | None]] = []
    if batch.purpose == "impressum":
        writer, contacts = _impressum_signals(
            extraction.payload, page, extraction.model
        )
    else:
        writer = _homepage_signals(extraction.payload, page, extraction.model)

    report.signals_written += _write_signals(
        conn,
        writer,
        company_id=company_id,
        run_id=batch.run_id,
        evidence_url=evidence_url,
        artifact_id=artifact_id,
        now=now,
    )
    _fill_company(conn, company_id, writer)
    if contacts:
        report.contacts_written += _write_contacts(
            conn, company_id, evidence_url, contacts, now=now
        )
    return (llm.RequestOutcome.SUCCEEDED.value, "")


def _record_usage(conn: sqlite3.Connection, run_id: int, usage: llm.Usage) -> None:
    """§7 control 8 and §4's token columns, on the **submitting** run (B3.1)."""
    conn.execute(
        "UPDATE run SET llm_input_tokens = COALESCE(llm_input_tokens,0) + ?, "
        "llm_output_tokens = COALESCE(llm_output_tokens,0) + ?, "
        "web_searches = COALESCE(web_searches,0) + ? WHERE id = ?",
        (usage.input_tokens, usage.output_tokens, usage.web_searches, run_id),
    )


def run(
    conn: sqlite3.Connection, provider: llm.LLMProvider, root: Path
) -> ReconcileResult:
    """§5.6's stage. **Finds its work in the database and nowhere else.**

    The reconciling run gets its own `run` row with `stage='reconcile'`, for
    started/finished timestamps and batches polled — *and it does not own the
    signals* (B4) or absorb the cost correction (B3.1). Both belong to the run
    that made the reservation.

    Safe to run repeatedly: a batch that closed is no longer in `open_batches`,
    a request that settled is skipped by `outcome IS NOT NULL`, and a signal
    that exists conflicts into `DO NOTHING`. Three independent reasons, because
    idempotency asserted once is a claim.
    """
    cursor = conn.execute(
        "INSERT INTO run (started_at, stage) VALUES (?, 'reconcile')", (_utc_now(),)
    )
    run_id = int(cursor.lastrowid or 0)
    result = ReconcileResult(run_id=run_id)
    result.reserved_unknown = reserved_batches(conn)
    polled = open_batches(conn)
    for batch in polled:
        result.batches.append(reconcile_batch(conn, provider, root, batch))
    conn.execute(
        "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
        # Requests polled, counted BEFORE the loop: counting after would report
        # what is still open rather than what this run looked at.
        (_utc_now(), sum(b.request_count for b in polled), run_id),
    )
    conn.commit()
    return result


__all__ = [
    "CONTACT_ONLY_FIELDS",
    "HOMEPAGE_KEYS",
    "IMPRESSUM_KEYS",
    "OPEN_STATUSES",
    "TERMINAL_STATUSES",
    "TEXT_UNREPRODUCIBLE",
    "BatchReport",
    "BatchRow",
    "ReconcileError",
    "ReconcileResult",
    "RequestRow",
    "actual_cost_usd",
    "open_batches",
    "reconcile_batch",
    "requests_of",
    "reserved_batches",
    "run",
    "sent_text_for",
]
