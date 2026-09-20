import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

from runtime.capabilities.domain_runtime import RemoteSensingTools
from runtime.storage import Store


def write_elevation(path: Path, values: np.ndarray, description: str) -> None:
    profile = {
        "driver": "GTiff",
        "width": values.shape[1],
        "height": values.shape[0],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:32650",
        "transform": from_origin(500000, 4700000, 1, 1),
        "nodata": np.nan,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values.astype("float32"), 1)
        dst.set_band_description(1, description)
        dst.update_tags(
            vertical_reference="synthetic_datum", vertical_units="m"
        )


class ForestStructureTests(unittest.TestCase):
    def test_chm_candidates_and_summary_keep_scientific_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows, columns = np.indices((64, 64))
            dtm = 100 + rows * 0.02
            first = np.maximum(
                0, 16 - 0.75 * np.hypot(rows - 20, columns - 20)
            )
            second = np.maximum(
                0, 13 - 0.65 * np.hypot(rows - 43, columns - 44)
            )
            canopy = np.maximum(first, second)
            dsm = dtm + canopy
            dsm[0, 0] = dtm[0, 0] - 1
            dsm[0, 1] = np.nan
            dsm_path = root / "dsm.tif"
            dtm_path = root / "dtm.tif"
            write_elevation(dsm_path, dsm, "dsm")
            write_elevation(dtm_path, dtm, "dtm")

            store = Store(root / "assets")
            with dsm_path.open("rb") as stream:
                dsm_asset = store.put(stream, "dsm.tif", "alice", "image/tiff")
            with dtm_path.open("rb") as stream:
                dtm_asset = store.put(stream, "dtm.tif", "alice", "image/tiff")
            tools = RemoteSensingTools(
                store, "alice", [dsm_asset["id"], dtm_asset["id"]]
            )

            mismatched_reference = tools.execute("build_canopy_height_model", {
                "dsm_asset_id": dsm_asset["id"],
                "dtm_asset_id": dtm_asset["id"],
                "vertical_reference": "unverified_datum",
            })
            self.assertFalse(mismatched_reference["ok"])

            built = tools.execute("build_canopy_height_model", {
                "dsm_asset_id": dsm_asset["id"],
                "dtm_asset_id": dtm_asset["id"],
                "vertical_reference": "synthetic_datum",
            })
            self.assertTrue(built["ok"], built)
            self.assertEqual(
                built["data"]["statistics"]["negative_height_pixel_count"], 1
            )
            chm_id = built["data"]["chm"]["id"]

            candidates = tools.execute("delineate_tree_candidates", {
                "chm_asset_id": chm_id,
                "minimum_height_m": 2.0,
                "smoothing_sigma_m": 1.0,
                "minimum_peak_distance_m": 10.0,
                "minimum_crown_area_m2": 4.0,
                "maximum_crown_area_m2": 2000.0,
                "parameter_source": "synthetic test configuration",
            })
            self.assertTrue(candidates["ok"], candidates)
            self.assertEqual(
                candidates["data"]["statistics"][
                    "upper_canopy_candidate_count"
                ],
                2,
            )
            crowns = store.path(
                candidates["data"]["candidate_crowns"]["id"], "alice"
            )
            payload = json.loads(crowns.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["features"]), 2)
            self.assertEqual(payload["coordinate_reference_system"], "EPSG:4326")

            labels_id = candidates["data"]["labels"]["id"]
            summary = tools.execute("summarize_forest_structure", {
                "chm_asset_id": chm_id,
                "labels_asset_id": labels_id,
            })
            self.assertTrue(summary["ok"], summary)
            self.assertEqual(
                summary["data"]["summary"]["upper_canopy_candidate_count"], 2
            )
            self.assertEqual(
                summary["data"]["summary"]["analysis_denominator"],
                "valid_chm_pixels",
            )
            self.assertIn("candidate_table", summary["data"])


if __name__ == "__main__":
    unittest.main()
