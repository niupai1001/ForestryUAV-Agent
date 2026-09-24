"""Data-inventory and suitability checks for the capability task set.

Two checks, one contract:

* ``counts`` -- the composition the Run reports, compared against the frozen
  manifest of a directory this repository created, so the answer is known
  independently of anything the Runtime does;
* ``suitability`` -- whether the Run found the planted quality problems and
  reached the correct conclusion about the goal, including the gap condition
  where one item cannot be assessed at all.

Both read only the answer's machine-readable block plus the trace. Numbers the
agent asserts in prose are not evidence, and neither is a claim that a check ran.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Verdict
from .capability_common import answer_text, claims_block


def _normalise_extension(key: object) -> str:
    """A countable extension: no leading dot, case-folded.

    The question is "how many files of each type are there", not "how many ways can
    the type be written".  ``.PNG``, ``PNG`` and ``png`` are the same answer, and a Run
    that gives it in any of those forms has done the work.  Treating them as different
    scored notation instead of the inventory: the first capability run answered
    ``{".PNG": 4, ".JPG": 2, ...}`` -- a complete and correct enumeration -- and was
    failed for it.
    """
    return str(key or "").strip().casefold().lstrip(".")


def _extension_counts(files: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in files:
        suffix = _normalise_extension(Path(name).suffix)
        if suffix:
            counts[suffix] = counts.get(suffix, 0) + 1
    return counts


def inventory_counts_match(*, trial: Path, gold: Path, report: Path) -> Verdict:
    """Require the reported composition to equal the directory's real composition."""
    contract = json.loads(gold.read_text(encoding="utf-8"))
    answer = answer_text(trial)
    claims = claims_block(answer, required=("counts",))
    expected = {
        key: int(value) for key, value in contract["counts_by_extension"].items()
        if int(value) > 0
    }
    recheck = _extension_counts(contract["files"])
    observations = [
        step.get("tool") for step in _trace_steps(trial)
        if step.get("status") == "success" and step.get("tool") in {"fs_list", "fs_read"}
    ]
    payload: dict[str, Any] = {
        "expected_counts": expected,
        "recomputed_from_manifest": recheck,
        "reported": claims.get("counts") if claims else None,
        "observing_actions": observations,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if expected != recheck:
        return Verdict(
            "unknown", "inventory-counts-v2", [report.name],
            "The frozen manifest and its own file list disagree, so the expected "
            "composition is not established.",
        )
    if claims is None:
        return Verdict(
            "unknown", "inventory-counts-v2", [report.name],
            "The answer contains no machine-readable counts block, so composition "
            "cannot be compared.",
        )
    reported = claims.get("counts")
    if not isinstance(reported, dict):
        return Verdict("fail", "inventory-counts-v2", [report.name],
                       "`counts` is not an object.")
    problems: list[str] = []
    # Normalise the reported keys first, so the two passes below compare types rather
    # than spellings.  Two reported spellings of the same type are merged by summing
    # them, which cannot invent a count that the answer did not state.
    normalised: dict[str, int] = {}
    for raw_key, raw_value in reported.items():
        key = _normalise_extension(raw_key)
        if not key:
            continue
        try:
            value = int(raw_value or 0)
        except (TypeError, ValueError):
            problems.append(f"{raw_key}: reported {raw_value!r}, which is not a count")
            continue
        normalised[key] = normalised.get(key, 0) + value
    payload["reported_normalised"] = normalised
    for key, want in sorted(expected.items()):
        got = normalised.get(key)
        if got is None:
            problems.append(f"{key}: not reported (expected {want})")
        elif got != want:
            problems.append(f"{key}: reported {got}, actual {want}")
    for key, got in sorted(normalised.items()):
        if key not in expected and got != 0:
            problems.append(f"{key}: reported {got}, but no such file exists")
    payload["problems"] = problems
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if problems:
        return Verdict("fail", "inventory-counts-v2", [report.name],
                       "The reported composition does not match the directory: "
                       + "; ".join(problems[:3]))
    if not observations:
        return Verdict(
            "unknown", "inventory-counts-v2", [report.name],
            "No successful listing or read was recorded, so the directory was never "
            "observed; a correct guess is not an inventory.",
        )
    return Verdict(
        "pass", "inventory-counts-v2", [report.name],
        f"Every extension count matches the directory ({len(expected)} kinds).",
    )


def _trace_steps(trial: Path) -> list[dict[str, Any]]:
    path = trial / "trace.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("steps", [])
    except (OSError, json.JSONDecodeError):
        return []


def suitability_matches(*, trial: Path, gold: Path, report: Path) -> Verdict:
    """Require the quality findings and the goal judgement, not a plausible story."""
    contract = json.loads(gold.read_text(encoding="utf-8"))
    answer = answer_text(trial)
    claims = claims_block(answer, required=("quality_problems", "usable_for_chm"))
    expected_problems = contract["quality_problems"]
    payload: dict[str, Any] = {
        "condition": contract["condition"],
        "expected_problems": [item["path"] for item in expected_problems],
        "expected_usable_for_chm": contract["usable_for_chm"],
        "expected_missing_evidence": contract["required_evidence_for_chm"],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if claims is None:
        return Verdict(
            "unknown", "inventory-suitability-v1", [report.name],
            "The answer contains no machine-readable block with `quality_problems` "
            "and `usable_for_chm`, so no judgement can be compared.",
        )
    reported = claims.get("quality_problems")
    if not isinstance(reported, list):
        return Verdict("fail", "inventory-suitability-v1", [report.name],
                       "`quality_problems` is not a list.")
    reported_paths = {
        str(item.get("path") or "") for item in reported if isinstance(item, dict)
    }
    missing = [
        item["path"] for item in expected_problems
        if item["path"] not in reported_paths
    ]
    usable = claims.get("usable_for_chm")
    missing_evidence = claims.get("missing_evidence")
    forbidden = _forbidden_claims(answer, contract, claims)
    payload.update({
        "reported_problems": sorted(reported_paths),
        "unreported_problems": missing,
        "reported_usable_for_chm": usable,
        "reported_missing_evidence": missing_evidence,
        "forbidden_claims_found": forbidden,
    })
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    problems: list[str] = []
    if missing:
        problems.append(f"undeclared quality problems: {', '.join(missing)}")
    if usable is not contract["usable_for_chm"]:
        problems.append(
            f"usable_for_chm reported {usable!r}, expected "
            f"{contract['usable_for_chm']!r}"
        )
    if not isinstance(missing_evidence, list) or not [
        item for item in missing_evidence if str(item).strip()
    ]:
        problems.append("missing_evidence is empty: the gap was not named")
    if forbidden:
        problems.append(f"unsupported claim: {forbidden[0]}")
    if problems:
        return Verdict("fail", "inventory-suitability-v1", [report.name],
                       "; ".join(problems[:3]))
    return Verdict(
        "pass", "inventory-suitability-v1", [report.name],
        f"All {len(expected_problems)} declared quality problems were reported and the "
        f"goal judgement ({contract['usable_for_chm']}) is correct.",
    )


def _forbidden_claims(
    answer: str, contract: dict, claims: dict
) -> list[str]:
    """Find claims the contract forbids regardless of the data.

    Two kinds: a declared action that the contract says must not happen (an
    inventory never starts photogrammetry), and prose that upgrades a readable file
    into a validated product.
    """
    found: list[str] = []
    if claims.get("photogrammetry_started") is True:
        found.append("the answer reports that photogrammetry was started")
    lowered = answer.casefold()
    for phrase in ("精度已验证", "精度已验证", "几何精度达到", "accuracy verified"):
        if phrase.casefold() in lowered and "未" not in lowered.split(phrase.casefold())[0][-8:]:
            found.append(f"unqualified precision claim: {phrase}")
    return found


__all__ = [
    "answer_text", "claims_block", "inventory_counts_match", "suitability_matches",
]
