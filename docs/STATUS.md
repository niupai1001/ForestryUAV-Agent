# Implementation / 实施状态

本文件区分实现与证据。完整目标未缩减：R01–R16、U01–U08、AC-01–AC-13 与三个阶段均保留。没有源码或通过证据的功能不标记完成。

2026-10-06 用户明确暂缓 Agent 联网搜索开发。新增连接器、搜索稳定化与联网搜索验收停止推进，已有实现和安全回归保留；相关项记为用户暂缓，不计作通过，不阻塞本轮本地通用能力交付。其余本地/领域/评估与规模目标继续推进。

| Batch / 批次 | State / 状态 | Delivered / 已有行为 | Evidence / 证据 | Remaining / 未完成 |
|---|---|---|---|---|
| P0 | in_progress | 独立应用、修订双语基线、uv/package 锁、实测 doctor | workspace/deployment.lock.json | 领域数据许可/格式、兼容地形、≥100 GB 数据、低资源选择 |
| P1 | in_progress | 真实模型与只读文件工具、持久任务、产物版本、本地 React 工作台 | small / large 数值烟测全部核对通过；2 项浏览器检查通过 | 语义验收、完整 UI 交互 |
| P2 | in_progress | 动作/attempt、请求去重、修订隔离、检查点；断网容器、独立 WSL、终态日志、同回合恢复事实；离线范围依赖和环境复用 | 真实计算/取消/超时、两模型运行中作业真实进程恢复通过；历史大模型调试/依赖UI通过，最新v9两模型失败；离线依赖真实15项通过；完整回归177项 | Agent依赖获取/源码构建、两模型稳定完整依赖UI、完整资源准入、全部恢复窗口及 UI、小模型完整调试/报告质量 |
| P3 | in_progress | 候选指纹/验收/清单；文档/知识/记忆；原生上下文/角色；H0/H1/H2；已有网络边界、持久输入/引用、无进展保护、工具范围与 UI 修订；有界事实及Schema实测绑定、原生计划与UI视图 | 完整回归177项；两模型文件范围恢复与修订/重传 UI 通过；最新43份输入/原生计划审计通过；历史搜索单项证据保留 | 角色异常/冲突交互、两模型完整本地通用及长任务质量、完整报告/通用评估；联网搜索用户暂缓 |
| P4 | not_started | — | — | 真实 ODM 航片流程与冠层分支 |
| P5 | not_started | — | — | 真实 PDAL/lidR、边界实例、指标 |
| P6 | not_started | — | — | 六组合冻结实验、低资源/规模实测、发布门槛和证据包 |

## Evidence commands / 验证命令

`pytest -q --basetemp workspace/test-temp-20261006-1`：12 passed，2.27 秒。覆盖认证/Origin/Host、请求重放与冲突、伪造授权、路径/版本、动作重传、产物不可变/篡改、消息与修订、取消终态、重启暂停、HTML sandbox、禁止云端和设置 CAS。

真实模型试次按 task_id 保存，不删除失败记录。已暴露并修复新框架的异步检查点与用量属性适配；两个模型均真实读取/写入过数值夹具，只有完整烟测全部独立检查通过的试次才能标为模型闭环通过。

浏览器复验：2 passed（11.0 秒），保留 desktop/mobile 截图和 Playwright JSON，截图不是执行证据。small task `fb6a156a-d3a4-498a-9467-b94021f381fb` 与 large task `5a3434f1-5a26-484e-a95f-655869e88ae0` 的六项独立核对均为 true，证据为 workspace/evidence/model-<profile>-<task_id>.json。

`pytest -q --basetemp workspace/test-temp-20261006-4`：18 passed（6.51 秒），增加创建响应丢失/重启对账、停止未确认、容器身份不匹配、排队输入变化、3 MiB 产物封存重试、作业权限及暂停检查。ExecutorFixture 是故障夹具，其通过不等于真实容器执行。

当前 Docker Desktop 后端在 initializing Inference manager 时因 dockerInference Unix socket 无法访问而退出，引擎 API 超时。单个套接字改名备份也返回“系统无法访问此文件”；未重置 Docker 或删除全局文件。代码作业缺少执行器时保持明确阻塞，不在宿主机执行生成代码。

新增验收边界后：24 passed（6.07 秒），Ruff 全通过。覆盖显式验收/请求重放、要求修改、规范/候选冲突、篡改阻止发布、封存后终态前崩溃的恢复、活跃任务不能伪造验收。用户验收始终标记 user_attested，scientific_accuracy 为 not_assessed；不把用户点击转成科学精度证据。

随后原生框架 Hook/输出 Schema/检查点兼容测试加入：25 passed（5.78 秒），Ruff 全通过。扩充工具集后的 small `4e1a0a08-8402-42ff-99b9-87df202ef12e` 及首次 UI 实际任务因输出协议而失败，记录保留。明确 final_result 约束并设置最多三次有界输出纠正后，small `769f75fb-48f7-41f1-9dae-24b90aac3f6a` 六项检查全部通过（10.19 秒）。同轮 large `6e9baab7-7992-4c46-9a25-d4e6b94ee485` 全部通过（28.25 秒）。

真实模型浏览器任务复验通过（12.1 秒／总 16.2 秒）：登记 CSV、页面发起任务、独立数值核对、源文件不变、显式验收、封存清单与已完成状态。workspace/evidence/workbench-acceptance.png 与 workbench-completed.png 为视觉记录；Playwright 测试及 JSON 为行为证据。两个基础浏览器检查在同次前端构建下已通过，实际模型检查在协议修复后独立复验。

CLI run 已实际登记并读取 CSV、产生报告并返回 waiting_user。初次 PowerShell 重定向暴露中文编码不一致；CLI stdout 改为 UTF-8 后复验，workspace/evidence/cli-run-utf8-20261006.json 为有效中文 JSON。CLI 明确区分回合完成与验收成功，等待超时不自动取消后台作业。

模型身份冻结已接入新任务：真实 Ollama 权重 digest、precision、size 与生成参数进入任务快照，调用前逐次核对；旧任务没有身份记录时停止执行并提示新建任务。25 项回归测试通过（4.40 秒）；新增“权重变化时在请求发出前停止”与原生框架两项定向测试均通过（0.30 秒）。冻结后的 small `372c47cc-49b3-4341-b37f-0dbb806e8c69`（12.22 秒）和 large `309ed7f5-13b7-4403-adcc-87c6cb72b134`（27.25 秒）烟测均全部核对通过，实际身份快照在相应证据 JSON 中。

文档/知识/记忆批次：完整 pytest 30 passed（9.09 秒），Ruff 通过。PDF 配置使用 pypdf 的公开资源限制，文本页和空白页行为分别核对；DOCX 逻辑文本不伪造版面页码。索引含源 SHA-256 与引用，检索只返回任务授权、仍匹配来源字节的内容。模型只有记忆读取工具，写入/停用须由认证用户 API 发起；停用后不再检索，已有审计和任务历史保留。

真实文档烟测：small `97df8929-8ba1-43f9-8745-e934af83fdd7`（15.23 秒）、large `304b3205-7f39-42f6-a4ab-70c01de5f33f`（33.25 秒）的八项核对全通过，包含实际文档读取、索引、检索、事实、来源引用、报告和源文件不变。每次证据保存在 workspace/evidence/documents-<profile>-<task_id>.json。长期记忆浏览器验证通过（1.5 秒／总 2.8 秒），实际完成保存、刷新及停用。

