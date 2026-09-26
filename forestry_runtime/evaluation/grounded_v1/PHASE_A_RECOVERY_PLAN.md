# 阶段 A 重建与收尾执行计划

生成时间：2026-09-26。本文基于对当前工作树的实际盘查，而不是对规划文档的复述。
适用前提：本工作树是 `git clone` 得到的新副本，原有 `data/` 不在 git 内，已全部缺失。

## 变更记录

**2026-09-26（收尾：步骤 5 / 7 / 8）**：四项未闭合项全部闭合，阶段 A 门禁达成。

- **步骤 2**：`grading.py` 新增 `assert_portable()`，`specs.build_tasks` 冻结前对
  `public_root` / `input_path` / 每个 `public_inputs` 断言——绝对路径与 `..` 逃逸
  直接拒绝。`TaskSpec.input_path` 改为 `PurePosixPath`（永不 `resolve`），
  需要落盘时用 `resolved_input_path` 锚定到本签出的 `RUNTIME_ROOT`。
  **任务文件盘符出现次数：0**。
- **步骤 5**：`grade_gis_analysis` 实现并按题分派——ID 12 逐像元比已发布
  `ruggedness.tif`（先校验冻结副本 SHA-256，不一致则拒绝评分）；ID 9 读交付 CSV
  的比值列。**正反用例 22 项**通过；真值端 ID 12 端到端复现 7,636,628 像元零差异
  （测试内用真实 `Elevation.tif` 重算后交由评分器判定）。
- **步骤 7**：三项 `unknown` 补齐判据，判据逐题写在冻结文件 `process_rubric` 内：
  - `dependencies_satisfied` 读 `environment_check` / `dependency_install` 的
    回执与 `No module named` 记录，**全部 14 题可判**；
  - `method_fits_data`、`key_parameters_correct` 读实际执行的 `code_run` 源码，
    按冻结标记组与参数值判定（数值按值比较，5500 与 5.5e3 等价）；
  - 未冻结判据的题（12 道 OAM）**继续保持 `unknown`**，不默认 Pass。
  - 顺带修掉一处真缺陷：过程检查原先只认 `action`/`tool_call`，而采集器实际写
    `tool_start`/`tool_end`——真实证据下这些检查**恒为 unknown**。已按采集器词汇
    修正（`tool_start` / `tool_end` / `ok` / `outcome_ok`）。
  - `_process_checks` 移到 `_finish` 末尾：原先在 `outcome` 定稿前运行，
    「失败后是否恢复」读到的是未定值。
- **步骤 8**：规则版本冻结为 `grounded-rules-1.1`（GIS 评分器、过程判据、事件词汇
  三项变更）；题库重新冻结并记录 `rules_version`。任何试点槽位尚未评分，
  故无已出结果改变含义——`data/runs/` 下的开发运行仍标记为 1.0。
  **同证据重复评分 3 次逐字节相同**（栅格题与矢量题各一，写入测试）。
- **回归**：`tests/test_grounded_grading.py` **66 项全部通过**（原 29 + GIS 22 +
  过程判据 13 + 可重复性 2）。全量套件 **353 通过 / 4 失败 / 26 收集错误**；
  4 项失败位于 `runtime.settings` / `runtime.lifecycle`，与评分器无引用关系，
  属既有问题；26 项收集错误源于本机缺 `pydantic`、`fastapi`、`pydantic_ai`
  等运行时代理依赖，属既有环境缺口。
- **门禁状态**：§4 五项硬性门禁**全部达成**（14 题皆有独立真值与可运行评分规则；
  任务文件无绝对路径；重建真值与冻结 gold 逐项相等；反例全通过且重复评分确定性；
  开发题已建立且来源不重叠）。阶段 A 收尾完成，可进入阶段 B。

**2026-09-26（数据重建）**：按 §3 步骤 1/3/4/6 重建数据资产，步骤 5 的真值部分完成、评分器未实现。

- 测试分片已重下并校验：334,303,139 B，SHA-256
  `5c10ac4cb8afaf18dae5aad5b22cc86bb80977116a8bcd8c16d41ae48b9b13cf`
- 全库审计重建：439 行 / 55 源 / 全 CC-BY 4.0 / 全 EPSG:3395 / 无网格失败 /
  51 行空前景 / 388 行可用 / 54 个可用源
