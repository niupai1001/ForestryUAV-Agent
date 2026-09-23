# Forestry Agent Runtime — 目标架构设计

> 状态：**实施完成（有取舍）**。阶段 0–6 已落地并提交；`context/` 层与 `run_store.py` 的进一步拆分**经评估后不做**，理由见文末「实施结论」
> 版本：v1 draft · 2026-09-19
> 适用范围：`forestry_runtime/` 的结构重构，以及 `evaluation/` 评价系统的接入
> 前置材料：`evaluation/FRAMEWORK.md`（评价协议 v0.1）、`evaluation/BUILD_GUIDE.md`、用户提供的研究报告
> 实施进度与验收数字另见 `VALIDATION.md`；分阶段计划见 `EVALUATION_INTEGRATION_PLAN.md`

---

## 0. 本文要解决的问题

本设计同时服务两个需求，而这两个需求是**同一件事的两面**：

- **需求 A**：把项目改成「简单、干净、职责明确、分工清楚」的框架，像 Claude Code / DeepSeek Harness 那样。
- **需求 B**：在外部接上观测评分系统，客观看到能力缺在哪、哪里能提升。

`evaluation/FRAMEWORK.md` 已经完成了需求 B 的**规则层**（三轨道、固定分母、门禁独立、`unknown` 保留、不设发布线），`scorecard.py` 已实现确定性聚合，`test_evaluation.py` 的 9 个测试守住了计分语义。**规则层不需要改。**

断点在规则层和 runtime 之间：`scorecard.py` 只吃 `records.json`，而生成它的采集器、独立 verifier、以及可被消费的 trace 都不存在。`FRAMEWORK.md` §8 自己写明「本版未接入」。

**本设计的核心论断：**

> 需求 A 的根因不是「文件太大」，而是**同一个能力的事实被切成五份且无一致性保障**。
> 需求 B 的瓶颈不是「缺计分规则」，而是**缺一个能被过程指标消费的 trace**。
> 而这两者由同一个东西解决：**一个能力 = 一个目录 = 一份声明（ToolSpec）**。

---

## 1. 现状诊断

### 1.1 根因：能力知识的多副本

以 `code_run` 为例，「这个能力是什么」的事实散落在五处：

| # | 位置 | 内容 |
|---|---|---|
| 1 | `runtime/tools.py:110-115` | Pydantic 参数 schema（`CodeRunArgs`） |
| 2 | `runtime/tools.py:168` | 给模型看的英文描述 |
| 3 | `runtime/tools.py:626-640` | 实际实现 `code_run()` |
| 4 | `host_bridge/server.py:241-292` | 真实约束（CPU/内存/PID/网络/只读） |
| 5 | `evaluation/FRAMEWORK.md` §4 | 判定标准 |

**没有任何机制保证五者一致**，而且已经出现了可观测的后果：

- `GENERIC_DEFINITIONS["code_run"]` 的描述声称 `network is disabled`，但 `host_bridge` 对 `kind=install` 是联网的（`server.py:255`）。模型读到的描述与真实行为**不同源**。
- `attempts` 表生产数据 **194 actions / 0 attempts**：因为「登记一次尝试」有两个实现路径（`run_store.begin_action` 会插 attempt，`append` 的 `tool_start` 投影不插），生产走的是后者。
- `job_refs` 的三份 upsert（`run_store.py:627-639`、`:682-698`、`:699-713`）已经漂移：用 `bool(result.get("ok"))` 与 `event.get("ok")` 决定同一个 `actions.state`。

**这三处不是三个 bug，是同一个 bug 的三次显形：没有单一声明点。**

### 1.2 规模与热点

| 文件 | 行数 | 实际问题 |
|---|---|---|
| `runtime/remote_sensing.py` | 2611 | 36 个方法覆盖 7 类关注点（栅格 IO ~498 行、林分结构 ~657 行、PROSAIL ~352 行、UAV 适配 ~174 行、NDVI ~143 行、ZIP ~87 行、派发 ~245 行）。**职责即「所有遥感能力」，等于没有职责。** |
| `runtime/run_store.py` | 849 | 9 张表的 DDL + 迁移全写在 `__init__`；`job_refs` upsert 写三遍 |
| `runtime/tools.py` | 739 | 14 个工具的参数类 + 描述 + 实现 + 派发 + 观察去重 + 执行记录 DB |
| `runtime/agent.py` | 710 | PydanticAI 适配 + 生命周期 hook + 上下文编译调用 + 事件翻译 + `SYSTEM` 提示 |
| `runtime/runs.py` | 580 | `_drive` 单方法 180 行，混了 claim/resume/stream/seal/reconcile/requeue |
| `runtime/app.py` | 500 | 9 类关注点；模块级单例 + 导入即副作用；手写 ASGI 中间件 |
| `frontend/src/App.tsx` | 612 | 单组件；20 个 `useState`；**伪造服务端事实**（`state:'running'`、`'未验证'`、`'new'`） |

### 1.3 必须保住的东西（不可回归）

| 资产 | 约束 |
|---|---|
| **85 个 runtime 测试** | `tests/` 除 `test_evaluation.py` 外的 9 个文件：`test_generic_runtime` 41、`test_tool_recovery` 17、`test_runtime` 8、`test_memory` 7、`test_uav_audit` 4、`test_lifecycle` 3、`test_run_api` 2、`test_host_bridge` 2、`test_forest_structure` 1；阶段迁移中必须持续绿灯 |
| **9 个计分器测试** | `tests/test_evaluation.py`；`test_repository_suite_has_frozen_coverage_without_backfilled_results` 冻结了 **18 cases / 46 planned slots** |
| **合计基线** | `94 passed + 4 subtests passed`（2026-09-19 实测，39s） |
| **`suite.json` 的 18 个 case 与 checks 键名** | `scorecard.py:120` 校验 `set(checks) - set(case["checks"])` 必须为空；**check 名是接口，不可改** |
| **事件流的分页语义** | `run_store.event_page` 的 `seq` 单调性；`ui.reconnect` 依赖 10,005 事件完整分页 |
| **`Action/Job/Artifact/Step/checkpoint` 命名** | `suite.json` 的 `core.csv`、`core.changed_input`、`gate.idempotency`、`knowledge.context` 的验收条款**直接引用这些标识符** |
| **`evaluation/results/` 历史记录** | `FRAMEWORK.md` §2 定的规矩：标 legacy，不回填、不删除 |

