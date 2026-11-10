# 不同排障继续与纯只读诊断 / 主Agent

日期2026-10-25；已实现、定向软件/原生流程验证；未部署。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6，前序工作树未提交改动保留。

## 新增可用操作

不同结构化失败的首次检查可以继续，不把“没有新增证据”直接等同“已经循环”。Verdict保留new=false、stalled持续累加，stop_required另行决定：重复请求、同一障碍、无有效事实且非结构化新失败的记账/空结果仍可停止。事件保存停止决定与call_id，check及同调用重放读取持久决定；旧事件缺字段按旧停止语义，不能通过升级清空已停止状态。任务累计请求/动作上限未改。

sqlite_probe已有mode=ro基础，但仍会NamedTemporaryFile写探针，且WAL读连接可能创建/修改shm；本次去掉写探针、只记权限提示，使用as_uri转义＋mode=ro&immutable=1读主文件。不能看到WAL实时提交，明确标unknown，不把主文件快照冒充实时状态。旁路stat竞态记录unknown，缺失库不创建，损坏库归因为read失败。

## 修改与证据

- src/forestry_agent/progress.py：停止判定与证据新颖性分离、持久决定及重放；无需新队列/数据库迁移。
- src/forestry_agent/agent.py::sqlite_probe：无临时写入及SQLite共享内存写入；错误归因、转义URI及主文件快照边界。
- tests/test_sqlite_probe.py：目录含#、实际live WAL、缺失/损坏库，核对前后数据库和sidecar字节/mtime无变化；禁止NamedTemporaryFile。
- tests/test_progress_exploration.py：不同检查跨Store重建/调用重放、重复请求换错误文字、换假设说法仍同障碍、原生完整Engine到最终正文。
- progress-probe-20261025.xml/log：新WAL/损坏及受影响旧障碍、重复、分页、失败、上下文恢复合同通过；新样本两处测试缺陷保留（错误预算字段、connect替身uri参数名冲突）。probe-fix证明完整task state不变；probe-uri证明修正替身后escaped URI及无写入通过。只复核失败项。
- explore-native-20261025.xml/log：实际Engine/原生工具/受守卫动作保存三项不同受控读取错误，第四请求提交最终正文；stalled为1/2/3且new=false，不制造进展；累计model_requests=4、原generation不变、无blocker/Job。模型为FixtureFunctionModel，不是实际Qwen能力实验。
- ruff F/E9通过生产改动；后续只添加原生流程测试。BASELINE-REPORT保存触发、复用、范围与停止条件。

## 剩余与接续

本次不是完整WP4完成：Progress仍主要按本turn记录，自动续接的原活动继承及可验证条件身份（输入版本/方法变化）仍需整合；任意新错误不等于有效科学探索，累计预算保留兜底。报告评分/既有错误解释仍未整改。真实模型策略判断、Driver B、检索比较、跨进程恢复、完整UI/执行器和发布仍待。

下一项完成活动级Progress与结构化条件身份，随后修报告/评分并接续总计划其余范围。sqlite_probe主文件快照有意不声称WAL当前状态；不可为了诊断恢复写探针或无保护打开库。科学产物与旧模型试次继续冻结。

未运行真实模型/科学作业、未启动持久服务或子Agent，无新增长期资源占用。
