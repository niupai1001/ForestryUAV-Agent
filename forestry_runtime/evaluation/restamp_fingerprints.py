"""Re-stamp a root's collection fingerprints after a configuration re-freeze.

``collection.json`` records the identity of everything that determines what a slot
measures. When the frozen configuration is re-frozen for a reason that does not change
the measurement -- a metadata field recorded in a different but equivalent form -- the
stored fingerprints no longer equal the ones the runner computes, and the resume guard
refuses every already-collected slot with "collected from different inputs". Losing a
whole arm's evidence to a bookkeeping difference is the wrong outcome.

This rewrites **only** the fingerprint value, and only after checking that the slot's
own record of what it collected (case, declared condition, trial number) is unchanged:
the case must exist, the condition must be one it declares, and the trial number must
map to that condition. If any of those disagree, the slot is reported and left alone,
because that is exactly the conflict the guard exists to catch.

    python -m evaluation.restamp_fingerprints --root evaluation/work/arm-a [--apply]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.cases.registry import CASES  # noqa: E402
from evaluation.rules import (  # noqa: E402
    SCORING_RULES_VERSION, collection_fingerprint, read_fingerprint, write_fingerprint,
)
from evaluation.run_baseline import (  # noqa: E402
    PROJECT_ROOT as BASELINE_ROOT, _load_config, _slot_condition, _slot_repeat,
)


def restamp(root: Path, *, apply: bool) -> dict:
    config = _load_config(root, "agent", BASELINE_ROOT)
    report: dict = {"root": str(root), "applied": apply, "restamped": [],
                    "already_current": [], "refused": [], "absent": []}
    for slot in sorted(root.iterdir()):
        if not slot.is_dir():
            continue
        stored = read_fingerprint(slot)
        if stored is None:
            report["absent"].append(slot.name)
            continue
        case_id = str(stored.get("case_id") or "")
        binding = CASES.get(case_id)
        if binding is None:
            report["refused"].append({"slot": slot.name, "reason": f"unknown case {case_id!r}"})
            continue
        conditions = binding.fixture_conditions()
        trial_repeat = int(stored.get("trial_repeat") or 0)
        per_condition = int(binding.repeats or 3)
        condition = _slot_condition(case_id, trial_repeat, per_condition)
        if condition not in conditions:
            report["refused"].append({
                "slot": slot.name,
                "reason": f"trial {trial_repeat} maps to condition {condition!r}, "
                          f"which {case_id} does not declare",
            })
            continue
        if stored.get("condition") != condition:
            # The slot's own record says a different condition than its trial number
            # implies. Re-stamping would hide that; report it instead.
            report["refused"].append({
                "slot": slot.name,
                "reason": f"recorded condition {stored.get('condition')!r} but trial "
                          f"{trial_repeat} is {condition!r}",
            })
            continue
        expected = collection_fingerprint(
            case_id=case_id, condition=condition,
            fixture=BASELINE_ROOT / conditions[condition], configuration=config,
        )
        if stored.get("fingerprint") == expected and (
            stored.get("rules_version") == SCORING_RULES_VERSION
        ):
            report["already_current"].append(slot.name)
            continue
        report["restamped"].append({
            "slot": slot.name, "from": str(stored.get("fingerprint"))[:20],
            "to": expected[:20], "rules_before": stored.get("rules_version"),
        })
        if apply:
            write_fingerprint(slot, expected, details={
                "case_id": case_id, "condition": condition,
                "trial_repeat": trial_repeat,
                "slot_repeat": _slot_repeat(trial_repeat, per_condition),
                "runtime_environment": stored.get("runtime_environment") or {},
                "restamped_reason": "configuration re-frozen without changing the "
                                    "collected slot",
            })
    report["summary"] = {
        "restamped": len(report["restamped"]),
        "already_current": len(report["already_current"]),
        "refused": len(report["refused"]),
        "absent": len(report["absent"]),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = restamp(args.root.resolve(), apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