最后两条是本设计最硬的约束：**评分系统已经引用了 runtime 的内部标识符，所以这些名字是公开契约，重构时不能自由改名。**

---

## 2. 目标目录结构

### 2.1 分层原则

四条边界，任何一行代码只能属于一层：

1. **kernel 不知道林业。** 没有 `rasterio`/`prosail`/`numpy` 导入。
2. **capabilities 不知道 HTTP / SQLite / Docker。** 不 import `fastapi`、`sqlite3`、`subprocess`。
3. **store 不做决策，只做存储。** 不含状态转移规则。
4. **evaluation 不 import runtime。** 只读产物，进程独立。

### 2.2 结构

```text
forestry_runtime/
├── runtime/
│   ├── kernel/                     # 内核：无业务、无领域依赖、可独立单测
│   │   ├── spec.py                 ★ ToolSpec：能力的唯一声明
│   │   ├── registry.py             ToolRegistry：声明 → 注册表 + 模型 schema
│   │   ├── protocol.py             参数校验 / 失败分类 / 结果包装（现 tool_protocol.py）
│   │   ├── errors.py               错误分层 Precondition / Execution / Domain
│   │   ├── events.py               事件类型与 EventSink 协议
│   │   └── trace.py                统一 Trace Schema 的构造与规范化
│   │
│   ├── exec/                       # 执行：权限与沙箱边界
│   │   ├── sandbox.py              容器生命周期与资源限额（现 host_bridge job 部分）
│   │   ├── grants.py               授权/撤销/覆盖判定（现 workspace.py）
│   │   ├── paths.py                ★ 路径规范化与越界检查（现重复 5 处）
│   │   └── bridge.py               Host Bridge 客户端（现 workspace.BridgeClient）
│   │
│   ├── store/                      # 持久化：只存储
│   │   ├── schema.py               ★ 全部 DDL + 迁移集中一处（现散在 5 个 __init__）
│   │   ├── runs.py                 RunStore
│   │   ├── events.py               事件流 + 分页 + 保留策略
│   │   └── steps.py                Step / Checkpoint 关联
│   │
│   ├── session/                    # 会话：生命周期与恢复
│   │   ├── state.py                ★ 状态机：合法转移表 + 终态守卫
│   │   ├── coordinator.py          RunCoordinator（现 runs.py）
│   │   ├── recovery.py             重启恢复 / monitor / cancel
│   │   └── lifecycle.py            会话资产生命周期（现 lifecycle.py）
│   │
│   ├── context/                    # 上下文
│   │   ├── compiler.py             ContextCompiler
│   │   ├── budget.py               token 预算与压缩策略
│   │   ├── memory.py               项目记忆
│   │   └── knowledge.py            索引 + FTS5/向量检索
│   │
│   ├── capabilities/               # ★ 能力插件：一个能力一个目录
│   │   ├── __init__.py             发现与装载
│   │   ├── fs/{tool.py,service.py}
│   │   ├── code_run/{tool.py,service.py}
│   │   ├── dependency_install/{tool.py,service.py}
│   │   ├── artifacts/{tool.py,service.py}
│   │   ├── knowledge_search/{tool.py,service.py}
│   │   ├── raster/                 ← 从 remote_sensing.py 拆出
│   │   ├── uav_audit/
│   │   ├── forest_structure/
│   │   └── prosail/
│   │
│   ├── api/                        # HTTP：只传输
│   │   ├── app.py                  组装 + lifespan
│   │   ├── deps.py                 identity / session store 依赖
│   │   ├── models.py               请求/响应模型
│   │   └── routes/
│   │       ├── sessions.py  runs.py  assets.py
│   │       └── workspace.py  projects.py  health.py
│   │
│   └── agent.py                    PydanticAI 适配（保留在顶层，它是内核的用户）
│
├── host_bridge/                    宿主机进程（唯一持有 Docker 控制权与宿主密钥）
│   ├── server.py                   HTTP 路由 + 鉴权
│   ├── docker_jobs.py              容器参数构造（现 server.py:229-343）
│   └── fsops.py                    宿主文件操作（现 server.py:129-230）
│
├── evaluation/                     ★ 评价端：独立进程，绝不 import runtime
│   ├── FRAMEWORK.md  suite.json  BUILD_GUIDE.md  README.md    （已完成，保留）
│   ├── scorecard.py                                          （已完成，保留）
│   ├── collect/                    采集：记录发生了什么
│   │   ├── api.py                  驱动已部署 Runtime 跑一个 case
│   │   ├── browser.py              真实浏览器观测（Playwright）
│   │   ├── engineering.py          pytest / 故障注入
│   │   └── trace.py                事件流 → 统一 Trace
│   ├── verify/                     判断：独立验证器，一 check 一函数
│   │   ├── base.py                 Verdict / Evidence / verifier 协议
│   │   ├── csv.py  raster.py  chm.py  inventory.py  rag.py  ui.py
│   │   └── gates.py                permissions / sandbox / idempotency / recovery
│   ├── cases/                      编排：case_id → fixture + verifier + 期望终态
│   ├── fixtures/                   输入与 gold（Agent 不可读）
│   │   └── gold/                   隐藏答案，运行时不挂载
│   └── results/                    原始证据包（已 gitignore）
│
└── frontend/src/
    ├── components/                 拆出：MessageList / TracePanel / Sidebar / Artifacts
    ├── hooks/useRunStream.ts       轮询 + 事件合并 + 重连（从 App.tsx 抽出）
    ├── api/client.ts               统一 request 封装
    └── App.tsx                     只做布局装配
```

