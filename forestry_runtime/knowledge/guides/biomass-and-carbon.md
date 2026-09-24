---
id: biomass-and-carbon
title: 林木生物量、碳储量与模型外推边界
summary: 遥感结构指标不能直接等于生物量；需样地、异速生长方程和不确定度。
tags: [生物量, 碳储量, agb, biomass, carbon, 异速生长, 样地, 胸径, 树高, 不确定度]
applies_to: [code_run, summarize_forest_structure]
version: 1
---

# 林木生物量、碳储量与模型外推边界

树冠面积、树高、点云密度和植被指数可作预测变量，**不直接等于**地上生物量（AGB）或碳储量。估算需要适用于目标树种/林型/尺度的异速生长方程或由外业样地标定的统计模型；不能把未经验证的通用系数套到任意森林。

先明确目标是单木、样地还是区域总量，保持标签与预测变量同一空间支持。异速方程选择、胸径/树高测量、样地位置、模型误差、单木遗漏和向区域外推都会贡献不确定度。报告模型来源、适用范围、验证样地、偏差/RMSE及不确定区间；跨林型和传感器须重新验证。

没有外业或可信校准资料时，可以提供结构指标、相对高低分布与可执行的采样设计，不能把它们改名为已验证的吨/公顷生物量或碳储量。

## 依据

- 《LIDAR-Based Forest Biomass Remote Sensing: A Review of Metrics, Methods, and Assessment Criteria for the Selection of Allometric Equations》，*Forests* (2023)：<https://doi.org/10.3390/f14102095>。
- 《Quantification of uncertainty in aboveground biomass estimates derived from small-footprint airborne LiDAR》，*Remote Sensing of Environment* (2018)：<https://doi.org/10.1016/j.rse.2018.07.022>。
