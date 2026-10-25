# runtime-core / Agent wb20261021

状态：实施中（第一条流程已实现并在隔离实例实跑；上下文、作业、持久化组件尚未迁出）
负责人：本次接手 Agent（工作目录 `C:\Users\yzh\WorkBuddy\Agent\forestry-uav-agent-v1`，基线提交 `7a0ed5b` + 工作树既有未提交研究改动）
写入范围：新增 `src/forestry_agent/runtime/`；修改 `src/forestry_agent/agent.py`。未触碰 `app.py`、`store.py`、`jobs.py`、`context.py`、`roles.py`、前端。
共享资源：占用本机 Ollama `127.0.0.1:11434`（qwen3.5:4b）与隔离实例 8781（工作区 `workspace/runtime-core-20261021/data`）。8769 主工作台与 8770 研究实例未重启、未改数据。验证结束后已停止 8781 实例并释放模型；需要复看时重启：
`PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m forestry_agent.cli serve --config workspace/runtime-core-20261021/app.yaml`

## 用户可用结果

- 会话里的模型请求改由 `ModelAdapter` 创建：provider、HTTP 客户端、响应头阶段、单次请求墙钟预算和原生流映射都集中在该组件，`agent.py::make_agent()` 不再自己建 provider 或维护共享阶段字典。
- 工具定义改为注册组件：`list_inputs`、`select_input_files`、`list_files`、`read_file`、`inspect_artifact`、`read_artifact` 由 `runtime/file_tools.py::register_file_tools()` 注册，schema、动作类型和调用实现来自同一份定义；执行仍走原来的 `Engine.perform()` 守卫通道（准入、动作登记、预算、结果投影不变）。
- 原生流事件按请求区分：新增/保留 `model.stream_opened`、`model.request_finished`、`model.request_timed_out`，每条带 `request_id`（本轮输入清单 ID）。响应头与首片段分别计时，未观测到响应头时 `layer=transport`，不会断言"模型正在加载"。
- 停止后保存实际收到的可见正文前缀，消息带 `interrupted: true`，并记 `run.interrupted_prefix` 事件；不补写未返回内容，也不标成完整回答。

## 修改与证据

新增文件：

- `src/forestry_agent/runtime/interfaces.py`：`RunScope`、`RequestTrace`、`ToolSpec`、`ModelChannel` 及 `ModelAdapter`/`ToolRegistry`/`SessionRepository` 契约。
- `src/forestry_agent/runtime/model_adapter.py`：`LocalModelAdapter`（`open()` 建通道、`request_guard()` 单请求墙钟与阶段槽、`observe()` 原生流归一化、`report_timeout()/finished()` 证据发布）。
- `src/forestry_agent/runtime/streams.py`：`AssistantStreamAccumulator`，一个请求一个累积器，思考/正文/工具参数分流。
- `src/forestry_agent/runtime/tools.py`：`ToolRegistry`（重名检查、`install()`、清理句柄）。
- `src/forestry_agent/runtime/file_tools.py`：六个文件/产物读取工具的定义与注册。

`agent.py` 改动（均在原有未提交改动之上追加，未回滚他人内容）：

1. `Deps` 用 `request_trace: RequestTrace` 取代 `model_stage` 字典与 `model_request_deadline`；新增 `scope` 属性与 `stream_prefix`。
2. `Engine.__init__` 装配 `self.model_adapter`；`make_agent()` 改为 `channel = adapter.open(profile)`、`bounded_model = adapter.request_guard(channel, budget_seconds=...)`，返回 `(agent, channel)`（`ModelChannel.aclose()` 与原 client 用法兼容）。
3. `after_model` 用 `adapter.finished()` 发布请求完成证据。
4. `model_events()` 改为委托 `adapter.observe()`，保留原签名供 `SubAgents` 与测试调用。
5. 六个文件工具从闭包移出，改为 `ToolRegistry` 注册后 `install(agent)`；其余工具（影像、作业、角色、记忆、网络）保持原位置，下一批再迁。
6. `run()` 收尾处 `record_interrupted_prefix()`：仅在回合未正常完成、未产生 assistant 消息、规范未推进时写中断前缀。

验证触发：结构化改动触及 `make_agent`、模型流、工具注册与运行收尾，属"相关代码变化"，需要一次全量回归 + 一条真实模型流程验证。

### 验证一：全量测试（改前/改后同口径对比）

命令：`./.venv/Scripts/python.exe -m pytest -q --tb=no --junitxml=<tmp>/base.xml`（改前）、`.../after1.xml`（改后）。

