"""§5.1 / M8 — `portal discover`, fixtures only (M1.107).

The brief's definition of done: *"Field mask is exactly `displayName`,
`websiteUri`, `formattedAddress`."* That is asserted on the wire — the header
httpx actually sends — not on a constant. The rest pins §5.1's dedupe on the
normalised domain, the request cap, `run.places_calls` counted as issued, the
keyless dry run, and that `fetch` without `--seed` reaches what `discover`
wrote.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from portal import cli, db, discover, migrate


def _page(places: list[dict], token: str | None = None) -> dict:
    payload: dict = {"places": places}
    if token:
        payload["nextPageToken"] = token
    return payload


def _place(name: str, website: str | None, address: str) -> dict:
    item: dict = {
        "displayName": {"text": name, "languageCode": "de"},
        "formattedAddress": address,
    }
    if website:
        item["websiteUri"] = website
    return item


class ScriptedClient:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[str | None] = []

    def search(self, query: str, *, page_token: str | None) -> dict:
        self.calls.append(page_token)
        return self.pages[len(self.calls) - 1]


class DiscoverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "portal.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(discover.API_KEY_ENV, None)

    def cli_out(self, **kwargs) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        kwargs.setdefault("region", "NRW")
        kwargs.setdefault("submit", False)
        kwargs.setdefault("dry_run", False)
        kwargs.setdefault("max_calls", discover.MAX_CALLS_PER_RUN)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_discover(self.db_path, "Zahnpflege Onlineshop", **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_field_mask_is_exactly_the_three_fields_on_the_wire(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_page([]))

        client = discover.HttpPlacesClient("k", transport=httpx.MockTransport(handler))
        client.search("x", page_token=None)
        (request,) = seen
        self.assertEqual(
            request.headers["x-goog-fieldmask"],
            "places.displayName,places.websiteUri,places.formattedAddress,nextPageToken",
        )
        self.assertNotIn("rating", request.headers["x-goog-fieldmask"])
        self.assertEqual(request.headers["x-goog-api-key"], "k")
        self.assertNotIn("k", str(request.url))
        self.assertEqual(json.loads(request.content)["textQuery"], "x")

    def test_http_error_never_carries_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "denied"})

        client = discover.HttpPlacesClient(
            "sekrit", transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(discover.PlacesError) as ctx:
            client.search("x", page_token=None)
        self.assertNotIn("sekrit", str(ctx.exception))

    def test_dedupes_on_the_normalised_domain_and_skips_no_website(self) -> None:
        client = ScriptedClient(
            [
                _page(
                    [
                        _place(
                            "Zahn Shop GmbH",
                            "https://WWW.Zahn-Shop.de/start",
                            "Musterstr. 1, 40210 Düsseldorf, Deutschland",
                        ),
                        _place(
                            "Zahn Shop (Filiale)",
                            "http://zahn-shop.de",
                            "x, 40210 Düsseldorf, Deutschland",
                        ),
                        _place("Ohne Web", None, "y"),
                        _place("Kaputt", "not a url", "z"),
                    ]
                )
            ]
        )
        report = discover.run(self.conn, client, "Zahnpflege", region="NRW")
        self.assertEqual(report.calls, 1)
        self.assertEqual(report.inserted, 1)
        self.assertEqual(report.no_website, 1)
        self.assertEqual(report.unusable, 1)
        row = self.conn.execute(
            "SELECT domain, legal_name, city, postal_code, country, discovery_source, discovery_query "
            "FROM company"
        ).fetchone()
        self.assertEqual(
            tuple(row),
            (
                "zahn-shop.de",
                "Zahn Shop GmbH",
                "Düsseldorf",
                "40210",
                "DE",
                "places",
                "Zahnpflege NRW",
            ),
        )
        # A second run with an overlapping page inserts nothing twice.
        report = discover.run(
            self.conn, ScriptedClient(client.pages), "Zahnpflege", region="NRW"
        )
        self.assertEqual(report.inserted, 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], 1
        )

    def test_pagination_stops_at_the_cap_and_counts_issued_requests(self) -> None:
        pages = [
            _page([_place(f"S{i}", f"https://s{i}.de", "")], token=f"t{i}")
            for i in range(5)
        ]
        client = ScriptedClient(pages)
        report = discover.run(self.conn, client, "q", max_calls=3)
        self.assertEqual(report.calls, 3)
        self.assertTrue(report.capped)
        self.assertEqual(client.calls, [None, "t0", "t1"])
        run = self.conn.execute(
            "SELECT places_calls, finished_at, companies_seen FROM run"
        ).fetchone()
        self.assertEqual((run[0], run[2]), (3, 3))
        self.assertIsNotNone(run[1])

    def test_a_failed_request_is_still_counted_and_the_run_marked(self) -> None:
        class Boom:
            def search(self, query, *, page_token):
                raise discover.PlacesError("Places answered HTTP 429")

        with self.assertRaises(discover.PlacesError):
            discover.run(self.conn, Boom(), "q")
        run = self.conn.execute(
            "SELECT places_calls, aborted_reason FROM run"
        ).fetchone()
        self.assertEqual(run[0], 1)
        self.assertIn("429", run[1])

    def test_dry_run_needs_no_key_and_names_control_1(self) -> None:
        code, out, _ = self.cli_out()
        self.assertEqual(code, 0)
        self.assertIn("Zahnpflege Onlineshop NRW", out)
        self.assertIn("places.displayName", out)
        self.assertIn("control 1", out)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM run").fetchone()[0], 0)

    def test_submit_without_a_key_stops(self) -> None:
        code, _, err = self.cli_out(submit=True)
        self.assertEqual(code, 2)
        self.assertIn(discover.API_KEY_ENV, err)
        code, _, err = self.cli_out(submit=True, dry_run=True)
        self.assertEqual(code, 2)
        self.assertIn("contradict", err)

    def test_submit_with_a_fake_client_reports(self) -> None:
        client = ScriptedClient([_page([_place("A", "https://a.de", "")])])
        code, out, _ = self.cli_out(submit=True, client=client)
        self.assertEqual(code, 0)
        self.assertIn("+ a.de", out)
        self.assertIn("1 new company row", out)

    def test_fetch_without_seed_refuses_an_empty_table(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_fetch(self.db_path, None, 1.0, 1)
        self.assertEqual(code, 2)
        self.assertIn("portal discover", err.getvalue())


if __name__ == "__main__":
    unittest.main()