### 2.3 与现状的对应（迁移映射）

| 现文件 | 目标 | 方式 |
|---|---|---|
| `runtime/tool_protocol.py` | `kernel/protocol.py` | 移动 |
| `runtime/domain_registry.py` | `capabilities/__init__.py` | 重写为插件发现 |
| `runtime/workspace.py` | `exec/grants.py` + `exec/paths.py` + `exec/bridge.py` | 拆分 |
| `runtime/storage.py` | `store/assets.py` | 移动 |
| `runtime/run_store.py` | `store/runs.py` + `store/events.py` + `store/schema.py` | 拆分 |
| `runtime/runs.py` | `session/coordinator.py` + `session/state.py` + `session/recovery.py` | 拆分 |
| `runtime/context.py` | `context/compiler.py` | 移动 |
| `runtime/memory.py` | `context/memory.py` + `context/knowledge.py` | 拆分 |
| `runtime/tools.py` | `capabilities/*/` | 逐个迁出后删除 |
| `runtime/remote_sensing.py` | `capabilities/{raster,uav_audit,forest_structure,prosail}/` | 拆分后删除 |
| `runtime/uav_audit.py` | `capabilities/uav_audit/` | 移动 |
| `runtime/lifecycle.py` | `session/lifecycle.py` | 移动 |
| `runtime/app.py` | `api/` | 拆分 |
| `runtime/agent.py` | `agent.py` | 就地收敛 |
| `host_bridge/server.py` | `host_bridge/{server,docker_jobs,fsops}.py` | 拆分 |

**最终 `runtime/` 顶层只剩 `agent.py`，其余全部归层。**

---

## 3. ToolSpec 契约

### 3.1 设计目标

一份声明同时派生四样东西，**改一处即四处同步**：

```text
                    ┌──────────────────────────────┐
                    │  ToolSpec（唯一声明）          │
                    └──────────────┬───────────────┘
         ┌───────────────┬─────────┴────────┬──────────────────┐
         ▼               ▼                  ▼                  ▼
  ①模型可见 schema  ②权限决策        ③trace 元数据      ④evaluation 必查项
   registry            exec.grants       kernel.trace       verify/ 骨架
```

### 3.2 定义

```python
# runtime/kernel/spec.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Literal, Sequence
from pydantic import BaseModel


class SideEffect(str, Enum):
    """副作用等级。决定 trace 记录、门禁判定与重放策略。"""
    NONE = "none"              # 纯观察：fs_list / fs_read / inspect_*
    FILE_WRITE = "file_write"  # 写 Workspace 或授权宿主文件
    DURABLE_JOB = "durable_job"  # 提交后台作业，有 job_id，需对账
    EXTERNAL = "external"      # 提交到 Runtime 之外（预留给未来的 MCP）


class Scope(BaseModel):
    """最小权限声明。exec.grants 据此判定，不依赖工具名。"""
    reads: list[Literal["workspace", "asset", "source"]] = ["workspace"]
    writes: list[Literal["workspace", "source_file"]] = []
    network: Literal["none", "registry_only"] = "none"
    host_path_args: list[str] = []   # 哪些参数可能携带宿主绝对路径


@dataclass(frozen=True)
class ToolSpec:
    # ---- 身份 ----
    name: str                          # 模型可见名，稳定，不可随意改
    description: str                   # 只此一份，registry 用它生成 schema

    # ---- 契约 ----
    params: type[BaseModel]            # 参数模型，只此一份
    returns: str                       # 产出类型名，见 §3.4

    # ---- 权限与副作用 ----
    scope: Scope
    side_effect: SideEffect

    # ---- 评价对接 ----
    equivalence_group: str             # 功能等价的工具归一组（研究文档"三层匹配"的第 2 层）
    verification: str | None = None    # 对应 evaluation/verify/*.py 里的 verifier 名

    # ---- 装载 ----
    plugin: str = "core"               # 归属插件，用于开关
    keywords: tuple[str, ...] = ()     # 延迟加载的领域词（现 domain_registry 的 keywords）
    deferred: bool = False             # 是否默认不加载 schema

    # ---- 实现 ----
    handler: Callable | None = None    # service 侧函数；registry 只绑定不调用

    def model_schema(self) -> dict:
        """模型可见 schema。复现现有 inline_schema() 的展开行为。"""
```

### 3.3 四个派生通道的细节

**① 模型可见 schema** — 保持现有行为不变。

现状：`runtime/agent.py:331` 用 `inline_schema(model.model_json_schema())` 展开 `$ref`（因为 Ollama 不认嵌套 `$ref`）。新设计把它移到 `registry.py`，**展开算法一字不改**（`kernel/protocol.py` 保留 `inline_schema`），确保 `test_tool_recovery.py:53-77` 与测试里断言 schema 字符数的用例不回归。

关键不变式：**`visible_count` 与 `visible_schema_chars` 必须与现状一致**。测试 `test_tool_recovery.py:300-318` 断言了工具数量，`agent.py:362-365` 计算字符数并写入 Context 清单。

**② 权限决策** — 从「工具名硬编码」改为「读 Scope」。

现状 `runtime/tools.py:465-486` 的 `_grant()` 按 `access` 字符串分支；`host_bridge/server.py:110-119` 的 `verify_grant` 只看 `access` 字段（**这是上一轮审计的 P0-2：HMAC 自签，撤销无效**）。

