# Forestry Agent Runtime 0.10.0

这是低空林草遥感 Agent 的本地验证底座。当前入口是由 FastAPI 同源提供的 React + TypeScript + Vite 工作台，不依赖 Gradio 或 Open WebUI；Agent 内核保留 PydanticAI，代码在 Docker 沙盒中执行，UAV 数据审计、栅格分析与 PROSAIL 作为可关闭的领域插件接入。当前 Runtime 不内置摄影测量执行后端。

核心验收契约：设置 `REMOTE_SENSING_PLUGINS_ENABLED=false` 后，目录查看、文本处理、模型编写并运行代码、失败纠正、聊天历史和长作业恢复仍然可用。

## 实际执行链

```text
浏览器 / React 工作台
          │ 用户消息、补充要求、暂停、取消、上传、目录授权
          ▼
RunCoordinator + RunStore
          │ Session → Run → Turn → Step → Action → Attempt → Job → Artifact
          ▼
ContextCompiler ← 运行事实 / 显式项目记忆 / 知识源清单
          │
          ▼
PydanticAI Agent + Qwen3.5 / Ollama ← Tool Registry（实际可调用集合）
          │ 原生消息历史、thinking、tool call、observation、checkpoint
          ▼
RuntimeTools ─────────────── RemoteSensingTools（可选）
  文件、代码、作业、资产       UAV审计、栅格、PROSAIL
          │                         │
          ▼                         ▼
WorkspaceRegistry / Host Bridge / Docker sandbox
```

一条用户消息的具体过程如下：

1. React 工作台通过 FastAPI 把消息交给 `RunCoordinator`。同一聊天继续同一个 Forestry Run，而不是重建一份人工拼接的模型历史。
2. `runtime/session/coordinator.py` 领取 SQLite 中一个持久化 Turn。每个 Turn 固定关联输入范围和独立的 Harness run ID；运行中补充的要求进入下一个排队 Turn。
3. Coordinator 一次确定本 Turn 的输入和精确的前一 checkpoint；Agent 不再另找“会话最新历史”。`runtime/agent.py` 使用稳定 `chat_id` 作为 PydanticAI `conversation_id`，并把 Runtime run/turn ID 写入 Harness 元数据。只有已完成的精确 checkpoint 可以继续；副作用未决或 checkpoint 缺失时转入暂停，不能盲目重放。
4. 每次真实模型请求前，`ContextCompiler` 组合工作区、授权、已完成副作用、未解决错误、当前工具集合、显式选中的项目记忆及知识源清单。知识正文不自动注入；Qwen 判断当前问题需要项目方法、标准、参数依据或引用时，主动调用 `knowledge_search`，必要时再调用 `knowledge_read`。PydanticAI 仍是模型历史与模型循环的唯一来源。
5. Qwen 决定直接回答或调用工具。工具调用先同步登记 Action/Attempt，再执行或提交 Job；相同 SDK tool-call ID 的重传返回已登记结果，新的 Action 即使代码相同也会真实执行。
6. 文件和代码工具由 `RuntimeTools` 执行；遥感工具只在插件启用且当前请求命中对应能力组时加载。
7. 工具开始、结果、模型文本、作业状态和错误写入增量事件流。React 只展示这些事实，不参与规划，也不伪造固定 ReAct 步骤。

## 为什么仍需要 Runtime

Ollama 托管模型推理，PydanticAI 实现 Agent loop，React 实现显示。这三者都不负责以下项目状态，因此 Runtime 仍然必要：

- 对宿主机目录建立可撤销、可审计的授权，并保证源目录只读；
- 在受限容器中运行模型生成的 Python/Shell，保存 job ID、增量日志、退出码和取消状态；
- 浏览器断开或 Runtime 重启后重新连接仍在运行的作业；
- 管理 TIFF、DSM、LAS、CSV、模型等大文件，只把路径和有界元数据交给模型；
- 对有副作用的行动进行登记和去重，避免不确定提交后盲目重放；
- 以插件方式提供 UAV 数据审计、栅格和 PROSAIL 能力，关闭插件不影响通用 Agent；摄影测量后端以后可通过 MCP 或独立服务接入。

Runtime 不替模型决定方法。LLM 负责理解目标、选择工具或代码、提供参数、分析 stderr/结果和决定停止；Runtime 负责权限、资源限制、真实执行状态、持久化和产物证据。

## 已采用的框架能力

