# GABench 非卫星任务筛选台账（GIS 六题）

本台账对应 `evaluation/GROUNDED_EXECUTION_PLAN.md` 第 2.2 节最后一段：**枚举 GABench 的非卫星任务，按“输入完整、不依赖在线私有服务、最终结果可程序化判定”筛除，再按矢量处理 / 栅格处理 / 组合分析各取两题。** 本文件只做筛选与复算方法记录，**不包含任何评分器实现**（评分器是后续步骤，由其他人负责）。

机器可读版本：`evaluation/grounded_v1/gabench_candidates.json`；获取记录：`data/gabench/ACQUISITION.md`；逐文件 SHA-256：`evaluation/work/gabench_acquisition_manifest.json`。

## 0. 结论（先讲清楚）

> **题库不足。** 按上述条件筛选后，**只有 4 道题**满足「输入完整 + 不依赖在线私有服务 + 最终结果可程序化独立复算」，其中**矢量处理 2 题（9、42）、栅格处理 1 题（12）、组合分析 0 题**。规划文档要求六题、且要求矢量/栅格/组合各两题，**缺 2 题**：
>
> - **缺 1 道“组合分析”题**（0/2）：12 道组合类候选全部因「最终交付只有 PNG」或「参考规范自相矛盾 / 参考产物未发布」被排除。
> - **缺 1 道“栅格处理”题**（1/2）：两道最接近的栅格候选（21 土地覆盖重分类、41 保护状态重分类）在**已发布参考栅格**与**题面映射**之间发现硬冲突，无法同时满足。
>
> 另外，唯一可复算的**组合约束**候选（42 三维通视）虽满足筛选条件，但其输入图层 CRS 互不相容（详见 §4），**不是有效的三维通视任务**。因此“满足条件”4 题中真正可用的只有 **3 题**。
>
> 依照规划文档「若不足六题满足条件，停在题库不足，不悄悄换成合成题」，**本台账不补造、不替换任何题目**，只公布缺口。

## 1. 枚举口径与上游固定版本

| 项 | 值 |
|---|---|
| 上游仓库 | https://github.com/GeoX-Lab/GABench |
| 固定提交 | `e8c64e883bbe45e2b94515c73941e7a0837320ae`（2026-09-03T16:52:10+08:00，docs: add Apache-2.0 license）|
| 提交号来源 | `git clone --depth 1` 后 `git rev-parse HEAD` |
| 任务清单文件 | `benchmark/benchmark.csv`，Git LFS 对象，193385 字节，SHA-256 `74cb8d877a6f507899b78eb4a8442a9e54f9bad52ddd93f488cfb4e479770e6c`（与 LFS oid 一致，两种途径下载结果一致）|
| 任务总数 | **57**（ID 1–57，无缺号、无重复）|
| 仓库许可 | Apache-2.0；数据集逐个许可未标注（见 §6）|

**枚举方式**：不依据 README 或论文摘要，而是直接解析 `benchmark/benchmark.csv` 的 `ID / Domain / Task Description / Data Description / Toolchain JSON / Result / Layers` 字段，并逐个核对每个任务引用的 `dataset/...` 文件在 pin 住的提交中是否真实存在。57 个任务全部按此逐条核对。

**“非卫星任务”口径**：以任务的**数据说明是否声明使用卫星影像**为准。结果是 **3 题为卫星依赖，54 题为非卫星**：ID 3（题面明写 Landsat 8 影像）、ID 32（灾前/灾后影像 `Before_storm.tif`/`After_storm.tif`）、ID 34（`2015_Nature_Africa_PR.2000/2015.tif`，2015 年 Nature 论文的非洲疟疾风险栅格，属卫星/模型派生产品；**仓库未说明具体传感器，标记为未核实**）。另需说明：`landCover.tif`、`Protected_Status.tif`、`mc_land_cover.tif`、`land_cover.tif` 按数据说明是 NLCD 等土地覆盖产品（通常由 Landsat 派生），但**上游未声明其卫星来源**，因此这几题不归入卫星依赖，只在此注明该口径判断。

