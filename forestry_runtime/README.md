# Forestry Runtime 0.2

独立运行的 Qwen3.5 文件工具 runtime。Open WebUI 负责对话和附件；本项目负责受控文件存储、工具定义、参数校验、真实工具执行和模型调用循环。

本版本不实现任务队列、检查点、执行日志数据库、RAG 或遥感分割算法。聊天历史由客户端传入；Runtime持久化文件、会话归属和清理状态。

## 删除聊天与资产清理（0.2）

- 仅管理更新后新建的普通聊天，不迁移旧聊天或旧数据卷。刷新、关闭浏览器及归档聊天不删除资产。
- Pipe传入经过归属验证的聊天ID；Runtime把文件保存在本项目 `data/sessions/<chat_id>/`。原件和工具产物属于同一聊天。
- 删除聊天后，Open WebUI的 `Forestry Chat Asset Cleanup` 事件函数自动通知Runtime；它不是模型工具。
- 正在执行的工具、上传和下载结束后，Runtime删除会话目录。Open WebUI中的上传和产物副本无其他引用时也会删除；跨聊天、知识库等引用保留。
- 后台每10秒重试；Open WebUI或Runtime重启后自动恢复。不要禁用清理事件函数。当前部署仅支持单实例Runtime和SQLite Open WebUI。
- `data/lifecycle.sqlite3`保留少量会话删除标记和未完成清理记录，防止旧请求重新创建已删除会话。
- 本机运行Docker Desktop、Open WebUI及Runtime服务是自动清理的前提；关闭浏览器不影响服务端清理。

直接使用API时，先 `POST /sessions` 传入 `{"chat_id":"实际聊天UUID"}`，随后在资产、工具执行和聊天请求中传入 `X-Chat-ID`。适配器凭服务端密钥调用会话删除及清理接口；这些接口不暴露给模型。

## 当前部署与使用

- Open WebUI：<http://localhost:3000>，刷新后选择 **Forestry Runtime**，建议新建普通聊天（不使用临时聊天）。
- Runtime：<http://localhost:8010/health>；接口文档：<http://localhost:8010/docs>。
- 模型：宿主机 Ollama `qwen3.5:4b`，runtime 经 `host.docker.internal:11434` 调用它。
- Docker 容器：`forestry-runtime`；本项目 `data` 目录绑定挂载为容器 `/data`，可直接在本机查看。
- Open WebUI Pipe：`forestry_runtime`。独立模型的 `file_context` 与 `builtin_tools` 关闭，避免重复 RAG/工具循环。
- 原有 Qwen 模型、两个测试/桥接工具和 MCP 连接保持原配置；新模型不使用它们。

在新聊天中拖入 TIFF、PNG、ZIP 或文本，发送：

> 检查这些附件。告诉我影像尺寸、坐标系、分辨率；列出ZIP内容。

> 解压这个ZIP，读取其中的notes.txt，并将检查结果保存为report.md。

> 给这个GeoTIFF生成预览，说明预览使用了哪些波段。

> 检查这个多光谱GeoTIFF的波段，然后计算NDVI并生成结果文件。

界面使用Open WebUI原生推理面板折叠显示Qwen返回的思考，并用状态事件显示工具名称、完成状态和耗时；完整工具参数与结果保留在Runtime事件及Agent观察中，不再以转义JSON写入回答正文。最终文件使用真实的 Open WebUI 下载链接。不要把模型自行解释的内容当作额外测量结果。本版本已有NDVI和初步候选林冠分割工具，尚无经过验证的最终林冠覆盖度或树高工具。

## 文件与工具

| 工具 | 行为 |
|---|---|
| `list_files` | 只列出本次会话传入/生成的文件 |
| `inspect_file` | TIFF真实CRS、CRS名称、波段描述、像元尺寸、网格面积；PNG/JPG尺寸；其他格式返回文件元数据 |
| `inspect_raster` | 分块扫描真实像元，精确统计掩膜、零值、负值、NaN/Inf、范围和均值，抽样计算分位数，并检查Red/NIR零分母与Alpha背景的重合关系 |
| `read_text` | UTF-8文本、CSV、JSON、GeoJSON等，默认最多12000字符 |
| `inspect_zip` | ZIP清单及路径、文件数量、解压体积检查 |
| `extract_zip` | 解压到独立资产，返回子文件ID和包内相对路径 |
| `preview_image` | TIFF/PNG/JPG/WEBP缩略图；TIFF前3波段或单波段、2–98%拉伸，仅供展示 |
| `calculate_ndvi` | 根据明确指定的Red/NIR波段，分块计算NDVI并输出保留空间参考的单波段float32 GeoTIFF，同时返回有效像元和统计值 |
| `segment_canopy` | 对NDVI使用Otsu或明确阈值生成uint8候选Mask；1=候选、0=有效非候选、255=无效。当前不能区分树冠和草本植被 |
| `save_text` | 新建txt/md/csv/json/geojson文件，不覆盖已有文件 |

上传接受一般文件格式，但“已保存”不等于该格式已有解析器。例如PDF、LAS可以保存，目前没有全文提取或点云处理工具。TIFF工具仅打开真实GTiff，不加载用户上传的VRT。整个像元网格的投影面积不等于森林面积、有效像元面积或样地面积。