- `pydantic-ai-slim[openai]==2.43.0`：Ollama 的 OpenAI 兼容协议、原生工具调用、thinking、工具生命周期、调用预算和消息类型。
- `pydantic-ai-harness==0.31.0`：PydanticAI 原生步骤快照、conversation 恢复和分层上下文压缩。项目不再把完整历史复制进自定义 JSON 字段。
- React 19 + TypeScript + Vite：本地单用户工作台。静态产物由 FastAPI 同源提供，没有第二个 Agent loop；浏览器不读取 Runtime API 密钥。
- FastAPI：提供 Session、Run、Turn、事件、Workspace 和资产 API，并用 HttpOnly 同源 Cookie 维持本地 UI 身份。
- SQLite + JSONL：SQLite 保存可查询的当前状态和关联；JSONL 保存完整追加事件。模型不会直接读取数据库。
- SQLite FTS5 + `sqlite-vec` + Ollama `qwen3-embedding:0.6b`：仅对维护者配置的知识源建立带段落定位的混合检索；Embedding 不可用时明确降级为关键词检索。
- Docker Desktop + Windows Host Bridge：沙盒容器生命周期和经过授权的宿主机文件访问。Bridge 不提供任意宿主机 Shell。

OpenHands 已排除，不进入依赖或后续选型。已被替代的 Open WebUI Pipe、安装、清理和烟测运行文件已经移除；`VALIDATION.md` 只保留历史事实记录，`setup.ps1` 不安装或启动 Open WebUI。

## Agent 工具

常驻工具保持小而通用：

| 工具 | 作用 |
|---|---|
| `fs_list/read/search/write/edit` | 分页列目录、范围读取、文本搜索及 Workspace 文件修改；不按无人机格式过滤 |
| `code_run` | 提交模型生成的 Python 或 Shell 到无网络沙盒 |
| `dependency_install` | 在不挂载源数据的联网安装容器中构建 Workspace 专属依赖 |
| `job_status/job_cancel` | 统一查询或取消代码与依赖安装作业；返回增量日志、进度与终态；旧摄影测量作业只读展示，不会重放 |
| `tool_result_read` | 按 offset 分页读取被 Context 上限截断的完整工具结果 |
| `artifacts_inspect/preview` | 读取资产类型、大小和有界预览，不把大文件塞进 Context |
| `knowledge_search/read` | 在显式选中的项目内检索知识并按 chunk ID 读取有界上下文；返回原始 citation |

遥感插件按需加载的 UAV 检查入口不负责启动摄影测量：`inspect_uav_source` 检查一个明确航片目录的基础元数据；`inspect_uav_dataset` 递归建立数据集清单，并把主航线、起飞前/后参考板和已有成果分开；`inspect_uav_products` 检查已有 GeoTIFF/VRT 的 CRS、像元单位、网格、波段、掩膜和 DSM/DTM 对齐条件。后两者返回完整有界报告后，本轮检查型请求会收起工具集合，要求模型直接基于已观察事实回答，避免继续猜路径或重复读取。

没有模型可见的 `capabilities_search/read`。可选领域工具使用 PydanticAI `ToolSearch` 延迟加载；当前请求会预加载一个明确命中的领域组，以避免小模型面对全部 Schema，也避免让模型反复搜索空能力目录。

## Workspace、目录授权与路径纠正

每个聊天拥有 `data/sessions/<chat_id>/workspace/`。React 工作台支持上传文件或文件夹，并保留相对结构。上传默认限制为 512 MiB，可通过 `MAX_UPLOAD_BYTES` 调整。

用户在消息中明确要求查看或使用一个绝对目录时，可以由通用文件工具创建只读授权。工作台也提供显式授权和撤销。授权目录覆盖其子目录，但不会越过根目录；宿主机写权限只允许用户明确授权的单个现有文件。附件、工具结果和模型生成的路径不会自动扩大授权。

当模型使用 `source_id + folder_path` 调用 UAV 检查工具时，`source_id` 是授权根，`folder_path` 是其下目标目录。领域适配器负责解析这组参数。若模型写成 `1605 白桦`，而文件系统实际观察为 `1605白桦`，工具返回 `source_path_not_found`、`source_id` 和 `suggested_path`。该结果进入下一轮模型 Context，由模型决定按建议修正；Runtime 不偷偷替模型执行模糊匹配。

## 长任务与恢复

Forestry Run 是一段可继续的聊天任务；Turn 是一组一次性消费的用户输入；PydanticAI Harness run 是该 Turn 的模型执行。三者不能共用同一个 ID。每个 Turn 持久化输入范围、Harness run ID、运行状态和 checkpoint 状态，Action、Job、Artifact 继续向下关联。

