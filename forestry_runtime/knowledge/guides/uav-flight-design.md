---
id: uav-flight-design
title: 林区 UAV 航线与摄影测量采集设计
summary: 任务目标、重叠、地形起伏、风和冠层纹理如何影响采集方案。
tags: [航线, 重叠度, 飞行高度, gsd, 地形跟随, 摄影测量, 航测, flight planning]
applies_to: [inspect_uav_dataset, inspect_uav_source, code_run]
version: 1
---

# 林区 UAV 航线与摄影测量采集设计

- 飞行设计从目标产物和最小可辨目标出发，记录计划的地面采样距离、航向/旁向重叠与相机姿态；标称飞行高度本身不等于实际 GSD。
- 复杂植被和起伏地形增加遮挡及匹配难度，应检查地形起伏造成的实际离地高度变化、影像覆盖缺口和航线间连接，而非把某个重叠百分比当作通用保证。
- 风致树冠运动、运动模糊、快速变化的云影会影响匹配与光谱一致性；检查原片清晰度和不同时段光照。
- OpenDroneMap 的重叠建议是规划参考，不是精度证明；成果质量仍需检查控制点/检查点、匹配和成图结果。

## 来源

- OpenDroneMap《Flying Tips》：<https://docs.opendronemap.org/flying/>。
- Torresan 等，*Remote Sensing* (2020) 林业 UAV 综述：<https://doi.org/10.3390/rs12061046>。
