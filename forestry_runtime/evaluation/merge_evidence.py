"""Merge a parallel collection shard back into its evidence root.

Collection is wall-clock bound: one trial at a time on a local model means a
72-run experiment can outlast the work it is measuring. Two collectors may run
against disjoint case sets, each in its own directory, because `run_baseline`
refuses to have two processes writing one root.

A shard is only mergeable when it is demonstrably the *same* experiment:

* byte-identical frozen configuration, so the two halves cannot have measured
  different sampling, budgets or model;
* the same scoring-rules version on every slot;
* no case collected in both halves -- a slot recorded twice would mean two
  different runs claim one ``(case, condition, repeat)`` slot, and the scorecard
  would silently keep whichever it read last.

Anything else is refused with the reason, so a botched merge cannot quietly
produce an experiment that looks complete.

    python -m evaluation.merge_evidence evaluation/work/arm-b --shard evaluation/work/arm-b-p2
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.rules import (  # noqa: E402
    SCORING_RULES_VERSION, collection_fingerprint, read_fingerprint,
)
from evaluation.cases.registry import CASES  # noqa: E402
from evaluation.run_baseline import _load_config, _slot_condition, _slot_repeat  # noqa: E402

CONFIG = "configuration-agent.json"
BOOKKEEPING = {"records.json", "scorecard.json"}


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _slots(root: Path) -> dict[str, Path]:
    return {
        entry.name: entry for entry in sorted(root.iterdir())
        if entry.is_dir() and (entry / "collection.json").is_file()
    }


def _rules_versions(slots: dict[str, Path]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name, path in slots.items():
        payload = json.loads((path / "collection.json").read_text(encoding="utf-8"))
        versions[name] = str(payload.get("rules_version") or "")
    return versions


def plan(root: Path, shard: Path) -> dict:
    report: dict = {"root": str(root), "shard": str(shard), "refused": [], "movable": []}
    if not root.is_dir() or not shard.is_dir():
        report["refused"].append("both the root and the shard must exist")
        return report
    root_config, shard_config = _digest(root / CONFIG), _digest(shard / CONFIG)
    report["configuration"] = {
        "root": root_config, "shard": shard_config,
        "identical": root_config is not None and root_config == shard_config,
    }
    if not report["configuration"]["identical"]:
        report["refused"].append(
            "the frozen configurations differ, so the two halves are not the same "
            "experiment"
        )

    root_slots, shard_slots = _slots(root), _slots(shard)
    overlap = sorted(set(root_slots) & set(shard_slots))
    report["overlap"] = overlap
    if overlap:
        report["refused"].append(
            "these slots exist in both halves: " + ", ".join(overlap[:8])
            + f"{' …' if len(overlap) > 8 else ''}"
        )

    stale = {
        name: version
        for name, version in _rules_versions(shard_slots).items()
        if version != SCORING_RULES_VERSION
    }
    report["stale_rules"] = stale
    if stale:
        report["refused"].append(
            f"{len(stale)} shard slot(s) were scored under rules other than "
            f"{SCORING_RULES_VERSION}"
        )

    report["movable"] = sorted(set(shard_slots) - set(overlap))

    # A movable slot must be the slot the *main* root would have collected. Comparing
    # only the two `configuration-agent.json` files misses the case that actually bit:
    # each root freezes its own copy, and a re-freeze after the shard was collected
    # makes them differ while every slot stays valid. The consequences are not
    # cosmetic -- a slot whose trace was recorded under a different configuration than
    # the main root's fails `require_slot` at re-scoring time, with the misleading
    # message "repeat does not match the requested slot".
    mismatched: dict[str, str] = {}
    if (root / CONFIG).is_file():
        main_config = _load_config(root, "agent", PROJECT_ROOT)
        for name in report["movable"]:
            fingerprint = read_fingerprint(shard_slots[name])
            case_id = str((fingerprint or {}).get("case_id") or "")
            binding = CASES.get(case_id)
            if binding is None:
                mismatched[name] = f"unknown case {case_id!r}"
                continue
            trial_repeat = int((fingerprint or {}).get("trial_repeat") or 0)
            per_condition = int(binding.repeats or 3)
            condition = _slot_condition(case_id, trial_repeat, per_condition)
            fixture = (PROJECT_ROOT / binding.fixture_conditions()[condition]).resolve()
            expected = collection_fingerprint(
                case_id=case_id, condition=condition,
                fixture=fixture, configuration=main_config,
            )
            if (fingerprint or {}).get("fingerprint") != expected:
                mismatched[name] = (
                    f"collected under a different configuration or fixture "
                    f"(condition {condition})"
                )
    report["slot_mismatches"] = mismatched
    if mismatched:
        report["refused"].append(
            f"{len(mismatched)} shard slot(s) do not match the main root's "
            "configuration; re-collect them with the arm's current configuration"
        )

    report["bookkeeping_in_shard"] = sorted(
        entry.name for entry in shard.iterdir() if entry.name in BOOKKEEPING
    )
    return report


def merge(root: Path, shard: Path, *, apply: bool) -> dict:
    report = plan(root, shard)
    report["applied"] = False
    if report["refused"]:
        return report
    if not apply:
        return report
    for name in report["movable"]:
        shutil.move(str(shard / name), str(root / name))
    for name in report["bookkeeping_in_shard"]:
        # The shard's own summary describes a subset of the experiment; keeping it
        # would let a later scorecard read a partial total as if it were the whole.
        (shard / name).unlink()
    report["applied"] = True
    report["root_slots_after"] = len(_slots(root))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="perform the merge; without it this is a dry run")
    args = parser.parse_args()
    report = merge(args.root.resolve(), args.shard.resolve(), apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
