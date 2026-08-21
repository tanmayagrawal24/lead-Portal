"""§5.5b's paid extraction — the entry point, gated, with nothing spent (M1.83).

**What this is, and what it deliberately is not.** 9a builds the *seam*: the
selection of what to send, the cleaning and capping of the text, the batch
requests, and the ledger gate they cannot get past without a clearance. It stops
at the point where §7 control 4's reservation would be made, because that
reservation has to make its two writes in one transaction (M1.72) and that needs
the caller 9b builds. **Nothing here contacts a provider, and every test drives
a fake.**

**Why the gate is the point of landing this now.** `llm.assert_ledger_guarded`
has been correct and unexercised since Unit 7: it fails the **import** on a
callable in `portal/llm.py` that is classified as neither paid nor free, and on
a paid one with no `@requires_ledger_clearance`. Until now no caller existed to
prove it engages on a real path. `submit` below is registered paid and
decorated, so it cannot run without a `LedgerClearance`, which only
`ledger.check_ceiling` constructs — and the negative control for this unit
removes the registration and watches the import fail.

**The client is injected (Unit 2's shape).** `llm.LLMProvider` is a Protocol, so
this module names no vendor, imports no SDK, and every test passes a fake. That
is what makes a paid stage testable under the CI M1.65 built, which forbids
`ANTHROPIC_API_KEY` outright.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from portal import impressum_audit, ledger, llm, parsers
from portal.ledger import LedgerClearance
from portal.urls import authority_of

#: §5.5b, verbatim. The instruction is part of the specification, not a prompt
#: detail: hallucinated Impressum data is the worst failure this stage has.
PROMPT_DISCIPLINE = (
    "Return null for any field not present on the page. Do not infer, do not "
    "guess, do not fill from general knowledge. If the page is not an "
    "Impressum, return all nulls."
)

#: §5.5b: *"cap at 60 KB"*, and it is called the primary defence against
#: unbounded token spend rather than an optimisation. Measured in bytes of the
#: cleaned text, not characters, because the cap exists to bound what crosses
#: the wire.
INPUT_CAP_BYTES = 60 * 1024

#: §5.5b: pages over the cap are truncated **from the end** — Impressum content
#: is near the top of an Impressum page, so the tail is what can be lost.
TRUNCATION_MARKER = "\n[… gekürzt auf 60 KB, §5.5b]"

#: Haiku 4.5 caps output at 64K (M1.50), and `llm.reserve_batch` refuses a
#: request asking for more. These extractions are a few hundred tokens of JSON;
#: the number is a bound for the reservation, not a target.
MAX_OUTPUT_TOKENS = 2048


@dataclass(frozen=True)
class Prepared:
    """One company's extraction input, with the text that will be sent.

    `sent_text` is carried on the object rather than recomputed, so that
    `verify.PageText` is built from **the string that was sent** and cannot be
    handed a different rendering of the same page (M1.43).
    """

    company_id: int
    domain: str
    artifact_id: int
    url: str
    kind: str
    sent_text: str
    truncated: bool

    @property
    def sent_sha256(self) -> str:
        """The digest `reconcile` checks its reconstruction against (M1.87).

        A property rather than a stored field, so the hash and the string it
        hashes cannot be set from two places — the same reason `_write` takes an
        artifact rather than a URL (M1.42).
        """
        return sha256_of(self.sent_text)


def sha256_of(sent_text: str) -> str:
    """The digest of a sent string, in **one expression** (M1.87).

    Written once and called from both sides — the reservation stores it, and
    `reconcile` recomputes it over its reconstruction and compares. Two
    expressions for one hash is M1.42's shape on the guard that exists to catch
    M1.43's, which would be a poor place for it.

    UTF-8 of the exact string handed to the provider. Not of the artifact bytes:
    the artifact is the *source*, and what §5.5b verifies against is the cleaned
    text that was actually sent.
    """
    return hashlib.sha256(sent_text.encode("utf-8")).hexdigest()


def clean(html: str) -> tuple[str, bool]:
    """§5.5b's input preparation, and M1.78's ruling on which text is sent.

    **Cleaned visible text, not raw HTML.** §5.5b's own requirement — strip
    `<script>`, `<style>`, `<svg>`, `<nav>`, comments; reduce to text — *is*
    `parsers.visible_text`'s contract, so this is one expression for it rather
    than a second one that could drift. Two consequences, both wanted: the model
    sees what a human reading the page sees, which is the only footing on which
    a substring check against "the cleaned page text" means anything; and
    §10.2's `Inh.`/`Inhaber` base rate is fixed at the visible-text reading
    (1 of 11) rather than the raw-HTML one, whose extra hits are tokens inside
    `<script>` blocks that `visible_text` decomposes on purpose.
    """
    text = parsers.visible_text(html)
    encoded = text.encode("utf-8")
    if len(encoded) <= INPUT_CAP_BYTES:
        return text, False
    kept = encoded[:INPUT_CAP_BYTES].decode("utf-8", errors="ignore")
    return kept + TRUNCATION_MARKER, True


def impressum_schema() -> dict[str, object]:
    """`ImpressumExtract` (§5.5b) as a JSON schema, made strict.

    Written out rather than derived from the Pydantic model at import time: the
    contract the provider is held to is the thing under review, and generating
    it means reviewing a generator instead. `llm.strict_json_schema` then adds
    `required` and `additionalProperties: false` for every object, so a field
    the model omits is an error rather than a silent `None` (§5.5b's null is an
    explicit null).
    """
    string = {"type": ["string", "null"]}
    return llm.strict_json_schema(
        {
            "type": "object",
            "properties": {
                "legal_name": string,
                "legal_form": string,
                "street": string,
                "postal_code": string,
                "city": string,
                "country": string,
                "managing_directors": {"type": "array", "items": {"type": "string"}},
                "owner_name": string,
                "register_court": string,
                "register_number": string,
                "vat_id": string,
                "email": string,
                "phone": string,
            },
        }
    )


def homepage_schema() -> dict[str, object]:
    """`HomepageExtract` (§5.5b), including M1.49's two `_evidence` spans.

    The spans are what give the two booleans anything to verify at all — a
    boolean has no string in it for a substring check to find — and §5.5b states
    the limit of what they buy: they prove the model did not fabricate its
    evidence, not that it reasoned correctly from it.
    """
    string = {"type": ["string", "null"]}
    return llm.strict_json_schema(
        {
            "type": "object",
            "properties": {
                "one_line_offer": string,
                "product_categories": {"type": "array", "items": {"type": "string"}},
                "audience": string,
                "owner_named_on_site": {"type": "boolean"},
                "owner_named_evidence": string,
                "own_brand": {"type": ["boolean", "null"]},
                "own_brand_evidence": string,
                "agency_credit": string,
            },
        }
    )


_SYSTEM = {
    "impressum": (
        "Du liest eine deutschsprachige Impressum-Seite eines Onlineshops und "
        "gibst die gesetzlich geforderten Angaben strukturiert zurück. "
        + PROMPT_DISCIPLINE
    ),
    "homepage": (
        "Du liest die Startseite eines deutschsprachigen Onlineshops und gibst "
        "zurück, was der Shop verkauft und wie er sich darstellt. Belege jede "
        "Ja/Nein-Angabe mit der wörtlich zitierten Stelle der Seite, an der du "
        "sie abliest. " + PROMPT_DISCIPLINE
    ),
}


#: The two §5.5b extractions, and the `llm_batch.purpose` vocabulary. Declared
#: rather than inferred, so a third purpose has to be added here and in the
#: schema's own comment rather than appearing as a string in one call site.
PURPOSES: tuple[str, ...] = ("impressum", "homepage")

# The newest 200-with-body homepage per company. The Impressum side has
# `impressum_audit.select_inputs` — A2 §7 as amended by M1.43 and M1.44 — and
# this is deliberately NOT a second copy of it: the two exclusions that make
# that selection what it is do not transfer. The homepage-hash exclusion is
# about an Impressum that is really a homepage, which is not a statement about
# homepages; and there is no "which of several candidates" question here,
# because a company has one homepage by construction (`fetch` derives it from
# the domain). What DOES transfer is M1.75's robots check, and that is applied
# through `impressum_audit.policy_for` rather than re-derived — one expression
# for "whose robots.txt governs this body".
_HOMEPAGE_SQL = """
SELECT c.id AS company_id, c.domain, a.id AS artifact_id, a.url, a.body_path
FROM company c
JOIN artifact a ON a.company_id = c.id
WHERE a.kind = 'homepage'
  AND a.http_status = 200
  AND a.body_path IS NOT NULL
