"""Counterexample tests for the grounded grader.

The plan requires the grader to be validated with adversarial examples before the
pilot: a shifted mask, a wrong CRS, an all-zero mask, a wrong class encoding, a
wrong denominator, a missing file, reused stale output, and a report that
contradicts its own artifact must all be caught -- and a correct alternative
execution path must not be punished.

These tests build synthetic trials on disk and grade them. No Runtime code is
imported, and nothing here needs a model.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from evaluation.grounded_v1 import metrics as M
from evaluation.grounded_v1.grading import (
    Check, GradeReport, TaskSpec, TrialBundle, grade_canopy_extraction,
    grade_canopy_statistics,
)

HEIGHT, WIDTH = 64, 64
TRANSFORM = from_origin(500000.0, 6000000.0, 10.0, 10.0)
CRS_UTM = CRS.from_epsg(32623)


def _write_raster(path: Path, array: np.ndarray, *, crs=CRS_UTM, transform=TRANSFORM,
                  dtype: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    dtype = dtype or ("uint8" if array.dtype == np.uint8 else "float32")
    with rasterio.open(path, "w", driver="GTiff", height=array.shape[0], width=array.shape[1],
                       count=1, dtype=dtype, crs=crs, transform=transform) as destination:
        destination.write(array.astype(dtype), 1)
    return path


def _truth() -> np.ndarray:
    truth = np.zeros((HEIGHT, WIDTH), dtype=bool)
    truth[8:40, 8:40] = True
    truth[50:60, 45:55] = True
    return truth


def _input_stack(path: Path, *, valid: np.ndarray | None = None) -> Path:
    """A 3-band RGB input; pixels outside ``valid`` are zero in all bands."""
    valid = np.ones((HEIGHT, WIDTH), dtype=bool) if valid is None else valid
    stack = np.zeros((3, HEIGHT, WIDTH), dtype=np.uint8)
    stack[:, valid] = 120
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=HEIGHT, width=WIDTH, count=3,
                       dtype="uint8", crs=CRS_UTM, transform=TRANSFORM) as destination:
        destination.write(stack)
    return path


def _trial(tmp_path: Path, *, artifacts: list[Path], answer: str = "", terminal: str = "completed",
           usage: dict | None = None, events: list[dict] | None = None) -> Path:
    package = tmp_path / "trial"
    (package / "artifacts").mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        target = package / "artifacts" / f"asset_{abs(hash(artifact.name)) % 1000}-{artifact.name}"
        target.write_bytes(artifact.read_bytes())
    (package / "trace.json").write_text(json.dumps({
        "run_id": "run-test", "case_id": "grounded", "repeat": 1,
        "configuration": {"model": "test"}, "terminal_state": terminal,
        "usage": usage or {},
    }), encoding="utf-8")
    (package / "raw").mkdir(parents=True, exist_ok=True)
    (package / "raw" / "events.json").write_text(json.dumps(
        events if events is not None else [
            {"type": "message", "content": answer},
            {"type": "action", "name": "code_run", "status": "ok"},
            {"type": "action", "name": "verify_output", "status": "ok"},
        ]
    ), encoding="utf-8")
    return package


def _extraction_spec(tmp_path: Path, *, input_path: Path) -> TaskSpec:
    return TaskSpec(
        task_id="oam-01", family="canopy_extraction", question="提取树冠",
        public_root=str(input_path.parent), public_inputs=[input_path.name],
        required_deliverables=["a 0/1 GeoTIFF"],
        grading_rule=["IoU on the frozen valid domain"],
        condition="rgb-not-all-zero", budget={"wall_seconds": 900},
        resources={}, tolerance={"coverage_points": 0.05},
    )


def _statistics_spec(tmp_path: Path, *, mask_path: Path) -> TaskSpec:
    """The declared public input *is* the provided canopy mask.

    It is the same file the grader reads as truth: for this family the mask is the
    whole observable domain, so the agent's input and the frozen truth coincide by
    construction. No other truth exists for these tasks.
    """
    return TaskSpec(
        task_id="oam-07", family="canopy_statistics", question="统计树冠",
        public_root=str(mask_path.parent), public_inputs=[mask_path.name],
        required_deliverables=["a JSON object"],
        grading_rule=["counts within tolerance"],
        condition="given-mask-tile", budget={"wall_seconds": 900},
        resources={}, tolerance={"pixels_absolute": 1.0, "pixels_relative": 0.0},
    )


def _statistics_mask(tmp_path: Path) -> Path:
    """Write the provided mask to both the public and the private root."""
    truth = _truth()
    mask_path = _write_raster(tmp_path / "public" / "oam-07-input.tif", truth.astype(np.uint8))
    _write_raster(tmp_path / "private" / "oam-07-gold.tif", truth.astype(np.uint8))
    return mask_path


# --------------------------------------------------------------------------- #
# The correct case, so the counterexamples mean something
# --------------------------------------------------------------------------- #

def test_perfect_extraction_passes_with_iou_one(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))
    report_json = tmp_path / "out" / "report.json"
    coverage = 100.0 * _truth().sum() / _truth().size
    report_json.write_text(json.dumps({"coverage_percent": coverage}), encoding="utf-8")

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered, report_json]), case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.validity["v"] == 1, report.validity
    assert report.quality["iou"] == pytest.approx(1.0)
    assert report.outcome["end_to_end"] == pytest.approx(100.0)
    assert report.checks["grid"].verdict == "pass"


def test_alternative_execution_path_is_not_punished(tmp_path: Path) -> None:
    """A correct result on the same grid but produced and named differently must pass."""
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    # float32 delivery instead of uint8, a different file name, and the coverage
    # stated only in the message text.
    delivered = _write_raster(tmp_path / "out" / "my_result_raster.tif", _truth().astype(np.float32))
    coverage = 100.0 * _truth().sum() / _truth().size
    answer = tmp_path / "out" / "answer.txt"
    answer.write_text(f"树冠覆盖率约为 {coverage:.2f}%", encoding="utf-8")

    bundle = TrialBundle.load(
        _trial(tmp_path, artifacts=[delivered], answer=f"树冠覆盖率约为 {coverage:.2f}%"),
        case_id="grounded",
    )
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["artifact_present"].verdict == "pass"
    assert report.checks["grid"].verdict == "pass"
    assert report.validity["v"] == 1
    assert report.quality["iou"] == pytest.approx(1.0)
    # The written figure is rounded to two decimals, which the frozen tolerance allows.
    assert report.checks["report_matches_artifact"].verdict == "pass"


# --------------------------------------------------------------------------- #
# Counterexamples: every one must be caught
# --------------------------------------------------------------------------- #

def test_shifted_mask_is_caught(tmp_path: Path) -> None:
    """A mask moved off the truth keeps the right area but not the right place."""
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    shifted = np.zeros_like(_truth(), dtype=np.uint8)
    shifted[6:14, 6:14] = 1  # same area as the truth block, moved two pixels
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", shifted)
    honest_figure = 100.0 * shifted.sum() / shifted.size

    bundle = TrialBundle.load(
        _trial(tmp_path, artifacts=[delivered], answer=f"覆盖率 {honest_figure:.2f}%"),
        case_id="grounded",
    )
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.quality["iou"] < 0.5
    assert report.checks["quality"].verdict == "pass"       # the comparison ran
    assert report.checks["report_matches_artifact"].verdict == "pass"  # and the figure is honest
    assert report.outcome["end_to_end"] < 50.0              # the result is what fails


def test_grossly_wrong_coverage_figure_invalidates_delivery(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 99.9%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.quality["iou"] == pytest.approx(1.0)
    assert report.checks["report_matches_artifact"].verdict == "fail"
    assert report.validity["v"] == 0


def test_wrong_crs_is_caught(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8),
                              crs=CRS.from_epsg(4326))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["grid"].verdict != "pass"
    assert report.validity["v"] == 0
    # No pixel comparison is recorded: the delivery was never placed on the grid,
    # so scoring it would mean scoring a raster that is not on this task's grid.
    assert report.quality == {}
    assert report.checks["quality"].verdict == "not_applicable"
    assert report.outcome["end_to_end"] == 0.0


def test_offset_grid_is_caught(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    moved = from_origin(500050.0, 6000000.0, 10.0, 10.0)
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8),
                              transform=moved)

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["grid"].verdict == "fail"
    assert report.validity["v"] == 0


def test_all_zero_mask_scores_zero_iou(tmp_path: Path) -> None:
    """An empty delivery on a non-empty sample is a wrong result.

    Delivery validity stays 1 here: the file is present, on the right grid and in
    the right encoding, and the answer's own figure agrees with it. The failure is
    the result, and the end-to-end score carries it.
    """
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", np.zeros((HEIGHT, WIDTH), dtype=np.uint8))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 0%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.quality["iou"] == pytest.approx(0.0)
    assert report.quality["recall"] == pytest.approx(0.0)
    assert report.validity["v"] == 1
    assert report.checks["quality"].verdict == "pass"          # the comparison ran correctly
    assert report.checks["report_matches_artifact"].verdict == "pass"
    assert report.outcome["end_to_end"] == pytest.approx(0.0)  # and the result is worth nothing


def test_wrong_class_encoding_is_caught(tmp_path: Path) -> None:
    """A 0/255 mask is a common encoding, and it is not the frozen contract."""
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", (_truth() * 255).astype(np.uint8))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["artifact_present"].verdict == "fail"
    assert report.validity["v"] == 0
    assert "0, 1" in report.checks["artifact_present"].detail


def test_nodata_marker_is_caught(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = tmp_path / "out" / "canopy.tif"
    delivered.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(delivered, "w", driver="GTiff", height=HEIGHT, width=WIDTH, count=1,
                       dtype="uint8", crs=CRS_UTM, transform=TRANSFORM, nodata=255) as destination:
        destination.write(_truth().astype(np.uint8), 1)

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["artifact_present"].verdict == "fail"
    assert "nodata" in report.checks["artifact_present"].detail


def test_missing_delivery_is_caught(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    keep = tmp_path / "out" / "notes.txt"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("I decided not to deliver a raster", encoding="utf-8")

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[keep], answer="完成"), case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.validity["v"] == 0
    assert report.outcome["end_to_end"] == 0.0
    assert "no GeoTIFF" in report.checks["artifact_present"].detail


def test_input_echo_is_not_counted_as_a_delivery(tmp_path: Path) -> None:
    """Uploading the fixture back is not an answer."""
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    echo = _write_raster(tmp_path / "out" / input_path.name, _truth().astype(np.uint8))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[echo], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.validity["v"] == 0
    assert "no GeoTIFF other than the input" in report.checks["artifact_present"].detail


def test_stale_reused_output_is_caught_by_the_report_check(tmp_path: Path) -> None:
    """A raster from another sample cannot match this sample's reported numbers."""
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    stale = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    stale[0:8, 0:8] = 1  # a small block somewhere else entirely
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", stale)
    report_json = tmp_path / "out" / "report.json"
    report_json.write_text(json.dumps({"coverage_percent": 41.02}), encoding="utf-8")

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered, report_json], answer="覆盖率 41.02%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["report_matches_artifact"].verdict == "fail"
    assert report.validity["v"] == 0


