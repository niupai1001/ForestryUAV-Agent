"""Declarative bindings between suite checks and independent verifiers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verifier:
    module: str
    function: str


@dataclass(frozen=True)
class Case:
    id: str
    track: str
    group: str
    checks: dict[str, Verifier]
    expected_terminal: str
    fixture: str
    gold: str


__all__ = ["Case", "Verifier"]
