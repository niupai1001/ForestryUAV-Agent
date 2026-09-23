"""Artifact-to-action provenance checks over the evaluator trace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Verdict


def artifact_links_to_action(
    *, trace: dict[str, Any], artifacts: Path, report: Path,
    expected_name: str | None = None,
) -> Verdict:
    declared = [item for item in trace.get("artifacts", []) if isinstance(item, dict)]
    step_links = {
        asset_id: step.get("action_id")
        for step in trace.get("steps", [])
        for asset_id in step.get("artifact_ids", [])
    }
    observations = []
    for item in declared:
        asset_id = item.get("id")
        downloaded = sorted(artifacts.glob(f"{asset_id}-*")) if asset_id else []
        observations.append({
            "asset_id": asset_id,
            "name": item.get("name"),
            "downloaded": [path.name for path in downloaded],
            "action_id": step_links.get(asset_id),
            "name_matches": expected_name is None or item.get("name") == expected_name,
        })
    matching = [
        item for item in observations
        if item["downloaded"] and item["action_id"] and item["name_matches"]
    ]
    payload = {"observed": observations, "matching": matching}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not declared:
        verdict, detail = "unknown", "The trace declares no output artifact."
    elif matching:
        verdict, detail = "pass", "A downloaded artifact is linked to its creating Action."
    else:
        verdict, detail = "fail", "Declared artifacts lack a download, Action link, or required name."
    return Verdict(verdict, "artifact-action-link-v1", [report.name], detail)


__all__ = ["artifact_links_to_action"]
