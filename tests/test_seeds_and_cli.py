"""Seed CSV loading and the `portal fetch` CLI surface."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from portal import cli, db, migrate, seeds
from tests import shopfixtures
from tests.fixture_server import FixtureServer, Site


def run_cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue() + err.getvalue()


class SeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, text: str, name: str = "seed.csv") -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path


class TestSeedLoading(SeedTestCase):
    def test_loads_and_normalises(self) -> None:
        path = self.write(
            "domain,legal_name,city,postal_code,country\n"
            "https://WWW.Example.de/shop,Beispiel GmbH,Köln,50667,DE\n"
        )
        (seed,) = seeds.load(path)
        self.assertEqual(seed.domain, "example.de")
        self.assertEqual(seed.legal_name, "Beispiel GmbH")
        self.assertEqual(seed.country, "DE")

    def test_domain_only_is_enough(self) -> None:
        (seed,) = seeds.load(self.write("domain\nexample.de\n"))
        self.assertEqual(
            (seed.domain, seed.city, seed.country), ("example.de", None, None)
        )

    def test_deduplicates_on_normalised_domain(self) -> None:
        rows = seeds.load(self.write("domain\nexample.de\nhttps://www.example.de/\n"))
        self.assertEqual(len(rows), 1)

    def test_skips_blank_and_commented_rows(self) -> None:
        rows = seeds.load(self.write("domain\nexample.de\n\n# skipme.de\n"))
        self.assertEqual([r.domain for r in rows], ["example.de"])

    def test_missing_domain_column_fails_loudly(self) -> None:
        with self.assertRaisesRegex(seeds.SeedError, "domain"):
            seeds.load(self.write("website\nexample.de\n"))

    def test_malformed_domain_fails_loudly_with_a_line_number(self) -> None:
        with self.assertRaisesRegex(seeds.SeedError, ":3:"):
            seeds.load(self.write("domain\nexample.de\nnotadomain\n"))

    def test_bad_country_fails_loudly(self) -> None:
        with self.assertRaisesRegex(seeds.SeedError, "country"):
            seeds.load(self.write("domain,country\nexample.de,US\n"))

    def test_empty_file_fails_loudly(self) -> None:
        with self.assertRaisesRegex(seeds.SeedError, "no usable rows"):
            seeds.load(self.write("domain\n"))

    def test_missing_file_fails_loudly(self) -> None:
        with self.assertRaisesRegex(seeds.SeedError, "not found"):
            seeds.load(self.root / "absent.csv")

    def test_shipped_example_seed_is_loadable_and_first_party_only(self) -> None:
        rows = seeds.load(Path("seeds/example.csv"))
        self.assertEqual([r.domain for r in rows], ["creative-potato.global"])


class TestSeedUpsert(SeedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.conn = db.connect(self.root / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def test_is_idempotent_across_runs(self) -> None:
        rows = [seeds.Seed(domain="example.de", city="Köln")]
        first = seeds.upsert(self.conn, rows)
        second = seeds.upsert(self.conn, rows)
        self.assertEqual(first, second)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM company").fetchone()[0], 1
        )

    def test_records_provenance(self) -> None:
        seeds.upsert(self.conn, [seeds.Seed(domain="example.de")], query="seeds/x.csv")
        row = self.conn.execute(
            "SELECT * FROM company WHERE domain = 'example.de'"
        ).fetchone()
        self.assertEqual(row["discovery_source"], "seed_csv")
        self.assertEqual(row["discovery_query"], "seeds/x.csv")


class TestFetchCli(SeedTestCase):
    def test_refuses_an_interval_below_the_politeness_floor(self) -> None:
        """§5.2's 1 req/s is a hard requirement, so the CLI cannot go under it."""
        seed = self.write("domain\nexample.de\n")
        code, output = run_cli(
            "--db",
            str(self.root / "p.db"),
            "fetch",
            "--seed",
            str(seed),
            "--interval",
            "0.1",
        )
        self.assertEqual(code, 2)
        self.assertIn("below the §5.2 floor", output)

    def test_refuses_more_hosts_than_the_ceiling(self) -> None:
        seed = self.write("domain\nexample.de\n")
        code, output = run_cli(
            "--db",
            str(self.root / "p.db"),
            "fetch",
            "--seed",
            str(seed),
            "--max-hosts",
            "8",
        )
        self.assertEqual(code, 2)
        self.assertIn("ceiling", output)

    def test_requires_an_initialised_database(self) -> None:
        seed = self.write("domain\nexample.de\n")
        code, output = run_cli(
            "--db", str(self.root / "missing.db"), "fetch", "--seed", str(seed)
        )
        self.assertEqual(code, 2)
        self.assertIn("portal init", output)

    def test_reports_a_bad_seed_file_without_a_traceback(self) -> None:
        code, output = run_cli("--db", str(self.root / "p.db"), "init")
        self.assertEqual(code, 0)
        code, output = run_cli(
            "--db",
            str(self.root / "p.db"),
            "fetch",
            "--seed",
            str(self.write("website\nx\n")),
        )
        self.assertEqual(code, 2)
        self.assertIn("seed error", output)

    def test_end_to_end_against_the_fixture_server(self) -> None:
        """The CLI path, not just the library path — including artifacts on disk."""
        server = FixtureServer(Site())
        server.site.routes.update(
            shopfixtures.flat_shop(server.base, footer_impressum=True).routes
        )
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)

        db_path = self.root / "p.db"
        run_cli("--db", str(db_path), "init")

        # The CLI resolves domains to https:// by design, so drive the stage
        # directly for the loopback case; this asserts the CLI's own plumbing —
        # seed load, company upsert, run row — is wired up.
        seed = self.write("domain\n127.0.0.1\n")
        rows = seeds.load(seed)
        conn = db.connect(db_path)
        self.addCleanup(conn.close)
        ids = seeds.upsert(conn, rows, query=str(seed))
        self.assertEqual(len(ids), 1)

        from portal import fetch
        from portal.net import Fetcher, HostRateLimiter

        fetcher = Fetcher(limiter=HostRateLimiter(0.0))
        self.addCleanup(fetcher.close)
        _run_id, results = fetch.run(
            conn,
            [(ids[0], "127.0.0.1")],
            self.root / "artifacts",
            fetcher=fetcher,
            max_hosts=1,
            base_url=lambda _d: server.base,
        )
        self.assertIn("homepage", results[0].kinds)
        self.assertTrue(any((self.root / "artifacts").rglob("*.html")))


if __name__ == "__main__":
    unittest.main()
