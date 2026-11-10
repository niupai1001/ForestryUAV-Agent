# 发现指引、科学能力声明与后台操作 / 主Agent

状态：已实现、受影响软件/浏览器组件验证通过、已构建静态包；未部署。
日期2026-10-26 UTC。目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1，基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6及所有前序未提交修改保留。

## 新增可用操作与范围

原生发现工具和根说明统一提示：可见schema只是已加载集合，未见工具/路径不能证明没有能力或资料；按用途检索，下一step调用，仍受权限约束。去掉无输入直接结束、影像固定调用的过程特例，允许先发现所需材料；明确write_file相对路径和容器/output不同。数值范围区分来源事实/本次参数/假设，不用极值证明因果。

ToolSpec增加可选英文摘要/输入输出/模态/权限描述/版本，原调用兼容；同一注册函数直接投影ToolDescriptor。影像与笔记实际声明已填写，未声明信息保持空值或既有默认，不另建手写权威目录。科学适用性解释回到process_imagery自身，现行先验不能解释身份未知旧LUT，camera归一化不等于独立定标。没改worker、数据、方法参数或旧结果。

JobsPanel提供名称、来源用户消息/缺项fallback、提交时间、产物下载、折叠日志、单作业取消。新增POST /tasks/{task_id}/jobs/{job_id}/cancel及JobCancel schema，只登记当前归属Job意图，等执行器确认才终态；不取消会话其它工作。Jobs.request_cancel单事务读/改/事件与可选用户幂等回执，既有内部删除/整体取消沿原调用不增加新的权限障碍；终态/重复意图不重发取消事件。

## 修改与证据

写入agent.py、runtime/discovery/interfaces/tools/job_tools/memory_tools、contracts/jobs/app/cli；JobsPanel.tsx/css与App传消息/产物props；JobCancel.schema.json；相关定向测试/Edge组件夹具与文档。Vite正常构建替换生成静态资产，不是部署运行服务。

- discovery-guidance-20261026 XML/log：真实SDK请求中指引、用途参数说明、隐藏→下一step、权限/恢复及descriptor源。0真实模型/科学Job。
- job-controls-20261026 XML/log：新API归属/ID冲突/重放、会话控制分离、四种终态不改写；原运行容器拒停时不伪终态与错误容器身份不操作。Fake执行器，非真实Docker证据。
- npm run build类型检查通过后Vite沙箱EPERM；仅授权vite build复核失败打包，48047已exit0，约3分8秒；未安装依赖。生成index-DNWy3Kde.js/index-DVBaniQc.css。
- Edge组件jobs-panel-20261026-api-fixed.json/png通过完整来源/下载/取消确认/刷新/折叠日志，截图已视觉检查，实际viewport900×800，无文字重叠截断。前三次夹具失败分别是漏/ui/、绝对模块解析、Mock glob未覆盖cancel子路径，原JSON/截图不覆盖，断言不放宽。最后Playwright服务8794自动回收；86118/82958等失败句柄均终态，最后运行exit0。
- 触发、精确命令、资源与停止条件均在BASELINE-REPORT先登记。ruff F/E9、git diff --check通过。文件选择器经源码核对已实现，未重建也未假称本批视觉验收。

## 剩余与接续

此批只证明新说明/声明进入产品协议和后台操作接入；不证明真实4B会发现记忆或可靠解释科学结果。原static/native两条失败完整保留，原9请求预算不重开；旧目录/向量仍按旧hash，不能把20/20算到新声明版本。metadata尚未逐一覆盖全部其它工具，不能宣称所有领域声明齐全。

浏览器Mock组件与后端实际API分别验证，尚非真实执行器/主入口完整E2E。下一项完善Job投递失败可见/可处理状态，并接真实执行器恢复/取消/归档闭环；随后完成科学/通用流程、旧会话兼容/增量迁移/保留新增数据回退和发布。原生产标签/27B实际窗口、外部完整链/测试审阅、正式阶段3比较继续保留。

无子Agent/跨聊天消息；无新模型/科学计算、无活进程/服务占用。现静态包已构建但运行实例未发布，整份V2保持未完成。