**网络/私有服务**：对全部 57 题的题面、数据说明与 Toolchain JSON 做过联网关键词扫描（http/https/download/API/Earth Engine/token/key 等），**未发现任何任务调用在线服务或私有服务**；所有工具都在本地 `toolbox/` 内，输入都在 `dataset/` 内。

**输入完整性**：55/57 题的引用输入全部存在；ID 42、43 引用的 `dataset/building.shp` **不存在**（实际为 `buildings.shp`，且参考工具链用的就是 `buildings.shp`），属题面路径名错误，执行不受阻。

## 2. 一组决定性的上游事实

**上游对“最终结果”的判定方式只有两种，其中 53/57 题只能靠视觉模型打分：**

1. `evaluation/vlm_judge.py` 第 551 行起：若 `Result` 以 `CHECK:` 开头 → 走 `evaluate_value_metric()`（读 JSON/CSV 键值与阈值比较，程序化）；**否则** → 取 `dataset/result/<Result>` 作为 GT 图，与 Agent 输出图拼接后交给 VLM（gpt-4o）按 `calculate_categorical_score()` 打分。

2. 结果分布：**53 项为 PNG**，仅 4 项不是 PNG —— ID 9（`deforestation_rate.csv`）、ID 27 / 42 / 53（`CHECK:JSON_VALUE:...`）。也就是说，**GABench 自己也只有 4 道题具备可程序化判定的最终结果**（其中 53 还不满足独立复算，见下）。


| Result 形态 | 题数 | 题号 |
|---|---:|---|

| PNG（上游只能用 VLM 比对 GT 图）| 53 | 除下列 4 题外的全部 |

| CSV | 1 | 9 |

| CHECK:JSON_VALUE | 3 | 27、42、53 |


这条事实是本轮“题库不足”的根本原因：**“最终结果可程序化判定”这一条把候选从 54（非卫星）直接压缩到 4。**

## 3. 逐题筛选表（全部 57 题）

字段说明：**家族**（矢量/栅格/组合/卫星/其他）、**输入**（是否在 pin 住的提交中齐全）、**联网**（是否需要在线/私有服务）、**最终结果**（Result 字段形态）、**判定**。

