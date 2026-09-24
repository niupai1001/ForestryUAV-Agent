---
id: forest-method-boundaries
title: CHM、冠层分水岭与候选单木边界
summary: CHM 的构建基线、负值处理要求，以及为什么分水岭输出只能称为“可见上层冠层候选”。
tags: [chm, 冠层高度, 树高, dsm, dtm, 分水岭, watershed, 单木, 树冠, 林分密度, 株数, 精度, 验证]
applies_to: [build_canopy_height_model, delineate_tree_candidates, summarize_forest_structure]
version: 1
---

# CHM、冠层分水岭与候选单木边界

## CHM 基线

CHM 的基线是 `DSM - DTM`，要求同一水平网格、同一高程单位、同一垂直基准。
这只约束高度模型路线。若任务是**俯视树冠分布或覆盖率**，可用 RGB 正射影像分割
可见树冠，不必先有 CHM；详见 `rgb-canopy-cover` 与 `canopy-metrics-and-ecology`。

负值必须保留并报告其比例：负 CHM 比例会暴露配准偏差、地面模型偏差和插值问题。
不得静默截断为零。在密林条件下，DTM 的地面分类可能成为主要误差来源，必须记录
地面分类来源（例如 OpenDroneMap 的 `dtm` 参数）以及水平坐标系、高程单位和垂直基准。

## 标记分水岭的性质

标记分水岭把像元值当作地形，从给定标记向外扩张，直到区域在水岭线相遇。
因此峰值位置与数量直接决定候选结果；噪声、平滑尺度、峰值最小间距、高度阈值和
冠层面积过滤都会改变结果。

在冠层 CHM 上，一个局部峰值**不一定**对应一棵真实树木：相邻树冠可能合并，
一个大树冠可能产生多个峰值，被压制的下层不可见，空洞和树冠摆动会产生虚假峰值。

因此输出必须命名为“可见上层冠层候选”，不得直接当作林木总株数。

## 每次运行必须记录的内容

CHM 来源、有效研究区、像元大小、高度阈值、平滑尺度、峰值最小间距、候选冠层面积范围，
以及每一项参数的来源。

没有独立外业样地或人工标注时，只能报告候选数量、有效区上的密度与分布，
**不得报告召回率、准确率或总株数误差**。

## 依据来源

- scikit-image 标记分水岭示例：<https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_watershed.html>