新设计：`exec/grants.py` 接收 `ToolSpec.scope` 与实参，产出 `GrantDecision`，并**在 bridge 侧改为按 grant_id 查服务端记录**（阶段 0 已修）。

**③ trace 元数据** — 事件里带上声明信息。

现状 `tool_start` 事件只有 `{action_id, attempt_id, name, arguments}`（`run_store.py:578-582`）。新设计加 `spec` 摘要：

```json
{"type":"tool_start", "action_id":"...", "name":"code_run",
 "arguments":{...},
 "spec":{"side_effect":"durable_job","equivalent":"execute_code",
         "scope":{"reads":["workspace","source"],"writes":["workspace"],
                  "network":"none"},"plugin":"core"}}
```

**新增字段，不改旧字段。** `scorecard.py` 不读 runtime 事件，所以对已完成的规则层透明。

**④ evaluation 必查项骨架** — `verification` 字段把工具接到 verifier。

现状：`suite.json` 的 `checks` 键名与任何代码无关联。新设计下，`collect/api.py` 可以在断言「这个 case 用了哪些工具」时，用 `equivalence_group` 做功能等价匹配，而不是精确工具名匹配。

### 3.4 产出类型（typed artifact）

研究文档 §「Artifact 应是一等类型」的落地。现状 `store/assets.sqlite3` 有 `artifact_kind` 列（`storage.py:35`），已支持 `file` / `job-output` / `sealed-output`——**方向是对的，只需扩展成类型系统**：

```python
ARTIFACT_TYPES = {
    "TextArtifact":      {"suffixes": {".txt", ".md", ".json", ".csv"}, "verifier": "text"},
    "TableArtifact":     {"suffixes": {".csv", ".tsv"},                 "verifier": "csv"},
    "RasterArtifact":    {"suffixes": {".tif", ".tiff", ".vrt"},        "verifier": "raster"},
    "ImageArtifact":     {"suffixes": {".png", ".jpg", ".jpeg"},        "verifier": "image"},
    "ModelArtifact":     {"suffixes": {".npz"},                         "verifier": "lut"},
    "VectorArtifact":    {"suffixes": {".geojson", ".shp"},             "verifier": "vector"},
    "EvidenceArtifact":  {"suffixes": None,                             "verifier": "provenance"},
}
```

`registry` 用 `spec.returns` + 实际后缀解析类型，写进 `artifact_kind`。`evaluation/verify/` 按类型分发——这正是 GISclaw 的做法（不同工件不同 verifier）。

---

## 4. Trace Schema

### 4.1 设计原则

1. **事件流是唯一事实来源。** 不新增「trace 数据库」；`store/events.py` 已经是 append-only、`seq` 单调、可分页的。trace 是**对事件流的规范化视图**，不是第二份存储。
2. **向后兼容。** 只加字段，不删不改既有字段名。历史 `events.jsonl` 仍可读。
3. **`seq` 是主键。** 分页、去重、断线重连全部依赖它（`ui.reconnect` 的 10,005 事件验收）。

### 4.2 事件类型与必填字段

| 事件 | 现有字段 | 需新增 | 服务的评价指标 |
|---|---|---|---|
| `run_state` | `state`, `state_version`, `recovery_reason` | `from_state` | `gate.recovery` |
| `user_message` | `content`, `input_id`, `queued` | `received_at` | `ui.send`（POST→气泡延迟） |
| `model_call` | `number`, `step_id`, `context`, `estimated_tokens` | `input_tokens`, `output_tokens` | 效率指标 |
| `tool_start` | `action_id`, `attempt_id`, `name`, `arguments` | **`spec`**（§3.3③）, `attempt_ordinal` | 全部过程指标 |
| `tool_end` | `action_id`, `ok`, `outcome_ok`, `duration_seconds`, `result` | `failure.stage`, `failure.code`, `artifacts` | 失败分类、Provenance |
| `job_reconciled` | `job_id`, `job_type`, `state`, `terminal`, `exit_code` | `container_id`, `resource_limits` | `gate.sandbox` |
| `done` | `state`, `artifacts`, `usage` | — | 终态判定 |
| **新增** `permission_decision` | — | `action_id`, `decision`, `basis`, `scope` | `gate.permissions` |
| **新增** `compaction` | — | `tier`, `tokens_before`, `tokens_after` | `knowledge.context` |

### 4.3 失败分类映射

`suite.json` 与 `FRAMEWORK.md` §7 定义了 15 个失败标签：

```text
ui_state / input_checkpoint / duplicate_side_effect / permission / tool_protocol /
path_grounding / data_semantics / algorithm_numeric / retrieval / unsupported_claim /
context_loss / premature_stop / resource / infrastructure / verifier
```

现状 `tool_protocol.execution_failure`（`tool_protocol.py:107-132`）产出 `failure.stage ∈ {preconditions, execution}` 与 `code`。**映射表放在 `kernel/protocol.py`**，是纯函数，可单测：

```python
FAILURE_TAXONOMY = {
    ("preconditions", "invalid_arguments"):          "tool_protocol",
    ("preconditions", "source_path_not_found"):      "path_grounding",
    ("preconditions", "known_invalid_source_path"):  "path_grounding",
    ("agent_control",  "duplicate_failed_call"):     "premature_stop",
    ("recovery",       "action_outcome_unsettled"):  "input_checkpoint",
    ("execution",      "*"):                         "algorithm_numeric",
    ("preconditions",  "not_ready"):                 "data_semantics",
}
```

未命中记 `unknown`——**不武断归因**，这是 `FRAMEWORK.md` §7 的明确要求。

### 4.4 统一 Trace 视图

`evaluation/collect/trace.py` 把事件流规范化成研究文档建议的 schema。**它在评价端，不在 runtime**（保持 evaluator 不 import runtime）：

