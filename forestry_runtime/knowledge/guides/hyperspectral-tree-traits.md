---
id: hyperspectral-tree-traits
title: 高光谱树种与叶片性状分析
summary: 高光谱的窄波段优势、标定需求、维度问题与地面性状验证。
tags: [高光谱, hyperspectral, 树种, 叶片性状, 光谱, 波段选择, dimensionality]
applies_to: [code_run, inspect_raster]
version: 1
---

# 高光谱树种与叶片性状分析

- 高光谱提供密集窄波段，可用于研究树种差异和叶片光学性状；其优势取决于传感器信噪比、几何/辐射校准和目标的实际可分性。
- 波段多不等于样本信息充分。样本量有限时需预先控制特征选择与模型复杂度，并把特征选择放进训练折内，防止测试集泄漏。
- 冠层混合像元、阴影、物候和观测角度会改变光谱；跨季节/地点迁移前需要独立测试。
- 光谱推断的生化参数应以外业叶片测量或可信参考验证；仅凭反演输出不能宣称已直接测量。

## 来源

- 《Exploiting hyperspectral and multispectral images in the detection of tree species: A review》，*Frontiers in Remote Sensing* (2023)：<https://doi.org/10.3389/frsen.2023.1136289>。
- Jacquemoud 等，*Remote Sensing of Environment* (2009)：<https://doi.org/10.1016/j.rse.2008.01.026>。
