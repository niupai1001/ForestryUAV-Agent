"""Record the arm a slot was collected under, from the container that ran it.

Every slot already stores the treatment switches in ``collection.json``, and
``compare_arms`` refuses to compare roots whose slots disagree with their declared
arm -- the mechanism that caught a real contamination once.  The recording was done
from the *collecting shell*, which is the wrong source: the Runtime reads those
switches from its own process environment, and the shell normally leaves one of them
unset.  The result was a slot marked ``DOMAIN_GUIDES_ENABLED: ""`` while the
container was demonstrably running arm B.

This repairs the record for slots collected before that was fixed, using the value
the container actually has.  It rewrites one field of ``collection.json`` and touches
nothing else: no prompt, no trace, no verdict.  It refuses to guess -- if the
container has no such value, the field is left alone.

    python -m evaluation.record_arm_provenance evaluation/work/arm-b
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.run_baseline import runtime_environment  # noqa: E402

SWITCHES = ("REMOTE_SENSING_PLUGINS_ENABLED", "DOMAIN_GUIDES_ENABLED")


def repair(root: Path, *, container: str, apply: bool) -> dict:
    actual = runtime_environment(container)
    unreadable = [name for name in SWITCHES if not actual.get(name)]
    result: dict = {
        "root": str(root),
        "container": container,
        "container_environment": actual,
        "unreadable_switches": unreadable,
        "repaired": [],
        "already_correct": 0,
        "skipped": [],
        "applied": apply,
    }
    if unreadable:
        result["refused"] = (
            "the container does not report " + ", ".join(unreadable)
            + "; recording a guessed value would make the contamination check "
              "meaningless"
        )
        return result
    for slot in sorted(root.iterdir()):
        record_file = slot / "collection.json"
        if not slot.is_dir() or not record_file.is_file():
            continue
        try:
            payload = json.loads(record_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result["skipped"].append({"slot": slot.name, "reason": str(exc)[:120]})
            continue
        recorded = payload.get("runtime_environment")
        if not isinstance(recorded, dict):
            result["skipped"].append({"slot": slot.name, "reason": "no runtime_environment"})
            continue
        if recorded == actual:
            result["already_correct"] += 1
            continue
        # Only ever fills a field the container confirms.  A non-empty recorded value
        # that disagrees is left as it is: that is precisely the contradiction the
        # contamination check exists to report, and it must stay visible.
        disagree = {
            name: value for name, value in recorded.items()
            if value and actual.get(name) and value != actual[name]
        }
        if disagree:
            result["skipped"].append({
                "slot": slot.name,
                "reason": f"recorded {disagree} contradicts the container",
            })
            continue
        payload["runtime_environment"] = actual
        if apply:
            record_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        result["repaired"].append(slot.name)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--container", default="forestry-runtime")
    parser.add_argument("--apply", action="store_true",
                        help="write the repair; without it this is a dry run")
    args = parser.parse_args()
    report = repair(args.root.resolve(), container=args.container, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report.get("refused") else 0


if __name__ == "__main__":
    raise SystemExit(main())
