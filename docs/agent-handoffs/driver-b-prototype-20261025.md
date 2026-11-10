# 独立Driver B原型 / 主Agent

日期2026-10-25。状态：代码落盘、静态/纯同步装配通过；**异步行为验证未运行、未部署、未作最终Driver裁决**。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6，前序工作树修改保留。

## 新增可用开发操作

research/driver_b.py是隔离独立模型/工具步骤循环，直接model.request_stream/FunctionToolset，不包装Agent.run/iter。保留现有Engine.run外层会话及任务提交，使用同一SessionRepository/动作/Jobs/上下文和StepStore；没有第二个生产事实库或执行器。显式下一step发现、冻结schema、按call_id批次结果、已启动调用取消排空及检查点写入已实现，但尚无异步运行证据。

生产不导入B、不提供B开关。原型明确拒绝H2/角色和审批接管，未实现原生计划/压缩等；不能直接迁移旧生产活动。短研究指令与生产A提示不等价，不能用更低token制造质量收益。

## 共享改动

- src/forestry_agent/runtime/tools.py::application_tools由原make_agent注册调用迁出，A/B使用相同文件/文档/图像/工作区/记忆/环境/科学Jobs注册定义；A的native spill额外传现有spill_store，B未构建spill能力。
- Engine.charge_model_request由原before_model事务逻辑迁出：任务累计预算、原活动映射、角色请求计费与事件同源。A before_model改调此方法。
- 因这两项提取，当前A的四条共享行为必须定向复核；旧A通过证据仍作为既有基线，不冒充本次工作树验证。

## 已读参考及证据

DeepSeek executor.ts完整契约和pipeline.ts前半执行/准入/取消排空已读，固定commit/源文件保存，详见REFERENCE-MATRIX。对应外部测试及后半链仍有缺项。本机PydanticAI/Harness公开接口已核对。

ruff F/E9通过所有本批代码。driver-b-schema-20261025.xml/log通过纯同步实际工具目录/必填schema/科学采用字段/参数校验装配；没有TestClient或事件循环，不是异步合同的替代。之后只增加coordinator合法目录求交和待运行负例，静态检查通过，不影响该已验证参数schema范围。

tests/test_driver_b_contract.py直接调用既有四条共享合同，对A/B各运行：多会话等待让位、Job终态自动接回、中断不重复副作用、发现/恢复。原输入和断言不改；另补B权限和同批冻结负例。全部仍待执行。

## 真实阻碍与接续

首次准备测试的functions.exec整体在执行前被自动审批审核用量限制中断，提示“usage limit，审核不能完成”，不是不安全判定。随后核对测试文件和driver-ab-20261025.log均不存在，说明该次apply_patch/ruff/pytest都未启动；已单独补写测试与验证登记。无待观察测试PID/会话，不得因旧脚本意图当成活运行，也不能把未执行记为失败模型试次。

普通沙箱asyncio初始化此前已确认挂起，禁止为绕过审核改在普通沙箱重建同一异步模型。允许的独立同步检查已完成；审核恢复后使用原已登记命令执行，不改简单目标。

待运行命令（工作目录本项目，需正常工具审批，不直接拼入其它执行渠道）：

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_driver_b_contract.py -q -p no:cacheprovider --basetemp=workspace/driver-ab-20261025 --junitxml=docs/rebuild/driver-ab-20261025.xml *> docs/rebuild/driver-ab-20261025.log
```

运行前再次核对无同试次日志/活进程；若有先观察原句柄，不能重启。首次真结果不论成功失败都保存；只修复受影响项，不重跑其它已通过合同。

docs/rebuild/DRIVER-DECISION.md为待裁决记录，不是通过证书。行为验证后再判断A最小可维护实现与B成本；真实推理/检索/H0-H1-H2比较仍须实际窗口身份及独立试次冻结。科学成果、旧模型试次及两次计量校准保留。

本轮无真实模型/科学执行、无部署、无新长期服务或子Agent，无新增长期资源占用。目标仍未完成；用量审核阻碍不阻止继续独立只读研究，但异步行为不能跳过。
