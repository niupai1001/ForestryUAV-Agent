---
id: thermal-forest-observation
title: 热红外林冠温度与异常筛查
summary: 热异常受观测条件影响，不能单独等同于干旱、病害或火情。
tags: [热红外, 温度, 热异常, thermal, 水分胁迫, 火情, 发射率]
applies_to: [code_run, inspect_raster]
version: 1
---

# 热红外林冠温度与异常筛查

- 热红外记录辐射温度相关信号，解释为冠层温度时需考虑发射率、反射辐射、距离和相机校准；不同飞行时刻的温度不能无条件比较。
- 热异常可作为水分胁迫、病害或火情的筛查线索，但受太阳角、风、空气湿度、土壤/天空混合像元和树冠遮挡影响。
- 优先在相近天气与时间窗口采集，并使用非异常对照区或地面测温核查；在复杂林冠中报告热异常候选而非病因确诊。
- 若目标是林火监测，还需区分活动火点、余热、暖地表与传感器饱和，不能把单个高温像元直接认定为火灾。

## 来源

- 《UAV-Based Forest Health Monitoring: A Systematic Review》，*Remote Sensing* (2022)：<https://doi.org/10.3390/rs14133205>。
- 《Airborne Optical and Thermal Remote Sensing for Wildfire Detection and Monitoring》，*Sensors* (2016)：<https://doi.org/10.3390/s16081310>。
