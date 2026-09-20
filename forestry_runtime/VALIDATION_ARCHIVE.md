# 历史验证记录（归档）

## 2026-09-19：系统评价协议与离线计分器 v0.1

- 基于当前代码/验收脚本和用户研究报告建立 `evaluation/FRAMEWORK.md`。只采用已核验的结果/过程分离、数值容差、类型化产物验证等原则；不把报告建议的十维权重或产品分档当作行业标准。
- 固定首轮 18 个任务契约：12 个 agent 任务各重复 3 次、4 个 engineering 门禁、2 个 ui 案例各重复 3 次。三条轨道分开统计，agent 内按通用执行、林业 UAV、知识/上下文三个组等权汇总；不要求固定工具名或黄金轨迹，不评分私有 thinking。
- 离线 `scorecard.py` 保留预定分母，缺检查/证据返回 unknown；限制同配置/轨道、唯一 trial 与重复槽位；有证据的门禁失败不能被成功率抵消。数值比较要求明确单位、有限数值和预定容差。当前无新协议证据包，真实输出为 incomplete、主分 null，不能把既有 85 项测试或历史 OpenHands 结果换算为 Agent 分数。
- 计分器的 9 项测试及 4 个子测试通过，涵盖缺证据、错轨道/mock、配置混杂、门禁失败、超时、重复槽位及数值单位/NaN；compileall 通过。测试中使用的记录是计分逻辑 fixture，不是模型验收。独立领域 verifier、浏览器采集及 36 次真实 Qwen pilot 尚待按契约接入和运行。

## 2026-09-19：React 对话发送与增量事件恢复修复

- 根因一是会话列表刷新会改变 `sessions` 引用，而负责加载所选会话的 Effect 同时依赖 `sessions` 并无条件清空 `events`；发送接口成功后的 `loadSessions()` 因此把刚显示的对话重置为空。现仅在 `chat_id` 真正切换时清空对话，会话标题、Run 状态或项目元数据刷新不再影响现有消息。
- 根因二是事件轮询只依赖 `run.id`；同一 Run 的下一 Turn 沿用原 ID，上一轮终态退出的轮询不会重新启动。现由成功提交递增轮询 epoch，从已接收的最大稳定事件序号继续读取，并按 `seq` 合并去重，不清空历史。
- 用户点击发送后立即显示本地待确认消息并清空输入框，不再等待 HTTP 返回或模型开始推理；持久化 `user_message` 事件携带稳定 `input_id`，到达后替换待确认状态。失败会移除待确认消息并恢复原输入。消息列表同时滚动到新消息，切换任务期间晚到的发送响应不会把界面切回旧任务。
- FastAPI 回归验证首条及后续 Turn 的 `user_message.input_id` 均可通过增量事件读取；全量工程测试 `85 passed`，Python compileall 和 React/Vite 生产构建通过。随后 Docker Engine 恢复到 29.7.2，已重建部署修复镜像。真实 Qwen HTTP 两轮 Run `run_841c0698a4844cb08bd59e88cdb7fc5e` 沿用同一个 Run，提交耗时分别为 146 ms、87 ms，两次 input_id 均可增量读取，两轮均 completed，第二轮回答“第二轮已收到。”；自行创建的测试会话随后删除。浏览器自动化初始化失败，尚无浏览器点击/渲染验收，HTTP 成功不能替代这项证据。

## 2026-09-19：移除内置摄影测量后端、模型按需 RAG 与真实林业 UAV 回归

- 当前 Runtime 已移除 NodeODM 客户端、摄影测量提交/等待/重试/成果收集工具及 `runtime/orthomosaic.py`。`compose.remote-sensing.yaml` 只启动 Runtime，不再创建额外摄影测量容器或工作卷；历史摄影测量 Job 只返回 `local_photogrammetry_backend_removed`，不会自动重放。以后可通过独立 MCP/服务接入摄影测量，当前未引入替代框架。
- 遥感插件保留只读 `inspect_uav_source`，新增 `inspect_uav_dataset` 与 `inspect_uav_products`，领域组仍为按需加载的 `uav-data-audit`。数据集清单以精确相对路径区分主航线、起飞前/后参考板和已有地理栅格；成果检查只读取 GeoTIFF/VRT 的 CRS、像元单位、网格、波段、掩膜、有界统计及 DSM/DTM 对齐条件，不启动摄影测量。`ready` 只表示文件、GPS、波段和基础元数据未发现阻断项，不证明重建必然成功，也不能否定已有历史正射成果。未定义的 RTK 元数据不再向模型暴露原始状态码，并明确禁止推断固定解、成功或精度。
- Context Compiler 不再自动检索知识正文，只注入显式项目记忆和知识源清单。Qwen 判断需要项目方法、标准、参数依据或引用时调用 `knowledge_search`，缺少邻近语境时再调用 `knowledge_read`；普通问题不触发 RAG。单 Turn 最多允许 3 次成功知识搜索，第四次返回结构化预算错误，阻止 4B 模型无界改写查询。
- 知识 citation 改为 ASCII 稳定引用 `knowledge://<source_id>/<chunk_id>#page/segment`，原始文件/URL 仍保存在 `document` 与 source locator 中。原因是实际 Qwen 会改写含中文的 Windows 绝对路径，导致看似引用但无法精确复用；索引版本升级为 `stable-citations-v3` 并真实重建变化内容。
- 使用只读真实目录 `E:\0730-多光谱正射(晴天飞行)\1_原始数据\1605白桦` 回归：清单观察到 328 个文件，其中主航线 294 张影像/49 个 CaptureUUID 曝光组，DJI FC6360，Blue/Green/NIR/Red/RedEdge 各 49 张，GPS 294/294，245 个多光谱文件包含波段、辐照度、辐射定标、渐晕与畸变元数据；`参考板/起飞前` 与 `参考板/起飞后` 分开为 12 张/2 组和 18 张/3 组，没有混入主航线统计。真实已有成果 `E:\0730-多光谱正射(晴天飞行)\3_正射影像\1605-白桦.tif` 可读，为 3216×2940、5 波段 float32、EPSG:4326、像元单位为度并含 2/4/8/16 overviews；其波段用途未写入描述，目录中没有 DSM/DTM，因此不能从现有文件构建 CHM，也不能据此宣称测量精度。本次未修改源数据，未启动正射或其他摄影测量任务。
- 真实 Ollama `qwen3.5:4b` 保持 thinking、temperature 0.2、context 32768、输出预算 8192；`qwen3-embedding:0.6b` 索引状态为 `ready`，并在检索后按 `KNOWLEDGE_EMBED_KEEP_ALIVE=0s` 释放，避免两个模型长期争用显存。四个固定领域验收均为 `passed=true`：数据集清单、已有正射质量、DSM/DTM/CHM 条件和知识边界；前三项各 2 次模型请求、1 次对应检查工具，知识项 2 次模型请求、1 次 `knowledge_search` 并返回稳定 citation。完整有界检查报告返回后，本轮检查型请求不再暴露其他工具，消除了模型把 `relative_path` 当成 `asset_id` 后继续空转的问题。普通 `2+2` 案例仍为 1 次模型请求、0 工具调用，证明 RAG 不是请求前自动执行。
- 新增知识文档 `uav_dataset_roles.md`、`geospatial_product_qa.md`、`forest_method_boundaries.md`，分别覆盖航次/参考板/成果角色、GeoTIFF 与 DSM/DTM/CHM 质量边界、分水岭候选与总株数的区别。内容引用 ODM、DJI、Rasterio 和 scikit-image 官方资料，模型检索结果保留 `knowledge://source/chunk#segment` 定位。
- 全量工程测试 `85 passed`；Python compileall、React/Vite 生产构建、两层 Compose 配置及 `setup.ps1` 语法检查通过。Compose 服务清单不含 NodeODM。真实模型验收脚本现归档为 `scripts/legacy_verify_qwen_uav_rag.py`，支持按 `audit`、`inventory`、`products`、`chm`、`knowledge`、`direct` 独立复测。部署核验时 `docker info` 仍无法连接 Linux Engine，Runtime `127.0.0.1:8010` 未监听；Docker Desktop 4.89.0 最新后端日志明确失败于 `sailor-ingest.sock` 无法改名，故本批没有声称新镜像已部署。本机 Ollama 与 Host Bridge 的真实验收不受此影响。

