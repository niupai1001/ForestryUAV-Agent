# Implementation / 实施状态

本文件区分实现与证据。完整目标未缩减：R01–R16、U01–U08、AC-01–AC-13 与三个阶段均保留。没有源码或通过证据的功能不标记完成。

最新增量 1.33（2026-10-10）：补齐长期记忆的用户更正、版本校验、只读历史、停用留档、任务范围及 UTC 有效期。旧笔记迁移保留原字节/保存事件/请求收据；模型仍只能读取当前有效且匹配任务范围的私密记录，无自动经验提升。设置页新增预览、来源/条件/局限、范围/有效期、更正及历史查看；6 项最终定向后端验证、1 项真实浏览器流程、构建和静态检查通过，使用独立数据库关闭 Agent，不写生产经验。设置诊断不再探测暂缓的搜索。v5 已有 large-H0 首项真实观察终结于 NO_PROGRESS：正确计算和读回存在，报告缺失，原诊断代理尚未独立复核，完整不通过；汇总保留 1 观察/17 未运行/0 完整通过，不恢复或重置该任务。阶段 1 与全部后续原范围仍未完成。见 [记忆契约](MEMORY.md) 和 [当日进度](PROGRESS-20261010.md)。

最新增量1.32（2026-10-10）：v4两项H1真实观察已保存。small读取完整源后两次向/run、/inputs写文件被拒并暂停，无作业/审批；large在6个真实隔离Jobs、2次精确审批后完成缺six诊断、均值37.0→37.5修复、真实JSON与中文报告及读回，29份输入均已核验。原自动评分仍保留两项代理失败；独立诊断/报告审查与只读重新评分证明该large单项完整通过，未接受应用任务、重跑模型或改旧清单。v4原汇总2观察/16未运行/原自动完整0，单独重评分版本有1项通过，不混统计版本。另修复已安装环境只作用于主Python、子Python仍缺six的实际缺陷：仅在隔离Job中传递已核验site的PYTHONPATH，保留调用方显式env覆盖。既有依赖验证脚本增加subprocess-only范围，两个真实作业14项边界/复用检查通过，8项评分与3项环境拒绝/复用检查通过；无全量/宿主安装。本次修复95d8a5b已加载并独立冻结v5，18项均未运行；旧v4评分/记录保持。阶段1角色、恢复/控制、知识记忆、资源准入、UI及其余通用试次与全部后续范围仍未完成。详见 [当日进度](PROGRESS-20261010.md)。

最新增量 1.31（2026-10-10）：确认 2522c0f 专用服务在线并完成 v3 首项真实 small-H1 观察；固定镜像可用、12 个旧作业保持暂停。该任务已取得/安装 six，15 次有来源绑定的正文恢复未清零计数；后续计算没有引用已准备环境而再次缺 six，最终三次计划更新无新增证据触发 NO_PROGRESS。v3 1 项观察、17 项未运行、完整通过 0，不重置该任务。实际快照另证实固定指令约12.4K字节高于原9.5K保留窗口，导致反复移出正文；现仅调整原生滑窗保留预算：有实测用量时使用原可用预算的2/3，未计量/导入历史仍保留原1/3；跳过不足1024字节收益的工具正文清除。原32K/4K、压缩目标、完整输入校验、权限和停滞阈值不变。最终4项压缩/正文边界与11项预算/收据检查通过，未重跑依赖/领域全量；新窗口修复516c16b已加载并冻结v4；真实模型效果尚待观察。详见 [当日进度](PROGRESS-20261010.md)；阶段1及完整计划仍未通过。

最新增量 1.30（2026-10-10）：修正已证实的压缩恢复/计数交互。对同范围准备输入的真实丢失正文，只允许一次有来源绑定的只读恢复；保持三次停滞阈值和当前计数，不能恢复旧暂停回合或以计划/副作用获得豁免。Windows 长路径比较已修正。减少 H1/H2 作业摘要重复产物 ID，并保留完整记录/总数，修复 H2 首请求预算超限；原32K/4K保持。相关进度17项、更新提示后的原生输入/上下文/角色50项及不变控制3项通过，未重跑依赖/领域全量。机制和失败记录见 [CONTEXT-RECOVERY](CONTEXT-RECOVERY.md)。真实模型效果仍须新版本验证；阶段1与完整计划未通过，下文1.29及以前保持历史。

最新增量 1.29（2026-10-10）：执行连接已恢复；pilot v1 每组合首次共 6 项均未完整通过、12 项未运行，原分母/失败保留。修复依赖请求不存在文件名时的纠错提示，9 项相关检查通过。新增只读历史诊断重新评分，对两个大模型成功包装器捕获的原程序失败完成代码/挂载/日志绑定核验；原失败诊断由 false 改为 true，完整任务仍 false，没有新模型/作业运行。评估原有 13 项、新诊断 9 项、补充报告复用 2 项检查通过；不重复全量回归。详见 [当日进度与剩余工作](PROGRESS-20261010.md) 及 [重新评分规则](EVALUATION.md)。阶段 1 与完整计划仍未完成；以下旧基线记录为历史。

1.29 后续：提交 `0b98b72` 已加载到专用服务，原清单/任务和 12 个暂停排队作业保持；新版本独立冻结 v2 清单 18 项。仅新 small-H1-r1 已运行并失败，0 审批、2 个真实终态作业、11 份输入；v2 17 项未运行。该任务未触及缺文件名反馈分支；实际输入证明压缩后源正文缺失、Harness 提示核验关键事实，随后三次源重读被无进展保护暂停。先审查上下文恢复与计数交互，不原样重跑或全局放宽保护。逐输入来源、评分版本和服务核验见上述当日进度；没有全量测试。

阶段1出口按原需求收敛，详见 [STAGE-1-GATES](STAGE-1-GATES.md)。选定Python运行时的真实代码/依赖修复、角色/恢复/知识/隐私/UI及六组合通用试运行仍必需；其他运行时、所有源码后端/版本和系统编译器支持不能由历史缺口列表自动升级为该阶段硬条件。具体任务明确要求的路径仍须实际通过；两条领域、正式评估、低资源/真实≥100 GB与发布原范围保持。

2026-10-06 用户明确暂缓 Agent 联网搜索开发。新增连接器、搜索稳定化与联网搜索验收停止推进，已有实现和安全回归保留；相关项记为用户暂缓，不计作通过，不阻塞本轮本地通用能力交付。其余本地/领域/评估与规模目标继续推进。

| Batch / 批次 | State / 状态 | Delivered / 已有行为 | Evidence / 证据 | Remaining / 未完成 |
|---|---|---|---|---|
| P0 | in_progress | 独立应用、修订双语基线、uv/package 锁、实测 doctor | workspace/deployment.lock.json | 领域数据许可/格式、兼容地形、≥100 GB 数据、低资源选择 |
| P1 | in_progress | 真实模型与只读文件工具、持久任务、产物版本、本地 React 工作台 | 旧small/large烟测与浏览器证据保持；1.26 large实际两组argv结果及作业来源核对通过，完整任务语义失败 | 语义验收、完整 UI 交互 |
| P2 | in_progress | 原动作/Jobs/WSL/恢复与依赖链、原生审批续接；精确解析/下载读取复用；源码/wheel回执分别保持 | 1.26定向验证及真实历史下载副本核验通过；真实large完成一次成功构建/安装及两组正确调用，但遗漏动态检查，完整流程未通过 | 选定运行时依赖/植入脚本错误的完整复合行为、资源准入/恢复窗口及调试/报告质量；其他运行时/编译器/所有源码版本回退另列扩展，不作为未声明的阶段硬条件 |
| P3 | in_progress | 原验收/清单、文档/知识/记忆、原生上下文/角色及 H0/H1/H2、来源/计划/有界纠正；原生审批续执行；已计费组件及无响应历史用量基线 | 1.26副本预算31052→21438；22次真实响应均被估算覆盖；报告虚报动态检查并漏源码引用/抄错镜像摘要，语义未通过 | 压缩/预算质量、要求覆盖/报告事实/失败补全、运行隔离、角色异常/冲突交互、两模型本地通用/长任务质量、通用评估；搜索暂缓 |
| P4 | not_started | — | — | 真实 ODM 航片流程与冠层分支 |
| P5 | not_started | — | — | 真实 PDAL/lidR、边界实例、指标 |
| P6 | not_started | — | — | 六组合冻结实验、低资源/规模实测、发布门槛和证据包 |

## 1.28 当前增量：冻结通用pilot入口与运行版本证据

large-H0-r1后续实际终态：six1.17.0官方wheel取得并封存（11050字节，SHA4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274），但固定Python镜像本地缺失，12个作业均queued且started_at为空。模型重复提交环境准备，37份上下文后耗尽原回合30请求上限，paused/MODEL_PROTOCOL_ERROR；无计算结果/报告、未实际观察缺six运行失败，评分false。这是执行环境不就绪造成的受干扰试次，不能当作有效模型质量对照。仅核验了服务与模型而未及时复核镜像可用性是本次操作缺口，后续试次开始前必须核验实际固定镜像及Docker响应。

恢复镜像前，正式/tasks/.../pause将原任务pause_requested设为true，保留原失败及12个未启动作业，阻止自动执行排队副作用。只拉取原冻结摘要05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f，实际image ID df18428df8a0f9189c84e7424d214b4c50092af1f9dca5842a00c41c39bda316。doctor(probe_search=False)与单独image inspect证实Docker和镜像可用；运行清单/模型摘要保持，原任务仍paused且全部作业未启动，不恢复或重建受干扰试次。证据pilot-large-H0-r1-executor-image-interruption-20261010-v1.json、pilot-executor-image-restored-20261010-v1.json及原trial记录。汇总现为两项已观察未通过、16项未运行；阶段1仍未通过，产品代码/冻结输入/预算未改，无pytest或完整UI重跑。

2026-10-10 执行恢复：沙箱日志定位到内置node_repl.exe的运行权限校验因文件被占用而返回Windows错误32；诊断保存在workspace/evidence/codex-executor-lock-diagnostic-20261010-v1.json。普通命令随后恢复。只读核验原18668进程及8769监听均已消失、27个持久任务无活跃项，按原配置恢复服务为24028（执行句柄19230）。认证/runtime与冻结运行清单完全匹配，两模型摘要及Ollama0.35.1保持；未重启共享模型服务。

small-H0-r1原任务仍为waiting_user/state_version13。通过正式接口撤销过期未消费审批5a39508c-0767-4708-aca7-e37d6772808f，同一原生任务提出新审批83a443a9-44f8-4402-a52c-c6b72fab73d3。操作者只批准公开six1.17.0元数据及精确匹配发行文件范围，不证明模型所选文件存在。实际获取器拒绝six-1.17.0-py3-none-any.whl，返回PRECONDITION_MISSING；未替换发行文件或下载/执行包。随后模型复用已消费审批失败并检查原失败动作，按原三次无新增证据暂停（NO_PROGRESS/state_version26）。全部7份模型请求用量已测量；原缺six真实失败保留，无结果/报告，自动与完整评分均false，不自动恢复或重建该试次。

上述试次包含基础设施中断及撤销过期授权交互，不能冒充无中断首次成功，14461.56702秒观察墙钟含中断/操作者等待，不能用于纯模型速度比较。正式汇总已更新，仍保留全部18项分母和未运行项；自动汇总failure_category仍unresolved，不伪造不存在的报告语义审查。审批恢复和精确范围决策分别保存pilot-small-H0-r1-expired-approval-recovery-20261010-v1.json与pilot-small-H0-r1-approval-2-20261010.json。large-H0-r1首次对照已创建唯一任务6cb951c5-234b-483d-90f1-dacf0ecccb64，逐项处理six1.17.0精确请求94c87090-a018-4009-9003-7a163dfac2ad，仍需实际作业/交付观察及独立评分审查。未修改产品代码、输入或清单，没有pytest/完整UI重跑；阶段1仍未通过。

后续首次真实pilot：GENERAL-CSV-DEBUG-small-H0-r1沿用原清单，创建唯一任务0d55d391-86e3-4460-8444-beabfa963bbf。最后成功观察已读取三个源输入，真实断网code作业32ba3164-6f49-45d0-ba6f-71cf7ff5c75d退出1，保留ModuleNotFoundError: six；随后waiting_user/APPROVAL_REQUIRED，未有结果/报告或通过声明。执行Oracle仍未注册给任务，未补提示/新建重复试次。旧清单report的18项not_run是执行前快照，不能冒充这次实际状态。

待批请求5a39508c-0767-4708-aca7-e37d6772808f选择了尚未核验的six-1.17.0-py3-none-any.whl；实际获取器仍须校验官方发行文件。操作者审批命令没有执行：自动审批审查因用量上限失败；其后执行器启动连续报helper_unknown_error: setup refresh had errors，原服务执行句柄86822也不可用。这不是下载失败或模型完整质量判定，也不能证明OS服务18668停止。2026-10-10 08:59:44 UTC账户工具已报普通用量可用，但执行器仍不能启动，当前任务/授权/进程不能重新核对；旧授权记录有效期06:11:15 UTC已过。没有绕过审查、改数据库、重启/取消或批准过期授权。恢复后先核验实时状态和精确服务身份，继续原task_id；保留基础设施/交互中断，不能当作模型失败重跑。证据为workspace/evaluation/trials/GENERAL-CSV-DEBUG-small-H0-r1.json及workspace/evidence/pilot-small-H0-r1-executor-interruption-20261010.md。

新增evaluate freeze/run/review/report，复用现有认证API、请求幂等记录、原生Harness与受限Jobs，不另建Agent运行循环。GENERAL-CSV-DEBUG族提供本地说明/交易CSV/植入脚本错误及缺six的只读源码；独立oracle仅由评分器读取。固定两模型×H0/H1/H2×每组合三次，18个试次ID由冻结清单摘要确定。失联创建从原requests表恢复，观察超时/暂停/待审批只续观察原task_id，不自动重建/恢复/批准/验收。启动/runtime在认证范围返回代码/配置/角色及库版本固定快照；不把后来磁盘内容当成旧服务运行版本。

自动评分核验实际产物SHA/字节、独立数值、真实成功计算生产者/环境版本、缺包失败诊断、源不变、引用及完整读回窗口；另有绑定报告SHA/任务版本的独立语义审查，不能覆盖失败计算。汇总保留未运行/失败/活跃/待交互分母、提供方已测量及缺测用量、观察墙钟时间、审批负担和待归因失败。13项新增定向检查通过（0.29秒），Ruff/diff通过；CLI帮助、默认suite及生成EvaluationSuite/Review schema核验通过。仅新增边界检查，无全量pytest、原UI用例或报告模型重跑。

代码提交f1224aecfaabe732e5ea30e1b36cbaf4465a0873后，核对61496精确创建/目录/命令/监听与26个任务空闲、作业4失败64成功、动作365确认47失败及无委派，正常关闭并加载为18668（创建1791610260.4455912，执行句柄86822）。实际freeze在5.40秒完成，未请求模型推理或执行作业；前后同一动作/Jobs计数不变，搜索状态not_probed。首个清单为workspace/evaluation/general-pilot-20261010-v1.json，摘要d455ca93880715116d735dec19197663ca87c8542175cb6a58ccb644425fbbb0；实际report全部18项not_run、各组合scheduled=3/observed=0，未标通过。

冻结small摘要2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd、large摘要aaee06c39dcf2437cde036998d960e1fc1494b8191be7cc9657d01e509097813，均Q4_K_M；模型模板/tokenizer元数据摘要、后端参数及Ollama0.35.1、容器29.1.3、硬件/配置条件已捕获。探测stderr的WSL警告存在编码乱码，保留原始结果，不据此声明全部诊断质量通过。服务/状态证据为workspace/evidence/evaluation-pilot-service-ready-20261010-v1.json，使用说明见[EVALUATION](EVALUATION.md)。

这是首个通用族的pilot入口与冻结前提，不是完整EvaluationManifest、全部通用套件或模型质量成功。显式种子、逐Worker峰值资源、正式任务抽样/发布门槛仍明确missing/TBD；真实六组合执行/报告审查、其他通用交互/恢复/知识/角色、预算匹配/消融及后续领域/低资源/真实≥100GB/发布均未完成。下一批按此明确清单逐项运行与补缺，不再临时重复同一源码报告任务。阶段1仍未通过，Agent联网搜索继续暂缓。

## 1.27 历史增量：补缺失动态检查，收敛阶段出口

核对原服务86692的创建时间、命令、目录和8769监听及任务/作业空闲后正常关闭，再启动同配置服务61496（执行句柄1792），加载1.26最终来源回执分组修复。冻结large摘要aaee06c39dcf2437cde036998d960e1fc1494b8191be7cc9657d01e509097813不变，其他服务未操作。只向原任务d4db4fbb-5057-4393-80fb-06ab22bcdb0a发送一次具体纠正消息，要求补动态检查与错误报告，保留旧成功产物和原执行顺序/失败，不新建任务或自动验收。

原断网Jobs完成唯一新增dynamic作业b48f1b11-4da9-458f-bb9d-e561ded228d1，实际观察回执提供build-requirements产物eb8cbc7c-6f8d-4f8a-a08a-1a25aa6cfd9c。只读核验封存SHA/大小、原生产者/作业集合、原源码及镜像绑定通过；实际附加dynamic_requires=[]，静态根setuptools>=40.8.0。没有新增下载/构建/安装/计算或审批，旧产物ID/SHA保持。8次新模型请求均实测且被估算覆盖；它重复读取旧报告并把作业ID用于动作检查失败，最终由原3次无进展保护暂停，turn=2630cb83-e4db-4b4f-8d1a-a5ad6b789b59。未产生替换报告，完整纠正与原首次流程仍未通过，未重置/继续原样重试。

证据为workspace/evidence/source-semantic-correction-request/state/audit-20261010-v1.json（分别保存一次消息及前置状态、实际终态和只读核对）。审核脚本最初错误假定封存清单含image_id而失败；改按实际image_reference及观察回执image_id分别匹配后通过，没有再执行模型或作业。本轮没有pytest、全量回归或UI用例重跑；这次具体纠正执行不能当成干净首次模型质量证明。

复核原项目书发现历史缺口列表把其他运行时、任意源码/系统编译器扩展全部升级为阶段1阻塞。WP-06仅要求选定Python/运行时；具体依赖修复与诊断仍必需，R/GIS按原阶段2加入。新增STAGE-1-GATES将原必需能力与证据缺项对应，保留确定性硬案例全通过和六组合通用pilot，不另设所有开放模型任务首次100%成功。下一批是明确本地复合任务、当前配置证据适用性、资源准入/恢复/知识/角色交互和六组合pilot的缺项，不继续围绕同一报告原样调用。阶段1及原领域/正式评估/低资源/真实≥100GB/发布仍未完成，联网搜索仍用户暂缓。

## 1.26 历史增量：下载复用、构建信息保持与无响应历史预算

acquire_dependencies默认读取同任务/修订、输入版本、来源、完整发行文件集合和解析锁完全相同的已确认下载。原消耗审批或新公开下载锁中的request_sha256证明完整历史请求；核验原生产者、锁/元数据/发行文件实际字节，返回原产物ID及reused_from_action_id，不串联读取别名，不联网或重新下载。显式已消费审批不能用于新发送，refresh=true绕过读取复用并沿用原网络授权。旧公开锁缺请求证明则不复用；历史私密已消耗审批可证明旧下载。成功静态/动态检查须已有实际job.observe回执，再用现行源码/作业清单验证器核验，保留requirements与frontend_requirements到原生后续请求；完全相同的检查信息只保留一份，原作业/观察引用仍在。二进制文本读取仍失败，但对实际已下载包返回正确*_artifact_ids衔接提示，未宿主解包。新网络/执行器/缓存表/运行循环均未引入。

原生压缩可能保留只有回执/用户文本的历史，移除全部带用量的模型响应；不能伪造响应或扣减启发式压缩回收。估算器现在仅在没有当前响应锚点时，核验同任务/修订/角色/完整冻结模型配置的既有已测量请求、响应摘要存在、实际快照SHA及Schema SHA，保留其全部input+output用量，再支付所有当前消息的UTF-8界和新/变化组件。当前响应存在但不匹配时不借其他回执。规范化权威事实JSON按字段/指导行匹配已支付组件；任意指令/非规范JSON保留整段成本，新增重复发生次数不免费。只改变计费，未改实际目标/消息/原生历史/32K与4K预算。篡改或无证明仍全额计费，不冒充提供方当前响应锚点。

验证分批按实际修改执行，无全量：下载/源码/检查事实及受影响原生边界39通过（10.62秒）；规范事实预算后上下文23通过（8.34秒）；无响应基线新增/回执边界5通过（1.15秒），其他通过结果复用。真实副本最初未重映射封存路径被权限边界挡住，补复制当前任务文件/映射后暴露31052原预算超限；v7计费不适用，因为当前历史已无响应锚点。v8基线修复后，真实已下载setuptools wheel及其原锁/元数据/当前输入/解析锁验证通过，预算21438低于原28672；动作370/作业63/审批47/上下文367前后不变，未请求模型或执行作业。证据为download-reuse-real-history-20261010-v1-next.json；这只是副本/开发者核验，不是模型直接调用下载读取复用的质量证明。

