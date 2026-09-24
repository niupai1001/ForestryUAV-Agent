"""Turn a finished collection into the A/B report, in the one order that is correct.

The steps have dependencies, and doing them out of order either invalidates evidence
or produces a report that looks final while describing a subset:

1. **discard** slots whose failure was infrastructure (Runtime unreachable, collector
   timeout). They carry no Agent outcome; re-collecting them costs minutes and removes
   `infra_error` from the engineering-reliability denominator it never belonged in.
2. **merge** the parallel root into its arm's main root. Only after this does an arm
   have all 36 slots in one place.
3. **rescore** under the current rules, so both arms are judged by one ruleset. This
   re-runs verifiers over existing evidence: no Runtime call, no model call.
4. **audit** that each arm holds exactly the 36 planned slots and that no slot is
   claimed twice, before any number is reported.
5. **report**.

    python -m evaluation.finalize_ab --a evaluation/work/arm-a --b evaluation/work/arm-b
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.ab_experiment import CAPABILITY_CASES, REPEATS  # noqa: E402
from evaluation.discard_slots import discard  # noqa: E402
from evaluation.merge_evidence import merge  # noqa: E402
from evaluation.report_ab import render  # noqa: E402, F401
from evaluation.rescore import rescore  # noqa: E402
from evaluation.compare_arms import compare  # noqa: E402

EXPECTED = {(case, repeat) for case in CAPABILITY_CASES
            for repeat in range(1, REPEATS * 2 + 1)}


def _records(root: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not root.is_dir():
        return found
    for slot in sorted(root.iterdir()):
        record = slot / "record.json"
        if slot.is_dir() and record.is_file():
            found[slot.name] = json.loads(record.read_text(encoding="utf-8"))
    return found


def audit(root: Path) -> dict:
    records = _records(root)
    names = set(records)
    missing = sorted(
        f"{case.replace('.', '-')}-{repeat}" for case, repeat in EXPECTED
        if f"{case.replace('.', '-')}-{repeat}" not in names
    )
    extra = sorted(names - {
        f"{case.replace('.', '-')}-{repeat}" for case, repeat in EXPECTED
    })
    seen: dict[str, str] = {}
    duplicates: list[dict] = []
    for name, record in records.items():
        trial_id = str(record.get("trial_id") or "")
        if not trial_id:
            duplicates.append({"slot": name, "reason": "empty trial id"})
            continue
        if trial_id in seen:
            duplicates.append({"slot": name, "trial_id": trial_id, "first": seen[trial_id]})
            continue
        seen[trial_id] = name
    ungraded = {
        name: record["status"] for name, record in records.items()
        if record["status"] in {"infra_error", "verifier_error"}
    }
    return {
        "root": str(root), "graded": len(records), "expected": len(EXPECTED),
        "complete": not missing and not extra,
        "missing": missing, "extra": extra,
        "duplicate_trial_ids": duplicates,
        "ungraded_reasons": ungraded,
    }


def finalize(arm_roots: dict[str, list[Path]], *, apply: bool) -> dict:
    report: dict = {"arms": {}, "applied": apply}
    for arm, roots in arm_roots.items():
        main, shards = roots[0], roots[1:]
        arm_report: dict = {"main": str(main), "shards": [str(s) for s in shards]}

        infra = [
            name for name, record in _records(main).items()
            if record["status"] in {"infra_error", "verifier_error"}
        ]
        arm_report["infrastructure_slots"] = infra
        if infra and apply:
            arm_report["discarded"] = discard(main, infra)["removed"]
        for shard in shards:
            shard_infra = [
                name for name, record in _records(shard).items()
                if record["status"] in {"infra_error", "verifier_error"}
            ]
            if shard_infra and apply:
                discard(shard, shard_infra)

        for shard in shards:
            # A shard whose slots all landed in the main root in an earlier pass is an
            # empty directory; merging it would compare frozen configurations that no
            # longer need to agree, and refuse the whole run for nothing.
            if not (shard / "configuration-agent.json").is_file():
                arm_report.setdefault("merges", []).append({
                    "shard": str(shard), "skipped": "no frozen configuration",
                })
                continue
            plan = merge(main, shard, apply=apply)
            arm_report.setdefault("merges", []).append({
                "shard": str(shard), "refused": plan["refused"],
                "movable": len(plan["movable"]), "applied": plan.get("applied", False),
            })
            if plan["refused"]:
                report["refused"] = True

        if apply:
            arm_report["rescore"] = rescore(main, apply=True)["summary"]
            # A shard that could not be merged still holds slots; re-score it too so
            # nothing is judged by a stale ruleset.
            for shard in shards:
                if (shard / "configuration-agent.json").is_file() and any(shard.iterdir()):
                    arm_report.setdefault("rescore_shards", {})[str(shard)] = (
                        rescore(shard, apply=True)["summary"]
                    )
        arm_report["audit"] = audit(main)
        report["arms"][arm] = arm_report
    report["ready"] = all(
        arm["audit"]["complete"] and not arm["audit"]["duplicate_trial_ids"]
        for arm in report["arms"].values()
    ) and not report.get("refused")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, default=Path("evaluation/work/arm-a"))
    parser.add_argument("--a-shard", type=Path, action="append", default=[])
    parser.add_argument("--b", type=Path, default=Path("evaluation/work/arm-b"))
    parser.add_argument("--b-shard", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path, default=Path("evaluation/AB_REPORT.md"))
    parser.add_argument("--comparison", type=Path,
                        default=Path("evaluation/work/ab-comparison.json"))
    parser.add_argument("--apply", action="store_true",
                        help="perform discard/merge/rescore; without it this is a dry run")
    args = parser.parse_args()

    arms = {
        "a": [args.a.resolve()] + [p.resolve() for p in args.a_shard],
        "b": [args.b.resolve()] + [p.resolve() for p in args.b_shard],
    }
    summary = finalize(arms, apply=args.apply)
    if args.apply and summary["ready"]:
        report = compare(arms["a"][0], arms["b"][0])
        args.report.write_text(
            render(report, root_a=str(arms["a"][0]), root_b=str(arms["b"][0])),
            encoding="utf-8",
        )
        args.comparison.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        summary["report"] = str(args.report)
        summary["comparable"] = report["comparable"]
        summary["paired"] = f"{report['slots_paired']}/{report['slots_total']}"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
