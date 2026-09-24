"""Read-only delivery of one trial's artifacts for grounded scoring.

The grader never starts the tested runtime, never imports it, and never executes
code the agent wrote. It opens the files the collector downloaded and decodes the
pixels. That is the whole surface, which is why the grader can be rerun on stored
evidence and produce the same report.

Grid policy, frozen with the question bank:

* the delivered raster must have the same ``crs`` *semantically* (``EPSG:4326``
  and ``WGS 84`` are one CRS, not two), and
* its cell grid and affine transform must match the frozen input within a tight
  tolerance.

A nominally different but equivalent CRS is therefore not punished, and a raster
that is offset, mirrored or resampled is. A delivery in a *different projection*
fails the grid requirement outright: resampling it would place it on the frozen
grid by the mere act of reprojection, which could let a wrong answer score as a
right one. The rule the pilot declares is "same CRS, same grid"; the grader
enforces exactly that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

#: Artifacts the collector stores are named ``asset_<id>-<original name>``.
ARTIFACT_PREFIX = "asset_"

VALID_CLASS_VALUES = {0, 1}


def delivered_name(path: Path) -> str:
    """Recover the artifact's original file name from ``asset_<id>-<name>``."""
    stem, separator, remainder = path.name.partition("-")
    return remainder if separator and stem.startswith(ARTIFACT_PREFIX) else path.name


@dataclass
class RasterDelivery:
    name: str
    path: str
    band_count: int | None = None
    dtype: str | None = None
    declared_crs: str | None = None
    shape: list[int] | None = None
    transform: list[float] | None = None
    nodata: float | None = None
    class_values_ok: bool | None = None
    observed_values: list[int] = field(default_factory=list)
    aligned: bool = False
    resampled: bool = False
    resampled_fraction: float | None = None
    reason: str = ""
    array: np.ndarray | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "band_count": self.band_count,
            "dtype": self.dtype, "declared_crs": self.declared_crs, "shape": self.shape,
            "transform": self.transform, "nodata": self.nodata,
            "class_values_ok": self.class_values_ok, "observed_values": self.observed_values,
            "aligned": self.aligned, "resampled": self.resampled,
            "resampled_fraction": self.resampled_fraction, "reason": self.reason,
        }


@dataclass
class JsonDelivery:
    name: str
    path: str
    payload: dict | None = None
    error: str = ""
    canopy_pixels: float | None = None
    valid_pixels: float | None = None
    coverage_percent: float | None = None
    raw_fields: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "error": self.error,
            "canopy_pixels": self.canopy_pixels, "valid_pixels": self.valid_pixels,
            "coverage_percent": self.coverage_percent, "raw_fields": self.raw_fields,
        }


