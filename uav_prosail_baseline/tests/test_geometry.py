import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "extract_flight_parameters.py"
SPEC = importlib.util.spec_from_file_location("extract_flight_parameters", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class GeometryTests(unittest.TestCase):
    def test_solar_position_is_physically_plausible_for_sample_flight(self):
        captured = datetime(2022, 7, 30, 16, 30, tzinfo=timezone(timedelta(hours=8)))
        zenith, azimuth = MODULE.solar_position(captured, 42.4085, 117.3147)
        self.assertLess(50.0, zenith)
        self.assertGreater(70.0, zenith)
        self.assertLess(240.0, azimuth)
        self.assertGreater(290.0, azimuth)

    def test_relative_azimuth_is_folded_to_180_degrees(self):
        self.assertEqual(MODULE._relative_azimuth(350.0, 10.0), 20.0)

    def test_xmp_attributes_and_nested_camera_elements_are_read(self):
        payload = (
            b'<rdf:Description drone-dji:CaptureUUID="capture-1" '
            b'drone-dji:BandName="Blue">'
            b'<Camera:CentralWavelength>450</Camera:CentralWavelength>'
            b'<Camera:WavelengthFWHM>16</Camera:WavelengthFWHM>'
            b'</rdf:Description>'
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "metadata.bin"
            path.write_bytes(payload)
            xmp = MODULE._extract_xmp(path)
        self.assertEqual(xmp["CaptureUUID"], "capture-1")
        self.assertEqual(xmp["BandName"], "Blue")
        self.assertEqual(xmp["CentralWavelength"], "450")
        self.assertEqual(xmp["WavelengthFWHM"], "16")


if __name__ == "__main__":
    unittest.main()
