---
id: control-and-georeferencing
title: GCP、检查点与几何精度
summary: 控制点用于约束模型，独立检查点用于评估误差；RTK 标记不能替代验证。
tags: [gcp, 检查点, 控制点, rtk, 几何精度, 定位, 坐标基准, checkpoint]
applies_to: [inspect_uav_dataset, inspect_uav_products, code_run]
version: 1
---

# GCP、检查点与几何精度

- GCP 参与几何调整，独立检查点不参与调整而用于评估成果误差；用训练模型的同一批点同时声称独立精度会产生乐观结果。
- 控制点要覆盖项目水平范围及高低地形，并在影像中清晰可辨；数量与布局依据面积、地形和重叠设计，不存在对所有林区都足够的固定点数。
- 检查点报告应区分水平与垂直误差，并写明测量仪器、坐标系、垂直基准、匹配方法和残差分布。
- RTK/PPK 相机定位可改善几何约束，但状态码或设备规格不是本次成果的独立精度评估。

## 来源

- OpenDroneMap《Ground Control Points》：<https://docs.opendronemap.org/gcp/>。
- OpenDroneMap《Outputs》：<https://docs.opendronemap.org/outputs/>。
