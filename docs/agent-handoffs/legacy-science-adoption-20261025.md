# 旧科学成果明确采用 / 主Agent

日期2026-10-25。状态：已实现、定向软件验证、真实旧记录只读审计；未部署、未验证真实模型自主采用。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6，前序未提交修改保留。

## 用户可用结果

新身份严格匹配继续自动复用。旧目录无完整身份时，Agent可先观察候选，核对当前目标与旧参数，再传reuse_candidate_id/adoption_note及匹配声明，明确采用旧方案。候选ID由完整条目hash生成；导入时重新核对输入、声明、镜像及产品SHA，记录采用依据、原来源和未知项。没有伪造当前算法身份或成功Job，没有重算。

已知完整身份不符不能用此入口绕过。指定反演LUT时，旧result必须能证明相同LUT SHA，否则拒绝；省略LUT时采用的是旧成果及旧方案，LUT先验/几何完整血缘仍明列未知。采用并不证明校准有效或现场精度。

## 修改与证据

- imagery.py新增成对可选字段及条件校验，旧请求序列化省略空新增字段，避免改变历史幂等载荷。选定候选无法命中时直接失败，绝不退回新作业。
- imagery_catalog.py候选指纹、明确采用及真实/请求身份分别保存；runtime/job_tools.py沿原守卫传参，无新增权限或用户流程开关。
- tests/test_imagery_catalog.py新增API采用、下载血缘、幂等、过期/方法不符/已知身份冲突/缺说明/输入或产物变化、原生工具经过真实perform的候选→采用，以及指定LUT边界；test_science_identity.py补旧请求序列化兼容。
- legacy-adopt首批采用/已知冲突通过；其余失败只是HTTP断言不符合实际错误通道，改精确错误码后legacy-adopt-errors通过。原失败保存，仅复核失败项。
- legacy-native-20261025.xml/log通过原生注册工具与序列化兼容；legacy-lut-20261025.xml/log通过原LUT采用与变更LUT拒绝。非模型行为合同，不等于真实模型正确选择。
- ruff F/E9通过（后续仅补测试LUT分支）。触发、影响、复用、停止条件见BASELINE-REPORT。无真实模型或科学执行。

## 真实旧记录审计

config/app.local.yaml的目录为workspace/lai-chat-fix-20261022/verified-imagery-catalog.json。research/audit_legacy_catalog_once.py使用SQLite mode=ro/query_only读取原Job，校验代码SHA、实际result.json SHA、声明/镜像/summary一致、产品归属及恢复祖先；只写新的审计报告。

输出docs/rebuild/legacy-catalog-audit-20261025.json，原正射Job a10b8b0d-2a6d-4423-86af-c914755173c0、反演Job f3fa2551-f5e5-4903-b3b4-cd52ee9c3ae2均完成记录核对。原LUT SHA保留。没有重新哈希全部科学产品来重复冻结验证，实际采用入口仍逐项验证产品；没有改目录、补造当前recipe身份或重算科学结果。审计脚本拒绝覆盖已有输出，后续读报告，不重跑同输入。

## 剩余与接续

旧方案采用依赖Agent真实判断，尚未做新真实模型试次；科学独立校准/地面精度仍未知。当前运行主入口未发布上述修改；跨进程writer、硬崩溃恢复、独立Driver B/检索收益、完整UI/执行器接缝及最终发布门均未完成。

下一项按计划继续无进展判定/报告纠错及只读sqlite_probe，复用本批成果和原始科学产物。对真实模型/执行器比较须先核实实际窗口并冻结独立试次，不重开已耗尽计量校准。无新增长期服务/模型/执行器占用。
