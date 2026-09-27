"""Remote-sensing capability parameter contracts and descriptions."""

from typing import Literal

from pydantic import Field, model_validator

from ..base import Args


class AssetArgs(Args):
    scope: Literal['auto', 'workspace', 'asset', 'source'] = Field(
        default='auto',
        description=(
            '输入所在的范围。默认auto：给出asset_id时按附件解析，给出绝对Windows'
            '路径时按已授权源目录解析，其余按workspace相对路径解析。'
            '也可以直接照抄上一个工具结果里的exact_reference。'
        ),
    )
    asset_id: str | None = Field(
        default=None,
        description='附件的精确asset_id；workspace文件改用path。',
    )
    path: str = Field(
        default='',
        description=(
            'workspace相对路径（如 oam-02.tif，不要加workspace/前缀），'
            '或用户明确要求使用的绝对Windows路径。'
        ),
    )
    source_id: str | None = Field(
        default=None,
        description='已授权源目录的source_id；与path一起使用。',
    )

class BandSelection(Args):
    red: int | None = Field(default=None, ge=1)
    green: int | None = Field(default=None, ge=1)
    blue: int | None = Field(default=None, ge=1)
    rededge: int | None = Field(default=None, ge=1)
    nir: int | None = Field(default=None, ge=1)
    alpha: int | None = Field(default=None, ge=1)

class RasterBandArgs(AssetArgs):
    bands: BandSelection = Field(
        description=(
            '按光谱角色填写从1开始的波段编号，只填写当前操作需要的角色；'
            '必须依据影像元数据或用户明确指定，不能按波段数量猜测。'
        )
    )

class InspectRasterArgs(AssetArgs):
    band_indices: list[int] = Field(
        default_factory=list,
        max_length=16,
        description=(
            '需要扫描的从1开始波段编号；留空时扫描全部波段，'
            '但单次最多16个波段。'
        ),
    )

class InspectRasterRegionArgs(AssetArgs):
    band: int = Field(default=1, ge=1, description='要查看的从1开始的波段编号。')
    window: list[int] | None = Field(
        default=None, max_length=4,
        description=(
            '像元窗口 [col_off, row_off, width, height]；留空时使用整幅影像（仍会降采样）。'
            '用于检查局部空间结果。'
        ),
    )
    max_size: int = Field(default=512, ge=64, le=1024, description='缩略图最长边像素数。')
    stretch_percentiles: list[float] = Field(
        default_factory=lambda: [2.0, 98.0], max_length=2,
        description='显示拉伸所用的分位数下界与上界。',
    )


class SegmentCanopyArgs(AssetArgs):
    threshold: float | None = Field(
        default=None,
        ge=-1,
        le=1,
        description=(
            'NDVI阈值。留空时使用有效NDVI直方图自动计算Otsu阈值；'
            '只有明确实验配置时才手工指定。'
        ),
    )

DEFINITIONS = {
    'inspect_file': (
        AssetArgs,
        '检查真实文件。接受workspace文件、附件或已授权源目录中的文件，'
        '用同一组参数（scope/path/asset_id/source_id）引用，'
        '并回传exact_reference供下一步直接使用。'
        'TIFF返回尺寸、CRS、分辨率、从1开始的波段编号及'
        '原始描述、NoData和数据集掩膜有效像元统计。明确波段描述是元数据'
        '证据，不得仅凭数量猜测含义。nodata=null不等于没有有效数据。'
        '有效区域统计不代表林冠分割，也不验证反射率定标。'
        'PNG/JPG返回尺寸。',
    ),
    'inspect_raster': (
        InspectRasterArgs,
        '对真实GeoTIFF执行像元级数据条件检查。分块精确统计各波段的掩膜、'
        'NaN/Inf、零值、负值、最小值、最大值和均值，并返回确定性抽样分位数。'
        '当元数据明确标记Red、NIR和Alpha时，还检查NDVI零分母与Alpha背景的'
        '重合关系。用于定量分析或分割前的数据判断，不生成新文件。',
    ),
    'inspect_raster_region': (
        InspectRasterRegionArgs,
        '对栅格的一个受控窗口（或整幅降采样）生成单波段缩略图，并返回同一批像元的'
        '有效数、最小/最大/均值与分位数，供模型查看空间结果而不是只看统计数字。'
        '缩略图同时登记为图片资产，用户可在界面直接查看。'
        '它只说明空间排列，不表示精度、也不表示分类或分割结果的含义。',
    ),
    'inspect_zip': (
        AssetArgs,
        '列出ZIP内容并检查路径、加密、符号链接、文件数量及解压大小限制。',
    ),
    'extract_zip': (
        AssetArgs,
        '将通过检查的ZIP文件解压并登记为独立文件资产，返回子文件asset_id。',
    ),
    'preview_image': (
        AssetArgs,
        '生成TIFF/PNG/JPG最长边1024像素的PNG预览。'
        'TIFF默认前3波段，仅供预览，不代表真彩色或分析结果。',
    ),
    'calculate_ndvi': (
        RasterBandArgs,
        '按窗口计算NDVI=(NIR-Red)/(NIR+Red)，生成单波段float32 GeoTIFF。'
        'bands中必须提供red和nir，编号从1开始，依据已有元数据或用户指定选择；'
        '工具会应用源文件记录的scale和offset，联合检查两波段掩膜、有限值和非零分母。'
        'NDVI表示植被指数，不等于林冠Mask或林冠覆盖率。',
    ),
    'segment_canopy': (
        SegmentCanopyArgs,
        '对calculate_ndvi生成的单波段NDVI GeoTIFF执行初步阈值分割。'
        '默认使用Otsu自动阈值，输出uint8候选林冠Mask：1=候选、0=有效非候选、'
        '255=无效。该基线不能区分树冠与草本植被，不能直接视为最终林冠产品。',
    ),
}