```json
{
  "run_id": "run_...", "turn_id": "turn_...", "agent_run_id": "agent_...",
  "case_id": "core.csv", "repeat": 1,
  "configuration": { "code_snapshot": "...", "model_digest": "...", ... },
  "steps": [
    {"step": 1, "seq": 42, "tool": "fs_read",
     "equivalent_group": "read_file", "side_effect": "none",
     "permission": "workspace_read",
     "args_normalized": {"path": "sales.csv", "max_lines": 200},
     "status": "success", "latency_ms": 143, "artifact_ids": []}
  ],
  "artifacts": [], "citations": [], "token_usage": {}, "safety_events": [],
  "terminal_state": "completed", "checkpoint_state": "complete"
}
```

`configuration` 的 9 个字段沿用 `scorecard.py:16-20` 的 `CONFIG_FIELDS`，**保证采集器产出的 records 能直接喂给已完成的计分器**。

---

## 5. 评分系统接入

### 5.1 目录职责（严格一对一）

| 文件 | 单一职责 | 明确不做 |
|---|---|---|
| `collect/api.py` | 驱动已部署 Runtime 跑一个 case，保存**原始**事件与产物 | 不做判定、不读 gold |
| `collect/browser.py` | 真实浏览器观测，记时间戳与 DOM | 不判定 UI 质量 |
| `collect/engineering.py` | 调 pytest / 故障注入，存 SQLite 实态 | 不判分 |
| `collect/trace.py` | 事件流 → §4.4 Trace | 不改事件、不补造字段 |
| `verify/<type>.py` | 按 check 名判 `pass/fail/unknown`，附证据路径 | 不重跑模型、不看回答措辞 |
| `scorecard.py` | 聚合已验证记录 | **已完成，不改** |
| `cases/<id>.py` | 声明 fixture / verifier 绑定 / 期望终态 | 不实现判定逻辑 |

### 5.2 Verifier 协议

```python
# evaluation/verify/base.py
@dataclass
class Verdict:
    verdict: Literal["pass", "fail", "unknown"]
    verifier: str          # "raster.py@v1"
    evidence: list[str]    # 相对证据包根目录的路径
    detail: str = ""

def verify(check_name: str, *, trace: dict, artifacts: Path,
           gold: Path, params: dict) -> Verdict: ...
```

**关键约束（来自 `scorecard.py:41-52` 的 `_proof`）**：
- `evidence` 必须是**相对路径**且文件真实存在于证据包内
- `verifier` 必须是非空字符串
- 缺任一项，检查降级为 `unknown`，**分母不减**

### 5.3 check 名到 verifier 的绑定（18 题 × check 名）

`suite.json` 的 check 名是散文条款的键，必须在 `cases/<id>.py` 里一一绑定。以三题为例：

```python
# evaluation/cases/core_csv.py
CASE = Case(
    id="core.csv", track="agent", group="core",
    checks={
        "table":    Verifier("verify/csv.py", fn="compare_by_business_key"),
        "artifact": Verifier("verify/provenance.py", fn="artifact_links_to_action"),
        "answer":   Verifier("verify/text.py", fn="claims_match_table"),
    },
    expected_terminal="completed",
    fixture="fixtures/core_csv/",
    gold="fixtures/gold/core_csv.json",
)
```

`table` 的实现必须遵守 `FRAMEWORK.md` §4 的明文要求：**「不能只验行数或相关系数」**——所以 `compare_by_business_key` 逐业务键比对记录、类型、空值、数值。

### 5.4 首批接入建议

**先做 `core.csv`**，理由：
- fixture 小（`make_core_fixtures.py` 已有 CSV 生成能力）
- 真值可手算（`FRAMEWORK.md` §4 的自然判据）
- 三轨道都能覆盖：agent（真跑）、engineering（幂等检查）、ui（气泡可见）
- **一个 case 打通 = 整条链验证完毕**，其余 17 题是复制模式

---

## 6. 分阶段迁移计划

### 通用规则

每个阶段结束时必须同时满足：

```powershell
python -m compileall -q runtime host_bridge tests evaluation
python -m pytest -q                          # 94 passed + 4 subtests —— 不许减少
python evaluation/scorecard.py               # qualification=incomplete, 退出码 2
```

**任一不满足即回退该阶段。** 不做「先全改完再一起测」。

> 护栏数字说明：`94` 是 2026-09-19 实测基线。注意 `VALIDATION.md:8/:19/:27` 里的 `85`/`88` 已过期——**重构成因之一就是文档数字与代码脱节**，所以护栏以实测为准，不以文档为准。

---

### 阶段 0 — 止血（与重构解耦，先做）

| # | 修复 | 位置 |
|---|---|---|
| 1 | `/files/{chat_id}/{asset_id}` 加鉴权，owner 用真实身份；**且**用保持会话引用的响应类包住流式发送 | `api/routes/assets.py` |
| 2 | bridge `verify_grant` 改为校验服务端 grant 记录 | `host_bridge/server.py:110-119` |
| 3 | 会话凭证与 `RUNTIME_API_KEY` 分离；去掉 `x-user-id` 默认 `'local'` | `app.py:87,192-214` |
| 4 | `_read_xmp` 改流式读（XMP 在 APP1 段，读前若干 KB） | `uav_audit.py:41-46` |
| 5 | `authorize_latest_request` 改精确匹配 + 系统目录黑名单 | `workspace.py:368-390` |
| 6 | `runs.sqlite3` 开 WAL + `synchronous=NORMAL` | `store/schema.py` |
| 7 | `canceling` 加超时预算与终态出路 | `session/recovery.py` |
| 8 | `await runs.reconcile()` 加错误边界 | `api/app.py:50-52` |

