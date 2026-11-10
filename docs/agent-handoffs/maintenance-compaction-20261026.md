# 维护入口与大批次压缩 / 主Agent

状态：实现及受影响合同通过；未部署。
日期：2026-10-26 UTC。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1，基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6及既有未提交修改保留。

## 用户可用结果与范围

从计划第1项接续：backup.create/restore与官方服务共用OS锁，阻止同库不同端口绕过；锁改在根.forestry-service.lock，恢复替换.forestry不会失去owner，也不归档活动锁。保留原端口独占以约束旧服务，原目录/备份身份拒绝仍在写入前检查。

tasks inspect使用store.inspect_task的mode=ro连接，复用静态read_task，不构造/迁移Store；事务读取最新提交状态，在其他writer等待/持有期间可读；缺库结构化错误且不创建目录。与immutable sqlite_probe用途不同，inspect需要读WAL当前提交，不使用immutable忽略WAL。

源码审阅新增固定Claw compact.rs及配对/中断样本、本机Harness裁剪/恢复事实和DeepSeek取消/结果流水线相关函数。发现Harness仅回看5条及旧ID跨已结算区误配，修复UsageAwareSlidingWindow对大批次分散结果、工具Retry和顺序重用ID的裁剪配对；保留实际目标/pin/receipt和完整请求预算。不是新增通用压缩框架，也未移植Rust/TS实质代码。

## 修改与证据

- src/forestry_agent/runtime/ownership.py锁位置；backup.py离线互斥/恢复前置；store.py只读检查及read_task静态化；cli.py只读入口；context.py配对闭包。
- tests/test_maintenance_ownership.py；test_workspace_ownership实际路径断言；tests/test_compaction_batch_pairs.py。
- maintenance-owner-20261026.xml/log：同工作区不同端口拒绝备份/恢复；复制/恢复发布时服务构造拒绝；原backup全部受影响合同；WAL只读无schema写入；缺库不建目录；新锁位置实际进程退出接管。
- compaction-batch-20261026.xml/log保留1失败4通过；失败为顺序重用ID时未达到旧前缀实际裁剪出口。修复后compaction-pair-fixed-20261026只复核原失败、受影响批次/Retry及pin/单receipt，通过；没有降断言。原Engine完整编译/目标/私密上下文证据在首次运行通过，未因无关修复重复。
- 验证前的触发、复用范围、命令和停止条件详见BASELINE-REPORT；ruff F/E9和git diff --check通过。
- 新参考源码按完整commit经GitHub connector读取，归档compact.rs并保留同目录Claw MIT许可。source-review-20261026.json保存归档和安装源hash；REFERENCE-MATRIX明确已读函数/测试、局限、不移植理由。未运行Rust/TS仓库测试。

## 限制与接续

应用使用新版create_app/lifespan及备份维护入口受锁约束；任意自写Store脚本、旧服务或跨主机文件系统仍非强制隔离。中间版本锁尚未部署，不能把残留旧位置文件当owner证据。升级先核对旧writer。

配对修复依赖Harness公开compact/reinject_pinned；receipt使用继承的3项受保护辅助方法，版本升级须定向检查。原损坏历史不虚构结果；必要批次无法装入窗口仍按既有预算拒绝，不升窗口/删配对。Claw原配对测试短输入未强制触发压缩这一局限已记录，不能照搬为充分证明。

尚缺：完整外部源码链/测试审阅、实际Ollama窗口/Thinking、真实模型任务收益、真实执行器/UI/科学自主流程、迁移回退与部署。下一项继续这些真实缺口，不重跑已冻结Driver、检索编码、token校准或科学计算。

无子Agent/跨聊天消息、无服务/模型/容器启动、无安装；所有本批测试命令已终态，无活资源。持续目标未完成，不把本批合同通过计作V2收口。
