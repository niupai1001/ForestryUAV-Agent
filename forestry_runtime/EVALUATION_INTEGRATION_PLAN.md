# 评分系统接入与修改计划

> 版本：v1 · 2026-09-19
> 状态：**P1 已完成并提交（`b57593a`）；P2 门禁已全部接入；P4.1 已完成**。剩余见文末「实施进度」
> 依据：`evaluation/{FRAMEWORK.md, suite.json, BUILD_GUIDE.md, README.md}` + 本会话实机核验
> 目标：给出评分系统的明确调用方式、`run_baseline.py` 设计与分阶段修改计划

---

## 实施进度（滚动更新 · 2026-09-19）

| 阶段 | 状态 | 实测结果 |
|---|---|---|
| **P1 入口 + 门禁接入** | ✅ 完成（`b57593a`） | `run_baseline.py`、`cases/registry.py`、`Case.harness/repeats`、D1 已修、`gate.permissions` 接入 |
| **P2 接完 4 个门禁** | ✅ 完成 | **4/4 pass，`gates=pass`，`engineering.gates=100.0`，覆盖率 1.0** |
| **P4.1 `forestry.ndvi`** | ✅ 完成 | fixture + 3 个独立 verifier + 8 个试点测试（含 4 个反例）+ 注册 |
| **P4.2 `forestry.chm`** | ⬜ 待做 | — |
| **P3 `exec/paths.py`** | ⬜ 待做 | 5 处重复仍在 |
| **P2.3 `run_store.py` 拆分** | ⬜ 待做 | 仍 931 行在顶层 |
| **P5 收尾** | 🔶 部分 | 已完成：破损脚本已删、`tsconfig.node.json` 已加 `noEmit`、生成物已清、前端 3 处伪造状态已修、两份文档状态行已更正。待做：`VALIDATION.md` 重构、`PROSAIL_WORKFLOW.md` 重写、`exec/context` 归位 |

**当前护栏**：`147 passed + 64 subtests`；`tsc --noEmit` exit 0；`vite build` 成功；无生成物污染源码树。

**当前覆盖度**：已实现 check **7 / 43**；有真实记录槽位 **4 / 46**（4 个门禁）。

---

## 0. 核验基线（全部实测，非文档引用）

| 项目 | 实测结果 |
|---|---|
| 测试套件（P1 前） | **132 passed + 64 subtests**，53s |
| 测试套件（当前） | **147 passed + 64 subtests** |
| `compileall` | exit 0 |
| `scorecard.py`（无记录） | exit 2，`qualification=incomplete`，`agent_macro_score=null`，45 个缺证据槽位 |
| **首次真实出分** | `gate.idempotency` → **score=100.0, evidence_coverage=1.0** |
| **工程轨完整出分** | 4 门禁全 pass → `engineering.gates=100.0`，`gates=pass` |
| suite 契约规模 | **18 case / 43 check / 46 槽位** |
| 已实现 check | **4 / 43（9%）**：`csv.compare_by_business_key`、`provenance.artifact_links_to_action`、`text.claims_match_table`、`gates.exactly_once` |
| 已完成 case 绑定 | **1 / 18**（`core.csv`） |
| 已完成门禁接入 | **1 / 4**（`gate.idempotency`） |
| 边界合规 | `evaluation/` 不 import `runtime`（grep 零命中，有测试守护） |
| 现有评分 CLI 入口 | **9 个** |

### 已跑通的完整证据链（P1 将复用它）

```powershell
python -m evaluation.collect.engineering --output <out> --project-root .
python -m evaluation.verify.idempotency_trial --trial <out> --configuration <cfg> --record <out>/record.json
python -m evaluation.collect.records --record <out>/record.json --output <base>/records.json
python evaluation/scorecard.py --records <base>/records.json
```

采集器的 8 项独立断言全部通过：

```
fault_tests_passed True   all_links_resolve True    one_input True    one_turn True
one_action True           one_attempt True          one_job True      failed_reservation_rolled_back True
```

### 核验中发现的 2 个调用陷阱（必须写进文档，避免重踩）

1. **必须用模块形式 `python -m evaluation.verify.xxx`**，不能用脚本形式 `python evaluation/verify/xxx.py`。
   原因：`evaluation/verify/*.py` 使用相对导入（`from .gates import ...`），脚本形式抛
   `ImportError: attempted relative import with no known parent package`。