| ID | 领域(Domain) | 任务简述 | 家族 | 输入 | 联网 | 最终结果 | 判定 |
|---:|---|---|---|---|---|---|---|
| 1 | Geostatistical Analysis | 克里金插值城市热岛 + 人口密度 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 2 | Vector Spatial Analysis | 公交服务区与人口统计制图 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 3 | Raster Spatial Analysis | Landsat 影像 NBR 火烧迹地 | 卫星 | 齐全 | 否 | PNG | 排除（卫星） |
| 4 | Raster Spatial Analysis | 多因子适宜性建模求风险区 | 组合 | 齐全 | 否 | PNG | 排除（规范冲突） |
| 5 | Geostatistical Analysis | 铅中毒优化热点分析（六边形） | 其他 | 齐全 | 否 | PNG | 排除（仅图） |
| 6 | Geostatistical Analysis | 麋鹿家域（MCP/KDE） | 其他 | 齐全 | 否 | PNG | 排除（仅图） |
| 7 | Hydrological Analysis | 地面沉降与洪涝影响范围 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 8 | Vector Spatial Analysis | 消防站缓冲区覆盖缺口 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 9 | Vector Spatial Analysis | Rondônia 道路缓冲带内森林砍伐率 | 矢量 | 齐全 | 否 | deforestation_rate.csv | **保留** |
| 10 | Vector Spatial Analysis | 受保护林区风险面积 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 11 | Raster Spatial Analysis | 珊瑚/海绵分组统计与制图 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 12 | Raster Spatial Analysis | 地形起伏度（局部极差） | 栅格 | 齐全 | 否 | PNG | **保留** |
| 13 | 3D Modeling and Analysis | 大西洋海洋剖面插值 | 其他 | 齐全 | 否 | PNG | 排除（仅图） |
| 14 | Geostatistical Analysis | 北美 280K 暖事件计数 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 15 | Raster Spatial Analysis | 空间天气 TEC 制图 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 16 | Geostatistical Analysis | 北美气温二次多项式拟合 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 17 | Geostatistical Analysis | 纽约交通事故点密度制图 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 18 | Vector Spatial Analysis | 旧金山行道树四分位数计数制图 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 19 | Geostatistical Analysis | 溶解氧 KDE 插值与制图 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 20 | Raster Spatial Analysis | Tasmania 锡钨矿产远景随机森林 | 栅格 | 齐全 | 否 | PNG | 排除（参考值未发布） |
| 21 | Raster Spatial Analysis | 土地覆盖重分类 | 栅格 | 齐全 | 否 | PNG | 排除（规范冲突） |
| 22 | Raster Spatial Analysis | 美洲狮廊道加权叠加 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 23 | Spatial Data Management | 坐标取整与重投影 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 24 | Geostatistical Analysis | 香港新冠风险画像空间相似性 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 25 | Raster Spatial Analysis | 地下水脆弱性适宜性建模 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 26 | Raster Spatial Analysis | 未开发高风险区提取 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 27 | Geostatistical Analysis | OD 流随机森林交互强度回归（MSE） | 其他 | 齐全 | 否 | CHECK:JSON_VALUE:model_performance.json:mse:<6000 | **保留** |
| 28 | 3D Modeling and Analysis | 海啸风险传播时间制图 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 29 | Geostatistical Analysis | L 函数/局部 K 空间聚类 | 其他 | 齐全 | 否 | PNG | 排除（仅图） |
| 30 | Hydrological Analysis | 洪水损失成本计算 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 31 | Vector Spatial Analysis | 道路可达性比例 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 32 | Raster Spatial Analysis | 风暴前后植被损失 | 卫星 | 齐全 | 否 | PNG | 排除（卫星） |
| 33 | Vector Spatial Analysis | 路网旅行时间制图 | 其他 | 齐全 | 否 | PNG | 排除（仅图） |
| 34 | Raster Spatial Analysis | 非洲疟疾风险变化 | 卫星 | 齐全 | 否 | PNG | 排除（卫星） |
| 35 | Geostatistical Analysis | 柏林 Airbnb 局部莫兰指数 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 36 | Raster Spatial Analysis | 地形起伏度标准化 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 37 | Raster Spatial Analysis | 不透水面分地块面积统计与回连 | 组合 | 齐全 | 否 | PNG | 排除（参考值未发布） |
| 38 | Geostatistical Analysis | 贷款数据热点分析 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 39 | Spatial Data Management | 无家可归人口州级汇总制图 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 40 | Geostatistical Analysis | 海草分布随机森林预测 | 其他 | 齐全 | 否 | PNG | 排除（仅图） |
| 41 | Raster Spatial Analysis | 保护状态重分类 | 栅格 | 齐全 | 否 | PNG | 排除（规范冲突） |
| 42 | 3D Modeling and Analysis | 三维建筑通视分析 | 矢量 | 缺 `building.shp` | 否 | CHECK:JSON_VALUE:los_stats.json:visible:==True | **保留** |
| 43 | 3D Modeling and Analysis | 三维建筑体块合并可视化 | 矢量 | 缺 `building.shp` | 否 | PNG | 排除（仅图） |
| 44 | Hydrological Analysis | DEM 填洼 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 45 | Hydrological Analysis | 汇流累积量 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 46 | Hydrological Analysis | 洼地深度分析 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 47 | Hydrological Analysis | 下游流向长度 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 48 | Hydrological Analysis | 上游流向长度 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 49 | Hydrological Analysis | 河网矢量化 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 50 | Hydrological Analysis | Strahler 河流分级 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 51 | Hydrological Analysis | Shreve 河流分级 | 栅格 | 齐全 | 否 | PNG | 排除（仅图） |
| 52 | Hydrological Analysis | 流域盆域划分 | 组合 | 齐全 | 否 | PNG | 排除（仅图） |
| 53 | Raster Spatial Analysis | 矿产远景随机森林 AUC | 栅格 | 齐全 | 否 | CHECK:JSON_VALUE:*rf_auc_metrics.json:roc_auc:>=0.9 | 排除（参考值未发布） |
| 54 | Vector Spatial Analysis | OD 重力模型出行意愿线 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 55 | Vector Spatial Analysis | 路网交通量分配 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 56 | Vector Spatial Analysis | 固定起终点最短路径 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |
| 57 | Vector Spatial Analysis | 学校—市场服务区覆盖 | 矢量 | 齐全 | 否 | PNG | 排除（仅图） |

