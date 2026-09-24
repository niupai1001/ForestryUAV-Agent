"""Run one frozen grounded task through the deployed Runtime and grade it.

This is the operator path for a single trial:

1. read the frozen ``TaskSpec`` from ``tasks.grounded-v1.1.json``;
2. upload the task's public inputs to the Runtime and start one Run;
3. wait for a terminal state, then collect every artifact the agent produced;
4. grade that evidence with the read-only grader and write a ``GradeReport``.

It never runs the agent's code on the host and never reads the private truth
before grading. The workspace is fresh per trial, because each trial must start
from a clean session.

    python -m evaluation.grounded_v1.run_task --task oam-01 --repeat 1
    python -m evaluation.grounded_v1.run_task --task oam-05 --repeat 2 --timeout 1800
    python -m evaluation.grounded_v1.run_task --list

The Runtime must already be serving. ``python -m evaluation.grounded_v1.run_task
--list`` prints the frozen tasks, their declared inputs and the time budget.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_TASKS_FILE = Path("evaluation/grounded_v1/tasks.grounded-v1.1.json")
DEFAULT_RUNS_ROOT = Path("data/runs")


def load_task(tasks_file: Path, task_id: str) -> dict:
    payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    for task in payload["tasks"]:
        if task["task_id"] == task_id:
            return task
    known = ", ".join(task["task_id"] for task in payload["tasks"])
    raise KeyError(f"unknown task {task_id!r}; the frozen suite has: {known}")


def list_tasks(tasks_file: Path) -> None:
    payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    print(f"suite {payload['suite_version']} -- {payload['task_count']} tasks")
    for task in payload["tasks"]:
        inputs = ", ".join(task["public_inputs"])
        print(f"  {task['task_id']:<34} {task['family']:<18} "
              f"{task['budget']['wall_seconds']:>5}s  {inputs}")


def _client(base_url: str, api_key: str, owner: str, chat_id: str):
    """Reuse the project collector when importable, so headers stay in one place."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evaluation.collect.api import RuntimeApiClient

    return RuntimeApiClient(base_url=base_url, api_key=api_key, owner=owner, chat_id=chat_id)


def run_trial(
    *, task: dict, base_url: str, api_key: str, owner: str, repeat: int,
    runs_root: Path, timeout_seconds: int, configuration: dict,
) -> dict:
    """Collect one trial. Returns the collector's result plus the trial path."""
    chat_id = str(uuid.uuid4())
    package = runs_root / f"grounded-{task['task_id']}-r{repeat}"
    if package.exists():
        raise FileExistsError(
            f"{package} already exists. A frozen slot is never overwritten: move it "
            "aside or pick another repeat."
        )
    client = _client(base_url, api_key, owner, chat_id)
    client.wait_until_healthy()

    fixtures = [Path(task["public_root"]) / name for name in task["public_inputs"]]
    missing = [str(path) for path in fixtures if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"declared public inputs are missing: {missing}")

    from evaluation.collect.api import collect_trial

    result = collect_trial(
        client=client, case_id=task["task_id"], repeat=repeat,
        prompt=task["question"], fixture_files=fixtures, output=package,
        configuration=configuration, timeout_seconds=timeout_seconds,
    )
    result["package"] = str(package)
    return result


PROBE_DIR = "probe"

PROBE_QUESTIONS = {
    "canopy_extraction": (
        "只做环境探查，不要交付成果：读出本任务输入栅格的行数、列数、波段数和坐标参考系统，"
        "并报告你实际能用的库（例如 rasterio、numpy、scipy、geopandas、shapely、pyproj 哪些可用）。"
    ),
    "canopy_statistics": (
        "只做环境探查，不要交付成果：读出本任务输入栅格的行数、列数、波段数和坐标参考系统，"
        "并报告你实际能用的库（例如 rasterio、numpy、scipy、geopandas、shapely、pyproj 哪些可用）。"
    ),
    "gis_analysis": (
        "只做环境探查，不要交付成果：列出本任务输入文件的实际文件名，并报告你实际能用的库"
        "（例如 geopandas、shapely、pyproj、fiona、rasterio、pandas 哪些可用），"
        "以及能否在不联网的情况下安装缺失的库。"
    ),
}