**为什么先做**：这 8 条里有 3 条是 `gate.permissions` 和 `gate.sandbox` 的直接判据。不修，评分系统跑出来的门禁永远是 `blocked`，采集再多也没有意义。

> 实施记录（2026-09-19）：第 1 条的流式释放无法用 `sessions.hold()` 上下文管理器实现——
> `FileResponse` 的发送发生在路由返回之后，没有可用的 `async with` 钩子。实际采用
> `runtime/api/routes/assets.py` 里的 `HeldFileResponse.__call__`，在 Starlette 完成
> （或中止）发送后的 `finally` 中释放引用。效果等价，且不引入额外抽象。

**验证**：85 个 runtime 测试绿灯（84 基线 + 阶段 0 新增状态/安全测试）；手工验证 `/files` 无凭证返回 401；`gate.permissions` 的 fixture 能跑出 `pass`。

---

### 阶段 1 — 框架内核（立声明点与 trace）

**新建**：`kernel/{spec,registry,protocol,errors,events,trace}.py`

**兼容策略（关键）**：**不删 `tools.py`**，而是让 `GENERIC_DEFINITIONS` 从 `ToolSpec` 生成：

```python
# 临时适配层，阶段 2 结束后删除
from .kernel.registry import build_registry
from .capabilities import load_specs

_SPECS = load_specs()                       # 14 个 ToolSpec
_REGISTRY = build_registry(_SPECS)
GENERIC_DEFINITIONS = _REGISTRY.as_legacy_definitions()   # 保持现有 dict 形状
```

这样 `tools.py:402-423` 的 `execute()`、`agent.py:355-386` 的 `_tools()`、所有测试**全部不用改**。

**同时**：`tool_start` 事件开始附带 `spec` 摘要（§3.3③）。新增字段不影响任何断言。

**验证**：
- 94 个测试全绿
- `_REGISTRY.as_legacy_definitions()` 与旧 `GENERIC_DEFINITIONS` **逐键 diff 为空**（写一个一次性断言脚本，跑完即删）
- `visible_schema_chars` 与改造前一致

---

### 阶段 2 — 能力迁移（一个目录一个能力）

顺序按依赖从小到大：

| 顺序 | 能力 | 源 | 难点 |
|---|---|---|---|
| 1 | `fs` | `tools.py:30-108, 488-601` | 观察去重键 `observation_key` |
| 2 | `artifacts` | `tools.py:138-145, 697-734` | 预览产出的资产登记 |
| 3 | `knowledge_search` | `tools.py:148-159, 363-400` | 依赖 `context/memory.py` |
| 4 | `code_run` + `dependency_install` | `tools.py:110-119, 603-644` | `ExecutionRecords` 应移入 store |
| 5 | `job_status` + `job_cancel` | `tools.py:666-695` | 与 `session/recovery.py` 共享 |

每迁完一个：删掉 `tools.py` 里的旧副本 → 跑测试。**`tools.py` 单调递减到 0 行后删除。**

**验证**：每步 94 测试绿灯；每步 `git diff --stat runtime/tools.py` 必须是负增长。

---

### 阶段 3 — 拆 `remote_sensing.py`（工作量最大）

| 目标插件 | 从哪来 | 顺带修的缺陷 |
|---|---|---|
| `capabilities/raster/` | `inspect_file`, `inspect_raster`, `preview_image`, ZIP, 栅格 IO | `_read_xmp` 已在阶段 0 修 |
| `capabilities/uav_audit/` | `uav_audit.py` + `inspect_uav_*` | 波段角色缺失强制化 |
| `capabilities/forest_structure/` | `build_canopy_height_model`, `delineate_tree_candidates`, `summarize_forest_structure` | `vertical_reference` 实际校验 |
| `capabilities/prosail/` | `simulate_prosail`, `build_prosail_lut`, `invert_prosail` | `spectral_RMSE` 量纲；LUT 反射率范围断言 |

**`DEFINITIONS`（`remote_sensing.py:353-453`）的 101 行描述文本原样保留** —— 它们已经是给模型的契约，且被 `test_tool_recovery.py` 的延迟加载测试间接覆盖。

**验证**：94 测试绿灯；`python -c "import runtime.api.app"` 在 `REMOTE_SENSING_PLUGINS_ENABLED=false` 下**不导入 `rasterio`/`prosail`/`scipy`**（`test_generic_runtime.py:438-457` 已断言此点）。

---

### 阶段 4 — 拆 HTTP 与会话

**4a. `api/`**：`app.py` 按资源拆成 `routes/*`；模块级单例移入 `lifespan`；**所有端点加 response model**（现在 API 直接暴露 SQLite schema，泄漏 `owner`、`managed_path`、`sha256`、绝对路径）。

**4b. `session/`**：`runs.py` 的 `_drive`（180 行）拆成 `coordinator.py` + `state.py` + `recovery.py`：

```python
# session/state.py —— 单点化状态机
TERMINAL = {"completed", "failed", "canceled", "cancel_incomplete"}
ACTIVE   = {"queued", "running", "waiting", "canceling"}
PAUSABLE = {"queued", "running"}

TRANSITIONS = {
    "queued":    {"running", "canceling", "canceled"},
    "running":   {"waiting", "paused", "completed", "failed", "canceling"},
    "waiting":   {"running", "paused", "canceled", "canceling"},
    "paused":    {"queued", "canceling"},
    "canceling": {"canceled", "cancel_incomplete", "running"},  # ← 阶段的出路
    # TERMINAL 无出边 —— 终态守卫
}

def transition(current: str, target: str) -> None:
    if current in TERMINAL:
        raise AssetError(f"{current} is terminal and cannot change to {target}")
    if target not in TRANSITIONS[current]:
        raise AssetError(f"Illegal transition {current} -> {target}")
```

