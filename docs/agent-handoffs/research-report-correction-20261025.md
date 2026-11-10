# 历史评分和报告更正 / 主Agent

日期2026-10-25。状态：实现/定向软件验证/历史只读重分类完成；无新模型/科学试次、未部署。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6，前序未提交修改保留。

## 新增可用操作

scripts/research_run.py的screening_score作为该套历史筛查的共同评分入口，research_table共用。自动项必须完整对应套件；语义manual不再被忽略，未审则不判full_task_passed。Job succeeded只作为终态记录，计算有效/方法可信/地面精度仍分开未知。旧原verdict不改写。

save_record独占创建；record/send须另存新输出，不能覆盖冻结记录；run/adopt在已有记录时于API接触前拒绝重建。重观察沿用原wall_seconds，不因baseline缺elapsed写成null。没有额外重试旧模型或重置预算。此保护不替代未来V2正式比较的完整试次冻结/累计计量；不应再把历史脚本当新比较调度器。

## 修改和证据

- scripts/research_run.py、research_table.py；tests/test_research_scoring.py验证自动项/语义/科学边界、无Job运行错误不猜执行器、独占原记录、API前禁止重建、原墙钟保留和呈现unknown。
- research-score-20261025.xml/log通过首批；research-table-20261025.xml/log通过表格独立样本。ruff F/E9通过；去掉一个脚本原有未使用seen变量。最后只补bool(checks)避免空列表的all()成为自动完成，不涉及真实试次。
- research/review_screening_records_once.py只读原11项记录，以原文件SHA和suite/scorer SHA另存docs/rebuild/SCREENING-CORRECTION-20261025.json。12计划、11运行、6自动通过、0已绑定完整语义通过；不表示6项都错。不重新执行原检查、不覆写原记录。脚本输出存在时拒绝重跑。
- docs/RESEARCH-20261021-zh.md标明历史性质并更正具体段落；docs/rebuild/REPORT-CORRECTION-20261025.md列来源、失败层、计量与科学口径。源记录09为MODEL_PROTOCOL_ERROR readonly且Job空，不能归容器；03/04是超时＋queued，不能单凭状态证明唯一根因。02/05墙钟null；旧文05的159秒不能冒充当前记录绑定值。
- 原28910为旧估计上界，16995+7002为字节，不应声称24K实际token或仅余4.5K。未因此重开tokenizer校准。

## 科学更正

任务05夹具LAI均值3.417、RMSE0.052与真实航次LAI0.9654413433140501、RMSE0.03791799685811614分别来自现存JSON/已冻结结果，不混合。camera是相机元数据归一化，不是已独立验证的反射率，也不能直接称原始DN；旧large对话第二轮解释错误、第三轮纠正的历史保持，不因此全项通过。NDVI是>=，Cab上界信号不证明唯一原因，光谱RMSE不等于地面LAI误差。无现场精度数据，不补造92%或生产可靠性结论。

## 剩余与接续

本轮收口评分与报告口径，不代表科研语义和完整计划已通过。下一项回到WP2独立最小Driver B的实际执行循环/共享四合同裁决；A原生发现已通过，不能改名当B。外部源码执行链/测试仍需补读；真实检索/H0-H1-H2比较前仍需核实实际服务窗口/Thinking并冻结独立试次。跨进程/崩溃、完整UI/执行器、方法条件身份、真实模型选择旧方案及发布迁移/回退继续保留。

原pilot evaluation已有独立报告SHA语义门，不重复测试。正确科学成果、旧模型失败、两次计量校准保持冻结。无服务/模型/执行器长期占用，无新子Agent或跨聊天消息。

审计源码复现：重分类后仅修改CLI示例及空检查列表防护，原审计不重跑。已在docs/rebuild/references/research-scoring按原scorer_sha256保存该次精确源码快照，并断言其字节SHA与JSON一致；该参考文件只保存，不执行。
