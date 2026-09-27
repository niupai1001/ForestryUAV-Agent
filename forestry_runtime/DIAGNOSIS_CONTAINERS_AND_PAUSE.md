# 容器数量、暂停与失败累积：证据诊断与处置

> 日期：2026-09-26 · 范围：`forestry_runtime` · 依据：本机 Docker 实测、`data/` 持久化事实、被暂停 Run 的完整事件流、源码

本文回答四个问题，每个结论都注明证据来源。**诊断优先于修复**：先确定底层原因，再决定改什么。

---

## 0. 结论摘要

| # | 问题 | 结论 |
|---|---|---|
| 1 | 为什么运行时会"产生大量容器" | **是设计，不是泄漏。** 每个动作（`code_run` / `dependency_install` / `environment_check`）各起一个一次性作业容器，退出即删。实测 Docker 里只有 2 个容器：`forestry-runtime`（Runtime 本体）与一个无关的 `open-webui`。**真正会累积的不是容器，是 `forestry-job:*` 提交镜像（13 个 / ≈2.6 GB）**，提交后无人回收。 |
| 2 | `[paused] 同一前提已连续三次失败…` 为什么由 Runtime 说出来 | 因为 `AgentPaused` 在工具派发处抛出，模型**从未拿到**解释机会；前端又把 Runtime 的机器文本追加进助手气泡。已在源码修复：模型现在一定拿到一次无工具的解释回合；前端不再把机器文本写进对话。 |
| 3 | 失败累积是"一次 run 三次"还是"一个 turn 三次" | **一个 Turn 内**。`AgentDependencies` 每 Turn 新建，三个计数器随 Turn 结束归零；同 Turn 内无重置（`1:7:...` 与 `1:7` 字面量恒等）。 |
| 3b | 为什么以前能完成、现在不能 | 不是因为"模型变差"或"暂停太早"，而是**依赖安装环节整条链路恒失败**：`host_bridge` 安装脚本文本引用 `os` 而该名称未定义 → 任何 `dependency_install` 都必然 `NameError` → 依赖永久 `unverified` → 所有需要计算的任务被 `blocked_by` 挡住。 |

---

## 1. 容器

### 1.1 实测（`docker ps -a`）

```
CONTAINER ID   IMAGE                        NAMES              STATUS
1c8c69fc4946   forestry_runtime-runtime     forestry-runtime   Up 3 hours (healthy)
34e8d74f098d   ghcr.io/open-webui/...       open-webui         Exited (255) 10 days ago
```

带 `forestry.runtime.managed=true` 标签的作业容器：**0 个**。

### 1.2 为什么"运行时会看到容器不断出现"

- `host_bridge/server.py:781 job_start` → `docker run -d --name forestry-<job_id>`（`server.py:852`）；
- 三种动作都会起容器：`code_run`/`shell`（`kind=python|shell`）、`dependency_install`（`kind=install`）、`environment_check`（`/jobs/run`，`--rm`）；
- 结束即回收：`job_status` 落盘终端观察后执行 `docker rm`（`server.py:1042`），失败则打印 `Managed job cleanup deferred` 交给看门狗；看门狗每 15 秒跑 `enforce_deadlines` + `prune_finished`（`server.py:1137`、`455-461`）。
- 并发上限 `AGENT_MAX_ACTIVE_JOBS`，`setup.ps1:138` 默认 **1**，因此同一时刻最多 1 个作业容器。

**这就是"完整沙盒"的实现方式**：`code_run` 容器是 `--read-only --cap-drop ALL --user 10001:10001 --network none`（`server.py:743-748`）。
架构上 Runtime 本体在 `forestry-runtime` 容器里，作业容器是它的**兄弟容器**（Host Bridge 在宿主机持有 Docker 控制权，见 `DEPLOY.md:17`）——所以"在 Docker 里运行"并不会阻止新的兄弟容器出现，这是刻意的边界设计，不是违规。

一次暂停的 Run（09:46–09:47）产生 4 个作业容器，全部已删除；`data/job-records/` 只留记录 4 份，与容器数一致。

### 1.3 真正会累积的东西

```
Images          34   13.87GB   可回收 4.72GB
  forestry-job:d2965e3c7715  192MB   4 小时前
  forestry-job:bd8a3d2b6de2  192MB   4 小时前
  ... 共 13 个 ≈2.6GB
Build Cache    205    8.601GB  可回收 6.367GB
Containers       2   96.44MB
```

