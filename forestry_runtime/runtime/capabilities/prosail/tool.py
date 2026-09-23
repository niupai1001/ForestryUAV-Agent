"""Remote-sensing capability parameter contracts and descriptions."""

from typing import Literal

from pydantic import Field, model_validator

from ..base import Args
from ..raster.tool import AssetArgs


PROSAIL_PARAMETER_NAMES = (
    'n', 'cab', 'car', 'cbrown', 'cw', 'cm', 'lai', 'lidfa',
    'hspot', 'ant', 'alpha', 'rsoil', 'psoil',
)

class ProsailState(Args):
    n: float = Field(gt=0, description='叶片结构参数N；必须由用户或知识来源提供。')
    cab: float = Field(ge=0, description='叶绿素a+b含量Cab；必须由用户或知识来源提供。')
    car: float = Field(ge=0, description='类胡萝卜素含量Car；必须由用户或知识来源提供。')
    cbrown: float = Field(ge=0, description='褐色素参数Cbrown；必须由用户或知识来源提供。')
    cw: float = Field(ge=0, description='叶片等效水厚度Cw；必须由用户或知识来源提供。')
    cm: float = Field(ge=0, description='叶片干物质含量Cm；必须由用户或知识来源提供。')
    lai: float = Field(ge=0, description='叶面积指数LAI；必须由用户或知识来源提供。')
    lidfa: float = Field(ge=0, le=90, description='平均叶倾角LIDFa（度）；必须由用户或知识来源提供。')
    hspot: float = Field(ge=0, description='热点参数hspot；必须由用户或知识来源提供。')
    ant: float = Field(ge=0, description='花青素含量Ant；必须由用户或知识来源提供。')
    alpha: float = Field(gt=0, le=90, description='PROSPECT表面反射参数alpha；必须由用户或知识来源提供。')
    rsoil: float = Field(ge=0, description='土壤亮度因子rsoil；必须由用户或知识来源提供。')
    psoil: float = Field(ge=0, le=1, description='干湿土壤混合系数psoil；必须由用户或知识来源提供。')

class ProsailGeometry(Args):
    solar_zenith: float = Field(ge=0, le=89, description='太阳天顶角（度）。')
    view_zenith: float = Field(ge=0, le=89, description='观测天顶角（度）。')
    relative_azimuth: float = Field(ge=0, le=360, description='太阳与观测方向相对方位角（度）。')

class ProsailModelSettings(Args):
    prospect_version: Literal['5', 'D'] = Field(description='明确选择PROSPECT-5或PROSPECT-D。')
    typelidf: Literal[2] = Field(description='当前工具支持的叶倾角分布类型；调用方必须明确传入2。')
    factor: Literal['SDR', 'BHR', 'DHR', 'HDR'] = Field(description='PROSAIL输出反射因子类型。')

class SensorBandpass(Args):
    name: str = Field(min_length=1, max_length=80, description='调用方定义的唯一波段名称。')
    lower_nm: float = Field(ge=400, le=2500, description='矩形响应下界（nm）。')
    upper_nm: float = Field(ge=400, le=2500, description='矩形响应上界（nm）。')

    @model_validator(mode='after')
    def ordered_bounds(self):
        if self.upper_nm <= self.lower_nm:
            raise ValueError('upper_nm必须大于lower_nm')
        return self

class ParameterRange(Args):
    minimum: float = Field(description='参数下限；固定参数可与maximum相等。')
    maximum: float = Field(description='参数上限；固定参数可与minimum相等。')

    @model_validator(mode='after')
    def ordered_bounds(self):
        if self.maximum < self.minimum:
            raise ValueError('maximum不能小于minimum')
        return self

class SimulateProsailArgs(Args):
    parameters: ProsailState
    geometry: ProsailGeometry
    model: ProsailModelSettings
    sensor_bands: list[SensorBandpass] = Field(min_length=1, max_length=16)
    parameter_source: str = Field(min_length=3, max_length=1000, description='参数值来自哪个用户输入、文件或知识库条目。')
    geometry_source: str = Field(min_length=3, max_length=1000, description='观测几何的来源。')
    sensor_response_source: str = Field(min_length=3, max_length=1000, description='波段范围或SRF近似的来源。')

    @model_validator(mode='after')
    def unique_bands(self):
        names = [band.name for band in self.sensor_bands]
        if len(names) != len(set(names)):
            raise ValueError('sensor_bands名称必须唯一')
        return self

