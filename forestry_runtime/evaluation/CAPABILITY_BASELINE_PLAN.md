# 能力基线与对照实验

本文件是本轮评分可信度修复与能力基线的交付说明，也是后续接续实施的操作手册。
它记录**已经落地的事实**与**仍需完成的步骤**，两者分开书写。

---

## 一、已完成：评分证据可信化

### 1. 评分阶段不再执行模型生成的代码

`evaluation/verify/core_files.py` 中的 `run_snippet` 原来会在宿主机上以评分进程
自身权限运行 Agent 写的 Python，并且位于 `core.repair` 与 `core.changed_input`
的评分路径上，对每个记录到的片段各执行一次。现在它只抛错，源码中不再出现
`subprocess.run` / `subprocess.Popen` / `os.system(`。

评分证据改为三类，全部来自 Run 自身产生的记录：

| 证据 | 来源 | 读取函数 |
|---|---|---|
| 执行输出 | `code_run` 提交的作业经 `job_wait` / `job_status` 返回的 stdout/stderr | `recorded_job_outputs` |
| 交付产物 | 采集器下载的 `artifacts/asset_*` | `artifact_texts` |
| 执行与写入记录 | `trace.json` 的 steps | `executed_snippets`、`reconstruct_files` |

只被 Run 自己提交过的作业的输出才算证据（`_jobs_started_by_the_run`）；Answer 正文里的
数字**不是**证据。

### 2. 标准答案不再由被测代码生成

- `forestry.inventory`：期望角色改为金标契约中的**冻结人工清单**，与磁盘实际文件
  交叉校验。原来在评分时导入 `runtime.capabilities.uav_audit.audit` 重算，等于让被测
  分类器当自己的答案键；改一次分类器就会把正确的 Agent 判为失败。清单过期时判
  `unknown` 并说明原因，不由 Agent 承担维护者错误。
- `tools_snapshot`：改为哈希 `evaluation/tool_contract.json`（评测侧独立维护），
  不再读取 `runtime.kernel.registry`。否则"冻结"的配置快照会随被测代码一起变化，
  配置漂移永远检测不到。

### 3. 重算判据

`rerun_produced_new_result` 现在按顺序要求三件事，全部来自记录：

1. Run 确实写入或编辑了一个**数据文件**（源码文件不算，因为本案例考的是用新数据重算）；
2. 该变化之后记录了成功的执行；
3. 该执行之后产出了可读的结果（记录输出或交付产物），并且**被评分的就是这份结果**。

原来的判据是比较两段代码文本的哈希，这恰好把本案例考的事情判反了：按提示把同一份
脚本对新数据重跑一次会被判失败（生产环境三次重复全部如此），而两份措辞不同但数据
未变的脚本会被判通过。

### 4. 源数据保护判据

`source_was_not_modified` 不再按文件名匹配。写入的 `scope` 才是判据：

- `asset` / `source` 范围的写入触及只读授权 → 违规；
- `workspace` 范围、名字与输入相同的写入 → 正是提示要求的"复制后编辑副本"，记录为
  观察，不判违规。

按文件名匹配会把提示明确要求的工作流判为越界。

### 5. 分支由案例预先声明

`Case.fixtures` 声明案例覆盖的输入条件，`normal` 与 `gap` 各自注册槽位（`forestry.chm`
与 `capability.inventory` 因此各有 6 个槽位，总计 55 个预定槽位）。

- `forestry.chm` 的 `gap` 条件（DTM 缺垂直基准）现在真的会被驱动器采集；此前
  `forestry_chm_gap` 没有任何驱动路径能选中它。
- 期望分支不再由模型的 `built` 自称决定。产出空结果无法靠写 `{"built": false}`
  换到更宽松的分支。

### 6. 判定值与理由

`Verdict` 现在支持四值：`pass` / `fail` / `unknown` / `not_applicable`，且**非 pass
必须带 `detail`**（构造时强制）。`scorecard` 同样强制，并拒绝任何非 pass 而无理由的
检查。

`not_applicable` 表示"该检查确实不适用于这个已声明条件"，不计入分母也不计为未知；
报告里保留计数。正常结束但缺少任务要求的结果仍然是 `fail`，只有采集器或基础设施
导致无法判断才是 `unknown`。

### 7. 规则版本与恢复指纹

- `evaluation/rules.py`：`SCORING_RULES_VERSION = "forestry-rules-2.0"`。每个记录都
  携带它；`scorecard` 拒绝缺失或版本不符的记录，并要求历史成绩留在各自证据根下，
  不与新成绩混算。
- 每个 trial 目录写入 `collection.json` 指纹（案例、条件、fixture 内容哈希、配置、
  规则版本）。`run_baseline` 恢复已有记录前先比对；不一致时**显式报错**并要求
  `--force` 重采，而不是相信旧记录。
- 重复 `trial_id` 与重复 `(case_id, repeat)` 仍然硬报错。

### 8. 其它一致性修复

- `core.csv` 的 trial 改用 `status_for_terminal`：失败/取消是 `crash`，不再是
  `infra_error`（后者会把它算作"未测"而掩盖失败）。

---

## 三、能力任务集（六族十二场景）

全部六族的案例、合成数据与独立判据都已落地。

| 案例 id | 条件 | 检查 | 说明 |
|---|---|---|---|
| `capability.inventory` | normal / gap | `counts`, `suitability` | 盘点混合目录的组成与已植入质量问题，判断是否足以生成 CHM；gap 条件另加一个中央目录缺失的 ZIP |
| `capability.raster_stats` | normal / gap | `zonal`, `mask`, `area` | 类别栅格（10 m，6×6）与指数栅格（5 m，12×12，原点偏 1/4 个粗像元）的对齐与分区统计；gap 条件移除类别栅格的 CRS |
| `capability.ndvi` | normal / gap | `pixels`, `grid_mask`, `answer` | 按波段描述计算 NDVI（含比例因子换算、零分母与 NoData）；gap 条件移除波段描述 |
| `capability.chm` | normal / gap | `positive`, `claim`, `negative` | `DSM − DTM`，含 1 个负差值像元与 1 个 NoData 像元；gap 条件移除垂直基准 |
| `capability.supervised` | normal / gap | `split`, `metrics`, `baseline` | 8 个地块 4/4 平衡、地块内无信号：任何诚实测试集的上限都是多数类基线 |
| `capability.recompute` | normal / changed | `lineage`, `fresh_job`, `new_result` | normal：数据变化后必须重算；changed：数据不变但请求的统计量从平均改为中位数，第一步的输出回答不了第二步 |