`_commit_job_image`（`server.py:377`）把成功的安装容器 `docker commit` 成 `forestry-job:<digest>`，让后续代码作业能看见新装的库。它**每次提交都新建 tag、从不删除旧 tag**，`workspace/.runtime/job-image` 只记最新一个。所以会话越多、缓存越多。这与"大量容器"观感同源，但对象是镜像不是容器。

**处置建议（未改，需你确认）**：在 `_commit_job_image` 成功后清理该工作区此前记录的 tag；或提供 `forestry-job` 保留策略（保留最近 N 个）。同时 `docker builder prune` 可立即回收 6.4 GB。

---

## 2. `[paused]` 为什么是 Runtime 在说话

### 2.1 事件流证据（`data/runs.sqlite3` → `turn_3b31d7d4cd124112bfa855993e193a80`）

关键片段：

```
seq 655  code_run  required_packages=["numpy",...]  -> blocked_by dependency_install（未执行）
seq 678  dependency_install ["numpy"]               -> install_failed（容器内 NameError: name 'os'）
seq 683  model_call #15
seq 684-707  thinking：模型准备「查看日志、修改安装脚本」——它已经定位到安装器问题
seq 708  error  「同一前提已连续三次失败，且证据没有变化。本轮停止尝试；…」 state=paused
seq 709  done   state=paused
```

**没有 `model_call #16`，也没有 `message` 事件。** 原因在 `agent.py`：

1. `prerequisite_stalled` 在 `dependency_install` 失败时置 `pause_reason`；
2. 工具派发包装器在工具返回后立即 `raise AgentPaused("Run paused before the next tool dispatch.")`；
3. 捕获 `AgentPaused` 时旧代码只在 `not emitted_text`（本轮尚无文字）时才请求模型解释——本轮模型早就说过话，于是**跳过解释**，直接 `yield {"type":"error", "content": 守卫模板}`；
4. 前端 `eventViews.ts` 把该 error 追加进助手气泡：`[paused] 同一前提已连续三次失败…`。

三重问题叠加：守卫在模型拿不到工具的时刻抛出、解释分支被"已经说过话"挡掉、机器文本被渲染成模型的话。

### 2.2 已实施修复（源码）

- `runtime/agent.py`
  - 新增 `AgentDependencies.observation_log` + `_observe()`：按序记录本轮每次调用的工具、参数、失败 code/信息或成功摘要（上限 40 条）。
  - 新增 `_stall_account()` / `_recall()`：把上述事实渲染成可放进指令的证据行。
  - 三处守卫（`prerequisite_stalled` / `identical_failed_call` / `repeated_read`）的 `pause_reason` 现在包含 `code=`、失败次数、最近失败调用与已获得事实，并要求模型用自己的话交代，不得复述 Runtime 文本。
  - `final_response_offered`（bool）→ `final_response_offered_at`（请求序号）：捕获 `AgentPaused` 时，只要模型还没真正被问过（含"守卫就在它刚给出的那次请求里落地"以及随后又被拒绝一次工具的情况），**一定**发起一次无工具收尾请求，由模型写出解释。
  - 收尾失败时不再静默：`_pause_account()` 明确标注"这一次没有生成模型说明"，并附上 Runtime 直接记录的观察事实与所需输入。
- `frontend/src/eventViews.ts`
  - `error` 事件不再写入助手气泡（`paused`/`waiting`）；`failed`/`canceled` 等终态仍保留文本（没有后续模型回合）。
  - `error` 事件改为进入 Trace 面板：机器事实不丢，但不再冒充模型的话。状态本身始终由标题栏状态徽标显示。

---

## 3. 失败累积的粒度与"以前能做、现在不能"

### 3.1 粒度：一个 Turn

- `agent.py:stream_agent` 每次调用都新建 `AgentDependencies(...)` → `prerequisite_failures`、`blocked_failures`、`stale_reads` 全部随 Turn 重置；
- 同 Turn 内无重置：`_prerequisite_key` 生成的 key 在环境类别（`unknown`）下是 `["unknown","environment","env"]` 字面量，`evidence_version` 也是字面量列表 —— **计数只增不清**；
- 因此：**同一 Turn 内，同一前提失败 3 次 → 暂停；用户下一句话即新 Turn，计数归零、限制解除**（这也是"补充要求后继续"能生效的原因）。

危险的一面：`identical_failed_call` 与 `prerequisite_stalled` 统计的是同一个失败，所以实际上"同一前提第 2 次失败"（第 1 次失败 + 2 次重复提交）就会触发暂停——比 `INPUT_GROUNDING_AND_RECOVERY.md` 规则 5（"连续行动没有新增事实时要求模型总结"）更早。

