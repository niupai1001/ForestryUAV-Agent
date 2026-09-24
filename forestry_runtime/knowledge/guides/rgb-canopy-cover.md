---
id: rgb-canopy-cover
title: RGB 正射影像的树冠分布与覆盖率
summary: 仅有 RGB 时如何分割可见树冠、计算覆盖率并标注误差；无需 NIR 或 CHM。
tags: [rgb, 可见光, 正射, 树冠, 林冠, 冠盖度, 覆盖率, tree canopy cover, crown segmentation, 林冠分割, 预览]
applies_to: [code_run, inspect_raster, segment_canopy, artifacts_preview]
version: 1
---

# RGB 正射影像的树冠分布与覆盖率

## 可回答的问题

高分辨率 RGB 正射影像可以提取**影像上可见的树冠投影**并估算其覆盖率；NIR、DSM、DTM 不是这一路线的必要输入。它们能帮助区分植被或提供高度约束，但缺失不能作为拒绝 RGB 树冠任务的理由。需要把“树冠”与“所有绿色植被”分开：草地、灌木、农作物、阴影和绿色人工物可造成混淆。若无地面或高度证据，结果应称为“RGB 可见树冠候选掩膜及覆盖率估计”。

## 方法按证据递进

1. 先读取波段、分辨率、CRS、仿射变换、有效掩膜并查看有代表性的原分辨率局部图；确定研究区分母，排除图外背景与无效像元。
2. 无标签时，可从 RGB 色彩空间、可见光植被指数（如 ExG）、纹理、对象大小和形状建立可解释的初步候选；阈值从当前影像样本或直方图确定，不能照搬通用常数。裸地、阴影、草地和灌木需专门检查。
3. 有少量人工标注时，优先比较简单阈值/对象规则与监督分类；有足量且跨场景的树冠标注时可用语义或实例分割。语义掩膜回答覆盖面积，实例分割才尝试区分相邻单木。
4. 保留原始 RGB、候选掩膜、叠加预览和方法/阈值记录。对边界、阴影、地物混淆区抽样复核，必要时修正或标注不确定区。

## 统计与表述

在面积适用的投影坐标系或等面积坐标系下，以树冠掩膜与研究区交集的面积除以研究区有效面积；若为同一规则网格且像元面积一致，可用有效树冠像元数除以研究区有效像元数。报告分子、分母、掩膜定义、像元面积/坐标系、被排除区域和百分比。像元尺寸不能从栅格宽高推断，需读仿射变换；经纬度坐标下不能把度直接当米计算面积。

没有独立标注时，可以给出**方法条件下的估计值**和预览，但不能声称已验证的准确率，也不能把 RGB 绿色掩膜直接写成林木总株数、叶面积指数或真实三维林冠闭合度。若掩膜明显包含草地，应报告“植被覆盖候选”并继续改进树冠筛选。

## 依据

- 《Ultrahigh-resolution boreal forest canopy mapping》，*International Journal of Applied Earth Observation and Geoinformation* (2022)，展示 UAV RGB 与摄影测量信息支持冠层制图，并比较经典分割基线：<https://doi.org/10.1016/j.jag.2022.102686>。
- 《Automatic tree-level based forest inventories retrieval via ultra-high resolution UAV images and deep learning》，*ISPRS Journal of Photogrammetry and Remote Sensing* (2026)，讨论 RGB 的成本/分辨率优势及光谱限制：<https://doi.org/10.1016/j.isprsjprs.2026.02.029>。
- 《A Review of Individual Tree Crown Detection and Delineation From Optical Remote Sensing Images》，*IEEE Geoscience and Remote Sensing Magazine* (2024)，综述树冠识别与分割方法及适用性：<https://doi.org/10.1109/MGRS.2024.3479871>。
