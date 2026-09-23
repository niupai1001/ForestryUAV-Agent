"""Remote-sensing capability parameter contracts and descriptions."""

from typing import Literal

from pydantic import Field, model_validator

from ..base import Args


class UavSource(Args):
    kind: Literal['folder', 'uploads'] = Field(
        description=(
            'folder读取白名单目录；uploads读取当前聊天已上传的影像或ZIP。'
        )
    )
    folder_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        description=(
            'kind=folder时必填。可以是完整Windows路径，或相对'
            'UAV_INPUT_HOST_ROOT的路径。'
        ),
    )
    source_id: str | None = Field(
        default=None,
        pattern=r'^grant_[0-9a-f]{32}$',
        description=(
            'kind=folder时引用fs_list返回的已授权宿主机目录。可单独使用以选择授权根，'
            '也可和相对folder_path一起使用以选择根目录下的子目录。'
        ),
    )
    recursive: bool = Field(
        default=False,
        description='folder默认只读顶层；确认子目录属于同一航次时才设为true。',
    )
    asset_ids: list[str] = Field(
        default_factory=list,
        max_length=1000,
        description=(
            'kind=uploads时可选。留空使用当前聊天全部影像/ZIP；'
            '有多批附件时填写通用fs_list返回的精确asset_id以选择一批。'
        ),
    )

    @model_validator(mode='after')
    def validate_kind(self):
        if self.kind == 'folder':
            if not self.folder_path and not self.source_id:
                raise ValueError('kind=folder时必须提供folder_path或source_id')
            if self.asset_ids:
                raise ValueError('kind=folder时不能提供asset_ids')
        elif self.folder_path is not None or self.source_id is not None or self.recursive:
            raise ValueError(
                'kind=uploads时不能提供folder_path、source_id或recursive=true'
            )
        return self

class UavDatasetArgs(Args):
    folder_path: str | None = Field(
        default=None, min_length=1, max_length=1000,
        description='待盘点数据集的完整Windows路径，或source_id根下的相对路径。',
    )
    source_id: str | None = Field(
        default=None, pattern=r'^grant_[0-9a-f]{32}$',
        description='已授权只读目录的精确引用。',
    )
    max_files: int = Field(default=5000, ge=1, le=20000)

    @model_validator(mode='after')
    def require_source(self):
        if not self.folder_path and not self.source_id:
            raise ValueError('必须提供folder_path或source_id')
        return self

class UavProductsArgs(Args):
    folder_path: str | None = Field(
        default=None, min_length=1, max_length=1000,
        description='已有遥感成果所在目录，或source_id根下的相对路径。',
    )
    source_id: str | None = Field(
        default=None, pattern=r'^grant_[0-9a-f]{32}$',
        description='已授权只读目录的精确引用。',
    )
    name_filter: str | None = Field(
        default=None, max_length=200,
        description='可选文件名子串，例如1605；只用于缩小检查范围，不做模糊替换。',
    )
    max_products: int = Field(default=32, ge=1, le=128)

    @model_validator(mode='after')
    def require_source(self):
        if not self.folder_path and not self.source_id:
            raise ValueError('必须提供folder_path或source_id')
        return self

DEFINITIONS = {
    'inspect_uav_source': (
        UavSource,
        '统一检查无人机数据源。kind=folder可直接使用用户明确要求的folder_path，'
        '或使用fs_list返回的source_id；uploads读取当前聊天附件。'
        '返回相机、影像文件数、航片组数、GPS、波段和辐射元数据；只读且不启动处理。'
        'ready只表示基础文件/元数据检查未发现阻断项，不代表摄影测量必然成功，'
        '也不能据此否定该目录或其他目录已经存在历史正射成果。'
        'capture_count是同步曝光组数，image_file_count是影像文件数。'
        'RTK字段只报告未分类元数据数量，缺少官方码表时不解释其定位质量。',
    ),
    'inspect_uav_dataset': (
        UavDatasetArgs,
        '递归盘点一个已授权林业UAV数据集，按主航线、起飞前/起飞后参考板、'
        '地理成果和未分类栅格返回精确相对路径与独立采集组。只读，不启动处理；'
        '不得把参考板、RGB和多光谱批次因共享父目录而混为一个输入。成功结果已是'
        '完整盘点，应直接据此回答；relative_path不是asset_id。',
    ),
    'inspect_uav_products': (
        UavProductsArgs,
        '递归检查已有正射、DSM、DTM、CHM或NDVI GeoTIFF/VRT的CRS、尺寸、'
        '像元大小、范围、波段、NoData、有界像元抽样和基础元数据QA；同时判断'
        'DSM/DTM是否在栅格网格上对齐。它不生成正射、不证明几何精度，视觉接缝、'
        '冠层变形和测量级精度仍需GIS/GCP检查。成功结果已是完整有界检查，应直接'
        '据此回答；products[].relative_path不是asset_id，不要再调用资产检查工具。',
    ),
}
