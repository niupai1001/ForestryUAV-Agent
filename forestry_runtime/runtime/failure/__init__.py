"""Failures as facts about resources, rather than as a count of past calls.

The Runtime used to decide "may this call run" by counting identical failures and by
bumping one global evidence generation on any new observation. Both were stand-ins
for a question it could not ask, because a failure was only ever a tool result in the
model's history. This package writes the failure down as what it actually is -- a
claim about a resource, falsified at a version -- so the question can be asked
directly, and so a report can attribute a failure to a cause without guessing.

Nothing here decides *whether the task is done*; that belongs to the task state. This
package only answers whether a specific call could still produce new information.
"""
from __future__ import annotations

from .classifier import (
    assumptions_of,
    evidence_from,
    resource_of_failure,
    resources_of,
    signature_of,
)
from .models import (
    Decision,
    FailureEvidence,
    ResourceRef,
    StrategySignature,
)
from .preflight import FailurePreflight
from .store import FailureStore, ResourceVersions
from .taxonomy import classify, label_for, semantics_for

__all__ = [
    "Decision", "FailureEvidence", "FailurePreflight", "FailureStore", "ResourceRef",
    "ResourceVersions", "StrategySignature", "assumptions_of", "classify",
    "evidence_from", "label_for", "resource_of_failure", "resources_of",
    "semantics_for", "signature_of",
]
