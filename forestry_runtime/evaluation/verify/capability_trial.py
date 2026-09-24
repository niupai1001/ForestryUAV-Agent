"""Trial adapters for the capability task set.

Each adapter turns one collected evidence package into a scorecard record. The
declared condition decides which checks apply, so a ``not_applicable`` verdict
always reflects the contract rather than something the answer claimed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .capability_common import (
    capability_record, load_trace, not_applicable, require_slot,
)
from .capability_raster import (
    area_is_derived_or_withheld, mask_and_denominator, zonal_statistics_match,
)
from .capability_supervised import (
    baseline_was_compared, predictions_and_metrics, split_respects_groups,
)
from .base import Verdict


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _fixture(condition: str) -> Path:
    return PROJECT_ROOT / "evaluation" / "fixtures" / f"capability_raster_stats{'' if condition == 'normal' else '_gap'}"


def _gold(condition: str) -> Path:
    return (
        PROJECT_ROOT / "evaluation" / "fixtures" / "gold"
        / f"capability_raster_stats{'' if condition == 'normal' else '_gap'}.json"
    )


def verify_raster_stats(
    trial: Path, configuration: dict, repeat: int, *, condition: str = "normal",
    gold: Path | None = None, case_id: str = "capability.raster_stats",
) -> dict[str, Any]:
    trace = load_trace(trial)
    require_slot(trace, configuration, repeat)
    gold = gold or _gold(condition)
    fixture = _fixture(condition)
    zonal = zonal_statistics_match(
        trial=trial, fixture=fixture, gold=gold,
        report=trial / "zonal-verifier.json", condition=condition,
    )
    mask = mask_and_denominator(
        trial=trial, fixture=fixture, gold=gold,
        report=trial / "mask-verifier.json",
    )
    area = area_is_derived_or_withheld(
        trial=trial, fixture=fixture, gold=gold,
        report=trial / "area-verifier.json", condition=condition,
    )
    return capability_record(
        trial=trial, trace=trace, case_id=case_id, condition=condition,
        checks={"zonal": zonal, "mask": mask, "area": area},
    )


def _supervised_fixture(condition: str) -> Path:
    suffix = "" if condition == "normal" else "_gap"
    return PROJECT_ROOT / "evaluation" / "fixtures" / f"capability_supervised{suffix}"


def _supervised_gold(condition: str) -> Path:
    suffix = "" if condition == "normal" else "_gap"
    return (
        PROJECT_ROOT / "evaluation" / "fixtures" / "gold"
        / f"capability_supervised{suffix}.json"
    )


def verify_supervised(
    trial: Path, configuration: dict, repeat: int, *, condition: str = "normal",
    gold: Path | None = None, case_id: str = "capability.supervised",
) -> dict[str, Any]:
    trace = load_trace(trial)
    require_slot(trace, configuration, repeat)
    gold = gold or _supervised_gold(condition)
    fixture = _supervised_fixture(condition)
    split = split_respects_groups(
        trial=trial, fixture=fixture, gold=gold, report=trial / "split-verifier.json",
    )
    metrics = predictions_and_metrics(
        trial=trial, fixture=fixture, gold=gold, report=trial / "metrics-verifier.json",
    )
    baseline = baseline_was_compared(
        trial=trial, fixture=fixture, gold=gold, report=trial / "baseline-verifier.json",
    )
    return capability_record(
        trial=trial, trace=trace, case_id=case_id, condition=condition,
        checks={"split": split, "metrics": metrics, "baseline": baseline},
    )


def verify_recompute(
    trial: Path, configuration: dict, repeat: int, *, condition: str = "normal",
    gold: Path | None = None, case_id: str = "capability.recompute",
) -> dict[str, Any]:
    """Recomputation, graded from recorded evidence only.

    Two declared conditions, both about not reusing a stale result:

    * ``normal`` -- the *data* changes between the two passes (the workspace copy is
      edited), so the second pass must recompute from new input;
    * ``changed`` -- the data does not change but the *requested statistic* does
      (mean, then median), so the second pass must recompute because the answer the
      first pass produced does not answer the second request.

    The second condition exists because "the file changed" is not the only reason a
    cached result stops being valid, and an Agent that only watches file hashes will
    hand back the mean when asked for the median.
    """
    from .core_repair import rerun_produced_new_result, source_was_not_modified
    from .core_repair_trial import summary_matches

    trace = load_trace(trial)
    require_slot(trace, configuration, repeat)
    fixture = PROJECT_ROOT / "evaluation" / "fixtures" / f"capability_recompute_{condition}"
    gold = gold or (
        PROJECT_ROOT / "evaluation" / "fixtures" / "gold"
        / f"capability_recompute_{condition}.json"
    )
    lineage = source_was_not_modified(
        trace=trace, gold=gold, report=trial / "lineage-verifier.json"
    )
    fresh = rerun_produced_new_result(
        trace=trace, gold=gold, report=trial / "rerun-verifier.json",
        require_input_change=condition == "normal",
    )
    result = summary_matches(
        trial=trial, case_dir=fixture, gold=gold,
        report=trial / "new-result-verifier.json", label="new-result",
    )
    return capability_record(
        trial=trial, trace=trace, case_id=case_id, condition=condition,
        checks={"lineage": lineage, "fresh_job": fresh, "new_result": result},
    )


def _verify_ndvi(
    trial: Path, configuration: dict, repeat: int, *, condition: str,
    case_id: str = "capability.ndvi",
) -> dict[str, Any]:
    """Reuse the NDVI verifiers against the capability fixture and its condition."""
    from .ndvi import (
        compare_ndvi_grid_mask, compare_ndvi_pixels, reported_facts_match_artifact,
    )

    trace = load_trace(trial)
    require_slot(trace, configuration, repeat)
    suffix = "" if condition == "normal" else "_gap"
    gold = (
        PROJECT_ROOT / "evaluation" / "fixtures" / "gold"
        / f"capability_ndvi{suffix}.json"
    )
    artifacts = trial / "artifacts"
    contract = json.loads(gold.read_text(encoding="utf-8"))
    if condition != "normal":
        reason = (
            "The declared condition withholds the band description, so the band roles "
            "cannot be established and no NDVI may be computed from them."
        )
        return capability_record(
            trial=trial, trace=trace, case_id=case_id, condition=condition,
            checks={
                "pixels": not_applicable("ndvi-pixels", reason),
                "grid_mask": not_applicable("ndvi-grid-mask", reason),
                "answer": _refusal_was_stated(trial, gold),
            },
        )
    return capability_record(
        trial=trial, trace=trace, case_id=case_id, condition=condition,
        checks={
            "pixels": compare_ndvi_pixels(
                artifacts=artifacts, gold=gold, report=trial / "pixels-verifier.json"
            ),
            "grid_mask": compare_ndvi_grid_mask(
                artifacts=artifacts, gold=gold,
                report=trial / "grid-mask-verifier.json",
            ),
            "answer": reported_facts_match_artifact(
                answer=_answer(trial), artifacts=artifacts, gold=gold,
                report=trial / "answer-verifier.json",
            ),
        },
    )


def _refusal_was_stated(trial: Path, gold: Path) -> Any:
    """A gap condition is satisfied by naming what is missing, not by guessing."""
    from .base import Verdict
    from .capability_common import answer_text

    contract = json.loads(gold.read_text(encoding="utf-8"))
    answer = answer_text(trial)
    lowered = answer.casefold()
    keywords = [str(item).casefold() for item in contract.get("gap_keywords", [])]
    delivered = [
        path.name for path in sorted((trial / "artifacts").glob("asset_*.tif"))
    ]
    hits = [word for word in keywords if word and word in lowered]
    payload = {"gap_keywords": keywords, "matched": hits, "delivered_rasters": delivered}
    (trial / "refusal-verifier.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if delivered:
        return Verdict(
            "fail", "ndvi-refusal-v1", ["refusal-verifier.json"],
            "A raster was delivered for a condition whose band roles cannot be "
            "established from the metadata.",
        )
    if not hits:
        return Verdict(
            "fail", "ndvi-refusal-v1", ["refusal-verifier.json"],
            "No NDVI was delivered, but the answer does not name the missing band "
            "information, so the refusal is indistinguishable from a silent failure.",
        )
    return Verdict(
        "pass", "ndvi-refusal-v1", ["refusal-verifier.json"],
        f"The missing evidence was named ({', '.join(hits[:3])}) and no raster was "
        "fabricated.",
    )


def _answer(trial: Path) -> str:
    from .capability_common import answer_text

    return answer_text(trial)


def _verify_chm(
    trial: Path, configuration: dict, repeat: int, *, condition: str,
    case_id: str = "capability.chm",
) -> dict[str, Any]:
    """Reuse the CHM verifiers against the capability fixture and its condition."""
    from .chm import (
        chm_claims_match_artifact, compare_chm_grid_mask, compare_chm_pixels,
        raster_candidates,
    )

    trace = load_trace(trial)
    require_slot(trace, configuration, repeat)
    suffix = "" if condition == "normal" else "_gap"
    gold = (
        PROJECT_ROOT / "evaluation" / "fixtures" / "gold"
        / f"capability_chm{suffix}.json"
    )
    contract = json.loads(gold.read_text(encoding="utf-8"))
    artifacts = trial / "artifacts"
    delivered, _ = raster_candidates(artifacts, contract)
    require_built = condition == "normal" or bool(delivered)
    claims = chm_claims_match_artifact(
        answer=_answer(trial), artifacts=artifacts, gold=gold,
        require_built=require_built, report=trial / "claims-verifier.json",
    )
    reason = (
        f"The declared condition is '{condition}': the inputs cannot support a CHM, "
        "so the pixel comparison does not apply."
    )
    if delivered:
        pixels = compare_chm_pixels(
            artifacts=artifacts, gold=gold, report=trial / "pixels-verifier.json"
        )
        claim = compare_chm_grid_mask(
            artifacts=artifacts, gold=gold, report=trial / "grid-verifier.json"
        )
    elif require_built:
        pixels = claim = Verdict(
            verdict="fail",
            verifier="capability-chm-missing-deliverable-v1",
            evidence=[],
            detail="The condition requires a canopy height model and no raster was "
                   "delivered.",
        )
    else:
        pixels = not_applicable("positive", reason)
        claim = not_applicable("claim", reason)
    return capability_record(
        trial=trial, trace=trace, case_id=case_id, condition=condition,
        checks={"positive": pixels, "claim": claim, "negative": claims},
    )


def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, case_id: str,
    gold: Path | None = None, condition: str = "normal", **_: Any,
) -> dict[str, Any]:
    """Dispatch on the case id; every capability trial adapter funnels through here."""
    if case_id == "capability.raster_stats":
        return verify_raster_stats(
            trial, configuration, repeat, condition=condition, gold=gold, case_id=case_id
        )
    if case_id == "capability.supervised":
        return verify_supervised(
            trial, configuration, repeat, condition=condition, gold=gold, case_id=case_id
        )
    if case_id == "capability.recompute":
        return verify_recompute(
            trial, configuration, repeat, condition=condition, gold=gold, case_id=case_id
        )
    if case_id == "capability.ndvi":
        return _verify_ndvi(
            trial, configuration, repeat, condition=condition, case_id=case_id
        )
    if case_id == "capability.chm":
        return _verify_chm(
            trial, configuration, repeat, condition=condition, case_id=case_id
        )
    raise ValueError(f"No capability trial adapter for {case_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--condition", default="normal")
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    trace = load_trace(args.trial)
    record = verify_trial(
        args.trial.resolve(), trace["configuration"], int(trace["repeat"]),
        case_id=args.case, gold=args.gold.resolve() if args.gold else None,
        condition=args.condition,
    )
    args.record.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "status": record["status"],
        "condition": record.get("condition"),
        "checks": {key: value["verdict"] for key, value in record["checks"].items()},
    }, ensure_ascii=False))
    return 0 if record["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
