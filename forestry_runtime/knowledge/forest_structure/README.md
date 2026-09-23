# 林分结构首轮知识清单

本目录是维护者审核后的首轮本地知识入口。它覆盖林业UAV数据角色、已有地理成果质量、CHM与候选单木的方法选择和验证边界，不定义树种、生物量或胸径模型。专题内容见 `uav_dataset_roles.md`、`geospatial_product_qa.md` 和 `forest_method_boundaries.md`。

## 处理与质量依据

- OpenDroneMap `dtm` 参数：<https://docs.opendronemap.org/arguments/dtm/>
- OpenDroneMap 输出目录和成果定义：<https://docs.opendronemap.org/outputs/>
- OpenDroneMap 地面控制点指南：<https://docs.opendronemap.org/gcp/>
- OpenDroneMap 硬件与资源要求：<https://docs.opendronemap.org/installation/>

选择 DTM 时必须记录地面分类来源、水平坐标系、高程单位和垂直基准。DSM 与 DTM 文件存在只说明处理器生成了成果，不说明密林下裸地估计或树高精度已经得到验证。

## 候选单木基线

- scikit-image 标记分水岭示例：<https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_watershed.html>

首轮算法使用平滑 CHM 的局部峰值作为标记，再以分水岭分割相连冠层。高度阈值、平滑尺度、峰值最小间距和候选冠层面积范围必须在每次运行中显式记录来源。输出称为“可见上层冠层候选”，不等同于林木总株数。

## 公开验证数据

- Open Forest Observatory 数据目录：<https://openforestobservatory.org/data/>
- OFO STAC API：<https://stac.cyverse.org>
- 首轮样例 STAC Item：`Open Forest Observatory / 000913`

样例 000913 提供原始航片以及公开的正射、DSM、DTM 和 CHM。第一步使用公开栅格验证知识引用与结构分析；第二步才用原始航片重建。公开处理成果用于结果对照，不作为独立地面真值。使用数据前应再次读取 STAC Item 的许可、资产链接和版本时间。

## 交付解释

林分结构报告至少给出输入资产、坐标系、像元大小、有效区、缺测比例、负 CHM 比例、全部算法参数及来源。候选覆盖率的分母是明确的研究区有效像元；没有独立外业或人工标注时不报告准确率。
