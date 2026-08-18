"""The provider-agnostic LLM layer (proposal v2 §4; build steps 1–3).

**This module has no caller yet, and that is the point.** Every LLM call site in
this spec belongs to M5, which is not built. The proposal's build order said step
1 would be "extracted from existing code" with "existing tests prove it" — there
is no existing code to extract, so the safety net named there does not exist and
this file carries its own tests instead. Building it before M5 is still the right
order: M5 writes the call sites, and writing them behind this interface is
cheaper than retrofitting a week-old implementation.

Three things live here, and they are here rather than in the provider because
they are **not** the provider's opinion:

1. **Prices, as dated data** (§7 control 10, M1.52). A price without an as-of
   date is a constant that was true once.
2. **Per-model limits, as declared data** (M1.50). Haiku 4.5's parameter surface
   is not the common one on three separate axes, and an interface that treats
   the common one as universal is wrong at the first call.
3. **The failure taxonomy** (§7 control 11, M1.51, M1.53) — including the two
   places a prepaid balance failure can surface, because which one is real is
   unverified and the seam has to represent both.

Both tables are asserted at import in the same shape as `ruleset.assert_declared`,
and for the same reason: a model whose price or limits are undeclared must fail
loudly rather than be called at an assumed one. That is the defect class this
project has hit repeatedly — a hand-maintained constant standing in for a fact
nothing checks (M1.21, B7, M1.42).

**Not built here, deliberately:** `llm_batch.status` has no `balance_exhausted`
value in the schema. Adding it means migration 010, and migration 010 ships with
the writer that sets it, in M5 — a schema value no code path ever writes is
§6.4's pipeline clear all over again (M1.45c).
"""

from __future__ import annotations

import functools
import inspect
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Protocol

from portal.ledger import LedgerBypass, LedgerClearance

TOKENS_PER_MTOK = 1_000_000


class LLMConfigError(RuntimeError):
    """The declared price or limit tables are malformed, or name an unknown model.

    Raised at import for a malformed table, and at lookup for an unknown one.
    Never at a point where money has already been committed.
    """


# ── prices, as dated data (§7 control 10) ───────────────────────────────


@dataclass(frozen=True)
class Price:
    """One row of the price table. `as_of` is a field, not a comment.

    `batch` is part of the key rather than a multiplier applied to the live row:
    the 50% batch discount is Anthropic's, today, and a provider that discounts
    differently — or not at all — must be expressible without arithmetic
    somewhere else in the codebase claiming to know the rule.
    """

    provider: str
    model: str
    batch: bool
    input_per_mtok: float
    output_per_mtok: float
    as_of: date
    #: Where the number came from, so the next reader can re-check it rather
    #: than re-derive it. M1.48's standing rule, applied to a vendor's figure.
    source: str


#: §7 control 10. Haiku 4.5 lists at $1.00 / $5.00 per MTok; the Batch API is
#: 50% off list, so the batch row is $0.50 / $2.50. Verified 2026-06-24.
PRICES: tuple[Price, ...] = (
    Price(
        "anthropic",
        "claude-haiku-4-5",
        False,
        1.00,
        5.00,
        date(2026, 6, 24),
        "Anthropic model pricing table",
    ),
    Price(
        "anthropic",
        "claude-haiku-4-5",
        True,
        0.50,
        2.50,
        date(2026, 6, 24),
        "list price less the Batch API's 50%",
    ),
)


#: §5.5c / §7 control 8. Charged by whoever runs the search, per search issued,
#: and **not** discounted by the Batch API. Kept beside the token prices because
#: it is the same kind of fact and gets the same as-of discipline.
WEB_SEARCH_PER_SEARCH_USD = 0.01
WEB_SEARCH_PRICE_AS_OF = date(2026, 8, 15)


# ── per-model limits (M1.50) ────────────────────────────────────────────


class Thinking(str, Enum):
    """How a model takes a thinking configuration, if it takes one at all.

    Three values because there are three behaviours, and the difference between
    the first two is a 400 rather than a degradation.
    """

    #: `thinking={"type": "adaptive"}`; `output_config.effort` available.
    ADAPTIVE = "adaptive"
    #: The older `thinking={"type": "enabled", "budget_tokens": N}`, N < max_tokens,
    #: minimum 1024. `effort` **errors** on these models.
    BUDGET_TOKENS = "budget_tokens"
    #: No thinking configuration is accepted.
    NONE = "none"


