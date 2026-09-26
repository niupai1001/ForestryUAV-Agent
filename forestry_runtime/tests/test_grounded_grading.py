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

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from evaluation.grounded_v1 import grading
from evaluation.grounded_v1 import metrics as M
from evaluation.grounded_v1.gabench_gold import gold_entry
from evaluation.grounded_v1.grading import (
    GROUNDED_RULES_VERSION, Check, GradeReport, TaskSpec, TrialBundle,
    grade_canopy_extraction, grade_canopy_statistics, grade_gis_analysis,
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
    # Indexed, not hashed: ``hash()`` of a string is salted per process, so a
    # hashed name would make these fixtures differ between two runs of the same
    # test and hide the very non-determinism the reproducibility test looks for.
    for index, artifact in enumerate(artifacts):
        target = package / "artifacts" / f"asset_{index:03d}-{artifact.name}"
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
# GIS family: ID 12 (ruggedness raster) and ID 9 (deforestation ratio)
# --------------------------------------------------------------------------- #

#: The two figures the published GABench task actually freezes. The second is the
#: route through ``shapely.make_valid()``, which repairs the invalid polygon a
#: different way; the frozen tolerance is deliberately wide enough to cover it.
GOLD_RATE = 0.4793110356552816
MAKE_VALID_RATE = 0.47930037874086123


def _ruggedness_dem() -> np.ndarray:
    """A deterministic 8-bit elevation surface standing in for Elevation.tif."""
    rows = np.arange(HEIGHT, dtype=np.float64)[:, None]
    cols = np.arange(WIDTH, dtype=np.float64)[None, :]
    dem = 30.0 + 12.0 * np.sin(rows / 7.0) + 9.0 * np.cos(cols / 5.0) + (rows * cols) % 37
    return np.clip(dem, 0, 255).astype(np.uint8)


def _neighbourhood_range(dem: np.ndarray) -> np.ndarray:
    """The 3x3 neighbourhood range the task asks for, edges by replication."""
    padded = np.pad(dem.astype(np.float64), 1, mode="edge")
    windows = np.stack([padded[i:i + HEIGHT, j:j + WIDTH] for i in range(3) for j in range(3)])
    return windows.max(axis=0) - windows.min(axis=0)


def _ruggedness_fixture(tmp_path: Path, monkeypatch) -> tuple[TaskSpec, dict, np.ndarray]:
    """Freeze a synthetic truth beside a synthetic input, as the real task does.

    The grader anchors every frozen path to ``RUNTIME_ROOT``, so the test points
    that root at the temporary directory and freezes the truth as a relative
    path with its digest, exactly as ``gabench_gold.py`` does.
    """
    dem = _ruggedness_dem()
    _write_raster(tmp_path / "public" / "Elevation.tif", dem)
    gold_array = _neighbourhood_range(dem)
    gold_raster = _write_raster(tmp_path / "private" / "gold" / "ruggedness.tif",
                                 gold_array.astype(np.float32))
    monkeypatch.setattr(grading, "RUNTIME_ROOT", tmp_path)
    spec = TaskSpec(
        task_id="gabench-12-terrain-ruggedness", family="gis_analysis",
        question="基于 Elevation.tif 计算地形起伏度",
        public_root=str(tmp_path / "public"), public_inputs=["Elevation.tif"],
        required_deliverables=["a GeoTIFF of terrain ruggedness on the input grid"],
        grading_rule=["every pixel of the delivered raster equals the reference"],
        condition="given-dem", budget={"wall_seconds": 900},
        resources={"python": ["rasterio", "numpy", "scipy"], "network": "not required"},
        tolerance={"pixels_absolute": 0.0},
        process_rubric={
            "method_markers": [
                ["generic_filter", "maximum_filter", "minimum_filter", "uniform_filter",
                 "neighborhood", "neighbourhood", "sliding_window", "rolling"],
                ["range", "ptp", "maximum", "minimum", "max", "min", "amax", "amin"],
            ],
            "key_parameters": [
                {"name": "neighbourhood_window_3x3",
                 "tokens": ["3x3", "3×3", "(3, 3)", "(3,3)", "3, 3", "size=3"]},
            ],
        },
    )
    gold = {
        "kind": "gis_raster", "gold_raster": "private/gold/ruggedness.tif",
        "gold_raster_sha256": hashlib.sha256(gold_raster.read_bytes()).hexdigest(),
    }
    return spec, gold, gold_array


def _ruggedness_trial(tmp_path: Path, artifacts: list[Path], *, answer: str = "完成",
                      terminal: str = "completed", events: list[dict] | None = None) -> Path:
    return _trial(tmp_path, artifacts=artifacts, answer=answer, terminal=terminal, events=events)


def test_ruggedness_matching_the_reference_passes(tmp_path: Path, monkeypatch) -> None:
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32))

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.checks["artifact_present"].verdict == "pass"
    assert report.checks["grid"].verdict == "pass"
    assert report.checks["task"].verdict == "pass"
    assert report.quality["mismatched_pixels"] == 0
    assert report.quality["compared_pixels"] == HEIGHT * WIDTH
    assert report.quality["max_abs_difference"] == pytest.approx(0.0)
    assert report.validity["v"] == 1
    assert report.outcome["end_to_end"] == 1
    # The grader records which truth it graded against, digest included.
    assert report.evidence["gold_raster_sha256"] == gold["gold_raster_sha256"]


