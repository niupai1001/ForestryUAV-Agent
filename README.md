# 林遥 · ForestryUAV-Agent V1

独立重新开发的单人本地 Agent。当前已实现真实模型/文件闭环和开发者工作台，完整通用产品、林业流程及发布评估仍在推进中。实现和验证状态见 [docs/STATUS.md](docs/STATUS.md)，修订需求见 [docs/BASELINE-zh.md](docs/BASELINE-zh.md)。

按用户 2026-10-06 指示，Agent 联网搜索先暂缓开发与验收；当前优先完成本地文件、代码调试、范围依赖、作业恢复及 UI，已有搜索代码和安全回归保留。

## 安装与启动

使用 Python 3.12 和 uv：

```powershell
uv sync --locked --group dev
uv run --locked forestry-agent doctor --config config/app.example.yaml --output workspace/deployment.lock.json
uv run --locked forestry-agent serve --config config/app.example.yaml
```

打开 `http://127.0.0.1:8765`。Ollama 必须在本机 `11434` 端口服务配置中的模型。UI 资源随应用打包，日常启动不需要 Node。

`doctor` 的退出码只表示默认模型是否满足启动前提（`startup_ready`）。`general_prerequisites_ready` 另外要求所有配置模型、搜索和受限执行器可用；这些探测也不等于通用产品验收，因此 `general_ready` 保持 false、`general_validation` 为 `not_assessed_by_doctor`。缺少领域工具不阻止通用启动。

Windows 可使用已实测的[项目独立 WSL 容器后端](docs/EXECUTOR-WSL.md)及 `config/app.wsl.example.yaml`。它采用签名 Ubuntu 镜像、固定系统包和不可变 Python 镜像摘要，复用相同作业管理器，不重置现有 Docker Desktop。默认示例仍为 Docker Desktop；须明确选择对应配置。搜索暂缓不会自动改变 doctor 原有的前提字段定义，暂缓项在实施状态中独立记录。

命令行使用相同本地服务、授权与任务契约：

```powershell
uv run --locked forestry-agent run --objective "读取输入，检查内容并保存中文报告" --input fixtures/general/heights.csv --model small --harness H2
uv run --locked forestry-agent tasks candidate --task <task-id>
# 实际检查回答和产物后，明确确认或要求修改：
uv run --locked forestry-agent tasks accept --task <task-id> --comment "已检查，满足目标"
uv run --locked forestry-agent tasks request_changes --task <task-id> --comment "请补充来源"
```

`run` 默认等待当前模型回合完成，返回“待确认”也不代表验收成功。CLI 等待超时返回退出码 3，后台任务保留，不自动取消；失败/暂停返回 2。`--spec` 接受 TaskCreate JSON，`--input` 的本地文件路径由 CLI 显式登记。

开发前端：

```powershell
cd frontend
npm ci --no-audit --no-fund
npm run build
npm test
```

测试：

```powershell
uv run --locked pytest -q --basetemp workspace/test-tmp
uv run --locked python scripts/smoke_models.py --models small large
uv run --locked python scripts/smoke_documents.py --models small large
uv run --locked python scripts/smoke_context.py --model large
uv run --locked python scripts/smoke_roles.py --models small large
uv run --locked python scripts/smoke_recovery.py --config config/app.wsl.example.yaml --models small large
uv run --locked python scripts/smoke_network.py --base-url http://127.0.0.1:8765 --models small large
uv run --locked python scripts/smoke_search.py --base-url http://127.0.0.1:8765 --models small large
uv run --locked python scripts/smoke_progress.py --base-url http://127.0.0.1:8765 --models small large
uv run --locked forestry-agent schema
```

`--basetemp` 是测试专用目录，pytest 会清理其内容，不要指向数据目录。模型烟测读取仓库内的小型 CSV，生成真实数值结果和报告，用独立已知值核对，并保留每次失败或通过的试次记录。

## 已有行为与当前限制

