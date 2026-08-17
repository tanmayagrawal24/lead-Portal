"""Command-line entry point.

Each pipeline stage is its own subcommand, independently re-runnable (§5).
Built so far: `init`, `fetch`, `extract-p1`, `score`, `serve`, plus the
inspection commands `diff-signals`, `audit-politeness`,
`audit-impressum-candidates` and `llm-prices`. `extract-p2` and `reconcile`
arrive with M5; `discover` with M8.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path

from portal import (
    __version__,
    audit,
    config,
    db,
    diff,
    extract,
    fetch,
    impressum_audit,
    llm,
    llm_anthropic,
    migrate,
    ruleset,
    score,
    seeds,
)
from portal import (
    serve as serve_mod,
)
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
    """Fetch every seeded domain under the §5.2 politeness rules."""
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
        targets = [
            (company_id, seed.domain)
            for company_id, seed in zip(company_ids, rows, strict=True)
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
    for result in results:
        if result.excluded_reason:
            print(f"  {result.domain}: EXCLUDED — {result.excluded_reason}")
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
        run_id, results = score.run(conn, phase=phase)
    finally:
        conn.close()

    if not results:
        print("no companies to score — run `portal extract-p1` first", file=sys.stderr)
        return 2

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


def cmd_llm_prices(reserve_kb: float | None) -> int:
    """§7 controls 4, 10 and 11, made readable before any of them spends anything.

    Prints the two declared tables — prices with their as-of dates, and the
    per-model limits M1.50 says an interface must not generalise away — plus the
    §7.1 arithmetic derived from them. It touches no database and issues no paid
    call, so it can be run at any time to answer "what does this tool currently
    believe a call costs, and as of when".

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
        # Asked for explicitly, and still worth saying out loud on the way past.
        print(
            f"WARNING: binding {host} — the database is served without "
            f"authentication to anything that can reach this host.",
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

    if args.command == "llm-prices":
        return cmd_llm_prices(args.reserve)

    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
