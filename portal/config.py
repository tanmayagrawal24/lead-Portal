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


def db_path() -> Path:
    """The database path, honouring the PORTAL_DB environment override."""
    override = os.environ.get("PORTAL_DB")
    return Path(override).expanduser() if override else DEFAULT_DB_PATH