@dataclass(frozen=True)
class ModelLimits:
    """What one model will and will not accept. Declared, never inferred.

    Every field here is a fact that differs across current models, and every one
    of them is a fact an interface would otherwise quietly assume. `reads` in
    `ruleset.Rule` exists for the same reason: the thing that varies has to be
    declared next to the thing that uses it, or it becomes a comment.
    """

    provider: str
    model: str
    context_tokens: int
    max_output_tokens: int
    #: Minimum cacheable prefix. Below it the cache write silently does not
    #: happen — no error, `cache_creation_input_tokens: 0`.
    cache_min_tokens: int
    thinking: Thinking
    supports_effort: bool
    supports_structured_outputs: bool
    supports_batch: bool
    #: The web-search tool variant this model accepts, or None. The newer
    #: `web_search_20260209` filters results with code execution *before* they
    #: reach the context window; the basic one does not, so raw results land in
    #: context in full (M1.54).
    web_search_tool: str | None
    verified_on: date


#: M1.50, verified 2026-08-16. Haiku 4.5 is the only current model below the
#: 128K output ceiling, and carries the highest prompt-cache minimum of any of
#: them. Neither is guessable from the others.
LIMITS: tuple[ModelLimits, ...] = (
    ModelLimits(
        provider="anthropic",
        model="claude-haiku-4-5",
        context_tokens=200_000,
        max_output_tokens=64_000,
        cache_min_tokens=4096,
        thinking=Thinking.BUDGET_TOKENS,
        supports_effort=False,
        supports_structured_outputs=True,
        supports_batch=True,
        web_search_tool="web_search_20250305",
        verified_on=date(2026, 8, 16),
    ),
)


def price_for(provider: str, model: str, *, batch: bool) -> Price:
    """The price row for one (provider, model, batch), or fail.

    Failing is the feature. §7's ceilings are arithmetic over these numbers, and
    an unpriced model reaching a reservation would produce a number that looks
    measured and is not.
    """
    for row in PRICES:
        if row.provider == provider and row.model == model and row.batch is batch:
            return row
    mode = "batch" if batch else "live"
    raise LLMConfigError(
        f"no {mode} price declared for {provider}/{model} — refusing to estimate "
        f"a cost for a model this table does not know"
    )


def limits_for(provider: str, model: str) -> ModelLimits:
    """The declared limits for one (provider, model), or fail."""
    for row in LIMITS:
        if row.provider == provider and row.model == model:
            return row
    raise LLMConfigError(
        f"no limits declared for {provider}/{model} — refusing to call a model "
        f"whose parameter surface this table does not know (M1.50)"
    )


