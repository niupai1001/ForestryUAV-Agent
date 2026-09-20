"""Shared verifier result contract; this package never imports Runtime code."""

from __future__ import annotations

from dataclasses import asdict, dataclass
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


__all__ = ["Verdict"]
