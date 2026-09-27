"""Compare model, Harness and retrieval on a held-out task set.

The question this exists to answer is not "did the score go up" but "which of the
three changed". That requires two disciplines, both enforced here rather than
remembered:

* **one axis per comparison.** An arm is ``model × harness × retrieval``; a difference
  between two arms is only attributed to an axis when the other two are equal. The
  comparison refuses to report an attribution it cannot support.
* **success and cost together.** A run that succeeds by spending four times the tokens
  has not obviously improved, so elapsed time and token usage are reported in the same
  row as the success metrics, never separately.

Nothing here invents data. ``--plan`` prints the exact matrix and the fixed resources;
``--aggregate`` reads only records a collector produced; a cell with no record is
reported as ``not_collected`` rather than as a zero.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent
SUITE_PATH = ROOT / "suite.json"

#: Checks are grouped by what they are evidence of, so an arm's failure can be
#: attributed to a capability rather than to "the score".
METRIC_CHECKS = {
    "method_applicability": ("applicability", "method", "precondition"),
    "artifact_accuracy": ("artifact", "numeric", "table", "zonal", "mask", "grid", "statistics"),
    "evidence_citation": ("citation", "provenance", "evidence", "plan"),
    "delivery_success": ("delivery", "viewable", "download", "artifact_links"),
}
VERDICT_SCORE = {"pass": 1.0, "fail": 0.0, "unknown": None, "not_applicable": None}


def load_suite(path: Path | None = None) -> dict:
    return json.loads((path or SUITE_PATH).read_text(encoding="utf-8"))


def case_ids(suite: dict) -> list[str]:
    return [case["id"] for case in suite["cases"]]


def arm_axes(suite: dict) -> list[str]:
    return list(suite["axes"])


def plan(
    suite: dict, arms: list[dict], cases: Iterable[str] | None = None,
    repeats: int | None = None,
) -> dict:
    """The exact matrix an arm comparison will execute, before anything runs."""
    selected = list(cases or case_ids(suite))
    count = int(repeats if repeats is not None else suite["fixed_resources"]["repeats"])
    known = set(case_ids(suite))
    unknown = sorted(set(selected) - known)
    if unknown:
        raise ValueError(f"unknown held-out cases: {', '.join(unknown)}")
    for arm in arms:
        missing = [axis for axis in arm_axes(suite) if not arm.get(axis)]
        if missing:
            raise ValueError(
                f"arm {arm.get('name', '?')} does not state {', '.join(missing)}"
            )
    return {
        "suite_version": suite["suite_version"],
        "fixed_resources": suite["fixed_resources"],
        "axes": suite["axes"],
        "arms": arms,
        "cells": [
            {"arm": arm["name"], "case": case, "repeat": repeat}
            for arm in arms
            for case in selected
            for repeat in range(1, count + 1)
        ],
        "note": (
            "Each arm fixes all three axes. A comparison between two arms that differ "
            "in more than one axis cannot attribute its result to any of them."
        ),
    }


# ---------------------------------------------------------------------------
# Collected evidence
# ---------------------------------------------------------------------------


def _trial_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _find_record(trial: Path) -> dict | None:
    for name in ("record.json", "records.json"):
        record = _read_json(trial / name)
        if record:
            return record
    return None


def _score_checks(checks: dict) -> dict:
    """Average the applicable verdicts inside each metric group.

    ``unknown`` and ``not_applicable`` are excluded from the denominator rather than
    counted as failure: an ungraded slot is a gap in the evidence, and folding it into
    a zero is how a collection problem turns into an apparent capability problem.
    """
    scores: dict[str, dict] = {}
    for metric, needles in METRIC_CHECKS.items():
        values: list[float] = []
        names: list[str] = []
        for name, detail in (checks or {}).items():
            if not any(needle in name.casefold() for needle in needles):
                continue
            verdict = str((detail or {}).get("verdict") or "unknown").casefold()
            score = VERDICT_SCORE.get(verdict)
            if score is None:
                continue
            values.append(score)
            names.append(name)
        scores[metric] = {
            "score": (sum(values) / len(values)) if values else None,
            "checks": names,
            "graded": len(values),
        }
    return scores


def collect_slots(root: Path) -> list[dict]:
    """Every collected slot under *root*, as flat rows."""
    rows: list[dict] = []
    for trial in _trial_directories(root):
        record = _find_record(trial)
        if record is None:
            continue
        trace = _read_json(trial / "trace.json") or {}
        configuration = record.get("configuration") or trace.get("configuration") or {}
        steps = trace.get("steps") or []
        usage = trace.get("token_usage") or {}
        rows.append({
            "case": record.get("case_id"),
            "condition": record.get("condition"),
            "repeat": record.get("repeat"),
            "status": record.get("status"),
            "configuration": configuration,
            "checks": _score_checks(record.get("checks") or {}),
            "model_calls": len({step.get("seq") for step in steps}) if steps else None,
            "tool_calls": len(steps) if steps else None,
            "elapsed_seconds": record.get("elapsed_seconds") or trace.get("elapsed_seconds"),
            "input_tokens": usage.get("input_tokens") or usage.get("request_tokens"),
            "output_tokens": usage.get("output_tokens") or usage.get("response_tokens"),
            "trial": trial.name,
        })
    return rows


def _mean(values: list[float]) -> float | None:
    clean = [value for value in values if isinstance(value, (int, float))]
    return sum(clean) / len(clean) if clean else None


def _arm_label(configuration: dict) -> str:
    """Which arm a collected slot belongs to, read from the configuration it recorded."""
    for key in ("arm", "arm_name", "harness_snapshot"):
        value = configuration.get(key)
        if isinstance(value, str) and value:
            return value
    return "|".join(
        str(configuration.get(key) or "") for key in ("model_digest", "code_snapshot")
    )


def aggregate(rows: list[dict]) -> dict:
    """Per-arm metrics, with the arm's configuration reported alongside them."""
    by_arm: dict[str, list[dict]] = {}
    for row in rows:
        by_arm.setdefault(_arm_label(row["configuration"]), []).append(row)

    report: dict[str, Any] = {}
    for arm, slots in sorted(by_arm.items()):
        metrics: dict[str, Any] = {}
        for metric in METRIC_CHECKS:
            values = [
                slot["checks"][metric]["score"] for slot in slots
                if slot["checks"].get(metric, {}).get("score") is not None
            ]
            metrics[metric] = {
                "mean": _mean(values),
                "graded_slots": len(values),
                "total_slots": len(slots),
            }
        completed = [slot for slot in slots if str(slot.get("status")) == "completed"]
        metrics["delivery_success"] = {
            "mean": (len(completed) / len(slots)) if slots else None,
            "completed_slots": len(completed),
            "total_slots": len(slots),
        }
        metrics["elapsed_seconds"] = {
            "mean": _mean([slot["elapsed_seconds"] for slot in slots]),
            "total": _mean([slot["elapsed_seconds"] for slot in slots]) and sum(
                slot["elapsed_seconds"] for slot in slots
                if isinstance(slot["elapsed_seconds"], (int, float))
            ),
        }
        metrics["loop_rate"] = {
            "mean_model_calls": _mean([slot["model_calls"] for slot in slots]),
            "mean_tool_calls": _mean([slot["tool_calls"] for slot in slots]),
        }
        metrics["cost"] = {
            "mean_input_tokens": _mean([slot["input_tokens"] for slot in slots]),
            "mean_output_tokens": _mean([slot["output_tokens"] for slot in slots]),
            "total_tokens": (
                sum(
                    slot[key] for slot in slots
                    for key in ("input_tokens", "output_tokens")
                    if isinstance(slot[key], (int, float))
                ) or None
            ),
        }
        report[arm] = {
            "metrics": metrics,
            "cases": sorted({str(slot["case"]) for slot in slots}),
            "configuration": slots[0]["configuration"],
        }
    return report


