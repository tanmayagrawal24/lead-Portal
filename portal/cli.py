"""Command-line entry point.

Each pipeline stage is its own subcommand, independently re-runnable (§5).
Built so far: `init`, `fetch`, `extract-p1`, `score`, `serve`, `pagespeed`
(§5.5a), plus the inspection commands `diff-signals`, `audit-politeness`,
`audit-impressum-candidates`, `llm-prices` and `extract-p2 --dry-run`.
`extract-p2`'s **submitting** half is built (9b) and is reachable from here
**only through `--submit`** (9c): without it the command is a dry run and
spends nothing. `reconcile` is a subcommand as of 9b and collects whatever
batches the database holds. `discover` arrives with M8.
"""

from __future__ import annotations

import argparse
import ipaddress
import sqlite3
import sys
from pathlib import Path

from portal import (
    __version__,
    ai_visibility,
    audit,
    config,
    db,
    diff,
    extract,
    extract_p2,
    fetch,
    impressum_audit,
    ledger,
    llm,
    llm_anthropic,
    migrate,
    pagespeed,
    ruleset,
    score,
    seeds,
)
from portal import (
    reconcile as reconcile_mod,
)
from portal import (
    serve as serve_mod,
)
from portal.artifacts import utc_now
from portal.net import MAX_CONCURRENT_HOSTS, Fetcher, HostRateLimiter, RequestLog


def cmd_init(path: Path) -> int:
    """Create the database and bring it to the current schema version."""
    existed = path.exists()
    conn = db.connect(path)
    try:
        applied = migrate.apply_pending(conn)
        version = migrate.current_version(conn)
        inventory = db.object_inventory(conn)
    finally:
        conn.close()

    if applied:
        verb = "Applied" if existed else f"Created {path}, applied"
        print(f"{verb} migration(s): {', '.join(f'{n:03d}' for n in applied)}")
    else:
        print(f"{path} already at migration {version:03d}; nothing to apply.")

    tables = inventory.get("table", [])
    views = inventory.get("view", [])
    print(f"Schema version {version:03d} — {len(tables)} tables, {len(views)} view(s)")
    print(f"  tables:   {', '.join(tables)}")
    print(f"  views:    {', '.join(views)}")
    print(
        f"  triggers: {len(inventory.get('trigger', []))}"
        f"  indexes: {len(inventory.get('index', []))}"
    )
    return 0


def cmd_fetch(path: Path, seed_path: Path, interval: float, max_hosts: int) -> int:
    """Fetch every seeded domain under the §5.2 politeness rules.

    **Every seeded domain that is not excluded** (audit finding 10). §6.4's
    `excluded = 1` is a standing verdict — a `duplicate_site` row is the same
    lead as another row, a `robots_disallowed` row has said no — and
    `extract-p1` has always read `WHERE excluded = 0`. This stage built its
    targets from the seed file alone, so every excluded company was re-crawled
    on every run: requests against a host whose owner had already been crawled
    under its own row, and against a host that had disallowed the crawl. Lifting
    an exclusion is an operator's act, not a stage's (§6.4), so the skip is
    printed rather than silent.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    try:
        rows = seeds.load(seed_path)
    except seeds.SeedError as exc:
        print(f"seed error: {exc}", file=sys.stderr)
        return 2

    conn = db.connect(path)
    try:
        # Fail here, not in a worker thread. A database at an older schema than
        # the code produces an OperationalError from inside the pool, after real
        # requests have already gone out to real hosts — which is how the third
        # crawl died halfway (M1.20). Migrations stay explicit; this only says so.
        version = migrate.current_version(conn)
        highest = migrate.discover()[-1][0]
        if version < highest:
            print(
                f"database at {path} is at migration {version:03d} but the code "
                f"ships {highest:03d} — run `portal init` first (it is idempotent)",
                file=sys.stderr,
            )
            return 2

        company_ids = seeds.upsert(conn, rows, query=str(seed_path))
        # The same predicate `extract-p1` applies at cli.py's `cmd_extract_p1`
        # and `score` applies in `ScoreStage.profiles`: `excluded = 0`. Read
        # from `company` after the upsert, because the verdict lives there and
        # the seed file cannot know it.
        excluded = {
            int(row["id"]): str(row["excluded_reason"] or "")
            for row in conn.execute(
                "SELECT id, excluded_reason FROM company WHERE excluded = 1"
            )
        }
        skipped = [
            (seed.domain, excluded[company_id])
            for company_id, seed in zip(company_ids, rows, strict=True)
            if company_id in excluded
        ]
        targets = [
            (company_id, seed.domain)
            for company_id, seed in zip(company_ids, rows, strict=True)
            if company_id not in excluded
        ]
        print(
            f"Fetching {len(targets)} domain(s) at {interval}s/host, {max_hosts} hosts max…"
        )
        log_path = config.request_log_path(path)
        fetcher = Fetcher(limiter=HostRateLimiter(interval), log=RequestLog(log_path))
        run_id, results = fetch.run(
            conn,
            targets,
            config.artifacts_root(path),
            fetcher=fetcher,
            max_hosts=max_hosts,
        )
    finally:
        conn.close()

    print(f"requests logged to {log_path} — audit with `portal audit-politeness`")
    print(f"\nrun {run_id}:")
    for domain, reason in skipped:
        print(f"  {domain}: SKIPPED — excluded (§6.4): {reason}")
    for result in results:
        if result.excluded_reason:
            print(f"  {result.domain}: EXCLUDED — {result.excluded_reason}")
            continue
        if result.failed:
            print(f"  {result.domain}: FAILED — {result.failed}")
            continue
        kinds = ", ".join(sorted(result.kinds)) or "nothing fetched"
        print(f"  {result.domain}: {kinds}")
        if result.product_sample:
            print(
                f"      product sample ({result.product_sample_tier}): {result.product_sample}"
            )
        for flag in result.review_flags:
            print(f"      review flag: {flag}")
        for note in result.notes:
            print(f"      note: {note}")
    return 0


def cmd_extract_p1(path: Path) -> int:
    """Deterministic extraction over artifacts already on disk (§5.3).

    Costs nothing and makes no requests, so it is always safe to re-run — which
    is the point: a parser fix is re-applied to the whole corpus by running this
    again, with no third-party server involved.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    conn = db.connect(path)
    try:
        version = migrate.current_version(conn)
        highest = migrate.discover()[-1][0]
        if version < highest:
            print(
                f"database at {path} is at migration {version:03d} but the code "
                f"ships {highest:03d} — run `portal init` first (it is idempotent)",
                file=sys.stderr,
            )
            return 2
        targets = [
            (row["id"], row["domain"])
            for row in conn.execute(
                "SELECT id, domain FROM company WHERE excluded = 0 ORDER BY id"
            )
        ]
        if not targets:
            print("no companies to extract — run `portal fetch` first", file=sys.stderr)
            return 2
        run_id, results = extract.run(conn, targets, config.artifacts_root(path))
    finally:
        conn.close()

    print(f"\nrun {run_id}: extract-p1 over {len(results)} companies")
    for result in results:
        print(f"  {result.domain}: {len(result.signals)} signals")
        for key, value in sorted(result.signals.items()):
            print(f"      {key} = {value}")
        for note in result.notes:
            print(f"      note: {note}")
        for flag in result.review_flags:
            print(f"      review: {flag}")
    return 0


