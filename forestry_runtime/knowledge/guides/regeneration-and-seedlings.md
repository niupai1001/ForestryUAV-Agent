---
id: regeneration-and-seedlings
title: 幼苗、更新与造林成效遥感
summary: 幼苗检测受分辨率和遮挡限制；成活率需要个体追踪或样地证据。
tags: [幼苗, 更新, 造林, 成活率, seedling, regeneration, restoration, 林下]
applies_to: [code_run, segment_canopy, inspect_raster]
version: 1
---

# 幼苗、更新与造林成效遥感

- 幼苗是否可见取决于冠幅相对像元大小、背景、阴影和上层遮挡；林下幼苗不能由俯视 RGB 的“未检测”推断为不存在。
- UAV RGB/多光谱可以提供可见幼苗候选、空间分布和局部密度；需要独立样地估计漏检率与误检率。
- “成活率”要求定义初始个体队列并在后续时相逐株匹配或使用有设计的样地调查；两个日期的绿色像元数量之比不是成活率。
- 更新评价须说明天然更新与人工造林、目标树种、物候、地块边界和观察日期。

## 来源

- 《UAV Applications in Forest Regeneration Survey: A Review and Case Study》，*Remote Sensing* (2026)：<https://doi.org/10.3390/rs18173027>。
- 《UAV-Supported Forest Regeneration: Current Trends, Challenges and Implications》，*Remote Sensing* (2021)：<https://doi.org/10.3390/rs13132596>。
