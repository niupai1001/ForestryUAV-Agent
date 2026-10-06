# 范围依赖准备与复用

当前增量支持用户明确授权的离线 Python wheel，在已有受限作业容器中安装并生成任务专属环境。Agent 自动下载依赖、源码包构建和完整依赖 UI 验收仍待实现或验证；联网搜索按用户指示暂缓。

在工作台登记每个 `.whl` 为只读来源，并将它们加入任务输入。选择通用能力，或在自定义范围中启用“准备隔离依赖”“执行受限代码”“观察作业”，以及所需的产物读写能力。模型依次调用：

1. `prepare_environment(wheel_refs=[授权资源ID...])`，返回持久作业。
2. `wait_job(job_id=实际作业ID)`，查看真实退出码和有界日志。成功结果的 `prepared_environment.artifact_id` 指向 `environment.zip`；`lock_artifact_id` 指向锁文件，不能作为代码环境。
3. `code_execute(code=Python代码, input_refs=[授权数据ID...], environment_artifact_id=实际环境归档ID)`，再等待作业并实际读回产物。再次执行可引用同一环境，不重新安装。

准备操作最多接收32个独立 wheel、总量128 MiB。固定 Linux Python 3.12 镜像；pip 使用 `--no-index --only-binary :all: --require-hashes`，保留传递依赖解析。缺少依赖或平台不兼容真实失败，不用 `--no-deps` 绕过。包名/版本、wheel 来源资源、SHA-256、大小、源版本及 Python/平台进入不可变锁文件；准备作业另外记录实际镜像 ID。锁文件不单独列 pip 版本，安装器随不可变镜像绑定。[pip 安全安装说明](https://pip.pypa.io/en/stable/topics/secure-installs/)

包安装和归档解压均在容器内，Windows 宿主不安装或执行包。准备时使用1 GiB Linux `/tmp`，避免 NTFS 挂载的安装语义差异；复用时在专用1 GiB `/environment` tmpfs 解压，允许原生扩展映射。代码作业普通 `/tmp` 仍为64 MiB、noexec。tmpfs 计入相同6 GiB容器内存预算；其余非特权用户、断网、只读根目录、能力移除及作业身份规则保持一致。[Docker tmpfs 说明](https://docs.docker.com/engine/storage/tmpfs/)

环境归档在提交、启动和恢复边界核对来源、哈希、准备作业、当前任务/修订及实际镜像身份。单个压缩归档最多128 MiB；解压最多30,000个文件、512 MiB文件内容，拒绝越界路径、重复项和符号链接。锁文件最多64 KiB。环境通过 `sys.path` 使用，不自动执行 `.pth` 文件；需要入口脚本、源码编译或系统库的包不保证兼容。子角色须显式取得环境归档产物授权，不能猜父任务的环境 ID。

原生等待结果直接返回可复用环境标识、实际安装版本和日志，避免两份完整配方触发结果外置。完整配方仍保存在作业记录和锁文件中，必要时实际读回。作业成功只是软件执行证据，不证明目标或科学精度通过。

## 真实验证

以下下载脚本仅供可信开发者准备固定的公开软件夹具，不是模型工具，不接触任务私密输入。默认从 PyPI 下载；显式选择 TUNA 镜像时仍依据官方 PyPI JSON 的大小和 SHA-256 核对，不能把同源哈希说成独立签名。[PyPI JSON API](https://docs.pypi.org/api/json/)、[TUNA 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)

```powershell
uv run --locked python scripts/download_dependency_fixture.py --source tuna
# 使用脚本实际返回的锁文件路径：
uv run --locked python scripts/smoke_dependencies.py --fixture-lock workspace/dependency-fixtures/fixture-lock-<id>.json --config config/app.wsl.example.yaml
```

软件夹具锁定 NumPy 2.2.6、python-dateutil 2.9.0.post0 和 six 1.17.0，另含故意不兼容的 Windows NumPy wheel。基础真实试次覆盖缺包、缺传递依赖、不兼容、实际安装和两次复用，并核对计算、容器、源文件及宿主包清单。完整证据与失败见 STATUS；它不代表两模型 UI 或完整阶段1验收。

浏览器试次需明确设置 `RUN_DEPENDENCIES_UI=1`、`FORESTRY_DEPENDENCY_FIXTURE_LOCK` 和测试服务地址 `FORESTRY_TEST_BASE_URL`，运行 `frontend/e2e/dependencies.spec.ts`。它从真实 UI 创建任务、选择能力及来源，使用真实模型完成失败诊断、安装、两次计算、中文报告和实际读回；模型预算不自动增加，失败保留。