def cmd_extract_p2(
    path: Path,
    *,
    dry_run: bool,
    submit: bool,
    purpose: str,
    model: str = llm_anthropic.DEFAULT_MODEL,
    provider: llm.LLMProvider | None = None,
) -> int:
    """§5.5b's paid extraction — dry by default, paid only on `--submit`.

    **The spend gate, restated for 9c rather than removed.** 9a made this
    command refuse without `--dry-run`, because the submitting path needed §7
    control 4's reservation and that was 9b's. 9b built it —
    `extract_p2.reserve_and_submit`, tested against a fake provider — and the
    refusal stayed, with a message that still said the reservation was 9b's.
    That message was stale, and the gate's *shape* was the wrong one: a command
    that refuses to do anything is a gate nobody can pass, so the first real
    spend would have been made by editing this function, which is the one way a
    gate should never be passed.

    So the safe default is unchanged in substance and different in form: with
    no flag, this is a **dry run** — what would be sent, who is withheld and
    why, no request, no key, no reservation. `--dry-run` says the same thing
    explicitly. **`--submit` is the written authorisation, expressed at the
    command line**, and it is the only way this command spends: it takes §7
    control 2's clearance first, then makes control 4's reservation and the
    submission in `reserve_and_submit`'s order (M1.72 — the two writes commit
    together, before `create` is called). `--submit --dry-run` is refused as a
    contradiction rather than resolved either way.

    `provider` is the injected seam (Unit 2's shape): `main` passes nothing and
    the Anthropic provider is built here, so a test can drive this exact path
    with a fake and CI, which forbids the key, never reaches a network.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    if submit and dry_run:
        print(
            "extract-p2: --submit and --dry-run contradict each other. A dry run "
            "sends nothing; --submit reserves and sends. Pass one.",
            file=sys.stderr,
        )
        return 2
    conn = db.connect(path)
    try:
        version = migrate.current_version(conn)
        highest = migrate.discover()[-1][0]
        if version < highest:
            print(
                f"database at {path} is at migration {version:03d} but the code "
                f"ships {highest:03d} — run `portal init` first (it is idempotent)",
                file=sys.stderr,
            )
            return 2
        prepared, skipped = extract_p2.prepare(
            conn, config.artifacts_root(path), purpose=purpose
        )
        requests = extract_p2.build_requests(prepared)

        label = "--submit" if submit else "--dry-run"
        verb = "will be sent" if submit else "would be sent"
        print(f"extract-p2 {label} ({purpose}): {len(prepared)} companies {verb}\n")
        for page, request in zip(prepared, requests, strict=True):
            size = len(page.sent_text.encode("utf-8"))
            note = "  (truncated at 60 KB, §5.5b)" if page.truncated else ""
            print(f"  {page.domain:28} {request.custom_id}")
            print(f"      {page.url}")
            print(f"      {size:,} bytes of cleaned visible text{note}")
        for entry in skipped:
            print(f"  {entry.domain:28} SKIPPED — {entry.reason}")

        if not submit:
            print(
                f"\n{len(requests)} batch request(s) built. Nothing was sent and "
                "nothing was reserved. To reserve and submit — §7 control 4, "
                "the first real spend — run again with --submit."
            )
            return 0
        if not prepared:
            print(
                "\nnothing to submit: no company is both admitted by §5.4 and "
                "holds a usable page for this purpose",
                file=sys.stderr,
            )
            return 2

        # §7 control 2 before §7 control 4, in that order: the outer bound
        # decides whether there is anything to price. `check_ceiling` raises
        # on an unreadable ledger rather than reading it as zero — the same
        # refusal `llm-prices --reserve` makes, for the same reason.
        try:
            clearance = ledger.check_ceiling(conn)
        except ledger.CeilingExceeded as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        except sqlite3.Error as exc:
            print(
                f"refused: the §7 control 2 ledger is not readable ({exc}). Run "
                f"`portal init` on {path} first — an unreadable ledger and an "
                f"empty one look alike, and treating this as $0 spent is how an "
                f"unmeasured number authorises a paid call.",
                file=sys.stderr,
            )
            return 2
        print(
            f"\n§7 control 2: ${clearance.spend_usd:.2f} of "
            f"${clearance.ceiling_usd:.2f} used over {clearance.window_days} "
            f"rolling days; ${clearance.headroom_usd:.2f} headroom"
        )

        chosen = provider or llm_anthropic.AnthropicProvider(model=model)
        # The submitting run. `reconcile` writes this batch's signals under
        # THIS run id (B4) and `company_profile` serves a stage's signals only
        # from a finished, un-aborted run (007), so it is closed on success and
        # marked on failure — never left open.
        cursor = conn.execute(
            "INSERT INTO run (started_at, stage) VALUES (?, 'extract-p2')",
            (utc_now(),),
        )
        run_id = int(cursor.lastrowid or 0)
        try:
            reservation = extract_p2.reserve_and_submit(
                conn,
                chosen,
                prepared,
                run_id=run_id,
                purpose=purpose,
                clearance=clearance,
            )
        except (llm_anthropic.MissingKeyError, llm_anthropic.BalanceExhausted) as exc:
            # `MissingKeyError` arrives from `count_tokens`, before the
            # reservation exists: nothing is on the books. `BalanceExhausted`
            # arrives from `submit`, AFTER it: the batch row is `reserved`
            # with no provider id, the money is counted, and only a person
            # releases it (migration 014). Both mark the run.
            _abort_run(conn, run_id, exc)
            print(str(exc), file=sys.stderr)
            return 2
        except BaseException as exc:
            _abort_run(conn, run_id, exc)
            raise
        conn.execute(
            "UPDATE run SET finished_at = ?, companies_seen = ? WHERE id = ?",
            (utc_now(), len(prepared), run_id),
        )
        conn.commit()
    finally:
        conn.close()

    est = reservation.estimate
    print(
        f"\nrun {run_id}: batch {reservation.batch_id} submitted as "
        f"{reservation.provider_batch_id} — {reservation.request_count} request(s)"
    )
    print(
        f"  reserved ${est.total_usd:.4f} ({est.input_tokens:,} measured input "
        f"tokens + {est.output_tokens:,} reserved output, batch price as-of "
        f"{est.price.as_of.isoformat()}) on run {run_id} and batch "
        f"{reservation.batch_id} in one transaction (M1.72)"
    )
    print("  collect with `portal reconcile` once the batch has ended (§5.6)")
    return 0


def _abort_run(conn: sqlite3.Connection, run_id: int, exc: BaseException) -> None:
    """M1.39: a run that did not reach its end says so, and serves nothing."""
    conn.execute(
        "UPDATE run SET aborted_reason = ? WHERE id = ?",
        (f"{type(exc).__name__}: {exc}"[:500], run_id),
    )
    conn.commit()


def cmd_ai_check(
    path: Path,
    *,
    dry_run: bool,
    submit: bool,
    queries: int = ai_visibility.DEFAULT_QUERIES,
    recheck: bool = False,
    model: str = llm_anthropic.DEFAULT_MODEL,
    provider: ai_visibility.SearchProvider | None = None,
) -> int:
    """§5.5c's AI-visibility check (M6) — dry by default, paid only on `--submit`.

    M1.102's gate, in M1.102's shape: no flag is a dry run that prints who
    would be checked with which literal queries, who is withheld and why, and
    what the run would cost before the prompt is even measured. `--submit` is
    the written authorisation. `--submit --dry-run` is refused as a
    contradiction. Fewer than two queries is refused outright, because §6.2's
    predicate needs two and one would silently disable a +15 rule (M1.23).

    The order on `--submit` is §7's: control 2's clearance first, then control
    3's reservation inside `ai_visibility.run`, then the calls. A dry key is
    caught before any run row exists; a balance that runs dry mid-run finishes
    the run with what was paid for (M1.105(c)).
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    if submit and dry_run:
        print(
            "ai-check: --submit and --dry-run contradict each other. Pass one.",
            file=sys.stderr,
        )
        return 2
    if not ai_visibility.MIN_QUERIES <= queries <= ai_visibility.MAX_QUERIES:
        print(
            f"ai-check: --queries must be {ai_visibility.MIN_QUERIES}–"
            f"{ai_visibility.MAX_QUERIES} (§5.5c). Below {ai_visibility.MIN_QUERIES} "
            f"`opp.ai_invisible` can never fire (M1.23); above "
            f"{ai_visibility.MAX_QUERIES} is the hard maximum.",
            file=sys.stderr,
        )
        return 2
    conn = db.connect(path)
    try:
        version = migrate.current_version(conn)
        highest = migrate.discover()[-1][0]
        if version < highest:
            print(
                f"database at {path} is at migration {version:03d} but the code "
                f"ships {highest:03d} — run `portal init` first (it is idempotent)",
                file=sys.stderr,
            )
            return 2
        plans, withheld = ai_visibility.prepare(conn, queries=queries, recheck=recheck)
        label = "--submit" if submit else "--dry-run"
        verb = "will be" if submit else "would be"
        print(f"ai-check {label}: {len(plans)} companies {verb} checked\n")
        for plan in plans:
            print(f"  {plan.domain:28} term: {plan.term!r}")
            for query in plan.queries:
                print(f"      „{query}“")
            print(f"      counts as named: {', '.join(plan.brand_terms)}")
        for entry in withheld:
            print(f"  {entry.domain:28} WITHHELD — {entry.reason}")

        floor = ai_visibility.unmeasured_floor(
            plans, provider=llm_anthropic.PROVIDER, model=model
        )
        searches = sum(len(p.queries) for p in plans) * ai_visibility.SEARCHES_PER_QUERY
        if not submit:
            print(
                f"\n{searches} search(es) at ${llm.WEB_SEARCH_PER_SEARCH_USD:.2f} plus "
                f"{ai_visibility.SEARCH_CONTEXT_TOKENS:,} allowance tokens per query "
                f"(as-of {ai_visibility.SEARCH_CONTEXT_AS_OF}) price at ${floor:.4f} "
                f"before the prompt is measured. Nothing was sent and nothing was "
                f"reserved. To reserve and run — §7 control 3, real spend — run "
                f"again with --submit."
            )
            return 0
        if not plans:
            print("\nnothing to check: every company was withheld", file=sys.stderr)
            return 2

        try:
            clearance = ledger.check_ceiling(conn)
        except ledger.CeilingExceeded as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        except sqlite3.Error as exc:
            print(
                f"refused: the §7 control 2 ledger is not readable ({exc}). Run "
                f"`portal init` on {path} first.",
                file=sys.stderr,
            )
            return 2
        print(
            f"\n§7 control 2: ${clearance.spend_usd:.2f} of "
            f"${clearance.ceiling_usd:.2f} used over {clearance.window_days} "
            f"rolling days; ${clearance.headroom_usd:.2f} headroom"
        )
        chosen = provider or llm_anthropic.AnthropicProvider(model=model)
        try:
            report = ai_visibility.run(conn, chosen, plans, clearance=clearance)
        except llm_anthropic.MissingKeyError as exc:
            # From `count_tokens`, before any run row exists: nothing is on
            # the books and there is nothing to abort.
            print(str(exc), file=sys.stderr)
            return 2
        except ai_visibility.RunCeilingExceeded as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    finally:
        conn.close()

    print(
        f"\nrun {report.run_id}: {len(report.checked)} of "
        f"{len(report.checked) + len(report.not_reached)} companies checked, "
        f"{report.web_searches} searches, {report.input_tokens:,} in / "
        f"{report.output_tokens:,} out tokens"
    )
    print(
        f"  reserved ${report.reserved_usd:.4f}, measured ${report.actual_usd:.4f}; "
        f"run.est_cost_usd now carries the measured actual (§7 control 3)"
    )
    if report.balance_exhausted:
        print(
            f"  ⛔ the balance ran dry; not reached: {', '.join(report.not_reached)}. "
            f"The run is finished with what was paid for (M1.105(c)); run again "
            f"once the key is topped up.",
            file=sys.stderr,
        )
        return 2
    print("  score with `portal score --phase 2`")
    return 0


