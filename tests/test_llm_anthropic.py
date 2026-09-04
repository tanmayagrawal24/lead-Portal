"""The Anthropic provider (`portal/llm_anthropic.py`, build step 1).

Driven through a **fake SDK client** rather than the network. That is not a
convenience: this is new code with no caller, and the failures it exists to
handle — a dry key, a batch that ended carrying expired members, results in the
wrong order — cannot be provoked on demand against a live key. A fake is the
only way to test the paths that matter, and it is honest about what it does not
prove: the *shape* of the SDK objects here is asserted from documentation, and
the one behaviour nobody could verify (where a prepaid failure surfaces, M1.53)
is tested at both seams precisely because we do not know which one fires.

Every test here runs without `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from portal import cli, db, ledger, llm, llm_anthropic, migrate


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


class FakeError(Exception):
    """An SDK exception carrying the two fields `_error_fields` reads."""

    def __init__(self, error_type: str | None, status_code: int | None) -> None:
        super().__init__(error_type or "error")
        self.type = error_type
        self.status_code = status_code


class FakeErrorWithBody(Exception):
    """The other shape: `.type` absent, the type nested under `.body`."""

    def __init__(self, error_type: str, status_code: int) -> None:
        super().__init__(error_type)
        self.body = {"error": {"type": error_type, "message": "no funds"}}
        self.status_code = status_code


def _message(payload_json: str, **usage) -> SimpleNamespace:
    counts = {"input_tokens": 1000, "output_tokens": 50}
    counts.update(usage)
    return SimpleNamespace(
        model="claude-haiku-4-5",
        content=[SimpleNamespace(type="text", text=payload_json)],
        usage=SimpleNamespace(**counts),
    )


def _result(custom_id: str, kind: str, **kw) -> SimpleNamespace:
    inner = SimpleNamespace(type=kind, **kw)
    return SimpleNamespace(custom_id=custom_id, result=inner)


class FakeBatches:
    def __init__(self, owner: FakeClient) -> None:
        self._owner = owner

    def create(self, *, requests):
        self._owner.submitted = requests
        if self._owner.submit_error is not None:
            raise self._owner.submit_error
        return SimpleNamespace(id="msgbatch_fake")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status=self._owner.status)

    def results(self, batch_id):
        return iter(self._owner.results)


class FakeMessages:
    def __init__(self, owner: FakeClient) -> None:
        self.batches = FakeBatches(owner)
        self._owner = owner

    def count_tokens(self, *, model, system, messages):
        self._owner.counted.append((model, system, messages))
        return SimpleNamespace(input_tokens=12_345)


class FakeClient:
    def __init__(self, *, status="ended", results=(), submit_error=None) -> None:
        self.status = status
        self.results = list(results)
        self.submit_error = submit_error
        self.submitted = None
        self.counted: list[tuple] = []
        self.messages = FakeMessages(self)


SCHEMA = {
    "type": "object",
    "properties": {"legal_name": {"type": "string"}, "city": {"type": "string"}},
}


def _request(custom_id: str = "impressum-1-31") -> llm.BatchRequest:
    return llm.BatchRequest(
        custom_id=custom_id,
        system="Return null for any field not present on the page.",
        user_text="<Impressum text>",
        json_schema=SCHEMA,
        max_tokens=2048,
    )


class Construction(unittest.TestCase):
    def test_an_undeclared_model_fails_at_construction(self) -> None:
        # Not at submit time, when a page is already in flight and a run is
        # already underway. M1.50: a model with no declared limits would
        # otherwise be called at an assumed parameter surface.
        with self.assertRaises(llm.LLMConfigError):
            llm_anthropic.AnthropicProvider(model="claude-opus-5")

    def test_the_module_imports_and_builds_params_without_a_key(self) -> None:
        provider = llm_anthropic.AnthropicProvider(client=None)
        self.assertEqual(provider.name, "anthropic")
        self.assertIsInstance(provider.build_params(_request()), dict)


class RequestConstruction(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = llm_anthropic.AnthropicProvider()

    def test_neither_effort_nor_thinking_is_sent(self) -> None:
        """M1.50a. `output_config.effort` errors on Haiku 4.5 and adaptive
        thinking is unavailable, so the stage sends neither — sidestepping the
        difference rather than encoding a workaround for it."""
        params = self.provider.build_params(_request())
        self.assertNotIn("thinking", params)
        self.assertNotIn("effort", params["output_config"])

    def test_structured_output_carries_a_strict_schema(self) -> None:
        params = self.provider.build_params(_request())
        schema = params["output_config"]["format"]["schema"]
        self.assertEqual(params["output_config"]["format"]["type"], "json_schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["city", "legal_name"])

    def test_output_above_the_model_cap_is_refused_before_submission(self) -> None:
        oversized = llm.BatchRequest("a", "s", "u", SCHEMA, 128_000)
        with self.assertRaises(llm.LLMConfigError):
            self.provider.build_params(oversized)


class Submission(unittest.TestCase):
    def test_a_successful_submit_returns_the_provider_batch_id(self) -> None:
        client = FakeClient()
        provider = llm_anthropic.AnthropicProvider(client=client)
        self.assertEqual(
            provider.submit_batch([_request()], clearance=_cleared()), "msgbatch_fake"
        )
        self.assertEqual(client.submitted[0]["custom_id"], "impressum-1-31")

    def test_a_dry_key_at_submit_raises_its_own_exception(self) -> None:
        """M1.53 seam one. `billing_error` is a 403 and so is `permission_error`;
        only `.type` separates them, and only one of them is a top-up."""
        client = FakeClient(submit_error=FakeError("billing_error", 403))
        provider = llm_anthropic.AnthropicProvider(client=client)
        with self.assertRaises(llm_anthropic.BalanceExhausted) as caught:
            provider.submit_batch([_request()], clearance=_cleared())
        self.assertIn("prepaid balance", str(caught.exception))

    def test_the_error_type_is_read_from_the_body_when_absent_on_the_exception(
        self,
    ) -> None:
        client = FakeClient(submit_error=FakeErrorWithBody("billing_error", 403))
        provider = llm_anthropic.AnthropicProvider(client=client)
        with self.assertRaises(llm_anthropic.BalanceExhausted):
            provider.submit_batch([_request()], clearance=_cleared())

    def test_a_permission_error_is_not_reported_as_a_dry_key(self) -> None:
        client = FakeClient(submit_error=FakeError("permission_error", 403))
        provider = llm_anthropic.AnthropicProvider(client=client)
        with self.assertRaises(FakeError):
            provider.submit_batch([_request()], clearance=_cleared())

    def test_an_overload_propagates_unchanged(self) -> None:
        client = FakeClient(submit_error=FakeError("overloaded_error", 529))
        provider = llm_anthropic.AnthropicProvider(client=client)
        with self.assertRaises(FakeError):
            provider.submit_batch([_request()], clearance=_cleared())


class Polling(unittest.TestCase):
    def test_an_unfinished_batch_reads_no_results(self) -> None:
        client = FakeClient(status="in_progress")
        provider = llm_anthropic.AnthropicProvider(client=client)
        result = provider.poll_batch("msgbatch_fake")
        self.assertIs(result.status, llm.BatchStatus.SUBMITTED)
        self.assertEqual(result.items, ())

    def test_results_are_mapped_by_custom_id_not_by_position(self) -> None:
        """M1.51. Submitted a, b; returned b, a. Reading by index attributes
        b's legal name to a — and substring verification passes, because the
        name genuinely is on the page it was read from."""
        client = FakeClient(
            results=[
                _result("ekomia.de", "succeeded", message=_message('{"city": "Bonn"}')),
                _result(
                    "snocks.com", "succeeded", message=_message('{"city": "Berlin"}')
                ),
            ]
        )
        provider = llm_anthropic.AnthropicProvider(client=client)
        by_id = llm.index_by_custom_id(provider.poll_batch("b").items)
        self.assertEqual(by_id["snocks.com"].extraction.payload["city"], "Berlin")
        self.assertEqual(by_id["ekomia.de"].extraction.payload["city"], "Bonn")

    def test_an_ended_batch_with_expired_members_is_not_reconciled(self) -> None:
        """M1.51's headline, end to end through the provider: `processing_status`
        is `ended` and the batch is still owed work."""
        client = FakeClient(
            results=[
                _result("a", "succeeded", message=_message("{}")),
                _result("b", "expired"),
            ]
        )
        provider = llm_anthropic.AnthropicProvider(client=client)
        result = provider.poll_batch("msgbatch_fake")
        # The PROVIDER-side status only (M1.86): this layer does not know what
        # was sent, so "every request" and "every result" are the same thing
        # from inside it. `reconcile` re-resolves against `llm_batch_request`.
        self.assertIs(result.status, llm.BatchStatus.EXPIRED)
        self.assertEqual(llm.resubmittable(result.items), ("b",))

    def test_errored_splits_into_retryable_and_not(self) -> None:
        client = FakeClient(
            results=[
                _result(
                    "bad",
                    "errored",
                    error=SimpleNamespace(type="invalid_request_error", message="nope"),
                ),
                _result(
                    "flaky",
                    "errored",
                    error=SimpleNamespace(type="api_error", message="boom"),
                ),
            ]
        )
        provider = llm_anthropic.AnthropicProvider(client=client)
        by_id = llm.index_by_custom_id(provider.poll_batch("b").items)
        self.assertIs(by_id["bad"].outcome, llm.RequestOutcome.INVALID_REQUEST)
        self.assertIs(by_id["flaky"].outcome, llm.RequestOutcome.SERVER_ERROR)
        self.assertEqual(by_id["bad"].error_message, "nope")

    def test_a_dry_key_inside_results_is_the_other_seam(self) -> None:
        """M1.53 seam two. Unverified which one is real, so both are exercised:
        here the batch was accepted and the balance emptied mid-flight."""
        client = FakeClient(
            results=[
                _result("a", "succeeded", message=_message("{}")),
                _result(
                    "b",
                    "errored",
                    error=SimpleNamespace(type="billing_error", message="no funds"),
                ),
            ]
        )
        provider = llm_anthropic.AnthropicProvider(client=client)
        result = provider.poll_batch("msgbatch_fake")
        self.assertIs(result.status, llm.BatchStatus.BALANCE_EXHAUSTED)

    def test_an_unknown_result_type_refuses_rather_than_guessing(self) -> None:
        client = FakeClient(results=[_result("a", "something_new")])
        provider = llm_anthropic.AnthropicProvider(client=client)
        with self.assertRaises(llm.LLMConfigError):
            provider.poll_batch("msgbatch_fake")

    def test_a_duplicate_custom_id_from_the_provider_fails_loudly(self) -> None:
        client = FakeClient(
            results=[
                _result("a", "succeeded", message=_message('{"city": "Bonn"}')),
                _result("a", "succeeded", message=_message('{"city": "Berlin"}')),
            ]
        )
        provider = llm_anthropic.AnthropicProvider(client=client)
        with self.assertRaises(llm.LLMConfigError):
            provider.poll_batch("msgbatch_fake")


class UsageReading(unittest.TestCase):
    def test_cache_creation_tokens_are_carried_through(self) -> None:
        """M1.50c — the only way to know whether the cache write happened."""
        client = FakeClient(
            results=[
                _result(
                    "a",
                    "succeeded",
                    message=_message("{}", cache_creation_input_tokens=0),
                )
            ]
        )
        provider = llm_anthropic.AnthropicProvider(client=client)
        item = provider.poll_batch("b").items[0]
        limits = provider.limits()
        self.assertEqual(item.extraction.usage.cache_creation_input_tokens, 0)
        self.assertFalse(
            llm.cache_write_observed(item.extraction.usage, limits, prompt_tokens=5000)
        )

    def test_token_counter_calls_count_tokens_for_this_model(self) -> None:
        client = FakeClient()
        provider = llm_anthropic.AnthropicProvider(client=client)
        estimate = llm.reserve_batch(
            [_request()],
            provider=provider.name,
            model=provider.model,
            count_tokens=provider.token_counter(),
            clearance=_cleared(),
        )
        self.assertEqual(client.counted[0][0], "claude-haiku-4-5")
        self.assertEqual(estimate.input_tokens, 12_345)


class NoKey(unittest.TestCase):
    def test_a_network_method_without_a_key_says_so_before_trying(self) -> None:
        # Cleared explicitly rather than relying on this machine not having one:
        # a test that passes because of the developer's environment is not a test.
        provider = llm_anthropic.AnthropicProvider()
        with (
            mock.patch.dict(os.environ, {llm_anthropic.API_KEY_ENV: ""}, clear=False),
            self.assertRaises(llm_anthropic.MissingKeyError),
        ):
            provider.submit_batch([_request()], clearance=_cleared())


class PricesCommand(unittest.TestCase):
    """`portal llm-prices` — the tables made readable before anything spends."""

    def setUp(self) -> None:
        # `--reserve` consults the §7 control 2 ledger, so it needs a real
        # database. This used to pass on any machine that happened to have run
        # `portal init` and failed in CI, which is M1.64's shape: a test whose
        # result depends on the developer's environment.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "portal.db"
        conn = db.connect(self.db)
        migrate.apply_pending(conn)
        conn.close()

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_it_prints_every_dated_row_and_the_facts_that_differ(self) -> None:
        code, text = self.run_cli("llm-prices")
        self.assertEqual(code, 0)
        self.assertIn("2026-06-24", text)  # the price as-of date
        self.assertIn("ERRORS on this model", text)  # M1.50a
        self.assertIn("64,000", text)  # M1.50b
        self.assertIn("4,096", text)  # M1.50c
        self.assertIn("UNVERIFIED", text)  # M1.53

    def test_it_reproduces_the_spec_extraction_row_from_the_table(self) -> None:
        # §7.1 says $0.015 per advancing company. Derived here, not restated.
        _, text = self.run_cli("llm-prices")
        self.assertIn("$0.0150 per advancing company", text)

    def test_the_reservation_refuses_to_guess_without_a_key(self) -> None:
        with mock.patch.dict(os.environ, {llm_anthropic.API_KEY_ENV: ""}, clear=False):
            code, text = self.run_cli(
                "--db", str(self.db), "llm-prices", "--reserve", "40"
            )
        self.assertEqual(code, 2)
        self.assertIn("cannot measure", text)
        self.assertIn("heuristic is refused", text)

    def test_the_ledger_is_consulted_before_the_key_is_even_looked_for(self) -> None:
        """§7 control 2 before §7 control 4. A runaway that is allowed to price
        itself first has already made the call the ceiling exists to refuse."""
        conn = db.connect(self.db)
        conn.execute(
            "INSERT INTO run (started_at, stage, est_cost_usd) "
            "VALUES (datetime('now','-1 days'), 'extract_p2', ?)",
            (ledger.MONTHLY_CEILING_USD * 20,),
        )
        conn.close()
        with mock.patch.dict(os.environ, {llm_anthropic.API_KEY_ENV: ""}, clear=False):
            code, text = self.run_cli(
                "--db", str(self.db), "llm-prices", "--reserve", "40"
            )
        self.assertEqual(code, 2)
        self.assertIn("§7 control 2", text)
        self.assertIn("runaway guard", text)
        # The key error is never reached: the ledger refused first.
        self.assertNotIn("cannot measure", text)

    def test_an_unreadable_ledger_refuses_rather_than_reading_as_zero(self) -> None:
        """A database with no `run` table is not a database with no spend.
        Fails closed, and names the fix instead of raising a traceback —
        this is the case that passed locally and failed in CI."""
        missing = Path(self._tmp.name) / "never-initialised.db"
        code, text = self.run_cli("--db", str(missing), "llm-prices", "--reserve", "40")
        self.assertEqual(code, 2)
        self.assertIn("not readable", text)
        self.assertIn("portal init", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
