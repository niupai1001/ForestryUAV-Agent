# V2 基线验证登记

## 实际旧库复制兼容审计（20261026）

先只读核对现存记录：20261021 imagery-prosail备份为实际43会话、v1～v4、state约41MB/native约49MB；WAL文件0字节，无非空journal。选该冻结来源，不选较早40会话版本混算。research/audit_legacy_migration_once.py唯一workspace/legacy-migration-audit-20261026，只复制这两个完整独立库到新目录；源文件immutable只读/hash前后校验，不直接迁移源、不改locator或用户记录。

触发为首次当前V2旧数据阶段验收：每个旧表按全部原列/原值排序计算逐行摘要，迁移前后要求原逻辑记录完全一致（schema_versions仅追加5/6）；再次Store初始化不得增改逻辑记录。逐实际task读取/Jobs投影，删除标记按原限制保留；按当前公开SqliteStepStore.list_runs/latest_snapshot读取全部原run的最新检查点，不执行工具/模型/Job。检查点可解析不等于可以重放副作用，未结算调用只记录；所有native原表/源文件再次摘要一致。FK历史问题若有如实记录，不自动修补。科学大栅格/外部输入和产物下载不重验，克隆没有重写绝对locator，不冒充已完成异地搬家。

命令.venv/Scripts/python.exe research/audit_legacy_migration_once.py，独占docs/rebuild/legacy-migration-audit-20261026.log，asyncio采用已知可用授权环境；0真实模型/执行器/科学重算。停止条件为实际全部旧行保留、重入幂等、旧run可读或明确失败证据、源字节未变；任何失败不删除克隆重建，按原位置继续分析，不修原备份来凑通过。

## 迁移原子性、重入与回退保留（20261026）

触发：执行计划第6项尚缺旧库迁移/回退证据，审计Store发现executescript先隐式提交外层空事务，DDL与后续版本/数据迁移未处于同一事务。最小修复是在脚本PRAGMA WAL后显式BEGIN IMMEDIATE，将建表和数据迁移共同受原transaction提交/回滚；已知更高schema版本>6提前拒绝，不尝试降级。无新迁移框架/表/数据重写。

tests/test_store_migration.py新增受控版本5登记前中断（新库/模拟旧v4库）→所有新增DDL及版本回滚→重入成功且旧任务字节语义不变；更高版本拒绝且DB文件字节不变；发布前备份＋发布后新消息/产物归档→旧备份拒绝覆盖活数据→隔离目录保留后恢复旧版，新增内容仍在独立保留与备份中。该回退保留不宣称新增消息已并回旧运行库，活动任务仍需适配，不删除任何用户数据。复用已有backup路径/篡改完整性证据，只有受影响的backup roundtrip定向复核。

命令.venv/Scripts/python.exe -m pytest -q tests/test_store_migration.py tests/test_backup.py::test_roundtrip_preserves_wal_state_checkpoint_history_artifact_and_session -p no:cacheprovider --basetemp=workspace/store-migration-20261026-tmp --junitxml=docs/rebuild/store-migration-20261026.xml，同名独占log。纯SQLite/文件夹具0模型0Job，无生产库改动。停止条件为失败原子回滚、重入不丢旧数据、未来版本不改写、新增数据不会被旧快照覆盖。通过即冻结，不重跑Job/模型/科学结果。实际旧备份复制兼容审计另行登记，不把模拟v4当实际43会话证据。

## 实际执行器重启与取消接缝预登记（20261026）

结果：executor-seam-20261026首次完整通过，实际句柄24125 exit0。Docker29.1.3/已有固定镜像，重建前后同Job c948ff3a-d70c-4f61-8128-d0adf6c4cbec及容器ID一致、唯一37字节封存产物；取消Job 04e14c79-67a3-4c7b-a0eb-cfa4c49bfcaa请求时running、真实退出后cancelled、后续文件未生成。总Job2/动作2/模型0/科学0。两cleanup.done=true且实际inspect容器不再存在，日志先归档、产物保留。完整身份/哈希/界限见EXECUTOR-SEAM-20261026及source凭证；没有安装/拉取/重启共享服务。达到停止条件，不重复这两作业或科学成果。

触发：WP3软件/组件接缝通过，而实际执行器跨应用重建、取消确认、归档未在当前版本验收；复用旧科学/镜像证据，不重算科学或下载镜像。research/executor_seam_once.py唯一目录workspace/executor-seam-20261026，先只读Docker version与固定code_image inspect；缺失即记录停止，不安装/拉取/重启共享Docker。隔离工作区与唯一应用writer，无真实模型、科学Job或用户输入数据。

最多2个真实常量代码Job（各120秒、观察≤90秒、累计动作≤4）：短任务在确认容器running后关闭应用，不停容器；重建应用读取原Job ID/容器身份并封存唯一文字产物。第二Job睡眠60秒、请求取消时必须仍running，只有真实执行器确认退出后cancelled，后续文件不生成。原账本计数只能2，模型0；清理先保存日志/材料再删除本批归属容器，产物保留。任何失败不重建新Job掩盖；若已有Job仍live，用原ID继续观察/取消，不能仅因脚本退出视为已停止。镜像/服务非本批所有，不停止或卸载。

命令.venv/Scripts/python.exe research/executor_seam_once.py，同名独占executor-seam-20261026.log；目录存在拒绝执行，原代码/配置/提交/Job身份/终态/清理另存。脚本包含实际不变量assert作为自检，不另造一套镜像夹具测试。通过整个接缝则冻结，缺环境则只报告执行器未验证，继续不依赖它的工作；不归咎模型、不简化为假Docker成功。

## Job投递失败可见与用户处理（20261026）

结果：delivery-attention-20261026通过全部登记新状态/用户处理、原续接预算/输入优先/幂等与受影响硬退出Job接缝。Engine.run的原子running认领防止skip后晚派发，重启后的旧running writer已暂停、未消费回执needs_attention，同event_id可恢复；consumed事实不因报告失败倒退。未执行自动模型失败重试，首次自动派发失败后转需处理。新schema只导出JobDeliveryControl，不批量重写旧导出。

前端类型/打包通过，句柄71816终态，静态资产index-ChTFvHxF.js/index-DVBaniQc.css。仅新增Edge completion-attention流程通过（jobs-panel-20261026-delivery JSON、delivery-attention-20261026-delivery.png已视觉检查），呈现不消费、失败原因可见、跳过刷新后仍保留原结果链接；无文字重叠/截断。原取消UI无相关分支变化复用旧证据。组件8794服务自动回收，无活进程。未部署；新应用状态协议老版本不认识，正式回退需保留新增回执/事件与状态映射，不能直接旧库覆盖。

触发：已实现终态事务/自动续接，但scheduled/claimed失败后无专门可见状态，也缺已呈现/明确跳过；dispatch异常可冒出worker回调，消费与后续报告失败未区分。沿原job_deliveries/events扩展状态presented/skipped/needs_attention，无第二表/队列、无schema重建。根回合失败与派发失败同事务记失败详情；已消费事实保持consumed，后续分析失败另显示。失败后不自动重开模型预算或重建相同试次；可由正常新writer读原event或用户明确跳过。自动调度仍仅首次pending/presented，继续原活动预算。

原生接口新增JobDeliveryControl及/tasks/{task}/jobs/{job}/delivery，present不是模型消费；skip不改Job事实/科学验收，当前运行writer拒绝跳过，排队自动turn原子停止。Engine.run在单写会话下先原子认领turn/核对skip，防止已读取queued快照的旧调度再启动；启动独占对账把旧running turn暂停并将未完投递显式needs_attention。原event_id/已完成产物/调用账本保留，ack不把skip改回consumed。Jobs列表与详情投影来自原事实，不写进Job payload。

测试触发为上述状态/运行入口改变：新增tests/test_job_delivery_attention.py，受影响tests/test_job_continuation.py与test_job_delivery.py；原test_runtime_hardcrash的Job pending/claimed两接缝按新状态契约定向复核（claimed崩溃后明确needs_attention，原event/唯一Job/消费后不投递断言不放宽）。不重跑文件副作用/OS锁或原模型试次。命令.venv/Scripts/python.exe -m pytest -q tests/test_job_delivery_attention.py tests/test_job_continuation.py tests/test_job_delivery.py tests/test_runtime_hardcrash.py::test_hardcrash_keeps_terminal_delivery_identity_without_new_job -p no:cacheprovider --basetemp=workspace/delivery-attention-20261026-tmp --junitxml=docs/rebuild/delivery-attention-20261026.xml，同名独占log。隔离软件模型/受控进程0真实模型0Docker；停止条件为失败/呈现/跳过可见、无自动模型重试、同事件可恢复、消费与报告失败分离、原预算/用户优先/幂等保持。

前端增加回执状态/失败说明、标记查看和跳过，succeeded显示“执行完成”而不暗示科学验收。新受影响类型/打包、浏览器组件失败展示→跳过→刷新流程另定向验证，不把构建当实际执行器验收；旧取消流程若未受影响复用已有证据。

## 后台作业日常操作入口（20261026）

本批收口：job-controls原生API与原执行器确认负例通过；schema仅新增JobCancel.schema.json，没有批量重写旧导出。TypeScript通过，授权vite打包通过，当前静态资产index-DNWy3Kde.js/index-DVBaniQc.css；未部署服务。组件最终api-fixed完整断言通过，实际截图已查看，来源/时间/命名/下载/日志排版无截断重叠；取消请求后仍显示运行中并等待确认，刷新保留意图，模拟执行器终态后才显示已取消。前三次夹具失败保留，未放宽断言；Playwright的8794服务已随终态回收，无活句柄，build48047终态。此为浏览器组件Mock API与后端真实路由分别验证，不冒充真实Docker/完整主入口E2E验收。默认static、原模型试次与科学结果未变。

