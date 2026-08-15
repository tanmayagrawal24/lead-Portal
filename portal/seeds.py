"""Seed CSV loading (§5.1).

Format — `domain` is required, everything else optional:

    domain,legal_name,city,postal_code,country
    example.de,Beispiel GmbH,Köln,50667,DE

Loading fails loudly on a malformed row rather than skipping it: a seed list is
hand-written and short, and a silently dropped line is a lead that vanishes
without anyone noticing.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portal.artifacts import utc_now
from portal.urls import normalise_domain

COUNTRIES = ("DE", "AT", "CH")


@dataclass(frozen=True)
class Seed:
    domain: str
    legal_name: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country: str | None = None


class SeedError(ValueError):
    pass


def load(path: Path) -> list[Seed]:
    if not path.is_file():
        raise SeedError(f"seed file not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "domain" not in reader.fieldnames:
            raise SeedError(f"{path}: a 'domain' column is required")

        seeds: list[Seed] = []
        seen: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            raw = (row.get("domain") or "").strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                domain = normalise_domain(raw)
            except ValueError as exc:
                raise SeedError(f"{path}:{line_number}: {exc}") from exc

            country = (row.get("country") or "").strip().upper() or None
            if country and country not in COUNTRIES:
                raise SeedError(
                    f"{path}:{line_number}: country must be one of {COUNTRIES}, got {country!r}"
                )
            if domain in seen:
                continue  # deduplicate on normalised domain, per §5.1
            seen.add(domain)
            seeds.append(
                Seed(
                    domain=domain,
                    legal_name=(row.get("legal_name") or "").strip() or None,
                    city=(row.get("city") or "").strip() or None,
                    postal_code=(row.get("postal_code") or "").strip() or None,
                    country=country,
                )
            )
    if not seeds:
        raise SeedError(f"{path}: no usable rows")
    return seeds


def upsert(
    conn: sqlite3.Connection, seeds: list[Seed], query: str | None = None
) -> list[int]:
    """Insert companies, returning their ids. Existing domains are left as-is."""
    ids: list[int] = []
    for seed in seeds:
        conn.execute(
            """
            INSERT INTO company
                (domain, legal_name, city, postal_code, country,
                 discovery_source, discovery_query, discovered_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT (domain) DO NOTHING
            """,
            (
                seed.domain,
                seed.legal_name,
                seed.city,
                seed.postal_code,
                seed.country,
                "seed_csv",
                query,
                utc_now(),
            ),
        )
        row = conn.execute(
            "SELECT id FROM company WHERE domain = ?", (seed.domain,)
        ).fetchone()
        ids.append(int(row["id"]))
    return ids