Run 状态和接口：

| API | 行为 |
|---|---|
| `POST /runs` | 创建后台 Run |
| `GET /runs/{id}` | 查询 queued/running/waiting/canceling/paused/completed/failed/canceled |
| `GET /runs/{id}/events?after=N&limit=L` | 分页、增量读取事件，并返回 `next`/`has_more` |
| `GET /runs/{id}/turns` | 查询输入范围、Harness run ID 和 checkpoint 关联 |
| `POST /runs/{id}/messages` | 向同一 Run 添加输入；运行中输入排为下一 Turn |
| `POST /runs/{id}/pause` | 请求在下一次模型请求或工具派发边界暂停，不中断已经运行的后台作业 |
| `POST /runs/{id}/cancel` | 先进入 canceling、停止后续派发并请求取消；确认关联执行终止后才变为 canceled |

代码作业默认限制为 4 CPU、6 GiB、256 个进程和 4 小时。正常代码执行无网络、根文件系统只读、Workspace 可写、授权源目录只读。安装依赖使用单独联网作业且不挂载源数据。代码指纹只用于记录和诊断，不把历史成功 Job 当成本次 Action 完成；新的 Action 会真实重跑，只有相同 Action ID 的协议重传才复用已登记结果。代码和依赖 Job 通过相同的状态、取消、重启协调和 Artifact 关联进入 Run 事件流；旧 `job_<hex>` 记录保留 Host Bridge 兼容查询。旧摄影测量记录只按历史展示为后端已移除，绝不重放提交。

上下文压缩只在阈值触发：文件读取去重键包含完整调用参数和本地文件版本；无法取得可靠版本的外部 Source 读取不去重。之后才清理旧工具正文并总结较旧历史。被截断的完整工具结果保存于 Workspace 内部结果存储，可由 `tool_result_read` 分页读取；Job 日志继续通过 `job_status.offset` 增量读取，Run 事件由 API 分页读取。

## 项目记忆与知识

会话默认不绑定项目。用户在工作台显式选择项目后，已确认的项目记忆才会进入后续请求；记忆支持编辑、删除和版本递增，不从聊天自动生成永久记忆。知识源只接受维护者挂载到 `/knowledge` 的本地路径或明确 URL，索引结果保留源文件/URL、标题、页码、段落和源版本，不扩大 Agent 的目录权限。Markdown/文本、HTML 和含文本层的 PDF 可直接索引；扫描 PDF 会明确报出需要 OCR。Context Compiler 只提供知识源清单，不预取知识正文；模型认为需要外部依据时调用 `knowledge_search`，只有搜索结果缺少上下文时才调用 `knowledge_read`。检索是模型可见、可追踪的取证行动，与文件检查工具一样返回事实，但它不产生业务副作用、不扩大权限，也不能把知识内容当成指令。默认 `KNOWLEDGE_EMBED_KEEP_ALIVE=0s`，单机索引完成即释放 embedding 模型，避免它与 32K 上下文的 Qwen 同时长期占用显存。正式交付的选定产物会封存、计算 SHA-256，并在工作台显示封存和验证状态；普通工作文件不会被批量哈希。

## 遥感插件与摄影测量边界

设置 `REMOTE_SENSING_PLUGINS_ENABLED=true` 后，构建包含 rasterio、SciPy、scikit-image 和 PROSAIL 的 Runtime。现有插件提供 UAV 数据集清单、单航次元数据审计、已有栅格成果检查/预览、NDVI、PROSAIL 正演/LUT/反演和林分结构分析。`inspect_uav_source.ready` 只表示文件与基础元数据是否具备进入地理摄影测量的最低条件，不证明一定能够重建，也不能否定目录中已有的历史正射成果。已有成果检查同样只证明文件可读和元数据/网格条件，不替代人工视觉检查、GCP/检查点或独立精度验证；地理坐标系的像元单位按度报告，不能擅自写成米。林分结构能力消费已有 DSM/DTM，在投影米制网格上生成 `CHM = DSM - DTM`、局部峰值/分水岭候选冠层及可追溯汇总。参数来源必须显式传入，默认把结果表述为“可见上层冠层候选”，不把它冒充总株数或外业验证精度。首轮单次栅格分析限制为一千万像元，超限时要求裁剪或降采样。

