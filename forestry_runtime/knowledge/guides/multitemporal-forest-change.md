---
id: multitemporal-forest-change
title: 多时相林冠变化与扰动监测
summary: 变化图须控制配准、季节、辐射、有效区和分类误差。
tags: [变化检测, 多时相, 监测, 林冠, 扰动, 砍伐, 恢复, temporal change, co-registration, 物候]
applies_to: [code_run, inspect_raster, raster_compare]
version: 1
---

# 多时相林冠变化与扰动监测

两个日期的像元差值既包含真实地表变化，也包含配准误差、拍摄视角、阴影、季节/物候、光照和处理流程差异。先把研究区、分辨率、栅格网格、有效掩膜和类别定义统一，利用稳定地物检查几何与辐射一致性，再比较林冠掩膜、对象或结构指标。

先分别评估每期分类/分割误差；变化区往往集中在边界和阴影，应抽样复核“减少”“增加”和“不变”三类。对树冠消失可称“疑似林冠损失”，不可仅凭两期影像判定是砍伐、火灾、病害或季节性落叶。变化面积以两期共同有效且可比较的研究区为分母，并报告缺测区。

## 依据

- Torresan 等，《Forestry Remote Sensing from Unmanned Aerial Vehicles》，*Remote Sensing* (2020)：<https://doi.org/10.3390/rs12061046>。
- Roberts 等，《Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure》，*Ecography* (2017)：<https://doi.org/10.1111/ecog.02881>。
