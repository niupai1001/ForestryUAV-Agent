# 部署到另一台 Windows 电脑

本文是「把当前这套低空林草遥感 Agent 搬到另一台带 Docker 的电脑」的完整步骤。
目标机器只需要三样东西：**Docker Desktop、Ollama、Python 3**。不再需要 Node/npm，
React 工作台由镜像内的 `frontend` 构建阶段编译。

## 1. 组件与端口

| 组件 | 运行位置 | 端口 | 依赖 |
|---|---|---|---|
| Runtime + 工作台 | 容器 `forestry-runtime` | 8010 | Docker Desktop |
| Host Bridge | 宿主机 Python 进程 | 8011（仅 127.0.0.1） | Python 3.11+ |
| 评分看板（可选） | 宿主机 Python 进程 | 8012（仅 127.0.0.1） | Python 3.11+ |
| 代码作业容器 | Docker | — | 镜像由 `.env` 的 `AGENT_JOB_IMAGE` 决定，默认 `forestry_runtime-runtime:latest` |
| 推理模型 | 宿主机 Ollama | 11434 | 由 `.env` 的 `OLLAMA_MODEL` 决定（未设置时用 `compose.yaml` 的默认值） |

Host Bridge 必须留在宿主机，不进容器：它要按用户授权直接读写宿主目录，并用 Docker
创建作业容器；一旦放进容器，宿主机盘符到容器路径的映射就因机器而异，无法通用部署。

## 2. 新电脑前置条件

1. **Docker Desktop**（WSL2 后端）
   <https://www.docker.com/products/docker-desktop/>，安装后启动一次，确认
   `docker info` 有输出。
2. **Ollama for Windows**：<https://ollama.com/download>
   先决定这台机器用哪个模型，写进 `.env` 的 `OLLAMA_MODEL`（例如 `qwen3.5:4b`），
   再执行 `ollama pull <那个模型>`。`setup.ps1` 会读取同一个值并自动补拉。
3. **Python 3.11+**：<https://www.python.org/downloads/>
   安装时勾选 *Add python.exe to PATH*。Host Bridge 只用标准库，不需要 pip 安装任何包。

> `npm`、`node` 不需要；`frontend/dist` 也不需要预先存在。

## 3. 方式 A：git clone + 一键启动（推荐）

```powershell
git clone https://github.com/niupai1001/ForestryUAV-Agent.git
cd ForestryUAV-Agent\forestry_runtime
```

然后**双击 `start.cmd`**，或在 PowerShell 中执行：

```powershell
.\setup.ps1 -OpenBrowser
```

`start.cmd` 会以 `-ExecutionPolicy Bypass` 调用 `setup.ps1`，后者依次完成：

1. 生成 `.env` 中的 `RUNTIME_API_KEY` / `UI_SESSION_KEY` / `HOST_BRIDGE_KEY`（已存在则保留）；
2. 建立 `data\`、`knowledge\`、以及指南核验台账占位文件；
3. 启动 Host Bridge 并等待 `http://127.0.0.1:8011/health`；
4. 确保 Ollama 在运行，缺失时自动 `ollama pull` 目标模型；
5. 确保 Docker Desktop 引擎可用（含已知的 Docker 陈旧 socket 修复）；
6. 预拉代码作业镜像 `python:3.12-slim`；
7. `docker compose up -d --build`，镜像内编译前端与安装 Python 依赖；
8. 轮询 `http://127.0.0.1:8010/health`，成功后打印版本并打开浏览器。

常用参数：

| 参数 | 作用 |
|---|---|
| `-OpenBrowser` | 就绪后打开工作台 |
| `-SkipBuild` | 不重建镜像，直接 `up -d --no-build`（改了代码后不要用） |
| `-SkipModelPull` | 不自动拉模型，只告警 |
| `-RotateRuntimeKey` | 轮换 `.env` 中全部密钥 |

首次构建需要联网（Node 基础镜像、Python 依赖），通常几分钟；之后重建会走缓存。

## 4. 方式 B：不经过 git 的可部署快照

只有当你**不打算把当前工作区提交到 git** 时才需要它。原因见下一节。

```powershell
cd forestry_runtime
.\scripts\make-release.ps1 -OpenFolder
```

