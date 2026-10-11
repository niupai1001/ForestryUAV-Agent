# 本地通用 pilot

2026-10-11 调整：已有清单及失败分母保留。阶段1优先交付剩余完整流程，按证据适用性补必要的两模型×H0/H1/H2覆盖；历史每组合三次的清单不作为每次修改后的重跑要求，也不要求先完成全部重复轮次再开发下一流程。正式重复、配对、消融与留出集中于阶段3。相关代码变化、新问题或阶段验收才触发重测，未受影响的通过证据复用；观察已运行任务不创建新试次。详见 [当前流程与冻结规则](STAGE-1-GATES.md)。

`forestry-agent evaluate` 复用现有认证 API、原生模型/harness 和受限 Jobs。评估入口负责冻结输入/独立答案/代码/配置、登记一次任务、续观察及评分，不增加 Agent 工具循环或执行器。它目前是通用 pilot 入口，不能作为完整 EvaluationManifest、留出发布或阶段1已通过的声明。

首个 `GENERAL-CSV-DEBUG` 任务族包含本地说明、三笔人工账目和植入计算错误/缺失six的脚本。执行 Agent 只获得三个源文件；`oracle.json`由独立评分器读取，不能注册给任务。输入脚本始终只读，只能在受限 Worker 运行。人工数据验证软件行为，不证明林业科学精度。

清单固定两模型、H0/H1/H2及每案例/组合三次重复，共18个独立试次ID。各试次共享目标/数值执行器/数据/权限/技术预算，H0/H1/H2能力仍由应用配方控制。重复ID由冻结清单摘要及组合/次数生成；同一清单换记录目录也不能借此新建一次模型试次。

启动本地服务并保存跟踪源码后，用实际配置冻结：

```powershell
forestry-agent evaluate freeze --config workspace/resolution-ui-config.yaml --manifest workspace/evaluation/general-pilot.json
forestry-agent evaluate run --config workspace/resolution-ui-config.yaml --manifest workspace/evaluation/general-pilot.json --trial GENERAL-CSV-DEBUG-small-H0-r1 --timeout 30
forestry-agent evaluate report --config workspace/resolution-ui-config.yaml --manifest workspace/evaluation/general-pilot.json --output workspace/evaluation/general-pilot-report.json
```

冻结清单拒绝覆盖。新增实验使用新路径，不能混改原模型/输入/评分器/源码。`GET /runtime`为认证后可读的服务启动快照，固定实际代码、角色及已安装库版本/配置摘要；冻结或执行时与当前评估源码比对。模型权重、模板/tokenizer元数据摘要、后端参数/版本、已探测硬件、容器版本及配置限额保存为试验条件。冻结使用`doctor(probe_search=False)`，不探测 Agent 搜索。

`run`单次只观察1–60秒；超时表示仍有后台任务，不取消或重启。再次对相同trial_id调用只核对原创建请求和继续观察原task_id，创建响应丢失也从现有应用请求表对账。OS文件锁及活跃任务检查限定此pilot单并发。已暂停/失败任务不自动恢复；待精确审批时保存交互需求，操作者在工作台处理后观察同一试次。评估入口不批准外发、不补提示、不自动验收或写生产记忆。

自动评分核对实际sealed产物字节、独立数值、成功计算生产者/作业、真实缺包失败诊断、环境安装版本、源字节、报告引用及完整读回窗口。文件存在、字段/关键词存在均不能证明报告主张正确，初次自动结果的`full_task_passed`因此为false。独立读报告及实际执行记录后，提供`EvaluationReview` JSON：manifest_sha256、trial_id、task_id、report_artifact_id、report_sha256、reviewer、criteria及observations。criteria逐项记录result_consistency、provenance_claims、language_and_limitations、failure_history、execution_boundaries的passed/failed/unknown；可注明failure_category。

报告文本代理对算术平均接受“平均”与“均值”两个等价标签；不修改冻结答案或报告，也不取消精确数值、真实执行与独立报告语义门槛。v4 large-H1实际报告因字面差异误判后，绑定原记录/报告与真实子程序诊断的只读重新评分单独证明完整通过。原自动评分与分母保留，`report`仍汇总原trial；重新评分sidecar必须注明评分源码版本，不能直接替换旧统计或当作新模型运行。

```powershell
forestry-agent evaluate review --config workspace/resolution-ui-config.yaml --manifest workspace/evaluation/general-pilot.json --review-file workspace/evaluation/review-small-H0-r1.json
```

审查绑定实际报告SHA及任务state_version；不能覆盖失败的数值/来源检查，不能调用应用验收接口。未变化的报告/任务及相关条件保持时，后续观察复用已有有效审查。汇总保留全部计划试次，包括未运行、创建未确认、失败、待审批和仍活跃；显示每组合分母、自动/完整通过数、实际提供方用量、观察墙钟时间、审批负担和未解决归因。样本重复不当作独立任务，当前不计算伪精确95%区间或宣称普遍排名。

## 捕获原程序失败的历史重新评分

成功退出的包装器可能通过 `subprocess.run` 实际运行原程序，并捕获其非零返回值和错误。不能仅因外层作业成功而漏记原程序失败，也不能仅凭打印出的错误文字认定运行已发生。这类记录需单独提供 `EvaluationDiagnosticReview`：绑定原 manifest/trial/task/job、提交代码 SHA、完整日志对象摘要、实际程序/数据 input_refs 和独立审查人；criteria 必须分别覆盖 `original_program_invoked`、`nonzero_exit_captured`、`diagnostic_from_execution` 的 passed/failed/unknown。

```powershell
forestry-agent evaluate rescore --config workspace/resolution-ui-config.yaml --manifest workspace/evaluation/general-pilot-20261010-v1.json --directory workspace/evaluation/trials --review-file workspace/evaluation/diagnostic-review-GENERAL-CSV-DEBUG-large-H1-r1-20261010-v1.json --output workspace/evaluation/rescored/GENERAL-CSV-DEBUG-large-H1-r1.json
```

`rescore` 允许评分代码演进，但原清单摘要、配置、输入/答案字节、原创建请求、模型/工作方式、任务状态、提交/实际执行文件、挂载与日志事件必须核对；只有 GET 和只读 SQLite 查询，不运行模型或 Jobs。独立审查必须阅读实际代码与日志，不能把模型解释当执行证明；非通过/未知诊断不能满足失败观察项。说明文档无需挂载到原程序作业。新记录单独保存原记录摘要、旧评分、新评分、当前评分代码摘要与审查，不覆盖原试次/清单/统计，也不自动验收任务。原报告审查仅在 task/report 版本及全部条件一致时复用，不能覆盖失败的自动检查。重新评分文件不由原 `report` 自动合并，应明确区分评分版本。

2026-10-10 已对 large-H1-r1 / large-H2-r1 的原程序实际缺 six 诊断重新评分；两项均没有所需结果 JSON 或报告，因此完整任务仍未通过。v1 清单保留 18 项分母、6 项已观察与 12 项未运行。最新现状见 [当日进度](PROGRESS-20261010.md)。

仍待完成：其他通用任务族/故障交互的冻结与实际六组合运行；显式生成种子控制、逐Worker峰值资源、预算匹配/消融、独立任务抽样/正式重复次数、pilot后发布阈值及留出门槛。上述项在清单中明确missing/TBD，不能默认通过。原领域、低资源、真实≥100GB和发布范围不变。阶段1出口见[STAGE-1-GATES](STAGE-1-GATES.md)。
