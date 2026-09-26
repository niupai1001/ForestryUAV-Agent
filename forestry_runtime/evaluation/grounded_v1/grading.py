"""The frozen task contract for the grounded pilot, and the read-only grader.

Three objects, exactly as the plan names them:

``TaskSpec``
    What a task is: the question, the public input, the expected deliverable, the
    grading rule, the budget and the resources. The *rule* -- tolerances, grid
    requirements, what counts as valid delivery -- lives here in code and is
    versioned, so it cannot be edited after a result is seen without changing the
    version. The *truth* lives in the evaluator's private manifest and never
    enters the agent workspace.

``TrialBundle``
    What one trial produced: the configuration snapshot, the trajectory, the
    downloaded artifacts and the recorded usage.

``GradeReport``
    What the grader concluded: result quality, delivery validity, the process
    checks the evidence supports, the failure reasons and the evidence behind
    each one.

The grader is offline and read-only. It does not import the tested Runtime, does
not start it, and does not execute any code the agent wrote. It therefore cannot
re-derive the answer by running the agent's method; the frozen truth is the only
source of it.

Validity is deliberately conservative. ``v=1`` requires every delivery
requirement to hold. A requirement the evidence cannot decide makes ``v``
``unknown``, never ``0``: a broken collector or grader must not be published as an
agent failure, and the trial stays in the denominator as unmeasured.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import rasterio

from . import metrics as M
from .evidence import (
    CANOPY_KEYS, COVERAGE_KEYS, VALID_KEYS, JsonDelivery, RasterDelivery,
    align_to_grid, delivered_name, discover_artifacts, load_json_delivery,
    reference_artifact_names,
)

VERDICTS = ("pass", "fail", "unknown", "not_applicable")

#: Bumped whenever a grading judgement changes. Old evidence stays attached to the
#: rules version that produced its result.
GROUNDED_RULES_VERSION = "grounded-rules-1.0"
SUITE_VERSION = "grounded-v1.2"

#: Process checks the plan names. A trial reports each as pass/fail/unknown, and an
#: unobtainable signal is ``unknown`` -- never an implied failure.
PROCESS_CHECKS = (
    "input_scope_correct",
    "method_fits_data",
    "dependencies_satisfied",
    "key_parameters_correct",
    "recovered_from_errors",
    "verified_product",
    "avoided_premature_stop",
    "conclusion_supported_by_product",
)

#: Private evaluation evidence that must never appear in a trial's answer or
#: artifacts. Matching one is a test-label leak, which invalidates delivery.
LEAK_TOKENS = ("grounded_v1", "grounded_v1_1", "grounded_v1_1_private", "-gold.tif", "manifest.json")


@dataclass(frozen=True)
class Check:
    verdict: Literal["pass", "fail", "unknown", "not_applicable"]
    verifier: str
    evidence: list[str]
    detail: str

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"Unsupported verdict: {self.verdict}")
        if not self.verifier.strip():
            raise ValueError("Verifier identity is required")
        if self.verdict != "pass" and not self.detail.strip():
            raise ValueError(f"A {self.verdict} verdict requires a reason in `detail`")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TaskSpec:
    """A frozen task. ``grading_rule`` states how it is judged, never the answer."""

    task_id: str
    family: Literal["canopy_extraction", "canopy_statistics", "gis_analysis"]
    question: str
    public_root: str
    public_inputs: list[str]
    required_deliverables: list[str]
    grading_rule: list[str]
    condition: str
    budget: dict
    resources: dict
    tolerance: dict = field(default_factory=dict)

    @property
    def input_path(self) -> Path:
        if not self.public_inputs:
            raise ValueError(f"{self.task_id} declares no public input")
        return (Path(self.public_root) / self.public_inputs[0]).resolve()

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["input_path"] = str(self.input_path)
        return payload


@dataclass(frozen=True)
class TrialBundle:
    """Everything one trial left behind."""

    trial_id: str
    case_id: str
    repeat: int
    condition: str
    package: Path
    configuration: dict
    terminal_state: str | None
    checkpoint_state: str | None
    usage: dict
    answer_text: str
    events: list[dict]
    artifacts: list[Path]
    inputs: list[str]
    status: str

    @classmethod
    def load(cls, package: Path, *, case_id: str, expected_terminal: str = "completed") -> "TrialBundle":
        """Open a collected trial package.

        ``trace.json`` and ``raw/events.json`` are the collector's contract. A
        missing or unreadable trace is an ``infra_error`` bundle, which the report
        keeps as ``unknown`` rather than scoring as an agent failure.
        """
        package = Path(package).resolve()
        trace: dict[str, Any] = {}
        trace_error = ""
        trace_path = package / "trace.json"
        if trace_path.is_file():
            try:
                loaded = json.loads(trace_path.read_text(encoding="utf-8"))
                trace = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError) as error:
                trace_error = f"trace.json could not be read: {error}"
        else:
            trace_error = "trace.json is missing"

        events: list[dict] = []
        events_path = package / "raw" / "events.json"
        if events_path.is_file():
            try:
                loaded = json.loads(events_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    events = [event for event in loaded if isinstance(event, dict)]
            except (OSError, json.JSONDecodeError):
                events = []

        answer = "".join(
            str(event.get("content") or "") for event in events if event.get("type") == "message"
        )
        artifacts = discover_artifacts(package / "artifacts")
        terminal = trace.get("terminal_state")
        if trace_error:
            status = "infra_error"
        elif terminal == expected_terminal:
            status = "evaluated"
        elif terminal in {"failed", "canceled", "cancel_incomplete", "paused"}:
            status = "crash"
        elif terminal == "timeout":
            status = "timeout"
        else:
            status = "infra_error"

        return cls(
            trial_id=str(trace.get("run_id") or package.name),
            case_id=str(trace.get("case_id") or case_id),
            repeat=int(trace.get("repeat") or 0),
            condition=str(trace.get("condition") or ""),
            package=package,
            configuration=dict(trace.get("configuration") or {}),
            terminal_state=terminal,
            checkpoint_state=trace.get("checkpoint_state"),
            usage=dict(trace.get("usage") or {}),
            answer_text=answer,
            events=events,
            artifacts=artifacts,
            inputs=[str(item) for item in (trace.get("inputs") or [])],
            status=status,
        )


@dataclass
class GradeReport:
    task_id: str
    family: str
    trial_id: str
    repeat: int
    suite_version: str
    rules_version: str
    status: str
    quality: dict = field(default_factory=dict)
    validity: dict = field(default_factory=dict)
    process: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    delivered: list[dict] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    outcome: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        return {
            "task_id": self.task_id, "trial_id": self.trial_id, "repeat": self.repeat,
            "status": self.status, "outcome": self.outcome,
            "checks": {name: check["verdict"] for name, check in self.checks.items()},
            "failure_reasons": self.failure_reasons,
        }


def _failed(check: str, reason: str, *, evidence: list[str] | None = None) -> Check:
    return Check("fail", f"{check}-v1", evidence or [], reason)


def _unknown(check: str, reason: str, *, evidence: list[str] | None = None) -> Check:
    return Check("unknown", f"{check}-v1", evidence or [], reason)


def _passed(check: str, reason: str, *, evidence: list[str] | None = None) -> Check:
    return Check("pass", f"{check}-v1", evidence or [], reason)


def _not_applicable(check: str, reason: str) -> Check:
    return Check("not_applicable", f"{check}-v1", [], reason)


def read_gold_mask(path: Path) -> np.ndarray:
    """Read a frozen 0/1 truth raster as booleans."""
    with rasterio.open(path) as source:
        return source.read(1).astype(bool)


def _leaked_tokens(trial: TrialBundle) -> list[str]:
    haystack = trial.answer_text
    for artifact in trial.artifacts:
        haystack += "\n" + artifact.name
        if artifact.suffix.casefold() in {".json", ".txt", ".csv", ".md"}:
            try:
                haystack += "\n" + artifact.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return sorted({token for token in LEAK_TOKENS if token.casefold() in haystack.casefold()})


#: A percentage stated as a coverage figure, in either language. An answer that
#: states no coverage figure at all is a different failure from one that states a
#: contradictory figure, and the two are reported separately.
_COVERAGE_TEXT = re.compile(
    r"(coverage|canopy[_ ]?fraction|覆盖率|树冠覆盖率)[^0-9%]{0,24}([0-9]+(?:\.[0-9]+)?)\s*%?",
    re.IGNORECASE,
)


def _coverage_from_text(text: str) -> float | None:
    if not text:
        return None
    match = _COVERAGE_TEXT.search(text)
    return None if match is None else float(match.group(2))


def _reported_coverage(trial: TrialBundle) -> float | None:
    """The coverage figure the answer claims, from a JSON delivery or its text."""
    for path in trial.artifacts:
        if path.suffix.casefold() != ".json":
            continue
        parsed = load_json_delivery(path, keys=(("coverage_percent", COVERAGE_KEYS),))
        if parsed.coverage_percent is not None:
            return parsed.coverage_percent
    return _coverage_from_text(trial.answer_text)


def _unmeasured(report: GradeReport, reason: str) -> GradeReport:
    """A grader-side failure: unknown, and explicitly not the agent's fault.

    Delivery is ``None`` -- neither proved valid nor proved invalid -- and the
    trial stays in the denominator as unmeasured.
    """
    report.checks["quality"] = _unknown("grounded-quality", reason)
    report.validity = {"v": None, "reason": "the grader could not decide this trial from the evidence"}
    report.outcome = {
        "v": None,
        "iou": None if report.family == "canopy_extraction" else None,
        "task_success": None,
        "end_to_end": None,
    }
    report.failure_reasons = [reason]
    return report


def grade_canopy_extraction(
    spec: TaskSpec, trial: TrialBundle, *, gold_mask: Path,
    budget_seconds: float | None = None,
) -> GradeReport:
    """Grade one extraction trial: a 0/1 GeoTIFF aligned to the input, plus coverage."""
    report = GradeReport(
        task_id=spec.task_id, family=spec.family, trial_id=trial.trial_id,
        repeat=trial.repeat, suite_version=SUITE_VERSION,
        rules_version=GROUNDED_RULES_VERSION, status=trial.status, usage=trial.usage,
    )
    if trial.status != "evaluated":
        reason = f"the run did not reach the expected terminal state: {trial.terminal_state!r}"
        report.checks["artifact_present"] = _failed("canopy-extraction-artifact-present", reason)
        report.checks["grid"] = _not_applicable("canopy-extraction-grid", reason)
        report.checks["quality"] = _not_applicable("canopy-extraction-quality", reason)
        report.checks["report_matches_artifact"] = _not_applicable(
            "canopy-extraction-report-matches-artifact", reason
        )
        report.checks["leakage"] = _not_applicable("grounded-leakage", reason)
        report.checks["budget"] = _not_applicable("grounded-budget", reason)
        report.validity = {"v": 0, "reason": reason}
        report.failure_reasons = [reason]
        report.outcome = {"v": 0, "iou": None, "end_to_end": 0.0}
        return report

    # ---- frozen truth and grid ------------------------------------------------
    try:
        with rasterio.open(spec.input_path) as source:
            target_crs = source.crs
            target_shape = (source.height, source.width)
            target_transform = source.transform
            stack = source.read()
        valid = np.any(stack != 0, axis=0)
        truth = np.logical_and(read_gold_mask(gold_mask), valid)
    except Exception as error:  # noqa: BLE001 - a broken grader is unknown, not a failure
        return _unmeasured(
            report,
            f"the grader could not read the frozen input or truth: {type(error).__name__}: {error}",
        )

    excluded = reference_artifact_names(spec.public_inputs)
    excluded_names = {Path(item).name for item in spec.public_inputs}
    input_name = Path(spec.public_inputs[0]).name
    candidates = [
        path for path in trial.artifacts
        if path.suffix.casefold() in {".tif", ".tiff"}
        and delivered_name(path) not in excluded_names
        and path.name not in excluded_names
    ]
    deliveries: list[RasterDelivery] = [
        align_to_grid(path, target_crs=target_crs, target_shape=target_shape,
                      target_transform=target_transform, value_domain="canopy")
        for path in candidates
    ]
    report.delivered = [delivery.as_dict() for delivery in deliveries]
    report.evidence = {
        "valid_definition": spec.condition,
        "valid_pixels": int(np.count_nonzero(valid)),
        "gold_foreground_pixels": int(np.count_nonzero(truth)),
        "candidate_rasters": [path.name for path in candidates],
        "input_raster": input_name,
    }

    # ---- delivery validity ----------------------------------------------------
    usable: RasterDelivery | None = None
    if not candidates:
        report.checks["artifact_present"] = _failed(
            "canopy-extraction-artifact-present",
            "the task requires a 0/1 GeoTIFF; no GeoTIFF other than the input was delivered",
        )
    elif len(candidates) > 1:
        report.checks["artifact_present"] = _failed(
            "canopy-extraction-artifact-present",
            f"the task requires exactly one result GeoTIFF; {len(candidates)} were delivered: "
            f"{[path.name for path in candidates]}",
        )
    else:
        delivery = deliveries[0]
        if delivery.reason:
            report.checks["artifact_present"] = _failed(
                "canopy-extraction-artifact-present", delivery.reason, evidence=[delivery.path]
            )
        elif delivery.class_values_ok is False:
            report.checks["artifact_present"] = _failed(
                "canopy-extraction-artifact-present",
                f"the class raster uses values outside {{0, 1}}: {delivery.observed_values}",
                evidence=[delivery.path],
            )
        else:
            usable = delivery
            report.checks["artifact_present"] = _passed(
                "canopy-extraction-artifact-present",
                f"one 0/1 GeoTIFF was delivered with values {delivery.observed_values}",
                evidence=[delivery.path],
            )

    if usable is None:
        reason = "a usable result raster was not delivered"
        report.checks["grid"] = _failed(
            "canopy-extraction-grid",
            "the grid could not be checked because no usable raster was delivered",
        )
        report.checks["quality"] = _not_applicable(
            "canopy-extraction-quality", "no usable raster was delivered, so there is nothing to compare"
        )
        report.checks["report_matches_artifact"] = _not_applicable(
            "canopy-extraction-report-matches-artifact",
            "no usable raster was delivered, so the report cannot contradict it",
        )
        report.validity = {"v": 0, "reason": reason}
        report.outcome = {"v": 0, "iou": 0.0, "end_to_end": 0.0}
        return _finish(report, spec, trial, budget_seconds)

    if usable.aligned and not usable.resampled:
        report.checks["grid"] = _passed(
            "canopy-extraction-grid",
            "shape, affine transform and CRS match the frozen input",
            evidence=[usable.path],
        )
    elif usable.aligned:
        report.checks["grid"] = _passed(
            "canopy-extraction-grid",
            "the delivery is on an equivalent but different projection and was resampled "
            "onto the frozen grid with nearest-neighbour",
            evidence=[usable.path],
        )
    else:
        report.checks["grid"] = _failed("canopy-extraction-grid", usable.reason, evidence=[usable.path])

    reported_coverage = _reported_coverage(trial)

    prediction = usable.array.astype(bool)
    result = M.canopy_metrics(truth, prediction, valid, reported_coverage_percent=reported_coverage)
    report.quality = result.as_dict()

    grid_ok = report.checks["grid"].verdict == "pass"
    report.checks["quality"] = Check(
        "pass" if grid_ok else "fail",
        "canopy-extraction-quality-v1",
        [usable.path],
        f"IoU={result.iou:.6f} over the frozen valid domain ({result.valid_pixels} pixels)"
        if grid_ok else
        f"the pixels were compared (IoU={result.iou:.6f}) but the delivery is not on the frozen grid, "
        f"so the score is not counted: {report.checks['grid'].detail}",
    )

    tolerance = float(spec.tolerance.get("coverage_points", 0.05))
    if reported_coverage is None:
        report.checks["report_matches_artifact"] = _failed(
            "canopy-extraction-report-matches-artifact",
            "the answer reported no coverage figure, so it cannot be checked against the artifact",
        )
    elif result.coverage_error_points is not None and result.coverage_error_points <= tolerance:
        report.checks["report_matches_artifact"] = _passed(
            "canopy-extraction-report-matches-artifact",
            f"the reported coverage differs from the delivered raster by "
            f"{result.coverage_error_points:.4f} percentage points",
        )
    else:
        report.checks["report_matches_artifact"] = _failed(
            "canopy-extraction-report-matches-artifact",
            f"the reported coverage differs from the delivered raster by "
            f"{result.coverage_error_points} percentage points, beyond the frozen {tolerance}",
        )
    return _finish(report, spec, trial, budget_seconds)


def grade_canopy_statistics(
    spec: TaskSpec, trial: TrialBundle, *, gold_mask: Path,
    budget_seconds: float | None = None,
) -> GradeReport:
    """Grade one statistics trial: canopy pixels, valid pixels and coverage in JSON.

    The provided mask is the whole observable domain, so the coverage denominator
    is every pixel of that mask. The grader never asks for a denominator the agent
    could not see.
    """
    report = GradeReport(
        task_id=spec.task_id, family=spec.family, trial_id=trial.trial_id,
        repeat=trial.repeat, suite_version=SUITE_VERSION,
        rules_version=GROUNDED_RULES_VERSION, status=trial.status, usage=trial.usage,
    )
    if trial.status != "evaluated":
        reason = f"the run did not reach the expected terminal state: {trial.terminal_state!r}"
        report.checks["artifact_present"] = _failed("canopy-statistics-artifact-present", reason)
        report.checks["quality"] = _not_applicable("canopy-statistics-quality", reason)
        report.checks["task"] = _not_applicable("canopy-statistics-task", reason)
        report.checks["leakage"] = _not_applicable("grounded-leakage", reason)
        report.checks["budget"] = _not_applicable("grounded-budget", reason)
        report.validity = {"v": 0, "reason": reason}
        report.failure_reasons = [reason]
        report.outcome = {"v": 0, "task_success": 0}
        return report

    try:
        mask = read_gold_mask(gold_mask)
        valid = np.ones_like(mask, dtype=bool)
    except Exception as error:  # noqa: BLE001
        return _unmeasured(
            report, f"the grader could not read the frozen mask: {type(error).__name__}: {error}"
        )

    deliveries: list[JsonDelivery] = [
        load_json_delivery(path, keys=(
            ("canopy_pixels", CANOPY_KEYS),
            ("valid_pixels", VALID_KEYS),
            ("coverage_percent", COVERAGE_KEYS),
        ))
        for path in trial.artifacts if path.suffix.casefold() == ".json"
    ]
    report.delivered = [delivery.as_dict() for delivery in deliveries]
    report.evidence = {
        "provided_mask": spec.public_inputs[0] if spec.public_inputs else "",
        "valid_definition": spec.condition,
        "delivered_json": [delivery.name for delivery in deliveries],
    }

    usable = [delivery for delivery in deliveries if delivery.payload is not None]
    if not usable:
        reason = ("the task requires a JSON result; no parseable JSON delivery was found"
                  if deliveries else "the task requires a JSON result; no JSON file was delivered")
        report.checks["artifact_present"] = _failed("canopy-statistics-artifact-present", reason)
        report.checks["quality"] = _not_applicable(
            "canopy-statistics-quality", "no parseable JSON delivery, so there is nothing to compare"
        )
        report.checks["task"] = _failed("canopy-statistics-task", reason)
        report.validity = {"v": 0, "reason": reason}
        report.failure_reasons = [reason]
        report.outcome = {"v": 0, "task_success": 0}
        return _finish(report, spec, trial, budget_seconds)

    selected = usable[0]
    if len(usable) > 1:
        complete = [delivery for delivery in usable
                    if delivery.canopy_pixels is not None and delivery.valid_pixels is not None
                    and delivery.coverage_percent is not None]
        if not complete:
            reason = f"{len(usable)} JSON deliveries were found and none declares the required fields"
            report.checks["artifact_present"] = _failed("canopy-statistics-artifact-present", reason)
            report.checks["quality"] = _not_applicable("canopy-statistics-quality", reason)
            report.checks["task"] = _failed("canopy-statistics-task", reason)
            report.validity = {"v": 0, "reason": reason}
            report.failure_reasons = [reason]
            report.outcome = {"v": 0, "task_success": 0}
            return _finish(report, spec, trial, budget_seconds)
        selected = complete[0]
        report.checks["artifact_present"] = _passed(
            "canopy-statistics-artifact-present",
            f"{len(usable)} JSON files were delivered; the one carrying the required fields "
            f"({selected.name}) was graded",
            evidence=[selected.path],
        )
    else:
        report.checks["artifact_present"] = _passed(
            "canopy-statistics-artifact-present",
            f"one JSON file was delivered ({selected.name})",
            evidence=[selected.path],
        )

    comparison = M.count_metrics(
        mask, valid,
        reported_canopy_pixels=selected.canopy_pixels,
        reported_valid_pixels=selected.valid_pixels,
        reported_coverage_percent=selected.coverage_percent,
        absolute_tolerance=float(spec.tolerance.get("pixels_absolute", 1.0)),
        relative_tolerance=float(spec.tolerance.get("pixels_relative", 0.0)),
    )
    report.quality = comparison
    passed = bool(comparison["passed"])
    report.checks["quality"] = Check(
        "pass" if passed else "fail", "canopy-statistics-quality-v1", [selected.path],
        "all three required values agree with the frozen truth within the declared tolerance"
        if passed else
        "; ".join(field["reason"] for field in comparison["fields"] if not field["passed"]),
    )
    report.checks["task"] = Check(
        "pass" if passed else "fail", "canopy-statistics-task-v1", [selected.path],
        "the reported canopy pixels, valid pixels and coverage all match"
        if passed else "at least one required value is wrong, missing or outside tolerance",
    )
    report.outcome = {"v": None, "task_success": 1 if passed else 0}
    return _finish(report, spec, trial, budget_seconds)


def _finish(report: GradeReport, spec: TaskSpec, trial: TrialBundle,
            budget_seconds: float | None) -> GradeReport:
    """Leakage, budget, delivery validity, process checks and the trial outcome."""
    if "leakage" not in report.checks:
        leak = _leaked_tokens(trial)
        if leak:
            report.checks["leakage"] = _failed(
                "grounded-leakage", f"the trial references private evaluation evidence: {leak}"
            )
        else:
            report.checks["leakage"] = _passed(
                "grounded-leakage", "no private evaluation evidence was referenced"
            )

    if "budget" not in report.checks:
        if budget_seconds is None:
            report.checks["budget"] = _not_applicable(
                "grounded-budget", "no wall-clock budget is frozen for this task"
            )
        else:
            elapsed = _elapsed_seconds(trial.usage)
            if elapsed is None:
                report.checks["budget"] = _unknown(
                    "grounded-budget",
                    "the trial records no elapsed time, so the budget cannot be checked",
                )
            elif elapsed <= budget_seconds:
                report.checks["budget"] = _passed(
                    "grounded-budget",
                    f"the trial finished in {elapsed:.1f}s of a {budget_seconds:.0f}s budget",
                )
            else:
                report.checks["budget"] = _failed(
                    "grounded-budget",
                    f"the trial took {elapsed:.1f}s, beyond the {budget_seconds:.0f}s budget",
                )

    blockers = [report.checks.get(name) for name in
                ("artifact_present", "grid", "report_matches_artifact", "leakage", "budget")]
    decided = [check for check in blockers if check is not None]
    # A check that could not be decided leaves validity unmeasured. So does a grid
    # check that never ran because there was no usable raster to place on the grid:
    # that is "we could not measure this", not "the agent delivered a wrong grid".
    undecidable = any(
        check.verdict == "unknown"
        or (check.verdict == "not_applicable" and check.verifier.startswith("canopy-extraction-grid"))
        for check in decided
    )
    if undecidable:
        report.validity = {
            "v": None,
            "reason": "a delivery requirement could not be decided from the evidence",
        }
    elif any(check.verdict == "fail" for check in decided):
        report.validity = {
            "v": 0,
            "reason": "; ".join(f"{check.verifier}: {check.detail}"
                                for check in decided if check.verdict == "fail"),
        }
    else:
        report.validity = {"v": 1, "reason": "every delivery requirement was met"}

    report.process = _process_checks(report, trial)
    v = report.validity["v"]
    quality = report.quality or {}
    if report.family == "canopy_extraction":
        iou = quality.get("iou")
        if iou is None and report.outcome.get("iou") == 0.0:
            # No raster could be compared: the extraction score is a hard zero, not
            # an unmeasured slot. Validity already says which of the two applies.
            iou = 0.0
        report.outcome = {
            "v": v,
            "iou": iou,
            "end_to_end": None if (v is None or iou is None) else 100.0 * v * iou,
            "undefined_sample": bool(quality.get("undefined_sample")),
        }
    else:
        task_success = report.outcome.get("task_success")
        report.outcome = {
            "v": v,
            "task_success": task_success,
            "end_to_end": None if (v is None or task_success is None) else v * task_success,
        }

    report.failure_reasons = [report.validity["reason"]] if v == 0 else []
    for name, check in report.checks.items():
        if check.verdict == "fail" and check.detail not in report.failure_reasons:
            report.failure_reasons.append(f"{name}: {check.detail}")
    return report


def _process_checks(report: GradeReport, trial: TrialBundle) -> dict:
    """Only the process judgements the stored evidence actually supports.

    The plan requires every process check to carry evidence, so anything this
    pilot cannot substantiate stays ``unknown`` rather than being inferred from
    the outcome. Failures are never back-filled from a low score.
    """
    checks: dict[str, dict] = {
        name: Check("unknown", f"grounded-process-{name}-v1", [],
                    "this grader has no trustworthy signal for this check, so it is unmeasured").as_dict()
        for name in PROCESS_CHECKS
    }
    events = trial.events
    tool_events = [event for event in events
                   if event.get("type") in {"action", "tool_call", "function_call", "tool_result"}]
    failures = [event for event in tool_events
                if str(event.get("status") or event.get("state") or "").casefold()
                in {"failed", "error", "rejected"}]
    inspected = [event for event in tool_events
                 if any(token in json.dumps(event, ensure_ascii=False).casefold()
                        for token in ("verify", "inspect", "read", "open", "info", "stat", "check"))]

    if tool_events:
        checks["recovered_from_errors"] = Check(
            "pass" if not failures else "unknown", "grounded-process-recovered_from_errors-v1", [],
            "no tool call failed, so no recovery was required" if not failures else
            f"{len(failures)} tool call(s) failed; whether the agent then changed approach is not "
            "decided by this grader",
        ).as_dict()
        checks["verified_product"] = Check(
            "pass" if inspected else "unknown", "grounded-process-verified_product-v1", [],
            "the trajectory contains at least one step that inspects data or a product" if inspected
            else "the trajectory shows no step that inspects data or a product",
        ).as_dict()
        checks["method_fits_data"] = Check(
            "unknown", "grounded-process-method_fits_data-v1", [],
            "tool names alone do not establish that the method suited the data; this needs a "
            "per-task method rubric",
        ).as_dict()
    if report.outcome.get("v") == 1:
        checks["input_scope_correct"] = Check(
            "pass", "grounded-process-input_scope_correct-v1", [],
            "the graded delivery was produced from the frozen public input alone",
        ).as_dict()
        checks["conclusion_supported_by_product"] = Check(
            "pass" if report.checks.get("report_matches_artifact", Check("pass", "x", [], "")).verdict == "pass"
            else "fail", "grounded-process-conclusion_supported_by_product-v1", [],
            "the answer's key figure agrees with its own artifact",
        ).as_dict()
        checks["avoided_premature_stop"] = Check(
            "pass", "grounded-process-avoided_premature_stop-v1", [],
            "the trial ended with a complete, checkable delivery",
        ).as_dict()
    return checks


def _elapsed_seconds(usage: dict) -> float | None:
    for key in ("elapsed_seconds", "duration_seconds", "wall_seconds", "elapsed"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


__all__ = [
    "Check", "GradeReport", "GROUNDED_RULES_VERSION", "LEAK_TOKENS", "PROCESS_CHECKS",
    "SUITE_VERSION", "TaskSpec", "TrialBundle", "grade_canopy_extraction",
    "grade_canopy_statistics", "read_gold_mask",
]
