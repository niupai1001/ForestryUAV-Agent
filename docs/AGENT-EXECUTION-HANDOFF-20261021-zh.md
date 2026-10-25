# ForestryUAV-Agent 实现指导：参考 Claw Code 与 DeepSeek Harness 整理或重建运行核心

> 2026-10-22 接续说明：当前实现以 [新执行计划](AGENT-IMPLEMENTATION-PLAN-20261022-zh.md) 为准，本文保留源码参考和原始设计依据。新计划继承未完成项，并修正异常回合历史、结果投影、作业复用/重试及“取消/预算耗尽统一停止所有 Job”的作用范围；不要将下文旧表述作为另一条执行队列。

2026-10-21。本文替代旧版 E01～E08 任务卡，重点说明怎么实现。多 Agent 的沟通与文件归属另见 [共同约定](AGENT-WORKING-AGREEMENT-zh.md)，不在这里重复管理流程。本文是交给执行 Agent 的方案，尚未实施核心重建。

## 交给执行 Agent 的指令

> 读取项目 AGENTS.md、交付追踪和本文，然后实际开发。先检查其他 Agent 的最新修改，复用已完成部分。参考下面列出的 Claw Code 和 DeepSeek Harness 源码，把运行核心整理为可替换的组件；如果继续在原有核心上加补丁会延续耦合，可以重建核心，不必维持旧框架。先交付“发消息→原生思考/正文流→工具读取→停止→继续对话”，再接入已有科学 Jobs 和会话清理。保留旧会话与正确科学成果，不重跑原始航片、LUT 或反演。按实际可用流程汇报，修复后只验证受影响部分。不要再扩展一套阶段管理体系。

本项目地址：`C:\Users\yzh\WorkBuddy\Agent\forestry-uav-agent-v1`。当前范围仍为多光谱正射与 PROSAIL LAI；点云后续暂缓，联网搜索和领域峰值吞吐取消。

## 先确定重建范围：运行核心可以换，科学成果不需要重做

当前值得处理的是职责耦合，而不只是文件长度：`agent.py::make_agent()` 同时创建 HTTP/provider、统计请求、编译上下文、拼指令、注册大量工具和安装运行 hooks；同一个模块还负责原生流、取消、恢复和专业工具接入。增加一个工具或调整一个模型设置，容易碰到同一个大文件。

**建议采用“重新组织运行核心，复用现有服务实现”的路线。** 将模型请求、上下文、工具注册和轮次生命周期移出大闭包，用明确接口连接。现有 ODM/PROSAIL、Jobs、资源管理和 SQLite 可先作为接口的实现继续使用。

本轮阅读时，工作树已陆续出现 `budget.py`、`trials.py` 和请求 ID 等变更，尚不能据文件存在认定完成。执行者先核对它们，不能再新建一套同名职责，也不能按本文较早观察覆盖正在实施的修改。

具体选型按以下判断完成，不单独开展长期“架构研究”：

| 现场核对结果 | 实施选择 |
|---|---|
| Pydantic AI/Harness 能在公开接口下满足流、恢复、工具结果配对和上下文控制 | 将其封装为运行驱动，抽离应用耦合，不再在单个闭包里堆全部业务 |
| 满足需求必须持续依赖框架私有内部、重复持久化或相互覆盖 hooks | 重建驱动，以本文接口接入 provider 与旧服务；可以替换 Harness，不将保留框架设为硬约束 |
| 直接采用 DeepSeek Harness 比自行实现更合适 | 先验证 Qwen/Ollama、多模态、Windows 执行器、旧会话/Job 适配这几个实际接缝；满足后可以采用，不能仅凭插件理念直接迁移全部数据 |

判断只需在交接里写明现有调用点、无法满足的契约、选择理由。不要把“可以重建”解释为删除旧项目后从零再开发科学功能，也不要仅把大文件拆成几个仍共享全部可变状态的小文件。

## 参考代码怎样转成我们的实现

以下源码已实际阅读；链接指向可变分支，执行者采用时记录具体提交，不把两个项目当成同一套 API。Claw Code 是非 Anthropic 官方实现；DeepSeek Harness 是 DeepSeek 官方开源项目，其 README 仍标注开发者预览。