2. **configuration 文件必须是无 BOM 的 UTF-8**。PowerShell 5.1 的 `Set-Content -Encoding UTF8`
   会写入 BOM，导致 `json.loads` 抛 `Unexpected UTF-8 BOM`。用 Python 写或用 `utf8NoBOM`。

---

## 1. 评分系统怎么调用

### 1.1 三个入口，层次分明

```
┌────────────────────── 在线半（需被测系统在跑）──────────────────────┐
│  collect/   驱动 Runtime / 跑故障注入 / 开浏览器，存原始证据            │
│  verify/    读证据 + 读 gold，独立判 pass / fail / unknown            │
└──────────────────────────────────────────────────────────────────────┘
                              ↓ records.json + 证据包
┌────────────────────── 离线半（完全独立）────────────────────────────┐
│  scorecard.py   纯 JSON 进、JSON 出。不启模型、不连网、不碰 runtime    │
└──────────────────────────────────────────────────────────────────────┘
```

| 入口 | 命令 | 何时用 |
|---|---|---|
| **离线计分** | `python evaluation/scorecard.py [--records <path>]` | 随时可跑。给证据包出分；不给则看覆盖度与缺失清单 |
| **三条轨道采集** | `python -m evaluation.collect.{engineering,api,browser}` | 需要证据时。engineering 不需要模型；api 需要已部署 Runtime；browser 需要 Playwright |
| **验证出记录** | `python -m evaluation.verify.{idempotency_trial,core_csv_trial,ui_trial}` | 采集之后，产出可计分的 record |
| **组装** | `python -m evaluation.collect.records` | 多条 record 合成 `records.json` 并重定位证据路径 |

### 1.2 退出码语义（只表达"测量状态"，不表达"系统好不好"）

| 退出码 | `qualification` | 含义 |
|---|---|---|
| 0 | `measured_unqualified` | 测完、门禁过，但**本版无校准过的发布线**，故不宣布"合格" |
| 1 | `blocked` | 某门禁有**已验证的失败**，优先修它 |
| 2 | `incomplete` | 证据未齐（46 槽位未填满）——**不是失败，是还没测** |

这是 `FRAMEWORK.md` §5 的设计：**不设 80 分线**，因为合格线需按真实失败成本校准。

### 1.3 三条轨道的完整调用（现状）

**工程门禁轨**（不需模型，最快）

```powershell
python -m evaluation.collect.engineering --output evaluation/work/baseline/gate-idempotency --project-root .
python -m evaluation.verify.idempotency_trial `
  --trial evaluation/work/baseline/gate-idempotency `
  --configuration evaluation/work/baseline/configuration-engineering.json `
  --record evaluation/work/baseline/gate-idempotency/record.json
python -m evaluation.collect.records --record evaluation/work/baseline/gate-idempotency/record.json --output evaluation/work/baseline/records.json
python evaluation/scorecard.py --records evaluation/work/baseline/records.json
```

**Agent 轨**（需已部署 Runtime + 真模型；每题每 repeat 一次干净 chat）

```powershell
$env:RUNTIME_API_KEY = "<evaluator 凭证>"
python -m evaluation.collect.api --base-url http://127.0.0.1:8010 `
  --owner evaluator --case-id core.csv --repeat 1 `
  --prompt-file evaluation/fixtures/core_csv/prompt.txt `
  --fixture evaluation/fixtures/core_csv/input.csv `
  --configuration evaluation/work/baseline/configuration-agent.json `
  --output evaluation/work/baseline/core-csv-1
python -m evaluation.verify.core_csv_trial --trial evaluation/work/baseline/core-csv-1 `
  --gold evaluation/fixtures/gold/core_csv.json `
  --record evaluation/work/baseline/core-csv-1/record.json
```

**UI 轨**（真实浏览器）

```powershell
npx playwright install chromium
python -m evaluation.collect.browser --output evaluation/work/baseline/ui-repeat-1 --project-root .
python -m evaluation.verify.ui_trial --trial evaluation/work/baseline/ui-repeat-1 `
  --configuration evaluation/work/baseline/configuration-ui.json --repeat 1 `
  --output evaluation/work/baseline/ui-repeat-1/records.json