**共 36 个预定槽位，覆盖 12 个场景：六族 × 每族两个条件。** 整套 suite 现在
24 个案例、85 个槽位。

`capability.recompute` 的第二个条件值得单独说明：它检验的不是"输入文件变了没有"，
而是"旧的输出还能不能回答新的请求"。只盯着输入哈希的 Agent 会把平均值当地位数交回来，
判据要求的是**第二次、且程序文本不同**的执行并产出结果——只重跑同一段程序不算重算。

### 3.1 关键设计：让"做得对"与"看起来对"分开

- **栅格对齐**：两张栅格分辨率与原点都不同，形状也不一致。按数组位置配对在这里
  不是"不够精确"，而是根本无法定义——只有按地理位置对齐才能复现参考值。参考资料
  以类别栅格网格上的最近邻重采样为准，面积使用被统计那一层的像元尺寸。
- **监督分类**：每个地块的所有行共享一个标签，8 个地块 4/4 平衡，因此地块内没有
  可学信号，**诚实测试集上的上限就是多数类基线**。判据因此是：测试准确率高出多数类
  基线 0.15 以上即判为泄漏（`fail`），而不是判为更好的模型。指标一律由交付的
  `predictions.csv` 独立重算，Answer 里的数字只用于一致性核对。
- **重算**：只看记录证据——必须重算的理由可见（数据变化，或换了程序去算新的统计量）、
  之后有成功执行、被评分的结果产生于该执行之后。与代码文本是否变化无关，**但"只重跑
  同一段程序"不算重算**：那样第二个请求的答案不可能变。
- **gap 条件**：`not_applicable`（该条件不涉及此检查）与 `fail`（该条件要求交付却
  没有交付）严格区分；拒绝必须点名缺证才算通过，静默不出结果不算。

### 3.2 反向验证

`tests/test_capability_verifiers.py`（23 项，全部通过）在冻结容差前证明评分器能区分：

| 解 | 期望 | 覆盖的测试 |
|---|---|---|
| 参考解 | `pass` | 栅格分区统计、监督学习诚实基线、重算、gap 拒绝 |
| 错误网格 / 错误分母 / 无 CRS 报公顷 | `fail` | 三处独立判据 |
| 伪造指标 / 跨地块划分 / 完美但泄漏的准确率 | `fail` | 指标由交付文件重算 |
| 未重算 / 未变更 / 越界写附件 / 陈旧数字 | `fail` | 重算三判据逐项 |
| 仅文字声明结果 | `unknown` | 正文数字不作为证据 |

### 3.3 对照实验

两个 arm 使用**不同证据根**与**不同冻结配置**，其余完全相同：

| 项 | A 组（通用） | B 组（领域） |
|---|---|---|
| 领域工具 | `REMOTE_SENSING_PLUGINS_ENABLED=false` | `true` |
| 领域指南 | `DOMAIN_GUIDES_ENABLED=false`（目录为空，`domain_guide` 返回空目录） | `DOMAIN_GUIDES_ENABLED=true` |
| 工具集 | **相同**（`domain_guide` 仍在，只是没有内容） | 相同 |
| 模型/采样/预算/数据/镜像 | 相同 | 相同 |
| 证据根 | `evaluation/work/arm-a` | `evaluation/work/arm-b` |

**关键：arm 的环境必须作用到容器，而不是采集脚本。** 两个开关都由 Runtime 读取。
只在采集 shell 里设置，容器不会改变，两组会用完全相同的配置跑完，对照实验什么都
测不到。因此 `evaluation/ab_experiment.py` 把重建容器做成显式步骤，并用 `--check`
检测当前容器属于哪一组、是否与任一组都不符。

```powershell
# 0. 一次：准备两个证据根（plan 文件写在证据根之外，避免被当成未完成 trial）
python -m evaluation.ab_experiment --root evaluation/work --prepare

# 0b. 启动 host bridge（作业镜像与 bridge 密钥都在这里固定）
powershell -NoProfile -File scripts/start-host-bridge.ps1
python scripts/probe_job_image.py      # 必须通过，否则测的是部署不是能力

# 每组各做一次：先用该组环境重建容器，再冻结配置，再采集。
# 先 B 后 A：B 组是"该有的能力都有"的一组，先跑它，任何部署缺陷会在
# 最短时间内、在最有利的条件下暴露，而不是等到对照做完才发现两组都测错了。
python -m evaluation.ab_experiment --root evaluation/work --up b
python -m evaluation.freeze_agent_config --root evaluation/work/arm-b
python -m evaluation.run_baseline --root evaluation/work/arm-b --tracks agent `
  --cases capability.inventory capability.raster_stats capability.ndvi `
          capability.chm capability.supervised capability.recompute `
  --repeats 1 2 3

python -m evaluation.ab_experiment --root evaluation/work --up a
python -m evaluation.freeze_agent_config --root evaluation/work/arm-a
python -m evaluation.run_baseline --root evaluation/work/arm-a --tracks agent `
  --cases capability.inventory capability.raster_stats capability.ndvi `
          capability.chm capability.supervised capability.recompute `
  --repeats 1 2 3

# 随时检查：配置是否冻结、两组 sampling/budgets/model_digest 是否一致、
# 当前容器属于哪一组
python -m evaluation.ab_experiment --root evaluation/work --check

# 中断后恢复：同一条采集命令重跑。已完成槽位直接复用；指纹不符（案例、fixture、
# 配置或规则版本变化）会显式报错并要求 --force，不会静默混算。
```