def _numeric(value: object) -> float | None:
    """Accept ``22.4``, ``"22.4"``, ``"22.4%"`` and ``"1,234"``; refuse the rest."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").rstrip("%").strip()
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _find_field(payload: object, names: tuple[str, ...]) -> float | None:
    """First numeric value under any accepted key, searched depth-first."""
    if isinstance(payload, dict):
        for name in names:
            for key, value in payload.items():
                if str(key).strip().casefold() == name:
                    number = _numeric(value)
                    if number is not None:
                        return number
        for value in payload.values():
            number = _find_field(value, names)
            if number is not None:
                return number
    elif isinstance(payload, list):
        for value in payload:
            number = _find_field(value, names)
            if number is not None:
                return number
    return None


CANOPY_KEYS = ("canopy_pixels", "canopy_pixel_count", "canopypixels", "tree_canopy_pixels",
               "树冠像元数", "canopy_count")
VALID_KEYS = ("valid_pixels", "valid_pixel_count", "total_valid_pixels", "validpixels",
              "有效像元数", "valid_count")
COVERAGE_KEYS = ("coverage_percent", "canopy_coverage_percent", "coverage", "canopy_fraction_percent",
                 "覆盖率", "coverage_pct", "canopy_coverage")


def align_to_grid(
    path: Path, *, target_crs, target_shape: tuple[int, int], target_transform,
    shape_tolerance: float = 0.0, transform_tolerance: float = 1e-6,
    value_domain: str = "canopy",
) -> RasterDelivery:
    """Decode one GeoTIFF and express it on the frozen grid.

    ``value_domain`` is ``"canopy"`` for a 0/1 class raster, ``"continuous"`` for
    a measured surface such as ruggedness.
    """
    delivery = RasterDelivery(name=delivered_name(path), path=path.name)
    try:
        with rasterio.open(path) as source:
            delivery.band_count = source.count
            delivery.dtype = source.dtypes[0] if source.count else None
            delivery.declared_crs = None if source.crs is None else str(source.crs)
            delivery.shape = [source.height, source.width]
            delivery.transform = [float(value) for value in source.transform[:6]]
            delivery.nodata = None if source.nodata is None else float(source.nodata)
            if source.count < 1:
                delivery.reason = "the raster has no band"
                return delivery
            if source.crs is None:
                delivery.reason = "the raster declares no coordinate reference system"
                return delivery
            band = source.read(1)
    except Exception as error:  # noqa: BLE001 - a broken delivery is a finding, not a crash
        delivery.reason = f"the raster could not be opened: {type(error).__name__}: {error}"
        return delivery

    if delivery.nodata is not None:
        delivery.reason = (f"the raster declares nodata={delivery.nodata}, but a 0/1 class "
                           "raster must not mark pixels as no-data")
        delivery.class_values_ok = False
        return delivery

    if value_domain == "canopy":
        observed = np.unique(band)
        delivery.observed_values = [int(value) for value in observed[:16]]
        delivery.class_values_ok = bool(set(int(value) for value in observed) <= VALID_CLASS_VALUES)
        if not delivery.class_values_ok:
            delivery.reason = ("the class raster uses values outside {0, 1}: "
                               f"{delivery.observed_values}")

    same_grid = (
        tuple(delivery.shape) == tuple(target_shape)
        and all(abs(float(value) - float(expected)) <= transform_tolerance
                for value, expected in zip(delivery.transform, list(target_transform)[:6]))
    )
    if source_crs_equivalent(delivery.declared_crs, target_crs) and same_grid:
        delivery.aligned = True
        delivery.array = band
        return delivery

    if not source_crs_equivalent(delivery.declared_crs, target_crs):
        # A delivery whose CRS is not equivalent was never placed on the frozen
        # grid. Resampling it would "bring it into line" by the mere act of
        # reprojection and could make a wrong answer score as a right one, so the
        # grid requirement fails here instead.
        delivery.reason = (
            f"the raster declares {delivery.declared_crs}, which is not equivalent to the "
            f"frozen input's {target_crs}"
        )
        return delivery

    delivery.reason = (
        "the raster is on a different grid than the frozen input: "
        f"shape {delivery.shape} vs {list(target_shape)}, transform "
        f"{delivery.transform} vs {[float(value) for value in list(target_transform)[:6]]}"
    )
    return delivery


def source_crs_equivalent(left: str | None, right) -> bool:
    """Semantic CRS equality: ``EPSG:4326`` and ``WGS 84`` are the same CRS."""
    if left is None or right is None:
        return False
    try:
        from rasterio.crs import CRS

        return CRS.from_user_input(left) == CRS.from_user_input(right)
    except Exception:  # noqa: BLE001 - unparseable CRS is simply not equivalent
        return False


def load_json_delivery(path: Path, *, keys: tuple[tuple[str, tuple[str, ...]], ...]) -> JsonDelivery:
    """Read one JSON delivery and pull the declared numbers out of it."""
    delivery = JsonDelivery(name=delivered_name(path), path=path.name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        delivery.error = f"the JSON delivery could not be parsed: {type(error).__name__}: {error}"
        return delivery
    if not isinstance(payload, dict):
        delivery.payload = {"value": payload}
        delivery.error = "the JSON delivery is not an object"
        return delivery
    delivery.payload = payload
    delivery.raw_fields = {str(key): value for key, value in payload.items()
                           if isinstance(value, (int, float, str, bool)) or value is None}
    for attribute, candidates in keys:
        setattr(delivery, attribute, _find_field(payload, tuple(name.casefold() for name in candidates)))
    return delivery


def discover_artifacts(directory: Path) -> list[Path]:
    """Every downloaded artifact, in a stable order."""
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file())


def reference_artifact_names(inputs: list[str]) -> set[str]:
    """Names that are inputs rather than deliveries, so they are never graded."""
    names = set()
    for item in inputs:
        name = Path(item).name
        names.add(name)
        names.add(Path(name).stem)
    return names


__all__ = [
    "ARTIFACT_PREFIX", "CANOPY_KEYS", "COVERAGE_KEYS", "VALID_CLASS_VALUES", "VALID_KEYS",
    "JsonDelivery", "RasterDelivery", "align_to_grid", "delivered_name",
    "discover_artifacts", "load_json_delivery", "reference_artifact_names",
    "source_crs_equivalent",
]
