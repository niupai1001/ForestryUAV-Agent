---
id: model-transfer-and-uncertainty
title: 跨林区模型迁移与不确定度报告
summary: 空间独立验证、域偏移和结果不确定区不能由训练分数替代。
tags: [模型迁移, 域偏移, 不确定度, 外部验证, spatial validation, transfer, generalization]
applies_to: [code_run, segment_canopy, delineate_tree_candidates]
version: 1
---

# 跨林区模型迁移与不确定度报告

- 同一航次相邻瓦片高度相关，随机切分通常高估跨地点泛化；测试划分应匹配实际部署目标（新地块、新季节或新传感器）。
- 在新林型、分辨率、光照或相机上使用模型前，检查输入分布和外部样本表现；原论文或训练集准确率不是新地点的准确率。
- 概率分数、像元熵或模型集成差异可作不确定性线索，但未经校准不能直接解释为错误概率。
- 汇报地图时标出未覆盖、云影/阴影、混合像元和低置信度区域；在独立参考样本上按空间和类别分层计算误差。

## 来源

- Roberts 等，《Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure》，*Ecography* (2017)：<https://doi.org/10.1111/ecog.02881>。
- Olofsson 等，《Good practices for estimating area and assessing accuracy of land change》，*Remote Sensing of Environment* (2014)：<https://doi.org/10.1016/j.rse.2014.02.015>。
