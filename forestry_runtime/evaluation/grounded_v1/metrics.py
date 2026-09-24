"""Deterministic quality metrics for the grounded canopy tasks.

Everything here is a pure function of arrays and frozen numbers, so a metric can
be recomputed from stored evidence and cannot drift with the runtime. The rules
that decide *what counts as a valid delivery* live in ``grading.py``; this module
only measures.

Conventions frozen with the question bank (``grounded-v1.1``):

* **Valid domain** -- a pixel is valid when at least one RGB band of the input is
  non-zero. Pixels that are zero in all bands carry no observation. The coverage
  denominator is the valid-pixel count, and the full-tile figure is reported next
  to it so the two are never confused.
* **Foreground** -- non-zero canopy. The annotation encodes classes as RGB
  colours: black ``(0, 0, 0)`` is background and every other colour is tree
  canopy. Anti-aliased boundary pixels exist, so the frozen binarisation rule is
  "any channel non-zero is canopy" rather than an exact colour match.
* **Empty foreground** -- both truth and prediction have no foreground: IoU is
  ``1`` by the frozen convention and the sample is reported separately, because a
  perfect score on an empty tile is not evidence of extraction ability. Truth has
  no foreground but the prediction does: IoU is ``0``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPSILON = 1e-9


@dataclass(frozen=True)
class ConfusionCounts:
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def support(self) -> int:
        return self.true_positive + self.false_negative

    @property
    def predicted(self) -> int:
        return self.true_positive + self.false_positive

    def as_dict(self) -> dict:
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "true_negative": self.true_negative,
            "truth_foreground_pixels": self.support,
            "predicted_foreground_pixels": self.predicted,
        }


@dataclass(frozen=True)
class CanopyMetrics:
    """IoU and its companions over a frozen valid domain."""

    counts: ConfusionCounts
    iou: float
    precision: float | None
    recall: float | None
    f1: float | None
    truth_empty: bool
    prediction_empty: bool
    valid_pixels: int
    evaluated_pixels: int
    coverage_error_points: float | None

    @property
    def undefined(self) -> bool:
        """Both sides empty: the sample carries no extraction signal."""
        return self.truth_empty and self.prediction_empty

    def as_dict(self) -> dict:
        return {
            "iou": self.iou,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "truth_foreground_empty": self.truth_empty,
            "prediction_foreground_empty": self.prediction_empty,
            "undefined_sample": self.undefined,
            "valid_pixels": self.valid_pixels,
            "evaluated_pixels": self.evaluated_pixels,
            "coverage_error_points": self.coverage_error_points,
            **self.counts.as_dict(),
        }


def confusion(truth: np.ndarray, prediction: np.ndarray, valid: np.ndarray) -> ConfusionCounts:
    """Confusion counts on the valid domain. Both inputs must already be boolean.

    Defined so that no pixel outside the valid domain can reach any cell:

    * FN -- truth foreground on a valid pixel that the prediction missed;
    * FP -- predicted foreground on a valid pixel that is not truth;
    * TN -- a valid pixel that is neither.
    """
    if truth.shape != prediction.shape or truth.shape != valid.shape:
        raise ValueError("Truth, prediction and valid mask must share one grid")
    domain = valid.astype(bool)
    actual = np.logical_and(truth, domain)
    guessed = np.logical_and(prediction, domain)
    return ConfusionCounts(
        true_positive=int(np.count_nonzero(actual & guessed)),
        false_positive=int(np.count_nonzero(guessed & ~actual)),
        false_negative=int(np.count_nonzero(actual & ~guessed)),
        true_negative=int(np.count_nonzero(domain & ~actual & ~guessed)),
    )


def iou_from_counts(counts: ConfusionCounts) -> float:
    union = counts.true_positive + counts.false_positive + counts.false_negative
    if union == 0:
        return 1.0
    return counts.true_positive / union


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def canopy_metrics(
    truth: np.ndarray, prediction: np.ndarray, valid: np.ndarray,
    *, reported_coverage_percent: float | None = None,
) -> CanopyMetrics:
    """Grade one canopy raster against the frozen truth.

    ``reported_coverage_percent`` is the figure the answer claims, if any; the
    absolute error in percentage points is returned so a report that contradicts
    its own artifact is visible rather than silently averaged away.
    """
    counts = confusion(truth, prediction, valid)
    precision = _ratio(counts.true_positive, counts.predicted)
    recall = _ratio(counts.true_positive, counts.support)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)

    valid_pixels = int(np.count_nonzero(valid))
    truth_pixels = counts.support
    predicted_pixels = counts.predicted
    if reported_coverage_percent is None or valid_pixels == 0:
        coverage_error = None
    else:
        actual = 100.0 * predicted_pixels / valid_pixels
        coverage_error = abs(float(reported_coverage_percent) - actual)

    return CanopyMetrics(
        counts=counts,
        iou=iou_from_counts(counts),
        precision=precision,
        recall=recall,
        f1=f1,
        truth_empty=truth_pixels == 0,
        prediction_empty=predicted_pixels == 0,
        valid_pixels=valid_pixels,
        evaluated_pixels=int(np.count_nonzero(valid)),
        coverage_error_points=coverage_error,
    )


def count_metrics(
    truth_foreground: np.ndarray, valid: np.ndarray,
    *, reported_canopy_pixels: float | None, reported_valid_pixels: float | None,
    reported_coverage_percent: float | None, absolute_tolerance: float,
    relative_tolerance: float,
) -> dict:
    """Grade the JSON count task: canopy pixels, valid pixels, coverage percent.

    Each reported number is compared with
    ``|reported - truth| <= max(absolute_tolerance, relative_tolerance * |truth|)``;
    the tolerance is frozen with the task before any run, never chosen after
    seeing a result. A missing or non-numeric field is a failure with that reason,
    not a zero error.
    """
    truth_canopy = int(np.count_nonzero(np.logical_and(truth_foreground, valid)))
    truth_valid = int(np.count_nonzero(valid))
    truth_coverage = 0.0 if truth_valid == 0 else 100.0 * truth_canopy / truth_valid

    def one(name: str, reported: float | None, expected: float) -> dict:
        allowed = max(absolute_tolerance, relative_tolerance * abs(expected))
        if reported is None:
            return {"field": name, "reported": None, "expected": expected,
                    "absolute_error": None, "allowed": allowed, "passed": False,
                    "reason": "the field is missing or not numeric"}
        error = abs(float(reported) - expected)
        passed = error <= allowed
        return {
            "field": name, "reported": float(reported), "expected": expected,
            "absolute_error": error, "allowed": allowed, "passed": passed,
            "reason": "" if passed else f"absolute error {error} exceeds the allowed {allowed}",
        }

    fields = [
        one("canopy_pixels", reported_canopy_pixels, float(truth_canopy)),
        one("valid_pixels", reported_valid_pixels, float(truth_valid)),
        one("coverage_percent", reported_coverage_percent, truth_coverage),
    ]
    return {
        "truth": {
            "canopy_pixels": truth_canopy,
            "valid_pixels": truth_valid,
            "coverage_percent": truth_coverage,
        },
        "tolerance": {"absolute": absolute_tolerance, "relative": relative_tolerance},
        "fields": fields,
        "passed": all(field["passed"] for field in fields),
    }


__all__ = [
    "CanopyMetrics", "ConfusionCounts", "canopy_metrics", "confusion",
    "count_metrics", "iou_from_counts",
]