上下文批次：原生 Harness 管理工具结果外置、按句柄读取、分层清理和滑窗。应用只保存编译输入的私有快照/SHA-256、顺序消息指纹、模型身份、源/动作/产物/作业引用与实测用量，不开发第二套历史管理器。规范/控制状态、当前回合原始请求逐次注入；动作读回限当前修订，封存文本读取有字节上限、跨 UTF-8 窗口不丢字符，不能读其他任务产物。38 项回归通过（6.84 秒），Ruff 通过；协议夹具证明外置内容完整、隔离、工具配对、权限/当前请求保留、超限停止和持续流式输出仍按总时限停止。这些协议夹具不替代真实模型试次。

预算首次试次 `72a3f0ee-dc9a-4399-b6e8-7cbc149b8890` / `de65d52b-a6f6-441b-8650-0cdbe8cdd7ef` 因重复计入历史中的旧指令副本而过早暂停；修正为实际消息文本、当前指令、工具 Schema 及协议余量。small `60fbffe3-d4fa-46f1-888a-45574dee77a6` 文档八项通过（18.25 秒）；large `5effca07-f792-406b-bc6f-67ef1969c690` 事实/引用正确，但数值是带单位字符串，严格核对未通过。明确 JSON 数值契约后 large `86347c0e-eb2e-4689-bd04-256de3c004e9` 八项通过（26.28 秒）。失败均保留。

真实长任务以21份软件夹具串联、两回合执行，不代表科学实测。small `bdc17357-92fe-4406-9e71-3a30e2504d5f` 已实际读21份并读回进度，但最新用户消息被滑窗移出后误判阶段，未检索/生成最终报告。原脚本只取100条事件的第一页；补充审计完整189条，纠正“没有读全/读回”的错误观察，原证据不删除。一次零输入用量不能算实测，现标记 unavailable。通过逐次重新注入当前原始请求修复阶段恢复。large `a189d751-3d6d-46c8-ab99-d3370ba0a4bd` 在第13次请求长时间无完整响应，558.48 秒手动暂停本测试，记录不通过；这暴露 HTTP 读取超时会被流式传输刷新，现新增冻结的单请求墙钟预算，并用持续流夹具证明停止。

修复后 small `27315e81-62a6-4b68-9e62-355d456e7518`（59.61 秒）顺序实际读取全部21份，无目录捷径，读回进度、检索最早来源、规范保持revision 1、压缩实际发生、所有实测用量在保守估算内，JSON事实和引用全部核对正确。32份私有输入快照独立核对 SHA-256、消息引用、工具配对、当前请求/只读策略/修订和预算均通过。报告只写局限，没有代号与数值，故整项严格不通过；原任务只明确要求报告说明局限，现已明确完整报告字段后复验，未放松检查。

明确报告契约后的 small `0918d2a6-43b0-43f4-b6cb-cca8953e07da`（77.08 秒）JSON事实、引用、中文报告、只读来源、完整21份顺序、进度读回、早期检索、实际压缩和用量均核对正确，33份输入快照独立验证全通过。原检查额外禁止任何目录列举，因一次授权目录列举标为失败；该条件过度定义了“不能猜序”的目的。新增更直接的严格顺序审计：21次来源读取必须分别发生在21个不同模型请求后，防止一批猜测路径替代串联读取。该审计全通过，原试次及失败条件仍保存；补充证据为 workspace/evidence/context-sequence-audit-<task_id>.json，快照证据为 context-snapshots-<task_id>.json。大模型的完整长任务质量仍未通过，不据小模型单次证据宣称两模型或阶段1验收成功。

## Roles / 角色批次

角色/配方批次：五个 YAML 长期配置随新任务冻结；仅 H2 根协调者按需委派，复用原生 SubAgents 循环。应用在委派前登记动作与 AssignmentRequest，创建独立依赖、输入/产物授权子集、角色与协调者能力交集、私有工具外置目录、输出命名空间和独立检查点。AssignmentResult 的角色种类、真实证据/产物/作业归属由代码核对；未结束作业、副作用或委派阻止终态/验收。子角色不能递归委派、修改父规范或提交父验收；质量结论作为建议保存，不自动合并或转换成科学精度证据。环境诊断不探测外部搜索地址。

权限/预算/恢复回归扩至53项（15.98秒），Ruff通过。新增覆盖两原生委派隔离、权限交集与越权无副作用、跨重启累计委派上限、父子共享请求预算、恢复进入新父回合后的计费、输入变化、虚构引用、未结束作业、独立作业产物目录、父/子检查点恢复不重复已确认写入、协调者预算、正在等待模型时的委派总时限，以及子作业停止后等待委派与父任务的取消终态对账。故障夹具仍不代表真实 Docker 执行。恢复测试暴露 SDK 不允许把旧原生 run ID 复用于带旧消息的新运行；现以 agent_runs 关联固定逻辑回合与每次新的原生运行，保留原消息 ID 与 Harness 检查点，不复制或改写原生历史。委派期限跨恢复保留，协调者本次运行也有冻结角色时限。Windows 私有快照和委派输出使用扩展路径 I/O；尚不宣称全部来源、文档和下载路径均已完成长路径验证。

真实角色试次：small `b4564e60-c9ff-4c78-b7a3-10af7b0a3708`（67.81秒）完成两个隔离角色与报告，但数据角色使用文本版本字符串代替完整来源对象，质量角色误将其判为通过，严格核对不通过。明确委派工具、输出字段和判据后 small `84202810-5088-408b-9e74-74dab70ae5b9`（8.53秒）添加不允许的 constraints 字段，在有界协议纠正后仍失败；现在工具参数描述包含严格 AssignmentRequest Schema，纠正消息列具体字段错误，不放松额外字段拒绝。large `6680410a-9181-49ab-9f7d-b8ff39366b1a`（132.08秒）全部12项核对通过，包含源文件不变、两角色真实读取、完整 SHA-256 来源对象、独立质量读回、独立输出和父级报告；子级私有输入没有父级内部标记。证据为 workspace/evidence/roles-<profile>-<task_id>.json。

更新后 small `d4478b44-91ef-40a7-8357-a1dc407b8b53`（43.42秒）事实、完整引用、质量读回和隔离通过，但父报告使用英文标题，严格检查未通过。同一任务首次中文补充回合（12.19秒）仍不通过。发现权威任务事实遗漏 output_preferences，现逐次注入并明确报告标题/正文遵守用户语言、保存后实际读回检查。第二次补充回合（12.25秒）全部13项核对通过，保留原两委派、原规范revision 1、新报告版本，原失败证据及产物不删除。通过证据为 workspace/evidence/roles-follow-up-small-d4478b44-91ef-40a7-8357-a1dc407b8b53-a359e9a23e9a43428a16778a568e890c.json；这不代表小模型初次单回合即可稳定成功。

前端构建与类型检查通过；新增角色浏览器检查1项通过（6.4秒），使用上述真实 large 记录核对工作方式选择、持久委派、真实 JSON 来源产物预览及桌面/手机布局无横向溢出。截图为 workspace/evidence/workbench-roles-desktop.png / workbench-roles-mobile.png。CLI增加 --harness 与三份角色/委派 JSON Schema。H0/H1/H2 配方定义与入口已实现，正式六组合实验、完整冲突/异常恢复交互和阶段1验收均未完成。

## Network and queued input / 网络与输入排队批次

