---
id: leaf-area-and-gap-fraction
title: 叶面积指数、冠层孔隙与遮阴
summary: LAI、孔隙率、冠盖度互不等价；间接反演受聚集和木质组分影响。
tags: [lai, 叶面积指数, gap fraction, 孔隙率, 遮阴, 聚集, clumping, 林冠]
applies_to: [code_run, calculate_ndvi, summarize_forest_structure]
version: 1
---

# 叶面积指数、冠层孔隙与遮阴

- LAI 表示叶面积与地表面积的比值；冠盖度表示俯视树冠投影比例；孔隙率描述特定视角未被冠层遮挡的比例。三者不是可互换的百分比。
- 从鱼眼照片、冠层仪或光学遥感估计 LAI 时，要说明观测角度、叶片空间聚集、木质组分和阴影假设。
- 在稀疏或高度异质的林分中，简单均匀冠层假设可能偏差；结果应与独立地面观测比较，并区分有效 LAI 与真实 LAI。
- 单张 RGB 正射影像上的绿色像元比例不能直接命名为 LAI。

## 来源

- Jonckheere 等，《Review of methods for in situ leaf area index determination: Part II》，*Agricultural and Forest Meteorology* (2004)：<https://doi.org/10.1016/j.agrformet.2003.08.001>。
- Yan 等，《Review of indirect optical measurements of leaf area index》，*Agricultural and Forest Meteorology* (2019)：<https://doi.org/10.1016/j.agrformet.2018.11.033>。