> 家族计数（按输入类型）：矢量 16、栅格 19、组合 12、卫星依赖 3、其他（纯表格/网络）：7。


## 4. 保留候选的独立复算方法

以下 4 题通过 §1 的三条筛选条件。复算方法的共同约束：**评分器只读题目输入与 Agent 交付物，用本项目自己的代码重算参考值，绝不调用被测 Runtime 的任何业务函数。** 目前环境（Python 3.14）只装有 numpy / rasterio / scipy / PIL，**没有 geopandas、shapely、pyproj、pandas、sklearn、netCDF4**，因此本地实际完成的独立复算只有 ID 12（完全一致）与 ID 42（几何量核对）；ID 9 只核对了算术，ID 27 无法独立复算。

### 4.1  ID 12｜Raster Spatial Analysis｜家族：栅格

- 题面：Your task is evaluating mountain lion habitat suitability, and the first step is calculating terrain ruggedness using elevation data. Visualizing the ruggedness…
- 上游 Result 字段：`ruggedness.png`；参考图层：`rugged_Elevation.tif`
- 输入文件（1 个，全部已在 pin 住的提交中获取）：`dataset/Elevation.tif`(31468285 B)

**独立复算方法**

独立复算方法（确定性，已在本机完整复算并与已发布参考产物逐像元比对）：(1) rasterio 打开 dataset/Elevation.tif，按 float64 读取第 1 波段 z（该文件 dtype=uint8、无 nodata 元数据、3062x2494、30 m、EPSG:26911）；(2) 对 z 施加 3x3 窗口的 local range 滤波（等价 scipy.ndimage.generic_filter(z, lambda v: nanmax(v)-nanmin(v), size=(3,3))，即每个像元邻域最大值减最小值）；(3) 结果即参考对象 rugged_Elevation.tif。本地验证：按上述步骤重算得到 min=0.0、max=36.0、无 NaN，与已发布 dataset/ruggedness.tif 逐像元完全相等（max|diff|=0.0，一致率 100%）。判定：比对提交栅格的网格（宽高/仿射/CRS 与 Elevation.tif 相同）、dtype 与逐像元整数相等（容差 0）。必须固化的约定：不要把 255 当作 nodata（该文件未声明 nodata；参考产物确实把 255 当普通高程参与计算），否则结果不同。

### 4.2  ID 9｜Vector Spatial Analysis｜家族：矢量

- 题面：Your task is to find the deforestation rate for the Brazilian state of Rondônia. The goal is to calculate the percentage of deforested area within a 5.5 km buff…
- 上游 Result 字段：`deforestation_rate.csv`；参考图层：`deforestation_rate.csv`
- 输入文件（2 个，全部已在 pin 住的提交中获取）：`dataset/deforestedArea.geojson`(21033068 B), `dataset/roads.geojson`(14959869 B)

**独立复算方法**