新任务冻结 private/public 上下文分类、搜索提供者与地址。默认 private，每个查询/URL 的精确载荷、目的地、规范修订、输入版本、有效期及 SHA-256 形成审批记录；认证用户决策有 user_event，批准单次消费，重复请求 ID 不重复审批或排队。审批前不做 DNS 查询；每次 DNS、连接、发送、重定向以及保存响应前重新检查控制/版本/授权。public 必须由创建接口明确声明，不允许私密输入、记忆或环境读取；普通消息不能改变分类，子角色禁用网络。读取器固定验证后的数值 IP，TLS 保留原主机校验，拒绝私有/混合 DNS、越界和降级重定向，限制 4 跳、2 MiB、40 秒总时限并主动停止持续逐字节响应。保存原始字节、提取文本及 URL/SHA-256/快照产物引用，声明字符集有界解码，不执行网页脚本或子资源。

修复消息接口以提示词字符串判断重放的逻辑。用户消息、审批决策与继续回合在同一应用 SQLite 事务提交，独立 turn_requests 关联 request_id 与真实用户事件；这是执行队列，不新增模型历史管理器。正在运行时接收补充、完成后按顺序启动；暂停不会被消息或审批解除，取消/规范修订停用排队回合，服务重启先暂停并对账。恢复优先继续中断的根回合，再处理后续输入；未处理回合、待审批/执行请求阻止验收。

完整 `pytest -q --basetemp workspace/test-temp-20261006-72`：98 passed（15.63 秒），Ruff 全通过，前端类型/构建通过。网络协议与原生续跑夹具覆盖私密载荷审批前无 DNS、单次消费/请求重放、跨任务/过期/撤销/拒绝/来源与规范变化、解析后撤销无连接、私有/混合/多播地址拒绝、连接关闭、缓慢头部/正文总时限、公开任务私密工具硬拒绝、错误搜索页面不伪装空结果、原始快照字节与字符集；原生模型夹具证明审批继续回合读取原检查点且不重复已确认动作。夹具通过不替代真实外网与模型试次。

真实网页/审批试次 small `3a3afbab-390a-4721-b864-dd81cd34e03c`（23.06 秒）、large `e65057b1-ff4c-46bd-b959-4cb1f7ff9c1e`（57.25 秒）均全部11项核对通过：提出精确 Python 官方主页 URL、待审批返回、审批前无快照；审批后恰好一次读取、单次消费、实际原始字节 SHA-256、中文报告真实 URL/摘要/快照引用、报告读回、原生继续回合以及未自动验收。证据 `workspace/evidence/network-<model>-<task_id>.json`，独立运行库在 `workspace/network-runtime`。测试审批由脚本本地操作者发起，仅针对公开软件网页夹具，不包含真实秘密、不代表真实人对任意外发的同意、不构成科学精度证据。

真实待审批 small `c8395da6-f09b-45a6-bf05-8ba748a2a605` 与 `d94e9477-a01b-4a1a-a292-682a0d42fdfc` 分别经工作台拒绝，后续回合完成、修订仍为1、审批仍为 rejected，没有网页快照。浏览器首次1项通过（4.8秒／总5.8秒），调整详情滚动后再验1项通过（5.4秒／总8.3秒），保留审批详情桌面/手机截图，无横向溢出或页面错误；截图仅作视觉记录。

搜索支持 Bing RSS 与 SearXNG JSON 解析，仍未通过真实完整搜索。实际 Bing 探测 timed out，证据 `workspace/evidence/search-probe-5ba096cc1400473eac0cb7101829dc70.json`；诊断 `network-doctor-20261006.json` 同样返回 search=failed，Docker API 仍 unavailable。SearXNG 需 JSON 启用，私有/回环或自定义端口本地实例尚未支持，领域网页 PDF 暂未接入网络读取。这些缺项仍在原计划内，阶段1与V1不标记完成。

## Search and bounded reading / 搜索与有界读回

默认搜索改为 Baidu HTML，保留 Bing RSS/SearXNG 配置。新任务冻结连接器，旧任务保持原配置。普通结果只采用真实目的 URL，过滤广告/推荐/脚本；验证码或不识别页面不伪装空结果。Baidu/SearXNG 的页码进入精确请求授权，下一页仍须实际验证；页码提示不证明分页可用。网页先返回完整来源元数据和最多4000 UTF-8字节的正文，完整字节快照和提取正文仍封存为产物。

原生按行读回长单行 JSON 会越过模型预算，新增同一 LocalFileStore 的4000字节窗口读取，不另建工具结果存储或消息历史。`read_artifact` 从12000缩为4000字节，避免正常正文窗口又被外置一层；EOF明确提示停止。写入精确 UTF-8 字节，修复 Windows LF→CRLF 导致预览游标与保存文件失配。原生用量锚点加新增正文/指令/完整 Schema 的保守余量替代重新计费全部历史；没有有效锚点仍按字节估算，实际用量超过估算则停止。新增 citation_actions/view=citation 从当前授权修订的真实来源动作复制元数据，核对快照，不代替正文事实检查。

