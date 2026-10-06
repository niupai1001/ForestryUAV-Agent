# Implementation / 实施状态

本文件区分实现与证据。完整目标未缩减：R01–R16、U01–U08、AC-01–AC-13 与三个阶段均保留。没有源码或通过证据的功能不标记完成。

| Batch / 批次 | State / 状态 | Delivered / 已有行为 | Evidence / 证据 | Remaining / 未完成 |
|---|---|---|---|---|
| P0 | in_progress | 独立应用、修订双语基线、uv/package 锁、实测 doctor | workspace/deployment.lock.json | 领域数据许可/格式、兼容地形、≥100 GB 数据、低资源选择 |
| P1 | in_progress | 真实模型与只读文件工具、持久任务、产物版本、本地 React 工作台 | small / large 数值烟测全部核对通过；2 项浏览器检查通过 | 语义验收、完整 UI 交互 |
| P2 | in_progress | 动作/attempt 先登记、请求去重、修订隔离、检查点保护、持久无网络容器作业与原生等待工具 | 18 项边界/故障测试通过；容器真实执行尚未通过 | Docker 环境修复、依赖准备、完整资源准入、真实重启/取消场景 |
| P3 | in_progress | 候选指纹、显式验收/清单；文档/知识/记忆；原生上下文整理/委派；H0/H1/H2；外发审批、公开读取/搜索、持久输入、完整引用、无进展保护、规范工具范围与 UI 修订 | 完整回归140项；两模型搜索报告、文件范围纠正恢复、范围创建/修订/重传 UI 通过 | 搜索稳定性及严格事实验收、本地 SearXNG、角色异常/冲突交互、两模型完整通用及长任务质量、完整报告/通用评估 |
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

## Next / 下一步

推进P2容器验证与依赖准备、受控本地搜索连接器及完整UI交互，闭合阶段1通用验收，并保留两模型通用能力及长任务质量验证。阶段1验收前不开发领域功能。低资源硬件是用户明确推迟的发布决策，不能填入假定设备或测量。
