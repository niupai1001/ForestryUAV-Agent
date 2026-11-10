# 实际主入口：会话、报告和归档Job读回

状态：捆绑前端连接真实隔离后端的本批读回流程通过，截图已查看；未部署。模型/Job调度均禁用，没有重跑科学或Docker作业。

## 报告流程

使用report-acceptance原task efcbe703-3234-459e-a720-caea008eff03的真实消息/产物。Edge访问实际/入口，而非独立组件或Mock API：选择会话→正文Markdown表格→产物预览→下载→新建空会话→切回原会话→刷新恢复→输入未发送草稿。下载3382字节及SHA与原产物相同；模型请求仍4、动作仍4。浏览器无页面错误或修改请求。

桌面1440×960及移动390×844截图已查看：会话、产物卡片、输入框和移动导航可见，无横向溢出；移动视图正文内部滚动，未隐藏原完整消息。测试不会批准报告科学内容；原报告仍为语义失败证据。

证据：workspace/evidence/report-main-20261026-server-path.json、同前缀verification.json及desktop/mobile.png；docs/rebuild/report-main-ui-server-path-20261026.log。首次webServer使用正斜杠解释器路径被Windows cmd误认，未启动服务或进入正文；修为反斜杠后原断言通过，首次日志/JSON保留。

## 原真实执行器Job流程

使用executor-seam原task ec6f8c6d-12ab-4205-9298-899c2da53f06。实际主入口后台任务面板展示原成功Job c948ff3a-d70c-4f61-8128-d0adf6c4cbec与原取消Job 04e14c79-67a3-4c7b-a0eb-cfa4c49bfcaa，终态分别显示执行完成和已取消，终态没有取消按钮。

打开已归档日志读取restart seam started；原容器已经清理，读取未启动容器。resume-proof.txt下载SHA保持5dd3f791590841e94a67f5012969b7148ff0a3ea56a90777ef36837f14b5b076。刷新仍显示原两个Job，模型请求0、动作2，无浏览器修改请求或页面错误。

证据：workspace/evidence/jobs-main-20261026.json、verification.json、png，docs/rebuild/jobs-main-ui-20261026.log。截图已查看，日志/执行细节默认折叠；本次手动展开的日志保留原WSL stderr字节及真实stdout，不把原代理警告误称执行失败。

## 实现与资源

research/report_ui_app.py两个factory分别固定结束任务的独立runtime、断言工作区和task身份，enable_agent=False，不启动Job循环。frontend/playwright.report.config.ts与playwright.jobs-main.config.ts分别管理8796、8797；reuseExistingServer=false防止接管已有服务。新增对应两个主入口Playwright用例。未修改产品代码/前端包，不触发重新构建或无关回归。

服务46432和28444由Playwright回收；命令均exit0，结束后端口8796/8797无监听。无模型加载、Job提交、Docker运行或共享服务重启。F/E9通过。

## 不能扩大解释的部分

- 浏览器读取已经结束的真实任务，未证明模型自主新数据计算、Job完成后真实模型可信科学报告。
- 本批只读，不替代执行中取消/投递操作；沿用此前Mock组件合同与真实Docker取消证据，不声称本批重复通过。
- 原Job由研究代码受控提交，其来源turn没有完整用户消息；界面透明显示历史消息ID，不能当真实用户来源闭环验收。
- 未操作原生Windows文件/目录选择对话框，未做机器断电或旧版服务真实回退，也未部署生产。

下一项补科学来源/方法声明与源码链研究；原报告失败和真实试次预算保持冻结，主入口已通过的读回不再逐轮重跑。
