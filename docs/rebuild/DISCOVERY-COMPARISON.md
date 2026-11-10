# 工具发现对照：开发集目录检索

批次ID：20261025，实际执行日志跨2026-10-26 UTC。状态：**离线目录检索与真实CPU embedding比较完成；真实Agent任务收益/生产默认切换未完成。**

## 冻结范围与方法

research/discovery-queries-v1.json人工标注20条英汉开发查询，覆盖正射/LUT/LAI、已有LAZ/冠层、代码/表格、文档/图像、知识/笔记和依赖环境。它是开发集，不能称留出或统计显著结果。点云只覆盖已有能力发现，没有恢复点云后续开发或执行科学作业。

目录从application_tools的真实注册函数，经Pydantic Tool生成schema与ToolRegistry.descriptors投影。23个注册工具，其中8个基础工具常驻、15个参与隐藏工具检索；web与实验ROI排除。目录冻结使用隔离空工作区和仅用于登记能力的假镜像身份，没有任务、模型、Worker或科学计算启动。

比较全注册目录、人工类别上界、BM25@8、E5向量@8、RRF混合@8。人工类别是已标注上界，不是自主路由分类器，也不是新的用户开关。目标集合是人工首选能力；未标注候选数不是实际错误调用或越权率。权限仍由运行守卫决定。

计量为注册schema JSON的UTF8字节，**不是token/provider实测输入**，不包括原生计划/溢出读取、search工具与system/history开销。真实任务上下文和成功率需另测。

## 结果与原始漏项

| 目录版本 / 方法 | 目标全部找到 | 平均注册schema字节 |
|---|---:|---:|
| v1 全目录 | 20/20 | 13750.00 |
| v1 人工类别上界 | 20/20 | 9077.05 |
| v1 BM25@8 | 17/20 | 8845.60 |
| v2 全目录 | 20/20 | 13979.00 |
| v2 人工类别上界 | 20/20 | 9196.20 |
| v2 BM25@8 | 20/20 | 9045.60 |
| v2 E5 embedding@8 | 20/20 | 9493.70 |
| v2 混合RRF@8 | 20/20 | 9473.65 |

v1漏q05冠层/树顶/单木、q06表格空白值与单位换算、q18英文个人笔记。原排名和失败保留discovery-lexical-v1-20261025.json。对应工具说明只写了实现术语/输入目录/英文标识符，缺清楚用途；修正是在原注册函数docstring加入真实能力语义，未增加按查询固定选工具或同义词特例，未改检索权重/阈值/返回数量。

v2仍用原20条查询，只换冻结目录。开发集修复后20/20不能证明泛化/留出性能。BM25相对v2全目录节省约35.3%注册schema字节；不能把它写成总上下文token节省或端到端加速。

组合报告复用已保存v2 lexical/类别结果，不重算；因为原报告仅保留lexical top8，混合协议在查看新排名前明确为lexical8＋dense16、RRF常数60、返回8，区别于脚本无基线模式默认16/16。所有实际设置在最终JSON中保留。

## 真实embedding身份和资源

选用官方[intfloat/multilingual-e5-small模型卡](https://huggingface.co/intfloat/multilingual-e5-small)所述多语言模型；模型卡标MIT许可。编码按其查询/文档前缀、attention mask均值池化、L2归一化及512截断方式实现；未加载远程Python代码。官方[ONNX导出目录](https://huggingface.co/intfloat/multilingual-e5-small/tree/614241f622f53c4eeff9890bdc4f31cfecc418b3/onnx)提供本次量化资产。

- 固定revision：614241f622f53c4eeff9890bdc4f31cfecc418b3。
- qint8导出：model_qint8_avx512_vnni.onnx；SHA256 dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88。
- tokenizer SHA256：0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39。
- CPUExecutionProvider，384维；指定2线程/batch8，本批35条文本（15工具＋20查询），到512上限的文本为0。expected工具标签未传入编码模型。
- 6个资产共135431472字节，官方LFS/Git内容hash逐项核对；只下载一次，保留manifest。研究target独立安装onnxruntime1.31.0/tokenizers0.23.3/numpy2.5.4，完整wheel hash锁归档，不改变产品venv/pyproject/uv.lock。
- 单批本地编码记录含加载共0.734秒、加载0.343秒；这不是与BM25同条件的延迟比较、吞吐或峰值资源基准。输出/started凭证存在则拒绝同身份重跑；没有追加推理凑结果。

量化/运行库/池化实现会限定本次结果，不能冒充完整精度权重或其它模型的结论。没有Qwen对话、GPU模型占用或科学作业；模型进程已退出。

## 本批取舍及未完成门

当前开发集上embedding/混合没有比修正后的BM25多召回目标，注册schema字节还略多。因此本批不将其升为产品依赖或引入常驻向量服务。研究资产与向量保留，后续无需重新下载/安装/编码来更正报告。

后续候选采用A原生ToolSearch＋中文BM25，手工BM25只是前序原型；但**生产默认仍static**，未仅凭这组开发查询切换。须先核实实际模型窗口/Thinking，固定同模型/输入/权限/预算，比较真实任务质量与请求信封成本，才能决定默认模式及移除重复激活路径。H0/H1/H2、small/large、正式重复/消融/留出仍按执行计划冻结独立试次，不用本表替代。

## 证据入口

- discovery-catalog-v1/v2-20261025.json：冻结目录；discovery-queries-v1.json在research目录，SHA绑定于结果。
- discovery-lexical-v1/v2-20261025.json：原始两版筛查，不覆盖。
- discovery-e5-v2-vectors-20261025.json及.started.json：真实单批编码，输入/模型/源码/包锁身份。
- discovery-comparison-v2-20261025.json：复用lexical原结果并新增向量/混合排序，含baseline SHA与各查询排名。
- discovery-embedding-assets-20261025.json、discovery-embedding-requirements-20261025.lock：资产/依赖记录；实际资产与target在workspace/retrieval-embedding-20261025。
- discovery-screen-unit、embedding-pool、discovery-reuse的XML/log：漏项保留、非法/不匹配向量拒绝、池化与禁止重算原基线。
- 原lexical脚本按源码SHA归档references/retrieval；实际比较脚本与单次encoder保留于research。
# 后续真实任务筛查（20261026）

离线结果不替代真实任务效果。后续同权重/实际32768窗口的static与native_bm25单任务筛查均未完成保存报告：static读到记忆但用绝对输出路径失败；native未发现记忆且伪造未保存下载链接。原失败/预算保留，默认static继续，详见[实际任务报告](DISCOVERY-TASK-SCREEN.md)。19秒/更少tokens不算质量收益；报告交付边界只补软件修复，不重跑原真实试次。

