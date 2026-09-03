"""§5.5a — PageSpeed Insights, and the writer for `perf.lighthouse_performance`.

**Built from §5.3's row and §5.5a's sentence, and from nothing else.** M1.98
records that an earlier `portal/pagespeed.py` survives in a stash on the
machine Unit 5 ran on. That machine is not this one, the stash is unreachable
from here, and this file does not cite it: it is a rebuild from the
specification, which is what §10.4b's standing instruction asks for. If the
two are ever compared, the comparison is worth more than either file — the
argument M1.98 makes for `verify.py`, and it holds here.

**The reader has been in place since M0 and nothing here touches it.**
`company_profile` projects this key to `lighthouse_perf` (migration 012, and
every revision of the view before it); `ruleset.opp.slow_site` (+10, §6.2)
fires below 50 on a written score and declines on an absent one (M3's audit —
*a NULL is not a low score*), with `_slow_site_settled` reading the same
column. The rule, its weight and its predicate are the spec's; this module
supplies the number they have been waiting for.

**Its own stage — normative, and the reason is migration 006.** §5.5a says
PageSpeed runs *"here"*, in `extract-p2`, and §4's `run.stage` comment folds it
in the same way. It is written as `stage = 'pagespeed'` instead, because
`company_profile` scopes every key to the latest FINISHED run per (company,
stage) and *"a later run of a stage is authoritative for everything that stage
owns, including the keys it deliberately did not write"*. Under a shared stage,
the LLM submission's run and this run would retract each other's signals per
company, whichever finished second. Migration 016's header records the same
decision from the schema's side.

**§5.3's cache — normative: do not re-run within 30 days.** A company whose
newest measurement is younger than `CACHE_DAYS` is reported as cached and gets
NO row in the new run. That is not an omission; it is how the cache works under
006's per-company scoping — the earlier run stays that company's authority for
this stage, and the value keeps being served. Writing the old number again
under a new `observed_at` would be a measurement that was never taken.

*The clock.* §5.3 says *"cache by `artifact.last_checked_at` age"*. That column
is advanced by every crawl (D5(b)), so a weekly `fetch` would keep it forever
young and a rule keyed on it would either never re-measure or always re-measure
— neither is the 30-day rule. The clock that the rule is coherent against is
the measurement's own `signal.observed_at`, which is what a PageSpeed run IS:
a check of the homepage URL at that moment. That reading is recorded here so
it can be objected to; if `artifact.last_checked_at` was meant literally, the
change is one predicate in `_CACHED_SQL`.

**The client is injected (Unit 2's shape).** `PageSpeedClient` is a Protocol,
`HttpPageSpeedClient` is the one implementation, and every test drives a fake:
nothing in the suite reaches `googleapis.com`. The key comes from
`PAGESPEED_API_KEY` and from nowhere else (§7 control 9), read at call time.
**If no key is present, `run` raises before a run row exists and stops.** The
API accepts keyless requests against a shared, unpredictable quota; that path
is refused on purpose, because a call this tool cannot account for is a call §7
control 1's Cloud Console cap cannot see, and control 9's last sentence is the
rule: *report that no credential is available and stop — not find one.*

**What is measured, and what is written.** The newest 200-with-body homepage
artifact per company admitted by §5.4's gate (`extract_p2.eligible_companies`,
so a company Phase 1 stopped is never measured — §7 control 7 applies to quota
as it applies to money). One signal per measured company: `value_num` is the
Lighthouse performance score on 0–100, `method = 'deterministic'` because it is
a measured number and the CHECK admits nothing else that would be honest,
`evidence_url` and `artifact_id` are the homepage artifact's own (M1.42 — one
expression, the row the URL came from), and `value_text` carries the strategy,
the Lighthouse version, the URL Lighthouse actually audited after redirects,
and the analysis timestamp. A response with no score — Lighthouse's
`runtimeError` — is a recorded failure for that company, never a signal.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from portal import extract_p2
from portal.net import USER_AGENT

#: §5.3's key. `company_profile` projects it to `lighthouse_perf`.
SIGNAL_KEY = "perf.lighthouse_performance"

#: `run.stage` for this writer. Its own value, for migration 006's reason —
#: see the module docstring.
STAGE = "pagespeed"

#: §7 control 9. Read at call time, never at import, and never logged.
API_KEY_ENV = "PAGESPEED_API_KEY"

#: The v5 endpoint, as the API reference names it (read 2026-09-03; the page
#: is dated 2024-09-03). `www.googleapis.com` serves the same method.
ENDPOINT = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"

#: **Normative.** The API's default is `desktop`; the PageSpeed web tool's
#: headline number, and the score §6.2's *"< 50"* reads as a shop's speed, is
#: the mobile one. The strategy is written into `value_text` on every row, so
#: a later change is a visible diff and not a silent recalibration.
STRATEGY = "mobile"

#: §5.3: *"do not re-run within 30 days."*
CACHE_DAYS = 30

#: §5.3: 15–30 s per site. The transport timeout has to clear the slow end
#: with room, or a slow shop reads as a failed measurement.
TIMEOUT_SECONDS = 90.0

#: A runaway guard in §7 control 2's sense, not a budget. A run's calls are
#: already bounded by the admitted set; this is what stops a defect in
#: `plan` — or a corpus that grew past what anyone meant to measure in one
#: sitting — from walking through a day's quota. Raise it deliberately, with
#: `--max-calls`, not by editing this line.
MAX_CALLS_PER_RUN = 100


class MissingKeyError(RuntimeError):
    """No API key in the environment. Raised before any network attempt and
    before any run row is written."""


class PageSpeedError(RuntimeError):
    """The API answered, and the answer is not a measurement.

    One company's worth: `run` records it on that company's outcome and moves
    on (Audit 3's shape), because a shop Lighthouse cannot audit is a fact
    about that shop and not about the run.
    """


class PageSpeedClient(Protocol):
    """One PageSpeed Insights request. Injected; tests pass a fake."""

    def run(self, url: str, *, strategy: str) -> dict[str, Any]: ...


class HttpPageSpeedClient:
    """`PageSpeedClient` over httpx. The only thing here that touches a network.

    **The key never leaves this object in a string.** httpx puts the request
    URL — query string and `key=` included — into `HTTPStatusError`'s message
    and into the `Request` hanging off every exception, so status is checked
    by hand and every failure is re-raised as a `PageSpeedError` carrying the
    exception's class name and nothing of its text (`from None`, so the chained
    traceback cannot carry it either).
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise MissingKeyError(f"{API_KEY_ENV} is empty")
        self._key = api_key
        #: `transport` is a test seam, as on `net.Fetcher`: a MockTransport
        #: lets the request construction be asserted offline.
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=timeout, transport=transport
        )

    def run(self, url: str, *, strategy: str) -> dict[str, Any]:
        try:
            response = self._client.get(
                ENDPOINT,
                params={
                    "url": url,
                    "strategy": strategy,
                    "category": "performance",
                    "key": self._key,
                },
            )
        except httpx.HTTPError as exc:
            raise PageSpeedError(
                f"{type(exc).__name__} while calling PageSpeed Insights"
            ) from None
        if response.status_code != 200:
            raise PageSpeedError(
                f"PageSpeed Insights answered HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError:
            raise PageSpeedError(
                "PageSpeed Insights answered with a body that is not JSON"
            ) from None
        if not isinstance(payload, dict):
            raise PageSpeedError("PageSpeed Insights answered with a non-object body")
        return payload

    def close(self) -> None:
        self._client.close()


def _client(explicit: PageSpeedClient | None = None) -> PageSpeedClient:
    """The client, imported lazily so this module needs no key to import.

    §7 control 9's last sentence, as code: no key means report and stop. There
    is deliberately no keyless branch — see the module docstring.
    """
    if explicit is not None:
        return explicit
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise MissingKeyError(
            f"{API_KEY_ENV} is not set. §7 control 9: keys come from the "
            f"environment only, and PageSpeed Insights needs one. No credential "
            f"is available, so nothing was measured — a keyless call against "
            f"the shared quota is refused, not attempted."
        )
    return HttpPageSpeedClient(key)


# ── the response, read ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Measurement:
    """What one PageSpeed Insights response says, reduced to what is written."""

    #: 0–100, rounded from Lighthouse's 0–1 category score.
    score: int
    #: `lighthouseResult.finalUrl` — the URL Lighthouse audited after following
    #: the shop's redirects. Provenance for the number; the signal's
    #: `evidence_url` stays the artifact's, per M1.42.
    final_url: str
    lighthouse_version: str
    analysed_at: str


def parse_measurement(payload: dict[str, Any]) -> Measurement:
    """`lighthouseResult.categories.performance.score`, or fail.

    The reference says the category score *"can be null"*, and when it is,
    `lighthouseResult.runtimeError` says why. Both are read rather than
    defaulted: a null score written as 0 would fire `opp.slow_site` on a shop
    Lighthouse never reached — A7's exact shape, on the rule M3's audit added
    the NULL guard to.
    """
    lighthouse = payload.get("lighthouseResult")
    if not isinstance(lighthouse, dict):
        raise PageSpeedError("response carries no lighthouseResult")
    categories = lighthouse.get("categories")
    performance = (
        categories.get("performance") if isinstance(categories, dict) else None
    )
    raw = performance.get("score") if isinstance(performance, dict) else None
    if raw is None:
        error = lighthouse.get("runtimeError")
        if isinstance(error, dict) and error.get("code"):
            raise PageSpeedError(
                f"Lighthouse reported no performance score: {error.get('code')} "
                f"— {error.get('message', '')}".rstrip(" —")
            )
        raise PageSpeedError(
            "Lighthouse reported no performance score and no runtime error"
        )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise PageSpeedError(f"performance score is not a number: {raw!r}") from None
    if not 0.0 <= value <= 1.0:
        raise PageSpeedError(f"performance score {value!r} is outside 0–1")
    return Measurement(
        score=round(value * 100),
        final_url=str(lighthouse.get("finalUrl") or payload.get("id") or ""),
        lighthouse_version=str(lighthouse.get("lighthouseVersion") or ""),
        analysed_at=str(payload.get("analysisUTCTimestamp") or ""),
    )


# ── the plan: who is measured, who is cached, who is not ─────────────────


@dataclass(frozen=True)
class Target:
    """One company to measure, and the artifact its number will be evidenced
    on. `url` and `artifact_id` come from the same row (M1.42)."""

    company_id: int
    domain: str
    artifact_id: int
    url: str


@dataclass(frozen=True)
class Cached:
    """A company whose newest measurement is younger than `CACHE_DAYS`."""

    company_id: int
    domain: str
    score: int
    observed_at: str
    run_id: int


@dataclass(frozen=True)
class Plan:
    targets: list[Target]
    cached: list[Cached]
    #: `(domain, reason)` — withheld by §5.4, or nothing to measure.
    skipped: list[tuple[str, str]]


# The newest 200-with-body homepage per company, the same choice
# `extract_p2._HOMEPAGE_SQL` makes for the LLM half (`a.id DESC`, first row
# wins), joined from the company side so a company with no such artifact is
# still listed and can be reported rather than dropped.
_HOMEPAGE_SQL = """
SELECT c.id AS company_id, c.domain, a.id AS artifact_id, a.url
FROM company c
LEFT JOIN artifact a ON a.id = (
    SELECT id FROM artifact
    WHERE company_id = c.id AND kind = 'homepage'
      AND http_status = 200 AND body_path IS NOT NULL
    ORDER BY id DESC LIMIT 1
)
ORDER BY c.domain
"""

# The newest measurement `company_profile` would serve: from a FINISHED,
# un-aborted run of this stage. An aborted run's number is not served (007),
# so it does not count as a measurement for the cache either.
_CACHED_SQL = """
SELECT s.value_num, s.observed_at, s.run_id
FROM signal s JOIN run r ON r.id = s.run_id
WHERE s.company_id = ? AND s.key = ? AND r.stage = ?
  AND r.finished_at IS NOT NULL AND r.aborted_reason IS NULL
ORDER BY s.observed_at DESC, s.id DESC
LIMIT 1
"""


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan(conn: sqlite3.Connection, *, now: datetime | None = None) -> Plan:
    """Who would be measured. **Free, makes no request.**

    §5.4's gate is `extract_p2.eligible_companies` — reused, not re-expressed,
    so the three withheld states are named here in the same words the LLM
    half's dry run uses. `now` is a parameter so the cache boundary is testable
    without waiting a month.
    """
    moment = now or datetime.now(UTC)
    cutoff = (moment - timedelta(days=CACHE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    admitted, withheld = extract_p2.eligible_companies(conn)

    targets: list[Target] = []
    cached: list[Cached] = []
    skipped: list[tuple[str, str]] = []
    for row in conn.execute(_HOMEPAGE_SQL):
        company_id = int(row["company_id"])
        domain = str(row["domain"])
        if company_id in withheld:
            skipped.append((domain, withheld[company_id]))
            continue
        if (
            company_id not in admitted
        ):  # pragma: no cover - eligible_companies covers every row
            skipped.append((domain, "not scored — no §5.4 verdict"))
            continue
        if row["artifact_id"] is None:
            skipped.append(
                (
                    domain,
                    "no fetched homepage (HTTP 200) to measure — run `portal fetch`",
                )
            )
            continue
        latest = conn.execute(_CACHED_SQL, (company_id, SIGNAL_KEY, STAGE)).fetchone()
        if latest is not None and str(latest["observed_at"]) > cutoff:
            cached.append(
                Cached(
                    company_id=company_id,
                    domain=domain,
                    score=int(latest["value_num"]),
                    observed_at=str(latest["observed_at"]),
                    run_id=int(latest["run_id"]),
                )
            )
            continue
        targets.append(
            Target(
                company_id=company_id,
                domain=domain,
                artifact_id=int(row["artifact_id"]),
                url=str(row["url"]),
            )
        )
    return Plan(targets=targets, cached=cached, skipped=skipped)


# ── the run ──────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    domain: str
    company_id: int
    #: `measured` or `failed`. Cached and skipped companies are on the plan,
    #: not here: this list is what the run DID.
    status: str
    score: int | None = None
    detail: str = ""


@dataclass
class RunResult:
    run_id: int
    plan: Plan
    outcomes: list[Outcome] = field(default_factory=list)
    #: Requests issued, which is also what `run.pagespeed_calls` holds.
    calls: int = 0


def _write(
    conn: sqlite3.Connection,
    run_id: int,
    target: Target,
    measurement: Measurement,
    *,
    now: str,
) -> None:
    """One signal, in §4's M1.5 idiom, evidenced on the artifact row.

    `ON CONFLICT ... DO NOTHING` on the uniqueness target only — never
    `INSERT OR IGNORE`, which would also swallow the CHECK on `method`.
    """
    value_text = (
        f"strategy={STRATEGY}; lighthouse={measurement.lighthouse_version}; "
        f"final_url={measurement.final_url}; analysed_at={measurement.analysed_at}"
    )
    conn.execute(
        """
        INSERT INTO signal
            (company_id, run_id, key, value_num, value_text, method,
             evidence_url, artifact_id, observed_at)
        VALUES (?,?,?,?,?,'deterministic',?,?,?)
        ON CONFLICT (run_id, company_id, key, evidence_url) DO NOTHING
        """,
        (
            target.company_id,
            run_id,
            SIGNAL_KEY,
            float(measurement.score),
            value_text,
            target.url,
            target.artifact_id,
            now,
        ),
    )


def run(
    conn: sqlite3.Connection,
    *,
    client: PageSpeedClient | None = None,
    max_calls: int = MAX_CALLS_PER_RUN,
    now: datetime | None = None,
) -> RunResult:
    """Measure every planned company and write the signals. **Reaches the
    network, through the injected client, once per target.**

    Order, and why:

    1.  The client is resolved FIRST. No key means `MissingKeyError` here,
        with no run row written — the tool stops, per §7 control 9.
    2.  The plan is checked against `max_calls` before a row exists, and a
        plan over the ceiling is refused whole rather than measured in part:
        a partial run would be a finished run that is authoritative for the
        companies it never reached (006), which is the wrong kind of partial.
    3.  `run.pagespeed_calls` is incremented as each request is ISSUED. The
        quota is consumed by the request; a failed one still counts.
    4.  A `PageSpeedError` is recorded on that company and the loop continues
        (Audit 3's shape). Anything else aborts the run and marks it so
        (M1.39), and an aborted run serves nothing.
    """
    resolved = _client(client)
    prepared = plan(conn, now=now)
    if len(prepared.targets) > max_calls:
        raise PageSpeedError(
            f"{len(prepared.targets)} sites to measure exceeds the per-run "
            f"ceiling of {max_calls} calls; nothing was measured. Raise it with "
            f"--max-calls if the corpus really is this size."
        )

    cursor = conn.execute(
        "INSERT INTO run (started_at, stage) VALUES (?, ?)", (_utc_now(), STAGE)
    )
    run_id = int(cursor.lastrowid or 0)
    result = RunResult(run_id=run_id, plan=prepared)
    try:
        for target in prepared.targets:
            result.calls += 1
            conn.execute(
                "UPDATE run SET pagespeed_calls = COALESCE(pagespeed_calls, 0) + 1 "
                "WHERE id = ?",
                (run_id,),
            )
            try:
                measurement = parse_measurement(
                    resolved.run(target.url, strategy=STRATEGY)
                )
            except PageSpeedError as exc:
                result.outcomes.append(
                    Outcome(target.domain, target.company_id, "failed", detail=str(exc))
                )
                continue
            _write(conn, run_id, target, measurement, now=_utc_now())
            result.outcomes.append(
                Outcome(
                    target.domain,
                    target.company_id,
                    "measured",
                    score=measurement.score,
                    detail=measurement.final_url,
                )
            )
    except BaseException as exc:
        conn.execute(
            "UPDATE run SET aborted_reason = ? WHERE id = ?",
            (f"{type(exc).__name__}: {exc}"[:500], run_id),
        )
        conn.commit()
        raise
    conn.execute(
        "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
        (_utc_now(), len(prepared.targets), run_id),
    )
    conn.commit()
    return result


__all__ = [
    "API_KEY_ENV",
    "CACHE_DAYS",
    "ENDPOINT",
    "MAX_CALLS_PER_RUN",
    "SIGNAL_KEY",
    "STAGE",
    "STRATEGY",
    "Cached",
    "HttpPageSpeedClient",
    "Measurement",
    "MissingKeyError",
    "Outcome",
    "PageSpeedClient",
    "PageSpeedError",
    "Plan",
    "RunResult",
    "Target",
    "parse_measurement",
    "plan",
    "run",
]
