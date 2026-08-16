"""Command-line entry point.

Each pipeline stage is its own subcommand, independently re-runnable (§5).
Only `init` exists so far; the rest arrive with their milestones.
"""

from __future__ import annotations

import argparse
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
    migrate,
    ruleset,
    score,
    seeds,
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
        print(
            f"{position:>2}  {result.band:4} {result.total:>5}  "
            f"{result.remaining_upside:>6} {gate:4}  {result.domain}"
        )

    for result in ranked:
        print(f"\n{result.domain} — {result.total} ({result.band})")
        for component in result.components:
            mark = f"{component.points:+d}" if component.points else "  ·"
            print(f"   {mark:>4}  {component.rule_id}")
            print(f"         {component.reason}")
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


def cmd_audit_politeness(log_path: Path) -> int:
    """Report measured spacing and host concurrency. Non-zero if §5.2 was broken.

    Exits non-zero on a breach so this is usable as an acceptance check rather
    than only as something to read.
    """
    if not log_path.exists():
        print(
            f"no request log at {log_path} — run `portal fetch` first", file=sys.stderr
        )
        return 2
    text, ok = audit.report(log_path)
    print(text)
    return 0 if ok else 1


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

    audit_parser = sub.add_parser(
        "audit-politeness",
        help="measure §5.2 spacing and host concurrency from the request log",
    )
    audit_parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="request log (default: data/requests.jsonl)",
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

    if args.command == "audit-politeness":
        return cmd_audit_politeness(
            args.log if args.log is not None else config.request_log_path(path)
        )

    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
