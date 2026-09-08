# 验证记录

## 2026-09-08：像元检查与候选林冠分割 0.4

- 建立Git基线提交`ffe2550`，并在`codex/raster-inspection-canopy-baseline`分支开发。
- 新增`inspect_raster`：分块精确统计掩膜、非有限值、零值、负值、范围和均值，使用确定性抽样分位数，并检查Red/NIR/Alpha空间重合关系。
- 当前真实286 MB正射影像检查表明：Band 5由color interpretation识别为Alpha；1,967,921个Alpha=0像元与Red、NIR同时为零及NDVI零分母像元完全重合，Alpha>0区域无零分母。
- 新增`segment_canopy`初始基线：仅接受`calculate_ndvi`产物，默认从512-bin NDVI直方图计算Otsu阈值，输出0/1/255 uint8候选Mask及像元、比例和投影面积统计。
- 13项自动测试全部通过，包括Alpha/零分母重合统计、显式阈值、自动Otsu、Mask类别与空间参考、非NDVI输入拒绝。
- 在真实NDVI临时副本上得到Otsu阈值0.525390625；有效像元14,445,751，候选像元13,167,320，占有效区91.1501%，候选面积11,848.63平方米。该结果仅为高NDVI植被候选区，不作为最终林冠覆盖率。
- 本地qwen3.5:4b完成`inspect_file → inspect_raster → calculate_ndvi → segment_canopy`。额外的快速`inspect_file`记录为工具效率问题，不影响数据和产物正确性，后续在Benchmark中评估。

## 2026-09-07：Open WebUI原生推理呈现 0.3.1

- Pipe将Runtime的thinking事件映射为OpenAI兼容的`reasoning_content`增量，由Open WebUI生成原生推理面板。
- 工具开始/结束改用Open WebUI状态事件；完整参数与结果不再作为HTML和转义JSON写入回答正文。
- 保留旧轨迹清除逻辑，旧对话中的`data-forestry-trace`内容不会进入后续模型上下文。

## 2026-09-07：NDVI与执行可见性 0.3

- 新增`calculate_ndvi`专业工具：按窗口读取Red/NIR，应用源数据scale/offset，联合检查两波段掩膜、有限值和非零分母，输出保留网格与坐标系的float32 GeoTIFF。
- Runtime启用Ollama原生thinking；Open WebUI折叠显示模型思考、工具名、参数、真实结果和耗时。界面轨迹不会再次进入模型上下文。
- 12项自动测试通过；新增NDVI数值、无效分母、地理参考、父资产关系和波段标签冲突测试。
- 真实qwen3.5:4b端到端验收通过：`inspect_file → calculate_ndvi`，无多余工具调用；合成影像期望NDVI均值与输出实测均为0.5，返回thinking事件。
- Open WebUI真实流式接口验收通过：响应包含可见的“模型思考”折叠区及最终回答；测试聊天随后删除。
- 已只读核验当前286,655,678字节真实附件：5214×3148、Band 1=Red、Band 3=NIR、EPSG:32650；未在验收中替用户生成不可见的孤立NDVI资产。

## 2026-09-07：会话资产生命周期 0.2

- 已部署Runtime 0.2、更新Pipe，并启用Open WebUI Event Function `forestry_cleanup`。不向模型注册删除工具。
- 基础及新增测试共11项通过：包括活跃工具期间延迟清理、另一聊天隔离、删除状态重启恢复、晚到产物登记、所属用户与路径校验、旧目录保留。
- 真实HTTP验收：新建聊天和附件 → Pipe → Qwen → read_text/save_text → Open WebUI报告下载，内容核对通过。
- 删除A后，A的工具产物自动删除；B仍引用的原件及B的Runtime资产保留。删除B后，原件自动删除，两边清理记录完成。
- Runtime离线时删除测试聊天，然后重启Open WebUI及Runtime：后台自动补清理，无需手动启动监控。
- 归档测试通过：跨一个清理周期后两边文件仍可下载；删除该归档聊天后文件自动清理。
- 仅接管更新后新建的普通聊天及本次版本登记的新文件。旧对话、旧数据卷不迁移、不清理。
- 实测为API链路验收；不将其描述为浏览器点击验收。当前仅支持单实例Runtime和SQLite Open WebUI。
- 资产位置：项目 `data/sessions/<chat_id>/`；`data/lifecycle.sqlite3`保留少量删除标记及待清理文件ID。

