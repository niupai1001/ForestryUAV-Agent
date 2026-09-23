from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from ..storage import AssetError


class ExecutionRecords:
    def __init__(self, workspace: Path):
        self.database = workspace / ".runtime" / "executions.sqlite3"
        with self.db() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL, before_json TEXT NOT NULL, artifacts_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL, updated_at REAL NOT NULL, after_json TEXT)"""
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "after_json" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN after_json TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS job_fingerprint ON jobs(fingerprint, created_at)")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add(self, job_id: str, fingerprint: str, kind: str, before: dict) -> None:
        now = time.time()
        with self.db() as conn:
            conn.execute(
                "INSERT INTO jobs(id,fingerprint,kind,state,before_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, fingerprint, kind, "submitting", json.dumps(before), now, now),
            )

    def get(self, job_id: str) -> dict:
        with self.db() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise AssetError("Code job is not registered in this workspace")
        return dict(row)

    def update(self, job_id: str, state: str, artifacts: list[dict] | None = None,
               after: dict[str, tuple[int, int]] | None = None) -> None:
        with self.db() as conn:
            conn.execute(
                """UPDATE jobs SET state=?, artifacts_json=COALESCE(?, artifacts_json),
                after_json=COALESCE(?, after_json), updated_at=? WHERE id=?""",
                (state, json.dumps(artifacts) if artifacts is not None else None,
                 json.dumps(after) if after is not None else None, time.time(), job_id),
            )


def snapshot_workspace(root: Path) -> dict[str, tuple[int, int]]:
    result = {}
    for path in root.rglob("*"):
        if path.is_file() and ".runtime" not in path.relative_to(root).parts:
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result
