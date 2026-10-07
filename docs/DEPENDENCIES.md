# 范围依赖准备与复用

1.12可从根需求自动解析wheel闭包，版本约束、extras和Linux目标marker使用成熟packaging/resolvelib处理，不另写回退算法。启用“获取公开依赖”“执行受限代码”“观察作业”，安装另需“准备隔离依赖”。流程：

1. `resolve_dependencies(requirements=["numpy==2.2.6","python-dateutil==2.9.0.post0"])`先返回无私密输入挂载的断网目标探测作业；`wait_job`成功后找到execution-target.json的真实artifact_id。同任务/修订/镜像已有成功目标时直接返回可复用target_artifact_id。
2. 用同requirements及target_artifact_id再次解析；私密上下文先等待元数据审批，再用匹配approval_id执行。审批明确包含传递依赖、最多32项目/64请求/32 MiB/180秒，只有官方PyPI索引和文件级元数据，尚不下载wheel。目标是实际容器CPython3.12/Linux/glibc，不使用Windows宿主标签。
3. 将返回的完整wheels和resolution_lock_artifact_id传给`acquire_dependencies(source="tuna")`。不带此前元数据审批ID；精确下载提出新审批，批准后使用新ID。解析锁要求集合及官方wheel SHA一致，变化须重新解析/批准。
4. 将获取的wheel_artifact_ids交给prepare_environment；成功wait_job之后复用prepared_environment.artifact_id。下载/解析成功不等于实际pip安装成功。

