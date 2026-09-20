import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from uav_tools import (
    archive_processing_outputs,
    build_odm_command,
    default_odm_options,
    derive_project_name,
    gdal_environment,
    inspect_dataset,
    get_odm_status,
    prepare_proj_data_directory,
    project_paths,
    save_json,
    validate_dataset,
)
from uav_tools.preview import display_scale


def make_test_image(
    path: Path,
    capture_uuid: str,
    band_name: str | None,
) -> None:
    exif = Image.Exif()
    exif[271] = "DJI"
    exif[272] = "FC6360"
    Image.new("L", (16, 16), color=100).save(path, exif=exif)
    attributes = [f'drone-dji:CaptureUUID="{capture_uuid}"']
    if band_name is not None:
        attributes.append(f'drone-dji:BandName="{band_name}"')
    with path.open("ab") as handle:
        handle.write((" ".join(attributes)).encode("utf-8"))


class MetadataToolTests(unittest.TestCase):
    def test_inspection_counts_captures_models_and_bands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for capture in ("a", "b"):
                make_test_image(root / f"{capture}_rgb.jpg", capture, None)
                make_test_image(root / f"{capture}_green.tif", capture, "Green")
                make_test_image(root / f"{capture}_nir.tif", capture, "NIR")
            report = inspect_dataset(root)

        self.assertEqual(report["image_file_count"], 6)
        self.assertEqual(report["capture_count"], 2)
        self.assertEqual(report["capture_group_size_counts"], {"3": 2})
        self.assertEqual(report["camera_models"], ["FC6360"])
        self.assertEqual(report["band_names"], ["Green", "NIR"])
        self.assertEqual(report["processing_mode"], "multispectral")
        self.assertEqual(validate_dataset(report), [])

    def test_project_name_is_derived_without_dataset_specific_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "1630_larch"
            root.mkdir()
            make_test_image(root / "first.jpg", "capture-a", None)
            name = derive_project_name(root)
        self.assertEqual(name, "uav_undated_fc6360_1630_larch")

    def test_validation_detects_incomplete_multispectral_bands(self):
        report = {
            "image_file_count": 5,
            "capture_count": 3,
            "processing_mode": "multispectral",
            "band_image_counts": {"Green": 3, "NIR": 2},
        }
        problems = validate_dataset(report)
        self.assertIn("各多光谱波段影像数量不一致", problems)
        self.assertIn("波段影像数量与 CaptureUUID 航片组数量不一致", problems)


