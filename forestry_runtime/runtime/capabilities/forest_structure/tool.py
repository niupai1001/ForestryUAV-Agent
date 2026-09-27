"""Remote-sensing capability parameter contracts and descriptions."""

from typing import Literal

from pydantic import Field, model_validator

from ..base import Args, InputRef


def _one_source(instance, value_field: str, ref_field: str) -> None:
    """Require exactly one of a legacy id field and a unified reference field."""
    if getattr(instance, ref_field) is None and not getattr(instance, value_field):
        raise ValueError(f"provide {ref_field} (scope/path/asset_id) or {value_field}")
    if getattr(instance, ref_field) is not None and getattr(instance, value_field):
        raise ValueError(f"give either {ref_field} or {value_field}, not both")
    return None


class BuildCanopyHeightArgs(Args):
    dsm: InputRef | None = Field(
        default=None,
        description='DSM输入引用。与dsm_asset_id二选一；workspace或授权源目录文件用这个。',
    )
    dtm: InputRef | None = Field(
        default=None,
        description='DTM输入引用。与dtm_asset_id二选一。',
    )
    dsm_asset_id: str | None = Field(default=None, description='DSM附件的精确资产ID（旧写法）。')
    dtm_asset_id: str | None = Field(default=None, description='DTM附件的精确资产ID（旧写法）。')
    vertical_reference: str = Field(
        min_length=3, max_length=1000,
        description='DSM与DTM元数据共同声明的垂直基准名称。',
    )

    @model_validator(mode='after')
    def one_input_each(self):
        _one_source(self, 'dsm_asset_id', 'dsm')
        _one_source(self, 'dtm_asset_id', 'dtm')
        return self

class DelineateTreeCandidatesArgs(Args):
    chm: InputRef | None = Field(
        default=None, description='CHM输入引用。与chm_asset_id二选一。',
    )
    chm_asset_id: str | None = Field(default=None, description='CHM资产的精确资产ID（旧写法）。')
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
        _one_source(self, 'chm_asset_id', 'chm')
        return self

class SummarizeForestStructureArgs(Args):
    chm: InputRef | None = Field(default=None, description='CHM输入引用。与chm_asset_id二选一。')
    labels: InputRef | None = Field(default=None, description='候选标签输入引用。与labels_asset_id二选一。')
    chm_asset_id: str | None = Field(default=None, description='候选分割所用的CHM资产ID（旧写法）。')
    labels_asset_id: str | None = Field(default=None, description='delineate_tree_candidates返回的候选冠层标签GeoTIFF（旧写法）。')

    @model_validator(mode='after')
    def one_input_each(self):
        _one_source(self, 'chm_asset_id', 'chm')
        _one_source(self, 'labels_asset_id', 'labels')
        return self

DEFINITIONS = {
    'build_canopy_height_model': (
        BuildCanopyHeightArgs,
        '以DSM减去对齐后的DTM生成单波段冠层高度模型。要求两者为投影米制GeoTIFF，'
        '并必须提供共同高程单位和垂直基准的证据；保留并报告负高程差，不静默截断。'
        'DSM和DTM可以是附件（dsm_asset_id/dtm_asset_id），也可以是workspace文件或'
        '已授权源目录中的文件（dsm/dtm引用，scope+path+asset_id+source_id）。',
    ),
    'delineate_tree_candidates': (
        DelineateTreeCandidatesArgs,
        '在CHM上做候选冠层分割与单木候选提取；输出是候选，不是实测株数。'
        'CHM可以是附件chm_asset_id，也可以是chm引用。',
    ),
    'summarize_forest_structure': (
        SummarizeForestStructureArgs,
        '汇总CHM与候选标签的统计量；两个输入都可以用引用或资产ID给出。',
    ),
}
