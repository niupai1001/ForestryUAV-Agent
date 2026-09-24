"""Re-score collected evidence under the current rules, without re-running anything.

A scoring rule can be wrong, and finding that out must not cost 72 more model runs.
This walks an evidence root, re-runs each slot's verifier over the evidence already
on disk, and rewrites the record and its fingerprint under the current rules version.

It deliberately does **not** re-use `run_baseline --verify-only`: that path runs the
resume guard first, which compares the stored fingerprint against a freshly computed
one. The comparison is right for its job -- deciding whether a *collection* may be
reused -- but a rules change moves the expected fingerprint, and the guard would then
refuse to re-score the very evidence the change has to be checked against.

What it refuses to do:

* never collects (no Runtime call, no model call);
* never overwrites a record it did not just build from the same slot's evidence;
* reports slots whose stored ``trial_id`` collides with another slot instead of
  letting the scorecard reject the whole root as a duplicate.

    python -m evaluation.rescore --root evaluation/work/arm-b
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.cases.registry import AGENT_TRIALS, CASES, CONDITIONAL_CASES  # noqa: E402
from evaluation.rules import (  # noqa: E402
    SCORING_RULES_VERSION, collection_fingerprint, read_fingerprint, write_fingerprint,
)
from evaluation.run_baseline import (  # noqa: E402
    PROJECT_ROOT as BASELINE_ROOT, _load_config, _slot_condition, _slot_repeat,
)


def _trace_configuration(slot: Path) -> dict:
    """The configuration the slot's own trace recorded, or an empty dict."""
    try:
        trace = json.loads((slot / "trace.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    configuration = trace.get("configuration")
    return configuration if isinstance(configuration, dict) else {}


def rescore(root: Path, *, apply: bool) -> dict:
    configs = {"agent": _load_config(root, "agent", BASELINE_ROOT)}
    report: dict = {
        "root": str(root), "rules_version": SCORING_RULES_VERSION,
        "rescored": [], "skipped": [], "duplicate_trial_ids": [], "applied": apply,
        "changed": [],
    }
    trial_ids: dict[str, str] = {}

    for slot in sorted(root.iterdir()):
        if not slot.is_dir():
            continue
        record_file = slot / "record.json"
        collection = read_fingerprint(slot)
        if not record_file.is_file() or collection is None:
            report["skipped"].append({"slot": slot.name, "reason": "no graded record"})
            continue
        case_id = str(collection.get("case_id") or "")
        binding = CASES.get(case_id)
        if binding is None:
            report["skipped"].append({"slot": slot.name, "reason": f"unknown case {case_id!r}"})
            continue

        previous = json.loads(record_file.read_text(encoding="utf-8"))
        trial_id = str(previous.get("trial_id") or "")
        if trial_id:
            if trial_id in trial_ids:
                report["duplicate_trial_ids"].append(
                    {"slot": slot.name, "trial_id": trial_id, "first_seen": trial_ids[trial_id]}
                )
                continue
            trial_ids[trial_id] = slot.name

        trial_repeat = int(collection.get("trial_repeat") or 0)
        per_condition = int(binding.repeats or 3)
        condition = _slot_condition(case_id, trial_repeat, per_condition)
        slot_repeat = _slot_repeat(trial_repeat, per_condition)

        # `require_slot` refuses a trace whose recorded configuration is not the frozen
        # one, which is right for a *collection*: it would mean the slot came from a
        # different experiment. For re-scoring it is too strict in one field.
        # `code_snapshot` carries a hash of the working tree, so writing a new
        # evaluation module after collection moves it while the slot stays valid; the
        # arms then cannot be re-scored under one ruleset, and a report that compares
        # A under one ruleset with B under another is exactly what must not happen.
        # The trace's own value is therefore used for that field -- it is the recorded
        # fact -- and the substitution is reported rather than hidden. Every field that
        # determines the treatment (sampling, budgets, model, prompts, tools, dataset)
        # is still compared in full.
        trace_config = _trace_configuration(slot)
        slot_config = configs["agent"]
        if trace_config and trace_config.get("code_snapshot") != slot_config.get("code_snapshot"):
            report.setdefault("code_snapshot_substituted", []).append({
                "slot": slot.name,
                "trace": str(trace_config.get("code_snapshot"))[:60],
                "frozen": str(slot_config.get("code_snapshot"))[:60],
            })
            slot_config = {**slot_config, "code_snapshot": trace_config.get("code_snapshot")}

        module = importlib.import_module(
            AGENT_TRIALS.get(case_id, "evaluation.verify.core_csv_trial")
        )
        extra = {"case_id": case_id}
        if case_id in CONDITIONAL_CASES:
            extra["condition"] = condition

        try:
            record = module.verify_trial(
                slot, slot_config, slot_repeat,
                gold=PROJECT_ROOT / binding.gold, **extra,
            )
        except Exception as exc:
            # Re-scoring must never leave an arm half-judged. A verifier that cannot
            # build a record says so for that slot and the run continues, so the caller
            # sees exactly which slot is questionable instead of a traceback from the
            # middle of the alphabet.
            report["skipped"].append({
                "slot": slot.name,
                "reason": f"{type(exc).__name__}: {exc}"[:200],
            })
            continue
        verdicts_before = {
            name: check["verdict"] for name, check in previous.get("checks", {}).items()
        }
        verdicts_after = {
            name: check["verdict"] for name, check in record.get("checks", {}).items()
        }
        movement = {
            name: [verdicts_before.get(name), verdict]
            for name, verdict in verdicts_after.items()
            if verdicts_before.get(name) != verdict
        }
        report["rescored"].append({
            "slot": slot.name, "status": record.get("status"),
            "changed": movement,
        })
        if movement:
            report["changed"].append({"slot": slot.name, "changes": movement})
        if not apply:
            continue
        record_file.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # The fingerprint identifies *what was collected*. Re-scoring collects nothing,
        # but the stored value must still be the one the runner will recompute next
        # time -- otherwise the resume guard reads every re-scored slot as "collected
        # from different inputs" and refuses to continue. That is the same identity
        # check `run_baseline` performs, which is why this is the same call.
        fixture = PROJECT_ROOT / binding.fixture_conditions()[condition]
        fingerprint = collection_fingerprint(
            case_id=case_id, condition=condition, fixture=fixture,
            configuration=configs["agent"],
        )
        write_fingerprint(
            slot, fingerprint,
            details={
                "case_id": case_id, "condition": condition,
                "trial_repeat": trial_repeat, "slot_repeat": slot_repeat,
                "runtime_environment": collection.get("runtime_environment") or {},
                "rescored_under": SCORING_RULES_VERSION,
            },
        )
    report["summary"] = {
        "rescored": len(report["rescored"]),
        "changed": len(report["changed"]),
        "skipped": len(report["skipped"]),
        "duplicate_trial_ids": len(report["duplicate_trial_ids"]),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="write the new records; without it this is a dry run")
    args = parser.parse_args()
    if not (args.root / "configuration-agent.json").is_file():
        print(json.dumps({
            "error": f"{args.root} has no configuration-agent.json",
        }, ensure_ascii=False, indent=2))
        return 2
    report = rescore(args.root.resolve(), apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["duplicate_trial_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