def assert_declared(
    prices: tuple[Price, ...] = PRICES, limits: tuple[ModelLimits, ...] = LIMITS
) -> None:
    """Startup assertion over both tables, mirroring `ruleset.assert_declared`.

    The checks are chosen for the ways these tables have gone wrong elsewhere in
    this project rather than for completeness: a duplicate row (two truths), a
    missing date (a constant wearing a measurement's clothes), a batch row that
    is not cheaper than its live row (a discount transcribed backwards), and a
    priced model with no declared limits (callable at an assumed parameter
    surface, which is M1.50's whole finding).
    """
    seen_prices: set[tuple[str, str, bool]] = set()
    for row in prices:
        key = (row.provider, row.model, row.batch)
        if key in seen_prices:
            raise LLMConfigError(f"duplicate price row: {key}")
        seen_prices.add(key)
        if row.input_per_mtok <= 0 or row.output_per_mtok <= 0:
            raise LLMConfigError(f"{key} has a non-positive price")
        if not isinstance(row.as_of, date):
            raise LLMConfigError(f"{key} has no as-of date")
        if not row.source:
            raise LLMConfigError(f"{key} does not say where its number came from")

    for row in prices:
        if not row.batch:
            continue
        live = (row.provider, row.model, False)
        if live not in seen_prices:
            raise LLMConfigError(
                f"{row.provider}/{row.model} has a batch price and no live price "
                f"to compare it against"
            )
        reference = price_for(row.provider, row.model, batch=False)
        if (
            row.input_per_mtok >= reference.input_per_mtok
            or row.output_per_mtok >= reference.output_per_mtok
        ):
            raise LLMConfigError(
                f"{row.provider}/{row.model} batch price is not below its live "
                f"price — a discount transcribed backwards reserves too little"
            )

    seen_limits: set[tuple[str, str]] = set()
    for lim in limits:
        key2 = (lim.provider, lim.model)
        if key2 in seen_limits:
            raise LLMConfigError(f"duplicate limits row: {key2}")
        seen_limits.add(key2)
        if lim.max_output_tokens >= lim.context_tokens:
            raise LLMConfigError(f"{key2} declares an output cap above its context")
        if lim.supports_effort and lim.thinking is Thinking.BUDGET_TOKENS:
            # M1.50(a): the two are mutually exclusive on every current model,
            # and the pairing is exactly the assumption that 400s on Haiku 4.5.
            raise LLMConfigError(
                f"{key2} declares both `effort` and `budget_tokens` thinking; "
                f"no current model accepts both"
            )
        if not isinstance(lim.verified_on, date):
            raise LLMConfigError(f"{key2} does not say when it was verified")

    for provider, model, _ in seen_prices:
        if (provider, model) not in seen_limits:
            raise LLMConfigError(
                f"{provider}/{model} is priced but declares no limits — it would "
                f"be callable at an assumed parameter surface (M1.50)"
            )


# ── §7 control 2's gate, asserted at import (M1.71) ─────────────────────
#
# §7's preamble: "implemented as code, not as discipline". A ledger nobody is
# obliged to consult is discipline. These three pieces make it a mechanism:
# `requires_ledger_clearance` refuses the call, the SURFACES tuples force every
# callable to be classified as paid or free, and `assert_ledger_guarded` runs
# both checks at import — before there is a caller, so M5 is written against
# the gate's presence rather than its absence.


