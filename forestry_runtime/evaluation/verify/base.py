"""Shared verifier result contract; this package never imports Runtime code."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Verdict:
    verdict: Literal["pass", "fail", "unknown"]
    verifier: str
    evidence: list[str]
    detail: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in {"pass", "fail", "unknown"}:
            raise ValueError(f"Unsupported verdict: {self.verdict}")
        if not self.verifier.strip():
            raise ValueError("Verifier identity is required")
        for item in self.evidence:
            path = Path(item)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Evidence paths must stay relative to the evidence package")

    def as_check(self) -> dict:
        return asdict(self)


def save_verdict(verdict: Verdict, root: Path) -> Verdict:
    """Downgrade a verdict whose declared evidence does not resolve inside *root*.

    ``scorecard._proof`` already refuses to pass a check whose evidence is missing;
    this is the verifier-side half of the same rule, so a verifier never hands back
    a verdict it cannot substantiate. Four trial modules previously carried their
    own copy of this loop.
    """
    for item in verdict.evidence:
        candidate = (root / item).resolve()
        if root.resolve() not in candidate.parents or not candidate.is_file():
            return replace(
                verdict, verdict="unknown", evidence=[],
                detail=f"Verifier evidence is missing or outside the trial package: {item}",
            )
    return verdict


def delivered_artifact_name(path: Path) -> str:
    """Recover the artifact's original name from ``asset_<id>-<name>``."""
    stem, separator, remainder = path.name.partition("-")
    return remainder if separator and stem.startswith("asset_") else path.name


def output_rasters(artifacts: Path, gold: dict) -> tuple[list[Path], list[Path]]:
    """Split delivered GeoTIFFs into (outputs, excluded inputs).

    The collector downloads every artifact the trace references, which includes
    the **uploaded fixture** alongside whatever the agent produced. Counting all
    GeoTIFFs therefore reports "expected exactly one" even when the agent
    delivered exactly one correct result -- a false failure that actually happened.

    Inputs are excluded by the names recorded in the gold contract rather than by
    guessing an output naming convention, because the gold is frozen and the
    convention is not part of any contract.
    """
    fixtures = {Path(name).name for name in gold.get("fixture_files", [])}
    outputs: list[Path] = []
    excluded: list[Path] = []
    for path in sorted(artifacts.glob("asset_*")):
        if not path.is_file() or path.suffix.casefold() not in {".tif", ".tiff"}:
            continue
        (excluded if delivered_artifact_name(path) in fixtures else outputs).append(path)
    return outputs, excluded


__all__ = [
    "Verdict", "delivered_artifact_name", "output_rasters", "save_verdict",
]
