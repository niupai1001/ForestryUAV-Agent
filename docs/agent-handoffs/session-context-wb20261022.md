# 会话持久事实与上下文构建 / wb20261022

状态：实施中（2026-10-22 计划第 5.1 节已接进真实调用路径；工具执行、JobExecutor、界面与报告未动）
负责人：本次接手 Agent（工作目录 `C:\Users\yzh\WorkBuddy\Agent\forestry-uav-agent-v1`，基线提交 `7a0ed5b` + 工作树既有未提交改动）
写入范围：新增 `src/forestry_agent/runtime/{prepared,session_repository}.py`、`tests/test_session_continuity.py`、本文件；修改 `context.py`、`agent.py`、`store.py`、`vision.py`、`runtime/model_adapter.py`、`docs/DELIVERY-TRACKER.md`。
未触碰：`app.py`、`jobs.py`、`roles.py`、`imagery*.py`、`sessions.py`、前端、`evaluation/`。
共享资源：验证阶段启动过隔离实例 8781（工作区 `workspace/runtime-core-20261021/data`，与 1.59 同一库）并短暂占用本机 Ollama `qwen3.5:4b`，验证后已停止实例、释放模型；8769 主工作台与 8770 研究实例未重启、未改数据、未部署。

## 用户可用结果

1. **失败、暂停、取消回合里的用户指令不再丢失。** 历史起点由「最近一个真正有已完成检查点的回合」决定，不再要求 `status='completed'`；没有任何可继承检查点时，本轮开场会带上「[会话承接]」段，逐条列出已提交但还没进入原生历史的用户消息（含回合状态、修订号、字数与截断说明），并附上该回合的停止原因与已保存的可见正文前缀字数。追问不会再退化成「只按最初目标继续」。
2. **中断时未返回的工具调用被明确结清。** 旧回合留下的 `pending` 动作改为 `failed` + `error.code=TURN_INTERRUPTED`，attempts 记 `effect_state=unconfirmed`，并写 `action.interrupted` 事件；作业记录保留，仍可继续 `wait_job`/取消。不再继续以 `pending` 阻塞交付与清理，也不冒充成功。仍在运行的回合、以及委派自己的活动不在结清范围。
3. **输入预算错误能指出实际组成。** 新增 `PreparedInput`：一条清单同时给出实际发送的 messages/tools/settings，以及 text / vision / tool_schema / framing / instruction_delta / usage_baseline 分项，每项标 `actual` / `estimated` / `unknown`。超窗时的错误文本直接列出各项（例如「编译输入保守估算 30,364 tokens 超过可用输入窗口 28,672 tokens；分项：工具定义15,098（估计）、对话文本14,162（估计）…」），`context.rejected` 事件也带同样分项。
4. **估算偏差与真实超窗分开。** 实测用量超过上界但仍在窗口内时，不再把回合判死并归因为「图片/历史过大」，而是把该清单的上界按实测结清（`bound_reconciled`、`reconciled_bound_tokens`），保留 `pre_send_bound_tokens` 作为原事实；只有实测真的超过「窗口 − 预留输出」才停（`CONTEXT_ESTIMATOR_INVALID`），文本里给出实测/可用/当时上界三个数和分项。
5. **作业记录能定位发起消息。** 模型可见的任务事实里，每条 `recent_jobs` 增加 `request_turn_id`（由 actions 关联），追问时可判断「这条作业是哪条消息发起的」；承接段额外列出未结束活动（作业编号、类型、状态、输入挂载数、结果计数）。
6. **状态与事件同一次提交。** `contexts.compile()` 的规范/控制校验、来源与动作引用、清单写入与 `context.compiled` 事件合并进同一个事务，读清单的调用方看不到「清单已写但状态已变」的中间态。
7. **只读查询不再抢写锁。** 新增 `Store.snapshot()`（`PRAGMA query_only=ON`，不取写锁），会话事实查询、凭证读取、配置快照读取改走它；`compile()` 里 `roles.artifacts()` 显式复用当前连接，避免在事务内再开事务。