| 要实现的机制 | 应读的源码与定位 | 应用到本项目 |
|---|---|---|
| 接口与运行驱动分离 | DeepSeek [架构说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)、[Agent 运行接口](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/src/runtime-types.ts)、[agent-loop 实现](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/index.ts) | 外部组件依赖接口；主循环可替换，插件不反向导入具体循环 |
| provider 输出归一化 | Claw [openai_compat.rs](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/api/src/providers/openai_compat.rs)，`StreamState::ingest_chunk` | 将实际返回的思考、正文、工具参数分片映射为各自事件，而不是混成文本 |
| 流式展示与最终落盘一致 | DeepSeek [assistant-stream.ts](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/assistant-stream.ts)，`AssistantStreamAttempt.push/settle/abandon` | 一个请求一个累积器；最终持久提交成功才发布完成，失败不能伪装完成 |
| 中断只作用于当前活动 | Claw [conversation.rs](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/conversation.rs)，`TurnInterruptSignal`、`TurnControlSignal.finish_or_take_steer` | 轮次取消与消息队列分开；结束与接收下一条消息的交界要原子处理 |
| 上下文裁剪保留工具配对 | Claw [compact.rs](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/compact.rs)，`compact_session` 的保留边界 | 保留完整调用/结果组；不能把孤立 tool result 发给模型。其长度除 4 粗估不照搬 |
| 工具注册与调度分离 | DeepSeek [tools/src/index.ts](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts)、[tool-calls.ts](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/tool-calls.ts)，`executeToolCalls/runGroup` | 统一参数校验和执行入口；并行有界，独占调用形成屏障，取消后停止派发并结清已启动调用 |
| 会话状态与持久化一致 | Claw [session.rs](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/session.rs)，`push_message/save_to_path` | 消息持久化失败不能留作已完成内存状态；本地使用现有 SQLite 事务，不必改为 JSONL |

DeepSeek 的“Everything is a Plugin”通过服务接口、类型化事件和组件生命周期实现。对本项目首先采用这些边界；不需要为每个函数建立插件包，也不必先做插件市场、热卸载或配置继承系统。[官方架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)

### 目标结构和接口

下列名称是**本项目建议接口**，不是声称第三方已经提供的 Python API。优先适配已有结构；每个接口只由一处定义，启动入口负责装配实现。

```text
FastAPI / UI
    │ send / stop / resume / events
    ▼
AgentRuntime（只管理轮次，驱动一次“模型→工具→模型”）
    ├─ ModelAdapter       模型配置、请求、原生流转换
    ├─ ContextBuilder     历史选取、指令/工具组装、输入预算
    ├─ ToolRegistry       工具定义、参数校验、执行和结果投影
    ├─ JobExecutor        长作业提交、查询、停止、恢复
    └─ SessionRepository  消息/事件/轮次提交与读取
         ▲
    原 SQLite / Jobs / Resources 作为适配实现

Imagery、PROSAIL、文件/图像读取等能力只向 ToolRegistry 注册，
不依赖 AgentRuntime 的具体实现。
```

接口最小形状如下；协议中的 Request/Chunk/Call/Result 等采用现有类型或薄适配，不同时维护两套完整消息模型。

```python
class ModelAdapter(Protocol):
    def stream(self, request: ModelRequest, run: RunScope) -> AsyncIterator[ModelChunk]: ...

class ContextBuilder(Protocol):
    async def build(self, session_id: str, turn_id: str,
                    tools: list[ToolSchema], run: RunScope) -> PreparedInput: ...

class ToolRegistry(Protocol):
    def schemas(self, scope: RunScope) -> list[ToolSchema]: ...
    async def invoke(self, call: ToolCall, scope: RunScope) -> ToolResult: ...

class JobExecutor(Protocol):
    async def submit(self, spec: JobSpec, scope: RunScope) -> JobRef: ...
    async def inspect(self, ref: JobRef) -> JobState: ...
    async def cancel(self, ref: JobRef) -> JobState: ...

class SessionRepository(Protocol):
    async def read(self, session_id: str) -> SessionView: ...
    async def commit(self, session_id: str, expected_version: int,
                     change: SessionChange) -> CommitReceipt: ...
```

`RunScope` 只携带明确身份、冻结配置、取消信号、预算句柄和所需服务引用；不要让它变成可以任意访问全部内核状态的万能对象。数据库事务/表名留在存储实现；HTTP 路由只调用 runtime，不能再直接修改模型循环内部。

