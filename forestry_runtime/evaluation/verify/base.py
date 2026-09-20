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


__all__ = ["Verdict", "save_verdict"]
