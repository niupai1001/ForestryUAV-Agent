# 自己跑一套题：操作手册

本文是**使用方**视角的执行步骤，已在本机运行中的栈上逐条验证。评测端（题库、评分器）已完成，但**当前部署还跑不了需要写代码的题**——原因见第 5 节，必须先修。

## 1. 先确认栈是活的

```powershell
# Runtime（容器，监听 8010）
Invoke-RestMethod http://127.0.0.1:8010/health | ConvertTo-Json
# 期望：status=ok, model=qwen3.8:27b, model_reachable=true, host_bridge_reachable=true

# 执行桥（宿主机进程，监听 8011，负责真正跑代码）
$key = (Get-Content .env | Where-Object { $_ -match '^HOST_BRIDGE_KEY=' }) -replace '^HOST_BRIDGE_KEY=',''
Invoke-RestMethod http://127.0.0.1:8011/health -Headers @{ Authorization = "Bearer $key" } | ConvertTo-Json

# Docker 与模型
docker ps --format "{{.Names}} {{.Status}}"
Invoke-RestMethod http://127.0.0.1:11434/api/tags | Select-Object -ExpandProperty models | Select-Object name
```

一条命令全启（含 Ollama、Docker、容器、浏览器工作台）：

```powershell
.\start.cmd
```

## 2. 看冻结了哪些题

```powershell
python -m evaluation.grounded_v1.run_task --list
```

15 题：`oam-01..06` 树冠提取、`oam-07..12` 树冠统计、`gabench-09/12/42` GIS。每题 900 秒预算、3 次重复。

## 3. 先探查环境（不占正式槽位）

```powershell
$env:RUNTIME_API_KEY = (Get-Content .env | Where-Object { $_ -match '^RUNTIME_API_KEY=' }) -replace '^RUNTIME_API_KEY=',''
python -m evaluation.grounded_v1.run_task --task oam-07 --probe --timeout 240
```

`--probe` 只问"你的环境有什么、输入是什么"，**不评分、不计入分母**，用独立的包名存放，不可能被误当成正式试验。任何 Agent 成绩在采集前都应先跑一次探查。

## 4. 跑一次正式试验并评分

```powershell
# 第 1 次重复
python -m evaluation.grounded_v1.run_task --task oam-01 --repeat 1

# 先只采集、看轨迹、暂不评分
python -m evaluation.grounded_v1.run_task --task oam-01 --repeat 1 --no-grade

# 完整跑一组（每题 3 次）
foreach ($t in @('oam-01','oam-02','oam-03','oam-04','oam-05','oam-06')) {
  foreach ($r in 1..3) { python -m evaluation.grounded_v1.run_task --task $t --repeat $r }
}
```

这条命令做的事，按顺序：

1. 从 `evaluation/grounded_v1/tasks.grounded-v1.1.json` 读该题冻结的任务书（题目、公开输入、评分规则、预算、容差）；
2. 新建一个**干净会话**（每次试验独立，UUID 会话号），上传该题的公开输入；
3. 发出题目（`TaskSpec.question`，即冻结的题面），等待终态；
4. 收集 Agent 产出的全部产物，落到 `data/runs/grounded-<task>-r<n>/`；
5. 用只读评分器打分，写 `grade-report.json`。

退出码：`0` = 交付有效（V=1），`2` = 未通过或未采集到，`3` = 重复评分结果不一致。

**正式槽位不会被覆盖**：同名目录已存在会直接报错，必须另选 repeat 或先移走。这是防止拿失败例反复重跑再挑最好一次。

## 5. 采集目录长什么样

```
data/runs/grounded-oam-01-r1/
  trace.json              # 配置快照、终态、重复号（评分器读它）
  raw/events.json         # 全量事件流：模型调用、工具调用、思考、消息
  raw/turns.json          # 轮次与 checkpoint
  raw/transport.json      # 采集器重试/等待计数
  artifacts/asset_<id>-<原文件名>   # Agent 交付的每个产物
  grade-report.json       # 评分结果：质量、V、过程检查、失败原因、证据
```