```

### 1.4 现状的问题（本计划要解决的）

| 问题 | 后果 |
|---|---|
| 46 个槽位要手工敲 4 步 × N 次 | 路径约定全靠 README 讲 |
| 路径写错 → 产出**假 `unknown`** | 看起来像"证据没齐"，实际是路径问题，会误导判断 |
| 只有 CLI，没有可调用 API | 无法被其他代码、定时任务或 UI 复用 |
| 三个 trial 的 `verify_trial` 签名不一致 | 编排器需写 if/else 特例 |

---

## 2. 目标：一个入口 + 一个可调用 API

```
                        evaluation/run_baseline.py
                    ┌──────────────────────────────┐
   方式 A（代码）    │  run_baseline(...) -> dict    │  ← 普通 Python 函数
   方式 B（命令行）  │  python -m evaluation.run_baseline │  ← 仅 A 的 argparse 外壳
                    └───────────────┬──────────────┘
                                    │ 逐 case、逐 repeat
             ┌──────────────────────┼──────────────────────┐
             ▼                      ▼                      ▼
        agent 轨              engineering 轨            ui 轨
     collect.api          collect.engineering      collect.browser
             │                      │                      │
             ▼                      ▼                      ▼
   verify.core_csv_trial   verify.idempotency_trial    verify.ui_trial
             └──────────────────────┼──────────────────────┘
                                    ▼
                        collect.records.assemble（已有）
                                    ▼
                        scorecard.scorecard()（已有，纯函数）
```

**只有一个新文件做编排，其余全是已有函数的直接调用。不引入框架、不引入基类。**

### 2.1 非 CLI 调用方式

非 CLI 的本质是"把逻辑写成普通函数，argparse 只是外壳"：

```python
from pathlib import Path
from evaluation.run_baseline import run_baseline

report = run_baseline(
    root=Path("evaluation/work/baseline"),
    tracks=["engineering"],          # None = 全部三条轨道
    cases=["gate.idempotency"],      # None = 全部已注册 case
    repeats={1},                     # None = 按 suite.json 预注册次数
)
print(report["qualification"], report["agent_macro_score"])
```

同一个函数服务三种消费者：

| 消费者 | 用法 |
|---|---|
| 人工调试 | `python -m evaluation.run_baseline --tracks engineering` |
| 其他 Python 代码 | `from evaluation.run_baseline import run_baseline` |
| 未来 UI / 定时任务 | 调同一函数 |

**不在第一版加 HTTP 端点**，理由见 §4。

---

## 3. `run_baseline.py` 的最小设计

### 3.1 `Case` 补 2 个字段

现状：`evaluation/cases/base.py` 的 `Case` 有 `fixture`/`gold` 字段，但**当前无任何编排代码读取**（仅 `test_evaluation_pipeline.py` 校验其 check 名与 `suite.json` 一致）。它是为编排预留但未消费的结构，因此补字段即可，不新建结构。

```python
@dataclass(frozen=True)
class Case:
    id: str
    track: str
    group: str
    checks: dict[str, Verifier]
    expected_terminal: str
    fixture: str
    gold: str
    harness: str = "agent"          # 新增：用哪个采集器
    repeats: int | None = None      # 新增：None = 读 suite.json
```

### 3.2 注册表：`cases/registry.py`（纯字典）

```python
from .core_csv import CASE as CORE_CSV

# 已接入独立 verifier 的 case；未注册的落到 unknown（scorecard 已能正确表达）
CASES = {case.id: case for case in (CORE_CSV,)}

