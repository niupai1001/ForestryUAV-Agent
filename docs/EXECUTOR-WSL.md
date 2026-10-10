# 项目独立 WSL 执行环境

1.24服务83696已确认完整关闭，候选26080执行句柄及监听在跨日观察时均不存在，才重新启动83512/会话21099。当前服务健康和加载源码见workspace/evidence/resolution-reuse-service-ready-20261010-v1.json。未运行完整流程新任务或重做旧作业；原实际解析锁/元数据与当前镜像只读核验通过，无新增动作/作业/审批。固定WSL执行器仍可用。qwen3.8:latest缺失别名已由相同摘要的本地qwen3.8:27b恢复，无新下载；小模型摘要变化保持历史证据边界。真实模型复用效果和完整流程仍未通过，详见[STATUS](STATUS.md)的1.24节；本地诊断须关闭搜索探测。

1.23 审批暂停/续执行仍使用原 Jobs/WSL/固定镜像，没有宿主执行或新的执行器。自有8769工作台经两次空闲核验和Ctrl-C完整关闭后加载最终候选，当前83696进程的身份、源码哈希和健康状态见 workspace/evidence/native-approval-service-ready-20261008-v2.json；历史15暂停/11等待任务保持，没有自动恢复。真实同任务 UI 续执行复用既有作业；最终参数说明已加载，尚未进行真实模型效果复验。完整流程未通过，当前证据见 [STATUS](STATUS.md) 的1.23节。

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
uv run --locked python scripts/smoke_recovery.py --config config/app.wsl.example.yaml --models small large
```

首次检查使用项目内合成 CSV，记录真实作业、容器身份/限制、结果、输入哈希、日志、取消和超时。重新打开对象/数据库的观察测试明确标记 `real_process_restart=false`，不能冒充进程中断恢复。浏览器真实模型调试检查另见 `frontend/e2e/executor.spec.ts`；使用 `RUN_EXECUTOR_UI=1` 显式启用，测试服务可用 `FORESTRY_TEST_BASE_URL` 指定。

实际终态日志随 `wait_job` 返回，使模型能够观察 traceback 并修复。日志最多最后 200 行、stdout 64 KiB、stderr 4 KiB，标记有界，不保证保留完整历史。WSL 自身的本地代理提示目前仍可能混入 stderr，不能把它误判为容器计算失败；作业终态依据匹配容器的实际退出码。

`smoke_recovery.py` 为每个模型创建独立配置、数据库和服务进程。在真实日志出现 RESTART_READY 后，只终止自己启动且核对过身份的服务，检查容器继续运行，再新建服务并显式恢复同一回合。记录两次进程身份、同一容器、一次提交/完成、数值、源哈希及实际读回；HTTP 日志读取预算覆盖两个有界 Docker 调用。失败记录和真实模型报告保留，不自动重跑未确认停止的服务，也不停止已有工作台服务。

两模型该运行中作业的真实进程恢复试次已通过14项检查，见 STATUS；仍未覆盖全部崩溃窗口、恢复 UI、完整依赖、低资源和长任务质量。模型报告存在含混表述，不能替代脚本进程/容器证据。联网搜索按用户 2026-10-06 指示暂缓开发；本部署下载运行时与 Agent 搜索能力分别记录。

新增[离线依赖准备与复用](DEPENDENCIES.md)仍复用同一 Jobs。pip 安装使用 Linux tmpfs；环境归档解压在专用 `/environment` tmpfs 中进行，允许原生扩展映射，代码作业普通 `/tmp` 保持 noexec。两者计入原有容器内存预算，不是额外内存。基础真实安装/两次复用试次通过15项检查；完整两模型 UI、Agent 依赖获取和源码构建仍未通过或完成。

English: This optional project-owned WSL distribution uses the existing isolated Docker job manager, an explicit Unix socket, fixed signed Ubuntu image, pinned guest packages and immutable Python image. It does not reset existing Docker/WSL data. Real restricted compute, cancellation and timeout were exercised; object/store reopen is not process restart. The offline dependency core trial passes 15 checks, but complete model UI, acquisition/source builds and general-stage acceptance remain pending. Agent network-search development is deferred by the user's instruction.


## 1.22 模型UI源码检查记录（2026-10-08）

专用8769服务的旧PID588/create1791433150.6078172在精确身份、任务/Jobs/动作/assignment闲置核验后尝试两次Ctrl-C未停止；重新核验后仅终止该已知闲置进程，确认终止并启动新PID63504/create1791434943.9302144（exec77541）。application/database ready、domain not_implemented；没有操作未知8768或自动恢复历史暂停任务。证据source-discovery-service-reload/terminal/ready-20261008-v1.json。

四个H1实际模型试次共8个终态Jobs（6成功/2失败），没有源码成功构建或最终环境/函数结果/报告，全部流程失败保留。每任务14项只读独立核验包括实际容器network=none、UID65534、根只读、6GiB/4CPU、输入只读，真实原生代码、生产者、产物字节、审批和源版本均通过。动态检查前端缺失的提交前拒绝由3种回归夹具验证；当前修复版模型试次未证明能走到完整动态检查。测试轮次和实际产品限制详见STATUS，不把Docker启动/退出码等同模型或科学验收。完整阶段1、领域、规模与发布仍待完成，联网搜索暂缓。