- `grounded_v1_1_public`（12 输入 + 12 提示）与 `grounded_v1_1_private`
  （12 gold mask + `manifest.json`）已重建，拒绝候选仍是 `image_id=1927`
- **复现验收通过**：12 组 `valid_pixels` / `gold_canopy_pixels` /
  `gold_coverage_percent` 与 v1.1、v1.2 两份冻结文件**逐项相等**
  （`evaluation/grounded_v1/verify_regenerated_gold.py`）
- 来源审计台账重建：`evaluation/work/grounded_v1_1_ledger.md`，与
  `PHASE_A_REPORT.md` §2 三条结论一致
- 依赖固化：`requirements-eval.txt`（含 geopandas 1.1.4 / shapely 2.1.2 / pyproj 3.8.0）
- GABench 仓库与 6 个 LFS 载荷全部落盘并逐文件校验 SHA-256，见
  `evaluation/grounded_v1/GABENCH_ACQUISITION.md`
- **ID 9 几何独立复算首次完成**：已发布毁林多边形拓扑无效，GEOS 拒绝按原样裁剪；
  参考工具链用 `buffer(0)` 修复，该路径复算得 `0.47931103565527894`，与已发布
  `0.4793110356552816` 相差 `2.66e-15`。台账 §4.2 的「几何未独立重算」缺口已闭合
- ID 12 复算确认：7,636,628 像元零差异
- 训练分片已下载（464,097,497 B，SHA-256 `ec8fe0c7…`），开发题 6 题已生成，
  与正式 12 源交集为空
- **未处理**：步骤 2 的绝对路径（v1.2 仍有 14 处）、步骤 5 的 `grade_gis_analysis`
  实现、步骤 7 过程检查判据、步骤 8 规则冻结

**2026-09-26（本次）**：使用方决定剔除 `gabench-42`，题库升版 `grounded-v1.2`。

- 新增 `tasks.grounded-v1.2.json`：15 题 → **14 题**（12 OAM + 2 GIS）；
- `grading.py` 的 `SUITE_VERSION` → `grounded-v1.2`；
- `run_task.py`、`specs.py` 的默认任务文件改指 v1.2；
- `tasks.grounded-v1.1.json` **原样保留**，不原地修改已冻结文件；
- 回归 `pytest tests/test_grounded_grading.py`：**29 项全部通过**。

剔除发生在任何 Agent 运行之前，符合「见到成绩后不得挑样本」的要求。
正式试点槽位随之由 45 改为 **42**（14 题 × 3 次）。

## 0. 先纠正一个前提：规划文档已过时

`GROUNDED_EXECUTION_PLAN.md`（18 题 / 阶段 A 进行中）**已被 `PHASE_A_REPORT.md` 取代**。
当前真实状态是：

| 项 | 规划文档 | 实际 |
|---|---|---|
| 题库版本 | — | `grounded-v1.1`，已冻结 |
| 题目数 | 18（6+6+6） | **14**（6 提取 + 6 统计 + 2 GIS），见变更记录 |
| GIS 来源 | 6 题 | **2 题**，组合分析 0 题，按计划停在「题库不足」 |
| OAM 评分器 | 进行中 | **已实现并验收**，29 项反例通过 |
| 槽位数 | 54（18×3） | **42**（14×3）——后续一律用 42 |

因此下一阶段不是「从零做阶段 A」，而是**先重建被 `.gitignore` 排除的数据资产，再收尾阶段 A 的四项未闭合项**。

## 1. 资产盘点

### 1.1 在 git 内，可直接复现

- `evaluation/grounded_v1/tasks.grounded-v1.1.json`——15 题 TaskSpec + 12 题真值元数据
- `grounded_v1/{grading,metrics,evidence,specs,grade_trial,run_task,oam,audit_sources,audit_shard}.py`
- `tests/test_grounded_grading.py`（29 项反例）
- `grounded_v1/GABENCH_LEDGER.md`、`gabench_candidates.json`（57 题全枚举台账）
- `evaluation/collect/`（证据采集）
- Runtime 已部署且 healthy，`/health` 两项依赖均为 true

### 1.2 缺失（`.gitignore` 排除了 `forestry_runtime/data/`）

- `data/oam_tcd/test-00000-of-00001.parquet`
- `data/oam_tcd/grounded_v1_1_public/`（12 个公开 GeoTIFF）
- `data/oam_tcd/grounded_v1_1_private/`（12 个 gold mask + `manifest.json`）
- `data/gabench/repo`（GABench 仓库固定提交）
- `evaluation/work/`（全库审计、换题台账、GABench 获取清单）
- `evaluation/grounded_v1/GABENCH_ACQUISITION.md`（原放 `data/gabench/`，因该目录被忽略而移入版本库）

