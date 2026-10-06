# 项目独立 WSL 执行环境

这是 Windows 本地部署选项。受限代码仍由已有 Jobs 管理器在非特权、断网、只读根目录的容器中执行，不在 Windows 宿主运行生成代码。原 Docker Desktop 配置继续可选；项目独立发行版不重置或注销现有发行版，不改变默认 WSL 发行版。

已实测 WSL 2.6.3、Ubuntu 24.04.5、Docker 29.1.3、containerd 2.2.1、runc 1.3.4，以及固定摘要的 Linux/amd64 Python 3.12.15 镜像。完整实测包清单和镜像身份写入 `workspace/isolated-executor/runtime-lock.json`。这是部署证据，不代表完整通用阶段或科学精度验收通过。

## 准备

前提为启用的 WSL 2、Git 自带 GPG 或可用的原生 GPG，以及已锁定的应用 Python 环境。以下为可信开发者安装命令，不提供给模型工具：

```powershell
uv run --locked python scripts/setup_wsl_executor.py download
uv run --locked python scripts/setup_wsl_executor.py import
uv run --locked python scripts/setup_wsl_executor.py install
uv run --locked python scripts/setup_wsl_executor.py pull
uv run --locked python scripts/setup_wsl_executor.py lock --config config/app.wsl.example.yaml
```

Ubuntu WSL 镜像使用固定 SHA-256，并验证官方签名清单及固定发布密钥指纹；导入前再次验证，锁文件中的布尔标记不能代替签名验证。同名发行版必须属于本项目安装目录，否则拒绝覆盖；非空未登记目录保留给操作者检查。系统包版本固定在安装脚本中，仓库不再提供该版本时安装失败，需要显式更新锁及验证，不能静默更换版本。

Docker 通过 guest Unix socket 访问，不开启未认证 TCP 端口。包与镜像下载仅在可信准备阶段进行，没有任务私密输入挂载。镜像采用 Docker 官方的 [Amazon ECR Public 发布路径](https://www.docker.com/blog/news-from-aws-reinvent-docker-official-images-on-amazon-ecr-public/)，固定实际镜像摘要；当前网络下 Docker Hub 与 Google 官方缓存连接超时，失败记录保留。

发行版目录和虚拟磁盘位于本项目 `workspace/isolated-executor/wsl`。移动工作区前需按 WSL 支持的流程另行处理发行版；脚本不会自动删除或迁移已登记发行版。现有 Windows 只读源路径转换为 `/mnt/<drive>/...`，拒绝 UNC 和相对路径；目录输入仍不支持。CLI 使用项目私有 Docker 配置，清除宿主凭证、代理和 WSLENV，并在 guest 使用 `env -i`。

## 启动和检查

```powershell
uv run --locked forestry-agent serve --config config/app.wsl.example.yaml
uv run --locked python scripts/smoke_executor.py --config config/app.wsl.example.yaml
```

首次检查使用项目内合成 CSV，记录真实作业、容器身份/限制、结果、输入哈希、日志、取消和超时。重新打开对象/数据库的观察测试明确标记 `real_process_restart=false`，不能冒充进程中断恢复。浏览器真实模型调试检查另见 `frontend/e2e/executor.spec.ts`；使用 `RUN_EXECUTOR_UI=1` 显式启用，测试服务可用 `FORESTRY_TEST_BASE_URL` 指定。

实际终态日志随 `wait_job` 返回，使模型能够观察 traceback 并修复。日志最多最后 200 行、stdout 64 KiB、stderr 4 KiB，标记有界，不保证保留完整历史。WSL 自身的本地代理提示目前仍可能混入 stderr，不能把它误判为容器计算失败；作业终态依据匹配容器的实际退出码。

完整依赖准备、真实服务进程中断恢复、低资源和长任务质量仍待验证。联网搜索按用户 2026-10-06 指示暂缓开发；本部署下载运行时与 Agent 搜索能力分别记录。

English: This optional project-owned WSL distribution uses the existing isolated Docker job manager, an explicit Unix socket, fixed signed Ubuntu image, pinned guest packages and immutable Python image. It does not reset existing Docker/WSL data. Real restricted compute, cancellation and timeout were exercised; object/store reopen is not process restart. Dependency preparation and complete general-stage acceptance remain pending. Agent network-search development is deferred by the user's instruction.
