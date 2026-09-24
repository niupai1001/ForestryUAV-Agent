---
id: geospatial-product-qa
title: 已有正射与高程成果质量检查
summary: 自动元数据检查能证明什么、不能证明什么，以及 DSM/DTM 配准与 NoData 的判读方式。
tags: [正射, 成果, 质量, 检查, orthomosaic, orthophoto, dsm, dtm, chm, crs, 坐标系, nodata, 掩膜, mask, 配准, 分辨率, 像元大小, 度, 米, 单位]
applies_to: [inspect_raster, inspect_uav_products, artifacts_inspect, build_canopy_height_model]
version: 1
---

# 已有正射与高程成果质量检查

## 自动检查覆盖的范围

第一阶段自动检查包括：栅格可读性，以及坐标系、仿射变换、宽高、像元大小、范围、
波段数、波段描述、数据类型、NoData/掩膜、有效像元比例。

CRS 与像元到世界坐标的仿射变换是**两个独立的定位要素**。CRS 正确但变换无效，
不表示成果定位正确。NoData 值、alpha 通道、内部掩膜和外部掩膜都可以表示无效区域；
`nodata=null` 不能证明不存在背景或缺测。
像元尺寸应由仿射变换和 CRS 单位判读，不能从影像宽高或文件体积猜测。
EPSG 代码应查询其正式定义，不能凭地理位置猜名称；例如 EPSG:3395 是
WGS 84 / World Mercator，而不是中国大地坐标系。

统计时必须区分两套掩膜约定：rasterio 的掩膜数组以 `True` 表示无效，而 GDAL 的
有效数据掩膜以非零表示有效。

## DSM 与 DTM 的配对

构建 CHM 之前必须确认 DSM 与 DTM 共享坐标系、栅格尺寸、仿射网格和覆盖范围，
或者存在**有记录**的重采样过程，并且共享高程单位与垂直基准。

文件名中出现 DSM/DTM 不构成上述任何一项的证据。

## 自动检查的边界

自动检查不能替代在原生分辨率下的目视检查：接缝、空洞、重影、冠层拖影、边缘畸变、
曝光台阶和多光谱错配应结合目视检查；其中部分异常也可由自动指标提示，不能称为
“只能目视发现”。

绝对几何精度不能由内嵌 GPS 或 CRS 推断。如果精度重要，需要分布良好的地面控制点
和独立检查点（OpenDroneMap 指南：平面与垂直均匀覆盖，通常 ≥5 个点，且每个点在
多张影像中可见）。

## 依据来源

- OpenDroneMap 输出目录与成果定义：<https://docs.opendronemap.org/outputs/>
- OpenDroneMap 地面控制点指南：<https://docs.opendronemap.org/gcp/>
- EPSG:3395 定义：<https://epsg.io/3395>
