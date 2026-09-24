---
id: fire-severity-and-recovery
title: 火烧迹地、烧毁程度与恢复监测
summary: 烧毁范围、严重度和恢复是不同目标；指数与 RGB 痕迹需现场核验。
tags: [森林火灾, 烧毁面积, 烧毁程度, 恢复, fire severity, burn severity, dNBR]
applies_to: [code_run, inspect_raster, calculate_ndvi]
version: 1
---

# 火烧迹地、烧毁程度与恢复监测

- 烧毁范围是空间分类，烧毁程度描述生态/植被受损程度，后续恢复描述随时间的变化；不能把黑色像元面积直接称作严重度。
- RGB 可识别部分炭化、灰烬和冠层损失；含 NIR/SWIR 的多光谱可支持相应指数。使用 dNBR 前必须确认 NIR 与 SWIR 波段及火前/火后影像，RGB 无法计算标准 dNBR。
- 阴影、裸地、深色岩石和火前林况会造成混淆。严重度等级需要说明参考标准和外业/高质量参考验证。
- 恢复监测应控制季节、时间间隔、配准和研究区，区分草本返青与乔木恢复。

## 来源

- 《Remote Sensing of Forest Burnt Area, Burn Severity, and Post-Fire Recovery: A Review》，*Remote Sensing* (2022)：<https://doi.org/10.3390/rs14194714>。
- 《A Review of the Applications of Remote Sensing in Fire Ecology》，*Remote Sensing* (2019)：<https://doi.org/10.3390/rs11222638>。
