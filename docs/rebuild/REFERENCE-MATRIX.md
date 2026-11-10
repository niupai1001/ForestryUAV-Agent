# 源码对照（进行中）

## 20261026接续：流提交与工具历史的实际源码/测试审阅

固定DeepSeek commit22ddee3792402c3e70ec5f86a63c3e3e895a2f60，通过GitHub connector读取并保存assistant-stream.ts、agent.ts、tool-history.ts及三个测试文件；同目录MIT LICENSE保留。文件hash与精确已读范围见source-stream-history-20261026.json。递归tree获取Transport closed，改定向contents目录列表成功；不把获取失败当无测试。仅源码研究，不安装/执行外部仓库，不重新运行本地已通过合同。

| 参考实际链与已读测试 | 具体行为及本地落点 | 取舍、证据和未验证边界 |
|---|---|---|
| AssistantStreamAttempt.start/push/settle/abandon全类；agent.ts请求循环451～604行 | 一个attempt的一次快照同时供累积、组块与展示；settle先同步session.append取得seq，再emit committed end，提交失败abandon。取消有安全前缀则持久化interrupted消息，无前缀/非取消错误保留attempt；finish error/aborted不当成功，未启动不造assistant结算 | 本地ModelAdapter.observe只发布assistant.draft/thinking，Engine.run的Store.change(run.finished)才提交正文、turn终态和Job消费；失败保留中断/阻碍。model.request_finished只代表请求使用证据，不等于回答/科学成功。继续原PydanticAI流与原生检查点，不复制TS accumulator/turn状态，不新增第二提交日志；已有原生思考、请求锁/取消批次与硬崩溃证据复用 |
| contract-regressions.spec.ts的finish-error/aborted、step boundary publication、turn/start和step/start提交前拒绝、assembly期间cancel/dispose样本 | 错误finish无assistant/message；观察者只看已提交边界；提交前拒绝不造turn/step；取消/释放发生在请求组装时不漏LLM调用 | 已审阅上述函数/断言，不宣称完整64KB测试全部读完或在本机执行。外部Session含post-commit观察者隔离，本地UI从事务events读，不移植Cordis observer框架。跨进程/未知副作用复用本地独立合同；源码相似不能替代本地真实验证 |
| request-error.spec.ts全文件 | middleware错误不当provider重试；同turn/step有限恢复，不新建turn；取消优先于retry；恢复listener抛错不再试 | 本地provider与应用错误分层、累计活动预算及暂停语义保留；不引入任意listener无限返回retry。该测试不证明外部所有策略都有有限总预算，不据此放宽本地预算。没有触发新模型/重试试次 |
| ToolHistoryProjection.apply/snapshot全类与tool-history.spec.ts全文件 | addition从其headerSeq查当时定义，不用最新目录倒推；快照深冻结；同名schema变化重开series，撤除再恢复同定义延续；缺update的旧历史回退完整active定义；restore继承事件，不篡改旧快照；缺定义add拒绝 | 本地每个PreparedInput/SDK envelope保存完整当次schema及hash，不依靠相同名称推导旧schema；ToolSetSnapshot下一step生效、执行仍重新鉴权，持久激活只恢复仍合法项。A驱动复用原生ToolSearch及StepStore，不复制TS的增量工具声明/缓存series；本地已有发现/同批不可激活/撤权/真实wire合同复用。跨版本旧未决请求的schema改变兼容仍须单独核销，不能从可解析快照推断能重放 |

本批收口的是上表两条研究链及指定测试审阅，不是全部外部源码工作。Gajae完整Job/恢复测试、LazyCodex doctor实际链与测试、安装版Harness恢复/工具快照对应测试等仍开放；未发现必须为本批阅读而新增第二套运行机制的依据。

## 20261026接续：维护入口、压缩真实边界与恢复事实

本节为最新接续；下文“B合同未执行”等为当时记录，当前Driver裁决以DRIVER-DECISION为准，不重开原实验。新读文件/本机安装源hash见source-review-20261026.json；只声明以下函数/样本已审阅，不把归档视为完整仓库/全部测试审阅。

