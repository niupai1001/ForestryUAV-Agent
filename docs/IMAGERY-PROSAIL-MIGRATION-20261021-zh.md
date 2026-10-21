# 旧版正射与PROSAIL迁移

## 本轮范围与验证前记录

- 用户2026-10-21确认：迁移1.0正射光谱拼接及PROSAIL LAI反演并测试；点云后续开发暂缓；联网搜索、领域峰值资源/吞吐评估取消。
- 触发：当前未接入这条完整业务链，旧源码与已有方法可复用；批量摄影测量不应把所有航片像素放入语言模型上下文。
- 影响：影像输入清单、旧ODM适配、LUT/反演适配、现有Jobs/产物/恢复、相关计划和开发文档。
- 复用：已验证通用Agent与Jobs、来源/产物权限、原生思考、上传/删除/清理、点云科学结果。旧正确影像/反演结果若找到则直接读回复用，不为迁移重新计算。
- 停止条件：接口定向检查与可用输入的真实迁移流程通过；缺少原始数据/运行时就明确记录阻碍，不以合成数据冒充原航片拼接或真实精度验收。只复核本轮改变/失败项。

## 已找到的旧实现

- 旧仓库：`../ForestryUAV-Agent`，当前HEAD `059b239`；不修改其未提交文件。
- 历史提交`f62a418`包含`uav_prosail_baseline/uav_tools`，提供inspect_flight、submit_orthomosaic、orthomosaic_status、finalize_orthomosaic。旧当前分支已移除内置摄影测量执行，不能把旧README当作当前可运行后端。
- 当前旧目录仍有`forestry_runtime/runtime/capabilities/prosail/service.py`的正演、LUT、加权KNN反演，以及单独的`run_prosail.py`示例。参数/波段映射、SRF近似和几何必须沿用有来源配置，不自动猜测。
- 旧实际路径：`E:\0730-多光谱正射(晴天飞行)\1_原始数据`与`E:\UAV_WORKSPACE`；用户确认旧原始记录已经删除，改用N盘新数据，不再寻找或声称复用了旧场景数值结果。

## 输入原理

航片目录/正射GeoTIFF/NPZ LUT以只读文件引用进入计算作业。模型接收有界清单、元数据和检查摘要，必要时只看预览；ODM/PROSAIL读取文件像素，计算大小不计作聊天token。自然视觉读图与科学文件处理是两条输入路径，不能因为文件后缀是图片就全量注入视觉上下文。

## 已实现的迁移

- `legacy_flight_metadata.py`和`legacy_extract_flight_parameters.py`恢复旧航次元数据/几何提取逻辑，记录历史提交来源；不移植旧Agent循环或后台进程执行器。
- `imagery.py`及`imagery_schema.py`提供inspect/build_lut/invert/orthomosaic操作；`POST /tasks/{task_id}/imagery`、CLI `imagery --task ... --request ...`与Agent原生`process_imagery`共用同一入口。模型自己按目标选择，未增加用户工作方式开关。
- `imagery_worker.py`保留旧场景脚本的参数采样顺序、随机种子、矩形波段响应、标准差归一化单最近邻和可选NDVI掩膜。**不是**后来工具层的加权KNN；两种方法没有混作一次迁移。
- 新增256×256窗口读取，避免条带TIFF整图加载；应用GeoTIFF scales/offsets，保留CRS/transform、NaN/有效掩膜，输出LAI、Cab、真实反射率光谱RMSE和≤1024边长预览。Cab单位为μg/cm²，光谱RMSE不是现场LAI精度。
- 旧NPZ的`reflectance/lai/cab`兼容；无波段名称时必须由有来源声明明确其列顺序。新LUT附波段、参数、声明；已有正确LUT直接作为只读产物输入。
- 影像与ODM各有独立固定镜像字段，不替换已冻结PDAL/lidR镜像。Jobs沿用现有权限、输入版本、同会话产物、取消、持久化、相同配方复用及终态容器清理。配置更新不隐式改写旧会话的镜像身份，使用新镜像须创建新会话。
- TIF/TIFF/DNG发送时仅给有界引用，不自动视觉解码。PNG/JPEG日常读图仍走已有视觉入口；科学计算可处理大文件，与模型聊天窗口分开。`input_refs={"flight":"文件夹ID"}`会在后端展开当前≤200张顶层航片，逐个派生只读文件挂载，不要求模型展开长清单；超过范围明确拒绝，不静默挑选部分。
- ODM适配以同一Jobs内前台子进程执行，记录命令/输入SHA/日志/产品元数据；成功且验证网格后只清理本作业内已知暂存与可重建缓存，保留最终产品及清单。失败不冒充成功，也不自动重建相同失败作业。