def test_ruggedness_with_wrong_pixels_fails_the_result_not_the_delivery(
    tmp_path: Path, monkeypatch,
) -> None:
    """A wrong surface is a wrong answer; it is still a valid delivery."""
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    wrong = gold_array.astype(np.float32).copy()
    wrong[0, 0] += 1.0
    wrong[10, 10] += 4.0
    wrong[63, 63] -= 2.0
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", wrong)

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.checks["task"].verdict == "fail"
    assert report.quality["mismatched_pixels"] == 3
    assert report.quality["max_abs_difference"] == pytest.approx(4.0)
    assert report.validity["v"] == 1          # delivered properly, on the right grid
    assert report.outcome["task_success"] == 0
    assert report.outcome["end_to_end"] == 0


def test_ruggedness_on_another_grid_fails_delivery(tmp_path: Path, monkeypatch) -> None:
    """The raster is delivered; what fails is that it is not on the input grid."""
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32),
                              transform=from_origin(500050.0, 6000000.0, 10.0, 10.0))

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.checks["artifact_present"].verdict == "pass"
    assert report.checks["grid"].verdict == "fail"
    assert report.validity["v"] == 0
    # No pixel comparison is recorded: the raster was never placed on this grid.
    assert report.quality == {}


def test_ruggedness_in_another_crs_fails_delivery(tmp_path: Path, monkeypatch) -> None:
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32),
                              crs=CRS.from_epsg(4326))

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.checks["artifact_present"].verdict == "pass"
    assert report.checks["grid"].verdict == "fail"
    assert "not equivalent" in report.checks["grid"].detail
    assert report.validity["v"] == 0
    assert report.quality == {}


def test_ruggedness_may_declare_nodata(tmp_path: Path, monkeypatch) -> None:
    """A continuous surface is not a 0/1 class raster, so nodata is allowed.

    The canopy rule that rejects a nodata marker exists because a class answer
    must account for every pixel. Applying it here would fail a correct
    ruggedness delivery for a rule that never applied to it.
    """
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32))
    with rasterio.open(delivered, "r+") as destination:
        destination.nodata = 0.0

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.checks["artifact_present"].verdict == "pass"
    assert report.checks["grid"].verdict == "pass"
    assert report.checks["task"].verdict == "pass"


