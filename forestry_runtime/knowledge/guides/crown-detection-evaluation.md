---
id: crown-detection-evaluation
title: 单木检测、树冠分割与评价指标
summary: 框、掩膜和树顶点的目标不同，精度评估要匹配输出类型。
tags: [单木检测, 树冠分割, bounding box, mask, iou, precision, recall, f1, 实例分割]
applies_to: [delineate_tree_candidates, segment_canopy, code_run]
version: 1
---

# 单木检测、树冠分割与评价指标

- 检测框用于定位候选树木，树冠掩膜用于面积与边界，树顶点用于位置；框面积不能直接作为精确冠幅面积。
- 评价前约定预测与参考树冠的一对一匹配、空间容差或 IoU 阈值。漏检、误检、相邻冠合并和单冠拆分应分别统计。
- 语义分割像元精度与单木实例召回率回答不同问题；总体像元准确率高不能证明每棵树都被找到。
- 参考标注和测试区域须独立于训练，按林型、密度、冠层重叠和树冠大小分层检查误差；没有参考标注时只能给候选与人工质检，不得给实测召回率。

## 来源

- 《Deep Learning for Tree Crown Detection and Delineation...: A Systematic Review》，*Forests* (2026)：<https://doi.org/10.3390/f17020179>。
- 《How to assess the accuracy of the individual tree-based forest inventory derived from remotely sensed data: a review》，*International Journal of Remote Sensing* (2016)：<https://doi.org/10.1080/01431161.2016.1214302>。