def test_report_contradicting_artifact_is_caught(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 99.9%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["quality"].verdict == "pass"
    assert report.checks["report_matches_artifact"].verdict == "fail"
    assert report.validity["v"] == 0


def test_two_rasters_is_ambiguous_and_fails_delivery(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    first = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))
    second = _write_raster(tmp_path / "out" / "canopy_v2.tif", _truth().astype(np.uint8))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[first, second], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.validity["v"] == 0
    assert "exactly one" in report.checks["artifact_present"].detail


def test_test_label_leak_invalidates_delivery(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))
    leak = tmp_path / "out" / "notes.json"
    leak.write_text(json.dumps({"source": "grounded_v1_1_private/oam-01-gold.tif"}), encoding="utf-8")

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered, leak], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.checks["leakage"].verdict == "fail"
    assert report.validity["v"] == 0


def test_incomplete_run_scores_zero_not_unknown(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], terminal="timeout"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.status == "timeout"
    assert report.validity["v"] == 0
    assert report.outcome["end_to_end"] == 0.0


def test_unreadable_trace_is_unknown_not_a_failure(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    package = tmp_path / "broken"
    (package / "artifacts").mkdir(parents=True, exist_ok=True)

    bundle = TrialBundle.load(package, case_id="grounded")
    assert bundle.status == "infra_error"

    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)
    assert report.validity["v"] == 0
    assert report.outcome["end_to_end"] == 0.0