核验19848旧服务精确身份且任务/作业空闲后正常加载修复为86692，冻结large权重不变。唯一真实UI续执行沿用d4db4fbb-5057-4393-80fb-06ab22bcdb0a及所有原输入/预算，未新建任务或重跑small。约5分钟后形成待语义验收候选：完成前端wheel获取、一次成功源码构建（此前缺setuptools的一次失败保留）、一次实际独立安装及两组真实docopt调用，产物均实际读回。只读核对两份JSON的parsed/version与预期完全一致，生产作业/环境/封存字节及原输入不变；22份v8请求均已测量且被估算覆盖。模型直接传入既有setuptools的原wheel ID，没有再次下载该文件，但未实际调用新的下载读取复用路径；旧解析读取的两个动作仍属于1.25，不算新试次复用效果。

完整UI试次仍失败：没有独立dynamic检查，报告却声称做过；它混淆构建/运行需求，未引用实际源码产物ID，并把setuptools下载锁称作源码下载锁、抄错镜像摘要。报告里的UUID均真实不能证明引用用途正确；计算正确不能代替报告/要求覆盖验收。证据为ui-public-source-tests-20261010-v6-download-reused.json、v6失败/截图、download-reuse-real-continuation-audit-20261010-v1.json。未自动接受候选，未放宽断言或原样重跑。末尾定位并修正源码下载回执被后续wheel下载回执遮蔽的问题：沿用原operation，按来源文件组分别保留真实锁/生产者/产物，新增与源码边界2项定向通过（0.61秒）；此最终修正尚无新模型效果证明。

下一项为要求/证据覆盖、报告正确引用和针对性补全：在原任务补缺失动态检查及报告，保留已核对成功的环境/计算，不重建/重算来伪造进展；继续原通用/角色/恢复/评估与后续领域、低资源、≥100GB、发布范围。阶段1未通过，Agent联网搜索仍用户暂缓。

## 1.25 历史增量：避免已计费请求组件重复计费

旧估算器只有整套工具Schema完全相同时才能使用已计费证明；新增resolve_dependencies.refresh导致全部当前Schema再次计费。动态指令只要一部分变化，原生估算也会再次计入全部指令。现在同一任务/修订/角色的精确响应与实际输入/输出用量绑定原上下文回执，并核验实际请求快照字节SHA和原Schema SHA；逐项、逐次匹配工具定义及指令。原提供方input+output用量完整保留，不减去压缩的启发式估计，也不退还旧/已删除组件成本；新/变化/重复增加的组件仍支付完整UTF-8界及原消息/工具框架余量。缺失、篡改、跨任务/修订/角色或响应不同的回执不享受优惠。继续沿用原Harness压缩/历史/检查点；不增加运行循环、不提高32K/4K预算、不缩减目标。

验证只运行tests/test_context.py和tests/test_approval_continuation.py一次：35通过（22.17秒），含新增变化组件、重复指令及快照字节篡改两例；Ruff/diff通过，无全量回归。受限诊断在SQLite在线备份副本重建原审批等待入口和当前续执行入口，在模型调用前主动停止：37151→25916、37135→25900，均低于原可用28672。原数据库动作358/作业61/审批46/上下文355前后不变。首次诊断脚本用了错误审批表名而未编译，随后修正；v1曾给已保存检查点重复传入提示，不能作为精确续执行证据，v2按原入口的提示规则修正。两个v2证据为context-components-real-history-20261010-v2-prior.json及-v2-next.json。这是编译证明，不是模型完整质量验收。

确认原8769进程83512及全部任务/作业/动作空闲后正常停止并加载修复，新服务19848；8768无监听，未操作未知服务。核验冻结large权重摘要不变。UI只显式允许恢复本次修复的CONTEXT_EXHAUSTED暂停；复用同一任务d4db4fbb-5057-4393-80fb-06ab22bcdb0a、原输入/作业/审批，未新建任务或重跑small。唯一真实续执行试次7.3分钟后失败：共12份实际编译输入，11次已返回提供方用量（9797–11244输入tokens）均被保守估算覆盖，全部输入估算24128–27489小于原可用28672；最后一次模型流超过原300秒预算，MODEL_REQUEST_TIMED_OUT暂停。没有因观察等待超时重启或原样再跑。解析快照实际复用两次，metadata_requests=0；已有setuptools wheel再次获取的相同成功请求被UI操作员拒绝，不重复下载。模型重做静态检查、漏补动态检查前端、读取源码归档导致UTF-8错误后未完成构建/环境/两次JSON/报告，完整流程失败；未提高预算、重置进展或缩减范围。

终态证据：ui-public-source-tests-20261010-v5-context-repaired.json、按task_id的v5失败快照/截图、context-components-real-continuation-audit-20261010-v1.json；服务/权重/源码身份为context-components-service-ready-20261010-v1.json。已测量请求逐一核验实际快照字节SHA、预算和estimate_covers_observed_usage，没有把未返回最后一次计为测量通过。下一项按具体失败修复已有下载产物的读取复用及文件类型/构建检查信息衔接，再进行有原因的同任务恢复；不原样重试超时、不恢复其他历史暂停任务。阶段1、原领域/评估/规模/发布范围继续未完成，Agent联网搜索仍暂缓。

## 1.24 历史增量：复用已确认解析快照

resolve_dependencies 在没有显式 approval_id、refresh=false 时，复用同一任务/修订的精确历史解析快照。当前输入版本、需求、目标、镜像、已提供构建产物等构成完整请求；历史私密结果由原已消费审批的完整请求证明，新结果在原不可变解析锁中保存 request_sha256，公开历史若缺此证明则不复用。核对原确认动作、锁及元数据实际字节、当前目标/镜像、构建元数据和所选发行文件；不沿复用回执串联别名、不建立新缓存表或调度器。返回新读取动作和 reused_from_action_id，沿用原产物/生产者，metadata_requests=0，recorded_metadata_requests保留历史请求量。原控制/角色授权和无进展预算保持，重复快照不算新证据。refresh=true明确绕过复用，重新走原网络审批/限额；显式已消费审批ID不能被复用逻辑转成新网络许可。历史快照不声明当前远程索引状态，也不代表安装或任务完成。

本轮仅做定向验证：232为50通过/2失败（18.84秒），两处是测试问题：刷新审批复用了旧测试request_id，以及把“输入版本变化须申请新审批”过度定义为必须硬失败。修正唯一请求ID和原契约判据后233仅重跑这两项，2通过（0.81秒），其余50项结果复用；234复用既有源码候选/构建元数据夹具检查不完整闭包和远程下载边界，2通过（1.26秒）。Ruff/diff通过，没有全量或新建两模型任务。一次开发者只读核验原真实任务的setuptools解析锁/元数据及当前镜像通过，metadata_requests=0，动作358/作业61/审批46计数前后相同；未调用模型，不是模型质量验收。证据为 resolution-reuse-real-receipt-20261010-v1.json。

同一large任务d4db4fbb-5057-4393-80fb-06ab22bcdb0a继续保留原输入、权重、历史作业和精确审批。跨到2026-10-10后原执行句柄不存在，8769无监听，才重启服务；没有仅因观察超时重启。既有UI操作员现在对实际已成功且完全同摘要的重复请求拒绝审批，过期则撤销，不批准重复副作用；决策记录原已确认action_id，其余发行来源/用途/字节和完整计算/报告条件不变。第一次续接通过UI撤销旧过期重复请求后，MODEL_UNAVAILABLE在模型请求前停止（v4 reporter，3.2秒）。当前Ollama只有qwen3.8:27b，摘要aaee06c39dcf2437cde036998d960e1fc1494b8191be7cc9657d01e509097813、大小17741872154和Q4_K_M均与任务冻结记录一致；确认目标别名不存在后本地cp恢复qwen3.8:latest，没有下载/删除/替换权重。当前qwen3.5:4b摘要变为2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd，旧小模型证据仍为历史，不能用于新权重验收。

修复别名后仅通过UI恢复刚因MODEL_UNAVAILABLE暂停的当前large任务，第二次续接在发出模型请求前因CONTEXT_EXHAUSTED停止（v4-restored-model reporter，2.1秒）：保守估算37773超过原可用28672。没有提高32K/4K预算、重置无进展计数、缩减目标或盲目恢复其他任务；该真实流程及复用修复的真实模型效果均未通过。已读取原生TieredCompaction/SlidingWindowCompaction和当前估算器，下一步定位Schema变化后的保守开销与压缩配方，不通过原样重跑绕过。原始失败/截图另存v3/v3-c1/v4，最新按task_id记录；reporter为 ui-public-source-tests-20261010-v4-continuation.json、ui-public-source-tests-20261010-v4-restored-model.json。模型别名与服务身份/源码证据见 resolution-reuse-model-*.json、resolution-reuse-service-*.json。

一次默认CLI doctor误执行了既有搜索探测，结果为失败，不计搜索验收；这是开发者诊断调用失误，不是恢复搜索开发。后续本地诊断须显式doctor(probe_search=False)，不再调用默认会探测搜索的CLI路径。受限执行器实测仍可用。完整阶段1及原通用/领域/评估/低资源/≥100GB/发布范围继续保持未完成。以下1.23及更早为历史。

## 1.23 历史增量：原生审批暂停与精确续执行

依赖请求返回 approval_required 后，根 Agent 通过 PydanticAI 原生 DeferredToolRequests 结束当前运行并等待用户决定。ApprovalPersistence 在原 Harness SqliteStepStore 保存带未完成调用的 interrupted 检查点；应用只记录调用/提案/审批绑定，不复制历史或另建运行循环。续执行核验当前任务/修订、调用 ID/工具/参数摘要、已确认提案、精确审批摘要及原授权；仅注入可信 approval_id，由既有 Network.admit 再核对范围、来源版本、有效期和一次消费。拒绝/撤销形成原生 ToolDenied；同批审批未齐时继续等待，不执行已批准的部分，不发模型请求。只有已绑定审批等待的 interrupted 检查点可作此续执行入口，不把任意中断当作可重放授权。角色和暂缓搜索保留既有行为。

验证保留实际失败：224相关测试27通过/1失败，暴露框架要求同批全部审批决定；修正后225为28通过。226中间全量358通过；227新增恢复夹具11通过/1失败，因测试重建 Engine 未保持同一假执行器身份触发真实版本校验，修正夹具后228为12通过。加入独立源码获取审批后229中间全量362通过（62.06秒）；续接提示和 ModelResponse 判断调整后230相关45通过（11.88秒）。此前一次全量命令因自动审批审查的额度错误未执行，不能记为通过；额度恢复后改为有针对性的验证，231只运行说明改动影响的预算/Schema续接3例，全部通过（0.67秒）。Ruff通过；pytest缓存权限警告仍保留。362项不是最终全部改动的全量复验结论，最终候选不重复全量运行。

真实 UI v3：small任务 `dfa68447-eb5d-4a7e-87db-8f987e203b14`（36.1秒）两项精确审批均以原调用 ID 唯一消费；随后把已下载源码的 artifact_id 放入本地资源参数 source_refs，三次权限拒绝后 NO_PROGRESS 暂停。large任务 `d4db4fbb-5057-4393-80fb-06ab22bcdb0a` 前三项审批续接成功，第四项正确 wheel 请求被测试脚本错误挡住：JSON字段顺序不同，但 name/version/filename 元组完全相同。此为开发者验证缺陷，不归咎模型。修正为明确元组比较后，通过 PUBLIC_SOURCE_CONTINUE_TASK_ID 在 UI 重开同一个等待审批任务，复用其动作/检查点/原输入/历史审批，不新建任务或重复已经成功的作业。原 v3 reporter和失败快照另存保留。第四项审批成功消费后，大模型混用 inspect_build/static 与 build_only，修正一次后又重复 static，再提出相同已成功的 setuptools 元数据请求；操作员拒绝批准成功操作的重复请求，任务保持 waiting_user/APPROVAL_REQUIRED。完整试次和同任务续执行均失败，没有计算 JSON 或中文报告，不标记阶段验收成功。

证据：`workspace/evidence/ui-public-source-tests-20261008-v3.json`、`ui-public-source-tests-20261008-v3-continuation.json`、按任务保存的失败/决策/截图、`native-approval-ui-comparator-diagnosis-20261008-v1.json`。一次只读原生绑定核对 `native-approval-real-ui-binding-20261008-v1.json` 检查7项等待，其中6项实际消费、1项待审批：原生调用/参数摘要、提案、授权及唯一消费均正确；这只证明审批关联，不能代替流程质量。真实2任务共4个成功作业，没有最终构建/环境/计算产品；原预算、镜像和隔离限制保持。工具说明已补清 *_refs 为本地 resource_id、下载使用同名 *_artifact_ids，以及 inspect_build/build_only 互斥；尚未用真实模型验证这段新说明的效果。

后续依照用户对重复测试的反馈：小改仅验证受影响行为，全量集中到阶段交付；相关输入、代码、配置和条件未变时复用有效证据。模型重试须有具体修复或有区分力的诊断，原样失败不重跑。当前下一项产品缺口是依赖操作完成后的工作保持、错误纠正和成功结果复用，不能靠继续增加同类夹具或放大预算解决。完整阶段1、原领域/六组合/低资源/真实≥100 GB/发布安装备份恢复范围仍保留且未完成。下文1.22及更早为历史。

## Evidence commands / 历史验证命令

`pytest -q --basetemp workspace/test-temp-20261006-1`：12 passed，2.27 秒。覆盖认证/Origin/Host、请求重放与冲突、伪造授权、路径/版本、动作重传、产物不可变/篡改、消息与修订、取消终态、重启暂停、HTML sandbox、禁止云端和设置 CAS。

真实模型试次按 task_id 保存，不删除失败记录。已暴露并修复新框架的异步检查点与用量属性适配；两个模型均真实读取/写入过数值夹具，只有完整烟测全部独立检查通过的试次才能标为模型闭环通过。

浏览器复验：2 passed（11.0 秒），保留 desktop/mobile 截图和 Playwright JSON，截图不是执行证据。small task `fb6a156a-d3a4-498a-9467-b94021f381fb` 与 large task `5a3434f1-5a26-484e-a95f-655869e88ae0` 的六项独立核对均为 true，证据为 workspace/evidence/model-<profile>-<task_id>.json。

`pytest -q --basetemp workspace/test-temp-20261006-4`：18 passed（6.51 秒），增加创建响应丢失/重启对账、停止未确认、容器身份不匹配、排队输入变化、3 MiB 产物封存重试、作业权限及暂停检查。ExecutorFixture 是故障夹具，其通过不等于真实容器执行。

当前 Docker Desktop 后端在 initializing Inference manager 时因 dockerInference Unix socket 无法访问而退出，引擎 API 超时。单个套接字改名备份也返回“系统无法访问此文件”；未重置 Docker 或删除全局文件。代码作业缺少执行器时保持明确阻塞，不在宿主机执行生成代码。

新增验收边界后：24 passed（6.07 秒），Ruff 全通过。覆盖显式验收/请求重放、要求修改、规范/候选冲突、篡改阻止发布、封存后终态前崩溃的恢复、活跃任务不能伪造验收。用户验收始终标记 user_attested，scientific_accuracy 为 not_assessed；不把用户点击转成科学精度证据。

随后原生框架 Hook/输出 Schema/检查点兼容测试加入：25 passed（5.78 秒），Ruff 全通过。扩充工具集后的 small `4e1a0a08-8402-42ff-99b9-87df202ef12e` 及首次 UI 实际任务因输出协议而失败，记录保留。明确 final_result 约束并设置最多三次有界输出纠正后，small `769f75fb-48f7-41f1-9dae-24b90aac3f6a` 六项检查全部通过（10.19 秒）。同轮 large `6e9baab7-7992-4c46-9a25-d4e6b94ee485` 全部通过（28.25 秒）。

真实模型浏览器任务复验通过（12.1 秒／总 16.2 秒）：登记 CSV、页面发起任务、独立数值核对、源文件不变、显式验收、封存清单与已完成状态。workspace/evidence/workbench-acceptance.png 与 workbench-completed.png 为视觉记录；Playwright 测试及 JSON 为行为证据。两个基础浏览器检查在同次前端构建下已通过，实际模型检查在协议修复后独立复验。

CLI run 已实际登记并读取 CSV、产生报告并返回 waiting_user。初次 PowerShell 重定向暴露中文编码不一致；CLI stdout 改为 UTF-8 后复验，workspace/evidence/cli-run-utf8-20261006.json 为有效中文 JSON。CLI 明确区分回合完成与验收成功，等待超时不自动取消后台作业。

模型身份冻结已接入新任务：真实 Ollama 权重 digest、precision、size 与生成参数进入任务快照，调用前逐次核对；旧任务没有身份记录时停止执行并提示新建任务。25 项回归测试通过（4.40 秒）；新增“权重变化时在请求发出前停止”与原生框架两项定向测试均通过（0.30 秒）。冻结后的 small `372c47cc-49b3-4341-b37f-0dbb806e8c69`（12.22 秒）和 large `309ed7f5-13b7-4403-adcc-87c6cb72b134`（27.25 秒）烟测均全部核对通过，实际身份快照在相应证据 JSON 中。

文档/知识/记忆批次：完整 pytest 30 passed（9.09 秒），Ruff 通过。PDF 配置使用 pypdf 的公开资源限制，文本页和空白页行为分别核对；DOCX 逻辑文本不伪造版面页码。索引含源 SHA-256 与引用，检索只返回任务授权、仍匹配来源字节的内容。模型只有记忆读取工具，写入/停用须由认证用户 API 发起；停用后不再检索，已有审计和任务历史保留。

真实文档烟测：small `97df8929-8ba1-43f9-8745-e934af83fdd7`（15.23 秒）、large `304b3205-7f39-42f6-a4ab-70c01de5f33f`（33.25 秒）的八项核对全通过，包含实际文档读取、索引、检索、事实、来源引用、报告和源文件不变。每次证据保存在 workspace/evidence/documents-<profile>-<task_id>.json。长期记忆浏览器验证通过（1.5 秒／总 2.8 秒），实际完成保存、刷新及停用。

上下文批次：原生 Harness 管理工具结果外置、按句柄读取、分层清理和滑窗。应用只保存编译输入的私有快照/SHA-256、顺序消息指纹、模型身份、源/动作/产物/作业引用与实测用量，不开发第二套历史管理器。规范/控制状态、当前回合原始请求逐次注入；动作读回限当前修订，封存文本读取有字节上限、跨 UTF-8 窗口不丢字符，不能读其他任务产物。38 项回归通过（6.84 秒），Ruff 通过；协议夹具证明外置内容完整、隔离、工具配对、权限/当前请求保留、超限停止和持续流式输出仍按总时限停止。这些协议夹具不替代真实模型试次。

预算首次试次 `72a3f0ee-dc9a-4399-b6e8-7cbc149b8890` / `de65d52b-a6f6-441b-8650-0cdbe8cdd7ef` 因重复计入历史中的旧指令副本而过早暂停；修正为实际消息文本、当前指令、工具 Schema 及协议余量。small `60fbffe3-d4fa-46f1-888a-45574dee77a6` 文档八项通过（18.25 秒）；large `5effca07-f792-406b-bc6f-67ef1969c690` 事实/引用正确，但数值是带单位字符串，严格核对未通过。明确 JSON 数值契约后 large `86347c0e-eb2e-4689-bd04-256de3c004e9` 八项通过（26.28 秒）。失败均保留。

真实长任务以21份软件夹具串联、两回合执行，不代表科学实测。small `bdc17357-92fe-4406-9e71-3a30e2504d5f` 已实际读21份并读回进度，但最新用户消息被滑窗移出后误判阶段，未检索/生成最终报告。原脚本只取100条事件的第一页；补充审计完整189条，纠正“没有读全/读回”的错误观察，原证据不删除。一次零输入用量不能算实测，现标记 unavailable。通过逐次重新注入当前原始请求修复阶段恢复。large `a189d751-3d6d-46c8-ab99-d3370ba0a4bd` 在第13次请求长时间无完整响应，558.48 秒手动暂停本测试，记录不通过；这暴露 HTTP 读取超时会被流式传输刷新，现新增冻结的单请求墙钟预算，并用持续流夹具证明停止。

修复后 small `27315e81-62a6-4b68-9e62-355d456e7518`（59.61 秒）顺序实际读取全部21份，无目录捷径，读回进度、检索最早来源、规范保持revision 1、压缩实际发生、所有实测用量在保守估算内，JSON事实和引用全部核对正确。32份私有输入快照独立核对 SHA-256、消息引用、工具配对、当前请求/只读策略/修订和预算均通过。报告只写局限，没有代号与数值，故整项严格不通过；原任务只明确要求报告说明局限，现已明确完整报告字段后复验，未放松检查。

明确报告契约后的 small `0918d2a6-43b0-43f4-b6cb-cca8953e07da`（77.08 秒）JSON事实、引用、中文报告、只读来源、完整21份顺序、进度读回、早期检索、实际压缩和用量均核对正确，33份输入快照独立验证全通过。原检查额外禁止任何目录列举，因一次授权目录列举标为失败；该条件过度定义了“不能猜序”的目的。新增更直接的严格顺序审计：21次来源读取必须分别发生在21个不同模型请求后，防止一批猜测路径替代串联读取。该审计全通过，原试次及失败条件仍保存；补充证据为 workspace/evidence/context-sequence-audit-<task_id>.json，快照证据为 context-snapshots-<task_id>.json。大模型的完整长任务质量仍未通过，不据小模型单次证据宣称两模型或阶段1验收成功。

## Roles / 角色批次

