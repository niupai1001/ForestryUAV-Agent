# 范围依赖准备与复用

## 1.22：动态检查前置条件及实际模型失败边界

动态检查需已授权、字节核验的pyproject-hooks==1.2.0和packaging==25.0 wheel；缺少或版本不符时返回PRECONDITION_MISSING，尚无Job，不得猜job_id或调用wait_job。依照同源码static结果，将实际requirements及frontend_requirements交既有解析/独立审批/获取，可分批补齐后dynamic；实际内容和后端约束仍在容器核验。源码解析锁在现有精确下载接口中是可选项，提供时必须核验；未提供时仍须独立精确审批及实际官方元数据/哈希校验。测试审批还要求候选/文件名来自真实解析结果，禁止猜测。没有强制重写成新工作流。

349项最终回归通过。新两轮四个模型UI试次均未完成：第一轮有一个测试强制锁错误，第二轮小模型猜wheel、大模型在构建依赖审批后无进展暂停。57份输入/来源及每任务14项独立完整性核验通过，不代表自主依赖流程验收。详见STATUS；下文1.21工具验证和旧试次为历史。

## 1.21：静态声明与标准后端 hook 发现构建依赖

沿用原 prepare_environment 的源码输入，传 inspect_build="static"，不提供 wheel；它与 build_only 互斥。提交后 wait_job 返回 build_requirements：inspection、requirements、frontend_requirements、实际清单 artifact_id/image_id 和 runtime_installation=false。静态检查在原受限容器内复用源码构建的有界解包，pyproject.toml 最多 1 MiB；读取 build-system.requires/backend/backend-path，缺声明的 setup.py 项目按标准使用 setuptools>=40.8.0 与 setuptools.build_meta:__legacy__。缺 required 字段、坏 TOML、越界 backend-path 或不合法 PEP508 需求如实失败，不执行 setup.py。返回清单只是有来源的构建需求，不是完整运行依赖闭包或环境。