- 本地会话认证、Origin/Host 检查；用户显式登记只读绝对路径，模型仅访问任务的资源 ID。
- SQLite 持久任务、规范修订、动作/attempt、追加事件；请求 ID 重放与冲突检查。
- 原生模型工具循环、模型检查点、跨重启累计动作和模型请求预算。
- 持久工具观察与无进展保护；重复结果、循环重读、失败和空末尾窗口触发真实暂停，恢复不清零。
- 文件窗口读取、任务工作区写入、不可变产物、SHA-256 检查、隔离 HTML 预览。
- 无网络容器作业句柄、身份对账、实际停止确认、流式封存输出；独立 WSL 真实合成计算/取消/超时通过，两模型运行中作业的真实服务进程恢复试次通过14项检查。完整恢复边界与 UI 仍待验证；终态 wait_job 带有界真实日志，可据 traceback 修复代码。
- UI 历史、模型切换、来源选择、执行记录、产物预览/下载、暂停/取消与恢复入口。
- 候选结果指纹、显式验收/要求修改、最终清单封存、发布后终态及封存中断恢复。
- PDF/DOCX/文本的有界读取与来源引用；中文本地知识索引、任务权限和源版本/字节核对。
- 工作台显式保存/停用长期记忆；模型可读，不能写入或制造授权。
- 原生工具结果外置/按句柄或 UTF-8 字节窗口读回、分层清理与滑窗；每次实际输入保留私有快照、SHA-256、源/动作/产物引用和用量核对。
- 当前规范、控制状态及当前回合原始请求逐次重新注入；按当前任务/修订找回已确认动作，按窗口读取封存文本产物。
- 应用恢复记录仅注入对应任务/修订/逻辑回合，H0/H1/H2 共享，子角色不接收父记录；该记录本身不证明进程中断或计算正确。
- 五个长期角色配置、原生按需委派；子角色独立历史、权限交集、输出目录、结构化结果和累计预算，结果不自动验收父任务。
- 工作台选择 H0/H1/H2 并查看实际委派范围、状态、检查和产物；CLI 支持相同配方。
- 网络查询/网页地址的精确范围审批、单次消费和撤销；发送前检查规范、来源与控制状态，审批前不解析 DNS。
- 固定公开 DNS 地址、逐跳检查重定向、总时限和大小限制；网页原始字节快照与 SHA-256 引用。
- 活跃运行中的补充消息和审批决策持久排队、请求 ID 去重；暂停保持暂停，重启须显式恢复。

模型输出不会自动被当成已验收成功。文件层检查完成后显示“待确认”，用户检查后可以要求修改，或确认满足目标并封存交付。用户验收不代表科学精度验证。服务重启先暂停并对账，缺少匹配检查点的已执行动作不会盲目重跑。受限代码真实执行、依赖准备、搜索、角色异常/冲突处理的完整 UI、各模型的长任务质量、完整报告、领域处理与正式评估尚未全部验证，不能据此宣称阶段 1 或完整 V1 已完成。

`max_unchanged_decisions`（默认3）冻结到新任务，旧任务在首次工具观察时固定实际值。保护按任务修订、逻辑用户回合和角色隔离，跨原生运行/重启保留；新文件字节、来源内容、文本窗口或作业状态是可观察的进展，重复观察、失败和空窗口累积连续计数。动作ID、时间戳或嵌套检查包装不会制造新进展。观察经排序后的 JSON 计指纹，新模型决策重复使用调用ID仍计数；恢复重放同一观察不重复计数。观察新颖性不证明目标已经完成，语义验收仍独立。

达到上限后返回 `NO_PROGRESS` 并暂停，已经确认的副作用保留；直接恢复返回同一错误，不增加模型请求。可在工作台补充纠正说明，再显式恢复：旧回合记录为 stopped，持久用户消息启动新回合；任务规范和累计动作/模型预算不因此重置。修改目标、输入、方法或约束仍使用任务 amendment。`smoke_progress.py` 故意重复读取软件夹具来触发保护，并保留暂停任务供 UI 检查；不代表普通任务质量评分。

角色定义在 `src/forestry_agent/roles.yaml`，也可用 `roles_path` 配置自定义 YAML；新任务冻结目录及其指纹。H0 为基础工具对照，关闭知识/记忆检索、委派和自动外置/压缩；H1 加入上下文整理和结果读回；H2 在 H1 上允许按需隔离委派。三种配方共有身份核对、权限、动作登记、预算和用户验收边界。配方入口不代表六组合正式实验已完成。恢复时保留逻辑回合与动作账本，每次 `Agent.run` 使用新的原生运行 ID，检查点仍由 Harness 保存，应用只记录回合与原生运行的关联。

上下文预算使用框架原生的实测用量锚点，对新增正文、变化指令和当前完整工具 Schema 添加 UTF-8 字节保守余量，并预留输出和协议开销；没有有效锚点时使用字节估算。实际输入 token 超过估算时明确停止。模型响应缺少用量时标记 unavailable，不能当作零用量校准。每个新任务冻结单次模型请求的总时限（默认 300 秒），流式响应也不能无限延长等待。完整输入快照仅保存在私有 `.forestry/model-inputs`，界面事件只返回引用与计数。软件烟测夹具不构成现场观测或科学精度证明。

