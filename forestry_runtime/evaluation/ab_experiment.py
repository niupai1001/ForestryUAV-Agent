"""Prepare and check the A/B controlled experiment.

Two arms, one variable. Everything except the domain layer is identical:

==================  ==========================  ==========================
                    A (general capability)      B (domain capability)
==================  ==========================  ==========================
domain tools        plugin disabled             plugin enabled
domain guides       disabled                    ``knowledge/guides``
model, sampling     identical                   identical
budgets             identical                   identical
data, prompts       identical                   identical
evidence root       ``work/arm-a``              ``work/arm-b``
==================  ==========================  ==========================

They run in separate evidence roots, so no scorecard can silently mix them, and
each root freezes its own configuration before collection starts.

    python -m evaluation.ab_experiment --root evaluation/work --prepare
    python -m evaluation.ab_experiment --root evaluation/work --check
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The twelve capability scenarios: six families, each measured on a normal run and
# on a condition where a required input is missing or unusable.
CAPABILITY_CASES = (
    "capability.inventory",
    "capability.raster_stats",
    "capability.ndvi",
    "capability.chm",
    "capability.supervised",
    "capability.recompute",
)
SCENARIOS = 12
REPEATS = 3
RUNS = SCENARIOS * REPEATS * 2

ARMS = {
    "a": {
        "label": "A 组：通用文件/代码执行能力",
        "env": {
            "REMOTE_SENSING_PLUGINS_ENABLED": "false",
            "DOMAIN_GUIDES_ENABLED": "false",
        },
    },
    "b": {
        "label": "B 组：相同通用能力 + 林业遥感工具与按需指南",
        "env": {
            "REMOTE_SENSING_PLUGINS_ENABLED": "true",
            "DOMAIN_GUIDES_ENABLED": "true",
        },
    },
}

COMPOSE_FILES = ("compose.yaml", "compose.eval.yaml")
CONTAINER = "forestry-runtime"


def _arm_root(root: Path, arm: str) -> Path:
    return root / f"arm-{arm}"


def up_command(arm: str) -> list[str]:
    """Recreate the Runtime container with this arm's environment.

    The arm's environment must reach the *container*, not just the collector
    process: `REMOTE_SENSING_PLUGINS_ENABLED` and `DOMAIN_GUIDES_ENABLED` are read
    by the Runtime. Setting them in the collecting shell would leave the container
    unchanged, both arms would run with identical settings, and the comparison
    would silently measure nothing.
    """
    spec = ARMS[arm]
    command = ["docker", "compose"]
    for name in COMPOSE_FILES:
        command += ["-f", name]
    command += ["up", "-d", "runtime"]
    return command


def up(arm: str, *, root: Path) -> dict:
    environment = dict(os.environ) | ARMS[arm]["env"]
    completed = subprocess.run(
        up_command(arm), cwd=PROJECT_ROOT, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return {
        "arm": arm,
        "command": " ".join(up_command(arm)),
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "")[-2000:],
        "stderr": (completed.stderr or "")[-2000:],
        "environment": ARMS[arm]["env"],
    }


def _plan_path(root: Path, arm: str) -> Path:
    """The arm's bookkeeping file.

    It deliberately does **not** live inside the arm root: a stray file there is
    treated by ``run_baseline`` as an incomplete trial directory, which aborts
    collection. The plan sits one level up, in the experiment root.
    """
    return root / f"arm-{arm}-plan.json"


def prepare(root: Path) -> dict:
    """Create both evidence roots and record each arm's frozen environment."""
    result: dict = {"runs": RUNS, "scenarios": SCENARIOS, "repeats": REPEATS, "arms": {}}
    root.mkdir(parents=True, exist_ok=True)
    for arm, spec in ARMS.items():
        directory = _arm_root(root, arm)
        directory.mkdir(parents=True, exist_ok=True)
        _plan_path(root, arm).write_text(
            json.dumps({
                "arm": arm, "label": spec["label"], "environment": spec["env"],
                "cases": list(CAPABILITY_CASES), "repeats": REPEATS,
                "scenarios": SCENARIOS,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["arms"][arm] = {
            "root": str(directory),
            "plan": str(_plan_path(root, arm)),
            "env": spec["env"],
            "up": " ".join(up_command(arm)),
            "freeze": (
                f"python -m evaluation.freeze_agent_config --root {directory}"
            ),
            "collect": (
                "python -m evaluation.run_baseline "
                f"--root {directory} --tracks agent "
                f"--cases {' '.join(CAPABILITY_CASES)} --repeats 1 2 3"
            ),
        }
        result["arms"][arm]["order"] = (
            f"1) {result['arms'][arm]['up']}   "
            f"2) {result['arms'][arm]['freeze']}   "
            f"3) {result['arms'][arm]['collect']}"
        )
    return result


def arm_in_effect(container: str = CONTAINER) -> dict:
    """Which arm the running container is actually configured for, if determinable."""
    completed = subprocess.run(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        return {"container": container, "running": False, "arm": None,
                "error": (completed.stderr or "").strip()[:300]}
    environment = {}
    for line in (completed.stdout or "").splitlines():
        key, _, value = line.partition("=")
        if key in {"REMOTE_SENSING_PLUGINS_ENABLED", "DOMAIN_GUIDES_ENABLED"}:
            environment[key] = value
    arm = None
    for name, spec in ARMS.items():
        if environment == spec["env"]:
            arm = name
            break
    return {"container": container, "running": True, "arm": arm,
            "environment": environment}


def check(root: Path) -> dict:
    """Report readiness without collecting anything."""
    report: dict = {"ready": True, "problems": [], "arms": {}}
    report["container"] = arm_in_effect()
    if report["container"].get("running") and report["container"].get("arm") is None:
        report["problems"].append(
            "the running Runtime container matches neither arm's environment; "
            "recreate it with `--up a` or `--up b` before collecting, or both arms "
            "will run with identical settings"
        )
    for arm, spec in ARMS.items():
        directory = _arm_root(root, arm)
        arm_report: dict = {"root": str(directory), "exists": directory.is_dir()}
        config = directory / "configuration-agent.json"
        arm_report["configuration_frozen"] = config.is_file()
        if not arm_report["configuration_frozen"]:
            report["problems"].append(
                f"arm {arm}: configuration-agent.json is missing; freeze it before "
                "collecting so the two arms are demonstrably identical apart from the "
                "domain layer"
            )
        else:
            frozen = json.loads(config.read_text(encoding="utf-8"))
            arm_report["sampling"] = frozen.get("sampling")
            arm_report["budgets"] = frozen.get("budgets")
            # These three have to be read here as well: the comparison below only
            # sees what this function puts in the report, so leaving them out made
            # the model, prompt and tool checks compare `None` with `None` and pass
            # no matter what the arms actually froze.
            for field in ("model_digest", "prompt_snapshot", "tools_snapshot",
                          "environment_snapshot", "code_snapshot"):
                arm_report[field] = frozen.get(field)
        records = sorted(directory.glob("*/record.json")) if directory.is_dir() else []
        arm_report["records"] = len(records)
        arm_report["slots_expected"] = SCENARIOS * REPEATS
        report["arms"][arm] = arm_report

    arms = report["arms"]
    if arms["a"].get("sampling") and arms["b"].get("sampling"):
        # `code_snapshot` and `environment_snapshot` are deliberately excluded: both
        # carry a working-tree or rebuild marker that differs whenever the evidence
        # is re-collected, and their job is to be *recorded*, not to be equal. The
        # fields below are the ones that decide whether the two arms measured the
        # same thing apart from the domain layer.
        for field in ("sampling", "budgets", "model_digest", "prompt_snapshot",
                      "tools_snapshot"):
            if arms["a"].get(field) != arms["b"].get(field):
                report["problems"].append(
                    f"the two arms differ in {field}; the comparison would not isolate "
                    "the domain layer"
                )
    report["ready"] = not report["problems"]
    report["environment"] = {
        arm: spec["env"] for arm, spec in ARMS.items()
    }
    report["runs_planned"] = RUNS
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("evaluation/work"))
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--up", choices=sorted(ARMS),
        help="Recreate the Runtime container with this arm's environment.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.up:
        print(json.dumps(up(args.up, root=root), ensure_ascii=False, indent=2))
    if args.prepare:
        print(json.dumps(prepare(root), ensure_ascii=False, indent=2))
    if args.check or not (args.prepare or args.up):
        print(json.dumps(check(root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
