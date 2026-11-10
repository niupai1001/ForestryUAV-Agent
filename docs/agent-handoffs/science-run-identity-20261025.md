# ScienceRunIdentity / 主Agent

日期：2026-10-25。状态：已实现、定向软件验证，未部署。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6。前序未提交改动保留。

## 新增可用操作

新正射、LUT、反演recipe带ScienceRunIdentity；worker将其随result.json保存。身份包含schema版本、操作/方法、输入字节hash（整航次保留文件名）、有效规范化声明、镜像、算法源码hash、预处理版本和定标来源。LUT声明包括先验/几何/响应，反演绑定LUT字节，因此不同先验/几何生成的不同LUT不可静默复用。

目录复用：raster+lut反演输入现可复用；当前完整身份与存档身份一致才导入，provenance产物保存该身份。缺声明、缺LUT、旧目录缺身份、当前recipe与旧身份失配分别说明原因，保持候选，不自动提交新计算。原有效声明不匹配仍不复用。权限继续走共享受守卫入口。

## 文件和证据

- 新增src/forestry_agent/science_identity.py：规范化JSON及SHA；recipe版本来自实际worker与旧航次预处理源码，无独立手写算法版本表。
- imagery.py在准备作业时绑定已授权真实输入；imagery_worker.py结果记录请求身份。inspect/旧恢复无完整身份时明确unknown，不编造。
- imagery_catalog.py强制完整身份匹配；runtime/job_tools.py同步说明；tests/test_science_identity.py、test_imagery_catalog.py覆盖相关边界。
- 触发/预算/复用/停止条件见BASELINE-REPORT。science-identity-20261025.xml/log通过首批。补method_id及真实反演API后，science-identity-invert-20261025中身份/原导入通过，新样本资源幂等键冲突未进入边界；修样本键后science-identity-invert-fix证明变LUT/缺LUT只候选。
- 原LUT导入已通过，但测试直接Path读血缘遇Windows长路径限制；改用用户下载API后science-invert-artifact-20261025.xml/log通过身份读回。仅复核失败项，保留原失败日志。
- ruff F/E9通过。本批没有真实模型/科学执行；worker结果保存验证使用替身build_lut，不能称真实PROSAIL验收。

## 剩余与下一交付

真实旧目录缺ScienceRunIdentity，不能伪造当前源码版本使其变绿。先定位目录配置及原request/result/input manifest/镜像记录，能证明的原身份保留；无法证明的字段保持未知，报告为候选。当前源码改过后，其身份不应冒充过去算法源码。需要设计显式、可追溯的旧成果核实/采用路径，保证正确科学结果不被迫重算，同时不静默声称完全匹配。

恢复作业现记录unknown_legacy_or_resume，不宣称已继承完整身份；后续需按原记录接续。定标来源声明是可追溯身份，非独立证明现场校准有效。真实新计算及科学精度未验收。其余Driver B、检索收益、跨进程/崩溃、UI/报告、部署仍在总计划。

无模型、服务或执行器长期占用；临时测试数据不迁入用户目录。正确ODM/PROSAIL成果未重算。