角色/配方批次：五个 YAML 长期配置随新任务冻结；仅 H2 根协调者按需委派，复用原生 SubAgents 循环。应用在委派前登记动作与 AssignmentRequest，创建独立依赖、输入/产物授权子集、角色与协调者能力交集、私有工具外置目录、输出命名空间和独立检查点。AssignmentResult 的角色种类、真实证据/产物/作业归属由代码核对；未结束作业、副作用或委派阻止终态/验收。子角色不能递归委派、修改父规范或提交父验收；质量结论作为建议保存，不自动合并或转换成科学精度证据。环境诊断不探测外部搜索地址。

权限/预算/恢复回归扩至53项（15.98秒），Ruff通过。新增覆盖两原生委派隔离、权限交集与越权无副作用、跨重启累计委派上限、父子共享请求预算、恢复进入新父回合后的计费、输入变化、虚构引用、未结束作业、独立作业产物目录、父/子检查点恢复不重复已确认写入、协调者预算、正在等待模型时的委派总时限，以及子作业停止后等待委派与父任务的取消终态对账。故障夹具仍不代表真实 Docker 执行。恢复测试暴露 SDK 不允许把旧原生 run ID 复用于带旧消息的新运行；现以 agent_runs 关联固定逻辑回合与每次新的原生运行，保留原消息 ID 与 Harness 检查点，不复制或改写原生历史。委派期限跨恢复保留，协调者本次运行也有冻结角色时限。Windows 私有快照和委派输出使用扩展路径 I/O；尚不宣称全部来源、文档和下载路径均已完成长路径验证。

真实角色试次：small `b4564e60-c9ff-4c78-b7a3-10af7b0a3708`（67.81秒）完成两个隔离角色与报告，但数据角色使用文本版本字符串代替完整来源对象，质量角色误将其判为通过，严格核对不通过。明确委派工具、输出字段和判据后 small `84202810-5088-408b-9e74-74dab70ae5b9`（8.53秒）添加不允许的 constraints 字段，在有界协议纠正后仍失败；现在工具参数描述包含严格 AssignmentRequest Schema，纠正消息列具体字段错误，不放松额外字段拒绝。large `6680410a-9181-49ab-9f7d-b8ff39366b1a`（132.08秒）全部12项核对通过，包含源文件不变、两角色真实读取、完整 SHA-256 来源对象、独立质量读回、独立输出和父级报告；子级私有输入没有父级内部标记。证据为 workspace/evidence/roles-<profile>-<task_id>.json。

更新后 small `d4478b44-91ef-40a7-8357-a1dc407b8b53`（43.42秒）事实、完整引用、质量读回和隔离通过，但父报告使用英文标题，严格检查未通过。同一任务首次中文补充回合（12.19秒）仍不通过。发现权威任务事实遗漏 output_preferences，现逐次注入并明确报告标题/正文遵守用户语言、保存后实际读回检查。第二次补充回合（12.25秒）全部13项核对通过，保留原两委派、原规范revision 1、新报告版本，原失败证据及产物不删除。通过证据为 workspace/evidence/roles-follow-up-small-d4478b44-91ef-40a7-8357-a1dc407b8b53-a359e9a23e9a43428a16778a568e890c.json；这不代表小模型初次单回合即可稳定成功。

前端构建与类型检查通过；新增角色浏览器检查1项通过（6.4秒），使用上述真实 large 记录核对工作方式选择、持久委派、真实 JSON 来源产物预览及桌面/手机布局无横向溢出。截图为 workspace/evidence/workbench-roles-desktop.png / workbench-roles-mobile.png。CLI增加 --harness 与三份角色/委派 JSON Schema。H0/H1/H2 配方定义与入口已实现，正式六组合实验、完整冲突/异常恢复交互和阶段1验收均未完成。

## Network and queued input / 网络与输入排队批次

新任务冻结 private/public 上下文分类、搜索提供者与地址。默认 private，每个查询/URL 的精确载荷、目的地、规范修订、输入版本、有效期及 SHA-256 形成审批记录；认证用户决策有 user_event，批准单次消费，重复请求 ID 不重复审批或排队。审批前不做 DNS 查询；每次 DNS、连接、发送、重定向以及保存响应前重新检查控制/版本/授权。public 必须由创建接口明确声明，不允许私密输入、记忆或环境读取；普通消息不能改变分类，子角色禁用网络。读取器固定验证后的数值 IP，TLS 保留原主机校验，拒绝私有/混合 DNS、越界和降级重定向，限制 4 跳、2 MiB、40 秒总时限并主动停止持续逐字节响应。保存原始字节、提取文本及 URL/SHA-256/快照产物引用，声明字符集有界解码，不执行网页脚本或子资源。

修复消息接口以提示词字符串判断重放的逻辑。用户消息、审批决策与继续回合在同一应用 SQLite 事务提交，独立 turn_requests 关联 request_id 与真实用户事件；这是执行队列，不新增模型历史管理器。正在运行时接收补充、完成后按顺序启动；暂停不会被消息或审批解除，取消/规范修订停用排队回合，服务重启先暂停并对账。恢复优先继续中断的根回合，再处理后续输入；未处理回合、待审批/执行请求阻止验收。

完整 `pytest -q --basetemp workspace/test-temp-20261006-72`：98 passed（15.63 秒），Ruff 全通过，前端类型/构建通过。网络协议与原生续跑夹具覆盖私密载荷审批前无 DNS、单次消费/请求重放、跨任务/过期/撤销/拒绝/来源与规范变化、解析后撤销无连接、私有/混合/多播地址拒绝、连接关闭、缓慢头部/正文总时限、公开任务私密工具硬拒绝、错误搜索页面不伪装空结果、原始快照字节与字符集；原生模型夹具证明审批继续回合读取原检查点且不重复已确认动作。夹具通过不替代真实外网与模型试次。

真实网页/审批试次 small `3a3afbab-390a-4721-b864-dd81cd34e03c`（23.06 秒）、large `e65057b1-ff4c-46bd-b959-4cb1f7ff9c1e`（57.25 秒）均全部11项核对通过：提出精确 Python 官方主页 URL、待审批返回、审批前无快照；审批后恰好一次读取、单次消费、实际原始字节 SHA-256、中文报告真实 URL/摘要/快照引用、报告读回、原生继续回合以及未自动验收。证据 `workspace/evidence/network-<model>-<task_id>.json`，独立运行库在 `workspace/network-runtime`。测试审批由脚本本地操作者发起，仅针对公开软件网页夹具，不包含真实秘密、不代表真实人对任意外发的同意、不构成科学精度证据。

真实待审批 small `c8395da6-f09b-45a6-bf05-8ba748a2a605` 与 `d94e9477-a01b-4a1a-a292-682a0d42fdfc` 分别经工作台拒绝，后续回合完成、修订仍为1、审批仍为 rejected，没有网页快照。浏览器首次1项通过（4.8秒／总5.8秒），调整详情滚动后再验1项通过（5.4秒／总8.3秒），保留审批详情桌面/手机截图，无横向溢出或页面错误；截图仅作视觉记录。

搜索支持 Bing RSS 与 SearXNG JSON 解析，仍未通过真实完整搜索。实际 Bing 探测 timed out，证据 `workspace/evidence/search-probe-5ba096cc1400473eac0cb7101829dc70.json`；诊断 `network-doctor-20261006.json` 同样返回 search=failed，Docker API 仍 unavailable。SearXNG 需 JSON 启用，私有/回环或自定义端口本地实例尚未支持，领域网页 PDF 暂未接入网络读取。这些缺项仍在原计划内，阶段1与V1不标记完成。

## Search and bounded reading / 搜索与有界读回

默认搜索改为 Baidu HTML，保留 Bing RSS/SearXNG 配置。新任务冻结连接器，旧任务保持原配置。普通结果只采用真实目的 URL，过滤广告/推荐/脚本；验证码或不识别页面不伪装空结果。Baidu/SearXNG 的页码进入精确请求授权，下一页仍须实际验证；页码提示不证明分页可用。网页先返回完整来源元数据和最多4000 UTF-8字节的正文，完整字节快照和提取正文仍封存为产物。

原生按行读回长单行 JSON 会越过模型预算，新增同一 LocalFileStore 的4000字节窗口读取，不另建工具结果存储或消息历史。`read_artifact` 从12000缩为4000字节，避免正常正文窗口又被外置一层；EOF明确提示停止。写入精确 UTF-8 字节，修复 Windows LF→CRLF 导致预览游标与保存文件失配。原生用量锚点加新增正文/指令/完整 Schema 的保守余量替代重新计费全部历史；没有有效锚点仍按字节估算，实际用量超过估算则停止。新增 citation_actions/view=citation 从当前授权修订的真实来源动作复制元数据，核对快照，不代替正文事实检查。

`doctor` 分开报告 startup_ready、general_prerequisites_ready 和 general_ready：仅默认模型前提决定 CLI 启动退出码，所有配置模型/搜索/受限执行器决定前提列表；工具、UI、恢复和产品验收不是探测能证明的，general_ready=false。实际诊断 `workspace/evidence/search-doctor-20261006-v2.json`：两个模型已安装、搜索7条可用，Docker unavailable，通用前提及产品验收未通过。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-88`：121 passed（25.49秒），Ruff/diff检查通过。新增协议/边界覆盖完整来源复制、跨任务/旧修订/角色拒绝、篡改快照、末次动作预算不重复计费、原生单行结果有界读回、直接产物分页不再外置、中文/emoji/换行逐字节重建、搜索页码授权以及探测不误称产品就绪。

真实 UI 搜索报告：small `8741c552-15c1-4537-a90c-6b310fc3ce25`（57.5秒、16模型请求）、large `8d2fb035-7362-4016-b547-6a5433813d85`（56.1秒、10请求），Playwright 2 passed（1.9分钟）。核对通过：工作台创建公开 H1 任务、实际冻结模型/provider、真实搜索后逐字选择官方结果链接、读取原文、两份原始快照哈希、中文报告/完整引用、报告读回、执行记录、刷新后的任务与文件。均 waiting_user，没有自动验收；截图已检查。UI 检查核对报告字段/来源，不能替代四个结构化事实类别的严格评分，更不能替代代码执行/依赖/恢复复合场景。

所有失败保留：此前 small `911774c3-cb07-478b-8594-8b6e3bbadc7a`、large `5eb9a52d-0253-4b85-9921-1e88a5d19b6a` 因嵌套外置窗口反复读取，达到30请求后暂停。更早的真实试次暴露错误类别值、来源链接改写、引用遗漏、大模型重复检查和过度预算停止；对应文件仍在 `workspace/evidence/search-*.json`。

最新严格事实试次 small `9cfb714d-3d9d-4f6f-989b-a42db65d0c5b`（9.70秒）、large `cbc814f9-71ee-41cf-b668-4e41e2f71a58`（11.05秒）均遇到搜索重定向超出授权范围，搜索/事实/报告评分未通过。不是把 UI 单次成功改成稳定性保证。搜索批次时 max_unchanged_decisions 仍只有配置，后续保护实现与验证见下节。

Evidence: `workspace/evidence/ui-search-<model>-<task_id>.json`, `search-<model>-<task_id>.json`, `workbench-search-<model>.png`. General-stage acceptance remains incomplete; no code execution or scientific accuracy claim is made by these software-search trials.

## Durable no-progress protection / 持久无进展保护

基线升为1.3，元数据库增量迁移为2：在应用存储记录观察指纹/决策/模型请求引用、逻辑回合/角色、冻结上限和连续计数。原生 Harness 继续拥有工具循环、历史和检查点。新观察重置连续计数；失败、无效窗口、空末尾及已见重复/循环观察累积。正规分页游标、来源内容与作业状态变化保留；包装动作ID、时间戳、快照ID和嵌套检查不制造新进展。排序后的JSON指纹避免首次结果与数据库重放因键顺序不同被误判为新事实。

新模型决策重复同一调用ID仍计无进展，但已确认动作不会重复执行；恢复重放同一观察不再次计数。达到上限后先持久提交观察，模型请求与工具执行入口拒绝继续，并以NO_PROGRESS暂停；已确认产物、动作与attempt保持可对账。无纠正输入的resume返回422且不增加模型请求。认证纠正消息仍在暂停时排队，明确resume后旧回合记为stopped，新回合处理纠正；旧计数、规范修订和任务累计预算保留。观察新颖性只是工程保护信号，不证明任意循环可检测或任务语义正确。

完整 `pytest -q --basetemp workspace/test-temp-20261006-97`：128 passed（21.18秒）；Ruff/diff检查通过，前端类型检查通过。新增7例覆盖跨数据库重开、冻结上限、循环读回、失败变化、EOF、重放去重/冲突、正规分页/作业状态、角色/修订隔离、来源字段保留、原生重复写与重复调用ID、停止后的无额外请求和认证纠正继续。原生单行字节存储仍逐字保留。

真实故障注入 small `7b8b4ee0-e351-4e32-8e1a-3eed55660104`（13.00秒）、large `953d2ed1-d15b-45dd-9f17-688c69fb08be`（29.67秒）：均恰好4次真实只读调用，观察计数0/1/2/3，NO_PROGRESS暂停，源哈希不变，无自动验收。证据 `workspace/evidence/progress-<model>-<task_id>.json`。这是刻意重复读取的软件保护案例，不是普通能力成功率试次。

真实UI纠正恢复检查：1 passed / 1 failed（6.0分钟）。large同一任务通过（35.3秒）：先拒绝直接恢复、无额外模型请求，纠正排队仍保持暂停、明确恢复才继续；规范revision仍1，累计请求4→9、动作4→11，旧停止记录保留；JSON准确为3/37.5/12.5、中文报告和两个文件真实读回，未调用代码/委派/网络，刷新及源保护通过。截图已检查，证据 `ui-progress-large-<task_id>.json`。

small恢复失败：尽管纠正请求明确不要执行代码，模型提交两个作业，均因DEPENDENCY_MISSING停留queued，container_id=null；UI观察320秒后超时，未把它当终态或成功。保存 `progress-small-recovery-before-cancel-<task_id>.json` 后，测试操作者取消该任务；实际复查任务cancelled、两作业cancelled，未启动容器。失败保留，不能将大模型通过外推到小模型；明确工具范围的服务端约束和小模型恢复质量继续列为缺项。新版UI测试也在观察超时时保存当前状态/事件，且核对禁用工具未调用。

English: 128 regression tests pass; both real models trigger the persistent repeated-observation guard. Only the large-model UI correction trial passes. The small-model correction trial submitted forbidden-by-request code jobs, which remained unstarted and were explicitly cancelled after evidence capture. General-stage acceptance remains incomplete.

## Task scope and corrective recovery / 任务工具范围与纠正恢复

基线1.4增加真实 TaskConstraints 边界，允许能力必须是已注册、无重复的列表，拒绝未知约束。约束来自认证创建/规范修订，存入已有 specs 并从该权威记录投影到 UI；未新增权限服务或第二份运行状态。省略/null继承配置，空列表禁用所有注册动作。未提交 constraints 的修订保留前一修订范围，响应丢失重试根据不可变原修订重新解析，普通消息不能扩权。原生工具发现、实际执行、委派交集和作业提交/启动共享同一检查；原生溢出读取只观察当前运行已有结果。完整通用能力默认保留。

UI 提供通用能力、文件与报告、仅回答、自定义选择，显示当前范围，并可明确修订目标/输入/范围、暂停后对账恢复；运行中补充消息可以排队。完整回归 `pytest -q --basetemp workspace/test-temp-20261006-101`：136 passed（38.01秒），Ruff/diff通过；前端正式构建通过。新增8例验证非法能力/字段、执行前拒绝、重开数据库、原生模型工具展示、委派交集、消息不能扩权、修订/重放、直接代码提交拒绝和排队作业取消。

显式文件范围下，两模型重新通过刻意重复读取保护：small `3b8e7f01-d375-4935-86af-9748e80ecd0a`，large `22790df4-4e3c-4314-89fa-8ebf1fc1c190`，各4次真实读取并暂停。浏览器纠正恢复2 passed（42.5秒）：small17.9秒、large23.4秒；均同一revision1保留原停止和累计预算，JSON准确为3/37.5/12.5，中文报告/两文件实际读回，源保护与重载通过，无代码、委派或网络动作。small累计11请求/10动作，large9请求/11动作。此前未设置显式范围的小模型失败仍保留，不把本次文件范围通过外推为全部通用能力质量通过。证据 `workspace/evidence/ui-progress-<model>-<task_id>.json`，截图已检查。

最终回归 `pytest -q --basetemp workspace/test-temp-20261006-103`：140 passed（29.61秒）；Ruff/diff通过。额外覆盖修订时保留输出偏好/指标请求/冻结字段，以及缓存控制回执不能取消后来回合、恢复重传不能新发起模型回合。暂停/修订/取消仅停止本次真实变更时捕获的执行令牌；恢复仅在本次真实变更后调度。旧请求重放只返回原始回执。

最终服务上的范围 UI：2 passed（1.2分钟），small `8ce4f59d-5f62-4030-85e2-73a2693b6fd9`（21.9秒，10请求/8动作）、large `01498d03-a4bb-4c5f-a2b5-f3c8df77fdc0`（49.2秒，7请求/9动作）。从“仅回答”创建开始，首回合零注册动作且回答4；经真实 UI 将目标/CSV输入/工具范围修订为revision2，保持模型配置、暂停并重载，显式恢复后交付准确3/37.5/12.5的JSON与中文报告、两文件真实读回、无代码/网络/委派/环境/记忆动作，保持待确认。再次实际POST原恢复请求返回原回执，任务状态及事件不变，没有新模型回合。证据 `ui-scope-<model>-<task_id>.json`，修改对话框/报告截图已检查。

范围 UI 测试的早期失败均保留：首次API登记遗漏Origin并与会话初始化竞争，未检查失败响应而等待不存在的来源；后一次从不同cwd启动时相对夹具路径错误；修正后又因未等待恢复HTTP回执而提前读取旧paused状态。后者两真实任务在测试离开后实际完成，失败JSON不回写成通过。脚本改为显式会话/Origin、登记响应检查、绝对夹具/证据路径、每试次唯一来源、等待恢复回执及失败后快照；原始失败报告/截图和任务记录均保留。上述测试脚本失败不能记作产品修订成功，也不把后续通过外推为通用阶段完成。

English: 140 regressions, both file-scoped corrective trials and both final real-model scope/amendment UI trials pass. Cached control receipts cannot affect newer runs or create another model turn. Default general capabilities remain available; earlier unrestricted model failures and test setup/race failures remain recorded. General-stage acceptance remains incomplete.

## Real restricted executor / 真实受限执行器

本次 Docker Desktop `desktop start --detach` 返回0，但后端实际再次在 dockerInference 套接字访问处退出，服务端 version 超时；`desktop stop --timeout 20` 返回1，未伪称停止完成，也未重置现有数据。改用项目独立发行版 `forestry-uav-agent-runtime`，实际导入路径 `workspace/isolated-executor/wsl`；Ubuntu 24.04.5 WSL 镜像388,975,696字节，SHA-256 `bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e`，官方签名及固定指纹验证通过。导入/安装/拉取/记录锁之前均重新验证来源、镜像与现有发行版归属。同名外部发行版和未登记非空目录拒绝覆盖。

guest Docker29.1.3/containerd2.2.1/runc1.3.4 实际服务端可用，24CPU/约124.45GiB guest RAM；这是当前开发环境，不是低资源验收。Docker Hub 与 Google 官方缓存实际pull因连接超时失败并返回1；Docker官方 Amazon ECR Public 路径成功。Python3.12.15 的实际 image ID及RepoDigest均为 `sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f`，WSL示例与测试配置采用该不可变引用。完整包/daemon/rootfs/image清单在 `workspace/isolated-executor/runtime-lock.json`，安装脚本固定此次解析的11个包版本；仓库缺少锁版本时失败，不静默升级。可信运行时下载不等于 Agent 搜索功能，后者按用户指示暂缓。

Docker适配器复用现有Jobs：显式Unix套接字、不新增TCP服务，参数数组与env -i，不使用shell；仅转换本地盘符的授权挂载，拒绝UNC/相对路径；私有客户端配置，不继承宿主DOCKER_AUTH_CONFIG/代理/WSLENV/合成秘密。doctor使用同一传输，探测仍不能证明通用产品验收。模型wait_job终态返回实际有界stdout/stderr，并在独立线程查询，不阻塞UI事件循环；job/角色权限与容器ID仍须匹配。

`scripts/smoke_executor.py` 实际3案例全部核对通过，证据 `executor-real-e9ad573c-133e-483f-8e48-edde14e9aff2.json`。计算task `3808603d-2603-4475-a3ce-a262e08cd16c`：3/37.5/12.5精确，源哈希不变，真实写源及写根目录失败，uid65534，网络连接失败、宿主路径/合成秘密不可见，network=none/root只读/cap_drop=ALL/PIDs128/6GiB内存与swap/4CPU实际配置相符，容器身份/日志/不可变结果核对通过。重建应用对象与存储后观察同一真实容器并回收结果，明确 `real_process_restart=false`，不能记为AC-07进程重启通过。取消task `f330b40a-ffb4-448c-bb40-72e962c883f5` 及超时task `1b0e7c93-8389-4c2f-90fa-e4e3f545d3cf`，同一容器实际Running=false、对应终态正确，无产物。

真实代码调试工作台：1 passed / 1 failed（2.4分钟）。large `99e23ff1-219e-42ed-9ad9-fbabfbff3fc3`（约1.5分钟、9请求/11动作）通过：真实原脚本NameError作业失败、wait_job得到traceback、修复后新作业成功，容器实际生成3/37.5/12.5 JSON，保存修复代码与中文报告并实际读回结果/报告；源文件不变、无网络/委派/环境/记忆动作，刷新后仍待确认，截图已检查。small `411b9243-63fa-45c6-8a01-653c1bb42c55`（50.0秒）实际失败→修复→正确计算/报告均存在，但漏读报告，严格UI验收失败，不改写为通过。每个作业的真实退出码和日志、全部事件及产物保留在 `ui-executor-<model>-<task_id>.json`；小模型另有 `ui-executor-failure-<task_id>.json`。

初次执行器烟测脚本误用未注册job.query导致任务400，修正为job.observe；没有启动该失败请求的容器。初次UI脚本误把内部restricted_code键当作可见标签，两模型在创建任务前失败，截图/报告保留 `ui-executor-test-results-20261006`。修正真实UI标签后第二次运行结果如上，保留 `ui-executor-test-results-20261006-v2` 与 `ui-executor-tests-20261006-v2.json`。当前WSL本身的UTF-16代理提示仍可能混入stderr；作业终态以真实容器退出码为准，不把提示当计算失败。

最终完整回归 `pytest -q --basetemp workspace/test-temp-20261006-109`：151 passed（25.01秒），Ruff/diff通过。新增覆盖WSL无shell参数、中文/空格盘符路径、非法分发名/UNC/相对路径/TCP端点拒绝、宿主凭证/代理隔离、输出边界、doctor前提逻辑以及失败诊断/越任务/替换容器拒绝。早期doctor夹具将绝对Docker可执行路径误判为成功、随后patch subprocess影响platform探测导致失败；已改为分别模拟Docker适配和系统探测，失败不作为运行时证据。前端产品未改动，无需重建静态资源。

English: The independent signed WSL runtime and immutable Python image are available. Real restricted compute, cancellation and timeout pass; store/object reopening is explicitly not process restart. The large-model debugging UI trial passes, while the small-model trial fails for omitted report readback despite real repair and correct computation. All failed setup/trials are preserved. 151 regressions pass. Dependencies, actual service-process recovery and complete local general acceptance remain pending; Agent network-search work is user-deferred.

## Real process recovery / 真实进程恢复

新增 `scripts/smoke_recovery.py`，为每个模型使用独立工作目录、数据库和 localhost 服务。核对新进程的命令、配置绝对路径、启动时间及自身进程树后，只有真实容器输出 RESTART_READY 才中断自己拥有的服务；确认原进程退出、同一容器仍运行，再启动新进程、观察 SERVICE_RESTARTED 暂停并显式恢复。原有8765/8766/8767服务没有停止或切换。生成代码仍在原受限容器中，测试驱动的真实终止不等于 mock。

初次 small `7b593322-bd82-4d4a-9658-7860a69afbfe` 已通过13/14检查，报告未实际读回而失败；large `8380ff83-7ed1-4c99-b627-76b6c7828e21` 实际恢复并结束，但验证脚本5秒HTTP读预算在收集日志时超时，整次记录仍失败，未回写为通过。日志端点包括inspect及logs两个各自有界的Docker调用，脚本现给出70秒HTTP读预算并记录失败阶段；超时不启动替代服务。读回检查改为实际确认的artifact.read回执，失败动作不能满足读回。

应用曾经历恢复但模型只看到已清除的阻塞，因此补充既有 `run.reconciled` 事件的任务修订/逻辑回合/原状态/阻塞原因，恢复后重新注入同一回合的权威事实。H0/H1/H2共享控制记录，子角色没有父记录，其他任务/修订/回合及没有回合字段的旧事件均排除。该事件不单独证明进程崩溃、容器存活或结果正确，也不授予权限。无需迁移数据库或第二套历史。

复验 `scripts/smoke_recovery.py --models small large` 返回0，两模型各14检查通过：small `07fa0365-22ca-4d46-9887-e0f216494ba2`、large `cb1a43aa-9f1c-4b25-b1f3-c3a2e79d8fb1`。均7累计模型请求/5注册动作，保留revision1；新服务进程身份、容器中断后继续运行/保持同一ID、一次代码提交/作业/完成标记、待确认、3/37.5/12.5精确、源哈希、报告存在和结果/报告真实读回、累计预算均通过。small服务PID456676→463408；large完整身份见证据。证据 `recovery-real-small-a10ef305-cd4b-4851-9494-19590811ac0c.json` 与 `recovery-real-large-a30d1b49-c526-4288-b9af-e623efb985f8.json`。

独立只读后审计，两模型各6检查通过：实际后续模型输入含同一恢复event_id，输入快照SHA一致、同一逻辑回合/两个不同原生run_id、报告SHA正确、脚本拥有的两次服务均已退出。审计 `recovery-input-audit-<model>-<trial_id>.json` 保存完整模型报告，未修改原始试次。报告语义仍有限：small一处称“未观察到重启中断”又称恢复正常；large承认应用SERVICE_RESTARTED记录，但将测试驱动的真实终止描述成框架“模拟”。14项通过不代表报告全部陈述正确，此质量问题仍保留。来源为3行合成CSV，不证明科学精度。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-112`：154 passed（30.59秒），Ruff/diff通过。新增三个原生H0/H1/H2恢复记录测试，覆盖恢复清除blocker后的重新注入、其他任务/修订/回合排除；角色隔离原生试次另检查父恢复记录不进入子角色。初次夹具错误假定enable_agent=false也创建turn，三项失败；改为显式建立运行中回合后通过，未作为真实恢复证据。

