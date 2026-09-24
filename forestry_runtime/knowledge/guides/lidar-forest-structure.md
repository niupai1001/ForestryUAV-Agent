---
id: lidar-forest-structure
title: UAV LiDAR 林分三维结构分析
summary: 点云分类、地面归一化、树高与单木候选，以及遮挡和点密度边界。
tags: [lidar, 激光雷达, 点云, ground classification, 地面点, 归一化, 树高, 单木, 林分结构]
applies_to: [code_run, build_canopy_height_model, delineate_tree_candidates, summarize_forest_structure]
version: 1
---

# UAV LiDAR 林分三维结构分析

LiDAR 多次回波和三维坐标可以提供比俯视 RGB 更直接的垂直结构信息，但“能穿透树冠”是概率性的：冠层密度、扫描角、航线和点密度决定可见地面及林下程度。没有足够地面回波时，DTM 仍可能偏差。

常见流程是检查坐标/高程基准和条带配准，过滤噪声，分类地面点，插值 DTM，以 DTM 归一化点高，再计算高度分位数、冠层密度、CHM 或单木候选。单木分割可在 CHM 或三维点云上做；两者在重叠冠、下层木和稀疏回波中具有不同遗漏/拆分误差。

输出树高、分层比例、候选树木和冠幅时，记录地面分类、归一化、点密度、阈值和边界处理。机载点云若没有直接观测树干胸径，不能把胸径作为直接测量值；需外业数据或经验证的模型推断。

## 依据

- McNicol 等，《To What Extent Can UAV Photogrammetry Replicate UAV LiDAR to Determine Forest Structure?》，*Journal of Geophysical Research: Biogeosciences* (2021)：<https://doi.org/10.1029/2021JG006586>。
- 《Performance of Individual Tree Segmentation Algorithms in Forest Ecosystems Using UAV LiDAR Data》，*Drones* (2024)：<https://doi.org/10.3390/drones8120772>。