这**一次解决四个问题**：状态字面量 4 处重复、`waiting` 的双重矛盾准入、`canceling` 无出路、`_drive` 覆写终态。

**验证**：94 测试绿灯；新增状态机单测（合法/非法转移、终态守卫）；`gate.recovery` 的「取消未确认时保持 canceling」不能回归。

---

### 阶段 5 — 接入评分系统

**5a.** 建 `collect/trace.py` + `collect/api.py`，跑第一个工程门禁 `gate.idempotency`（最易验证：注入故障 → 查 `inputs`/`Turn`/`Action`/`Attempt`/`Job` 关联计数）。

**5b.** 建 `verify/csv.py` + `cases/core_csv.py` + `fixtures/core_csv/`。

**5c.** 端到端跑 `core.csv` × 3 次，产出第一份真实 `records.json`，喂给 `scorecard.py`。

**这里必须能回答三个问题**（`BUILD_GUIDE.md` §一 的研发问题）：
1. 系统在哪些任务上能正确交付？→ 逐 case 逐 repeat 结果
2. 失败发生在哪个环节？→ `FAILURE_TAXONOMY` 聚合
3. 一次改动是否在相同条件下改善？→ 同 `configuration` 的配对比较

**5d.** 补 `frontend/` 的 `ui.send` / `ui.reconnect` 采集：先拆 `App.tsx`（删掉 UI 发明的事实），再接 Playwright。**顺序不能反**——UI 自己伪造状态会让 ui 轨道判定失真。

---

### 阶段 6 — 收尾

| # | 内容 |
|---|---|
| 1 | 删除遗迹：`scripts/update_openwebui_pipe.py`、`scripts/smoke_preview.py`（import 不存在的模块）；重写或删除 `PROSAIL_WORKFLOW.md`（七项配置全错） |
| 2 | 删死代码：`agent_messages_json` 列、`RunStore.transcript()`、`mark_inputs_consumed()`；**决定 `attempts` 层去留**（要么修好生产路径真写，要么删除并同步改 `suite.json` 的 `gate.idempotency`） |
| 3 | 修 `frontend/tsconfig.node.json` 加 `noEmit`，删生成的 `vite.config.js`/`.d.ts`/`*.tsbuildinfo`（**当前它遮蔽手写的 `.ts`**） |
| 4 | 加 CI：`compileall` + `pytest` + `tsc --noEmit` + `vite build` |
| 5 | 重构 `VALIDATION.md`：倒序只留有效事实，历史移 `VALIDATION_ARCHIVE.md`，修正 88/85 → 94 |
| 6 | **把当前版本提交**（48 个未跟踪文件含全部核心模块与依赖锁） |

---

## 7. 关键设计决策与风险

### 7.1 为什么是「原地渐进」而不是「新仓库重写」

你选择了原地渐进。我同意，理由是具体的：**`suite.json` 的验收条款已经引用了 runtime 的内部标识符**（`Action`/`Job`/`Artifact`/`Step`/`checkpoint`/`inputs`/`Turn`/`Attempt`）。这些名字现在是公开契约。新仓库重写必须同时重建这整套语义，等于把 18 题的契约作废——而 `FRAMEWORK.md` §2 刚立了「旧记录不删、标 legacy」的规矩。

### 7.2 最大风险：阶段 1 的兼容层

`GENERIC_DEFINITIONS` 是 dict，`agent.py:355-386` 和多个测试直接依赖它的形状（`{name: (Model, description)}`）。**兼容层必须产出逐键相同的 dict**，否则会大面积回归。

**缓解**：阶段 1 结束时跑一次性 diff 断言（新旧 dict 逐键比对），确认后才进阶段 2。

### 7.3 第二个风险：`suite.json` 的 46 槽位不是「已实现」

`suite.json` 的 `status` 是 `pilot_contracts_verifiers_pending`，`fixture` 字段是**散文描述**而非真实 fixture 路径。`BUILD_GUIDE.md` §3.2 明说「目前 fixture 字段说明需要什么输入，尚不代表该题全部样本和采集器已制作完成」。

**所以阶段 5 不是「接上」，而是「实现 18 题的 fixture + verifier」**，这是独立于重构的一整块工作量。设计只负责把接口留对。

### 7.4 不做的事（明确排除）

| 排除项 | 理由 |
|---|---|
| 十维加权总分（25/15/15/…） | `FRAMEWORK.md` §1 已明确「不直接采用」。研究文档把它列为建议而非已验证 |
| 70/80/90 发布线 | 同上；`FRAMEWORK.md` §5 明确「当前不设 80 分发布线」 |
| 专家轨迹 embedding 相似度 | `FRAMEWORK.md` §7 明确「不评分」 |
| LLM judge 直接给总分 | `FRAMEWORK.md` §4 只允许它「提取候选声明或辅助标注」 |
| 引入 MCP / 新 Agent 内核 | `FRAMEWORK.md` §8「不添加 Agent 内核、模型循环或生产管理后台」 |
| 让 evaluator import runtime | 违反 §2.1 边界 4；会破坏「采集/判断/聚合分离」 |

### 7.5 需要补的机制（`FRAMEWORK.md` 提到但 `scorecard.py` 未实现）

**任务簇**：`FRAMEWORK.md` §5 要求「同一个基础任务的改写/扰动属于同一任务簇，先簇内平均，再组内平均」，但 `scorecard.py` 无 `cluster` 字段。**扩展题量前必须补**，否则加近似题会改变分组权重——这正是 `BUILD_GUIDE.md` §八 的警告。

建议在 `suite.json` 的 case 上加 `"cluster": "core.csv"` 字段，`scorecard.py` 的 `_summary` 前先做簇内聚合。**这会改动已完成文件，需单独一个阶段并同步更新 `test_evaluation.py`。**

---

