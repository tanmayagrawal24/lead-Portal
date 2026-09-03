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

    @llm.requires_ledger_clearance
    def ask_with_search(
        self,
        *,
        system: str,
        user_text: str,
        max_tokens: int,
        max_searches: int,
        clearance: llm.LedgerClearance,
    ) -> llm.SearchAnswer:
        """§5.5c's one paid call: a **live** message with the web-search tool.

        Live rather than batched, because §7.1 prices this sub-stage *"on Haiku
        4.5, live"* and the Batch API does not discount the per-search fee
        anyway (control 8) — so batching would save the token half and cost
        the 24-hour wait on the half that is not saved. Money is committed the
        moment `messages.create` returns, which is why this carries §7 control
        2's gate itself and not only through its caller (M1.71).

        `max_searches` is passed to the tool as `max_uses`: the reservation
        priced exactly that many searches, and a bound that is not sent to the
        provider is a bound written where it cannot bind (M1.103's shape).
        The tool variant comes from `LIMITS`, not from a literal here — on
        Haiku 4.5 it is the basic `web_search_20250305`, and the consequence
        M1.54 records (raw results land in context in full) is priced in
        `ai_visibility.SEARCH_CONTEXT_TOKENS`.

        A `billing_error` here is M1.53's *other* seam: the balance can run
        dry between one company's call and the next, after real spend. It is
        raised as `BalanceExhausted` so the caller can stop and say so.
        """
        tool = self._limits.web_search_tool
        if tool is None:
            raise llm.LLMConfigError(
                f"{self.model} declares no web-search tool (M1.54); §5.5c cannot run on it"
            )
        if max_tokens > self._limits.max_output_tokens:
            raise llm.LLMConfigError(
                f"{max_tokens} output tokens exceeds {self.model}'s "
                f"{self._limits.max_output_tokens} cap (M1.50b)"
            )
        client = _client(self._client)
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_text}],
                tools=[{"type": tool, "name": "web_search", "max_uses": max_searches}],
            )
        except Exception as exc:
            outcome = llm.classify_api_error(*_error_fields(exc))
            if outcome is llm.RequestOutcome.BALANCE_EXHAUSTED:
                raise BalanceExhausted(
                    "the API key's prepaid balance is exhausted mid-run; calls "
                    "already answered were paid for and their signals stand"
                ) from exc
            raise
        text = "".join(
            str(getattr(block, "text", "") or "")
            for block in getattr(message, "content", ())
            if getattr(block, "type", None) == "text"
        )
        return llm.SearchAnswer(
            text=text,
            usage=_usage(message),
            stop_reason=str(getattr(message, "stop_reason", "") or ""),
            model=str(getattr(message, "model", self.model) or self.model),
        )

    def list_batches(self, *, limit: int = 20) -> tuple[llm.BatchListing, ...]:
        """§10.7b's closing instrument: every batch the account has, newest first.

        **Free, read-only, and the only way this project asks the account what
        it has spent.** M1.100 ruled *whether a batch was ever submitted* OPEN
        rather than zero, because every local record of `llm_batch` went with
        the corpus and *"no key on this machine"* is a statement about a
        machine. The closing procedure it wrote was a pasted Python one-liner;
        this is the same call as a command, so that closing the question is a
        thing one runs rather than a thing one types (M1.73's lesson: a state
        that is a command cannot be reported on without being measured).

        It needs a key and it makes no paid call — `messages.batches.list` is a
        read. Without a key it raises `MissingKeyError` before any network
        attempt, and does NOT look for another credential (§7 control 9).
        """
        client = _client(self._client)
        listed: list[llm.BatchListing] = []
        for batch in client.messages.batches.list(limit=limit):
            counts = getattr(batch, "request_counts", None)
            listed.append(
                llm.BatchListing(
                    provider_batch_id=str(getattr(batch, "id", "")),
                    processing_status=str(getattr(batch, "processing_status", "")),
                    created_at=str(getattr(batch, "created_at", "")),
                    expires_at=str(getattr(batch, "expires_at", "") or ""),
                    succeeded=int(getattr(counts, "succeeded", 0) or 0),
                    errored=int(getattr(counts, "errored", 0) or 0),
                    expired=int(getattr(counts, "expired", 0) or 0),
                    canceled=int(getattr(counts, "canceled", 0) or 0),
                    processing=int(getattr(counts, "processing", 0) or 0),
                )
            )
        return tuple(listed)

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
            provider_batch_id,
            # **The provider-side question only, and the call site says so
            # (M1.86).** `expected` is what was SENT, and this layer does not
            # know it — `llm_batch_request` does, and `reconcile` re-resolves
            # against that stored set. Passing the returned ids here is not a
            # workaround for the missing set; it is the honest statement that
            # from inside the provider, "every request" and "every result" are
            # the same thing. The status on this object is therefore an upper
            # bound on how finished the batch is, never a lower one.
            llm.resolve_batch_status(items, expected=[i.custom_id for i in items]),
            items,
        )

    # ── result mapping (offline) ────────────────────────────────────────

    def _result_item(self, result: Any) -> llm.BatchResultItem:
        """One returned result onto the taxonomy. Keyed by `custom_id`, always."""
        custom_id = str(result.custom_id)
        kind = result.result.type

        if kind == "succeeded":
            message = result.result.message
            payload, problem = _json_payload(message)
            if payload is None:
                # Audit finding 8. `succeeded` is the PROVIDER's word for "a
                # message came back", and a message can come back truncated
                # at `max_tokens`, refused, or otherwise carrying no JSON.
                # Raising here took the whole batch down for one company —
                # Audit 3's shape one stage on. It is a disposition instead:
                # not retryable, because the same request at the same bound
                # does the same thing (§5.6, spend with a known outcome), and
                # it CARRIES ITS USAGE, because it was paid for.
                return llm.BatchResultItem(
                    custom_id,
                    llm.RequestOutcome.INVALID_REQUEST,
                    error_message=problem,
                    usage=_usage(message),
                )
            return llm.BatchResultItem(
                custom_id,
                llm.RequestOutcome.SUCCEEDED,
                extraction=llm.Extraction(
                    custom_id=custom_id,
                    payload=payload,
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


def _json_payload(message: Any) -> tuple[dict[str, Any] | None, str]:
    """The validated JSON out of a structured-output response, or why not.

    `output_config.format` guarantees the first text block is valid JSON **when
    the model finished** — so this reads that block rather than searching for
    the first thing that parses. Returns `(payload, "")` on success and
    `(None, reason)` for the three ways a `succeeded` result carries nothing
    usable (audit finding 8):

    * `stop_reason == "max_tokens"` — the JSON was cut off. The text block is
      not parsed at all: a prefix of a JSON document is not a document, and
      "it happened to parse" would be a value written from a truncated page.
    * `stop_reason == "refusal"` — the model declined.
    * no text block, or text that is not JSON — a contract violation that used
      to raise `LLMConfigError` and abort the whole poll.

    All three are mapped to `INVALID_REQUEST` by the caller. The reason string
    is the request's `error_message`, which `reconcile` stores on
    `llm_batch_request` and prints, so the operator sees *which* company came
    back empty and *why*, rather than a traceback for the batch.
    """
    import json

    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "max_tokens":
        return None, (
            "truncated: the response reached its max_tokens bound before the "
            "JSON closed; a resubmission at the same bound would truncate again"
        )
    if stop_reason == "refusal":
        return None, "refused: the model declined to produce the structured output"

    for block in message.content:
        if getattr(block, "type", None) == "text":
            try:
                return dict(json.loads(block.text)), ""
            except (TypeError, ValueError) as exc:
                return None, f"response text is not a JSON object: {exc}"
    return None, "structured-output response carried no text block"


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

#: `submit_batch` and `ask_with_search` are the two calls that commit spend. `count_input_tokens` and
#: `token_counter` reach the network and are **free** — `count_tokens` is not a
#: paid endpoint (M1.52) — and `poll_batch` reads a result already paid for.
PAID_SURFACES: tuple[str, ...] = ("ask_with_search", "submit_batch")
FREE_SURFACES: tuple[str, ...] = (
    "build_params",
    "count_input_tokens",
    "limits",
    "list_batches",
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