索引单份8 MiB、Core Metadata单份2 MiB，最多100解析轮；撤回文件、无SHA文件级元数据、不兼容文件不作为候选。默认不选预发布，显式版本约束可以选择；所需URL/路径依赖不开放，目标marker不适用的声明不会发送请求。冲突或预算耗尽明确失败，不返回部分集合。解析锁/不可变快照保留候选SHA、目标作业及实际镜像；同源哈希不是独立签名。[Simple/PEP658规范](https://packaging.python.org/en/latest/specifications/simple-repository-api/)、[resolvelib官方provider示例](https://github.com/sarugaku/resolvelib/blob/main/examples/pypi_wheel_provider.py)

`scripts/smoke_acquisition.py --config config/app.wsl.example.yaml --source tuna --resolve`真实验证解析/获取/安装/两次复用；15项通过，不调用模型。设置RUN_RESOLUTION_UI=1和FORESTRY_TEST_BASE_URL运行frontend/e2e/resolution.spec.ts验证完整模型流程；当前两试次失败，详情见STATUS。源码构建、其他运行时/元数据回退及两模型稳定完整质量仍未完成。以下1.11及更早为历史说明。

1.11增加根协调者公开依赖获取。调用`acquire_dependencies(wheels=[{name:"six",version:"1.17.0",filename:"six-1.17.0-py2.py3-none-any.whl"}],source="pypi")`；私密任务先在UI核对精确请求并批准，再携带匹配approval_id执行。将返回的真实wheel_artifact_ids传给`prepare_environment(wheel_artifact_ids=[...])`；wait_job成功后仍使用prepared_environment.artifact_id复用环境。下载成功不等于安装成功。

source可选pypi/tuna，每次1–32项/128 MiB，官方元数据单份2 MiB、总时限180秒。只接收精确包名/版本/发行wheel文件名，不接收任意URL或私有索引；官方JSON绑定未撤回文件、大小/SHA，TUNA仅提供字节。沿用固定公开DNS/TLS及精确审批边界，逐请求复核控制、来源版本、修订和撤销，不发送本地正文/凭证/环境代理。工作台显示完整包请求、全部元数据地址、目的地和限额。

结果包括不可变wheel、官方元数据快照和acquisition-lock.json。准备作业只读挂载本任务当前修订已确认获取、哈希仍匹配的产物；子角色须显式获得产物授权。安装/解压仍在原受限断网容器，完整传递依赖由pip验证，不用--no-deps。模型须提供完整wheel集合；版本候选/自动解析、源码构建和完整两模型任务稳定性待完成，官方同源哈希不构成独立签名。

实测命令：`uv run --locked python scripts/smoke_acquisition.py --config config/app.wsl.example.yaml --source tuna`。它通过应用工具与显式审批获取NumPy/dateutil/six，再安装、两次复用计算/读回、核对断网/只读源/宿主包；没有调用模型。设置RUN_ACQUISITION_UI=1与FORESTRY_TEST_BASE_URL运行frontend/e2e/acquisition.spec.ts，可验证真实模型审批下载/锁文件读回；这是组件检查，不代替复合安装/报告验收。212项回归及详细失败/成功证据见STATUS。

以下为此前离线安装与复合任务的历史说明。

当前增量支持用户明确授权的离线 Python wheel，在已有受限作业容器中安装并生成任务专属环境。大模型通过加强后的依赖UI试次及独立报告审查，小模型仍失败；Agent 自动下载依赖、源码包构建和两模型完整依赖验收仍待完成。联网搜索按用户指示暂缓。

在工作台登记每个 `.whl` 为只读来源，并将它们加入任务输入。选择通用能力，或在自定义范围中启用“准备隔离依赖”“执行受限代码”“观察作业”，以及所需的产物读写能力。模型依次调用：

1. `prepare_environment(wheel_refs=[授权资源ID...])`，返回持久作业。
2. `wait_job(job_id=实际作业ID)`，查看真实退出码和有界日志。成功结果的 `prepared_environment.artifact_id` 指向 `environment.zip`；`lock_artifact_id` 指向锁文件，不能作为代码环境。
3. `code_execute(code=Python代码, input_refs=[授权数据ID...], environment_artifact_id=实际环境归档ID)`，再等待作业并实际读回产物。再次执行可引用同一环境，不重新安装。

准备操作最多接收32个独立 wheel、总量128 MiB。固定 Linux Python 3.12 镜像；pip 使用 `--no-index --only-binary :all: --require-hashes`，保留传递依赖解析。缺少依赖或平台不兼容真实失败，不用 `--no-deps` 绕过。包名/版本、wheel 来源资源、SHA-256、大小、源版本及 Python/平台进入不可变锁文件；准备作业另外记录实际镜像 ID。锁文件不单独列 pip 版本，安装器随不可变镜像绑定。[pip 安全安装说明](https://pip.pypa.io/en/stable/topics/secure-installs/)

包安装和归档解压均在容器内，Windows 宿主不安装或执行包。准备时使用1 GiB Linux `/tmp`，避免 NTFS 挂载的安装语义差异；复用时在专用1 GiB `/environment` tmpfs 解压，允许原生扩展映射。代码作业普通 `/tmp` 仍为64 MiB、noexec。tmpfs 计入相同6 GiB容器内存预算；其余非特权用户、断网、只读根目录、能力移除及作业身份规则保持一致。[Docker tmpfs 说明](https://docs.docker.com/engine/storage/tmpfs/)

环境归档在提交、启动和恢复边界核对来源、哈希、准备作业、当前任务/修订及实际镜像身份。单个压缩归档最多128 MiB；解压最多30,000个文件、512 MiB文件内容，拒绝越界路径、重复项和符号链接。锁文件最多64 KiB。环境通过 `sys.path` 使用，不自动执行 `.pth` 文件；需要入口脚本、源码编译或系统库的包不保证兼容。子角色须显式取得环境归档产物授权，不能猜父任务的环境 ID。

原生等待结果直接返回可复用环境标识、实际安装版本和日志，避免两份完整配方触发结果外置。完整配方仍保存在作业记录和锁文件中，必要时实际读回。作业成功只是软件执行证据，不证明目标或科学精度通过。

H1/H2 根指令另外提供已有产物名称/ID和当前修订最近作业的有界记录，诊断只复制已确认终态观察的stderr末尾512字节；记录不能代替新观察。报告复制实际job_id，不能将动作回执ID标为作业；仅对实际返回来源citation的读取动作使用citation_actions，代码/作业通常没有来源citation。不同标识不可混用，read_artifact只接受artifact_id与offset。

## 模型回执与失败修正（1.13）

提交的模型回执省略重复environment_recipe及environment.manifest，完整API/作业/确认动作仍保留，观察继续提供实际日志和环境身份。H1/H2根事实最多保留当前修订各一份确认解析与下载的动作/锁/wheel身份摘要，无正文，不替代实际字节、安装或验收核验。活动审批提供完整范围；终态模型摘要含scope_is_summary与消费动作，不能当作新授权。H0与子角色不注入这些依赖事实。

resolve_dependencies的target_artifact_id接受UUID或null，不接受字符串"None"。wait_job不存在的ID通过原动作日志返回失败，提示使用成功提交回执中的job.job_id，不能使用action_id；原生模型仍受既有纠正、重复操作及请求预算限制。权限/修订/控制错误不会被自动重试。元数据请求失败仍消费本次批准，后续新尝试须重新提出匹配范围，不得重用消费审批。

完整回归243项。v2大模型完整流程经同任务零执行复核及独立来源审计通过，原API字段误报保存；v2小模型、最新v3两模型失败。此单次成功及39份已发输入审计不等于两模型稳定质量或阶段1验收。完整证据见STATUS。

## 审批阶段与失败后新尝试（1.14）

依赖审批回执、H1/H2审批事实和既有继续消息含requested_tool，来自原请求kind/operation。元数据批准后重调resolve_dependencies，下载批准后重调acquire_dependencies，使用原精确参数；两类审批不可混用。审批消费不证明请求成功：失败后的新尝试须去掉旧approval_id，提出新范围再等待用户决定。应用不会自动重试或用摘要重新授权。

过期的pending/approved审批不会继续阻塞同摘要的新请求：不带旧ID提出时，原记录在同一事务标为expired，返回新pending；旧显式ID仍拒绝。用户拒绝/撤销、来源变化、修订、控制和实际外发的有效期校验保持，新记录不能在批准前发送。

251项回归包括两类失败或过期后重新审批的协议夹具。真实UI脚本仅对前次消费动作确认失败、相同request_sha256的精确软件夹具批准重试，每类最多一次初始审批/两次重试；所有配置预算和成功阶段/产物/报告检查保持。脚本的额外审批交互与产品结果分开记录，原v4失败保留，最新结果见STATUS。

## 真实验证

以下下载脚本仅供可信开发者准备固定的公开软件夹具，不是模型工具，不接触任务私密输入。默认从 PyPI 下载；显式选择 TUNA 镜像时仍依据官方 PyPI JSON 的大小和 SHA-256 核对，不能把同源哈希说成独立签名。[PyPI JSON API](https://docs.pypi.org/api/json/)、[TUNA 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)

```powershell
uv run --locked python scripts/download_dependency_fixture.py --source tuna
# 使用脚本实际返回的锁文件路径：
uv run --locked python scripts/smoke_dependencies.py --fixture-lock workspace/dependency-fixtures/fixture-lock-<id>.json --config config/app.wsl.example.yaml
```

软件夹具锁定 NumPy 2.2.6、python-dateutil 2.9.0.post0 和 six 1.17.0，另含故意不兼容的 Windows NumPy wheel。基础真实试次覆盖缺包、缺传递依赖、不兼容、实际安装和两次复用，并核对计算、容器、源文件及宿主包清单。完整证据与失败见 STATUS；它不代表两模型 UI 或完整阶段1验收。

浏览器试次需明确设置 `RUN_DEPENDENCIES_UI=1`、`FORESTRY_DEPENDENCY_FIXTURE_LOCK` 和测试服务地址 `FORESTRY_TEST_BASE_URL`，运行 `frontend/e2e/dependencies.spec.ts`。它从真实 UI 创建任务、选择能力及来源，使用真实模型完成失败诊断、安装、两次计算、中文报告和实际读回；模型预算不自动增加，失败保留。

历史v7为1通过/1失败：large `979c4090-c284-4f7e-ba14-f7438e251d31` 完成准确的五个作业、两次同环境计算和三份文件读回，报告SHA/真实作业引用及过程语义另行核对。small `3923952f-416e-47c4-a1cc-bfb1b509fa38` 虽生成两个正确命名的结果，却继续重复计算和安装，达到30请求上限；报告错把artifact.read动作ID写成缺包作业ID，且没有报告读回。完整失败保留。两模型45份输入快照的独立审计通过，只证明输入/用量与记录绑定，不代表小模型任务成功或完整阶段1验收。详情见 STATUS。

1.10在H1/H2根事实中保留同回合最近8个不同产物的最后确认读取窗口/SHA/来源动作，不复制正文；has_more=false且offset>0只表示末尾窗口，不能证明全文已读、当前未篡改或验收。准备成功的environment_artifact_id与environment_observation_action_id来自匹配真实作业/归档/准备动作的确认回执，未观察成功不猜归档或锁文件身份。需要找回正文或新窗口仍应实际读取。

历史v8大模型因报告引用匹配误报失败，同任务零执行补充复核通过；计划进度仍过期。v9、v10两模型均失败。最新v11小模型重复准备/失败观察，没有计算结果；大模型完成两次同环境计算和JSON读回，随后重复读取，未写报告；两者NO_PROGRESS暂停。38份实际输入、确认回执、原生提醒、步骤ID和完整目标去重审计通过，只证明集成，不能宣称完整依赖UI或两模型质量通过。完整回归188项通过，原始失败分别保存，详见STATUS。

1.15（历史）：修正同任务/修订/角色及精确响应/工具Schema匹配的跨轮次重复计费，清单记录用量来源context/turn；不提高原32K/4K预算或增加历史管理器。254项完整回归通过，v6大模型3次跨轮次来源核验通过。但最新两模型完整流程仍失败：小模型重复观察后无进展暂停，大模型三次元数据超时后提出超过两次重试门槛的第四审批，均无锁/下载/安装/结果/报告。32份已发输入审计通过不代替验收，阶段1及原源码构建/恢复/角色/评估等缺口保持，搜索暂缓。详见STATUS；前述1.14及更早为历史。

当前1.16：仅依赖Simple JSON索引采用有界gzip，压缩传输及解压累计均受原32 MiB预算限制，原时间/审批/来源/权限保护不变；wheel及非索引读取仍identity，不开发联网搜索。271项最终完整回归和真实解析/下载/断网安装/两次复用15项通过。v7大模型完整UI与独立来源审计通过；小模型解析/下载成功后重复已完成阶段而暂停。37份输入补充审计通过，原审计误报和模型失败保留。完整通用阶段及源码构建/元数据回退/恢复/角色/评估等缺口仍未完成，详见STATUS。
