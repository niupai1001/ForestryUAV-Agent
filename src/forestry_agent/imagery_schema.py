"""Explicit scientific declarations; never infer reflectance or band order from a filename."""
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Boundary

PARAMETERS = ("n", "cab", "car", "cbrown", "cw", "cm", "lai", "lidfa", "hspot", "rsoil", "psoil")
LEGACY_RANGES = {"n": (1.2, 2.2), "cab": (20, 70), "car": (4, 18), "cbrown": (0, .5),
    "cw": (.005, .035), "cm": (.004, .020), "lai": (.3, 7), "lidfa": (30, 70),
    "hspot": (.01, .30), "rsoil": (.5, 1.5), "psoil": (0, 1)}


class Band(Boundary):
    name: str = Field(min_length=1, max_length=80)
    center_nm: float = Field(ge=400, le=2500, allow_inf_nan=False)
    half_width_nm: float = Field(gt=0, le=200, allow_inf_nan=False)

    @model_validator(mode="after")
    def interval(self):
        if self.center_nm - self.half_width_nm < 400 or self.center_nm + self.half_width_nm > 2500:
            raise ValueError("响应窗口必须在400–2500nm内")
        return self


class LutDeclaration(Boundary):
    parameter_source: str = Field(min_length=3, max_length=2000)
    geometry_source: str = Field(min_length=3, max_length=2000)
    sensor_response_source: str = Field(min_length=3, max_length=2000)
    ranges: dict[str, tuple[float, float]]
    bands: list[Band] = Field(min_length=2, max_length=16)
    solar_zenith: float = Field(ge=0, lt=90, allow_inf_nan=False)
    view_zenith: float = Field(ge=0, lt=90, allow_inf_nan=False)
    relative_azimuth: float = Field(ge=0, le=360, allow_inf_nan=False)
    lut_size: int = Field(default=500, ge=10, le=100000)
    seed: int = Field(default=20260914, ge=0, le=2147483647)

    @model_validator(mode="after")
    def parameters(self):
        import math
        if set(self.ranges) != set(PARAMETERS):
            raise ValueError("必须明确旧版11个参数范围")
        for name, (low, high) in self.ranges.items():
            if not all(math.isfinite(v) for v in (low, high)) or low > high or low < 0:
                raise ValueError("参数范围须有限、非负且min≤max")
            if (name == "n" and low <= 0) or (name == "lidfa" and high > 90) or (name == "psoil" and high > 1):
                raise ValueError("PROSAIL参数物理范围无效")
        if len({b.name for b in self.bands}) != len(self.bands):
            raise ValueError("波段名称不能重复")
        return self


class InversionDeclaration(Boundary):
    reflectance: Literal["calibrated_fraction_0_1"]
    calibration_source: str = Field(min_length=3, max_length=2000)
    band_mapping_source: str = Field(min_length=3, max_length=2000)
    band_names: list[str] = Field(min_length=2, max_length=16)
    raster_bands: list[int] = Field(min_length=2, max_length=16)
    alpha_band: int | None = Field(default=None, ge=1)
    ndvi_threshold: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    red_name: str = "Red"
    nir_name: str = "NIR"

    @model_validator(mode="after")
    def mapping(self):
        if (len(self.band_names) != len(self.raster_bands) or len(set(self.band_names)) != len(self.band_names)
                or len(set(self.raster_bands)) != len(self.raster_bands) or min(self.raster_bands) < 1):
            raise ValueError("波段映射须为唯一的一一对应")
        if self.alpha_band in self.raster_bands:
            raise ValueError("Alpha不能同时作为反射率波段")
        if self.ndvi_threshold is not None and not {self.red_name, self.nir_name} <= set(self.band_names):
            raise ValueError("NDVI掩膜需要明确Red/NIR波段")
        return self


class OdmDeclaration(Boundary):
    processing_mode: Literal["rgb", "multispectral"]
    source_note: str = Field(min_length=3, max_length=2000)
    radiometric_calibration: Literal["camera", "camera+sun"] = "camera"
    orthophoto_resolution_cm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    dsm: bool = True


def validate_declaration(operation, value):
    return {"build_lut": LutDeclaration, "invert": InversionDeclaration,
            "orthomosaic": OdmDeclaration}[operation].model_validate(value).model_dump()
