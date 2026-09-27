"""Map a reported failure to a cause label and to what a retry is allowed to do.

Two vocabularies meet here, and keeping them separate is the point:

* **label** -- *why* it failed, in the 15 words ``evaluation/FRAMEWORK.md`` section 7
  already fixes (plus ``unknown``). This is a diagnostic, written down so a report
  can attribute a failure to a cause; it is never used to decide control flow.
* **class + policy** -- *what the next call may do*. This is the part the Runtime
  acts on.

Conflating them is how a counter became a safety rule: "three failures" said nothing
about why, so the Run could not tell a retry that was genuinely new work from one
that was stalling. The label is for humans and reports; the policy is for the loop.

Every mapping is a pure function of the reported failure. Where nothing justifies a
cause, the label is ``unknown`` and the policy is ``model_decides`` -- the Runtime
does not guess at a cause to have something to write down.
"""
from __future__ import annotations

from .models import FailureClass, RetryPolicy

#: (stage, code) -> label. ``*`` matches any value in that position.
#: Taken from ARCHITECTURE_TARGET.md section 4.3 so the Runtime and the evaluation
#: reports cannot drift into two failure vocabularies.
FAILURE_TAXONOMY: dict[tuple[str, str], str] = {
    ("preconditions", "invalid_arguments"): "tool_protocol",
    ("preconditions", "source_path_not_found"): "path_grounding",
    ("preconditions", "known_invalid_source_path"): "path_grounding",
    ("preconditions", "requested_input_unavailable"): "path_grounding",
    ("preconditions", "not_ready"): "data_semantics",
    ("agent_control", "duplicate_failed_call"): "premature_stop",
    ("recovery", "action_outcome_unsettled"): "input_checkpoint",
    ("execution", "*"): "algorithm_numeric",
    ("*", "permission_denied"): "permission",
    ("*", "timeout"): "infrastructure",
    ("*", "rate_limited"): "infrastructure",
    ("*", "temporarily_unavailable"): "infrastructure",
}

#: Code-level fallbacks for producers that report a code without a stage.
CODE_LABELS: dict[str, str] = {
    "workspace_path_not_found": "path_grounding",
    "file_not_found": "path_grounding",
    "directory_not_found": "path_grounding",
    "install_failed": "resource",
    "import_failed": "resource",
    "missing_system_library": "resource",
    "unverified_module": "resource",
    "dependency_missing": "resource",
    "path_not_found": "path_grounding",
    "not_found": "path_grounding",
    "missing_input": "path_grounding",
    "invalid_parameter": "tool_protocol",
    "invalid_arguments": "tool_protocol",
    "schema_error": "tool_protocol",
    "duplicate_failed_call": "premature_stop",
    "duplicate_side_effect": "duplicate_side_effect",
    "action_outcome_unsettled": "input_checkpoint",
    "execution_failed": "algorithm_numeric",
    "timeout": "infrastructure",
    "rate_limited": "infrastructure",
}

#: label -> (failure_class, retry_policy).
#: Only the deterministic row is a hard rule: re-running the same mechanism against
#: the same version of the same resource, with the same assumption already falsified,
#: cannot produce a different answer, so it is not executed again. Everything else
#: is either bounded (backoff), requires reconciling an uncertain side effect first,
#: or is handed back to the model because the Runtime has no basis to decide.
RETRY_SEMANTICS: dict[str, tuple[FailureClass, RetryPolicy]] = {
    "path_grounding": ("deterministic", "requires_new_evidence"),
    "tool_protocol": ("deterministic", "requires_new_evidence"),
    "permission": ("deterministic", "requires_new_evidence"),
    "premature_stop": ("deterministic", "requires_new_evidence"),
    "resource": ("deterministic", "requires_new_evidence"),
    "data_semantics": ("deterministic", "requires_new_evidence"),
    "verifier": ("unknown", "model_decides"),
    "infrastructure": ("transient", "backoff"),
    "input_checkpoint": ("side_effect_uncertain", "reconcile_only"),
    "duplicate_side_effect": ("side_effect_uncertain", "reconcile_only"),
    "algorithm_numeric": ("unknown", "model_decides"),
    "retrieval": ("unknown", "model_decides"),
    "context_loss": ("unknown", "model_decides"),
    "unsupported_claim": ("unknown", "model_decides"),
    "ui_state": ("unknown", "model_decides"),
    "unknown": ("unknown", "model_decides"),
}