module-fixed已进入真实组件并通过来源/结果/详情默认折叠，但取消子路径未命中原jobs** Mock glob，落到Vite代理并因8765未运行拒绝连接；原失败保留。修Mock为/tasks/task-fixture/**覆盖cancel/logs子路径，产品/API断言不改，仅该流程以api-fixed独立证据再验。前三次是不同夹具接缝，均无真实模型/科学Job，也未对生产服务执行取消。

Vite授权环境打包已终态通过（1849 modules，约3分8秒），未安装、未启动生产服务。浏览器base-fixed已进入夹具HTML，但绝对module URL被Vite预转换当作/ui目录物理文件而找不到；第二失败JSON/截图保留，产品组件仍未执行。修为相对module入口./jobs-panel.tsx，让Vite按HTML位置解析；第三次仅该原失败流程、独立module-fixed证据，未改产品断言或重跑后端。

定向API取消合同及原执行器确认/身份负例通过。npm run build的TypeScript阶段通过，Vite在普通沙箱读lucide路径EPERM；只在授权环境运行失败的vite build，未重跑类型阶段。组件浏览器首次jobs-panel-20261026失败于夹具URL漏/ui/，实际页面为Vite base提示页，原JSON/error-context保留，不是产品取消失败；修正夹具入口与module URL为/ui/e2e路径，使用独立FORESTRY_JOBS_EVIDENCE=20261026-base-fixed重验原同一组件流程，断言不变，原证据不覆盖。

触发：WP3接续核对发现系统文件/目录选择器已在attachments.pick_local与前端入口实现，不重建；JobsPanel虽有常驻入口但仅展示技术资源，缺来源用户消息/命名/结果链接/日志与单作业取消。新增JobCancel请求及/tasks/{task}/jobs/{job}/cancel，沿Jobs.request_cancel同事务核对归属/幂等回执/取消意图；不修改整个会话取消标志，不在确认执行器退出前伪造终态。原内部取消调用复用同一路径，已终态或已请求不重复事件。schema CLI同步导出JobCancel。

前端复用现有Job公共字段、消息/产物清单与logs API，呈现名称/状态/来源/提交时间/结果下载，非终态可取消；请求后显示等待执行器确认。技术日志/资源细节折叠，不加新队列/轮询模型。验证范围：tests/test_job_cancel_api.py原生API归属、请求ID冲突/重放、单Job与会话控制分离、四种终态不改写；原tests/test_jobs.py::test_cancel_not_confirmed_while_executor_still_running及test_unrelated_container_cannot_be_started_or_killed受影响复核。隔离Fake执行器0真实Docker/模型，命令.venv/Scripts/python.exe -m pytest -q tests/test_job_cancel_api.py tests/test_jobs.py::test_cancel_not_confirmed_while_executor_still_running tests/test_jobs.py::test_unrelated_container_cannot_be_started_or_killed -p no:cacheprovider --basetemp=workspace/job-controls-20261026-tmp --junitxml=docs/rebuild/job-controls-20261026.xml，同名独占log。

前端先npm run build确认类型/打包，浏览器独立夹具再核对可见来源/下载/取消确认与折叠日志；构建通过不能替代视觉与真实执行器验收。停止条件为新增用户操作真实接上权威API且未提前宣称取消/科学验收；无关上传、旧科学和模型证据复用，不全量重测。

## 隐藏能力指引与单源科学声明（20261026）

结果：discovery-guidance-20261026通过实际SDK指引/查询说明、下一步工具集、原生恢复/权限负例及实际注册descriptor声明；ruff通过。不跑真实模型，不能把此批软件通过改写两条真实筛查失败。新增metadata与科学说明属于新目录版本，原离线目录/向量/真实请求仍按旧hash冻结。

触发：真实native试次没有调用已可见search_tools，却因没看到记忆路径而宣称笔记未提供；审查发现应用将原生search描述覆盖成很短说明，未告知schema只是已加载子集。根提示还硬性要求无输入直接结束，并按影像关键词固定process_imagery，与V2按需发现/减少过程特例冲突；write_file与容器/output的边界在根提示不清晰。此次修复为统一发现指引（mode非static时加入根说明，同时用于原生search描述/用途参数）、无输入先说明必要材料且允许能力发现、移除固定影像路由、明确任务相对路径和容器路径、一般事实/参数/假设证据区分。

工具声明同源补齐：ToolSpec可声明英文摘要/输入输出/模态/权限层/版本，ToolRegistry从同一注册定义投影ToolDescriptor；process_imagery与read_user_memory填实际元数据，未声明保持unknown/默认，不造第二工具目录、不改变准入。科学适用说明回到process_imagery本身：本次LUT先验与历史未知项、边界不证明因果、camera归一化与独立定标区别；未改科学worker/参数或旧结果。当前描述目录属于新版本，旧v2检索/向量结果不冒充该版本成绩，不重算已冻结比较。

验证：tests/test_tool_descriptor_metadata.py核查实际注册→descriptor及未知字段；改tests/test_native_discovery_wire.py增加实际SDK首次请求的完整指引/查询参数说明断言，同时复用原下一step schema/权限冻结契约；运行tests/test_native_discovery.py受影响权限/恢复负例。命令.venv/Scripts/python.exe -m pytest -q tests/test_tool_descriptor_metadata.py tests/test_native_discovery_wire.py tests/test_native_discovery.py -p no:cacheprovider --basetemp=workspace/discovery-guidance-20261026-tmp --junitxml=docs/rebuild/discovery-guidance-20261026.xml，同名独占log。软件Mock模型0真实请求0科学/容器，无原失败试次重跑；停止条件为实际wire说明存在、仍下一step激活且不越权、元数据单源准确。此证据只证明指引/协议接入，不宣称真实4B已完成发现或科学解释。

## 真实任务暴露的报告交付边界（20261026）

结果：report-delivery-20261026通过未知/跨任务/合法内联Markdown产物链接、实际工具绝对路径→相对路径保存回执、原结构化输出恢复与3次上限。ruff最初发现研究脚本prepare遗留未用source赋值，实际执行字节先按protocol SHA归档后仅删赋值，后续ruff通过。没有改变真实试次结果、预算或协议，也没有发修复后追加模型请求。

触发：冻结的static真实任务读到输入/记忆但两次以/output绝对路径写报告均失败，误归因输入目录写权限；native_bm25真实任务未发现记忆、未写产物却在Markdown给伪artifact下载链接。原两试次保留，不追加真实模型请求。通用修复：write_file单源说明与绝对路径错误明确当前任务相对路径/输入只读与输出写入分离；根输出验证器对正文Markdown artifact链接与结构化artifact_ids同样核查当前会话已封存身份及字节，不允许借空artifact_ids绕过；失败走原生ModelRetry及原重试预算。

验证只运行新增tests/test_report_delivery.py：未知/他会话/真实链接，及实际原生工具绝对路径错误→合法相对路径→封存与真实链接；原tests/test_output_recovery.py为受影响输出重试接缝，定向复核，不改原输出次数/断言。命令.venv/Scripts/python.exe -m pytest -q tests/test_report_delivery.py tests/test_output_recovery.py -p no:cacheprovider --basetemp=workspace/report-delivery-20261026-tmp --junitxml=docs/rebuild/report-delivery-20261026.xml，同名独占log。软件模型夹具、零真实模型/科学/容器；停止条件为伪链接/跨会话被拒而真实产物可交付、原恢复预算保持。此次产品source变化发生在两条真实试次均已终态之后，不污染原对照身份，也不重跑其失败。

## 实际窗口与A发现任务筛查预登记（20261026）

结果：guard通过；finalize成功冻结协议。static句柄30625、native句柄60620均exit0仅表示研究脚本结束，**两任务语义均失败**：static6请求6动作、无产物；native3请求2动作、无发现/记忆/文件却伪造链接。两次实际窗口32768、相同别名digest、SDKmedium/0.2/4096，累计总9真实请求，零Job/科学重算；未用尽不追加。两after.models=[]，仅释放研究别名，不停Ollama。完整逐标准审阅/配置局限与原输出hash见DISCOVERY-TASK-SCREEN、discovery-task-results-20261026.json；默认static保持。该窗口证明研究别名，不证明原生产模型或27B；别名本机manifest缺失导致tokenizer仍回退，不编造精确绑定。科学解释/真实交付未收口。

prepare首次实际执行：API已成功创建研究别名、show声明num_ctx32768，但本地预期位置没有新alias manifest，prepare在读取此文件验证层时失败，原log保留。无推理/模型预算消耗，/api/ps仍空；没有重新创建别名。只读核对源/别名服务返回Modelfile的两个FROM blob路径相同，与源已有本机manifest的模型/投影digest吻合；模板/thinking相同。新增finalize仅继续已成功创建的别名元数据核验与协议冻结，不重放create；源digest再次核实。别名无本地manifest因此当前tokenizer绑定不可用，保持显式回退并纳入比较限制，不伪造manifest或把源digest冒充别名digest。实际窗口仍需在任务请求期间取证，不能据声明先称已生效。

触发：离线检索已完成而真实任务收益未验证，官方兼容API明确不能用OpenAI请求设置context size；当前模型未声明num_ctx，配置32768不能当实际窗口。按官方/api/create已有from＋parameters创建独立研究别名forestry-v2-4b-ctx32768-20261026:latest（源qwen3.5:4b，digest d8b0...38c04），只设置num_ctx32768，不修改原标签/服务环境、不下载/量化新权重。创建前确认无驻留模型/别名不存在，记录payload/result/show，比较本机model/projector层digest、模板和thinking元数据，别名身份另存。官方依据：https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size 、https://docs.ollama.com/api/create 、https://docs.ollama.com/api/ps 。原始文档是当前版本，安装版本通过实际API响应核对，不能仅以文档断言本机已支持。

新任务不是第三次计量校准：固定中文“既有PROSAIL成果＋保存的方法笔记→可信核查报告”完整目标，统计和方法均只从已审计catalog投影，源hash保存，不读/重算大栅格或LUT。任务需自主读取输入/记忆、解释、写报告并交付链接。A/static与A/native_bm25依次各单遍，H1、同4B共享权重/量化/上下文32768/medium思考/temperature0.2/output4096/输入/提示/权限/软件source hash冻结；仅发现模式不同。每模式12模型请求、30动作、900秒，总最多24请求/60动作/1800秒，真实科学Job与Docker执行0；动作权限不含code.execute。不同模式隔离工作区，不双写会话。单任务已见开发筛查，不是正式配对/留出/显著性或新数据科学能力验收。

运行脚本research/discovery_task_once.py，prepare只创建配置别名与协议、无推理；static/native_bm25各目录首次创建，存在即拒绝，不重试失败、不换简单目标。开始前冻结protocol/source/script/input/notes，模型预算按实际Engine累计；任务期间从/api/ps取实际context_length/digest，缺失标未验证，出现外部模型或窗口不符停止该比较。完成/失败后仅释放本次研究别名，保留所有会话/错误/请求信封/产物与原目标；不能关闭共享Ollama或卸载其他owner。

本批脚本guard验证触发为新增试次/资源保护逻辑，tests/test_discovery_task_once.py仅软件验证occupied不创建别名、已有试次不触发模型API；命令.venv/Scripts/python.exe -m pytest -q tests/test_discovery_task_once.py -p no:cacheprovider --basetemp=workspace/discovery-task-guard-20261026-tmp --junitxml=docs/rebuild/discovery-task-guard-20261026.xml，同名独占log。通过后prepare一次、依协议顺序static一次再native一次；各步骤自己的日志不覆盖。模型报告语义必须人工按预登记七项审阅，不以关键词/Job状态自动判科学成功。两种模式成功/失败皆保存，不能追加到变绿；默认产品static本轮不自动修改。

## 大批次压缩配对边界（20261026）

结果：compaction-batch原1失败/4通过保留；compaction-pair-fixed原失败与受影响普通/Retry分支、pin与单receipt新增边界通过。未放宽原“旧已结算前缀须实际裁剪”断言。Engine编译/目标/私密清单证据复用首次该项通过。原native普通尾裁剪路径保持；新配对修复不修改源messages、tool args/result内容或token窗口，不发额外模型请求。静态检查通过，达到本批停止条件；仍不代表任意历史损坏可自动修复或视觉/模板已精确计量。

首次compaction-batch-20261026为4通过1失败：普通大批次/Retry/完整丢弃/原生Engine接缝通过；已结算后重用同ID样本保留了不必要旧批次，未达到预先登记的可裁剪出口。原因是再次调用native cutoff会将旧call_id与新结果误关联，不能靠放宽断言掩盖。修复为仅在确实拆分时保留原候选survivors并扩回全历史闭包tail；依然使用原生公开reinject_pinned，receipt通过继承的原生受保护方法统一结算一次。没有第二模型循环/压缩服务，但这三项receipt辅助方法属于锁定Harness版本的兼容点，升级须定向核对。原失败保留，单独复核reused-ID失败项与新增pin/单receipt合同，另存compaction-pair-fixed-20261026；此前普通大批次两项受同一修复分支影响，一并定向复核，完整丢弃及原生Engine无修复分支变化不重跑。

触发：对照固定Claw compact_session及配对测试、安装版Harness0.54的_shared.find_safe_cutoff/_is_safe_cutoff，后者仅在cutoff前后5条搜索调用，12项批次返回分为多个ModelRequest时可能保留结果却裁去调用。外部Claw配对测试默认10000token阈值而样本很短，未强制触发压缩，不能直接拿它作充分验收。现有test_context配对合同为相邻call/return，未覆盖此形态，不重跑旧样本来掩盖缺口。

实现：UsageAwareSlidingWindow仍调用Harness原生compact；对其候选裁剪按原历史顺序匹配tool_call_id（含工具RetryPromptPart、同ID在已结算后再用），仅发现新拆分时计算全历史安全tail，再请求原生compact保留该tail；原生pins/receipt保留，完整请求预算不抬高。没有复制Rust代码或fork依赖。

验证范围：新增tests/test_compaction_batch_pairs.py大批次正常/参数重试/顺序重用ID、可完整丢弃的已结算批次；补test_context.py::test_compaction_preserves_tool_pairing_authoritative_facts_and_private_manifest这一受影响原生Engine接缝。复用tokenizer/raw/chat校准、其它上下文估算证据，不发模型真实请求、不科学重算。命令.venv/Scripts/python.exe -m pytest -q tests/test_compaction_batch_pairs.py tests/test_context.py::test_compaction_preserves_tool_pairing_authoritative_facts_and_private_manifest -p no:cacheprovider --basetemp=workspace/compaction-batch-20261026-tmp --junitxml=docs/rebuild/compaction-batch-20261026.xml；同名独占log。停止条件为确实发生裁剪、无拆分配对、旧目标/原生编译请求证据保留；安全tail超预算仍按既有拒绝，不删配对或抬窗口。通过即冻结。

## 离线维护入口与只读任务检查（20261026）

结果：maintenance-owner-20261026所有登记项通过；原备份/恢复roundtrip、篡改/路径拒绝、空检查点与真实同名产品保留。新版锁文件位置已改为工作区根.forestry-service.lock，前批.forestry/service.lock描述仅为当时实现；未部署过该中间版本，旧文件不作为owner依据、不删除。只读任务检查在另一连接BEGIN IMMEDIATE持有期间读到最新已提交WAL状态，SQL trace无schema/写事务；CLI缺库输出DATABASE_READ_FAILED且不建工作区。无活进程/服务/模型/容器，本批停止条件达到；不再重跑相同通过项。

触发：源码审计确认backup._offline只独占配置端口，同工作区不同端口实例可绕过；cli tasks inspect构造Store会初始化/迁移schema并BEGIN IMMEDIATE。更改：备份/恢复同时持有workspace_writer，锁位置改到工作区根.forestry-service.lock（恢复替换.forestry期间仍同一个OS对象，锁不入备份）；恢复先验证原工作区身份，避免错误配置创建目录。tasks inspect使用mode=ro只读快照、复用Store.read_task，不初始化schema，缺库返回结构化错误不建库。

影响：create/restore离线串行、恢复原路径保护、备份文件集合、任务检查及原单写锁位置。复用上一批关闭排空/硬退出/Job恢复证据，不因锁路径改名重跑这些完整流程。运行tests/test_maintenance_ownership.py新增合同、tests/test_backup.py受影响原备份/恢复合同，以及test_workspace_ownership中实际进程锁重定位项。隔离临时工作区/端口，无模型/容器/生产库操作；预算0模型0真实Job。命令.venv/Scripts/python.exe -m pytest -q tests/test_maintenance_ownership.py tests/test_backup.py tests/test_workspace_ownership.py::test_process_lock_survives_stale_file_and_releases_on_actual_process_death -p no:cacheprovider --basetemp=workspace/maintenance-owner-20261026-tmp --junitxml=docs/rebuild/maintenance-owner-20261026.xml；独占同名log。停止条件：不同端口服务阻止维护；复制和恢复发布期间阻止新服务；原备份/恢复身份/防篡改仍有效；只读读取WAL最新提交、不写schema且缺库不建目录；实际进程死后新锁路径可接管。静态检查只覆盖本批文件。

## 工作区服务单写与关闭排空（20261026）

本批最终结果：ownership-20261026保留原1失败/4通过；修正真实owner PID条件后ownership-process-fixed-20261026仅原失败项通过。hardcrash-20261026证明文件副作用窗口及已提交结果窗口；ownership-job-crash-20261026证明pending/claimed终态在实际解释器被强制结束后同事件恢复、确认后再次启动不重投且无第二Job。ruff F/E9通过。本批所有子进程实际owner及启动器均已wait终态；只读进程观察句柄97702 exit0，无遗留本批进程/模型/容器。

生产变化：create_app构造期间先取得同工作区锁（覆盖schema迁移/认证key生成），lifespan启动恢复至shutdown/Job同步tick排空持续持有锁；已存活owner不按时间被剥夺，不引入代际租约或第二事实库。无持锁者时遗留锁文件保留并正常复用，不删除文件来接管。启动同步恢复与关闭工作均在取消时排空，旧线程结束前不能释放owner。

范围限制：锁只覆盖采用新版create_app/lifespan的服务入口，不宣称独立Store维护脚本、旧版本服务、禁用lifespan的外部ASGI启动或跨主机共享文件系统已经被统一隔离。升级部署仍须核实旧服务/数据库写入者归属并串行交接；真实Docker重启/取消、跨栈/CLI维护入口与UI发布尚未验收。这里的强制进程退出不是断电/存储损坏试验，也不代表完整V2通过。下一批继续补源码链/旧入口兼容、实际模型窗口及完整运行流程，禁止无触发重跑本批通过项。

硬崩溃文件接缝hardcrash-20261026两条通过，未重跑owner已通过项。下一新增接缝为同文件test_hardcrash_keeps_terminal_delivery_identity_without_new_job两种状态：Jobs.submit登记后以受控失败夹具保存终态，分别在pending未领取及claimed未消费时kill实际owner；启动对账后同event_id可恢复领取，重复save不新增终态，观察确认后再启动仍consumed，作业始终只有原1项，零模型/容器执行。该夹具终态不冒充实际Docker成功或科学结果。运行专属ownership-job-crash-20261026.log/xml、workspace/ownership-job-crash-20261026-tmp，只选择新增Job合同，通过即停止。

新增真实进程崩溃合同登记：tests/test_runtime_hardcrash.py使用实际create_app/TestClient/Engine.perform/Resources.write，子进程在（1）文件与immutable artifact落盘后、动作结果提交前，（2）动作结果提交后、回答前，回报真实PID并阻塞；父进程先确认第二实例拒绝，再强制kill/wait实际owner，重新创建服务进行启动对账并尝试同call。验收为实际文件与唯一产物不丢、未知动作拒绝重放且不标成功、已确认动作返回原结果、累计动作仍1、零模型请求。只运行新增两条接缝，复用已通过owner/关闭合同；不涉及真实科学或Docker。命令.venv/Scripts/python.exe -m pytest -q tests/test_runtime_hardcrash.py -p no:cacheprovider --basetemp=workspace/hardcrash-20261026-tmp --junitxml=docs/rebuild/hardcrash-20261026.xml，另存同名log，通过即停止。Job终态提交后硬退出尚不在此合同范围，不以此替代完整硬崩溃验收。

首次ownership-20261026结果：第二实例/预构造生命周期拒绝、启动失败清理、关闭期间同步tick提交四条通过；子进程退出后立即获取锁失败。原测试只kill/wait Popen启动句柄，Windows venv解释器可能是redirector，未记录真正持锁解释器PID，因此不能据此证明持锁者已死或锁实现有残留。临时只读进程观察已终态、无匹配进程，不清除锁。修正失败夹具为子进程回报实际PID并核对父子关系，kill/wait实际owner及启动器；只复核原失败项，另存ownership-process-fixed-20261026证据，不重跑已通过四条。此次为实际进程终止条件改变，不重建相同失败条件。

触发：核查发现create_app/lifespan未持有跨进程工作区锁，第二实例可运行启动恢复；Jobs.loop取消to_thread会遗弃仍可写入的tick。改动为runtime/ownership.py标准库OS锁、构造及lifespan接入、启动同步恢复和关闭排空、Job tick屏蔽取消直至同步退出。影响仅同工作区第二服务、启动失败、关闭期间旧线程及正常启动/关闭；单进程并发/模型/科学成果旧证据复用。

本次验证：tests/test_workspace_ownership.py覆盖子进程实际存活时拒绝接管、强制终止后遗留锁文件可接管、不同工作区独立；构造及预构造第二实例启动前拒绝；启动失败释放；关闭被取消仍等待真实同步事务提交且期间新owner不能进入。隔离tmp数据库/工作区，不触及生产库，不发模型/Docker请求。使用.venv/Scripts/python.exe -m pytest -q tests/test_workspace_ownership.py -p no:cacheprovider --basetemp=workspace/ownership-20261026-tmp --junitxml=docs/rebuild/ownership-20261026.xml，输出独占ownership-20261026.log。asyncio已知沙箱限制，申请正常沙箱外执行；无模型/动作/Job科学预算消耗。通过受影响合同及静态检查即停止，不全量回归。不声称任意独立Store脚本已受服务锁约束，硬崩溃副作用恢复另补接缝。

## 原生发现适配验证登记

触发：已安装PydanticAI 2.54.0实际提供ToolSearch(strategy callable)、defer_loading及类型化历史恢复，避免长期重复维护激活权威。新增native_bm25隔离配置，复用现有中文BM25/权限筛选，原生能力负责发现/恢复；旧bm25仅作对照，生产默认static不变。影响：下一step可见集合、无越权、历史恢复、请求冻结。先对两条路径复用同一个发现/恢复/权限契约，仅运行新增native变体；通过后再扩展共享边界，不将这当完整Driver B或所有对照完成。

## 模板计量验证登记

触发：新增本机GGUF chat_template读取、安全渲染实际wire messages/tools、原生工具参数JSON解码副本与特殊token计数；模型发送前保存模板估计并核对窗口。影响：消息/工具/思考模板形状、不得修改原请求、图像占位不冒充0成本、未知模板明确未知。先运行test_template_measure软件格式夹具；到达边界即停止，必要实际模板校准另行登记，不重跑已完成raw试次。

真实模板校准登记：复用已保存SDK请求，核对其消息/实际工具定义通过本机chat template的输入计数。research/calibrate_template_once.py唯一目录template-calibration-20261025，前置raw试次成功且已用1请求；本计量活动总上限2，本次新增1请求、300秒、1输出token、0工具执行/科学Job，固定同一4B digest。使用/api/chat（明确options num_ctx=32768、think=false、keep_alive=0）并保存从SDK形状到原生chat形状的副本；与此前raw输入/配置不同，不覆盖或重跑原试次。停止条件为模板估计覆盖provider输入用量；exact_template_match另列，单个样本覆盖不声明全模板精确。已有目录拒绝执行；不足则保留原失败调查，不追加到变绿。

结果：template-measure-20261025.xml通过模板形状/原请求不变/视觉unknown/沙箱边界；本机12工具请求校准provider2938、本地3155、含余量3471，本样本覆盖但非精确。总2请求已用尽，未追加，模型已释放；SDK原字节按记录hash补封存，不重算。离线JSON变体排查没有精确复现服务，没有据此修改计量配方。

新增边界：wire-template-budget-20261025.xml证明预算拒绝先保存wire_attempts、wire_template_measurement及model.wire_rejected事件，且MockTransport未收到请求；template-usage-20261025.xml证明有视觉/缺usage时不推导模板差额或假视觉实耗。ruff通过。保留视觉processor未知这一真实限制，不把软件通过/校准样本覆盖当成所有图像与模板的精确计量。

## Qwen35文本BPE计量验证登记

真实校准登记：新代码引入模型BPE后需核对其与本机provider一致性，旧模型任务/科学结果不重跑。使用research/calibrate_tokenizer_once.py唯一试次tokenizer-calibration-20261025，冻结qwen3.5:4b digest d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04；已只读确认Ollama无驻留模型。预算1次generate、300秒、0工具/科学Job，raw文本含中英/组合字符/数字/工具JSON、num_predict=1/num_ctx=32768/think=false。仅验证BPE计量，不评估任务完成质量，也不代替旧失败类型。请求前持久扣预算，目录已存在即拒绝重跑；结束后仅释放本次模型。停止条件为provider prompt_eval_count与本地raw BPE一致；不同则保留失败不重复试。模板和图像不在此校准结论范围。

触发：复用本机GGUF的gpt2/qwen35词表与现有tiktoken，实现身份匹配、元数据有界读取及BPE计量；接入PreparedInput和既有压缩扩展点。影响：文本/工具schema/指令增量使用同一counter，保留实测锚点；模板/图像仍估计，10%文本余量与加倍framing显式记录。无匹配资产时明确回退字节上界，不把其他编码当Qwen。先运行tiny真实格式GGUF夹具的合并/身份/损坏边界，不要求测试机安装真实模型；随后核对本机真实词表并只补预算相关集成。停止条件为受影响计量证据通过，旧失败不在未修复条件下重跑。

结果：tokenizer-20261025.xml/log通过BPE合并/身份/格式/损坏边界；真实raw校准严格1请求完成，6162字节→本地/提供方均1684 tokens，1853文本余量覆盖，模型已释放。完整报告与请求/响应保存在唯一试次目录，没有重建或重置预算。10%文本余量和翻倍framing是可解释的工程估计，不是完整模板实测上界。

旧失败分项裁决：qwen-context-20261025.xml复用原native_facts/source_history两条完整样本，以显式绑定已核验Qwen tokenizer的新配置通过，模型输出仍是软件夹具；未将旧无身份模式计作同一配置成功。输出恢复两项改为真正无效的final_result结构，而非拒绝已合法支持的普通正文；原生恢复与原三次重试上限通过。历史原输入/原失败XML保持。

最后一项预算续接：发现新消息API已会直接排队，但旧oracle仍要求再点resume；同时耗尽旧turn未在消息入口标stopped。已让明确纠正消息在同事务标停耗尽旧turn并排队新turn，保留累计请求/动作/成果；budget-correction-20261025.xml证明原回合直接恢复仍拒绝，纠正后不需额外resume、累计从2→3、动作仍2、原产物不重做。未用更简单目标替换原场景。

依赖：uv沙箱解释器缓存权限失败已终态；获准沙箱外uv lock --offline成功，仅补tiktoken直接依赖声明，包版本未变，没有安装/下载。ruff因测试多余Path导入报F401，已去掉导入；后续静态核验不触发重复模型/科学计算。

## 实际请求信封验证登记

触发：发送前HTTP hook保存准确provider正文与逐尝试身份，绑定PreparedInput与模型身份，并再次检查当前控制。影响：实际字节可重建、重试不覆盖、认证头/URL秘密不落盘、不带身份不发送、暂停后不发送，公开上下文不暴露wire文件。先用httpx2.MockTransport验证，无真实网络/模型调用；通过即停止。复用原生流和请求槽证据，不重验无关科学流程。

结果：request-envelope-20261025.xml/log的原始字节/逐尝试不覆盖/凭据排除/无身份不发送两项通过；另两项暴露Windows长文件名超过MAX_PATH，按既有native_path修复后request-envelope-fixed证明发送绑定与暂停拦截通过。增加实际body.model与冻结模型名核对后request-envelope-model记录相应通过。仅复核受影响失败/新增分支。

request-envelope-sdk原始记录暴露新增测试中误放断言导致NameError（SDK请求/记录断言实际已执行），修复测试后request-envelope-sdk-fixed通过。request-envelope-stream证明真实Engine→原生流→OpenAI兼容SDK→Mock SSE转换、实际发送字节记录及完整正文落盘。软件HTTP响应usage/digest明确是夹具，不代表真实Qwen token实耗或权重验证。

每次传输尝试独立不可覆盖文件，保存原始JSON正文及元数据，关联contexts中的编译输入hash/工具schema hash、预期模型身份和实际model字段。请求头不落盘、endpoint不含身份/查询秘密；正文按原字节保存于会话专属model-inputs目录，公开上下文不暴露wire_attempts。HTTP hook返回前再次检查当前控制/修订与身份绑定，状态变化可阻止传输。记录语义为wire_prepared，不冒充已经送达或生成成功。

Qwen35本机GGUF分词调查见TOKENIZER-FINDINGS.md。当前计量上界缺口仍存在，未运行旧失败、真实模型或科学计算。达到本批请求信封停止条件，后续无相关改动不重复验证。

## 动态发现验证登记

触发：WP2新增BM25发现模式、单源描述、逐请求ToolSetSnapshot及执行成员检查。影响：中文必要工具召回、检索下一step激活、已有请求快照不被修改、权限过滤、激活恢复与旧static兼容。先运行test_tool_discovery离线索引及实际Engine原生发现→读取→正文流程；达到行为契约即冻结。生产默认暂保留static，等待同规格对照裁决；不以该兼容默认宣称动态发现已部署。复用已通过原生流/检查点和权限基础证据，不全量回归或科学重算。

结果：tool-discovery-20261025.xml/log证明双语索引及实际发现→下一step读取笔记→完整正文；权限受限时不激活/执行记忆读取，重建run factory恢复合法激活。tool-discovery-catalog-20261025.xml/log证明实际注册目录在无输入时可检索正射/LAI/既有LAZ/CSV缺失值/图像计数；只启用科学描述夹具，不运行科学Job。该批还定向复核static原生检查点和能力边界兼容。tool-discovery-samebatch-20261025.xml/log证明同批先搜索再猜测隐藏工具仍不会执行隐藏工具，保持原请求冻结集合。ruff F/E9通过。

描述由ToolRegistry和当前授权ToolDefinition派生，未另写工具清单；summary_en来自稳定英文工具标识，模态/输出等未声明元数据保留空值，后续科学能力声明继续补齐，不猜测。BM25使用标准公式、中文单字/双字及检索同义词，不替模型决定使用哪项能力。新任务冻结tool_discovery_mode；旧会话默认static，bm25实验可启用。激活事件按修订/委派范围恢复，并与当前合法集合取交集；ToolsSnapshot保存实际模型定义hash/名字/request_id/revision，执行时检查成员及当前控制状态，原perform继续权威鉴权。

尚未完成：embedding/混合对照、真实模型选择与成本收益、模板级计量、完整请求信封、工具历史版本迁移与Driver A/B四契约裁决。未改生产模式、未部署或调用真实模型。

## 中断批次结清

触发：V2要求取消不遗弃已启动副作用、同批未执行调用结清，检查原生取消源码后新增受控写入阻塞夹具。复用既有暂停/请求槽/续聊证据；仅验证批次副作用与检查点、受影响审批等待。停止条件：真实Engine暂停批次结清后续聊不重放，正常审批部分批次不误结清。

发现：cancelled-batch-20261025.xml/log证明暂停后Engine.run已结束而已启动同步写线程未结束，独占许可提前释放。修复为在执行许可内shield已启动操作，取消时等待它将真实结果保存后传播取消；未启动同批操作不派发。

第二处缺口：cancelled-batch-fixed-20261025.xml/log证明排空副作用后，中断快照被默认latest_checkpoint读取隐藏。异常回合收尾显式读取当前native_run的include_interrupted快照，根据原action journal逐call_id配对：真实结果直接复用、无动作记录为cancelled/CALL_NOT_EXECUTED、已有动作但无结果为unconfirmed。只在异常路径处理，正常Deferred等待保留原协议。消息边界完整标complete仅代表配对，不代表用户任务完成。

最终证据：cancelled-batch-frontier与cancelled-batch-resume XML/log通过；后者确认started为completed、未启动兄弟为cancelled、任务保持paused，新回合完整回答且写入次数不增加。cancelled-batch-deferred XML/log证明正常部分审批、已结算读取不被误结清或重做。ruff F/E9通过。原两份失败留存，修复后仅复核受影响项；未真实模型/科学计算/部署。

源码依据：本机PydanticAI _cancel.py、_tool_execution.py取消时cancel_and_drain、_agent_graph.py中断时追加不完整ModelRequest；本机Harness StepPersistence on_run_error保存中断frontier。应用只补动作权威结果与原步骤库适配，不另建工具结果账本。更多断流/硬崩溃组合及跨进程writer仍未全面验收。

## 本次验证登记：自动续接与等待让位

触发：新增set_job_follow_up、完成回调调度、turn_continuations及活动预算关联、wait_job新消息让位。影响：仅通知不唤醒、欠报告自动串行续接且只一次、暂停与用户输入优先、自动续接不重置原活动预算、显式等待让位不取消Job、删除新关联表。复用已通过终态持久/消费/消息接回与科学成果；先运行test_job_continuation新离线契约。停止条件：上述实际原生流程通过；只修复并复核失败项，不重跑无关基线或科学计算。

结果：job-continuation-20261025.xml/log五项通过（含真实Engine/原生流的自动正文提交与原活动预算耗尽时零新增模型调用）；job-continuation-race-20261025.xml/log证明Worker旧快照不能清掉新登记意图。只运行新增竞态，未复跑已通过流程。ruff F/E9通过。

实现：set_job_follow_up通过原job.observe守卫登记剩余工作，null为仅通知；Jobs应用循环tick后分派持久终态，确定性turn_id及turn_continuations/schema6保存自动续接与原活动身份，先排用户输入。自动轮次沿用原活动模型请求预算和会话累计预算；消费/暂停/旧修订不重复自动派发。活跃回合下一step接入终态时也携带remaining_work。显式等待每秒检查新排队消息并让位，不终止Job。Worker保存保留最新完成意图；调度事务复读最新Job，删除/清理不唤醒失效会话。未重算或调用真实模型。

适用边界：这是模型主动登记完成策略后的自动接回机制；真实模型能否正确选择登记仍待真实判断接缝验证。所有取消批次结清、跨进程writer、无进展跨活动累计及A/B Driver等未验证事项继续保留，不因本批通过推定完成。

状态：基线已完成，五项均失败。日期：2026-10-25。复现后未重复运行。

触发：用户明确授权V2本次例外，五项既有失败各复现一次。
范围：test_native_loop一项H2、test_output_recovery两项、test_progress两项；不运行全量，不调用真实模型或科学执行器。
复用：2026-10-22工具执行交接与原始 `C:/Users/yzh/AppData/Local/Temp/after_s6.xml`。
停止：各用例一次完成即冻结；异常/环境差异单独保存，不重新运行追求同样失败。
证据：本目录 baseline-native-20261025.xml 与 baseline-native-20261025.log（有效环境，4.43秒）；历史XML保留为baseline-historical-20261022.xml。

原失败分别涉及：两项预算拒绝、两项无效文本输出未按原生协议恢复、一项恢复后状态created而非paused。根因尚须结合当前复现和源码逐项裁决，不沿用统一归因。

环境说明：第一次临时目录权限错误，未执行正文；第二次沙箱内进程卡在事件循环初始化。独立5秒探针输出before_loop后超时（asyncio.run(asyncio.sleep(0))），因此停止命令行身份核对过的本轮进程47040，在沙箱外使用独立目录完成唯一有效基线。没有更换测试输入/代码，没有真实模型调用。

## 当前裁决

- 两项上下文失败仍为工具schema与文本的字节上界超预算，待WP2计量与工具发现修复。
- 两项output_recovery仍要求拒绝普通文本，而现行Agent明确支持TextOutput(public_answer)。按完整正文产品契约先核对断言适用性，不直接强制JSON输出。
- 请求预算续接项仍为messages返回created而非paused；需检查消息入队/自动推进契约，不能以改断言掩盖累计预算重置。

## 请求锁改动的定向验证

触发：WP1将整回合全局锁改为同会话锁，模型适配器持有共享请求锁。影响：请求互斥/完整流消费/取消释放/原生回合结束与超时承接。复用：原生图和安装版本_streaming_handler源码表明handler等待stream_done并关闭request_stream后才返回。

新增隔离行为验证：tests/test_model_request_slot.py通过，证据request-slot-20261025.xml/log；覆盖流未结束时禁止其他请求、外部等待不占槽、执行中与排队取消后槽可用。未证明完整应用级多会话或Inbox竞态。

接续检查：只运行test_native_hooks_output_schema_and_checkpoint_compatible、test_new_message_recovers_model_timeout_without_restarting_failed_turn_or_resetting_budget、test_complete_public_answer_is_preserved_when_backend_also_emits_legacy_summary。停止条件：原生流/检查点和受影响结束/超时路径通过，随后停止该范围验证，不重跑五项未修复基线。

## 步骤输入与应用级多会话

触发：新增claim_step_input、历史承接去重，waiting_job允许启动排队消息；需要验证原生历史实际保存及完整跨会话流程。复用前述请求槽与原生提交证据；本范围通过即停止，不重复基线。

- 用户输入继续由turns/turn_requests持久保存；claim_step_input在同事务内将queued输入转steered并记录领取事件。下一模型步骤通过受支持接口同步请求与原生历史。无检查点时原始用户输入仍可承接，有检查点时通过输入身份避免重复注入。未创建第二个用户消息队列。
- step-inbox-20261025.xml验证领取顺序/单次领取/重建仓库后的事实保存；其早期原生流程断言不足，不能证明最终回答提交。
- step-inbox-history与step-inbox-native-api记录能证明原生历史含输入，但旧流夹具不支持TextPart，缺最终消息验证；这些记录保留，相关完成声明由下列新证据替代。
- multisession-20261025.xml失败原因是测试的工具专用stream_adapter把TextPart当ToolCallPart，并非跨会话资源锁失败。改用已有public_stream_adapter，补最终正文和无blocker检查。
- runtime-completion-20261025.xml/log两项通过：A等待受控工具时B已提交完整回答且A未结束，释放后A完成；新指令在下一原生step出现一次、检查点中一次，后续回合无重复并提交最终正文。
- 旧文件/输入修订排队兼容用例通过（step-inbox-native-api记录）；没有新增真实模型或科学Job。

未证明：跨进程多writer防护、任意崩溃时刻、所有工具消息批次取消、持久Job终态接回和A/B完整选择。以上仍属于未完成范围。

## Job终态消费与结束边界（本轮新增）

触发：新增job_deliveries事务投递状态、等待默认值、原生步骤接回及删除关联表；后台任务UI入口改动。复用：既有Jobs执行/取消/清理实现和已冻结科学成果；只验证受影响接缝。

- schema_versions=5，新增job_deliveries仅保存投递状态，终态事实仍属于Jobs/events。同事务写job状态、终态事件与投递记录；重复终态去重，已提交终态拒绝异终态迟到覆盖。旧历史终态没有猜测补发。
- claim_job_input在同会话模型步骤读取最多8项，paused/取消/修订不匹配不领取；claimed在失败时保留，完整回答提交同事务改consumed；请求历史有事件身份时不重复注入。主动wait_job观察结果成功持久记录后确认消费，UI读取不消费模型投递。
- wait_job默认0秒立即查询；显式1–120秒等待保持；后台Job预算未改。后台任务按钮脱离showDiagnostics，已有JobsPanel复用。
- job-delivery-20261025.xml/log通过：原子持久、重复/迟到回调、事务事件失败回滚、重建manager/repository恢复claim、消费幂等、暂停不领取，以及受影响创建恢复/取消路径。
- job-delivery-flow-20261025.xml记录3项通过（原生接回到完整正文后消费、默认查询不sleep、显式等待兼容），1项因测试辅助函数名错误失败。仅修该测试后job-delivery-delete-20261025.xml通过，证明清理先删delivery，不留外键违例。旧失败保留。
- finish-boundary-20261025.xml通过：最终响应时提交的新消息未被本step领取，原排队事务与done callback启动唯一下一回合，两条完整正文持久保存，无剩余queued输入。
- 前端普通沙箱构建被realpath EPERM阻断；获准沙箱外npm run build成功（1849模块，2m19s），静态入口与哈希JS同步更新。未部署或浏览器视觉验收。
- 受影响Python代码ruff F/E9通过。达到上述范围停止条件；未重跑旧五项或科学计算。

仍未交付：空闲会话根据原目标自动唤醒模型/仅文件UI通知的策略；Job显式前台等待对新消息的即时让位；全批次中断结清；跨进程单writer；工具发现/请求信封/A-B裁决及后续科学语义等。终态已可在下一现有step接入，不得宣称全部WP1/WP3完成。

## 科学复用候选边界（2026-10-25）
触发：imagery_catalog缺少本次声明时直接导入并宣称完成；修正为候选回执，显式声明按同一schema规范化比较。影响：API/原生process_imagery共享复用入口。复用：已有科学成果与Jobs执行证据，不启动模型/科学作业。验证范围：无声明无导入、规范化默认参数可复用、定标/分辨率/模式变化不复用、既有授权/输入/产物破坏边界。停止条件：上述定向边界通过；完整ScienceRunIdentity、LUT血缘和算法/预处理身份仍未完成。
首次science-candidates：候选/显式复用/参数变化/产物破坏通过；旧输入变化用例因辅助submit现默认带声明，落入合法新计算提交，临时库出现Job记录而原断言要求无Job。恢复这两项原本无声明的输入条件，仅复核bytes/additional_file失败项；不改生产行为或重跑已通过项。

## ScienceRunIdentity绑定（2026-10-25）
触发：旧复用只匹配数据/声明/镜像，没有算法/预处理与LUT身份；新recipe记录完整身份，目录缺身份或身份不符返回透明候选，不触发重新计算。影响prepare_imagery、worker结果记录、catalog导入。复用前批候选/参数边界及科学结果；不重算。验证范围：三种recipe请求身份、worker回执持久化（替身科学函数）、LUT/定标身份变化、目录旧身份/算法/预处理/定标失配，以及受影响原复用边界。停止条件：此范围定向验证通过，不宣称完整科研精度或旧目录补录完成。
首批science-identity范围通过。新增触发：补充反演raster+lut实际API导入接缝，science identity加入明确method_id；候选缺声明与缺身份状态区分。只复核新增反演三种状态（原LUT/变更/缺失）及受身份字段变化影响的recipe/目录合同；不重跑其它原授权和输入变化边界。
反演API新增样本首次在资源登记被幂等键保护拒绝：测试沿用fixture已有declaration request_id却换了路径，未进入复用。仅改新样本登记键为invert前缀，定向复核三项；其余身份与导入合同已经通过不重跑。
反演changed/missing通过；same已完成真实导入断言，但测试直接Path读取超过Windows MAX_PATH，未读到已写产物。改通过用户实际artifact内容API读回身份，仅复核same，不改生产逻辑。

## 明确采用旧科学候选（2026-10-25）
触发：完整身份校验使真实旧目录只能返回候选，需实现V2允许模型核对旧方案后透明采用的路径，禁止补造当前算法身份。修改共享ImageryRequest/submit/reuse及原生工具字段；候选ID绑定完整目录条目，采用说明与未知身份随产物保存。影响声明校验、幂等回执、失效候选不能回退计算。复用前批正确科学成果与身份合同。测试范围仅新增采用/过期/方法不符/已知身份不符/缺说明/输入或产物变化、原生传参和请求序列化兼容；停止条件上述通过，不调用真实模型或执行器。
首次legacy-adopt：采用并下载未知血缘、幂等重放及拒绝已知身份绕过通过；五个失败样本的无导入/无Job断言已通过，但HTTP状态预期与应用通道不符。改以精确结构化错误码验证实际拒绝层，仅复核这五项，不重跑已通过采用路径。
legacy-adopt-errors五项结构化错误码通过。只读旧目录审计实际成功，保存legacy-catalog-audit-20261025.json：两项原Job code/result哈希、声明、镜像、目录产品归属匹配；不重算/不改目录/不把原code SHA冒充当前recipe身份。新增验证：原生注册工具经真实perform完成候选→明确采用及旧请求序列化兼容，防止新增可选字段改变历史幂等载荷。
原生工具采用与序列化兼容通过。最后补旧反演采用的指定LUT接缝：旧result含原LUT哈希时相同LUT可明确采用；提供变更LUT必须拒绝，不允许用adoption_note替代数据身份。仅运行新增legacy_same/legacy_changed，已有完整身份反演样本继续复用。

## 只读诊断与不同排障检查（2026-10-25）
触发：sqlite_probe虽mode=ro仍创建临时写探针，WAL只读连接可能改共享内存；改无写探针、escaped immutable主文件快照，有旁路文件明确实时提交未知。Progress将无新证据一律stalled>=limit停止；改不同结构化失败的首次检查不自动判循环，仍不算新证据、不重置stalled/预算；重复请求/同阻碍/无效记账仍停止。停止决定随既有事件持久化，旧记录保留原语义。影响异常诊断、progress.record/check/重放。复用调度、科学产物、上下文恢复证据；定向覆盖纯只读/实际WAL/缺失损坏库、不同排障跨重建/重放、相同请求/障碍，以及受影响旧重复/失败/分页/上下文恢复边界。通过即停止，不启动模型或科学作业。
首批progress-probe中WAL/缺失损坏库、重复与恢复边界通过；新检查用例尾部引用不存在的model_request_count字段，改核对完整task state保持不变（更强）。URI用例未进入connect，追加实际诊断回执定位，只复核两个失败项。
probe-fix中新检查持久化/完整task state不变通过；URI失败定位为测试替身参数名uri与connect(uri=True)冲突，改名database，仅复核该项。生产诊断没有对应缺陷，不重跑WAL等通过项。
probe-uri通过escaped immutable只读URI及无文件变化；其它边界证据冻结。新增一次原生Engine完整流程：真实守卫登记三个不同受控读取失败→模型下一请求完整报告，检查累计请求实际计费、无新证据声明、预算配置不变、无Job；只运行新增样本。

## 活动级Progress（2026-10-25）
触发：Progress此前按turn查询，持久Job自动续接虽继承模型预算但会重置stalled/事实去重。改复用既有turn_continuations映射查原活动所有观察；原生调用重放仍限定当前turn/call/request，角色与新用户活动不串。事件追加活动及修订身份，旧映射记录可直接查询无需重写。影响latest/check/重复观察/重复决策/障碍计数/previous事件；原context恢复保持原真实turn范围。验证新增活动继承/重建/同call跨turn/真实Job自动续接，以及受影响之前排障与重复/恢复合同；科学成果与模型试次复用，不新发真实模型。通过即停。
活动级首批progress-activity通过（含实际Job完成回调→原生继续→继承阈值停止），不重置活动/模型预算。新增触发：资源读取的真实失败缺条件身份，来自不同文件/已变化版本的同错误被合并同障碍；Failure可带后端condition，真实资源读捕获resource/path/version，经同步/异步动作通道保存，Evidence/decision使用该身份，模型假设/args不能伪造condition。测试新增真实binary文件失败、变化版本、原journal重放与原生完整报告；仅复核条件相关及生产异常通道原合同，不重跑活动成功项。
条件身份范围progress-conditions通过。新发现接缝：活动继承后若在Job新终态投递前check，真实新证据可能被旧stall挡住；调整先认领并按event_id一次观察已提交终态，再检查progress。同事件跨请求/自动回合不重复奖励，消费仍由正常回答提交确认。前批活动真实Job样本只证明继承停止，现因新终态观察语义变更需更新为真实新事实→stalled0→后续记账stalled1不再清零；不是靠更简单目标掩盖失败。仅复核该受影响流程及终态投递/预算边界。
job-progress范围通过，完整回答、预算耗尽零新增请求及原终态消费保持。补一处跨重建＋新自动回合的重复终态合同：按同事件已观察事实跳过，不重新清零原stalled，Job仍原succeeded。只执行该新增样本。

## 历史研究评分与报告归因纠正（2026-10-25）
触发：research_run自动字段全通过即completed，未把manual纳入完整契约门；research_table把completed译为成功且未知墙钟填0；record重判取elapsed而不是原wall_seconds并覆盖原文件。任务09原记录无Job且MODEL_PROTOCOL_ERROR readonly，旧报告部分位置却归执行器。修评分/汇总分开自动/语义/Job状态/科学证据，未知保持未知；重观察新文件独占保存，已存在run在API前拒绝。验证只用软件夹具：语义门、失败层、完整检查集、不可覆盖/禁止同试次重建、墙钟保留；通过即停。随后只读重分类原11项记录另存审计，不重跑模型、检查或科学计算。
research-score首批通过。只读重分类原记录输出SCREENING-CORRECTION-20261025.json：12计划/11有记录/6自动项通过/0绑定完整语义通过，原verdict/原文件SHA保留，任务02/05墙钟保持null。同步更正RESEARCH-20261021及REPORT-CORRECTION，区分夹具与真实科学结果，不重新算数或执行检查。新增表格呈现样本只确认unknown不填0、自动项不叫成功；仅运行该新增样本。
评分审计源码快照已按原scorer_sha256归档references/research-scoring，字节SHA断言一致。后续CLI示例/空列表防护不影响screening_score结论，复用原重分类，不重新运行原检查或改写原审计。

## 独立Driver B（2026-10-25，行为验证未运行）
触发：V2要求独立Python循环，不把native_bm25配置当B。新增research/driver_b.py直接控制model.request_stream/公共FunctionToolset/批次与检查点，复用现有Engine会话、动作、Jobs、上下文、请求锁和StepStore；工具注册与事务模型预算提到application_tools/charge_model_request供A/B共享。生产无B开关。参考固定DeepSeek executor/pipeline的完整结算和取消排空，源码已归档、未复制TS框架。
拟运行tests/test_driver_b_contract.py的四条既有原文合同（A/B各一份），A因共享提取而定向复核，B不改输入和断言。停止条件为明确的共享合同结果与不支持范围，不发真实模型或科学计算。
首次验证调用在工具执行前被自动审批审核的用量限制中断，不是代码不安全判定；该次apply_patch/ruff/pytest整组均未执行，无log/XML/活进程。后续只读核对确认测试文件缺失，已单独补写；普通静态ruff通过，异步行为验证仍待审批审核恢复，不绕过限制在普通沙箱重建已知会挂起的asyncio运行。
不受审批阻碍的独立工作：添加纯同步schema装配合同（真实应用工具注册同源、搜索/文件必填schema、科学采用参数及执行前校验保留），不启动TestClient/事件循环，也不替代被阻断的四条异步行为合同。仅运行该新同步样本，范围达到即停止。
driver-b-schema-20261025.xml/log纯同步装配通过；B追加coordinator合法集合求交及待运行权限/同批负例后静态ruff通过，schema范围未变不重跑。已将未执行原因、原命令和无活句柄写入driver-b-prototype交接/DRIVER-DECISION；不宣称A/B四合同通过。
审批恢复后原driver-ab命令首次实际执行，句柄94103已终态exit1，driver-ab-20261025.xml/log保留：A四合同通过；B多会话/中断恢复/发现及权限/同批负例通过，Job接回失败。原run-errors证实失败为B主动DRIVER_B_UNSUPPORTED拒绝原合同H2任务，不是Job重复计算、环境不可用或模型错误。保留原合同和失败，不改成H1凑通过。根据V2优先最简单已满足路线，选择A继续生产整合；B当前覆盖有限，不据此推断其架构无法实现。B不扩建H2/审批/压缩，也不作真实模型成本优劣结论。
共享注册/charge_model_request迁出已由A四合同验证；补受影响原累计预算耗尽边界（只A原True样本），原False/A其它三合同已过不重跑。通过即停止本驱动裁决批次。
driver-a-budget通过，A驱动选择本批收口。原实验源码按SHA冻结references/driver-ab，未选B的共享比较入口归research/driver_contracts.py，保留原输入/断言/失败，collect-only确认可显式收集原全部case，不重跑行为试次。此归档不将B失败改判通过。DRIVER-DECISION含A选择理由、B覆盖缺口、C可行性与尚未发布/比较边界。

## 标注工具检索开发筛查（2026-10-25批次）
触发：WP2/5要求工具发现收益裁决，已有5类原生发现合同仅证明特定查询可用，不代替目录检索比较。先冻结20条英汉开发查询（真实任务类别，非留出/统计样本）与单源真实注册schema/descriptor；对全目录、人工分类上界、BM25做目标召回与注册schema UTF8字节比较。类别基线不是自主分类器；字节排除原生/search/system开销，不当真实tokens。embedding/混合支持精确目录/查询hash＋模型revision/dimension绑定的输入，未验证资产时明确not_run，不以零向量/英文BERT凑结论。
新脚本先验证漏召回不被掩盖、未知embedding不算失败/成功、向量绑定/非法值和RRF；随后目录/基线各只运行一次，独占输出，不启动任务/模型/Jobs或重新计算科学成果。停止条件得到原始漏召回与资源缺口，不追加同配置实验到全绿。
目录v1和20条查询已各冻结一次：all_registered20/20，人工分类上界20/20，BM25@8为17/20；平均注册schema字节13750/9077.05/8845.6。原漏项q05冠层/树顶/单木、q06表格清理单位、q18英文notes，完整原排名保留discovery-lexical-v1-20261025。当前缓存仅英文BERT/视觉模型，embedding未运行，不能算零分。
相关改动触发v2：只在既有工具定义docstring补真实用途（code_execute表格/单位；已存在canopy树顶/单木；memory英文notes），不按query设固定路由或加同义词特例，不扩展点云实现。检索器/查询/阈值不改；目录身份新版本，冻结后对同一开发集单遍比较，不代表留出收益。
v2目录仅单源语义描述变化，同20开发查询BM25@8为20/20，平均schema9045.6字节/全目录13979；初始17/20原结果保留，不把已见开发集修复视为留出增益。为完成原计划第二检索器比较，先核实缓存无合适多语言embedding，当前venv无onnxruntime/tokenizers/transformers/torch，仅numpy。官方intfloat E5模型卡确认多语言/MIT/查询文档前缀/掩码均值归一化/512截断；官方仓库提供118MB qint8 ONNX。拟独立研究target目录安装CPU依赖并锁hash，不修改pyproject/uv.lock/产品环境，不安装torch、不发Qwen对话或科学作业。资源上限：模型资产合计≤160MiB，CPU2线程、batch≤8、推理总300秒；单批一次，输出存在不重跑。
研究CPU依赖hash锁与target安装成功（onnxruntime1.31.0/tokenizers0.23.3/numpy2.5.4及完整依赖锁），产品venv/项目依赖未安装变动。官方E5资产固定revision614241f622f53c4eeff9890bdc4f31cfecc418b3，6文件官方LFS/Git内容hash均验证ready，下载会话19307已exit0；原manifest和日志保留。池化单元验证通过：mask排除padding、L2归一化、空/零拒绝。
现在发起单次真实CPU embedding编码（不是Qwen对话/科学计算），v2目录/原20查询hash绑定、expected labels不入模型、2线程/batch8/300秒，先写started凭证，输出/凭证存在拒绝同身份二次推理。编码后只读向量跑混合对照；不重复前轮lexical模型或科学输出。
E5真实CPU编码首次成功，35条文本（15隐藏工具+20查询），向量/started凭证/源代码与资产hash均保存，0.734秒记录含会话加载；未按效果追加推理。后续组合报告直接复用lexical-v2结果，不重算正确BM25/分类基线；原报告只保留lexical top8，因此混合协议在查看新排名前冻结为lexical8＋dense16、RRF60、返回8（明确区别于无基线runner默认16/16）。添加禁止重算的单元合同，仅跑该新增项后做向量排序一次，不再调用编码模型。
最终discovery-comparison-v2复用原lexical/classification，向量/混合各20/20，schema字节9493.7/9473.65；BM25为9045.6、全目录13979。无额外目标召回，当前不把embedding加入产品依赖；不从开发集/字节推导模型质量或显著性。DISCOVERY-COMPARISON与交接记录资源、官方MIT/revision、原失败、范围与下一实际窗口/任务对照门；本批离线检索范围停止，不追加模型编码。

## 迁移备份权限失败定向复核（20261026）
触发：现存store-migration两项在已完成副本写入后的Path.resolve(strict=True)被沙箱WinError5拦截，已有备份测试此前在沙箱外通过。新条件仅将这两项移到获准非沙箱进程，保持代码、输入语义、断言及原失败不变。影响：备份完整校验、原位置恢复、发布后新增数据保留。复用：已通过迁移原子/重入/未来版本三项、实际旧备份43任务107run复制审计及全部科学结果；不重跑。命令：pytest仅两个原失败node，独占store-migration-permission-20261026目录和XML/log；模型0/Job0。停止：两个原失败通过或遇具体新失败，禁止同条件重试。该范围仅证明新增数据被保全，不证明跨版本合并或可继续使用。

## 回退后重新启用新增数据（20261026）
触发：原回退用例只断言保留副本，不能证明新增消息/产物可重新成为活动工作区。新增受影响分支：从旧快照退出时保留旧快照数据，再在原路径完整恢复发布后快照，验证原消息/产物/外键及继续写入；不合并部分表。复用已通过备份往返及迁移/真实旧库审计，原断言保持，仅运行扩展的rollback节点。权限条件沿已确认非沙箱，独占rollback-return-20261026目录/XML/log；模型0/Job0。停止：该完整往返通过或具体失败，停止后不扩测。边界：未运行历史旧版本服务，不证明旧版本能安全消费V2新状态。
权限复核结果：store-migration-permission两项原失败exit0，断言/产品代码不变，仅沙箱条件变化。扩展回退结果：rollback-return exit0，完整快照切回恢复新增消息/产物、无外键违例并可继续写入。该范围停止，不重跑。旧备份审计只读统计：61 complete/45 interrupted/1无快照，45run的49未结清调用未执行，不能声明继续推理兼容。证据/身份/发布操作边界汇总MIGRATION-COMPATIBILITY-20261026.md。

## 通用报告修复后的单次版本验收（20261026）
触发：原static/native真实失败之后，write_file相对路径反馈、根回答内联产物身份/哈希验证、事实/参数/假设与方法证据说明已改变，软件report-delivery/发现说明证据已通过，真实能力尚未证明。一次阶段验收保持原目标/输入/方法笔记/4B研究别名与实际32768窗口；仅static默认路径，不重开旧试次、不重新比较native。使用report_acceptance_once.py复用原runner，独占新目录，prepare要求source身份改变；原目录/日志不变。总上限12模型请求/30动作/900秒，科学Job0，code.execute不授权。停止：本单次任务终态或上限，无追加试次；失败如实保留。语义验收沿原7项：统计/CRS、方法来源、camera与独立定标、Cab边界不得猜因果、光谱拟合不等于现场精度、真实文件和链接、无重算。共享模型占用则运行前拒绝；只释放本批别名，不停止共享服务。先冻结源/协议，再仅run一次；当前此验收预算不用于阶段3统计。

## 报告正文内产物链接统一准入（20261026）
触发：report-acceptance真实新版本单次已终态，报告保存成功但正文含虚构verification_report_001下载路径，根回答原校验未覆盖正文。改动：Resources.validate_artifact_links复用同任务/哈希验证，根回答及write_file Markdown正文共用；写入前拒绝无效链接，说明自链接须等真实回执。受影响：未知、跨任务、被篡改引用不得封存或最终交付，合法已有引用/原相对路径恢复不退化。新增四种原生软件用例并复核原report-delivery受影响项；独占report-body-links-20261026 XML/log。原真实报告/协议不改、不发新模型请求；模型试次预算停止。停止条件软件链接合同通过或具体失败；科学语义仍未验收，不加科学关键词黑名单。
report-body-links首次5项通过，3项新负例在夹具错误期待kind=error处失败；应用既有工具失败合同实际为failed。只改夹具类别名，保持VERIFICATION_FAILED/无落盘/无产物/原生两请求断言；仅补unknown/foreign/tampered三个失败参数，不重跑valid及已通过原项。新增fixed日志和独占目录，原失败保留。

## 单次报告产物真实下载接缝（20261026）
触发：新真实任务首次保存了产物，原runner只查Store未覆盖HTTP下载。只开启该隔离工作区应用enable_agent=False，GET该任务产物content与download=true，核对真实字节/SHA/类型和未新增模型请求/Job。复用report.json原task/artifact/预算及原科学结果。停止：两读取通过或明确失败；不发送新目标，不启动模型、不重开已结束试次，记录endpoint-verification.json独占结果。不把下载成功当科学语义验收。
report-acceptance单次结果：session94207 exit0，task efcbe703-3234-459e-a720-caea008eff03，4模型请求/4动作/20.281秒/Jobs0；保存报告dd4250ae-6678-483c-82b2-922f186f98be，HTTP预览和下载均字节SHA匹配，不能抵销科学语义失败。after.models为空。原试次预算停止。report-body-links软件5项原通过＋3失败夹具修正后通过，原失败保留；不重跑真实模型。详见REPORT-ACCEPTANCE-20261026.md。

## 主入口真实报告读回/刷新（20261026）
触发：真实报告保存/HTTP下载已通过，但浏览器仍只有旧Mock组件证据。单次阶段接缝采用原结束报告任务/原字节，research/report_ui_app.py固定隔离runtime并enable_agent=False；8796独占临时HTTP由Playwright webServer管理，不复用已有服务。验证：实际捆绑主入口选原会话→Markdown表格→产物预览→HTTP下载SHA→切换会话→刷新恢复→桌面/移动截图；不发新消息、不开模型/科学Job。所有浏览器请求只允许GET/HEAD，任务模型/动作计数不变；原科学语义失败不变。证据report-main-20261026目录/JSON/截图，不覆盖。复用真实模型4请求、原HTTP字节、此前Jobs组件/实际Docker及迁移证据，不重跑它们。停止：该流程通过或具体失败；仅修失败/受影响分支，不展开全UI回归。该范围不证明原生文件选择器或模型新数据自主执行。
report-main首次未启动服务/未进测试正文：Windows cmd把../.venv开头误识别为..命令。仅修webServer解释器路径为Windows反斜杠，原断言/目标不变；新证据后缀20261026-server-path，不覆盖原启动失败。8796无本批已启动服务，不重建模型试次。

## 主入口真实归档Job读回（20261026）
触发：Jobs此前浏览器为Mock组件，需确认真实后端/归档日志/结果在实际主入口可用。复用executor-seam已完成清理的两个真实Job和原37字节产物，不重新提交或运行容器；make_jobs_app固定该隔离runtime且enable_agent=False，不开启Job循环。端口8797由Playwright独占管理，原8796已无监听。仅GET/HEAD：主入口进入原会话/后台任务，读取成功/取消终态、归档日志、原结果SHA，刷新后保留两Job；任务模型0动作2保持。证据jobs-main-20261026独占JSON/截图/log；停止为该接缝通过或具体失败。此范围不替代源用户消息、执行中取消交互或新数据/科学自主验收，原真实执行器和Mock取消证据继续复用。
主入口结果：report-main-server-path与jobs-main均exit0；真实后端无Mock，模型/动作计数保持，三张桌面/移动/Jobs截图已查看，端口8796/8797无监听。已达到各自停止条件，不重复本批读取。范围及未验证边界见MAIN-ENTRY-20261026.md。

源码研究接续（无运行验证触发）：固定DeepSeek22ddee的assistant-stream/agent请求段、tool-history及指定测试已读取/归档，source-stream-history-20261026.json记录hash与精确范围；REFERENCE-MATRIX映射本地原生流/事务提交/完整请求schema与不移植理由。外部测试未执行，未安装其环境；无产品代码变化，不重跑本地合同。旧未决schema跨版本兼容仍未证明。

## 普通文件读取的有界真实来源引用（20261026）
触发：真实报告把正文lut_sha256误作输入SHA；现有fs.read只返状态版本，不能走cited_content顶层citation复制。相关改动：Resources.read仅对本次已读原始字节计算window_sha256，只有offset0完整读到尾才source_sha256，否则null/byte_window；不追加全文件读取。read_document原完整哈希citation保持，write_file复用既有citation_actions/动作权限，不新造来源表或科学断言。影响：BOM原字节身份、小文件源SHA与内容LUT分离、首/尾分页不冒充全文件、原生read→write真实引用链。复用已通过科学计算/报告链接/主入口，不重跑。定向tests/test_file_citations.py加既有文本分页/来源复制受影响项，独占file-citations-20261026 XML/log；软件模型，真实模型/Job0。停止：上述合同通过或具体失败；不追加真实报告试次，不把可追溯等同科学结论正确。