## 新授权数据与参数依据

目录：`N:\Users\yzh\Data\0无人机数据分享\0无人机数据分享\多光谱影像`。80张TIFF，20个CaptureUUID组，每组Green/Red/NIR/RedEdge各一张，DJI M3M；拍摄时间2024-07-18 08:47:45–08:50:54。全部有Irradiance、暗角和畸变元数据；这不等于独立验证了辐射定标准确度。

按中国拍摄地的**UTC+8时间假设**，旧NOAA近似公式估算太阳天顶角中位数48.17096°、航高中位数59.999m；PROSAIL试验明确采用正射近天底观测0°/相对方位0°近似。矩形半宽复用旧方法及BandFreq token，完整厂家SRF并不存在，不能把近似响应当实测SRF；旧提取器把width token标为nominal_fwhm的字段只作原始历史字段，不据此断言完整光谱响应。

证据：`workspace/imagery-migration-20261021/flight-inspection.json`与`metadata/flight_parameters.json`、`capture_metadata.csv`、`band_metadata.csv`。

## 验证与冻结结果

1. 新TIFF分流与新入口的定向检查通过：80个附件没有自动解码，镜像按任务冻结、未授权来源拒绝、同配方请求复用同一Job。未重跑基础/点云流程。
2. 复用本机旧镜像`sha256:d54664352b9d40d8d4d21cc38349dbd4623f1202177b41ff7dadc9756ca33969`，含numpy2.2.5/scipy1.15.3/rasterio1.5.1/prosail2.0.5。没有重复安装科学依赖。
3. 实际Jobs已生成本航次几何下500行LUT（seed20260914，旧场景探索性先验）。Job `982409af-aa4f-4b24-b894-36353bee7c4b`，LUT产物`25d390bd-196c-4e62-bc49-8aa73f5c069a`。不因后续文档更新或ODM准备重建该LUT。
4. 数值契约Job `cca70ddc-4329-4b6a-949c-83716e0219b1`成功：精确LUT光谱取回对应LAI/Cab、RMSE=0、Alpha/越界/NaN排除、13个有效像元、CRS/网格保持。这是构造输入的软件数值不变量，**不代表N盘航片的LAI结果或现场精度**。模型请求0。汇总在`workspace/imagery-migration-20261021/numerical-validation.json`。
5. 因新增工具Schema，针对原600×600图片做隔离副本的离线预算检查；最初版本27478，追加恢复参数与声明引导后的**最终部署版本27616≤28672**，28个工具，视觉部分489；模型请求0，原会话未变。最终证据`context-with-imagery-final.json`，前一版本记录不与最终版本混算。最终诊断先遇到SDK单次run_id复用冲突，改为独立诊断身份完成测量，未重置真实试次预算。文件夹展开定向检查已通过，未重跑其它已通过项。

## 真实运行与完成范围

