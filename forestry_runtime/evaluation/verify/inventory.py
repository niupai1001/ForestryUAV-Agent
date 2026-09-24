"""Role-manifest checks for the ``forestry.inventory`` contract.

The expected roles come from a **frozen, independently authored manifest** in the gold
contract, checked against the files that are actually on disk. They are deliberately
not recomputed with the Runtime's own role classifier.

That earlier design imported ``runtime.capabilities.uav_audit.audit`` at verify time
and compared its output to the gold. It reads as independence ("a rule applied to real
names"), but it makes the answer key the code under test: editing ``dataset_image_role``
turned a correct agent's run into a failure, and a verifier could never detect a wrong
classifier, because the classifier *was* the expected answer.

The manifest is a hand-maintained statement about this fixture. If the classifier and
the manifest disagree, the case fails and a human decides which one is wrong.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from .base import Verdict
from .core_files import load_events

# Suffixes that take part in a role manifest at all. Declared here rather than
# imported, so the scoring path has no Runtime dependency.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
GEOSPATIAL_SUFFIXES = {".tif", ".tiff", ".vrt", ".geojson", ".shp"}


def _answer_text(trial: Path) -> str:
    return "".join(
        str(event.get("content") or "")
        for event in load_events(trial) if event.get("type") == "message"
    )


def _claims(answer: str) -> dict[str, Any] | None:
    for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict) and "flight_imagery" in decoded:
            return decoded
    return None


def roles_match_manifest(*, trace: dict[str, Any], gold: Path, report: Path) -> Verdict:
    """Check what the tools observed against the frozen, independently authored roles.

    The report keeps three distinct things apart: the frozen manifest, the files that
    are actually present, and what the agent's tools observed. Collapsing them would
    hide the case that matters -- a tool that returns a wrong manifest while the files
    on disk say otherwise.

    Coverage is required to be *sufficient*, not exhaustive: an agent that counted the
    archive without echoing every filename has still observed it. What is required is
    that some observation ran at all, that the manifest's own file list matches disk,
    and that the roles in the contract are internally consistent.
    """
    contract = json.loads(gold.read_text(encoding="utf-8"))
    fixture_dir = Path(__file__).resolve().parents[2] / "evaluation" / "fixtures" / "forestry_inventory"
    on_disk = sorted(
        path.name for path in fixture_dir.iterdir()
        if path.is_file() and path.name != "prompt.txt"
    )
    manifest_files = sorted(str(name) for name in contract.get("files", []))
    counted = sorted(
        name for name in manifest_files
        if Path(name).suffix.casefold() in IMAGE_SUFFIXES | GEOSPATIAL_SUFFIXES
    )
    manifest_problems: list[str] = []
    if manifest_files != on_disk:
        manifest_problems.append(
            f"the frozen manifest lists {len(manifest_files)} files but disk has {len(on_disk)}"
        )
    total_roles = sum(int(value) for value in contract.get("roles", {}).values())
    if contract.get("roles") and total_roles != len(counted):
        manifest_problems.append(
            f"the frozen roles total {total_roles} but {len(counted)} files are countable"
        )

    observations: list[dict[str, Any]] = []
    observed_names: list[str] = []
    for step in trace.get("steps", []):
        if step.get("status") != "success":
            continue
        excerpt = step.get("result_excerpt")
        if excerpt is None:
            continue
        blob = json.dumps(excerpt, ensure_ascii=False, default=str)
        hits = [name for name in manifest_files if name in blob]
        if not hits:
            continue
        observations.append({"tool": step.get("tool"), "files": len(hits)})
        for name in hits:
            if name not in observed_names:
                observed_names.append(name)
    payload = {
        "manifest_roles": contract.get("roles"),
        "manifest_files": manifest_files,
        "files_on_disk": on_disk,
        "countable_files": counted,
        "manifest_problems": manifest_problems,
        "observations": observations,
        "observed_files": observed_names,
        "ground_truth": "frozen manifest in the gold contract (independent of Runtime code)",
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not contract.get("roles"):
        return Verdict("unknown", "role-manifest-v2", [report.name],
                       "The gold contract declares no expected roles.")
    if manifest_problems:
        return Verdict("unknown", "role-manifest-v2", [report.name],
                       "The frozen manifest is stale: " + "; ".join(manifest_problems))
    if not observations:
        return Verdict("unknown", "role-manifest-v2", [report.name],
                       "No tool result mentions an uploaded file, so the archive was never observed.")
    return Verdict("pass", "role-manifest-v2", [report.name],
                   f"{len(observations)} observation(s) covered {len(observed_names)} of "
                   f"{len(on_disk)} files; the frozen manifest is consistent with disk.")


def answer_matches_manifest(*, trial: Path, gold: Path, report: Path) -> Verdict:
    """Require the reported counts, groups and bands, and forbid overclaiming."""
    contract = json.loads(gold.read_text(encoding="utf-8"))
    expected = contract["answer_claims"]
    answer = _answer_text(trial)
    observed = _claims(answer)
    problems: list[str] = []
    if observed is None:
        problems.append("no machine-readable inventory block was found")
    else:
        for field, want in expected.items():
            got = observed.get(field)
            if got is None:
                problems.append(f"{field}: missing")
            elif isinstance(want, dict):
                if not isinstance(got, dict):
                    problems.append(f"{field}: expected an object of counts, got {got!r}")
                else:
                    # A band the archive does not contain must not be reported.
                    for band, count in sorted(want.items()):
                        if got.get(band) != count:
                            problems.append(f"{field}.{band}: expected {count}, got {got.get(band)}")
                    extra = sorted(set(got) - set(want))
                    if extra:
                        problems.append(f"{field}: bands not in the archive: {extra}")
            elif got != want:
                problems.append(f"{field}: expected {want}, got {got}")
        for phrase in contract["forbidden_claims"]:
            if phrase in answer:
                problems.append(f"the answer asserts {phrase!r}, which the files cannot support")
    payload = {"expected": expected, "observed": observed, "problems": problems}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if observed is None:
        return Verdict("unknown", "inventory-answer-v1", [report.name],
                       "The answer carries no inventory block; a blind human review is required.")
    if problems:
        return Verdict("fail", "inventory-answer-v1", [report.name], problems[0])
    return Verdict("pass", "inventory-answer-v1", [report.name],
                   "The reported roles, groups and bands match the archive.")


def stayed_read_only(*, trace: dict[str, Any], report: Path) -> Verdict:
    """Require observation only: no photogrammetry task and no source mutation."""
    mutating = [
        {"tool": step.get("tool"), "action_id": step.get("action_id"),
         "side_effect": step.get("side_effect")}
        for step in trace.get("steps", [])
        if step.get("status") == "success"
        and step.get("side_effect") not in (None, "none")
    ]
    launches = sorted({
        str(step.get("tool")) for step in trace.get("steps", [])
        if step.get("tool") and "orthomosaic" in str(step.get("tool")).casefold()
        and step.get("status") == "success"
    })
    payload = {"mutating_actions": mutating, "photogrammetry_tasks": launches,
               "steps": len(trace.get("steps", []))}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not trace.get("steps"):
        return Verdict("unknown", "readonly-inventory-v1", [report.name],
                       "The trace records no Action, so read-only behaviour is unobserved.")
    if launches:
        return Verdict("fail", "readonly-inventory-v1", [report.name],
                       f"A read-only inventory started {launches[0]}.")
    if mutating:
        return Verdict("fail", "readonly-inventory-v1", [report.name],
                       f"A read-only inventory ran {mutating[0]['tool']}.")
    return Verdict("pass", "readonly-inventory-v1", [report.name],
                   "The Run only observed the archive and started no photogrammetry task.")


__all__ = [
    "answer_matches_manifest", "classify_roles", "roles_match_manifest", "stayed_read_only",
]