### 1.3 本次已完成（可复算）

- 通过 `hf-mirror.com` 以固定 revision `d97d4da0ebbb6e249ae95ac5e19656babd972eb2`
  重新取得测试分片：334,303,139 字节
- **SHA-256 校验一致**：`5c10ac4cb8afaf18dae5aad5b22cc86bb80977116a8bcd8c16d41ae48b9b13cf`
- 结构核对：**439 样本 / 55 个 `oam_id`**，与规划文档一致
- 字段确认：`image_id, image, annotation, oam_id, license, biome, crs, bounds, …`

网络事实（本机）：`huggingface.co`、`github.com` 直连不可达；`pypi.org`、
`hf-mirror.com`、`gitclone.com` 可达。**所有外部获取一律走镜像，并在台账记录镜像来源。**

## 2. 必须先处理的三个问题

### 2.1 TaskSpec 内嵌了原开发机绝对路径（阻断）

15 题的 `input_path` 全部写成
`E:\Document\ChatGPT\林业无人机遥感agent开发\forestry_runtime\data\...`，
该文件在本机不存在。**不修则题库在任何第二台机器上不可用。**

处理：改为相对 `public_root` 的路径，`public_root` 保持仓库相对路径；
在冻结脚本中加入断言——禁止绝对路径写入任务文件。

### 2.2 GIS 三题没有真值，也没有评分函数（已知未闭合）

`tasks.grounded-v1.1.json` 的 `gold` 只有 12 个键（`oam-01..12`），
`gabench-09/12/42` 无真值；`grading.py` 只导出 `grade_canopy_extraction` 与
`grade_canopy_statistics`，**没有 `grade_gis_analysis`**。三者必须同时补齐，
否则这三题在阶段 B 只能记为未测量。

### 2.3 `gabench-42` 是已被自己证明的坏题（需决策）

`GABENCH_LEDGER.md` §4.3 的实测结论：

- `point3d.prj` 为 Web Mercator（控制点约 `(-13.3 km, 6712.9 km)`），
  `buildings.prj` 为 British National Grid（顶点约 `(530.7 km, 181.2 km)`），
  两层 CRS 完全不同，而流程从不重投影；
- 5172 个顶点中 **1237 个等于 `-1.797e308`**（DOUBLE 最小值哨兵）；
- 其余顶点到视线 2D 线段的最近距离为 **6,553,765 m**，落在 2 m 阈值内的顶点数为 **0**；
- 故 `visible=true` 由「无任何障碍点通过筛选」得出，**与三维通视无关**。

台账原文要求：「如需使用必须重做数据（统一 CRS）并重新冻结版本」。
当前题库却已纳入该题，与台账结论自相矛盾。

**已决策并执行：剔除 `gabench-42`，题库升版 `grounded-v1.2`，正式试点 14 题 / 42 槽位。**
（同批被保留却未使用的 ID 27 不能替代：属「其他」家族且参考模型未发布，无法独立复算。）
因此本报告其余部分凡提到 GIS 处，均按**两题**（ID 9、12）计。

## 3. 执行顺序

依赖严格，前一步不完成不进入下一步。

### 步骤 1：环境固化

- 依赖：`pyarrow`（已装 25.0.1）、`numpy`、`rasterio`、`scipy`；
  GIS 三题另需 `geopandas`、`shapely`、`pyproj`（pypi 可达）。
- 固定版本并写入 `requirements-eval.txt`，避免评分器在不同机器上结果漂移。
- **验收**：新环境可导入全部依赖；`pytest tests/test_grounded_grading.py` 29 项全通过。

### 步骤 2：修路径并重锚题库

- 去除 15 题 `input_path` 的绝对路径，改为相对路径；
- 在 `specs.py` 生成端加断言，禁止绝对路径进入冻结文件。
- **验收**：任务文件中不含任何盘符；`python -m evaluation.grounded_v1.run_task --list` 正常列出 15 题。

### 步骤 3：重建 OAM 公开输入与私有真值

- 用 `oam.py` 从已校验的 parquet 按固定种子重选 12 个 `oam_id`；
- **必须复现 v1.1 的 `selection_rule`**：来源首候选瓦片树冠前景为空则整源跳过
  （这是 `oam-09` 被换掉、`image_id=1927` 被拒绝的原因）；
