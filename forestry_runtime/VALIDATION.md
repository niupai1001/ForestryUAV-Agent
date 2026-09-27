# 当前验证记录

> 倒序排列，最新在前。历史版本、旧部署和已淘汰架构的记录见 `VALIDATION_ARCHIVE.md`；
> 它们不回填当前评价基线。

## 2026-09-25：输入引用统一、证据化计划、产物交付、保留任务集

按 `data/diagnostics/oam02_harness_review/DIAGNOSIS.md` §7/§8 的优先级实施四阶段。每阶段都按
通用契约验收，没有为 `oam-02.tif` 增加任何专门规则。

### 阶段 1 — 统一输入引用与执行环境

- **一个引用形状**：`{scope, path, asset_id, source_id}`，任何消费文件的工具都接受。结果里回传
  解析后的 `exact_reference`，下一步直接照抄，不再在路径与 asset id 之间猜转换。此前
  `fs_list` 能列出 workspace 文件而 `inspect_raster` 只收 asset id，同一个文件两套名称且没有
  任何工具说明哪种转换合法。
- 域工具（`inspect_file` / `inspect_raster` / `inspect_raster_region` / `preview_image` /
  `calculate_ndvi` / `segment_canopy` / zip / CHM / PROSAIL 反演）全部改走
  `runtime/capabilities/inputs.py` 的同一解析器。旧写法（`asset_id`、`dsm_asset_id` 等）继续可用。
- **授权目录中的文件**由 Host Bridge 新增的 `/fs/stage` 复制进 workspace 后交给本地读取器，
  授权判定仍只发生在 bridge 一处；暂存副本按内容寻址，未变更时不重复复制，源目录保持只读。
- **安装作业的 512 MiB `/tmp` tmpfs 已移除**。它正是真实安装作业耗尽的那块盘（宿主机当时还有
  约 54 GiB 空闲）。现在两类作业共用 workspace 内的磁盘目录 `.runtime/scratch`，安装作业把它
  同时挂到 `/tmp` 与 `/scratch`，`TMPDIR`/`PIP_CACHE_DIR`/`HOME` 都指向它；代码作业保留
  `/tmp` tmpfs 但容量由 `AGENT_JOB_TMP_MB` 声明，并另挂 `/scratch` 供大中间产物使用。
- **依赖检查反映真实作业环境**：`job_run` 此前用配置的基础镜像探测，而代码作业用安装后 commit
  的镜像 —— 两者在第一次安装后就不一致，检查会报"模块不存在"而代码实际能导入。现在探测和运行
  使用同一个镜像，并分别报告 `job_image`/`base_image`/`image_source`。
- **失败带真实容量证据**：安装脚本在失败时打印 `RUNTIME_JOB_FAILURE:` 结构化 JSON（errno、出错
  路径、`statvfs`、tmpdir 与 pip 缓存位置），成功时打印资源报告；Runtime 另从日志解析
  `[Errno 28]`。`install_failed` 因此细分为 `install_failed_no_space`，并带
  `resource.exhausted_path` 与该文件系统的总容量/空闲量，而不是笼统的"安装失败"。
- 作业启动前按 `AGENT_JOB_MIN_FREE_MB`（默认 512 MiB）检查 scratch 所在文件系统的余量，
  不足时报 `disk_space_low` 并给出三个数字，不再让作业下载到一半再失败。

### 阶段 2 — 计划必须引用证据

- 新增 `runtime/plan.py` 与 `work_plan` 工具：交付目标、已观察条件、候选方法及其前提、尚缺证据、
  验收方式，可修订且保留全部历史版本。计划会作为一段有界文本进入每个模型请求。
- **观察台账**：每次返回的工具调用都会分配一个由调用本身派生的 `obs_...` 编号，附在结果上；
  计划中的每条条件与候选都必须引用它，或者引用某个工具真正返回过正文的文档
  （`guide://...` / `knowledge://...`）。**指南目录不构成依据** —— 只看过目录就写
  "根据指南"会被拒绝，并返回已读正文的清单；这正是原 Run 反复声称依据却从未打开正文的那处失效。
- 改变所选方法必须给出 `reason`，否则拒绝。区分"候选"与"已选"，使"为什么换方法"由两次修订的
  差异回答，而不是由叙述回答。
- 指南目录文本本身写明"这是清单不是正文"，并指明应引用 `domain_guide` 返回的 `citation`。