## 2026-09-19：林分结构知识与工具首批实现

- 知识索引现支持 Markdown/文本、HTML 和含文本层 PDF，检索结果携带标题、页码/段落、源版本与精确 citation；扫描 PDF 明确进入需 OCR 的失败状态。索引格式版本进入内容摘要，旧索引会真实重建；`knowledge_read` 的相邻读取限定在同一文档和页码，避免多页 PDF 的相同 ordinal 串页。
- 通用工具增加 `knowledge_search` 与 `knowledge_read`，只读取当前会话显式选择的项目。React 项目区可新增、刷新、查看版本/状态并删除知识源。Compose 将维护者目录只读挂载到 `/knowledge`，修复容器无法读取 Windows `KNOWLEDGE_ROOTS` 路径的问题。
- ODM 参数增加独立 `build_dtm` 和显式 `reuse_existing`；相同 Action 的协议重传仍幂等，而用户发起的新 Action 可要求真实重跑。成果检查分别记录 DSM/DTM。
- 新增 `build_canopy_height_model`、`delineate_tree_candidates`、`summarize_forest_structure`：要求投影米制 DSM/DTM、显式垂直基准与算法参数来源，保留负 CHM 事实，输出带来源标签的 CHM、候选标签、冠层/顶点 GeoJSON、CSV/JSON 汇总。结果只表述为可见上层冠层候选；没有独立参考数据时不报告准确率。
- 全量工程测试 88 项通过；Python compileall、Vite 生产构建和 Compose 配置检查通过。当前 Docker Desktop Linux engine 因本机 `sailor-ingest.sock` / secrets engine 运行时 socket 故障仍未启动；相关目录仅改名留档，未删除镜像、卷或项目数据。Ollama HTTP 服务当前未监听，因此本批尚未完成镜像构建、部署和真实 Qwen 验收。

## 2026-09-18：可信执行链、逐请求上下文与项目记忆

- PydanticAI 现为模型历史、工具协议与请求生命周期的唯一权威。真实请求边界由公开 Hooks 记录；Coordinator 把本 Turn 的精确输入和前一 Harness run ID 传给 Agent，删除了 Agent 自行搜索会话最新历史的恢复分支。
- SQLite 新增稳定 Input、Step、Action Attempt 与恢复关联。Action/Attempt 的执行前登记、结果状态和对应事件在同一事务提交；相同 SDK tool-call ID 重传不重复执行，新的 Action 即使代码一致也会生成新 Job。故障注入已验证事件写入失败时 Action/Attempt 不会半提交。
- 取消先进入 `canceling`；Docker/Job 状态暂时无法确认时保持 unknown 和非终态，不误判 orphaned/canceled。作业创建 Action/Turn 不会被后续状态观察覆盖。
- 每次模型请求都记录 Context 清单，包括历史消息数、实际工具名/Schema 字符、运行事实、项目绑定、检索模式、估算输入和输出预留。读取去重包含完整参数与读取时版本；编辑支持预期版本冲突；路径错误返回精确引用和候选而不静默修正。
- 新增显式项目、确认后记忆、版本化编辑/删除，以及受 `KNOWLEDGE_ROOTS` 限制的本地/明确 URL 知识索引。真实 `qwen3-embedding:0.6b` 返回 1024 维向量，SQLite FTS5 + `sqlite-vec` 验收得到 `ready/hybrid` 和精确的 `README.md#segment-N` 引用；向量不可用时状态明确为 `keyword_only`。
- 知识索引 API 实测在 111 ms 内以 HTTP 202 返回持久化 `indexing` 状态，随后后台转为 `ready`；相同内容再次索引复用既有 5 个 chunk/向量，不重建未变化内容。真实知识回答 Run `run_5f7264f1801847c6b181dcbce3292362` 为 `retrieval_mode=hybrid`、`budget_ok=true`、0 次工具调用，并输出可追溯的 `README.md#segment-2` 本地来源路径。
- React 增加项目选择、记忆确认/编辑/删除、Run 状态版本、canceling/恢复事实，以及产物类型、封存和验证状态。消息正文继续由 `react-markdown` + GFM 呈现，thinking 只显示 SDK 返回的原生内容。
- 真实 Ollama `qwen3.5:4b`（thinking 开启、temperature 0.2、context 32768、输出 8192）完成两 Turn CSV 写入、精确读取、带版本差异编辑和复读；重复 `input_id` 没有新增 Turn。遥感插件关闭时仍可使用 12 个通用工具。最新显式绑定项目的真实 Run `run_af3534da2788421da34ec6107e0f2723` 用 1 次模型请求、0 次工具调用准确复述“源目录只读、修改写入 Workspace”；请求清单为 `project_bound=true`、无知识源时正确标记 `retrieval_mode=keyword`、`estimated_total_tokens=10202/32768`、`budget_ok=true`。真实知识源索引另行得到 `hybrid`，不再把只有记忆、没有向量条目的请求误标为混合检索。
- 删除已无生产调用者的 Open WebUI Pipe、安装/清理/烟测脚本和对应旧 Pipe 测试；旧数据不迁移、不重放，历史验证记录仍保留。当前启动链只包含 React/FastAPI、Ollama、Host Bridge、Docker 和可选 NodeODM。
- 全量 81 项活跃工程测试通过，包括 10,005 条事件的 500 条分页顺序/终态、事务故障注入、重复输入、行动重传、输入变化重跑、取消不确定态、checkpoint 精确恢复、插件隔离、知识权限/引用及向量实际落库。Vite 生产构建、两套 Compose 配置、Python compileall 和 `setup.ps1` 语法检查通过。
- Docker Desktop 起初因本机 `sailor-ingest.sock.stale` 重命名失败而退出；未删除或重置 Docker 运行时文件。Engine 后续恢复到 29.7.2 后，核心镜像真实构建并确认插件关闭时恰有 12 个通用工具、未导入 `runtime.remote_sensing`、`sqlite-vec` 可用；遥感生产镜像也已重建并部署，`/health`、React UI、NodeODM 均正常。部署后真实 Run `run_cc4c71cb547245f091b34b26d832a46a` 由 Qwen 用 `fs_write → fs_read` 创建并核对 CSV，3 次模型请求、2 次成功工具调用、`budget_ok=true`；测试会话随后已请求清理。

