# 主入口真实报告与Job读回 / 主Agent

状态：真实后端主入口本批读回及视觉检查通过，未部署。
2026-10-26，目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1，基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6及原未提交改动。单写入，无子Agent/跨聊天通信。

## 可用结果

真实报告任务的会话切换、刷新恢复、Markdown表格、预览及字节一致下载通过；移动无横向溢出。真实Docker原Job的成功/取消终态、归档日志和结果可在主入口读回，刷新仍是原两个Job。全程GET/HEAD，原模型/动作计数不变。

## 修改与证据

research/report_ui_app.py增加固定隔离runtime、禁用Agent的两个factory；frontend两套专用Playwright配置及report-main-entry/jobs-main-entry用例。没有产品代码或静态包修改，不重建前端。

BASELINE-REPORT事先登记两类触发/复用/停止。第一次8796启动失败是Windows cmd解释器正斜杠路径，未进测试正文；修反斜杠后原断言通过，server-path证据独占另存。8797首次通过。各JSON/log/截图和限制见MAIN-ENTRY-20261026.md。三张截图已查看；F/E9通过。

原报告语义仍失败，原Job容器已清理，这次只是读取归档，不重开计算。研究Job的来源turn不是完整用户对话，因此未把ID回退显示当真实来源消息验收。

资源：8796服务46432、8797服务28444均由Playwright回收，命令exit0，端口结束后无监听；无模型/容器运行，无共享服务重启。

## 接续

本轮随后补读DeepSeek固定22ddee的assistant-stream全类、agent请求提交段、tool-history全类及对应tool-history/request-error全测试和contract-regressions指定样本。归档及hash/精确范围在source-stream-history-20261026.json，映射与不移植理由在REFERENCE-MATRIX；不执行外部测试、不移植TS核心、不重跑本地有效证据。该研究补足流提交/历史schema两条链，未证明跨版本未决请求可重放；其余参考测试研究仍开放。

继续科学来源/方法声明、源码与对应测试审阅；可信真实科学报告、新数据自主计算、来源消息/执行中整段用户交互、原生选择器、原生产/27B窗口、阶段3裁决及正式发布仍开放。禁止重跑本批读回或旧模型失败；已有正确科学成果继续复用。