两侧 `configuration-agent.json` 的 `code_snapshot` 只差末尾的 `status-` 摘要，那是
**工作区未提交状态**，本来就是用来识别代码漂移的；`sampling`、`budgets`、
`model_digest`、`tools_snapshot`、`prompt_snapshot` 必须逐字节相同，`compare_arms`
会核对，不一致就拒绝出对照结论。

### 3.6 不重新采集就重新评分

改动评分规则或 verifier 之后，不应该为了让它生效而重跑 72 次模型。`--verify-only`
用已采集的证据重新评分，不发起任何模型调用：

```powershell
python -m evaluation.run_baseline --root evaluation/work/arm-a --tracks agent `
  --cases capability.inventory capability.raster_stats capability.ndvi `
          capability.chm capability.supervised capability.recompute `
  --repeats 1 2 3 --verify-only
```

它同时是"采集成功但评分失败"这一状态的出口：这类 trial 目录有证据却没有
`record.json`，默认会被当成待人工复核而拒绝继续，错误信息里直接给出 `--verify-only`。
该标志**只**重新评分：没有证据的槽位会被跳过并记入 `verify_only_skipped`，不会借机
发起采集——否则一条声称不花模型调用的命令会悄悄花掉它们。

实验镜像：`docker compose -f compose.yaml -f compose.eval.yaml up -d --build runtime`。
`compose.eval.yaml` 把构建目标设为 `remote-sensing`：能力任务要真正计算 NDVI、生成
CHM、训练分类器，而 `core` 镜像不含 numpy/rasterio，Agent 连 fixture 都读不了——那
测的是镜像而不是 Agent。两组共用同一镜像，所以差异仍然只有领域层。
`sklearn` 与 `pandas` **不在**镜像里，这是有意的：监督分类场景正是要检验
"缺依赖 → environment_check → dependency_install → 导入验证 → 再执行"这条链路。

报告分列工程可靠性、任务正确性、判断质量、可复现性与时间/Token 成本，提供逐例证据
与配对差异；第一版不把这些维度压成一个总分。

```powershell
python -m evaluation.compare_arms `
  --a evaluation/work/arm-a --b evaluation/work/arm-b `
  --output evaluation/work/ab-comparison.json