## 2026-09-17：上下文纠错、统一 Job 与真实沙盒验证 0.10.0

- 文件读取去重键现包含 scope、分页、范围、查询、glob 等完整参数以及本地文件版本；外部 Source 没有可靠版本令牌时不去重，避免删掉必要观察。
- 超过 Context 返回上限的工具结果会得到 `result_id`、总字符数和有界 excerpt；`tool_result_read` 可按 offset 读完整 JSON，不再只提示“日志中存在”。
- 常驻 `job_status/job_cancel` 统一处理代码、依赖安装和 ODM Job。RunCoordinator 对两类 Job 使用同一套终态、取消和重启协调；`job_reconciled` 更新不会丢失原 Action/Turn，ODM Artifact 带来源 `job_id`。
- NodeODM 取消使用官方 `/task/cancel` 接口；服务端持久化 canceled 终态。React 行动流为成功的 `fs_edit` 显示真实替换前后差异，并在 Job/Turn 结束后刷新 Artifact。
- 修复 Linux Runtime 把 Windows `RUNTIME_DATA_HOST_ROOT` 误解析成 `/app/E:\...` 的路径错误；不确定提交核对失败时保留最初的 Bridge 错误，不再被“容器不存在”覆盖。
- Docker Desktop 的两个损坏 socket 目录已改名留档并重建：`run.stale-20260917-111759`、`docker-secrets-engine.stale-20260917-112005`，以及复发后 `run.stale-20260917-112135`；未删除镜像、卷或项目数据。第三次启动后 Engine 29.7.2 正常。
- 当前镜像真实部署通过：Runtime 0.10.0、React UI、NodeODM 2.2.4 健康。真实无网沙盒连续运行两次相同代码，输入从 `one` 改为 `two` 后得到不同 Job ID 和对应输出，证明没有复用旧成功作业；core 镜像在关闭遥感插件后未导入 `runtime.remote_sensing`，12 个通用工具仍存在。
- 全量自动测试通过（74 passed）；Vite 生产构建、生产依赖审计（0 vulnerabilities）、PowerShell 启动脚本语法和双 Compose 配置均通过。
- 更正本日早先的错误环境结论：Ollama 0.33.2 安装在 `E:\AI\Ollama\ollama.exe`，模型仓库由 `OLLAMA_MODELS=E:\AI\OllamaModels` 指定，并存在 `qwen3.5:4b` manifest。启动既有服务后，原生 API 确认 4.7B/Q4_K_M、tools/thinking 能力；在 `num_ctx=32768`、`temperature=0.2`、`num_predict=8192` 下返回独立 thinking 字段和正确结果。
- 真实 Runtime Run `run_070e40bbd4ed4d00a411fb3fbe74143a` 完成 CSV 读取、`broken.py` 差异修复、两个离线 Docker 代码 Job、增量状态读取和 `report.json` 产物核验。首轮真实模型曾把精确引用 `result.txt` 改成 `workspace/result.txt` 并写入嵌套目录；工具协议现明确 workspace 路径已相对根目录，错误前缀返回 `workspace_path_has_root_prefix` 与精确重试参数，不做静默改写。修复后 `report.json` 和 `result.txt` 均位于根目录，未生成嵌套 `workspace/`。
- 两 Turn Run `run_88002d20764e47819d7d3a3d8aae6fad` 先从授权父目录观察真实名称 `1605白桦` 并列出文件，再按需加载 `inspect_uav_source`；它对无效 19 字节 JPG 返回 `ready=false`、元数据不可读和 GPS 缺失，且没有启动正射任务。两个 Turn 的 Harness checkpoint 均为 `complete`，长事件流分别有 818 和 774 个新增事件并通过分页完整读取。

## 2026-09-16：可信执行链与 React 工作台 0.10.0

- 增加持久化 Turn，把用户输入范围、PydanticAI Harness run ID、checkpoint 状态与 Runtime Run 显式关联；Action、Job 与 Artifact 可继续追溯到对应 Turn。
- 运行中补充要求排入下一 Turn。Runtime 重启只自动继续具有完整 checkpoint 的 Turn；缺失 checkpoint 或存在未决工具副作用时保持暂停，不自动重放。
- 事件 API 改为可分页的增量读取，长事件流不再被固定 500 条截断。React 工作台从序号 0 追赶全部页，再长轮询新事件，并展示真实 thinking、消息、工具参数/结果、作业、错误、Turn 和产物。
- 移除 Gradio 运行依赖与挂载，前端改为 React + TypeScript + Vite，由 FastAPI 同源提供；本地身份使用 HttpOnly Cookie，API 密钥不进入前端脚本。
- 移除领域路径静默替换。错误 Observation 仍给出精确候选，但必须由模型修改参数后重试。
- `code_run` 的成功复用加入 Workspace 后态校验；引用外部 Source 的成功作业不跨提交复用，避免输入变化后命中旧结果。
- 自动测试包含 1205 条事件分页、运行中补充输入、暂停边界、完整/缺失 checkpoint 重启、Workspace 输入变化和外部 Source 复用等回归。本机 `compileall` 与全量测试通过（66 passed）；Vite 生产构建、生产依赖审计（0 vulnerabilities）和 `docker compose config --quiet` 通过。Docker/真实 Qwen 需要 Docker Desktop 可用后另行验收。

