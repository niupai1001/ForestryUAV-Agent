# 本地通用 pilot

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

```powershell
forestry-agent evaluate review --config workspace/resolution-ui-config.yaml --manifest workspace/evaluation/general-pilot.json --review-file workspace/evaluation/review-small-H0-r1.json
```

审查绑定实际报告SHA及任务state_version；不能覆盖失败的数值/来源检查，不能调用应用验收接口。未变化的报告/任务及相关条件保持时，后续观察复用已有有效审查。汇总保留全部计划试次，包括未运行、创建未确认、失败、待审批和仍活跃；显示每组合分母、自动/完整通过数、实际提供方用量、观察墙钟时间、审批负担和未解决归因。样本重复不当作独立任务，当前不计算伪精确95%区间或宣称普遍排名。

仍待完成：其他通用任务族/故障交互的冻结与实际六组合运行；显式生成种子控制、逐Worker峰值资源、预算匹配/消融、独立任务抽样/正式重复次数、pilot后发布阈值及留出门槛。上述项在清单中明确missing/TBD，不能默认通过。原领域、低资源、真实≥100GB和发布范围不变。阶段1出口见[STAGE-1-GATES](STAGE-1-GATES.md)。
