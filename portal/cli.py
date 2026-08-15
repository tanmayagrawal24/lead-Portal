"""Command-line entry point.

Each pipeline stage is its own subcommand, independently re-runnable (§5).
Only `init` exists so far; the rest arrive with their milestones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portal import __version__, config, db, fetch, migrate, seeds
from portal.net import MAX_CONCURRENT_HOSTS, Fetcher, HostRateLimiter


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
        company_ids = seeds.upsert(conn, rows, query=str(seed_path))
        targets = [
            (company_id, seed.domain)
            for company_id, seed in zip(company_ids, rows, strict=True)
        ]
        print(
            f"Fetching {len(targets)} domain(s) at {interval}s/host, {max_hosts} hosts max…"
        )
        fetcher = Fetcher(limiter=HostRateLimiter(interval))
        run_id, results = fetch.run(
            conn,
            targets,
            config.artifacts_root(path),
            fetcher=fetcher,
            max_hosts=max_hosts,
        )
    finally:
        conn.close()

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

    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