## 2026-09-16：Windows 一键启动

- 新增仓库根目录 `start-forestry-agent.cmd` 和 Runtime 目录 `start.cmd`。双击后统一调用 `setup.ps1`，成功才打开工作台；失败会保留窗口和实际错误。
- `setup.ps1` 现在等待 Runtime `/health` 成功后才报告就绪，并可通过 `-OpenBrowser` 打开页面。
- 本机 Docker Desktop 4.89.0 实测遇到损坏的 `sailor-ingest.sock` 和 `docker-secrets-engine/engine.sock`。启动器仅在后台日志命中精确错误、Docker 已退出且路径严格位于预期本地运行时目录时，把对应目录改名留档并重建空目录，最多重试三次；不改动镜像、卷或项目数据。
- 真实冷启动验证通过：Host Bridge 和 Ollama 正常，`forestry-runtime` 健康，NodeODM 运行，Runtime 0.9.1 返回 `remote_sensing_plugins=true`。

## 2026-09-14：路径纠错交还 Agent 0.8.5

- 真实失败 Run `run_cacda8ccc3c1457e8658f56d0ca00e3c` 因用户写的 `1605 白桦` 与磁盘真实名称 `1605白桦` 不同，产生 25 次模型调用和 24 次工具调用；模型虽已看到根目录列表，仍通过变更工具、scope 和空格形式重复访问同一错误目标。
- 原始消息已确认：用户输入和真实磁盘名均为 `1605白桦`，但上一轮 assistant 把六个目录全部自行插入空格，并作为聊天历史传入下一轮。Runtime 不静默改写路径或替模型执行。授权目录内存在唯一的仅空白差异候选时，失败 Observation 明确返回 `source_path_not_found`、`requested_path`、真实观察到的 `suggested_path` 和 `source_id`；Qwen 必须依据这些事实自行修改参数并重新调用。
- Agent Loop 记录已经证实不存在的源路径。后续即使换用另一个文件或正射工具重复提交同一错误路径，也只返回 `known_invalid_source_path`，不再次访问文件系统；正确的候选路径仍允许执行。
- 真实 `qwen3.5:4b` 已验证纠错：首次提交 `1605 白桦` 收到结构化候选后，下一轮由模型自行改为 `1605白桦` 并成功检查真实目录；Runtime 没有替它执行修正后的调用。
- 未经用户明确要求的 `recursive=true` 现在返回 `recursive_scope_unconfirmed`，由模型决定改用顶层范围或先检查子目录，避免把另一个子目录的同名影像混入并误判数据不可用。
- 57 项自动测试全部通过，包括精确相对目录直接解析、候选仅作为 Observation 返回、递归范围前置条件以及跨工具重复错误路径不再执行。
- 部署后以故意保留错误 assistant 历史的同一三轮对话复测（Run `run_6a61ebe5585147cdb46e0ae52e4470db`）：Qwen 首次沿用 `1605 白桦`，收到候选后自行改为 `1605白桦`；第二次 `inspect_uav_source` 得到 294 张、49 组、`ready=true`。全程 3 次模型调用、2 次工具调用，无重复失败、无 coordinator error、无递归误扫，也未启动 NodeODM。

## 2026-09-14：正射子目录范围修正 0.8.4

- 0.8.3 同会话实测已消除 coordinator 崩溃，但发现 Qwen 同时传入父目录 `source_id` 和相对 `folder_path=1605 白桦` 时，转换层忽略了相对路径并扫描整个授权根，错误检查了 3396 张影像。该结果已明确判为无效，未启动 NodeODM。
- 现在 `source_id` 固定表示授权根，`folder_path` 可表示其下相对子目录；Runtime 在 Host Bridge 上解析真实目录并再次验证没有越界，然后只把解析后的子目录交给正射工具。`..` 和授权根外绝对路径均被拒绝。
- 新增范围回归测试，验证白桦子目录不会退化成父目录；完整测试增至 52 项。
- 部署 0.8.4 后使用原聊天、原用户和真实 `qwen3.5:4b` 复测“检查 1605 白桦，但不要启动任务”。每个 `tool_start` 都有对应 `tool_end`，无 coordinator error；模型在一次可见的相对路径失败后读取已有授权并换用正确绝对路径，最终只检查 `1605白桦`：294 张影像、49 个航片组、五个多光谱波段各 49 张、GPS 294/294、`ready=true`。全程没有调用 `start_orthomosaic`。

## 2026-09-14：授权继承与工具失败闭环 0.8.3

- 修复正射领域工具授权预处理异常绕过 `tool_end` 的问题。预处理失败现在转换为标准 execution failure Observation，Open WebUI 收到配对的工具结束状态，LLM 可解释失败并决定停止或换方法；不再由 Run coordinator 直接输出内部 `AssetError`。
- 已有只读目录授权现在覆盖其子目录。用户在授权一个航片根目录后，可以用“1605 白桦”等子目录名称继续任务；Runtime 解析模型给出的完整子路径并复用最具体的父授权，不扩大到授权根之外。
- 新增两项回归测试验证父授权子目录访问，以及领域授权失败的 `tool_start → tool_end → LLM message` 事件顺序。完整测试增至 51 项。

## 2026-09-14：宿主目录与正射工具接入 0.8.2