def test_grader_side_failure_is_unknown_and_leaves_the_denominator(tmp_path: Path) -> None:
    """A missing truth file is the grader's problem, never the agent's failure."""
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))
    missing_gold = tmp_path / "private" / "does-not-exist.tif"

    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[delivered], answer="覆盖率 41%"),
                              case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=missing_gold)

    assert report.validity["v"] is None
    assert report.outcome["end_to_end"] is None
    assert report.checks["quality"].verdict == "unknown"


def test_budget_overrun_fails_delivery(tmp_path: Path) -> None:
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", _truth().astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", _truth().astype(np.uint8))
    bundle = TrialBundle.load(
        _trial(tmp_path, artifacts=[delivered], answer="覆盖率 41%",
               usage={"elapsed_seconds": 5000.0}), case_id="grounded")
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold, budget_seconds=900.0)
    assert report.checks["budget"].verdict == "fail"
    assert report.validity["v"] == 0


# --------------------------------------------------------------------------- #
# Statistics family
# --------------------------------------------------------------------------- #

def _statistics_trial(tmp_path: Path, payload: dict, *, answer: str = "") -> Path:
    result = tmp_path / "out" / "stats.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(payload), encoding="utf-8")
    return _trial(tmp_path, artifacts=[result], answer=answer)