组件在应用启动时按依赖装配，例如文件能力拿 Resources/Repository，影像能力拿 JobExecutor。组件安装函数注册工具并返回清理句柄；启动失败撤回自己的注册，关闭时先停止所属活动再释放连接。第一版采用静态装配即可。

接手时首先抽出模型适配与工具注册，保留薄的原 `AgentEngine` 入口兼容 API；如果当前驱动无法满足下面的语义，就在同一接口后重建驱动。避免新旧驱动同时写同一会话，也不同时发送两次真实模型请求进行“影子比较”。

## 把四个实际问题落实到代码

### 一、原生流、卡住和停止后不能续聊

**当前接缝。** 模型 HTTP 客户端实际上在 `agent.py::make_agent()`，`client.py::LocalClient` 是调用本地工作台 API 的客户端，不能把 provider 修复主要放在那里。现在 `model_events()` 已处理 ThinkingPart/TextPart，前端也已有 NativeThinking；缺口应沿链路定位，不再造第二个思考组件。

**具体实现：**

1. 从 `make_agent()` 提取 provider 创建、响应头 hook 和流转换到 ModelAdapter。冻结模型、context/output 限制和 thinking 配置后再请求，实际发送与记录使用同一快照。
2. 每次模型调用单独创建请求状态。将传输回调绑定 request ID；若用 ContextVar，作用域必须覆盖整个异步流消费，不能在只返回迭代器时就释放。避免共享 `stage_holder["current"]` 被下一次请求覆盖。
3. 区分连接/响应头、原始字节、有效思考/正文/工具事件。SDK 启动事件和重放事件不能计为模型首片段。无响应头只能说明未观测到响应头，不能直接断言模型正在加载。
4. 参考 Claw 的分型转换，在 SDK 已正确提供原生事件时直接使用；只补缺失的 provider 适配，不额外手写整套 SSE 解析器。保持 `assistant.thinking.delta`、`assistant.draft` 等现有事件兼容。
5. 借鉴 DeepSeek 的请求累积器：同一个块累积结果同时用于实时显示与最终消息。正常结束保存完整正文；取消保存已收到的可见前缀及 interrupted 标记。只有持久化成功才发布已完成。
6. 以 `session/turn/request/part` 区分内容，前端按已有 event ID/sequence 去重。新轮次不接收旧轮次的迟到片段；重连读取旧事件，不重新请求模型。UI 接收与服务器耗时分别记录。
7. 当前轮次取消使用新的轮次级信号，不能永久污染会话的发送能力。事务中同时处理轮次结束和下一条已入队消息，避免结束时刚收到的消息丢失；用户继续聊天无需先手工“恢复”整个会话。

核心流程应容易读成下面这样；有 Harness 时由适配器映射到其运行接口，不在外面再套一个重复工具循环：

```text
领取本轮用户消息 → 编译本轮输入 → 预占请求预算
→ 请求并累积原生内容块
→ 提交本次模型消息/中断记录
→ 若有完整工具调用：交给工具执行器，记录结果，再编译下一步
→ 否则结束本轮，接收下一条消息
```

取消不是伪造工具成功：未启动调用可以记录“取消前未执行”；已启动调用要等待执行器结清或记录“停止待确认”。不把流结束等同于外部计算结束。

**做到什么算解决：** 同一会话能看到实际原生流，点击停止后发送下一条，刷新后内容一致。只有框架事件却没有模型数据时能指出缺口。保留 300 秒诊断基线；关闭思考、换短问题或无限延长超时不能证明原卡住问题已解决。

### 二、上传图像/文档就顶满上下文

**当前接缝。** `context.py::input_bound()` 以 UTF-8 字节上界计工具和新指令，历史锚点在部分压缩场景仍累计旧成本；它是保守估计，不是模型实耗。`vision.py` 已有批图索引和 1024 预览；不能认定这部分完全没有实现。

**具体实现：**