class OdmAndPreviewToolTests(unittest.TestCase):
    def test_archive_processing_outputs_preserves_inputs_and_log(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = project_paths(Path(folder) / "workspace", "flight_a")
            paths["images"].mkdir(parents=True)
            paths["manifest"].write_text("{}", encoding="utf-8")
            paths["log"].write_text("failed", encoding="utf-8")
            (paths["project"] / "opensfm").mkdir()
            (paths["project"] / "images.json").write_text("{}", encoding="utf-8")

            result = archive_processing_outputs(paths)
            backup = Path(result["backup"])

            self.assertTrue(paths["images"].is_dir())
            self.assertTrue(paths["manifest"].is_file())
            self.assertTrue(paths["log"].is_file())
            self.assertTrue((backup / "opensfm").is_dir())
            self.assertTrue((backup / "images.json").is_file())
            self.assertTrue((backup / "recovery_manifest.json").is_file())
            self.assertEqual(result["moved_count"], 2)

    def test_odm_environment_removes_outer_virtualenv_state(self):
        inherited = {
            "_OLD_VIRTUAL_PATH": "C:/outer/path",
            "VIRTUAL_ENV": "C:/outer/venv",
            "PYTHONHOME": "C:/outer/python",
            "PYTHONPATH": "C:/outer/modules",
        }
        with patch.dict("os.environ", inherited):
            environment = gdal_environment(Path("D:/ODM"))
        for name in inherited:
            self.assertNotIn(name, environment)
        self.assertEqual(
            environment["GDAL_DATA"],
            str(Path("D:/ODM/SuperBuild/install/bin/data/gdal")),
        )
        self.assertEqual(environment["PYTHONUTF8"], "1")

    def test_proj_data_is_copied_to_ascii_compatibility_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            odm_home = root / "中文运行时"
            source = (
                odm_home
                / "SuperBuild"
                / "install"
                / "bin"
                / "data"
                / "proj"
            )
            source.mkdir(parents=True)
            (source / "proj.db").write_bytes(b"database")
            target = root / "proj_cache"

            result = prepare_proj_data_directory(odm_home, target)

            self.assertEqual(result, target.absolute())
            self.assertEqual((result / "proj.db").read_bytes(), b"database")
            environment = gdal_environment(odm_home, result)
            self.assertEqual(environment["PROJ_LIB"], str(result))
            self.assertEqual(environment["PROJ_DATA"], str(result))

    def test_dead_worker_is_interrupted(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = project_paths(Path(folder), "flight_a")
            paths["images"].mkdir(parents=True)
            paths["log"].write_text("starting\n", encoding="utf-8")
            save_json(
                paths["job"],
                {
                    "schema_version": "2.0",
                    "state": "running",
                    "pid": 99999999,
                    "pid_create_time": 1.0,
                },
            )
            status = get_odm_status(paths)
        self.assertEqual(status["state"], "interrupted")

    def test_worker_exit_state_is_authoritative(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = project_paths(Path(folder), "flight_a")
            paths["images"].mkdir(parents=True)
            paths["orthophoto"].parent.mkdir(parents=True)
            paths["orthophoto"].write_bytes(b"raster")
            save_json(
                paths["job"],
                {
                    "schema_version": "2.0",
                    "state": "succeeded",
                    "return_code": 0,
                },
            )
            status = get_odm_status(paths)
        self.assertEqual(status["state"], "succeeded")

    def test_project_paths_use_standard_odm_layout(self):
        paths = project_paths(Path("D:/workspace"), "flight_a")
        self.assertEqual(
            paths["orthophoto"],
            Path("D:/workspace/jobs/flight_a/odm_orthophoto/odm_orthophoto.tif"),
        )
        self.assertEqual(paths["job"], Path("D:/workspace/jobs/flight_a/job.json"))
        self.assertEqual(paths["result"], Path("D:/workspace/jobs/flight_a/result.json"))

    def test_command_builder_returns_arguments_without_starting_odm(self):
        with tempfile.TemporaryDirectory() as folder:
            odm_home = Path(folder)
            (odm_home / "run.bat").write_text("@echo off\n", encoding="utf-8")
            command = build_odm_command(
                odm_home,
                Path("D:/projects"),
                "flight_a",
                {"radiometric_calibration": "camera"},
            )
        self.assertEqual(command[0], str(odm_home / "run.bat"))
        self.assertIn("--radiometric-calibration", command)
        self.assertNotIn("--primary-band", command)
        self.assertNotIn("--max-concurrency", command)

    def test_default_options_leave_resolution_and_primary_band_to_odm(self):
        options = default_odm_options({"processing_mode": "multispectral"})
        self.assertEqual(
            options,
            {"skip_3dmodel": True, "radiometric_calibration": "camera"},
        )

    def test_display_scale_uses_non_negative_floor_for_reflectance(self):
        low, high = display_scale(
            {
                "minimum": -0.02,
                "maximum": 0.8,
                "mean": 0.2,
                "stdDev": 0.1,
            }
        )
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.45)

    def test_display_scale_prefers_alpha_masked_sample_percentiles(self):
        scale = display_scale(
            {
                "minimum": -0.01,
                "maximum": 4294967296.0,
                "displayScale": [0.004, 0.21],
            }
        )
        self.assertEqual(scale, (0.004, 0.21))


if __name__ == "__main__":
    unittest.main()
