"""Clear stale active Runs so Runtime startup reconciliation can complete.

The Runtime reconciles every active Run at startup by probing every durable job
reference. After a series of interrupted collections the store held dozens of Runs
that could never settle, each probe waiting on the host bridge, so startup never
finished and the service restarted forever. Their evidence is infrastructure-only
(no graded trial depends on it), and the trial workspaces live under their own
session directories, which this does not touch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time

ACTIVE = ("queued", "running", "waiting", "canceling")


def clear(database: Path, *, dry_run: bool = False) -> dict:
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, state, chat_id FROM runs WHERE state IN "
            "('queued','running','waiting','canceling')"
        ).fetchall()
        run_ids = [row["id"] for row in rows]
        jobs = 0
        if run_ids:
            jobs = connection.execute(
                "SELECT COUNT(*) FROM job_refs WHERE run_id IN "
                f"({','.join('?' * len(run_ids))})",
                run_ids,
            ).fetchone()[0]
        report = {
            "database": str(database),
            "active_runs": len(run_ids),
            "job_refs": jobs,
            "states": {},
            "dry_run": dry_run,
        }
        for row in rows:
            report["states"][row["state"]] = report["states"].get(row["state"], 0) + 1
        if dry_run or not run_ids:
            return report
        now = time.time()
        placeholders = ",".join("?" * len(run_ids))
        connection.execute(
            f"UPDATE turns SET state='canceled', updated_at=? WHERE run_id IN ({placeholders})",
            (now, *run_ids),
        )
        connection.execute(
            f"UPDATE runs SET state='canceled', cancel_requested=1, pause_requested=0, "
            f"final_json=?, updated_at=? WHERE id IN ({placeholders})",
            (
                json.dumps({
                    "error": "cleared by evaluation maintenance: the Run could not be "
                             "reconciled and blocked Runtime startup",
                }, ensure_ascii=False),
                now, *run_ids,
            ),
        )
        connection.commit()
        report["cleared"] = len(run_ids)
        return report
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/runs.sqlite3"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(clear(args.database, dry_run=args.dry_run),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