def test_missing_ruggedness_raster_fails_delivery(tmp_path: Path, monkeypatch) -> None:
    spec, gold, _ = _ruggedness_fixture(tmp_path, monkeypatch)
    notes = tmp_path / "out" / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("I decided not to deliver a raster", encoding="utf-8")

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [notes], answer="完成"),
                              case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.validity["v"] == 0
    assert "no GeoTIFF other than the input" in report.checks["artifact_present"].detail


def test_dem_echo_is_not_a_ruggedness_delivery(tmp_path: Path, monkeypatch) -> None:
    """Uploading Elevation.tif back is not an answer, even on the right grid."""
    spec, gold, _ = _ruggedness_fixture(tmp_path, monkeypatch)
    dem = _ruggedness_dem()
    echo = _write_raster(tmp_path / "out" / "Elevation.tif", dem)

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [echo]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.validity["v"] == 0
    assert "no GeoTIFF other than the input" in report.checks["artifact_present"].detail


def test_two_ruggedness_rasters_are_ambiguous(tmp_path: Path, monkeypatch) -> None:
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    first = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32))
    second = _write_raster(tmp_path / "out" / "ruggedness_v2.tif", gold_array.astype(np.float32))

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [first, second]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.checks["artifact_present"].verdict == "fail"
    assert "exactly one" in report.checks["artifact_present"].detail
    assert report.validity["v"] == 0


def test_tampered_gold_raster_refuses_to_grade(tmp_path: Path, monkeypatch) -> None:
    """Truth that no longer hashes to the frozen digest is not truth."""
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    # Freeze the digest, then change the file it belongs to.
    _write_raster(tmp_path / "private" / "gold" / "ruggedness.tif",
                  (gold_array + 1.0).astype(np.float32))
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32))

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.validity["v"] is None            # unmeasured, not the agent's fault
    assert report.outcome["task_success"] is None
    assert "does not match" in report.failure_reasons[0]


def test_missing_gold_raster_refuses_to_grade(tmp_path: Path, monkeypatch) -> None:
    """The frozen truth names a file that is not there: the grader must not guess.

    The path is simply never written, rather than written and deleted, because a
    deletion inside a test is indistinguishable from a real one and trips the
    sandbox's bulk-delete guard when the suite is run in full.
    """
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    gold = dict(gold, gold_raster="private/gold/never-written.tif")
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32))

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.validity["v"] is None
    assert "missing" in report.failure_reasons[0]


#: The published GABench inputs. ``data/`` is gitignored, so these are absent on a
#: fresh checkout and the test that needs them skips instead of failing.
REAL_DATASET = Path(__file__).resolve().parents[1] / "data" / "gabench" / "repo" / "dataset"
REAL_GOLD_RASTER = (Path(__file__).resolve().parents[1] / "data" / "gabench" / "private"
                    / "gold" / "ruggedness-Elevation.tif")