def requires_ledger_clearance(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a paid surface, and refuse to run it without a `LedgerClearance`.

    The clearance is keyword-only and required, so Python rejects a call that
    omits it before this wrapper runs; the wrapper is what rejects a call that
    passes `None`, or a look-alike, to get past the signature. Only
    `ledger.check_ceiling` constructs a real one.
    """

    @functools.wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        if not isinstance(kwargs.get("clearance"), LedgerClearance):
            raise LedgerBypass(
                f"{fn.__qualname__} is a paid surface and was called without a "
                f"§7 control 2 ledger clearance. Call `ledger.check_ceiling(conn)` "
                f"and pass what it returns; there is no way to opt out, because "
                f"an opt-out is the bypass this gate exists to prevent."
            )
        return fn(*args, **kwargs)

    guarded._ledger_guarded = True  # type: ignore[attr-defined]
    return guarded


#: Callables that reserve or commit spend. Each must carry the decorator.
PAID_SURFACES: tuple[str, ...] = ("reserve_batch",)

#: Callables that provably cost nothing: pure arithmetic over the tables, or
#: reads of a result already paid for. Listed rather than inferred, so that a
#: new callable is a **build failure** until somebody decides which it is.
FREE_SURFACES: tuple[str, ...] = (
    "assert_declared",
    "assert_ledger_guarded",
    "cache_write_observed",
    "classify_api_error",
    "estimate_cost",
    "index_by_custom_id",
    "limits_for",
    "price_for",
    "requires_ledger_clearance",
    "resolve_batch_status",
    "resubmittable",
    "strict_json_schema",
)


def assert_ledger_guarded(
    namespace: Any,
    *,
    paid: tuple[str, ...],
    free: tuple[str, ...],
    where: str,
) -> None:
    """Startup assertion over the paid/free split, mirroring `assert_declared`.

    Three checks, each for a way this goes wrong rather than for completeness:
    a paid surface missing its decorator (a gate written and not attached), a
    name in either tuple that no longer exists (a registry describing code that
    was deleted — B7's shape, a row that reads as implemented), and a callable
    in neither tuple (**the one that matters**: the new paid path nobody
    classified). The third is why the free list is written out longhand.
    """
    overlap = set(paid) & set(free)
    if overlap:
        raise LLMConfigError(f"{where}: {sorted(overlap)} listed as both paid and free")

    declared = set(paid) | set(free)
    members = {
        name for name, obj in _module_callables(namespace) if not name.startswith("_")
    }

    for name in sorted(declared - members):
        raise LLMConfigError(
            f"{where}: '{name}' is registered as a {'paid' if name in paid else 'free'} "
            f"surface but no such callable exists — a registry that describes "
            f"absent code reads as implemented (B7's shape)"
        )

    for name in sorted(members - declared):
        raise LLMConfigError(
            f"{where}: '{name}' is classified as neither paid nor free. Add it to "
            f"PAID_SURFACES (and decorate it with @requires_ledger_clearance) or "
            f"to FREE_SURFACES. §7 control 2 cannot gate a path nobody declared."
        )

    for name in sorted(paid):
        target = dict(_module_callables(namespace))[name]
        if not getattr(target, "_ledger_guarded", False):
            raise LLMConfigError(
                f"{where}: '{name}' is a paid surface with no §7 control 2 gate. "
                f"Decorate it with @requires_ledger_clearance — the ledger is a "
                f"mechanism only while every paid path is obliged to consult it."
            )


def _module_callables(namespace: Any) -> list[tuple[str, Any]]:
    """The functions *defined by* `namespace`, ignoring anything it imported.

    A module is filtered on `__module__` so that `dataclass` and `functools`
    do not read as undeclared paid paths; a class is filtered on its own
    `vars`, and `__init__` is excluded because constructing a provider spends
    nothing — the call does.
    """
    if inspect.ismodule(namespace):
        return [
            (name, obj)
            for name, obj in vars(namespace).items()
            if inspect.isfunction(obj) and obj.__module__ == namespace.__name__
        ]
    return [
        (name, obj)
        for name, obj in vars(namespace).items()
        if inspect.isfunction(obj) and name != "__init__"
    ]


# ── the failure taxonomy (§7 control 11, M1.51, M1.53) ──────────────────


class RequestOutcome(str, Enum):
    """What happened to **one** request inside a batch.

    The four the API returns, plus the two this tool has to distinguish that the
    API's own vocabulary does not.
    """

    SUCCEEDED = "succeeded"
    #: `errored` splits (M1.51). Malformed: it will be malformed again, so a
    #: retry is spend with a known outcome.
    INVALID_REQUEST = "invalid_request"
    #: `errored`, provider-side. Safe to retry.
    SERVER_ERROR = "server_error"
    CANCELED = "canceled"
    #: Past the 24-hour maximum. Arrives through the **success** path: a batch
    #: ends normally carrying these alongside succeeded results.
    EXPIRED = "expired"
    #: The key ran dry. Its own outcome, never folded into a server error
    #: (§7 control 11) — the two need different operator responses.
    BALANCE_EXHAUSTED = "balance_exhausted"

    @property
    def retryable(self) -> bool:
        """Would submitting the same request again plausibly do something else?

        `INVALID_REQUEST` is false by construction. `BALANCE_EXHAUSTED` is false
        because retrying spends nothing and tells the operator nothing they do
        not already know — the resolution is a person topping up a key.
        """
        return self in (
            RequestOutcome.SERVER_ERROR,
            RequestOutcome.EXPIRED,
        )


class BatchStatus(str, Enum):
    """Batch-level status. The five §4 declares, plus the one §7 control 11 adds.

    `BALANCE_EXHAUSTED` has no `llm_batch.status` CHECK value yet: that is
    migration 010, and it ships with M5's writer rather than ahead of it.
    """

    SUBMITTED = "submitted"
    COMPLETED = "completed"
    RECONCILED = "reconciled"
    FAILED = "failed"
    EXPIRED = "expired"
    BALANCE_EXHAUSTED = "balance_exhausted"


#: The two places a prepaid balance failure can surface, per M1.53. Which one is
#: real is **unverified** — it needs a live key — and the two are different
#: designs: the first sets the dry-key status before any spend is committed, the
#: second discovers it at reconcile with part of the batch already paid for.
class BalanceFailurePoint(str, Enum):
    SUBMIT = "submit"
    RESULTS = "results"


#: What the code assumes until a real key settles it. Submit-time is the safer
#: assumption: it is the one under which the tool stops before committing spend
#: it cannot pay for. Recorded as a value rather than as a comment so that
#: changing the assumption is a diff and not an archaeology exercise.
ASSUMED_BALANCE_FAILURE_POINT = BalanceFailurePoint.SUBMIT


def classify_api_error(
    error_type: str | None, status_code: int | None
) -> RequestOutcome:
    """Map a provider error onto the taxonomy above.

    **`billing_error` is a 403 and so is `permission_error`** (M1.53). The two
    are distinguishable only through the error object's `.type` — `e.type` in
    Python, `.errorType()` in Java, `.Type()` in Go — never through the status
    code, which is what most code branches on. Branching on 403 alone reports a
    dry key as a permissions problem and sends the operator to the wrong place.

    Takes the two fields rather than an SDK exception so that the mapping is
    testable without the SDK, and so the same function serves both call sites
    (submit and results) as M1.53 requires.
    """
    if error_type == "billing_error":
        return RequestOutcome.BALANCE_EXHAUSTED
    if error_type == "invalid_request_error":
        return RequestOutcome.INVALID_REQUEST
    if error_type in (
        "api_error",
        "overloaded_error",
        "rate_limit_error",
        "timeout_error",
    ):
        return RequestOutcome.SERVER_ERROR
    if error_type in ("authentication_error", "permission_error", "not_found_error"):
        # Not a transient and not a money problem: a misconfiguration. It is an
        # invalid request in the only sense that matters here — resubmitting the
        # same thing under the same key does the same thing.
        return RequestOutcome.INVALID_REQUEST
    if status_code is not None and status_code >= 500:
        return RequestOutcome.SERVER_ERROR
    if status_code == 403:
        # A 403 whose `.type` we could not read. Refusing to guess between
        # billing and permission is the whole point of this function existing.
        raise LLMConfigError(
            "403 with no error type: cannot distinguish billing_error from "
            "permission_error, and they need different operator responses"
        )
    return RequestOutcome.SERVER_ERROR


# ── the data the Protocol moves ─────────────────────────────────────────


@dataclass(frozen=True)
class Usage:
    """Real token counts off a response. Never an estimate.

    `cache_creation_input_tokens` is here because on Haiku 4.5 it is the only
    way to know whether a cache write happened at all: below the 4096-token
    minimum it is silently 0 with no error (M1.50c).
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    web_searches: int = 0


@dataclass(frozen=True)
class Extraction:
    """One parsed extraction plus what it actually cost.

    Carries real token counts so §7's reservation reconciles against measured
    usage rather than against the estimate that reserved it (§5.6 B3.1).
    """

    custom_id: str
    payload: dict[str, Any]
    usage: Usage
    model: str


@dataclass(frozen=True)
class BatchRequest:
    """One request in a batch.

    `custom_id` is not decoration. Batch results come back in **arbitrary
    order** (M1.51), so this is the only thing tying a returned legal name to
    the company it was read for.
    """

    custom_id: str
    system: str
    user_text: str
    json_schema: dict[str, Any]
    max_tokens: int


@dataclass(frozen=True)
class BatchResultItem:
    custom_id: str
    outcome: RequestOutcome
    extraction: Extraction | None = None
    error_message: str = ""


@dataclass(frozen=True)
class BatchResult:
    provider_batch_id: str
    status: BatchStatus
    items: tuple[BatchResultItem, ...]


@dataclass(frozen=True)
class CostEstimate:
    """A reservation, with its arithmetic kept rather than collapsed to a total."""

    input_tokens: int
    output_tokens: int
    web_searches: int
    price: Price
    total_usd: float


class TokenCounter(Protocol):
    """`count_tokens` for one model. Model-specific, and therefore not a heuristic.

    A Protocol rather than a concrete call so the estimator is testable offline
    and so a provider with a different counting endpoint satisfies the same
    interface. §7 control 4: a character-length rule of thumb calibrated on one
    tokenizer is a second expression describing what the first one does.
    """

    def __call__(self, *, system: str, user_text: str) -> int: ...


class LLMProvider(Protocol):
    """Proposal v2 §4. Selected by configuration, never inferred."""

    name: str
    model: str

    def limits(self) -> ModelLimits: ...

    def count_input_tokens(self, request: BatchRequest) -> int: ...

    def submit_batch(
        self, requests: Sequence[BatchRequest], *, clearance: LedgerClearance
    ) -> str: ...

    def poll_batch(self, provider_batch_id: str) -> BatchResult: ...


# ── estimation and reconciliation, as pure functions ────────────────────


def estimate_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    provider: str,
    model: str,
    batch: bool,
    web_searches: int = 0,
) -> CostEstimate:
    """§7 controls 3 and 4. Arithmetic over the dated table, and nothing else."""
    price = price_for(provider, model, batch=batch)
    total = (
        input_tokens / TOKENS_PER_MTOK * price.input_per_mtok
        + output_tokens / TOKENS_PER_MTOK * price.output_per_mtok
        # §7 control 8: the per-search fee is not discounted by the Batch API,
        # so it is added after the batch-priced tokens rather than inside them.
        + web_searches * WEB_SEARCH_PER_SEARCH_USD
    )
    return CostEstimate(input_tokens, output_tokens, web_searches, price, total)


@requires_ledger_clearance
def reserve_batch(
    requests: Sequence[BatchRequest],
    *,
    provider: str,
    model: str,
    count_tokens: TokenCounter,
    clearance: LedgerClearance,
) -> CostEstimate:
    """The §7 control 4 reservation, measured rather than guessed (M1.52).

    **`clearance` is §7 control 2's, and it is required** (M1.71). The outer
    bound is consulted *before* the per-batch arithmetic, not after: control 2
    is what stops a runaway, and a runaway that is allowed to price itself
    first has already made the call this ceiling exists to refuse. The token is
    threaded rather than re-checked here so the ledger is read once per attempt
    and the reading can be reported — see `LedgerClearance`.

    Input tokens come from `count_tokens` for the model actually being called.
    Output is bounded by each request's own `max_tokens`: the reservation must
    be an upper bound, and the only upper bound the API guarantees is the one we
    asked for.

    **A `count_tokens` failure propagates and aborts the submission.** It does
    not fall back to an estimate. §7 exists to make an under-reserved batch
    impossible, and a fallback estimate is precisely how an unmeasured number
    enters the ledger looking like a measured one.
    """
    limits = limits_for(provider, model)
    input_tokens = 0
    output_tokens = 0
    for request in requests:
        if request.max_tokens > limits.max_output_tokens:
            raise LLMConfigError(
                f"{request.custom_id} asks for {request.max_tokens} output tokens; "
                f"{provider}/{model} caps at {limits.max_output_tokens} (M1.50b)"
            )
        counted = count_tokens(system=request.system, user_text=request.user_text)
        if counted > limits.context_tokens:
            raise LLMConfigError(
                f"{request.custom_id} is {counted} tokens against a "
                f"{limits.context_tokens}-token context window"
            )
        input_tokens += counted
        output_tokens += request.max_tokens
    return estimate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider=provider,
        model=model,
        batch=True,
    )


