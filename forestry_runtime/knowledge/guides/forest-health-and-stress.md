---
id: forest-health-and-stress
title: 林木健康、胁迫与病虫害遥感判读
summary: 可见症状、光谱和热异常能支持筛查；病因判定需要独立证据。
tags: [林木健康, 病虫害, 胁迫, 枯死, 失绿, 热红外, 植被指数, forest health, disease, stress]
applies_to: [code_run, inspect_raster, calculate_ndvi]
version: 1
---

# 林木健康、胁迫与病虫害遥感判读

RGB 可发现失绿、褐变、落叶和死亡冠等**可见症状**；多光谱/高光谱可能对色素和冠层生理变化更敏感；热红外可提供温度异常线索。但异常可能由干旱、物候、阴影、坡向、土壤、相机处理或病虫害多种因素造成，单期影像不能单独确定病因。

应先提出明确的目标类别和观测窗口，检查光照、空间分辨率、树冠分割与对照区，再用独立外业诊断/多时相证据验证。可输出“疑似异常树冠/优先巡查区域”，不得把模型概率或指数阈值直接称为病害确诊率。

变化监测须避免把季节、降雨后温度、太阳角和不同相机标定带来的差异当作真实健康变化。

## 依据

- 《UAV-Based Forest Health Monitoring: A Systematic Review》，*Remote Sensing* (2022)：<https://doi.org/10.3390/rs14133205>。
- Torresan 等，《Forestry Remote Sensing from Unmanned Aerial Vehicles》，*Remote Sensing* (2020)：<https://doi.org/10.3390/rs12061046>。
