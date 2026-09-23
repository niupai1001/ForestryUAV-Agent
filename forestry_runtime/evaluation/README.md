# Agent evaluation

框架制作说明见 [BUILD_GUIDE.md](BUILD_GUIDE.md)，包含设计依据、文件职责、计分演算、证据包制作和后续接入步骤。

当前评价协议见 [FRAMEWORK.md](FRAMEWORK.md)，预先登记的任务见
[suite.json](suite.json)。它将真实模型、工程门禁和浏览器交互分为独立轨道；
结果成功率与过程诊断分开，未测项目不默认通过。旧烟测和历史 OpenHands
实验不导入当前基线。

运行离线计分器（不调用模型、不修改运行数据）：

```powershell
python evaluation/scorecard.py
python evaluation/scorecard.py --records evaluation/work/baseline/records.json
python -m pytest -q tests/test_evaluation.py
```

第一条在尚无证据时输出 `incomplete`、`agent_macro_score: null` 和缺失清单，
退出码 2。`scorecard.py` 负责确定性聚合，**不替代领域 verifier、浏览器测试或
真实模型执行器**。suite.json 是待接入独立验证的任务契约，不是已完成验收。

已接入 case 可由一个普通 Python 函数或其 CLI 外壳统一运行，并自动复用已有
`record.json`/`records.json` 断点：

```powershell
python -m evaluation.run_baseline --tracks engineering
```

```python
from pathlib import Path
from evaluation.run_baseline import run_baseline

report = run_baseline(root=Path("evaluation/work/baseline"), tracks=["engineering"])
```

验证器必须以模块形式运行（`python -m evaluation.verify.xxx`），不能直接执行文件；
configuration JSON 必须是**无 BOM 的 UTF-8**，编排器会对 BOM 给出明确错误。工程轨
在缺少 configuration 时会冻结并写入一份本机配置；Agent/UI 轨必须由评价者事先提供
完整冻结配置。

## 浏览器界面

不想用命令行时，用本地可视化界面读分并触发运行：

```powershell
python -m evaluation.dashboard                       # 默认 127.0.0.1:8012，读 evaluation/work/baseline
python -m evaluation.dashboard --root evaluation/work/score-now --port 8012
python -m evaluation.dashboard --run-tracks engineering   # 起服务的同时立即开跑
```

打开 `http://127.0.0.1:8012/` 可以看到：

- **总体判定**：`qualification`、`gates`、`agent_macro_score`、最弱能力组，以及缺证据上下界；
- **能力组**：每组分数、证据覆盖率、通过/计划、未知数；
- **逐题结果**：每个 case 每次重复的 verdict 与原因、得分、覆盖率、是否门禁；
- **缺证据原因**：按原因归并的未测槽位——未测不代表失败，分母不缩减；
- **运行基线**：选轨道后点「运行」，页面轮询进度并自动刷新结果。

界面只用 Python 标准库，不 import `runtime`，也不依赖 Runtime 镜像：Runtime 停着也能
启动它看历史分数。它只读 `--root` 目录内的 `scorecard.json` 与 `records.json`，同一
时刻只允许一次运行（第二次触发会立刻被拒，不会挂起）。

`records.json` 是由评价端采集器生成的数组。每条记录对应一个预先登记的重复
槽位；示例结构如下（下面是格式说明，不是实际验收结果）：

```json
[
  {
    "suite_version": "forestry-eval-0.1",
    "case_id": "core.csv",
    "track": "agent",
    "execution": "real_model",
    "repeat": 1,
    "trial_id": "csv-trial-001",
    "configuration": {
      "code_snapshot": "immutable snapshot including working-tree changes",
      "model_digest": "actual Ollama model digest",
      "prompt_snapshot": "frozen prompt revision",
      "tools_snapshot": "frozen tool definitions and enabled plugins",
      "dataset_version": "frozen suite dataset revision",
      "environment_snapshot": "container digest, dependencies, hardware, warm/cold policy",
      "evaluator_version": "independent verifier revision",
      "sampling": {"thinking": true, "temperature": 0.2, "context": 32768, "output": 8192},
      "budgets": {"model_requests": 12, "wall_seconds": 600}
    },
    "status": "evaluated",
    "status_evidence": {
      "verifier": "termination-v1",
      "evidence": ["csv-trial-001/events.json", "csv-trial-001/verifier.json"]
    },
    "checks": {
      "table": {"verdict": "pass", "verifier": "csv-keys-values-v1", "evidence": ["csv-trial-001/verifier.json"]},
      "artifact": {"verdict": "pass", "verifier": "artifact-download-v1", "evidence": ["csv-trial-001/verifier.json"]},
      "answer": {"verdict": "unknown", "verifier": "claim-review-v1", "evidence": []}
    }
  }
]
```

证据路径相对于 records.json 所在目录，必须是目录内存在的文件；不读取工具
结果中的任意宿主路径。`verifier` 标明独立判定器版本，`verifier.json` 中必须
保存观察值、预期值/版本、断言、证据定位；语义人工复核还记录审核人和分歧。
证据文件存在性只是入口检查，不能验证文件真伪；不得让被评 Agent 写入此目录。

