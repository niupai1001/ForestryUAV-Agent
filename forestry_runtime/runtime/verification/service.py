"""Ask all three layers, once, in order, and report what they support.

Order is the point. Execution first, because a structural check on an artifact whose
producer crashed is a check on debris; structure second, because asking whether a
file is missing whether it means what the task asked is a category error. A layer is
skipped when the one before it already failed -- not as an optimisation, but because
running it would produce a verdict about a run that is already known to be broken,
and a verdict is the one thing here that gets quoted later.

The state a caller may claim is passed through ``clamp``: a component that built the
artifact wants to report success, and it may, up to what was actually checked.
"""
from __future__ import annotations

from .checks import (
    RunFacts, execution_checks, semantic_checks, structural_checks,
)
from .models import (
    STATE_FAILED, STATE_UNVERIFIED, STATE_VERIFIED, Check, VerificationReport,
    clamp, fold, _text,
)
from .registry import VerifierRegistry

#: What to do about the checks that most commonly fail. Written for a reader who has
#: only the report, so each names the thing to fix rather than the check's internals.
ACTION_FOR_CHECK = {
    "no_failed_tool_calls": "re-run the failed call, or record why it cannot succeed",
    "no_blocked_calls": "produce the new evidence the block names, then retry",
    "run_reached_a_conclusion": "continue the run; it stopped before concluding",
    "artifact_delivered": "produce the deliverable before reporting completion",
    "artifact_files_exist": "re-produce the missing file and confirm it is written",
    "artifact_files_readable": "fix the file or its permissions so it can be read back",
    "producer_reported_success": "inspect the producer's failure before re-running",
    "product_has_crs": "write the CRS into the product before delivering it",
    "product_has_valid_pixels": "the product is empty or all nodata; re-check the inputs",
    "requirements_settled": "settle the open requirements or mark them blocked honestly",
    "no_blocked_requirements": "resolve the blocker or renegotiate the requirement",
    "plan_reports_complete": "close the plan or report the run as blocked",
}


class VerificationService:
    """Verify a finished run against facts it already produced."""

    def __init__(self, registry: VerifierRegistry | None = None):
        self.registry = registry or VerifierRegistry()

    def evaluate(
        self, facts: RunFacts, *, task: str = "", claimed: str | None = None,
    ) -> VerificationReport:
        """Run the layers and return one report.

        ``claimed`` is what the caller would like to report. It is clamped, never
        trusted: this is the only place where "can this run call itself complete" is
        decided.
        """
        execution = execution_checks(facts)
        structural = structural_checks(facts)

        earlier_failed = any(check.blocking for check in execution + structural)
        if earlier_failed:
            # Nothing to ask a verifier about: the run is already known broken, and a
            # semantic verdict on it would be quoted as if it were about the product.
            verdicts = []
        else:
            verdicts = self.registry.verdicts(facts.artifacts, task)
        semantic = semantic_checks(verdicts)

        checks = execution + structural + semantic
        state, reasons = fold(checks)

        report = VerificationReport(
            state=state,
            checks=checks,
            reasons=reasons,
            next_actions=self._next_actions(checks, facts),
            evidence_ids=self._evidence_ids(facts),
        )
        if claimed is not None:
            report.state = clamp(claimed, report)
        return report

    def state_of(self, facts: RunFacts, *, task: str = "") -> str:
        return self.evaluate(facts, task=task).state

    def _next_actions(self, checks: list[Check], facts: RunFacts) -> list[str]:
        actions: list[str] = []
        for check in checks:
            if check.blocking:
                action = ACTION_FOR_CHECK.get(check.name)
                if action and action not in actions:
                    actions.append(action)
        for check in checks:
            if check.outcome == "not_run" and check.layer == "semantic":
                kinds = sorted({
                    _text(item.get("artifact_kind") or item.get("media_type"), 60)
                    for item in facts.artifacts if isinstance(item, dict)
                })
                detail = ", ".join(item for item in kinds if item) or "artifact"
                actions.append(
                    f"no semantic verdict for {detail}: register a verifier or record "
                    "a sampling review before claiming completion"
                )
                break
        return actions[:6]

    @staticmethod
    def _evidence_ids(facts: RunFacts) -> list[str]:
        ids: list[str] = []
        for artifact in facts.artifacts:
            if not isinstance(artifact, dict):
                continue
            identifier = artifact.get("asset_id") or artifact.get("id")
            if identifier:
                ids.append(str(identifier))
        return ids[:20]


__all__ = [
    "ACTION_FOR_CHECK", "RunFacts", "STATE_FAILED", "STATE_UNVERIFIED",
    "STATE_VERIFIED", "VerificationService",
]
