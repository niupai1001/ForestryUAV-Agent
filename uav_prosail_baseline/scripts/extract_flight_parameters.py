#!/usr/bin/env python3
"""Extract deterministic UAV flight, geometry and band metadata.

The script reads image pixels/metadata without modifying the input directory.
It intentionally preserves undocumented RTK status values as raw values rather
than translating them into accuracy claims.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff", ".dng"}
XMP_ATTRIBUTE = re.compile(rb'(?:drone-dji|Camera):([A-Za-z0-9_-]+)="([^"]*)"')
XMP_SIMPLE_ELEMENT = re.compile(
    rb"<Camera:([A-Za-z0-9_-]+)>([^<]*)</Camera:\1>",
    re.DOTALL,
)
BAND_FREQUENCY = re.compile(r"^\s*([0-9.]+)\s*\(\+/-\s*([0-9.]+)\)\s*nm\s*$", re.I)


def _float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _median(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    return statistics.median(clean) if clean else None


def _extract_xmp(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    result = {
        key.decode("ascii"): value.decode("utf-8", errors="replace")
        for key, value in XMP_ATTRIBUTE.findall(data)
    }
    result.update(
        {
            key.decode("ascii"): value.decode("utf-8", errors="replace").strip()
            for key, value in XMP_SIMPLE_ELEMENT.findall(data)
        }
    )
    return result


def _capture_time(image: Image.Image) -> str | None:
    exif = image.getexif()
    exif_ifd: dict[int, Any] = {}
    try:
        exif_ifd = exif.get_ifd(34665)
    except Exception:
        pass
    value = exif_ifd.get(36867) or exif.get(306)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip() if value else None


def _time_zone(timezone_name: str | None, utc_offset_hours: float | None):
    if utc_offset_hours is not None:
        return timezone(timedelta(hours=utc_offset_hours))
    if not timezone_name:
        raise ValueError("Either timezone_name or utc_offset_hours is required")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(
            f"IANA timezone {timezone_name!r} is unavailable in this Python environment; "
            "pass --utc-offset-hours instead"
        ) from error


def _parse_local_time(value: str, time_zone) -> datetime:
    parsed = None
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value[:19], pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"Unsupported capture time: {value!r}")
    return parsed.replace(tzinfo=time_zone)


def solar_position(captured_at: datetime, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    """Return approximate solar zenith and azimuth (north-clockwise), in degrees.

    The implementation follows the NOAA fractional-year equations and is
    sufficient for flight-level PROSAIL geometry. `captured_at` must be aware.
    """
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must include timezone information")
    utc = captured_at.astimezone(timezone.utc)
    day = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (day - 1 + (hour - 12.0) / 24.0)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.00148 * math.sin(3.0 * gamma)
    )
    offset_hours = captured_at.utcoffset().total_seconds() / 3600.0
    local_minutes = captured_at.hour * 60.0 + captured_at.minute + captured_at.second / 60.0
    time_offset = equation_of_time + 4.0 * longitude_deg - 60.0 * offset_hours
    true_solar_minutes = (local_minutes + time_offset) % 1440.0
    hour_angle_deg = true_solar_minutes / 4.0 - 180.0
    latitude = math.radians(latitude_deg)
    hour_angle = math.radians(hour_angle_deg)
    cos_zenith = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    zenith = math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))
    azimuth = (
        math.degrees(
            math.atan2(
                math.sin(hour_angle),
                math.cos(hour_angle) * math.sin(latitude)
                - math.tan(declination) * math.cos(latitude),
            )
        )
        + 180.0
    ) % 360.0
    return zenith, azimuth


def _relative_azimuth(solar_azimuth: float, view_azimuth: float) -> float:
    difference = abs((solar_azimuth - view_azimuth) % 360.0)
    return min(difference, 360.0 - difference)


def read_image_metadata(path: Path, time_zone) -> dict[str, Any]:
    xmp = _extract_xmp(path)
    with Image.open(path) as image:
        captured_text = _capture_time(image)
        captured_at = _parse_local_time(captured_text, time_zone) if captured_text else None
        exif = image.getexif()
        make = exif.get(271)
        model = exif.get(272)
        if isinstance(make, bytes):
            make = make.decode("utf-8", errors="replace")
        if isinstance(model, bytes):
            model = model.decode("utf-8", errors="replace")
        make = str(make).strip().strip("\x00") if make else None
        model = str(model).strip().strip("\x00") if model else None
        record = {
            "path": str(path),
            "name": path.name,
            "width_px": image.width,
            "height_px": image.height,
            "make": make,
            "model": model,
            "captured_at": captured_at.isoformat() if captured_at else None,
            "capture_uuid": xmp.get("CaptureUUID") or path.stem,
            "band": xmp.get("BandName"),
            "band_frequency": xmp.get("BandFreq"),
            "central_wavelength_nm": _float(xmp.get("CentralWavelength")),
            "wavelength_fwhm_nm": _float(xmp.get("WavelengthFWHM")),
            "sensor_index": xmp.get("SensorIndex"),
            "latitude_deg": _float(xmp.get("GpsLatitude")),
            "longitude_deg": _float(xmp.get("GpsLongitude")),
            "absolute_altitude_m_raw": _float(xmp.get("AbsoluteAltitude")),
            "relative_altitude_m": _float(xmp.get("RelativeAltitude")),
            "gimbal_pitch_deg": _float(xmp.get("GimbalPitchDegree")),
            "gimbal_yaw_deg": _float(xmp.get("GimbalYawDegree")),
            "gimbal_roll_deg": _float(xmp.get("GimbalRollDegree")),
            "flight_pitch_deg": _float(xmp.get("FlightPitchDegree")),
            "flight_yaw_deg": _float(xmp.get("FlightYawDegree")),
            "flight_roll_deg": _float(xmp.get("FlightRollDegree")),
            "calibrated_focal_length_px": _float(xmp.get("CalibratedFocalLength")),
            "rtk_flag_raw": xmp.get("RtkFlag"),
            "rtk_source_type_raw": xmp.get("RtkSrcType"),
            "rtk_std_lon_raw": _float(xmp.get("RtkStdLon")),
            "rtk_std_lat_raw": _float(xmp.get("RtkStdLat")),
            "rtk_std_hgt_raw": _float(xmp.get("RtkStdHgt")),
            "irradiance_raw": _float(xmp.get("Irradiance")),
            "sensor_gain_raw": _float(xmp.get("SensorGain")),
            "exposure_time_raw": _float(xmp.get("ExposureTime")),
            "has_vignetting_metadata": "VignettingData" in xmp or "VignettingPolynomial" in xmp,
            "has_dewarp_metadata": "DewarpData" in xmp or "PerspectiveDistortion" in xmp,
        }
    latitude = record["latitude_deg"]
    longitude = record["longitude_deg"]
    if captured_at and latitude is not None and longitude is not None:
        sza, saa = solar_position(captured_at, latitude, longitude)
        record["solar_zenith_deg"] = sza
        record["solar_azimuth_deg"] = saa
    else:
        record["solar_zenith_deg"] = None
        record["solar_azimuth_deg"] = None
    pitch = record["gimbal_pitch_deg"]
    yaw = record["gimbal_yaw_deg"]
    record["view_zenith_nadir_approx_deg"] = abs(90.0 - abs(pitch)) if pitch is not None else None
    record["view_azimuth_gimbal_approx_deg"] = yaw % 360.0 if yaw is not None else None
    if record["solar_azimuth_deg"] is not None and record["view_azimuth_gimbal_approx_deg"] is not None:
        record["relative_azimuth_approx_deg"] = _relative_azimuth(
            record["solar_azimuth_deg"], record["view_azimuth_gimbal_approx_deg"]
        )
    else:
        record["relative_azimuth_approx_deg"] = None
    height = record["relative_altitude_m"]
    focal_px = record["calibrated_focal_length_px"]
    record["gsd_nadir_approx_cm"] = 100.0 * height / focal_px if height and focal_px else None
    return record


def _group_captures(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["capture_uuid"]].append(record)
    captures = []
    for capture_uuid, items in grouped.items():
        representative = min(items, key=lambda item: 0 if item["band"] is None else 1)
        band_items = sorted((item for item in items if item["band"]), key=lambda item: item["sensor_index"] or "")
        row = {
            "capture_uuid": capture_uuid,
            "captured_at": representative["captured_at"],
            "latitude_deg": _median(item["latitude_deg"] for item in items),
            "longitude_deg": _median(item["longitude_deg"] for item in items),
            "relative_altitude_m": _median(item["relative_altitude_m"] for item in items),
            "gimbal_pitch_deg": _median(item["gimbal_pitch_deg"] for item in items),
            "gimbal_yaw_deg": _median(item["gimbal_yaw_deg"] for item in items),
            "view_zenith_nadir_approx_deg": _median(item["view_zenith_nadir_approx_deg"] for item in items),
            "solar_zenith_deg": _median(item["solar_zenith_deg"] for item in items),
            "solar_azimuth_deg": _median(item["solar_azimuth_deg"] for item in items),
            "relative_azimuth_approx_deg": _median(item["relative_azimuth_approx_deg"] for item in items),
            "gsd_nadir_approx_cm": _median(item["gsd_nadir_approx_cm"] for item in items),
            "rtk_flag_raw": representative["rtk_flag_raw"],
            "rtk_std_lon_raw": _median(item["rtk_std_lon_raw"] for item in items),
            "rtk_std_lat_raw": _median(item["rtk_std_lat_raw"] for item in items),
            "rtk_std_hgt_raw": _median(item["rtk_std_hgt_raw"] for item in items),
            "bands": ";".join(item["band"] for item in band_items),
            "files": ";".join(item["name"] for item in items),
        }
        for item in band_items:
            row[f"irradiance_{item['band']}_raw"] = item["irradiance_raw"]
        captures.append(row)
    return sorted(captures, key=lambda item: (item["captured_at"] or "", item["capture_uuid"]))


def _band_table(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["band"]:
            grouped[record["band"]].append(record)
    rows = []
    for band, items in grouped.items():
        frequencies = Counter(item["band_frequency"] for item in items if item["band_frequency"])
        nominal = frequencies.most_common(1)[0][0] if frequencies else None
        match = BAND_FREQUENCY.match(nominal or "")
        center_nm = _median(item["central_wavelength_nm"] for item in items)
        fwhm_nm = _median(item["wavelength_fwhm_nm"] for item in items)
        rows.append({
            "band": band,
            "sensor_index": Counter(item["sensor_index"] for item in items).most_common(1)[0][0],
            "nominal_center_nm": center_nm if center_nm is not None else (float(match.group(1)) if match else None),
            "nominal_fwhm_nm": fwhm_nm,
            "band_frequency_width_token_nm": float(match.group(2)) if match else None,
            "band_frequency_raw": nominal,
            "image_count": len(items),
            "irradiance_median_raw": _median(item["irradiance_raw"] for item in items),
            "srf_status": "nominal_center_and_fwhm_only",
        })
    return sorted(rows, key=lambda item: item["sensor_index"] or "")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def extract(
    input_dir: Path,
    output_dir: Path,
    timezone_name: str | None,
    utc_offset_hours: float | None,
    recursive: bool,
) -> dict[str, Any]:
    paths = input_dir.rglob("*") if recursive else input_dir.iterdir()
    images = sorted(path for path in paths if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        raise ValueError(f"No supported images found in {input_dir}")
    time_zone = _time_zone(timezone_name, utc_offset_hours)
    records = [read_image_metadata(path, time_zone) for path in images]
    captures = _group_captures(records)
    bands = _band_table(records)
    times = [datetime.fromisoformat(item["captured_at"]) for item in captures if item["captured_at"]]
    ppk_assets = sorted(
        str(path.relative_to(input_dir))
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() not in IMAGE_SUFFIXES
    )
    summary = {
        "schema_version": "1.0",
        "input_directory": str(input_dir.resolve()),
        "scan_mode": "recursive" if recursive else "top_level_only",
        "timezone_assumption": timezone_name or f"UTC{utc_offset_hours:+g}",
        "camera_makes": sorted({item["make"] for item in records if item["make"]}),
        "camera_models": sorted({item["model"] for item in records if item["model"]}),
        "image_count": len(records),
        "capture_count": len(captures),
        "capture_start": min(times).isoformat() if times else None,
        "capture_end": max(times).isoformat() if times else None,
        "median_latitude_deg": _median(item["latitude_deg"] for item in captures),
        "median_longitude_deg": _median(item["longitude_deg"] for item in captures),
        "median_relative_altitude_m": _median(item["relative_altitude_m"] for item in captures),
        "median_gsd_nadir_approx_cm": _median(item["gsd_nadir_approx_cm"] for item in captures),
        "median_solar_zenith_deg": _median(item["solar_zenith_deg"] for item in captures),
        "median_solar_azimuth_deg": _median(item["solar_azimuth_deg"] for item in captures),
        "median_view_zenith_nadir_approx_deg": _median(item["view_zenith_nadir_approx_deg"] for item in captures),
        "median_relative_azimuth_approx_deg": _median(item["relative_azimuth_approx_deg"] for item in captures),
        "rtk_flags_raw": dict(Counter(item["rtk_flag_raw"] for item in records if item["rtk_flag_raw"])),
        "median_rtk_std_lon_raw": _median(item["rtk_std_lon_raw"] for item in records),
        "median_rtk_std_lat_raw": _median(item["rtk_std_lat_raw"] for item in records),
        "median_rtk_std_hgt_raw": _median(item["rtk_std_hgt_raw"] for item in records),
        "images_with_irradiance": sum(item["irradiance_raw"] is not None for item in records),
        "images_with_vignetting_metadata": sum(item["has_vignetting_metadata"] for item in records),
        "images_with_dewarp_metadata": sum(item["has_dewarp_metadata"] for item in records),
        "ppk_or_auxiliary_assets": ppk_assets,
        "bands": bands,
        "provenance": {
            "image_count": "Top-level filesystem enumeration of JPG/JPEG/TIF/TIFF/DNG files.",
            "capture_count": "Number of unique XMP CaptureUUID values; filename stem is used only as fallback.",
            "camera_make_model": "EXIF Make and Model.",
            "capture_time": "EXIF DateTimeOriginal with the user-supplied timezone or UTC offset.",
            "gps": "DJI XMP GpsLatitude and GpsLongitude.",
            "altitude": "DJI XMP RelativeAltitude; AbsoluteAltitude is preserved as a raw field per image.",
            "gimbal_and_flight_attitude": "DJI XMP Gimbal*Degree and Flight*Degree fields.",
            "rtk": "DJI XMP RtkFlag, RtkSrcType, RtkStdLon, RtkStdLat and RtkStdHgt, preserved as raw vendor fields.",
            "band_definition": "DJI XMP BandName, BandFreq, SensorIndex, CentralWavelength and WavelengthFWHM.",
            "irradiance": "DJI XMP Irradiance; values are preserved as raw camera metadata.",
            "vignetting": "Presence of DJI XMP VignettingData or VignettingPolynomial.",
            "dewarp": "Presence of DJI XMP DewarpData or PerspectiveDistortion.",
            "gsd": "100 * RelativeAltitude[m] / CalibratedFocalLength[pixels], nadir pinhole approximation.",
            "solar_angles": "Calculated from GPS, capture time and timezone with NOAA fractional-year equations.",
            "view_angles": "Flight-level approximation from gimbal pitch/yaw; not a per-orthomosaic-pixel angle layer.",
        },
        "geometry_notes": {
            "solar": "Computed from image local time, timezone assumption and GPS using NOAA equations.",
            "view": "Approximation from gimbal orientation; a per-pixel angle requires adjusted camera poses and DSM.",
            "rtk": "Raw vendor fields are preserved; fixed-solution accuracy requires flight/base provenance and checkpoints.",
            "srf": "Band center/FWHM metadata is not a complete spectral response function.",
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "flight_parameters.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(output_dir / "capture_metadata.csv", captures)
    _write_csv(output_dir / "band_metadata.csv", bands)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="One acquisition directory")
    parser.add_argument("--output-dir", required=True, type=Path)
    time_group = parser.add_mutually_exclusive_group(required=True)
    time_group.add_argument("--timezone", help="IANA timezone, for example Asia/Shanghai")
    time_group.add_argument("--utc-offset-hours", type=float, help="Fixed local offset from UTC, for example 8")
    parser.add_argument("--recursive", action="store_true", help="Include nested folders from the same acquisition")
    args = parser.parse_args()
    summary = extract(
        args.input,
        args.output_dir,
        args.timezone,
        args.utc_offset_hours,
        args.recursive,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