独立复算方法（确定性，已在环境内核对算术一致性）：(1) 读取 dataset/roads.geojson 与 dataset/deforestedArea.geojson；题面规定无 CRS 时按 EPSG:4326 处理；(2) 两者重投影到 EPSG:32723（UTM 23S），参考实现使用该 CRS；(3) 道路 buffer 5500 m；(4) 缓冲面 dissolve 后求面积 A_buf；(5) 以 deforestedArea 裁剪 dissolve 缓冲面，求交集面积 A_def；(6) 参考值 = A_def / A_buf。参考工具链记录的中间量为 A_buf=179792795540.404 m^2、A_def=86176671033.82933 m^2、比值=0.4793110356552816，与已发布 dataset/result/deforestation_rate.csv 逐位一致（本地已核对：86176671033.82933/179792795540.404 == 0.4793110356552816）。判定：读取提交的 deforestation_rate.csv 中 percentage_deforestation 列，按相对容差（建议 rtol=1e-6，绝对下限 1e-9）比较。未完成的部分：运行时环境缺 geopandas/shapely，未能用独立实现实际重算并比对，标记为“算术已核对、几何未独立重算”。

### 4.3  ID 42｜3D Modeling and Analysis｜家族：矢量

- 题面：This task performs a 3D line-of-sight (LOS) analysis between two specified points (Observer ID 0 and Target ID 2) from a 3D point shapefile, using 3D building d…
- 上游 Result 字段：`CHECK:JSON_VALUE:los_stats.json:visible:==True`；参考图层：`los_stats.json`
- 输入文件（3 个，全部已在 pin 住的提交中获取）：`dataset/building.shp`（**提交中不存在**）, `dataset/buildings.shp`(184100 B), `dataset/point3d.shp`(232 B)
- ⚠ 输入路径缺陷：题面/CSV 引用的 dataset/building.shp 在 pin 住的提交中不存在；实际文件是 dataset/buildings.shp（MultiPatch 建筑体块），参考工具链本身使用的就是 buildings.shp，因此执行不受阻，但题面路径名错误

**独立复算方法**

独立复算方法（确定性；控制算法与输入几何均已在本地核对）：(1) 从 dataset/point3d.shp（PointZ，3 个要素，FID/索引 0/1/2）取索引 0 与 2 的 (x,y,z)：observer=[-13278.191700000316, 6712896.986199997, 36.0]、target=[-13440.065900001675, 6712930.1426, 160.0]；(2) 从 dataset/buildings.shp（MultiPatch，56 个要素，5172 个顶点）取全部顶点作为障碍点；(3) 对每个障碍点：跳过与 observer 或 target 的 2D 距离 < 0.5 m 的点；计算该点到 observer-target 2D 线段的距离，> 2.0 m 则跳过；否则在线段参数 t（裁剪到 [0,1]）处求视线高度 sight_z = Az + t*(Bz-Az)，若 Pz >= sight_z - 1e-3 则判定被遮挡；(4) 无遮挡则 visible=true，写入 los_stats.json。已发布 dataset/result/los_stats.json 为 {"visible": true}。判定：读取提交的 los_stats.json 的 visible 布尔值做精确比较。重大语义缺陷：point3d.prj 为 WGS_1984_Web_Mercator_Auxiliary_Sphere（obs/target 在约 (-13.3 km, 6712.9 km)），buildings.prj 为 British_National_Grid（顶点在约 (530.7 km, 181.2 km)），两层 CRS 完全不同且流程从不重投影；本地实测：5172 个顶点中 1237 个坐标值等于 -1.797e308(DOUBLE 最小值哨兵)，其余顶点到该 2D 线段的最小距离为 6553765.20 m，位于 2 m 阈值内的顶点数为 0，因此 visible=true 由“无任何障碍点通过筛选”得出，与真实三维通视无关。结论：可复算，但该题不是有效的三维通视任务；如需使用必须重做数据（统一 CRS）并重新冻结版本。

### 4.4  ID 27｜Geostatistical Analysis｜家族：其他（表格/模型，非 GIS 家族）

- 题面：Your task is to estimate the interaction strengths between subregions using a Random Forest model. First, load the OD flow data and socio-economic attribute dat…
- 上游 Result 字段：`CHECK:JSON_VALUE:model_performance.json:mse:<6000`；参考图层：`model_performance.json`
- 输入文件（2 个，全部已在 pin 住的提交中获取）：`dataset/od_data.csv`(147863 B), `dataset/socioeconomic_data.csv`(5703 B)