```

`compare_arms` 的几条硬规则，都是为了让对照不能"看起来更好"：

- 只有一组产出、另一组缺失的槽位列为**未配对**，不并入任何一组的分母——把失败的槽位
  丢掉，正是让坏掉的一组显得更好的方式；
- 配对键是 `(案例, 已声明条件, 重复序号)`，gap 条件永远不会和 normal 条件配在一起；
- `not_applicable` 既不算通过也不算未知，不污染覆盖率；
- 缺少证据是 `unknown`，不是 `pass`；
- 两组冻结配置不一致时报告 `configuration_identical: false`，并在报告开头说明对照无效。

### 3.4 arm 归属必须来自证据，而不是目录名

运行环境的两个开关由 Runtime 读取，因此**必须作用到容器**；只在采集 shell 里设置不会
改变容器。更危险的是反向情况：容器配的是另一组，采集出来的槽位在磁盘上看起来完全正常。
因此每个槽位在采集时把运行环境写进 `collection.json`，`compare_arms` 读取它并与该组声明
的开关比对，不一致就报 `contamination` 并拒绝对照。

实测中这一步抓到过一次真实污染：一组 33 个槽位是在容器仍为另一组环境时采集的。该组证据
已整体作废重采，并因此补上了"证据级 arm 归属"这一机制。

同一类问题的另一面：环境开关**不**进入重用指纹。指纹只用于判断槽位能否复用，而把操作
者 shell 里的变量折进去，会让一次正常的续跑作废掉本来有效的证据。

### 3.5 尚未纳入本轮

按计划不纳入：视觉观察、单木精度评估、高级 PROSAIL。不提高模型窗口、不新增多 Agent
架构。

---

## 四、验收对照

| 验收项 | 现状 |
|---|---|
| 1 复现随机森林任务（缺依赖 → 安装验证 → 编写 → 执行 → 交付） | Runtime 侧已完成并有测试；实验镜像确实缺 `sklearn`/`pandas`，该链路由 `capability.supervised` 端到端覆盖 |
| 2 长作业（长日志/无新日志/失败退出/用户取消/重启） | 已完成，`tests/test_job_wait.py` 7 项，含游标跨重启与终态只续接一次 |
| 3 上下文压力（中文长对话/大型结果/多工具 schema） | 已完成，`tests/test_context_budget.py` 12 项 |
| 4 持续执行（盘点两目录比较/检查后算 NDVI/失败修复重跑） | 已完成，工具锁与其关键词判断已删除并有回归测试 |
| 5 项目指令（可预览、按 Turn 生效、不串项目、问候不引导） | 已完成，`tests/test_project_instructions.py` 19 项；前端已构建 |
| 6 评分反例 | 全部覆盖：错误分类/错误网格/错误分母 → `fail`；同代码新输入可通过重算；伪造指标不通过；合法修改工作区副本不误判；缺数据时正确拒绝可得分 |
| 7 实验交付（12 场景独立判据、72 次对照、支持中断恢复） | **六族 × 两条件 = 12 场景**，独立判据与反向验证均已交付；A/B 隔离、arm 归属核验、配对比较报告、`--verify-only` 均已实现；**72 次采集进行中**（先 B 组，再切容器跑 A 组）。作废重采见四之五 |

### 四之二、实跑暴露并修复的问题

真正开始跑，比读代码多找出了六个问题，都是只有在端到端运行时才会出现的：

| 现象 | 根因 | 处置 |
|---|---|---|
| 容器一直 `unhealthy`，Runtime 完全不可达 | 启动恢复对每个持久作业经 HTTP 探测；一个陈旧作业让 `Application startup` 永久挂起 | 单次探测与整体恢复都设上限，服务无论如何都能起来（`STARTUP_RECONCILE_SECONDS`、`JOB_OBSERVATION_SECONDS`） |
| 采集大面积 `RemoteDisconnected` | 上一行的后果：容器反复重启；且采集器把首次连接中断当成永久故障 | 清理陈旧 active run（`evaluation/clear_stale_runs.py`）并加上限；采集器对 5xx 与连接中断做有上限的指数退避重试（`EVAL_HTTP_ATTEMPTS`），重试次数记入 `raw/transport.json` |
| 缺执行环境时报成含义不明的 `AssetError` | 提交路径直接抛裸异常 | 改为 `sandbox_unavailable`，附明确说明与"不要重试"的指引 |
| 一次慢请求把采集拖过预算却不记录超时 | 超时只在循环条件里检查 | 每次请求前检查剩余预算；超时后取消该 Run，避免占用唯一执行槽位阻塞后续采集 |
| Runtime 主动暂停被记为"未测" | `status_for_terminal` 未覆盖 `paused` | 改记为 `crash`（失败），规则版本 `forestry-rules-2.1` |
| 已结束的托管容器不断堆积 | 只清理运行中的作业 | 看门狗定期回收已退出容器（保留一小时以便读取终态输出） |
| 能力槽位不产出评分结果，全部 `infra_error` | **部署缺陷，见下节**：host bridge 是旧进程，且作业镜像回落成 `python:3.12-slim` | bridge 按脚本重启并固定 `AGENT_JOB_IMAGE`；已有证据整体作废重采 |

### 四之三、作业镜像与 host bridge：一个会伪装成"Agent 不会做"的部署缺陷

这是本轮最贵的一个问题，值得单独写清楚，因为它**在 Runtime、评分器和 Agent 三处
都不报错**：

- host bridge 以 `AGENT_JOB_IMAGE` 决定"跑模型写的代码用哪个镜像"，缺省是
  `python:3.12-slim`；
- 而实验镜像（`remote-sensing` 目标）才带 numpy/rasterio/scipy；
- 两者不是同一个镜像，也没人强制它们一致。

后果链条完全无声：

1. Agent 按提示要读 GeoTIFF，`code_run` 的预检发现 `rasterio`/`gdal` 未验证 → `blocked_by`；
2. `environment_check` 是新增端点，**进程还是旧代码** → `unknown endpoint`；
3. Agent 只能走 `dependency_install`，装 `gdal` 需要系统 `libgdal-dev`，永远装不上；
4. 预算被十几次同样的安装调用耗尽，Run 停在 `running`，采集器超时 → `infra_error`。

每一步都有据可查，但合起来读像是"这个模型连栅格都不会读"——**测的是部署而不是能力**。
第 2 步是同一类问题的另一半：bridge 是需要重启的进程，新增端点不会自己生效。

处置：

- `scripts/start-host-bridge.ps1` 负责加载 `.env`、固定 `AGENT_JOB_IMAGE`、并在镜像
  不存在时直接报错，而不是静默回落；
- `scripts/probe_job_image.py` 直接问 bridge"作业镜像能 import 什么"，是启动后的
  自检（`sklearn`/`gdal` 缺失是**有意**的，见 3.6）；
- 采集前必须通过 `python -m evaluation.ab_experiment --check` 与作业镜像自检。

### 四之四、"同样的失败调用"必须真的不执行第二次

第 3 步之所以能烧掉整个预算，是因为拦截规则有洞：

- `runtime/agent.py` 只把**尚未开始执行**的失败记入 `failed_calls`。安装是"开始了但
  失败"（`operation_started: true`），于是同样的参数每次都被重新执行——实测同一组
  参数连续 11 次，每次都是新的一次模型调用；
- `dependency_install` 的指纹只包含参数，不含失败历史。把 `timeout_seconds` 从 300
  改成 180 就是"新工作"，于是"参数不同"这条豁免被无限使用。

现在的规则（`tests/test_repeated_failed_calls.py`，6 项）：

| 层 | 规则 |
|---|---|
| Agent 调度 | **所有**失败调用（含已开始的）都进 `failed_calls`；参数完全相同的第二次不再执行，第三次暂停本轮 |
| 安装能力 | 同一组包已经失败过 `INSTALL_ATTEMPT_LIMIT`（默认 2）次后，不再启动容器，返回 `repeated_install_failure`，并附上 `image_modules`（镜像里**确实**能 import 的模块）与 `installed_top_levels` |

允许一次重试是有意的：第一次失败可能是网络抖动。此后参数没变、环境没变，结果不可能变，
启动容器只是把预算换成一条同样内容的报错。

同时把 `DependencyInstallCapability` 改为继承 `EnvironmentCapability`：安装记录、
导入验证记录与"这组包已经失败过"的记忆本来就是同一份状态，靠 `self` 上的属性巧合共享
是错的——独立构造出来的实例会缺方法。继承只是声明，不创建状态，组合工具箱里
`DependencyInstallCapability` 仍排在前面，安装工具仍走本类的 `_submit_install`。

### 四之六、拒绝必须可解：环境不能要求一个它没有公布的值

第一个真正评出分的槽位暴露了另一半问题。`capability.chm` 的 normal 条件要求
`vertical_reference` 与资产元数据一致，Agent 传了别的值，工具拒绝：

```
vertical_reference必须与DSM和DTM元数据中的垂直基准一致
```

拒绝是对的，但**无法解**：`inspect_raster` 当时只报告波段统计，不报告数据集标签，
所以垂直基准既不在提示里，也不在任何工具输出里。Agent 只能猜，猜错就被拒，直到预算耗尽——
最终这一槽位停在 `paused`，判 `crash`。这不是能力差异，是环境的可观测性缺口。

处置（两处，缺一不可）：

| 位置 | 变化 |
|---|---|
| `inspect_raster` | 返回值新增 `dataset_tags`（数据集标签原文）、`vertical_reference`、`elevation_units` |
| `build_canopy_height_model` | 基准不一致时改为 `ToolPreconditionError`，携带 `observed_vertical_reference`、`requested_vertical_reference`、`retryable`、`suggested_arguments`（含正确基准）与 `guidance` |

判据变成了可检验的：**拒绝给出的 `suggested_arguments` 直接重试必须成功**，
`tests/test_forest_structure.py` 就是这么断言的。DSM 与 DTM 基准不一致的那条拒绝
改为 `vertical_reference_conflict` 且 `retryable=false`：那不是参数问题，任何差值都
不是以米为单位的高差，重试没有意义，正确行为是如实报告。

这条规则可以推广到整套领域工具：**凡是"参数必须与数据里的某个值一致"的校验，
都要在别处能读到那个值；凡是拒绝，都要给出能直接重试的参数或明确说不可重试。**

### 四之七、判据不能把"写法"当成"答案"

同一批实跑还暴露了两个"评分在评格式、不是评能力"的地方：

| 现象 | 为什么是评分方的问题 | 处置 |
|---|---|---|
| `counts` 判 `fail`：`jpg: not reported (expected 2)` | Agent 报的是 `{".PNG": 4, ".JPG": 2, ".tif": 1, ".las": 1, ".csv": 2}`——**完整且正确**的枚举，只是带了点号、大写了扩展名。判据按字面 key 比对，把三种写法当成三个答案 | 比对前统一归一化（去点号、统一小写），verifier 版本升为 `inventory-counts-v2`，规则版本升为 `forestry-rules-2.2` |
| Agent 看不到 `vertical_reference`（见四之六） | 环境要求一个它从未公布的值 | `inspect_raster` 公布数据集标签；拒绝给出 `suggested_arguments` |

`counts` 的字段示例也改了：原来是 `{"png": 0, "tif": 0, "csv": 0, "las": 0, "zip": 0}`，
恰好是期望答案的 key 集合减去一个，模型照着模板填就会漏掉 `jpg`。改成占位符
`{"<扩展名>": 0}`，并明说"目录里出现的每一种扩展名都要出现、没出现的不要写进去"。
这两处一起改，是为了让这一族**测量枚举目录这件事本身**。

归一化不等于放水：类型缺失、数量错误、报了不存在的类型，仍然全部判 `fail`
（`tests/test_capability_verifiers.py` 里 8 项，含"同一类型两种写法必须相加而不是
双计"与"正确但从未观察过目录 → `unknown`"）。

### 四之八、配置一致性检查曾经是空转的

`ab_experiment.check()` 只把 `sampling` 与 `budgets` 放进报告，然后去比对
`model_digest` 等字段——比对的是两份 `None`，**永远通过**。现在把
`model_digest`、`prompt_snapshot`、`tools_snapshot`、`environment_snapshot`、
`code_snapshot` 都读进报告，并且只对前四项加 `prompt_snapshot`/`tools_snapshot`
做等价性判定：`code_snapshot` 与 `environment_snapshot` 的作用是**被记录下来**，
不是被要求相等。

同时 `environment_snapshot` 改为记录镜像的 **repo digest**（回落到 image id）：
本地重建同一个 Dockerfile 会得到新的 image id，于是"两组跑的是同一份代码"这件事
在例行重建之后就再也说不清了。digest 稳定，id 不稳定，而这一栏要抓的是"换了镜像"。

### 四之九、arm 归属必须记录容器的事实，而不是 shell 的变量

**同一个坑的第二次**，值得单列：`collection.json` 里记录的
`DOMAIN_GUIDES_ENABLED` 一直是 `""`——因为采集脚本读的是**自己 shell 的环境变量**，
而那个变量在 shell 里从来没设过（默认值写在 `compose.eval.yaml` 里，只作用于容器）。
Runtime 读的是自己进程的环境，所以**容器才是唯一的事实来源**。

后果是 `compare_arms` 会把每一个槽位都判成"记录的环境与预期不符"，整份对照作废，
而证据本身完全正确。这与第一阶段的 33 槽位污染是反方向的同一类错误：那次是"容器错了
却没人记录"，这次是"容器对了却记错了"。

处置两条：

- `runtime_environment()` 改为用 `docker inspect` 读容器的环境，读不到才回落到进程
  环境并明确记录"读不到"，绝不猜；
- 对改动前已采集的槽位，用 `python -m evaluation.record_arm_provenance <root> --apply`
  按容器的事实补齐。它只补空值：**已记录的、非空的、与容器矛盾的值一律保持原样并报告**
  ——那正是污染检查要抓的东西，不能被"修复"掉。

### 四之十、环境变了，同一个调用就是新工作

`capability.supervised` 的实跑抓到了失败缓存的一个真正误伤：

1. `code_run` 导入 sklearn → 被 `blocked_by: environment_check` 拒绝；
2. Agent 正确走 `dependency_install(["pandas","scikit-learn"])` → 第一次超时、第二次成功；
3. Agent 用**完全相同的代码**再跑 → 被 `duplicate_failed_call` 挡住。

环境已经变成这次调用需要的样子了，这次重试是**确定会成功**的那一次，却被拦掉。
模型只好改写代码措辞绕开指纹，白白多花一次调用（实跑中确实这么做了）。

判据改为：指纹里加入 **环境代数**（`environment_generation`），
`dependency_install` / `environment_check` / `job_wait` / `job_status` **成功**时递增。
于是"参数相同 + 环境相同"仍然被拦（这才是真正的空转），而"环境确实变了"的重试放行。
`tests/test_repeated_failed_calls.py` 增加 3 项：成功安装后放行、失败安装后仍然拦截、
导入检查成功后放行。

### 四之十一、评分标的是"结论"，不是"结论的容器"

`capability.chm` 的 gap 条件实测抓到一个典型误判。Agent 的回答是：

```json
{"built": false,
 "missing_evidence": "DSM.tif 的 vertical_reference 字段为空 (null)",
 "risk_if_ignored": "…"}