- 生成 `grounded_v1_1_public/`（12 个 tif）与 `grounded_v1_1_private/`
  （12 个 gold mask + `manifest.json`，含 `selection_rule` 与 `rejected_candidates`）。
- **验收（关键）**：重算的 `valid_pixels`、`gold_canopy_pixels`、`gold_coverage_percent`
  与 `tasks.grounded-v1.1.json` 中已冻结的 12 组 gold 元数据**逐项相等**。
  这是「复现成功」的唯一判据；不等则说明种子或选择规则未对齐，**不得覆盖原冻结值**，
  而应升版重冻结。

### 步骤 4：重跑质量审计

- `audit_shard.py`（全库 439 样本）+ `audit_sources.py` →
  重建 `evaluation/work/oam_shard_audit.json` 与 `grounded_v1_1_ledger.md`；
- 复核三项事实：空前景样本约 51 个、整块全零像元比例、标注为 RGB 类别色
  （黑为背景，二值化规则「任一通道非零即为树冠」）。
- **验收**：审计结果与 `PHASE_A_REPORT.md` §2 的三条结论一致。

### 步骤 5：GIS 两题评分器与真值（`gabench-09`、`gabench-12`）

1. 用 `gitclone.com` 镜像获取 GABench 固定提交
   `e8c64e883bbe45e2b94515c73941e7a0837320ae`，记录镜像来源与逐文件 SHA-256；
2. 实现 `grade_gis_analysis`，按台账 §4 的复算方法：
   - ID 12：3×3 邻域极差，逐像元整数相等，容差 0（**注意 255 不是 nodata**）；
   - ID 9：重投影 EPSG:32723 → buffer 5500 m → dissolve → 求交 → 比值，
     参考值 `0.4793110356552816`。
3. 为两题建立 gold 并写入 `tasks.grounded-v1.2.json`。
- **验收**：两题各自跑通「正确交付 → 成功」「错误交付 → 失败」正反用例；
  ID 12 能与已发布 `ruggedness.tif` 逐像元一致。

### 步骤 6：建立开发题

- 另取官方训练划分，抽取与正式 12 个来源**互不重叠**的开发题，
  专用于调试评分器与 900 s 预算，**不进入正式分母**。
- **验收**：开发题与正式题 `oam_id` 交集为空；现有反例测试改用开发题重跑仍全通过。

### 步骤 7：补齐过程检查判据

- 八项中 `method_fits_data`、`key_parameters_correct`、`dependencies_satisfied`
  仍为 `unknown`。逐题写下可观察判据后实现；
- **未完成前继续保持 `unknown`**，严禁默认 Pass。

### 步骤 8：冻结规则版本并回归

- 冻结 `grounded-rules-1.1`（含 GIS 与过程检查变更）；
- 全量回归 29 项反例 + 新增 GIS 反例；
- 对同一证据重复评分 3 次，报告须逐字节相同。

## 4. 进入阶段 B 的条件

阶段 A 收尾的**硬性门禁**，缺一不可：

1. 14 题均有独立真值与可运行评分规则；
2. `tasks.*.json` 无绝对路径，第二台机器可原样运行；
3. 重建的 OAM 真值与已冻结 gold 元数据逐项相等；
4. 反例测试全通过，重复评分确定性；
5. 开发题已建立且来源不重叠。

满足后进入阶段 B：当前 Harness 与最小参考配置同模型同资源对照，
**42 个预定槽位**（14 题 × 3 次）逐次记录，
未完成与超时保留在分母内，三次结果全部报告，不取最佳一次。

在此之前不发布任何分组成绩，也不开始正式试点。

## 5. 报告必须同时公布的缺口

即便全部完成，以下事实必须在成绩报告中写明，不得省略：

- GIS 组合分析 **0 题**，栅格处理差 1 题，题库相对规划缺口 3 题（原 2 题 + 剔除的 42）；
- `gabench-42` 已被剔除及其理由（CRS 不相容、`visible=true` 不由真实通视得出）；
- 题库版本 `grounded-v1.2`，与 `PHASE_A_REPORT.md` 记录的 v1.1 差异即本次剔除；
- 公开真值隔离只防止运行时读取答案，不能证明模型预训练阶段未见过数据；
- 子集成绩不等于 OAM-TCD 官方排行榜成绩。
