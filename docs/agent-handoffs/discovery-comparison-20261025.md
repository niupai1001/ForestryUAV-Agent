# 工具目录检索对照 / 主Agent

批次20261025，实际日志为2026-10-26 UTC。离线比较已完成；真实模型任务收益与生产默认未裁决，未部署。
目录C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1；基线3eb534011ece8a6df5d0ddc9ae19a075a536c7b6及前序未提交改动保留。

## 本批交付

research/discovery_benchmark.py对真实注册目录/人工首选工具查询冻结输入hash，比较全目录、人工类别上界、BM25、embedding、RRF；后者支持严格模型/目录/查询hash和维度校验，未知资产不计0分。类别上界不是模型分类，schema字节不是token/总请求成本。

research/discovery-queries-v1.json为20条英汉开发查询，不作为留出或正式统计。原目录23工具、15隐藏；无web/实验ROI，pointcloud只发现已有能力。freeze只启动同步组件装配，不启任务/模型/worker。

v1为17/20，原漏q05/q06/q18保留。只在job_tools.py的code_execute/process_lidar_canopy及memory_tools.py的read_user_memory原定义docstring补用途语义，未改执行逻辑、分词权重或按query硬路由。v2同集20/20，这是已见开发集修复，不是泛化证明。

真实E5与混合各20/20；v2平均schema字节：全目录13979、类别9196.2、BM25为9045.6、embedding为9493.7、混合9473.65。组合报告直接复用lexical-v2，不再计算它；混合8条已存lexical＋16条dense、RRF60，参数事先记录。当前无额外目标召回收益，不把embedding加入产品；native_bm25候选仍待真实任务比较，默认static保持。

## 资产/运行及证据

官方E5多语言模型卡/MIT/编码方式和ONNX目录已核对，见DISCOVERY-COMPARISON。固定revision614241f622f53c4eeff9890bdc4f31cfecc418b3，qint8 ONNX与tokenizer等6文件共135431472字节逐项官方LFS/Git hash验证。fetch脚本输出目录存在就拒绝重新下载，源manifest/日志保留。

独立workspace/retrieval-embedding-20261025/packages，hash锁onnxruntime1.31.0/tokenizers0.23.3/numpy2.5.4；没有更改产品venv/pyproject/uv.lock（这些文件已有前序修改，不等于本批安装造成）。没有Torch或HF远程Python执行。CPU2线程/batch8，模型只看工具元数据与query，无expected工具标签/用户影像。

encode_discovery_once.py一次编码35文本，原started凭证和vectors保存，含资产/目录/查询/源码/依赖hash、CPU provider、384维及实际计时。首次成功，模型进程已退出，无GPU/Ollama占用。没有重开两次旧token校准预算或Qwen对话。

日志/JSON/XML详见DISCOVERY-COMPARISON证据入口。discovery-screen-unit/embedding-pool/discovery-reuse通过；ruff F/E9通过。实际asset获取会话19307已exit0；无活句柄。原lexical源文件按SHA快照保存，后续组合功能新增不重跑旧结果。

## 下一项与剩余

优先核实Ollama实际模型窗口/Thinking，不能用架构最大窗口或配置32768替代生效证据。随后冻结真实任务对照输入、代码/工具目录、模型digest、权限及总预算，分别验证static与A原生BM25。原手工BM25仅前序原型，真实收益与旧会话兼容核对后再移出生产重复路径，不现在盲删或默认启用。

此批不替代H0/H1/H2、small/large、正式重复/消融/留出，也不代表WP2/WP5完整完成。跨进程/硬崩溃、UI/执行器、科学新数据自主交付、发布迁移/回退门继续保留。

不要重新编码同资产/目录/查询或重建同失败试次。报告更正直接读JSON/原向量；需要新版本先说明变化与预算。无服务启动/部署、无科学重算、无子Agent或跨聊天消息。