def resolve_batch_status(items: Sequence[BatchResultItem]) -> BatchStatus:
    """What a batch's status becomes once its results have been read (M1.51).

    **This is the function that stops a partially-processed batch being marked
    done.** A batch ends normally carrying expired members, so "the batch ended,
    therefore the batch is complete" writes `reconciled` over companies that
    were never extracted — partial-ness arriving through the success path, which
    is not where anyone looks for it.

    A batch is `reconciled` only when every request succeeded or failed in a way
    that resubmitting cannot change. Anything still owed keeps the batch open.
    """
    if not items:
        # An empty result set is not a finished batch. It is a batch we have not
        # read, and the difference matters to §7's ledger.
        return BatchStatus.SUBMITTED
    outcomes = {item.outcome for item in items}
    if RequestOutcome.BALANCE_EXHAUSTED in outcomes:
        return BatchStatus.BALANCE_EXHAUSTED
    if any(o.retryable for o in outcomes):
        # Expired or server-errored members are still owed. The batch stays
        # open, and §7 reserves their re-submission as the new spend it is.
        return BatchStatus.COMPLETED
    return BatchStatus.RECONCILED


def resubmittable(items: Sequence[BatchResultItem]) -> tuple[str, ...]:
    """The `custom_id`s worth submitting again, in input order.

    Deliberately excludes `INVALID_REQUEST`: the request was malformed and will
    be malformed again, so a retry is spend with a known outcome. M1.4's rule —
    when two errors are unequal, take the safe side — applied to money.
    """
    return tuple(item.custom_id for item in items if item.outcome.retryable)


