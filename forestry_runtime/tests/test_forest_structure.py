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


class RasterMetadataVisibilityTests(unittest.TestCase):
    """Geospatial metadata a downstream tool demands must be readable upstream.

    The CHM builder refuses a call whose ``vertical_reference`` disagrees with the
    asset metadata.  That refusal is only fair if the Agent can read that metadata
    first; otherwise the environment is asking for a value it never published.
    """

    def test_inspect_raster_publishes_the_vertical_datum(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "dsm.tif"
            write_elevation(path, np.ones((4, 4), dtype="float32"), "dsm")

            store = Store(root / "assets")
            with path.open("rb") as stream:
                asset = store.put(stream, "dsm.tif", "alice", "image/tiff")
            tools = RemoteSensingTools(store, "alice", [asset["id"]])

            report = tools.execute("inspect_raster", {"asset_id": asset["id"]})
            self.assertTrue(report["ok"], report)
            data = report["data"]
            self.assertEqual(data["vertical_reference"], "synthetic_datum")
            self.assertEqual(data["elevation_units"], "m")
            self.assertEqual(data["dataset_tags"]["vertical_reference"], "synthetic_datum")


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
            # A refusal must be resolvable inside the environment.  The caller
            # supplied a datum that does not match the metadata; the metadata holds
            # the value the tool will accept, so the refusal names it.  Without this
            # the Agent can see the rejection but cannot learn what to send, and
            # spends its budget re-sending variants of a wrong guess -- observed in
            # the first capability baseline run.
            refusal = mismatched_reference["failure"]
            self.assertEqual(refusal["code"], "vertical_reference_mismatch")
            self.assertEqual(refusal["observed_vertical_reference"], "synthetic_datum")
            self.assertEqual(refusal["requested_vertical_reference"], "unverified_datum")
            self.assertTrue(refusal["retryable"])
            self.assertEqual(
                refusal["suggested_arguments"]["vertical_reference"], "synthetic_datum"
            )

            # The suggested arguments are the whole remedy: retrying with them works.
            retried = tools.execute(
                "build_canopy_height_model", refusal["suggested_arguments"]
            )
            self.assertTrue(retried["ok"], retried)

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

            empty = tools.execute("delineate_tree_candidates", {
                "chm_asset_id": chm_id,
                "minimum_height_m": 50.0,
                "smoothing_sigma_m": 1.0,
                "minimum_peak_distance_m": 10.0,
                "minimum_crown_area_m2": 4.0,
                "parameter_source": "synthetic test configuration",
            })
            self.assertTrue(empty["ok"], empty)
            self.assertEqual(empty["outcome"], "empty")
            self.assertEqual(empty["data"]["code"], "no_pixels_above_height")
            self.assertIsNone(empty["control_verified"])
            self.assertNotIn("labels", empty["data"])

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

            # Every product declares what its values are, because a verifier can only
            # check a file against a claim. The CHM claims its measured range and the
            # DSM grid; the label raster claims its id bound; the GeoJSONs claim the
            # candidate count -- the headline number of canopy statistics.
            chm_semantics = built["data"]["chm"]["metadata"]["semantics"]
            self.assertEqual(chm_semantics["quantity"], "canopy_height")
            self.assertEqual(chm_semantics["kind"], "height")
            self.assertEqual(chm_semantics["grid"]["width"], 64)
            self.assertEqual(chm_semantics["grid"]["height"], 64)
            self.assertEqual(len(chm_semantics["valid_range"]), 2)

            label_semantics = candidates["data"]["labels"]["metadata"]["semantics"]
            self.assertEqual(label_semantics["kind"], "label")
            self.assertEqual(label_semantics["valid_range"], [0, 2])

            crown_semantics = candidates["data"]["candidate_crowns"][
                "metadata"]["semantics"]
            self.assertEqual(crown_semantics["feature_count"], 2)
            self.assertEqual(crown_semantics["geometry_type"], "Polygon")

            top_semantics = candidates["data"]["candidate_tops"][
                "metadata"]["semantics"]
            self.assertEqual(top_semantics["feature_count"], 2)
            self.assertEqual(top_semantics["geometry_type"], "Point")

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