加入父恢复记录隔离断言后的最终完整回归 `pytest -q --basetemp workspace/test-temp-20261006-113`：154 passed（36.55秒），Ruff/diff及恢复脚本语法检查通过。脚本另保证首次运行创建证据目录、health/session独立2秒读预算、用PID与创建时间共同识别新进程、清理未确认时保存失败证据。产品前端未改动，无需重建；脚本这些生命周期收尾不改变上述真实试次输入和计算路径，实际试次记录保持原样。

English: Both models pass 14 checks following real interruption of an owned service while an already confirmed container job is running. A separate six-check audit verifies actual prepared inputs, same logical turn/fresh native runs, hashes and terminated owned processes. Original failures remain. Scoped application recovery facts are reinjected without leaking parent facts to roles or other turns. Model reports retain ambiguous interruption statements; this is limited recovery evidence, not complete report, AC-07, UI or general-stage acceptance. 154 regressions pass; search remains user-deferred.

## Scoped offline dependencies / 离线范围依赖

基线1.7新增 environment.prepare，复用现有动作/attempt、Jobs、资源授权、角色交集及不可变产物；不另建安装循环或第二套作业数据库。pip 在断网、非特权容器中使用固定wheel、完整传递解析、only-binary/no-index/require-hashes；生成 environment.zip 与 environment-lock.json。复用绑定当前任务/修订、实际准备作业、环境哈希和实际镜像ID；提交/启动/恢复核对身份，子角色须显式获得环境归档授权。准备最多32个wheel/128MiB；环境归档最多128MiB，解压最多30,000文件/512MiB，锁文件64KiB；拒绝越界、重复和符号链接。包安装及解压均不在Windows宿主执行。详见 DEPENDENCIES。

可信开发者夹具下载依据固定版本的官方PyPI JSON；直接下载实际超时失败，原部分文件及元数据保留。TUNA HTTPS镜像下载成功，全部大小/官方SHA一致；锁文件 `fixture-lock-f098f689-e309-4c78-924d-c1bc2a731239.json` 记录NumPy2.2.6的Linux/Windows wheel、python-dateutil2.9.0.post0及six1.17.0。该记录的url字段是官方原始URL，download_source=tuna；实际镜像URL由脚本替换固定前缀形成，不称独立签名。后续脚本分别记录实际下载URL与已有文件核对，缓存核对不能伪称重新下载。此下载不是Agent工具，不代表Agent依赖获取通过，也不属于暂缓的Agent搜索开发。

基础真实试次失败均保留：`faaca376-b229-48a7-9b28-f411fb7578b5` 在NTFS输出挂载内安装出现INSTALLER临时文件/权限错误；改为Linux `/tmp` 准备后，`ee59b343-fb00-4b34-a178-6b31f130390a` 安装和首次复用成功，第二次NTFS解压达到原60秒预算；转为Linux `/environment` tmpfs后，`139bc4a2-f64a-4ee8-a1d6-9a763e6d17ba` 因默认noexec不能映射NumPy原生扩展而失败。最终只为专用环境tmpfs明确exec/nosuid/nodev，代码作业普通 `/tmp` 仍64MiB/noexec；准备tmpfs及环境tmpfs各1GiB，计入原6GiB容器内存预算，不降低其余隔离，也不延长基础60秒计算预算。

`smoke_dependencies.py` 最终试次 `14ce03b2-d599-4175-878b-4d7f4c7af5e1`、task `eb87eea8-faca-4b7a-a0c6-aef7bbcb6aad` 返回0，15检查全部通过：实际缺NumPy、实际缺传递six、Windows wheel平台不兼容、真实成功安装、准确版本锁、两次同一环境/哈希复用、实际镜像一致、两次执行成功、3/37.5/12.5及日期精确、uid65534、实际网络失败、所有容器断网/只读根/cap-drop、CSV与wheel不变、宿主包清单不变。环境可在新应用对象/存储中再次解析；明确不是进程重启试次。证据 `dependencies-real-<trial_id>.json`。软件夹具不证明科学精度。

两模型浏览器检查尚未通过。第一轮在基础试次结束前启动，测试后端缺少exec选项：small `cae30e6f-9c55-4179-bd8f-a3f630509f22`、large `7fa408c9-29d8-4b71-bffd-fb5046593e69` 实际失败并达到30请求保护；这是测试启动过早，记录保留。确认专用8768测试服务无活跃任务、非终态作业及未确认动作，并核对进程命令/配置后重载，不停止8765/8766/8767。第二轮small `4f811977-7b73-411a-b587-f7d74739bdd4` 实际安装成功，但反复把锁文件ID当环境归档而到请求上限；large `ee4fb6b2-fdb5-45aa-9fe8-158155c21a36` 首次计算成功，重复嵌套查动作后耗尽上下文。

诊断发现终态环境结果重复两份完整配方，6014字节刚好触发原生6000字节外置，环境ID位于后部。现在原生等待结果直接保留环境ID、锁文件ID、哈希/镜像与安装版本，省略重复完整配方；持久作业/API/锁文件证据保留，不更换Harness外置机制。提示明确锁文件不能当环境，不按列表顺序猜ID，观察作业使用wait_job。第三轮两模型均真实安装并完成首次计算，但small `e1ca64d7-3fe0-449b-8f58-4f8b5bd84155`（14请求/12动作）误向read_artifact传额外limit，并使用来源资源ID，原生协议保护暂停；large `b72d5d36-b5f8-4845-b17e-7ff925aafcf7`（10请求）嵌套查阅旧动作后触发上下文保护。未提高预算、容许额外字段、删除读回/复用检查或改写为通过。

独立只读后审计，两模型首次JSON的SHA、完整数值/日期/实际三个版本，以及源文件不变各3项通过，补充证据 `ui-dependencies-result-audit-<task_id>.json`；不改变完整UI失败。三轮Playwright报告分别保留 `ui-dependencies-tests-20261006-v1/v2/v3.json`，原失败截图和任务JSON保留。第三轮截图已检查，任务实际显示暂停。最新专用服务8768加载修复API；其他既有服务尚未重载此批API。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-121`：165 passed（31.97秒），Ruff/diff通过；前端正式构建通过。新增离线权限、未知输入、源码/危险名称、环境伪造/跨任务/旧修订/篡改、镜像变化执行前停止、子角色环境授权、完整hash/传递解析、JSON字面量及有界原生环境身份测试。测试wheel和Docker夹具不冒充实际安装；实际运行由上述独立容器证据支持。脚本在观察过期或阻塞后停止后续提交，对本试次已提交作业通过原有Jobs申请取消并记录终态确认；无法确认时整次仍失败，不盲目重跑。

English: Baseline1.7 adds scoped offline wheel preparation and immutable reuse through existing Jobs and permissions. The real core trial passes all15 checks; earlier NTFS/noexec failures remain preserved. Both final model UI trials perform real installation and one correct computation but remain failed for small-model tool arguments and large-model recursive inspection/context exhaustion. Separate read-only audits confirm exact first results, hashes and unchanged sources. 165 regressions, Ruff/diff and production frontend build pass. Agent acquisition/source builds and full local general acceptance remain incomplete; search stays user-deferred.

## Confirmed dependency facts and schema accounting / 依赖事实与Schema计费（1.8）

2026-10-07收尾，试次文件保留20261006运行标识。H1/H2根指令从现有产物/Jobs记录取得最近24个名称/ID和当前修订最近8个作业的有界身份、状态、环境与结果引用；不复制代码/环境清单/文件正文、不新增Docker探测。失败/超时诊断只复制已确认终态job.observe回执的stderr末尾512 UTF-8字节，保留source_action_id与截断标记，视为不可信正文。记录状态不等于新的观察或验收。H0不增加辅助事实，子角色不接收父产物/作业元数据。初始回合请求与objective完全一致时以全文objective和显式标志表示，不重复注入；不同后续请求仍完整保留。

上下文估算沿用原生正用量锚点；只有对应响应内容摘要、实测用量及同task/turn/revision/assignment的已覆盖请求清单一致，且当前工具Schema摘要相同，才避免再次计费已由锚点覆盖的Schema。变化的工具/响应、其他任务/回合/修订、缺用量及无响应摘要的旧记录均保留完整Schema余量。响应摘要仅排除框架后加的run_id/conversation_id/metadata/workspace_ref，不排除实际响应parts或provider用量。实际用量超过估算仍停止，不创建第二套历史。原生分层压缩目标从可用输入预算1/2改为3/4（32K/4K配置为21,504），ClearToolResults、滑窗、外置及硬上下文/输出上限继续保留；这是保留阈值调整，尚不证明完整长任务质量或小模型稳定性。

UI第4轮1通过/1失败，原报告 `ui-dependencies-tests-20261006-v4.json` 保留。small `6569a17a-0a68-42da-a276-2b655b76e8f2` 曾通过原流程检查，但独立读回发现成功环境被列在失败过程，真实environment.prepare动作ID被当成作业ID。所有UUID已登记仍不足以证明引用类型正确；补充报告/引用审计明确完整报告质量失败，不回写原流程JSON。large `66178daf-9d45-4ec9-a58d-23c78fbe7d10` 已实际完成两次计算及读回，查旧观察后上下文估算耗尽。第5轮两模型均因重复旧观察触发NO_PROGRESS而失败（small `3e4dc56c-3bd6-4f97-b9fe-f0c82d5c30a4`、large `3103b601-669b-4d9b-9d7b-0f27616503ff`）；Schema新计费实际生效，仍不将质量失败改为通过。

提示现明确resource_id/artifact_id/job_id/action_id不可混用，read_artifact没有limit；仅实际来源读取返回citation时才用citation_actions，代码/作业通常没有来源citation。第6轮1通过/1失败：small `6493f5cd-1305-4f4f-b985-529da5428b7b` 重复读锁/首次结果后NO_PROGRESS；large `538470d1-a04d-44f7-93a8-a870adafbc2a` 通过加强的完整UI及独立报告阅读。检查新增所有UUID须属于真实任务证据、作业标签只能对应真实job_id、五个实际作业均须引用；其余两次失败、唯一环境/两次复用、精确数值/版本、实际三份读回和源保护要求不删除。正则检查只覆盖标识，报告语义另外审查。

最终第7轮1通过/1失败，总3.9分钟，`ui-dependencies-tests-20261006-v7.json`。small `3923952f-416e-47c4-a1cc-bfb1b509fa38` 已产生正确命名的两个JSON，又重复计算/安装，最终10个作业（7成功/3失败）和30请求后由原调用上限暂停；报告错把artifact.read动作ID当缺包作业ID、将准备环境写成生成计算结果、遗漏重复执行，并声称只读CSV无法核验，报告也未实际读回。独立报告审计明确失败。不能因产物存在或数值正确宣称该任务通过。

large `979c4090-c284-4f7e-ba14-f7438e251d31` 15请求、16注册动作，保持revision1/waiting_user。实际缺包失败 `9dd5a10d-9a33-415a-a1dd-247fc80df355`、平台失败 `92cfd3fe-9d47-41f2-b859-af42686f4d59`、成功准备 `c0d32bc0-4c03-4b62-bebd-57cac149870e`、计算 `c072cfb9-7f09-4ffe-9e17-32e2a4aa1a33` 和 `3342c127-9b4b-49d2-8228-dcab28aa0e43` 均准确。复用同一 `7c70fdb1-e441-4d74-8f3b-c5581a506873` 环境/哈希/镜像，两个JSON均3/37.5/12.5、日期2026-10-06及三个固定版本；报告 `22aadec9-2bae-4bae-807e-4599f5682e8d` 的SHA、全部五个真实作业引用、顺序/状态/来源/环境/产品/局限另行核对一致。三份产物实际读回，页面刷新仍待确认，无页面错误。完整UI通过不是用户验收，也不证明科学精度。

独立只读审计第7轮全部45份私有prepared输入（small30、large15）：原文件SHA、实际SchemaSHA、严格同回合/修订/角色响应回执绑定、正用量且估算覆盖、初始请求全文一次、有界任务归属与512字节已确认诊断均通过；43份使用bound_measured_schema、2份初始输入全额Schema。证据 `ui-dependencies-context-audit-corrected-<task_id>.json`；首次审计错误以未转义原文搜索JSON字符串，导致canonical_request检查失败，原JSON保留，修正为JSON编码后的全文比对后另存审计，非产品失败。独立报告审计 `ui-dependencies-report-audit-v7-<task_id>.json` 保存完整正文与质量结论。输入证据通过不代表small质量通过。所有历次试次和截图保留；最终large截图已视觉检查，报告预览可读；来源选择器累积同名夹具使页面很长，完整UI成熟度仍需处理。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-131`：169 passed（34.36秒），最终Ruff/diff通过。新增原生三模式有界元数据/诊断、真实Schema收据隔离及父元数据不进入子角色的检查。此前恢复测试虽参数化H0/H1/H2却遗漏make_agent快照，实际均用H1；现传入冻结快照后三种真实模式通过，旧测试不夸称三模式覆盖。新增测试初期动作外键及夹具参数错误已修复，夹具不是实际执行器证据。产品前端源码未改动，无需重新打包；专用8768加载最终API，其他已有服务未重载。最终只读DB检查无active任务、非终态作业或未确认动作，未停止既有服务。

English: Baseline1.8 adds bounded root-only artifact/job facts, confirmed diagnostic tails and schema accounting bound to the actual measured native response and exact scope/schema. The native compaction target changes from half to three quarters of available input budget; hard limits remain. The strengthened final dependency UI trial is one pass/one failure: large passes actual flow and independent report review; small repeats execution/installation, exhausts30 requests and mislabels a read-action ID as a job. All45 prepared inputs pass separate provenance/usage audits, not task acceptance. Original failures remain, including an audit JSON-escaping mistake corrected in a separate file.169 regressions and Ruff/diff pass. Complete local general quality, dependency acquisition/source builds and recovery/UI gates remain pending; search is user-deferred.

## Native planning and UI / 原生计划与工作台（1.9）

2026-10-07 增加task.plan（19项注册能力中的一项）及原生write_plan/read_plan，只在H1/H2与任务/冻结角色交集允许时开放。复用Planning/PlanItem/SqlitePlanStore；native_plan_items表位于同一SQLite，按任务/修订/逻辑回合/委派隔离。16步/8192字节及字段上限，最多一步执行中。范围和控制检查跨原生整表提交持有单服务应用锁，未证明多进程同库控制。动作与预算沿用现有机制；自报计划不算新增执行证据或重置无进展。API/UI标记model_proposed/not_assessed，不改变验收或权威状态。没有另建Agent循环、历史或计划调度器，详见PLANNING。

初始协议测试2失败：已解析PlanItem对象不能直接写JSON动作参数。修复为记录call.args_as_dict()原始参数，仍由原生类型化handler执行，随后6项通过。再加入原生压缩及子角色计划写入隔离，共8项通过。PlanItem导出原生类型，工具/提交边界另外施加上限。

原始v8两模型UI均记录失败，完整文件`workspace/evidence/ui-dependencies-tests-20261007-v8.json`保留。small `2c91d9fd-a1da-4793-910e-ec3936e466bd`：22请求/22动作、7作业，仅result-1，NO_PROGRESS暂停，未完成第二结果/报告。large `bdfabe01-a90d-47be-8ee7-c23c969bd42b`：15请求/17动作、五真实作业、一个环境、两次计算及三份文件实际读回。其测试正则把“作业复用同一environment_artifact_id”的环境ID误作作业ID；改为显式作业标签，另核对仍能发现历史v7小模型误标读取动作ID。对同一任务的只读报告复核通过（13.5秒），0新模型请求/作业，补充证据`ui-dependencies-same-task-recheck-<task_id>.json`。原失败不覆盖，复核不是新试次或完整验收；其计划仍过期，仅两步完成。