| 固定来源与本轮实际读取 | 可迁移的行为/发现 | 本地落点、已有证据与不移植理由 |
|---|---|---|
| Claw 8aab1d4e5410f9ab5e5e7f03c8a87de479e814ec compact.rs::compact_session/should_compact；compaction_does_not_split_tool_use_tool_result_pair；conversation.rs::interrupt_after_stream_synthesizes_results_for_pending_tools/stream_error_during_interrupt_is_reported_as_interruption | 压缩边界保留调用与结果；停止前未执行工具必须结清、不派发。但其配对样本沿用默认10000 token阈值，短样本未强制触发压缩；邻接断言不能证明任意批次安全 | 本机context.py::UsageAwareSlidingWindow保留原生策略，新增12调用/分散返回/Retry/顺序重用ID且明确裁剪断言；已有取消批次真实结果证据复用。无Rust代码移植，不采用相邻推断、字节/4计量或其续聊强制文案。compact.rs通过GitHub connector按完整commit归档，MIT与版权沿用同目录LICENSE；未运行Rust仓库测试 |
| 安装版Harness0.54.0 compaction/_shared.py::_is_safe_cutoff/find_safe_cutoff/find_token_cutoff、SlidingWindowCompaction.compact、公共reinject_pinned | cutoff附近只回看5消息；大批次结果分散时会漏调用。它向后查同ID时未以已结算边界截断，还会把旧已结算调用和新复用ID关联 | UsageAwareSlidingWindow顺序配对原历史，对候选确实拆分时补全安全tail。正常路径仍native，pin用公开接口，receipt三项辅助方法为继承兼容点，升级须定向验收；完整请求预算不抬高。compaction-batch原失败保留，compaction-pair-fixed补失败与受影响分支，不重跑校准 |
| 安装版Harness0.54.0 step_persistence/recovery.py::inspect_recovery、_capability.py StepPersistence的崩溃/快照契约 | latest、settled和未决effect只提供恢复事实，不授予重放权；completed effect也不能证明规范化结果已进入检查点 | 继续Store/动作账本与原生检查点分别负责事实/位置，未知窗口拒绝重放；复用上一批实际kill进程后的文件/Job恢复证据。没有新检查点数据库、第二日志权威；尚未读完所有恢复/工具快照测试 |
| DeepSeek 22ddee3792402c3e70ec5f86a63c3e3e895a2f60 pipeline.ts::dispatchToolBody/dispatch/complete/finish/notifyResult/serviceAsk/postExecute及信号融合/abort结果辅助函数 | 已启动promise排空后才结算取消；调用/结果对象快照、只读观察者不能改权威结果；无审批服务不应默认放行 | 本地工具取消排空与新Jobs tick/启动恢复/关闭排空统一保护拥有资源的寿命；没有Cordis流水线移植。外部将取消后的成功结果改为ABORTED，本地不能因此抹掉已提交副作用事实，保留真实结果与活动中断状态。外部observer异步失败只记warn不能替代本地必须持久化的Job投递。对应仓库测试仍须补读，不宣称整段研究收口 |

维护入口本地发现：backup原先只按端口判离线，现与create_app共用根目录.forestry-service.lock；tasks inspect不再构造Store写schema，使用mode=ro读取提交状态。原pilot_lock及launcher OS锁为实现参考，不引入租约调度。maintenance-owner-20261026证明不同端口、复制/恢复发布过程及WAL只读边界。旧版服务、任意独立Store脚本及跨主机存储不因这次修改自动受锁保护，发布必须核对旧writer归属。

## Driver B研究接续（2026-10-25）

DeepSeek固定22ddee3792402c3e70ec5f86a63c3e3e895a2f60的tools/src/executor.ts、pipeline.ts已通过GitHub connector归档到references/deepseek-harness。已读完整executor契约与pipeline前半的executionMode/createExecution/prepare/cancellationResult/dispatchToolBody/around-dispatch：冻结toolSet准入、输入快照、默认独占、调用/结果一一结算，取消已启动工作须排空。pipeline完整文件保存，但后半和对应测试尚未逐条审阅，不把归档当全部研究完成。

本地落点为research/driver_b.py显式步骤与批次循环，复用原ToolScheduler/perform/StepStore，不移植Cordis对象、WeakMap信号融合或TS服务框架。仅行为契约借鉴，无TS实质代码复制；参考目录已有该仓库MIT许可。

