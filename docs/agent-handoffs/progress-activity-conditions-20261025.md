# 活动级Progress与条件身份 / 主Agent

日期2026-10-25。已实现、定向软件/原生流程验证；未部署。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6，前序未提交修改保留。

## 新增可用操作

Progress按现有turn_continuations关联原活动的所有观察、重复请求、障碍与持久停止决定；角色独立、新用户活动独立。观察仍保存真实turn身份，原生调用重放只在本turn/call/request内判定，自动回合复用同call_id不会被误认作已执行。本批不新建状态表、不改历史进展行、不重置冻结阈值或累计预算。

Failure新增可选后端condition。Resources.read对二进制/编码失败记录实际resource_id/relative_path/version，经同步/异步动作及原生异常通道保存；Evidence障碍与请求决策关联该条件。相同文本错误来自不同文件或变化版本可继续检查，但new仍false、stalled仍累加。模型args里的hypothesis不能产生condition；文件stat版本不是科学成果的字节/方法身份，科学复用继续使用完整hash。

Job新终态属于新事实：before_model先认领合法终态，Progress按event_id只观察一次，再检查进展。这样旧停滞不阻止分析新结果；相同终态进入下一请求/重建/另一自动回合不能再清零。消费确认仍由正常最终回答提交或成功主动观察，不因为Progress已看过就提前消费；模型预算照旧累计。

## 修改与证据

- src/forestry_agent/progress.py：活动查询、角色/实际调用范围、事件activity_turn_id/spec_revision、条件决策及终态事实去重。
- contracts.py、resources.py：可选后端条件及实际读取失败来源；agent.py保存条件并将终态观察放在检查之前；runtime/evidence.py保存condition_sha256并将其纳入障碍身份。
- tests/test_progress_activity.py：跨重建累计、相同call_id跨turn、同call重放、角色/新用户隔离、完整Job接回、新终态一次新证据及重投不奖励。
- tests/test_progress_conditions.py：实际二进制文件错误、不同文件/变化版本、原动作Journal保留旧条件、原样重试停止，实际原生流程三项二进制来源检查→完整报告。
- progress-activity-20261025.xml/log通过首批活动及受影响排障/重复/恢复合同。该批Job样本当时验证继承阈值停止；随后发现真实新终态应先进入证据，按相关代码语义更改了同一目标的预期，原证据保留、不当最终行为结论。
- progress-conditions-20261025.xml/log通过真实条件及受影响旧失败/障碍合同。
- job-progress-20261025.xml/log为最终Job接回证据：原stalled1/2，新终态0（有新证据），后续记账1，下一请求不再次清零并提交完整正文；模型预算耗尽仍零新增请求，原终态投递/消费边界通过。
- job-redelivery-progress-20261025.xml/log补跨Store重建及另一自动回合的同终态不清零，原Job不重算。
- ruff F/E9通过生产改动；最后仅新增重投合同。BASELINE-REPORT有每次触发、范围、复用与停止条件。无真实模型/科学执行。

## 剩余与接续

后端条件机制目前接入文件读取失败，不代表所有科学方法、输入版本冲突及执行器暂态故障都已有条件身份；无可靠条件时保留保守旧判定。新用户纠正活动依旧单独记录，任务累计预算不会因此重置。

下一交付修报告评分和既有科学解释归因，继续区分软件完成/方法可信/地面精度；不要重算正确成果。Driver B、embedding/混合收益、实际模型生效窗口、跨进程/崩溃、完整UI/执行器与发布迁移/回退仍在主计划未完成范围。

没有部署、未启动长期服务/模型/科学执行器或子Agent，无新增长期资源占用。两次计量校准和原模型失败试次不重开。