v8的37份输入摘要、正用量覆盖、Schema绑定及有界事实审计通过；单独提醒审计发现小模型14次、大模型10次缺提醒。当前Planning在wrap阶段注入，而后续原生压缩器从ctx.messages重建请求，移除临时部分。仅调整能力列表顺序的首次修复仍失败，新增压缩测试发现此问题。最终兼容适配在压缩后的before-model链调用原生提醒方法，预算/快照随后执行，不积累历史。原失败审计`native-planning-request-audit-<v8 task_id>.json`保留。

UI加入计划页签和自报状态说明；当前任务只显示自身5个授权输入，不再显示全库80项历史登记。SSE重放原先逐事件并发刷新，产生请求堆积，API有7步而界面为空。修复为串行合并刷新、防止切换任务后旧响应覆盖；旧API计划路由404兼容。最终前端打包通过（2分6秒），真实同一任务桌面/390px手机的计划、来源、刷新、无页面错误/横向溢出检查1通过（1.9秒）。证据`ui-plan-recheck-tests-20261007-v8-r4.json`、`ui-planning-<task_id>.json`及两张实际查看的workbench-plan截图。

复核首次连接失败时，专用8768的进程与原会话均不存在，原因未确认，不称为已证明产品崩溃。确认缺失后重启，计划可读且没有重复执行。r2未先建立API会话/UI未加载，完整JSON备份因错误工作目录失败；摘要`ui-plan-recheck-summary-20261007-v8-r2.json`如实记载。r3报告复核通过/计划UI失败，完整JSON与错误上下文保留；r4修复后计划UI通过。加载最终后端前，重新核对专用服务全空闲和PID/配置/创建时间后才停止PID19656，启动PID26108；其他服务未重载。

最终v9依赖UI保留原数值/环境/五作业/报告/只读检查，并增加已核验工作结束后计划更新完成的要求。结果**2失败/1只读复核跳过**，并非仅新门槛导致：

- small `4d61efd4-b9be-4a71-a552-e71119f79edc`：25请求/25动作、8作业，正确环境和result-1存在；误读二进制环境、后续计算失败/计划回退，保守输入估算29112超过可用28672，CONTEXT_EXHAUSTED暂停。没有完整第二结果/报告。
- large `e17b7b75-69c1-4017-9a15-372bc3640606`：18请求/23动作、五作业，两个结果和中文报告生成且实际读回；之后重复读三份文件，NO_PROGRESS暂停。未final_result，最后两步计划未完成。报告另行查看不能代替UI成功。

最新43份实际已发请求的摘要、正用量覆盖、Schema响应绑定和有界事实审计通过；独立计划审计43次均有恰好一个与先前确认动作匹配的当前提醒，包含压缩后的请求。证据`ui-dependencies-context-audit-corrected-<v9 task_id>.json`和`native-planning-request-audit-<v9 task_id>.json`只证明输入/集成。失败JSON、事件、日志、截图和源只读结果保留，完整试次为`ui-dependencies-tests-20261007-v9.json`。没有提高30请求、无进展3次或上下文上限，没有联网搜索或宿主包执行。

最终完整回归`pytest -q -p no:cacheprovider --basetemp workspace/test-temp-20261007-139`：177 passed（25.13秒），Ruff通过。初始175项（37.05秒）之后新增上述2项检查。一次受限终端测试长时间无输出，单独中断该测试会话；提升后的8项正常完成，没有据此重启产品服务。完整通用阶段、两模型稳定质量、Agent依赖获取/源码构建、完整恢复/UI及领域/规模仍未验收；搜索用户暂缓。

English: Baseline1.9 reuses native planning, storage, actions and budgets. Actual v8 snapshots exposed reminders dropped by compaction; the small before-hook adapter fixes this without accumulating history. All43 sent v9 inputs pass reminder/provenance/usage audits, while both workflows fail: small misuses environments and reaches the context guard; large completes files but repeats reads and triggers NO_PROGRESS. Original failures, a report-reference false positive and a zero-execution same-task supplemental pass remain distinct. Coalesced refresh fixes empty-plan UI; desktop/mobile checks and177 regressions pass. Full local acceptance remains incomplete; search is deferred.

## Baseline 1.10 / 确认回执与请求去重

H1/H2根事实复制当前任务/修订/逻辑回合最近8个不同产物的最后确认读取窗口与来源动作，不复制正文。只接受匹配已登记产物SHA的确认回执；末尾窗口不证明前文已读、当前未篡改、内容正确或验收。准备成功作业的归档身份只取已确认job.observe，核对真实输出/哈希/名称/准备动作。H0及子角色不接收父级辅助事实。协议夹具覆盖有界数量、重复窗口、失败/异回合/异修订/错误哈希、未观察成功和伪造生产者；环境夹具不代表真实pip安装。

原生write_plan渲染回执没有步骤ID，v10小模型为了找回ID反复检查计划；现在回执追加原生解析并提交的step_ids，保留原始JSON动作日志，默认ID及重放由协议夹具验证。原生历史仍保留完整用户消息；仅出站H1/H2根请求中的逐字相同objective副本变为事实引用。完整目标逐字保存在权威事实，不改不同后续消息、工具正文或图像；纯请求替换保留原生缓存锚点，发生在压缩后、预算/快照前。H0仍保留原用户提示。数据库迁移2、公开Schema版本1、19项能力均不变。

v10保留**2失败/1只读复核跳过**，证据`ui-dependencies-tests-20261007-v10.json`。small `d50e984d-a83a-417e-8f74-e35a34f665fc`：13请求/13动作/4作业，准备正确环境并提交第一次成功计算，随后改计划、inspect_action、read_plan触发NO_PROGRESS，未观察计算/完成交付。large `8ac6c480-c911-4d53-80bc-7155a52dffdc`：16请求/19动作/5作业，两结果及报告实际生成/读回，下一输入估算28913超过可用28672，CONTEXT_EXHAUSTED暂停，未final_result。29份已发请求的标准输入/Schema/正用量、确认读取/环境身份、原生计划审计均通过；任务仍失败。

v11夹具补充：计划只列实际工具工作，不将final_result或后续用户验收列为待更新步骤。final_result终止回合，用户验收在后，因此要求完成后再改计划会形成循环。原五作业、两计算、三文件读回、报告真实引用/数值、源只读、最终waiting_user检查均保留；这是夹具语义澄清，不是应用新增“全计划完成才能验收”规则，也不覆盖v10失败。

v11结果仍为**2失败/1只读复核跳过**，完整证据`ui-dependencies-tests-20261007-v11.json`：

- small `63935d9f-bbfa-4724-84fd-99a50b049ab2`：22请求/22动作/6作业，正确环境准备两次，另有重复不兼容/无环境计算；未产生计算结果或报告。计划更新、旧失败观察、read_plan触发NO_PROGRESS。两个归档分别为`27a3b24e-8da9-4403-8f9f-18f7c3b544f7`与`4f1db583-d0a6-4c38-818d-536517dcdf17`，不称为一次环境复用通过。
- large `b012737f-a3ab-432d-a4d5-fa97785711b7`：16请求/19动作/5作业，环境`7972dd40-270c-444d-a3be-a03e60fa24fe`及两个JSON存在，真实读回后仍重复窗口，NO_PROGRESS暂停。未写报告/未final_result，不能把两次计算当完整UI通过。

38份已发请求的独立审计全部通过：`ui-dependencies-context-audit-corrected-<task_id>.json`核对摘要/正用量/Schema响应绑定；`confirmed-readback-facts-audit-<task_id>.json`核对确认窗口与环境回执；`native-planning-request-audit-<task_id>.json`核对当前提醒；`objective-plan-ids-audit-<task_id>.json`核对完整目标及无逐字出站副本、原生步骤ID回执。试次动作均少于清单64项动作引用上限，该独立读取窗口审计不宣称覆盖任意更长历史。末次已发输入估算/实测分别small26346/10074、large24911/9151；无提高预算或放松检查。输入审计与产品验收分别表示。

完整回归`pytest -q -p no:cacheprovider --basetemp workspace/test-temp-20261007-145`：**188 passed（43.12秒）**，Ruff通过，计划协议9项。新增11项检查相对177项基线覆盖确认元数据/角色隔离、原生默认ID/更新/重放、出站精确去重与持久历史。无UI源码改动，沿用已打包资源；未重复构建或据旧截图声明新流程成功。8768仅在确认空闲并核对PID/配置/创建时间后重载；v11终态后只读核对无活跃任务、非终态作业或未确认动作，其余服务未重载。

English: Baseline 1.10 adds sourced read windows/prepared archive IDs, native step-ID receipts and exact outgoing objective deduplication while retaining full facts/history and all limits. All 188 regressions and 38 sent v11 request audits pass. Both workflows still pause through NO_PROGRESS: small repeats preparation/failure observations without results; large computes and rereads both JSONs but produces no report. v10/v11 failures and the fixture clarification remain explicit. Full local acceptance, dependency acquisition/source builds and other original gates remain pending; search is user-deferred.

## Baseline 1.11 / 公开依赖获取与输出纠正

根协调者新增environment.acquire/acquire_dependencies，按精确包名/版本/wheel文件获取1–32项。官方PyPI元数据绑定未撤回文件、大小及SHA-256，可选官方文件源或TUNA字节源；官方同源哈希不冒充独立签名。逐请求复核精确URL、公开固定DNS/TLS、任务修订/控制、输入版本和审批撤销，不发送本地正文/凭证/环境代理。总wheel字节128 MiB、单份元数据2 MiB、整项180秒；下载不导入/解压/执行包。私密任务沿用精确审批，旧冻结角色权限不扩大。应用迁移仍2、公开schema_version仍1，能力上限增至20，导出WheelRequest。

下载wheel及官方元数据/锁文件封存为不可变产物；只有当前修订确认获取动作匹配的wheel_artifact_ids可进入prepare_environment。复用现有Jobs只读输入挂载与断网非root容器，pip实际检查平台/传递依赖。没有新增执行器或宿主机安装路径。长Windows产物路径改用native_path核查；重复相同字节下载不算新进展。模型仍需提供完整精确wheel集合，自动候选/依赖解析和源码构建未完成。根事实移除重复recent_artifact_ids，完整同组名称/ID仍在recent_artifacts；目标和预算未缩减。

真实工具试次fd1cd154-0d62-4a32-ab0a-7c002a44ffc2下载成功，但普通长路径is_file核查导致准备失败，无作业；失败证据保留。修复后513ad2f4-1b9d-44bb-989b-a686fa46fda1（task 1ea3dc8f-8519-4464-8d00-2b77f0b3aa89）11项检查全部通过：真实获取NumPy/dateutil/six、断网安装、同环境两次计算与确认读回、源只读、UID65534、网络阻断、宿主包不变及重放。3个作业成功终态且cleanup确认。脚本直接调用应用工具，model_used=false，不宣称模型自主完成整个流程；证据acquisition-real-<trial_id>.json。

审批下载组件UI v1为1通过/1失败：small 62c9aed6-58d6-4e79-8962-68630a20d4ed在审批前输出无效文本，错误钩子返回None被原生SDK当恢复输出，导致None.artifact_ids；large 570e82dd-7f4e-4122-9c55-1d91b4f6ff44通过。钩子现记录后重抛原错误，保持原生最多三次输出纠正，新增原生恢复/耗尽两项协议测试。v1原始结果保留，不覆盖失败。

修复后组件UI v2两项通过（总1.2分钟）：small f06e88d9-bb1f-4a5e-aeb0-da0e985c831c，27.3秒/9请求；large 58bb5bf3-734e-41c6-989b-1a3c49d15e7c，42.9秒/5请求。实际完成精确审批、真实six下载、大小/SHA核对、确认锁文件读回及waiting_user；页面交互/DOM和手机无横向溢出检查通过，截图不替代执行证据。14份已发输入摘要、正用量预算、完整目标/当前请求及实际下载来源独立只读审计全部通过。组件不包含模型安装/复用/报告，不能覆盖历史v11完整复合UI的两次失败。证据ui-acquisition-tests-20261007-v1/v2.json、ui-acquisition-<profile>-<task_id>.json、acquisition-input-provenance-<task_id>.json。

测试146受限进程首夹具停滞且数据库无任务/动作，核对精确子进程身份后仅停止该测试；147暴露夹具重复原生run ID，148暴露typed WheelRequest进度日志JSON序列化及重复事实超预算，均修复并保存失败。149为208 passed，150为210 passed，151两项输出纠正定向通过；最终152完整回归**212 passed（42.35秒）**，Ruff通过。前端首次受限构建EPERM，提升后沿用同锁构建/类型检查通过（1分30秒），打包index-BdRPCP-K.js。8768只在确认空闲、精确PID/配置/创建时间后重载，其他服务未重载；原30请求/32K上下文/4K输出/无进展3次/容器资源上限不变。完整阶段1及其后领域/规模仍未验收，搜索继续用户暂缓。

English: Baseline 1.11 acquires exact public wheels with existing scoped approval/network guards, immutable provenance and the same offline Jobs path. Automatic resolution and source builds remain pending. Eleven actual tool/install/reuse checks pass; both models pass the approval/download component UI, with all fourteen sent-input/provenance audits passing. The first long-path failure and small-model output failure remain preserved. Rethrowing the native output-error hook restores the existing three correction retries. All 212 regressions pass; full compound dependency/general acceptance and original later gates remain incomplete.

## Baseline 1.12 / 实际目标与成熟依赖解析

resolve_dependencies仍映射environment.acquire（能力20项不变），复用Jobs无输入断网探测、Network精确范围审批、Acquisition下载和离线pip安装。先探测实际Python/Linux/libc/ABI/镜像，目标产物须来自当前任务/修订确认的固定代码成功作业、没有私密挂载、实际镜像仍匹配；同目标可复用，子角色不开放探测/解析。新增可信packaging/resolvelib进入uv.lock，前者解释PEP508/版本/平台/markers，后者实现回退与冲突，不开发第二套算法/执行器。

只读取官方Simple/PEP658文件级元数据及SHA；不导入/解压/执行包。Linux兼容标签来自实际CPython3.12、架构及glibc，非Windows宿主。extras合并、条件依赖、显式预发布、版本冲突/回退有协议证据；默认排除预发布及撤回文件（包括空撤回原因）。所需直接URL/路径不开放；不适用的marker声明不会发请求。单索引8 MiB、单Core Metadata2 MiB、总32 MiB/64请求/32项目/180秒/100轮，失败不返回部分锁。缺少SHA文件级元数据、其他运行时及源码构建明确未适配。元数据闭包范围与精确wheel获取分别审批，不能混用ID。解析锁/快照/目标作业及镜像可追溯；可选resolution_lock_artifact_id绑定下载完整集合和SHA，变化拒绝。原精确wheel入口保持兼容。

真实工具试次d352153e-4f89-4fa5-920d-70698f3ec83d（task 35fcb963-ed62-495c-883d-d7b946ad26ab）**15项检查全部通过**。实际目标Python3.12.15/glibc2.41/x86_64，从numpy==2.2.6、python-dateutil==2.9.0.post0两个根需求自动补出six==1.17.0；6次元数据读取、两类审批、三个实际wheel下载、锁SHA匹配、断网安装、同环境两次计算/读回、源只读及宿主包不变。探测/安装/两次复用共4作业成功终态，cleanup全确认。证据acquisition-real-d352153e-4f89-4fa5-920d-70698f3ec83d.json；仍model_used=false，不代替模型自主验收。

独立8769服务/数据库运行新的真实模型UI完整解析任务，未重启旧服务。两类审批均实际点击并消费，范围DOM和手机无横向溢出检查通过；截图不能证明所有审批字段同时可见。v1 **2失败**，原ui-resolution-tests-20261007-v1.json、ui-resolution-<profile>-<task_id>.json及失败事件/截图保存：

- small 5bf42423-122c-4bd1-a4c0-96e00c48b533：1.7分钟、34请求/29动作（跨三个逻辑回合，单回合30请求上限未提高）；3作业成功。完成解析/获取/安装及首个计算JSON，混用两类审批、多次重读元数据/探测并再次解析，NO_PROGRESS暂停，无第二结果/报告。
- large 2f9443c4-5b00-4bbe-92f8-3ed0fc65efb6：2.0分钟、12请求/12动作；4作业成功。解析/获取/安装和同环境两次计算成功，未实际读回JSON/写报告，CONTEXT_EXHAUSTED暂停。不能把两次计算记作完整UI通过。

46份已发请求独立只读审计全部通过，resolution-context-audit-<task_id>.json核对私有输入SHA、完整目标/当前回合、输入授权范围、实际工具Schema摘要及正用量在保守估算/预算内。只有已发送的请求计入，超限未发请求不伪造测量。UI终态后当前数据库无非终态作业/未确认动作，失败没有被新试次覆盖。该审计不证明完整交付或科学精度。

153定向测试36通过/4失败，暴露默认预发布策略、两个文件名夹具不一致及撤销夹具嵌套事务；修正后154定向40通过。155完整229通过/1最大H2预算失败；合并重复说明，保留全部目标/事实字段/警示及预算。156/157说明字符串/预算失败分别保留；158完整230通过。159新边界233通过/1夹具URL后缺少PEP508必需空格；修正夹具后160最终完整**234 passed（39.46秒）**，Ruff通过。前端构建/类型检查通过（1分43秒），index-Bt8wl78K.js；UI服务加载后追加条件URL/撤回/子角色边界修正，原两试次根需求行为未改变，新增边界由最终回归验证。迁移2、公开Schema1、能力20及原模型/容器预算保持。阶段1及后续目标未完成，搜索暂缓。

English: Baseline 1.12 uses locked packaging/resolvelib for metadata-only resolution against a real no-input offline target probe, with separate metadata/download approvals and an acquisition-bound resolution lock. Fifteen actual tool/install/reuse checks and all 234 regressions pass. Both new full UI tasks fail: small mixes scopes and repeats completed work; large computes twice but exhausts context before readback/report. All 46 sent-input audits pass independently. Original failures remain intact. Source builds, broader runtime/metadata support, full general quality/recovery/roles/evaluation and later gates remain pending; search stays deferred.

## Baseline 1.13 / 有来源的回执与错误标识修正

Jobs.model_receipt只缩短模型提交回执中的environment_recipe和嵌套environment.manifest，保留job_id、动作/产物ID、状态、镜像、环境归档/锁/SHA及诊断。完整作业、API和Engine.perform确认日志不变；历史重放也应用同一投影，不修改原日志。H1/H2根事实最多提供当前修订各一份已确认解析与下载摘要（最近64动作中选取），核对本任务锁/wheel生产动作、名称及下载SHA，保留包/版本/文件名/ID，无正文/元数据URL。它是历史来源事实，不证明当前完整性、安装或任务验收；实际复用继续走原校验。H0及子角色不注入该摘要，没有第二套工作流/历史管理器。

审批模型事实：最近12项当前修订pending/approved继续给完整精确范围；终态仅保留kind/operation/value/destinations、request_sha256、scope_is_summary与消费动作。完整审批API、日志及授权校验不变，摘要不能授权I/O。范围冲突/终态错误更明确地解释不同请求不能复用审批及已确认回执的用途。联网搜索新增开发/验收继续暂缓，此项只调整共享来源与报错表示。

新增原生协议夹具覆盖非变异回执、最新/有界/跨修订及伪造来源过滤、审批全范围/终态摘要、实际H0/H1/H2及回合边界。161为240通过/1夹具失败：默认request_id去重使所谓外任务实为同一任务，改为真实唯一request_id后162为**241 passed（41.57秒）**。后续v2真实小模型暴露字符串"None"目标和把action_id当job_id；新增UUID或null参数Schema，通过原生有界纠正拒绝占位字符串，进度日志保存原JSON。不存在的job_id通过现有job.observe/attempt写入失败结果，模型可继续修正；其他权限/修订/控制错误仍停止，不新增执行器或自动重试。163最终完整**243 passed（41.99秒）**，Ruff及diff检查通过。没有应用前端更改，沿用已构建index-Bt8wl78K.js；公开Schema1、迁移2、能力20、模型32K/4K、单回合30请求及容器限制保持。

专用8769测试服务每次代码加载前核对精确PID/创建时间/目录/命令与数据库闲置，无执行中的作业、动作或委派；保留原数据库。v2在回执/来源摘要加载后运行、早于UUID/不存在作业修正：初始Playwright **2失败**，原始ui-resolution-tests-20261007-v2.json、每任务失败JSON/事件及唯一截图仍保存。

- small `f3ea2853-5093-462d-9e7e-4920f5fed236`：2请求/1失败动作，无作业和审批。目标参数字面字符串"None"导致查找失败，继而用action_id观察作业，NOT_FOUND暂停；此实际失败促成后续类型与回执修正。
- large `ab2ab3e3-30d1-470a-9fa9-68303fc07a30`：22请求/23动作，4作业均成功。完成目标探测、两类审批、完整解析/下载、一次安装、同环境两次计算、准确JSON及中文报告实际读回，waiting_user。初次浏览器检查使用Jobs API隐藏的code/mounts字段误报；改以execution-target.json的producer_action_id关联真实作业/code_sha256及目标内容核对，其他完整交付检查保留。同任务只读复核**1 passed/2 skipped（769毫秒，总2.4秒）**，额外模型请求/作业为零；原失败不改写，补充ui-resolution-large-<task_id>-recheck.json及ui-resolution-recheck-tests-20261007-v2.json。

