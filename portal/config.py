"""Runtime configuration.

Secrets come from the environment only and are never read here — this module
holds paths, which are not secrets. See §7 control 9.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Default database location. Override with PORTAL_DB or the CLI's --db.
DEFAULT_DB_PATH = REPO_ROOT / "data" / "portal.db"


#: §5.2 politeness floor, in seconds per host. Not configurable below this.
POLITENESS_INTERVAL = 1.0


def db_path() -> Path:
    """The database path, honouring the PORTAL_DB environment override."""
    override = os.environ.get("PORTAL_DB")
    return Path(override).expanduser() if override else DEFAULT_DB_PATH


def request_log_path(database: Path | None = None) -> Path:
    """Where every issued request is logged (§5.2, M1.19), beside the database.

    Appended across runs rather than truncated: each line carries its own
    timestamp, and "how did we behave last month" is a question worth being
    able to answer. It lives under `data/`, which is gitignored — request logs
    name third-party hosts and are local evidence, not repository content.
    """
    base = (database or db_path()).parent
    return base / "requests.jsonl"


def artifacts_root(database: Path | None = None) -> Path:
    """Where fetched bodies live: `data/artifacts/` beside the database (§5.2)."""
    base = (database or db_path()).parent
    return base / "artifacts"
