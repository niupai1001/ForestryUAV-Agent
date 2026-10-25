# 聊天气泡与科学成果复用 / wb-lai-fix-20261022

状态：已部署；完整三轮尚待模型正确完成。
负责人：本聊天单写入者。保留原有未提交修改；未提交全工作树。
新增操作：首条/后续消息共用ChatMessage，用户右侧有色气泡，16px正文，TIFF用文件卡片；影像工具支持唯一目录/正射自动选取；配置的已验证成果目录按完整输入字节/镜像/参数/输出SHA校验复用并复制至当前会话，生成来源记录，无伪造成功Job。
修改：frontend/src/{ChatMessage.tsx,App.tsx,Conversation.css,MessageAttachments.tsx}；src/forestry_agent/{imagery_catalog.py,imagery.py,config.py,agent.py,runtime/job_tools.py}；tests/test_imagery_catalog.py；本机config/app.local.yaml登记目录。构建static已生成。
验证触发与停止条件：workspace/lai-chat-fix-20261022/verification.md。新增复用边界检查通过，checks.xml；ruff通过。前端构建已生成index-CNqjwKNx.js/index-BWE5R7Jf.css。网页观察被浏览器URL安全策略拒绝，未绕过，视觉验看尚未完成。
真实模型：v1空输入错误调用文件工具触发NO_PROGRESS；补按实际授权裁剪工具后，v2-small三轮均返回，但第二轮混淆LiDAR，第三轮错误声称输入缺失，仍失败。v2-small/保存原始轨迹。正射复用为真实成功，反演未交付。原始2026-10-21科学结果未重算。
当前使用同代码不同模型large检查原样三轮，session.json和turn1/2/3.json记录当前进展；不能把正常返回当科学语义通过。运行负责人继续串行观察，不能新建同配置失败试次。
主8769原本不可连接，启动修复服务，数据未替换。原库存在历史queued Job，应用恢复规则处理；不能声称全库闲置/无历史任务。本轮无新科学计算。
真实剩余：模型正确选工具、保持任务范围、基于已有成果最终交付；右侧气泡视觉验看。跨会话复用入口已验证文件归属和改动拒绝，不能称全功能验收。

## 最终接续结果

large会话 `1d9f1702-c861-4f07-9b2c-9e86ac10318c` 三条原文逐轮waiting_user返回，第三轮有真实LAI统计、GeoTIFF下载及内嵌PNG（artifact b2e175df-a402-4163-884d-efe99bb67e99），API核对PNG 200、签名正确。未人工添加结果附件，工具自主命中已验证目录并封存至当前会话。此为已计算成果的自主复用交付，不能称重新计算通过。small失败仍保留。large第二轮把camera归一化误称原始DN，第三轮纠正为ODM相机元数据归一化；报告准确性不能全项判通过。NDVI边界表述也有>与>=细小差异，实际以冻结声明为准。网页观察仍受浏览器策略阻碍，视觉未验看；构建和服务已部署。
停止模型运行，不再重放同一成功流程；全局默认仍small，没有静默更换用户设置。主服务保留运行，科学计算未重跑。