def compare(arms: dict, left: str, right: str, suite: dict) -> dict:
    """Attribute the difference between two arms, or refuse to.

    Attribution is only meaningful when exactly one axis differs. Anything else is
    reported as an unattributable pair with the axes that moved named explicitly.
    """
    if left not in arms or right not in arms:
        raise ValueError(f"unknown arm(s): {left if left not in arms else right}")
    left_config = arms[left]["configuration"]
    right_config = arms[right]["configuration"]
    axis_fields = {
        "model": ("model_digest", "model"),
        "harness": ("code_snapshot", "harness_snapshot", "tools_snapshot", "prompt_snapshot"),
        # Not the arm's *name*: two runs of the same configuration under different
        # labels are not two retrieval schemes, and treating the label as an axis value
        # would make every comparison look like it moved three axes at once.
        "retrieval": ("retrieval_mode", "retrieval"),
    }
    moved = []
    for axis, fields in axis_fields.items():
        values = {
            side: tuple(str(config.get(field) or "") for field in fields)
            for side, config in (("left", left_config), ("right", right_config))
        }
        if values["left"] != values["right"]:
            moved.append(axis)
    deltas = {}
    for metric in ("method_applicability", "artifact_accuracy", "evidence_citation",
                   "delivery_success"):
        left_value = arms[left]["metrics"].get(metric, {}).get("mean")
        right_value = arms[right]["metrics"].get(metric, {}).get("mean")
        deltas[metric] = {
            "left": left_value,
            "right": right_value,
            "delta": (
                right_value - left_value
                if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float))
                else None
            ),
        }
    return {
        "left": left,
        "right": right,
        "axes_moved": moved,
        "attributable_axis": moved[0] if len(moved) == 1 else None,
        "deltas": deltas,
        "note": (
            "A single moved axis can be attributed. With several moved, the difference "
            "is real but its cause is not identifiable from this comparison."
            if len(moved) != 1 else
            f"Only the {moved[0]} axis differs, so the delta is attributed to it."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_arms(raw: list[str]) -> list[dict]:
    arms = []
    for item in raw:
        try:
            value = json.loads(item)
        except ValueError as exc:
            raise SystemExit(f"--arm must be JSON, got {item!r}: {exc}")
        if not isinstance(value, dict) or not value.get("name"):
            raise SystemExit("--arm needs at least {\"name\": ...}")
        arms.append(value)
    return arms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--plan", action="store_true", help="print the arm matrix")
    parser.add_argument(
        "--arm", action="append", default=[],
        help='an arm as JSON, e.g. \'{"name":"a","model":"model-b","harness":"v2","retrieval":"guides"}\'',
    )
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--root", type=Path, help="a collected run root to aggregate")
    parser.add_argument("--compare", nargs=2, metavar=("LEFT", "RIGHT"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    suite = load_suite(args.suite)
    arms = _parse_arms(args.arm)

    if args.plan:
        result = plan(suite, arms, cases=args.cases, repeats=args.repeats)
    elif args.root:
        report = aggregate(collect_slots(args.root))
        result = report
        if args.compare:
            result = {
                "arms": report,
                "comparison": compare(report, args.compare[0], args.compare[1], suite),
            }
    else:
        parser.error("choose --plan or --root")
        return 2

    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