ORDER BY c.domain, a.id DESC
"""


def _homepage_inputs(
    conn: sqlite3.Connection, root: Path
) -> tuple[list[impressum_audit.Input], list[impressum_audit.Skipped]]:
    """The homepage each company is measured on, plus the ones with none.

    Same shape as `select_inputs` and the same two return values, because "no
    usable homepage" is a finding for the same reason "no usable Impressum" is:
    a company dropped silently is indistinguishable from a company that was
    never considered, and §5.4's gate has already decided this one is worth
    paying for.
    """
    chosen: dict[int, impressum_audit.Input] = {}
    rejected: dict[int, str] = {}
    policies: dict[tuple[int, str], object] = {}
    for row in conn.execute(_HOMEPAGE_SQL):
        company_id = int(row["company_id"])
        if company_id in chosen:
            continue  # ORDER BY a.id DESC — the first survivor is the newest
        url = str(row["url"])
        key = (company_id, authority_of(url))
        if key not in policies:
            policies[key] = impressum_audit.policy_for(conn, company_id, url, root)
        policy = policies[key]
        if not policy.allows(url):  # type: ignore[attr-defined]
            unavailable = policy.unavailable  # type: ignore[attr-defined]
            rejected.setdefault(
                company_id,
                f"homepage {url} not allowed by robots ({unavailable})"
                if unavailable is not None
                else f"homepage {url} disallowed by robots.txt",
            )
            continue
        chosen[company_id] = impressum_audit.Input(
            company_id=company_id,
            domain=str(row["domain"]),
            artifact_id=int(row["artifact_id"]),
            url=url,
            body_path=str(row["body_path"]),
        )
    skipped = [
        impressum_audit.Skipped(domain=domain, reason=reason)
        for company_id, reason in sorted(rejected.items())
        if company_id not in chosen
        and (domain := _domain_of(conn, company_id)) is not None
    ]
    return list(chosen.values()), skipped


def _domain_of(conn: sqlite3.Connection, company_id: int) -> str | None:
    row = conn.execute(
        "SELECT domain FROM company WHERE id = ?", (company_id,)
    ).fetchone()
    return str(row["domain"]) if row is not None else None


def prepare(
    conn: sqlite3.Connection, root: Path, *, purpose: str = "impressum"
) -> tuple[list[Prepared], list[impressum_audit.Skipped]]:
    """What would be sent, per company, for one purpose. **Free, makes no request.**

    The Impressum selection is `impressum_audit.select_inputs` — A2 §7 as
    amended by M1.43 and M1.44 — reused rather than re-expressed. It is already
    the project's single expression for *which stored Impressum a company is
    measured on*, it already excludes an artifact whose content hash matches
    that company's homepage (the `snocks.com` row that is the homepage filed as
    an Impressum) and any body the company's own origin's robots.txt disallows,
    and re-deriving the same rule here is exactly the second expression M1.42 is
    about. A company with no usable Impressum is returned as `Skipped`, not
    dropped: "no usable Impressum" is a finding.

    **`purpose` is a parameter and not two functions**, because `llm_batch`
    carries `purpose` as data and a batch is one purpose's worth of requests
    (§4). One batch of Impressum requests and one of homepage requests are two
    reservations, two rows, and two independent fates — which is what makes an
    expired homepage batch cost the Impressum extraction nothing.
    """
    if purpose not in PURPOSES:
        raise ValueError(
            f"unknown extraction purpose {purpose!r}; §4 declares {PURPOSES}"
        )
    if purpose == "impressum":
        inputs, skipped = impressum_audit.select_inputs(conn, root)
    else:
        inputs, skipped = _homepage_inputs(conn, root)
    prepared: list[Prepared] = []
    for chosen in inputs:
        body = (root / chosen.body_path).read_text(encoding="utf-8", errors="replace")
        sent, truncated = clean(body)
        prepared.append(
            Prepared(
                company_id=chosen.company_id,
                domain=chosen.domain,
                artifact_id=chosen.artifact_id,
                url=chosen.url,
                kind=purpose,
                sent_text=sent,
                truncated=truncated,
            )
        )
    return prepared, skipped


def build_requests(prepared: list[Prepared]) -> list[llm.BatchRequest]:
    """One `BatchRequest` per prepared page. **Free.**

    `custom_id` carries the company id and the artifact id, because batch
    results come back in **arbitrary order** (M1.51) and this is the only thing
    tying a returned legal name to the company it was read for. Reading result
    *n* as request *n* attributes a name and a set of directors to the wrong
    company — M1.17's failure with a new cause — and **substring verification
    does not catch it**, because the values are genuinely present on the page
    they came from. The artifact id rides along so the signal's `evidence_url`
    and `artifact_id` come from the row the text was read off (M1.42).
    """
    schema = {"impressum": impressum_schema(), "homepage": homepage_schema()}
    return [
        llm.BatchRequest(
            custom_id=f"{page.kind}:{page.company_id}:{page.artifact_id}",
            system=_SYSTEM[page.kind],
            user_text=page.sent_text,
            json_schema=schema[page.kind],
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        for page in prepared
    ]


def parse_custom_id(custom_id: str) -> tuple[str, int, int]:
    """The inverse of `build_requests`' key, in one place so the two cannot
    disagree about what a `custom_id` means."""
    kind, company_id, artifact_id = custom_id.split(":")
    return kind, int(company_id), int(artifact_id)


@llm.requires_ledger_clearance
def submit(
    provider: llm.LLMProvider,
    requests: list[llm.BatchRequest],
    *,
    clearance: LedgerClearance,
) -> str:
    """Hand the batch to the injected provider. **The one paid surface here.**

    It is registered in `PAID_SURFACES` below and decorated above, so
    `assert_ledger_guarded` fails the **import** if either is ever removed, and
    the decorator refuses the call without a `LedgerClearance` — which only
    `ledger.check_ceiling` constructs, after reading §7 control 2's rolling
    30-day window against the ceiling.

    **9a stops here and 9b continues.** What is missing is §7 control 4's
    reservation: `llm.reserve_batch` measures the tokens and prices them, and
    the resulting estimate must be written to `llm_batch.est_cost_usd` and
    `run.est_cost_usd` **in one transaction** (M1.72), or a crash between the
    two leaves the batch on the books and the ledger blind to it. That is the
    one path in §7 that fails open, it needs the caller, and building it half
    way would be worse than not building it: a reservation that usually commits
    both writes is a ledger that is usually right.
    """
    return provider.submit_batch(requests, clearance=clearance)


@dataclass(frozen=True)
class Reservation:
    """What §7 control 4 committed, and where the row that records it lives.

    `provider_batch_id` is `None` where the submit call's outcome is unknown —
    the batch row is `reserved`, the money is counted, and only a human can say
    what happened. See migration 014.
    """

    batch_id: int
    provider_batch_id: str | None
    estimate: llm.CostEstimate
    request_count: int

    @property
    def submitted(self) -> bool:
        return self.provider_batch_id is not None


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _commit_reservation(
    conn: sqlite3.Connection,
    prepared: list[Prepared],
    requests: list[llm.BatchRequest],
    *,
    run_id: int,
    purpose: str,
    total_usd: float,
    now: str,
    clearance: LedgerClearance,
    run_ceiling_usd: float = ledger.RUN_CEILING_USD,
) -> int:
    """**M1.72: §7 control 4's two writes, committed together or not at all.**

    Control 4 reserves into `llm_batch.est_cost_usd` **and** `run.est_cost_usd`.
    Control 2's ledger sums `run` alone (M1.69 — summing both halves the
    ceiling). So a crash between the two leaves the batch on the books and the
    ledger unaware of it, which **under-counts** the rolling total: the one
    fail-OPEN path in a section where every other failure is biased to abort.
    `db.connect` opens in autocommit, so the transaction is explicit and there
    is nothing implicit to rely on.

    `BEGIN IMMEDIATE` rather than a deferred `BEGIN`: the write lock is taken at
    the start rather than at the first write, so a second writer cannot arrive
    between the two statements and turn the rollback into a busy error.

    The request rows go inside the same transaction, and that is not tidiness.
    Migration 015's whole argument is that §5.6's *"every one of its requests"*
    is a question about a SET; a batch row committed without its set would leave
    `reconcile` unable to ask it, which is the defect 015 closes re-created one
    layer down.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        batch_id = _write_batch_row(
            conn,
            prepared,
            run_id=run_id,
            purpose=purpose,
            total_usd=total_usd,
            now=now,
            request_count=len(requests),
        )
        _charge_run(
            conn,
            run_id=run_id,
            total_usd=total_usd,
            clearance=clearance,
            run_ceiling_usd=run_ceiling_usd,
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return batch_id


def _write_batch_row(
    conn: sqlite3.Connection,
    prepared: list[Prepared],
    *,
    run_id: int,
    purpose: str,
    total_usd: float,
    now: str,
    request_count: int,
) -> int:
    """Half one of M1.72's pair: the batch and the set of requests in it."""
    cursor = conn.execute(
        "INSERT INTO llm_batch (provider_batch_id, run_id, purpose, "
        "request_count, est_cost_usd, status, reserved_at) "
        "VALUES (NULL, ?, ?, ?, ?, 'reserved', ?)",
        (run_id, purpose, request_count, total_usd, now),
    )
    batch_id = int(cursor.lastrowid or 0)
    for page in prepared:
        conn.execute(
            "INSERT INTO llm_batch_request (batch_id, custom_id, company_id, "
            "artifact_id, sent_text_sha256, sent_bytes) VALUES (?,?,?,?,?,?)",
            (
                batch_id,
                f"{page.kind}:{page.company_id}:{page.artifact_id}",
                page.company_id,
                page.artifact_id,
                page.sent_sha256,
                len(page.sent_text.encode("utf-8")),
            ),
        )
    return batch_id


def _charge_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    total_usd: float,
    clearance: LedgerClearance,
    run_ceiling_usd: float = ledger.RUN_CEILING_USD,
) -> None:
    """Half two of M1.72's pair: **the ledger**, which is the half control 2 reads.

    A separate function from `_write_batch_row` for one reason and it is not
    style: the negative control for M1.72 has to fail *between* the two writes,
    and a test that can only fail before or after the pair proves nothing about
    whether they commit together.

    **§7 control 3 is enforced here because this is the only place a reservation
    reaches `run.est_cost_usd`.** Putting the check at the single write rather
    than at the caller is what makes it unbypassable: a future second caller
    gets the ceiling by construction instead of by remembering. `ledger.
    charge_run` raises `RunCeilingExceeded` before writing anything, and this
    runs inside `_commit_reservation`'s `BEGIN IMMEDIATE`, so the refusal rolls
    back the batch row with it — **no batch on the books, no money reserved,
    nothing submitted.**
    """
    ledger.charge_run(
        conn,
        run_id=run_id,
        usd=total_usd,
        clearance=clearance,
        ceiling_usd=run_ceiling_usd,
    )


