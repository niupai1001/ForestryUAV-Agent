"""Remote-sensing capability parameter contracts and descriptions."""

from typing import Literal

from pydantic import Field, model_validator

from ..base import Args


class BuildCanopyHeightArgs(Args):
    dsm_asset_id: str = Field(description='DSM GeoTIFF的精确资产ID。')
    dtm_asset_id: str = Field(description='DTM GeoTIFF的精确资产ID。')
    vertical_reference: str = Field(
        min_length=3, max_length=1000,
        description='DSM与DTM元数据共同声明的垂直基准名称。',
    )

class DelineateTreeCandidatesArgs(Args):
    chm_asset_id: str = Field(description='build_canopy_height_model返回的CHM资产ID。')
    minimum_height_m: float = Field(gt=0, le=100)
    smoothing_sigma_m: float = Field(gt=0, le=20)
    minimum_peak_distance_m: float = Field(gt=0, le=50)
    minimum_crown_area_m2: float = Field(gt=0, le=10000)
    maximum_crown_area_m2: float | None = Field(default=None, gt=0, le=50000)
    parameter_source: str = Field(
        min_length=3, max_length=1000,
        description='高度、平滑、峰值间距和冠幅面积参数的用户输入或知识库来源。',
    )

    @model_validator(mode='after')
    def crown_area_order(self):
        if (
            self.maximum_crown_area_m2 is not None
            and self.maximum_crown_area_m2 < self.minimum_crown_area_m2
        ):
            raise ValueError('maximum_crown_area_m2不能小于minimum_crown_area_m2')
        return self

class SummarizeForestStructureArgs(Args):
    chm_asset_id: str = Field(description='候选分割所用的CHM资产ID。')
    labels_asset_id: str = Field(description='delineate_tree_candidates返回的候选冠层标签GeoTIFF。')

DEFINITIONS = {
    'build_canopy_height_model': (
        BuildCanopyHeightArgs,
        '以DSM减去对齐后的DTM生成单波段冠层高度模型。要求两者为投影米制GeoTIFF，'
        '并必须提供共同高程单位和垂直基准的证据；保留并报告负高程差，不静默截断。',
    ),
    'delineate_tree_candidates': (
        DelineateTreeCandidatesArgs,
        '在有界CHM上使用高斯平滑、局部峰值和标记分水岭生成可见上层冠层候选。'
        '所有米制参数及来源必填；输出不能解释为林木总株数或经过地面验证的单木清查。',
    ),
    'summarize_forest_structure': (
        SummarizeForestStructureArgs,
        '根据CHM和候选冠层标签计算候选数量、有效面积内密度、候选覆盖率、树高和等效冠幅分布，'
        '同时输出CSV与JSON。研究区分母是CHM有效像元，不等同于样地或林班边界。',
    ),
}