def test_correct_statistics_pass(tmp_path: Path) -> None:
    truth = _truth()
    mask_path = _statistics_mask(tmp_path)
    canopy = int(truth.sum())
    valid = truth.size
    payload = {"canopy_pixels": canopy, "valid_pixels": valid,
               "coverage_percent": round(100.0 * canopy / valid, 4)}
    bundle = TrialBundle.load(_statistics_trial(tmp_path, payload), case_id="grounded")
    report = grade_canopy_statistics(_statistics_spec(tmp_path, mask_path=mask_path),
                                     bundle, gold_mask=tmp_path / "private" / "oam-07-gold.tif")
    assert report.checks["task"].verdict == "pass"
    assert report.outcome["task_success"] == 1


def test_wrong_denominator_is_caught(tmp_path: Path) -> None:
    """A denominator that excludes the no-observation pixels is not observable here."""
    truth = _truth()
    mask_path = _statistics_mask(tmp_path)
    canopy = int(truth.sum())
    shrunk = canopy  # e.g. counting only the foreground as the domain
    payload = {"canopy_pixels": canopy, "valid_pixels": shrunk,
               "coverage_percent": 100.0}
    bundle = TrialBundle.load(_statistics_trial(tmp_path, payload), case_id="grounded")
    report = grade_canopy_statistics(_statistics_spec(tmp_path, mask_path=mask_path),
                                     bundle, gold_mask=tmp_path / "private" / "oam-07-gold.tif")
    assert report.checks["task"].verdict == "fail"
    assert report.outcome["task_success"] == 0
    failed = [field["field"] for field in report.quality["fields"] if not field["passed"]]
    assert "valid_pixels" in failed and "coverage_percent" in failed


def test_off_by_one_pixel_is_within_the_frozen_tolerance(tmp_path: Path) -> None:
    truth = _truth()
    mask_path = _statistics_mask(tmp_path)
    canopy = int(truth.sum())
    valid = truth.size
    payload = {"canopy_pixels": canopy + 1, "valid_pixels": valid,
               "coverage_percent": round(100.0 * (canopy + 1) / valid, 4)}
    bundle = TrialBundle.load(_statistics_trial(tmp_path, payload), case_id="grounded")
    report = grade_canopy_statistics(_statistics_spec(tmp_path, mask_path=mask_path),
                                     bundle, gold_mask=tmp_path / "private" / "oam-07-gold.tif")
    assert report.checks["task"].verdict == "pass"


