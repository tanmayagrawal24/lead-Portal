"""`portal audit-impressum-candidates` (M1.48).

The command exists because A2 §8's numbers shipped without their instrument and
could not be re-derived. So the properties under test are not "it counts things"
— they are the two that make the numbers trustworthy: the **selection** is the
ratified one (M1.43's homepage-hash guard *and* M1.44's robots guard), and the
counts and the values are measured over the **same** pages, because a second
expression describing what the first one does is this project's most-repeated
defect.

Fixtures are hand-written. The brief forbids harvesting a real Impressum into
`tests/` — redaction was attempted and leaked — so these carry `Max Mustermann`
and `Musterstraße` with the structural markup that matters.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portal import cli, db, impressum_audit, migrate

IMPRESSUM = """<html><body>
<h1>Impressum</h1>
<p>Angaben gem&auml;&szlig; &sect; 5 DDG</p>
<p>Musterhandel GmbH<br>Musterstra&szlig;e 1<br>12345 Musterstadt</p>
<p>Vertreten durch: Gesch&auml;ftsf&uuml;hrer Max Mustermann</p>
<p>Telefon: +49 123 4567890<br>E-Mail: info@muster.de</p>
<p>Registergericht: Amtsgericht Musterstadt<br>Registernummer: HRB 12345</p>
<p>USt-IdNr.: DE123456789</p>
</body></html>"""

# Same markup, different company details — used as the *newer* artifact so a test
# can tell which page was measured without printing anything from either.
NEWER = IMPRESSUM.replace("12345 Musterstadt", "54321 Andernorts").replace(
    "Musterhandel GmbH", "Zweite Handels GmbH"
)

HOMEPAGE = "<html><body><h1>Shop</h1><p>Willkommen</p></body></html>"

ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow: /admin\n"
ROBOTS_DENY_POLICIES = "User-agent: *\nDisallow: /policies/\n"


class ImpressumAuditTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "artifacts"
        self.root.mkdir()
        self.path = self.base / "portal.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        migrate.apply_pending(self.conn)

    def company(self, domain: str) -> int:
        return int(
            self.conn.execute(
                "INSERT INTO company (domain, discovery_source, discovered_at) "
                "VALUES (?, 'seed_csv', '2026-08-15T00:00:00Z')",
                (domain,),
            ).lastrowid
        )

    def artifact(
        self,
        company_id: int,
        domain: str,
        kind: str,
        body: str,
        *,
        url: str | None = None,
        status: int | None = 200,
        digest: str | None = None,
    ) -> int:
        name = f"{kind}-{len(list(self.root.rglob('*')))}.txt"
        directory = self.root / domain
        directory.mkdir(exist_ok=True)
        (directory / name).write_text(body, encoding="utf-8")
        return int(
            self.conn.execute(
                "INSERT INTO artifact (company_id, kind, url, http_status, "
                "content_hash, body_path, fetched_at) VALUES (?,?,?,?,?,?,?)",
                (
                    company_id,
                    kind,
                    url or f"https://{domain}/{kind}",
                    status,
                    digest or f"hash-{kind}-{name}",
                    f"{domain}/{name}",
                    "2026-08-15T00:00:00Z",
                ),
            ).lastrowid
        )

    # ---- selection ----------------------------------------------------

    def test_the_newest_impressum_wins(self) -> None:
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(company_id, "muster.de", "impressum", IMPRESSUM)
        newest = self.artifact(company_id, "muster.de", "impressum", NEWER)
        inputs, skipped = impressum_audit.select_inputs(self.conn, self.root)
        self.assertEqual([i.artifact_id for i in inputs], [newest])
        self.assertEqual(skipped, [])

    def test_an_impressum_that_is_the_homepage_is_excluded(self) -> None:
        """M1.43. The guard is structural — the hash, not the URL — so it catches
        a mis-filed page however it was mis-filed."""
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        real = self.artifact(company_id, "muster.de", "impressum", IMPRESSUM)
        self.artifact(company_id, "muster.de", "homepage", HOMEPAGE, digest="same")
        # Filed later, so "newest" would pick it without the guard.
        self.artifact(company_id, "muster.de", "impressum", HOMEPAGE, digest="same")
        inputs, _ = impressum_audit.select_inputs(self.conn, self.root)
        self.assertEqual([i.artifact_id for i in inputs], [real])

    def test_a_robots_disallowed_body_is_excluded(self) -> None:
        """M1.44. The body is on disk and returns 200; it should never have been
        fetched, and it is that company's newest Impressum."""
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_DENY_POLICIES)
        allowed = self.artifact(
            company_id,
            "muster.de",
            "impressum",
            IMPRESSUM,
            url="https://muster.de/impressum",
        )
        self.artifact(
            company_id,
            "muster.de",
            "impressum",
            NEWER,
            url="https://muster.de/policies/legal-notice",
        )
        inputs, _ = impressum_audit.select_inputs(self.conn, self.root)
        self.assertEqual([i.artifact_id for i in inputs], [allowed])

    def test_a_company_whose_only_impressum_is_disallowed_has_none(self) -> None:
        """`snocks.com`'s state after both repairs, and the reason `skipped` is
        reported rather than dropped: 'no usable Impressum' is a finding."""
        company_id = self.company("snocks.example")
        self.artifact(company_id, "snocks.example", "robots", ROBOTS_DENY_POLICIES)
        self.artifact(
            company_id,
            "snocks.example",
            "impressum",
            IMPRESSUM,
            url="https://snocks.example/policies/legal-notice",
        )
        inputs, skipped = impressum_audit.select_inputs(self.conn, self.root)
        self.assertEqual(inputs, [])
        self.assertEqual([s.domain for s in skipped], ["snocks.example"])
        self.assertIn("M1.44", skipped[0].reason)

    def test_a_non_200_impressum_is_not_a_page(self) -> None:
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(company_id, "muster.de", "impressum", "", status=404)
        inputs, skipped = impressum_audit.select_inputs(self.conn, self.root)
        self.assertEqual(inputs, [])
        self.assertEqual([s.domain for s in skipped], ["muster.de"])

    # ---- counting -----------------------------------------------------

    def test_counts_find_the_hand_written_patterns(self) -> None:
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(company_id, "muster.de", "impressum", IMPRESSUM)
        result = impressum_audit.audit(self.conn, self.root)
        for name in (
            "USt-IdNr shape",
            "HRA/HRB number",
            "Amtsgericht",
            "e-mail shape",
            "Geschaeftsfuehrer label",
            "PLZ + Ort shape",
        ):
            with self.subTest(pattern=name):
                self.assertEqual(result.present[name], 1)

    def test_inh_abbreviation_is_matched(self) -> None:
        """The obvious spelling — `\\bInh(?:\\.|aber)\\b` — reports zero for the
        abbreviated form, because `\\b` after `.` needs a word character next.
        That bug produced a 0/12 for §10.2's lever on a first measurement."""
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(
            company_id,
            "muster.de",
            "impressum",
            "<html><body><p>Mustershop Inh. Max Mustermann</p>"
            "<p>Musterstra&szlig;e 1<br>12345 Musterstadt</p></body></html>",
        )
        result = impressum_audit.audit(self.conn, self.root)
        self.assertEqual(result.present["Inh./Inhaber marker"], 1)

    def test_a_marker_only_inside_a_script_block_is_not_visible_text(self) -> None:
        """§10.2's base rate is instrument-dependent, and this is the mechanism:
        `visible_text` decomposes `<script>` deliberately, so a JSON-LD 'Inhaber'
        does not count as a marker on the page."""
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(
            company_id,
            "muster.de",
            "impressum",
            '<html><body><script type="application/ld+json">'
            '{"jobTitle": "Inhaber"}</script>'
            "<p>Musterstra&szlig;e 1<br>12345 Musterstadt</p></body></html>",
        )
        result = impressum_audit.audit(self.conn, self.root)
        self.assertEqual(result.present["Inh./Inhaber marker"], 0)

    def test_plz_ort_stops_at_the_city_and_skips_register_numbers(self) -> None:
        """The adversarial cases the first version of this pattern failed.

        `Registernummer: HRB 12345` followed by any capitalised word has the
        exact shape of a postal code and a city, and it sits in the same provider
        block §5.3 anchors on — so it inflated the count of the one candidate A2
        wants to move to Phase 1. The over-long spans are the other half: they do
        not change a count, but they make a correct match look wrong in the
        operator's accuracy check, which is the only reason the values mode
        exists.
        """
        cases = [
            ("12345 Musterstadt Vertreten durch", "12345 Musterstadt"),
            ("60311 Frankfurt am Main Telefon", "60311 Frankfurt am Main"),
            ("78050 Villingen-Schwenningen E-Mail", "78050 Villingen-Schwenningen"),
            ("61348 Bad Homburg Registergericht", "61348 Bad Homburg"),
            ("91541 Rothenburg ob der Tauber Sitz", "91541 Rothenburg ob der Tauber"),
            ("Registernummer: HRB 12345 USt-IdNr.", None),
            ("HRA 98765 Amtsgericht", None),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                match = impressum_audit.PLZ_ORT.search(text)
                self.assertEqual(match.group(0) if match else None, expected)

    def test_report_prints_no_matched_text(self) -> None:
        """§8: a count of pattern presence, never the value. The Impressum
        fixture's own details must not appear in the report."""
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(company_id, "muster.de", "impressum", IMPRESSUM)
        text = impressum_audit.report(impressum_audit.audit(self.conn, self.root))
        for secret in (
            "Mustermann",
            "Musterstraße",
            "Musterstadt",
            "DE123456789",
            "info@muster.de",
            "HRB 12345",
        ):
            with self.subTest(value=secret):
                self.assertNotIn(secret, text)

    # ---- the two modes agree ------------------------------------------

    def test_values_and_counts_read_the_same_page(self) -> None:
        """The property the module is built around. If the value mode selected
        differently from the count mode, the operator's accuracy check would be
        checking a page the table was not measured on — and nothing would say so.
        """
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(company_id, "muster.de", "impressum", IMPRESSUM)
        self.artifact(company_id, "muster.de", "impressum", NEWER)
        values = dict(impressum_audit.plz_ort_values(self.conn, self.root))
        # NEWER carries the second PLZ; the count mode picked NEWER as newest.
        self.assertEqual(values["muster.de"], ["54321 Andernorts"])

    def test_show_values_writes_nothing(self) -> None:
        """The operator's accuracy check must leave no trace on disk (§8)."""
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(company_id, "muster.de", "impressum", IMPRESSUM)
        before = {p: p.stat().st_mtime_ns for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(
            cli.main(
                ["--db", str(self.path), "audit-impressum-candidates", "--show-values"]
            ),
            0,
        )
        after = {p: p.stat().st_mtime_ns for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_cli_runs_and_reports(self) -> None:
        company_id = self.company("muster.de")
        self.artifact(company_id, "muster.de", "robots", ROBOTS_ALLOW_ALL)
        self.artifact(company_id, "muster.de", "impressum", IMPRESSUM)
        self.assertEqual(
            cli.main(["--db", str(self.path), "audit-impressum-candidates"]), 0
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