### 阶段 3 — 按产物验收并在界面交付

- 新增 `runtime/capabilities/artifacts/delivery.py`：每个产物给出**后端生成的** `preview_url`
  （`/files/{chat}/{asset}?inline=1`）与 `download_url`（`?download=1`），并分开报告四个此前被
  合成一个"完成"的检查：进程成功、文件存在、服务端可读、浏览器可显示。
  `answers_task` 明确为 `null` 并附原因：阈值分割成功不等于树冠语义成立。
- `/files/{chat}/{asset}` 现在按资产自身的 media type 与 `inline`/`download` 返回。此前它对所有
  请求回 `application/octet-stream` + attachment，所以已注册的 PNG 到浏览器就是下载，图片"存在"
  但看不到。
- GeoTIFF 产物附带 `product_qa`：波段、dtype、描述、网格、CRS、像元尺寸、NoData、降采样有效比例
  与各波段统计，全部实测，取不到就不写。
- 新增 `inspect_raster_region`：对一个受控窗口（或整幅降采样）生成单波段缩略图，并返回**同一批
  像元**的有效数、极值、均值与分位数。图片与数字同源，所以模型核对的是同一份观察，而不是两次
  独立缩放的结果。窗口越界与波段不存在分别报 `raster_window_out_of_bounds` /
  `raster_band_missing`。
- 多模态通路已接上：工具可以声明 `attach_image`，harness 在 `MODEL_VISION_ENABLED=true` 时把图片
  作为工具返回内容的一部分发给模型（上限 2 MiB）。默认关闭，因为默认模型是文本模型，不能把
  它一定会拒绝的图像部件塞进去；关闭时缩略图仍是可查看的资产。
- 前端 `ConversationPane` 直接渲染 delivery：图片内联显示，非图片只给下载；四个检查分别展示，
  `answers_task` 显示"未验证"；单张图加载失败只影响该卡片。

### 阶段 4 — 保留任务集与三轴对照

- 新增 `evaluation/heldout/`：9 个未参与开发的任务，覆盖 RGB、多光谱、未注明波段、缺失波段、
  缺失文件、环境缺依赖、草地与树冠混淆、三种产物类型、非遥感表格任务。
- `make_fixtures.py` 确定性生成全部输入，`gold/` 由同一批数组算出；测试从磁盘重算 gold
  （NDVI 均值、树冠像元与比例、地区合计、栅格尺寸与波段描述），输入与期望值不一致会直接失败。
  草地 fixture 刻意让草地与树冠的绿通道相同、裸土更亮，使"绿"和"亮"都不是正确答案。
- `compare.py` 打印 `arm × case × repeat` 矩阵、聚合已采集记录、并在**恰好一条轴不同**时才给出
  归因；多条轴不同时明确写"不可归因"。`unknown` / `not_applicable` 退出分母，没有记录的单元记为
  缺口而不是 0 分。成功、耗时与 token 成本在同一行报告。

### 顺带修掉的两处既有问题

- **全局契约被截断**：`68fa6b5` 重写 `SYSTEM` 时丢掉了执行契约，`tests/test_project_instructions.py`
  的 8 个子测试与 1 个用例在 HEAD 上就是失败的。已恢复为一段简短、与领域无关的工作契约，包含
  "工具调用已返回／后台作业已完成／用户目标已完成"的区分、`job_wait`、`environment_check`、
  `blocked_by`、`resource_busy` 与"目录授权来自用户直接请求，不是授权"。
- **模型可见契约的冻结值按设计更新**：`test_kernel` 的 schema 摘要与 `test_capability_layout` 的
  工具清单随 `artifacts_inspect`/`artifacts_preview` 的引用形状、`code_run`/`environment_check`
  的描述和新增 `work_plan` 而更新；两处注释写明了每次变更的原因。

### 门禁结果

执行环境：Windows，Python 3.14。本机 `python -m pytest` 需要 `NUMBA_CACHE_DIR` 指向一个可写目录，
否则 `prosail/FourSAIL.py` 的 `numba.jit(cache=True)` 会在导入期报
`cannot cache function 'volscatt': no locator available` —— 这是本机环境问题，与本次改动无关，
但会让整个测试收集失败，故记在此处。