**独立复算方法**

部分可程序化：上游规则为 CHECK:JSON_VALUE:model_performance.json:mse:<6000（读取 JSON 键 mse 并做数值比较）。参考值：{"mse": 5679.815885878322, "best_params": null}（dataset/result/model_performance.json）。不能独立复算的原因：参考产物只剩 28_best_model.joblib 未发布、tune_model_hyperparameters 的搜索网格与随机种子未定义；仅 split_train_test 的 random_state=42 已知。因此只能复刻整条流水线（aggregate_od_flows -> prepare_model_data -> split(random_state=42,test_size=0.2) -> GridSearchCV -> MSE）得到一个“同量级”的 MSE，不能保证与参考值一致。

### 4.5 已做过的本地验证记录

| 验证 | 方法 | 结果 |
|---|---|---|
| ID 12 逐像元复算 | rasterio 读 `Elevation.tif` 为 float64，scipy `generic_filter(size=(3,3))` 求邻域极差，与已发布 `ruggedness.tif` 比对 | **完全一致**：max\|diff\|=0.0，一致率 100%，min=0、max=36、无 NaN |
| ID 9 算术 | `86176671033.82933 / 179792795540.404` 与已发布 `deforestation_rate.csv` 的 0.4793110356552816 比对 | **逐位一致**（`0.4793110356552816`）|
| ID 42 控制点 | 直接解析 `point3d.shp`（PointZ）几何，与参考工具链写死的 observer/target 坐标比对 | **完全一致**（索引 0 → `[-13278.191700000316, 6712896.986199997, 36.0]`；索引 2 → `[-13440.065900001675, 6712930.1426, 160.0]`）|
| ID 42 障碍点 | 解析 `buildings.shp`（MultiPatch，56 要素 / 5172 顶点），按参考算法筛选 | 1237 个顶点坐标等于 -1.797e308（DOUBLE 最小值哨兵）；**落在 2 m 阈值内的顶点数为 0**，最近顶点距线段 6553765.20 m → `visible=true` 由“无有效障碍”得出 |
| ID 21 复算 vs 参考栅格 | 按题面映射逐像元重分类 `landCover.tif`，与 `landCover_reclassified.tif` 比对 | 33676 像元不一致，**全部来自 code 90（19262）与 code 95（14414）**：题面写 →3，参考栅格实际是 →4 |
| ID 41 类合并 | 统计 `Protected_Status.tif` 各输入码在参考栅格中的输出 | code 4(363658) + code 255(3615622) = 参考栅格类别 10 的 3979280 像元，**两类不可区分** |

## 5. 为什么不足六题：缺口与最近的落选者

| 家族 | 需求 | 满足全部条件的题数 | 差距 |
|---|---:|---:|---|
| 矢量处理 | 2 | **2**（ID 9、42）| 0 |
| 栅格处理 | 2 | **1**（ID 12）| −1 |
| 组合分析 | 2 | **0** | −2 |
| 合计 | 6 | 4 | **−2**（且家族结构也不对）|

换句话说：即使只要求总数六题，也差 2 题；按“每族两题”的结构要求，矢量够、栅格差 1、组合差 2。

### 5.1 最接近的“栅格处理”落选者

