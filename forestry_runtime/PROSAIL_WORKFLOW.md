# PROSAIL 正演、LUT 与影像反演

## 边界

Runtime 只接受已经完成辐射定标、以 0–1 无量纲反射率比例保存的 GeoTIFF。PROSAIL 能力不负责把 DN 转为反射率，也不会从波段数量猜测波段角色、用树种“默认值”补参数，或把反演结果解释成外业精度。

四个或少量离散波段通常不足以唯一约束全部 PROSAIL 参数。输出是给定参数范围、观测几何、模型设置、波段响应近似与近邻算法条件下的估计；LAI、Cab 等参数仍需独立地面数据验证。

## 三个工具

1. `simulate_prosail`：一次正演。调用方必须提供 13 个叶片/冠层/土壤参数、观测几何、`prospect_version`、`typelidf=2`、反射因子类型、传感器波段范围，以及三类输入的证据来源。输出完整 400–2500 nm 光谱 CSV 和聚合波段反射率。
2. `build_prosail_lut`：生成 LUT，不读取影像。`parameter_ranges` 必须完整且只能包含 `n/cab/car/cbrown/cw/cm/lai/lidfa/hspot/ant/alpha/rsoil/psoil`；同时显式提供 `lut_size`、`seed` 和 `uniform_random` 或 `latin_hypercube` 采样方式。输出 `prosail_lut.npz`。
3. `invert_prosail`：使用已有 LUT 对反射率 GeoTIFF 做加权近邻反演。调用方必须逐项提供 LUT 波段名到栅格波段号的映射、待反演参数、近邻数和映射证据；Alpha 波段可选。该工具不生成 LUT、不分割植被、不生成预览。

所有 `parameter_source`、`geometry_source`、`sensor_response_source` 和 `band_mapping_source` 必须定位到用户输入、已读取文件或项目知识证据。缺少来源时应先获取证据或报告条件不足。

## 检查顺序

1. 用 `inspect_raster` 核对影像格式、CRS、掩膜、scale/offset、有效值范围和明确的波段元数据。
2. 确认应用 scale/offset 后的选定波段均为有限的 0–1 反射率比例；Alpha 仅作为有效掩膜，不参与光谱匹配。
3. 冻结参数范围、几何、PROSPECT/SAIL 设置、矩形带宽近似、LUT 大小、随机种子与采样法，记录来源后生成 LUT。
4. 按证据建立 `band_mapping`，选择目标参数和 `neighbors` 后反演。
5. 独立检查输出 GeoTIFF 的网格、有效掩膜、波段描述、统计与来源标签。

## 输出解释

反演 GeoTIFF 的波段为调用方选择的 `prosail_<parameter>`，最后一层为 `spectral_RMSE`。参数层单位标记为 `model_parameter`；RMSE 单位为 `reflectance_fraction`。无效像元为 NaN，空间网格继承输入影像。

RMSE 只表示已选波段与 LUT 邻近光谱的拟合差，不代表参数估计精度。结果必须同时报告 LUT 资产、波段映射、有效/无效像元数和事先冻结的配置。
