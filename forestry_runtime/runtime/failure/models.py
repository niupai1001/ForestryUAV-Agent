"""The shapes a failure takes once it is a fact about the world, not a log line.

The defect this exists to fix: a failure used to live only as a tool result in the
model's history, plus a counter in process memory. Neither could answer the question
the Runtime actually has to answer on the next call -- "has the thing this failure
was about changed since?" -- so the Runtime fell back to counting. Three identical
calls meant pause, whatever those three calls were about, and *any* new observation
bumped one global counter that unlocked every recorded failure at once, including
ones about resources nobody had touched.

So a failure is recorded here as four things:

* **what it was about** (``resource_key``), not which tool reported it;
* **which version of that resource it was about** (``resource_version``), so an
  unrelated change cannot silently reopen it;
* **which assumption it falsified** (``invalid_assumptions``), because "this path
  does not exist" and "this module is not importable" are different facts even when
  the same tool reports them;
* **what the next call is allowed to do** (``retry_policy``), which replaces the
  count-to-three rule with something that names a reason.

The label vocabulary is the one ``evaluation/FRAMEWORK.md`` section 7 already fixes.
This module does not invent a second taxonomy: a classification that two documents
disagree on cannot be used to attribute a failure to a cause.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any, Literal

#: Whether a retry could plausibly succeed without anything in the world changing.
FailureClass = Literal["deterministic", "transient", "side_effect_uncertain", "unknown"]

#: What the Runtime is permitted to do with the *next* call that looks like this one.
RetryPolicy = Literal["requires_new_evidence", "backoff", "reconcile_only", "model_decides"]

#: What the preflight decides for one concrete call.
Action = Literal["execute", "decline", "reconcile"]

UNKNOWN_LABEL = "unknown"
UNKNOWN_CODE = "unknown"


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _digest(value: str, size: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def _text(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True)
class ResourceRef:
    """One thing in the world a call depended on.

    ``kind`` is deliberately coarse -- path, module, distribution ... -- because the
    question the Runtime asks is "did *this* change", and two calls naming the same
    thing through different tools have to agree on what they are talking about.
    """

    kind: str
    key: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "key": self.key}


@dataclass(frozen=True)
class StrategySignature:
    """The mechanism a call used, separated from the resource it used it on.

    Splitting these is what lets "same mechanism, resource has changed" be new work
    while "same mechanism, resource unchanged, assumption already falsified" is not.
    The current fingerprint fused tool + arguments + one global generation, so it
    could not tell those two apart.

    Built mechanically from the tool name and its arguments, never from a model's
    description of what it is doing: a semantic signature that drifts would turn a
    hard safety rule into a coin flip.
    """

    tool_family: str
    mechanism: str
    resource_keys: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        """Identity of the mechanism, deliberately excluding ``assumptions``.

        Assumptions say what a failure falsified; they are the *finding*, not the
        mechanism. Folding them into the identity would mean a call could never be
        recognised as the same call again before it had failed once with the same
        outcome -- the lookup would need to know the failure to find the failure.
        """
        return "stg_" + _digest("|".join((
            self.tool_family,
            self.mechanism,
            ",".join(self.resource_keys),
        )))

    def as_dict(self) -> dict:
        return {
            "tool_family": self.tool_family,
            "mechanism": self.mechanism,
            "resource_keys": list(self.resource_keys),
            "assumptions": list(self.assumptions),
        }


@dataclass
class FailureEvidence:
    """One failure, recorded as a fact about a resource at a version."""

    id: str = ""
    #: FRAMEWORK.md section 7 label. ``unknown`` when nothing justifies a cause.
    label: str = UNKNOWN_LABEL
    stage: str = ""
    code: str = UNKNOWN_CODE
    failure_class: FailureClass = "unknown"
    retry_policy: RetryPolicy = "model_decides"
    resource_key: str | None = None
    resource_version: str = "0"
    invalid_assumptions: list[str] = field(default_factory=list)
    strategy_id: str = ""
    tool: str = ""
    #: Observations (``obs_`` ids) this failure was recorded against, so "what did
    #: we know when this failed" stays answerable after the fact.
    evidence_ids: list[str] = field(default_factory=list)
    message: str = ""
    occurrences: int = 1
    first_at: float = 0.0
    last_at: float = 0.0

    def __post_init__(self) -> None:
        now = time.time()
        if not self.first_at:
            self.first_at = now
        if not self.last_at:
            self.last_at = self.first_at
        if not self.id:
            self.id = "fev_" + _digest("|".join((
                self.strategy_id, str(self.resource_key or "-"), self.resource_version,
            )))

    @property
    def settled(self) -> bool:
        """Whether this failure still constrains the next call.

        A failure stops constraining when the resource it was about has moved on: the
        version recorded here is no longer the version the next call would meet.
        """
        return self.resource_version != "0"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "stage": self.stage, "code": self.code,
            "failure_class": self.failure_class, "retry_policy": self.retry_policy,
            "resource_key": self.resource_key, "resource_version": self.resource_version,
            "invalid_assumptions": list(self.invalid_assumptions),
            "strategy_id": self.strategy_id, "tool": self.tool,
            "evidence_ids": list(self.evidence_ids), "message": self.message,
            "occurrences": self.occurrences,
            "first_at": self.first_at, "last_at": self.last_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FailureEvidence:
        known = {
            "id", "label", "stage", "code", "failure_class", "retry_policy",
            "resource_key", "resource_version", "invalid_assumptions", "strategy_id",
            "tool", "evidence_ids", "message", "occurrences", "first_at", "last_at",
        }
        return cls(**{key: value for key, value in (data or {}).items() if key in known})


@dataclass
class Decision:
    """What the preflight concluded about one call, and why."""

    action: Action = "execute"
    policy: RetryPolicy = "model_decides"
    #: Machine-readable reason; the model-facing wording is built by the caller so a
    #: guard rail's text never becomes the model's only account of a failure.
    reason: str = ""
    label: str = UNKNOWN_LABEL
    code: str = UNKNOWN_CODE
    stage: str = ""
    message: str = ""
    evidence_id: str = ""
    resource_key: str | None = None
    invalid_assumptions: list[str] = field(default_factory=list)
    occurrences: int = 0

    @property
    def blocked(self) -> bool:
        return self.action != "execute"

    def as_dict(self) -> dict:
        return {
            "action": self.action, "policy": self.policy, "reason": self.reason,
            "label": self.label, "code": self.code, "stage": self.stage,
            "message": self.message, "evidence_id": self.evidence_id,
            "resource_key": self.resource_key,
            "invalid_assumptions": list(self.invalid_assumptions),
            "occurrences": self.occurrences,
        }


__all__ = [
    "Action", "Decision", "FailureClass", "FailureEvidence", "ResourceRef",
    "RetryPolicy", "StrategySignature", "UNKNOWN_CODE", "UNKNOWN_LABEL",
    "_digest", "_stable", "_text",
]