- **ID 21**：近似命中：方法完全确定（题面给出 11->10,21->8,22->7,23->8,24->9,31->6,41->2,42->1,43->2,52->3,71->3,72->3,81->4,82->6,90->3,95->3,255->10），输入 dataset/landCover.tif 为 uint8、3062x2494、30 m、EPSG:26911、无 nodata，共 17 个取值 [0,11,21,22,23,24,31,41,42,43,52,71,81,82,90,95,255]；实做后发现唯一冲突是湿地方向：题面 90->3、95->3，而已发布 dataset/landCover_reclassified.tif 中 90、95 全部为 4（本地逐像元比对：我的复算与参考栅格不一致的 33676 像元恰好就是 code 90 的 19262 个与 code 95 的 14414 个；其余 126 个 code 全部一致，含 41->2、21->8、42->1、52/71->3，且 0 与 255 原样保留）。
- **ID 41**：近似命中：映射确定（0->1,1->3,2->6,3->9,4->10,255->10），输入 dataset/Protected_Status.tif 为 uint8、3062x2494、30 m、EPSG:26911、无 nodata；但 4 与 255 都映射到 10，已发布 dataset/Protected_Status_reclassified.tif 中类别 10 共 3979280 像元 = input 4(363658) + input 255(3615622)，两类被合并，无法区分。
- **ID 36**：近似命中：3x3 focal range 后 min-max 标准化到 [1,10] 完全确定，输入 Elevation.tif 已获取；但发布产物只有 ruggedness_norm.png，且 Elevation.tif 无 nodata 元数据（0 与 255 语义未定义）。
- **ID 14**：近似命中：count_netcdf_spells 为确定性算法（阈值 280K、连续 5 年），输入 E1_north_america.nc 已获取；但发布产物只有 temperature_statistic_vis.png。

### 5.2 最接近的“组合分析”落选者

- **ID 4**：近似命中：题面写明了完整重分类表与加权公式（vulnerable = drainage*5 + water_depth*4；risk = vulnerable*8 + land_cover*10、EPSG:32126、以 mc_boundary 为范围、以 mc_land_cover 为像元与 snap），输入 mc_land_cover.tif（uint8、3025x3896、30 m、nodata=255）与 mc_soils.shp 均已获取；但“vulnerable_areas 标准化到 [1,10]”与参考工具链的“先加权、再 min-max 标准化、最后与 land_cover 加权”顺序冲突，且 canonical final_risk.tif 未发布。
- **ID 37**：近似命中：calculate_area_above_threshold（toolbox/Spatial.py:948）对每个地块用 rasterio.mask 裁出窗口、以像素面积 0.25 m^2（impervious.tif 为 EPSG:2246、0.5 m 分辨率、nodata=255、取值 {0,1}）统计 value>0 的像元数并乘像素面积，再按 Parcel_ID 回连地块；流程确定，但 canonical 产物 parcels_impervious_join.geojson 未随仓库发布，参考值无法核对。

### 5.3 其他被“参考值未发布 / 非确定性”挡住的候选

- **ID 27**：虽被保留（见 §4.4），但严格说它只满足「最终结果可程序化读取 + 阈值判定」，**不满足“可独立复算同一参考值”**；若按更严的“参考值必须可由输入确定性重算”口径，它应从保留名单中剔除，届时保留数降为 3 题（矢量 2、栅格 1、组合 0）。
- **ID 53**：最终交付 data_rf_auc_metrics.json 的 roc_auc 可程序化读取（上游规则 >=0.9），但参考模型未发布（data.joblib 缺失）、tune 参数未定义，无法独立复算同一参考值
- **ID 35**：最终交付仅 PNG（moran_local.png）；Local Moran's I 的参考实现（PySAL/esda）与随机化参数未发布
- **ID 5**：最终交付仅 PNG（blood_lead_hotspots_hex.png）；Optimized Hot Spot Analysis 的参考实现未发布，参考值不唯一

### 5.4 若要补足六题，需要上游或本项目补什么

1. **组合分析两张**：需要上游为某个“矢量输入 + 栅格输入”任务**发布 canonical 中间产物**（例如 ID 4 的 `final_risk.tif`、ID 37 的 `parcels_impervious_join.geojson`），或**消除题面与参考实现的规范冲突**（ID 4 的标准化顺序、ID 25 的越界/nodata 处理）。在此之前任何复算都只能得到“同量级”而不是可判定的参考值。

2. **栅格处理一张**：需要上游修正 ID 21 的湿地方向映射（题面 90/95 →3 与参考栅格 →4 的冲突），或把 ID 41 的输出类别改成不产生 255 与 4 合并的编码，或为 ID 36 / ID 14 补发 canonical 栅格/数值产物。