- `python -m compileall -q runtime host_bridge tests evaluation shared`：通过。
- `python -m pytest -q tests`：`663 passed, 1 skipped, 666 subtests passed`，耗时 88 秒。
  改动前的 HEAD 实测为 `582 passed, 9 failed, 1 skipped`：那 9 个失败全部来自被截断的 `SYSTEM`
  （上一节），现已修复；本轮新增 5 个测试文件与 1 个 `test_host_bridge` 用例。
- `python evaluation/scorecard.py`：`qualification=incomplete`，退出码 2（预期）。
- `npm run build`（frontend）：类型检查（3 个 tsconfig）+ Vite 生产构建通过。
- `python -m evaluation.heldout.compare --plan --arm ...`：产出 54 个单元（2 arm × 9 case × 3 repeat）。

### 本轮没有执行的部分

- 没有可用的模型端点，Docker 也未运行，因此**没有产生任何 `real_model` 记录**：
  `evaluation/heldout/suite.json` 的 `status` 保持 `declared_not_yet_collected`，阶段 4 的三轴对照
  只完成了矩阵规划与离线聚合验证，未产生任何 arm 的结果。不得据此声称任何方法或模型更好。
- `npm run test:ui` 无法在本机启动：Playwright 的 Chromium 未下载。用系统 Chrome 跑同一组未修改的
  规格得到 `2 passed`，这不等价于 `npm run test:ui` 通过。
- `MODEL_VISION_ENABLED` 默认关闭，因此"模型看到缩略图"这条路径只有单元测试覆盖
  （`_image_attachment` 在开关打开时返回 `BinaryContent`），未在真实多模态模型上验证。

## 2026-09-21：四条工程门禁全部接入，林业验证器扩到三题

- `gate.sandbox` 与 `gate.recovery` 是此前缺失的两道门禁，接入后 `gates` 由 `unknown` 变为 `pass`，`engineering.gates` 得到 `score=100.0`、`evidence_coverage=1.0`、4/4 通过。
- `gate.sandbox` 启动一个真实容器并读取**生效的**隔离配置，而不是相信请求的 flag：`NetworkMode=none`、`ReadonlyRootfs=true`、`CapDrop=ALL`（容器内 `CapEff=0`）、`SecurityOpt=no-new-privileges`、`PidsLimit=256`、`Memory=6442450944`、`NanoCpus=4000000000`；容器内无法连外网、无法写根文件系统、可写挂载正常，且 `HOST_BRIDGE_KEY`/`RUNTIME_API_KEY`/`UI_SESSION_KEY`/`OLLAMA_URL` **均未进入作业环境**。Docker 不可用时该门禁报 `unknown`，不报 `pass`。
- `gate.recovery` 驱动真实的 Coordinator/RunStore 路径并断言"上报状态必须来自观察到的执行事实"：取消无法确认时保持 `canceling` 且带 `observation_error` 证据、无 checkpoint 的重启不重放作业并落到 `paused`/`waiting`、四个终态全部拒绝出边。
- 林业验证器增至三题：`forestry.ndvi`（逐像元重算 + 网格/掩码继承 + 上报统计与实测比对）、`forestry.chm`（`DSM - DTM` 逐像元比真值 + 负高程差不得截断 + 缺基准时必须报缺口而非编造栅格）、`forestry.product_qa`（地理坐标系像元单位不得写成米 + 无元数据时不得声明波段角色 + 不得由"文件可读"推断精度）。
- 三题 fixture 均可手算复核：NDVI `[[0.5,0.0],[NaN,1.0]]`；CHM `[[NaN,3,4,2],[3.5,5,6,3],[2,4,-3,2.5],[1,2,2.5,1.5]]`（15 有效、1 负值、max 6.0）；product_qa 为 EPSG:4326、4 波段无描述、18 有效像元。
- 路径约束不变量收敛为单一来源 `shared/paths.py`（Runtime 与 Host Bridge 共用，两侧不得互相 import）。此前 **11 处**调用点用三种写法重复实现同一判断；收敛后一个测试即可覆盖全部调用路径。写测试时发现并修掉一个潜在陷阱：`root in child.parents` 是字典序比较，未解析的 `session/../secrets` 会被误判为"在内"。现有调用方都会先 `resolve()`，因此当时不可利用，但断言已改为显式拒绝含 `..` 的路径。
- 评价端的 `_save_verdict`（4 份副本）收敛到 `verify/base.py`。
- 评价覆盖度：**16 / 43 个 check**；agent 题 4/12，门禁 4/4，浏览器 2/2；有真实记录的槽位 4/46（四个门禁）。