`doctor` 分开报告 startup_ready、general_prerequisites_ready 和 general_ready：仅默认模型前提决定 CLI 启动退出码，所有配置模型/搜索/受限执行器决定前提列表；工具、UI、恢复和产品验收不是探测能证明的，general_ready=false。实际诊断 `workspace/evidence/search-doctor-20261006-v2.json`：两个模型已安装、搜索7条可用，Docker unavailable，通用前提及产品验收未通过。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-88`：121 passed（25.49秒），Ruff/diff检查通过。新增协议/边界覆盖完整来源复制、跨任务/旧修订/角色拒绝、篡改快照、末次动作预算不重复计费、原生单行结果有界读回、直接产物分页不再外置、中文/emoji/换行逐字节重建、搜索页码授权以及探测不误称产品就绪。

真实 UI 搜索报告：small `8741c552-15c1-4537-a90c-6b310fc3ce25`（57.5秒、16模型请求）、large `8d2fb035-7362-4016-b547-6a5433813d85`（56.1秒、10请求），Playwright 2 passed（1.9分钟）。核对通过：工作台创建公开 H1 任务、实际冻结模型/provider、真实搜索后逐字选择官方结果链接、读取原文、两份原始快照哈希、中文报告/完整引用、报告读回、执行记录、刷新后的任务与文件。均 waiting_user，没有自动验收；截图已检查。UI 检查核对报告字段/来源，不能替代四个结构化事实类别的严格评分，更不能替代代码执行/依赖/恢复复合场景。

所有失败保留：此前 small `911774c3-cb07-478b-8594-8b6e3bbadc7a`、large `5eb9a52d-0253-4b85-9921-1e88a5d19b6a` 因嵌套外置窗口反复读取，达到30请求后暂停。更早的真实试次暴露错误类别值、来源链接改写、引用遗漏、大模型重复检查和过度预算停止；对应文件仍在 `workspace/evidence/search-*.json`。

最新严格事实试次 small `9cfb714d-3d9d-4f6f-989b-a42db65d0c5b`（9.70秒）、large `cbc814f9-71ee-41cf-b668-4e41e2f71a58`（11.05秒）均遇到搜索重定向超出授权范围，搜索/事实/报告评分未通过。不是把 UI 单次成功改成稳定性保证。搜索批次时 max_unchanged_decisions 仍只有配置，后续保护实现与验证见下节。

Evidence: `workspace/evidence/ui-search-<model>-<task_id>.json`, `search-<model>-<task_id>.json`, `workbench-search-<model>.png`. General-stage acceptance remains incomplete; no code execution or scientific accuracy claim is made by these software-search trials.

## Durable no-progress protection / 持久无进展保护

基线升为1.3，元数据库增量迁移为2：在应用存储记录观察指纹/决策/模型请求引用、逻辑回合/角色、冻结上限和连续计数。原生 Harness 继续拥有工具循环、历史和检查点。新观察重置连续计数；失败、无效窗口、空末尾及已见重复/循环观察累积。正规分页游标、来源内容与作业状态变化保留；包装动作ID、时间戳、快照ID和嵌套检查不制造新进展。排序后的JSON指纹避免首次结果与数据库重放因键顺序不同被误判为新事实。

新模型决策重复同一调用ID仍计无进展，但已确认动作不会重复执行；恢复重放同一观察不再次计数。达到上限后先持久提交观察，模型请求与工具执行入口拒绝继续，并以NO_PROGRESS暂停；已确认产物、动作与attempt保持可对账。无纠正输入的resume返回422且不增加模型请求。认证纠正消息仍在暂停时排队，明确resume后旧回合记为stopped，新回合处理纠正；旧计数、规范修订和任务累计预算保留。观察新颖性只是工程保护信号，不证明任意循环可检测或任务语义正确。

完整 `pytest -q --basetemp workspace/test-temp-20261006-97`：128 passed（21.18秒）；Ruff/diff检查通过，前端类型检查通过。新增7例覆盖跨数据库重开、冻结上限、循环读回、失败变化、EOF、重放去重/冲突、正规分页/作业状态、角色/修订隔离、来源字段保留、原生重复写与重复调用ID、停止后的无额外请求和认证纠正继续。原生单行字节存储仍逐字保留。

真实故障注入 small `7b8b4ee0-e351-4e32-8e1a-3eed55660104`（13.00秒）、large `953d2ed1-d15b-45dd-9f17-688c69fb08be`（29.67秒）：均恰好4次真实只读调用，观察计数0/1/2/3，NO_PROGRESS暂停，源哈希不变，无自动验收。证据 `workspace/evidence/progress-<model>-<task_id>.json`。这是刻意重复读取的软件保护案例，不是普通能力成功率试次。

真实UI纠正恢复检查：1 passed / 1 failed（6.0分钟）。large同一任务通过（35.3秒）：先拒绝直接恢复、无额外模型请求，纠正排队仍保持暂停、明确恢复才继续；规范revision仍1，累计请求4→9、动作4→11，旧停止记录保留；JSON准确为3/37.5/12.5、中文报告和两个文件真实读回，未调用代码/委派/网络，刷新及源保护通过。截图已检查，证据 `ui-progress-large-<task_id>.json`。

small恢复失败：尽管纠正请求明确不要执行代码，模型提交两个作业，均因DEPENDENCY_MISSING停留queued，container_id=null；UI观察320秒后超时，未把它当终态或成功。保存 `progress-small-recovery-before-cancel-<task_id>.json` 后，测试操作者取消该任务；实际复查任务cancelled、两作业cancelled，未启动容器。失败保留，不能将大模型通过外推到小模型；明确工具范围的服务端约束和小模型恢复质量继续列为缺项。新版UI测试也在观察超时时保存当前状态/事件，且核对禁用工具未调用。

English: 128 regression tests pass; both real models trigger the persistent repeated-observation guard. Only the large-model UI correction trial passes. The small-model correction trial submitted forbidden-by-request code jobs, which remained unstarted and were explicitly cancelled after evidence capture. General-stage acceptance remains incomplete.

## Task scope and corrective recovery / 任务工具范围与纠正恢复

基线1.4增加真实 TaskConstraints 边界，允许能力必须是已注册、无重复的列表，拒绝未知约束。约束来自认证创建/规范修订，存入已有 specs 并从该权威记录投影到 UI；未新增权限服务或第二份运行状态。省略/null继承配置，空列表禁用所有注册动作。未提交 constraints 的修订保留前一修订范围，响应丢失重试根据不可变原修订重新解析，普通消息不能扩权。原生工具发现、实际执行、委派交集和作业提交/启动共享同一检查；原生溢出读取只观察当前运行已有结果。完整通用能力默认保留。

UI 提供通用能力、文件与报告、仅回答、自定义选择，显示当前范围，并可明确修订目标/输入/范围、暂停后对账恢复；运行中补充消息可以排队。完整回归 `pytest -q --basetemp workspace/test-temp-20261006-101`：136 passed（38.01秒），Ruff/diff通过；前端正式构建通过。新增8例验证非法能力/字段、执行前拒绝、重开数据库、原生模型工具展示、委派交集、消息不能扩权、修订/重放、直接代码提交拒绝和排队作业取消。

显式文件范围下，两模型重新通过刻意重复读取保护：small `3b8e7f01-d375-4935-86af-9748e80ecd0a`，large `22790df4-4e3c-4314-89fa-8ebf1fc1c190`，各4次真实读取并暂停。浏览器纠正恢复2 passed（42.5秒）：small17.9秒、large23.4秒；均同一revision1保留原停止和累计预算，JSON准确为3/37.5/12.5，中文报告/两文件实际读回，源保护与重载通过，无代码、委派或网络动作。small累计11请求/10动作，large9请求/11动作。此前未设置显式范围的小模型失败仍保留，不把本次文件范围通过外推为全部通用能力质量通过。证据 `workspace/evidence/ui-progress-<model>-<task_id>.json`，截图已检查。

最终回归 `pytest -q --basetemp workspace/test-temp-20261006-103`：140 passed（29.61秒）；Ruff/diff通过。额外覆盖修订时保留输出偏好/指标请求/冻结字段，以及缓存控制回执不能取消后来回合、恢复重传不能新发起模型回合。暂停/修订/取消仅停止本次真实变更时捕获的执行令牌；恢复仅在本次真实变更后调度。旧请求重放只返回原始回执。

最终服务上的范围 UI：2 passed（1.2分钟），small `8ce4f59d-5f62-4030-85e2-73a2693b6fd9`（21.9秒，10请求/8动作）、large `01498d03-a4bb-4c5f-a2b5-f3c8df77fdc0`（49.2秒，7请求/9动作）。从“仅回答”创建开始，首回合零注册动作且回答4；经真实 UI 将目标/CSV输入/工具范围修订为revision2，保持模型配置、暂停并重载，显式恢复后交付准确3/37.5/12.5的JSON与中文报告、两文件真实读回、无代码/网络/委派/环境/记忆动作，保持待确认。再次实际POST原恢复请求返回原回执，任务状态及事件不变，没有新模型回合。证据 `ui-scope-<model>-<task_id>.json`，修改对话框/报告截图已检查。

范围 UI 测试的早期失败均保留：首次API登记遗漏Origin并与会话初始化竞争，未检查失败响应而等待不存在的来源；后一次从不同cwd启动时相对夹具路径错误；修正后又因未等待恢复HTTP回执而提前读取旧paused状态。后者两真实任务在测试离开后实际完成，失败JSON不回写成通过。脚本改为显式会话/Origin、登记响应检查、绝对夹具/证据路径、每试次唯一来源、等待恢复回执及失败后快照；原始失败报告/截图和任务记录均保留。上述测试脚本失败不能记作产品修订成功，也不把后续通过外推为通用阶段完成。

English: 140 regressions, both file-scoped corrective trials and both final real-model scope/amendment UI trials pass. Cached control receipts cannot affect newer runs or create another model turn. Default general capabilities remain available; earlier unrestricted model failures and test setup/race failures remain recorded. General-stage acceptance remains incomplete.

## Real restricted executor / 真实受限执行器

本次 Docker Desktop `desktop start --detach` 返回0，但后端实际再次在 dockerInference 套接字访问处退出，服务端 version 超时；`desktop stop --timeout 20` 返回1，未伪称停止完成，也未重置现有数据。改用项目独立发行版 `forestry-uav-agent-runtime`，实际导入路径 `workspace/isolated-executor/wsl`；Ubuntu 24.04.5 WSL 镜像388,975,696字节，SHA-256 `bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e`，官方签名及固定指纹验证通过。导入/安装/拉取/记录锁之前均重新验证来源、镜像与现有发行版归属。同名外部发行版和未登记非空目录拒绝覆盖。

guest Docker29.1.3/containerd2.2.1/runc1.3.4 实际服务端可用，24CPU/约124.45GiB guest RAM；这是当前开发环境，不是低资源验收。Docker Hub 与 Google 官方缓存实际pull因连接超时失败并返回1；Docker官方 Amazon ECR Public 路径成功。Python3.12.15 的实际 image ID及RepoDigest均为 `sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f`，WSL示例与测试配置采用该不可变引用。完整包/daemon/rootfs/image清单在 `workspace/isolated-executor/runtime-lock.json`，安装脚本固定此次解析的11个包版本；仓库缺少锁版本时失败，不静默升级。可信运行时下载不等于 Agent 搜索功能，后者按用户指示暂缓。

Docker适配器复用现有Jobs：显式Unix套接字、不新增TCP服务，参数数组与env -i，不使用shell；仅转换本地盘符的授权挂载，拒绝UNC/相对路径；私有客户端配置，不继承宿主DOCKER_AUTH_CONFIG/代理/WSLENV/合成秘密。doctor使用同一传输，探测仍不能证明通用产品验收。模型wait_job终态返回实际有界stdout/stderr，并在独立线程查询，不阻塞UI事件循环；job/角色权限与容器ID仍须匹配。

`scripts/smoke_executor.py` 实际3案例全部核对通过，证据 `executor-real-e9ad573c-133e-483f-8e48-edde14e9aff2.json`。计算task `3808603d-2603-4475-a3ce-a262e08cd16c`：3/37.5/12.5精确，源哈希不变，真实写源及写根目录失败，uid65534，网络连接失败、宿主路径/合成秘密不可见，network=none/root只读/cap_drop=ALL/PIDs128/6GiB内存与swap/4CPU实际配置相符，容器身份/日志/不可变结果核对通过。重建应用对象与存储后观察同一真实容器并回收结果，明确 `real_process_restart=false`，不能记为AC-07进程重启通过。取消task `f330b40a-ffb4-448c-bb40-72e962c883f5` 及超时task `1b0e7c93-8389-4c2f-90fa-e4e3f545d3cf`，同一容器实际Running=false、对应终态正确，无产物。

真实代码调试工作台：1 passed / 1 failed（2.4分钟）。large `99e23ff1-219e-42ed-9ad9-fbabfbff3fc3`（约1.5分钟、9请求/11动作）通过：真实原脚本NameError作业失败、wait_job得到traceback、修复后新作业成功，容器实际生成3/37.5/12.5 JSON，保存修复代码与中文报告并实际读回结果/报告；源文件不变、无网络/委派/环境/记忆动作，刷新后仍待确认，截图已检查。small `411b9243-63fa-45c6-8a01-653c1bb42c55`（50.0秒）实际失败→修复→正确计算/报告均存在，但漏读报告，严格UI验收失败，不改写为通过。每个作业的真实退出码和日志、全部事件及产物保留在 `ui-executor-<model>-<task_id>.json`；小模型另有 `ui-executor-failure-<task_id>.json`。

初次执行器烟测脚本误用未注册job.query导致任务400，修正为job.observe；没有启动该失败请求的容器。初次UI脚本误把内部restricted_code键当作可见标签，两模型在创建任务前失败，截图/报告保留 `ui-executor-test-results-20261006`。修正真实UI标签后第二次运行结果如上，保留 `ui-executor-test-results-20261006-v2` 与 `ui-executor-tests-20261006-v2.json`。当前WSL本身的UTF-16代理提示仍可能混入stderr；作业终态以真实容器退出码为准，不把提示当计算失败。

最终完整回归 `pytest -q --basetemp workspace/test-temp-20261006-109`：151 passed（25.01秒），Ruff/diff通过。新增覆盖WSL无shell参数、中文/空格盘符路径、非法分发名/UNC/相对路径/TCP端点拒绝、宿主凭证/代理隔离、输出边界、doctor前提逻辑以及失败诊断/越任务/替换容器拒绝。早期doctor夹具将绝对Docker可执行路径误判为成功、随后patch subprocess影响platform探测导致失败；已改为分别模拟Docker适配和系统探测，失败不作为运行时证据。前端产品未改动，无需重建静态资源。

English: The independent signed WSL runtime and immutable Python image are available. Real restricted compute, cancellation and timeout pass; store/object reopening is explicitly not process restart. The large-model debugging UI trial passes, while the small-model trial fails for omitted report readback despite real repair and correct computation. All failed setup/trials are preserved. 151 regressions pass. Dependencies, actual service-process recovery and complete local general acceptance remain pending; Agent network-search work is user-deferred.

## Real process recovery / 真实进程恢复

新增 `scripts/smoke_recovery.py`，为每个模型使用独立工作目录、数据库和 localhost 服务。核对新进程的命令、配置绝对路径、启动时间及自身进程树后，只有真实容器输出 RESTART_READY 才中断自己拥有的服务；确认原进程退出、同一容器仍运行，再启动新进程、观察 SERVICE_RESTARTED 暂停并显式恢复。原有8765/8766/8767服务没有停止或切换。生成代码仍在原受限容器中，测试驱动的真实终止不等于 mock。

初次 small `7b593322-bd82-4d4a-9658-7860a69afbfe` 已通过13/14检查，报告未实际读回而失败；large `8380ff83-7ed1-4c99-b627-76b6c7828e21` 实际恢复并结束，但验证脚本5秒HTTP读预算在收集日志时超时，整次记录仍失败，未回写为通过。日志端点包括inspect及logs两个各自有界的Docker调用，脚本现给出70秒HTTP读预算并记录失败阶段；超时不启动替代服务。读回检查改为实际确认的artifact.read回执，失败动作不能满足读回。

应用曾经历恢复但模型只看到已清除的阻塞，因此补充既有 `run.reconciled` 事件的任务修订/逻辑回合/原状态/阻塞原因，恢复后重新注入同一回合的权威事实。H0/H1/H2共享控制记录，子角色没有父记录，其他任务/修订/回合及没有回合字段的旧事件均排除。该事件不单独证明进程崩溃、容器存活或结果正确，也不授予权限。无需迁移数据库或第二套历史。

复验 `scripts/smoke_recovery.py --models small large` 返回0，两模型各14检查通过：small `07fa0365-22ca-4d46-9887-e0f216494ba2`、large `cb1a43aa-9f1c-4b25-b1f3-c3a2e79d8fb1`。均7累计模型请求/5注册动作，保留revision1；新服务进程身份、容器中断后继续运行/保持同一ID、一次代码提交/作业/完成标记、待确认、3/37.5/12.5精确、源哈希、报告存在和结果/报告真实读回、累计预算均通过。small服务PID456676→463408；large完整身份见证据。证据 `recovery-real-small-a10ef305-cd4b-4851-9494-19590811ac0c.json` 与 `recovery-real-large-a30d1b49-c526-4288-b9af-e623efb985f8.json`。

独立只读后审计，两模型各6检查通过：实际后续模型输入含同一恢复event_id，输入快照SHA一致、同一逻辑回合/两个不同原生run_id、报告SHA正确、脚本拥有的两次服务均已退出。审计 `recovery-input-audit-<model>-<trial_id>.json` 保存完整模型报告，未修改原始试次。报告语义仍有限：small一处称“未观察到重启中断”又称恢复正常；large承认应用SERVICE_RESTARTED记录，但将测试驱动的真实终止描述成框架“模拟”。14项通过不代表报告全部陈述正确，此质量问题仍保留。来源为3行合成CSV，不证明科学精度。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-112`：154 passed（30.59秒），Ruff/diff通过。新增三个原生H0/H1/H2恢复记录测试，覆盖恢复清除blocker后的重新注入、其他任务/修订/回合排除；角色隔离原生试次另检查父恢复记录不进入子角色。初次夹具错误假定enable_agent=false也创建turn，三项失败；改为显式建立运行中回合后通过，未作为真实恢复证据。

