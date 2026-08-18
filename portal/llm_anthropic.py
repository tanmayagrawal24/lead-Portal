"""The Anthropic implementation of `llm.LLMProvider` (build step 1).

Everything provider-specific lives here and nothing else does: `portal/llm.py`
holds the prices, the limits and the failure taxonomy, because those are facts
about the world rather than opinions of this SDK.

**Why the SDK client is injected rather than constructed.** §7 control 9 puts
every key in the environment, and a client built at import would make this
module unimportable without one — which would in turn make every test here need
a key to run. The client is a constructor argument with a lazy default, so the
offline paths (schema construction, request building, result mapping, cost
arithmetic) are exercised without one and only the two methods that genuinely
talk to the API need it.

**Two decisions worth keeping.**

*No `thinking`, no `effort`, on either call.* These are schema-constrained
extractions from a page already on disk; there is nothing to reason about. It
also sidesteps M1.50(a) entirely rather than encoding a workaround: `effort`
**errors** on Haiku 4.5 and adaptive thinking is unavailable, so an interface
that passed either through generically would fail at the first call. The limits
table records what the model would accept; this stage sends neither.

*Results are mapped, never renumbered.* `_result_item` reads `custom_id` off
each returned result and nothing reads an index. Results come back in arbitrary
order (M1.51) and position-keyed reading attributes a legal name to the wrong
company.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from portal import llm

#: §5.5b. Latency is irrelevant for extraction, so everything goes through the
#: Batch API at 50% off list.
PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-haiku-4-5"

#: §7 control 9. Read at call time, never at import, and never logged.
API_KEY_ENV = "ANTHROPIC_API_KEY"


class MissingKeyError(RuntimeError):
    """No API key in the environment. Raised before any network attempt."""


def _client(explicit: Any = None) -> Any:
    """The SDK client, imported lazily so this module imports without a key."""
    if explicit is not None:
        return explicit
    if not os.environ.get(API_KEY_ENV):
        raise MissingKeyError(
            f"{API_KEY_ENV} is not set. §7 control 9: keys come from the "
            f"environment only, and this call needs one."
        )
    import anthropic

    return anthropic.Anthropic()


def _error_fields(exc: BaseException) -> tuple[str | None, int | None]:
    """Pull `.type` and the status code off an SDK exception.

    `.type` is the load-bearing one: `billing_error` and `permission_error` are
    **both 403**, and only `.type` separates "the key ran dry" from "the key is
    not allowed to do this" (M1.53). Read defensively — an exception shape that
    has neither is classified by status code, and a 403 with no type refuses to
    guess (see `llm.classify_api_error`).
    """
    error_type = getattr(exc, "type", None)
    if error_type is None:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            inner = body.get("error")
            if isinstance(inner, dict):
                error_type = inner.get("type")
    status = getattr(exc, "status_code", None)
    return (error_type if isinstance(error_type, str) else None, status)


class AnthropicProvider:
    """`llm.LLMProvider` over the Messages Batches API."""

    def __init__(self, model: str = DEFAULT_MODEL, client: Any = None) -> None:
        self.name = PROVIDER
        self.model = model
        # Fails here rather than at submit time if the model is undeclared: a
        # model with no declared limits would otherwise be called at an assumed
        # parameter surface, which is M1.50's entire finding.
        self._limits = llm.limits_for(PROVIDER, model)
        if not self._limits.supports_batch:
            raise llm.LLMConfigError(f"{model} has no Batch API; §5.5b assumes one")
        self._client = client

    # ── declaration ─────────────────────────────────────────────────────

    def limits(self) -> llm.ModelLimits:
        return self._limits

    def price(self, *, batch: bool = True) -> llm.Price:
        return llm.price_for(PROVIDER, self.model, batch=batch)

    # ── request construction (offline) ──────────────────────────────────

    def build_params(self, request: llm.BatchRequest) -> dict[str, Any]:
        """The Messages params for one batch request.

        Structured outputs via `output_config.format`, which Haiku 4.5 supports
        (M1.50) — so §5.5b's contract holds and the response is validated rather
        than parsed out of prose. No `thinking` and no `output_config.effort`:
        see the module docstring.
        """
        if request.max_tokens > self._limits.max_output_tokens:
            raise llm.LLMConfigError(
                f"{request.custom_id}: {request.max_tokens} output tokens exceeds "
                f"{self.model}'s {self._limits.max_output_tokens} cap (M1.50b)"
            )
        return {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user_text}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": llm.strict_json_schema(request.json_schema),
                }
            },
        }

    # ── the two methods that talk to the API ────────────────────────────

    def count_input_tokens(self, request: llm.BatchRequest) -> int:
        """§7 control 4's reservation input, measured for **this** model.

        A network call, and a free one. It is allowed to fail; a failure
        propagates and aborts the submission rather than falling back to an
        estimate (M1.52).
        """
        client = _client(self._client)
        counted = client.messages.count_tokens(
            model=self.model,
            system=request.system,
            messages=[{"role": "user", "content": request.user_text}],
        )
        return int(counted.input_tokens)

    def token_counter(self) -> llm.TokenCounter:
        """`count_input_tokens` in the shape `llm.reserve_batch` wants."""

        def count(*, system: str, user_text: str) -> int:
            return self.count_input_tokens(
                llm.BatchRequest(
                    custom_id="count",
                    system=system,
                    user_text=user_text,
                    json_schema={},
                    max_tokens=1,
                )
            )

        return count

    @llm.requires_ledger_clearance
    def submit_batch(
        self,
        requests: Sequence[llm.BatchRequest],
        *,
        clearance: llm.LedgerClearance,
    ) -> str:
        """Submit, and classify a submit-time failure rather than re-raising raw.

        **This is where money is actually committed**, and so it carries §7
        control 2's gate in its own right rather than trusting that
        `reserve_batch` ran first (M1.71). Gating only the reservation would
        have made the assertion decorative: `reserve_batch` spends nothing — it
        is arithmetic — while `messages.batches.create` below is irrevocable the
        moment it returns.

        This is one of M1.53's two seams. If a prepaid balance surfaces here, it
        arrives as a `billing_error` on this call and the batch never exists —
        so nothing has been committed and the run aborts on a dry key rather
        than on "the provider failed". Which seam is real is unverified;
        `llm.ASSUMED_BALANCE_FAILURE_POINT` records what this code assumes.
        """
        client = _client(self._client)
        payload = [
            {"custom_id": r.custom_id, "params": self.build_params(r)} for r in requests
        ]
        try:
            batch = client.messages.batches.create(requests=payload)
        except Exception as exc:
            outcome = llm.classify_api_error(*_error_fields(exc))
            if outcome is llm.RequestOutcome.BALANCE_EXHAUSTED:
                raise BalanceExhausted(
                    "the API key's prepaid balance is exhausted; the batch was "
                    "not created and nothing was committed"
                ) from exc
            raise
        return str(batch.id)

    def poll_batch(self, provider_batch_id: str) -> llm.BatchResult:
        """Poll, and read results by `custom_id` (M1.51).

        Returns `SUBMITTED` while `processing_status` is anything but `ended` —
        including a batch that has ended *badly*. A batch that ended is read
        result by result, because an ended batch can carry expired members
        alongside succeeded ones and that is the partially-processed case
        arriving through the success path.
        """
        client = _client(self._client)
        batch = client.messages.batches.retrieve(provider_batch_id)
        if getattr(batch, "processing_status", None) != "ended":
            return llm.BatchResult(provider_batch_id, llm.BatchStatus.SUBMITTED, ())

        items = tuple(
            self._result_item(result)
            for result in client.messages.batches.results(provider_batch_id)
        )
        # Raises on a duplicate id rather than keeping whichever arrived last.
        llm.index_by_custom_id(items)
        return llm.BatchResult(
            provider_batch_id, llm.resolve_batch_status(items), items
        )

    # ── result mapping (offline) ────────────────────────────────────────

    def _result_item(self, result: Any) -> llm.BatchResultItem:
        """One returned result onto the taxonomy. Keyed by `custom_id`, always."""
        custom_id = str(result.custom_id)
        kind = result.result.type

        if kind == "succeeded":
            message = result.result.message
            return llm.BatchResultItem(
                custom_id,
                llm.RequestOutcome.SUCCEEDED,
                extraction=llm.Extraction(
                    custom_id=custom_id,
                    payload=_json_payload(message),
                    usage=_usage(message),
                    model=str(getattr(message, "model", self.model)),
                ),
            )
        if kind == "errored":
            error = getattr(result.result, "error", None)
            error_type = getattr(error, "type", None)
            outcome = llm.classify_api_error(error_type, None)
            return llm.BatchResultItem(
                custom_id,
                outcome,
                error_message=str(getattr(error, "message", "") or ""),
            )
        if kind == "expired":
            # Past the 24-hour maximum. Not an error path: this arrives on a
            # batch whose `processing_status` is `ended` (M1.51).
            return llm.BatchResultItem(custom_id, llm.RequestOutcome.EXPIRED)
        if kind == "canceled":
            return llm.BatchResultItem(custom_id, llm.RequestOutcome.CANCELED)
        raise llm.LLMConfigError(
            f"unknown batch result type {kind!r} for {custom_id} — refusing to "
            f"treat an unrecognised outcome as a success or as a retry"
        )


class BalanceExhausted(RuntimeError):
    """The prepaid balance ran dry (§7 control 11).

    Its own exception rather than a generic provider failure, so `reconcile` can
    report *"this batch stopped because the key ran dry"* in those words. The two
    need different operator responses and one of them is not an engineering task.
    """


def _json_payload(message: Any) -> dict[str, Any]:
    """The validated JSON out of a structured-output response.

    `output_config.format` guarantees the first text block is valid JSON, so
    this reads that block rather than searching for the first thing that parses.
    """
    import json

    for block in message.content:
        if getattr(block, "type", None) == "text":
            return dict(json.loads(block.text))
    raise llm.LLMConfigError("structured-output response carried no text block")


def _usage(message: Any) -> llm.Usage:
    usage = message.usage
    server = getattr(usage, "server_tool_use", None)
    return llm.Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        # §7 control 8 accumulates this into `run.web_searches`. Zero for
        # extraction; the field exists because the same Usage carries §5.5c.
        web_searches=int(getattr(server, "web_search_requests", 0) or 0),
    )


# ── §7 control 2, asserted at import for the provider too (M1.71) ───────
#
# The same mechanism `llm.py` applies to itself, pointed at the class that
# holds the only irrevocable call in the project. The free list is written out
# so that adding a method here is a build failure until somebody has decided
# whether it spends money.

#: `submit_batch` is the only call that commits spend. `count_input_tokens` and
#: `token_counter` reach the network and are **free** — `count_tokens` is not a
#: paid endpoint (M1.52) — and `poll_batch` reads a result already paid for.
PAID_SURFACES: tuple[str, ...] = ("submit_batch",)
FREE_SURFACES: tuple[str, ...] = (
    "build_params",
    "count_input_tokens",
    "limits",
    "poll_batch",
    "price",
    "token_counter",
)

llm.assert_ledger_guarded(
    AnthropicProvider,
    paid=PAID_SURFACES,
    free=FREE_SURFACES,
    where="portal.llm_anthropic.AnthropicProvider",
)
