"""`portal llm-batches` — §10.7b's closing procedure as a command (M1.104).

Three states, and the test pins that the command distinguishes them the way
§10.7b does: no key is OPEN and exit 2 (never zero), an empty listing is
CLOSED-at-zero, a non-empty listing is CLOSED-not-zero with every id printed.
It also pins what the module-level guard already enforces at import: the new
provider method is classified **free**, because listing what was paid for is
a read and not a spend.
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from portal import cli, llm, llm_anthropic


class _AutoPage:
    """The SDK's page object: iterating it fetches the NEXT page too, so a
    bare `for` walks the whole account whatever `limit` said."""

    def __init__(self, rows: list) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class _Batches:
    def __init__(self, rows: list) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return _AutoPage(self.rows)


def _client(rows: list) -> tuple[SimpleNamespace, _Batches]:
    batches = _Batches(rows)
    return SimpleNamespace(messages=SimpleNamespace(batches=batches)), batches


def _row(id_: str, status: str, **counts: int) -> SimpleNamespace:
    base = {"succeeded": 0, "errored": 0, "expired": 0, "canceled": 0, "processing": 0}
    base.update(counts)
    return SimpleNamespace(
        id=id_,
        processing_status=status,
        created_at="2026-08-21T04:00:00Z",
        expires_at="2026-08-22T04:00:00Z",
        request_counts=SimpleNamespace(**base),
    )


class LlmBatchesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(llm_anthropic.API_KEY_ENV, None)

    def run_cli(self, provider, limit: int = 20) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_llm_batches(limit, provider=provider)
        return code, out.getvalue(), err.getvalue()

    def test_list_batches_is_a_free_surface(self) -> None:
        self.assertIn("list_batches", llm_anthropic.FREE_SURFACES)
        self.assertNotIn("list_batches", llm_anthropic.PAID_SURFACES)

    def test_no_key_leaves_the_question_open_and_exits_2(self) -> None:
        code, out, err = self.run_cli(llm_anthropic.AnthropicProvider())
        self.assertEqual(code, 2)
        self.assertIn("OPEN", err)
        self.assertIn("not zero", err.lower())
        self.assertEqual(out, "")

    def test_empty_listing_closes_at_zero(self) -> None:
        client, batches = _client([])
        provider = llm_anthropic.AnthropicProvider(client=client)
        code, out, _ = self.run_cli(provider, limit=7)
        self.assertEqual(code, 0)
        self.assertIn("CLOSED with the answer ZERO", out)
        self.assertEqual(batches.calls, [{"limit": 7}])

    def test_listing_prints_every_id_and_the_resubmission_warning(self) -> None:
        client, _ = _client(
            [
                _row("msgbatch_01", "ended", succeeded=11, expired=2),
                _row("msgbatch_02", "in_progress", processing=5),
            ]
        )
        provider = llm_anthropic.AnthropicProvider(client=client)
        code, out, _ = self.run_cli(provider)
        self.assertEqual(code, 0)
        self.assertIn("msgbatch_01", out)
        self.assertIn("msgbatch_02", out)
        self.assertIn("NOT zero", out)
        self.assertIn("DOUBLE THE COST", out)

    def test_limit_bounds_the_listing_despite_auto_pagination(self) -> None:
        """Unit 10 audit (M1.108): `limit` is the SDK's page size and the
        page auto-fetches on iteration; the command must bound it itself."""
        client, _ = _client([_row(f"msgbatch_{i:02}", "ended") for i in range(7)])
        listed = llm_anthropic.AnthropicProvider(client=client).list_batches(limit=3)
        self.assertEqual(
            [b.provider_batch_id for b in listed],
            ["msgbatch_00", "msgbatch_01", "msgbatch_02"],
        )

    def test_listing_carries_the_counts_whole(self) -> None:
        client, _ = _client([_row("msgbatch_03", "ended", succeeded=3, errored=1)])
        (only,) = llm_anthropic.AnthropicProvider(client=client).list_batches()
        self.assertIsInstance(only, llm.BatchListing)
        self.assertEqual((only.succeeded, only.errored, only.total), (3, 1, 4))


if __name__ == "__main__":
    unittest.main()
