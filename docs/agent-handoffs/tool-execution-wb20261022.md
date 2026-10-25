# 工具执行与无进展控制（2026-10-22 计划第 5.2 节）

状态：实施中（第 5.1、5.2 节已接进真实调用路径；JobExecutor/后台作业生命周期、界面、诊断与报告、主入口未动）
工作树：`C:\Users\yzh\WorkBuddy\Agent\forestry-uav-agent-v1`（未部署 8769）

## 写入范围

- 新增：`src/forestry_agent/runtime/{evidence,scheduling,projection}.py`
- 新增能力组件：`src/forestry_agent/runtime/{document,workspace,memory,environment,job,network,native}_tools.py`
- 修改：`progress.py`（判定改读结构化事实、返回 `Verdict`）、`runtime/tools.py`（`mode` 声明与校验）、
  `runtime/interfaces.py`（`ToolSpec.mode`）、`runtime/file_tools.py`（读取声明并行）、`agent.py`（执行通道与注册装配）
- 新增测试：`tests/test_tool_execution.py`（9 项）
- 文档：`docs/DELIVERY-TRACKER.md` 1.61、本文件

## 共享资源

- `perform()` 仍是唯一守卫通道：准入 → 权限/取消/预算 → 动作登记 → 执行 → 结果保存与事件。
  `guarded_tool` 只在它外面加「执行许可」和「进展判定」两段，不复制准入逻辑。
- 动作表/尝试表/事件表结构未改（`progress_observations` 无新列），新增事实只进事件载荷。
- 8769 主工作台与 `forestry_runtime/` 未改。

## 用户可见结果

1. 相同事实不会因为换包装、换句柄、换时间戳被算成新进展；正文没变的重读会正常计入停滞。
2. 同一阻碍换工具反复撞到时，停止消息会点名该阻碍（例：`同一阻碍已重复出现 2 次（失败类别 PRECONDITION_MISSING）`），
   而不是只报「没有新增证据」。
3. 相同工具+相同参数的重复请求被单独标出（事件 `repeated_request`），可与「换了范围」区分。
4. 只读工具可以并行（上限 `config.parallel_tool_reads`，默认 2），写入、依赖获取、作业提交一律独占；
   不同会话互不阻塞，一个会话的长作业观察不会让另一个会话的读取排队。
5. 应用关闭后不再开始新的工具调用；已经开始的调用继续跑完并写回真实结果，消息里调用/结果保持配对。
6. 事件 `action.observed` 带 `ui`（可读名称/状态/作业/产物数/错误码），界面不必解析正文。
7. 20 个原闭包工具从 `make_agent()` 迁到组件注册，模型可见 schema、动作类型、调度声明、实现同源；
   能力缺失的提示与拒绝理由沿用原有 `Failure` 码。

## 验证

- 全量：530 项，5 失败 / 525 通过（`C:/Users/yzh/AppData/Local/Temp/after_s6.xml`）。
- 失败集合与 1.60 改前逐项相同：`test_native_loop[H2]`、`test_output_recovery`×2、`test_progress`
  `test_recent_source_bodies…`、`test_progress::test_native_turn_request_limit…`；均为同一断言同一条件，
  根因是工具 schema 全文计费 15–16K tokens 使上下文溢出与重试判定，需模型模板级 tokenizer/凭证覆盖才能闭环。
- `ruff check src tests --select F,E9` 全绿。
- 本轮未跑真实模型：迁移由测试覆盖（`test_capabilities`、`test_imagery`、`test_jobs`、`test_roles` 等直接走新注册路径）；
  真实链路留给 5.3 后台作业接入后一次性验证，避免重复占用模型与执行器。

## 剩余项

5.3 后台作业（JobRef、短等待返回「仍在运行」、终态通知按事件身份去重、控制事件作用范围表、
日志/检查点先保存后清理、删除标记竞态、重放/接回/复用/重试分离）、5.4 界面、5.5 诊断与报告、5.6 主入口收口。
`sqlite_probe` 仍会写库。

## 下一项具体操作

按计划第 7 节顺序进入 5.3：先给 `jobs.py` 的提交回执补 JobRef（名称/类型/来源消息/逻辑活动/调用与尝试/
状态/时间/日志/结果/恢复关系），再把 `wait_job` 的长轮询改成「短等待到期返回仍在运行」，
最后补终态通知的串行处理与去重，并按第 5.3 节表格核对每个控制事件的作用范围。

## 不能重复的试次

- 不要重跑 `test_native_loop[H2]`/`test_output_recovery`/`test_progress` 两项既有失败去寻找「新原因」：
  它们与基线同一条件，只有在补齐 tokenizer 或改重试判定后才可能变绿。
- 不要把 `parallel_reads` 调大来掩盖上下文溢出；那不是进度。