- 改前：517 项，11 失败；改后（含本批全部改动，`after2.xml`）：517 项，11 失败；**新增失败 0，修复 0**（失败集合逐一相同）。
- 既有 11 项失败与本轮无关，属工作树既有状态：`test_context`（5，期望文案"编译输入保守估算/超过预留输出"与现文案不符，其中 4 项实为上下文上界拦截）、`test_output_recovery`（2，纯文本输出未按原生协议重试）、`test_progress`（2）、`test_native_loop[H2]`（1）、`test_conversation_flow::test_view_image_*`（1，CONTEXT_ESTIMATOR_INVALID）。
- 说明：本轮未修改这些测试或降低其目标，也未把它们当作通过。

### 验证二：真实模型流程（隔离实例 8781）

脚本 `workspace/runtime-core-20261021/smoke.py`（工作区已被 gitignore）。夹具为本地文本 `sample-P17-*.txt`，模型 `qwen3.5:4b`，`harness_mode=H1`。

任务 `76f708f6-8f60-4744-b8b9-0b52e981d44b`（RUN=5）与后续 RUN=6：

- 第一回合：`fs.read` 动作 1 次（已确认完成），回答正文含 `P-17` 与 `油松`；原生思考 112 字符、`assistant.draft` 13 条；两次请求的 `model.request_finished` 分别带 `request_id=8a7ca3db…/f08a9be4…`，`headers_seconds=1.547/2.047`，`first_fragment_seconds=1.562/1.656`，`layer=generation`。
- 停止：可见正文 42 字时取消，任务终态 `cancelled`，`run.stopped` 1 条，会话仍可继续。
- 续聊：同一会话发送新消息后正常完成，回答 `1+1 等于 2`。
- 停止前缀：RUN=5 未落盘（当时前缀只在请求正常返回时赋值，取消路径拿不到）。已改为边收边记（`observe(on_visible=...)`）。

复核记录（RUN=6，任务 `ff558471-4815-459c-814f-bd549c2323bc`）：

- 第一回合：`fs.read` 1 次，回答含 `P-17`/`油松`，原生思考 178 字符、`assistant.draft` 13 条；两次请求的 `request_id=301f7292…/6ff121cc…`，`headers_seconds=1.672/1.719`，`first_fragment_seconds=1.672/1.235`，`layer=generation`。
- 停止：可见正文 82 字时取消，任务终态 `cancelled`，写入 `run.interrupted_prefix`（1 条），消息列表末尾出现 `interrupted: true` 的 assistant 消息，正文 108 字，正是停止前看到的前缀。
- 续聊：同会话再发「1+1 等于几」，正常完成并回答 `1+1 等于 2。`；中断消息仍在新用户消息之前，顺序正确。
- 这也说明 `ContextVar` 不足以覆盖驱动的请求上下文：阶段槽最后加在 `ModelChannel` 上（一次运行一个 client，请求顺序发出），响应头才稳定落在 1.2–2.0 秒。

## 剩余与接续

本轮**没有**做、也不声称完成的部分（均属 2026-10-21 指导文档后续步骤）：

1. 上下文组件（指导文档第二节）：`ContextBuilder`/`PreparedInput` 仍未拆出，`context.py::input_bound()` 的 text/vision/tool/framing 分项计量、历史锚点叠加问题、附件按需读取都未改。task11 一类"视觉 token 为 0 却按图片过大拒绝"的路径未修。
2. 工具路由与调度（第三节）：`ToolRegistry` 只覆盖六个文件工具，并行/独占调度、`JobExecutor` 适配、`process_imagery` 注册化未做。
3. 持久化与清理（第四节）：`SessionRepository` 只是契约；`sqlite_probe()` 仍会建表/写入/删表，尚未改为只读诊断；删除、恢复、Job 接回未动。
4. 报告与科学结论（末节）：task03/05/09/12 的评分与归因修正、已有正射/LUT/LAI 成果的自主读取报告未做。

真实阻碍：`qwen3.5:4b` 在 32K 上下文下首片段约 1.3–2.0 秒，可用；没有再测 300 秒等待问题（那是 27B/262K 下的观察，本轮未复现也未声称修复）。

下一项具体操作（按指导文档顺序）：把 `context.py` 的编译与计量包成 `ContextBuilder`，输出 `PreparedInput`（messages/tools/settings/计量分项），先修"估算当作实耗"和"视觉为 0 仍按图片拒绝"，再用同一隔离实例复跑一次多图会话。

不能重复的试次：本轮 517 项全量已在同一代码上跑过两次（改前基线、改后对比），除上述 11 项外不要逐轮重跑；真实模型冒烟的任务 ID 已记录，不要用同一 request_id 重放。8769 主工作台仍在运行旧代码，部署由运行负责人单独执行。