@pytest.mark.skipif(
    not (REAL_DATASET / "Elevation.tif").is_file() or not REAL_GOLD_RASTER.is_file(),
    reason="the published GABench inputs are gitignored and not present on this checkout",
)
def test_ruggedness_matches_the_published_reference_pixel_for_pixel(tmp_path: Path) -> None:
    """The acceptance criterion for ID 12, run against the real frozen truth.

    ``recompute_gabench.py`` already proves the *value*; this proves the *grader*
    reproduces it end to end: the delivered raster is recomputed here from
    Elevation.tif, handed to the grader as an ordinary artifact, and judged
    against the frozen copy of the published ruggedness.tif.
    """
    from scipy.ndimage import maximum_filter, minimum_filter

    with rasterio.open(REAL_DATASET / "Elevation.tif") as source:
        elevation = source.read(1).astype("float64")
        profile = source.profile
    # Identical to the generic_filter route the recomputation script used, only
    # fast enough to run inside the suite (checked equal on random input).
    rugged = maximum_filter(elevation, size=3) - minimum_filter(elevation, size=3)

    delivered = tmp_path / "out" / "ruggedness.tif"
    delivered.parent.mkdir(parents=True, exist_ok=True)
    profile.update(dtype="float32", count=1)
    with rasterio.open(delivered, "w", **profile) as destination:
        destination.write(rugged.astype("float32"), 1)

    spec = TaskSpec(
        task_id="gabench-12-terrain-ruggedness", family="gis_analysis",
        question="用高程数据计算地形起伏度",
        public_root=(REAL_DATASET.relative_to(Path(__file__).resolve().parents[1])).as_posix(),
        public_inputs=["Elevation.tif"],
        required_deliverables=["a GeoTIFF of terrain ruggedness on the input grid"],
        grading_rule=["every pixel of the delivered raster equals the reference"],
        condition="raster", budget={"wall_seconds": 900}, resources={},
        tolerance={"pixels_absolute": 0.0},
    )
    gold = gold_entry("gabench-12-terrain-ruggedness")

    bundle = TrialBundle.load(_ruggedness_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.checks["grid"].verdict == "pass"
    assert report.quality["mismatched_pixels"] == 0
    assert report.quality["compared_pixels"] == gold["valid_pixels"]
    assert report.quality["max_abs_difference"] == pytest.approx(0.0)
    assert report.checks["task"].verdict == "pass"
    assert report.validity["v"] == 1
    assert report.outcome["end_to_end"] == 1


# --- ID 9 ------------------------------------------------------------------ #

def _rate_spec(tmp_path: Path) -> TaskSpec:
    return TaskSpec(
        task_id="gabench-09-deforestation-buffer", family="gis_analysis",
        question="道路 5.5 km 缓冲区内的砍伐率",
        public_root=str(tmp_path / "public"),
        public_inputs=["roads.geojson", "deforestedArea.geojson"],
        required_deliverables=["deforestation_rate.csv holding the ratio"],
        grading_rule=["the reported ratio is within the frozen tolerance of the reference"],
        condition="given-vectors", budget={"wall_seconds": 900},
        resources={"python": ["geopandas", "shapely", "pyproj"], "network": "not required"},
        tolerance={"value_absolute": 1e-9, "value_relative": 1e-4},
        process_rubric={
            "method_markers": [
                ["buffer"],
                ["dissolve", "union_all", "unary_union"],
                ["intersection", "intersect", "clip", "overlay"],
            ],
            "key_parameters": [
                {"name": "buffer_distance_5500_m", "numeric": 5500},
                {"name": "projected_crs_epsg_32723", "tokens": ["32723"]},
            ],
        },
    )


def _rate_gold() -> dict:
    return {"kind": "gis_vector", "gold_value": GOLD_RATE,
            "gold_field": "percentage_deforestation"}


def _rate_csv(tmp_path: Path, text: str, *, name: str = "deforestation_rate.csv") -> Path:
    path = tmp_path / "out" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rate_trial(tmp_path: Path, artifacts: list[Path], *, answer: str = "砍伐率 0.479") -> Path:
    return _trial(tmp_path, artifacts=artifacts, answer=answer)


def test_correct_deforestation_ratio_passes(tmp_path: Path) -> None:
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, f"percentage_deforestation\n{GOLD_RATE!r}\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["artifact_present"].verdict == "pass"
    assert report.checks["task"].verdict == "pass"
    assert report.quality["absolute_error"] < 1e-12
    assert report.validity["v"] == 1
    assert report.outcome["end_to_end"] == 1


def test_alternative_geometry_repair_is_inside_the_frozen_tolerance(tmp_path: Path) -> None:
    """The ``make_valid`` route is a defensible reading and must not be punished.

    The published polygon is invalid; repairing it with a zero-width buffer gives
    the frozen value and ``make_valid`` gives a value 1.07e-05 away. The frozen
    relative tolerance covers both, because which repair is "the" answer is not
    decidable from the published task.
    """
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, "percentage_deforestation\n0.47930037874086123\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.quality["reported"] == pytest.approx(MAKE_VALID_RATE)
    assert report.checks["task"].verdict == "pass"


def test_percentage_instead_of_ratio_is_rejected(tmp_path: Path) -> None:
    """A percentage is a different quantity, not a rounding of the ratio."""
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, "percentage_deforestation\n47.93110356552816\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["task"].verdict == "fail"
    assert "percentage" in report.checks["task"].detail
    assert report.outcome["task_success"] == 0


def test_wrong_deforestation_ratio_fails_the_result_not_the_delivery(tmp_path: Path) -> None:
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, "percentage_deforestation\n0.5\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["task"].verdict == "fail"
    assert report.validity["v"] == 1
    assert report.outcome["task_success"] == 0
    assert report.outcome["end_to_end"] == 0


def test_chinese_ratio_column_is_accepted(tmp_path: Path) -> None:
    """The column the task names may be spelled in the task's own language."""
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, f"砍伐率\n{GOLD_RATE!r}\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["artifact_present"].verdict == "pass"
    assert report.checks["task"].verdict == "pass"


def test_bare_number_without_a_header_is_not_guessed(tmp_path: Path) -> None:
    """A right number in the wrong shape is still not an answer to this task."""
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, f"{GOLD_RATE!r}\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["task"].verdict == "fail"
    assert report.outcome["task_success"] == 0
    assert "no ratio column" in report.checks["task"].detail


def test_empty_csv_is_not_guessed(tmp_path: Path) -> None:
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, "")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["task"].verdict == "fail"
    assert "no header row" in report.checks["task"].detail