def probe_trial(
    *, task: dict, base_url: str, api_key: str, owner: str, runs_root: Path,
    timeout_seconds: int,
) -> dict:
    """One throwaway run that only reports the agent's real environment.

    A probe is not a trial. It is not graded, not counted, and carries a distinct
    package name so it can never be mistaken for a frozen slot. Its purpose is to
    surface the blockers -- a missing geospatial stack, an unreadable input, a
    budget that is far too small -- before any of the three real repeats are spent
    on them.
    """
    package = runs_root / f"grounded-{task['task_id']}-{PROBE_DIR}"
    if package.exists():
        raise FileExistsError(f"{package} already exists; remove it to probe again")
    client = _client(base_url, api_key, owner, str(uuid.uuid4()))
    client.wait_until_healthy()
    fixtures = [Path(task["public_root"]) / name for name in task["public_inputs"]]
    question = PROBE_QUESTIONS.get(task["family"], PROBE_QUESTIONS["canopy_extraction"])

    from evaluation.collect.api import collect_trial

    result = collect_trial(
        client=client, case_id=f"{task['task_id']}-probe", repeat=0, prompt=question,
        fixture_files=fixtures, output=package,
        configuration={"harness": "forestry-runtime", "probe": True,
                       "task_id": task["task_id"], "base_url": base_url},
        timeout_seconds=timeout_seconds,
    )
    result["package"] = str(package)
    return result


def _answer_text(events_path: Path) -> str:
    """The assistant messages of a collected trial, joined for a quick read."""
    if not events_path.is_file():
        return ""
    try:
        events = json.loads(events_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(events, list):
        return ""
    return "\n".join(
        str(event.get("content") or "") for event in events
        if isinstance(event, dict) and event.get("type") == "message"
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", help="frozen task id, for example oam-01")
    parser.add_argument("--repeat", type=int, default=1, help="which of the three slots (1..3)")
    parser.add_argument("--tasks-file", type=Path, default=DEFAULT_TASKS_FILE)
    parser.add_argument("--gold-root", type=Path, default=Path("data/oam_tcd/grounded_v1_1_private"))
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--base-url", default=os.environ.get("RUNTIME_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--api-key", default=os.environ.get("RUNTIME_API_KEY", ""))
    parser.add_argument("--owner", default="evaluator")
    parser.add_argument("--timeout", type=int, default=None,
                        help="collection budget; defaults to the task's frozen wall-clock budget")
    parser.add_argument("--list", action="store_true", help="print the frozen tasks and exit")
    parser.add_argument("--no-grade", action="store_true",
                        help="collect the trial without grading it (useful for a first look)")
    parser.add_argument("--probe", action="store_true",
                        help="one throwaway run that only reports the agent's environment; "
                             "never graded and never counted as a slot")
    args = parser.parse_args()

    if args.list:
        list_tasks(args.tasks_file)
        return 0
    if not args.task:
        parser.error("--task is required unless --list is given")

    task = load_task(args.tasks_file, args.task)
    budget = int(task["budget"]["wall_seconds"])
    timeout = args.timeout or (120 if args.probe else budget)
    if not args.api_key:
        parser.error("--api-key or RUNTIME_API_KEY is required (see .env)")

    if args.probe:
        print(f"PROBE {task['task_id']} -- not a trial, not graded, not counted")
        result = probe_trial(
            task=task, base_url=args.base_url, api_key=args.api_key, owner=args.owner,
            runs_root=args.runs_root, timeout_seconds=timeout,
        )
        message = _answer_text(Path(result["package"]) / "raw" / "events.json")
        print(json.dumps({"collected": result.get("status"),
                          "package": result.get("package")}, ensure_ascii=False, indent=2))
        print("\n--- the agent reported ---\n" + (message or "(no message captured)"))
        return 0

    print(f"task      : {task['task_id']} ({task['family']})")
    print(f"inputs    : {', '.join(task['public_inputs'])}")
    print(f"repeat    : {args.repeat}")
    print(f"budget    : {budget}s (collection timeout {timeout}s)")
    print(f"prompt    : {task['question']}")
    print()

    result = run_trial(
        task=task, base_url=args.base_url, api_key=args.api_key, owner=args.owner,
        repeat=args.repeat, runs_root=args.runs_root, timeout_seconds=timeout,
        configuration={
            "harness": "forestry-runtime", "base_url": args.base_url,
            "model": "qwen3.5:4b", "kernel": "pydantic-ai",
            "task_id": task["task_id"], "repeat": args.repeat,
        },
    )
    print(json.dumps({
        "collected": result.get("status"),
        "package": result.get("package"),
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    if result.get("trace") is None:
        print("collection produced no runtime run; nothing to grade", file=sys.stderr)
        return 2
    if args.no_grade:
        return 0

    from .grade_trial import grade

    report = grade(task, Path(result["package"]), args.gold_root.resolve())
    report_path = Path(result["package"]) / "grade-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "task_id": report["task_id"], "status": report["status"],
        "validity": report["validity"], "outcome": report["outcome"],
        "checks": {name: check["verdict"] for name, check in report["checks"].items()},
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0 if report["validity"]["v"] == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
