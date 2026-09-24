---
id: tree-species-mapping
title: 无人机树种识别与物候信息
summary: RGB/多光谱/结构特征的树种分类条件、训练标签和跨季节迁移限制。
tags: [树种, 物种, 分类, 物候, 季节, tree species, rgb, multispectral, hyperspectral, 标签]
applies_to: [code_run, inspect_raster, segment_canopy]
version: 1
---

# 无人机树种识别与物候信息

树种识别依赖可区分的冠层颜色、纹理、物候、光谱或结构，以及可信树种标签。RGB 可在合适物候和高空间分辨率下作为输入；多光谱/高光谱可增加光谱信息，LiDAR 可增加结构信息，但更多波段不自动保证更高准确率。

先确定分类单位是像元、树冠对象还是样地。训练标签应来自独立外业或可核查的专家标注；相邻像元/同一树冠不可跨训练和测试集。跨林型、季节、传感器或地区时要用外部样地重评估，不把原地点精度外推。易混树种可合并到可辨类别，但须明确报告分类层级和未知类。

多季节影像可能利用物候差异改善可分性，前提是配准和辐射/季节一致性可接受。无标签时只能做探索性聚类或提出候选，不能宣称确认树种。

## 依据

- 《Fusing multi-season UAS images with convolutional neural networks to map tree species in Amazonian forests》，*Ecological Informatics* (2022)：<https://www.sciencedirect.com/science/article/pii/S1574954122002655>。
- Roberts 等，《Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure》，*Ecography* (2017)：<https://doi.org/10.1111/ecog.02881>。
