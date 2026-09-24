# A 组 / B 组 能力对照报告

- A 组（通用文件/代码能力）：`evaluation\work\arm-a`
- B 组（相同通用能力 + 林业遥感工具与按需指南）：`evaluation\work\arm-b`
- 配对槽位：36 / 36，未配对 0
- 冻结配置一致：**True**
- 证据可对照：**True**

本报告不给出单一总分。五个维度分开汇报，因为它们的成因与修法都不同：
工程可靠性、任务正确性、判断质量、可复现性、时间/Token 成本。

## 一、工程可靠性

| | 已评分 | 覆盖率 | 状态分布 | 终态分布 | 模型调用 | 工具调用 |
|---|---|---|---|---|---|---|
| A 通用 | 36/36 | 100% | crash 15, evaluated 21 | canceled 2, completed 21, failed 5, paused 8 | 499 | 582 |
| B 领域 | 36/36 | 100% | crash 10, evaluated 26 | completed 26, failed 1, paused 9 | 405 | 404 |

`crash` 指 Run 被 Runtime 主动暂停（模型调用预算耗尽或重复失败调用被拦截），
是 Agent 侧结果；`infra_error` 指 Runtime 不可达，不计入能力。

## 二、三、任务正确性与判断质量

每个「案例 × 条件」一行，单元格为 `pass / fail / unknown / not_applicable` 计数。

| 案例 × 条件 | A 组 | B 组 | 配对 | A/B 逐次差异 |
|---|---|---|---|---|
| `capability.chm@gap` | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 | 3/3 | — |
| `capability.chm@normal` | 1 / 2 / 0 / 0 | 3 / 0 / 0 / 0 | 3/3 | r2: A=fail B=pass; r3: A=fail B=pass |
| `capability.inventory@gap` | 0 / 3 / 0 / 0 | 0 / 2 / 1 / 0 | 3/3 | r2: A=fail B=unknown |
| `capability.inventory@normal` | 1 / 2 / 0 / 0 | 1 / 2 / 0 / 0 | 3/3 | r1: A=fail B=pass; r3: A=pass B=fail |
| `capability.ndvi@gap` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 | 3/3 | — |
| `capability.ndvi@normal` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 | 3/3 | — |
| `capability.raster_stats@gap` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 | 3/3 | — |
| `capability.raster_stats@normal` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 | 3/3 | — |
| `capability.recompute@changed` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 | 3/3 | — |
| `capability.recompute@normal` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 | 3/3 | — |
| `capability.supervised@gap` | 0 / 3 / 0 / 0 | 0 / 2 / 1 / 0 | 3/3 | r1: A=fail B=unknown |
| `capability.supervised@normal` | 0 / 3 / 0 / 0 | 0 / 2 / 1 / 0 | 3/3 | r3: A=fail B=unknown |

逐项判据（check）的分布，用来定位「哪一条判断不同」，而不是只看总数：

