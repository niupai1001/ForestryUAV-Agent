# 代表性系统结构化对比矩阵

> 检索截止：2026-08-31。证据优先级为论文/会议原文与官方仓库（一级），官方项目页或数据页（二级），检索摘要仅作发现线索。`预印本`不等于同行评审。未找到公开证据时明确写“未核验”，不按项目名称推断。

## 1. 身份、状态与开放性

| 系统 | 基本信息与状态 | 机构（论文署名） | 论文 | 代码/模型/数据 |
|---|---|---|---|---|
| Tree-GPT | Du 等，2023；ISPRS Geospatial Week 会议论文，同行评审会议档案 | 深圳大学智慧城市研究院等 | [ISPRS 论文与 DOI](https://doi.org/10.5194/isprs-archives-XLVIII-1-W2-2023-1729-2023)，[arXiv](https://arxiv.org/abs/2310.04698) | 截止日未检索到作者官方代码仓库；论文原型所用 SAM 可公开获得，但不等于 Tree-GPT 开源 |
| Remote Sensing ChatGPT（RS-ChatGPT） | Guo 等，2024；IGARSS 2024，DOI 10.1109/IGARSS53475.2024.10640736 | 武汉大学 | [arXiv](https://arxiv.org/abs/2401.09083)，[IGARSS 页面](https://www.2024.ieeeigarss.org/view_paper.php?PaperNum=2390) | [官方 GitHub](https://github.com/HaonanGuo/Remote-Sensing-ChatGPT)；代码及若干模型权重链接公开，无专门 agent 基准数据集 |
| Change-Agent | Liu 等，2024；IEEE TGRS 62，同行评审，DOI 10.1109/TGRS.2024.3425815 | 北京航空航天大学等 | [arXiv](https://arxiv.org/abs/2403.19646) | [官方 GitHub](https://github.com/Chen-Yang-Liu/Change-Agent)；LEVIR-MCI、MCI 权重与代码公开 |
| RS-Agent | Xu 等，2024 首发、2026 v4；截至截止日仍以 arXiv/官方仓库为主要可核验证据，未核验到正式同行评审卷期 | 北京邮电大学、香港城市大学、湖南师范大学等 | [arXiv](https://arxiv.org/abs/2406.07089) | [官方 GitHub](https://github.com/IntelliSensing/RS-Agent)；代码开放程度以仓库当前内容为准 |
| GeoAgent（自动地理空间分析版本） | Chen 等，2024 预印本；后以“Automating Geospatial Vision Tasks with a Large Language Model Agent”发表于 ECML-PKDD 2025 | 法国高校/研究机构团队；机构细节见原文 | [arXiv](https://arxiv.org/abs/2410.18792)，[出版页与 DOI](https://doi.org/10.1007/978-3-662-72243-5_13) | 论文报告 benchmark；本矩阵未把同名地址标准化、图像地理定位或 QGIS 插件误并入该系统 |
| GIS Copilot | Li 等，2024 预印本，后续正式出版状态需以作者页更新为准 | 美国高校 GIScience 团队（详见原文） | [arXiv](https://arxiv.org/abs/2411.03205) | 公开情况以论文项目页为准；本研究仅把它作为自然语言到 GIS 工具链基线 |
| GeoBenchX | Krechetova、Kochedykov，2025；ACM SIGSPATIAL GeoGenAgent'25 workshop，同行评审 workshop | Solirin AI | [arXiv](https://arxiv.org/abs/2503.18129)，[ACM DOI](https://doi.org/10.1145/3764915.3770721) | [官方 GitHub](https://github.com/Solirinai/geobenchx)；代码公开，数据包与许可完整性需逐项检查 |
| ThinkGeo | Shabbir 等，2025；截至截止日以技术报告为主（预印本） | MBZUAI、IBM Research 等 | [arXiv](https://arxiv.org/abs/2505.23752)，[项目页](https://mbzuai-oryx.github.io/ThinkGeo/) | [官方数据](https://huggingface.co/datasets/MBZUAI/ThinkGeo)；项目页提供代码入口 |
| Earth-Agent / Earth-Bench | Feng 等，2025 预印本；已作为 ICLR 2026 conference paper 发表 | 上海人工智能实验室、中山大学、清华大学深圳国际研究生院 | [ICLR/OpenReview](https://openreview.net/forum?id=dkIXAbWuxO)，[arXiv](https://arxiv.org/abs/2509.23141) | [官方 GitHub](https://github.com/opendatalab/Earth-Agent)，[Earth-Bench](https://huggingface.co/datasets/Sssunset/Earth-Bench)，[项目页](https://opendatalab.github.io/Earth-Agent/) |
| CangLing-KnowFlow / KnowFlow-Bench | Chen 等，2025；arXiv:2512.15231，预印本 | 中科院空天信息创新研究院、国科大、HZDR、Lancaster、Griffith 等 | [arXiv](https://arxiv.org/abs/2512.15231)，[项目页](https://cangling-agent.github.io/KnowFlow/) | 项目页宣称提供 benchmark/assets；截止日需逐项检查实际可下载性，不能把网页说明等同完整开源 |
| OpenEarth-Agent / OpenEarth-Bench | Zhao 等，2026；预印本 | 多机构团队，详见原文 | [arXiv](https://arxiv.org/abs/2603.22148) | [官方 GitHub](https://github.com/walking-shadow/OpenEarth-Agent)；仓库 TODO 明示模型代码和 benchmark 尚待开放，因此当前属于“仓库公开、核心资产未完整公开” |
| RemoteAgent / VagueEO | Yao 等，2026；预印本 | 河海大学、东南大学、中山大学等 | [arXiv](https://arxiv.org/abs/2604.07765) | [官方 GitHub](https://github.com/1e12Leon/RemoteAgent)；代码/数据的实际完整性应按复现实验再审计 |
| UAV-MAS / UAVQA-Bench | Zhang 等，2026-08；预印本，尚未经同行评审 | 复旦大学、上海人工智能实验室等（论文署名） | [arXiv](https://arxiv.org/abs/2608.11738) | 截止日未检索到可确认的官方代码/数据仓库；论文称 1,500 人工 QA，不能据此推断已经开放 |
| GeoForge | Xiao 等，2026-08；预印本，尚未经同行评审 | 机构见原文 | [arXiv](https://arxiv.org/abs/2608.10494) | 截止日未检索到可确认官方仓库；开放性记为未核验 |
| `smartforest.bjfu.edu.cn` | 2026-08-31 DNS 解析失败；网页搜索未返回项目文档 | 域名指向北京林业大学，但这不能证明具体项目归属或能力 | 无可核验论文与页面 | 无可核验代码/模型/数据；相关公开教学文章仅能旁证该校开展林草视觉与大模型实践，不能旁证该站功能 |

## 2. 数据、任务和智能体机制

| 系统 | 数据类型 | 任务范围 | 智能体结构与规划 | 真实执行 | 可靠性机制 | 评价方式 |
|---|---|---|---|---|---|---|
| Tree-GPT | 航空/无人机森林 RGB、文中还涉及 LiDAR/树高等结构信息；主要是已形成的影像/产品而非原始相片 | SAM 引导树冠分割、树高/冠幅等参数查询、统计、可视化、简单机器学习 | 单控制器；图像理解模块+林业知识库+本地代码执行；链式思考生成代码 | 是，执行本地代码与视觉模块 | 依赖交互提示和人工多轮修正；未见系统性的空间约束验证、故障注入或自动重规划实验 | 少量示例/3 个影像瓦片，搜索、可视化和分析演示；缺少标准化 agent 指标 |
| RS-ChatGPT | 遥感 RGB 成品影像 | caption、场景分类、检测、实例/语义分割、计数、边缘；可串联 | 单智能体；Prompt 模板→任务规划→逐步执行→回复；视觉 caption 作为视觉提示 | 是，调用 7 类预训练模型/算法 | 主要依赖工具返回和对话；仓库 TODO 仍包括同任务模型选择，未见强约束/故障恢复基准 | 任务规划正确性与案例；不覆盖摄影测量、空间产品质量或长期运行可靠性 |
| Change-Agent | 双时相卫星遥感影像 | 像素变化检测、变化描述、对象计数、原因分析 | LLM“脑”+ MCI“眼”；交互式单智能体 | 是，调用 MCI 并组合分析 | MCI 多任务输出提供相互支撑，但不是通用工作流验证/恢复 | LEVIR-MCI；检测与 caption 指标，重点是感知模型而非全流程 agent |
| RS-Agent | 光学与 SAR 成品影像 | 18 类任务/9 数据集；低层增强、分类、检测、计数、VQA、SAR 处理等 | 单控制器+27 工具+Solution Space+Knowledge Space；Task-Aware Retrieval 先定任务类型，再检索 SOP；DualRAG | 是；模板经过代表数据执行和 I/O 一致性检查 | 有执行后的上下文反馈，但论文主线不是显式故障注入和动态图修复；长期记忆较弱 | 规划准确率（>95% 为论文报告）、终端任务指标、检索消融、7 个多工具模板 |
| GeoAgent（自动地理空间分析） | 矢量、栅格、在线数据与 Python GIS 库 | 数据获取、处理、分析、可视化；单/多轮 | 代码解释器+静态分析+RAG，嵌入 MCTS 搜索候选程序 | 是，执行代码并利用错误反馈 | 静态分析和执行反馈；MCTS 选择候选；空间语义/生态有效性仍不充分 | 自建单/多轮 benchmark，函数调用和任务完成率 |
| GIS Copilot | GIS 矢量/栅格数据 | 百余基础、中级、高级空间分析任务 | 单智能体自主规划和 GIS 工具调用 | 是 | 运行反馈；公开摘要不足以证明完整的数据血缘/领域结论校验 | 三档复杂度、任务成功/工具链完成情况 |
| GeoBenchX | GIS 表格、矢量、栅格与 API | 约 202 个多步骤 GIS 任务，含刻意不可解任务 | LangGraph ReAct，23 个函数，最多 25 轮 | 是 | `reject_task` 检验不可完成识别；但主要用 LLM-as-judge 与参考轨迹，不重算所有空间答案 | 可解/不可解、轨迹等价、token/步骤效率；非确定性 judge 是局限 |
| ThinkGeo | 中高分辨率卫星/航空光学影像 | 436 个任务，城市、灾害、环境、交通等；感知、计算、逻辑、标注 | ReAct 风格，14 个可执行工具 | 是 | 有逐步轨迹评估，但并非运行期自愈框架 | 指令遵循、工具/参数、步骤一致性、最终答案；未覆盖原始 UAV 数据链 |
| Earth-Agent | Spectrum、Products、RGB；主要卫星/产品数据 | 14 类科学任务，指数、反演、感知、时空分析、统计 | 单控制器 ReAct/POMDP 形式；MCP 接入 104 个预定义工具；短期交互记忆 | 是 | 通过工具输出更新状态；论文有错误分析和轨迹评价，但未以动态恢复和长期经验为核心 | Earth-Bench：248 问题、13,729 图像、平均 5.4 步；Tool-Any/In-Order/Exact、参数准确率、终值与效率 |
| CangLing-KnowFlow | 多类遥感产品/卫星数据；论文工具示例要求空间分辨率等参数 | 162 实际任务、324 ground-truth 工作流 | Orchestrator+PKB（1,008 高层模板）+动态执行引擎+演化记忆；DAG 实例化 | 是（论文框架） | 显式失败检测；参数修改、节点替换、节点插入；成功固化与失败 Pattern-Action 记忆 | KnowFlow-Bench，任务成功率、首轮准确、调用数、恢复；对 Reflexion/ThinkGeo 消融；仍以预印本证据看待 |
| OpenEarth-Agent | 多源开放环境 EO 数据 | 596 个全流程案例；未见工具时创建新工具 | 自适应规划+多阶段工具创建+跨域知识整合 | 论文声称真实创建/执行；核心代码与 benchmark 尚未开放，复现待定 | 数据/任务适配是主线；工具安全、科学验证与失败恢复细节需复现审计 | OpenEarth-Bench、Earth-Bench 迁移；工具创建等价性；当前开放不足 |
| RemoteAgent | EO RGB 图像，多粒度标注 | 10 类意图：分类、计数、定位、检测、分割、变化检测等 | 经 GRPO 强化微调的 MLLM 为认知核心；能力边界路由；密集任务经 MCP 调专用工具 | 是 | 重点是“是否调用工具”的能力边界；不是长链故障恢复系统 | VagueEO：训练 5 类各 1,000；测试 10 类各 100；意图识别、原生能力、外部执行 |
| UAV-MAS | 13 个公开 UAV 航拍数据集，图像级问答/grounding；不是原始摄影测量照片集 | 6 能力维度、16 任务、1,500 人工 QA | 多智能体/模块：DSPE 工具路由、CAIR 迭代验证、DAAS 难度自适应搜索 | 调用视觉工具；训练自由 | 中间答案可信度与一致性检查、难度自适应搜索；不验证 CRS/GSD/产品血缘 | 客观选择题与视觉定位；论文报告 32B 版 77.0% OA；预印本且资产未核验开放 |
| GeoForge | EO 工作流与多类 geospatial benchmark | 跨任务 EO 推理、工具规划、经验复用 | 不更新 LLM 参数；工作流图记忆+动作经验+适配 SOP；安全门控轨迹蒸馏 | 是（论文报告） | 从完成轨迹抽取可复用知识，约束感知检索；“安全门控”需代码/实验复核 | 多个地理空间 benchmark 的任务准确和轨迹质量；极新预印本 |

## 3. 主要贡献、局限与本研究关系

| 系统 | 真正解决的问题 | 主要局限 | 可借鉴 | 与本研究的潜在重复 | 本研究必须越过的边界 |
|---|---|---|---|---|---|
| Tree-GPT | 首次把林业知识、树冠分割和代码分析整合为可交互专家原型 | 小规模演示；无原始影像重建；无标准基准；人工指导较多；未见系统恢复 | 林业参数知识和视觉结果数据库；“视觉工具+知识+代码”模块化 | 单木分割、参数统计、自然语言分析 | 从 raw photos 到产品；显式空间状态；三层验证；故障恢复；跨区域基准 |
| RS-ChatGPT | 证明通用 LLM 可编排多种遥感视觉模型 | 工具少、模型路由较浅；空间元数据和不可完成识别不足 | 原子技能封装和视觉提示 | 图像解译工具调用 | 约束驱动规划、真实 GIS/摄影测量、血缘、可靠性实验 |
| Change-Agent | 把变化掩膜和变化描述统一在交互系统中 | 单一变化场景、依赖专用 MCI；非通用全流程 | 草地阶段的变化检测/解释技能 | 双时相变化解释 | 跨产品配准/时相一致性、失败诊断、生态指标验证 |
| RS-Agent | 任务类型先验、专家 SOP 和知识 RAG 显著提升工具规划 | 专家模板规模有限；主要成品影像；动态修复、长期经验和空间血缘不是核心 | Solution Space/Knowledge Space 分离、Task-Aware Retrieval、工具层级 | 程序知识检索、视觉工具路由 | UAV 原始数据与产品依赖、可解性判断、验证驱动重规划、林草方法学基准 |
| GeoAgent | 以执行、静态分析和搜索提升 GIS 代码正确性 | 代码能运行不等于空间/生态正确；MCTS 成本高 | 静态分析+执行反馈；候选计划搜索 | GIS 自动分析 | CRS/GSD/单位/语义类型系统，产品/领域验证与数据血缘 |
| GeoBenchX | 把不可解任务和多步 GIS 工具轨迹纳入评测 | LLM-as-judge 非确定；缺少重算式空间真值；无 UAV/林草 | 不可完成任务、轨迹效率、分层难度 | agent benchmark 的通用部分 | 建立可重算终值、空间几何和生态结论三层 oracle |
| ThinkGeo | 系统评估遥感图像上的多步工具使用 | 主要图像 QA/计算；元数据和原始数据链弱 | 步骤级指标、工具错误分类 | 视觉+工具 agent 评价 | UAV 采集/重建/血缘/质量、林草指标和故障注入 |
| Earth-Agent | 跨 RGB/光谱/产品的 104 工具体系和双层轨迹/终值评价 | 预定义工具闭集；主要卫星 EO；恢复、领域长期经验不是核心 | MCP、工具分类、双层评价、Auto-Planning vs Instruction-Following | 多工具 EO 工作流和基准 | 低空 UAV 几何链、空间状态类型、可验证失败恢复、森林闭环 |
| CangLing-KnowFlow | 将专家程序知识、动态图修复和演化记忆统一 | 预印本；高层模板可能掩盖参数级空间正确性；跨任务广而林草/UAV 专深不足 | DAG 修复算子、Pattern-Action、经验门控 | 程序知识、恢复、记忆最接近本研究 | 以 UAV/林草物理与生态约束把“通用修复”做成可证伪方法；独立数据和重算 oracle |
| OpenEarth-Agent | 从闭集工具调用扩展到开放环境工具创建 | 核心资产尚未开放；动态代码安全、可验证性风险高 | 后期新工具接入和工具创建评测 | 阶段4工具迁移 | 第一阶段不做自由工具创建；先做受约束技能组合与验证，再有限开放 |
| RemoteAgent | 模糊意图识别和“MLLM 内生能力/外部工具”边界路由 | 模糊查询为模拟生成；重点是视觉任务粒度，不是长链地理处理 | 能力边界路由、VagueEO 构造方法、RL 仅在有必要时使用 | 意图解析/模型路由 | 把歧义落到 CRS、GSD、精度、统计单元和可解性；优先推理时澄清而非直接微调 |
| UAV-MAS | 针对 UAV 图像尺度、朝向、密度难题做工具路由和迭代验证 | 仍是图像 QA/grounding；不含 SfM/MVS、RTK/GCP、地图产品；最新预印本 | UAV 特有错误分类、难度自适应计算、多工具交叉检查 | UAV 视觉理解和多智能体验证 | 从照片集到地理产品与林草结论；单智能体/分层架构需经消融决定 |
| GeoForge | 非参数自演化地复用工作流图、局部经验和 SOP | 极新预印本、无已核验官方代码；经验污染和错误固化仍是风险 | 三粒度记忆与安全门控 | 阶段4经验复用/技能迁移 | 只从通过程序、空间、生态三层验证的轨迹学习，做跨区留出实验 |

## 4. 与六个重点系统的实质差异（结论版）

| 对象 | 它的中心问题 | 本研究的中心问题 | 实质差异 |
|---|---|---|---|
| Tree-GPT | 林业成品影像的分割、参数数据库和交互分析 | 原始 UAV 照片到可审计林草地图/指标/报告 | 不是增加几个工具，而是引入摄影测量产品依赖、空间类型状态、可解性判断与系统可靠性实验 |
| RS-Agent | 以任务类型和专家 solution 提高多视觉/SAR工具规划 | 以数据/空间/生态约束生成并验证完整低空流程 | 规划先验从“任务标签/模板”推进为“带前后置条件与质量契约的可执行任务图” |
| CangLing-KnowFlow | 通用遥感工作流模板、动态图修复和经验演化 | UAV 林草强约束下的验证驱动修复 | 方法上最接近；创新必须落在低空特有观测链、三层 oracle、可解释故障归因和跨区反事实实验，而非再做一个 PKB |
| Earth-Agent | 多 EO 模态、104 个预定义工具、轨迹与终值评价 | 原始照片/SfM/GIS/林草语义的产品链和可靠性 | Earth-Bench 不评相机网络、GCP/RTK、重投影、冠幅/覆盖度生态合理性；本研究强调这些可重算状态和失败注入 |
| RemoteAgent | 通过 RL 学习模糊意图与视觉任务粒度，决定内生回答或调用密集工具 | 把自然语言转为带空间、精度和数据适用性约束的任务契约 | 本研究先采用结构化不确定性与必要澄清；除非基线证明推理时方法不足，不默认微调 MLLM |
| UAV-MAS | UAV 单图理解/推理，多智能体视觉工具路由 | UAV 照片集全流程地理产品与林草分析 | UAV-MAS 不处理相机/航带/重建/CRS/血缘，也不证明地图和生态参数正确；多智能体仅作对比基线，不作预设答案 |

## 5. 独立判断

1. 最强的新颖性窗口不是“林业版 Earth-Agent”，而是**约束与证据驱动的低空观测工作流智能体**：每个动作带数据契约、空间状态和可验证后置条件，失败后基于诊断证据修复任务图。
2. CangLing-KnowFlow 已经覆盖“程序知识+动态图修复+演化记忆”的宽泛叙事；若本研究只复现这三件事并换成林业工具，重复风险很高。
3. 从原始照片开始有科学价值，但必须把摄影测量当作**受控实验环境和约束来源**，而不是把主要篇幅用于重写 SfM/MVS。重建工具本身属于工程支撑，智能参数选择、可解性判定、质量诊断与跨阶段误差传播才可能形成方法贡献。
4. 第一篇论文不应同时包含森林、草地、自由工具创建、长期自演化和复杂多智能体。最小可发表闭环应收缩为：**已有正射/DSM 或受控原始照片 → 单木冠实例 → 数量/冠幅/覆盖度 → 空间统计 → 地图/报告，并在故障注入下验证约束规划与恢复**。
