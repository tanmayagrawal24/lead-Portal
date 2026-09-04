"""§5.5a — the PageSpeed writer, its cache, its gate, and its key discipline.

**Nothing here contacts Google.** `pagespeed.PageSpeedClient` is a Protocol
and every test passes a fake; the one test that exercises the real
`HttpPageSpeedClient` does so through an `httpx.MockTransport`, to assert what
the request would carry and that a failure cannot leak the key. The suite
runs with `PAGESPEED_API_KEY` explicitly absent, the way CI runs without
`ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import httpx

from portal import cli, db, migrate, pagespeed, score


def _payload(score_value: float | None, **lighthouse: Any) -> dict[str, Any]:
    """A response in the shape the API reference documents."""
    result: dict[str, Any] = {
        "requestedUrl": "https://muster.de/",
        "finalUrl": "https://www.muster.de/",
        "lighthouseVersion": "12.6.0",
        "categories": {"performance": {"id": "performance", "score": score_value}},
    }
    result.update(lighthouse)
    return {
        "id": "https://www.muster.de/",
        "analysisUTCTimestamp": "2026-09-03T10:00:00.000Z",
        "lighthouseResult": result,
    }


class FakeClient:
    """Records the URLs it was asked to measure and answers from a script."""

    def __init__(self, answers: dict[str, dict[str, Any] | Exception]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def run(self, url: str, *, strategy: str) -> dict[str, Any]:
        self.calls.append((url, strategy))
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


class PageSpeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "portal.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        # The key is absent for every test unless one sets it.
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(pagespeed.API_KEY_ENV, None)

    # -- fixture writers ------------------------------------------------
    def company(
        self,
        domain: str,
        *,
        admitted: int | None = 1,
        homepage: bool = True,
        excluded: bool = False,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at, excluded, "
            "excluded_reason) VALUES (?, 'seed_csv', '2026-08-01T00:00:00Z', ?, ?)",
            (
                domain,
                int(excluded),
                "duplicate_site: x is company #1" if excluded else None,
            ),
        )
        company_id = int(cur.lastrowid or 0)
        if homepage:
            self.conn.execute(
                "INSERT INTO artifact (company_id, kind, url, http_status, "
                "content_hash, body_path, fetched_at) VALUES (?,'homepage',?,200,?,?,?)",
                (
                    company_id,
                    f"https://{domain}/",
                    f"h{company_id}",
                    "x.html",
                    "2026-08-01",
                ),
            )
        if admitted is not None:
            run_id = self.run_row("score-p1")
            self.conn.execute(
                "INSERT INTO signal (company_id, run_id, key, value_num, method, "
                "evidence_url, observed_at) VALUES (?,?,'gate.phase2_admitted',?,"
                "'deterministic','',datetime('now'))",
                (company_id, run_id, admitted),
            )
        return company_id

    def run_row(self, stage: str, *, finished: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO run (started_at, finished_at, stage) VALUES "
            "('2026-08-02T00:00:00Z', ?, ?)",
            ("2026-08-02T00:00:00Z" if finished else None, stage),
        )
        return int(cur.lastrowid or 0)

    def signals(self, company_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT s.*, r.stage FROM signal s JOIN run r ON r.id = s.run_id "
                "WHERE s.company_id = ? AND s.key = ? ORDER BY s.id",
                (company_id, pagespeed.SIGNAL_KEY),
            )
        )

    def served(self, company_id: int) -> float | None:
        row = self.conn.execute(
            "SELECT lighthouse_perf FROM company_profile WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        return row["lighthouse_perf"]


class TheMigration(PageSpeedTestCase):
    def test_016_adds_the_counter_beside_its_siblings(self) -> None:
        columns = {
            row["name"]: row["dflt_value"]
            for row in self.conn.execute("PRAGMA table_info(run)")
        }
        self.assertIn("pagespeed_calls", columns)
        self.assertEqual(columns["pagespeed_calls"], columns["places_calls"])


class NoKey(PageSpeedTestCase):
    def test_no_key_means_report_and_stop_before_any_row_is_written(self) -> None:
        """§7 control 9's last sentence. The fake is not even consulted, and
        no run row exists — the tool stopped rather than degrading to the
        keyless quota."""
        self.company("muster.de")
        with self.assertRaises(pagespeed.MissingKeyError) as caught:
            pagespeed.run(self.conn)
        self.assertIn(pagespeed.API_KEY_ENV, str(caught.exception))
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM run WHERE stage = ?", (pagespeed.STAGE,)
            ).fetchone()[0],
            0,
        )

    def test_the_module_imports_and_plans_without_a_key(self) -> None:
        self.company("muster.de")
        prepared = pagespeed.plan(self.conn)
        self.assertEqual([t.domain for t in prepared.targets], ["muster.de"])


class TheWriter(PageSpeedTestCase):
    def test_a_measurement_is_one_signal_evidenced_on_the_homepage_row(self) -> None:
        company_id = self.company("muster.de")
        client = FakeClient({"https://muster.de/": _payload(0.31)})
        result = pagespeed.run(self.conn, client=client)

        self.assertEqual(client.calls, [("https://muster.de/", pagespeed.STRATEGY)])
        (row,) = self.signals(company_id)
        self.assertEqual(row["value_num"], 31.0)
        self.assertEqual(row["method"], "deterministic")
        self.assertEqual(row["evidence_url"], "https://muster.de/")
        artifact = self.conn.execute(
            "SELECT id FROM artifact WHERE company_id = ? AND kind = 'homepage'",
            (company_id,),
        ).fetchone()
        self.assertEqual(row["artifact_id"], artifact["id"])
        self.assertEqual(row["stage"], pagespeed.STAGE)
        self.assertIn("strategy=mobile", row["value_text"])
        self.assertIn("final_url=https://www.muster.de/", row["value_text"])
        self.assertIn("lighthouse=12.6.0", row["value_text"])

        run = self.conn.execute(
            "SELECT * FROM run WHERE id = ?", (result.run_id,)
        ).fetchone()
        self.assertEqual(run["stage"], pagespeed.STAGE)
        self.assertEqual(run["pagespeed_calls"], 1)
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(run["companies_seen"], 1)
        self.assertEqual(self.served(company_id), 31.0)

    def test_the_reader_that_was_waiting_fires_on_it(self) -> None:
        """End to end through the existing reader: `opp.slow_site` (+10) has
        been declared since M0 and dormant since. Below 50 it fires; at or
        above it declines. Neither the rule nor its threshold is touched."""
        slow = self.company("slow.de")
        fast = self.company("fast.de")
        pagespeed.run(
            self.conn,
            client=FakeClient(
                {"https://slow.de/": _payload(0.31), "https://fast.de/": _payload(0.92)}
            ),
        )
        _, results = score.run(self.conn, phase=2)
        by_id = {r.company_id: r for r in results}
        points = {
            c.rule_id: c.points
            for c in by_id[slow].components
            if c.rule_id == "opp.slow_site"
        }
        self.assertEqual(points, {"opp.slow_site": 10})
        self.assertNotIn("opp.slow_site", {c.rule_id for c in by_id[fast].components})

    def test_a_response_with_no_score_is_a_recorded_failure_not_a_signal(self) -> None:
        """A7 on the rule M3's audit guarded: a null score written as 0 would
        fire `opp.slow_site` on a shop Lighthouse never reached."""
        company_id = self.company("down.de")
        client = FakeClient(
            {
                "https://down.de/": _payload(
                    None,
                    runtimeError={
                        "code": "FAILED_DOCUMENT_REQUEST",
                        "message": "net::ERR",
                    },
                )
            }
        )
        result = pagespeed.run(self.conn, client=client)
        self.assertEqual(self.signals(company_id), [])
        (outcome,) = result.outcomes
        self.assertEqual(outcome.status, "failed")
        self.assertIn("FAILED_DOCUMENT_REQUEST", outcome.detail)
        # The request was issued, so it counts against the quota regardless.
        run = self.conn.execute(
            "SELECT * FROM run WHERE id = ?", (result.run_id,)
        ).fetchone()
        self.assertEqual(run["pagespeed_calls"], 1)
        self.assertIsNotNone(run["finished_at"])

    def test_one_failure_does_not_stop_the_others(self) -> None:
        a = self.company("a.de")
        b = self.company("b.de")
        client = FakeClient(
            {
                "https://a.de/": pagespeed.PageSpeedError("HTTP 500"),
                "https://b.de/": _payload(0.5),
            }
        )
        pagespeed.run(self.conn, client=client)
        self.assertEqual(self.signals(a), [])
        self.assertEqual(len(self.signals(b)), 1)
        self.assertEqual(self.served(b), 50.0)

    def test_an_unexpected_exception_aborts_and_marks_the_run(self) -> None:
        """M1.39: a crashed run is marked aborted and `company_profile` serves
        nothing from it — including the companies it did write."""
        a = self.company("a.de")
        self.company("b.de")
        client = FakeClient(
            {"https://a.de/": _payload(0.4), "https://b.de/": RuntimeError("boom")}
        )
        with self.assertRaises(RuntimeError):
            pagespeed.run(self.conn, client=client)
        run = self.conn.execute(
            "SELECT * FROM run WHERE stage = ?", (pagespeed.STAGE,)
        ).fetchone()
        self.assertIn("RuntimeError", run["aborted_reason"])
        self.assertIsNone(run["finished_at"])
        self.assertEqual(len(self.signals(a)), 1)
        self.assertIsNone(self.served(a))

    def test_the_per_run_ceiling_refuses_the_whole_plan(self) -> None:
        for n in range(3):
            self.company(f"s{n}.de")
        client = FakeClient({})
        with self.assertRaises(pagespeed.PageSpeedError):
            pagespeed.run(self.conn, client=client, max_calls=2)
        self.assertEqual(client.calls, [])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM run WHERE stage = ?", (pagespeed.STAGE,)
            ).fetchone()[0],
            0,
        )


class TheGate(PageSpeedTestCase):
    def test_only_admitted_companies_with_a_homepage_are_measured(self) -> None:
        admitted = self.company("in.de")
        self.company("stopped.de", admitted=0)
        self.company("unscored.de", admitted=None)
        self.company("dup.de", excluded=True)
        self.company("nohome.de", homepage=False)
        prepared = pagespeed.plan(self.conn)
        self.assertEqual([t.company_id for t in prepared.targets], [admitted])
        reasons = dict(prepared.skipped)
        self.assertIn("§5.4 gate", reasons["stopped.de"])
        self.assertIn("not scored", reasons["unscored.de"])
        self.assertIn("excluded (§6.4)", reasons["dup.de"])
        self.assertIn("no fetched homepage", reasons["nohome.de"])

    def test_a_withheld_company_is_never_sent_to_the_client(self) -> None:
        self.company("stopped.de", admitted=0)
        client = FakeClient({})
        pagespeed.run(self.conn, client=client)
        self.assertEqual(client.calls, [])


class TheCache(PageSpeedTestCase):
    """§5.3: cache by age, do not re-run within 30 days — and under migration
    006's per-(company, stage) scoping, "cached" means the earlier run stays
    that company's authority, which is what keeps the value being served."""

    def test_within_30_days_nothing_is_called_and_the_value_is_still_served(self):
        company_id = self.company("muster.de")
        first = FakeClient({"https://muster.de/": _payload(0.31)})
        pagespeed.run(self.conn, client=first)

        second = FakeClient({"https://muster.de/": _payload(0.99)})
        result = pagespeed.run(self.conn, client=second)

        self.assertEqual(second.calls, [])
        (cached,) = result.plan.cached
        self.assertEqual((cached.domain, cached.score), ("muster.de", 31))
        # The second run finished with no row for this company — and the
        # profile still serves the first run's number, not NULL.
        self.assertEqual(len(self.signals(company_id)), 1)
        self.assertEqual(self.served(company_id), 31.0)
        run = self.conn.execute(
            "SELECT * FROM run WHERE id = ?", (result.run_id,)
        ).fetchone()
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(run["pagespeed_calls"], 0)

    def test_after_30_days_it_is_measured_again_and_the_new_value_wins(self):
        company_id = self.company("muster.de")
        pagespeed.run(
            self.conn, client=FakeClient({"https://muster.de/": _payload(0.31)})
        )
        later = datetime.now(UTC) + timedelta(days=pagespeed.CACHE_DAYS, seconds=1)
        second = FakeClient({"https://muster.de/": _payload(0.72)})
        pagespeed.run(self.conn, client=second, now=later)
        self.assertEqual(len(second.calls), 1)
        self.assertEqual(len(self.signals(company_id)), 2)
        self.assertEqual(self.served(company_id), 72.0)

    def test_an_aborted_runs_measurement_does_not_count_as_cached(self):
        """The profile serves nothing from an aborted run (007), so neither
        may the cache: a company whose only number is from a crashed run is
        measured again."""
        company_id = self.company("muster.de")
        run_id = self.run_row(pagespeed.STAGE, finished=False)
        self.conn.execute("UPDATE run SET aborted_reason = 'x' WHERE id = ?", (run_id,))
        self.conn.execute(
            "INSERT INTO signal (company_id, run_id, key, value_num, method, "
            "evidence_url, observed_at) VALUES (?,?,?,31,'deterministic',"
            "'https://muster.de/', ?)",
            (company_id, run_id, pagespeed.SIGNAL_KEY, pagespeed._utc_now()),
        )
        client = FakeClient({"https://muster.de/": _payload(0.5)})
        pagespeed.run(self.conn, client=client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.served(company_id), 50.0)


