"""Grade one collected trial, or prove the grader is reproducible on stored evidence.

This is the operator entry point. It reads the frozen task file, loads one trial
package, and writes a ``GradeReport``. It never starts the tested Runtime and
never executes agent-written code.

    python -m evaluation.grounded_v1.grade_trial --task oam-01 --trial <package>
    python -m evaluation.grounded_v1.grade_trial --task oam-01 --trial <package> --repeat-check 2

``--repeat-check N`` grades the same evidence N times and fails if any two reports
differ, which is the plan's "the grader must produce the same result on the same
evidence" acceptance condition.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .grading import (
    TaskSpec, TrialBundle, grade_canopy_extraction, grade_canopy_statistics,
    grade_gis_analysis,
)


def load_task(tasks_file: Path, task_id: str) -> dict:
    payload = json.loads(Path(tasks_file).read_text(encoding="utf-8"))
    for task in payload["tasks"]:
        if task["task_id"] == task_id:
            return task
    raise KeyError(f"{task_id} is not in {tasks_file}")


def load_gold(tasks_file: Path, task_id: str) -> dict:
    payload = json.loads(Path(tasks_file).read_text(encoding="utf-8"))
    return dict((payload.get("gold") or {}).get(task_id) or {})


def spec_from(task: dict) -> TaskSpec:
    return TaskSpec(
        task_id=task["task_id"], family=task["family"], question=task["question"],
        public_root=task["public_root"], public_inputs=list(task["public_inputs"]),
        required_deliverables=list(task["required_deliverables"]),
        grading_rule=list(task["grading_rule"]), condition=task["condition"],
        budget=dict(task["budget"]), resources=dict(task["resources"]),
        tolerance=dict(task.get("tolerance") or {}),
        # Without this the frozen rubric is silently dropped and the method and
        # parameter checks stay unmeasured on every real trial.
        process_rubric=dict(task.get("process_rubric") or {}),
    )


def grade(task: dict, trial_path: Path, gold_root: Path, gold: dict | None = None) -> dict:
    spec = spec_from(task)
    bundle = TrialBundle.load(trial_path, case_id=task["task_id"])
    budget = task["budget"].get("wall_seconds")
    if task["family"] == "canopy_extraction":
        report = grade_canopy_extraction(
            spec, bundle, gold_mask=gold_root / f"{task['task_id']}-gold.tif",
            budget_seconds=budget,
        )
    elif task["family"] == "canopy_statistics":
        report = grade_canopy_statistics(
            spec, bundle, gold_mask=gold_root / f"{task['task_id']}-gold.tif",
            budget_seconds=budget,
        )
    elif task["family"] == "gis_analysis":
        report = grade_gis_analysis(
            spec, bundle, gold=gold or {}, budget_seconds=budget,
        )
    else:
        raise NotImplementedError(f"no grader is frozen for the family {task['family']!r}")
    return report.as_dict()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--tasks-file", type=Path,
                        default=Path("evaluation/grounded_v1/tasks.grounded-v1.2.json"))
    parser.add_argument("--gold-root", type=Path,
                        default=Path("data/oam_tcd/grounded_v1_1_private"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--repeat-check", type=int, default=0,
                        help="grade the same evidence this many times and fail on any difference")
    args = parser.parse_args()

    task = load_task(args.tasks_file, args.task)
    gold = load_gold(args.tasks_file, args.task)

    def grade_once() -> dict:
        return grade(task, args.trial.resolve(), args.gold_root.resolve(), gold)

    report = grade_once()
    if args.repeat_check > 1:
        baseline = json.dumps(report, ensure_ascii=False, sort_keys=True)
        for _ in range(args.repeat_check - 1):
            again = json.dumps(grade_once(), ensure_ascii=False, sort_keys=True)
            if again != baseline:
                print(json.dumps({"repeatable": False, "task": args.task},
                                 ensure_ascii=False))
                return 3
        report["_repeat_check"] = {"runs": args.repeat_check, "identical": True}
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "task_id": report["task_id"], "status": report["status"],
        "validity": report["validity"], "outcome": report["outcome"],
        "checks": {name: check["verdict"] for name, check in report["checks"].items()},
        "repeat_check": report.get("_repeat_check"),
    }, ensure_ascii=False, indent=2))
    return 0 if report["validity"]["v"] == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