| 判据 | A 组 | B 组 |
|---|---|---|
| `capability.chm@gap:claim` | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 |
| `capability.chm@gap:negative` | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 |
| `capability.chm@gap:positive` | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 |
| `capability.chm@normal:claim` | 1 / 2 / 0 / 0 | 3 / 0 / 0 / 0 |
| `capability.chm@normal:negative` | 1 / 0 / 2 / 0 | 3 / 0 / 0 / 0 |
| `capability.chm@normal:positive` | 1 / 2 / 0 / 0 | 3 / 0 / 0 / 0 |
| `capability.inventory@gap:counts` | 0 / 3 / 0 / 0 | 0 / 1 / 2 / 0 |
| `capability.inventory@gap:suitability` | 3 / 0 / 0 / 0 | 2 / 0 / 1 / 0 |
| `capability.inventory@normal:counts` | 1 / 1 / 1 / 0 | 1 / 1 / 1 / 0 |
| `capability.inventory@normal:suitability` | 2 / 1 / 0 / 0 | 1 / 1 / 1 / 0 |
| `capability.ndvi@gap:answer` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.ndvi@gap:grid_mask` | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 |
| `capability.ndvi@gap:pixels` | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 |
| `capability.ndvi@normal:answer` | 0 / 0 / 3 / 0 | 0 / 3 / 0 / 0 |
| `capability.ndvi@normal:grid_mask` | 0 / 0 / 3 / 0 | 0 / 3 / 0 / 0 |
| `capability.ndvi@normal:pixels` | 0 / 0 / 3 / 0 | 0 / 3 / 0 / 0 |
| `capability.raster_stats@gap:area` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.raster_stats@gap:mask` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.raster_stats@gap:zonal` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.raster_stats@normal:area` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.raster_stats@normal:mask` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.raster_stats@normal:zonal` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.recompute@changed:fresh_job` | 0 / 3 / 0 / 0 | 0 / 3 / 0 / 0 |
| `capability.recompute@changed:lineage` | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 |
| `capability.recompute@changed:new_result` | 0 / 2 / 1 / 0 | 0 / 3 / 0 / 0 |
| `capability.recompute@normal:fresh_job` | 0 / 3 / 0 / 0 | 1 / 2 / 0 / 0 |
| `capability.recompute@normal:lineage` | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 |
| `capability.recompute@normal:new_result` | 0 / 0 / 3 / 0 | 0 / 1 / 2 / 0 |
| `capability.supervised@gap:baseline` | 1 / 2 / 0 / 0 | 2 / 1 / 0 / 0 |
| `capability.supervised@gap:metrics` | 0 / 2 / 1 / 0 | 0 / 2 / 1 / 0 |
| `capability.supervised@gap:split` | 1 / 2 / 0 / 0 | 2 / 1 / 0 / 0 |
| `capability.supervised@normal:baseline` | 0 / 3 / 0 / 0 | 1 / 2 / 0 / 0 |
| `capability.supervised@normal:metrics` | 0 / 3 / 0 / 0 | 0 / 2 / 1 / 0 |
| `capability.supervised@normal:split` | 2 / 1 / 0 / 0 | 1 / 2 / 0 / 0 |

## 四、可复现性

同条件三次重复是否给出一致结论（`unanimous` 为 True 表示三次相同）。

| 案例 × 条件 | A 组 | B 组 |
|---|---|---|
| `capability.chm@gap` | True (1 种 / 3 次) | True (1 种 / 3 次) |
| `capability.chm@normal` | False (2 种 / 3 次) | True (1 种 / 3 次) |
| `capability.inventory@gap` | True (1 种 / 3 次) | False (2 种 / 3 次) |
| `capability.inventory@normal` | False (2 种 / 3 次) | False (2 种 / 3 次) |
| `capability.ndvi@gap` | True (1 种 / 3 次) | True (1 种 / 3 次) |
| `capability.ndvi@normal` | True (1 种 / 3 次) | True (1 种 / 3 次) |
| `capability.raster_stats@gap` | True (1 种 / 3 次) | True (1 种 / 3 次) |
| `capability.raster_stats@normal` | True (1 种 / 3 次) | True (1 种 / 3 次) |
| `capability.recompute@changed` | True (1 种 / 3 次) | True (1 种 / 3 次) |
| `capability.recompute@normal` | True (1 种 / 3 次) | True (1 种 / 3 次) |
| `capability.supervised@gap` | True (1 种 / 3 次) | False (2 种 / 3 次) |
| `capability.supervised@normal` | True (1 种 / 3 次) | False (2 种 / 3 次) |

## 五、时间与 Token 成本

模型调用与工具调用来自每槽位的 `trace.json` / `raw/events.json`，不是估算。

| | 模型调用合计 | 工具调用合计 | 每次已评分槽位平均模型调用 |
|---|---|---|---|
| A 通用 | 499 | 582 | 13.9 |
| B 领域 | 405 | 404 | 11.2 |

## 判读规则

- A 组与 B 组仅领域层不同；configuration_drift 或 contamination 非空说明对照无效。
- 各维度分开报告：工程可靠性、任务正确性、判断质量、可复现性、成本。
- 未配对槽位单独列出，不并入任一组的分母。
- 未配对槽位、`infra_error` 与 `unknown` 都不计入通过率分子，但都保留在报告里。
