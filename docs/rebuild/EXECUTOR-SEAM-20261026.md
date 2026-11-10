# 当前版本真实执行器接缝

2026-10-26 UTC。本批已完成真实Docker的跨应用重建、取消退出确认、归档与所属容器清理；**不是模型自主工作流或科学计算验收，也未部署主入口。**

使用既有配置的WSL发行版forestry-uav-agent-runtime、Unix Docker端点与固定code_image，没有安装、拉取镜像或重启共享Docker。Docker实际版本29.1.3，已缓存镜像ID sha256:df18428df8a0f9189c84e7424d214b4c50092af1f9dca5842a00c41c39bda316。镜像引用及执行资源见workspace/executor-seam-20261026/config.json，version/image原始stdout/stderr保留；WSL stderr含编码不完整的代理提示，不改写成执行错误。

独立工作区workspace/executor-seam-20261026/runtime。API实际使用create_app/TestClient、enable_agent=False；用户任务与Engine.perform/Jobs.submit/真实Docker通道均为当前实现。无模型调用，不把测试身份后端当成真实模型证据；Job代码只睡眠/写固定文字，不读科学数据。

| 操作 | 原始身份和实际结果 |
|---|---|
| 任务 | ec6f8c6d-12ab-4205-9298-899c2da53f06，累计动作2、模型请求0 |
| 跨应用重建Job | c948ff3a-d70c-4f61-8128-d0adf6c4cbec |
| 重建前后同一容器 | 4ac71e593ca064f28d0490cf0fe56b4103819133c52f8fffb4223c2bfc6601ad，前后均running，最后succeeded |
| 唯一封存产物 | d72e9c22-e872-4b31-b779-86ae1fd0578f，resume-proof.txt，37字节，SHA256 5dd3f791590841e94a67f5012969b7148ff0a3ea56a90777ef36837f14b5b076 |
| 取消Job | 04e14c79-67a3-4c7b-a0eb-cfa4c49bfcaa，容器52670d72ed35802042e79cff239079fcc4cffb853e0e8dc5d552c7057bd541ee |
| 取消确认 | API回执仍running且cancel_requested=true；执行器tick确认退出后才cancelled，后续文件未生成，产物0 |
| 清理 | 两容器先归档日志再删除，cleanup.done=true；inspect均不再存在，原37字节产物仍封存 |

应用第一次关闭时不杀后台Job，第二个应用获得同一工作区owner后读取原Job，沿原容器ID对账并收集结果。整个数据库仅2个Job，不新建替代任务掩盖恢复。第二Job仍在60秒睡眠时取消，保留真实终态而非直接改数据库假成功。所有断言首次通过，未重复试次。

证据在workspace/executor-seam-20261026：executor-version/image、config、两submission、started、restarted-job、terminal、cancel-intent与各Job cleanup JSON；最终report.json；命令日志docs/rebuild/executor-seam-20261026.log。执行脚本research/executor_seam_once.py使用唯一目录防重建，预算每Job120秒/观察90秒/最多2 Job。源码与脚本及原报告hash绑定于executor-seam-source-20261026.json，记录于终态后且期间没有运行源码改动。

实际句柄24125已exit0，本批两容器均确认清理，没有本批活作业/服务。没有停止共享Docker、Ollama或未知资源。已有科学产物、原真实模型失败和校准预算全部保留；这个接缝不替代ODM/PROSAIL新数据自主流程、真实模型报告、完整浏览器主入口或发布迁移验收。
