# A驱动真实任务单遍筛查：static与native_bm25

2026-10-26 UTC。两条试次已结束，**均未完成原任务**；默认继续static，不启用native_bm25、不追加真实试次。不是正式统计/留出比较，也不代表新数据科学能力验收。

## 配置和实际窗口

原权重qwen3.5:4b，digest d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04；研究别名forestry-v2-4b-ctx32768-20261026:latest，digest 49144205011ed15f4437f33a19f5c46c677a0aac93c30e287734bfd0c7bdf6e8。官方兼容API文档要求用模型配置设置窗口，不能把应用context_limit当服务已接受的参数。[官方说明](https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size)

本机Ollama 0.40.7实际接受/api/create的from＋parameters.num_ctx。源和别名Modelfile的两个FROM blob与源本机manifest模型/投影层一致，未下载、重量化或修改原标签；模板和thinking元数据相同。别名没有出现在预期本地manifest目录，首次prepare因此失败，原日志保留；只读finalize继续已成功创建的别名，没有重放create。分词器对别名缺manifest显式回退，不伪造绑定；这属于当前比较限制。

**两次任务推理期间/api/ps均实际返回context_length=32768、相同alias digest、Q4_K_M、llamacpp、size_vram=4220549856。** 它证明本次别名请求的运行窗口，不证明原生产标签、未来配置或27B窗口已验证。实际SDK正文两边为temperature0.2、max_completion_tokens4096、reasoning_effort=medium；原生思考实际保存，布尔模型的命名档位语义依据show及兼容API，不把medium当独立第三种硬件思考档位。

## 任务、协议与预算

原目标完整保留在workspace/discovery-task-20261026/protocol.json：结合已保存方法笔记与既有真实PROSAIL统计，核对LAI/Cab/光谱残差/像元/坐标、定标与波段、边界与适用性、现场精度及独立验证，保存中文Markdown报告并交付下载链接。统计和笔记分别从已审计catalog投影，原catalog SHA256 b59e883aabcc263abd0d8304002c10bc69e6d70f19900f32ea739e6f8dc38675；未重算科学成果。

两边A/H1、同目标/输入/方法笔记/授权能力/软件source hash，隔离工作区，仅发现模式变化。static实际首请求工具含read_user_memory；native首请求含search_tools但不含隐藏记忆。每边12请求/30动作/900秒，总24请求/60动作/1800秒，禁止code.execute，因此真实科学/容器Job均0。protocol SHA256 d16c899b5c471c5bb3dffaba8b3129a5a96842cb6de56d58203718821a691088。实际执行脚本已按SHA归档references/retrieval/discovery_task_once-6c85efe23b7d72b408adfdb1281b027b64ea20e96abcb6626f70de09631ffb6f.py；后续仅删除未用赋值消除F841，不能把当前脚本字节冒充原试次。

## 实际结果

| 项目 | static | native_bm25 |
|---|---|---|
| task_id | 8ec0230d-2a72-4dc9-87bc-6115e3241ddb | feb8e53f-5832-41c0-a3fa-991663231ac1 |
| 模型请求/动作 | 6 / 6 | 3 / 2 |
| 累计provider输入/输出tokens | 42575 / 4728 | 11520 / 1778 |
| 墙钟 | 40.094秒 | 19秒 |
| 方法笔记 | 实际memory.read成功 | 未调用发现/读取，错误称笔记未提供 |
| 文件交付 | 两次使用/output绝对路径失败，产物0 | 未写文件，产物0，但伪造artifact下载链接 |
| 软件终态 | waiting_user | waiting_user |
| 原目标是否完成 | 否 | 否 |

token为多次请求累计，不能拿42575与单请求32768窗口比较。耗时和token更少伴随任务缺失，**不是检索收益**；单任务单遍不能推导稳定成功率、显著性或p95。

## 按预登记标准审阅实际内容

1. 统计/CRS：两边转述了来源中的LAI均值约0.965、Cab约68.66、光谱残差约0.038、4935083像元和EPSG:32650。没有重新计算验证栅格。
2. 方法来源：static确实读取方法笔记；native未发现却把可用笔记说成缺失，且未提供真实笔记引用，失败。
3. 定标：static区分ODM camera归一化与独立验证；native缺方法证据，泛化为需要FLAASH/6S等处理，未证明适用于本航次。
4. Cab边界：static写入草稿将约70说成典型森林饱和阈值、最终称约束影响显著；native写“通常70–80”并猜高光照/定标等原因。这些数值范围与因果强度没有本次证据支持。两边均不能通过完整科学解释验收。
5. 现场精度：两边都明确不能宣称现场LAI精度，static明确区分光谱拟合残差与现场误差。这一子项通过不抵销其余失败。
6. 实际保存/链接：两边均无报告产物。static误把相对路径问题归因输入授权；native的0797bdf7-e31a-4e36-b50c-e053c421a06d不在任何本任务产物回执中，下载不可用。
7. 无重算：两边jobs_count=0，未触发容器/科学计算；这一约束满足。

原最终正文分别另存discovery-task-static-answer-20261026.md、discovery-task-native_bm25-answer-20261026.md，仅为研究输出证据，含上述错误，不作为可信科研报告交付。

## 决定与后续修复

本任务没有证明native_bm25提高真实任务质量，且暴露隐藏记忆遗漏，默认static保持。离线20/20不能覆盖真实自主发现失败。A驱动选择不因此自动重开B/C，首先修本地通用调用与交付边界、继续科学方法说明和证据约束。

两条试次均终态后才修产品：write_file明确任务相对路径与输入权限分离；最终根回答将内联Markdown产物链接纳入当前任务已封存身份/哈希核验，不能用artifact_ids=[]绕过。report-delivery-20261026的软件原生模型/工具接缝验证实际写入、未知/跨任务/合法链接及原输出重试预算；没有重跑这两条真实试次，不能声称模型已学会正确交付或科学问题已修好。

证据：workspace/discovery-task-20261026下原before/alias请求与响应/show/protocol、两目录started/config/report/runtime-window/release/after；docs/rebuild/discovery-task-results-20261026.json绑定报告hash/指标；同名prepare/finalize/static/native日志；guard与report-delivery XML/log。两次after.models均空，只卸载本批研究别名，未停止Ollama，别名配置保留用于追溯。当前无本批活进程或模型占用。

仍缺：原生产标签窗口、27B条件、完整真实发现/专业报告流程、新数据自主计算、H0/H1/H2及阶段3正式比较、UI/执行器/兼容迁移与发布。未经新条件和新协议登记，不重开本次失败预算。