- 完整 Windows/UNC 路径传给 `fs_list/read/search` 时自动路由到宿主机来源；不再要求 Qwen 同时猜中 `scope=source`。真实 Qwen 对 `E:\Document\ChatGPT 这个文件夹下有什么东西` 只调用一次 `fs_list`，返回两个真实顶层目录并创建只读授权。
- `inspect_uav_source` 与 `start_orthomosaic` 复用通用目录授权，既可直接使用最新请求中的完整路径，也可使用 `fs_list` 返回的 `source_id`。正射检查与启动 Schema 已从嵌套 `source/options` 改为扁平字段，旧嵌套字典仍兼容。
- PydanticAI 关键词 ToolSearch 在真实中文 Qwen 测试中没有主动揭示正射工具，模型产生了 17 次错误行动后被取消。现在由 Runtime 对当前请求做无状态中英文分类，只预加载命中的一个领域组，其余工具继续 deferred；没有恢复 `capabilities_search/read`。
- 修复后真实 Qwen 以一次 `inspect_uav_source` 检查 `E:\0730-多光谱正射(晴天飞行)\1_原始数据\1630落叶松`，2 次模型调用完成；真实结果为 462 张影像、77 个航片组、`ready=true`，且没有启动 NodeODM 作业。
- 遥感扩展已启用，Runtime、NodeODM、Host Bridge 和 Open WebUI Pipe 已重新部署。完整自动测试现为 49 项并全部通过。

## 2026-09-14：PydanticAI 内核与工具精简 0.8.1

- 用 `pydantic-ai-slim[openai]==2.43.0` 替换 `runtime/agent.py` 中自建的 Ollama `/api/chat`、Tool Call 和 XML 修复循环；Open WebUI Pipe 保持唯一界面入口，Forestry 模型只运行 PydanticAI 这一层 Agent Loop。
- PydanticAI 负责 Qwen/Ollama 消息协议、thinking/tool 事件、模型调用预算和类型化消息历史。RunStore 新增兼容字段保存 PydanticAI 消息；执行事件、作业引用和副作用状态仍由项目数据库管理。
- 该阶段删除旧的 Prompt 路由和能力 catalog。后续 0.8.2 的无状态分类检索是在真实中文模型无法发现 deferred 正射工具后加入，只选择一个相关领域组，不恢复旧 `capabilities_search/read`、持久化 catalog 或直接 `/tools` 入口。
- 同一 Run 内完全相同的失败工具调用只执行一次；改变参数、工具或方法不受限制。删除通用参数/执行失败结果以及领域作业结果中的规定性 `next_action`、`next_tool` 和轮询建议；作业只返回经 `JobObservation` 校验的状态、终态、是否待整理、失败原因和诊断可用性等事实，由模型决定下一步。未被生产代码使用的 `RecoveryContext` 同时删除。
- 更新框架级测试，不再模拟自建 Ollama JSON/XML 协议。在隔离安装的锁定依赖 `pydantic-ai-slim[openai]==2.43.0` 下，删除三项旧能力目录测试后现有 46 项测试通过，覆盖 PydanticAI 工具观察、thinking 事件、失败后换方法、独立批量行动、相同失败去重、延迟遥感工具、Run 恢复和原有遥感算法。尚未安装该依赖的宿主机系统 Python 会在导入 Agent 测试时失败；生产镜像由 `requirements-core.txt` 安装它。
- `setup.ps1` 成功启动 Ollama 与 Host Bridge，但 Docker Desktop Linux engine 未就绪，因此镜像构建和 Open WebUI 部署尚未发生。
- 宿主机真实 `qwen3.5:4b` 已通过 PydanticAI 做三项最小协议验证：能力询问为 1 次模型调用、0 次工具调用；混合目录任务为 `fs_list → fs_read`；替代文件任务为 `fs_list → fs_read`。三项均无 XML、协议重试或重复工具调用。
- 混合目录任务中，工具正确返回 `species=larch`，模型却把它解释成“云杉”。因此只能确认 PydanticAI/Qwen 工具协议通过，不能宣布语义或专业判断验收通过；后续验收必须把结论与工具证据分开计分。
- 删除旧能力目录并重命名代码作业工具后，真实 `qwen3.5:4b` 仍按 `fs_write → fs_read` 完成文件创建与核验，共三次模型调用，文件内容与要求一致。
- Open WebUI 0.11.3 已具备原生工具调用和 Open Terminal。普通短任务优先复用这些能力；Forestry Runtime 的保留范围收敛为遥感动态能力、长作业、副作用协调、资产证据和专业验证。Open Terminal 0.13.0 的 API 覆盖文件、进程、增量日志和取消，但进程提交不接受调用方 Action ID，不能保证 Runtime 在不确定提交后按原 ID 协调，因此暂不替代持久代码作业。

## 2026-09-13：通用 Agent Runtime 0.7

- 按预设门槛验证 OpenHands SDK 1.47.0。隔离安装和公开扩展接口通过；真实 `qwen3.5:4b` 九次通用任务通过 7 次，脚本修复为 1/3。一次针对工作目录和输出路径的适配后该任务为 0/3，因此拒绝候选，不把 OpenHands 加入生产依赖。原始事件位于忽略提交的 `evaluation/results/`，选型结论见 `evaluation/KERNEL_SELECTION.md`。
- Agent Core 改为领域无关行动循环。删除全局 `force_report`、失败后撤工具、固定批次中断和重复恢复提示；保留真实参数校验、失败 Observation、模型调用预算和证据约束。
- 新增通用 `fs.*`、`exec.*`、`artifacts.*`；Runtime 根据最新用户请求检索并加载遥感 Schema，加载状态随 Workspace 持久化。`capabilities_search/read` 保留为内部/API 接口，不进入模型 Tool Schema。关闭遥感插件时不导入 `rasterio`、`numpy`、`prosail` 或领域工具模块。
- 新增聊天 Workspace、Windows Host Bridge 和目录授权。读取只授权真实目录；宿主机写入只授权一个已存在文件；代码容器中的源目录只读。Bridge 不提供宿主机命令执行。
- 新增 `/runs` 状态与增量事件 API、SQLite 行动/作业引用和 JSONL 完整日志。修复同步 FastAPI 端点在线程池中无法启动后台 Run 的问题。HTTP 断开不取消 Run；Runtime 停止将 Run 标为 paused，重启按 Docker 标签恢复作业并在终态后继续 Agent。
- 代码作业使用独立容器，默认 4 CPU、6 GiB、256 PID、4 小时；无网络、只读根文件系统、移除 capabilities、禁止提权。依赖安装作业可联网但不挂载源数据。watchdog 在无客户端轮询时仍执行超时限制。
- 修复终态进度事件与作业线程完成之间的竞态；终态现在保证先于 `tool_end` 送达。无新日志的 `exec_read` 轮询由 Runtime 完成，不消耗模型调用。
- 通用核心与遥感镜像/Compose 已分开。基础 `compose.yaml` 默认禁用插件且不依赖 NodeODM 或预设 UAV 目录；`compose.remote-sensing.yaml` 才安装遥感依赖、挂载旧路径并启动 NodeODM。
- 宿主机 49 项测试全部通过（通用文件、权限、Run API、恢复、长任务、不确定提交协调、依赖版本清单、Pipe 上传、生命周期和已有遥感功能）。基础及遥感 Compose 合并配置均可解析。
- Docker Desktop 恢复后已重新构建并部署核心镜像；`/health` 返回 0.7.0、`remote_sensing_plugins=false`。针对“你现在的工具能力有哪些？”运行真实 `qwen3.5:4b` 三次（Run `run_20ac9ea2d8b1497d833f4bbf2c4cf79f`、`run_4b58b2bf24c4470e87a94cfe32b0e3dd`、`run_7fe26edc15b14a12a43dfab548248934`），三次均一次模型调用完成，无工具调用、无协议重试、无错误。模型 Tool Schema 从故障日志中的 13 个工具/9082 字符降为 11 个工具/8152 字符。