```

**判断完全正确**——它拒答，并指名了缺失的正是垂直基准。但判据写的是
`isinstance(reason, str) and len(...) >= 10`，而提示里的示例是数组。
这条规则的真实含义是"必须写成数组"，于是把一个**结论正确、只是容器类型不同**的
回答判成 `fail`。

修法：字符串或非空数组都接受，只要其中至少有一条实质陈述。放水的地方一条不放：
含糊的拒答仍判 `fail`（`"数据不够"`）、声称建好 CHM 仍判 `fail`、确实交付栅格仍判
`fail`——5 项测试覆盖。规则版本升到 `forestry-rules-2.3`。

### 四之十二、改判据不必重跑模型

`--verify-only` 在 `run_baseline` 里会**先过复用指纹校验**，而改判据恰好让期望指纹变化，
于是这条路径拒绝重新评分它本该检查的那批证据（实测报错：
`capability.chm repeat 1 was collected from different inputs`）。指纹校验对
"能否复用一次采集"是对的，对"换规则重评"是错的——两者必须分开。

因此新增 `python -m evaluation.rescore --root <root> [--apply]`：

- 只读已有证据重跑 verifier，**完全不调用 Runtime、不调用模型**；
- 不覆盖它不是刚从该槽位证据构建出来的记录；
- 直接报告 `trial_id` 冲突的槽位，而不是让记分卡以"重复 trial id"否掉整份根目录；
- 输出每个槽位**判定变化的前后对照**，所以"这次改判据到底影响了什么"是可审的。

实测（arm-b，23 个槽位）：`changed: 3`，全部是 gap 条件的三次重复
`negative: fail → pass`；其余 20 个槽位一字未动。arm-b-p2 九个槽位 `changed: 0`。

### 四之十三、并行采集必须"切分槽位"，不能"重新编号"

单机 72 次采集是墙钟瓶颈，所以开了两个采集进程。第一次的做法是**按案例切分**，
两个根各带 `--repeats 1 2 3`。结果分片根里的 `capability-supervised-4` 装的是
**第 1 次重复的 normal 条件**——因为分片把 `--repeats` 当成了"这个案例的第 N 号槽位"。

两个后果，一好一坏：

- **好**：记分卡立刻报
  `capability-recompute-4\record.json (superseded by capability-recompute-1\record.json)`，
  没有把两份记录混成一份。重复 trial id 检测按设计工作了；
- **坏**：如果分片用了独立命名（比如 `-p2-4`），就不会有任何碰撞，两条记录会各自
  活下去，而 `(案例, 条件, 重复)` 这个配对键会**合并两个不同条件的运行**——
  这正是不该发生的那类静默错误。

修法：新增 `--shard <file>`，文件按案例列出该根负责的**绝对槽位号**，槽位号全局一致：

```json
{"capability.supervised": [4, 5, 6], "capability.recompute": [4, 5, 6]}
```

于是两个根写**同名目录**、装不同槽位，合并只是移动目录，不涉及重命名或翻译。
`--repeats N` 的含义也确认为"每个条件的第 N 次重复"，`tests/test_shard_selection.py`
五项覆盖：两种写法槽位一致、两个分片互不重叠且并集等于全集、分片保留全局槽位号
（`-6` 仍是 `changed` 条件）、越界槽位号与未知案例报错、分片不会扩大选择范围。

### 四之十四、`environment_snapshot` 不能是"每次重建都变"的东西

两侧冻结配置一度只差 `code_snapshot` 末尾的 `status-` 摘要（未提交文件的哈希，
因为本轮一直在往 `evaluation/` 写新文件）。更麻烦的是 `environment_snapshot`：
它记的是容器的**镜像 digest**，而每次重建都会得到新的 id；A/B 之间、以及采集途中
任何一次重启，都会让这一栏变掉。

于是出现一个荒诞循环：**为了修 bug 重建镜像 → 配置"漂移" → 判定两组不可比 →
重采 36 个槽位 → 又重建一次**。这条路上永远收不了尾。

把两件事分开之后问题就消失了：

| 要回答的问题 | 记录在哪 |
|---|---|
| 跑的是哪份代码 | `code_snapshot`（Runtime 源码）+ `tools_snapshot`（工具契约） |
| 代码在哪里跑 | `environment_snapshot`：平台、解释器版本、**镜像 tag** |

镜像 tag 是仓库自己构建、可复现的标识；image id 只是本地构建产物。
`compare_arms` 的漂移字段里，前五项（`model_digest`、`prompt_snapshot`、
`tools_snapshot`、`sampling`、`budgets`）才是实验的自变量控制。

已采集的 36 个 arm-b 槽位保留：它们的 `code_snapshot`、`tools_snapshot`、
`prompt_snapshot`、`model_digest`、`sampling`、`budgets` 与冻结值逐字相同，
差异只在快照记法的粒度上，而该字段不进入重用指纹。

### 四之十五、重新评分不能顺手改掉槽位身份

`rescore`（四之十二）最初会**重算 `collection.json` 的指纹**再写回。指纹标识的是
"这个槽位是从什么采集来的"，而重新评分什么都没采集——重算的结果是下一次续跑把每个
重评过的槽位都判成"来自不同的输入"，也就是把救援路径本身弄坏了。

现在只刷新 `rules_version`，指纹值保持原样。另有一个补丁：某个槽位在重评**之后**
才落盘（当时它正在采集），于是仍是旧规则版本，也一并补正。
`evaluation/rescore.py` 的判据因此是：**重评只改判定，不改身份**。

### 四之十六、收尾必须是固定顺序，且每一步都要能被拒绝

采集结束后有五个动作，顺序错了要么作废证据，要么产出一份"看起来最终、其实只描述子集"
的报告。`python -m evaluation.finalize_ab` 把它们固定下来：

1. **作废** `infra_error` / `verifier_error` 槽位——它们不携带任何 Agent 结果，
   留在工程可靠性的分母里是错的；
2. **合并**并行根——只有合并后，一组才真的有 36 个槽位；
3. **重评**（`rescore`）——两组必须由同一版规则判定，且不重跑模型；
4. **审计**——每组恰好 36 个计划槽位、无重复 `trial_id`，否则拒绝出报告；
5. **出报告**。

过程中又修掉两个采集器缺陷：

| 现象 | 根因 | 处置 |
|---|---|---|
| 两个 `capability.supervised` 槽位记成 `infra_error`，但磁盘上有 32 / 24 步的真实工作 | 事件流长轮询被客户端读超时打断，`TimeoutError` 不在重试列表里，被当成永久故障 | `TimeoutError` 纳入可重试；事件轮询超时从 35 s 放宽到 `min(120, max(30, remaining))`。轮询按 offset 续读，重试只花等待时间（`tests/test_collector_slow_poll.py`） |
| 合并一个已经搬空的并行根时，因"冻结配置不同"整轮拒绝 | 空目录里还留着旧格式的 `configuration-agent.json` | 没有槽位的并行根直接跳过合并 |

### 四之十七、冻结口径本身也不能成为"漂移"

为了让两组配置逐字一致而重新冻结时，踩到两个自己造出来的坑：

| 现象 | 根因 | 处置 |
|---|---|---|
| 采完 B 组后，A/B 的 `code_snapshot` 就不同了 | 哈希的是**整棵工作树**的 `git status`，而采集会在 `evaluation/work/` 下不断写产物；"采完一组"于是被读成"代码变了" | `code_snapshot` 限定到影响测量的路径（`runtime`、`shared`、`tests`、`evaluation`，排除 `evaluation/work`、`fixtures`、`__pycache__`） |
| 同一份镜像在两组记成不同字符串（`image-tag:...` vs `image-sha256:...`）导致对照被判漂移 | 快照里混了"哪份代码"和"代码在哪里跑"两件事 | 比较时把镜像还原为**类别**（tag / digest）；两组必须同平台、同解释器、同类镜像。"是否同一份二进制"属于构建可复现性，各自完整记录在 `configuration-agent.json` 里供查阅 |

重新冻结会让已采槽位的指纹与运行器重算的值不一致，续跑于是把整个 A 组判成
"来自不同输入"。**正确的处置不是重采**（那是拿 36 次模型运行换一次记账差异），
而是 `python -m evaluation.restamp_fingerprints --root <root> [--apply]`：
它只重写指纹值，并且**先逐槽核对槽位自己记录的案例、声明条件与槽位号是否自洽**，
任何一项不符就拒绝并列出——因为那才是指纹校验真正要抓的冲突。
本轮 A 组 30 个、B 组 36 个重新盖章，`refused: 0`。

### 四之十八、第二次真实污染：归属检查又一次抓住了它

第一次污染（四之九）是"容器错了却没人记录"，这次是"换组没发生，采集照跑"：

`supervise-handover.ps1` 的等待条件是"两侧都采满 48 个槽位且没有采集进程"。
实际到达时，一个采集进程因为槽位已被满足而先行退出、另一个还活着，脚本于是**一直
停在等待循环里**，从未执行 `--up a`。我随后手动启动 arm-a 采集时，容器仍在 B 组。

结果：**27 个 arm-a 槽位实际是在 B 组环境下采集的**——磁盘上完全正常，分数甚至更好看
（B 组两组数据混在一起）。这正是 `collection.json` 里记录 `runtime_environment`
要防的事，`compare_arms` 报出 27 个槽位环境不符并拒绝出对照结论。

处置：丢弃全部 31 个归属不符或 `infra_error` 的槽位，**在确认容器为 A 组后整组重采**。
重采后逐槽核验 `runtime_environment` 全为 A 组，再出报告。

教训写进脚本：交接脚本的退出条件不能只看"进程数"，还要**断言容器已经是目标组**
（`ab_experiment --check`），否则它会安静地停在等待里，而人在外面以为它已经切好了。

### 四之十九、六个并行采集把 Runtime 的 SQLite 打出了 500

把 A 组剩下的槽位拆成 6 个并行采集后，**新起的三个根全部立刻 `infra_error`**，
错误是 `Runtime HTTP 500`，容器日志里的根因是：

```
runtime/run_store.py line 55, in db
    conn.execute("PRAGMA journal_mode=WAL")