def test_missing_statistics_field_is_a_failure_not_a_zero(tmp_path: Path) -> None:
    truth = _truth()
    mask_path = _statistics_mask(tmp_path)
    payload = {"canopy_pixels": int(truth.sum()), "coverage_percent": 20.0}
    bundle = TrialBundle.load(_statistics_trial(tmp_path, payload), case_id="grounded")
    report = grade_canopy_statistics(_statistics_spec(tmp_path, mask_path=mask_path),
                                     bundle, gold_mask=tmp_path / "private" / "oam-07-gold.tif")
    assert report.checks["task"].verdict == "fail"
    detail = report.quality["fields"][1]
    assert detail["field"] == "valid_pixels" and detail["reported"] is None


def test_statistics_accepts_nested_and_percent_formatted_values(tmp_path: Path) -> None:
    truth = _truth()
    mask_path = _statistics_mask(tmp_path)
    canopy = int(truth.sum())
    valid = truth.size
    payload = {
        "result": {
            "树冠像元数": canopy,
            "有效像元数": f"{valid}",
            "覆盖率": f"{100.0 * canopy / valid:.4f}%",
        }
    }
    bundle = TrialBundle.load(_statistics_trial(tmp_path, payload), case_id="grounded")
    report = grade_canopy_statistics(_statistics_spec(tmp_path, mask_path=mask_path),
                                     bundle, gold_mask=tmp_path / "private" / "oam-07-gold.tif")
    assert report.checks["task"].verdict == "pass", report.quality


def test_statistics_without_json_fails(tmp_path: Path) -> None:
    truth = _truth()
    mask_path = _statistics_mask(tmp_path)
    other = tmp_path / "out" / "notes.txt"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("coverage is about 40 percent", encoding="utf-8")
    bundle = TrialBundle.load(_trial(tmp_path, artifacts=[other], answer="完成"), case_id="grounded")
    report = grade_canopy_statistics(_statistics_spec(tmp_path, mask_path=mask_path),
                                     bundle, gold_mask=tmp_path / "private" / "oam-07-gold.tif")
    assert report.checks["task"].verdict == "fail"
    assert report.outcome["task_success"] == 0


# --------------------------------------------------------------------------- #
# Metric edge cases that the report must keep distinguishable
# --------------------------------------------------------------------------- #

def test_empty_foreground_on_both_sides_scores_one_but_is_flagged() -> None:
    empty = np.zeros((8, 8), dtype=bool)
    valid = np.ones((8, 8), dtype=bool)
    result = M.canopy_metrics(empty, empty, valid)
    assert result.iou == 1.0
    assert result.undefined is True
    assert result.precision is None
    assert result.recall is None


def test_only_prediction_has_foreground_scores_zero() -> None:
    empty = np.zeros((8, 8), dtype=bool)
    guessed = np.zeros((8, 8), dtype=bool)
    guessed[0, 0] = True
    result = M.canopy_metrics(empty, guessed, np.ones((8, 8), dtype=bool))
    assert result.iou == 0.0
    assert result.undefined is False


def test_invalid_pixels_are_excluded_from_the_iou_domain() -> None:
    truth = np.ones((4, 4), dtype=bool)
    prediction = np.zeros((4, 4), dtype=bool)
    valid = np.zeros((4, 4), dtype=bool)
    valid[0, 0] = True  # a single valid pixel, where truth has foreground and the prediction missed it
    result = M.canopy_metrics(truth, prediction, valid)
    assert result.counts.false_negative == 1
    assert result.counts.true_negative == 0
    assert result.counts.true_positive == 0
    assert result.counts.false_positive == 0
    assert result.valid_pixels == 1
    assert result.iou == 0.0

    # Now the valid pixel is background, which the prediction correctly left empty.
    background = np.zeros((4, 4), dtype=bool)
    result = M.canopy_metrics(background, background, valid)
    assert result.counts.true_negative == 1
    assert result.undefined is True  # empty foreground on both sides, flagged separately


def test_check_requires_a_reason_for_every_non_pass_verdict() -> None:
    with pytest.raises(ValueError):
        Check("fail", "x-v1", [], "")
    with pytest.raises(ValueError):
        Check("unknown", "x-v1", [], "")
    Check("pass", "x-v1", [], "")