### 更正一条此前的判断

早先的审计记录称 `spectral_RMSE` 是"归一化空间残差、量纲错误"。复核实现后确认**该判断有误**：KD-tree 的 `scale` 只用于邻居查询与距离权重，RMSE 由 `observations - modelled` 在真实反射率空间计算（`runtime/capabilities/prosail/service.py:345-347`）。`PROSAIL_WORKFLOW.md` 将其单位标为 `reflectance_fraction` 是准确的，无需修改。

## 2026-09-20：目标架构阶段 0–6

- 安全与恢复边界已收紧：文件下载使用独立 UI session 身份，API 不再接受隐式默认用户；UAV XMP 读取有界；Run 数据库启用 WAL；取消无法确认时保持 `cancel_incomplete`/非伪终态。
- 工具内核已统一为 `ToolSpec` 注册表，参数 schema、权限、side effect、等价能力与 trace 元数据由同一声明派生。通用工具和遥感能力分别迁入 `runtime/capabilities/<name>/`，旧 `runtime/tools.py` 与单体遥感模块已删除。
- Session 状态机集中在 `runtime/session/state.py`；Run 协调和恢复拆分到 `runtime/session/`；FastAPI 入口与路由拆分到 `runtime/api/`。连续 Turn 不会先把 Run 写成终态再非法重开。
- 评价端已实现 API/工程证据采集、统一 trace、CSV 逐业务键验证、产物 Action 关联、结构化回答验证和 records 证据路径重定位。评价模块有独立测试保证不导入 `runtime`，gold 不由 API collector 读取。
- 首个真实 engineering 证据包位于忽略提交的 `evaluation/work/architecture-20260920/`。`gate.idempotency` 故障注入 2/2 通过；持久化探针中 Input、Turn、Action、Attempt、Job 各 1 条，重复、孤儿关联、多 Attempt 和失败预留残留均为 0，verifier 判定 `pass`。
- `core.csv` fixture、gold、collector 和三个 verifier 已就绪，但本机 `127.0.0.1:8010` 未监听且当前进程没有 Runtime API 凭据，因此没有执行或伪造 `core.csv × 3` 的 `real_model` 记录。恢复部署后按 `evaluation/README.md` 运行。
- React 已拆出 API client、`useRunStream`、事件投影、对话和行动流组件。界面不再固定声称模型为 Qwen，不再把缺少状态的 `done` 事件推断成 completed，工具结果优先使用服务端 `outcome_ok`。
- Playwright 1.55.0 + Chromium 140 已真实执行受控 UI repeat 1：`ui.send` 在延迟确认前绘制待提交气泡并在 503 后恢复输入；`ui.reconnect` 分页合并 10,005 个事件，刷新前后均恢复 completed。两项通过，四个 check 均由 verifier 判定 `pass`；其余 repeat 2/3 未运行，UI 轨道仍不完整。
- `Attempt` 层保留：生产 Action 路径会真实写入、开始和结算 Attempt，且 `gate.idempotency` 已直接核对。旧 `agent_messages_json` 新建列、未调用 transcript、旧输入消费方法、Open WebUI 遗留脚本及 TypeScript 生成物已移除。PROSAIL 工作流已按现行三个工具和证据必填契约重写。

## 当前门禁结果

执行环境：Windows，Python 3.14；前端使用仓库锁定的 Node 依赖。

- `python -m compileall -q runtime host_bridge tests evaluation shared`：通过。
- `python -m pytest -q`：`180 passed, 13 warnings, 91 subtests passed`，耗时 83.85 秒。
- `python -m evaluation.run_baseline --root evaluation/work/baseline --tracks engineering`：`gates=pass`，`engineering.gates=100.0`，覆盖率 1.0；整体 `qualification=incomplete`（其余槽位未测）。
- `python evaluation/scorecard.py`：`qualification=incomplete`，预期退出码 2。
- `npm run build`：TypeScript 应用、Vite 与 E2E 三配置 `noEmit` 检查及 Vite 生产构建通过。
- `npm run test:ui`：真实 Chromium `2 passed`；评价证据包保存在忽略提交的 `evaluation/work/architecture-20260920/ui-browser-repeat1/`。
