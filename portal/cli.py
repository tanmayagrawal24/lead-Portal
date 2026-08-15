"""Command-line entry point.

Each pipeline stage is its own subcommand, independently re-runnable (§5).
Only `init` exists so far; the rest arrive with their milestones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portal import __version__, config, db, migrate


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.db if args.db is not None else config.db_path()

    if args.command == "init":
        return cmd_init(path)

    raise AssertionError(f"unhandled command: {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
