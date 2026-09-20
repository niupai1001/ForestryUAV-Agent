# 当前验证记录

## 2026-09-20：目标架构阶段 0–6

- 安全与恢复边界已收紧：文件下载使用独立 UI session 身份，API 不再接受隐式默认用户；UAV XMP 读取有界；Run 数据库启用 WAL；取消无法确认时保持 `cancel_incomplete`/非伪终态。
- 工具内核已统一为 `ToolSpec` 注册表，参数 schema、权限、side effect、等价能力与 trace 元数据由同一声明派生。通用工具和遥感能力分别迁入 `runtime/capabilities/<name>/`，旧 `runtime/tools.py` 与单体遥感模块已删除。
- Session 状态机集中在 `runtime/session/state.py`；Run 协调和恢复拆分到 `runtime/session/`；FastAPI 入口与路由拆分到 `runtime/api/`。连续 Turn 不会先把 Run 写成终态再非法重开。
- 评价端已实现 API/工程证据采集、统一 trace、CSV 逐业务键验证、产物 Action 关联、结构化回答验证和 records 证据路径重定位。评价模块有独立测试保证不导入 `runtime`，gold 不由 API collector 读取。
- 首个真实 engineering 证据包位于忽略提交的 `evaluation/work/architecture-20260920/`。`gate.idempotency` 故障注入 2/2 通过；持久化探针中 Input、Turn、Action、Attempt、Job 各 1 条，重复、孤儿关联、多 Attempt 和失败预留残留均为 0，verifier 判定 `pass`。因为其他 45 个预注册槽位未测，正式 scorecard 仍为 `incomplete`，没有发布 Agent 总分。
- `core.csv` fixture、gold、collector 和三个 verifier 已就绪，但本机 `127.0.0.1:8010` 未监听且当前进程没有 Runtime API 凭据，因此没有执行或伪造 `core.csv × 3` 的 `real_model` 记录。恢复部署后按 `evaluation/README.md` 运行。
- React 已拆出 API client、`useRunStream`、事件投影、对话和行动流组件。界面不再固定声称模型为 Qwen，不再把缺少状态的 `done` 事件推断成 completed，工具结果优先使用服务端 `outcome_ok`。
- Playwright 1.55.0 + Chromium 140 已真实执行受控 UI repeat 1：`ui.send` 在延迟确认前绘制待提交气泡并在 503 后恢复输入；`ui.reconnect` 分页合并 10,005 个事件，刷新前后均恢复 completed。两项测试通过，四个 check 均由 verifier 判定 `pass`；其余 repeat 2/3 未运行，UI 轨道仍不完整。
- `Attempt` 层保留：生产 Action 路径会真实写入、开始和结算 Attempt，且 `gate.idempotency` 已直接核对。旧 `agent_messages_json` 新建列、未调用 transcript、旧输入消费方法、Open WebUI 遗留脚本及 TypeScript 生成物已移除。PROSAIL 工作流已按现行三个工具和证据必填契约重写。

## 当前门禁结果

执行环境：Windows，Python 3.14；前端使用仓库锁定的 Node 依赖。

- `python -m compileall -q runtime host_bridge tests evaluation`：通过。
- `python -m pytest -q`：`132 passed, 9 warnings, 64 subtests passed`，耗时 56.70 秒。
- `python evaluation/scorecard.py`：`qualification=incomplete`，预期退出码 2。
- `npm run build`：TypeScript 应用、Vite 与 E2E 三配置 `noEmit` 检查及 Vite 生产构建通过。
- `npm run test:ui`：真实 Chromium `2 passed`；评价证据包保存在忽略提交的 `evaluation/work/architecture-20260920/ui-browser-repeat1/`。

历史版本、旧部署和已淘汰架构的验证记录见 `VALIDATION_ARCHIVE.md`；它们不回填当前评价基线。