1. ContextBuilder 输出 `PreparedInput`：实际消息、工具 schema、模型设置及一份计量结果；计量至少区分 text/vision/tool/framing、方法和 exact/estimated/unknown。不能用序列化快照的字节总数代替模型 token。
2. 优先取得匹配当前模型模板的本地 tokenizer/图像处理参数；没有可核查组件时仍标明估计，不从别的模型照搬公式。现有 provider usage 用于同配置校准，不是永久“支付过所以免费”的凭证：每轮窗口取决于本轮实际发送内容。
3. 一个历史 usage 锚点只有在模型、模板、指令、工具与保留前缀身份一致时才可用于增量估计。历史被裁剪后不能无限叠加旧基线和全部新消息；重建本轮估计，保留误差说明。也不能随意减去未测量的历史成本。
4. 顺序为“选择引用/读取范围→组装当前请求→估计→必要时压缩→重新估计→发送”。沿用 Harness 的工具结果落盘和压缩功能，只修计量与边界，除非已选择更换驱动。
5. Claw 的压缩边界保护提供思路，但本项目应按 call ID 验证整个保留片段：多工具并行时，不能只看相邻两条。未完成调用不删；超长已完成结果换成带路径和读取方法的引用；原始历史仍保存在会话。
6. 上传只登记 attachment ID、所属 message、源身份和必要元数据。模型要读时才取正文/图像。大批图片先 `list_inputs`，再 `view_image`；后续请求只有仍需要的图片内容入窗。不能“每轮删所有图片”导致后续视觉问题失去依据。
7. 文本读取返回 offset/next/truncated；实际读盘也必须限量。Claw [file_ops.rs 的 read_file](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/file_ops.rs) 先全文读入再截取，本项目只借用范围输出形式，不照搬其 I/O 实现。
8. 普通图像使用预览或明确 ROI，原图保留；科学 TIFF 走栅格元数据/局部读取和计算工具。裁剪结果同时返回原始尺寸、裁剪范围和缩放关系。新增 crop 参数先检查现有工具是否已有等价能力。
9. 若工具 schema 确实是主要开销，使用稳定能力目录按需加入具体定义，定义只来自同一注册表。模型可自主加载专业能力；不让用户选择“通用/专业”。安全约束和预算始终生效。

**做到什么算解决：** task11 的原目标不会在视觉 token 为 0 时被错判为图片过大；Agent 能按需读现有成果。批量附件保存在消息处但不会全部重复内联，已读取内容可追溯。Claw 的固定 10K 阈值和长度除 4 估算都不作为当前 Qwen 的实现参数。

### 三、工具路由与专业能力扩展

**参考机制。** DeepSeek 的工具注册表把模型可见定义、执行函数和结果投影分开；调度器处理独占/并行与中断，而具体工具只实现业务。[工具接口说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/tools.md) [调度源码](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/tool-calls.ts)

**具体实现：**

1. 把 `make_agent()` 里文件、图像、记忆、影像等工具闭包迁移为能力注册函数，例如 `register_file_tools(registry, resources)`、`register_imagery_tools(registry, jobs)`。每个工具保留稳定名称、参数模型、执行函数、输出模型和模型可见结果渲染。
2. 一份定义派生 schema 与路由，不能再手工维护三份工具列表。注册时检查重名/依赖缺失；schema 只含模型需要的 name/description/parameters，数据库、执行函数、权限配置、内部结果元数据不进入请求。
3. 沿用现有 `perform/guarded_tool` 中已验证的准入、动作记录和预算逻辑，移到唯一的调用通道：校验参数→检查权限/取消/预算→登记动作→执行→校验结果→保存→生成有界模型内容。先查近期 `budget.py` 是否已实现对应部分，不另造重复守卫。
4. 工具返回事实数据与错误类型，Agent 决定怎样回答。大结果保存产物引用和必要统计，不把全部栅格/日志塞回模型。错误分为输入可纠正、环境不可用、运行失败和取消；不能将所有异常转成继续重试。
5. 工具默认串行；只读且确认并发安全的工具才加入有界并行池。执行可以并发，持久状态提交和结果与 call ID 的对应必须稳定。修改文件、提交计算或更新共享状态的工具按独占执行。
6. 参考 DeepSeek 中止后不继续补充新调用、结清已开始调用的机制。不要用取消协程就假定底层容器已经停了；实际停止由 JobExecutor 确认。
7. 对 `process_imagery` 保留业务实现，改为注册一个工具适配器。接入新能力应仅添加一个注册组件和所需执行实现，不需要改主循环或全局 UI 分支。
8. 现有 `wait_job()` 已用应用轮询，不消耗模型请求，先复用。若恢复缺口要求延后返回工具结果，采用当前安装 Pydantic 支持的 deferred tool 接口，以同一 tool_call_id 接回；不能在循环里反复让模型问“结束了吗”。[官方 Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)

以“检查已有影像”为例，组件只需要做以下事情：