def test_csv_without_a_ratio_column_is_caught(tmp_path: Path) -> None:
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, "buffer_area,clipped_area\n179792795540.4,86176671033.8\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["task"].verdict == "fail"
    assert "buffer_area" in report.checks["task"].detail


def test_missing_ratio_csv_fails_delivery(tmp_path: Path) -> None:
    spec = _rate_spec(tmp_path)
    notes = tmp_path / "out" / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("about 48 percent", encoding="utf-8")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [notes]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.validity["v"] == 0
    assert "no CSV was delivered" in report.checks["artifact_present"].detail


def test_two_result_csvs_are_ambiguous(tmp_path: Path) -> None:
    spec = _rate_spec(tmp_path)
    first = _rate_csv(tmp_path, f"percentage_deforestation\n{GOLD_RATE!r}\n")
    second = _rate_csv(tmp_path, f"deforestation_rate\n{GOLD_RATE!r}\n", name="result.csv")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [first, second]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold=_rate_gold())

    assert report.checks["artifact_present"].verdict == "fail"
    assert "exactly one" in report.checks["artifact_present"].detail
    assert report.validity["v"] == 0


def test_gis_trial_without_frozen_gold_is_unmeasured(tmp_path: Path) -> None:
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, f"percentage_deforestation\n{GOLD_RATE!r}\n")

    bundle = TrialBundle.load(_rate_trial(tmp_path, [delivered]), case_id="grounded")
    report = grade_gis_analysis(spec, bundle, gold={})

    assert report.validity["v"] is None
    assert report.outcome["task_success"] is None
    assert "no frozen gold" in report.failure_reasons[0]


def test_gis_incomplete_run_scores_zero(tmp_path: Path, monkeypatch) -> None:
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32))

    bundle = TrialBundle.load(
        _ruggedness_trial(tmp_path, [delivered], terminal="failed"), case_id="grounded"
    )
    report = grade_gis_analysis(spec, bundle, gold=gold)

    assert report.validity["v"] == 0
    assert report.outcome["task_success"] == 0
    assert "terminal state" in report.failure_reasons[0]


# --------------------------------------------------------------------------- #
# Process checks: decided only from evidence the run itself recorded
# --------------------------------------------------------------------------- #

