"""The provider-agnostic LLM layer (`portal/llm.py`, build steps 1–3).

**The properties under test are not "the arithmetic is right".** They are the
four places this layer is the only thing standing between the tool and a wrong
answer that looks right:

1. **A batch that ended is not a batch that finished.** `expired` is a per-request
   result arriving on the *success* path, so `resolve_batch_status` is what stops
   a partially-processed batch being written `reconciled` over companies that were
   never extracted (M1.51).
2. **`billing_error` and `permission_error` are both 403.** Classifying by status
   code reports a dry key as a permissions problem (M1.53).
3. **Results are keyed by `custom_id`, never by position.** Position-keyed reading
   attributes a legal name to the wrong company, and substring verification does
   not catch it because the value genuinely is on the page it came from (M1.51).
4. **A reservation is measured or it does not happen.** `count_tokens` failing
   must propagate, not fall back to an estimate (M1.52).

The table assertions are tested the way `ruleset.assert_declared` is: by feeding
them the specific malformed shapes this project has actually produced elsewhere.
"""

from __future__ import annotations

import unittest
from datetime import date

from portal import ledger, llm


def _cleared() -> ledger.LedgerClearance:
    """A §7 control 2 clearance for tests that are not testing the ledger.

    Constructed directly rather than through `check_ceiling`, which needs a
    database these tests have no reason to open. That the dataclass is
    constructible is deliberate and is not the hole it looks like: the gate
    exists to stop a paid path being reached **without anyone deciding**, and
    writing `spend_usd=0.0` by hand is a decision. `tests/test_cost_ledger.py`
    is where the gate itself is tested.
    """
    return ledger.LedgerClearance(
        spend_usd=0.0,
        ceiling_usd=ledger.MONTHLY_CEILING_USD,
        window_days=ledger.WINDOW_DAYS,
        taken_at="2026-08-18T12:00:00Z",
    )


def _price(**kw) -> llm.Price:
    base = {
        "provider": "anthropic",
        "model": "test-model",
        "batch": False,
        "input_per_mtok": 1.0,
        "output_per_mtok": 5.0,
        "as_of": date(2026, 6, 24),
        "source": "test",
    }
    base.update(kw)
    return llm.Price(**base)


def _limits(**kw) -> llm.ModelLimits:
    base = {
        "provider": "anthropic",
        "model": "test-model",
        "context_tokens": 200_000,
        "max_output_tokens": 64_000,
        "cache_min_tokens": 4096,
        "thinking": llm.Thinking.BUDGET_TOKENS,
        "supports_effort": False,
        "supports_structured_outputs": True,
        "supports_batch": True,
        "web_search_tool": "web_search_20250305",
        "verified_on": date(2026, 8, 16),
    }
    base.update(kw)
    return llm.ModelLimits(**base)


def _item(custom_id: str, outcome: llm.RequestOutcome) -> llm.BatchResultItem:
    return llm.BatchResultItem(custom_id, outcome)