将 requirements + frontend_requirements 交原 resolve_dependencies，目标仍由同一固定镜像实际探测；元数据和精确下载仍分别审批。检查前端固定 pyproject-hooks==1.2.0/packaging==25.0，不属于应用宿主安装。取得完整已核验 wheel 后，对同一源码传 inspect_build="dynamic"。临时隔离 venv 先从只读输入核验 SHA 并离线完整安装 wheel，再由原生 pip 核对静态版本/extra/marker；标准 BuildBackendHookCaller/get_requires_for_build_wheel 只在容器内执行源码后端，无静默联网或禁用最终原生构建隔离。[PyPA hook API](https://pyproject-hooks.readthedocs.io/en/latest/pyproject_hooks.html)、[pip 构建依赖与 legacy 回退](https://pip.pypa.io/en/stable/reference/build-system/)

动态结果同时保留静态与附加需求。每组最多 32 个有界字符串，合并去重后最多 32 个 PEP508 根，不接受 URL/路径/私有索引；清单最多 256 KiB。返回完整 requirements + frontend_requirements，仍通过原解析和独立获取审批补齐。然后 build_only 生成源码 wheel，重新解析原运行 requirements，最后只安装完整运行 wheel 集合；检查用前端及构建工具不必进入最终环境。动态检查不是 wheel 构建，返回 build_requirements，不返回 prepared_wheels 或 prepared_environment。即使后端额外写环境归档，也不能由该用途授权。应用核验当前任务/修订的确认作业、精确 Worker 代码、源码实际字节、镜像、清单 SHA/生产者/用途及公开需求格式；排队输入、权限撤销、角色输入交集、原 CPU/内存/时限和断网/非 root 不变。模型回执省略重复项目明细，完整 API/锁/动作记录保留，可按 artifact_id 读回。

`smoke_public_source.py --source pypi --discover-build` 的实际 docopt 静态根是 setuptools>=40.8.0，既有解析器实际选 setuptools84.0.0；标准动态 hook 返回空附加需求，最后只安装 docopt 并两次真实调用。24 项工具及 30 项独立核对通过。`smoke_source_build.py --source pypi --discover-build` 的 in-tree 夹具从实际 TOML 得到 setuptools==80.9.0，从真实 hook 得到 wheel==0.45.1，独立审批解析/获取后 tar/zip 均构建成功，最终仅运行 wheel 安装，两次 answer()=42 与 JSON 读回正确。保留 12 类归档拒绝、静态/动态依赖缺失与实际缺 gcc，19 项工具及 20 项独立核对通过。两命令使用 config/app.wsl.example.yaml；本轮无模型或科学验收。

冻结旧版本模板只用于精确核验已登记源码 wheel，新的 Jobs 总是用现行共享 Worker；旧实际产品字节/代码核对通过。完整回归 346 项通过。当前没有自动跨未构建源码版本回退，也未完成互不兼容的多项目构建工具链、编译器/系统库、其他运行时/缺哈希元数据回退或模型自主完整源码流程；原要求保留。搜索继续暂缓；下文 1.20 及更早为历史。

## 1.20：自动源码候选与用途明确的解析锁

resolve_dependencies 使用原 requirements、target_artifact_id、source 和可选 built_wheel_artifact_ids。依赖元数据读取仍采用原精确审批，原 64 请求/32 项目/32 MiB/100 解析轮/180 秒与目标/授权检查保持。官方索引里的 .tar.gz/.zip 须匹配项目、版本、Requires-Python、未撤回状态、SHA 和文件主机/路径；兼容 wheel 在同版本优先。若原生解析选中源码，返回 wheels=[]、source_candidates（name/version/filename）、source_candidate_sha256 和 resolution_complete=false；此时没有下载或解包源码，也没有执行后端。

python_source_candidate 锁将该候选绑定到不可变实际索引、目标/镜像、原需求、extra 和 SHA，不能当作完整运行依赖闭包。将 source_candidates 作为 source_packages，与同一 resolution_lock_artifact_id 传给 acquire_dependencies；源码下载须另行精确审批，官方发行 SHA 必须同时匹配候选锁，不能混入 wheel 或换选文件。python_wheel_resolution 锁继续只授权完整远程 wheel 子集，不能授权源码。当前工具参数/能力、动作记录和确认来源机制复用，不另建流程调度器。H1/H2 已确认候选事实也明确标记闭包未完成，不证明安装或当前可复用性。

取得 source_artifact_ids 后，沿用下文 build_only 和实际元数据流程；构建依赖目前仍须提供已核验 wheel。构建生成的项目名与 PEP440 版本必须匹配当前已确认源码的获取身份（等价版本如 1.17/1.17.0 可通过）。随后以原运行 requirements 和生成 built_wheel_artifact_ids 重新解析，只有完整 wheel 解析锁才表示该轮依赖闭包完成；有远程项另批下载，否则直接用已有 wheel 完整安装。当前候选选择会在构建前停止，尚未自动跨未构建源码版本回溯处理后续冲突；需要显式收窄需求重新选择，不宣称完整源码自动修复。

真实验证：`scripts/smoke_public_source.py --config config/app.wsl.example.yaml --source pypi --source-candidate` 只给 docopt==0.6.2 根需求，不指定源码文件名，实际索引选出 docopt-0.6.2.tar.gz。五次独立审批、六个终态 Jobs、一次成功构建、独立最终安装及两次实际函数调用/JSON 读回，21 项工具检查和 25 项独立来源检查通过。固定 Python3.12.15/pip25.0.1 的实际日志显示 PEP517 构建，即使该版本源码为 setup.py 项目。脚本仍预列 setuptools/wheel 构建工具，未证明构建依赖自动发现。328 项最终完整回归通过；本轮无模型/UI 或科学验收，1.19 模型试次原失败保留。静态/动态构建依赖发现与审批获取、编译器/系统库、其他运行时/缺哈希元数据回退及完整阶段门槛保持。搜索继续用户暂缓；下文 1.19 及更早为历史。

## 1.19：构建wheel复用与实际元数据运行依赖解析

需要先取得构建产物或避免缺运行依赖时重复构建，可以沿用prepare_environment的源码/wheel参数并传build_only=true。必须有源码，仍需environment.prepare和code.execute，源码后端只在原断网受限Jobs执行。wait_job成功返回prepared_wheels.wheel_artifact_ids、built_wheels里的metadata_artifact_id及lock_artifact_id；它没有prepared_environment，不能直接给code_execute。source-build-lock.json和独立.metadata记录实际生产者、源码/生成wheel/元数据SHA、pip原生隔离与runtime_installation=false。元数据只在容器读取wheel，宿主只核验有界纯文本/字节。

把根PEP508需求、实际目标target_artifact_id和上述built_wheel_artifact_ids交resolve_dependencies；私密任务仍需精确元数据审批，批准后保留全部相同参数重调。现有resolvelib从实际生成Requires-Dist推导依赖、extra和marker。返回wheels只包含需下载的远程项，返回built_wheel_artifact_ids包含选中的既有构建产物；解析锁绑定两组及Core Metadata SHA和真实目标镜像。非空wheels/解析锁交acquire_dependencies另行精确审批，下载成功后合并远程wheel_artifact_ids与既有构建IDs交prepare_environment，不再传源码或build_only。无远程项可直接安装已有wheel IDs。构建工具无需进入最终环境，实际pip仍核验完整依赖和哈希，不自动跳过缺项。

生成wheel只可由当前任务/修订确认的真实构建作业供给；在解析、下载锁检查及排队安装时重新核验源配方/实际产物/SHA。Jobs保存source_build_only用途，观察只返回prepared_wheels；即使源码后端额外写出environment.zip，也不能被该作业授权为已安装环境。原生循环、权限交集、固定镜像、只读输入、断网/非root及原资源/请求预算保持。

`scripts/smoke_public_source.py --config config/app.wsl.example.yaml --source pypi --build-only`的最终试次只为构建工具显式列根需求。源码构建一次后，以sampleproject==4.0.0为唯一运行根，由实际元数据推导peppercorn；保留缺构建/运行依赖两个真实失败，补齐后复用同wheel/同镜像并计算两次。16项工具及21项独立来源检查通过，完整回归313项通过；无模型/科学验收。自动公开源码候选及静态/动态构建依赖获取回退、编译器/系统库支持尚待实现。最新只构建/安装本地模型UI大模型通过/独立18项通过，小模型因第二次计算猜测答案且缺JSON/报告而失败；34份实际输入审计不替代质量。完整其余门槛见STATUS；搜索继续用户暂缓。下文1.18及更早为历史。

## 1.18：精确公开源码获取

在既有acquire_dependencies中使用source_packages，例如`[{"name":"sampleproject","version":"4.0.0","filename":"sampleproject-4.0.0.tar.gz"}]`；源码单独请求时可省略wheels。wheels只接受.whl，source_packages只接受.tar.gz/.zip；每次所有文件总计1–32项/128 MiB，包名与文件名不得重复。私密任务核对完整精确载荷后审批，用相同参数和此approval_id重调；获取核对官方JSON里的sdist类型、名字/版本、未撤回状态、文件名、大小及SHA，下载只保存不可变字节。[PyPI发行JSON](https://docs.pypi.org/api/json/)、[源码发行格式](https://packaging.python.org/en/latest/specifications/source-distribution-format/)

返回的source_artifact_ids传给prepare_environment同名参数；本地授权文件仍用source_refs，两者兼容。同一受限Jobs在构建前核对本任务当前修订已确认获取动作/产物/字节，源码另需code.execute；确认的源码ID不扩大子角色产物范围。构建/运行wheel仍用wheel_refs或wheel_artifact_ids。wheel解析锁仅绑定完整wheel集合，不能拿它授权源码或复用下载审批；源码的构建与运行依赖须按真实后端/安装日志和相应需求取得。最终安装保留完整哈希/依赖检查。

确认依赖摘要分别保留真实wheel/source产物ID，重复下载相同字节不能因新ID成为进展。通用提示现区分文档write_file与任务要求的计算作业产物：后者由代码写/output，随后真实读回，不能在容器外重造。CLI schema命令新增共享PackageRequest导出，保留WheelRequest兼容。

`scripts/smoke_public_source.py --config config/app.wsl.example.yaml --source pypi`不调用模型：精确下载sampleproject4.0.0源码，保留setuptools缺失失败，探测实际容器并解析setuptools80.9.0/wheel0.45.1/peppercorn0.6的完整wheel集合，独立审批获取，再保留缺peppercorn的实际安装失败；补齐后构建一次并复用两次。最终6作业终态、12项工具检查与14项独立来源检查通过。两次计算实际使用sample.simple.add_one(41)，版本4.0.0/peppercorn0.6、非root、网络阻断及产物读回均核对。来源归档仅在受限容器解包/执行，宿主包不变。前两试次在汇总时取错配方/生成wheel的字段而失败，原记录保留，不能改成模型成功。

300项完整回归通过。源码UI v2仍使用已授权本地夹具：大模型完整通过及15项独立来源核验通过，小模型JSON生产者/读回已正确但报告遗漏构建job_id、产物UUID被标为SHA，完整失败；21份输入审计不是质量验收。自动源码候选/构建及运行依赖回退、编译器/系统库、其他运行时或完整阶段验收仍待开发与实测。公开源码工具通过不代表Agent自主源码下载通过。联网搜索继续暂缓。以下1.17及更早为历史。

## 1.17：授权本地源码构建

prepare_environment保留wheel_refs（授权文件）和wheel_artifact_ids（当前修订确认获取的wheel产物），新增可选source_refs（单独授权的.tar.gz/.zip文件）。所有输入合计1–32个/128 MiB。源码执行额外需code.execute；同一动作及作业沿用原角色交集、只读输入、控制/恢复/撤销保护，不增加能力或执行循环。

容器核对源码/输入wheel的大小与SHA，再有界解包；累计最多30,000项/512 MiB文件内容，tar解压流连同元数据/填充累计640 MiB。拒绝越界/绝对/反斜杠/NUL路径、规范化重复项、链接/特殊/加密文件及多项目歧义，gzip CRC/截断由标准库核验。不会在Windows宿主解包或执行包代码。仅此类作业使用有界1 GiB /build执行tmpfs；/tmp仍noexec，根只读、网络none、非root、CPU/内存/时限不变。

pip wheel使用原生PEP517隔离、--no-index/--find-links，从所给已核验wheel取得静态及动态构建依赖。缺后端/动态依赖/编译器时实际失败，保留日志供有界修复。--no-deps仅用于把一个源码转换为wheel，最终安装保持--only-binary :all: --require-hashes及完整依赖解析，不用它绕过依赖。所有提供的构建/运行wheel也安装到最终环境，并如实记录；未另建依赖解析器。[pip构建系统](https://pip.pypa.io/en/stable/reference/build-system/)、[pip wheel](https://pip.pypa.io/en/stable/cli/pip_wheel/)

生成wheel作为实际产物封存；environment-lock.json/环境内相同锁记录sources、原wheels、built_wheels（来源源码ID/SHA、文件名/SHA/大小）、pip版本/原生隔离和安装版本。环境解析须匹配真实准备作业/当前任务修订/实际镜像及生成wheel产物；wait_job成功的prepared_environment.artifact_id用于原code_execute两次或后续复用。模型回执省略重复配方，完整API/日志/锁保持。

`scripts/smoke_source_build.py --config config/app.wsl.example.yaml --source tuna`验证真实作业：14项通过，含12类归档故障、缺静态/动态依赖后补齐、tar/zip纯Python构建及同归档两次执行/读回；独立10项来源核验通过。固定镜像Python3.12.15/pip25.0.1缺gcc，原生扩展实际失败仍未解决。设置RUN_SOURCE_BUILD_UI=1及FORESTRY_SOURCE_FIXTURE_LOCK运行frontend/e2e/source-build.spec.ts，模型工作/报告单独验收，结果见STATUS。此增量没有公共sdist获取/自动源码回退、编译器/系统库镜像配方、其他运行时或完整阶段验收；这些原要求继续保留。联网搜索暂缓。

下文1.16及更早的阶段计数、wheel限定说明均为历史，不能当作当前源码或自动解析能力限制。

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

1.16（历史）：仅依赖Simple JSON索引采用有界gzip，压缩传输及解压累计均受原32 MiB预算限制，原时间/审批/来源/权限保护不变；wheel及非索引读取仍identity，不开发联网搜索。271项最终完整回归和真实解析/下载/断网安装/两次复用15项通过。v7大模型完整UI与独立来源审计通过；小模型解析/下载成功后重复已完成阶段而暂停。37份输入补充审计通过，原审计误报和模型失败保留。完整通用阶段及源码构建/元数据回退/恢复/角色/评估等缺口仍未完成，详见STATUS。


1.17当前源码UI：大模型8请求/11动作完成1构建/2复用、锁与JSON/报告读回，13项独立来源检查通过；小模型13请求/12动作虽实际算出正确数值，却用write_file另存JSON、漏读JSON且报告缺构建job_id，完整试次失败。两模型21份输入审计通过不代替质量。282项最终回归/源码工具14项/工具来源10项通过，完整通用阶段及公开源码获取/自动回退、编译器/系统库等剩余要求保留，详见STATUS。搜索暂缓；前述1.16及更早为历史。
