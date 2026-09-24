---
id: forest-gaps-and-habitat
title: 林隙、结构异质性与栖息地代理指标
summary: 可从冠层和点云描述林隙/结构，不能直接等同生物多样性真值。
tags: [林隙, 斑块, 破碎化, 栖息地, 生物多样性, 结构异质性, gap, habitat]
applies_to: [code_run, segment_canopy, summarize_forest_structure]
version: 1
---

# 林隙、结构异质性与栖息地代理指标

- 林隙提取前须定义最小面积、研究尺度、冠层高度或覆盖阈值；不同定义会得到不同林隙数量和面积。
- RGB/正射可描述可见冠层开口，CHM/LiDAR 可增加高度和垂直层次信息；阴影或裸地不自动等于生态林隙。
- 结构异质性指标可作为栖息地条件的代理变量，但不能直接等于物种丰富度、种群数量或栖息地质量。
- 建立生态关系需要独立物种/生境观测，并考虑林型、空间尺度和观测偏差。

## 来源

- 《Assessing biodiversity using forest structure indicators based on airborne laser scanning data》，*Forest Ecology and Management* (2023)：<https://www.sciencedirect.com/science/article/pii/S0378112723006102>。
- 《Recent Advances in Unmanned Aerial Vehicles Forest Remote Sensing—Part II》，*Forests* (2021)：<https://doi.org/10.3390/f12040397>。