def index_by_custom_id(
    items: Sequence[BatchResultItem],
) -> dict[str, BatchResultItem]:
    """Key results by `custom_id`. The only correct way to read them (M1.51).

    Results are returned in arbitrary order. Reading result *n* as belonging to
    request *n* attributes a legal name and a set of Geschäftsführer to the
    wrong company — M1.17's failure with a new cause, and substring verification
    does not catch it, because the values genuinely are on the page they came
    from. A duplicate id is a provider contract violation and fails here rather
    than silently keeping whichever arrived last.
    """
    out: dict[str, BatchResultItem] = {}
    for item in items:
        if item.custom_id in out:
            raise LLMConfigError(
                f"duplicate custom_id in batch results: {item.custom_id}"
            )
        out[item.custom_id] = item
    return out


def cache_write_observed(usage: Usage, limits: ModelLimits, prompt_tokens: int) -> bool:
    """Did the prompt cache actually get written? (M1.50c)

    Below the model's minimum the write silently does not happen: no error, just
    `cache_creation_input_tokens: 0`. So a caller that caches must **assert the
    observed value** rather than assume the write happened, which is what this
    exists to make easy. Returns False, loudly comparable, rather than raising —
    the caller decides whether a missed cache is an error or a note.
    """
    if prompt_tokens < limits.cache_min_tokens:
        return False
    return usage.cache_creation_input_tokens > 0


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic-generated schema acceptable to structured outputs.

    Structured outputs require `additionalProperties: false` and an explicit
    `required` on every object. Pydantic emits neither for optional fields, so
    this walks the schema and adds them. Every field is required at the schema
    level and nullable at the type level, which is what §5.5b's prompt discipline
    already asks for: *return `null` for any field not present on the page* is a
    value the model must supply, not a key it may omit.
    """
    out = dict(schema)
    if out.get("type") == "object":
        properties = out.get("properties", {})
        out["properties"] = {k: strict_json_schema(v) for k, v in properties.items()}
        out["required"] = sorted(properties)
        out["additionalProperties"] = False
    for key in ("items", "additionalItems"):
        if isinstance(out.get(key), dict):
            out[key] = strict_json_schema(out[key])
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [
                strict_json_schema(s) if isinstance(s, dict) else s for s in out[key]
            ]
    if isinstance(out.get("$defs"), dict):
        out["$defs"] = {k: strict_json_schema(v) for k, v in out["$defs"].items()}
    return out


assert_declared()
assert_ledger_guarded(
    sys.modules[__name__], paid=PAID_SURFACES, free=FREE_SURFACES, where="portal.llm"
)


__all__ = [
    "ASSUMED_BALANCE_FAILURE_POINT",
    "FREE_SURFACES",
    "LIMITS",
    "PAID_SURFACES",
    "PRICES",
    "WEB_SEARCH_PER_SEARCH_USD",
    "BalanceFailurePoint",
    "BatchRequest",
    "BatchResult",
    "BatchResultItem",
    "BatchStatus",
    "CostEstimate",
    "Extraction",
    "LLMConfigError",
    "LLMProvider",
    "ModelLimits",
    "Price",
    "RequestOutcome",
    "Thinking",
    "TokenCounter",
    "Usage",
    "assert_declared",
    "assert_ledger_guarded",
    "cache_write_observed",
    "classify_api_error",
    "estimate_cost",
    "index_by_custom_id",
    "limits_for",
    "price_for",
    "requires_ledger_clearance",
    "reserve_batch",
    "resolve_batch_status",
    "resubmittable",
    "strict_json_schema",
]
