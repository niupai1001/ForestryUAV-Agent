# 回执失败处理与真实执行器 / 主Agent

状态：新行为/受影响软件和浏览器组件验证通过；真实执行器跨应用重建/取消/归档通过；静态包已构建；未部署。
2026-10-26 UTC，目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1。
基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6和前序未提交修改均保留。

## 用户可用结果

Jobs完成回执增加已呈现、明确跳过、需处理，与模型消费区分。派发失败或回合失败保留原事件/错误，不静默丢失或无限唤醒；已读事实不因后续报告失败倒退未读。用户可继续正常续聊读原事件或明确跳过；没有新增“重试”按钮重置预算。当前running writer拒绝跳过，queued自动续接原子停止；Engine.run先在同事务认领running并检查skip，防止旧排队快照迟到启动。启动独占对账把旧running turn暂停并标未完成投递需处理，保留旧事实。

前端显示回执/分析失败，提供标记查看/跳过；执行器succeeded称执行完成，不说科学已验收。列表/详情投影直接读原job_deliveries/turns/events，不复制进Job payload，也无新队列/数据库表。

实际执行器使用既有WSL Docker29.1.3和固定缓存镜像，在隔离工作区运行两个常量Job。第一次应用关闭后原Job及容器保持running，重建应用收集唯一文字产物；第二Job取消请求仍running，确认退出后cancelled，不生成后续文件。两容器先归档再清理，保留结果。不是模型自主任务/科学重算。

## 写入与证据

- agent.py调度失败、异常/启动对账、原子认领；runtime/session_repository.py失败/领取；jobs.py状态投影/用户处理/保持skip；contracts/app/cli及JobDeliveryControl.schema.json。
- JobsPanel.tsx新状态和按钮；对应Edge新增一条attention流程。测试辅助test_runtime_hardcrash的claimed崩溃期望更新为needs_attention，原同event/唯一Job/确认后不重投断言保持，不放宽目标。
- delivery-attention-20261026 XML/log：新增与受影响原Job续接/消费/预算/输入优先/硬退出均通过，软件模型，无真实推理。
- build71816 exit0：TypeScript和Vite通过；静态index-ChTFvHxF.js/index-DVBaniQc.css。Edge只运行新attention流程，通过并视觉检查截图；原取消流程不重跑。8794由Playwright回收。
- research/executor_seam_once.py首次实际运行24125 exit0，独占目录workspace/executor-seam-20261026。任务ec6f8c6d-12ab-4205-9298-899c2da53f06；Job c948ff3a-d70c-4f61-8128-d0adf6c4cbec（跨重建）、04e14c79-67a3-4c7b-a0eb-cfa4c49bfcaa（取消）均已终态/清理。完整镜像/容器/产物/日志hash在EXECUTOR-SEAM-20261026.md及原JSON；原报告hash/源码凭证executor-seam-source-20261026.json。
- 验证前触发、影响、复用范围、预算、停止条件在BASELINE-REPORT；ruff F/E9与diff检查通过。不重跑旧模型失败、校准、检索编码或科学成果。

## 限制与下一交付

自动完成投递首次派发/回合失败后即需处理，不默认发第二个相同模型任务；原生provider受控请求重试仍沿原身份/预算。新writer读回使用原event，已消费只提示报告失败，不假装计算未完成。新状态协议在旧版本不识别，回退需显式映射并保留新增事件/消息/产物；尚未完成正式旧数据迁移/回退验收。

真实执行器证据是App实例重建（旧实例正常关闭）和真实容器生存/取消，不是机器断电/Daemon重启；实际进程kill由此前独立软件合同覆盖，不混算为本批实跑。浏览器是Mock API组件，不冒充主入口完整E2E。模型自主专业流程、新数据计算入口/科学语义、完整UI/旧数据兼容、源码链测试审阅、原生产/27B窗口、阶段3正式比较与部署继续开放。

下一项：以已有正确科学产物和已冻结失败定位通用/科学完整交付缺口，同时准备迁移重入与保留新增数据的回退；不重建本批Job或原9请求真实模型试次。

资源已释放：全部本批脚本/测试/构建终态，两个所属容器inspect为空，日志与产物保留；未停止共享Docker/Ollama、无模型占用、无子Agent/跨聊天消息。