def _tool_event(name: str, *, ok: bool | None = None, result: dict | None = None,
                arguments: dict | None = None) -> dict:
    """One event in the vocabulary the collector actually writes."""
    event: dict = {"type": "tool_start" if arguments else "tool_end", "name": name}
    if arguments is not None:
        event["arguments"] = arguments
    if result is not None:
        event["result"] = result
    if ok is not None:
        event["ok"] = ok
        event["outcome_ok"] = ok
    return event


def _code_event(code: str) -> dict:
    return _tool_event("code_run", arguments={"language": "python", "code": code})


def _answer_event(text: str) -> dict:
    """The agent's own closing message: the answer text the grader reads."""
    return {"type": "message", "content": text}


def _process_report(tmp_path: Path, events: list[dict]) -> GradeReport:
    """Grade a correct ID 9 delivery carrying the given trajectory."""
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, f"percentage_deforestation\n{GOLD_RATE!r}\n")
    bundle = TrialBundle.load(
        _trial(tmp_path, artifacts=[delivered],
               events=[_answer_event(f"砍伐率 {GOLD_RATE}")] + list(events)),
        case_id="grounded",
    )
    return grade_gis_analysis(spec, bundle, gold=_rate_gold())


def _module_result(modules: list[tuple[str, bool]]) -> dict:
    return {
        "ok": True,
        "data": {"modules": [
            {"module": name, "importable": importable,
             "error": None if importable else f"ModuleNotFoundError: No module named '{name}'"}
            for name, importable in modules
        ]},
    }


def test_process_checks_read_the_event_vocabulary_the_collector_writes(tmp_path: Path) -> None:
    """The collector emits ``tool_start``/``tool_end``, not ``action``.

    Reading only the older vocabulary left every process check unmeasured on real
    evidence, which is invisible in a synthetic fixture and total on a real run.
    """
    report = _process_report(tmp_path, [
        _tool_event("inspect_file", ok=True, result={"ok": True}),
        _tool_event("code_run", ok=True, result={"ok": True}),
    ])

    assert report.process["verified_product"]["verdict"] == "pass"
    assert "inspect_file" in report.process["verified_product"]["evidence"]
    assert report.process["recovered_from_errors"]["verdict"] == "pass"


def test_missing_dependency_is_a_dependency_failure(tmp_path: Path) -> None:
    report = _process_report(tmp_path, [
        _tool_event("environment_check", ok=True,
                    result=_module_result([("geopandas", False), ("shapely", True),
                                           ("pyproj", True)])),
    ])

    check = report.process["dependencies_satisfied"]
    assert check["verdict"] == "fail"
    assert check["evidence"] == ["geopandas"]


def test_dependency_installed_later_satisfies_the_task(tmp_path: Path) -> None:
    """A package missing at probe time and installed afterwards is satisfied."""
    report = _process_report(tmp_path, [
        _tool_event("environment_check", ok=True,
                    result=_module_result([("geopandas", False), ("shapely", True),
                                           ("pyproj", True)])),
        _tool_event("dependency_install", ok=True, result={
            "ok": True,
            "data": {"verification": {
                "state": "succeeded", "requirements": ["geopandas"],
                "installed_packages": [{"name": "geopandas", "version": "1.1.4"}],
            }},
        }),
    ])

    check = report.process["dependencies_satisfied"]
    assert check["verdict"] == "pass"
    assert set(check["evidence"]) == {"geopandas", "shapely", "pyproj"}


def test_dependencies_stay_unknown_when_the_run_never_probed_them(tmp_path: Path) -> None:
    report = _process_report(tmp_path, [_code_event("import geopandas as gpd\n")])

    check = report.process["dependencies_satisfied"]
    assert check["verdict"] == "unknown"
    assert "no dependency probe" in check["detail"]


