import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from runtime.storage import AssetError
from runtime.capabilities.uav_audit.audit import (
    InputImage, InputPathMapper, UavInspectionService, inspect_images,
)


def image_with_xmp(path: Path, capture: str, band: str) -> None:
    Image.new("RGB", (12, 12), (40, 80, 30)).save(path, format="JPEG")
    with path.open("ab") as stream:
        stream.write((
            f' drone-dji:CaptureUUID="{capture}"'
            f' drone-dji:BandName="{band}"'
            ' drone-dji:GpsLatitude="45.0" drone-dji:GpsLongitude="127.0"'
            ' drone-dji:HorizontalIrradiance="1"'
            ' drone-dji:RadiometricCalibration="1"'
        ).encode("ascii"))


class UavAuditTests(unittest.TestCase):
    def test_nonrecursive_empty_audit_names_the_unchecked_child_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = root / "1605白桦"
            child.mkdir()
            image_with_xmp(child / "image.jpg", "capture-1", "Red")
            service = UavInspectionService(
                mapper=InputPathMapper(root, str(root)), temp_root=root,
            )
            result = service.inspect_folder(str(root), recursive=False)
            self.assertEqual(result["outcome"], "empty")
            self.assertIsNone(result["control_verified"])
            self.assertEqual(result["child_directories"], ["1605白桦"])
            self.assertFalse(result["checked_scope"]["recursive"])
            recursive = service.inspect_folder(str(root), recursive=True)
            self.assertEqual(recursive["image_file_count"], 1)

    def test_multispectral_audit_uses_metadata_without_starting_a_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = []
            for capture in ("capture-1", "capture-2"):
                for band in ("Green", "Red"):
                    path = root / f"{capture}_{band}.jpg"
                    image_with_xmp(path, capture, band)
                    images.append(InputImage(path.name, path))

            result = inspect_images(
                images, source_type="test", source_label="synthetic flight"
            )

            self.assertTrue(result["ready"], result)
            self.assertEqual(result["processing_mode"], "multispectral")
            self.assertEqual(result["capture_count"], 2)
            self.assertEqual(result["gps_file_count"], 4)
            self.assertTrue(result["radiometric_calibration_ready"])
            self.assertEqual(
                result["assessment_scope"],
                "basic_metadata_for_georeferenced_photogrammetry",
            )
            self.assertIn("不代表外部摄影测量一定成功", result["ready_meaning"])

    def test_host_mapping_is_read_only_and_limited_to_configured_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            flight = root / "1605白桦"
            flight.mkdir()
            mapper = InputPathMapper(root, r"E:\UAV")
            self.assertEqual(
                mapper.input_path(r"E:\UAV\1605白桦"), flight.resolve()
            )
            with self.assertRaises(AssetError):
                mapper.input_path(r"E:\outside\flight")

    def test_uploaded_zip_is_bounded_and_does_not_escape_temp_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = io.BytesIO()
            Image.new("RGB", (8, 8), (10, 20, 30)).save(image, format="JPEG")
            archive = root / "flight.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("flight/image.jpg", image.getvalue())
            service = UavInspectionService(temp_root=root)
            result = service.inspect_uploads([{
                "name": archive.name, "path": str(archive)
            }])
            self.assertEqual(result["image_file_count"], 1)
            self.assertFalse(result["ready"])
            self.assertIn("GPS", result["problems"][0])

            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as output:
                output.writestr("../escape.jpg", image.getvalue())
            with self.assertRaises(AssetError):
                service.inspect_uploads([{
                    "name": unsafe.name, "path": str(unsafe)
                }])

    def test_dataset_inventory_separates_flight_panels_and_products(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(2):
                image_with_xmp(
                    root / f"flight_{index}_Red.jpg", f"flight-{index}", "Red"
                )
            before = root / "参考板" / "起飞前"
            after = root / "参考板" / "起飞后"
            before.mkdir(parents=True)
            after.mkdir(parents=True)
            image_with_xmp(before / "panel_Red.jpg", "before-1", "Red")
            image_with_xmp(after / "panel_Red.jpg", "after-1", "Red")
            product = root / "3_正射影像"
            product.mkdir()
            (product / "plot.tif").write_bytes(b"not-read-by-inventory")

            service = UavInspectionService(
                mapper=InputPathMapper(root, str(root)), temp_root=root
            )
            result = service.inventory_dataset(str(root))

            self.assertEqual(result["roles"]["flight_imagery"]["file_count"], 2)
            self.assertEqual(
                result["roles"]["reference_panel_before"]["file_count"], 1
            )
            self.assertEqual(
                result["roles"]["reference_panel_after"]["file_count"], 1
            )
            self.assertEqual(
                result["geospatial_product_counts"], {"orthomosaic": 1}
            )
            self.assertEqual(len(result["acquisition_groups"]), 3)
            self.assertIn("保持独立", result["separation_rule"])


if __name__ == "__main__":
    unittest.main()