加入父恢复记录隔离断言后的最终完整回归 `pytest -q --basetemp workspace/test-temp-20261006-113`：154 passed（36.55秒），Ruff/diff及恢复脚本语法检查通过。脚本另保证首次运行创建证据目录、health/session独立2秒读预算、用PID与创建时间共同识别新进程、清理未确认时保存失败证据。产品前端未改动，无需重建；脚本这些生命周期收尾不改变上述真实试次输入和计算路径，实际试次记录保持原样。

English: Both models pass 14 checks following real interruption of an owned service while an already confirmed container job is running. A separate six-check audit verifies actual prepared inputs, same logical turn/fresh native runs, hashes and terminated owned processes. Original failures remain. Scoped application recovery facts are reinjected without leaking parent facts to roles or other turns. Model reports retain ambiguous interruption statements; this is limited recovery evidence, not complete report, AC-07, UI or general-stage acceptance. 154 regressions pass; search remains user-deferred.

## Scoped offline dependencies / 离线范围依赖

基线1.7新增 environment.prepare，复用现有动作/attempt、Jobs、资源授权、角色交集及不可变产物；不另建安装循环或第二套作业数据库。pip 在断网、非特权容器中使用固定wheel、完整传递解析、only-binary/no-index/require-hashes；生成 environment.zip 与 environment-lock.json。复用绑定当前任务/修订、实际准备作业、环境哈希和实际镜像ID；提交/启动/恢复核对身份，子角色须显式获得环境归档授权。准备最多32个wheel/128MiB；环境归档最多128MiB，解压最多30,000文件/512MiB，锁文件64KiB；拒绝越界、重复和符号链接。包安装及解压均不在Windows宿主执行。详见 DEPENDENCIES。

