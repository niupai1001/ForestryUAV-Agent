"""Turn the facts a run already has into checks.

Nothing here inspects the world again. Every check reads facts the Runtime already
recorded -- tool outcomes, artifact descriptors, work-plan state -- because a
verification pass that re-runs the work is not verification, it is a second attempt
with better lighting.

The split matters:

* **execution** asks "did the machinery work" -- no call failed, nothing was blocked,
  the run did not stop before it said what it was doing;
* **structural** asks "is the product well-formed and complete" -- files exist, are
  readable, their producer reported success, every open requirement is settled;
* **semantic** asks "does it mean what the task asked", which only a verifier for
  that artifact kind can answer, and which is answered ``not_run`` when no such
  verifier exists.

That last case is the whole reason for the three-state result. A GeoTIFF that exists
and is readable has passed everything a Runtime can check mechanically, and none of
it says whether the mask inside is the right forest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import STATE_UNVERIFIED, Check, _text

#: Stop reasons that mean the run ended before it finished saying what it was doing.
EARLY_STOP_REASONS = frozenset({"premature_stop", "max_steps", "budget_exhausted",
                                "aborted", "interrupted"})


@dataclass
class RunFacts:
    """What the Run knows about itself, collected for verification."""

    #: Tool names whose call returned a failure during this run.
    tool_failures: list[str] = field(default_factory=list)
    #: Calls the preflight declined, as ``"tool: reason"``.
    blocked_calls: list[str] = field(default_factory=list)
    #: Artifact descriptors, i.e. the output of ``artifacts_deliver``.
    artifacts: list[dict] = field(default_factory=list)
    #: Requirement ids still open on the work plan.
    open_requirements: list[str] = field(default_factory=list)
    #: Requirement ids explicitly blocked.
    blocked_requirements: list[str] = field(default_factory=list)
    #: ``WorkPlan.completion_state()``.
    plan_completion: str = "unknown"
    #: Why the run stopped, when it did not stop on its own terms.
    stop_reason: str = ""


def execution_checks(facts: RunFacts) -> list[Check]:
    """Did the machinery work."""
    failures = [_text(item) for item in facts.tool_failures if _text(item)]
    checks = [
        Check(
            layer="execution", name="no_failed_tool_calls",
            outcome="failed" if failures else "passed",
            detail=("failed calls: " + ", ".join(failures[:6])) if failures else "",
        )
    ]
    blocked = [_text(item) for item in facts.blocked_calls if _text(item)]
    checks.append(Check(
        layer="execution", name="no_blocked_calls",
        outcome="failed" if blocked else "passed",
        detail=("blocked: " + "; ".join(blocked[:4])) if blocked else "",
    ))
    early = str(facts.stop_reason or "").strip().casefold() in EARLY_STOP_REASONS
    checks.append(Check(
        layer="execution", name="run_reached_a_conclusion",
        outcome="failed" if early else "passed",
        detail=f"stop_reason={facts.stop_reason}" if early else "",
    ))
    return checks


def structural_checks(facts: RunFacts) -> list[Check]:
    """Is the product well-formed and complete."""
    checks: list[Check] = []
    artifacts = [item for item in facts.artifacts if isinstance(item, dict)]

    if not artifacts:
        checks.append(Check(
            layer="structural", name="artifact_delivered", outcome="failed",
            detail="the run delivered no artifact",
        ))
    else:
        missing = []
        unreadable = []
        producer_failed = []
        for item in artifacts:
            name = _text(item.get("name") or item.get("asset_id"), 120) or "artifact"
            checks_field = item.get("checks") if isinstance(item.get("checks"), dict) else {}
            if not checks_field.get("file_exists", True):
                missing.append(name)
            if not checks_field.get("server_can_read", True):
                unreadable.append(name)
            if not checks_field.get("producer_reported_success", True):
                producer_failed.append(name)
        checks.append(Check(
            layer="structural", name="artifact_files_exist",
            outcome="failed" if missing else "passed",
            detail=("missing: " + ", ".join(missing[:6])) if missing else "",
        ))
        checks.append(Check(
            layer="structural", name="artifact_files_readable",
            outcome="failed" if unreadable else "passed",
            detail=("unreadable: " + ", ".join(unreadable[:6])) if unreadable else "",
        ))
        checks.append(Check(
            layer="structural", name="producer_reported_success",
            outcome="failed" if producer_failed else "passed",
            detail=("producer failed: " + ", ".join(producer_failed[:6])) if producer_failed else "",
        ))
        for item in artifacts:
            checks.extend(product_qa_checks(item))

    open_ids = [_text(item, 120) for item in facts.open_requirements if _text(item)]
    checks.append(Check(
        layer="structural", name="requirements_settled",
        outcome="failed" if open_ids else "passed",
        detail=("open: " + ", ".join(open_ids[:6])) if open_ids else "",
    ))
    blocked_ids = [_text(item, 120) for item in facts.blocked_requirements if _text(item)]
    checks.append(Check(
        layer="structural", name="no_blocked_requirements",
        outcome="failed" if blocked_ids else "passed",
        detail=("blocked: " + ", ".join(blocked_ids[:6])) if blocked_ids else "",
    ))
    completion = str(facts.plan_completion or "unknown").casefold()
    checks.append(Check(
        layer="structural", name="plan_reports_complete",
        outcome="failed" if completion == "blocked" else "passed",
        detail=f"plan_completion={completion}" if completion == "blocked" else "",
    ))
    return checks


def product_qa_checks(artifact: dict) -> list[Check]:
    """What the product's own measured metadata can establish.

    Deliberately structural, not semantic: a raster that has a CRS and a non-empty
    valid area is a well-formed raster, and neither number says what its values mean.
    Putting these in the semantic layer would let a product qualify as
    ``verified_complete`` on the strength of having been measured.
    """
    qa = artifact.get("product_qa")
    if not isinstance(qa, dict) or not qa:
        return []
    if qa.get("error"):
        return [Check(
            layer="structural", name="product_qa_readable", outcome="failed",
            detail=str(qa.get("error"))[:200],
        )]
    name = _text(artifact.get("name") or artifact.get("asset_id"), 120) or "artifact"
    checks: list[Check] = []
    has_crs = bool(qa.get("crs"))
    checks.append(Check(
        layer="structural", name="product_has_crs",
        outcome="passed" if has_crs else "failed",
        detail="" if has_crs else f"{name} carries no CRS",
    ))
    valid = qa.get("valid_fraction_sampled")
    if isinstance(valid, (int, float)):
        checks.append(Check(
            layer="structural", name="product_has_valid_pixels",
            outcome="passed" if valid > 0 else "failed",
            detail=f"{name}: valid_fraction_sampled={round(float(valid), 4)}",
        ))
    return checks


def semantic_checks(verdicts: list[Check]) -> list[Check]:
    """Does it mean what the task asked.

    With no verifier for the artifact kind there is no check to report as passed --
    the honest answer is ``not_run``, which keeps the result at
    ``delivered_unverified`` instead of letting an empty layer read as agreement.
    """
    if not verdicts:
        return [Check(
            layer="semantic", name="artifact_answers_task", outcome="not_run",
            detail="no verifier is registered for the delivered artifact kind",
        )]
    return list(verdicts)


def unverified_report_reason(facts: RunFacts) -> str:
    """One sentence for the common middle state, for logs and traces."""
    return (
        f"delivered {len(facts.artifacts)} artifact(s) with no semantic verdict; "
        f"reported as {STATE_UNVERIFIED}"
    )


__all__ = [
    "EARLY_STOP_REASONS", "RunFacts", "execution_checks", "product_qa_checks",
    "semantic_checks", "structural_checks", "unverified_report_reason",
]
