"""后台运行本地 ODM，并把可靠的退出状态写入 job.json。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .odm import gdal_environment
from .project import now_iso, save_json


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    job_path = Path(sys.argv[1])
    log_path = Path(sys.argv[2])
    odm_home = Path(sys.argv[3])
    proj_data = Path(sys.argv[4]) if sys.argv[4] else None
    job = json.loads(job_path.read_text(encoding="utf-8"))
    command = [str(item) for item in job["command"]]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as text_log:
        text_log.write(
            f"\n[{now_iso()}] Starting: {subprocess.list2cmdline(command)}\n"
        )
    with log_path.open("ab") as binary_log:
        completed = subprocess.run(
            command,
            cwd=str(odm_home),
            stdout=binary_log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=gdal_environment(odm_home, proj_data),
            check=False,
        )

    current = json.loads(job_path.read_text(encoding="utf-8"))
    current.update(
        {
            "state": "succeeded" if completed.returncode == 0 else "failed",
            "return_code": completed.returncode,
            "finished_at": now_iso(),
        }
    )
    save_json(job_path, current)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