## 2026-09-12：Ollama 连接恢复

- Open WebUI、Runtime 0.6.2 和 NodeODM 均在运行；宿为机 `127.0.0.1:11434` 当时明确拒绝连接，且没有 Ollama 进程。Open WebUI 日志同时记录无法连接 `host.docker.internal:11434`。
- 已以隐藏进程启动 `E:\AI\Ollama\ollama.exe serve`。宿为机 API 返回 `qwen3.5:4b`，Runtime 容器访问同一 API 返回 HTTP 200，真实 Runtime 对话返回“连接正常”。
- Agent 对 `httpx.ConnectError` 返回明确的 Ollama 依赖错误。`setup.ps1` 现在会检查并在需要时启动 Ollama，避免部署完成后第一次对话才发现服务缺失。
- Runtime 启动期间曾出现 `lifecycle.sqlite3` 无法打开；当前 `/data`、数据库文件权限正常，最近日志未再出现该错误。

## 2026-09-11：后台失败语义与诊断入口 0.6.2

- 实际两个樟子松任务的 NodeODM 状态均为 30，`status.errorMessage` 为 `ENOTDIR: not a directory, scandir .../images`，`processingTime=-1`，处理日志为空。原代码漏读该嵌套字段，导致 `last_error=null`。
- 新增 `get_job_diagnostics`，分别表达接口 `ok` 与任务 `outcome_ok`；失败任务会进入恢复上下文。按 job_id 保留最新诊断，诊断完成后不再返回指向自身的 next_tool。
- 去重覆盖失败任务，同输入、参数和输出要求只返回已有任务，避免模型猜测性重复上传。
- 26 项测试覆盖实际嵌套错误结构、空日志、失败任务重提只提交一次、数据源检查成功不能清除后台失败。
- 已通过容器内工具读取两个真实任务的诊断。工作目录已被 NodeODM 的初始化失败清理流程移除；一次小规模目录移动探针成功，尚未复现底层 ENOTDIR 原因，不能声称后端故障已经修复。

## 2026-09-11：工具协议与失败恢复 0.6.1

- 修正直接把嵌套 `$ref` 交给 Ollama 的协议问题：向模型提供展开后的对象类型；合法的对象/数组 JSON 字符串在契约约束下解码，再严格校验。
- 失败区分参数校验与业务执行。下一轮获得失败字段、期望类型、是否执行及恢复上下文；同批失败后的动作标记为未执行，等待模型重选。
- 系统提示去除按业务名称预设的工具链，领域条件放在工具说明中。修正“附件为空等于挂载目录不可读”的上下文冲突。
- Ollama 上游改为 NDJSON 流式读取；测试断言首个思考事件出现时，后续模型响应尚未读入。
- 25 项测试在宿主机和 Runtime 容器内分别通过。新增用例验证嵌套参数类型、合法序列化恢复/非法值拒绝、失败后的批次中断、修正参数后继续、读取失败后解压并消费新资产。Runtime 已部署为 0.6.1，健康接口确认版本；Pipe 0.6.0 可直接接收新的增量思考事件。
- 跨工具动作选择使用可控模型响应，真实文件操作由工具执行；不把这项测试等同于真实 Qwen 自主恢复成功率。真实 Qwen 复现调用被自动审批以 `blocked by policy` 拒绝，未取得本版模型验收结果。

## 2026-09-11：通用数据源、波段和长时任务 0.6

- 用 `inspect_uav_source` 和 `start_orthomosaic` 取代目录/上传各一套的入口。`source.kind=folder` 接受白名单路径；`source.kind=uploads` 默认使用当前消息的全部影像或ZIP，也可显式选择 `asset_ids`。
- 新增 `JobManager`、`get_job_status`、`wait_for_job` 和 `finalize_job`。当前接入 NodeODM，但调用契约不包含正射专用字段，可后续注册其他耗时任务提供者。
- 默认调用 `wait_for_job` 保持当前请求，向 Open WebUI 发送进度心跳，终止后自动整理成果并继续同一轮 Qwen 回答。断线不取消 NodeODM 任务，可用原 `job_id` 续查。
- NDVI 和 PROSAIL 改用共享 `bands` 结构，各工具仅校验自己必需的波段角色，不再维护两套重复波段参数。
- 20 项自动测试在宿主机与 `forestry-runtime` 容器内分别全部通过；新覆盖统一数据源、任务路由、进度事件、自动整理、上传子集授权和旧工具名称移除。
- Runtime/Pipe 已部署为 0.6.0，Open WebUI 等待时限已设为 14400 秒，健康检查和 Open WebUI→Pipe→Runtime→Qwen 流式链路通过。
- 真实目录顶层只读检查为 462 张、77 组、GPS 462/462、五个多光谱波段各 77 张，`ready=true`。递归时还会读到 `起飞后` 子目录中 24 张同名影像，因 NodeODM 上传名称冲突而正确返回 `ready=false`。当前数据应保持默认 `source.recursive=false`。本次没有提交新的真实 NodeODM 摄影测量任务。

## 2026-09-11：Open WebUI + Qwen + NodeODM 正射任务链 0.5