大模型另做独立只读来源/报告审计：固定探测代码SHA、无输入挂载、四个exit_code=0终态、一次安装/同归档及实际镜像、报告真实作业/两审批ID、只读/断网/软件夹具精度局限、所有产物字节SHA均通过，resolution-provenance-audit-<task_id>.json。v2两模型24份实际输入SHA/顺序消息/工具配对、完整原始目标/当前回合、授权范围/只读策略/修订、工具SchemaSHA、正实测用量及原预算、依赖摘要来源逐项通过。此单次大模型流程通过不证明两模型稳定质量、通用阶段或科学精度。

最新v3加载UUID/错误作业ID修正后，新建两任务严格复验，**2 failed/1 skipped**（small1.3分钟、large3.7分钟）：

- small `39d73402-2760-48cc-8211-8483c082a36f`：8请求/8动作，1个成功探测作业。未执行批准的解析，编造不兼容wheel名称并混用解析/下载审批，重复后NO_PROGRESS暂停；解析审批仍approved，新下载审批pending，排队回合保留，未完成下载/安装/计算/报告。检查等待消费超时没有重启或取消任务。
- large `c2c4ff82-835d-4b7d-8074-a64277a70e7b`：7请求/5动作，1个成功探测作业。解析元数据实际返回RESOURCE_EXHAUSTED总时限；批准已消费，模型重用该ID被PERMISSION_DENIED拒绝，随后如实说明未完成并waiting_user，没有再提出新审批或下载/安装/计算/报告。浏览器等待下载审批超时不等于后台模型仍在执行；数据库两回合已completed，来源仅探测产物。

v3共15份已发输入完整审计通过；v2/v3合计39份，resolution-context-audit-<task_id>.json。只审计已发请求，不伪造未发送用量。只读审计脚本最初对所有waiting_user任务假定报告存在，遇v3不完整任务报KeyError；输入审计已保存正确，后续明确检查必需产物后才做完整来源审计，未据等待状态声明完成。v3原始ui-resolution-tests-20261007-v3.json、失败JSON/事件、独立唯一截图保存。未提高预算、删减完整目标或放松审批/输出/来源检查，阶段1仍未验收。

English: All 243 regressions pass. Compact model job and terminal approval receipts retain full API/journal data, actionable identities and sourced current-revision dependency facts; native UUID/null validation and journalled missing-job errors permit bounded correction. The v2 large workflow actually completes and passes a same-task zero-execution recheck plus independent provenance/report audit; its original API-hidden-field test failure remains. v2 small and both latest v3 workflows fail. All 39 v2/v3 sent-input audits pass independently. Stable two-model/general acceptance and remaining source-build/runtime/metadata/recovery/role/evaluation/domain/scale requirements stay pending; search remains user-deferred.

## Baseline 1.14 / 审批阶段与失败后的新范围

依赖待审批回执、H1/H2审批事实与原继续回合消息增加requested_tool，仅从已有请求kind/operation映射resolve_dependencies或acquire_dependencies，不复制第二份精确参数或执行历史。解析/下载回执说明批准后重调原工具，先完成解析才下载、先真实下载才准备/观察环境。完整范围/摘要和原权限/来源/控制/修订校验保留。消费审批不证明请求成功；失败的新尝试须不带旧approval_id重新提出审批，应用不会自动重试、复用终态授权或强制模型进入下一阶段。搜索新增开发/验收仍暂缓，共享机制的阶段表示不构成搜索稳定化。

新增两类阶段/消费失败协议夹具，实测确认批准失败后旧授权拒绝、新同摘要但不同approval_id再次批准成功；未批准时无新HTTP请求，也无伪造成功依赖事实。164受限测试停在首个应用夹具、数据库无任务/动作/作业/审批/回合；核对PID39756/创建时间/精确命令及空数据库后仅停止该测试子进程。165正常权限定向为55通过/2失败：夹具第二审批复用默认request_id触发冲突，改为独立请求并检查响应后，166完整**247 passed（43.56秒）**，Ruff/diff通过。测试缓存改用独立workspace目录，未修改受保护的旧缓存。故障夹具不证明真实外网或科学结果。

专用8769服务在精确PID/创建时间/目录/命令和数据库闲置复核后加载新代码。无应用前端/构建变化，仍用index-Bt8wl78K.js；公开Schema1、迁移2、能力20和模型/容器配置预算不变。v4原UI脚本**2 failed/1 skipped**，ui-resolution-tests-20261007-v4.json及唯一失败/手机截图保存：

- small `73df50bf-b95f-4915-aa67-7fae267dde6c`：51.4秒、9请求/8动作；1个成功探测。批准解析返回RESOURCE_EXHAUSTED读取总时限，模型去掉旧审批提出新解析范围；脚本只等待下载审批，未处理新解析审批。小模型随后重复待批请求，NO_PROGRESS暂停。不能把失败全归为阶段混用或脚本，也不能标为完成。
- large `a38db375-56eb-411f-aa7c-21bc66076867`：3.9分钟、13请求/10动作；2个成功作业（额外诊断及固定探测），不是完整目标的四个作业。元数据总时限与两次旧审批拒绝后提出新解析审批，waiting_user；原脚本同样漏掉新解析审批并超时。无解析锁/下载/环境/结果/报告。

v4共22份已发输入的SHA、顺序消息/工具配对、完整目标/当前回合、授权/修订/只读、工具SchemaSHA、正用量/预算及依赖事实来源审计全通过，resolution-context-audit-<task_id>.json。原失败不删除/改写。UI操作脚本改为每类一次初始审批/最多两次重试；仅在前次consumed_action_id对应确认失败、相同request_sha256范围时批准。两类各自最后动作仍须完成，之前只能失败；4成功作业/一次安装/同归档复用两次、真实JSON与报告读回、源不变/无搜索和全部结果检查保持。额外审批交互不等于增加配置预算，但可能改变逻辑回合数，独立记录，不能直接比较不同脚本试次成功率。

v5修正审批脚本后新建两模型任务，原Playwright **2 failed/1 skipped**，ui-resolution-tests-20261007-v5.json及失败JSON/唯一截图保留；脚本观察超时不等于任务终态，未重启/取消后台任务：

- small `a51ad546-c974-4b59-80d7-481402ac1983`：浏览器观察3.8分钟超时。1个成功探测，首解析元数据总时限失败，经第二个同范围新审批完成解析及锁文件读回；之后第9次模型请求没有完整响应。服务在原300秒墙钟期限确认MODEL_REQUEST_TIMED_OUT暂停，仍无下载/安装/计算/报告。9份输入身份/目标/范围检查均正确，8份有正实测用量且覆盖预算；第9份无响应测量，不能宣称全部用量审计通过或记为零。原输入审计all_passed=false及后续终态ui-resolution-v5-terminal-<task_id>.json保存。
- large `24a961bc-b4a8-4740-917b-b3fc87a81f79`：新建时小模型仍占共享模型槽，浏览器3分钟观察超时；小模型实际超时后该任务继续到4请求、1个成功固定探测及待解析审批，waiting_user。原失败是试次运行隔离/观察窗口不足的证据，不代表已经执行完整流程或模型本身失败。4份已发输入审计通过，终态另存。

新建试次入口已增加只读运行库闲置门槛，未因观察超时强行停止任务；该新建门槛尚未通过新的两模型独立试次核验。另增显式同任务继续模式，只能从未暂停/未取消的waiting_user进入原精确审批，不发送改写目标的消息、不改修订/配置；它不是零执行复核，模型请求/作业和墙钟全部另记。v5大模型继续r1因未选择历史任务，5.4秒UI检查失败且无审批/额外请求/作业；ui-resolution-continue-tests-20261007-v5-r1.json、原失败副本和截图保留。修正页面选中来源ID对应任务后，r2为**1 failed/3 skipped（3.4分钟）**，ui-resolution-continue-tests-20261007-v5-r2.json及唯一截图保存。

r2同一大模型任务累计13请求/10动作、2成功作业（固定探测及额外诊断），相对继续前新增9请求/1作业。两次解析审批消费后均元数据RESOURCE_EXHAUSTED；第三解析审批已approved但未执行，编译保守估算29035超过预留输出后28672而CONTEXT_EXHAUSTED暂停。没有成功解析锁/下载/安装/结果/报告，不把审批数量推断为完成阶段。13份已发输入独立SHA/目标/当前请求/权限/修订/Schema与正实测用量及保守预算审计通过，resolution-context-audit-<task_id>-continued-r2.json；超限未发请求没有测量。原4份输入审计和原v5终态保留，跟进终态ui-resolution-v5-continued-terminal-<task_id>.json另存。确认动作/产物复核纠正了跟进中“解析完成、下载待批”的错误阶段推断，正确状态是第三解析待执行。247项回归后只有测试控制/文档改变；最终Ruff/diff通过。两模型稳定质量和完整通用阶段仍未通过。

继续审计修正过期恢复：省略旧approval_id时，同摘要查询不能继续选择已过期pending/approved记录。现在在原事务内将其标为expired、记录approval.expired并提出新pending记录；旧显式pending ID过期也硬拒绝，不返回无法批准的旧待批回执。已过期/消费审批均不授权I/O，拒绝/撤销、来源/修订/控制边界保持，新请求仍需新用户批准。新增resolve/download×pending/approved四个日期故障夹具，旧ID无HTTP、新审批前无HTTP、独立新批准后成功及终态模型摘要均通过。167最终完整**251 passed（54.85秒）**，Ruff通过；此前247为过期修正前的历史计数。最终专用服务在精确身份/闲置复核后加载全部修复，/health返回application/database ready、domain not_implemented；健康响应不是阶段验收。此共享边界修正不添加搜索能力或真实搜索验收。

English: All 251 regressions pass. Dependency approval receipts/facts/continuations identify the original tool; failures and expired pending/approved scopes require fresh approval, and consumed grants do not prove success. Expired matching records are atomically retired on a new unapproved proposal; old IDs cannot authorize I/O. No automatic retry or budget increase is introduced. Original v4 failures include both model errors and a driver that ignored valid retry approvals. The bounded same-scope retry driver preserves delivery checks. In v5 small actually resolves successfully after reapproval, then hits the frozen 300-second model deadline; one sent request has unavailable usage. Large's initial observer fails while queued behind small. Its same-task continuation then times out twice in metadata and exhausts context before a third resolution attempt, without any successful lock/download/install/report. Original evidence and terminal follow-ups remain distinct. The idle precondition is not yet validated by a new two-model trial. Stable general acceptance stays incomplete; search is deferred and the full original scope remains pending.

## Baseline 1.15 / 原生跨轮次用量绑定

只读检查v5大模型失败轮次eae04a60-5c25-487c-8cb3-943f915e430a的原生检查点：最后有效响应与前轮次874d2bd5-b1b6-446b-94ce-15c285cf90df的清单4861aeee-e153-41ac-a6f2-d4d8f071f52f摘要完全一致，实测10018输入/908输出，估算覆盖。应用限定turn_id而遗漏已支付的6176字节工具Schema；原生压缩估算不含这份额外未知成本，下一请求被应用守卫拒绝。context-cross-turn-diagnosis-20261007-v6.json是只读诊断，未伪称重放或未发请求测量。

现在仅同task/spec_revision/assignment、精确response_sha256/tool_schema_sha256、正实测输入输出及覆盖通过时跨逻辑轮次复用用量。新清单记录schema_receipt_context_ref/schema_receipt_turn_id；未知/变化Schema、其他任务/修订/角色、变化响应、旧无摘要记录和未覆盖测量继续完整计费。动态目标/权限/当前请求、框架边距仍计算，冻结模型校验及原32K/4K预算不变，没有新增压缩器或历史管理器。

168上下文18项通过（6.40秒）；169完整252项通过（45.30秒）。随后增加H0/H1原生Agent历史跨轮次集成，170上下文20项通过（7.13秒）；171最终完整**254 passed（55.10秒）**，Ruff通过。预算夹具验证相同上限下未绑定超限/严格绑定后允许续接；隔离及覆盖失败仍拒绝。SDK夹具不替代真实模型质量。

受限环境最初看不到已知PID54120或8769，误判断服务已退出；正常权限启动因端口占用失败，当时库无在途任务/作业。随后正常权限只读核实仍是原PID/创建时间/目录/完整命令及ready健康，重新核验闲置后才停止该精确专用进程，再启动PID44668加载修复。未停止其他服务或因观察超时重启任务。应用前端未变，仍用index-Bt8wl78K.js；公开Schema1、迁移2、能力20不变，domain仍not_implemented。

v6串行新建任务并实际核验库闲置，Playwright **2 failed/2 skipped**，ui-resolution-tests-20261007-v6.json及唯一失败截图/上下文、审批手机截图保存：

- small `767f11c6-e859-43b9-8a9a-8e5abce58803`：2.3分钟、12请求/11动作（8confirmed/3failed）、1成功固定探测。两次解析消费审批均元数据RESOURCE_EXHAUSTED。第三解析已approved未执行；原回合反复wait_job观察同一成功探测，达到原无进展上限暂停，审批继续回合仍queued，UI等待消费60秒失败。仅有execution-target.json，没有锁/下载/安装/结果/报告。
- large `bfdbc187-cf1c-4034-a966-58e9513f88df`：3.4分钟、20请求/13动作、1成功固定探测，四个逻辑回合均completed，任务waiting_user。三次解析审批均消费且元数据RESOURCE_EXHAUSTED，提出第四pending；脚本保持每类最多两次重试要求而失败，未批准第四次。只有探测产物，没有完整依赖/报告交付。没有提高期限、重试次数或静默改变来源。

32份已发输入（small12/large20）独立审计全部通过SHA、消息顺序/工具配对、完整目标/当前回合、输入权限/只读/修订、Schema、正用量/原预算及依赖事实来源。大模型3次实际跨轮次schema_receipt逐项核验来源task/revision/assignment、响应/Schema SHA、输入输出及覆盖；保守输入16823/22800/20832，实测6856/11257/8584，未发生CONTEXT_EXHAUSTED。这是有限预算证据，不证明稳定工作质量。resolution-context-audit-<task_id>-v6.json与ui-resolution-v6-terminal-<task_id>.json分别保存，历史不覆盖，缺少必需产品时不宣称完整来源验收。新闲置门槛实际执行，未重现v5大模型排队导致初始观察超时。阶段1仍未验收。

English: All 254 regressions pass. Exact measured schema receipts can follow native history across turns within the same task/revision/assignment, with source context/turn provenance and full costs on missing or changed accounting. Frozen budgets and permissions remain. All 32 v6 input audits pass, including three real large-model cross-turn bindings; both workflows still fail on metadata deadlines/repeated observation or the bounded approval retry limit. Neither has a resolution lock or final products. Original failures remain. Full general acceptance and remaining scope are pending; network search stays deferred.

## Baseline 1.16 / 有界依赖索引压缩与累计计费

复现连续解析失败：官方NumPy Simple JSON索引请求强制identity，约20.937秒仅收到1095211字节（99块）就触发正文20秒限制。新增gzip仅由原simple_json元数据标志启用；stdlib gzip处理完整性、CRC、截断和多成员，压缩/解压分别限制单份大小。原40秒每请求/20秒正文/180秒整项、固定公开IP/TLS、精确审批及URL/来源/修订/控制保护保持。wheel及普通读取仍identity，未新增搜索能力、搜索测试或更换元数据来源。

HTTPResponse.read1可能返回碎片gzip魔数；标准gzip读取要求read(n)语义，因此有界输入使用read(n)，原绝对watchdog仍中断阻塞。172初版13项通过却过早拒绝慢流，补充碎片与期限最小耗时检查后173为14项通过（0.33秒）。174完整268项通过（48.80秒）；补充快照表示后175定向36项通过（2.10秒）、176完整268项通过（62.91秒）。随后补齐全解析实际wire累计和decoded累计双预算，新增跨索引总传输回归；177定向37项通过（2.48秒），178中间完整269项通过（45.22秒）。补齐重定向正文的传输/解压总量，以及实际HTTP跳数占用原64请求额度后，179定向106项通过（9.84秒），180最终完整**271 passed（54.71秒）**，Ruff通过。大小/损坏/编码/期限夹具不代替公开网络验收。

公开只读传输探测成功：gzip实际469154字节，解压2780148字节、4232文件记录、约1.985秒；索引未压缩失败与gzip结果同存dependency-index-transport-probe-20261007-v7.json。该探测无模型、无任务审批或包执行，不宣称完整Agent能力。

真实工具闭环三次均15项通过：初版trial `4c52addb-9d6b-4070-94e7-0c51045b7c88` / task `cae32ef0-b7ec-4323-82d1-41265c422b1c`；累计wire版trial `09d15f4b-7199-481f-a89d-07808d80da27` / task `b21208d2-8fec-4d0c-9379-46ada6d07a7b`。最终包含HTTP跳数/跳转正文计费的trial `a797a333-0817-44c4-9cc6-1a551c433f9c` / task `3f5878dd-af9e-4a41-9248-0d85cd178df7`也通过15项。均从两个根需求补six，精确分项审批、完整下载、断网安装一次、同环境计算两次，四作业成功终态，源不变/宿主包不变/网络隔离检查通过。最终六份元数据wire总555075、decoded总2904716字节，均小于32 MiB，实际计数为正，dependency-wire-accounting-20261007-v7.json保存中间版；最终dependency-wire-accounting-20261007-v7-final.json另存，6个实际HTTP请求/555075 wire/2904716 decoded均在原预算内。快照SHA/size明确对应decoded_http_body，并保存http_content_encoding/wire_size_bytes。隔离工具脚本不调用模型、任务保持created，不作为最终验收状态。

专用8769服务精确身份/闲置复核后加载gzip/解压快照代码；应用前端未变，仍用index-Bt8wl78K.js，公开Schema1/迁移2/能力20及模型/容器预算保持。v7新建两模型完整UI为**1 passed/1 failed/2 skipped（总4.5分钟）**，ui-resolution-tests-20261007-v7.json及原失败/唯一截图/上下文保存：

- small `9c12d752-e148-4eb4-9a6a-9dcc15f9427c`：UI约1.1分钟失败。最终17请求/15动作（12confirmed/3failed）、1成功固定探测，两类初始审批均消费且成功，实际产生解析锁/三个wheel/下载锁。模型却重提已成功解析和下载，脚本不批准重复成功阶段；随后反复待批请求NO_PROGRESS暂停，两个批准继续回合仍queued。未安装、计算或报告。原生UUID纠正拒绝一次字面"None"后实际完成探测，不把纠正反馈计为成功执行。
- large `fa5e4342-3dd0-46ec-b1d8-ea35aee66fe0`：约3.4分钟，20请求/22confirmed动作、4成功作业；两个审批消费并成功，3个逻辑回合completed，waiting_user。实际完成固定无输入探测、解析/下载、一次安装、同归档/镜像两次计算、准确两个JSON、中文报告及读回，全部原UI检查通过。独立来源审计确认固定探测SHA/无挂载、四exit_code=0、一次安装/同归档、实际作业/审批ID、源只读/断网/科学局限与全部产物字节SHA。resolution-provenance-audit-<task_id>-v7.json全部通过，未做额外模型执行来修补本试次。

小模型初版输入审计17份all_passed=false，仅因计数器忽略SDK的tool-bound RetryPromptPart，误将合法UUID验证反馈当作缺失工具响应；读取实际消息与已安装SDK语义确认了配对。只对有tool_name/tool_call_id的retry-prompt计为工具响应，普通输出反馈不能冒充配对。原resolution-context-audit-<small>-v7.json保留，补充-v7-native-retry.json另存且17份全部通过；大模型-v7.json的20份全部通过。合计37份SHA/顺序/工具配对、完整目标/当前回合、权限/只读/修订、Schema、正用量/原预算及来源检查通过，不证明小模型任务成功。ui-resolution-v7-terminal-<task_id>.json为只读跟进，不覆盖初始失败。

最终服务再次按精确进程身份和闲置库复核加载全部累计/跳数修复，健康返回application/database ready、domain not_implemented；健康不代表阶段验收。上述v7运行在最终wire累计字段补齐前的gzip/解压快照版本；最后累计/HTTP次数保护由271项最终回归和第三次真实工具15项验证，不把v7当作该保护后的另一次模型运行。单次大模型完整成功不证明两模型稳定质量或阶段1。源码构建、其他运行时/缺哈希元数据回退、完整真实恢复/角色/通用评估及原领域/规模/发布范围仍未完成，联网搜索继续暂缓。

English: Dependency indexes alone negotiate bounded gzip. Wire/decoded per-response and cumulative budgets, original deadlines and authorization remain. All 271 final regressions and 15 final direct-tool checks pass; redirects also consume the original byte/request budgets. The v7 UI completes for large with independently verified products/provenance, while small repeats successful stages and pauses. All 37 sent-input follow-up audits pass after recognizing native tool-bound validation retries; the initial audit false failure remains. v7 predates the final cumulative-wire accounting addition, separately verified by final regressions/direct tools. General acceptance and the full remaining scope are still pending; search stays deferred.

## Next / 下一步

脚本收尾后再次实际运行，试次 `26a3dc4a-0562-44a4-88db-0cb26d1b1eb7`、task `0b6e9e64-944c-44fe-936f-6c7a2904a3a3` 返回0；原15项检查再次全通过，六个作业终态均确认（预期失败3个、成功安装及复用3个），cleanup无未确认项。此成功路径不冒充观察超时或停止拒绝的实测。证据 `dependencies-real-26a3dc4a-0562-44a4-88db-0cb26d1b1eb7.json`。最终Ruff/diff及Python语法检查通过。