`status` 可为 evaluated / timeout / crash / infra_error / verifier_error。
evaluated 意味着已经验证到契约要求的终态（可包括正确拒绝/安全暂停）。
timeout/crash 需要状态证据才能计已知失败；infra_error/verifier_error 不删除槽位，
显示 unknown。有独立证据的检查失败不会被环境错误遮蔽。契约检查漏项为 unknown；
额外检查、混合配置、错用轨道、重复槽位、第四次尝试替换前三次均拒绝计分。
每个轨道单独冻结一个配置；消融或配置变化另生成 scorecard。

以下为历史辅助材料及 fixture 入口：

This directory contains domain-neutral fixtures and short framework decisions.
`KERNEL_SELECTION.md` retains the completed OpenHands rejection result;
OpenHands code and dependencies have been removed. `OPEN_TERMINAL_ASSESSMENT.md`
records why ordinary Open Terminal work belongs in Open WebUI while durable
Forestry jobs still need caller-controlled action identity.

Create a fresh fixture set with:

```powershell
python evaluation/make_core_fixtures.py --output evaluation/work
```

The fixtures cover mixed-directory inspection, CSV processing, and recovery from
a real Python error. They are used for real Qwen evaluations independent of the
selected UI or execution backend. Generated work directories and JSONL traces
are ignored by Git.

## 已接入的首条证据链

评价端模块不导入 `runtime`。`collect/api.py` 只驱动已部署 API 并保存原始事件、
Turn、产物和规范化 trace；`verify/` 读取证据包和评价端 gold 后独立判定；
`collect/records.py` 最后校验并重定位证据路径。gold 不上传到 Agent Workspace，
API collector 也不读取 gold。

运行 `gate.idempotency` 工程门禁：

```powershell
python -m evaluation.collect.engineering `
  --output evaluation/work/baseline/gate-idempotency --project-root .
python -m evaluation.verify.idempotency_trial `
  --trial evaluation/work/baseline/gate-idempotency `
  --configuration evaluation/work/baseline/configuration-engineering.json `
  --record evaluation/work/baseline/gate-idempotency/record.json
```

采集器在子进程内执行故障注入，随后由评价端直接查询 SQLite，核对
`inputs/Turn/Action/Attempt/Job` 的数量、重复项与孤儿关联。pytest 成功本身不够；
持久化计数也必须满足 verifier 断言。

`core.csv` fixture 位于 `fixtures/core_csv/`，真值位于 evaluator 独享的
`fixtures/gold/core_csv.json`。先冻结完整的 9 项 configuration，再对每个 repeat
使用干净 chat 和独立输出目录：

```powershell
$env:RUNTIME_API_KEY = "<local evaluator credential>"
python -m evaluation.collect.api --base-url http://127.0.0.1:8010 `
  --owner evaluator --case-id core.csv --repeat 1 `
  --prompt-file evaluation/fixtures/core_csv/prompt.txt `
  --fixture evaluation/fixtures/core_csv/input.csv `
  --configuration evaluation/work/baseline/configuration-agent.json `
  --output evaluation/work/baseline/core-csv-1
python -m evaluation.verify.core_csv_trial `
  --trial evaluation/work/baseline/core-csv-1 `
  --gold evaluation/fixtures/gold/core_csv.json `
  --record evaluation/work/baseline/core-csv-1/record.json
```

对 repeat 2、3 重复以上两步，不能挑选最好结果或覆盖既有槽位。随后组装记录：

```powershell
python -m evaluation.collect.records `
  --record evaluation/work/baseline/core-csv-1/record.json `
  --record evaluation/work/baseline/core-csv-2/record.json `
  --record evaluation/work/baseline/core-csv-3/record.json `
  --output evaluation/work/baseline/records.json
python evaluation/scorecard.py --records evaluation/work/baseline/records.json
```

CSV verifier 按 `plot_id` 逐键核对全量记录、字段类型、空值与预注册数值容差；
产物 verifier 要求下载文件关联创建它的 Action；回答检查只接受提示词要求的结构化
事实块，无法机器解析时为 `unknown`，不会凭关键词猜测通过。只有真实模型经部署 API
产生的 Agent 记录才能标记 `execution=real_model`。

浏览器轨道使用真实 Chromium 和受控 API，以便把渲染/重连缺陷与模型波动分开：

```powershell
npx playwright install chromium
python -m evaluation.collect.browser `
  --output evaluation/work/baseline/ui-repeat-1 --project-root .
python -m evaluation.verify.ui_trial `
  --trial evaluation/work/baseline/ui-repeat-1 `
  --configuration evaluation/work/baseline/configuration-ui.json `
  --repeat 1 --output evaluation/work/baseline/ui-repeat-1/records.json
```

`ui.send` 在 POST 被评价端延迟时检查用户气泡已绘制，失败后输入可恢复；
`ui.reconnect` 分页读取 10,005 个事件、按 `seq` 去重并在刷新后恢复真实终态。
Playwright 原始 JSON 报告和测试附件是证据；只运行一次仍只占 repeat 1，不能补齐每题
预注册的三个重复槽位。