class ShippedTables(unittest.TestCase):
    """The real tables, checked against the facts they were written from."""

    def test_shipped_tables_pass_their_own_assertion(self) -> None:
        llm.assert_declared()

    def test_haiku_batch_row_is_half_of_list(self) -> None:
        live = llm.price_for("anthropic", "claude-haiku-4-5", batch=False)
        batch = llm.price_for("anthropic", "claude-haiku-4-5", batch=True)
        self.assertEqual(batch.input_per_mtok, live.input_per_mtok / 2)
        self.assertEqual(batch.output_per_mtok, live.output_per_mtok / 2)
        self.assertEqual(batch.as_of, date(2026, 6, 24))

    def test_haiku_limits_carry_the_three_facts_that_differ(self) -> None:
        # M1.50: an interface that generalises any of these is wrong at the
        # first call, so they are asserted here rather than trusted.
        lim = llm.limits_for("anthropic", "claude-haiku-4-5")
        self.assertFalse(lim.supports_effort)
        self.assertIs(lim.thinking, llm.Thinking.BUDGET_TOKENS)
        self.assertEqual(lim.max_output_tokens, 64_000)
        self.assertEqual(lim.cache_min_tokens, 4096)
        self.assertTrue(lim.supports_structured_outputs)
        self.assertEqual(lim.web_search_tool, "web_search_20250305")

    def test_unknown_model_refuses_rather_than_defaulting(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.price_for("anthropic", "claude-opus-5", batch=True)
        with self.assertRaises(llm.LLMConfigError):
            llm.limits_for("anthropic", "claude-opus-5")


class TableAssertions(unittest.TestCase):
    """Each malformed shape this project has actually produced somewhere."""

    def test_duplicate_price_row(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared((_price(), _price()), (_limits(),))

    def test_price_without_as_of_is_a_constant_not_a_measurement(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared((_price(as_of="2026-06-24"),), (_limits(),))

    def test_price_without_a_source(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared((_price(source=""),), (_limits(),))

    def test_batch_price_above_live_is_a_discount_transcribed_backwards(self) -> None:
        # The failure mode this catches reserves *less* than the call costs.
        rows = (_price(), _price(batch=True, input_per_mtok=2.0, output_per_mtok=10.0))
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared(rows, (_limits(),))

    def test_batch_price_with_no_live_price_to_compare(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared((_price(batch=True, input_per_mtok=0.5),), (_limits(),))

    def test_priced_model_with_no_declared_limits(self) -> None:
        # Callable at an assumed parameter surface — M1.50's whole finding.
        with self.assertRaises(llm.LLMConfigError) as caught:
            llm.assert_declared((_price(),), ())
        self.assertIn("declares no limits", str(caught.exception))

    def test_effort_and_budget_tokens_together_is_rejected(self) -> None:
        rows = (_limits(supports_effort=True, thinking=llm.Thinking.BUDGET_TOKENS),)
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared((_price(),), rows)

    def test_output_cap_above_context(self) -> None:
        rows = (_limits(max_output_tokens=300_000),)
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared((_price(),), rows)

    def test_limits_without_a_verified_date(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.assert_declared((_price(),), (_limits(verified_on="2026-08-16"),))


class ErrorClassification(unittest.TestCase):
    """§7 control 11 / M1.53 — 403 is not enough to tell the two apart."""

    def test_billing_error_is_its_own_outcome(self) -> None:
        self.assertIs(
            llm.classify_api_error("billing_error", 403),
            llm.RequestOutcome.BALANCE_EXHAUSTED,
        )

    def test_permission_error_shares_the_status_code_and_not_the_meaning(self) -> None:
        # Same 403. Different operator response: one is a top-up, one is a key.
        self.assertIs(
            llm.classify_api_error("permission_error", 403),
            llm.RequestOutcome.INVALID_REQUEST,
        )

    def test_403_without_a_type_refuses_to_guess(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.classify_api_error(None, 403)

    def test_overload_and_5xx_are_retryable(self) -> None:
        self.assertTrue(llm.classify_api_error("overloaded_error", 529).retryable)
        self.assertTrue(llm.classify_api_error(None, 503).retryable)

    def test_invalid_request_is_never_retryable(self) -> None:
        self.assertFalse(llm.classify_api_error("invalid_request_error", 400).retryable)

    def test_balance_exhausted_is_not_retryable(self) -> None:
        # Retrying spends nothing and tells the operator nothing new.
        self.assertFalse(llm.RequestOutcome.BALANCE_EXHAUSTED.retryable)


class Estimation(unittest.TestCase):
    def test_batch_arithmetic_reproduces_the_spec_row(self) -> None:
        # §7.1's extraction row is $0.015 for ~30k batch input tokens. The point
        # is that the figure is derived from the dated table rather than being a
        # second copy of the number (M1.42's shape).
        est = llm.estimate_cost(
            input_tokens=30_000,
            output_tokens=0,
            provider="anthropic",
            model="claude-haiku-4-5",
            batch=True,
        )
        self.assertAlmostEqual(est.total_usd, 0.015, places=6)

    def test_web_search_fee_is_added_outside_the_batch_discount(self) -> None:
        # §7 control 8: the per-search charge is not discounted by the Batch API.
        est = llm.estimate_cost(
            input_tokens=0,
            output_tokens=0,
            provider="anthropic",
            model="claude-haiku-4-5",
            batch=True,
            web_searches=2,
        )
        self.assertAlmostEqual(est.total_usd, 0.02, places=6)

    def test_reservation_measures_input_and_bounds_output(self) -> None:
        calls: list[tuple[str, str]] = []

        def counter(*, system: str, user_text: str) -> int:
            calls.append((system, user_text))
            return 10_000

        requests = [
            llm.BatchRequest("a", "sys", "page a", {}, 2048),
            llm.BatchRequest("b", "sys", "page b", {}, 2048),
        ]
        est = llm.reserve_batch(
            requests,
            provider="anthropic",
            model="claude-haiku-4-5",
            count_tokens=counter,
            clearance=_cleared(),
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(est.input_tokens, 20_000)
        # Output is reserved at the requested ceiling: the only upper bound the
        # API guarantees is the one we asked for.
        self.assertEqual(est.output_tokens, 4096)

    def test_count_tokens_failure_aborts_rather_than_estimating(self) -> None:
        """M1.52. A fallback estimate is how an unmeasured number gets into the
        ledger looking measured, so the failure must propagate."""

        def counter(*, system: str, user_text: str) -> int:
            raise RuntimeError("network down")

        with self.assertRaises(RuntimeError):
            llm.reserve_batch(
                [llm.BatchRequest("a", "s", "u", {}, 2048)],
                provider="anthropic",
                model="claude-haiku-4-5",
                count_tokens=counter,
                clearance=_cleared(),
            )

    def test_output_above_the_model_cap_is_refused(self) -> None:
        # M1.50b: Haiku 4.5 caps at 64K, not the 128K every other model allows.
        with self.assertRaises(llm.LLMConfigError):
            llm.reserve_batch(
                [llm.BatchRequest("a", "s", "u", {}, 128_000)],
                provider="anthropic",
                model="claude-haiku-4-5",
                count_tokens=lambda **_: 10,
                clearance=_cleared(),
            )

    def test_input_above_the_context_window_is_refused(self) -> None:
        with self.assertRaises(llm.LLMConfigError):
            llm.reserve_batch(
                [llm.BatchRequest("a", "s", "u", {}, 2048)],
                provider="anthropic",
                model="claude-haiku-4-5",
                count_tokens=lambda **_: 500_000,
                clearance=_cleared(),
            )


class BatchDisposition(unittest.TestCase):
    """M1.51 — the partially-processed case arrives through the success path."""

    def test_a_batch_that_ended_with_expired_members_is_not_reconciled(self) -> None:
        """The headline. A batch reaches `processing_status: ended` carrying a
        mix of succeeded and expired; treating "ended" as "done" writes
        `reconciled` over companies that were never extracted."""
        items = [
            _item("a", llm.RequestOutcome.SUCCEEDED),
            _item("b", llm.RequestOutcome.EXPIRED),
        ]
        # 9b: `expired` now CLOSES the batch rather than leaving it "completed"
        # (M1.86). Nothing more arrives from this batch; §5.6 re-submits `b` as
        # new spend in a new batch that §7 reserves like any other. `expired` and
        # `failed` were declared in §4 and reachable from nothing before this.
        self.assertIs(
            llm.resolve_batch_status(items, expected=["a", "b"]),
            llm.BatchStatus.EXPIRED,
        )
        self.assertEqual(llm.resubmittable(items), ("b",))

    def test_all_succeeded_reconciles(self) -> None:
        items = [_item("a", llm.RequestOutcome.SUCCEEDED)]
        self.assertIs(
            llm.resolve_batch_status(items, expected=["a"]),
            llm.BatchStatus.RECONCILED,
        )
        self.assertEqual(llm.resubmittable(items), ())

    def test_invalid_request_owes_nothing_and_closes_the_batch(self) -> None:
        # Malformed will be malformed again; re-submitting is spend with a known
        # outcome, so the batch is finished even though a request failed.
        items = [
            _item("a", llm.RequestOutcome.SUCCEEDED),
            _item("b", llm.RequestOutcome.INVALID_REQUEST),
        ]
        self.assertIs(
            llm.resolve_batch_status(items, expected=["a", "b"]),
            llm.BatchStatus.RECONCILED,
        )
        self.assertEqual(llm.resubmittable(items), ())

    def test_server_error_closes_the_batch_as_failed(self) -> None:
        """9b: terminal for THIS batch, and distinct from `expired` because the
        two send an operator to different places — one waits, one retries."""
        items = [_item("a", llm.RequestOutcome.SERVER_ERROR)]
        self.assertIs(
            llm.resolve_batch_status(items, expected=["a"]), llm.BatchStatus.FAILED
        )
        self.assertEqual(llm.resubmittable(items), ("a",))

    def test_balance_exhausted_is_its_own_status_not_a_failure(self) -> None:
        # §7 control 11: `reconcile` must be able to say "the key ran dry".
        items = [
            _item("a", llm.RequestOutcome.SUCCEEDED),
            _item("b", llm.RequestOutcome.BALANCE_EXHAUSTED),
        ]
        self.assertIs(
            llm.resolve_batch_status(items, expected=["a", "b"]),
            llm.BatchStatus.BALANCE_EXHAUSTED,
        )

    def test_no_results_read_is_not_a_finished_batch(self) -> None:
        self.assertIs(
            llm.resolve_batch_status([], expected=["a"]), llm.BatchStatus.SUBMITTED
        )

    def test_a_request_with_no_result_at_all_keeps_the_batch_open(self) -> None:
        """**M1.86, measured before it was fixed.** Ten sent, eight returned:
        the version that took only `items` said RECONCILED and `resubmittable`
        said nothing, so two companies went unextracted with nothing naming
        them. §5.6 fact 2 is a rule about a SET and `request_count` is a number.
        """
        sent = [f"impressum:{i}:{i}" for i in range(10)]
        returned = [_item(cid, llm.RequestOutcome.SUCCEEDED) for cid in sent[:8]]
        self.assertIs(
            llm.resolve_batch_status(returned, expected=sent),
            llm.BatchStatus.COMPLETED,
        )
        # And the guard M1.51 built cannot see it: nothing is retryable.
        self.assertEqual(llm.resubmittable(returned), ())

    def test_both_balance_failure_seams_are_representable(self) -> None:
        """M1.53 — which one is real is unverified, so both must exist and the
        assumption must be a value rather than a comment."""
        self.assertIn(llm.RequestOutcome.BALANCE_EXHAUSTED, llm.RequestOutcome)
        self.assertIn(llm.BatchStatus.BALANCE_EXHAUSTED, llm.BatchStatus)
        self.assertIs(llm.ASSUMED_BALANCE_FAILURE_POINT, llm.BalanceFailurePoint.SUBMIT)


class ResultKeying(unittest.TestCase):
    """M1.51 — arbitrary order. Position-keyed reading is M1.17 with a new cause."""

    def test_results_are_found_by_custom_id_whatever_the_order(self) -> None:
        forwards = [
            _item("snocks.com", llm.RequestOutcome.SUCCEEDED),
            _item("ekomia.de", llm.RequestOutcome.EXPIRED),
        ]
        backwards = list(reversed(forwards))
        self.assertEqual(
            llm.index_by_custom_id(forwards)["ekomia.de"].outcome,
            llm.index_by_custom_id(backwards)["ekomia.de"].outcome,
        )

    def test_duplicate_custom_id_fails_rather_than_keeping_the_last(self) -> None:
        items = [
            _item("a", llm.RequestOutcome.SUCCEEDED),
            _item("a", llm.RequestOutcome.EXPIRED),
        ]
        with self.assertRaises(llm.LLMConfigError):
            llm.index_by_custom_id(items)


class CacheObservation(unittest.TestCase):
    """M1.50c — below the minimum the write silently does not happen."""

    def test_below_the_minimum_is_never_a_write(self) -> None:
        lim = llm.limits_for("anthropic", "claude-haiku-4-5")
        usage = llm.Usage(input_tokens=500, output_tokens=10)
        self.assertFalse(llm.cache_write_observed(usage, lim, prompt_tokens=500))

    def test_above_the_minimum_still_needs_the_observed_value(self) -> None:
        lim = llm.limits_for("anthropic", "claude-haiku-4-5")
        silent = llm.Usage(input_tokens=5000, output_tokens=10)
        self.assertFalse(llm.cache_write_observed(silent, lim, prompt_tokens=5000))
        written = llm.Usage(
            input_tokens=5000, output_tokens=10, cache_creation_input_tokens=5000
        )
        self.assertTrue(llm.cache_write_observed(written, lim, prompt_tokens=5000))


class StrictSchema(unittest.TestCase):
    def test_objects_gain_required_and_no_additional_properties(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "legal_name": {"type": "string"},
                "nested": {"type": "object", "properties": {"a": {"type": "string"}}},
            },
        }
        strict = llm.strict_json_schema(schema)
        self.assertEqual(strict["required"], ["legal_name", "nested"])
        self.assertFalse(strict["additionalProperties"])
        self.assertEqual(strict["properties"]["nested"]["required"], ["a"])
        self.assertFalse(strict["properties"]["nested"]["additionalProperties"])

    def test_the_input_schema_is_not_mutated(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        llm.strict_json_schema(schema)
        self.assertNotIn("required", schema)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