工具结果超过 6000 字节时由原生 Harness 外置；长单行 JSON 无法安全按行读回时，使用同一原生存储的 `read_tool_result_window`，每次最多 4000 UTF-8 字节，按 `next` 继续。网页提取正文和 `read_artifact` 同样返回最多 4000 字节的窗口，完整正文保存在产物中。`has_more=false` 表示已到末尾。文本写入保持提供的 UTF-8 字节，不由 Windows 改写换行。

模型可用 `inspect_action(view="citation")` 仅查看来源引用，并用 `write_file(citation_actions={"citation": "实际来源action_id"})` 复制完整来源元数据：JSON 对象合入指定字段，Markdown/文本附加来源区。复制限于当前授权任务/修订的已确认动作，并核对快照哈希；冲突引用拒绝写入。这个功能不替模型核对正文事实，也不证明报告已经满足目标。

文档读取当前支持最大 64 MiB：PDF 按文本页读取，未启用 OCR；DOCX 提取逻辑文本，不伪造版面页码。知识索引保存在本地，检索时检查任务授权与来源 SHA-256；内容变化后须重建索引。长期记忆停用后不再供模型检索，已有任务和授权审计历史保留。

新任务默认是私密上下文：联网工具先提出完整查询/URL、允许目的地、来源版本、请求 SHA-256 和 30 分钟有效期，用户在工作台逐项批准、拒绝或撤销。批准只消费一次，任何修改须重新申请。用户可在创建时明确声明任务目标、补充消息和输入均为公开信息；该任务不能读取私密来源、长期记忆或本机环境。普通消息或模型输出不能修改此冻结分类。网页最多 2 MiB，仅提取 HTML、文本、Markdown、JSON，保存原始快照；不执行页面脚本或抓取子资源。各跳只允许 HTTP(S) 80/443 的公开地址，拒绝私密/混合 DNS、超范围或 HTTPS 降级重定向。子角色目前禁用网络。

搜索连接器默认使用 `search_provider: baidu_html`，提取带真实目的 URL 的普通结果，过滤广告、推荐和脚本；识别不到结果结构或遇到验证码时返回失败，不冒充零结果。可配置 `bing_rss` 或设置 `search_base_url` 的 `searxng`；SearXNG 须启用[官方 JSON 搜索 API](https://docs.searxng.org/dev/search_api.html)。Baidu/SearXNG 支持 `page=1..10`，页码纳入精确授权范围；`next_page` 是候选请求，`has_more=null` 不保证下一页可用。Bing 仅支持第一页。新任务冻结提供商，旧任务保持原配置。

当前公开地址规则也用于搜索，私有/回环部署及自定义端口的本地 SearXNG 尚未支持。本机真实 Baidu 搜索和来源读取已取得字节快照，完整两模型搜索报告流程仍以独立检查结果为准；Bing 在本机探测超时。脚本提出的 HTTPS→HTTP 降级不会执行。`smoke_network.py` 的脚本审批仅包含公开 Python 官方网页地址，用于验证授权机制，不代表真实用户已批准其他载荷，也不替代完整搜索流程验收。

不要将真实私密输入、凭证、模型检查点、工作区和大数据提交到 Git。`workspace/.forestry/local.key` 仅供本地 CLI/API 使用，浏览器凭证在 HttpOnly Cookie 中。应用只绑定本机，不支持远程共享部署。

任务创建时可选择“任务工具范围”：通用能力、文件与报告、仅回答或自定义。范围存入当前规范的 `constraints.allowed_capabilities`，同时约束工具展示、实际执行、代码作业及委派。省略/null继承配置上限；空列表禁止所有注册动作。源文件只读、角色上限和外发审批继续有效。原生溢出读取只观察本次运行已有结果，不授予新的资源访问能力。

工作台的“修改目标、输入与工具范围”会创建新规范修订并暂停执行；核对历史动作后再恢复。API amendment 未提供 constraints 时保留上一修订范围；明确提供 {} 才恢复配置上限。运行中可发送排队补充消息，消息本身不能扩大工具权限。`smoke_progress.py --file-only` 用明确文件范围复验无进展停止及纠正恢复；这仍是软件夹具验收，不代表全部通用能力或科学精度已通过。