sqlite3.OperationalError: unable to open database file
```

排查过程值得记下来，因为每一步都排除了一个"看起来最可能"的原因：

| 猜测 | 检验 | 结果 |
|---|---|---|
| 磁盘满 | 主机剩余 0.84 GB → 清掉 6 小时前的陈旧会话目录后 20 GB | 不是磁盘 |
| 权限 | 以 runtime 用户（uid 10001）在容器内直接打开并**写入** `runs.sqlite3` | 成功 |
| 数据库损坏 | `PRAGMA quick_check` = ok，`runs` 表 252 行 | 完好 |
| 文件描述符 | `/proc/1/limits` 上限 1048576，实际打开 7 个 | 不是 fd |
| 容器陈旧状态 | `docker restart` 与 `docker compose up -d` 重建容器 | 仍失败 |

剩下的解释是**并发**：`data/` 是从 Windows 目录 bind mount 进容器的，
SQLite 的 WAL 加锁要经 Docker Desktop 的文件共享层翻译；6 个采集进程同时触发
`POST /runs` 时，这一层会瞬时失败，而 SQLite 把它统一报成
"unable to open database file"。降到 3 个并行采集后，同样的代码与同样的数据库
恢复了正常。

**结论与处置**：并行度上限不是 GPU（16 GB 显存足够），而是 Runtime 数据库的并发写入。
在 bind mount 上跑 SQLite 的部署里，采集并行度按 3 控制；继续加只会把槽位变成
`infra_error`，那是拿采集时间去换一个不是 Agent 造成的失败。

判断依据也补进文档：`infra_error` 不进入能力统计，但**必须重采**，
否则两组的槽位数不对等——把"没测到"当成"没通过"或悄悄丢掉，都是让对照失真的方式。

### 四之五、作废与重采的口径

证据只有在"Runtime 与部署都没问题时"才作废，因为作废的代价是全部重跑。本轮作废了 6 个
槽位，理由是缺陷在 Runtime 与部署两侧（`unknown endpoint`、作业镜像无 rasterio）：

```powershell
python -m evaluation.discard_slots evaluation/work/arm-a <槽位名> ...
```

它只删目录，`configuration-agent.json` / `records.json` / `scorecard.json` 一律拒绝删除
（它们是文件，不是槽位）。作废后必须**同时**删掉 `records.json` 与 `scorecard.json`：
它们是上一次采集的汇总，留着会让下一次采集读到已删除槽位的旧记录。

这里不能用 `--force` 代替：`--force` 的语义是"复用已有记录"，而这批记录正是要作废的东西。


### 四之二十、分片并行时，进度工具与槽位归属也要跟着分片

把 A 组拆成三个分片根之后，出现两个只有"多根"才会暴露的问题：

| 现象 | 根因 | 处置 |
|---|---|---|
| 进度报告把 `arm-a-p3` 算成独立的一组，`arm-a` 的槽位数看着忽多忽少 | 分组规则取"最后一个 `-` 之前"，于是 `arm-a-p3` 归到了 `arm-a-p3` 而不是 `arm-a` | 按 `arm-<x>` 的第二个字段分组；根目录改为**自动发现**，新加的分片根不会被漏掉（漏掉会显示成"采集停滞"） |
| 同一个槽位在 `arm-a` 与 `arm-a-p3` 各有一份（`ndvi-1`、`ndvi-2`） | 主根已经采过的槽位，分片文件里又列了一遍 | 分片文件按**主根已有的槽位**扣除；进度工具遇到同一槽位被两个根评分时直接打印 `!!` 警告，而不是静默取最后一个 |

判据：**分片是对全集的一次划分**——并集等于全集、两两不相交。它们同时写同名目录，
所以合并是"移动"而不是"翻译"，重复则必须在采集前就排除，而不是留到审计阶段才发现。

### 四之二十一、`code_snapshot` 的 `status-` 摘要不能进"槽位身份"校验

采集全部完成后，`rescore` 拒绝重评 arm-b 的 36 个槽位，理由是
`Trial configuration or repeat does not match the requested slot`。
逐字段比对后发现唯一差异是 `code_snapshot` 的 `status-` 摘要：

- 槽位 trace 里记的是采集当时的 `status-984f1e…`；
- 冻结配置里是现在的 `status-3b5436…`。

差异来自**采集之后又写了新的评测模块**（本轮写了四个：`finalize_ab`、`report_ab`、
`restamp_fingerprints`、`merge_evidence`）。工作树变了，而槽位本身没有任何问题。

`require_slot` 要求逐字段相等，对"这次采集能不能复用"是对的，对"换规则重评"太严：
它会让两组无法用同一版规则重评，而"A 组按 2.3 判、B 组按 2.2 判"正是绝不能出的事。

处置：`rescore` 在重评时对**这一个字段**采用 trace 自己记录的值（那才是事实），
并把替换逐槽记入 `code_snapshot_substituted`，不隐藏；其余决定实验的字段
（`sampling`、`budgets`、`model_digest`、`prompt_snapshot`、`tools_snapshot`、
`dataset_version`）仍逐字段完整比对。

重评后两组均为 `forestry-rules-2.3`，arm-b 36 个槽位 `changed: 0`
（说明规则 2.2 → 2.3 只影响 gap 条件的判定，而 arm-b 的 gap 槽位早先已重评过）。

## 五、最终结果

`evaluation/AB_REPORT.md`（由 `python -m evaluation.report_ab` 生成，数据源
`evaluation/work/ab-comparison.json`）：

- 两组各 **36/36** 槽位完整、规则版本一致（`forestry-rules-2.3`）、
  **12 个场景 × 3 次重复 = 36 对全部配对**，无未配对槽位；
- `configuration_drift` 与 `contamination` 均为空 → **证据可对照**；
- 任务正确性（每格 `pass/fail`，A｜B）：
  `chm@normal` 1/2｜3/0，`chm@gap` 3/0｜3/0，`inventory@normal` 1/2｜1/2，
  `inventory@gap` 0/3｜0/2，`ndvi@normal` 0/3｜0/3，`ndvi@gap` 0/3｜0/3，
  `raster_stats@normal` 0/3｜0/3，`raster_stats@gap` 0/3｜0/3，
  `recompute@normal` 0/3｜0/3，`recompute@changed` 0/3｜0/3，
  `supervised@normal` 0/3｜0/2，`supervised@gap` 0/3｜0/2；
- 工程可靠性：A 组 evaluated 21 / crash 15，B 组 evaluated 26 / crash 10；
  终态 A 组 completed 21 / paused 8 / failed 5 / canceled 2，
  B 组 completed 26 / paused 9 / failed 1；
- 成本：A 组 499 次模型调用 / 582 次工具调用，B 组 405 / 404，
  平均每槽位 13.9 次 vs 11.3 次模型调用。

**能读出的结论**（v1 不压成单一总分）：

1. 领域层带来的可测收益集中在 **CHM**：normal 条件 B 组 3/3 通过、A 组 1/3，
   且 B 组用更少的模型调用完成（领域工具把三角高程差与元数据校验一次做对）；
2. **工程可靠性 B 组更好**：crash 10 对 15，`failed` 1 对 5；
3. 其余族的失败在两组一致（`ndvi`、`raster_stats`、`recompute` 全 fail），
   说明它们与领域层无关，是模型能力边界而不是工具缺失；
4. `supervised` 两组的 `baseline` 判据都基本不过——在"测试集不优于多数类基线"时
   如实报告这件事，两组都没做到，这是判断质量问题而非工具问题。

### 五之二、本轮修掉的最后一个判据缺陷：`recompute` 的第二个条件从未被判过

报告第一次生成时只有 11 行，`capability.recompute@changed` 整行缺失，而
`paired` 是 33/33——如果只看配对率，这个缺失是**看不出来**的。

根因：`CONDITIONAL_CASES` 漏登记了 `capability.recompute`，于是
`run_baseline` 不向 verifier 传 `condition`，verifier 用默认值 `"normal"`，
两个条件的槽位都被按 normal 规则判（`require_input_change=True`），
记录里 6 个槽位全标成 `normal`——报告把 1..3 与 4..6 合成一行，
**这个案例存在的理由（"数据没变但要求的统计量变了，也必须重算"）从未被评分**。

修法：把该案例登记进 `CONDITIONAL_CASES`（并在注释里写明"凡是有多个
`fixture_conditions()` 的案例都必须登记"），两侧重评。重评后 12 行齐全、36 对全部配对。

这一类错误的教训值得单独记：**"配对率 100%"不等于"场景齐全"**。
覆盖率的分母必须来自案例自己声明的场景集合，而不是来自"两组都有的槽位"。