- 新增六个模型工具：白名单目录检查、聊天上传检查、两种 NodeODM 提交方式、任务状态查询和成果整理。模型没有宿主机 Shell 或任意路径访问权限。
- \`compose.yaml\` 使用官方 \`opendronemap/nodeodm\` 服务。原始数据根目录以只读方式挂载；NodeODM 内部工作区使用 Linux 命名卷，结果统一保存在 \`E:\UAV_WORKSPACE\jobs\<job_id>\`。\`docker compose config\` 解析通过。
- 辐射定标为证据驱动的 \`auto\`：只在各多光谱航片的辐照度与相机校正元数据完整时向 ODM 传入 \`camera\`，否则保持 \`none\` 并告警。正射分辨率、并发数、主波段和 DSM 不根据某一数据集写死。
- 对 \`E:\0730-多光谱正射(晴天飞行)\1_原始数据\1630落叶松\` 做了只读检查：462 个影像文件、77 个 \`CaptureUUID\` 航片组、相机 \`FC6360\`；Blue/Green/Red/RedEdge/NIR 各 77 张；GPS 462/462。
- 同一实测中，385 张单波段影像均含辐照度、相机辐射校正、渐晕和畸变元数据；462 个文件均含 RTK 字段，厂商状态码为 \`50\`。因此该航次的 \`recommended_radiometric_calibration\` 为 \`camera\`。
- 18 项本地自动测试全部通过，新覆盖 Windows 白名单路径映射/越界拒绝、ZIP 航片检查、有/无辐射元数据时的自动定标选择、最小 ODM 参数、NodeODM 状态、用户隔离、指定成果解包、GeoTIFF 基础质量检查与 PNG 预览。
- 未提交这批 1.57 GB 真实航片，以免在用户确认前重复运行长时摄影测量。容器级健康验收待 Docker Desktop 恢复；当前它因 \`sailor-ingest.sock\` 旧运行时套接字无法重命名而在启动时崩溃，与本项目 Compose 无关。

## 2026-09-08：像元检查与候选林冠分割 0.4

- 建立Git基线提交`ffe2550`，并在`codex/raster-inspection-canopy-baseline`分支开发。
- 新增`inspect_raster`：分块精确统计掩膜、非有限值、零值、负值、范围和均值，使用确定性抽样分位数，并检查Red/NIR/Alpha空间重合关系。
- 当前真实286 MB正射影像检查表明：Band 5由color interpretation识别为Alpha；1,967,921个Alpha=0像元与Red、NIR同时为零及NDVI零分母像元完全重合，Alpha>0区域无零分母。
- 新增`segment_canopy`初始基线：仅接受`calculate_ndvi`产物，默认从512-bin NDVI直方图计算Otsu阈值，输出0/1/255 uint8候选Mask及像元、比例和投影面积统计。
- 13项自动测试全部通过，包括Alpha/零分母重合统计、显式阈值、自动Otsu、Mask类别与空间参考、非NDVI输入拒绝。
- 在真实NDVI临时副本上得到Otsu阈值0.525390625；有效像元14,445,751，候选像元13,167,320，占有效区91.1501%，候选面积11,848.63平方米。该结果仅为高NDVI植被候选区，不作为最终林冠覆盖率。
- 本地qwen3.5:4b完成`inspect_file → inspect_raster → calculate_ndvi → segment_canopy`。额外的快速`inspect_file`记录为工具效率问题，不影响数据和产物正确性，后续在Benchmark中评估。

## 2026-09-07：Open WebUI原生推理呈现 0.3.1

- Pipe将Runtime的thinking事件映射为OpenAI兼容的`reasoning_content`增量，由Open WebUI生成原生推理面板。
- 工具开始/结束改用Open WebUI状态事件；完整参数与结果不再作为HTML和转义JSON写入回答正文。
- 保留旧轨迹清除逻辑，旧对话中的`data-forestry-trace`内容不会进入后续模型上下文。

## 2026-09-07：NDVI与执行可见性 0.3

- 新增`calculate_ndvi`专业工具：按窗口读取Red/NIR，应用源数据scale/offset，联合检查两波段掩膜、有限值和非零分母，输出保留网格与坐标系的float32 GeoTIFF。
- Runtime启用Ollama原生thinking；Open WebUI折叠显示模型思考、工具名、参数、真实结果和耗时。界面轨迹不会再次进入模型上下文。
- 12项自动测试通过；新增NDVI数值、无效分母、地理参考、父资产关系和波段标签冲突测试。
- 真实qwen3.5:4b端到端验收通过：`inspect_file → calculate_ndvi`，无多余工具调用；合成影像期望NDVI均值与输出实测均为0.5，返回thinking事件。
- Open WebUI真实流式接口验收通过：响应包含可见的“模型思考”折叠区及最终回答；测试聊天随后删除。
- 已只读核验当前286,655,678字节真实附件：5214×3148、Band 1=Red、Band 3=NIR、EPSG:32650；未在验收中替用户生成不可见的孤立NDVI资产。

## 2026-09-07：会话资产生命周期 0.2

- 已部署Runtime 0.2、更新Pipe，并启用Open WebUI Event Function `forestry_cleanup`。不向模型注册删除工具。
- 基础及新增测试共11项通过：包括活跃工具期间延迟清理、另一聊天隔离、删除状态重启恢复、晚到产物登记、所属用户与路径校验、旧目录保留。
- 真实HTTP验收：新建聊天和附件 → Pipe → Qwen → read_text/save_text → Open WebUI报告下载，内容核对通过。
- 删除A后，A的工具产物自动删除；B仍引用的原件及B的Runtime资产保留。删除B后，原件自动删除，两边清理记录完成。
- Runtime离线时删除测试聊天，然后重启Open WebUI及Runtime：后台自动补清理，无需手动启动监控。
- 归档测试通过：跨一个清理周期后两边文件仍可下载；删除该归档聊天后文件自动清理。
- 仅接管更新后新建的普通聊天及本次版本登记的新文件。旧对话、旧数据卷不迁移、不清理。
- 实测为API链路验收；不将其描述为浏览器点击验收。当前仅支持单实例Runtime和SQLite Open WebUI。
- 资产位置：项目 `data/sessions/<chat_id>/`；`data/lifecycle.sqlite3`保留少量删除标记及待清理文件ID。

复测脚本：`scripts/verify_lifecycle.py`（运行于Open WebUI容器）。模式依次为 `prepare`、`delete-a`、`delete-b`；恢复测试使用 `prepare-recovery`、停Runtime、`delete-offline`、重启两服务、`verify-recovery`。这些模式仅删除自行创建的验收聊天。

部署命令：`setup.ps1` 更新两项函数后重启Open WebUI，以确保后台函数采用新代码。后台重试间隔10秒，Runtime清理检查间隔2秒；工具仍在执行或文件仍有其他引用时会延迟删除。

验证环境：2026-09-05，Windows Docker Desktop；Open WebUI 0.11.3；Ollama 0.33.2；qwen3.5:4b。

## 已通过

- 6项自动测试：资产持久化与隔离、上传大小限制、真实TIFF及预览、ZIP安全检查/解压和文本读取、模型工具结果回传、API认证与下载、未知工具和轮数上限（部分测试项在同一用例中）。
- Open WebUI管理员API已确认 `forestry_runtime` 模型可见，Pipe已启用。
- Open WebUI容器可访问runtime健康接口；runtime可调用宿主机Ollama。
- TIFF（image/tiff、process=false）、PNG（image/png、process=false）、ZIP（application/zip、process=true）按当前聊天上传器的组合上传成功；ZIP的Open WebUI处理状态返回completed，文件均能到达Pipe。
- 端到端：合成TIFF/PNG/ZIP上传 → Qwen选择真实工具 → 读取尺寸/CRS、ZIP解压、读取文本 → 保存Markdown → 通过Open WebUI文件接口下载并核对内容。
- 预览端到端：生成64×48 PNG、下载并检查真实像素尺寸；下一轮通过上一轮的文件链接重新登记并读取文件。
- Runtime容器健康运行，文件存储在持久化Docker卷；运行代码可正常解析。

## 实测边界

- 第一次模型报告曾将32650误解释为南半球，并自行算错网格面积。工具现返回CRS名称与程序计算的网格面积，提示词要求直接引用；这不代表4B模型的所有专业解释都已经可靠。
- 模型曾生成占位下载链接。运行时现剥离模型输出中的Markdown链接，只由适配器附加已保存文件的真实链接。
- 一次跨轮预览回答没有包含验收要求的尺寸，重复测试通过。模型措辞/工具选择仍有随机性，不能把一次成功当作稳定性基准。
- 浏览器自动化工具在本机初始化失败；本轮完成的是当前Open WebUI真实HTTP接口、原版上传器源码对应行为和下载内容验证，未进行浏览器点击/截图验收。
- 数据均为合成验收数据，未验证GB级真实航测影像吞吐量或生产并发。

没有添加任务状态、执行日志数据库或后台任务模块。

## Pipe 0.1.1：大图片上传修复

- 初版适配器对Base64文本设置40000000字符上限，折合约30 MB原图，与runtime的512 MiB限额不一致。
- 改为512 MiB可配置限额，分块解码到临时磁盘文件；根据SHA-256复用同时作为附件和内联图片传入的文件。
- 新增32 MiB解码完整性、边界大小和非法编码测试；不再依赖一次性解码整个图片到内存。
- 真实HTTP端到端通过：33,579,368字节TIFF同时以Open WebUI附件和Base64图片传入，Qwen通过工具返回4096×4096尺寸、单波段和WGS 84 / UTM zone 50N；runtime按校验值检查仅登记一份资产。
# 2026-09-15：Agent 可观察工作台 0.9.1

- 原界面只把工具事件放入折叠 JSON，完全忽略已经由 PydanticAI 产生并持久化的 `thinking` 与模型轮次。现改为固定三栏：左侧任务/文件/目录授权，中间连续对话，右侧 Agent 运行轨迹。
- 模型原生 thinking 在聊天气泡中使用 Gradio reasoning 折叠，同时在右栏与模型轮次、工具参数、Observation、后台作业进度和失败按真实事件顺序显示为 `THINK → ACT → OBSERVE`。界面不产生 Agent 决策，也不重复保存事件。
- 真实 Qwen 只读演示 Run `run_3078190b2d6a4eca8df3a277e3b74ca7` 完成两次模型调用：先输出 thinking，再调用一次 `fs_list`，接收成功 Observation 后继续 thinking 并回答。部署后的聊天折叠和右栏四类轨迹均从这组持久化事件恢复。
- 锁定 Python 3.12 依赖下完整执行 63 项测试，全部通过；新增工作台静态资源隔离和 ReAct 可见性验证。

# 2026-09-15：Gradio + PydanticAI 本地工作台 0.9.0

- 默认开发入口已从 Open WebUI Pipe 切换为 Gradio 工作台；支持新建/切换/删除聊天、连续对话、工具与作业状态、停止、文件/文件夹上传、目录授权与撤销、产物下载。
- Gradio 挂载在 `/workbench/`，根地址自动跳转。这样它的 `/workbench/assets/*` 前端资源不会与 Runtime 已有的受鉴权 `/assets/*` 产物接口冲突；部署后主 JS、CSS 和健康接口均返回 200。
- 同一聊天现在继续同一个 Forestry Run，并只把新增用户输入交给下一次模型执行。PydanticAI Harness 使用稳定 `conversation_id=chat_id` 和每次调用唯一的 Agent run ID；从最近完整步骤快照恢复原生消息及工具 Observation。
- 引入 `pydantic-ai-harness==0.31.0` 的 StepPersistence 和分层压缩。重复读取先去重，旧工具正文达到阈值后清理，更长历史才总结；旧的应用级完整模型历史字段停止使用。
- UAV 目录参数解析移到 `RemoteSensingTools` 边界。`source_id + 相对路径`、精确子目录及空格差异建议都以结构化工具结果返回，PydanticAI loop 不再因预处理异常中断。
- `setup.ps1` 不再修改或重启 Open WebUI，只启动 Host Bridge、Ollama 和 Runtime。Open WebUI 代码保留为静态回退材料。
- 在锁定的 Python 3.12 依赖上执行完整测试：新增重复失败暂停与两轮路径恢复验证后 61 项通过。
- 真实 `qwen3.5:4b` 两轮验收通过：第一轮列出 `E:\0730-多光谱正射(晴天飞行)\1_原始数据` 并观察到 `1605白桦`；第二轮用户只说“检查1605白桦”，模型恢复原 `fs_list` Observation。首次工具参数误写为 `1605 白桦`，收到结构化 `suggested_arguments` 后主动改为 `source_id + 1605白桦`，成功检查 294 张影像并得到 `ready=true`，未启动正射任务。事件保存在 `evaluation/work/two-turn-path/282a7093c6b64bff91189f33d89015f1/events.json`。
- Docker Desktop 4.89.0 本轮因其本地 `sailor-ingest.sock` 无法重命名而在 Linux 引擎启动前崩溃；主机策略拒绝自动删除该临时 socket，因此新镜像部署和容器内正射提交验收仍待 Docker 恢复。