class ReadingTheResponse(unittest.TestCase):
    def test_the_score_is_rounded_onto_0_to_100(self) -> None:
        self.assertEqual(pagespeed.parse_measurement(_payload(0.93)).score, 93)
        self.assertEqual(pagespeed.parse_measurement(_payload(0.0)).score, 0)
        self.assertEqual(pagespeed.parse_measurement(_payload(1.0)).score, 100)

    def test_a_score_outside_the_unit_interval_is_refused(self) -> None:
        with self.assertRaises(pagespeed.PageSpeedError):
            pagespeed.parse_measurement(_payload(93))

    def test_a_missing_lighthouse_result_is_refused(self) -> None:
        with self.assertRaises(pagespeed.PageSpeedError):
            pagespeed.parse_measurement({"id": "x"})

    def test_a_null_score_names_the_runtime_error(self) -> None:
        with self.assertRaises(pagespeed.PageSpeedError) as caught:
            pagespeed.parse_measurement(
                _payload(None, runtimeError={"code": "NO_FCP", "message": "no paint"})
            )
        self.assertIn("NO_FCP", str(caught.exception))


class TheHttpClient(unittest.TestCase):
    """The one implementation of the Protocol, through a MockTransport."""

    KEY = "AIzaSy-not-a-real-key-0000"

    def _client(self, handler) -> pagespeed.HttpPageSpeedClient:
        client = pagespeed.HttpPageSpeedClient(
            self.KEY, transport=httpx.MockTransport(handler)
        )
        self.addCleanup(client.close)
        return client

    def test_the_request_carries_url_strategy_category_and_key(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_payload(0.5))

        payload = self._client(handler).run("https://muster.de/", strategy="mobile")
        self.assertEqual(pagespeed.parse_measurement(payload).score, 50)
        (request,) = seen
        self.assertEqual(request.url.host, "pagespeedonline.googleapis.com")
        params = request.url.params
        self.assertEqual(params["url"], "https://muster.de/")
        self.assertEqual(params["strategy"], "mobile")
        self.assertEqual(params["category"], "performance")
        self.assertEqual(params["key"], self.KEY)
        self.assertIn("CreativePotatoesBot", request.headers["user-agent"])

    def test_a_failure_never_carries_the_key(self) -> None:
        """httpx's own error text embeds the request URL, `key=` included."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": {"message": "denied"}})

        with self.assertRaises(pagespeed.PageSpeedError) as caught:
            self._client(handler).run("https://muster.de/", strategy="mobile")
        self.assertIn("403", str(caught.exception))
        self.assertNotIn(self.KEY, repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_a_transport_error_is_a_pagespeed_error_without_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with self.assertRaises(pagespeed.PageSpeedError) as caught:
            self._client(handler).run("https://muster.de/", strategy="mobile")
        self.assertIn("ConnectError", str(caught.exception))
        self.assertNotIn(self.KEY, str(caught.exception))


class TheCommand(PageSpeedTestCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--db", str(self.db_path), *argv])
        return code, out.getvalue() + err.getvalue()

    def test_dry_run_needs_no_key_and_makes_no_run_row(self) -> None:
        self.company("muster.de")
        self.company("stopped.de", admitted=0)
        code, text = self.run_cli("pagespeed", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertIn("1 homepage(s) would be measured", text)
        self.assertIn("stopped.de", text)
        self.assertIn("Nothing was measured", text)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM run WHERE stage = ?", (pagespeed.STAGE,)
            ).fetchone()[0],
            0,
        )

    def test_without_a_key_the_real_run_says_so_and_exits_2(self) -> None:
        self.company("muster.de")
        code, text = self.run_cli("pagespeed")
        self.assertEqual(code, 2)
        self.assertIn(pagespeed.API_KEY_ENV, text)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM run WHERE stage = ?", (pagespeed.STAGE,)
            ).fetchone()[0],
            0,
        )

    def test_max_calls_below_one_is_refused(self) -> None:
        code, text = self.run_cli("pagespeed", "--max-calls", "0")
        self.assertEqual(code, 2)
        self.assertIn("--max-calls", text)

    def test_a_stale_schema_is_refused_before_anything_else(self) -> None:
        self.conn.execute("PRAGMA user_version = 15")
        code, text = self.run_cli("pagespeed", "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("portal init", text)


if __name__ == "__main__":
    unittest.main()
