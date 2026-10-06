# 林遥 · ForestryUAV-Agent V1

独立重新开发的单人本地 Agent。当前已实现真实模型/文件闭环和开发者工作台，完整通用产品、林业流程及发布评估仍在推进中。实现和验证状态见 [docs/STATUS.md](docs/STATUS.md)，修订需求见 [docs/BASELINE-zh.md](docs/BASELINE-zh.md)。

## 安装与启动

使用 Python 3.12 和 uv：

```powershell
uv sync --locked --group dev
uv run --locked forestry-agent doctor --config config/app.example.yaml --output workspace/deployment.lock.json
uv run --locked forestry-agent serve --config config/app.example.yaml
```

打开 `http://127.0.0.1:8765`。Ollama 必须在本机 `11434` 端口服务配置中的模型。UI 资源随应用打包，日常启动不需要 Node。

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
uv run --locked forestry-agent schema
```

`--basetemp` 是测试专用目录，pytest 会清理其内容，不要指向数据目录。模型烟测读取仓库内的小型 CSV，生成真实数值结果和报告，用独立已知值核对，并保留每次失败或通过的试次记录。

## 已有行为与当前限制

- 本地会话认证、Origin/Host 检查；用户显式登记只读绝对路径，模型仅访问任务的资源 ID。
- SQLite 持久任务、规范修订、动作/attempt、追加事件；请求 ID 重放与冲突检查。
- 原生模型工具循环、模型检查点、跨重启累计动作和模型请求预算。
- 文件窗口读取、任务工作区写入、不可变产物、SHA-256 检查、隔离 HTML 预览。
- 无网络容器作业句柄、身份对账、实际停止确认、重启观察、流式封存输出；真实 Docker 烟测仍待环境恢复。
- UI 历史、模型切换、来源选择、执行记录、产物预览/下载、暂停/取消与恢复入口。
- 候选结果指纹、显式验收/要求修改、最终清单封存、发布后终态及封存中断恢复。
- PDF/DOCX/文本的有界读取与来源引用；中文本地知识索引、任务权限和源版本/字节核对。
- 工作台显式保存/停用长期记忆；模型可读，不能写入或制造授权。
- 原生工具结果外置/按句柄读回、分层清理与滑窗；每次实际输入保留私有快照、SHA-256、源/动作/产物引用和用量核对。
- 当前规范、控制状态及当前回合原始请求逐次重新注入；按当前任务/修订找回已确认动作，按窗口读取封存文本产物。
- 五个长期角色配置、原生按需委派；子角色独立历史、权限交集、输出目录、结构化结果和累计预算，结果不自动验收父任务。
- 工作台选择 H0/H1/H2 并查看实际委派范围、状态、检查和产物；CLI 支持相同配方。

模型输出不会自动被当成已验收成功。文件层检查完成后显示“待确认”，用户检查后可以要求修改，或确认满足目标并封存交付。用户验收不代表科学精度验证。服务重启先暂停并对账，缺少匹配检查点的已执行动作不会盲目重跑。受限代码真实执行、依赖准备、搜索、角色异常/冲突处理的完整 UI、各模型的长任务质量、完整报告、领域处理与正式评估尚未全部验证，不能据此宣称阶段 1 或完整 V1 已完成。

角色定义在 `src/forestry_agent/roles.yaml`，也可用 `roles_path` 配置自定义 YAML；新任务冻结目录及其指纹。H0 为基础工具对照，关闭知识/记忆检索、委派和自动外置/压缩；H1 加入上下文整理和结果读回；H2 在 H1 上允许按需隔离委派。三种配方共有身份核对、权限、动作登记、预算和用户验收边界。配方入口不代表六组合正式实验已完成。恢复时保留逻辑回合与动作账本，每次 `Agent.run` 使用新的原生运行 ID，检查点仍由 Harness 保存，应用只记录回合与原生运行的关联。

上下文预算当前使用 UTF-8 字节保守估算，并预留输出和工具协议开销；超限明确停止，不静默改写用户目标。模型响应缺少用量时标记 unavailable，不能当作零用量校准。每个新任务冻结单次模型请求的总时限（默认 300 秒），流式响应也不能无限延长等待。完整输入快照仅保存在私有 `.forestry/model-inputs`，界面事件只返回引用与计数。软件烟测夹具不构成现场观测或科学精度证明。

文档读取当前支持最大 64 MiB：PDF 按文本页读取，未启用 OCR；DOCX 提取逻辑文本，不伪造版面页码。知识索引保存在本地，检索时检查任务授权与来源 SHA-256；内容变化后须重建索引。长期记忆停用后不再供模型检索，已有任务和授权审计历史保留。

不要将真实私密输入、凭证、模型检查点、工作区和大数据提交到 Git。`workspace/.forestry/local.key` 仅供本地 CLI/API 使用，浏览器凭证在 HttpOnly Cookie 中。应用只绑定本机，不支持远程共享部署。