可信开发者夹具下载依据固定版本的官方PyPI JSON；直接下载实际超时失败，原部分文件及元数据保留。TUNA HTTPS镜像下载成功，全部大小/官方SHA一致；锁文件 `fixture-lock-f098f689-e309-4c78-924d-c1bc2a731239.json` 记录NumPy2.2.6的Linux/Windows wheel、python-dateutil2.9.0.post0及six1.17.0。该记录的url字段是官方原始URL，download_source=tuna；实际镜像URL由脚本替换固定前缀形成，不称独立签名。后续脚本分别记录实际下载URL与已有文件核对，缓存核对不能伪称重新下载。此下载不是Agent工具，不代表Agent依赖获取通过，也不属于暂缓的Agent搜索开发。

基础真实试次失败均保留：`faaca376-b229-48a7-9b28-f411fb7578b5` 在NTFS输出挂载内安装出现INSTALLER临时文件/权限错误；改为Linux `/tmp` 准备后，`ee59b343-fb00-4b34-a178-6b31f130390a` 安装和首次复用成功，第二次NTFS解压达到原60秒预算；转为Linux `/environment` tmpfs后，`139bc4a2-f64a-4ee8-a1d6-9a763e6d17ba` 因默认noexec不能映射NumPy原生扩展而失败。最终只为专用环境tmpfs明确exec/nosuid/nodev，代码作业普通 `/tmp` 仍64MiB/noexec；准备tmpfs及环境tmpfs各1GiB，计入原6GiB容器内存预算，不降低其余隔离，也不延长基础60秒计算预算。

`smoke_dependencies.py` 最终试次 `14ce03b2-d599-4175-878b-4d7f4c7af5e1`、task `eb87eea8-faca-4b7a-a0c6-aef7bbcb6aad` 返回0，15检查全部通过：实际缺NumPy、实际缺传递six、Windows wheel平台不兼容、真实成功安装、准确版本锁、两次同一环境/哈希复用、实际镜像一致、两次执行成功、3/37.5/12.5及日期精确、uid65534、实际网络失败、所有容器断网/只读根/cap-drop、CSV与wheel不变、宿主包清单不变。环境可在新应用对象/存储中再次解析；明确不是进程重启试次。证据 `dependencies-real-<trial_id>.json`。软件夹具不证明科学精度。

两模型浏览器检查尚未通过。第一轮在基础试次结束前启动，测试后端缺少exec选项：small `cae30e6f-9c55-4179-bd8f-a3f630509f22`、large `7fa408c9-29d8-4b71-bffd-fb5046593e69` 实际失败并达到30请求保护；这是测试启动过早，记录保留。确认专用8768测试服务无活跃任务、非终态作业及未确认动作，并核对进程命令/配置后重载，不停止8765/8766/8767。第二轮small `4f811977-7b73-411a-b587-f7d74739bdd4` 实际安装成功，但反复把锁文件ID当环境归档而到请求上限；large `ee4fb6b2-fdb5-45aa-9fe8-158155c21a36` 首次计算成功，重复嵌套查动作后耗尽上下文。

诊断发现终态环境结果重复两份完整配方，6014字节刚好触发原生6000字节外置，环境ID位于后部。现在原生等待结果直接保留环境ID、锁文件ID、哈希/镜像与安装版本，省略重复完整配方；持久作业/API/锁文件证据保留，不更换Harness外置机制。提示明确锁文件不能当环境，不按列表顺序猜ID，观察作业使用wait_job。第三轮两模型均真实安装并完成首次计算，但small `e1ca64d7-3fe0-449b-8f58-4f8b5bd84155`（14请求/12动作）误向read_artifact传额外limit，并使用来源资源ID，原生协议保护暂停；large `b72d5d36-b5f8-4845-b17e-7ff925aafcf7`（10请求）嵌套查阅旧动作后触发上下文保护。未提高预算、容许额外字段、删除读回/复用检查或改写为通过。

