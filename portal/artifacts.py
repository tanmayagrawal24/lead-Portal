"""Artifact storage: bodies on disk, rows in SQLite (§4, §5.2).

Bodies are never stored in SQLite. `artifact.body_path` holds a path relative
to the artifacts root, so the whole `data/` tree can be moved without
rewriting rows.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from portal.net import Response

KINDS = ("robots", "homepage", "sitemap", "impressum", "blog_index", "product_page")

_UNSAFE = re.compile(r"[^a-z0-9._-]+")

#: Extension per kind, so the on-disk tree is browsable.
_SUFFIX = {"robots": "txt", "sitemap": "xml"}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")


def content_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: int
    kind: str
    url: str
    status: int | None
    content_hash: str | None
    body_path: str | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.content_hash is not None


class ArtifactStore:
    """Writes bodies under `root/{domain}/{kind}-{timestamp}.{ext}` (§5.2)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _write_body(self, domain: str, kind: str, body: bytes) -> str:
        safe_domain = _UNSAFE.sub("-", domain.lower()) or "unknown"
        directory = self.root / safe_domain
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{kind}-{_stamp()}.{_SUFFIX.get(kind, 'html')}"
        (directory / name).write_bytes(body)
        return f"{safe_domain}/{name}"

    def body_of(self, artifact: StoredArtifact) -> bytes:
        if not artifact.body_path:
            return b""
        return (self.root / artifact.body_path).read_bytes()

    def record(
        self,
        conn: sqlite3.Connection,
        company_id: int,
        domain: str,
        kind: str,
        response: Response,
    ) -> StoredArtifact:
        """Store one fetch attempt, successful or not.

        Success uses the §4 D5(b) upsert: identical content never creates a
        second row, and `last_checked_at` advances so the 30-day PageSpeed
        cache rule in §5.3 stays answerable.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown artifact kind: {kind!r}")
        now = utc_now()

        if response.body is None or response.status != 200:
            return self._record_failure(conn, company_id, kind, response, now)

        digest = content_hash(response.body)
        existing = conn.execute(
            "SELECT id, body_path FROM artifact "
            "WHERE company_id = ? AND kind = ? AND content_hash = ?",
            (company_id, kind, digest),
        ).fetchone()
        body_path = (
            existing["body_path"]
            if existing
            else self._write_body(domain, kind, response.body)
        )

        conn.execute(
            """
            INSERT INTO artifact
                (company_id, kind, url, http_status, content_hash, body_path, bytes,
                 fetched_at, last_checked_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            -- The WHERE repeats uq_artifact_identity's partial-index predicate;
            -- SQLite will not match the conflict target without it (§4).
            ON CONFLICT (company_id, kind, content_hash) WHERE content_hash IS NOT NULL
            DO UPDATE
            SET last_checked_at = excluded.last_checked_at,
                http_status     = excluded.http_status
            """,
            (
                company_id,
                kind,
                response.url,
                response.status,
                digest,
                body_path,
                len(response.body),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM artifact WHERE company_id = ? AND kind = ? AND content_hash = ?",
            (company_id, kind, digest),
        ).fetchone()
        return StoredArtifact(
            int(row["id"]), kind, response.url, response.status, digest, body_path, None
        )

    def _record_failure(
        self,
        conn: sqlite3.Connection,
        company_id: int,
        kind: str,
        response: Response,
        now: str,
    ) -> StoredArtifact:
        """Failures carry no content_hash, so `uq_artifact_identity` — a partial
        index over non-NULL hashes — does not apply to them.

        Left to a plain INSERT, every re-run would append another row for the
        same dead URL and the table would grow without bound. So a failure row
        for the same (company, kind, url) is updated in place instead. The
        history of *when* a URL last failed is worth keeping; a row per attempt
        is not.
        """
        error = response.error or f"http_{response.status}"
        existing = conn.execute(
            "SELECT id FROM artifact "
            "WHERE company_id = ? AND kind = ? AND url = ? AND content_hash IS NULL",
            (company_id, kind, response.url),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE artifact SET http_status = ?, error = ?, last_checked_at = ? WHERE id = ?",
                (response.status, error, now, existing["id"]),
            )
            artifact_id = int(existing["id"])
        else:
            cursor = conn.execute(
                """
                INSERT INTO artifact
                    (company_id, kind, url, http_status, error, fetched_at, last_checked_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (company_id, kind, response.url, response.status, error, now, now),
            )
            artifact_id = int(cursor.lastrowid)
        return StoredArtifact(
            artifact_id, kind, response.url, response.status, None, None, error
        )
