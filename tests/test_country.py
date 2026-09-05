"""M1.128. The country a company belongs to: derivation, backfill, and filter.

The load-bearing tests here are the ordering ones and the agreement one.
Ordering, because the whole rule is *which evidence beats which*: a TLD beats a
run's tag, and a measurement beats both. Agreement, because migration 021 says
`domain LIKE '%.de'` and `countries.from_tld` says `TLD_COUNTRY[tld(domain)]`,
and two expressions of one rule is the defect this project has recorded five
times (M1.109, M1.115, M1.121, M1.122, and the parser drift in M1.121 again).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from portal import countries, db, leadlist, migrate, seeds, serve, status

# Every TLD present in the real 248-row corpus, plus the two that are refused
# by name and one that is simply unknown. The point of pinning the real ones is
# that the migration ran against these and nothing else.
CORPUS_TLDS = (
    ("beispiel.de", "DE"),
    ("beispiel.at", "AT"),
    ("beispiel.ch", "CH"),
    ("beispiel.com", None),
    ("beispiel.shop", None),
    ("beispiel.eu", None),
    ("beispiel.berlin", None),
    ("bank.lu", None),
    ("stiftung.li", None),
    ("beispiel.xyz", None),
)


class Derivation(unittest.TestCase):
    def test_the_tld_answers_where_it_names_a_country(self) -> None:
        for domain, expected in CORPUS_TLDS:
            with self.subTest(domain=domain):
                self.assertEqual(countries.from_tld(domain), expected)

    def test_the_tld_beats_the_runs_tag(self) -> None:
        """The tag says what the operator was LOOKING FOR; the TLD is what the
        company itself registered. Weaker evidence never overrides stronger."""
        self.assertEqual(countries.derive("beispiel.at", region="DE"), "AT")
        self.assertEqual(countries.derive("beispiel.ch", region="DE"), "CH")

    def test_the_runs_tag_answers_only_where_the_tld_does_not(self) -> None:
        self.assertEqual(countries.derive("beispiel.com", region="CH"), "CH")
        self.assertEqual(countries.derive("beispiel.shop", region="AT"), "AT")

    def test_no_tld_and_no_tag_is_null_and_not_a_guess(self) -> None:
        self.assertIsNone(countries.derive("beispiel.com"))
        self.assertIsNone(countries.derive("beispiel.com", region=None))

    def test_a_subdomain_does_not_confuse_the_suffix(self) -> None:
        self.assertEqual(countries.derive("shop.beispiel.de"), "DE")
        self.assertEqual(countries.derive("www.limami.at"), "AT")

    def test_case_and_a_trailing_dot_are_normalised(self) -> None:
        self.assertEqual(countries.derive("BEISPIEL.DE"), "DE")
        self.assertEqual(countries.derive("beispiel.de."), "DE")

    def test_a_bare_label_with_no_dot_is_not_a_country(self) -> None:
        self.assertIsNone(countries.derive("localhost"))
        self.assertIsNone(countries.derive(""))


class TheCountriesTheColumnCannotHold(unittest.TestCase):
    """`.lu` and `.li` derive to NULL — the question each test answers is
    whether that NULL is a decision or an omission."""

    def test_they_are_named_rather_than_forgotten(self) -> None:
        self.assertEqual(countries.OUT_OF_SCOPE_TLD, {"lu": "LU", "li": "LI"})
        self.assertTrue(countries.is_out_of_scope("bank.lu"))
        self.assertTrue(countries.is_out_of_scope("stiftung.li"))
        self.assertFalse(countries.is_out_of_scope("beispiel.com"))

    def test_derive_returns_null_rather_than_a_value_the_column_refuses(self) -> None:
        """Returning 'LU' would fail a PAID discover run partway through, with
        rows already bought. It fails at the argument instead."""
        self.assertIsNone(countries.derive("bank.lu"))
        self.assertIsNone(countries.derive("bank.lu", region="DE"))

    def test_normalise_refuses_them_by_name_and_says_why(self) -> None:
        with self.assertRaises(ValueError) as caught:
            countries.normalise("LU")
        self.assertIn("M1.128", str(caught.exception))

    def test_the_column_would_in_fact_refuse_them(self) -> None:
        """The reason for all of the above, asserted against the schema rather
        than trusted from a comment."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "portal.db")
            migrate.apply_pending(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO company (domain, country, discovery_source, "
                    "discovered_at) VALUES ('bank.lu','LU','manual','2026-09-05')"
                )
            conn.close()


class TheRunTag(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def test_run_carries_a_country_column_with_the_same_check(self) -> None:
        self.conn.execute(
            "INSERT INTO run (started_at, stage, country) "
            "VALUES ('2026-09-05T00:00:00Z','discover','CH')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO run (started_at, stage, country) "
                "VALUES ('2026-09-05T00:00:00Z','discover','LU')"
            )

    def test_an_untagged_run_is_null_rather_than_a_default_country(self) -> None:
        self.conn.execute(
            "INSERT INTO run (started_at, stage) VALUES ('2026-09-05','discover')"
        )
        self.assertIsNone(
            self.conn.execute("SELECT country FROM run ORDER BY id DESC").fetchone()[0]
        )

    def test_it_is_not_named_region_because_region_already_means_something(
        self,
    ) -> None:
        """`--region` holds free text that is concatenated into the prompt and
        stored as `discovery_query`. One flag cannot be both."""
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(run)")}
        self.assertIn("country", columns)
        self.assertNotIn("region", columns)


class TheBackfill(unittest.TestCase):
    """Migration 021 writes the same rule in SQL that `countries.from_tld`
    writes in Python. These are the tests that keep them from drifting."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "portal.db"

    def _at_020(self) -> sqlite3.Connection:
        """A database one migration short, so 021 has rows to act on."""
        conn = db.connect(self.path)
        migrations = [m for m in migrate.discover() if m[0] <= 20]
        for number, file in migrations:
            conn.executescript(
                f"BEGIN;\n{file.read_text(encoding='utf-8')}\n"
                f"PRAGMA user_version = {number};\nCOMMIT;"
            )
        return conn

    def test_the_migration_and_from_tld_agree_on_every_corpus_tld(self) -> None:
        conn = self._at_020()
        self.addCleanup(conn.close)
        for index, (domain, _) in enumerate(CORPUS_TLDS, start=1):
            conn.execute(
                "INSERT INTO company (id, domain, discovery_source, discovered_at) "
                "VALUES (?,?,'llm_websearch','2026-09-01T00:00:00Z')",
                (index, domain),
            )
        conn.commit()
        migrate.apply_pending(conn)

        stored = dict(conn.execute("SELECT domain, country FROM company"))
        for domain, expected in CORPUS_TLDS:
            with self.subTest(domain=domain):
                self.assertEqual(stored[domain], expected)
                self.assertEqual(stored[domain], countries.from_tld(domain))

    def test_it_never_overwrites_a_measured_value(self) -> None:
        """`reconcile` writes `impressum.country`, read off the company's own
        page. A derivation is a placeholder for that, never a replacement."""
        conn = self._at_020()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO company (domain, country, discovery_source, discovered_at) "
            "VALUES ('beispiel.de','CH','manual','2026-09-01T00:00:00Z')"
        )
        conn.commit()
        migrate.apply_pending(conn)
        self.assertEqual(
            conn.execute("SELECT country FROM company").fetchone()[0], "CH"
        )

    def test_re_running_it_changes_nothing(self) -> None:
        conn = self._at_020()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO company (domain, discovery_source, discovered_at) "
            "VALUES ('beispiel.de','manual','2026-09-01T00:00:00Z')"
        )
        conn.commit()
        migrate.apply_pending(conn)
        # `user_version` is what stops the whole file running twice; what has
        # to be idempotent on its own is the backfill, because `WHERE country
        # IS NULL` is the only thing standing between it and a measured value.
        sql = Path(migrate.MIGRATIONS_DIR / "021_company_country.sql").read_text()
        backfill = [
            line for line in sql.splitlines() if line.startswith("UPDATE company")
        ]
        self.assertEqual(len(backfill), len(countries.TLD_COUNTRY))
        conn.executescript("\n".join(backfill))
        self.assertEqual(
            conn.execute("SELECT country FROM company").fetchone()[0], "DE"
        )


class TheWriters(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "portal.db")
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def test_seeds_fill_a_blank_country_from_the_domain(self) -> None:
        seeds.upsert(self.conn, [seeds.Seed(domain="beispiel.de")], "seeds.csv")
        self.assertEqual(
            self.conn.execute("SELECT country FROM company").fetchone()[0], "DE"
        )

    def test_seeds_keep_what_the_csv_asserted(self) -> None:
        """A human wrote the CSV. A TLD is a guess."""
        seeds.upsert(
            self.conn, [seeds.Seed(domain="beispiel.de", country="CH")], "seeds.csv"
        )
        self.assertEqual(
            self.conn.execute("SELECT country FROM company").fetchone()[0], "CH"
        )

    def test_seeds_share_one_list_of_countries_with_the_schema(self) -> None:
        self.assertIs(seeds.COUNTRIES, countries.COUNTRIES)


class TheFilter(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "portal.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.conn.executescript(
            """
            INSERT INTO company (id, domain, discovery_source, discovered_at)
            VALUES (1,'ein-shop.de','llm_websearch','2026-09-01T00:00:00Z'),
                   (2,'ein-shop.ch','llm_websearch','2026-09-01T00:00:00Z'),
                   (3,'ein-shop.at','llm_websearch','2026-09-01T00:00:00Z'),
                   (4,'ein-shop.com','llm_websearch','2026-09-01T00:00:00Z');
            UPDATE company SET country = 'DE' WHERE id = 1;
            UPDATE company SET country = 'CH' WHERE id = 2;
            UPDATE company SET country = 'AT' WHERE id = 3;
            """
        )
        self.conn.commit()
        self.app = serve.create_app(
            self.path, Path(self.tmp.name), Path(self.tmp.name) / "briefs"
        )
        self.client = TestClient(self.app)

    def test_country_ch_narrows_the_list_to_one(self) -> None:
        leads = leadlist.LeadList(self.conn).leads(leadlist.Filters(country="CH"))
        self.assertEqual([lead.domain for lead in leads], ["ein-shop.ch"])

    def test_no_country_shows_every_row_including_the_ones_without_one(self) -> None:
        leads = leadlist.LeadList(self.conn).leads(leadlist.Filters())
        self.assertEqual(len(leads), 4)

    def test_the_dropdown_now_has_options(self) -> None:
        """M1.128's finding: the control shipped in M1.41 against an empty
        column, so it rendered with nothing to choose."""
        facets = leadlist.LeadList(self.conn).facets()
        self.assertEqual(facets["country"], ["AT", "CH", "DE"])

    def test_the_route_filters_and_the_url_carries_the_state(self) -> None:
        response = self.client.get("/", params={"country": "CH"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("ein-shop.ch", response.text)
        self.assertNotIn("ein-shop.de", response.text)

    def test_the_badge_renders_for_a_country_and_for_its_absence(self) -> None:
        """The rendered SPAN, not the class name: `.cc-DE` is also a selector
        in the page's own stylesheet, so asserting the bare token would pass
        on an empty table."""
        body = self.client.get("/").text
        for country in ("DE", "CH", "AT"):
            with self.subTest(country=country):
                self.assertIn(f'<span class="cc cc-{country}"', body)
        self.assertIn('<span class="cc cc-none"', body)

    def test_a_filtered_page_carries_only_that_countrys_badges(self) -> None:
        body = self.client.get("/", params={"country": "CH"}).text
        self.assertIn('<span class="cc cc-CH"', body)
        for absent in ("DE", "AT", "none"):
            with self.subTest(country=absent):
                self.assertNotIn(f'<span class="cc cc-{absent}"', body)

    def test_the_badge_is_one_partial_and_not_two_copies(self) -> None:
        row = Path(serve.__file__).parent / "templates" / "_row.html"
        detail = Path(serve.__file__).parent / "templates" / "_detail.html"
        for template in (row, detail):
            with self.subTest(template=template.name):
                self.assertIn('include "_country.html"', template.read_text())

    def test_the_detail_panel_shows_it_too(self) -> None:
        body = self.client.get("/company/2/detail").text
        self.assertIn("cc-CH", body)
        self.assertIn("?country=CH", body)


class TheStatusBreakdown(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "portal.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)
        self.conn.executescript(
            """
            INSERT INTO company (id, domain, country, discovery_source, discovered_at)
            VALUES (1,'a.de','DE','llm_websearch','2026-09-01T00:00:00Z'),
                   (2,'b.de','DE','llm_websearch','2026-09-01T00:00:00Z'),
                   (3,'c.ch','CH','llm_websearch','2026-09-01T00:00:00Z'),
                   (4,'d.com',NULL,'llm_websearch','2026-09-01T00:00:00Z');
            """
        )
        self.conn.commit()
        self.conn.row_factory = sqlite3.Row

    def test_it_counts_by_country_largest_first(self) -> None:
        rows = status.read(self.conn).by_country
        self.assertEqual([(r.label, r.n) for r in rows][:2], [("DE", 2), ("CH", 1)])

    def test_unknown_is_a_named_bucket_and_sorts_last(self) -> None:
        """An absence, not a fourth country (M1.59). Named, because a bucket
        nobody can see is a bucket nobody empties."""
        rows = status.read(self.conn).by_country
        self.assertEqual(rows[-1].label, "nicht bestimmt")
        self.assertEqual(rows[-1].n, 1)
        self.assertEqual(rows[-1].href, "")

    def test_each_country_links_into_the_filtered_list(self) -> None:
        rows = status.read(self.conn).by_country
        self.assertEqual(next(r.href for r in rows if r.label == "CH"), "/?country=CH")

    def test_the_page_renders_the_section(self) -> None:
        client = TestClient(
            serve.create_app(self.path, Path(self.tmp.name), Path(self.tmp.name) / "b")
        )
        body = client.get("/status").text
        self.assertIn("Nach Land", body)
        self.assertIn("nicht bestimmt", body)
