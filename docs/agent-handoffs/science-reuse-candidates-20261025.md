# 科学复用候选边界 / 主Agent

日期：2026-10-25。状态：已实现、定向软件验证；未部署。
目录：C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6，工作树包含前序大量未提交修改，勿整体覆盖。

## 用户可用结果

无本次参数声明时，process_imagery和API的共享reuse_verified返回reuse_candidates，含旧声明、来源、限制、数据/镜像和产物校验信息。没有成果导入、Job或完成声明。模型核对方法符合目标后仍可保存声明JSON并再次请求复用；不要求用户额外切换模式。

显式声明与旧声明均按同一schema规范化，避免默认值缺省导致误判；source_note仅为来源文字，数值/定标配置仍严格比较。正确科学成果只读校验与导入，不重算。

## 修改与证据

- src/forestry_agent/imagery_catalog.py：预读声明、双侧规范化比较、无声明候选收集（可多项）、无导入回执。
- src/forestry_agent/runtime/job_tools.py：原生工具说明同步候选/确认语义；沿用现有守卫执行与权限路径。
- tests/test_imagery_catalog.py：有效ODM声明夹具、无声明候选、默认值一致可导入、定标/分辨率/模式变化不复用；旧输入与损坏产物样本保留。
- BASELINE-REPORT记录触发与停止条件；science-candidates-20261025.xml/log中相关新行为通过，两个旧输入用例因submit默认改带声明而排入合法新计算路径，临时库产生Job记录，未证明科学执行。随后仅恢复这两项原本无声明条件并复核；science-candidates-fix-20261025.xml/log通过，原失败保留。
- ruff F/E9通过（生产改动无后续变化）。不运行真实模型、ODM或PROSAIL，不重跑其它冻结证据。

## 真实剩余与下一交付

本次只完成V2“缺少声明时给候选”的明确缺口，不代表ScienceRunIdentity完整。算法版本、预处理版本、定标来源及LUT参数/几何血缘仍需绑定；反演目前仅raster快捷候选，与带lut计算输入的关系还需完整契约。旧目录缺元数据不能猜测补齐或直接宣称全匹配。

下一项检查实际目录/worker产物记录，建立可验证的科学身份与旧目录未知语义，覆盖LUT先验/几何变化、定标/波段变化；复用正确计算成果，报告单独纠正。Driver B、检索对照、跨进程恢复和发布等主计划剩余继续保留。

未启动持久服务或模型，无新增长期资源占用；本地临时测试记录不迁入用户数据。