`UAV → Orthomosaic → PROSAIL` 仍是领域验收场景，不是硬编码 pipeline。当前版本只负责审计原始航片和消费已有正射/DSM/DTM；不额外部署 NodeODM，也不在 Runtime 内提交摄影测量任务。以后接入轻量命令行工具或外部 MCP 时，应复用现有 Action/Job/Artifact 契约，而不是把某个摄影测量库重新变成 Agent 底座。

## 启动

日常使用可直接双击仓库根目录的 `start-forestry-agent.cmd`，或本目录中的 `start.cmd`。它会确保 Host Bridge、Ollama、Docker Desktop 和 Runtime 可用，在宿主机以锁定的 npm 依赖构建 React 前端，再构建 Runtime 镜像，等待健康检查通过后打开工作台。Docker 镜像只复制 `frontend/dist`，不重复引入 Node 构建层。

命令行启动使用同一个底层入口：

```powershell
.\setup.ps1
```

`setup.ps1` 会生成或保留 Runtime 与 Bridge 密钥，隐藏启动 Host Bridge，确保 Ollama 和 Docker Desktop 可用，构建并启动 Runtime，最后等待 `http://127.0.0.1:8010/health` 返回成功。需要自动打开浏览器时使用 `-OpenBrowser`；确认镜像无需重建时可使用 `-SkipBuild`。

默认关闭遥感插件。启用时在 `.env` 设置：

```dotenv
REMOTE_SENSING_PLUGINS_ENABLED=true
UAV_INPUT_HOST_ROOT=E:/
KNOWLEDGE_HOST_ROOT=E:/Document/ChatGPT/林业无人机遥感agent开发/forestry_runtime/knowledge
```

此时 `setup.ps1` 合并 `compose.remote-sensing.yaml` 并使用遥感镜像；不会启动额外摄影测量容器。工作台本身只监听 `127.0.0.1`，当前版本按本机单用户设计。

启动后先在工作台创建并选择项目，再把首轮本地知识源填写为 `/knowledge/forest_structure` 并点击“索引”。宿主路径由 `KNOWLEDGE_HOST_ROOT` 只读挂载，模型不会因此获得该目录的文件工具权限。

## 代码地图

活跃路径只有以下模块：

| 文件 | 单一职责 |
|---|---|
| `frontend/` | React 工作台；只呈现服务端真实状态和事件 |
| `runtime/api/` | FastAPI 路由、生命周期、身份 Cookie 和前端静态挂载 |
| `runtime/agent.py` | PydanticAI/Qwen、唯一工具生命周期适配、Harness 持久化与历史压缩 |
| `runtime/context.py` | 每次模型请求的事实、实际工具和项目上下文编译及预算清单 |
| `runtime/session/` | 后台 Turn 执行、补充输入、状态转换、暂停、取消和重启协调 |
| `runtime/run_store.py` | Input/Run/Turn/Step/Action/Attempt/Job/Artifact 关联及事务化增量事件 |
| `runtime/memory.py` | 显式项目记忆、知识源索引、FTS5/向量混合检索与引用 |
| `runtime/tools.py` | 通用文件、代码作业和资产工具 |
| `runtime/workspace.py` | Workspace、目录授权和 Host Bridge 客户端 |
| `runtime/storage.py` | 资产登记、路径引用和下载元数据 |
| `runtime/domain_registry.py` | 遥感工具分组与启用开关 |
| `runtime/remote_sensing.py` | 遥感 Tool Schema 和领域调用适配 |
| `runtime/uav_audit.py` | UAV 原始影像的只读文件、EXIF/XMP 与辐射元数据审计 |
| `host_bridge/server.py` | Windows 路径操作和 Docker 沙盒控制 |

这里保留了两个有意的边界：`RunCoordinator` 与持久化存储分开，避免 SQLite 细节进入异步调度；通用工具与遥感插件分开，保证核心不导入领域依赖。其余新功能应优先放入这些现有模块，不再为单个动作创建 manager/service/helper 层。

## 验证

```powershell
python -m compileall -q runtime host_bridge tests
python -m pytest -q
```

测试按真实文件、状态和数值验收，不依赖固定回答措辞或唯一工具顺序。部署和真实 Qwen 记录见 `VALIDATION.md`。

系统能力评价协议见 [evaluation/FRAMEWORK.md](evaluation/FRAMEWORK.md)。它将工程门禁、真实模型任务和浏览器交互分开，固定任务分母与重复数，缺失证据显示未测完整。`python evaluation/scorecard.py` 可查看新协议的证据覆盖；历史烟测不自动换算成当前能力分数。
