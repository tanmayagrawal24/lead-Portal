"""§5.1 — `portal discover`: Places API (New) Text Search into `company` (M8).

**Built last, as the brief said, and for the brief's reason**: everything
upstream was validated on a hand-written seed list at zero cost, and paying
to discover companies before the parsers were trusted would have been paying
to test parsers. Now there is nothing upstream left to build without a key.

**What binds here, in the order §7 puts it.**

1. **§7 control 1 is the outer bound and it lives in Cloud Console, not in
   this file.** The brief's definition of done says *"quota cap confirmed in
   Cloud Console first"*; no code can confirm that, so `--submit` prints the
   sentence and does not check a box for the operator.
2. **The field mask is exactly `places.displayName`, `places.websiteUri`,
   `places.formattedAddress`** — §5.1 in the brief's wording (`displayName`,
   `websiteUri`, `formattedAddress`; the `places.` prefix is what the New
   API's `X-Goog-FieldMask` header wants for a search response). Asking for
   `rating` or `reviews` moves the call to a dearer SKU. The header is a
   module constant and a test asserts it verbatim on the wire.
3. **`run.places_calls` counts requests ISSUED** — the §4 sibling of
   `pagespeed_calls` (M1.101), for the same reason: a quota is consumed by
   the request, not by its success. `MAX_CALLS_PER_RUN` is the inner guard.
   Nothing here touches `est_cost_usd`: the Console cap is what makes the
   SKU free, and a ledger that sums money is not handed a count of calls.
4. **Dedupe on the normalised domain** (§5.1), through `seeds.normalise_domain`
   — the single expression the seed loader already uses — and through
   `company.domain`'s UNIQUE with `ON CONFLICT DO NOTHING`, so a re-run with
   an overlapping query inserts nothing twice. A place with no `websiteUri`
   is skipped and counted: it is a shop this tool cannot fetch, not an error.
5. **Dry by default (M1.102's gate).** With no flag the command prints the
   query, the region, the field mask and the request count it would issue,
   and needs no key. `--submit` needs `GOOGLE_PLACES_API_KEY` from the
   environment and nothing else (§7 control 9).

**Pagination is bounded by the request cap, not by the result count.** Each
page is one request; `nextPageToken` is followed only while the cap allows,
so a broad query stops at `MAX_CALLS_PER_RUN` pages and says so.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from portal.artifacts import utc_now
from portal.urls import normalise_domain

STAGE = "discover"
API_KEY_ENV = "GOOGLE_PLACES_API_KEY"
ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.displayName,places.websiteUri,places.formattedAddress,nextPageToken"
)
#: The three §5.1 fields, and the token the next page needs. Nothing else.
FIELDS: tuple[str, ...] = ("displayName", "websiteUri", "formattedAddress")
MAX_CALLS_PER_RUN = 10
PAGE_SIZE = 20
TIMEOUT_SECONDS = 20.0
USER_AGENT = "lead-portal/discover (+localhost; contact: operator)"


class MissingKeyError(RuntimeError):
    """`GOOGLE_PLACES_API_KEY` is unset. Raised before any network attempt."""


class PlacesError(RuntimeError):
    """A Places request failed. Carries the class name, never the URL (M1.101's
    reason on `pagespeed`: the key rides in a header here, but the habit is
    the point)."""


class PlacesClient(Protocol):
    def search(self, query: str, *, page_token: str | None) -> dict[str, Any]: ...


class HttpPlacesClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise MissingKeyError(f"{API_KEY_ENV} is empty")
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
                "Content-Type": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    def search(self, query: str, *, page_token: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": PAGE_SIZE,
            "languageCode": "de",
        }
        if page_token:
            body["pageToken"] = page_token
        try:
            response = self._client.post(ENDPOINT, json=body)
        except httpx.HTTPError as exc:
            raise PlacesError(f"{type(exc).__name__} while calling Places") from None
        if response.status_code != 200:
            raise PlacesError(f"Places answered HTTP {response.status_code}")
        try:
            return dict(response.json())
        except ValueError:
            raise PlacesError("Places answered with a body that is not JSON") from None


@dataclass(frozen=True)
class Found:
    domain: str
    display_name: str
    address: str
    inserted: bool


@dataclass
class Report:
    run_id: int
    query: str
    calls: int = 0
    found: list[Found] = field(default_factory=list)
    no_website: int = 0
    unusable: int = 0
    capped: bool = False

    @property
    def inserted(self) -> int:
        return sum(1 for f in self.found if f.inserted)


def _postal_and_city(address: str) -> tuple[str | None, str | None, str | None]:
    """`Musterstraße 1, 40210 Düsseldorf, Deutschland` → (40210, Düsseldorf, DE).
    Best effort and never wrong-by-guessing: anything that does not fit the
    German-style `<plz> <city>` part is left NULL."""
    parts = [p.strip() for p in address.split(",")]
    country = (
        {
            "deutschland": "DE",
            "germany": "DE",
            "österreich": "AT",
            "austria": "AT",
            "schweiz": "CH",
            "switzerland": "CH",
        }.get(parts[-1].lower())
        if parts
        else None
    )
    for part in parts:
        tokens = part.split(maxsplit=1)
        if len(tokens) == 2 and tokens[0].isdigit() and 4 <= len(tokens[0]) <= 5:
            return tokens[0], tokens[1], country
    return None, None, country


def _place(item: dict[str, Any]) -> tuple[str | None, str, str]:
    name = item.get("displayName")
    display = str(name.get("text", "")) if isinstance(name, dict) else str(name or "")
    return item.get("websiteUri"), display, str(item.get("formattedAddress", "") or "")


def run(
    conn: sqlite3.Connection,
    client: PlacesClient,
    query: str,
    *,
    region: str = "",
    max_calls: int = MAX_CALLS_PER_RUN,
) -> Report:
    text = f"{query} {region}".strip()
    run_id = int(
        conn.execute(
            "INSERT INTO run (started_at, stage) VALUES (?, ?)", (utc_now(), STAGE)
        ).lastrowid
        or 0
    )
    conn.commit()
    report = Report(run_id=run_id, query=text)
    token: str | None = None
    try:
        while True:
            if report.calls >= max_calls:
                report.capped = True
                break
            # Counted as ISSUED, before the outcome is known (M1.101).
            conn.execute(
                "UPDATE run SET places_calls = COALESCE(places_calls, 0) + 1 WHERE id = ?",
                (run_id,),
            )
            conn.commit()
            report.calls += 1
            payload = client.search(text, page_token=token)
            for item in payload.get("places", []) or []:
                website, display, address = _place(item)
                if not website:
                    report.no_website += 1
                    continue
                try:
                    domain = normalise_domain(str(website))
                except ValueError:
                    report.unusable += 1
                    continue
                postal, city, country = _postal_and_city(address)
                cursor = conn.execute(
                    "INSERT INTO company (domain, legal_name, city, postal_code, country, "
                    "discovery_source, discovery_query, discovered_at) "
                    "VALUES (?,?,?,?,?,'places',?,?) ON CONFLICT (domain) DO NOTHING",
                    (domain, display or None, city, postal, country, text, utc_now()),
                )
                report.found.append(
                    Found(domain, display, address, cursor.rowcount == 1)
                )
            conn.commit()
            token = payload.get("nextPageToken") or None
            if not token:
                break
    except BaseException as exc:
        conn.execute(
            "UPDATE run SET aborted_reason = ? WHERE id = ?",
            (f"{type(exc).__name__}: {exc}"[:500], run_id),
        )
        conn.commit()
        raise
    conn.execute(
        "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
        (utc_now(), report.inserted, run_id),
    )
    conn.commit()
    return report


def client_from_env(transport: httpx.BaseTransport | None = None) -> HttpPlacesClient:
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise MissingKeyError(
            f"{API_KEY_ENV} is not set. §7 control 9: keys come from the environment "
            f"only, and this call needs one."
        )
    return HttpPlacesClient(key, transport=transport)


__all__ = [
    "API_KEY_ENV",
    "ENDPOINT",
    "FIELDS",
    "FIELD_MASK",
    "MAX_CALLS_PER_RUN",
    "STAGE",
    "Found",
    "HttpPlacesClient",
    "MissingKeyError",
    "PlacesClient",
    "PlacesError",
    "Report",
    "client_from_env",
    "run",
]