### 3.2 根因链（为什么现在完不成）

| 层 | 事实 | 证据 |
|---|---|---|
| L0 | **Host Bridge 安装脚本回归**：脚本内 `import importlib.metadata as m, json, pathlib, subprocess, sys`，但脚本体引用 `os.environ`；`_resource_report_source()` 只定义了别名 `_os` | 3 个作业容器日志逐字一致：`File "<string>", line 35, in <module> / NameError: name 'os' is not defined`（`data/job-records/job_{bc18318e,0e1bac62,cdb04e60}*.json`） |
| L1 | 任何 `dependency_install` 都恒失败 → 依赖永远 `unverified`；`code_run(required_packages=[...])` 被 `blocked_by` 拒绝 | 事件流 seq 655/656、440–444、464–468、678–682 |
| L2 | 该任务（树冠/植被计算）必须有 numpy/rasterio，于是任务的唯一入口被永久堵死 | 同一事件流；`runtime/capabilities/code_run/service.py:106-123` |
| L3 | 守卫阈值本身是对的（同一前提反复失败就该停），但因为 L0–L2，用户唯一看得见的信息就是守卫那句话 | 对比：9/24 的 Run 里 3 次 install 成功 → 任务完成 |

**"以前能完成"的对照证据**：`run_165bd09458634b18a1d37939cb441501`（9/24）job_refs 里 3 个 install `succeeded`、1 个 `failed`，任务产出 `oam-02_preview.png`；9/26 三次 install 全 `failed`。

**已被上游修好的部分**：`git show HEAD:forestry_runtime/host_bridge/server.py` 里不存在 `_resource_report_source`，也**不引用任何 `os.environ`**（即旧的安装脚本不可能产生该 NameError）；工作树版本第 205 行已包含 `import os`，工作树文件 mtime 10:34:07，当前 Bridge 进程 10:34:32 启动 —— 当前源码是修好的。本文复核方式：直接生成脚本并在本机执行，第 35 行正常通过（唯一差异是 Windows 无 `statvfs`，属预期）。

**注意**：`runtime/` 与 `host_bridge/` 都有大量未提交改动（`agent.py` +267 行、`server.py` +487 行区域），上面这 30 秒的时间窗说明修复与重启**可能**同时发生，但无法从磁盘追溯被覆盖的旧版本，故仍需一次真机安装作业确认。

---

## 4. 现在还需要做什么

1. **重建 Runtime 镜像**（前端改动要经 `npm run typecheck` + `vite build` 进镜像；`tsc --noEmit` 已本地通过）：
   ```powershell
   .\setup.ps1
   ```
2. **清掉错误的依赖状态**：删除会话工作区 `.runtime/deps/environment.json`，否则旧结论仍会把 `numpy` 判为不可用。
3. **真机验证一次安装**：新会话里让它 `dependency_install` 一个包，应看到容器内 `RUNTIME_INSTALL_PLAN=…` 且 `state=succeeded`（不再有 `NameError`）。
4. **回收镜像/缓存**（可选，立即释放 ~9 GB）：`docker image prune -a` 会误删缓存镜像，建议先确认；`docker builder prune` 更安全。
5. **待你决定的设计项**：
   - `forestry-job:*` 提交镜像的保留策略；
   - 是否让 `identical_failed_call` 只做"拒绝重复提交"而不再单独触发暂停（目前它与 `prerequisite_stalled` 统计同一失败，使阈值提前到第 2 次失败）。

---

## 5. 本次验证状态

| 项目 | 结果 |
|---|---|
| Docker 容器泄漏 | 无（2 个容器，作业容器 0 个） |
| `tests/`（除 `test_generic_runtime`） | 通过（1 项 `test_ab_experiment` 失败为既有环境问题：容器 arm 环境不匹配，与本次改动无关，已用 git stash 对照确认） |
| `tests/test_generic_runtime.py` | 46 passed |
| 守卫相关：`final_statement` / `repeated_failed_calls` / `repeated_reads` / `tool_recovery` / `lifecycle` / `run_api` / `paused_run_scoring` / `kernel` | 72 passed, 27 subtests |
| 前端 `tsc --noEmit` | 通过 |
| 安装脚本 `NameError` | 工作树版本已复现验证为"不再出现" |
| 真机 `dependency_install` | **待验证**（需要重建镜像后由你触发一次） |