产物名保留原始文件名，评分器按原名判断"哪个是输入回传、哪个是交付"。

## 6. ⚠ 当前部署的阻断问题（已实测确认）

**Agent 的代码沙箱里没有任何地理空间库，连 numpy 都没有。**

这不是猜测。探查事件原文（`data/runs/grounded-oam-07-probe/raw/events.json`）：

```json
{"name": "environment_check", "arguments": {"modules": ["rasterio","numpy","scipy","geopandas","shapely","pyproj"]}}
```

结果：

```
rasterio   ModuleNotFoundError
numpy      ModuleNotFoundError
scipy      ModuleNotFoundError
geopandas  ModuleNotFoundError
shapely    ModuleNotFoundError
pyproj     ModuleNotFoundError
installed_package_count: 0
```

Agent 自己的结论也是"**可用库：无**"。

### 6.1 为什么会这样

你的系统有**两个不同的执行环境**：

| 环境 | 镜像 | 有什么 | 干什么 |
|---|---|---|---|
| Runtime 服务本体 | `forestry_runtime-runtime`（内置 core + 遥测依赖） | rasterio、numpy、scipy、GDAL | 提供 API、只读检视文件 |
| **Agent 代码沙箱** | `python:3.12-slim`（裸镜像） | **什么都没有** | 真正执行 Agent 写的 python |

`inspect_file`、`inspect_raster` 这类工具跑在 Runtime 容器里，所以能读出"2048×2048、EPSG:3395"；但一旦要求 Agent 自己写代码算象元，就落到裸镜像里，`import rasterio` 直接失败。

计划的阶段 B 要求对照双方"预装库"一致，而当前部署的运行环境对本题库而言是**不能执行**的。

### 6.2 我实测过的修复路径

Agent 自带 `dependency_install` 工具，桥接器会用 `pip install --target /deps` 安装，`/deps` 再以 `PYTHONPATH=/deps` 挂进沙箱。这条路**本身是通的**，但不够：

```
① pip install rasterio → 成功（能联网取 wheel）
② 裸镜像里 import rasterio → ImportError: libexpat.so.1: cannot open shared object file
③ 装上 libexpat1 后再 import → 成功：rasterio 1.5.1 / GDAL 3.12.4 / numpy 2.5.3
```

即缺的是**系统级 .so**，pip 装不出来，必须在镜像层解决。

### 6.3 建议的修法

建一个"作业镜像"，把系统库和依赖预装进去，然后把 `AGENT_JOB_IMAGE` 指向它（该变量在 `.env.example` 与 `setup.ps1` 中已存在，默认 `python:3.12-slim`）：

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libexpat1 libgdal32 libgeos-c1v5 proj-data && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir rasterio==1.5.1 numpy==2.2.5 scipy==1.15.3 \
      shapely pyproj fiona geopandas scikit-image
```

然后在 `.env` 里设 `AGENT_JOB_IMAGE=<该镜像>`，重启栈。

这样做同时满足计划的两条要求：**同模型同资源的对照**必须预装一致的库，**不同系统之间**也不因缺库而把环境问题记成算法失败。

## 7. 建议的执行顺序

1. 修作业镜像（第 6.3 节），重启栈；
2. 对每一族各跑一次 `--probe`，确认 `import rasterio/numpy/scipy` 可用、GIS 题的库可用；
3. 先跑 `oam-07`（最简单，只需读掩膜计数）一轮，确认端到端能出 `grade-report.json`；
4. 再按第 4 节把 15 题 × 3 次跑满；
5. 全部采完后再看分组成绩——**不要在采集过程中根据成绩改题面或调评分规则**。

在作业镜像修好之前，任何树冠提取/统计题的结果都只会是"环境失败"，那不是领域能力的证据。