## 8. 一页速览

```text
目标：一个能力 = 一个目录 = 一份声明
      ↓ 派生
①模型 schema  ②权限决策  ③trace 元数据  ④evaluation 必查项

分层：kernel(无业务) → exec(边界) → store(只存储) → session/context → capabilities → api
评价：evaluation/ 独立进程，只读产物，绝不 import runtime

阶段 0  止血 8 条           ← 门禁判据，必须先修
阶段 1  kernel + ToolSpec   ← 兼容层，94 测试不许动
阶段 2  迁移 14 个能力       ← tools.py 单调递减到删除
阶段 3  拆 remote_sensing   ← 2611 行 → 4 个插件
阶段 4  拆 app.py / runs.py ← HTTP + 状态机单点化
阶段 5  接评分系统           ← 先 gate.idempotency，再 core.csv
阶段 6  删遗迹 / CI / 提交版本

每阶段护栏：compileall + 94 passed + scorecard 仍 incomplete
```

---

## 9. 待你确认的三个问题

1. **阶段 0 与阶段 1 是否合并执行？** 阶段 0 是纯止血（8 个独立修复），阶段 1 是框架内核。两者无耦合，但阶段 0 能让评分系统尽快产出有意义结果。
2. **阶段 3 拆 `remote_sensing.py` 时，是否顺带修域层 6 个缺陷？** 顺带修成本最低（同一批文件、同一批测试），但会让该阶段变大。
3. **`attempts` 层去留**：生产 0 行、`suite.json` 的 `gate.idempotency` 引用了它。是修好生产路径让它真写，还是删除并同步改契约？
---

## 10. 实施结论（2026-09-21）

### 已完成

| 设计目标 | 结果 |
|---|---|
| **阶段 0 止血** | 8 项全部落地。D1 的流式释放用 `HeldFileResponse.__call__` 而非 `sessions.hold()`——后者是上下文管理器，覆盖不到路由返回之后的发送 |
| **阶段 1 kernel** | `kernel/{spec,registry,protocol,errors,events,trace}.py` 建成，`ToolSpec` 从一份声明派生 schema / 权限 / trace 元数据 |
| **阶段 2 能力迁移** | 14 个能力全部迁入 `capabilities/<name>/`；`tools.py`(739) 与 `remote_sensing.py`(**2611**) 已删除 |
| **阶段 3 拆单体遥感** | 拆成 `raster`(929) / `prosail`(383) / `forest_structure`(542) / `uav_audit`(760) |
| **阶段 4 HTTP + 会话** | `app.py` 500→249；6 个 `routes/*`；`session/{coordinator,recovery,state}.py`，状态机含终态守卫与 `cancel_incomplete` 出路 |
| **阶段 5 评分系统** | 4/4 门禁接入（`gates=pass`）；4/12 agent 题有独立 verifier；`run_baseline()` 可代码调用；**16/43 check** |
| **阶段 6 收尾** | 破损脚本已删、`tsconfig.node.json` 补 `noEmit`、生成物已清、前端 3 处伪造状态已修、`VALIDATION.md` 重构为倒序 + 归档、`PROSAIL_WORKFLOW.md` 已按现行三工具重写 |
| **路径不变量单点化** | 11 处内联比较收敛为 `shared/paths.py` 一个定义，Runtime 与 Bridge 共享 |

### 经评估后不做，并说明原因

**`run_store.py` 不再按 `runs/events/schema` 三分。** 设计原文假设它承担"状态机 + 事件流 + 建表"。实际核查后：

- **状态机已经独立**：`session/state.py` 持有 `TERMINAL`/`ACTIVE`/`TRANSITIONS`/`transition()`，且 `set_state`/`request_cancel`/`finish_cancel`/`finish_cancel_incomplete` 全部经由它守卫（取消路径是嵌套调用，不是绕过）。
- **建表与迁移已抽出**：`store/schema.py`，且现在有 6 个测试 + 20 subtests 直接覆盖，包括"旧库原地升级不丢行"。
- **剩余的是数据访问方法**，共 46 个，全部围绕 `runs.sqlite3` 的事务。继续拆成 `store/events.py` 只会把事务边界摊到多个文件，而当前没有任何消费者需要"单独的事件流存储"。
- `store/executions.py`（作业记录）已独立，因为它有独立的库与生命周期。

结论：**为外观一致性拆分事务代码，风险大于收益。** 该文件的行数来自表数量和 9 张表的 CRUD，不是内聚性问题。

**`context/` 层不建。** `context.py`(62 行) + `memory.py`(622 行) 合计 684 行，分属"每次请求的上下文编译"与"项目记忆 + 知识检索"两件事，边界清楚且都已被 `runtime/` 顶层命名表达。为两个文件建一层目录不增加可读性。若将来 `memory.py` 的知识检索部分继续增长（例如加入重排或增量索引），再按功能而非按层拆分。

### 与本设计文档的偏差

1. **`shared/paths.py` 是新增的，原设计没有。** 设计把不变量放在 `runtime/exec/paths.py`，但 Host Bridge 是独立宿主进程、不能 import `runtime`。实测还发现两侧各自持有一份会让 `is_within is is_within` 为 `False`（两个模块对象），即"单一来源"名存实亡。最终落在中性 `shared/` 包，Dockerfile 两个 target 都复制它。`exec/` 只保留这一个重导出模块，`exec/grants.py` 与 `exec/sandbox.py` 未单独建——授权判定仍在 `workspace.py`，容器参数构造仍在 `host_bridge/server.py`，两者都已被 `gate.permissions` 与 `gate.sandbox` 真实覆盖。
2. **`_save_verdict` 的收敛不在原设计内**，是实施中发现的 4 份副本。
3. **`InputPathMapper._within` 是第 11 处重复**，原设计统计为 10 处。