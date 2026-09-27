"""What a finished run is allowed to claim about itself.

The defect this exists to fix is a vocabulary problem, not a tooling problem. The
Runtime could only say "done" or "not done". Those two words are not enough for the
three situations a run actually ends in:

* it produced the thing and something checked that the thing is right;
* it produced the thing, the thing exists and is well-formed, and *nobody* checked
  whether it means what the task asked for -- the common case: a GeoTIFF written by
  a threshold is a file, not an answer;
* it did not produce the thing.

Collapsing the second into the first is how a green run gets reported as a success
that no one can defend. So the result is three-valued, and the middle value is the
default rather than the exception:

    verified_complete     -> layers pass, including a semantic verdict
    delivered_unverified  -> the product exists and is well-formed; truth unknown
    failed                -> something below that did not hold

Two rules follow, and they are enforced here rather than left to callers:

1. **A verdict that failed can never yield ``verified_complete``.** Not as a default,
   not after a fallback, not because a later layer passed.
2. **"Correct shape, no truth value" is ``delivered_unverified``.** A structural pass
   is evidence about the container, not about the contents.

The layering is deliberate and ordered: execution -> structural -> semantic. Each
layer is only meaningful if the one before it held, and a later layer can never
promote a result the earlier one already demoted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

LayerName = Literal["execution", "structural", "semantic"]

LAYERS: tuple[str, ...] = ("execution", "structural", "semantic")

#: One check either holds, does not hold, or could not be evaluated. "Could not be
#: evaluated" is a first-class outcome: a semantic check nobody ran is not a pass
#: waiting to be discovered, it is the reason the result is ``delivered_unverified``.
CheckOutcome = Literal["passed", "failed", "not_run"]

STATE_VERIFIED = "verified_complete"
STATE_UNVERIFIED = "delivered_unverified"
STATE_FAILED = "failed"

VERIFICATION_STATES: tuple[str, ...] = (STATE_VERIFIED, STATE_UNVERIFIED, STATE_FAILED)


@dataclass
class Check:
    """One question asked of the run, and the answer."""

    layer: str
    name: str
    outcome: CheckOutcome = "not_run"
    detail: str = ""
    evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ValueError(f"unknown verification layer: {self.layer!r}")
        if self.outcome not in ("passed", "failed", "not_run"):
            raise ValueError(f"unknown check outcome: {self.outcome!r}")

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    @property
    def blocking(self) -> bool:
        return self.outcome == "failed"

    def as_dict(self) -> dict:
        return {
            "layer": self.layer, "name": self.name, "outcome": self.outcome,
            "detail": self.detail, "evidence_ids": list(self.evidence_ids),
        }


@dataclass
class VerificationReport:
    """The result of asking all three layers about one run."""

    state: str = STATE_UNVERIFIED
    checks: list[Check] = field(default_factory=list)
    #: Why the state is what it is. A state without reasons is not auditable.
    reasons: list[str] = field(default_factory=list)
    #: What would have to happen for this to become ``verified_complete``.
    next_actions: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.state == STATE_VERIFIED

    def by_layer(self, layer: str) -> list[Check]:
        return [check for check in self.checks if check.layer == layer]

    def failed_checks(self) -> list[Check]:
        return [check for check in self.checks if check.blocking]

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "verified": self.verified,
            "reasons": list(self.reasons),
            "next_actions": list(self.next_actions),
            "evidence_ids": list(self.evidence_ids),
            "layers": {
                layer: [check.as_dict() for check in self.by_layer(layer)]
                for layer in LAYERS
            },
        }

    def render(self) -> str:
        """Short model-facing text. States are named, never softened."""
        lines = [f"verification: {self.state}"]
        for reason in self.reasons[:6]:
            lines.append(f"- {reason}")
        for action in self.next_actions[:4]:
            lines.append(f"- next: {action}")
        return "\n".join(lines)


def fold(checks: list[Check]) -> tuple[str, list[str]]:
    """Combine layers into one state.

    Two things are decided here and nowhere else:

    * any ``failed`` check demotes the whole result to ``failed``, whatever layer
      reported it and whatever later layers say;
    * anything unevaluated keeps the result at ``delivered_unverified``, because
      unevaluated is exactly the state "exists but unproven".

    ``verified_complete`` is therefore reachable only by every check on every layer
    having actually passed -- it is the strictest outcome, not the default.
    """
    failed = [check for check in checks if check.blocking]
    if failed:
        reasons = [
            f"{check.layer}/{check.name}: {check.detail or 'failed'}"
            for check in failed[:6]
        ]
        return STATE_FAILED, reasons

    unevaluated = [check for check in checks if check.outcome == "not_run"]
    if unevaluated:
        reasons = [
            f"{check.layer}/{check.name} was not evaluated"
            + (f" ({check.detail})" if check.detail else "")
            for check in unevaluated[:6]
        ]
        return STATE_UNVERIFIED, reasons

    if not checks:
        return STATE_UNVERIFIED, ["no verification checks were run"]

    return STATE_VERIFIED, ["all layers passed"]


def clamp(claimed: str, report: VerificationReport) -> str:
    """Never let a caller claim more than the layers support.

    A component that produced an artifact wants to say it succeeded. It may say so
    only up to what was actually checked; this is the single place that is enforced,
    so "who is allowed to upgrade a result" has one answer.
    """
    if claimed == STATE_VERIFIED and report.state != STATE_VERIFIED:
        return report.state
    if claimed == STATE_UNVERIFIED and report.state == STATE_FAILED:
        return report.state
    if claimed not in VERIFICATION_STATES:
        return report.state
    return claimed


def _text(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


__all__ = [
    "Check", "CheckOutcome", "LAYERS", "LayerName", "STATE_FAILED", "STATE_UNVERIFIED",
    "STATE_VERIFIED", "VERIFICATION_STATES", "VerificationReport", "clamp", "fold",
    "_text",
]