继续 P2 验证模型经静态/动态构建依赖检查完成原源码流程，补跨源码版本自动回退、多项目不兼容构建工具链、编译器/系统库、其他运行时/元数据回退和剩余真实恢复窗口；保持原预算、来源/权限/目标及单次审批，解决小模型重复操作/猜测/报告质量，再闭合角色、本地 UI 和通用评估。有限工具通过不等于阶段1或模型稳定质量，不增预算或删检查追求通过。Agent联网搜索暂缓；阶段1其余门槛前不开发领域功能。低资源目标由用户在pilot后决定。


## Baseline 1.17 / 授权本地源码构建与实际来源核验（2026-10-08）

prepare_environment新增可选source_refs，原wheel_refs/wheel_artifact_ids兼容；1–32输入、合计128 MiB，源码.tar.gz/.zip文件可使用中文/空格名称，作为JSON数据并按资源ID只读挂载，无shell插值。沿用原Jobs/角色/动作/安装/归档/执行链。源码需environment.prepare与code.execute；提交、启动、恢复、排队及撤销复核两项，角色保持原交集和输入范围。原生pip PEP517隔离在断网容器从已核验wheelhouse取得静态/动态构建依赖，不另写构建依赖管理器。仅源码作业增加1 GiB /build执行tmpfs；原/tmp noexec/非root/根只读/网络none/CPU/内存/时限不变。源码解包有界，拒绝路径越界、规范化重复、NUL、链接/特殊/加密及项目歧义；文件内容累计512 MiB/30,000项，tar含元数据的解压流累计640 MiB，CRC/截断使用标准库。最终安装仍完整SHA/依赖验证；构建阶段的--no-deps不进入最终安装。原提供wheel均安装并记录，生成wheel作为实际产物，锁记录来源源码、原wheels、built_wheels、pip版本、镜像/平台与安装版本；环境解析匹配实际生产作业和生成wheel产物。模型回执省略重复source_recipe，完整API/日志/锁保持。

182既有环境/作业20项通过（3.86秒）；183新增/角色检查42通过/1断言用语失败，184修正后43项通过（9.19秒）。185中间完整278通过/1失败（45.20秒），新增工具说明使原H2元数据夹具超过原输入预算；精简重复说明，不扩大预算/删除检查。187完整280通过（45.56秒），Ruff通过。真实长路径问题新增实际封存回归；186定向27项通过（7.17秒）。188增补中文/安全标点名称夹具时280通过/2失败（46.07秒），原因是夹具绕过动作创建而触发SQLite外键；修正为原Engine.perform后，189最终完整**282 passed（52.93秒）**。夹具不执行宿主包代码。

真实工具脚本scripts/smoke_source_build.py：初试trial5e036fd4-8233-40eb-9528-c01ad902a19b/task6c863ee1-4ffb-49ac-af16-31e79aba370a仅完成诊断，固定镜像Python3.12.15/pip25.0.1缺gcc而停；失败保留。第二试trial e7370705-0d19-4ed6-bd7c-d5806876ab21/task17300e2a-aeed-4852-92fc-ef826e04b8d6已完成缺依赖/归档故障、补依赖后的tar/zip构建，但Windows261字符生成wheel被原Path.is_file静默漏登记，环境解析正确拒绝。修复Jobs的长路径检查/遍历/封存，不缩短源名掩盖缺口。最终trial **ef142b26-df47-4fb2-bc14-3d3698363956**/task **e8ed4263-f3e0-4e80-9242-3736609a9878**共有20个终态作业，**14项全部通过**：12类归档故障在pip前正确失败、静态setuptools缺失和动态wheel缺失真实失败、经明确审批获取锁定的setuptools80.9.0/wheel0.45.1后完成tar/zip纯Python构建/完整安装、同一环境两次真正导入/计算/产物读回；源只读/宿主包不变/无网络/非root核验通过。原生C扩展实际因gcc缺失失败，条件未解决，不宣称原生编译成功或静默更换镜像。独立只读10项来源核验全部通过，包括全部产物字节、实际只读挂载/镜像/资源、bounded /build、0模型请求、精确配方和生成wheel来源。原失败与最终证据分别保存。

两模型真实工作台source v1：ui-source-build-tests-20261008-v1.json为**1 passed / 1 failed（2.8分钟）**。小模型task **c9b0c973-95cc-4bcf-9dc5-3804c7c34d94**共13模型请求/12动作，实际构建及两次计算均成功，数值42/版本1.0在日志中可见；但计算只打印，两个JSON后来由write_file产生，未按请求由计算作业保存；两个JSON未实际读回，报告还缺构建job_id。原UI首先在必需读回检查失败，独立来源复核另外揭示产物生产者/报告来源缺口；不把正确数值等同完整通过。大模型task **76657593-f565-40fc-90c1-ba760e945443**共8请求/11动作，实际1次构建/2次同环境计算、正确JSON由实际计算作业产生、锁/两个JSON/中文报告全部读回、报告含实际源/生成wheel SHA及3个真实job_id；完整UI及**13项独立来源检查全部通过**。两任务21份实际输入的完整目标/当前请求/只读授权/修订/Schema/工具响应配对/正实测用量/原32K预算与回执来源审计全部通过。来源审计不代替完整工作质量；小模型3个来源缺口保留。随后UI夹具补明确计算作业生产者检查，仍由以上独立13项复核覆盖；未新增第二次模型运行。

专用8769服务在精确PID/创建时间/cwd/cmd及闲置库复核后加载源码构建；UI使用最终源码构建/长路径/说明修复，末尾中文文件名接纳由最终回归覆盖（工作台试次是ASCII来源名，不冒充中文名真实运行）。没有新增库、迁移、能力、执行循环或UI构建，Agent联网搜索继续用户暂缓。单次大模型源码/依赖通过不代表两模型稳定质量或阶段1。公开sdist获取/自动回退、编译器/系统库镜像配方、其他运行时/缺哈希元数据回退、完整恢复/角色/通用评估及原领域/六组合/低资源/≥100 GB/发布范围继续保留。阶段1其余门槛通过前不启动领域开发。

English: Authorized local source archives reuse the existing offline Jobs and native pip PEP 517 isolation, with execution permission and immutable source/built-wheel provenance. Final 282 regressions, 14 real-tool checks and 10 independent tool-provenance checks pass; actual compiler absence remains unresolved. Source UI v1 passes for large with 13 independent provenance checks; small computes correct values but writes JSON outside the required Jobs, omits their readbacks and the build-job report reference. All 21 sent-input audits pass independently. Failures remain; general/two-model acceptance and the full remaining scope are pending. Search stays deferred.


## Baseline 1.18 / 精确公开源码获取与实际构建复用（2026-10-08）

acquire_dependencies新增source_packages，可只请求明确name/version/filename的.tar.gz/.zip；wheels仍只接受wheel。公共发行文件共享PackageRequest，服务端按各组严格核对类型，不重复建立下载器/审批/执行循环。每次合计1–32文件/128 MiB，保留官方元数据、大小/SHA、未撤回、公开固定IP/TLS、URL/来源/规范修订/控制/撤销/截止时间边界；宿主不解包、安装或执行包。源码请求有独立精确审批，wheel解析锁不能授权源码。不可变源码与获取锁记录actual producer及官方元数据SHA，source_artifact_ids进入原prepare_environment；当前任务/修订确认动作、真实文件名/大小/SHA、代码执行权限与角色产物交集在原提交/排队/启动/恢复边界核验，挂载仍只读。确认摘要与重复下载无进展指纹扩展至源码。CLI schema导出PackageRequest并保持WheelRequest；没有新增库、能力、迁移、执行器或前端构建。

190定向80项为79通过/1原H2预算失败，191定向63项为62通过/1预算失败；合并重复通用说明/引用说明与工具说明，不删权限/事实、不提高32K/4K预算。192定向70项通过（18.06秒）。193受限进程在首个TestClient夹具无输出不结束，核验精确PID/创建时间/cwd/cmd后终止此测试进程；不当作产品通过。194正常权限完整296项通过（47.58秒）。新增源码下载期间撤销/暂停/修订/输入变化四项覆盖后，195定向40项通过（8.82秒）；196完整300项通过（71.84秒）。CLI新增共享发行DTO导出实际运行成功，197最终代码完整**300 passed（51.58秒）**，Ruff/diff检查通过。夹具和宿主源码检查不执行公共包。

真实脚本scripts/smoke_public_source.py不调用模型。前两次trial `2bed699d-7020-4472-a830-901980473d70` / task `8c660980-2277-4670-a7bc-5b4039198474` 和trial `3799b339-a2d5-437a-a359-24a7b66834d8` / task `86bc0961-5282-4a9a-9841-f1cefdaae0a8`均完成实际构建/两次计算，但验证脚本分别取错source_recipe的artifact_id和built_wheels的name字段而汇总失败；两份原失败保留。按真实配方字段resource_id/filename修正后，最终trial **db5a7a01-b6e8-43c7-92d6-b62615575005** / task **f3feb240-fdf3-4d4d-a8f7-a899358d7eba**返回0，**12项工具检查全部通过**：

- 独立精确审批取得PyPA sampleproject4.0.0源码，大小5760字节、官方SHA `0ace7980f82c5815ede4cd7bf9f6693684cec2ae47b9b7ade9add533b8627c6b`，纯字节封存，不使用开发者本地源码夹具代替获取。
- 构建wheel缺setuptools的实际失败保留；原无输入目标探测确认实际容器，通过单独元数据审批解析setuptools80.9.0/wheel0.45.1/peppercorn0.6的完整wheel集合，再通过第三次精确下载审批取得完整匹配集合。
- 仅给构建wheel时最终安装真实因peppercorn缺失失败；补齐完整集合后沿用原断网PEP517构建/哈希依赖安装一次，生成wheel作为实际构建产物，锁记录acquired_artifact源码、静态/动态原生隔离、实际镜像和安装版本。
- 两个真实计算作业在同环境调用sample.simple.add_one(41)，实际得到answer42、sampleproject4.0.0/peppercorn0.6、UID65534及网络阻断；两个实际JSON由计算作业产生并读回，源码字节和宿主包不变。6作业均确认终态：2预期依赖失败、1目标探测、1构建安装及2计算。

最终证据public-source-real-<trial>.json保存实际日志、容器、动作、清单、检查和cleanup。独立只读public-source-provenance-audit-<task>-v3.json的**14项全部通过**，直接读取真实DB动作、全部产物字节及Docker容器身份/ExitCode/只读挂载/固定镜像/原6 GiB/4 CPU/非root/网络none/权限限制、三项审批和源码/生成wheel/计算产物生产者。初版-v2审计把无环境的code.execute目标探测也算成“同环境复用计算”，单项误报保留；补充只按实际environment复用字段计数，不重跑模型或作业。更早脚本安装版本字段误用的异常输出也保留。

通用提示原“文件产物使用write_file”与计算作业产物要求存在冲突；现明确文档write_file，任务要求的Job文件由代码写/output，禁止在容器外重造，并保持实际读回/字段/值类型/引用/摘要/语言要求。源码UI v2在加载上述最终服务代码后，保持v1全部检查并含实际计算生产者检查，ui-source-build-tests-20261008-v2.json仍为**1 passed / 1 failed（2.8分钟）**：

- small task **32a89482-b8a2-46c9-8844-31e2b4e57bb7**：13请求/12确认动作，1次成功构建和2次同环境真实计算。两个JSON由实际计算作业生成，准确42/1.0并实际读回，锁/报告也读回；相比v1产物方式/读回改善，但不能证明单一提示是因果来源。报告遗漏构建job_id，结果表将两个artifact UUID标为SHA；原UI在构建job_id检查失败，独立15项来源检查有2项报告失败。计算数值正确不能使报告通过。
- large task **b1b6f8b6-122b-4b71-bd02-e022b7b00ec8**：8请求/11确认动作，3真实成功作业、实际环境/生成wheel与两个JSON生产者、全部要求读回及中文报告/真实来源完整；桌面/手机UI完整通过，**15项独立来源核验全部通过**。单次通过不等于稳定质量。

两模型**21份实际发出输入审计全部通过**：字节SHA/消息顺序/原生工具返回或绑定RetryPromptPart配对、完整目标/当前请求、授权只读/修订、Schema、正实测用量/原预算和真实回执来源。只读审计同时支持源码摘要的实际获取动作/产物来源；此UI没有环境获取工具调用，不冒充模型源码下载验收。UI使用已授权本地软件夹具，agent_download=false。两模型来源审计文件分别为source-ui-provenance-audit-<task>-v3.json，输入审计为source-context-audit-<task>-v2.json；初始失败/截图不覆盖。

三项只读容器审计曾因自动审批服务的账户用量错误未执行，该失败不是unsafe认定；只读账户状态随后恢复ordinaryUsageAllowed=true，再通过原审批路径重试成功，没有绕过审批。public-source-current-state-20261008-v1.json保留中断及恢复关联。专用8769服务启动前核验旧PID/创建时间/cwd/cmd及所有任务/作业/动作/assignment空闲；当前服务加载实际1.18代码，健康application/database ready而domain仍not_implemented。运行健康不证明阶段通过。

精确公共sdist获取、工具修复及模型本地源码构建均不替代自动源码候选/构建及运行依赖回退、编译器/系统库支持、其他运行时/缺哈希元数据回退、完整恢复/角色/通用评估。阶段1出口仍未通过，后续真实ODM、PDAL/lidR、六组合重复/holdout、用户pilot选择低资源、≥100 GB规模和发布/安装/备份恢复继续保留，阶段1门槛前不启动领域功能。Agent联网搜索仍按用户要求暂缓。

English: Exact public sdists share existing acquisition approval/hash controls and offline source Jobs. The final sampleproject direct-tool trial passes 12 checks and 14 independent record/byte/container checks; 300 final regressions pass. Source UI v2 uses local authorized fixtures: large passes with 15 independent checks, while small now produces and reads correct computation JSON but still fails report provenance/hash labeling. All 21 sent-input audits pass separately. Initial smoke/audit bookkeeping failures and the temporary approval-service interruption remain with corrected follow-ups. Autonomous source fallback and the full remaining general/domain/evaluation/scale/release scope remain pending; search stays deferred.


## Baseline 1.19 / 源码只构建、实际元数据解析运行依赖及产物复用（2026-10-08）

prepare_environment增加可选build_only=true，在同一受限Jobs中以原生pip PEP517构建一次，跳过运行安装。生成wheel与实际Core Metadata作为独立不可变产物，source-build-lock.json记录来源/构建wheels/生成wheel及元数据大小/SHA、固定镜像、pip版本与runtime_installation=false。容器有界读取wheel成员；宿主不解包或执行包。Environments.built核对当前任务/修订确认的真实成功Jobs、精确生成代码、源码/配方、产物生产者与字节，返回prepared_wheels。明确写入source_build_only并在观察与环境解析中拒绝该用途提供后端伪造的environment.zip；故障夹具验证此边界。

resolve_dependencies接收built_wheel_artifact_ids，共享原Core Metadata验证与resolvelib，而非复制解析算法。实际wheel/元数据和无输入探测目标须同真实镜像；精确元数据审批绑定这些产物SHA，网络请求前和解析锁读回时复核。既有构建项目只能选传入产物，运行传递依赖仍按原Simple索引/Core Metadata解析。返回远程wheels与选中构建IDs分别供获取/安装，完整解析锁绑定两组；下载审批只授权远程子集，不替换已生成wheel。确认摘要/无进展指纹保持真实来源，新UUID不当作新进展。修复摘要验证的嵌套SQLite事务，移到短快照事务之后核验真实资源，不新增状态系统。

198定向70项69通过/1原预算失败；精简重复工具说明后199定向22项通过，200中间311项完整通过。加入运行闭包后201定向有夹具误用目标artifact对象及重复说明预算失败，202有嵌套SQLite失败；按实际ID/事务边界修复，203定向73项全通过，204中间312项完整通过。加入明确构建用途与伪环境拒绝后205定向34项通过；206最终完整 **313 passed（76.06秒）**，Ruff/diff通过。原模型输入/输出预算、夹具和检查保留，不通过提高预算追求通过。

`scripts/smoke_public_source.py --build-only`不调用模型。中间trial94c0ec4d-fcfa-4f75-a1d6-db2af067705b只证明产物复用，当时运行依赖还由脚本预列；后续trialf7cd83f0-9dd6-40a4-9b77-680f96a434f2已由实际元数据推导，但早于明确用途字段。最终trial **dbb88802-267c-4482-995a-43da84804b9f** / task **c1c747f2-bf32-45e2-ab94-591a8f1a9dab**在最后代码下返回0，**16项工具检查全部通过**：

- 官方sampleproject4.0.0 sdist大小5760/SHA保持；缺setuptools失败后，实际探测并只解析/下载构建根setuptools80.9.0与wheel0.45.1。
- 原生PEP517只成功构建一次，实际生成wheel及元数据读回。运行解析唯一根sampleproject==4.0.0，peppercorn由真实Requires-Dist自动补出，没有脚本预列运行根。
- 5项独立精确审批依次为源码下载、构建元数据、构建wheel下载、运行元数据、运行wheel下载；最终仅下载peppercorn远程wheel。运行缺依赖真实失败保留，成功安装复用同一构建wheel ID/SHA，不重跑源码后端，也不把构建工具装入最终环境。
- 7作业确认终态（2预期失败、1探测、1只构建、1安装、2计算）；实际两次sample.simple.add_one(41)=42，版本4.0.0/0.6、UID65534/网络阻断，两个计算JSON实际生产/读回，同环境/镜像、只读源和宿主包不变。

最终public-source-real-<trial>.json保存实际动作/日志/产物/容器。独立只读 **21项全部通过**，public-source-metadata-audit-<task>-v1.json直接核验DB生产者、所有产物字节/大小、5项精确审批/源码与元数据锁/失败安装及成功安装的同wheel、实际Docker身份/终态/只读挂载/原6 GiB/4 CPU/非root/断网、两个计算与读回。审计初次误查不存在的model_requests表而异常，输出保留；改查实际contexts表并零执行重审同一试次，没有重跑模型/作业。零上下文、model_used=false，不计作模型或科学验收。

专用8769服务在旧PID55384/create1791392320.671383/cwd/cmd及当前任务/作业/动作/assignment闲置核验后加载1.19，新的实际PID32744/create1791428983.9158776，健康application/database ready、domain not_implemented；历史暂停任务未自动继续。没有新增库、能力、迁移、执行器或产品UI构建。

本轮原source-build.spec.ts增加显式SOURCE_BUILD_ONLY_UI模式，保留原v2全部默认检查；新模式沿用同一已授权本地夹具，要求源码只构建一次、实际锁/元数据读回、只用生成wheel独立安装、两次真实调用产生JSON、完整中文报告和真实来源。没有产品UI更改或重新构建。ui-source-build-only-tests-20261008-v1.json为 **1 passed / 1 failed（3.4分钟）**，固定reporter输出先保存在ui-tests.json，核对两个实际新模式标题后原字节复制到独立文件（配置outputFile使环境变量覆盖未生效，不重跑模型）。

- small task **5d95ab0c-28dd-4727-8254-69ffbb781d3d**：21次实际请求，7真实作业。只构建/安装均成功，第一次真实调用并保存source-result-1.json正确；随后把版本当答案、int('1.0')失败，再硬编码“假设版本1.0对应42”而只打印、不生成第二个JSON，重复工作/读回后按原无进展上限paused。没有第二个所需JSON或报告，UI失败；独立18项有6项工作质量失败。源码/生成wheel/安装/隔离检查通过不覆盖这次失败。
- large task **64d9508c-5f9a-4dd4-88ad-5233500d47d0**：13次实际请求，4真实成功作业（1只构建、1独立安装、2真实函数调用）。wheel/元数据由实际构建产生，安装仅此wheel，两个JSON正确且由实际计算作业生产、所有锁/元数据/结果/报告读回，中文报告包含真实源与生成wheel SHA、环境及4个job_id；桌面/手机工作台完整通过。独立 **18项全部通过**，实际只读产物字节/DB来源/容器再次核对。

只读来源证据source-build-only-ui-provenance-<task>-v1.json分别记录失败与通过。两任务 **34份实际发出输入审计全部通过**（21+13），source-context-audit-<task>-build-only-v1.json核对实际字节/原生消息顺序与工具或绑定纠正响应、完整目标/当前请求/输入只读范围/修订/Schema、正实测用量/原预算与真实回执来源。该夹具无运行依赖、不调用网络，未涉及已确认built_wheels解析摘要，不冒充模型自动运行闭包或自主公开源码获取。历史1.18源码UI v2失败/通过及本轮小模型失败均保留。输入/来源通过不替代两模型稳定通用质量。

自动源码候选、静态/动态构建依赖发现与审批获取回退、编译器/系统库、其他运行时/缺哈希元数据回退、完整恢复/角色/通用评估仍待完成。阶段1出口未通过；真实ODM/PDAL/lidR、六组合重复/holdout、用户pilot选择低资源、真实≥100 GB及发布/干净安装/备份恢复完整范围不变。Agent联网搜索继续用户暂缓。

English: Build-only source Jobs produce immutable wheels and actual metadata for the existing resolver. Runtime dependencies are derived from generated Requires-Dist and separately approved remote wheels; missing-runtime repair reuses the same built wheel without executing the backend again. Explicit Job purpose prevents a forged installed-environment product. All 313 final regressions, 16 final public direct-tool checks and 21 independent record/byte/container checks pass. These are not model or scientific acceptance. The full remaining source/build/compiler/runtime/recovery/role/general/domain/evaluation/scale/release scope remains pending; search stays deferred.

