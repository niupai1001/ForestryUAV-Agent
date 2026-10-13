# Windows 一键打开林业 Agent

双击项目上级目录的 **`启动林业Agent.cmd`**，或项目目录中的 **`Start-Agent.cmd`**。入口会在默认浏览器显示启动状态，准备完成后进入 Agent 工作台。启动日志位于 `workspace/launcher/`，不需要打开多个终端。

入口执行以下操作：

1. 核对当前 Agent 的配置与工作区身份，已有服务直接复用。
2. 检查 Ollama；未运行时在后台启动 `ollama serve`，读取本地已安装模型列表。
3. 检查配置指定的 Docker 后端。专用 WSL 配置唤醒发行版并启动 `docker.service`；Docker Desktop 配置启动 Docker Desktop。检查配置中的本地镜像。
4. Agent 未运行时使用现有 `.venv` 启动后端；健康及工作区身份核验通过后打开工作台。前端已随应用构建，日常启动不需要 Node 或前端开发服务。

当前机器默认沿用 `workspace/resolution-ui-config.yaml`，地址为 **http://127.0.0.1:8769**，历史任务保存在原工作区。配置选择顺序：显式参数 → `config/app.local.yaml` → 现有主工作台配置 `workspace/resolution-ui-config.yaml` → `config/app.wsl.example.yaml`。如需固定其他配置，可手动复制并修改 `config/app.local.yaml`，该文件不提交版本库。

在 PowerShell 指定其他配置：

```powershell
.\scripts\Start-Agent.ps1 -Config "C:\完整路径\app.yaml"
```

首次准备环境需先按 [README](../README.md) 安装锁定的 Python 依赖、Ollama 和模型，并按 [WSL 执行环境说明](EXECUTOR-WSL.md) 准备专用发行版或使用已安装的 Docker Desktop。一键入口负责日常启动，不自动安装软件、下载模型/镜像或重建科学环境。科学作业仍要求所选应用配置包含已准备的固定 `science_image`；入口不会给历史任务追加科学执行权限。

重复点击不会重复启动已核验的服务；启动过程使用 Windows 文件锁避免并发启动。同端口出现身份或配置不匹配时，入口保留原服务并显示原因。正在运行的工作台若尚未加载磁盘上的更新，会显示提示，由用户先结清/暂停任务再决定重启。

环境缺项时，状态页保留原因和日志位置，并另开工作台供查看历史；此状态不表示模型或代码作业已可用。后台服务持续运行，关闭浏览器不会关闭 Agent。入口不自动恢复暂停任务、不调用模型、不提交作业、不探测联网搜索，也不重新计算已有结果。

入口验收仅覆盖新增启动行为。验证触发、复用证据和实际结果见 [启动交付记录](ONE-CLICK-START-DELIVERY-20261013.md)。