# 门禁的验证入口是固定文件，不需要 Case 对象（无 fixture/prompt/gold）
GATE_VERIFIERS = {
    "gate.idempotency": ("evaluation.verify.idempotency_trial", "verify_trial"),
}
```

**18 题不必现在全注册**：未实现的自然落到 `unknown` 并留在预定分母内，符合 `FRAMEWORK.md` §5「未知不从分母删除」。

### 3.3 主流程

```python
def run_baseline(*, root, tracks=None, cases=None, repeats=None) -> dict:
    suite = _load_suite()
    configs = {t: _load_config(root, t) for t in ("agent", "engineering", "ui")}
    records = []
    for case, repeat in _slots(suite, tracks, cases, repeats):
        trial = root / f"{case['id'].replace('.', '-')}-{repeat}"
        if (trial / "record.json").exists():
            records.append(trial / "record.json")      # 断点续跑
            continue
        collect(case, trial, configs[case["track"]])   # 按 track 分派
        verify(case, trial, configs[case["track"]], repeat)
        records.append(trial / "record.json")
    assembled = root / "records.json"
    assemble(records, assembled)
    report = scorecard(suite, json.loads(assembled.read_text(encoding="utf-8")), root)
    (root / "scorecard.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
```

**四个设计点，每个都必要**：

| 点 | 为什么必须 |
|---|---|
| 按 `case["track"]` 分派 | 三条轨道采集方式本质不同（HTTP / 子进程+SQLite / Playwright），不统一就要三个编排器 |
| 断点续跑 | Agent 轨每题 ×3 次真模型调用，中断重跑代价高 |
| 复用 `records.assemble` | 已存在（含 `_rebase` 证据路径校验），不重写 |
| 复用 `scorecard` | 已存在且验证过，`run_baseline` **不重复计分逻辑** |

### 3.4 一处必要的签名统一

实测三个 trial 签名不一致：

```python
verify.core_csv_trial.verify_trial(trial, gold)              -> dict
verify.idempotency_trial.verify_trial(trial, configuration)  -> dict
verify.ui_trial.verify_trial(trial, configuration, repeat)   -> list[dict]
```

若不统一，编排器需为每题写特例——那正是"多余封装"的开端。最小改法：给 `core_csv_trial.verify_trial` 增加**可选**参数 `configuration=None`，三者统一为 `(trial, configuration, repeat)`。

代价：1 模块 3 行。收益：编排器零 if/else。

---

## 4. 明确**不**做的事

| 不做 | 理由 |
|---|---|
| 不重写 9 个已有 CLI | 各自可用（有 `__main__` guard），`run_baseline` 直接调其内部函数。重写是造轮子 |
| 不建 harness 基类 / 抽象接口 | 只有 3 种采集方式，字典分派足够；基类在此是纯开销 |
| 不新建独立评价服务进程 | 多一个进程要管；第一版纯 Python API 足够 |
| 不在 runtime 加评分端点 | 会引入 runtime → evaluation 反向耦合，且 runtime 重启影响评价。真要 UI 时再加约 20 行薄端点（读 `records.json` → `scorecard` → JSON），不影响本设计 |
| 不重新实现计分 | `scorecard.py` 已实现且 9 个测试守着 |
| 不重新实现证据路径重定位 | `records.py:_rebase` 已实现且已测 |
| 不引入十维加权总分 / 发布线 | `FRAMEWORK.md` §1/§5 已明确排除 |
| 不做任务簇（cluster）机制 | `FRAMEWORK.md` §5 提到，但**仅在扩展题量变体时才需要**。现在 18 题独立，加了是过早优化。P4 扩多题变体时再补 |

---

## 5. 分阶段修改计划

每阶段结束的护栏（与 `ARCHITECTURE_TARGET.md` §6 一致）：

```powershell
python -m compileall -q runtime host_bridge tests evaluation
python -m pytest -q                          # 132 passed + 64 subtests，不许减少
python -m evaluation.run_baseline --tracks engineering   # P1 之后可用
```

---

### P1 · 入口 + 门禁接入（本轮建议做）

| # | 内容 | 复用什么 |
|---|---|---|
| 1.1 | `Case` 补 `harness` / `repeats` 两字段 | 已有 `cases/base.py` |
| 1.2 | 新建 `cases/registry.py`（纯字典：1 个 agent 题 + 门禁映射） | — |
| 1.3 | 统一 trial 签名（`core_csv_trial` 加可选 `configuration`） | 已存在模块 |
| 1.4 | 新建 `evaluation/run_baseline.py`：`run_baseline()` + argparse 外壳 | 直接调 `collect.*` / `verify.*` / `scorecard` |
| 1.5 | 写 `gate.permissions` 采集器 + verifier | 复用 `tests/test_host_bridge.py` 已有的 confinement 测试 |
| 1.6 | 修 D1：`/files` 保持会话引用直到流式发送完成 | 实现在 `runtime/api/routes/assets.py` 的 `HeldFileResponse`（`sessions.hold()` 是上下文管理器，无法覆盖路由返回之后的发送） |
| 1.7 | 加测试：断点续跑 + 路径约定 + 非 CLI 调用 | 已有 `test_evaluation_pipeline.py` |

**为什么 1.5 与 1.6 同批**：`gate.permissions` 的判据含"授权不扩张 + 源只读"，而 D1 会让权限测试不稳定。

D1 的具体缺陷曾位于 `runtime/api/routes/assets.py`（重构前）：

```python
@router.get('/files/{chat_id}/{asset_id}')
def local_file(chat_id: str, asset_id: str, owner: str = Depends(api.identity)):
    store = api.sessions.acquire(owner, chat_id)
    try:
        asset = store.get(asset_id, owner)
        path = store.path(asset_id, owner)
    finally:
        api.sessions.release(chat_id)          # ← 在 FileResponse 开始流式之前就释放
    return FileResponse(path, ...)
```

`finally` 在 `return FileResponse(...)` **之前**执行，而 `runtime/lifecycle.py` 的回收协程每 2 秒调用一次 `reap()`，会在活跃计数为 0 时 `rmtree` 会话目录 → **大文件下载可能中途被删**。正确做法是保持会话引用直到流式发送完成。实现上**不能用 `sessions.hold(chat_id)`**——`FileResponse` 的发送发生在路由返回之后，没有可用的 `async with` 钩子；实际采用 `HeldFileResponse.__call__`，在 Starlette 完成（或中止）发送后的 `finally` 中释放引用。效果等价，且不引入额外抽象。

先修再接，门禁一次接上就是**真实 pass**，而非先红后绿（长期红色会让人麻木，失去信号价值）。

**实测数字变化**：

| 指标 | 现在 | P1 后 |
|---|---|---|
| 已实现 check | 4 / 43 | 5 / 43 |
| 有真实记录的槽位 | 1 / 46 | 2 / 46 |
| 单入口 `run_baseline` | ❌ | ✅ |
| 非 CLI 调用方式 | ❌ | ✅ |

---

### P2 · 接完 4 个门禁

| # | 内容 | 前置 |
|---|---|---|
| 2.1 | `gate.recovery` 采集器 + verifier | 复用新增 `tests/test_session_state.py` |
| 2.2 | `gate.sandbox` 采集器 + verifier（断言 `docker inspect` 实态与 `ToolSpec.scope` 一致） | **需 Docker**；不可用时必须报 `unknown`，**不得报 `pass`** |
| 2.3 | 修 D2：`run_store.py`（931 行）拆入 `runtime/store/` | — |

**为什么门禁优先于领域题**：门禁只需故障注入 + SQLite 查询 + `docker inspect`，**不需要真模型、不需要无人机数据、不需要浏览器**。是唯一低成本可产出客观判定的部分，且按 `FRAMEWORK.md` §5 任一失败即 `blocked` —— 4 个槽位，最高杠杆。

---

### P3 · 抽 `exec/paths.py`（门禁能否成立的前提）

| # | 内容 |
|---|---|
| 3.1 | 收敛重复 **5 处**的 containment 检查为 `exec/paths.py` 单一函数 |
| 3.2 | `exec/grants.py` 读 `ToolSpec.scope` 做决策（而非按工具名分支） |
| 3.3 | `gate.permissions` 断言打到 `exec/paths.py` + `exec/grants.py` |

实测的 5 处副本（行号为 2026-09-19 核验）：

```
runtime/workspace.py:205          if target != root and root not in target.parents
runtime/storage.py:78             if path != self.root and self.root not in path.parents
runtime/storage.py:134            if target != self.root and self.root not in target.parents
host_bridge/server.py:57          if target != root and root not in target.parents
host_bridge/server.py:243         if path != self.data_root and self.data_root not in path.parents
```

**5 份副本、两套错误类型、零共享测试。** 门禁要验"越权被拒"，若逻辑有 5 份，门禁无法保证覆盖全部调用路径。**这不是重构洁癖，是门禁无法成立的技术原因。**

同时：`ToolSpec`（`kernel/spec.py`）已含 `Scope`/`SideEffect`/`equivalence_group`/`verification`，但**目前无任何消费方**。`exec/` 是它的第一个真实消费者；无消费者则声明会腐化。

---

### P4 · 林业领域 verifier（最大价值）

按「能从现有测试的合成数据生成 fixture」排序，成本从低到高：

| 顺序 | case | 复用 |
|---|---|---|
| 1 | `forestry.ndvi` | `test_runtime.py` 已有合成 fixture（red/nir 手算 → mean 0.5） |
| 2 | `forestry.chm` | `test_forest_structure.py` 已有合成 CHM |
| 3 | `forestry.product_qa` | 需合成 GeoTIFF（`make_core_fixtures.py` 有 rasterio 生成能力） |
| 4 | `core.paths` / `core.repair` / `core.changed_input` | 逻辑部分已在 `test_generic_runtime.py` |
| 5 | `knowledge.*` | 需冻结知识源 + gold 段落 |
| 6 | `ui.send` / `ui.reconnect` | `verify/ui_trial.py` 已有 verifier 逻辑，缺 Playwright 采集 |

**为什么这个顺序**：前两项 fixture 可从现有测试的合成数组直接生成，**不需真实无人机数据**；且它们恰好覆盖审计发现的科学缺陷（波段角色缺失放行、`vertical_reference` 零验证、`spectral_RMSE` 量纲）。

---

### P5 · 收尾与第二次全量测量

- D3：前端 3 处伪造服务端状态（`|| 'new'`、`'未验证'`）——影响 `ui` 轨道判定
- D4：`exec/`、`context/` 层归位
- 阶段 6：重构 `VALIDATION.md`（261 行，含过期 88/85 数字）、重写 `PROSAIL_WORKFLOW.md`（七项配置与实现不符）、删破损脚本（`scripts/update_openwebui_pipe.py`、`scripts/smoke_preview.py` import 不存在的模块）、修 `frontend/vite.config.js` 遮蔽手写 `.ts`
- 更新 `ARCHITECTURE_TARGET.md` 状态行（现仍写"尚未修改任何代码"，与实际严重不符）
- 跑满 46 槽位 → 第一份完整 scorecard，`agent_macro_score` 首次出现真实值
- 提交（`.idea/` 加 `.gitignore`；确认 `uav_prosail_baseline/` 是否入库）

---

## 6. 为什么是这个顺序

> **先让评分系统能看见，再改系统。**

现状 `agent_macro_score = None`、45/46 槽位空 —— **任何系统改进都无法被证明有效**。而 4 个门禁是唯一已具备"客观、可重复、不需要模型和无人机数据"三性质的验证，接线成本最低。先吃下这 4 个，就有了一个**能对改动说"是/否"的裁判**；然后修裁判抓出的缺陷，再逐题扩到林业领域。

### 可量化提升

| 指标 | 现在 | P1 后 | P3 后 |
|---|---|---|---|
| 已实现 check | 4 / 43 (9%) | 5 / 43 (12%) | 7 / 43 (16%) |
| 有真实记录的槽位 | 1 / 46 | 2 / 46 | 4 / 46 |
| `qualification` | `incomplete` | `incomplete` | `blocked` 或向 `measured_unqualified` 推进 |
| `agent_macro_score` | `None` | `None` | P4 后才出现 |

---

## 7. 待确认

1. **P1 是否现在开做？** 范围见 §5 P1（1.1–1.7）。
2. **Docker 当前是否可用？** `gate.sandbox` 需 `docker inspect` 实态证据。若不可用，按"报 `unknown` + 记录环境证据"实现，等 Docker 可用时再实跑。
3. **非 CLI 形态确认**：第一版给**纯 Python 函数**（`from evaluation.run_baseline import run_baseline`）是否足够？还是需要在 P1 顺带在 runtime 加薄 HTTP 端点（约 20 行，`GET /evaluation/scorecard` 读 `records.json` 返回 JSON）以便接 workbench 按钮？

---

## 8. 相关文件

- `evaluation/FRAMEWORK.md` — 评价协议（三轨道、固定分母、门禁独立、不设发布线）
- `evaluation/suite.json` — 18 题契约（43 check / 46 槽位）
- `evaluation/BUILD_GUIDE.md` — 框架制作说明与后续接入步骤
- `evaluation/README.md` — 运行命令与证据包格式
- `evaluation/scorecard.py` — 离线计分（已实现，本计划不修改）
- `ARCHITECTURE_TARGET.md` — 目标架构设计（分层、ToolSpec、Trace Schema）
