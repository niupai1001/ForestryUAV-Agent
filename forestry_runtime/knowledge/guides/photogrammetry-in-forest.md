---
id: photogrammetry-in-forest
title: 森林摄影测量、正射与高度模型
summary: SfM/密集匹配的林区适用性、DSM/DTM/CHM 差异和密林地面恢复限制。
tags: [摄影测量, sfm, mvs, 正射, 点云, dsm, dtm, chm, 树高, 林地, 地面模型]
applies_to: [inspect_uav_dataset, inspect_uav_products, build_canopy_height_model, code_run]
version: 1
---

# 森林摄影测量、正射与高度模型

重叠影像通过 SfM 求取相机姿态，密集匹配恢复可见表面；正射图是几何校正后的影像，DSM 表示可见表面高程，DTM 表示地面高程，CHM 是匹配基准下的相对冠层高度。正射图本身不含可靠的三维高度。

林冠纹理重复、风致枝叶运动、阴影、曝光差异和航线重叠不足会削弱匹配；密林下的地面不可见时，影像点云很难单独恢复真实 DTM。可使用独立可信地形模型，但要核查获取时点、网格、水平/垂直基准和误差。光学重建主要描述可见冠层表面，不自动给出林下结构。

检查成果时分开评估：影像连接与覆盖、控制点和独立检查点残差、正射接缝/重影、点云空洞、DTM 地面点来源、DSM/DTM 对齐及 CHM 负值。只有元数据完整不能证明定位或高度精度。

## 依据

- Graham 等，《Evaluation of Ground Surface Models Derived from Unmanned Aerial Systems with Digital Aerial Photogrammetry in a Disturbed Conifer Forest》，*Remote Sensing* (2019)：<https://doi.org/10.3390/rs11010084>。
- McNicol 等，《To What Extent Can UAV Photogrammetry Replicate UAV LiDAR to Determine Forest Structure?》，*Journal of Geophysical Research: Biogeosciences* (2021)：<https://doi.org/10.1029/2021JG006586>。
