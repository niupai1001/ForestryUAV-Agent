# 能力开关对照实验矩阵 v1（capability-switch-matrix-v1）

评估默认关闭的四项能力开关（统一检索 `RETRIEVAL_FABRIC_ENABLED`、代码检索
`RETRIEVAL_CODE_ENABLED`、子代理 `SUBAGENTS_ENABLED`、安全并发
`SCHEDULER_PARALLEL_READS=4`）在**打包开启**状态下的实际效果，并与上下文窗口、
模型版本两个变量做三因素交叉对照。

> 因素一将四个开关作为一个**捆绑因素**处理（off = 全默认，on = 四项全开）。
> 单开关分解属后续消融，不在本矩阵内。

## 一、实验矩阵（2 × 2 × 2 = 8 臂）

| 编号 | 开关 | 上下文 | 模型 | 角色 |
|------|------|--------|------|------|
| E1 | off | 32K (32768) | qwen3.5:4b | **基线对照**（当前默认配置） |
| E2 | **on** | 32K (32768) | qwen3.5:4b | 开关实验组（对照 E1） |
| E3 | off | **128K (131072)** | qwen3.5:4b | 上下文对照组（对照 E1） |
| E4 | **on** | **128K (131072)** | qwen3.5:4b | 开关 × 上下文（对照 E3） |
| E5 | off | 32K (32768) | **qwen3.8:27b** | 模型对照组（对照 E1） |
| E6 | **on** | 32K (32768) | **qwen3.8:27b** | 开关 × 模型（对照 E5） |
| E7 | off | **128K (131072)** | **qwen3.8:27b** | 模型 × 上下文（对照 E3 / E5） |
| E8 | **on** | **128K (131072)** | **qwen3.8:27b** | 全开配置（对照 E7） |

### 因素对比（每因素 4 组配对，配对内只动目标变量）

- **开关效应**：E1↔E2、E3↔E4、E5↔E6、E7↔E8（控制上下文与模型）
- **上下文效应**：E1↔E3、E2↔E4、E5↔E7、E6↔E8（控制开关与模型）
- **模型效应**：E1↔E5、E2↔E6、E3↔E7、E4↔E8（控制开关与上下文）

### 开关 on 态的确切定义

```
RETRIEVAL_FABRIC_ENABLED=true
RETRIEVAL_CODE_ENABLED=true
SUBAGENTS_ENABLED=true
SCHEDULER_PARALLEL_READS=4
```

off 态 = 以上四个变量全部缺省（容器默认：false/false/false/1）。

## 二、执行结构与变量注入方式

变量按其生效层级决定注入方式，这是矩阵能落地的关键：

| 变量 | 生效层级 | 注入方式 |
|------|----------|----------|
| 四个开关 | 容器环境（进程导入时读 `os.getenv`） | 写 `.env` → `docker compose up -d runtime` 重建容器（相位切换） |
| `OLLAMA_CONTEXT` | per-trial settings（override 已验证直达模型层与预算层） | `run_task.py --settings '{"OLLAMA_CONTEXT":"..."}'` |
| `OLLAMA_MODEL` | per-trial settings（同上） | `run_task.py --settings '{"OLLAMA_MODEL":"..."}'` |

因此执行顺序为**两个容器相位**：

1. 相位 A（开关 off）：顺序跑 E1 → E3 → E5 → E7（臂间无需重建容器）；
2. 相位 B（开关 on，重建容器）：顺序跑 E2 → E4 → E6 → E8。

臂内每 trial 由 per-trial settings 指定上下文与模型，两相位之外零容器操作。

## 三、评估指标

**M1 任务质量（主指标）**
- grounded 判分通过与否与分项得分（题库 `grounded-v1.2`，判定规则 `grounded-rules-1.1`）。

**M2 任务状态**
- 终止状态分布：completed / paused / failed / canceled；
- 计划结算：`completion_state`（complete / open / blocked，来自 plan store）。

**M3 效率**
- 模型请求数、工具调用数、输入/输出 token 累计、墙钟时长。

**M4 开关机制生效性（仅 on 臂；用于确认实验有效性）**
- 检索段存在且 `retrieval_mode=fused`、`retrieval_count` 分布、检索源类型分布；
- `delegate` 调用次数；code 源候选出现与否。
- **有效性判据**：on 臂 trial 的 trace 中必须能观察到 M4 证据；全部缺失则该臂
  实验无效（开关未生效），必须排查后重跑，不得计入对比。

## 四、样本规模

| 阶段 | 题目 | 每臂槽数 | 总 trial 数 |
|------|------|----------|-------------|
| 试点 pilot | oam-07（1 题）× 2 重复 | 2 | 8 臂 × 2 = **16** |
| 完整 full | 冻结题库 14 题 × 3 重复 | 42 | 8 臂 × 42 = **336** |

槽位编号在臂内独立（每臂独立 `--runs-root`），不与既有基线槽位（1..3）混用。

## 五、结果判定标准

1. **有效跑**：trial 达到任一终态（completed / paused / failed）且 trace 完整。
   `infra_error`、无 trace 的超时残壳记为无效，单独列出不计入对比。
2. **试点（n=2/臂）**：只做方向性判读——逐配对报告 Δ（通过 0→1、请求数差、
   token 差、时长差），不宣称显著；同时用 E2/E4/E6/E8 验证 M4 生效性。
3. **完整（n=42/臂）**：
   - 题级聚合（每题 3 重复取多数结果），得每臂通过率与效率中位数；
   - **开关效应判定**：4 组配对方向一致，且通过率差 ≥ 10 个百分点，判
     "开关有实际效果"；方向不一致则判"效果依赖上下文/模型组合"并逐组报告；
   - **上下文效应**：完成率变化为主，token 经济性（每通过任务的 token 成本）为辅；
   - **模型效应**：通过率差为主，效率为辅；
   - 统计参考线：题级配对符号检验 p < 0.05（n=14 配对）。
4. **终止状态单独解读**：paused/failed 不计入"通过"，但作为稳定性证据报告
   （删除计数式暂停后，预期 paused 来自预算耗尽或 `missing_input` 等合法来源）。

## 六、控制变量（除目标变量外全部冻结）

- 同一镜像 commit（本矩阵运行前重建，`git rev-parse HEAD` 记入每臂 provenance）；
- 题库 `suite_version=grounded-v1.2` 冻结不动；判定规则 `grounded-rules-1.1` 不动；
- 每 trial 超时统一 900s；temperature 等其余运行参数走容器默认；
- 串行执行（单 ollama 实例，避免并发挤占 KV cache 污染时长指标）；
- 臂间不共享 runs-root；重跑前清臂目录（FileExistsError 守卫）；
- 采集侧统一（同 collector、同 api-key 流程、同 `run_task.py` 版本）。

## 七、已知风险

- **27b @ 128K**：KV cache 可能超出本机内存/显存。若 OOM，该臂记 infra 失败，
  降级方案：128K 仅在 4b 臂（E3/E4）成立，E7/E8 改 64K 并在报告显著标注；
- **on 态改变可见 schema**（delegate 进入工具列表）——这是处理变量的一部分，
  但意味着 E2/E4/E6/E8 的提示词预算与 off 臂不可逐 token 比较，M3 的 token
  指标以"每完成一步的净输入"解读；
- qwen3.5:4b 在 128K 的长上下文质量本身是实验要回答的问题，不是缺陷。
