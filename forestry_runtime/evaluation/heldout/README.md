# 保留任务集与三轴对照

这套任务不参与开发。它存在的唯一理由是：**在它上面测到的进步，不能是调它调出来的进步。**

## 组成

| 文件 | 作用 |
| --- | --- |
| `suite.json` | 任务声明：目标、固定输入、产物、验收方式、固定资源与三条对照轴 |
| `make_fixtures.py` | 确定性地生成全部输入与 `gold/`；不复制任何历史实验的数据 |
| `fixtures/<id>/` | 固定输入（GeoTIFF / PNG / CSV）与 `prompt.txt` |
| `gold/<id>.json` | 由同一批数组算出的期望值，可离线复核 |
| `compare.py` | 打印对照矩阵、聚合已采集记录、按单轴归因 |

覆盖的类别：RGB、真正多光谱、未注明波段、缺失波段、缺失文件、环境缺依赖、
草地与树冠混淆、不同产物类型（表 / 图片 / 栅格）、非遥感表格任务。

## 三条轴

| 轴 | 取值 | 说明 |
| --- | --- | --- |
| 模型 | `model-a` / `model-b` | 只换模型，其他两轴不动 |
| Harness | 代码快照 `v1` / `v2` | 工具、提示分层、验收机制；模型与检索不动 |
| 检索 | `none` / `guides` / `corpus` | 不检索、现有指南、项目文献 |

一次对照只能移动一条轴。这是 `compare.py` 强制的：`--compare` 会先读出两条记录
各自记录的轴取值，只有当**恰好一条轴不同**时才给出归因；两条以上不同时明确写成
"不可归因"，并列出移动过的轴。把多轴差异算到某一轴上，是这类实验最常见的错误。

## 复核固定输入是否真的固定

```powershell
python -m evaluation.heldout.make_fixtures     # 重新生成输入与 gold
python -m pytest -q tests/test_heldout_suite.py
```

测试会从磁盘上的 fixture 重算 gold（NDVI 均值、树冠像元数与比例、地区合计、
栅格尺寸与波段描述），所以 gold 与输入不一致时测试会失败，而不是被静默沿用。

## 打印对照矩阵

```powershell
python -m evaluation.heldout.compare --plan `
  --arm '{"name":"m-a|v1|none","model":"model-a","harness":"v1","retrieval":"none"}' `
  --arm '{"name":"m-b|v1|none","model":"model-b","harness":"v1","retrieval":"none"}'
```

输出包含固定资源、三条轴、每个 arm 以及全部 `arm × case × repeat` 单元（默认 3 次重复）。

## 采集与聚合

采集仍走既有评价端：每个单元由 `evaluation.collect.api` 驱动一次已部署的 Runtime，
产物落在 `<root>/<case>-<repeat>/`，其中 `record.json` 是判定结果、`trace.json` 是过程。

```powershell
python -m evaluation.heldout.compare --root evaluation/work/heldout `
  --compare 'm-a|v1|none' 'm-b|v1|none'
```

聚合只读已存在的记录：

- 没有记录的单元不会变成 0 分，只会在 `graded_slots` 与 `total_slots` 的差里显示为缺口；
- 判定为 `unknown` 或 `not_applicable` 的 check 退出分母，不计为失败；
- 指标按"这份 check 是哪种证据"分组：

| 指标 | 取自哪类 check | 回答的问题 |
| --- | --- | --- |
| `method_applicability` | `applicability` / `method` / `precondition` | 所选方法的前提在观察到的输入上成立吗 |
| `artifact_accuracy` | `artifact` / `numeric` / `table` / `zonal` / `mask` / `grid` / `statistics` | 产物与独立算出的答案一致吗 |
| `evidence_citation` | `citation` / `provenance` / `evidence` / `plan` | 判断是否指向本次真正取得的观察或正文 |
| `delivery_success` | 终态 | 是否到达预期终态并交付 |
| `loop_rate` | trace 的步骤 | 模型调用与工具调用次数 |
| `elapsed_seconds` | 记录 / trace | 墙钟耗时 |
| `cost` | trace 的 `token_usage` | 输入与输出 token |

成功率和成本在同一行报告，不分开：用四倍 token 换来的成功并不是显而易见的改进。

## 本环境中没有执行的部分

本机当前没有可用的模型端点，Docker 也未运行，因此**没有**产生任何 `real_model` 记录，
`suite.json` 的 `status` 保持 `declared_not_yet_collected`。上面三条命令中：

- `make_fixtures` 与 `pytest tests/test_heldout_suite.py` 已实际运行并通过；
- `--plan` 已实际运行并产出 54 个单元（2 arm × 9 case × 3 repeat）；
- 采集与三轴归因需要一台已部署且能访问模型的 Runtime；未运行时不得声称任何 arms 的结果。

这些限制写在 `VALIDATION.md` 的对应条目里。