@llm.requires_ledger_clearance
def reserve_and_submit(
    conn: sqlite3.Connection,
    provider: llm.LLMProvider,
    prepared: list[Prepared],
    *,
    run_id: int,
    purpose: str,
    clearance: LedgerClearance,
    run_ceiling_usd: float = ledger.RUN_CEILING_USD,
) -> Reservation:
    """§7 control 4 end to end: measure, reserve, submit, record. **Paid.**

    **The order is the specification's, taken literally.** Control 4 says the
    reservation is made *"before the submit call returns"*, and it means it: a
    submitted batch is committed spend regardless of whether the process
    survives to read the result, so the ledger must already know about it when
    `create` is called. Everything about migration 014's `reserved` status
    follows from that one word.

        1.  `llm.reserve_batch` — `count_tokens` for the model actually being
            called, priced off the dated table. Free, and it can fail: a failed
            count **aborts** rather than falling back to an estimate (M1.52),
            because a fallback is how an unmeasured number enters the ledger
            looking measured.
        2.  **one transaction** — the batch row, its request set, and the run's
            reservation (M1.72). Nothing is spent yet.
        3.  `submit` — the money is gone the moment this returns.
        4.  the provider id and `submitted_at`, recorded.

    **A crash between 2 and 4 leaves `status = 'reserved'` with no provider id,
    and that state is read as *the money is gone*.** It over-counts §7 control
    2, which is control 3's own stated preference: *"can only over-count, never
    under-count — the failure mode is a conservatively aborted run"*. It is not
    released automatically, ever. Only a measured actual corrects a reservation
    (§7 control 12).

    **This is not reachable from the CLI.** `portal extract-p2` still exits 2
    without `--dry-run`, and that stays true until 9c is authorised in writing.
    The function exists, is tested against a fake provider, and has no caller
    that can spend — which is the same order the ledger itself shipped in
    (M1.69–M1.71): the mechanism before the spend, so the spend is written
    against its presence.
    """
    if purpose not in PURPOSES:
        raise ValueError(
            f"unknown extraction purpose {purpose!r}; §4 declares {PURPOSES}"
        )
    if not prepared:
        raise ValueError(
            "refusing to reserve an empty batch: §7 control 4 reserves at "
            "submission time, and a reservation with nothing in it is a ledger "
            "entry for work that was never requested"
        )
    if any(page.kind != purpose for page in prepared):
        raise ValueError(
            f"every page in a {purpose!r} batch must be that purpose; "
            f"`llm_batch.purpose` is one value for the whole row (§4)"
        )

    requests = build_requests(prepared)
    estimate = llm.reserve_batch(
        requests,
        provider=provider.name,
        model=provider.model,
        count_tokens=provider.token_counter(),
        clearance=clearance,
    )

    now = _utc_now()
    batch_id = _commit_reservation(
        conn,
        prepared,
        requests,
        run_id=run_id,
        purpose=purpose,
        total_usd=estimate.total_usd,
        now=now,
        clearance=clearance,
        run_ceiling_usd=run_ceiling_usd,
    )

    provider_batch_id = submit(provider, requests, clearance=clearance)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE llm_batch SET provider_batch_id = ?, status = 'submitted', "
            "submitted_at = ? WHERE id = ?",
            (provider_batch_id, _utc_now(), batch_id),
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    return Reservation(
        batch_id=batch_id,
        provider_batch_id=provider_batch_id,
        estimate=estimate,
        request_count=len(requests),
    )


#: §7 control 2's classification, in `assert_ledger_guarded`'s shape. The free
#: list is written out longhand for the reason the assertion exists: the check
#: that matters is the third one, the new paid path nobody classified, and it
#: only works if every callable is named somewhere.
PAID_SURFACES: tuple[str, ...] = ("reserve_and_submit", "submit")
FREE_SURFACES: tuple[str, ...] = (
    "build_requests",
    "clean",
    "homepage_schema",
    "impressum_schema",
    "parse_custom_id",
    "prepare",
    "sha256_of",
)

llm.assert_ledger_guarded(
    sys.modules[__name__],
    paid=PAID_SURFACES,
    free=FREE_SURFACES,
    where="portal.extract_p2",
)


__all__ = [
    "FREE_SURFACES",
    "INPUT_CAP_BYTES",
    "MAX_OUTPUT_TOKENS",
    "PAID_SURFACES",
    "PROMPT_DISCIPLINE",
    "PURPOSES",
    "Prepared",
    "Reservation",
    "build_requests",
    "clean",
    "homepage_schema",
    "impressum_schema",
    "parse_custom_id",
    "prepare",
    "reserve_and_submit",
    "sha256_of",
    "submit",
]
