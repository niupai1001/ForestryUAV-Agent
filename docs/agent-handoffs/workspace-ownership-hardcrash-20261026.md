# 工作区单写与硬退出 / 主Agent

状态：已实现并完成受影响隔离软件/真实进程接缝验证；未部署。
日期：2026-10-26 UTC（项目批次接续2026-10-25）。
工作区：C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1。
基线：3eb534011ece8a6df5d0ddc9ae19a075a536c7b6及前序大量未提交修改，未清理、重置或提交。
本次写入：src/forestry_agent/runtime/ownership.py、app.py构造/lifespan、jobs.py::loop；tests/test_workspace_ownership.py、tests/test_runtime_hardcrash.py；基线登记、交付追踪、本交接。

## 用户可用结果

继续“完成计划”，本轮从已修订计划第1项实施实际缺口，不停留于重写计划。新版同工作区服务从构造、启动恢复到关闭排空串行持有OS锁，第二实例拒绝介入。不同工作区不互锁；进程实际退出后可接管，遗留文件不需删除。采用与项目pilot_lock/Windows launcher相同的标准库锁机制，无依赖/租约服务/第二队列。

Jobs.loop不再在取消await时遗弃同步tick。启动同步恢复和整个关闭过程同样等待已开始工作结束，期间不释放owner；这不把后台容器生命周期绑定到模型锁。

## 接口与证据

- workspace_writer(root)：构造与lifespan两处获取；占用抛WORKSPACE_IN_USE（409），只锁服务工作区，不改SQLite schema。OS自动处理进程死亡，存活owner不按超时被抢占，无需迟到旧owner的租约代际。
- finish_owned/owned_thread：shield拥有的异步清理/同步线程，取消后排空再传播；模型/科学/数据库权威保持原实现。
- ownership-20261026原证据为4通过1失败。失败夹具只kill/wait Windows venv启动器，没有实际owner PID，不能证明持锁者已死；原日志不覆盖。改回报PID并核对父子关系、kill/wait实际解释器后，ownership-process-fixed-20261026仅原失败项通过。
- hardcrash-20261026：实际create_app/TestClient/Engine.perform/Resources.write；在immutable artifact提交后/动作结果前、动作结果后/回答前各kill实际进程。重启保留文件和唯一产物，pending动作拒绝重放，confirmed动作返回原结果，动作计费1、模型0。
- ownership-job-crash-20261026：真实Jobs.submit/save事务，受控failed终态分别pending/claimed时硬退出；新服务恢复同event_id，重复save不重复终态，观察确认后再次启动仍consumed，Job只有1。受控终态不代表真实执行器运行或科学有效。
- 精确命令、验证触发、受影响范围与停止条件已在BASELINE-REPORT运行前登记；XML/log本批名一致。ruff --select F,E9通过。未重跑无关基础/模型/科学计算或原校准。

## 真实边界与接续

仅新版官方create_app/lifespan服务入口受保证；独立Store脚本、旧版本服务、禁用lifespan的ASGI入口、跨主机共享存储未统一隔离。部署先查明并交接旧服务/写入者，不能因新锁空闲推断旧服务不存在。真实Docker/取消清理、旧数据兼容、UI、科学自主判断、比较、迁移回退和发布继续开放；进程kill不是断电或磁盘故障验证。

下一项：继续第1项外部源码执行链/测试审阅及旧维护入口兼容；沿ModelProfile→Adapter→实际SDK→Ollama核实窗口/Thinking后才进入已选A的真实任务对照。原A/B、检索向量、科学结果和本批通过证据直接复用。

无子Agent/跨聊天消息/部署/真实模型/容器。所有本批子进程实际owner及启动器均已终态；只读观察句柄97702也已exit0，没有本批共享资源占用。不要重建相同失败或重复本批已通过项。
