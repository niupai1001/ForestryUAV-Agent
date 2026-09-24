"""Shared verifier result contract; this package never imports Runtime code."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

VERDICTS = ("pass", "fail", "unknown", "not_applicable")


@dataclass(frozen=True)
class Verdict:
    """One check's outcome.

    ``reason`` is always populated, whatever the verdict:

    * ``pass`` -- what was observed and why it satisfies the contract;
    * ``fail`` -- the specific requirement that was not met;
    * ``unknown`` -- the collector or the infrastructure prevented a judgement. A Run
      that ended normally but did not deliver what the task required is a ``fail``,
      not an ``unknown``;
    * ``not_applicable`` -- the check does not apply to this declared condition (for
      example the pixel comparison in a case whose input cannot support the product),
      with the reason it is inapplicable.
    """

    verdict: Literal["pass", "fail", "unknown", "not_applicable"]
    verifier: str
    evidence: list[str]
    detail: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"Unsupported verdict: {self.verdict}")
        if not self.verifier.strip():
            raise ValueError("Verifier identity is required")
        if self.verdict != "pass" and not self.detail.strip():
            raise ValueError(
                f"A {self.verdict} verdict requires a reason in `detail`"
            )
        for item in self.evidence:
            path = Path(item)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Evidence paths must stay relative to the evidence package")

    @property
    def reason(self) -> str:
        return self.detail

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


def status_for_terminal(actual: str | None, expected: str = "completed") -> str:
    """Map a Run's terminal state onto a scorecard trial status.

    ``scorecard`` treats ``timeout`` and ``crash`` as verified failures and
    ``infra_error`` as unknown.

    * ``failed`` / ``canceled`` / ``cancel_incomplete`` -- the task did not succeed.
    * ``paused`` -- the Runtime stopped the Run deliberately: a pause request, an
      exhausted model-call budget, or the repeated-failure guard rail. That is an
      Agent outcome, not a collector problem, and an Agent that loops on failing
      calls has failed the task. Recording it as ``infra_error`` let the most
      instructive failure mode -- getting stuck -- read as "untested".
    * still running or unreachable -- ``infra_error``, which stays in the
      denominator as unknown.
    """
    if actual == expected:
        return "evaluated"
    if actual in {"failed", "canceled", "cancel_incomplete", "paused"}:
        return "crash"
    if actual == "timeout":
        return "timeout"
    return "infra_error"


__all__ = [
    "VERDICTS", "Verdict", "delivered_artifact_name", "output_rasters",
    "save_verdict", "status_for_terminal",
]
