# 低空林草遥感全流程智能体前沿研究综述

> 检索截止：2026-08-31。主题范围严格限定为：无人机光学影像为主、无自主飞控、森林先于草地、智能体方法创新+可运行原型。

## 1. 已有资料盘点与检索策略

### 1.1 本地资料盘点

工作区初始没有论文、文献库、代码、数据集或研究笔记。因此本综述不继承未经核验的本地结论，所有关键判断重新链接到论文原文、会议/期刊页、官方仓库、官方数据页或正式标准。

### 1.2 检索问题簇

检索按以下概念簇交叉组合，而不是仅检索项目名：

1. `remote sensing / Earth observation / geospatial / GIS` × `agent / copilot / tool use / workflow / MCP`；
2. `UAV / drone / low-altitude / photogrammetry` × `agent / automation / quality / GCP / RTK / SfM / MVS`；
3. `forest / individual tree crown / tree species / canopy cover` × `UAV RGB / DSM / instance segmentation / benchmark`；
4. `grassland / vegetation cover / bare ground / degradation / change` × `UAV RGB / semantic segmentation`；
5. `planning / task graph / procedural knowledge / memory / failure recovery / clarification` × `geospatial`；
6. 点名系统逐一做名称消歧、论文状态、代码、数据和指标核验。

时间上以 2023-01-01 至 2026-08-31 为重点，必要时纳入 ReAct、W3C PROV、OGC API Processes、STAC、DeepForest 等更早但仍构成方法基础的工作。

### 1.3 来源与证据分级

- A 级：正式论文全文、出版社/会议页、标准正文、作者官方仓库和官方数据集。
- B 级：作者项目页、机构正式页面、经过身份核验的模型卡。
- C 级：论文索引和检索摘要，仅用于发现；关键结论不单独依赖 C 级。
- 预印本单独标注，不把 arXiv DOI 当作同行评审 DOI。
- 同名系统不合并。例如本综述中的 GeoAgent 指 Chen 等“自动地理空间分析”版本，不是地址标准化、图像地理定位或 QGIS 社区插件。

### 1.4 检索可达性记录

- Crossref 与 arXiv 预检可访问；PubMed 在本机出现自签名证书链错误。该故障不影响本主题的大多数 CS/RS 论文，但已记录。
- OpenAlex 批量补检遇到 HTTP 429，未把失败结果当成“没有文献”。改由论文/会议页和官方仓库交叉核验。
- `smartforest.bjfu.edu.cn` 于 2026-08-31 DNS 无法解析，搜索引擎没有返回可核验项目页。北京林业大学公开教学论文能旁证其开展林草视觉、大模型问答库与教学平台实践，但不能旁证该域名项目的具体功能。

## 2. 研究版图概览

近三年形成了四条相互靠近、但尚未真正汇合的路线：

1. **视觉工具编排**：RS-ChatGPT、Tree-GPT、Change-Agent、RS-Agent 把检测/分割/分类模型包装成可调用工具。
2. **地理空间工作流规划**：GeoAgent、GIS Copilot、GeoBenchX、Spatial-Agent 等强调 GIS 代码、工具序列和空间概念。
3. **综合 EO 智能体与基准**：ThinkGeo、Earth-Agent/Earth-Bench、CangLing-KnowFlow、OpenEarth-Agent 推进到多步科学工作流、程序知识、恢复和开放工具环境。
4. **UAV/意图/经验专门化**：RemoteAgent 研究模糊 EO 意图与能力边界；UAV-MAS 研究 UAV 单图尺度/朝向/密度和迭代验证；GeoForge 研究非参数经验演化。

共同缺口是：几乎都从“可直接解释的影像或产品”开始，而不是从具有相机网络、EXIF/POS、RTK/GCP、重建质量和空间血缘的原始 UAV 照片集开始；也几乎没有把“程序运行正确—空间产品正确—林草结论正确”同时作为可重算验收对象。

## 3. 按研究阶段整理的前沿依据

## 阶段0：边界、数据和评价基准

### 3.1 智能体与 benchmark 设计

