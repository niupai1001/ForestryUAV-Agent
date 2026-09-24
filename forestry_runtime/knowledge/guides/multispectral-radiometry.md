---
id: multispectral-radiometry
title: 多光谱反射率、植被指数与辐射一致性
summary: 波段核实、参考板/光照传感器、阴影和跨航次可比性的判据。
tags: [多光谱, 反射率, 辐射定标, 参考板, 光照, 阴影, ndvi, red edge, nir, 植被指数]
applies_to: [calculate_ndvi, inspect_uav_dataset, inspect_raster, code_run]
version: 1
---

# 多光谱反射率、植被指数与辐射一致性

植被指数能帮助识别植被或描述冠层状态，但指数不是物种、健康或覆盖率的直接真值。计算前核实波段中心/带宽、顺序、曝光和产品单位；DN、辐亮度、表观反射率和地表反射率不能混称。NDVI 要 Red 与 NIR；RGB 树冠分割不依赖 NDVI。

参考板和下行光照传感器提供辐射校正依据；云影、变光照、太阳/观测角、自动曝光及拼接可能让同一地物在影像中呈现不同数值。单次参考板采样未必能消除航程中的光照变化。跨日期/相机比较前，检查同波段定义、校准流程、阴影处理和稳定地物的一致性。

指数阈值须在当前目标与背景上验证；NDVI 高值可能来自草本或灌木，不能独立证明为乔木冠层。指数饱和、土壤背景和阴影可能使生物物理解释失真。

## 依据

- Swaminathan 等，《Radiometric calibration of UAV multispectral images under changing illumination conditions with a downwelling light sensor》，*The Plant Phenome Journal* (2024)：<https://doi.org/10.1002/ppj2.70005>。
- Torresan 等，《Forestry Remote Sensing from Unmanned Aerial Vehicles》，*Remote Sensing* (2020)：<https://doi.org/10.3390/rs12061046>。
