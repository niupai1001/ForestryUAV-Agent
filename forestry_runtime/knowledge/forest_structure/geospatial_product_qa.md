# 已有正射与高程成果质量检查

## 自动元数据检查

正射影像、DSM、DTM、CHM 首先应可被栅格库读取，并记录 CRS、仿射变换、宽高、像元大小、范围、波段数、波段描述、数据类型、NoData/掩膜和有效像元比例。Rasterio 将 CRS 与像元到世界坐标的变换视为地理配准的两个独立组成部分；仅有 CRS 而缺少有效变换，不能认为成果已正确定位。

来源：<https://rasterio.readthedocs.io/en/stable/topics/georeferencing.html>

NoData 数值、alpha、内部 mask 和外部 mask 都可能表示无效区域，不能因为 `nodata=null` 就断言没有背景或缺测。Rasterio 的 masked array 中 `True` 表示无效，GDAL 有效数据 mask 则以非零表示有效，统计时必须区分。

来源：<https://rasterio.readthedocs.io/en/stable/topics/masks.html>

## 成果角色与 CHM 前提

OpenDroneMap 官方输出定义中，正射为 GeoTIFF，DSM 表示地物表面，DTM 表示地面。无论成果由何种软件生成，构建 CHM 前都必须确认 DSM 与 DTM 的 CRS、栅格尺寸、仿射网格和覆盖范围一致或经过有记录的重采样，还必须确认共同高程单位和垂直基准。文件名中出现 DSM/DTM 不是这些条件的证据。

来源：<https://docs.opendronemap.org/outputs/>

## 自动检查不能替代的内容

可读、投影明确且像元有效的 GeoTIFF 仍可能存在接缝、空洞、重影、冠层拉花、边缘变形、曝光台阶或多光谱错位，需要在 GIS 中查看原始分辨率和关键区域。绝对几何精度不能从嵌入 GPS 或 CRS 推断；若精度重要，应使用分布合理的地面控制点和独立检查点。OpenDroneMap 官方指南建议 GCP 在平面和高程上均匀覆盖，通常至少五个，并使每个点出现在多张影像中。

来源：<https://docs.opendronemap.org/gcp/>