```text
输入：本会话授权的 artifact/resource ID
执行：解析实际路径 → 读取 CRS/尺寸/波段/已有统计
完整结果：保存结构化产物
给模型：返回关键元数据 + 产物引用 + 可继续读取范围
给 UI：沿用通用工具进度和附件呈现
```

这条路径不重新拼接影像，也不触发反演。“新增功能”应成为注册组件，不应再往大闭包增加一套独立生命周期。

### 四、会话持久化、执行恢复与删除

**当前接缝。** `Store.transaction/event/replay` 已有事务、事件和请求幂等；`Jobs.tick()` 已有取消及终态结清；`Sessions.delete/recover` 已有删除标记和可恢复清理。需要补的是一致性和遗漏路径，不能宣布它们全无然后重写一遍。

**具体实现：**

1. SessionRepository 封装原 Store；一次轮次/消息状态变更和其持久事件在同一事务内提交，提交后再通知 UI。DeepSeek 的 `settle()` 与 Claw 的 `push_message()` 都可作为对照。原生步骤快照和应用事件分别是什么、谁负责恢复要写清，不能两套数据各自声称为最终状态。
2. 普通观察和故障日志不修改业务库；移除 `sqlite_probe` 的建表/写入/删表行为，按 request ID 保留错误。真正的数据库写入串行边界由存储实现负责，而不是各工具临时加锁。
3. 消息、请求、调用、Job 的身份串起来。恢复先查询原 Job 的真实状态和产物，成功则接回结果，未完成则继续观察；不能因进程重启重新 submit 同一副作用。
4. 取消/预算耗尽统一调用 JobExecutor 的停止入口，并阻止排队任务启动。区分取消已请求、已退出和停止待确认；最终完成条件包含关联动作和子任务的结清。
5. 删除复用 `session_deletions` 标记：先冻结会话→结清活动→按所有权清理→提交删除完成。覆盖晚到回调、重复删除和清理中崩溃；外部源目录只移除引用，共享文件按引用判断。
6. 新核心通过 Repository 读取旧会话，不让两个核心同时接管一个会话。旧 Harness 检查点不能无损迁移时，旧运行先由旧驱动结清；新消息从兼容读取的已提交历史继续。不能伪称可以跨框架直接加载内部检查点。

上述设计不要求全量事件溯源重建数据库。先保留现有表和 ID，用适配层收敛所有权；确需 schema 变化时采用可兼容的增量迁移。删除清理和保存失败行为必须可重试，不得把用户源文件当测试夹具。

## 如何开始实现，而不是再写一轮计划

从当前代码中提取 **ModelAdapter + 一种文件读取工具**，接上原 Repository 和 UI，跑通“对话→读取文件→回答→停止后再聊”。这是检查接口是否真正可用的最小完整流程，不是新建空壳插件框架。

随后把 ContextBuilder 接到同一路径解决真实上下文失败，再接已有 JobExecutor/影像工具。每迁出一个职责，移除对应旧分支或保留明确单向兼容转发，不能长期新旧实现同时生效。新核心可以用应用配置选择，不增加用户每轮必须选择的运行模式。

第一条流程不必等待完整历史研究报告重建完毕。历史评分纠正可以并行做；真实模型运行前只要求当前试次的身份、预算和资源占用可追溯。相同失败在无修复或环境变化前不重跑。

最后以已有真实正射/LUT/LAI/Cab/残差成果完成一次自主读取与报告修订。允许轻量只读元数据 Job，禁止借验证重算正确科学产品；Cab 上界只能报告有依据的现象和待核查原因。报告评分需修正 task03 单位 oracle、task05 合成标签、task09 故障归因及 task12 语义裁决，但这些文档问题不变成独立开发关卡。

**交付标准只保留三个：**

- 用户流程可用：原生流、文件/批图读取、停止续聊、已有科学报告、恢复/删除按受影响范围完成。
- 架构确实可扩展：新增能力只注册组件，替换模型/执行器不改主循环；不能只是把同一大对象拆到多个文件。
- 证据可复用：定向验证触发明确，原失败保留，正确计算不重跑。实现、验证、部署分别如实标记。

多 Agent 只按组件分配文件；共享 `agent.py` 和运行服务由同一集成负责人协调。已有正确改动和未提交内容必须保留，本文不授权覆盖他人工作。旧 E 编号如已用于交接可保留追溯，但不再要求按八张卡串行推进。
