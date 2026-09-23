"""Run real-browser UI contracts and preserve Playwright's raw JSON report."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Any


def collect_browser(output: Path, *, project_root: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    executable = project_root / "frontend" / "node_modules" / ".bin" / (
        "playwright.cmd" if os.name == "nt" else "playwright"
    )
    command = [str(executable), "test", "--reporter=json"]
    completed = subprocess.run(
        command, cwd=project_root / "frontend",
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=240, check=False,
    )
    raw_report = output / "playwright-report.json"
    raw_report.write_text(completed.stdout, encoding="utf-8")
    (output / "playwright-stderr.txt").write_text(completed.stderr, encoding="utf-8")
    summary: dict[str, Any] = {
        "command": command,
        "returncode": completed.returncode,
        "report": raw_report.name,
    }
    try:
        report = json.loads(completed.stdout)
        summary["stats"] = report.get("stats", {})
        summary["errors"] = report.get("errors", [])
    except json.JSONDecodeError as exc:
        summary["collector_error"] = f"Playwright did not emit JSON: {exc}"
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    summary = collect_browser(args.output, project_root=args.project_root.resolve())
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["returncode"] == 0 and "collector_error" not in summary else 2


if __name__ == "__main__":
    raise SystemExit(main())