## 修改与证据

新增：

- `src/forestry_agent/runtime/prepared.py`：`Component`、`PreparedInput`、`measure()`；分项计量与解释文本的唯一实现，`context.input_bound()` 变成它的薄封装。
- `src/forestry_agent/runtime/session_repository.py`：`StoreSessionRepository`（实现既有 `SessionRepository` 契约的 `read/commit`，并给出 `turns`/`committed_requests`/`unrepresented_requests`/`activities`/`open_activities`/`turn_outcomes`/`settle_interrupted_actions`）。旧记录缺的字段标 `unknown`，不猜。
- `tests/test_session_continuity.py`：失败回合续聊仍带该回合指令、完成回合不重复承接、中断动作结清且幂等、`prepared_input` 里工具结果必须对得上调用 ID。
- 本文件。

修改：

1. `context.py`：`input_bound()` 委托 `measure()`；`compile()` 改为「只读绑定凭证 → 生成 `PreparedInput` → `_admit()` 判定 → 单事务落清单与事件」；`measured()` 区分 `estimate_covers_observed_usage`（估算是否覆盖）与 `estimate_valid`（实测是否超过可用窗口），并写入 `estimate_shortfall_tokens`/`bound_reconciled`；`_binding()` 把「凭证不可复用」与「计量失效」分开（`estimate_valid is False`，或估算没覆盖且没有按实测结清）。
2. `vision.py`：新增 `visual_measure()`，返回图像 token 上界、张数、可解码张数与状态（无图=`actual` 0、可量=`estimated`、解码失败=`unknown`）；`visual_token_bound()` 保持原语义与断言值不变。
3. `agent.py`：`Engine.sessions` 接入仓库；`run()` 用 `history_anchor()` 回退探测检查点（保留审批/委派中断快照的原生恢复路径），用 `carried_block()` 生成承接段，用 `settle_interrupted_actions()` 结清旧回合动作，并写 `session.carried_requests` 事件；`run.stopped` 事件补 `turn_id`/`blocker`（状态里的 blockers 会被下个回合清空，原因必须留在事件里）；`durable_task_facts()` 的 `recent_jobs` 改为 join actions 取 `request_turn_id`。
4. `store.py`：新增 `snapshot()`。
5. `runtime/model_adapter.py`：单请求超时文本区分「单次请求墙钟预算」与「回合/作业预算」，带秒数与阶段（`transport` / `prefill_or_server` / `generation`）。

### 验证：全量测试（改前/改后同口径）

命令：`./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --junitxml=<tmp>/{base,after}.xml`。

- 改前：517 项，11 失败（既有）。
- 改后：521 项（含新增 4 项行为用例），5 失败，516 通过。
- 失败的 11 项里 **6 项已修**：`test_context` ×5（4 条断言旧文案、1 条实测/估算混用）、`test_conversation_flow::test_view_image_tool_returns_visual_content_not_filename_only`（视觉用量被判成估算失效）。
- 剩余 5 项与基线**同一断言、同一条件**，只是错误文本换成了带分项的新文本，属既有失败，本轮未修（见下）。

过程中修掉的三类回归/缺陷（都在全量或冒烟里暴露）：

- `history_anchor` 一开始只接受 complete 检查点，审批续接的 interrupted 快照接不上，`test_approval_continuation` 6 项失败 → 改回 `latest_checkpoint()` 的判定。
- `compile()` 把 `roles.artifacts()` 移进事务后，它内部又开事务（`BEGIN IMMEDIATE`），`test_roles` 3 项报 `database is locked` → 传 `db` 复用连接。
- 承接段读的是 `interrupted_prefix_characters`，仓库写的是 `assistant_prefix_characters`，真实模型冒烟里看不到中断前缀说明 → 统一字段名并补用例 `test_carried_block_reports_saved_prefix_and_stop_reason`。

### 验证：真实模型（隔离实例 8781）