它会把当前工作区（含未跟踪文件、遵守 `.gitignore`）打包成一个 zip，解压后目录结构与
仓库一致，直接双击其中的 `forestry_runtime\start.cmd` 即可。`.env`、`data\`、
`node_modules`、`frontend\dist` 等被排除，密钥由目标机器首次启动时重新生成。

### 为什么有时不能只靠 git clone

`git clone` 拿到的是**已提交**的状态。当前工作区里有大量未提交内容（新增的
`evaluation/` 用例、`compose.eval.yaml`、知识指南、运行时改动等）。如果你直接 clone，
新电脑跑起来的是旧代码，不是你现在这台机器上验证过的系统。两条路：

- 先 `git add -A && git commit && git push`，再用方式 A；
- 或者用方式 B 打包当前工作区。

## 5. 首次使用

1. 打开 <http://127.0.0.1:8010/>，在工作台创建并选择项目。
2. 知识索引：把本地知识源填 `/knowledge/guides` 再点「索引」。
   宿主目录由 `KNOWLEDGE_HOST_ROOT` 只读挂载，模型不会因此获得该目录的文件工具权限。
3. 需要无人机/栅格/PROSAIL 能力时，在 `.env` 中设置：

   ```dotenv
   REMOTE_SENSING_PLUGINS_ENABLED=true
   UAV_INPUT_HOST_ROOT=E:/UAV_INPUT
   ```

   然后重新执行 `.\setup.ps1`（会叠加 `compose.remote-sensing.yaml` 并重建镜像）。

## 6. 迁移已有数据

想保留会话、作业记录与授权，把这三样从旧机器复制到新机器的同名位置：

| 内容 | 说明 |
|---|---|
| `data\` | SQLite、事件 JSONL、工作区与资产 |
| `knowledge\` | 知识源原文（若两边一致可省略） |
| `.env` | 想沿用同一套密钥才复制；否则让新机器重新生成更安全 |

```powershell
robocopy "旧机器\forestry_runtime\data" "新机器\forestry_runtime\data" /E /R:1 /W:1
```

注意：**正在运行的作业不会迁移**。作业容器留在旧机器上，新机器的 Host Bridge 只把
历史记录当成只读事实展示，不会盲目重放（这也是 Runtime 的既定语义）。

## 7. 端口与挂载

| 容器内路径 | 宿主来源 | 权限 |
|---|---|---|
| `/data` | `forestry_runtime\data` | 读写 |
| `/knowledge` | `KNOWLEDGE_HOST_ROOT`（默认 `.\knowledge`） | 只读 |
| `/guide-verification/GUIDE_VERIFICATION.md` | `evaluation\grounded_v1\` 下的台账 | 只读 |
| `/uav-input` | `UAV_INPUT_HOST_ROOT`（仅遥感镜像） | 只读 |

工作台只监听 `127.0.0.1`，当前版本按本机单用户设计，不要直接暴露到公网。

## 8. 故障排查

| 现象 | 原因与处理 |
|---|---|
| `A Python 3 interpreter is required` | 宿主机没装 Python 或没加入 PATH；装完重开终端 |
| `Host bridge did not start` | 看 `data\host-bridge-error.log`，多为 8011 端口被占用 |
| `Ollama is not running and ollama.exe was not found` | 安装 Ollama 后重跑 `start.cmd` |
| 模型未安装 | `setup.ps1` 会按 `.env` 的 `OLLAMA_MODEL` 自动拉取；想跳过用 `start.cmd -SkipModelPull`。模型与本机实际下载的不一致时，`/health` 仍会显示 `model_reachable: true`，要到推理时才会失败 |
| `Docker Desktop Linux engine is unavailable` | 手动启动 Docker Desktop，等它完全就绪后重跑 |
| Docker 启动后引擎立刻退出，日志里有 `检测到 localhost 代理配置，但未镜像到 WSL` | 见下方「本地代理导致 WSL 起不来」 |
| 构建卡在 `npm ci` / 前端报错 | 首次构建需联网；确认能访问 Docker Hub 与 npm registry |
| 工作台报模型错误 | `docker compose logs runtime`，并确认宿主 11434 可达 |
| 改了代码但行为没变 | 去掉 `-SkipBuild` 重新执行 `.\setup.ps1` |

### 本地代理导致 WSL 起不来

如果这台机器开着系统代理且地址是 `127.0.0.1:<端口>`（Clash、v2ray 等常见配置），WSL2 会
自动把该代理镜像进 Linux 子系统，而 NAT 模式不支持 localhost 代理，`wsl.exe` 随之失败，
Docker Desktop 的 Linux 引擎会启动后立刻退出。日志里能看到：

```text
wsl: 检测到 localhost 代理配置，但未镜像到 WSL。NAT 模式下的 WSL 不支持 localhost 代理。
```

处理方式是在 `%USERPROFILE%\.wslconfig` 中关闭自动代理，然后 `wsl --shutdown` 并重启
Docker Desktop：

```ini
[wsl2]
autoProxy=false
```

Docker 拉取镜像仍可通过 Docker Desktop 自己的 *Settings → Resources → Proxies* 配置，
不受此项影响。Windows 11 22H2 及以上也可以改用 `networkingMode=mirrored`。

## 9. 验证部署成功

```powershell
docker compose ps                     # runtime 应为 healthy
Invoke-RestMethod http://127.0.0.1:8010/health
```

`health` 返回中 `dependencies.model_reachable` 与 `dependencies.host_bridge_reachable`
都应为 `true`，否则 `status` 会显示为 `degraded`，并带出具体原因。
