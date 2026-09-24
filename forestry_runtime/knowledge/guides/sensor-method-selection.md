---
id: sensor-method-selection
title: 林业任务的传感器与方法选型
summary: RGB、多光谱、热红外、摄影测量和 LiDAR 各能回答哪些林业问题。
tags: [无人机, 林业, rgb, 多光谱, hyperspectral, lidar, 激光雷达, 热红外, 传感器, 方法选择]
applies_to: [code_run, inspect_uav_source, inspect_raster, domain_guide]
version: 1
---

# 林业任务的传感器与方法选型

先确定目标量，再选输入与方法；传感器名称本身不保证结果质量。

| 输入 | 可优先尝试 | 主要边界 |
| --- | --- | --- |
| RGB 正射 | 可见树冠/林隙、颜色纹理、对象分割、可见症状 | 无 NIR 指数；草灌与树冠可能混淆；单期 RGB 难以证明病因 |
| 多光谱/高光谱 | 反射率、物种/生理差异、植被指数 | 要核实波段响应、辐射校准、阴影和时相 |
| 热红外 | 冠层温度与相对热异常 | 受天气、时刻、发射率、混合像元影响，不直接等于水分胁迫或病害 |
| 影像摄影测量 | 表层点云/DSM、正射、可见上层结构 | 稠密冠层下地面和下层难恢复；高度依赖可信 DTM |
| LiDAR | 三维点云、冠层高度与垂直结构、一定条件下的地面回波 | 点密度/扫描几何/遮挡影响结果；仍需分类、配准和验证 |

同一目标可有多条路线。例如树冠覆盖可由 RGB 分割、多光谱分类或高度约束分割得到；只有树高/三维结构问题才必须有相应高程信息。缺某传感器时先评估现有输入能否得到**定义更窄但有用的结果**，并说明输出所代表的对象。

## 依据

- Torresan 等，《Forestry Remote Sensing from Unmanned Aerial Vehicles: A Review Focusing on the Data, Processing and Potentialities》，*Remote Sensing* (2020)：<https://doi.org/10.3390/rs12061046>。
- McNicol 等，《To What Extent Can UAV Photogrammetry Replicate UAV LiDAR to Determine Forest Structure?》，*Journal of Geophysical Research: Biogeosciences* (2021)：<https://doi.org/10.1029/2021JG006586>。
