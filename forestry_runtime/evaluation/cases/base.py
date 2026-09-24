"""Declarative bindings between suite checks and independent verifiers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verifier:
    module: str
    function: str


@dataclass(frozen=True)
class Case:
    """One evaluation contract.

    ``fixtures`` is the complete set of input conditions this contract covers, each
    with the expected behaviour *declared in advance*:

    * ``normal`` -- the task must be carried out and the deliverable produced;
    * ``gap`` -- a required input is missing or unusable, so refusing to compute and
      naming the missing evidence is the correct outcome.

    Declaring the branch here, rather than inferring it from what the model claimed it
    built, is what makes the grade trustworthy: a run that produces nothing cannot move
    itself onto the easier branch by writing ``{"built": false}``.

    ``fixture``/``gold`` remain the canonical single-fixture binding used by trials that
    cover one condition.
    """

    id: str
    track: str
    group: str
    checks: dict[str, Verifier]
    expected_terminal: str
    fixture: str
    gold: str
    harness: str = "agent"
    repeats: int | None = None
    fixtures: dict[str, str] | None = None

    def fixture_conditions(self) -> dict[str, str]:
        """Every input condition with its fixture path, normal first."""
        declared = dict(self.fixtures or {})
        declared.setdefault("normal", self.fixture)
        return declared


__all__ = ["Case", "Verifier"]