复测脚本：`scripts/verify_lifecycle.py`（运行于Open WebUI容器）。模式依次为 `prepare`、`delete-a`、`delete-b`；恢复测试使用 `prepare-recovery`、停Runtime、`delete-offline`、重启两服务、`verify-recovery`。这些模式仅删除自行创建的验收聊天。

部署命令：`setup.ps1` 更新两项函数后重启Open WebUI，以确保后台函数采用新代码。后台重试间隔10秒，Runtime清理检查间隔2秒；工具仍在执行或文件仍有其他引用时会延迟删除。

验证环境：2026-09-05，Windows Docker Desktop；Open WebUI 0.11.3；Ollama 0.33.2；qwen3.5:4b。

## 已通过

- 6项自动测试：资产持久化与隔离、上传大小限制、真实TIFF及预览、ZIP安全检查/解压和文本读取、模型工具结果回传、API认证与下载、未知工具和轮数上限（部分测试项在同一用例中）。
- Open WebUI管理员API已确认 `forestry_runtime` 模型可见，Pipe已启用。
- Open WebUI容器可访问runtime健康接口；runtime可调用宿主机Ollama。
- TIFF（image/tiff、process=false）、PNG（image/png、process=false）、ZIP（application/zip、process=true）按当前聊天上传器的组合上传成功；ZIP的Open WebUI处理状态返回completed，文件均能到达Pipe。
- 端到端：合成TIFF/PNG/ZIP上传 → Qwen选择真实工具 → 读取尺寸/CRS、ZIP解压、读取文本 → 保存Markdown → 通过Open WebUI文件接口下载并核对内容。
- 预览端到端：生成64×48 PNG、下载并检查真实像素尺寸；下一轮通过上一轮的文件链接重新登记并读取文件。
- Runtime容器健康运行，文件存储在持久化Docker卷；运行代码可正常解析。

## 实测边界

- 第一次模型报告曾将32650误解释为南半球，并自行算错网格面积。工具现返回CRS名称与程序计算的网格面积，提示词要求直接引用；这不代表4B模型的所有专业解释都已经可靠。
- 模型曾生成占位下载链接。运行时现剥离模型输出中的Markdown链接，只由适配器附加已保存文件的真实链接。
- 一次跨轮预览回答没有包含验收要求的尺寸，重复测试通过。模型措辞/工具选择仍有随机性，不能把一次成功当作稳定性基准。
- 浏览器自动化工具在本机初始化失败；本轮完成的是当前Open WebUI真实HTTP接口、原版上传器源码对应行为和下载内容验证，未进行浏览器点击/截图验收。
- 数据均为合成验收数据，未验证GB级真实航测影像吞吐量或生产并发。

没有添加任务状态、执行日志数据库或后台任务模块。

## Pipe 0.1.1：大图片上传修复

- 初版适配器对Base64文本设置40000000字符上限，折合约30 MB原图，与runtime的512 MiB限额不一致。
- 改为512 MiB可配置限额，分块解码到临时磁盘文件；根据SHA-256复用同时作为附件和内联图片传入的文件。
- 新增32 MiB解码完整性、边界大小和非法编码测试；不再依赖一次性解码整个图片到内存。
- 真实HTTP端到端通过：33,579,368字节TIFF同时以Open WebUI附件和Base64图片传入，Qwen通过工具返回4096×4096尺寸、单波段和WGS 84 / UTM zone 50N；runtime按校验值检查仅登记一份资产。