文件物理位置为 `/data/sessions/<chat_id>/asset_<uuid>/content`，原始文件名等信息在该会话目录的 `assets.sqlite3`。只通过 `asset_id` 寻址，模型没有宿主机任意目录访问或shell权限。压缩包目录关系以 `archive_path` 返回，每个成员单独登记，不能直接作为依赖相对目录的完整工程运行。

## 对接自己的界面

所有业务接口需 `Authorization: Bearer <RUNTIME_API_KEY>`。Open WebUI 适配器在服务端保管密钥，并传入用户身份 `X-User-ID`；密钥不能发给浏览器。此密钥代表受信任后端，持有者可以指定身份，未来开放多租户API时应改为独立用户认证。

1. `POST /assets`：multipart字段 `file`，返回 `{id,name,size,sha256,...}`。
2. `POST /chat`：发送完整所需历史和附件ID。

```json
{
  "messages": [{"role":"user", "content":"检查这份影像"}],
  "asset_ids": ["asset_实际上传返回的ID"],
  "stream": true
}
```

流式响应是 **NDJSON**，包含 `tool_start`、`tool_end`、`message`、`error`、`done`；这是临时响应事件，不会保存成执行日志。模型每一轮使用非流式Ollama调用，因此最终文字按整段返回，不是逐token显示。非流式返回 `{content,artifacts,ok}`。

其他接口：`GET /assets`、`GET /assets/{id}`、`GET /assets/{id}/content`、`GET /tools`、`POST /tools/execute`。接口不是完整的 OpenAI Chat Completions API；当前通过 Pipe 适配 Open WebUI。

## 部署与维护

在此目录执行：

```powershell
.\setup.ps1
docker compose ps
docker compose stop
docker compose start
```

`setup.ps1` 生成本地 `.env`，构建镜像并启动，再通过现有 Open WebUI 的本地管理员API安装 Pipe 和清理 Event Function。重复安装保留清理范围起始时间。安装脚本在容器内使用现有实例签名密钥生成短时管理员令牌，不打印或导出令牌。支持本次检查的 Open WebUI 0.11.3；其他版本需要核对接口。

重复安装更新本项目专属Pipe与模型。安装前的Pipe快照位于Open WebUI数据卷的 `forestry_runtime_backups`。实际密钥不进入Git或Docker构建上下文。卸载时可在Open WebUI禁用Forestry Runtime函数，然后 `docker compose down`；不要使用 `down -v`，除非明确要删除全部runtime文件。

容器以非root用户运行，宿主机端口只绑定127.0.0.1。Docker Desktop运行时，另一个容器可通过 `host.docker.internal:8010` 访问。已有Open WebUI容器的重启策略未改动。

## 限制

Pipe 0.1.1的内联图片上限为512 MiB（Valves中的`MAX_FILE_MB`），已移除初版约30 MB的Base64限制；采用分块解码到临时磁盘文件，并复用相同SHA-256的已上传附件。Open WebUI自身仍可能在内存中构造Base64。

- 单个runtime文件最多512 MiB；ZIP最多1000条目、解压总大小512 MiB、压缩比200。可配置上传大小，不建议用本版处理数十GB航测原片。
- 每个请求最多8轮、每轮最多8次工具调用；没有后台任务或断点恢复。
- 消息合计超过60000字符时明确拒绝，要求新建聊天；暂不自动压缩上下文。
- 新会话文件按上述生命周期清理；旧资产不纳入。暂没有配额管理。
- Open WebUI本身可能压缩图片，并有独立上传内存/大小限制。定量分析应传原始TIFF；PNG预览不保留空间参考保证。
- 本版以文件工具为主；未将图片像素送入Qwen做视觉推理。
- 显示的是Qwen通过Ollama原生`thinking`字段返回的内容及真实工具事件，不是Runtime内部的隐藏日志；模型可能省略或压缩部分推理。
- 模型仍可能解释错误。真实参数来自工具；业务算法接入后还需对应的领域校验。

## 验证

```powershell
docker cp openwebui_pipe.py forestry-runtime:/tmp/forestry_runtime_pipe.py
docker cp tests/. forestry-runtime:/tmp/tests
docker exec -e PYTHONPATH=/app forestry-runtime python -m unittest discover -s /tmp/tests -v
```

测试覆盖真实GeoTIFF与预览、ZIP解压/路径拒绝、用户和会话隔离、上传限额、工具调用结果回传、API认证与下载、错误轮数上限。`scripts/make_fixtures.py`生成合成验收数据；`scripts/smoke_openwebui.py`在Open WebUI容器内验证HTTP→Pipe→runtime→Qwen→工具→下载完整链路。`scripts/check_chat_uploads.py`检查聊天上传器使用的MIME/process组合。详细结果与测试边界见 [VALIDATION.md](VALIDATION.md)。

## 扩展位置

- `runtime/storage.py`：文件管理。
- `runtime/tools.py`：新增参数模型、工具说明和实现；暂不需要MCP才能扩展。
- `runtime/agent.py`：模型调用与工具循环，将来在这里接入任务状态和工作流。
- `runtime/app.py`：独立HTTP入口。
- `openwebui_pipe.py`：附件ID授权解析、传输与结果下载适配。

参考：[Open WebUI Pipe](https://docs.openwebui.com/features/extensibility/plugin/functions/pipe/)、[Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling)。
