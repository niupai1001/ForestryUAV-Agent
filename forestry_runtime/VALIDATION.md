# 当前验证记录

> 倒序排列，最新在前。历史版本、旧部署和已淘汰架构的记录见 `VALIDATION_ARCHIVE.md`；
> 它们不回填当前评价基线。

## 2026-09-21：四条工程门禁全部接入，林业验证器扩到三题

- `gate.sandbox` 与 `gate.recovery` 是此前缺失的两道门禁，接入后 `gates` 由 `unknown` 变为 `pass`，`engineering.gates` 得到 `score=100.0`、`evidence_coverage=1.0`、4/4 通过。
- `gate.sandbox` 启动一个真实容器并读取**生效的**隔离配置，而不是相信请求的 flag：`NetworkMode=none`、`ReadonlyRootfs=true`、`CapDrop=ALL`（容器内 `CapEff=0`）、`SecurityOpt=no-new-privileges`、`PidsLimit=256`、`Memory=6442450944`、`NanoCpus=4000000000`；容器内无法连外网、无法写根文件系统、可写挂载正常，且 `HOST_BRIDGE_KEY`/`RUNTIME_API_KEY`/`UI_SESSION_KEY`/`OLLAMA_URL` **均未进入作业环境**。Docker 不可用时该门禁报 `unknown`，不报 `pass`。
- `gate.recovery` 驱动真实的 Coordinator/RunStore 路径并断言"上报状态必须来自观察到的执行事实"：取消无法确认时保持 `canceling` 且带 `observation_error` 证据、无 checkpoint 的重启不重放作业并落到 `paused`/`waiting`、四个终态全部拒绝出边。
- 林业验证器增至三题：`forestry.ndvi`（逐像元重算 + 网格/掩码继承 + 上报统计与实测比对）、`forestry.chm`（`DSM - DTM` 逐像元比真值 + 负高程差不得截断 + 缺基准时必须报缺口而非编造栅格）、`forestry.product_qa`（地理坐标系像元单位不得写成米 + 无元数据时不得声明波段角色 + 不得由"文件可读"推断精度）。
- 三题 fixture 均可手算复核：NDVI `[[0.5,0.0],[NaN,1.0]]`；CHM `[[NaN,3,4,2],[3.5,5,6,3],[2,4,-3,2.5],[1,2,2.5,1.5]]`（15 有效、1 负值、max 6.0）；product_qa 为 EPSG:4326、4 波段无描述、18 有效像元。
- 路径约束不变量收敛为单一来源 `shared/paths.py`（Runtime 与 Host Bridge 共用，两侧不得互相 import）。此前 **11 处**调用点用三种写法重复实现同一判断；收敛后一个测试即可覆盖全部调用路径。写测试时发现并修掉一个潜在陷阱：`root in child.parents` 是字典序比较，未解析的 `session/../secrets` 会被误判为"在内"。现有调用方都会先 `resolve()`，因此当时不可利用，但断言已改为显式拒绝含 `..` 的路径。
- 评价端的 `_save_verdict`（4 份副本）收敛到 `verify/base.py`。
- 评价覆盖度：**16 / 43 个 check**；agent 题 4/12，门禁 4/4，浏览器 2/2；有真实记录的槽位 4/46（四个门禁）。

### 更正一条此前的判断

早先的审计记录称 `spectral_RMSE` 是"归一化空间残差、量纲错误"。复核实现后确认**该判断有误**：KD-tree 的 `scale` 只用于邻居查询与距离权重，RMSE 由 `observations - modelled` 在真实反射率空间计算（`runtime/capabilities/prosail/service.py:345-347`）。`PROSAIL_WORKFLOW.md` 将其单位标为 `reflectance_fraction` 是准确的，无需修改。

## 2026-09-20：目标架构阶段 0–6

- 安全与恢复边界已收紧：文件下载使用独立 UI session 身份，API 不再接受隐式默认用户；UAV XMP 读取有界；Run 数据库启用 WAL；取消无法确认时保持 `cancel_incomplete`/非伪终态。
- 工具内核已统一为 `ToolSpec` 注册表，参数 schema、权限、side effect、等价能力与 trace 元数据由同一声明派生。通用工具和遥感能力分别迁入 `runtime/capabilities/<name>/`，旧 `runtime/tools.py` 与单体遥感模块已删除。
- Session 状态机集中在 `runtime/session/state.py`；Run 协调和恢复拆分到 `runtime/session/`；FastAPI 入口与路由拆分到 `runtime/api/`。连续 Turn 不会先把 Run 写成终态再非法重开。
- 评价端已实现 API/工程证据采集、统一 trace、CSV 逐业务键验证、产物 Action 关联、结构化回答验证和 records 证据路径重定位。评价模块有独立测试保证不导入 `runtime`，gold 不由 API collector 读取。
- 首个真实 engineering 证据包位于忽略提交的 `evaluation/work/architecture-20260920/`。`gate.idempotency` 故障注入 2/2 通过；持久化探针中 Input、Turn、Action、Attempt、Job 各 1 条，重复、孤儿关联、多 Attempt 和失败预留残留均为 0，verifier 判定 `pass`。
- `core.csv` fixture、gold、collector 和三个 verifier 已就绪，但本机 `127.0.0.1:8010` 未监听且当前进程没有 Runtime API 凭据，因此没有执行或伪造 `core.csv × 3` 的 `real_model` 记录。恢复部署后按 `evaluation/README.md` 运行。
- React 已拆出 API client、`useRunStream`、事件投影、对话和行动流组件。界面不再固定声称模型为 Qwen，不再把缺少状态的 `done` 事件推断成 completed，工具结果优先使用服务端 `outcome_ok`。
- Playwright 1.55.0 + Chromium 140 已真实执行受控 UI repeat 1：`ui.send` 在延迟确认前绘制待提交气泡并在 503 后恢复输入；`ui.reconnect` 分页合并 10,005 个事件，刷新前后均恢复 completed。两项通过，四个 check 均由 verifier 判定 `pass`；其余 repeat 2/3 未运行，UI 轨道仍不完整。
- `Attempt` 层保留：生产 Action 路径会真实写入、开始和结算 Attempt，且 `gate.idempotency` 已直接核对。旧 `agent_messages_json` 新建列、未调用 transcript、旧输入消费方法、Open WebUI 遗留脚本及 TypeScript 生成物已移除。PROSAIL 工作流已按现行三个工具和证据必填契约重写。

## 当前门禁结果

执行环境：Windows，Python 3.14；前端使用仓库锁定的 Node 依赖。

- `python -m compileall -q runtime host_bridge tests evaluation shared`：通过。
- `python -m pytest -q`：`180 passed, 13 warnings, 91 subtests passed`，耗时 83.85 秒。
- `python -m evaluation.run_baseline --root evaluation/work/baseline --tracks engineering`：`gates=pass`，`engineering.gates=100.0`，覆盖率 1.0；整体 `qualification=incomplete`（其余槽位未测）。
- `python evaluation/scorecard.py`：`qualification=incomplete`，预期退出码 2。
- `npm run build`：TypeScript 应用、Vite 与 E2E 三配置 `noEmit` 检查及 Vite 生产构建通过。
- `npm run test:ui`：真实 Chromium `2 passed`；评价证据包保存在忽略提交的 `evaluation/work/architecture-20260920/ui-browser-repeat1/`。