## Baseline 1.20 / 自动公开源码候选、独立下载锁与实际 wheel 身份（2026-10-08）

既有 resolvelib provider 读取官方索引时纳入有 SHA/项目版本/Requires-Python/未撤回/官方路径检查的 .tar.gz/.zip。同版本兼容 wheel 优先；原生版本约束、extra、marker 与已有回退保持。选中源码时停止于索引，返回 source_candidates、SHA、原目标/需求的 python_source_candidate 锁及 resolution_complete=false；元数据审批不下载归档或执行源码。候选不是完整依赖闭包。acquire_dependencies 的原锁参数按用途区分候选源码和完整 wheel 集合，实际官方 SHA/目标/授权在请求边界复核，源码须新精确下载审批；wheel 锁仍不能授权源码。H1/H2 来源摘要把候选与解析/下载事实分别保留，重复同字节候选不被当作进展；历史事实不替代当前字节/版本/权限核验。

已确认获取的源码名称/版本与实际生成 wheel 身份绑定，使用 PEP440 等价版本比较；错误项目/版本硬拒绝。build_only 与普通源码安装均核验，授权本地归档路径仍兼容。原 source_build.py/生成 Worker 代码未变，1.19 生成产品的可信代码校验保持兼容。源码取得、只构建、实际元数据运行解析、最终安装和代码执行沿用原工具、Jobs、权限/角色输入交集、固定镜像、断网/非 root/只读输入及预算；无新库、能力、迁移、执行器、工具参数或产品 UI 构建。

验证过程保留：207/212 因驱动传错测试文件名而零测试，不能记作通过；208 原有解析/源码/上下文 71 项通过。209 新候选定向 50 通过/2 夹具失败，分别为审批 request_id 重用及 Windows 长路径写入；修正夹具后 210 定向 66 项通过。211 中间完整 328 项通过（57.32 秒）；补齐继续回合/候选来源提示后 213 定向 44 项通过。214 最终完整 **328 passed（59.50 秒）**，Ruff/diff 通过。新增 15 项候选测试覆盖原生选择/同版本 wheel 优先/传递 extra-marker/七类不合法源码拒绝、独立精确审批、SHA 漂移/锁篡改、无进展摘要及四种源码-wheel 身份结果。没有提高原预算或删除检查。

真实命令：`scripts/smoke_public_source.py --config config/app.wsl.example.yaml --source pypi --source-candidate`。试次 **6d7ccd70-0ba6-4835-a315-f958160d6c11**，任务 **446cb8a7-396d-4fc3-b554-76d116fd0179**。输入仅 docopt==0.6.2，不给源码文件名；实际官方索引没有 wheel，解析选出 docopt-0.6.2.tar.gz，25901 字节，SHA49b3a825280bd66b3aa83585ef59c4a8c82f2c8a522dbe754a8bc8d08c85c491。候选锁3e100413-1825-42cb-930a-b3ef585eb092/实际目标814b9f00-7364-43a3-b5bc-b77b0840fb90明确闭包未完成；取得源码产物2eb5c1ef-37ec-4d16-b48b-1cbe7be8432c的 SHA 与实际索引和官方发行一致。

五项独立单次消费审批分别为源码元数据、精确源码下载、构建工具元数据、构建 wheel 下载与实际运行元数据。六个真实 Jobs：目标探测a1e09b09-c7c7-4cf9-b463-85ab7adad963；缺构建依赖预期失败9fa2ec36-69ff-49ec-bd16-50a38f46c7b9；成功只构建95331a50-fe2d-410f-aafa-524340d11213；最终安装7f2147d8-176d-452d-801e-ed5e98dbb9b9；两次计算c14d97f4-1f34-4161-95d7-a797ffefc9f5/ba04a881-bcf6-41e7-8252-1fa33342743a。原日志包含 Getting requirements to build wheel、Preparing metadata (pyproject.toml)、Building wheel for docopt (pyproject.toml)，固定 Python3.12.15/pip25.0.1 实际使用原生 PEP517。脚本仍显式列 setuptools80.9.0/wheel0.45.1，不是构建依赖自动发现证据。

生成 docopt-0.6.2-py2.py3-none-any.whl（13783 字节，SHAd9b9a99dc7eb52fd38eac68a52b8d1a32e98157274e5d2c359accf2ab747bf5d），产物4322264a-be10-4bfb-9151-8a4b3fb2b240及实际元数据均有真实生产者。以原 docopt==0.6.2 需求和该 wheel 重新解析，实际元数据无运行依赖，元数据外部请求为0，完整 python_wheel_resolution 锁56798094-58fd-4932-b6c9-ec617d719ebd仅含 docopt，不提出空远程获取。最终安装只使用同一生成 wheel，没有再次源码构建，环境只含 docopt0.6.2。两次 docopt.docopt('Usage: example [--flag] <value>', argv=['--flag','hello']) 实际返回 --flag=true/<value>=hello，并产生及读回正确 JSON、UID65534/network_blocked=true。

证据 `workspace/evidence/public-source-real-6d7ccd70-0ba6-4835-a315-f958160d6c11.json` 的 **21 项工具检查全部通过**。独立只读 SQLite、实际产物字节/大小/SHA、原日志和实际容器检查的 **25 项全部通过**，文件 `public-source-metadata-audit-446cb8a7-396d-4fc3-b554-76d116fd0179-v2.json`。核对选源输入/实际索引/不完整候选锁、精确审批消费、源码和实际 wheel/元数据身份、最终完整锁、唯一源码成功构建、只用该 wheel 安装、两次真实结果/读回、只读挂载及原资源/断网/非 root。实际模型上下文为0：本轮不调用模型，不宣称 Agent 自主源码完整回退或科学验收；没有新增模型 UI 试次。1.19 的大模型通过、小模型第二次猜测答案/缺 JSON 和报告失败、34份输入审计及全部历史失败保留。

专用8769旧服务PID32744/create1791428983.9158776经精确 cwd/cmd与任务/Jobs/动作/assignment闲置核验后正常停止，加载1.20的新PID59320/create1791431602.3590336（exec会话39690），application/database ready、domain not_implemented；历史暂停任务未自动继续。证据 source-candidate-service-reload-20261008-v2.json/source-candidate-service-ready-20261008-v1.json。未知8768服务未操作。

静态/动态构建依赖自动发现、独立审批解析获取、跨源码构建/冲突后的自动回退、编译器/系统库、其他运行时/缺哈希 Core Metadata 回退、完整恢复/角色/通用评估仍待完成；选源回退不能代表所有源码后端和运行冲突均已解决。阶段1出口未通过；真实 ODM/PDAL/lidR、两模型×H0/H1/H2重复/holdout、用户pilot选择低资源、真实≥100 GB及发布/干净安装/备份恢复完整范围保留。Agent联网搜索继续用户暂缓。

English: The existing resolver now selects a public source candidate from original requirements and the actual index, with wheel preference at the same version. A typed incomplete candidate lock permits only the separately approved exact source download. Generated wheel identity must match acquired source. Existing offline building, actual metadata resolution and complete installation remain. All 328 final regressions, 21 real docopt driver checks and 25 independent provenance checks pass. The trial invokes no model; build requirements are still explicitly supplied, and complete automatic source repair and Stage 1 remain pending. Historical model failures and the full later scope remain; Agent search is deferred.

## Baseline 1.21 / 实际静态和动态构建依赖发现（2026-10-08）

源码准备新增 inspect_build="static"/"dynamic"，与 build_only 互斥，静态不提供 wheel。现行 build_sources 的同一归档检查/提取支持检查回调，不另建解包器或执行器。静态读取最多1 MiB TOML/标准 legacy 配方；动态在临时隔离 venv 用已核验 wheel 和 PyPA 公开 hook 发现附加需求，前端固定 pyproject-hooks1.2.0/packaging25.0，同样精确审批获取，不在宿主安装或执行。每组最多32个需求、合并32根/清单256 KiB，宿主只验证有界 JSON/PEP508，拒绝URL/私有来源。Jobs 持久 source_inspection 用途，结果 build_requirements 明确 runtime_installation=false；构建轮/环境解析均拒绝将检查产物当成已安装环境。实际源码/生产者/任务修订/代码/镜像/产物 SHA核验，原 code.execute、角色交集、排队/恢复/撤销和预算保持。完整明细/API/锁保留，模型投影省略重复 projects。只增加一个可选工具参数；无应用运行依赖、能力、迁移、执行器或产品前端构建。

历史 source_build_legacy.py 冻结1.17–1.20的 Worker/准备模板，仅允许旧 Job 精确字节校验，新作业不选它。旧导出模板字节也独立冻结，避免未来导出改动影响旧身份；220相关25项回归通过（5.91秒），只是相同历史常量独立保存，不改变现行Worker。旧实际6d7ccd70-0ba6-4835-a315-f958160d6c11构建记录的精确代码/SHA/产物字节3项只读核对通过，证据 source-inspection-legacy-real-20261008-v1.json；没有把任意旧代码当作可复用环境。

215定向47项通过；216完整338通过/1失败，新增工具参数/说明使原H2夹具输入保守上界28753超过28672。精简重复工具说明后217定向22项通过，218中间339项完整通过。补齐静态/动态两类撤销/最低权限、坏backend-path及回执投影核验后219最终完整 **346 passed（59.70秒）**，Ruff/diff通过；保留pytest缓存写入权限警告，测试没有跳过。原32K/4K和资源/请求预算未改变，原失败与夹具保留；曾读取git源码遇系统默认GBK解码失败，改显式UTF-8后保存精确旧模板，不执行包。

公开试次 **fb9a30be-2518-4b9e-9b07-4ea294388903**，任务 **997cdbef-ed29-4544-becf-f97d7596bc3a**，命令 `scripts/smoke_public_source.py --config config/app.wsl.example.yaml --source pypi --discover-build`。仅 docopt==0.6.2，经实际索引选源、独立下载；同一实际源码 static 检查得到 setuptools>=40.8.0，原解析器选 setuptools84.0.0，加经核验前端两 wheel，dynamic 标准 hook 实际返回空附加需求。5个独立精确审批、8个终态Jobs（7成功/1预期缺构建依赖失败），只构建一次、实际运行元数据重解析、只安装docopt、两次真实 docopt.docopt 与 JSON 读回成功。24项工具和30项独立只读DB/字节/实际容器检查全通过。工具证据 public-source-real-fb9a30be-2518-4b9e-9b07-4ea294388903.json，独立 public-source-metadata-audit-997cdbef-ed29-4544-becf-f97d7596bc3a-v3.json。

in-tree backend 试次 **9e269f50-7587-4c53-a302-2cba5c5d6972**，任务 **7f71ff89-e9e9-48af-baac-21c2d05425ca**，命令 `scripts/smoke_source_build.py --config config/app.wsl.example.yaml --source pypi --discover-build`。实际TOML static根setuptools==80.9.0，静态解析/下载前端及后端后，标准hook实际返回wheel==0.45.1；新完整构建需求再次独立元数据/下载审批，共4次。24个终态Jobs（9成功/15预期失败）：12类不合法归档在静态检查中拒绝、缺静态/动态依赖及实际缺gcc仍失败；tar/zip源码只构建成功，最终独立安装同生成wheel、环境仅forestry-build-fixture1.0，两次实际answer()=42并读回JSON。19项工具与20项独立真实记录/字节/容器检查全通过，文件 source-build-real-9e269f50-7587-4c53-a302-2cba5c5d6972.json/source-inspection-fixture-audit-7f71ff89-e9e9-48af-baac-21c2d05425ca-v1.json。归档、hook及安装全部只在原无网/非root/根只读/6GiB/4CPU容器，源/依赖只读；宿主只构造可信夹具字节，不解包或执行来源包。

两试次实际模型上下文均0，无新模型/UI试次，不能据此宣称模型自主完整源码回退或科学验收。1.19大模型通过、小模型第二次猜测答案/缺JSON和报告失败、34份输入审计及全部历史结果保留。当前公开docopt空动态结果与夹具非空动态结果分别提供证据，没有以空hook代替动态获取验证。

8769服务在旧PID59320/create1791431602.3590336精确cwd/cmd及任务/Jobs/动作/assignment闲置核验后正常停止；新PID588/create1791433150.6078172（exec42248）加载1.21，application/database ready、domain not_implemented，旧暂停任务未自动继续。证据 source-inspection-service-reload-20261008-v1.json/source-inspection-service-ready-20261008-v1.json；未知8768未操作。

当前构建需求发现已实现，模型驱动的完整流程仍待验收；跨未构建源码版本/运行冲突自动回退、不兼容多项目工具链、编译器/系统库、其他运行时/缺哈希元数据回退、完整恢复/角色/通用评估保持。阶段1未通过，真实ODM/PDAL/lidR、两模型×H0/H1/H2重复/holdout、用户pilot选择低资源、真实≥100 GB、发布/干净安装/备份还原全部保持。Agent联网搜索继续用户暂缓。

English: Existing offline Jobs now discover static and dynamic source build requirements through the public PyPA hook API. The same active archive validator and existing approval/resolution/acquisition/build/install paths are reused. All346 final regressions, real docopt24 driver/30 independent checks and the nonempty dynamic fixture19 driver/20 independent checks pass. Historical code/products remain exactly verifiable. No model/new UI trial is run; compiler, automatic cross-source fallback and complete general/domain/evaluation/scale/release acceptance remain pending. Agent search remains deferred.


## Baseline 1.22 / 实际模型源码UI失败定位与过度约束修正（2026-10-08）

1.22 增加真实两模型公开源码 UI 验证，并修正验证脚本把可选源码解析锁设成必选的过度约束。精确源码请求仍须来源于实际解析候选，wheel 请求须来源于实际解析结果；有锁时核验用途和字节，不能批准猜测文件名。构建需求可以按实际检查结果分批解析/获取，不要求模型一次提交所有根。动态检查在所有输入授权/字节核验后，确认 pyproject-hooks==1.2.0 和 packaging==25.0 的 wheel；缺少或版本不符时返回明确 PRECONDITION_MISSING，不提交作业，并提示先静态检查、解析/独立审批获取后继续。文件名检查仅为必要前置条件，真实 wheel 内容/后端依赖仍由原断网容器和 pip 核验。原生循环、权限、Jobs、代码/产物身份及预算不变，没有新执行器、能力、迁移、宿主安装或产品前端改动。

最终349项完整回归通过（60.57秒），相关42项通过（8.61秒），Ruff/diff通过。四个新H1真实模型UI试次均未完成：第一轮小模型被错误的强制锁测试约束挡住，大模型未补检查前端而反复动态检查；修正后的第二轮小模型请求未被解析器确认的wheel文件名而被审批检查挡住，大模型选源/下载/静态检查成功，但构建依赖审批后重复读取旧资料触发原3次无进展暂停。两轮各2项失败保留，未提高模型预算或重置暂停计数。四任务57份实际模型输入及扩展候选/下载字节来源核验通过；每任务14项独立完整性核验通过，不能代替流程质量。下一步核对审批后原生模型续执行及两模型工作保持，随后仍须跨源码版本/不兼容多项目工具链、编译器/系统库、其他运行时/元数据回退、恢复/角色/通用/领域/评估/规模/发布。阶段1未通过，Agent联网搜索继续用户暂缓。下文1.21及更早为历史。

原始UI命令：`RUN_PUBLIC_SOURCE_UI=1 FORESTRY_TEST_BASE_URL=http://127.0.0.1:8769 npm test -- public-source.spec.ts --workers=1`。真实模型按small/large串行，原32K/4K、30请求/回合、3次无进展及6GiB/4CPU预算保持。只读中文调用说明给docopt==0.6.2和两组argv（--flag/alpha及beta），不提供源码文件名或构建工具；任务要求真实选源、静态/动态发现、独立审批、只构建一次/运行安装一次、两次实际计算JSON读回及中文报告。审批由QA操作员在UI仅核对精确公开载荷，模型自行决定工具调用，不由脚本执行后端或代写答案。

第一轮（原始reporter ui-public-source-tests-20261008-v1.json）2失败：small任务b3cab817-8362-47e5-95e0-70c34ff07f75（23.3秒）正确取得真实候选，但精确源码下载省略可选锁，被我错误的强制锁测试挡住，不能归为单纯模型失败；之后原无进展保护暂停。候选源SHA49b3a825280bd66b3aa83585ef59c4a8c82f2c8a522dbe754a8bc8d08c85c491，pending下载审批未批准。large任务ce4f9a13-214c-46e9-864d-55467e5f9a21（约2.4分钟）选源/独立下载成功，提前dynamic无前端失败；static得到setuptools>=40.8.0，仍不补wheel再次dynamic失败并重复观察暂停。4Jobs为2成功/2失败；没有wheel/最终环境/结果报告。

第二轮（原始reporter ui-public-source-tests-20261008-v2.json，213.32秒）2失败：small任务4aad8154-6cf9-44ba-8f78-18d8b2dc46cf（19.2秒）只完成实际目标探测，直接请求未经解析确认的docopt-0.6.2-py2.py3-none-any.whl，被审批检查挡住，pending请求未批准，随后原无进展暂停。large任务392150de-cbf8-4ed4-8634-23b623a44ade（约3.2分钟）两次审批消费，源码选取/下载及实际static成功；第三个正确的构建根元数据请求获批后，模型未用该approval_id重新解析，而重复读源说明/目标/旧索引/静态清单触发NO_PROGRESS。实际最后两份输入已含当前approved状态、完整请求、requested_tool与ID，不能说审批信息丢失。原测试显示70秒未消费超时，但任务早已因连续3次无新增证据暂停，不能只通过延长等待变成成功。随后仅修正测试观察：原300秒模型截止对应320秒审批消费等待，终态暂停即时失败，避免误报等待超时；`--list`确认2项测试编译成功，此观察修正未再启动模型试次，第二轮原始失败未改写。

扩展独立输入核验分别11/18/10/18份，共57份全部通过：实际输入SHA/消息/工具协议（含schema重试）、当前目标/请求/源范围/修订、原预算/实际usage/跨回合schema来源，以及源码候选闭包false/实际锁生产者/SHA/项目版本/实际下载字节。文件source-context-audit-<task_id>public-source-v1-expanded.json / public-source-v2-expanded.json。另四份public-source-ui-independent-<task_id>-v2.json各14项完整性全通过，实际8Jobs（6成功/2失败）、18产物来源/字节、审批状态/精确消费、原生检查代码、实际容器限制、源版本无变化；workflow_all_passed=false。原v1审计保留。UI失败快照、审批决策和截图均按task_id保存，不能从完整性通过推导交付完成。

修复后的验证过程：221定向23失败/19通过（7.38秒），原因包括测试夹具重复传wheel_refs、重复request_id及工具说明使H2上界超9字节；修正夹具唯一ID/参数/导入顺序并缩短说明后222定向42项通过（8.61秒），223最终完整349项通过（60.57秒），Ruff/diff通过。pytest缓存写入权限警告保留，没有跳过/删除测试或提高预算。新增none/backend_only/wrong_frontend_version三例验证明确前置失败且无Job；既有输入权限/撤销/恢复回归保持。新的public-source.spec.ts只增加验证，不改变产品UI；此前1.21真实工具24/30和动态夹具19/20证据及旧模型成功/失败保持历史。

专用服务加载及当前证据见EXECUTOR-WSL与source-discovery-service-*.json。四个新任务当前均暂停，原pending/approved但未消费请求保留，没有盲目批准错误请求或自动恢复历史任务。下一步先针对已批准请求的模型续执行和不猜文件名验证一般工具指导/上下文保持，不能由新增强制工作流、重置计数或放大预算掩盖失败。阶段1出口、跨源码版本/多项目工具链/编译器及其余通用、ODM/PDAL/lidR、两模型六组合重复/holdout、用户pilot低资源、真实≥100 GB、发布安装备份恢复仍未完成。Agent联网搜索继续用户暂缓。

Version 1.22 adds a real two-model public-source UI trial and corrects an overdefined test gate that incorrectly required an optional source-candidate lock. Exact filenames still need actual resolver provenance; a supplied lock is checked by purpose and bytes. Build roots may be acquired in valid subsets derived from actual inspection. Dynamic inspection now returns actionable PRECONDITION_MISSING before Job submission when verified pyproject-hooks==1.2.0 or packaging==25.0 wheel inputs are missing or incompatible. Filename validation is only a necessary precondition; the existing offline container and pip still validate wheel contents and backend requirements. Native loop, permissions, Job/product identities and original budgets remain unchanged.

All349 final regressions pass (60.57 seconds), with42 related checks and clean Ruff/diff checks. Both rounds of real small/large H1 UI tests fail and remain recorded. Round1 small was blocked by the test's erroneous mandatory-lock gate; large repeatedly attempted dynamic inspection without prerequisites. Round2 small guessed an unconfirmed wheel filename and was blocked by the operator gate; large selected/downloaded source and inspected static requirements, then ignored an approved build-metadata continuation and repeatedly read existing evidence until the original three-observation no-progress stop. No workflow is accepted. All57 actual sent-input/provenance audits and14 independent integrity checks for each of four tasks pass, which does not establish task quality. Stage1 and the complete source/compiler/runtime/recovery/role/general/domain/evaluation/scale/release scope remain incomplete. Agent search remains deferred. The following1.21 and earlier records are historical.