def cmd_pagespeed(path: Path, dry_run: bool, max_calls: int) -> int:
    """§5.5a — PageSpeed Insights over the homepages §5.4 admitted.

    Free of charge (§7.1) and not free of consequence: every measurement is a
    request against a keyed quota and takes 15–30 s, so `--dry-run` shows the
    plan — who would be measured, who is cached under §5.3's 30-day rule, and
    who is withheld and why — without a key and without a call. The real run
    needs `PAGESPEED_API_KEY` in the environment and stops, saying so, if it is
    absent (§7 control 9). Its own `run.stage`, for migration 006's reason;
    see `portal/pagespeed.py`.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    conn = db.connect(path)
    try:
        version = migrate.current_version(conn)
        highest = migrate.discover()[-1][0]
        if version < highest:
            print(
                f"database at {path} is at migration {version:03d} but the code "
                f"ships {highest:03d} — run `portal init` first (it is idempotent)",
                file=sys.stderr,
            )
            return 2
        if dry_run:
            prepared = pagespeed.plan(conn)
            print(
                f"pagespeed --dry-run: {len(prepared.targets)} homepage(s) would be "
                f"measured ({pagespeed.STRATEGY}), {len(prepared.cached)} cached\n"
            )
            _print_plan(prepared)
            print("\nNothing was measured and no request was made.")
            return 0
        try:
            result = pagespeed.run(conn, max_calls=max_calls)
        except pagespeed.MissingKeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except pagespeed.PageSpeedError as exc:
            print(f"pagespeed refused: {exc}", file=sys.stderr)
            return 2
    finally:
        conn.close()

    print(
        f"\nrun {result.run_id}: pagespeed over {len(result.plan.targets)} "
        f"homepage(s), {result.calls} call(s) issued"
    )
    for outcome in result.outcomes:
        if outcome.status == "measured":
            print(f"  {outcome.domain:28} {outcome.score:>3}/100  {outcome.detail}")
        else:
            print(f"  {outcome.domain:28} FAILED — {outcome.detail}")
    _print_plan(result.plan)
    return 0


def _print_plan(prepared: pagespeed.Plan) -> None:
    """The half of the plan a run does not touch: cached and withheld rows."""
    for entry in prepared.cached:
        print(
            f"  {entry.domain:28} cached — {entry.score}/100 measured "
            f"{entry.observed_at} (run {entry.run_id}); §5.3 keeps it "
            f"{pagespeed.CACHE_DAYS} days"
        )
    for domain, reason in prepared.skipped:
        print(f"  {domain:28} SKIPPED — {reason}")


def cmd_reconcile(path: Path, model: str) -> int:
    """§5.6. Poll every open batch, verify what came back, write it down.

    **Finds its work in `llm_batch` and nowhere else**, which is what makes it
    survivable: a batch takes up to 24 hours and its results stay retrievable
    for 29 days, so the process that submitted it is routinely gone. Nothing is
    handed over.

    It needs `ANTHROPIC_API_KEY`, because polling is a call — a free one, on a
    result already paid for, but a call. It commits no new spend: every path
    here either reads a result or writes what a reservation already committed.

    **Today it will find nothing in production**, because `extract-p2` refuses
    to submit until 9c is authorised. That is the same order the ledger shipped
    in (M1.69–M1.71): the collector exists before the thing that gives it work,
    so the work is written against its presence.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    conn = db.connect(path)
    try:
        open_batches = reconcile_mod.open_batches(conn)
        reserved = reconcile_mod.reserved_batches(conn)
        if not open_batches and not reserved:
            print("reconcile: no open batches. Nothing to collect.")
            return 0
        provider = llm_anthropic.AnthropicProvider(model=model)
        result = reconcile_mod.run(conn, provider, config.artifacts_root(path))
    except llm_anthropic.MissingKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        conn.close()

    print(f"reconcile: run {result.run_id}, {len(result.batches)} batch(es) polled\n")
    for report in result.batches:
        arrow = f"{report.status_before} -> {report.status_after}"
        print(f"  batch {report.batch_id} ({report.purpose}) {arrow}")
        print(f"      {report.provider_batch_id}")
        if report.dispositions:
            summary = ", ".join(
                f"{n}x {name}" for name, n in sorted(report.dispositions.items())
            )
            print(f"      {summary}")
        print(
            f"      {report.signals_written} signal(s), "
            f"{report.contacts_written} contact(s)"
        )
        if report.actual_cost_usd is not None:
            print(
                f"      reserved ${report.est_cost_usd:.4f}, "
                f"measured ${report.actual_cost_usd:.4f}, "
                f"ledger {report.ledger_delta_usd:+.4f} on run"
            )
        if report.still_owed:
            # M1.86: a request with no result at all. Named, because
            # `request_count` cannot name it and nothing else would.
            print(f"      STILL OWED: {', '.join(report.still_owed)}")
        if report.resubmittable:
            print(
                f"      resubmittable as NEW spend (§5.6): "
                f"{', '.join(report.resubmittable)}"
            )
        if report.note:
            print(f"      {report.note}")
    for batch in result.reserved_unknown:
        # Migration 014. The money is counted and nothing releases it
        # automatically; only a person can say what happened.
        print(
            f"  batch {batch.id} ({batch.purpose}) RESERVED, submit outcome "
            f"unknown — ${batch.est_cost_usd:.4f} is counted against run "
            f"{batch.run_id} and will not be released automatically"
        )
    return 0


