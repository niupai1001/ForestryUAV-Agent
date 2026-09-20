#!/usr/bin/env python3
"""Inspect EXIF/XMP metadata embedded in one UAV image without modifying it."""

from __future__ import annotations

import argparse
import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image


XMP_ATTRIBUTE = re.compile(rb'(?:drone-dji|Camera):([A-Za-z0-9_-]+)="([^"]*)"')
XMP_SIMPLE_ELEMENT = re.compile(
    rb"<Camera:([A-Za-z0-9_-]+)>([^<]*)</Camera:\1>",
    re.DOTALL,
)
XMP_PACKET = re.compile(rb"<x:xmpmeta\b.*?</x:xmpmeta>", re.DOTALL)
OMITTED_EXIF_TAGS = {"MakerNote", "XMLPacket"}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00")
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value).strip("\x00")


def _named_tags(values: dict[int, Any], names: dict[int, str]) -> dict[str, Any]:
    result = {}
    for tag_id, value in values.items():
        name = names.get(tag_id, f"Tag_{tag_id}")
        if name not in OMITTED_EXIF_TAGS:
            result[name] = _json_value(value)
    return result


def inspect_image(path: Path, show_raw_xmp: bool) -> dict[str, Any]:
    raw = path.read_bytes()
    xmp = {
        key.decode("ascii"): value.decode("utf-8", errors="replace")
        for key, value in XMP_ATTRIBUTE.findall(raw)
    }
    xmp.update(
        {
            key.decode("ascii"): value.decode("utf-8", errors="replace").strip()
            for key, value in XMP_SIMPLE_ELEMENT.findall(raw)
        }
    )
    with Image.open(path) as image:
        exif_object = image.getexif()
        exif_main = _named_tags(dict(exif_object), ExifTags.TAGS)
        try:
            exif_sub_ifd = _named_tags(exif_object.get_ifd(34665), ExifTags.TAGS)
        except Exception:
            exif_sub_ifd = {}
        try:
            gps_ifd = _named_tags(exif_object.get_ifd(34853), ExifTags.GPSTAGS)
        except Exception:
            gps_ifd = {}
        result = {
            "image": str(path.resolve()),
            "format": image.format,
            "size_px": [image.width, image.height],
            "exif": exif_main,
            "exif_sub_ifd": exif_sub_ifd,
            "gps_ifd": gps_ifd,
            "xmp": xmp,
            "important_fields": {
                "camera_make": {"source": "EXIF Make", "value": exif_main.get("Make")},
                "camera_model": {"source": "EXIF Model", "value": exif_main.get("Model")},
                "capture_time": {
                    "source": "EXIF DateTimeOriginal",
                    "value": exif_sub_ifd.get("DateTimeOriginal") or exif_main.get("DateTime"),
                },
                "capture_uuid": {"source": "XMP CaptureUUID", "value": xmp.get("CaptureUUID")},
                "band_name": {"source": "XMP BandName", "value": xmp.get("BandName")},
                "band_frequency": {"source": "XMP BandFreq", "value": xmp.get("BandFreq")},
                "central_wavelength": {
                    "source": "XMP Camera:CentralWavelength",
                    "value": xmp.get("CentralWavelength"),
                },
                "wavelength_fwhm": {
                    "source": "XMP Camera:WavelengthFWHM",
                    "value": xmp.get("WavelengthFWHM"),
                },
                "latitude": {"source": "XMP GpsLatitude", "value": xmp.get("GpsLatitude")},
                "longitude": {"source": "XMP GpsLongitude", "value": xmp.get("GpsLongitude")},
                "relative_altitude": {
                    "source": "XMP RelativeAltitude",
                    "value": xmp.get("RelativeAltitude"),
                },
                "gimbal_pitch": {
                    "source": "XMP GimbalPitchDegree",
                    "value": xmp.get("GimbalPitchDegree"),
                },
                "gimbal_yaw": {
                    "source": "XMP GimbalYawDegree",
                    "value": xmp.get("GimbalYawDegree"),
                },
                "irradiance": {"source": "XMP Irradiance", "value": xmp.get("Irradiance")},
                "rtk_flag": {"source": "XMP RtkFlag", "value": xmp.get("RtkFlag")},
                "rtk_std_lon": {"source": "XMP RtkStdLon", "value": xmp.get("RtkStdLon")},
                "rtk_std_lat": {"source": "XMP RtkStdLat", "value": xmp.get("RtkStdLat")},
                "rtk_std_hgt": {"source": "XMP RtkStdHgt", "value": xmp.get("RtkStdHgt")},
                "vignetting_metadata": {
                    "source": "XMP VignettingData/VignettingPolynomial",
                    "present": "VignettingData" in xmp or "VignettingPolynomial" in xmp,
                },
                "dewarp_metadata": {
                    "source": "XMP DewarpData/PerspectiveDistortion",
                    "present": "DewarpData" in xmp or "PerspectiveDistortion" in xmp,
                },
            },
        }
    if show_raw_xmp:
        packet = XMP_PACKET.search(raw)
        result["raw_xmp"] = packet.group(0).decode("utf-8", errors="replace") if packet else None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--show-raw-xmp", action="store_true")
    args = parser.parse_args()
    result = inspect_image(args.image, args.show_raw_xmp)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
