"""Pilot tests for the independent NDVI verifiers.

These assert the verifier's semantics, not a model's ability: a correct raster
passes, and each characteristic error (wrong pixel value, broken grid, silently
zero-filled invalid pixel, fabricated statistics) fails.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

from evaluation.verify.ndvi import (
    compare_ndvi_grid_mask,
    compare_ndvi_pixels,
    reported_facts_match_artifact,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evaluation" / "fixtures" / "gold" / "forestry_ndvi.json"


def write_raster(
    path: Path, values: np.ndarray, *, crs: str = "EPSG:32650",
    transform=from_origin(500000, 3100000, 0.03, 0.03), dtype: str = "float32",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
        count=1, dtype=dtype, crs=crs, transform=transform, nodata=np.nan,
    ) as dataset:
        dataset.write(values.astype(dtype), 1)
    return path


class NdviVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.artifacts = Path(self.temp.name) / "artifacts"
        self.artifacts.mkdir()
        self.contract = json.loads(GOLD.read_text(encoding="utf-8"))

    def correct_raster(self, name="asset_correct-ndvi.tif") -> Path:
        values = np.array([[0.5, 0.0], [np.nan, 1.0]], dtype="float32")
        return write_raster(self.artifacts / name, values)

    def test_correct_raster_passes_every_check(self):
        self.correct_raster()
        pixels = compare_ndvi_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json"
        )
        grid = compare_ndvi_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json"
        )
        facts = reported_facts_match_artifact(
            answer=json.dumps({
                "valid_pixel_count": 3, "zero_denominator_pixel_count": 1,
                "mean": 0.5, "crs": "EPSG:32650", "output_shape": [2, 2],
            }),
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "f.json",
        )
        self.assertEqual(pixels.verdict, "pass", pixels.detail)
        self.assertEqual(grid.verdict, "pass", grid.detail)
        self.assertEqual(facts.verdict, "pass", facts.detail)

    def test_wrong_pixel_value_fails_even_when_shape_matches(self):
        # (2-1)/(2+1) instead of 0.0: a shape-only check would accept this.
        values = np.array([[0.5, 0.3333333], [np.nan, 1.0]], dtype="float32")
        write_raster(self.artifacts / "asset_wrong-ndvi.tif", values)
        verdict = compare_ndvi_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json"
        )
        self.assertEqual(verdict.verdict, "fail")

    def test_uniformly_scaled_raster_fails_despite_perfect_correlation(self):
        # Multiplying every value by 10 keeps correlation at 1.0 but is wrong.
        values = np.array([[5.0, 0.0], [np.nan, 10.0]], dtype="float32")
        write_raster(self.artifacts / "asset_scaled-ndvi.tif", values)
        verdict = compare_ndvi_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json"
        )
        self.assertEqual(verdict.verdict, "fail")

    def test_zero_filled_invalid_pixel_fails_the_mask_check(self):
        values = np.array([[0.5, 0.0], [0.0, 1.0]], dtype="float32")
        write_raster(self.artifacts / "asset_zerofilled-ndvi.tif", values)
        verdict = compare_ndvi_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertFalse(verdict.verdict == "pass")

    def test_broken_grid_fails_the_grid_check(self):
        values = np.array([[0.5, 0.0], [np.nan, 1.0]], dtype="float32")
        write_raster(
            self.artifacts / "asset_reprojected-ndvi.tif", values,
            crs="EPSG:4326", transform=from_origin(100, 30, 0.001, 0.001),
        )
        verdict = compare_ndvi_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json"
        )
        self.assertEqual(verdict.verdict, "fail")

    def test_fabricated_statistics_fail_against_the_measured_artifact(self):
        self.correct_raster()
        verdict = reported_facts_match_artifact(
            answer=json.dumps({
                "valid_pixel_count": 4, "zero_denominator_pixel_count": 0,
                "mean": 0.75, "crs": "EPSG:32650", "output_shape": [2, 2],
            }),
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "f.json",
        )
        self.assertEqual(verdict.verdict, "fail")

    def test_unparseable_answer_is_unknown_not_pass(self):
        self.correct_raster()
        verdict = reported_facts_match_artifact(
            answer="已经完成 NDVI 计算，结果看起来正常。",
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "f.json",
        )
        self.assertEqual(verdict.verdict, "unknown")

    def test_missing_artifact_is_unknown_rather_than_a_silent_pass(self):
        verdict = compare_ndvi_pixels(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "p.json"
        )
        self.assertEqual(verdict.verdict, "unknown")
        grid = compare_ndvi_grid_mask(
            artifacts=self.artifacts, gold=GOLD, report=self.artifacts / "g.json"
        )
        self.assertEqual(grid.verdict, "unknown")


if __name__ == "__main__":
    unittest.main()
