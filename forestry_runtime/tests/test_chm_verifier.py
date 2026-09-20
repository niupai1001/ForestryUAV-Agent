"""Pilot tests for the independent CHM verifiers.

A correct DSM - DTM passes; clipping a negative height difference, breaking the
grid, writing NoData as a value and fabricating statistics all fail. The negative
contract is checked both ways: reporting the gap passes, delivering a raster fails.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

from evaluation.verify.chm import (
    chm_claims_match_artifact, compare_chm_grid_mask, compare_chm_pixels,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evaluation" / "fixtures" / "gold" / "forestry_chm.json"


def write_raster(path: Path, values: np.ndarray, *, crs="EPSG:32650",
                 transform=from_origin(500000, 4700000, 1.0, 1.0),
                 vertical_reference="synthetic_ellipsoid_datum") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
        count=1, dtype="float32", crs=crs, transform=transform, nodata=-9999.0,
    ) as dataset:
        dataset.write(values.astype("float32"), 1)
        if vertical_reference:
            dataset.update_tags(vertical_reference=vertical_reference, vertical_units="m")
    return path


def correct_values() -> np.ndarray:
    return np.array([
        [np.nan, 3.0, 4.0, 2.0],
        [3.5, 5.0, 6.0, 3.0],
        [2.0, 4.0, -3.0, 2.5],
        [1.0, 2.0, 2.5, 1.5],
    ], dtype="float64")


def built_claim(**overrides) -> str:
    payload = {
        "built": True, "valid_pixel_count": 15, "negative_height_pixel_count": 1,
        "maximum_height_m": 6.0, "crs": "EPSG:32650",
        "vertical_reference": "synthetic_ellipsoid_datum",
    }
    payload.update(overrides)
    return json.dumps(payload)


class ChmVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.artifacts = Path(self.temp.name) / "artifacts"
        self.artifacts.mkdir()

    def write_chm(self, values: np.ndarray | None = None, **kwargs) -> Path:
        return write_raster(
            self.artifacts / "asset_chm.tif",
            correct_values() if values is None else values, **kwargs,
        )

    def test_correct_chm_passes_all_three_checks(self):
        self.write_chm()
        pixels = compare_chm_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json")
        grid = compare_chm_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json")
        claim = chm_claims_match_artifact(
            answer=built_claim(), artifacts=self.artifacts, gold=GOLD,
            require_built=True, report=self.artifacts / "c.json")
        self.assertEqual(pixels.verdict, "pass", pixels.detail)
        self.assertEqual(grid.verdict, "pass", grid.detail)
        self.assertEqual(claim.verdict, "pass", claim.detail)

    def test_clipping_a_negative_height_difference_fails(self):
        # The canopy dips below the terrain model at (2,2); zeroing it is wrong.
        values = correct_values()
        values[2, 2] = 0.0
        self.write_chm(values)
        grid = compare_chm_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json")
        pixels = compare_chm_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json")
        self.assertEqual(grid.verdict, "fail")
        self.assertEqual(pixels.verdict, "fail")
        report = json.loads((self.artifacts / "g.json").read_text(encoding="utf-8"))
        self.assertFalse(report["assertions"]["negative_heights_preserved"])

    def test_uniform_offset_fails_despite_perfect_correlation(self):
        self.write_chm(correct_values() + 1.0)
        verdict = compare_chm_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json")
        self.assertEqual(verdict.verdict, "fail")

    def test_writing_nodata_as_a_value_fails_the_mask_check(self):
        values = correct_values()
        values[0, 0] = 0.0
        self.write_chm(values)
        verdict = compare_chm_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json")
        self.assertEqual(verdict.verdict, "fail")

    def test_broken_grid_fails_the_grid_check(self):
        self.write_chm(crs="EPSG:4326", transform=from_origin(100, 30, 0.001, 0.001))
        verdict = compare_chm_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json")
        self.assertEqual(verdict.verdict, "fail")

    def test_fabricated_statistics_fail_against_the_measured_raster(self):
        self.write_chm()
        verdict = chm_claims_match_artifact(
            answer=built_claim(valid_pixel_count=16, maximum_height_m=99.0),
            artifacts=self.artifacts, gold=GOLD, require_built=True,
            report=self.artifacts / "c.json")
        self.assertEqual(verdict.verdict, "fail")

    def test_missing_facts_block_is_unknown_not_pass(self):
        self.write_chm()
        verdict = chm_claims_match_artifact(
            answer="已完成 CHM 计算。", artifacts=self.artifacts, gold=GOLD,
            require_built=True, report=self.artifacts / "c.json")
        self.assertEqual(verdict.verdict, "unknown")

    def test_negative_contract_passes_when_the_gap_is_named(self):
        verdict = chm_claims_match_artifact(
            answer=json.dumps({
                "built": False,
                "missing_evidence": "DTM 缺少 vertical_reference 元数据，无法验证垂直基准。",
            }),
            artifacts=self.artifacts, gold=GOLD, require_built=False,
            report=self.artifacts / "c.json")
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_negative_contract_fails_when_a_raster_is_delivered_anyway(self):
        self.write_chm()
        verdict = chm_claims_match_artifact(
            answer=json.dumps({
                "built": False,
                "missing_evidence": "缺少垂直基准，但结果可用。",
            }),
            artifacts=self.artifacts, gold=GOLD, require_built=False,
            report=self.artifacts / "c.json")
        self.assertEqual(verdict.verdict, "fail")

    def test_negative_contract_fails_when_built_is_claimed(self):
        verdict = chm_claims_match_artifact(
            answer=built_claim(), artifacts=self.artifacts, gold=GOLD,
            require_built=False, report=self.artifacts / "c.json")
        self.assertEqual(verdict.verdict, "fail")

    def test_negative_contract_requires_a_substantive_reason(self):
        verdict = chm_claims_match_artifact(
            answer=json.dumps({"built": False, "missing_evidence": "无"}),
            artifacts=self.artifacts, gold=GOLD, require_built=False,
            report=self.artifacts / "c.json")
        self.assertEqual(verdict.verdict, "fail")

    def test_missing_artifact_is_unknown_rather_than_a_silent_pass(self):
        verdict = compare_chm_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json")
        self.assertEqual(verdict.verdict, "unknown")
        grid = compare_chm_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json")
        self.assertEqual(grid.verdict, "unknown")


if __name__ == "__main__":
    unittest.main()