| 研究 | 证据与贡献 | 对本阶段的直接启示 | 不足 |
|---|---|---|---|
| [GeoBenchX](https://arxiv.org/abs/2503.18129)（2025；后发表于 ACM workshop） | 约 202 个 GIS 多步任务，23 个函数；含刻意不可完成任务；比较参考工具轨迹 | 必须纳入不可完成/需补数据任务，不能只测成功案例 | 主要依赖 LLM-as-judge，不重算全部空间终值；无 UAV/林草 |
| [ThinkGeo](https://arxiv.org/abs/2505.23752)（2025，预印本） | 436 个遥感 agent 任务、14 个工具，逐步与端到端两种评价 | 评价需求理解、工具、参数、步骤和最终答案应分开 | 主要是卫星/航空图像上的短链任务 |
| [Earth-Agent/Earth-Bench](https://arxiv.org/abs/2509.23141)（ICLR 2026） | 248 个专家任务、13,729 幅图、104 工具；轨迹和终值双层评价；平均 5.4 步 | 可借鉴 Auto-Planning/Instruction-Following 双设置，以及 Tool-Any/In-Order/Exact/Parameter 指标 | 不覆盖照片配准、RTK/GCP、正射/DSM 几何质量和林草参数 |
| [CangLing-KnowFlow](https://arxiv.org/abs/2512.15231)（2025，预印本） | KnowFlow-Bench 324 个 workflow；程序知识、失败恢复和记忆消融 | benchmark 应有故障注入、恢复成本和经验复用 | 高层工作流真值不一定等价于参数级空间正确 |
| [UAVQA-Bench](https://arxiv.org/abs/2608.11738)（2026，预印本） | 13 个 UAV 数据集、1,500 人工 QA、6 能力维度/16 任务 | UAV 的尺度变化、任意朝向、高密目标应纳入视觉子基准 | 单图 QA/grounding，不是摄影测量全流程 |
| [ACEBench](https://aclanthology.org/2025.findings-emnlp.697/)（EMNLP 2025 Findings） | 将正常、歧义/缺失、多智能体多轮工具使用分开评价 | 可建立低空林草版“明确—歧义—不可行—中途变更”需求集 | 非地理空间领域 |

### 3.2 首个森林闭环的数据与视觉基线

推荐“树冠实例分割→数量、冠幅/面积、覆盖度→空间格局→地图/报告”，而不是一开始加入树高、胸径、蓄积量或全树种体系。原因是 RGB 正射影像可直接支持冠层水平几何，DSM 可作为可选增强；树高需要可靠 DTM/地面高程，密闭林分中由 RGB 摄影测量得到 DTM 常不可靠；胸径和蓄积量则需要区域/树种专属异速生长模型与实测标定。

| 数据/工具 | 类型与开放性 | 用途 | 风险 |
|---|---|---|---|
| [BAMFORESTS](https://doi.org/10.3390/rs16111935) | 2024 UAV 超高分辨率影像、树冠实例/树种、校正 DSM | 原型验证 RGB/DSM、多任务和分辨率鲁棒性 | 区域和树种有限，不代替本地数据 |
| [ForestSeg/TreeCoG](https://pubmed.ncbi.nlm.nih.gov/41559312/) | 2026 UAV RGB，2,944 标注图像，多季节热带林 | 检验季节与森林结构差异 | 数据许可、下载完整性需实施前复核 |
| [UAV LiDAR+RGB dense mixed forest dataset](https://doi.org/10.1038/s41598-024-72669-5) | 2024 开放 UAV LiDAR 点云和 RGB 正射 | 可作几何/单木位置旁证，不把 LiDAR 变成主输入 | 模态超出首阶段主边界；仅作辅助验证 |
| [NeonTreeEvaluation](https://zenodo.org/records/5914554) | 航空 RGB、LiDAR、CHM、hyperspectral 和树冠标注 | 检测/分割通用性外部测试 | 非 UAV，GSD/生态区与本地任务不同 |
| [OAM-TCD](https://arxiv.org/abs/2407.11743) | 全球多样高分辨率航空树冠数据、模型与 benchmark 代码 | 检验跨区域域偏移 | 标签质量和成像尺度不等于 UAV 林分调查 |
| [DeepForest](https://github.com/weecology/DeepForest) | 开源 airborne RGB 树冠检测包和预训练模型 | 检测基线、快速原子技能 | 默认模型跨林型不保证可用，输出框不足以可靠算冠幅 |
| [detectree2](https://github.com/PatBall1/detectree2) | Mask R-CNN 树冠实例分割、模型园和训练流程 | 实例分割强基线 | 依赖域适配；密冠重叠仍困难 |
| [SAM2 tree-detection-framework](https://github.com/open-forest-observatory/tree-detection-framework) | SAM 2.1 自动 mask 与 DeepForest/detectree2 benchmark 代码 | 视觉基础模型工具化和无需/少量标注基线 | 通用 SAM mask 不天然等于生态意义上的单木冠 |

### 3.3 指标分层

- 需求层：意图槽位 F1、关键约束召回率、正确澄清率、过度澄清率、不可完成识别 AUROC/F1。
- 计划层：合法 DAG 比例、必要步骤召回、顺序/依赖准确率、参数/单位准确率、计划编辑距离。
- 视觉层：冠实例 AP50/AP75、mask AP、对象级 F1、计数 MAE/RMSE、面积/冠幅误差。
- 空间层：CRS/extent/resolution 一致率、几何有效率、位置 RMSE、拓扑错误、重投影后面积偏差。
- 林草结论层：覆盖度 MAE、样地/林分统计偏差、空间格局指标误差、置信区间覆盖率。
- 系统层：任务成功率、首轮成功率、恢复率、误恢复率、恢复调用/时间/成本、可复现率。

## 阶段1：标准遥感产品输入的小任务智能体

### 3.4 遥感视觉工具化

- [Remote Sensing ChatGPT](https://arxiv.org/abs/2401.09083) 以任务描述编排 caption、分类、检测、实例/语义分割、计数和 Canny 等工具，证明“模型即工具”的可行性；不足是工具和模型选择较浅，空间元数据不是核心。
- [Tree-GPT](https://doi.org/10.5194/isprs-archives-XLVIII-1-W2-2023-1729-2023) 将 SAM、树木结构参数数据库、林业知识和本地代码执行组合；它与本研究应用最接近，但只有小规模原型示例，缺少标准化 agent 可靠性实验。
- [RS-Agent](https://arxiv.org/abs/2406.07089) 以 Central Controller、27 工具、Solution Space 和 Knowledge Space 构建任务类型驱动的规划；其 34 个 solution 文档包括 27 个单工具和 7 个多工具模板。对本研究最有价值的是把“任务程序知识”和“解释性领域知识”分开。
- [Change-Agent](https://doi.org/10.1109/TGRS.2024.3425815) 说明专用视觉模型可以同时提供像素掩膜和语言描述；草地变化阶段可复用其“像素证据+语义解释”思想，但不能直接迁移卫星建筑变化模型。
- [Earth-Agent](https://github.com/opendatalab/Earth-Agent) 用 MCP 统一 104 个工具。MCP 适合作为互操作层，但协议本身不提供领域正确性；工具描述仍须增加 CRS、GSD、单位、许可范围、成本、前后置条件和质量契约。

### 3.5 模型是否需要训练

第一阶段宜采用三层路线：

1. **零样本/现成工具基线**：DeepForest、detectree2 预训练权重、SAM/SAM2 自动 mask、通用/遥感视觉编码器；用于建立下限和识别域偏移。
2. **轻量微调主线**：对树冠实例分割模型做本地少量标注微调或参数高效适配。这很可能是必须的，因为冠层形态、GSD、季节、阴影和树种结构变化大。
3. **专用新模型仅在证据充分时**：如果 RGB+DSM 明显优于 RGB 且现有结构无法有效融合，再研究 DSM-aware 模型。已有 2025 年工作显示 DSM 有潜力提升 SAM 树冠分割，[Assessing SAM](https://arxiv.org/abs/2503.20199) 和 [Bringing SAM to new heights](https://arxiv.org/abs/2506.04970) 可作方法线索，但二者为预印本。

不建议为树木数量、冠幅、覆盖度另训端到端回归模型；这些量优先由经过验证的实例 mask 和 GIS 几何计算得到，便于审计。

## 阶段2：原始照片到报告的森林闭环

### 3.6 摄影测量工具与自动化基础

| 工具/标准 | 可用能力 | 在智能体中的角色 | 不能算作方法创新的部分 |
|---|---|---|---|
| [OpenDroneMap/ODM](https://docs.opendronemap.org/) | 开源 SfM/MVS；正射、DSM/DTM、点云、质量报告；可分阶段 rerun | 首选可复现实验后端；参数空间和错误日志可用于恢复实验 | 直接调用默认流水线 |
| [WebODM API](https://docs.webodm.org/api/task/) | 上传照片、异步任务、状态、错误、options、产品下载、从部分阶段重跑 | 适合原型中的真实长任务管理和可观测性 | UI、队列和文件下载本身 |
| [ODM options](https://docs.webodm.org/options-flags/) | GCP、geo/POS、force-gps、RTK、阶段选择、特征类型、DEM/ortho/report | 可将工具参数转换为有类型的技能契约 | 简单把 CLI 参数暴露给 LLM |
| [Metashape Python API](https://wiki.agisoft.com/wiki/Python) | 商业后端；匹配、对齐、深度、点云、DEM、正射、报告与 batch | 第二摄影测量后端，用于工具替换和跨后端一致性 | 商业软件批处理脚本本身 |
| [MicMac](https://github.com/micmacIGN/micmac) | IGN/ENSG 开源摄影测量工具 | 备选研究后端，适合算法透明性要求 | 重写已有重建命令 |
| [OGC API Processes](https://ogcapi.ogc.org/processes/overview.html) | 过程描述、异步 job、状态、结果、异常 | 技能接口和任务状态语义参考 | 协议实现本身 |

### 3.7 低空 UAV 特有问题

相较卫星产品，低空原始数据给智能体增加了以下必须建模的状态：

1. 不是单张影像，而是具有重叠图、相机内外参和航带结构的照片集合；局部失败会传播到稀疏/稠密重建。
2. EXIF/POS、RTK 和 GCP 的坐标基准、垂直基准、时间同步、精度字段可能缺失或互相冲突。
3. GSD 不是上传照片的固定属性，而取决于焦距、像元、航高、地形和重建产品；同一项目可空间变化。
4. 森林冠层存在重复纹理、风致运动、阴影、遮挡和低可见地面，导致匹配、DSM 和正射接缝异常。
5. 超高分辨率会造成巨幅栅格、切片边界重复检测、GPU/内存/磁盘瓶颈，必须有资源感知规划。
6. 绝对精度、相对精度和视觉美观不同；没有检查点时，漂亮正射图不能证明地图量测可靠。
7. DSM 是表面模型，不等于冠高；冠高需要 DTM。密闭林冠由 RGB SfM 得到的 DTM 可能不可用。
8. 多架次/多季节变化要求辐射、几何、物候和观测角度可比；不能把 RGB 值差直接解释为生态变化。

### 3.8 空间状态与数据血缘

- [STAC](https://www.ogc.org/standards/stac/) 适合描述照片集、正射、DSM、mask、矢量和报告等资产；[Projection Extension](https://github.com/stac-extensions/projection) 可保存 CRS、shape、transform。
- [W3C PROV](https://www.w3.org/TR/prov-overview/) 提供 Entity–Activity–Agent 的通用来源模型，适合记录“哪个输入、工具版本、参数和环境产生哪个产品”。
- [OGC API Processes](https://docs.ogc.org/is/18-062r2/18-062r2.html) 提供过程、job、状态、结果和异常的接口语义。
- 本研究应采用轻量混合：STAC 管空间资产，PROV 风格图管派生关系，工作流 DAG 管计划/执行；不要建立与实验无关的庞大本体。

## 阶段3：约束感知、验证与失败恢复

### 3.9 三层验证

1. **程序正确**：调用成功、schema 合法、文件存在、格式可读、无 NaN/空结果、日志无致命错误。
2. **空间产品正确**：CRS/垂直基准/单位/GSD/extent/transform/拓扑一致；GCP/检查点 RMSE 合格；重叠范围、像元对齐和几何有效。
3. **林草结论正确**：单木实例与人工标签一致；数量、冠幅、覆盖度和空间格局在独立样地上无系统偏差；结论受数据能力边界约束。

只有第 1 层通过不能发布地图；第 2 层通过也不保证树冠分割与生态统计正确。

### 3.10 动态修复与反馈

- [CangLing-KnowFlow](https://arxiv.org/html/2512.15231) 将失败定义为显式工具错误或未达到预设质量，支持参数修改、节点替换和节点插入，并把验证过的修复固化为 Pattern-Action。它是最直接基线。
- [GeoAgent](https://arxiv.org/abs/2410.18792) 的代码解释器、静态分析、RAG+MCTS 表明可以用执行反馈搜索候选 GIS 程序，但执行成功仍需空间/领域 oracle。
- [GeoBenchX](https://github.com/Solirinai/geobenchx) 的不可解任务说明“拒绝/请求补数据”本身应计为正确行为；本项目背景要求智能体尽量继续，因此需再区分：可自动降级、需澄清、不可完成。
- [Structured Uncertainty guided Clarification](https://aclanthology.org/2026.findings-acl.2028/) 用工具参数域上的结构化不确定性和信息价值决定问什么、何时停止；可转化为 GSD、目标类别、AOI、统计单元、精度要求等槽位。

### 3.11 如何避免“规则堆叠”

方法创新不应宣称“规则越多越智能”，而应提出可证伪的**约束—诊断—修复模型**：

- 把每个工具形式化为数据类型、空间状态、前置条件、后置条件和质量观测；
- 将故障归因为数据不足、参数不当、工具不适配、资源不足、上游污染或用户目标不可满足；
- 依据后验故障概率和预期效用选择修复算子，而非固定 if/else；
- 在未见过的故障组合和跨区域数据上测试；
- 与固定规则、ReAct、Reflexion、仅重试、仅换参、KnowFlow 风格恢复对比；
- 报告恢复成功、误修复、额外成本、最终空间/生态质量，而不只报告任务没报错。

规则仍然必要，因为 CRS 和单位有确定语义；创新点在于规则被形式化为可组合约束，并由证据驱动诊断与计划搜索，而不是把所有专业知识藏在提示词里。

## 阶段4：跨区域、跨场景和经验复用

### 3.12 记忆与迁移

- [CangLing-KnowFlow](https://arxiv.org/abs/2512.15231) 以成功工作流和失败修复更新程序知识。
- [GeoForge](https://arxiv.org/abs/2608.10494)（2026-08，预印本）把经验分为 Workflow Graph Memory、Action-Level Experiences 和适配 SOP，并在不更新 LLM 参数的条件下复用。
- [RemoteAgent](https://arxiv.org/abs/2604.07765) 用 RL 对齐模糊意图和能力边界，但其必要性不能自动推广到本研究。

本研究的经验只应在三层验证通过后入库，并带适用上下文：林型、季节、GSD、相机、航高、重叠、风/光照、工具版本、模型权重、标注体系和质量结果。跨区留一验证比随机切分更能证明迁移。

### 3.13 从森林到草地的迁移

保持不变的部分：意图契约、工具/技能 schema、照片质检、摄影测量、STAC/PROV 状态、DAG 执行、三层验证、报告和恢复机制。

替换的领域插件：

- 任务本体：`tree crown instance` 替换为 `vegetation/bare soil/litter/shrub patch`；
- 视觉技能：实例分割切换为语义分割/斑块分割；
- 指标计算：数量/冠幅/郁闭度切换为覆盖度、裸地比例、斑块度和退化等级；
- 领域 oracle：树冠对象级 AP 切换为类别 IoU、覆盖度误差、斑块格局与样方一致性；
- 变化阶段增加时相配准、物候/光照一致性和最小制图单元。

草地 RGB 的谱辨识能力有限。2024 年[荒漠草地退化指数研究](https://doi.org/10.1016/j.ecolind.2024.112194)使用多光谱且数据不开放，说明“退化等级”常依赖超出 RGB 的证据。首个草地扩展应先做植被/裸地/枯落物覆盖和斑块，不直接承诺可靠退化机理诊断。

## 阶段5：系统、基准与论文整合

### 3.14 应形成的基准维度

| 维度 | 样例 | 真值/验证器 |
|---|---|---|
| 需求 | “算一下这片林子的郁闭度”但未给 AOI/定义 | 专家标注任务契约；澄清/默认/降级的可接受集合 |
| 可解性 | 缺少空间参考、无足够重叠、目标超出 RGB 能力 | 数据能力标签与允许动作 |
| 计划 | raw→QC→reconstruction→ortho/DSM→crown→GIS→report | 专家 DAG；允许等价路径而非唯一字符串 |
| 工具/参数 | RTK 与 GCP 冲突、错误 CRS、单位错、GSD 超出模型范围 | schema+空间类型检查器+参数区间 |
| 摄影测量 | 低重叠、模糊、重复纹理、错 POS、资源不足 | 合成/真实故障注入；相机/GCP/检查点和产品质量 |
| 视觉 | 冠重叠、阴影、跨季节、跨 GSD、边缘重复 | 独立实例标注；对象/像素/计数指标 |
| GIS | 重投影、AOI 裁剪、面积、邻近与格局 | 可重算几何/统计 oracle |
| 恢复 | 换参、换工具、插入预处理、回退、请求数据 | 最终质量+恢复成本+是否误修复 |
| 报告 | 地图、指标、限制和血缘 | 结构化字段、事实一致性和引用的产品 ID |
| 迁移 | 新区域/季节/GSD/工具/草地 | 留区、留季节、留工具测试和接入工时/改动量 |

### 3.15 论文组合建议

1. **方法论文 A：空间与领域约束的任务图生成**。贡献是 typed geospatial workflow、可解性判断和主动澄清；阶段1-2 数据。
2. **方法论文 B：验证驱动故障诊断与动态图修复**。贡献是三层 oracle、故障归因和修复策略；阶段3 故障注入与跨区测试。
3. **数据/基准论文：Low-altitude Forest-Agent Bench**。贡献是原始照片—产品—实例—统计—报告的多层真值和不可完成/故障案例。
4. **应用/系统论文：森林闭环与草地迁移**。只有当跨区域和草地迁移有充分实证时再形成；否则合并为系统论文/软件说明。

## 4. 重点系统研究差距

详细字段见 `system_comparison_matrix.md`。归纳如下：

- Tree-GPT 已占据“林业知识+SAM+代码分析”先发位置，本研究不能把同类原型包装为新方法。
- RS-Agent 已证明任务类型+Solution Space 比直接 ReAct 更稳；本研究应把 solution 提升为带空间类型和质量契约的工作流。
- Earth-Agent 已覆盖大型 MCP 工具集和轨迹/终值评价；本研究必须用 UAV 原始观测链和确定性空间/生态 oracle 区分。
- CangLing-KnowFlow 已覆盖 PKB、修复、记忆；本研究须证明低空林草约束带来新的故障模型和可泛化修复机制。
- RemoteAgent 已研究模糊意图和工具边界；本研究应聚焦任务参数/空间精度的不确定性，先做推理时主动澄清，不默认训练 MLLM。
- UAV-MAS 已研究 UAV 图像理解的尺度、旋转和密度；本研究应明确它不处理多照片几何和地理产品链。

## 5. 十二个重点问题的独立回答

### 5.1 与 Tree-GPT、RS-Agent、CangLing-KnowFlow、Earth-Agent、RemoteAgent、UAV-MAS 的实质差异

差异不是应用名，而是问题定义：**以原始低空观测为状态、以空间与林草约束为类型、以三层可重算验证为反馈、以最终地图/指标可审计为成功条件的长链智能体**。如果没有这四点，就容易退化为已有系统的林业工具换皮。

### 5.2 低空 UAV 的特殊问题

相机网络与重叠、RTK/GCP/POS 冲突、垂直基准、空间变化 GSD、植被风动和重复纹理、超大栅格、正射接缝、DSM/DTM 混淆、绝对/相对精度分离、跨航次辐射/几何不可比、资源成本和长任务失败传播。

### 5.3 林草领域约束

- “树”是生态对象，不是任意视觉 mask；冠重叠、林层和林缘影响实例定义。
- 冠幅、覆盖度、郁闭度定义和统计单元必须显式；投影面积与地表面积不能混用。
- 树种 RGB 可辨识性依赖物候、冠层视角和类别体系；开放集/未知类必须存在。
- 树高需要 DSM–DTM 及垂直基准，胸径/蓄积量需要地区异速模型和实测，不得从 RGB 直接“推断”。
- 草地退化是生态综合概念，RGB 覆盖并不自动等价于退化等级。

### 5.4 摄影测量是否掩盖方法创新

会，如果目标写成“自动调用 ODM 生成正射”。不会，如果摄影测量仅是可控后端，而研究对象是数据可解性、参数/模型选择、跨阶段误差传播、质量诊断和验证驱动修复。建议论文实验中固定 1 个主后端（ODM），1 个替代后端（Metashape 或 MicMac），不自行研究 SfM 核心算法。

### 5.5 最小可发表完整闭环

推荐：用户自然语言 + 已有正射/可选 DSM → 解析 AOI/指标/精度 → 树冠实例分割（预训练与轻量微调工具路由）→ 计数、冠幅/面积、覆盖度、最近邻/聚集度 → 空间/生态验证 → 地图与报告；加入数据不足和 4–6 类工具/参数故障。若必须体现 raw photos，可加入一个受控 raw→ODM→ortho 的前置链，但不把新摄影测量算法作为贡献。

### 5.6 哪些视觉模型需训练

- 大概率需微调：本地树冠实例分割；本地树种识别；草地细分类/退化等级；跨季节变化模型。
- 可先直接作为工具：影像模糊/质量检测、SAM/SAM2 提示分割、DeepForest 检测、detectree2 预训练、通用 caption/MLLM 辅助检查、传统 GIS 和几何计算。
- 不宜黑箱训练：树木数量、冠幅、覆盖度、空间格局，优先从可验证几何派生。

### 5.7 是否微调语言或多模态模型

阶段0-3不默认需要。用强通用 LLM+结构化 schema+程序知识检索+验证反馈建立基线。只有在固定模型、固定工具、充分提示与 RAG 后，仍在跨表述意图映射/澄清/路由上出现稳定、可标注错误，且数据量足够时，才比较 SFT/偏好优化/RL。视觉 MLLM 不承担像素级最终产品。

### 5.8 单智能体、分层智能体和多智能体

推荐**分层单控制器架构**：高层任务契约与 DAG 规划器；确定性执行器；独立验证器/策略模块。它可以实现职责分离而不必让每个模块都由 LLM 扮演。多智能体只在工具空间过大、并行专家意见或独立验证确有收益时作为实验变体。比较同一 LLM 调用预算下的成功率、成本和错误传播。

### 5.9 证明恢复不是规则堆叠

用形式化状态与可学习/检索的诊断策略，在未见故障组合上比较 fixed rules、retry-only、ReAct、Reflexion、KnowFlow-like、提出方法；对每类约束和记忆做消融；报告误恢复、最终产品质量、成本和校准，而不是仅报“恢复成功”。

### 5.10 低空林草 benchmark

必须同时含 raw photo project、空间元数据、标准产品、视觉标签、样地指标、专家 DAG、等价路径、故障版本和不可解标签；按区域/季节/航高/GSD/相机留出；提供确定性 GIS/统计 oracle 和允许误差；隐藏测试集避免模板污染。

### 5.11 科学创新与平台工程

科学创新：可解性与主动澄清模型；空间/领域 typed task graph；三层验证模型；故障归因与验证驱动修复；经验迁移与可靠性 benchmark。工程支撑：聊天 UI、账户、数据库 CRUD、文件上传、ODM/QGIS 容器、日志面板、地图前端、API 网关。视觉新模型只有在有明确算法和跨区实验证据时才是科学创新。

### 5.12 森林到草地迁移

保持任务契约、摄影测量、状态/血缘、执行器、验证框架和报告；替换领域本体、视觉技能、指标算子和领域 oracle。以“接入新增技能需要多少 schema/知识/代码改动、是否无需改 planner、零/少样本成功率”为迁移指标，而不是重新做一个 Grass-Agent。

## 6. 研究范围收缩建议

当前完整设想适合一个研究方向或多篇论文，不适合作为单篇论文的一次性交付。建议强制收缩：

1. 第一闭环只做树冠实例、数量、冠幅/面积、覆盖度和 1–2 个空间格局指标。
2. 树种识别作为可选技能，在有足够本地标签时加入；否则只设“已知类/未知类”实验。
3. 原始照片链只支持 RGB+EXIF/POS+可选 RTK/GCP，主后端 ODM；不做自主飞行、多光谱标定、LiDAR 主流程。
4. 长期自演化和自由工具创建推迟到阶段4；阶段3只做受控修复和验证过的经验复用。
5. 草地阶段先做植被/裸地/枯落物覆盖与斑块，不直接做广义退化等级。

## 7. 核心参考入口

- 遥感智能体综述：[Agentic AI in Remote Sensing（WACV 2026 Workshop）](https://openaccess.thecvf.com/content/WACV2026W/GeoCV/html/Talemi_Agentic_AI_in_Remote_Sensing_Foundations_Taxonomy_and_Emerging_Systems_WACVW_2026_paper.html)
- 工具协议：[MCP specification](https://modelcontextprotocol.io/specification/2025-03-26/index)
- 空间过程：[OGC API Processes](https://ogcapi.ogc.org/processes/overview.html)
- 空间资产：[STAC specification](https://github.com/radiantearth/stac-spec)
- 数据血缘：[W3C PROV Overview](https://www.w3.org/TR/prov-overview/)
- 摄影测量：[OpenDroneMap documentation](https://docs.opendronemap.org/)、[WebODM API](https://docs.webodm.org/api/task/)
- 视觉工具：[DeepForest](https://github.com/weecology/DeepForest)、[detectree2](https://github.com/PatBall1/detectree2)