已核对本机PydanticAI 2.54.0的公共Model.request_stream、ModelRequestContext/Parameters、RunContext、FunctionToolset.get_tools/call_tool及返回ToolsetTool的参数校验；Harness 0.54.0的ContinuableSnapshot/SerializedStepStore可作为独立步骤的保存介质。B不调用Agent.run/iter，未依赖内部Agent图；仍需自管批次与协议边界，不能用静态装配证明行为完成。当前四合同因审批审核用量限制未执行，见DRIVER-DECISION.md。

下载源码仅作研究，不执行。失败下载与成功connector读取分开记录；不是全部参考研究完成。

| 已核对源码 | 观察到的机制 | 本地落点与不移植部分 |
|---|---|---|
| 本机PydanticAI 2.54.0 `_agent_graph.py::_streaming_handler` | handler在request_stream上下文内等待stream_done，流关闭后才返回；取消会清理wrap task | model_adapter.request_guard可持有共享槽至handler返回；已改请求锁，不另造流消费者 |
| Claw 8aab1d4e5410f9ab5e5e7f03c8a87de479e814ec `TurnControlSignal` | steer/take/finish_or_take_steer共享Mutex；空队列检查与closed提交原子；abort清空队列 | 迁移语义到SQLite事务，不能采用进程内VecDeque作持久权威；完整文件已存 references/claw-code/conversation.rs，含相关测试，尚待逐条审阅测试 |
| DeepSeek 22ddee3792402c3e70ec5f86a63c3e3e895a2f60 `ReactLoopInbox` | next-step/next-turn分层；splice去重校验跨两队列；先session.append后emit；claim一次领取step批次及最多一个turn | 扩展现有turn_requests及会话事实，参考边界和去重，不复制TypeScript投影框架；源码references/deepseek-harness/inbox.ts |
| Gajae 789b4b7 `SearchToolBm25Tool.execute` | 检索未激活集合，调用activateDiscoveredTools，给模型短回执，详情另存 | 扩展单份ToolSpec/ToolRegistry，下一step冻结；不移植TUI渲染和英文分词假设。完整SHA及索引/测试尚待核对；源码references/gajae-code/search-tool-bm25.ts |

未完成：Claw压缩配对与测试逐项核对，Gajae完整SHA/索引/Job协议/测试，DeepSeek工具执行/流提交/测试，LazyCodex工程组织链路与测试，本机Harness恢复与工具快照扩展点。尚无外部生产代码移植；现有锁改动依据本机原生handler契约。

获取方式：raw下载多次连接关闭/超时；改用GitHub connector后上述源码可读取并归档。不继续重复raw请求。Claw/DeepSeek LICENSE已留存；采用任何外部实现前继续核对对应许可及归属。

## 动态发现接续

Gajae完整SHA现已核实为789b4b74f1501a62df4a8fc18b1b1a07a10c9cfa，MIT LICENSE保存到references/gajae-code。已读取并归档tool-index.ts：学习单源短描述、名称/摘要/schema keys索引、恢复与允许集合求交，不移植其英文Unicode词分割、MCP服务分类或TUI；本地用标准BM25独立实现中文n-gram/领域同义词。search-tool-bm25.ts的短回执→激活机制由原prepare_tools扩展点接入，下一step生效；同批不可执行隐藏工具已有原生行为证据。外部测试逐条对照仍未完成，不能将本地测试等同外部项目测试全量移植。

已额外读取Gajae job-manager.ts中wait handle acknowledge、按generation抑制已消费投递、enqueueDelivery/失败重试及可见失败投影，保留观察/确认分离语义；本地继续SQLite权威，不复制其进程内JobManager。完整有限投递失败展示等接缝尚待补齐。

## 分词/模板来源

llama.cpp源码核对固定到6050b467c07a95c7b87debd4ddac2f8ba8d9b807，src/llama-vocab.cpp的QWEN35预分词规则（包含Unicode组合标记）用于本机模型词表。MIT许可与The ggml authors版权已保留到references/llama.cpp/LICENSE及运行包runtime/licenses/llama-cpp-MIT.txt，生产pattern处注明来源。GGUF metadata解析为本地有界实现，BPE使用已锁定tiktoken；模板取用户本机模型资产，通过已有Jinja2沙箱渲染，不复制额外模型或执行其代码。

Ollama官方docs/api.md已核对raw=true绕过模板、prompt_eval_count、options和keep_alive语义。两次真实计量分别验证raw文本与实际chat模板，明确输入/配置差异与总预算，未据此推导科学或模型能力结论。