#: Failures whose operation may already have happened. Re-running is never the answer
#: for these -- the outstanding question is what the earlier attempt did, which is a
#: reconciliation, not a retry.
UNCERTAIN_SIDE_EFFECTS = {"partial", "partial_install", "unknown", "started", "unsettled"}

DEFAULT_LABEL = "unknown"


def label_for(stage: str, code: str) -> str:
    """The FRAMEWORK.md label for one reported failure, or ``unknown``.

    Resolved in three passes -- exact (stage, code), wildcard stage, then bare code --
    because producers differ in how much they report. Falling through to ``unknown``
    is correct behaviour, not a gap to be papered over: an unattributed failure must
    stay unattributed rather than inherit a plausible-looking cause.
    """
    stage = str(stage or "").strip()
    code = str(code or "").strip()
    if not code:
        return DEFAULT_LABEL
    if (stage, code) in FAILURE_TAXONOMY:
        return FAILURE_TAXONOMY[(stage, code)]
    if ("*", code) in FAILURE_TAXONOMY:
        return FAILURE_TAXONOMY[("*", code)]
    if (stage, "*") in FAILURE_TAXONOMY:
        return FAILURE_TAXONOMY[(stage, "*")]
    return CODE_LABELS.get(code, DEFAULT_LABEL)


def semantics_for(label: str) -> tuple[FailureClass, RetryPolicy]:
    return RETRY_SEMANTICS.get(label, RETRY_SEMANTICS[DEFAULT_LABEL])


def side_effect_uncertain(failure: dict) -> bool:
    """Whether the operation may have started and left the world half-changed.

    Read from the producer's own statement rather than inferred from the tool name:
    a failure that says ``operation_started`` is a different situation from one that
    was refused before anything ran, and only the producer knows which it was.
    """
    if not isinstance(failure, dict):
        return False
    # Refused before anything ran. A call rejected at the preconditions stage has no
    # partial effect to reconcile, whatever the producer happened to write into
    # `side_effects` -- and producers routinely default that field to "unknown".
    if str(failure.get("stage") or "").strip().casefold() == "preconditions":
        return False
    if failure.get("operation_started") is True:
        return True
    declared = str(failure.get("side_effects") or "").strip().casefold()
    if declared in ("", "none"):
        return False
    return True


def classify(failure: dict) -> tuple[str, FailureClass, RetryPolicy]:
    """Label, class and retry policy for one reported failure."""
    failure = failure if isinstance(failure, dict) else {}
    label = label_for(failure.get("stage", ""), failure.get("code", ""))
    failure_class, policy = semantics_for(label)
    # An uncertain side effect outranks the label's own policy. The label answers
    # "why"; whether the earlier attempt may have partially happened is a separate
    # question, and answering it wrong is how a retry duplicates a side effect.
    if side_effect_uncertain(failure):
        failure_class, policy = "side_effect_uncertain", "reconcile_only"
    return label, failure_class, policy


def policy_allows_retry(policy: RetryPolicy) -> bool:
    return policy in ("backoff", "model_decides")


__all__ = [
    "CODE_LABELS", "DEFAULT_LABEL", "FAILURE_TAXONOMY", "RETRY_SEMANTICS",
    "UNCERTAIN_SIDE_EFFECTS", "classify", "label_for", "policy_allows_retry",
    "semantics_for", "side_effect_uncertain",
]