独立只读后审计，两模型首次JSON的SHA、完整数值/日期/实际三个版本，以及源文件不变各3项通过，补充证据 `ui-dependencies-result-audit-<task_id>.json`；不改变完整UI失败。三轮Playwright报告分别保留 `ui-dependencies-tests-20261006-v1/v2/v3.json`，原失败截图和任务JSON保留。第三轮截图已检查，任务实际显示暂停。最新专用服务8768加载修复API；其他既有服务尚未重载此批API。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-121`：165 passed（31.97秒），Ruff/diff通过；前端正式构建通过。新增离线权限、未知输入、源码/危险名称、环境伪造/跨任务/旧修订/篡改、镜像变化执行前停止、子角色环境授权、完整hash/传递解析、JSON字面量及有界原生环境身份测试。测试wheel和Docker夹具不冒充实际安装；实际运行由上述独立容器证据支持。脚本在观察过期或阻塞后停止后续提交，对本试次已提交作业通过原有Jobs申请取消并记录终态确认；无法确认时整次仍失败，不盲目重跑。

English: Baseline1.7 adds scoped offline wheel preparation and immutable reuse through existing Jobs and permissions. The real core trial passes all15 checks; earlier NTFS/noexec failures remain preserved. Both final model UI trials perform real installation and one correct computation but remain failed for small-model tool arguments and large-model recursive inspection/context exhaustion. Separate read-only audits confirm exact first results, hashes and unchanged sources. 165 regressions, Ruff/diff and production frontend build pass. Agent acquisition/source builds and full local general acceptance remain incomplete; search stays user-deferred.

## Confirmed dependency facts and schema accounting / 依赖事实与Schema计费（1.8）

2026-10-07收尾，试次文件保留20261006运行标识。H1/H2根指令从现有产物/Jobs记录取得最近24个名称/ID和当前修订最近8个作业的有界身份、状态、环境与结果引用；不复制代码/环境清单/文件正文、不新增Docker探测。失败/超时诊断只复制已确认终态job.observe回执的stderr末尾512 UTF-8字节，保留source_action_id与截断标记，视为不可信正文。记录状态不等于新的观察或验收。H0不增加辅助事实，子角色不接收父产物/作业元数据。初始回合请求与objective完全一致时以全文objective和显式标志表示，不重复注入；不同后续请求仍完整保留。

上下文估算沿用原生正用量锚点；只有对应响应内容摘要、实测用量及同task/turn/revision/assignment的已覆盖请求清单一致，且当前工具Schema摘要相同，才避免再次计费已由锚点覆盖的Schema。变化的工具/响应、其他任务/回合/修订、缺用量及无响应摘要的旧记录均保留完整Schema余量。响应摘要仅排除框架后加的run_id/conversation_id/metadata/workspace_ref，不排除实际响应parts或provider用量。实际用量超过估算仍停止，不创建第二套历史。原生分层压缩目标从可用输入预算1/2改为3/4（32K/4K配置为21,504），ClearToolResults、滑窗、外置及硬上下文/输出上限继续保留；这是保留阈值调整，尚不证明完整长任务质量或小模型稳定性。

UI第4轮1通过/1失败，原报告 `ui-dependencies-tests-20261006-v4.json` 保留。small `6569a17a-0a68-42da-a276-2b655b76e8f2` 曾通过原流程检查，但独立读回发现成功环境被列在失败过程，真实environment.prepare动作ID被当成作业ID。所有UUID已登记仍不足以证明引用类型正确；补充报告/引用审计明确完整报告质量失败，不回写原流程JSON。large `66178daf-9d45-4ec9-a58d-23c78fbe7d10` 已实际完成两次计算及读回，查旧观察后上下文估算耗尽。第5轮两模型均因重复旧观察触发NO_PROGRESS而失败（small `3e4dc56c-3bd6-4f97-b9fe-f0c82d5c30a4`、large `3103b601-669b-4d9b-9d7b-0f27616503ff`）；Schema新计费实际生效，仍不将质量失败改为通过。

提示现明确resource_id/artifact_id/job_id/action_id不可混用，read_artifact没有limit；仅实际来源读取返回citation时才用citation_actions，代码/作业通常没有来源citation。第6轮1通过/1失败：small `6493f5cd-1305-4f4f-b985-529da5428b7b` 重复读锁/首次结果后NO_PROGRESS；large `538470d1-a04d-44f7-93a8-a870adafbc2a` 通过加强的完整UI及独立报告阅读。检查新增所有UUID须属于真实任务证据、作业标签只能对应真实job_id、五个实际作业均须引用；其余两次失败、唯一环境/两次复用、精确数值/版本、实际三份读回和源保护要求不删除。正则检查只覆盖标识，报告语义另外审查。

最终第7轮1通过/1失败，总3.9分钟，`ui-dependencies-tests-20261006-v7.json`。small `3923952f-416e-47c4-a1cc-bfb1b509fa38` 已产生正确命名的两个JSON，又重复计算/安装，最终10个作业（7成功/3失败）和30请求后由原调用上限暂停；报告错把artifact.read动作ID当缺包作业ID、将准备环境写成生成计算结果、遗漏重复执行，并声称只读CSV无法核验，报告也未实际读回。独立报告审计明确失败。不能因产物存在或数值正确宣称该任务通过。

large `979c4090-c284-4f7e-ba14-f7438e251d31` 15请求、16注册动作，保持revision1/waiting_user。实际缺包失败 `9dd5a10d-9a33-415a-a1dd-247fc80df355`、平台失败 `92cfd3fe-9d47-41f2-b859-af42686f4d59`、成功准备 `c0d32bc0-4c03-4b62-bebd-57cac149870e`、计算 `c072cfb9-7f09-4ffe-9e17-32e2a4aa1a33` 和 `3342c127-9b4b-49d2-8228-dcab28aa0e43` 均准确。复用同一 `7c70fdb1-e441-4d74-8f3b-c5581a506873` 环境/哈希/镜像，两个JSON均3/37.5/12.5、日期2026-10-06及三个固定版本；报告 `22aadec9-2bae-4bae-807e-4599f5682e8d` 的SHA、全部五个真实作业引用、顺序/状态/来源/环境/产品/局限另行核对一致。三份产物实际读回，页面刷新仍待确认，无页面错误。完整UI通过不是用户验收，也不证明科学精度。

独立只读审计第7轮全部45份私有prepared输入（small30、large15）：原文件SHA、实际SchemaSHA、严格同回合/修订/角色响应回执绑定、正用量且估算覆盖、初始请求全文一次、有界任务归属与512字节已确认诊断均通过；43份使用bound_measured_schema、2份初始输入全额Schema。证据 `ui-dependencies-context-audit-corrected-<task_id>.json`；首次审计错误以未转义原文搜索JSON字符串，导致canonical_request检查失败，原JSON保留，修正为JSON编码后的全文比对后另存审计，非产品失败。独立报告审计 `ui-dependencies-report-audit-v7-<task_id>.json` 保存完整正文与质量结论。输入证据通过不代表small质量通过。所有历次试次和截图保留；最终large截图已视觉检查，报告预览可读；来源选择器累积同名夹具使页面很长，完整UI成熟度仍需处理。

完整回归 `pytest -q --basetemp workspace/test-temp-20261006-131`：169 passed（34.36秒），最终Ruff/diff通过。新增原生三模式有界元数据/诊断、真实Schema收据隔离及父元数据不进入子角色的检查。此前恢复测试虽参数化H0/H1/H2却遗漏make_agent快照，实际均用H1；现传入冻结快照后三种真实模式通过，旧测试不夸称三模式覆盖。新增测试初期动作外键及夹具参数错误已修复，夹具不是实际执行器证据。产品前端源码未改动，无需重新打包；专用8768加载最终API，其他已有服务未重载。最终只读DB检查无active任务、非终态作业或未确认动作，未停止既有服务。

English: Baseline1.8 adds bounded root-only artifact/job facts, confirmed diagnostic tails and schema accounting bound to the actual measured native response and exact scope/schema. The native compaction target changes from half to three quarters of available input budget; hard limits remain. The strengthened final dependency UI trial is one pass/one failure: large passes actual flow and independent report review; small repeats execution/installation, exhausts30 requests and mislabels a read-action ID as a job. All45 prepared inputs pass separate provenance/usage audits, not task acceptance. Original failures remain, including an audit JSON-escaping mistake corrected in a separate file.169 regressions and Ruff/diff pass. Complete local general quality, dependency acquisition/source builds and recovery/UI gates remain pending; search is user-deferred.

## Native planning and UI / 原生计划与工作台（1.9）

2026-10-07 增加task.plan（19项注册能力中的一项）及原生write_plan/read_plan，只在H1/H2与任务/冻结角色交集允许时开放。复用Planning/PlanItem/SqlitePlanStore；native_plan_items表位于同一SQLite，按任务/修订/逻辑回合/委派隔离。16步/8192字节及字段上限，最多一步执行中。范围和控制检查跨原生整表提交持有单服务应用锁，未证明多进程同库控制。动作与预算沿用现有机制；自报计划不算新增执行证据或重置无进展。API/UI标记model_proposed/not_assessed，不改变验收或权威状态。没有另建Agent循环、历史或计划调度器，详见PLANNING。

初始协议测试2失败：已解析PlanItem对象不能直接写JSON动作参数。修复为记录call.args_as_dict()原始参数，仍由原生类型化handler执行，随后6项通过。再加入原生压缩及子角色计划写入隔离，共8项通过。PlanItem导出原生类型，工具/提交边界另外施加上限。

原始v8两模型UI均记录失败，完整文件`workspace/evidence/ui-dependencies-tests-20261007-v8.json`保留。small `2c91d9fd-a1da-4793-910e-ec3936e466bd`：22请求/22动作、7作业，仅result-1，NO_PROGRESS暂停，未完成第二结果/报告。large `bdfabe01-a90d-47be-8ee7-c23c969bd42b`：15请求/17动作、五真实作业、一个环境、两次计算及三份文件实际读回。其测试正则把“作业复用同一environment_artifact_id”的环境ID误作作业ID；改为显式作业标签，另核对仍能发现历史v7小模型误标读取动作ID。对同一任务的只读报告复核通过（13.5秒），0新模型请求/作业，补充证据`ui-dependencies-same-task-recheck-<task_id>.json`。原失败不覆盖，复核不是新试次或完整验收；其计划仍过期，仅两步完成。

v8的37份输入摘要、正用量覆盖、Schema绑定及有界事实审计通过；单独提醒审计发现小模型14次、大模型10次缺提醒。当前Planning在wrap阶段注入，而后续原生压缩器从ctx.messages重建请求，移除临时部分。仅调整能力列表顺序的首次修复仍失败，新增压缩测试发现此问题。最终兼容适配在压缩后的before-model链调用原生提醒方法，预算/快照随后执行，不积累历史。原失败审计`native-planning-request-audit-<v8 task_id>.json`保留。

UI加入计划页签和自报状态说明；当前任务只显示自身5个授权输入，不再显示全库80项历史登记。SSE重放原先逐事件并发刷新，产生请求堆积，API有7步而界面为空。修复为串行合并刷新、防止切换任务后旧响应覆盖；旧API计划路由404兼容。最终前端打包通过（2分6秒），真实同一任务桌面/390px手机的计划、来源、刷新、无页面错误/横向溢出检查1通过（1.9秒）。证据`ui-plan-recheck-tests-20261007-v8-r4.json`、`ui-planning-<task_id>.json`及两张实际查看的workbench-plan截图。

复核首次连接失败时，专用8768的进程与原会话均不存在，原因未确认，不称为已证明产品崩溃。确认缺失后重启，计划可读且没有重复执行。r2未先建立API会话/UI未加载，完整JSON备份因错误工作目录失败；摘要`ui-plan-recheck-summary-20261007-v8-r2.json`如实记载。r3报告复核通过/计划UI失败，完整JSON与错误上下文保留；r4修复后计划UI通过。加载最终后端前，重新核对专用服务全空闲和PID/配置/创建时间后才停止PID19656，启动PID26108；其他服务未重载。

最终v9依赖UI保留原数值/环境/五作业/报告/只读检查，并增加已核验工作结束后计划更新完成的要求。结果**2失败/1只读复核跳过**，并非仅新门槛导致：

- small `4d61efd4-b9be-4a71-a552-e71119f79edc`：25请求/25动作、8作业，正确环境和result-1存在；误读二进制环境、后续计算失败/计划回退，保守输入估算29112超过可用28672，CONTEXT_EXHAUSTED暂停。没有完整第二结果/报告。
- large `e17b7b75-69c1-4017-9a15-372bc3640606`：18请求/23动作、五作业，两个结果和中文报告生成且实际读回；之后重复读三份文件，NO_PROGRESS暂停。未final_result，最后两步计划未完成。报告另行查看不能代替UI成功。

最新43份实际已发请求的摘要、正用量覆盖、Schema响应绑定和有界事实审计通过；独立计划审计43次均有恰好一个与先前确认动作匹配的当前提醒，包含压缩后的请求。证据`ui-dependencies-context-audit-corrected-<v9 task_id>.json`和`native-planning-request-audit-<v9 task_id>.json`只证明输入/集成。失败JSON、事件、日志、截图和源只读结果保留，完整试次为`ui-dependencies-tests-20261007-v9.json`。没有提高30请求、无进展3次或上下文上限，没有联网搜索或宿主包执行。

最终完整回归`pytest -q -p no:cacheprovider --basetemp workspace/test-temp-20261007-139`：177 passed（25.13秒），Ruff通过。初始175项（37.05秒）之后新增上述2项检查。一次受限终端测试长时间无输出，单独中断该测试会话；提升后的8项正常完成，没有据此重启产品服务。完整通用阶段、两模型稳定质量、Agent依赖获取/源码构建、完整恢复/UI及领域/规模仍未验收；搜索用户暂缓。

English: Baseline1.9 reuses native planning, storage, actions and budgets. Actual v8 snapshots exposed reminders dropped by compaction; the small before-hook adapter fixes this without accumulating history. All43 sent v9 inputs pass reminder/provenance/usage audits, while both workflows fail: small misuses environments and reaches the context guard; large completes files but repeats reads and triggers NO_PROGRESS. Original failures, a report-reference false positive and a zero-execution same-task supplemental pass remain distinct. Coalesced refresh fixes empty-plan UI; desktop/mobile checks and177 regressions pass. Full local acceptance remains incomplete; search is deferred.

## Next / 下一步

脚本收尾后再次实际运行，试次 `26a3dc4a-0562-44a4-88db-0cb26d1b1eb7`、task `0b6e9e64-944c-44fe-936f-6c7a2904a3a3` 返回0；原15项检查再次全通过，六个作业终态均确认（预期失败3个、成功安装及复用3个），cleanup无未确认项。此成功路径不冒充观察超时或停止拒绝的实测。证据 `dependencies-real-26a3dc4a-0562-44a4-88db-0cb26d1b1eb7.json`。最终Ruff/diff及Python语法检查通过。

继续P2小模型重复操作/报告质量、依赖自动获取/条件源码构建、剩余真实恢复窗口，再闭合本地UI/通用评估。大模型依赖UI的有限通过不代表两模型或完整依赖验收；不继续通过增加预算或删除检查追求通过。Agent 联网搜索按用户指示暂缓。阶段1其余门槛验收前不开发领域功能。低资源硬件仍由用户在 pilot 后决定，不能填入假定设备或测量。