def test_key_parameters_are_read_from_the_executed_code(tmp_path: Path) -> None:
    report = _process_report(tmp_path, [_code_event(
        "import geopandas as gpd\n"
        "roads = gpd.read_file('roads.geojson').to_crs('EPSG:32723')\n"
        "buffer = roads.buffer(5500).union_all()\n"
        "clipped = buffer.intersection(deforested)\n"
    )])

    check = report.process["key_parameters_correct"]
    assert check["verdict"] == "pass"
    assert set(check["evidence"]) == {"buffer_distance_5500_m", "projected_crs_epsg_32723"}


def test_another_buffer_distance_is_reported(tmp_path: Path) -> None:
    """The frozen distance is 5500 m; a different one is a finding, not a guess."""
    report = _process_report(tmp_path, [_code_event(
        "roads = roads.to_crs('EPSG:32723')\nbuffer = roads.buffer(5000).union_all()\n"
    )])

    check = report.process["key_parameters_correct"]
    assert check["verdict"] == "fail"
    assert check["evidence"] == ["buffer_distance_5500_m"]


def test_method_rubric_matched_from_the_executed_code(tmp_path: Path) -> None:
    report = _process_report(tmp_path, [_code_event(
        "buffer = roads.to_crs('EPSG:32723').buffer(5500).union_all()\n"
        "clipped = buffer.intersection(deforested)\n"
    )])

    assert report.process["method_fits_data"]["verdict"] == "pass"


def test_method_missing_a_required_step_is_reported(tmp_path: Path) -> None:
    """Without a dissolve, overlapping buffers count shared ground twice."""
    report = _process_report(tmp_path, [_code_event(
        "buffer = roads.to_crs('EPSG:32723').buffer(5500)\n"
        "clipped = buffer.intersection(deforested)\n"
    )])

    check = report.process["method_fits_data"]
    assert check["verdict"] == "fail"
    assert "dissolve" in check["detail"]


def test_process_checks_stay_unknown_without_code_or_rubric(tmp_path: Path) -> None:
    """No executed code means no method and no parameters can be read."""
    report = _process_report(tmp_path, [_tool_event("artifacts_inspect", ok=True,
                                                    result={"ok": True})])

    assert report.process["method_fits_data"]["verdict"] == "unknown"
    assert report.process["key_parameters_correct"]["verdict"] == "unknown"
    assert "no executed code" in report.process["method_fits_data"]["detail"]


def test_a_task_without_a_rubric_is_never_failed_for_its_method(tmp_path: Path) -> None:
    """A correct alternative execution path must not be punished (step 5's rule).

    The canopy tasks freeze no method rubric, because several segmentations are
    equally defensible; the check must then stay unmeasured even though code
    exists.
    """
    truth = _truth()
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", truth.astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", truth.astype(np.uint8))
    coverage = 100.0 * truth.sum() / truth.size
    bundle = TrialBundle.load(
        _trial(tmp_path, artifacts=[delivered], answer=f"覆盖率 {coverage:.2f}%",
               events=[_code_event("mask = (stack.max(axis=0) > 0).astype('uint8')\n")]),
        case_id="grounded",
    )
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    assert report.process["method_fits_data"]["verdict"] == "unknown"
    assert "no method rubric" in report.process["method_fits_data"]["detail"]
    assert report.checks["task" if "task" in report.checks else "quality"].verdict == "pass"


def test_a_failed_process_check_does_not_change_the_result(tmp_path: Path) -> None:
    """Process findings are reported alongside the score, never folded into it."""
    report = _process_report(tmp_path, [_code_event(
        "buffer = roads.buffer(5500).union_all()\nclipped = buffer.intersection(deforested)\n"
    )])

    assert report.process["key_parameters_correct"]["verdict"] == "fail"  # no 32723 anywhere
    assert report.validity["v"] == 1
    assert report.outcome["task_success"] == 1
    assert report.outcome["end_to_end"] == 1