class BuildProsailLutArgs(Args):
    parameter_ranges: dict[str, ParameterRange] = Field(
        description='必须包含n、cab、car、cbrown、cw、cm、lai、lidfa、hspot、ant、alpha、rsoil、psoil的上下限。'
    )
    geometry: ProsailGeometry
    model: ProsailModelSettings
    sensor_bands: list[SensorBandpass] = Field(min_length=1, max_length=16)
    lut_size: int = Field(ge=10, le=100000)
    seed: int = Field(ge=0, le=2147483647)
    sampling: Literal['uniform_random', 'latin_hypercube']
    parameter_source: str = Field(min_length=3, max_length=1000, description='全部参数范围来自哪个用户输入、文件或知识库条目。')
    geometry_source: str = Field(min_length=3, max_length=1000, description='观测几何的来源。')
    sensor_response_source: str = Field(min_length=3, max_length=1000, description='波段范围或SRF近似的来源。')

    @model_validator(mode='after')
    def complete_inputs(self):
        if set(self.parameter_ranges) != set(PROSAIL_PARAMETER_NAMES):
            raise ValueError('parameter_ranges必须完整且只能包含全部13个PROSAIL参数')
        for endpoint in ('minimum', 'maximum'):
            ProsailState.model_validate({
                name: getattr(bounds, endpoint)
                for name, bounds in self.parameter_ranges.items()
            })
        names = [band.name for band in self.sensor_bands]
        if len(names) != len(set(names)):
            raise ValueError('sensor_bands名称必须唯一')
        return self

class RasterLutBand(Args):
    lut_band: str = Field(min_length=1, max_length=80)
    raster_band: int = Field(ge=1)

class ProsailInversionArgs(AssetArgs):
    lut_asset_id: str = Field(description='build_prosail_lut返回的精确LUT资产ID。')
    band_mapping: list[RasterLutBand] = Field(min_length=1, max_length=16)
    target_parameters: list[Literal[
        'n', 'cab', 'car', 'cbrown', 'cw', 'cm', 'lai', 'lidfa',
        'hspot', 'ant', 'alpha', 'rsoil', 'psoil',
    ]] = Field(min_length=1, max_length=13)
    alpha_band: int | None = Field(default=None, ge=1)
    neighbors: int = Field(ge=1, le=100)
    band_mapping_source: str = Field(min_length=3, max_length=1000, description='栅格波段与LUT波段对应关系的证据来源。')

    @model_validator(mode='after')
    def unique_selections(self):
        lut_bands = [item.lut_band for item in self.band_mapping]
        raster_bands = [item.raster_band for item in self.band_mapping]
        if len(lut_bands) != len(set(lut_bands)) or len(raster_bands) != len(set(raster_bands)):
            raise ValueError('LUT波段和栅格波段映射都必须唯一')
        if len(self.target_parameters) != len(set(self.target_parameters)):
            raise ValueError('target_parameters不能重复')
        return self

DEFINITIONS = {
    'simulate_prosail': (
        SimulateProsailArgs,
        '执行一次PROSAIL正演并输出完整光谱CSV和调用方定义波段的聚合反射率。'
        '全部叶片、冠层、土壤、观测几何、模型选项和波段范围均为必填；'
        '它们必须来自用户、已读取文件或知识库证据，缺失时不要调用或猜测。',
    ),
    'build_prosail_lut': (
        BuildProsailLutArgs,
        '根据调用方完整提供的13个PROSAIL参数范围、观测几何、模型选项和波段范围生成LUT资产。'
        '本工具只生成LUT，不读取影像、不执行反演。参数范围及来源均必填；'
        '没有用户、文件或知识库依据时应先获取依据，不得代填通用植被或树种默认值。',
    ),
    'invert_prosail': (
        ProsailInversionArgs,
        '使用已有PROSAIL LUT资产对反射率GeoTIFF执行加权近邻反演，'
        '输出调用方指定参数和光谱RMSE的GeoTIFF。它不生成LUT、不分割植被、不生成预览。'
        'LUT波段与栅格波段的映射必须由用户、影像元数据或知识库证据提供，不能按数量猜测。',
    ),
}