3. **矢量处理（可选加固）**：ID 42 若要保留，必须先统一 `point3d` 与 `buildings` 的 CRS 并重新冻结版本；否则应替换为另一道“数值型交付”的矢量题——但 54 道非卫星题中**再无第三道具备非图像、可独立复算的交付物**，因此本项目无法自行补足。

4. **不得做的事**：不得用合成题、OAM-TCD 题或“把 PNG 改成栅格重判”来充数——后者会改变题目语义与冻结版本，属于替换题目，不符合规划文档的“不悄悄换成合成题”。

## 6. 许可

- 上游仓库：**Apache License 2.0**（Apache-2.0 (LICENSE in repo root)）。

- **数据集许可：未核实。** 上游 `dataset/` 未逐项标注许可/来源/再分发条款，仓库内也检索不到逐数据集 LICENSE 或 README。本台账对以下数据一律记为「许可未核实」，使用前需向原始数据提供者确认：`landCover.tif`、`Protected_Status.tif`、`mc_land_cover.tif`、`land_cover.tif`（NLCD 类土地覆盖）、`G_2014/G_2015.tif`（Landsat 8）、`2015_Nature_Africa_PR.*.tif`、`Elevation.tif`、`AtlanticDEM.tif`、`CatalinaBathymetry.tif`、`Global_ocean_measurements.shp`、`berlin_listings.csv`（Airbnb）、`HamiltonDemographics.geojson`、`CensusBlock.geojson`、`ShikokuPopulation.geojson`、`homeless_data.xlsx`、`A1B_north_america.nc`、`E1_north_america.nc`、`roads.geojson`/`deforestedArea.geojson`、`net.shp`/`city_Net_Junctions.shp`/`supermarkets.shp`/`school.shp`/`Marketplace.shp`/`network.shp`、`buildings.shp`/`point3d.shp` 等。

- 本台账与获取脚本不更改、不再分发上述数据，只在本地 `data/gabench/` 内保留上游副本用于复算与评分器开发。

## 7. 可复现的获取路径

```powershell
# 1) 克隆（本机默认 TLS 校验被拦截代理破坏，只读克隆时禁用校验；完整性用 SHA-256 校验）
git -c http.sslVerify=false clone --depth 1 https://github.com/GeoX-Lab/GABench.git data/gabench/repo
git -C data/gabench/repo rev-parse HEAD   # 期望 e8c64e883bbe45e2b94515c73941e7a0837320ae

# 2) 只取任务清单（LFS media 端点；GitHub 的 zip/raw 只会给你 130 字节指针）
Invoke-WebRequest -Uri "https://media.githubusercontent.com/media/GeoX-Lab/GABench/e8c64e883bbe45e2b94515c73941e7a0837320ae/benchmark/benchmark.csv" -OutFile benchmark.csv
# 期望 SHA-256 74cb8d877a6f507899b78eb4a8442a9e54f9bad52ddd93f488cfb4e479770e6c / 193385 字节

# 3) 本地已核对：data/gabench/repo sha256 逐文件清单
#    evaluation/work/gabench_acquisition_manifest.json（276 个文件，1.42 GiB）
```

获取细节（含关键文件 SHA-256 表、TLS 说明、许可提示）见 `data/gabench/ACQUISITION.md`。

## 8. 本台账没有做什么

- **没有实现任何评分器**（符合任务约束：评分器是后续步骤）。§4 只是「复算方法说明」，不是可执行代码。

- **没有改动** `data/oam_tcd/`、`runtime/`、`evaluation/cases/`、`evaluation/verify/` 或任何既有测试；本轮新增文件仅位于 `data/gabench/`、`evaluation/grounded_v1/`、`evaluation/work/`。

- **没有把 PNG 交付改判为栅格交付**来抢救候选：那会改变题目语义，属于替换题目。

- **没有凭记忆描述任何题目**：57 题的家族、输入、Result 形态均来自 `benchmark/benchmark.csv`（LFS 实体）与 `dataset/` 实际文件，且在正文中给出了可复核的判据（像元计数、SHA-256、文件大小、行号）。

