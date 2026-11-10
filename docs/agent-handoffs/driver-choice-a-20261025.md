# A/B共享行为裁决 / 主Agent

日期2026-10-25；实际日志UTC已跨到2026-10-26，沿用原批次ID不重命名覆盖。
状态：本批驱动选择为A；未部署、整份V2未完成。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6及前序未提交修改保留。

## 本批交付

正常审批现已恢复。确认原日志/XML不存在后，首次实际执行原driver-ab命令，exec会话94103已exit1。不是重启活试次，也未绕过此前审核限制。driver-ab-20261025.xml/log保留所有结果。

A四合同通过：A会话外部工具等待时B会话能回答、Job终态一次自动接回不重算、中断批次先排空真实副作用且续聊不重做、隐藏合法工具下一step发现/恢复。A共同application_tools及charge_model_request提取已覆盖；driver-a-budget-20261025.xml/log另补原活动预算耗尽零新模型请求。

B是真正直接request_stream/FunctionToolset的独立循环；多会话、中断恢复、发现/恢复、权限及同批冻结负例通过。唯一Job接回失败：workspace/driver-ab-20261025/test_shared_runtime_contract_j1/workspace/.forestry/run-errors.log明确DRIVER_B_UNSUPPORTED，在模型调用前拒绝原合同H2任务。没有创建第二Job或重算科学结果。保留该覆盖缺口，不改测试为H1凑通过。

根据V2选择最简单已满足路线，本批选A继续整合；没有A无法消除的结构性缺陷证据，不为让B全绿扩建H2/审批/压缩。不能从B的主动能力拒绝推导另一种架构永远不行，也没有真实模型成本优劣结论。DRIVER-DECISION已列证据、未支持范围及C路线可行性边界。

## 实验文件归属

原tests/test_driver_b_contract.py按完整SHA归档docs/rebuild/references/driver-ab/test_driver_b_contract-b51c55ae2e12ead3e2c27f7d8c4e7fca85d4d000ccfb505d89ff62b27feb16c9.py。

未选B的比较入口移到research/driver_contracts.py，只增加原tests/conftest夹具导入与路径装配，合同输入/断言保持不变，未改xfail/预期。移出生产默认测试收集是研究候选归档，不把原失败转为通过；原失败仍是裁决依据。collect-only已确认原四合同A/B及两个负例均可被显式选择，未重跑行为试次。

research/driver_b.py保留研究，不提供生产开关、不调用Agent.run/iter；生产继续唯一A驱动/Store/Jobs。共同注册和预算方法是已验证的复用入口，不删除或回退全工作树。

## 剩余与下一项

驱动技术选择本批收口；原ToolSearch、手工BM25与静态发现的最终收益/保留路径还需标注召回、embedding/混合及实际任务对照。先核对现有工具目录和缓存资产，冻结检索集；真实比较前核实服务实际窗口/Thinking，不用架构上限或配置值冒充。C仅可行性报告已写，不迁移语言/生产。

跨进程/硬崩溃、完整UI/执行器接缝、方法条件身份、真实模型科学方案选择、兼容迁移/回退和发布门仍未完成。科学成果、旧模型失败、两次计量校准全部冻结。

本批全部离线模型/临时Jobs，无真实推理或科学计算、无部署。测试句柄已终态，无新长期资源或子Agent。