旧原生ODM运行时已不存在。官方仓库不存在`3.6.0`标签（manifest unknown），改用文档默认标签下载，已冻结`opendronemap/odm@sha256:c0d9a0c56fcb97277b9a9f0c30f2c274afb14344ec378c9bb1d231623017596b`，本地image ID `sha256:dec08da40c58f67796a77b57029dc50d01e627fa2f2704e9057d470982a4fbb5`。不会把文档版本当镜像存在证明。官方依据：[ODM安装/调用](https://github.com/OpenDroneMap/ODM)、[多光谱定标说明](https://docs.opendronemap.org/multispectral/)。

### 真实运行发现与定向修复

| 问题 | 原因 | 修复与复用 |
|---|---|---|
| 80张航片挂载后容器状态JSON报错 | 完整Docker inspect重复描述挂载，超过执行器64KiB返回上限并被截断；不是模型token超限 | Docker只投影身份、状态和运行限制，保留原容器ID与权限检查；原Job创建后恢复读取并首次启动，没有重新提交 |
| ODM纹理阶段PermissionError | DrvFS可写文件但容器65534用户无法复制POSIX权限位，`shutil.copy`数据复制完成后copymode失败 | 保留非root、只读root、断网限制；只对本作业project内普通文件的EPERM复制元数据做兼容，数据复制错误仍抛出；已生成SfM、稠密云、网格不重算 |

原真实作业`cddf2b58-84ab-4b04-865c-fbf1bcb14542`保留failed，383份产物约2.97GB封存，任务`7cbf2d4f-75b8-4439-8076-93b17943e0f1`。新增`resume_job_id`将同会话、同镜像、同声明、原来源版本未变的失败作业产物验SHA后封装为只读恢复包；校验重建和网格存在，再用ODM已有检查点继续，不传科学重建的rerun/reset参数。恢复有新代码和明确故障原因，不是重复相同失败试次；原失败记录及LUT均保持。

恢复Job`295fc935-b35c-4ebb-b7a7-198b8cfe9d4b`生成了正确正射/DSM，随后在报告静态资产复制权限位时失败。限定源为本project或固定镜像/code、目标为本project普通文件后，Job`a10b8b0d-2a6d-4423-86af-c914755173c0`**仅从odm_report修报告**并成功，没有重算正射或前期科学产品。反演Job`f3fa2551-f5e5-4903-b3b4-cd52ee9c3ae2`复用原LUT成功。

交付正射/DSM 3118×1888、EPSG:32650、约5cm；实际波段Red/Green/NIR/RedEdge/未命名Alpha，按[1,2,4,3]匹配LUT，4,935,083像元有模型估计。独立验读CRS/transform/网格与float32/NaN一致。发现GDAL掩膜all_valid不能替代浮点Alpha：另只读核对，692348个透明像元中有效LAI为0，**已有正确反演未重做**；后续入口补按colorinterp自动识别未命名Alpha。

完整结果、下载路径、科学质量信号和原失败/恢复记录见[实跑报告](IMAGERY-RESULT-20261021-zh.md)；用户/API操作见[使用说明](IMAGERY-USAGE-zh.md)。Cab均值贴近先验上界、LAI预览空间变化较少，属于需要核查先验/SRF/定标适用性的实际质量信号，不以换参数重跑掩盖，也不宣称现场精度。已验证的是专业工具链与文件引用处理；真实Qwen自主规划并完成整条科学对话尚未验收。已有模型300秒无首段输出仍是独立未解决问题，本迁移未对其重建试次。

### 清理与部署收口

首次清理发现Windows根目录本身短、子文件深路径超过限制时，普通rmtree仍失败。`native_path(force=True)`仅用于已核验归属的递归清理目标；Jobs与会话删除共用修复。新增深路径隔离夹具验证删除且外部原文件保留；随后只重试两个未清理作业，全部成功。本轮终态容器/重复输出副本已回收，科学产物/恢复包/原失败证据保留，N盘原文件未删除。见`terminal-cleanup-final.json`。恢复可选字段省略空值，原请求重放仍返回原failed Job、新增Job0，见`old-request-replay.json`。

已部署原`http://127.0.0.1:8769/`；升级前后43个会话、110个Job保持。本地部署源码SHA256：`cd5af482502d60ece43b0ad783a8079583c28bafb42afe34c5ace365d5d59688`。只读能力回执确认两个影像方法已配置，搜索关闭。使用时刷新工作台并新建会话以冻结影像镜像；未替换旧会话科学配置或删除真实会话。