def test_recovery_is_credited_when_a_failed_call_preceded_a_valid_delivery(tmp_path: Path) -> None:
    truth = _truth()
    input_path = _input_stack(tmp_path / "public" / "oam-01.tif")
    gold = _write_raster(tmp_path / "private" / "oam-01-gold.tif", truth.astype(np.uint8))
    delivered = _write_raster(tmp_path / "out" / "canopy.tif", truth.astype(np.uint8))
    coverage = 100.0 * truth.sum() / truth.size
    bundle = TrialBundle.load(
        _trial(tmp_path, artifacts=[delivered], answer=f"覆盖率 {coverage:.2f}%", events=[
            _answer_event(f"树冠覆盖率约为 {coverage:.2f}%"),
            _tool_event("code_run", ok=False, result={"ok": False, "error": "boom"}),
            _tool_event("code_run", ok=True, result={"ok": True}),
            _tool_event("inspect_file", ok=True, result={"ok": True}),
        ]),
        case_id="grounded",
    )
    report = grade_canopy_extraction(_extraction_spec(tmp_path, input_path=input_path),
                                     bundle, gold_mask=gold)

    check = report.process["recovered_from_errors"]
    assert check["verdict"] == "pass"
    assert "1 tool call(s) failed" in check["detail"]


# --------------------------------------------------------------------------- #
# Reproducibility: the same evidence must always produce the same bytes
# --------------------------------------------------------------------------- #

def _three_times(spec: TaskSpec, package: Path, gold: dict) -> list[str]:
    """Grade one package three times, reloading the evidence each time."""
    encoded = []
    for _ in range(3):
        bundle = TrialBundle.load(package, case_id="grounded")
        report = grade_gis_analysis(spec, bundle, gold=gold)
        encoded.append(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return encoded


def test_grading_the_same_evidence_three_times_is_byte_identical(tmp_path: Path) -> None:
    """The plan's reproducibility gate, on both GIS tasks.

    The report has to be byte-identical, not merely equal in its verdicts:
    otherwise a later comparison between two trials could differ for reasons
    that have nothing to do with the trials.
    """
    spec = _rate_spec(tmp_path)
    delivered = _rate_csv(tmp_path, f"percentage_deforestation\n{GOLD_RATE!r}\n")
    package = _trial(tmp_path, artifacts=[delivered], events=[
        _answer_event(f"砍伐率 {GOLD_RATE}"),
        _tool_event("environment_check", ok=True,
                    result=_module_result([("geopandas", True), ("shapely", True),
                                           ("pyproj", True)])),
        _code_event("b = roads.to_crs('EPSG:32723').buffer(5500).union_all()\n"
                    "c = b.intersection(deforested)\n"),
    ])
    encoded = _three_times(spec, package, _rate_gold())

    assert encoded[0] == encoded[1] == encoded[2]
    # The bytes carry the rules they were produced under, so a report can always
    # be traced back to a rules version instead of to whatever is current.
    assert json.loads(encoded[0])["rules_version"] == GROUNDED_RULES_VERSION


def test_raster_grading_is_byte_identical_too(tmp_path: Path, monkeypatch) -> None:
    """The pixel-exact task: the raster path must be reproducible as well."""
    spec, gold, gold_array = _ruggedness_fixture(tmp_path, monkeypatch)
    delivered = _write_raster(tmp_path / "out" / "ruggedness.tif", gold_array.astype(np.float32))
    package = _ruggedness_trial(tmp_path, [delivered], events=[
        _answer_event("已输出起伏度栅格"),
        _code_event("r = maximum_filter(e, size=3) - minimum_filter(e, size=3)\n"),
    ])

    encoded = _three_times(spec, package, gold)

    assert encoded[0] == encoded[1] == encoded[2]
    assert json.loads(encoded[0])["quality"]["mismatched_pixels"] == 0


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