def cmd_score(path: Path, phase: int) -> int:
    """§5.4 / §6. A pure recompute over `company_profile` — no network, no cost.

    Printed as a **ranked lead list** rather than as a log, because that is what
    the stage produces: the ordering is the product, and a score whose reasons
    cannot be read is not a lead.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    conn = db.connect(path)
    try:
        version = migrate.current_version(conn)
        highest = migrate.discover()[-1][0]
        if version < highest:
            print(
                f"database at {path} is at migration {version:03d} but the code "
                f"ships {highest:03d} — run `portal init` first (it is idempotent)",
                file=sys.stderr,
            )
            return 2
        # §5.7: the warning goes out BEFORE the score is written, because a
        # provisional number that is printed and then explained is a number a
        # reader has already copied. It does not refuse — the score is the
        # best reading available and `reconcile` will supersede it under the
        # submitting run's own id (B4) — but it says so, loudly, on stderr.
        pending = score.unreconciled_batches(conn) if phase == 2 else []
        for batch in pending:
            who = ", ".join(batch.domains) if batch.domains else "no company rows"
            print(
                f"⚠ score --phase 2: batch {batch.batch_id} "
                f"({batch.purpose}, {batch.status}, submitted "
                f"{batch.submitted_at or 'never — reserved only'}, provider id "
                f"{batch.provider_batch_id or 'none'}) is NOT reconciled — "
                f"Phase-2 signals for {who} are still in flight. This score is "
                f"provisional for them; run `portal reconcile` and score again.",
                file=sys.stderr,
            )
        run_id, results = score.run(conn, phase=phase)
    finally:
        conn.close()

    if not results:
        print("no companies to score — run `portal extract-p1` first", file=sys.stderr)
        return 2
    if pending:
        print(
            f"\n⚠ {len(pending)} unreconciled batch(es) — see the warnings above; "
            f"this Phase-2 score is provisional (§5.7).",
            file=sys.stderr,
        )

    print(f"\nrun {run_id}: score --phase {phase}, ruleset {ruleset.RULESET_VERSION}")
    ranked = sorted(results, key=lambda r: (-r.total, r.domain))
    print(f"\n{'#':>2}  {'band':4} {'score':>5}  {'upside':>6} {'gate':4}  domain")
    print("─" * 72)
    for position, result in enumerate(ranked, 1):
        gate = "P2" if result.admitted else "stop"
        # The block is printed in the ranked list and not only in the detail
        # below it, because the ranked list is what a person reads before
        # deciding who to call (§8, A7's third axis).
        blocked = "  ⛔ KONTAKT GESPERRT" if result.contact_blocked else ""
        print(
            f"{position:>2}  {result.band:4} {result.total:>5}  "
            f"{result.remaining_upside:>6} {gate:4}  {result.domain}{blocked}"
        )

    for result in ranked:
        print(f"\n{result.domain} — {result.total} ({result.band})")
        for component in result.components:
            mark = f"{component.points:+d}" if component.points else "  ·"
            print(f"   {mark:>4}  {component.rule_id}")
            print(f"         {component.reason}")
        for flag in result.review_flags:
            print(f"    →  review: {flag.reason}")
        if result.contact_blocked:
            print(
                "    ⛔ Kontakt gesperrt: Die Bewertung ist möglicherweise zu "
                "hoch. Erst nach Prüfung kontaktieren."
            )
    return 0


def cmd_diff_signals(
    path: Path,
    from_run: int | None,
    to_run: int | None,
    stage: str,
    list_runs: bool,
) -> int:
    """M1.28 — what changed, per domain, between two runs.

    Costs nothing and touches no third party. The reason it is a command rather
    than a habit is in `portal/diff.py`: three defects in two milestones passed
    the suite and were caught only by comparing a run against the previous one,
    two of them hidden behind a plausible-looking state.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    conn = db.connect(path)
    try:
        candidates = diff.runs(conn, None if stage == "any" else stage)
        if list_runs:
            if not candidates:
                print("no runs have written signals yet")
                return 0
            for row in candidates:
                print(
                    f"  run {row['id']:>4}  {row['stage']:<12} {row['started_at']}"
                    f"  {row['signals']} signal(s)"
                )
            return 0

        if from_run is None or to_run is None:
            if len(candidates) < 2:
                print(
                    "need two runs that wrote signals to diff; "
                    f"found {len(candidates)} for stage {stage!r}",
                    file=sys.stderr,
                )
                return 2
            # Newest first, so the *later* run is the head of the list.
            to_run = to_run if to_run is not None else int(candidates[0]["id"])
            from_run = from_run if from_run is not None else int(candidates[1]["id"])

        changes = diff.compare(
            diff.snapshot(conn, from_run), diff.snapshot(conn, to_run)
        )
        print(diff.report(changes, from_run, to_run))
    finally:
        conn.close()
    return 0