工作区 `workspace/runtime-core-20261021/data`（既有 1.59 的会话保留在同一库），模型 `qwen3.5:4b`，`harness_mode=H1`，脚本 `smoke.py`（读文件 → 长回答中途停止 → 同会话续聊）。验证后实例已停止，模型已释放。

- 任务 `a5ad480a…`（RUN=7）：第一回合 `fs.read` 1 次，回答 `P-17`/`油松`；第二回合写到 98 字时取消，任务 `cancelled`，`run.stopped` 带 `turn_id` 与 `user_requested_stop=true`，`run.interrupted_prefix` 记 123 字，消息列表出现 `interrupted` 正文；第三回合续聊时出现 `session.carried_requests`：`bridged_from_turn_id` 指向第一回合，`carried` 含第二回合原文（状态 `paused`，修订 1）。该实例的 prepared input 里确认出现「[会话承接] … 请用中文写一段…」，说明承接段真的随请求发出，不只是事件记录。
- 任务 `510c430b…`（RUN=8，修字段名之后）：同样流程，56 字处取消、前缀 85 字；承接段变为「该回合已中断，只保存了 85 字可见正文前缀，不是完整回答」，第三回合回答 `2`。两轮请求 `headers_seconds` 0.92–1.20 秒（首次冷启动 6.11 秒），首片段与响应头基本同步。

这证明的是：**取消/暂停回合之后，用户上一次的要求仍然进入下一轮的模型输入**，而 1.59 之后到 1.60 之前它只会以「已完成回合的历史」形式存在，取消回合的要求会整段消失。

## 产品范围之外、不能当作已解决

1. 剩余 5 项既有失败：`test_native_loop[…H2]`、`test_output_recovery` ×2、`test_progress` ×2。前两项与后两项的断言与基线一致（H2 与 progress 的上下文溢出、输出恢复的重试判定），本轮只改了提示文本。
2. `test_progress::test_recent_source_bodies_survive_changed_instructions_larger_than_old_tail` 的溢出原因是工具定义 15,098 tokens 全文计费（无可复用凭证）。这与计划第 5.1 节第 3 条「优先使用适配模型模板的 tokenizer」相关，但需要接入模型模板的真实度量，本轮未做。
3. 附件侧（计划 5.1 第 5 条）：上传/发送只建消息附件的路径未改；批图索引沿用 1.56 的实现。本轮只保证「已需要的视觉内容」在计量上分项可见，没有改「每轮是否重发」的取舍。
4. 压缩侧（第 6 条）：`deduplicate_objective`、`UsageAwareSlidingWindow` 未改；只加了一条不变量测试（prepared_input 里工具结果必须对得上调用 ID），未验证压缩器的配对实现。
5. 工具执行与 JobExecutor（第 5.2 节）、后台作业生命周期（5.3）、界面（5.4）、诊断与报告（5.5）、主入口收口（5.6）全部未动。`sqlite_probe` 仍会写库。

真实阻碍：`test_progress` 那类溢出的根因是工具 schema 全文计费，需要模型模板级 tokenizer 与「已付凭证」覆盖更多组件；只在缺省 emoji 词表上调数字不解决问题。

下一项具体操作（按计划第 7 节顺序）：进入 5.2「工具执行与无进展控制」——先把 `perform()` 的固定顺序（校验 → 权限/取消/预算 → 登记 → 执行 → 结果校验与事实保存 → 进展判定 → 投影）落到 `runtime/tools.py` 与能力注册文件，再把 `progress.py` 的判据换成结构化事实（输入身份/版本、读取范围、实际结果、失败类别、作业状态），不解析包装文本。

不能重复的试次：本轮四次全量（base / after1 / after2 / after3）已在同一代码树上跑过，最终结论取最后一次（521 项 / 5 失败 / 516 通过）；不要用「测试数量增加」或重复请求当作进度。新增的 4 个用例是行为证据，不是指标。8769 主工作台仍运行旧代码，部署由运行负责人单独执行。
