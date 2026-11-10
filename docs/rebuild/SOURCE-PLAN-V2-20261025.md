# ForestryUAV-Agent：对照开源 Agent 实现的系统性重构计划（V2）

**日期**：2026-10-25  
**当前项目**：[niupai1001/ForestryUAV-Agent](https://github.com/niupai1001/ForestryUAV-Agent)，`master@3eb534011ece8a6df5d0ddc9ae19a075a536c7b6`  
**对照仓库**：Claw Code `8aab1d4e`、Gajae-Code `789b4b7`、LazyCodex `c5e8181`、DeepSeek Harness 默认分支（2026-10-25）；兼顾现有 PydanticAI / pydantic-ai-harness。  
**文件性质**：研究及开发方案，并非已修改程序、复现测试或验证生产运行。测试数量与真实实验结论均来自用户仓库记录，须由下一轮独立核对。

## 0. 本版与上一版最重要的变化

上一版倾向于“保留已有框架，只局部借鉴外部实现”。这一原则过于保守，不利于解决运行核心的设计问题。本版明确允许三种措施：

1. **直接复用成熟库/API或合规代码**：当现有 Python 库已有等价机制，优先使用并编写适配器；MIT 等许可证允许时，也可提取有限的成熟算法/测试逻辑并按要求保留版权和许可说明。
2. **移植经过验证的机制**：外部使用 Rust、TypeScript 时，可以研究其数据模型、调用时序、状态协议、错误恢复和测试，移植到 Python，而非仅抄写函数名称。以等价测试验证行为，不保留无意义的技术栈结构。
3. **替换现有核心模块，必要时重构 Agent Driver**：如果实证显示当前 PydanticAI Harness 和自建外层的组合无法以可维护方式满足需求，允许替换该段运行核心；但是必须给出替换的前后行为契约、迁移计划、回滚路径与 A/B 数据，不能因为“新框架更先进”而整体重写。

**不预先承诺保留一切，也不预先承诺推翻一切。以行为、复杂度和成本决定实现。**

## 1. 项目现在真正的症结

### 1.1 已由当前源码和交接记录直接支持的事实

- `agent.py::Engine.run()` 在整个回合外层持有 `self.model_slot = asyncio.Semaphore(1)`，而不是只占用模型生成区间。跨会话存在无谓排队风险。
- `ToolRegistry` 已经统一注册工具，但 `prepare_tools()` 主要通过输入、权限、特性开关筛选；没有已经验证的语义检索→模型主动发现→下一次请求激活工具的完整闭环。
- `context.py` / `runtime/prepared.py` 已有较完整的上下文清单、实测凭证及保守分项估算，但 `byte_bound()` 是 UTF-8 字节数，不能直接等同真实模型 token 数。已有核心测试涉及上下文超限和恢复失败。
- `progress.py` / `runtime/evidence.py` 已把无进展检测转为结构化事实指纹，比旧版本更正确；但 `stalled >= limit` 仍把无新增观察与无效探索联系得过紧。
- `runtime/job_tools.py::wait_job()` 默认等45秒、最长120秒；持久 Jobs 已有，但 job 状态、用户输入、完成通知的串行接回尚缺已验证的统一契约。
- `frontend/src/App.tsx` 中 Jobs 入口受 `showDiagnostics` 控制，后台作业仍不够像正常产品的一部分。
- `imagery_catalog.py` 已通过输入身份、镜像和输出 SHA 复用科学成果；未提供科学参数声明时的候选复用条件，需要严谨区分“输入匹配”与“本次方法要求匹配”。
- 开发交接记录：1.61 对应全量测试 530 项，525 项通过、5 项已知失败；不能把“老失败”当成无关紧要。大模型曾自主完成现有 LAI 成果的三轮对话复用和交付；这不等于新数据自主科学计算已经全面验收。

### 1.2 从事实推导出的六个根因假设

| 编号 | 症结 | 表层现象 | 应验证的深层原因 | 不能直接下的结论 |
|---|---|---|---|---|
| R1 | **Agent 的执行生命周期与资源生命周期混淆** | 多会话互相等待、长任务阻塞 | 模型锁、会话锁、工具锁、Job 锁粒度不合理 | 不能仅凭代码断言实际吞吐下降多少 |
| R2 | **工具注册有了，能力发现未完成** | 描述膨胀、工具选择慢或漏选 | 可用能力清单与模型实际可见工具集没有分层 | 不能认为所有模型都需要复杂向量检索 |
| R3 | **上下文系统兼有计量与强制组织职责** | 估算超限、恢复脆弱、证据易碎片化 | 多层清理/窗口裁剪与原生历史/动态指令相互作用 | 不能把字节估算当成真实消耗 |
| R4 | **保护性控制抢占部分决策权** | 无进展停止、硬编码输出重试 | “规则守护事实”扩展成“替模型规定过程” | 不能以移除所有安全保护为解法 |
| R5 | **状态、事件和运行中的 Agent 没有统一接回语义** | Job 已存在却交流不顺、恢复复杂 | 回合队列、工具结果、Job 终态、用户纠正不是同一顺序契约 | 不能因为用了 SQLite 就认定已实现事件驱动 |
| R6 | **科学工具与科学判断尚未成为完整能力闭环** | 计算能成功，解释仍有错误 | 方法选择、适用性、证据、复用和精度声明未统一 | 不能把 Cab 边界效应直接归因模型架构失败 |

这些根因要求“架构重新划分职责”，而非给每个失败分别增加一次 Prompt 特例。

## 2. 开源研究：分别学习什么、可实现到何种程度

### 2.1 Claw Code：研究会话控制和工具消息完整性

- 仓库：[ultraworkers/claw-code](https://github.com/ultraworkers/claw-code)，本轮查看 `main@8aab1d4e`。
- **成熟度说明**：README 明确说 Claw Code 是 agent-managed exhibit，不是推荐的正式生产项目；因此用它研究具体的控制语义和测试即可，不以仓库规模或“Claw”名称作为技术背书。
- [`conversation.rs::TurnControlSignal`](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/conversation.rs#L120-L216)：`steer()`、`finish_or_take_steer()`、`abort()` 在一个共享状态内协调新指令、结束和中止。**迁移目标**：同会话的新消息只能在确定的 step 边界被认领；完成当前回合与收到新消息不能竞态丢失。
- [`ConversationRuntime::run_turn`](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/conversation.rs#L573-L845)：显式循环、tool_use 与 tool_result 配对，停止/拒绝仍生成相应结果。**迁移目标**：断流、取消、失败不产生孤儿工具调用，重复恢复不会重做副作用。
- [`compact_session`](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/compact.rs#L96-L193) 和对应[配对回归测试](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/compact.rs#L767-L835)：不能在工具调用/工具结果中间裁剪。**迁移目标**：按真实 call_id 和 step 身份保留边界，不照搬“按相邻消息推断”的具体实现。
- [`Session`](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/session.rs#L230-L350)：保存与加载时的失败回退。项目已有 SQLite，无须改用 JSONL。

### 2.2 Gajae-Code：重点研究动态工具检索、异步 Job 和 SubAgent

- 仓库：[Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code)，本轮查看 `main@789b4b7`。其 README 同时说明是 beta、experimental；作为可读源码和行为测试来源，不能把所有功能状态等同稳定性认证。

**动态工具发现——最值得建立原型的机制：**

- [`search-tool-bm25.ts`](https://github.com/Yeachan-Heo/gajae-code/blob/main/packages/coding-agent/src/tools/search-tool-bm25.ts)：模型调用 `search_tool_bm25(query, limit)`，获取排序结果，并通过 session hooks 激活隐藏工具。默认 limit=8；模型看到短 JSON 回执，不必吞下全部工具说明。
- [`tool-index.ts`](https://github.com/Yeachan-Heo/gajae-code/blob/main/packages/coding-agent/src/tool-discovery/tool-index.ts)：索引 `name/label/server/summary/schema_keys`，加权 BM25 排序；工具激活前需排除已激活工具，恢复时需重新核对允许名单。
- [`docs/tools/search_tool_bm25.md`](https://github.com/Yeachan-Heo/gajae-code/blob/main/docs/tools/search_tool_bm25.md)：完整记录动态激活、检索索引、缓存和不同 discovery modes。
- **本地化注意**：它的英文/标识符分词逻辑不一定适用于“树冠识别、影像拼接、反射率校正”等中文问题。第一版可采用英汉领域同义词、字/词 n-gram 和 BM25 比较；只有匹配失败数据支持时再引入 embedding。检索出的工具不自动取得权限。

**后台任务——值得学习而非复制内存 JobManager：**

- [`job-manager.ts`](https://github.com/Yeachan-Heo/gajae-code/blob/main/packages/coding-agent/src/async/job-manager.ts)、[`job.ts`](https://github.com/Yeachan-Heo/gajae-code/blob/main/packages/coding-agent/src/tools/job.ts) 和 [Job 文档](https://github.com/Yeachan-Heo/gajae-code/blob/main/docs/tools/job.md)：区分 `list/poll/tail/cancel`，前台等待可让位、终态自动投递、已被主动观察的终态不重复投递、有限重试与投递失败处理。
- **本地化目标**：保留本项目已有的 SQLite Job 权威、Docker 资源/日志/产物追踪；移植的是 `CompletionDelivery`（终态投递）和 `Acknowledgement`（观察/投递确认）协议。不以 Gajae 的内存 JobRegistry 替换现有持久 Jobs。

**SubAgent：**

- [`task` 文档](https://github.com/Yeachan-Heo/gajae-code/blob/main/docs/tools/task.md)：含并发上限、任务输出引用、父子工作范围与结构化回执。可以研究“子任务真正独立才并行”“子任务摘要而非全量文本回灌”的实现。
- **不移植**：Git worktree 代码隔离等编程 Agent 特有机制不直接作为无人机遥感任务的必要前提；无人机任务更需要只读数据引用、科学方法/环境身份和不可篡改产物血缘。

### 2.3 DeepSeek Harness：学习并可能采用完整的职责和事件契约

- 仓库：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)；其[架构文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)清楚区分 `Agent Loop / Session / Tools / LLM / Scope`。这更适合作为**目标架构的契约参考**。
- [`agent-loop/src/inbox.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/inbox.ts)：持久 Inbox，将输入分为 `next-turn` 与 `next-step`，用户消息不会因为 Agent 忙碌而消失。
- [`tools/README.md`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/README.md)：`when` 条件激活与 `ResolvedToolSet`，让**模型可见工具定义与实际可调用工具**对应同一份冻结快照。
- [`tools/src/executor.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/executor.ts) 与 [`pipeline.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/pipeline.ts)：完整批次的 call/result 关联、统一准入、并行安全、取消后结清。
- [`agent-loop/src/assistant-stream.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/assistant-stream.ts)：流式草稿是实时显示；持久提交才是真正的完成消息；失败则保持未完成记录。
- [`session/src/tool-history.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/tool-history.ts)：工具集在历史请求中如何声明、增删和恢复，不依赖“当前 ToolRegistry 的最新状态”反推旧请求。
- **不必采用**：不因为架构优秀就将现有 FastAPI/SQLite/Python 整体迁到 TypeScript/Cordis。可以先在 Python 内做等价接口与合同测试；若现有 Driver 难以实现等价语义，再开展内核替换原型。

### 2.4 LazyCodex：研究工程组织、验证与角色分配，不当作通用 Runtime 替代品

- 仓库：[code-yeongyu/lazycodex](https://github.com/code-yeongyu/lazycodex)，本轮查看 `main@c5e8181`。
- README 表明它主要向 Codex 分发 OmO 工作流、项目记忆、规划、执行与验证能力，并提供 doctor 健康检查。适合学习研发组织（任务边界、代码代理分工、工具诊断、结果复核），不是当前 Python Agent Core 的直接替代。
- 对 ForestryUAV-Agent 的具体用途：把系统重构任务拆成互不污染的源码研究、实现、接口测试和实验裁决；Codex 分派时必须保证一份模块只有一个写入者。不要把其强制长循环、巨量角色或编码工作流搬到林业用户的日常任务中。

### 2.5 PydanticAI / Harness：从“接口名称学习”转为“版本固定、读内部实现、做适配验证”

现有 `pyproject.toml` 为 `pydantic-ai-slim[openai]>=2.43,<3`、`pydantic-ai-harness>=0.31,<1`，范围较宽。第一步需要从锁文件及实际环境读取**精确安装版本**，查看其原生 capability、tool_prepare、compaction、checkpoint、deferred results、usage 以及 streaming 生命周期；然后判断：

- 现成的原生能力已具备：**调用 / 薄适配**，删除重复实现。
- 原生能力基本具备、但实现不满足科学作业语义：**基于官方扩展点补充**，避免 fork 大包。
- API 不支持核心原子性或事件重放要求，且有可重复证据：**隔离替换此段 Driver**，不是继续通过 Hook 再打十层补丁。

尤其不能把 `DeferredToolRequests/DeferredToolResults` 与提交后即返回 `JobRef` 的异步模式混在同一次操作中；每个 call 在一个时刻只能拥有一个确定的状态语义。

## 3. 应当重建的目标运行架构

保留现有科学执行、数据库、文件资源和前端主体，但将运行职责重新定义：

```text
用户 / UI / API
       │   user_message / approval / cancel / job_terminal
       ▼
[Durable Inbox（SQLite事件/消息入口）]  ← 全部活动按身份入队、去重
       │  一个会话领取下一条待处理输入
       ▼
[Agent Driver（PydanticAI 优先，可替换）]
       │    ├─ Context Builder：历史 + 证据引用 + 记忆/知识 + token预算
       │    ├─ Tool Discovery：工具摘要目录 → 检索 → 激活schema
       │    ├─ Model Adapter：只在模型流生成时占GPU/模型slot
       │    └─ Decision & Recovery：观察 → 判断目标进展 → 下一步
       │
       ▼
[Guarded Tool Executor（同一份 ToolSetSnapshot）]
       │    ├─ 权限/输入身份/参数校验/幂等
       │    ├─ 只读有限并行 / 副作用独占
       │    └─ action_id/call_id/attempt_id 记录真实结果
       ▼
[Durable Job Executor] ───────────────────┐
       │ ODM / PROSAIL / PDAL / lidR / code / environment
       │ 终态与产物先持久提交       │
       └───────────────────> JobCompleted事件 ───> Durable Inbox
       │
       ▼
[科学证据与产物仓库] → UI交付/报告 → 后续分析请求
```

**五条约束**：

1. `model_request`、`agent_turn`、`tool_execution`、`job_lifetime` 是四种不同的生命周期，不能用一把锁或一个倒计时统治。
2. `ToolSpec` 是注册定义；`ToolSetSnapshot` 是本 step 对模型可见的集合；`PermissionDecision` 是执行时的权威准入。三者不得互相冒充。
3. `SessionEvent` 是提交的事实，`StreamFrame` 是可丢失/重播的展示；后者不能替代前者做任务验收。
4. 任务计划是模型的工作记事，不能修改权限、科学方法、产物真实性或程序控制状态。
5. 领域方法的科学适用性由声明、元数据、算法约束和证据评价，不由关键词匹配或文件存在直接决定。

## 4. 内核技术选型：真正允许三条路线竞争

| 路线 | 定义 | 可获得的益处 | 风险 | 选择条件 |
|---|---|---|---|---|
| A. **保留 PydanticAI 驱动、重构外围** | 用已有 Hooks/Tools/Deferred/StepStore 实现新契约；去掉重复胶水 | Python 生态、领域工具兼容、改动成本低 | 原生 Hook 顺序/锁/恢复语义仍可能制约 | 能用接口测试证明所有核心不变量 |
| B. **保留模型与工具层，替换回合调度/执行驱动** | 自建最小 Python Agent Loop，使用 PydanticAI 的模型/消息/工具Schema接口或兼容 Adapter，借鉴 DeepSeek/Claw 的步骤语义 | 直接控制 Input Inbox、step、调度、checkpoint 与 Job 接回 | 代码所有权扩大，容易重新造低质量 Agent | A 的不可消除缺陷有稳定复现，且 B 在最小实验中明显优于 A |
| C. **整体采用另一既有 Runtime/语言** | DeepSeek Harness 或其他完整栈作为主驱动，领域工具经 RPC/MCP/服务暴露 | 复用成熟生态中的部分核心能力 | 数据迁移、部署复杂、进程通信、Python/R地理栅格工作流适配代价大 | 只有当前 Runtime 结构性无解，且跨栈原型取得可测收益才考虑 |

**本轮默认先做 A 的最小原型，同时允许 B 作为同样规格的竞争实验。C 只做技术可行性报告，不立即迁移生产。** 不必等 A 的所有阶段结束才探索 B；但必须在独立分支或沙箱进行，避免扰动旧数据。

**决策门**：完成“多会话+异步 Job+中断恢复+动态工具”四个共享行为测试后，比较通过情况、实现重复度、复杂度、上下文耗费和安全边界。选择最简单且稳定通过的一条路。不能只因为代码行数多或喜欢某个开源项目决定。

## 5. 工作包与执行顺序

### WP0：同步实际状态，建立真实对照底座（立即）

**参考**：LazyCodex 的 doctor / 独立研发与验证流程；Claw 的配对测试。

- 读取 Git HEAD、未提交修改、实际8769服务的进程版本、模型 served name/digest、Ollama真实上下文、Thinking、PydanticAI具体版本、工具清单及配置。**部署版本不能用Git HEAD替代**。
- 将已报告的5项失败测试实际复现，按原因分类：代码缺陷、配置/执行器限制、错误断言、环境不可用。保留原日志和可回放输入。
- 抽取既有真实不良轨迹：300秒等待；未输入仍反复调用工具；CSV/代码作业容器失败；多轮 LAI复用的small/large差异。
- 建立可离线重放的模型输出→工具调用→工具结果夹具，冻结历史真实科学产物，不重算 ODM/PROSAIL 只为代码验证。
- 只对模型真实选择能力有需求的测试才发起真实Qwen调用；可重复协议问题先运行离线测试。

**产物**：`docs/rebuild/CURRENT-FACTS.md`、`docs/rebuild/REFERENCE-MATRIX.md`、`tests/runtime_contract/`、`docs/rebuild/BASELINE-REPORT.md`。

**出口**：能够明确指认现用版本、5项失败原因、典型轨迹及本次对照的可复现配置。

### WP1：用开源项目行为契约改造 Agent 生命周期（最高优先级）

**参考**：Claw `TurnControlSignal`、DeepSeek `Inbox/Turn/Step` 与 `assistant-stream`。

- `agent.py::Engine.run()` 的 `model_slot` 由“整回合”缩为“模型请求从开始到完整流消费结束”；模型等待 GPU 时保留所在会话上下文，但不能持有全局 Job/DB 锁。不同会话可进入各自的 run，模型生成并发上限初始仍为1。
- 保证同会话单 writer：新用户输入由 durable inbox 入队，可在本回合下一step消费；不要在生成过程中强行改写已经冻结的模型请求。实现 `steer`（继续指令）、`stop_current_reply`（结束当前生成）、`pause_activity`（暂停未来动作）、`cancel_job`（终止具体作业）、`delete_session`（安全清理）互不混淆。
- 设计冻结的 `ModelRequestEnvelope`：identity(model digest, params, schema hash, prompt hash, request id) 与发往模型的流区间同生命周期。持久化完整可重建应用侧请求；响应头、首chunk、终止、异常分层记录。
- 一个批次内的每个工具调用都结算相应结果；未执行以 cancelled/skipped 结果结算，已开始且不知结果为 unconfirmed；绝不虚构 success，也不偷偷重放副作用。
- 复核 SQLite 单写与 Native Harness 步骤库：跨会话放开后，旧的 `SerializedStepStore` 仍能保护同库写入；避免把一个锁范围变小却造成 WAL/readonly 回归。

**核心测试**：A 会话在 `wait_job`/长外部工具等待，B 会话可问答；同会话消息提交与结束竞态；中途停止和跨重启续聊；并发两个只读调用；一个副作用调用断流后不重做；模型流断开不丢实际已提交事实。

**出口**：四层生命周期互不越权；同一会话绝不并行跑两个决策者；模型推理并发可配置而与工具等待独立。

### WP2：动态工具检索与上下文协议联动重建（最高优先级）

**参考**：Gajae `search_tool_bm25`，DeepSeek `ResolvedToolSet`/`ToolHistoryProjection`。

- 在 `ToolRegistry` 中增加 `ToolDescriptor`：`name, summary_zh, summary_en, capability, domain, modalities, accepts, produces, schema_ref, permission_tier, load_mode, version`。定义只维护一份；工具说明不用重复存到其他手写表。
- 核心常驻工具控制在“足够支持发现、只读探查、基础执行”的最小集合；非核心工具按需检索并在下一模型step可见。对没有输入的任务保留“发现/解释为什么需要输入”的路由，不能因为没有附件把模型完全锁死在回答能力中。
- 做两个检索器对照：①轻量词法/BM25（含中文分词、英汉同义词、专门领域词表）；②embedding检索+可选重排。先测召回与错误激活，再比较整体模型任务收益，不先默认embedding更好。
- `ToolSetSnapshot` 在 step 创建时冻结，记录 `tool_names/schema_hash/reason/source_rank/auth_scope`，模型可见与真实准入来自同一快照；在这个 step 中不增删 Schema；后续激活不会改变已经发出的工具请求。
- 现有 `PreparedInput` 改为“统一可重建请求 + 可校准token估算”。尽可能使用模型实际 tokenizer/服务输入用量；不可准确测量时给估计上界及误差来源。安全余量有边界，不能因只用了 UTF-8字节就拒绝原本可发送的请求。
- 明确区分：短期消息/运行事实、检索进来的文档原文、长期显式记忆、工具目录描述、科学方法知识。按任务需要编译，引用先于长正文注入。历史压缩保护 user objective、关键 tool-use/result 配对、未完成活动和当前证据引用；允许旧证据按ID读取回来。
- 记录提示词成本分解：固定 system，当前状态，历史，工具 schema，文档检索，图片和结果摘要。仅指标可解释还不够，用户任务成功率不能下降。

**测试**：至少覆盖中文“正射影像重建/多光谱LAI/LAZ地形/CSV缺失值/图像计数”等不同领域输入，设定人工标注的目标工具集合。隐藏的合法工具可通过检索激活，越权工具即便被检索也不能执行。压缩前后相同问题的事实引用一致；无孤儿工具结果。

**出口**：动态检索不会降低重要工具召回；实测/估算上下文成本明确下降或在相同预算下正确完成更多任务；不通过“强行缩短回答”制造改善。

### WP3：完整的后台工作、进展与用户操作闭环

**参考**：Gajae `JobManager` + `JobTool` 的 completion delivery / ack，DeepSeek Inbox；复用现有 SQLite Jobs。

- 作业提交立即返回 `JobRef`：`job_id, operation, input_identity, method_identity, status, submitted_at, originating_turn, producer_action`。不能把“已提交”“已完成”“科学验收”合并为一个 completed。
- 增加 `JobCompleted` 持久事件，携带 `job_id/generation/terminal_revision/event_id`，**先在 Store 中持久化状态与事件，再投递**；inbox 按 `event_id` 去重，重复回调/服务恢复不可启动第二次计算。
- 增加 `delivery_state`：未读、已呈现、已消费/明确跳过、需人工处理；既有 `wait_job` 已消费的结果避免再被自动投递。这里的“恰好一次”是**逻辑效果幂等**，不是宣称网络和进程通知物理上只发生一次。
- 模型与后台作业的交接策略：耗时长的计算提交后可结束当前对话并返回用户；作业完成时如果用户原任务仍欠“分析/报告”，则唤起一个合法 step；如果目标只要求生成文件，UI 自动展示成功结果即可，避免无意义的模型开销。
- Job 短等待默认为“尽快返回状态”，45—120秒等待改为用户或模型明确选择的前台等待选项；当已有长 Job 正在运行，新的用户消息不需要在这个等待中排队。
- 前端把 Jobs 常驻显示为正常业务面板（出现作业时），日志、资源/容器细节折叠。至少提供运行状态、来源用户消息、执行阶段、取消与结果链接。
- 不能用 `asyncio.create_task()` 的进程内 Future 作为持久权威。重启和机器断电后由 Store/Job ID/容器状态完成对账。

**测试**：提交→等待→切换会话→刷新浏览器→重启→收到终态→生成摘要/报告；Job观察与终态投递竞争；取消发生在容器退出前后；消息与作业同时到达；同一结果只能被当前活动消费一次但 UI 可多次读取。

**出口**：用户不用反复发问“做完了吗”；无重算、无虚假完成、无丢失完成事件；保存原科学结果。

### WP4：真正提升 Agent 决策与专业科学判断

**参考**：Gajae结构化错误分类、DeepSeek Guarded Executor；本项目已有 `Progress`、`Evidence`、Imagery/LiDAR。

- 将 `Progress` 从“重复观察次数”升级为“动作重复与任务证据状态”两层。保存：`call_signature`、`input_version`、`method/parameters`、`obstacle_identity`、`result_identity`、`known_hypothesis/verification_target`。模型的假设文字不直接给自己加分；只有与可核查的检查或新条件相符才认定有效测试。
- 对确定性权限错误、缺必填值和输入版本冲突：不在原参数下无脑重试。对短暂 provider /网络服务故障：限制次数、退避、保留同一请求身份；对科学算法不适用：允许提议替代方法，但不得静默修改已明确冻结的用户参数。
- 减少生产层的能力回答字符串黑名单、按特定词触发固定工具等规则，把它们转换为通用 E2E 回归样例；强制规则只保留数据安全、授权、不可逆副作用、证据真实性等边界。
- 林业科学工具做**能力声明与适用性描述**（而非仅工具简介）：输入数据类型、空间尺度、波段、地理单位、必要元数据、算法假设、输出产品、失败条件、可验证指标。
- 提供方法候选与选择记录：ODM/正射、PROSAIL-LUT/反演、LiDAR terrain/HAG/CHM/instances，各工具的使用前提由可靠元数据检查；Agent 能说明为什么选择、何时需要用户数据、为什么拒绝给出精度结论。
- `imagery_catalog` 的复用升级为 `ScienceRunIdentity = data hash + effective normalized declaration + algorithm version + image digest + calibration provenance + preprocessing version`；缺少参数声明时只能返回透明的已有候选方案，而不是默认其完全符合新请求。模型可基于已知旧参数判断是否符合用户目标。
- 区分三级证据：①计算/产物字节及元数据正确；②模型使用的方法与解释可信；③独立地面精度经验证。只有③才可声称对应精度；光谱残差不是地面LAI精度。
- 不主动恢复已按用户决定取消的联网搜索、点云剩余扩展或领域峰值吞吐测试；保留已存在工具，目标是质量而非范围膨胀。

**测试**：相同输入但先验范围或太阳/观测几何不同→不静默复用；Cab 上界饱和→报告说明原因和不可得结论；错误影像定标/波段→拒绝猜测；同一错误动作循环→停止，但合理的替代检查→继续；运行结果成功但科学解释错误→不能给最终科研验收通过。

**出口**：Agent 能自主完成一条新或可验证复用的专业工作流，能定位不可靠结论的证据原因，报告有追溯的参数与空间元数据。

### WP5：综合比较、路径选择与清理

- 三种对照：**H0最简Agent基线**、**H1运行基础+按需发现/可靠上下文**、**H2有条件SubAgent**。在相同模型权重/quantization/context/思考/任务和执行器条件下运行；每个实验都记录实际使用的 schema 集和 model digest。
- 工具发现消融：全量 tools / 分类静态 tools / BM25发现 / 混合检索；不同大小的本地模型分别观察是否收益一致。
- 生命周期对照：旧全局回合锁与新模型请求锁，对两个会话、长外部等待和并行只读工具测实际延迟、吞吐和正确性。
- 子Agent的增益要体现在正确率、并行效率、隔离和真实回执，不能只看“代理数量”或“完成了几个子步骤”。单Agent足够时不强行委派。
- 科学评估与软件评估分别统计，不能把所有“Job returned success”都计作“科学任务成功”。
- 移除被新能力取代的重复代码，保留必要兼容适配；最终统一部署入口，显示运行 commit、模型 digest、版本、执行器与配置摘要。

**出口**：形成可复现实验的最终裁决：哪些外部机制已移植，哪些现有模块被替换，哪些没有收益而被放弃；形成下轮专门科学研究的稳定基线。

## 6. 可执行的验证矩阵与决策门

| 维度 | 必须观测什么 | 可判定成功的最低要求 | 不可接受的“假成功” |
|---|---|---|---|
| 多会话 | A有Job或外部等待时B能否回答 | 不被A的非模型等待无谓阻塞；请求并发不超资源限 | 只将全局信号量变成无限并发 |
| 工具发现 | 候选检索/激活及执行 | 对预设必要工具存在可成功发现路径，权限不被绕过 | 事先人工硬编码正确工具给模型 |
| 上下文 | 每次输入成本、组成、真实Provider用量 | 同身份请求可追踪、压缩不丢配对、模型不因错误预算无故拒绝 | 靠无限抬context_limit或删历史凑通过 |
| 可恢复性 | 取消、超时、服务重启与继续 | 不重复副作用；尚未确定事实保留unknown | 重启后重算并谎称恢复 |
| Job | 持久终态、通知、查看、取消 | 消费幂等，状态可见，报告只依据真实完成 | 每次刷新唤醒模型轮询 |
| 纠错 | 同阻碍/新策略/新证据 | 无效重复被拦，有效替代检验可继续 | 无约束地无限重试或用关键词黑名单 |
| 科学判断 | 方法/参数/空间元数据/精度 | 科学输出与结论分层，必要限制有来源 | 把文件有效说成模型科学正确 |
| 工程成本 | 请求时长、token、行动数、代码量 | 相同任务质量不降，复杂度受控；有实际收益证据 | 因迁移流行框架就宣称架构升级 |

指标以**配对比较和行为不变量**为主，不在未经基线测量前承诺“成功率必须高于95%”等拍脑袋数值。建议使用任务逐项成功/失败、首次响应时延p50/p95、总耗时、工具调用正确率、人工干预次数、正确科学声明比例和错误复用率，并对失败逐项分析。无可用执行器时只报告“执行环境未验证”，不算模型能力失败。

## 7. 对应已有源码的替换/保留清单

| 原位置 | 建议动作 | 对应外部源码 | 改造结果 |
|---|---|---|---|
| `agent.py::Engine.run` | **结构性改造**；可竞争替换Driver | Claw `ConversationRuntime`；DeepSeek `agent-loop` | 生命周期分离、合法step、同会话顺序 |
| `runtime/model_adapter.py` | **保留并加强** | DeepSeek `assistant-stream` | 模型请求粒度限流、流事实与提交分离 |
| `runtime/tools.py` | **扩展/局部移植** | Gajae `search-tool-bm25` / `tool-index`；DeepSeek `ResolvedToolSet` | 动态检索+冻结工具集 |
| `runtime/prepared.py`、`context.py` | **重构局部接口** | DeepSeek `session/tool-history`；Claw `compact` | 有来源的准备请求、计量校准、完整历史 |
| `runtime/session_repository.py`、`store.py` | **保留数据库，扩展消息契约** | DeepSeek `inbox`；Claw `TurnControlSignal` | durable inbox、用户新指令/Job事件接回 |
| `runtime/scheduling.py` | **保留和修正边界** | DeepSeek `tools/pipeline` | 显式独占与有界并行，执行前确认身份 |
| `jobs.py`、`runtime/job_tools.py` | **保留主执行器，移植通知协议** | Gajae `async/job-manager` / `job` | 完成投递、幂等消费、前台让位 |
| `progress.py`、`runtime/evidence.py` | **更改判定模型** | Gajae错误分类；DeepSeek工具结果契约 | 不重复无效动作、允许真正替代策略 |
| `roles.py` | **保留，按能力证据决定启用** | Gajae `task` | 有界委派与结构化回执；不强制使用 |
| `imagery_catalog.py` 和科学工具 | **升级领域语义协议** | 主要来源是本项目方法与科学证据要求 | 方法适用性、精确结果身份、三级验证 |
| `frontend/src/App.tsx`、`JobsPanel.tsx` | **可见性改造** | Gajae job UX语义；DeepSeek状态呈现 | 日常后台任务、诊断详情分离 |

## 8. 对Codex的执行规则与交付组织

建议采用以下工作方法，而不是把本文件逐条机械转换成几十个任务卡：

1. **先运行源码研究工作包**：每个关键领域至少读对照项目的一条真实执行链与测试，而不是只读 README。形成外部实现→本项目问题→可迁移接口→代价→测试证明的源码对照表。必须写出不移植的原因。
2. **以行为合同驱动重构**：先为同会话输入、ToolSet快照、Job终态/消息接回、压缩配对写最小失败夹具，再修改实现。每次迁移保留真正改变的时序和错误语义，不强求复制外部文件目录。
3. **允许竞争原型**：对 Driver A/B 路线各做最小可运行验证；如果 A 已稳健通过，无必要扩大 B。如果 A 受结构限制，B 可取代其运行层而复用领域组件。
4. **避免平行权威**：同一任务身份、工具结果、Job事实与会话事件只有一个真相来源；旧路径下线而非永久维护双写。
5. **验证分层**：契约单测→离线事件回放→隔离真实模型交互→真实Job接缝→正式配对能力评估。不要把“以前运行过”当作当前版本独立验证。
6. **保留科研证据**：不删除旧会话/科学结果、不静默修改科学参数、不把任务进展百分比当模型指标。按用户已冻结的范围开发。

**每个工作包交付**：真实新增行为、改动文件、移植代码与许可证说明、接口定义、受影响测试输出、已知失败及原因、运行版本/实例、回滚指引、下一阻碍。不要用“模块增加了多少”衡量研发进度。

## 9. 可直接交给Codex的核心任务说明

> 基于 `niupai1001/ForestryUAV-Agent@3eb5340` 深入研究 Claw Code、Gajae-Code、DeepSeek Harness、LazyCodex，以及本机实际安装版本的 PydanticAI / pydantic-ai-harness。此次不是局部修补，也不是限制只能借鉴理念：允许合规复用、移植算法和测试、替换现有核心模块，并将保留 PydanticAI 驱动与最小替代 Driver 作为可比较的技术路线。首先检查最新实际部署/工作树、复现既有5项核心失败与当前不良轨迹；然后以“同会话顺序与新消息接回、模型请求级资源锁、工具语义发现与冻结ToolSet、可重建上下文和压缩完整性、持久Jobs终态投递与幂等消费”作为第一批核心行为合同，实现最小端到端闭环。继续修正无进展误拦、特例输出规则与科学成果复用语义。保留已验证的领域工具、历史数据、报告与运行边界，避免新旧实现双写。先给出外部源码与本项目的模块级差异、A/B Driver取舍和修改路径，再实施能够明确验证收益的重构。每轮提交说明行为变化、文件、测试、实际运行版本、剩余缺陷；禁止宣称未运行过的模型/科学能力已验收。

## 10. 参考源码索引与合规说明

- 当前仓库：<https://github.com/niupai1001/ForestryUAV-Agent>
- Claw Code：<https://github.com/ultraworkers/claw-code>；`rust/crates/runtime/src/conversation.rs` / `compact.rs` / `session.rs`。
- Gajae-Code：<https://github.com/Yeachan-Heo/gajae-code>；`packages/coding-agent/src/tools/search-tool-bm25.ts`、`tool-discovery/tool-index.ts`、`async/job-manager.ts`、`tools/job.ts`、`task/index.ts`。
- DeepSeek Harness：<https://github.com/deepseek-ai/deepseek-harness>；`docs/architecture.md`、`packages/core/agent-loop`、`core/tools`、`core/session`。
- LazyCodex：<https://github.com/code-yeongyu/lazycodex>；README、project memory、doctor 与工作流组件。
- 本项目现存规范：`docs/AGENT-IMPLEMENTATION-PLAN-20261022-zh.md`、`docs/DELIVERY-TRACKER.md`、`research/tasks.research-v1.json`、`tests/`。

本轮查看的 Claw Code、Gajae-Code、DeepSeek Harness、LazyCodex 根仓库均展示 MIT License，移植/复制实质代码时必须保留原版权声明和许可文本，并核查**被复制具体文件/依赖**的许可及版本。当前 ForestryUAV-Agent 根目录未发现 `LICENSE` 文件；在发布含移植代码的版本前，应由仓库所有者明确项目整体许可证与第三方声明，不推定未指定许可证允许外部任意再分发。本计划不代表对外部代码质量、安全性和生产稳定性的全面认证。