def cmd_audit_politeness(log_path: Path, path: Path) -> int:
    """Report measured spacing, host concurrency and robots coverage. Non-zero
    if §5.2 was broken.

    Exits non-zero on a breach so this is usable as an acceptance check rather
    than only as something to read.

    The database is required, not optional (M1.62). Spacing alone was a green
    light over a policy that may never have been read: an unrestricted policy
    is measured against the *default* interval, and passes. A missing database
    is therefore an error here rather than a narrower audit.
    """
    if not log_path.exists():
        print(
            f"no request log at {log_path} — run `portal fetch` first", file=sys.stderr
        )
        return 2
    if not path.exists():
        print(
            f"no database at {path} — spacing alone cannot show whether a "
            "robots.txt was read (M1.62); run `portal init`",
            file=sys.stderr,
        )
        return 2
    conn = db.connect(path)
    try:
        text, ok = audit.report(log_path, conn=conn)
    finally:
        conn.close()
    print(text)
    return 0 if ok else 1


def cmd_audit_impressum_candidates(path: Path, show_values: bool) -> int:
    """M1.48 — the instrument behind A2 §8, committed rather than transcribed.

    Default output is counts of pattern presence and nothing else. `--show-values`
    prints the PLZ + Ort spans for the operator's accuracy check (A2 item 10) and
    **writes nothing** — that check needs the values, and §8 forbids an extracted
    personal value entering the repo or a report.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    root = config.artifacts_root(path)
    conn = db.connect(path)
    try:
        if show_values:
            rows = impressum_audit.plz_ort_values(conn, root)
            print("PLZ + Ort candidates — terminal only, nothing is written.")
            print("Values are personal-adjacent; do not paste them anywhere.\n")
            for domain, spans in rows:
                shown = "  |  ".join(spans) if spans else "(no match in block)"
                print(f"  {domain:26} {shown}")
            return 0
        print(impressum_audit.report(impressum_audit.audit(conn, root)))
    finally:
        conn.close()
    return 0


def cmd_llm_prices(database: Path, reserve_kb: float | None) -> int:
    """§7 controls 4, 10 and 11, made readable before any of them spends anything.

    Prints the two declared tables — prices with their as-of dates, and the
    per-model limits M1.50 says an interface must not generalise away — plus the
    §7.1 arithmetic derived from them. The tables issue no paid call, so this can
    be run at any time to answer "what does this tool currently believe a call
    costs, and as of when".

    **`--reserve` now opens the database, and that is the point.** It performs a
    real §7 control 4 reservation, and a real reservation is one the §7 control 2
    ledger has cleared — the printed tables still touch nothing.

    `--reserve KB` performs a real §7 control 4 reservation over a page of that
    size, which means a real `count_tokens` call and therefore a key. Without
    one it says so rather than substituting a heuristic (M1.52).
    """
    print("LLM prices — dated data, asserted at import (§7 control 10, M1.52)\n")
    header = f"  {'provider/model':28} {'mode':6} {'in $/MTok':>10} {'out $/MTok':>11}  as-of"
    print(header)
    for row in llm.PRICES:
        mode = "batch" if row.batch else "live"
        print(
            f"  {row.provider + '/' + row.model:28} {mode:6} "
            f"{row.input_per_mtok:10.2f} {row.output_per_mtok:11.2f}  "
            f"{row.as_of.isoformat()}  ({row.source})"
        )
    print(
        f"\n  web search: ${llm.WEB_SEARCH_PER_SEARCH_USD:.2f}/search, "
        f"as-of {llm.WEB_SEARCH_PRICE_AS_OF.isoformat()} — "
        "not discounted by the Batch API (§7 control 8)"
    )

    print("\nModel limits — declared, never inferred (M1.50)\n")
    for lim in llm.LIMITS:
        print(f"  {lim.provider}/{lim.model}  (verified {lim.verified_on.isoformat()})")
        print(
            f"    context {lim.context_tokens:,} tokens; max output "
            f"{lim.max_output_tokens:,}"
            + (
                "  ← below the 128K ceiling every other current model has"
                if lim.max_output_tokens < 128_000
                else ""
            )
        )
        print(
            f"    prompt-cache minimum {lim.cache_min_tokens:,} tokens — below it the "
            "write silently does not happen"
        )
        print(
            f"    thinking: {lim.thinking.value}; output_config.effort "
            f"{'accepted' if lim.supports_effort else 'ERRORS on this model'}"
        )
        print(
            f"    structured outputs: {'yes' if lim.supports_structured_outputs else 'no'}"
            f"; batch API: {'yes' if lim.supports_batch else 'no'}"
            f"; web search tool: {lim.web_search_tool or 'none'}"
        )

    est = llm.estimate_cost(
        input_tokens=30_000,
        output_tokens=0,
        provider="anthropic",
        model="claude-haiku-4-5",
        batch=True,
    )
    print(
        f"\n§7.1 check — 30k batch input tokens on {est.price.model}: "
        f"${est.total_usd:.4f} per advancing company"
    )
    print(
        "  (the spec's extraction row reads $0.015; this is arithmetic over the "
        "table above, not a second copy of that number)"
    )

    print(
        f"\nPrepaid balance: assumed to surface at "
        f"{llm.ASSUMED_BALANCE_FAILURE_POINT.value}-time (M1.53). UNVERIFIED — it "
        f"needs a live key. Both seams exist."
    )

    if reserve_kb is None:
        return 0

    print(f"\n§7 control 4 reservation over a {reserve_kb:g} KB page:")

    # §7 control 2 before §7 control 4, in that order: the outer bound decides
    # whether there is anything to price, and pricing first would mean the
    # runaway has already been measured before the guard that exists to stop it
    # is asked. `check_ceiling` raises on an unreadable ledger rather than
    # reading it as zero.
    conn = db.connect(database)
    try:
        clearance = ledger.check_ceiling(conn)
    except ledger.CeilingExceeded as exc:
        print(f"  refused: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        # A database with no `run` table is not a database with no spend.
        # Reading "no such table" as $0 is the fail-open direction, and it is
        # the one §7 control 2 exists to close — so this refuses and names the
        # fix rather than pricing a call against a ledger it could not read.
        print(
            f"  refused: the §7 control 2 ledger is not readable ({exc}). "
            f"Run `portal init` on {database} first — an unreadable ledger and "
            f"an empty one look alike, and treating this as $0 spent is how an "
            f"unmeasured number authorises a paid call.",
            file=sys.stderr,
        )
        return 2
    finally:
        conn.close()
    print(
        f"  §7 control 2: ${clearance.spend_usd:.2f} of "
        f"${clearance.ceiling_usd:.2f} used over {clearance.window_days} rolling "
        f"days; ${clearance.headroom_usd:.2f} headroom"
    )

    provider = llm_anthropic.AnthropicProvider()
    # A page of the given size, capped as §5.5b caps every real input at 60 KB.
    body = "Impressum. " * int(reserve_kb * 1024 / 11)
    request = llm.BatchRequest(
        custom_id="reservation-probe",
        system="Return null for any field not present on the page.",
        user_text=body[:61440],
        json_schema={"type": "object", "properties": {}},
        max_tokens=2048,
    )
    try:
        estimate = llm.reserve_batch(
            [request],
            provider=provider.name,
            model=provider.model,
            count_tokens=provider.token_counter(),
            clearance=clearance,
        )
    except llm_anthropic.MissingKeyError as exc:
        print(f"  cannot measure: {exc}")
        print(
            "  §7 control 4 takes its input from count_tokens, which is a network "
            "call. There is no offline substitute and a heuristic is refused "
            "(M1.52) — a fallback estimate is how an unmeasured number enters the "
            "ledger looking measured."
        )
        return 2
    print(
        f"  measured {estimate.input_tokens:,} input tokens + "
        f"{estimate.output_tokens:,} reserved output = ${estimate.total_usd:.4f}"
    )
    return 0


#: Hostnames that resolve to the loopback interface and are spelled, not numeric.
#: `ipaddress` cannot answer for these, and a name lookup would make the guard
#: depend on whatever the machine's resolver currently believes — which is the
#: one thing a security check must not do.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def is_loopback_bind(host: str) -> bool:
    """Does this bind address reach only this machine?

    Everything else — including the wildcards `0.0.0.0`, `::` and the empty
    string — is treated as public. A wildcard binds every interface the machine
    has, so it is the *most* exposed address rather than an unspecified one, and
    reading "unspecified" as "probably fine" is how this class of mistake is
    usually made.
    """
    name = host.strip().lower()
    if not name:
        return False
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name.strip("[]")).is_loopback
    except ValueError:
        # A hostname we cannot classify without a resolver. Refuse rather than
        # resolve: an unknown name is not evidence of a loopback interface.
        return False


def cmd_llm_batches(
    limit: int,
    *,
    model: str = llm_anthropic.DEFAULT_MODEL,
    provider: llm_anthropic.AnthropicProvider | None = None,
) -> int:
    """§10.7b's closing procedure, as a command (M1.104).

    Lists every message batch the account holds, newest first, and says in
    words what a listed batch means: committed spend, results retrievable for
    29 days from creation, resubmission doubles the cost. **Touches no
    database and reserves nothing** — it is a read of what has already been
    paid for, which is why it is classified free and needs no clearance.

    Exit 0 with batches: the question is closed and the answer is *not zero*;
    the ids are printed because with `llm_batch` gone they are the only route
    back to any results. Exit 0 with none: the account has never submitted.
    Exit 2 without a key: the question stays OPEN, and this command says so
    rather than reporting zero — §7 control 9 forbids finding a credential.
    """
    active = provider or llm_anthropic.AnthropicProvider(model)
    try:
        listed = active.list_batches(limit=limit)
    except llm_anthropic.MissingKeyError as exc:
        print(
            f"llm-batches: {exc}\n"
            "§10.7b stays OPEN. This is not zero: no key on this machine is a "
            "statement about this machine (M1.98, M1.100). Run it where the "
            "billing account's key is set, or read the Console's usage view.",
            file=sys.stderr,
        )
        return 2

    if not listed:
        print(
            f"llm-batches: the account holds no message batches (limit {limit}).\n"
            f"§10.7b is CLOSED with the answer ZERO, as of {utc_now()}: "
            "no batch has ever been submitted on this key's account. Record the "
            "date in the register — it is a measurement, not a fact about the code."
        )
        return 0

    print(f"llm-batches: {len(listed)} batch(es) on this account (newest first)\n")
    print(
        f"  {'id':40} {'status':12} {'created':22} {'ok':>4} {'err':>4} {'exp':>4} {'can':>4} {'run':>4}"
    )
    for b in listed:
        print(
            f"  {b.provider_batch_id:40} {b.processing_status:12} {b.created_at:22} "
            f"{b.succeeded:>4} {b.errored:>4} {b.expired:>4} {b.canceled:>4} {b.processing:>4}"
        )
    print(
        "\n§10.7b is CLOSED and the answer is NOT zero. Every batch above is "
        "committed spend whether or not its results were ever read (§5.6). "
        "Results stay retrievable for 29 days from `created`; retrieving costs "
        "nothing extra; RESUBMITTING WOULD DOUBLE THE COST. Write the ids down "
        "before anything else — with `llm_batch` gone they are the only route "
        "back to the results — and do not run `extract-p2 --submit` until each "
        "one is accounted for."
    )
    return 0


def cmd_serve(path: Path, host: str, port: int, allow_public_bind: bool = False) -> int:
    """§9's page. Refuses rather than starting against a database with no schema
    — an empty table is indistinguishable from a corpus nothing has scored.

    **And refuses a non-loopback bind unless it is asked for in words.** §1 says
    single operator, §8 says the data is third-party personal data, and until now
    both said it only in prose: `--host 0.0.0.0` printed a warning in `--help`
    that nobody reads at the moment they type it, and then published an
    unauthenticated database holding other people's Impressum details. The
    warning was text; this makes the code say it. Nothing about the default
    changes — the guard is invisible unless you leave loopback.

    Timing is deliberate: this lands **before M5**, which is when the database
    starts holding LLM-extracted personal data rather than page bytes.
    """
    if not path.exists():
        print(f"no database at {path} — run `portal init` first", file=sys.stderr)
        return 2
    if not is_loopback_bind(host) and not allow_public_bind:
        print(
            f"refusing to bind {host!r}: that is not a loopback address, and this "
            f"tool has no authentication of any kind (§1). Binding it publishes a "
            f"database of third-party personal data (§8) to everything that can "
            f"reach this host.\n"
            f"If that is genuinely what you want, say so: "
            f"--host {host} --allow-public-bind",
            file=sys.stderr,
        )
        return 2
    if not is_loopback_bind(host):
        # Asked for explicitly — and off loopback, credentials are not optional
        # (audit finding 5): §8's rows are third-party personal data.
        try:
            has_auth = serve_mod.basic_auth_from_env() is not None
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not has_auth:
            print(
                f"refusing to bind {host!r} without credentials: set "
                f"{serve_mod.BASIC_AUTH_ENV}=user:password in the environment "
                f"first. A public bind with no authentication publishes a database "
                f"of third-party personal data (§8).",
                file=sys.stderr,
            )
            return 2
        print(
            f"WARNING: binding {host} — the page is reachable from other machines; "
            f"HTTP Basic auth is on, and it should also sit behind TLS.",
            file=sys.stderr,
        )
    print(f"lead portal on http://{host}:{port}  (database: {path})")
    return serve_mod.serve(host, port, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portal",
        description="Lead Portal — DACH e-commerce lead research (localhost only).",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="database path (default: PORTAL_DB, else data/portal.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "init",
        help="create the database and apply all migrations; safe to re-run",
    )

    fetch_parser = sub.add_parser(
        "fetch", help="fetch robots, homepage, sitemaps, Impressum and a product sample"
    )
    fetch_parser.add_argument(
        "--seed", type=Path, required=True, help="seed CSV with a 'domain' column"
    )
    fetch_parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="seconds between requests to one host (default: 1.0, the §5.2 floor). "
        "Values below the floor are refused.",
    )
    sub.add_parser(
        "extract-p1",
        help="deterministic §5.3 signals from artifacts already on disk; no requests",
    )

    score_parser = sub.add_parser(
        "score",
        help="§6 ruleset over company_profile as a ranked lead list; pure, no cost",
    )
    score_parser.add_argument(
        "--phase",
        type=int,
        choices=(1, 2),
        default=1,
        help="scoring phase (default: 1)",
    )

    diff_parser = sub.add_parser(
        "diff-signals",
        help="per-domain signal diff between two runs; defaults to the last two",
    )
    diff_parser.add_argument(
        "--from", dest="from_run", type=int, default=None, help="earlier run id"
    )
    diff_parser.add_argument(
        "--to", dest="to_run", type=int, default=None, help="later run id"
    )
    diff_parser.add_argument(
        "--stage",
        default="extract-p1",
        help="restrict the default run pair to one stage (default: extract-p1); "
        "'any' compares across stages",
    )
    diff_parser.add_argument(
        "--list", action="store_true", help="list runs that wrote signals and exit"
    )

    serve_parser = sub.add_parser(
        "serve",
        help="the §9 review page on localhost; read-only except flag resolution",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (default: 127.0.0.1). §1 is a single-operator tool with "
        "no auth, so binding anywhere reachable publishes an unauthenticated database.",
    )
    serve_parser.add_argument(
        "--port", type=int, default=8000, help="port (default: 8000)"
    )
    serve_parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        help="permit a non-loopback --host. Required, because binding one "
        "publishes an unauthenticated database of third-party personal data "
        "(§1, §8). Without this flag a non-loopback address is refused.",
    )

    audit_parser = sub.add_parser(
        "audit-politeness",
        help="measure §5.2 spacing and host concurrency from the request log, "
        "and robots.txt coverage from the artifact table",
    )
    audit_parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="request log (default: data/requests.jsonl)",
    )

    candidates_parser = sub.add_parser(
        "audit-impressum-candidates",
        help="which Impressum fields a deterministic parser could reach (M1.48); "
        "counts only, nothing is written",
    )
    candidates_parser.add_argument(
        "--show-values",
        action="store_true",
        help="print the PLZ + Ort spans to this terminal for the accuracy check "
        "(A2 item 10). Writes nothing. Do not paste the output anywhere.",
    )

    p2_parser = sub.add_parser(
        "extract-p2",
        help="§5.5b's paid extraction. A dry run unless --submit is given: "
        "prints what would be sent and sends nothing",
    )
    p2_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent and send nothing (the default)",
    )
    p2_parser.add_argument(
        "--submit",
        action="store_true",
        help="reserve (§7 control 4) and submit the batch. THIS SPENDS MONEY: "
        "it is the written authorisation for the first real spend, expressed "
        "at the command line. Needs ANTHROPIC_API_KEY.",
    )
    p2_parser.add_argument(
        "--purpose",
        choices=extract_p2.PURPOSES,
        default="impressum",
        help="which §5.5b extraction to prepare or submit (default: impressum); "
        "a batch is one purpose (§4)",
    )
    p2_parser.add_argument(
        "--model",
        default=llm_anthropic.DEFAULT_MODEL,
        help=f"the model to submit to (default {llm_anthropic.DEFAULT_MODEL})",
    )

    ai_parser = sub.add_parser(
        "ai-check",
        help="§5.5c's AI-visibility check (M6). A dry run unless --submit is "
        "given: prints the literal queries and sends nothing",
    )
    ai_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and send nothing (the default)",
    )
    ai_parser.add_argument(
        "--submit",
        action="store_true",
        help="reserve (§7 control 3) and run the live web-search calls. THIS "
        "SPENDS MONEY: ~$0.05–0.06 per company. Needs ANTHROPIC_API_KEY.",
    )
    ai_parser.add_argument(
        "--queries",
        type=int,
        default=ai_visibility.DEFAULT_QUERIES,
        help=f"queries per company, {ai_visibility.MIN_QUERIES}–"
        f"{ai_visibility.MAX_QUERIES} (default {ai_visibility.DEFAULT_QUERIES})",
    )
    ai_parser.add_argument(
        "--recheck",
        action="store_true",
        help="include companies already checked; a re-check is new spend",
    )
    ai_parser.add_argument(
        "--model",
        default=llm_anthropic.DEFAULT_MODEL,
        help=f"the model to ask (default {llm_anthropic.DEFAULT_MODEL})",
    )

    pagespeed_parser = sub.add_parser(
        "pagespeed",
        help="§5.5a: PageSpeed Insights over admitted homepages; free tier, keyed "
        "quota, 15–30 s per site, cached 30 days (§5.3)",
    )
    pagespeed_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print who would be measured, who is cached and who is withheld; "
        "no key needed, no request made",
    )
    pagespeed_parser.add_argument(
        "--max-calls",
        type=int,
        default=pagespeed.MAX_CALLS_PER_RUN,
        help=f"refuse a run that would issue more requests than this "
        f"(default {pagespeed.MAX_CALLS_PER_RUN}); a runaway guard, not a budget",
    )

    reconcile_parser = sub.add_parser(
        "reconcile",
        help="§5.6: poll every open batch, verify results against the sent "
        "text, write signals and contacts. Reads its work from the database",
    )
    reconcile_parser.add_argument(
        "--model",
        default=llm_anthropic.DEFAULT_MODEL,
        help=f"the model whose batches to poll (default {llm_anthropic.DEFAULT_MODEL})",
    )

    batches_parser = sub.add_parser(
        "llm-batches",
        help="§10.7b: list every message batch on the account — read-only, no "
        "spend, needs ANTHROPIC_API_KEY. The closing procedure, as a command",
    )
    batches_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="how many batches to list, newest first (default 20)",
    )
    batches_parser.add_argument(
        "--model",
        default=llm_anthropic.DEFAULT_MODEL,
        help=f"provider model (only used to build the client; default "
        f"{llm_anthropic.DEFAULT_MODEL})",
    )

    prices_parser = sub.add_parser(
        "llm-prices",
        help="the declared price and model-limit tables with their as-of dates; "
        "no database, no paid call",
    )
    prices_parser.add_argument(
        "--reserve",
        type=float,
        default=None,
        metavar="KB",
        help="perform a real §7 control 4 reservation over a page of this size. "
        "Needs ANTHROPIC_API_KEY: count_tokens is a network call and there is no "
        "offline substitute (M1.52).",
    )

    fetch_parser.add_argument(
        "--max-hosts",
        type=int,
        default=MAX_CONCURRENT_HOSTS,
        help=f"hosts in flight (default and §5.2 ceiling: {MAX_CONCURRENT_HOSTS})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.db if args.db is not None else config.db_path()

    if args.command == "init":
        return cmd_init(path)

    if args.command == "fetch":
        # §5.2's politeness numbers are hard requirements, so the CLI cannot be
        # used to go below the floor or above the ceiling — not even by typo.
        interval = (
            config.POLITENESS_INTERVAL if args.interval is None else args.interval
        )
        if interval < config.POLITENESS_INTERVAL:
            print(
                f"--interval {interval} is below the §5.2 floor of "
                f"{config.POLITENESS_INTERVAL}s per host; refusing",
                file=sys.stderr,
            )
            return 2
        if args.max_hosts < 1 or args.max_hosts > MAX_CONCURRENT_HOSTS:
            print(
                f"--max-hosts must be between 1 and the §5.2 ceiling of "
                f"{MAX_CONCURRENT_HOSTS}; refusing",
                file=sys.stderr,
            )
            return 2
        return cmd_fetch(path, args.seed, interval, args.max_hosts)

    if args.command == "extract-p1":
        return cmd_extract_p1(path)

    if args.command == "extract-p2":
        return cmd_extract_p2(
            path,
            dry_run=args.dry_run,
            submit=args.submit,
            purpose=args.purpose,
            model=args.model,
        )
    if args.command == "reconcile":
        return cmd_reconcile(path, args.model)

    if args.command == "ai-check":
        return cmd_ai_check(
            path,
            dry_run=args.dry_run,
            submit=args.submit,
            queries=args.queries,
            recheck=args.recheck,
            model=args.model,
        )
    if args.command == "pagespeed":
        if args.max_calls < 1:
            print("--max-calls must be at least 1; refusing", file=sys.stderr)
            return 2
        return cmd_pagespeed(path, args.dry_run, args.max_calls)

    if args.command == "score":
        return cmd_score(path, args.phase)

    if args.command == "diff-signals":
        return cmd_diff_signals(path, args.from_run, args.to_run, args.stage, args.list)

    if args.command == "serve":
        return cmd_serve(path, args.host, args.port, args.allow_public_bind)

    if args.command == "audit-politeness":
        return cmd_audit_politeness(
            args.log if args.log is not None else config.request_log_path(path),
            path,
        )

    if args.command == "audit-impressum-candidates":
        return cmd_audit_impressum_candidates(path, args.show_values)

    if args.command == "llm-batches":
        return cmd_llm_batches(args.limit, model=args.model)
    if args.command == "llm-prices":
        return cmd_llm_prices(path, args.reserve)

    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
