# 领域指南逐条核验台账

本文件为维护者核验资料；Runtime 只读取状态作内部排序，不把状态、来源或评分注入模型上下文。
`verified` 仅表示该行有直接可核查的权威来源；`unverified` 表示尚无逐条核验证据；
`wrong` 保留已纠正或删除的旧说法。来源必须直接支持本行完整表述，否则保持 `unverified`。
修改指南正文后运行 `python scripts/sync_guide_verification.py`；正文变化会生成新 ID 并重置为未核验。

| id | guide | claim | status | source | action |
| --- | --- | --- | --- | --- | --- |
| Cbea70a614f1f9491 | biomass-and-carbon | 树冠面积、树高、点云密度和植被指数可作预测变量，**不直接等于**地上生物量（AGB）或碳储量。 | verified | https://doi.org/10.3390/f14102095 | 保留 |
| Cab7e6d8a8a4cf88c | biomass-and-carbon | 估算需要适用于目标树种/林型/尺度的异速生长方程或由外业样地标定的统计模型； | verified | https://doi.org/10.3390/f14102095 | 保留 |
| Cfb61133475de1696 | biomass-and-carbon | 不能把未经验证的通用系数套到任意森林。 | unverified | 待核验 | 改写 |
| C41077c2d7c43cf12 | biomass-and-carbon | 先明确目标是单木、样地还是区域总量，保持标签与预测变量同一空间支持。 | unverified | 待核验 | 改写 |
| C3d0f2908a22b50a9 | biomass-and-carbon | 异速方程选择、胸径/树高测量、样地位置、模型误差、单木遗漏和向区域外推都会贡献不确定度。 | verified | https://doi.org/10.1016/j.rse.2018.07.022 | 保留 |
| C22b4283f505d371b | biomass-and-carbon | 报告模型来源、适用范围、验证样地、偏差/RMSE及不确定区间； | unverified | 待核验 | 改写 |
| Caf2c5e63be682d15 | biomass-and-carbon | 跨林型和传感器须重新验证。 | unverified | 待核验 | 改写 |
| C3146b9e96e760a7c | biomass-and-carbon | 没有外业或可信校准资料时，可以提供结构指标、相对高低分布与可执行的采样设计，不能把它们改名为已验证的吨/公顷生物量或碳储量。 | unverified | 待核验 | 改写 |
| Cf25b7b832e1b8344 | canopy-metrics-and-ecology | 林冠覆盖度（canopy cover）通常指树冠垂直投影占地面面积的比例； | verified | https://www2.cifor.org/mla/download/publication/Assessing%20canopies.pdf | 保留 |
| C0beda9d90f6dca80 | canopy-metrics-and-ecology | 林冠闭合度（canopy closure）指从某个地面点仰视时树冠遮挡天空半球的比例。 | verified | https://www2.cifor.org/mla/download/publication/Assessing%20canopies.pdf | 保留 |
| C43016b550462e739 | canopy-metrics-and-ecology | 二者视角与采样单位不同，不能混称。 | verified | https://www2.cifor.org/mla/download/publication/Assessing%20canopies.pdf | 保留 |
| C558ff0982267b3c0 | canopy-metrics-and-ecology | 俯视正射影像更直接支持前者； | unverified | 待核验 | 改写 |
| C5dfc1fe5661e29cd | canopy-metrics-and-ecology | 仰视鱼眼照片、冠层分析仪等用于后者。 | unverified | 待核验 | 改写 |
| C42c840eff6864d3c | canopy-metrics-and-ecology | 不同法规或调查规程对“郁闭度”的定义可能另有规定，报告时写出操作性定义。 | unverified | 待核验 | 改写 |
| Ca8dfe7dd7dcc6071 | canopy-metrics-and-ecology | “树冠覆盖率”也不等于“植被覆盖率”：后者可包含灌木、草本与作物； | unverified | 待核验 | 改写 |
| Cee3f10486aa96da1 | canopy-metrics-and-ecology | 不等于叶面积指数，后者表达单位地表面积上的叶面积； | unverified | 待核验 | 改写 |
| Cf92b936f384ea274 | canopy-metrics-and-ecology | 也不等于林木株数。 | unverified | 待核验 | 改写 |
| C259a2e49d0e877be | canopy-metrics-and-ecology | 树冠重叠时，覆盖率按**投影并集**计一次，不能把各单木冠幅面积简单相加后除以地块面积。 | unverified | 待核验 | 改写 |
| C8a9c9efce412d574 | canopy-metrics-and-ecology | 研究区和有效区需要预先确定。 | unverified | 待核验 | 改写 |
| Cd7693b3c85c40c7c | canopy-metrics-and-ecology | 仅对正射图有效像元取分母会得到“影像有效区覆盖率”； | unverified | 待核验 | 改写 |
| C1dbc5ce15ef7c991 | canopy-metrics-and-ecology | 若用户要求整个地块覆盖率，地块内的缺测区域需要单独标注，不能静默缩小分母。 | unverified | 待核验 | 改写 |
| C25ff4b612234eb83 | canopy-metrics-and-ecology | 跨时相比较须使用一致的研究区、树冠定义、季节/物候和质量标准。 | unverified | 待核验 | 改写 |
| C86e932b3ee683d21 | control-and-georeferencing | GCP 参与几何调整，独立检查点不参与调整而用于评估成果误差； | verified | https://docs.opendronemap.org/gcp/ | 保留 |
| Ca5b7a5ea6d4794f3 | control-and-georeferencing | 用训练模型的同一批点同时声称独立精度会产生乐观结果。 | unverified | 待核验 | 改写 |
| Cc801e41dab9759c3 | control-and-georeferencing | 控制点要覆盖项目水平范围及高低地形，并在影像中清晰可辨； | verified | https://docs.opendronemap.org/gcp/ | 保留 |
| C2c4c75567fc25052 | control-and-georeferencing | 数量与布局依据面积、地形和重叠设计，不存在对所有林区都足够的固定点数。 | unverified | 待核验 | 改写 |
| Cbac164dd33a2079b | control-and-georeferencing | 检查点报告应区分水平与垂直误差，并写明测量仪器、坐标系、垂直基准、匹配方法和残差分布。 | unverified | 待核验 | 改写 |
| C690d491b83f86756 | control-and-georeferencing | RTK/PPK 相机定位可改善几何约束，但状态码或设备规格不是本次成果的独立精度评估。 | unverified | 待核验 | 改写 |
| C3c2b63be5bfc9657 | crown-detection-evaluation | 检测框用于定位候选树木，树冠掩膜用于面积与边界，树顶点用于位置； | verified | https://doi.org/10.3390/f17020179 | 保留 |
| C3bec573470b1a739 | crown-detection-evaluation | 框面积不能直接作为精确冠幅面积。 | unverified | 待核验 | 改写 |
| Ce55bc7ab5e027db9 | crown-detection-evaluation | 评价前约定预测与参考树冠的一对一匹配、空间容差或 IoU 阈值。 | unverified | 待核验 | 改写 |
| Cbe26fa8dea56b417 | crown-detection-evaluation | 漏检、误检、相邻冠合并和单冠拆分应分别统计。 | unverified | 待核验 | 改写 |
| Cb17d2b2c3b4a2e39 | crown-detection-evaluation | 语义分割像元精度与单木实例召回率回答不同问题； | verified | https://doi.org/10.3390/f17020179 | 保留 |
| C753ae93d374250e6 | crown-detection-evaluation | 总体像元准确率高不能证明每棵树都被找到。 | unverified | 待核验 | 改写 |
| C3ce893ad2593c4d1 | crown-detection-evaluation | 参考标注和测试区域须独立于训练，按林型、密度、冠层重叠和树冠大小分层检查误差； | unverified | 待核验 | 改写 |
| Cfe71db74fd17cae7 | crown-detection-evaluation | 没有参考标注时只能给候选与人工质检，不得给实测召回率。 | unverified | 待核验 | 改写 |
| C94aafb56953366fe | fire-severity-and-recovery | 烧毁范围是空间分类，烧毁程度描述生态/植被受损程度，后续恢复描述随时间的变化； | verified | https://doi.org/10.3390/rs14194714 | 保留 |
| C96ef632e2edb3fbf | fire-severity-and-recovery | 不能把黑色像元面积直接称作严重度。 | unverified | 待核验 | 改写 |
| C290d0ba8db6ca754 | fire-severity-and-recovery | RGB 可识别部分炭化、灰烬和冠层损失； | unverified | 待核验 | 改写 |
| C034175e7a01d5f37 | fire-severity-and-recovery | 含 NIR/SWIR 的多光谱可支持相应指数。 | unverified | 待核验 | 改写 |
| C26726e46c9a6f6cc | fire-severity-and-recovery | 使用 dNBR 前必须确认 NIR 与 SWIR 波段及火前/火后影像，RGB 无法计算标准 dNBR。 | verified | https://doi.org/10.3390/rs14194714 | 保留 |
| Cb60e02b149c29708 | fire-severity-and-recovery | 阴影、裸地、深色岩石和火前林况会造成混淆。 | unverified | 待核验 | 改写 |
| C7d8cfc438671eedd | fire-severity-and-recovery | 严重度等级需要说明参考标准和外业/高质量参考验证。 | unverified | 待核验 | 改写 |
| C933104f061fec944 | fire-severity-and-recovery | 恢复监测应控制季节、时间间隔、配准和研究区，区分草本返青与乔木恢复。 | unverified | 待核验 | 改写 |
| Ce41d40f0f8c4354d | forest-gaps-and-habitat | 林隙提取前须定义最小面积、研究尺度、冠层高度或覆盖阈值； | unverified | 待核验 | 改写 |
| C3b69dc9f8b45a0cf | forest-gaps-and-habitat | 不同定义会得到不同林隙数量和面积。 | unverified | 待核验 | 改写 |
| Ccd978a86b6dd62fe | forest-gaps-and-habitat | RGB/正射可描述可见冠层开口，CHM/LiDAR 可增加高度和垂直层次信息； | unverified | 待核验 | 改写 |
| C9c5546da3d12b6fd | forest-gaps-and-habitat | 阴影或裸地不自动等于生态林隙。 | unverified | 待核验 | 改写 |
| C093418ec7e7acd00 | forest-gaps-and-habitat | 结构异质性指标可作为栖息地条件的代理变量，但不能直接等于物种丰富度、种群数量或栖息地质量。 | verified | https://www.sciencedirect.com/science/article/pii/S0378112723006102 | 保留 |
| C834b59f96823870a | forest-gaps-and-habitat | 建立生态关系需要独立物种/生境观测，并考虑林型、空间尺度和观测偏差。 | unverified | 待核验 | 改写 |
| C3db51baac6551389 | forest-health-and-stress | RGB 可发现失绿、褐变、落叶和死亡冠等**可见症状**； | verified | https://doi.org/10.3390/rs14133205 | 保留 |
| C51b079c575a85355 | forest-health-and-stress | 多光谱/高光谱可能对色素和冠层生理变化更敏感； | verified | https://doi.org/10.3390/rs14133205 | 保留 |
| C852e97c698c76423 | forest-health-and-stress | 热红外可提供温度异常线索。 | unverified | 待核验 | 改写 |
| Cd2d5831fce502aff | forest-health-and-stress | 但异常可能由干旱、物候、阴影、坡向、土壤、相机处理或病虫害多种因素造成，单期影像不能单独确定病因。 | unverified | 待核验 | 改写 |
| Cdf6af7c07db1d4a1 | forest-health-and-stress | 应先提出明确的目标类别和观测窗口，检查光照、空间分辨率、树冠分割与对照区，再用独立外业诊断/多时相证据验证。 | unverified | 待核验 | 改写 |
| C51361955d1ca4aee | forest-health-and-stress | 可输出“疑似异常树冠/优先巡查区域”，不得把模型概率或指数阈值直接称为病害确诊率。 | unverified | 待核验 | 改写 |
| C126eb1619f88b542 | forest-health-and-stress | 变化监测须避免把季节、降雨后温度、太阳角和不同相机标定带来的差异当作真实健康变化。 | unverified | 待核验 | 改写 |
| C316ecaf383ee4ead | forest-inventory-sampling | 明确总体、抽样单元和目标量：单木、样地、像元和区域面积不可混作同一种统计单位。 | unverified | 待核验 | 改写 |
| Cfba063465b6232db | forest-inventory-sampling | 地图精度评估应有概率抽样或说明偏离概率抽样的后果； | verified | https://doi.org/10.1016/j.rse.2014.02.015 | 保留 |
| Ced151350e51bf8ac | forest-inventory-sampling | 参考标签在时间与空间上要足以代表被评估单元，并尽量独立于制图过程。 | unverified | 待核验 | 改写 |
| C6e0563ecac8af265 | forest-inventory-sampling | 对类别面积，应同时报告地图直接计数与基于参考样本的面积估计，尤其在分类误差明显时； | verified | https://doi.org/10.1016/j.rse.2014.02.015 | 保留 |
| C3b2a019b28d04743 | forest-inventory-sampling | 给出混淆矩阵、用户/生产者精度及不确定区间。 | verified | https://doi.org/10.1016/j.rse.2014.02.015 | 保留 |
| C7e8eb509980d921f | forest-inventory-sampling | 缺少独立参考样本时可报告地图内部统计量，不能报告经验证的分类精度或把地图面积当作无误差真值。 | unverified | 待核验 | 改写 |
| C1476ce13f44ec24a | forest-method-boundaries | CHM 的基线是 `DSM - DTM`，要求同一水平网格、同一高程单位、同一垂直基准。 | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| C10abc44a7e462b15 | forest-method-boundaries | 这只约束高度模型路线。 | unverified | 待核验 | 改写 |
| C29882afb1e4d5cde | forest-method-boundaries | 若任务是**俯视树冠分布或覆盖率**，可用 RGB 正射影像分割 可见树冠，不必先有 CHM； | unverified | 待核验 | 改写 |
| C617056efdba6d149 | forest-method-boundaries | 详见 `rgb-canopy-cover` 与 `canopy-metrics-and-ecology`。 | unverified | 待核验 | 改写 |
| C9b125a8e8dad08c9 | forest-method-boundaries | 负值必须保留并报告其比例：负 CHM 比例会暴露配准偏差、地面模型偏差和插值问题。 | unverified | 待核验 | 改写 |
| Cfdf5d3befab27881 | forest-method-boundaries | 不得静默截断为零。 | unverified | 待核验 | 改写 |
| C7e670f35f98346e3 | forest-method-boundaries | 在密林条件下，DTM 的地面分类可能成为主要误差来源，必须记录 地面分类来源（例如 OpenDroneMap 的 `dtm` 参数）以及水平坐标系、高程单位和垂直基准。 | unverified | 待核验 | 改写 |
| C65a428b5378a9f61 | forest-method-boundaries | 标记分水岭把像元值当作地形，从给定标记向外扩张，直到区域在水岭线相遇。 | verified | https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_watershed.html | 保留 |
| Cb8a578ef7db15a61 | forest-method-boundaries | 因此峰值位置与数量直接决定候选结果； | verified | https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_watershed.html | 保留 |
| Cd2d19b7a30610b1b | forest-method-boundaries | 噪声、平滑尺度、峰值最小间距、高度阈值和 冠层面积过滤都会改变结果。 | unverified | 待核验 | 改写 |
| Cc7bed104dda29128 | forest-method-boundaries | 在冠层 CHM 上，一个局部峰值**不一定**对应一棵真实树木：相邻树冠可能合并， 一个大树冠可能产生多个峰值，被压制的下层不可见，空洞和树冠摆动会产生虚假峰值。 | verified | https://doi.org/10.1109/MGRS.2024.3479871 | 保留 |
| Ca422df73bbc3b264 | forest-method-boundaries | 因此输出必须命名为“可见上层冠层候选”，不得直接当作林木总株数。 | unverified | 待核验 | 改写 |
| Cedd831314405b568 | forest-method-boundaries | CHM 来源、有效研究区、像元大小、高度阈值、平滑尺度、峰值最小间距、候选冠层面积范围， 以及每一项参数的来源。 | unverified | 待核验 | 改写 |
| C154e4bbb7da15d74 | forest-method-boundaries | 没有独立外业样地或人工标注时，只能报告候选数量、有效区上的密度与分布， **不得报告召回率、准确率或总株数误差**。 | unverified | 待核验 | 改写 |
| C7516fc79893e8c1b | geospatial-product-qa | 第一阶段自动检查包括：栅格可读性，以及坐标系、仿射变换、宽高、像元大小、范围、 波段数、波段描述、数据类型、NoData/掩膜、有效像元比例。 | unverified | 待核验 | 改写 |
| Ca91fcee414124010 | geospatial-product-qa | CRS 与像元到世界坐标的仿射变换是**两个独立的定位要素**。 | verified | https://rasterio.readthedocs.io/en/stable/topics/transforms.html | 保留 |
| Cf91214cfd440b15a | geospatial-product-qa | CRS 正确但变换无效， 不表示成果定位正确。 | unverified | 待核验 | 改写 |
| Cc9384a7cfbc309c9 | geospatial-product-qa | NoData 值、alpha 通道、内部掩膜和外部掩膜都可以表示无效区域； | verified | https://rasterio.readthedocs.io/en/latest/topics/masks.html | 保留 |
| C601066398ca37ede | geospatial-product-qa | `nodata=null` 不能证明不存在背景或缺测。 | unverified | 待核验 | 改写 |
| C37ca807fef784456 | geospatial-product-qa | 像元尺寸应由仿射变换和 CRS 单位判读，不能从影像宽高或文件体积猜测。 | unverified | 待核验 | 改写 |
| C58706f8ece49214d | geospatial-product-qa | EPSG 代码应查询其正式定义，不能凭地理位置猜名称； | unverified | 待核验 | 改写 |
| Ca55eafc8dd55c2d0 | geospatial-product-qa | 例如 EPSG:3395 是 WGS 84 / World Mercator，而不是中国大地坐标系。 | verified | https://epsg.io/3395 | 保留 |
| C2b68d47667d44773 | geospatial-product-qa | 统计时必须区分两套掩膜约定：rasterio 的掩膜数组以 `True` 表示无效，而 GDAL 的 有效数据掩膜以非零表示有效。 | verified | https://rasterio.readthedocs.io/en/latest/topics/masks.html | 保留 |
| Cde2425f6af122188 | geospatial-product-qa | 构建 CHM 之前必须确认 DSM 与 DTM 共享坐标系、栅格尺寸、仿射网格和覆盖范围， 或者存在**有记录**的重采样过程，并且共享高程单位与垂直基准。 | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| C5b1f5c675c73f3e5 | geospatial-product-qa | 文件名中出现 DSM/DTM 不构成上述任何一项的证据。 | unverified | 待核验 | 改写 |
| C2dddb26b1ceb7230 | geospatial-product-qa | 自动检查不能替代在原生分辨率下的目视检查：接缝、空洞、重影、冠层拖影、边缘畸变、 曝光台阶和多光谱错配应结合目视检查； | unverified | 待核验 | 改写 |
| C63933fba9490960c | geospatial-product-qa | 其中部分异常也可由自动指标提示，不能称为 “只能目视发现”。 | unverified | 待核验 | 改写 |
| C66fca257c6fcdfb2 | geospatial-product-qa | 绝对几何精度不能由内嵌 GPS 或 CRS 推断。 | unverified | 待核验 | 改写 |
| Cc820fe217d1524d3 | geospatial-product-qa | 如果精度重要，需要分布良好的地面控制点 和独立检查点（OpenDroneMap 指南：平面与垂直均匀覆盖，通常 ≥5 个点，且每个点在 多张影像中可见）。 | unverified | 待核验 | 改写 |
| C4946b114b72b6c95 | hyperspectral-tree-traits | 高光谱提供密集窄波段，可用于研究树种差异和叶片光学性状； | verified | https://doi.org/10.3389/frsen.2023.1136289 | 保留 |
| C818aa3f969dfa54b | hyperspectral-tree-traits | 其优势取决于传感器信噪比、几何/辐射校准和目标的实际可分性。 | unverified | 待核验 | 改写 |
| C7ea917696a9a9738 | hyperspectral-tree-traits | 波段多不等于样本信息充分。 | unverified | 待核验 | 改写 |
| C274e0bdaab1f1891 | hyperspectral-tree-traits | 样本量有限时需预先控制特征选择与模型复杂度，并把特征选择放进训练折内，防止测试集泄漏。 | unverified | 待核验 | 改写 |
| Cf0e32205a5e8d8c0 | hyperspectral-tree-traits | 冠层混合像元、阴影、物候和观测角度会改变光谱； | unverified | 待核验 | 改写 |
| C0f010156d63cb10f | hyperspectral-tree-traits | 跨季节/地点迁移前需要独立测试。 | verified | https://doi.org/10.3389/frsen.2023.1136289 | 保留 |
| C570846dc587667ea | hyperspectral-tree-traits | 光谱推断的生化参数应以外业叶片测量或可信参考验证； | unverified | 待核验 | 改写 |
| Ce80963ea55fdcede | hyperspectral-tree-traits | 仅凭反演输出不能宣称已直接测量。 | unverified | 待核验 | 改写 |
| C81e6797c6bbecd99 | leaf-area-and-gap-fraction | LAI 表示叶面积与地表面积的比值； | verified | https://doi.org/10.1016/j.agrformet.2003.08.001 | 保留 |
| Ce24632daba126969 | leaf-area-and-gap-fraction | 冠盖度表示俯视树冠投影比例； | unverified | 待核验 | 改写 |
| C6168c86dacc8b27d | leaf-area-and-gap-fraction | 孔隙率描述特定视角未被冠层遮挡的比例。 | unverified | 待核验 | 改写 |
| Cc8dabebed46e65da | leaf-area-and-gap-fraction | 三者不是可互换的百分比。 | unverified | 待核验 | 改写 |
| C33a88fb83d23d583 | leaf-area-and-gap-fraction | 从鱼眼照片、冠层仪或光学遥感估计 LAI 时，要说明观测角度、叶片空间聚集、木质组分和阴影假设。 | verified | https://doi.org/10.1016/j.agrformet.2003.08.001 | 保留 |
| Cf07978f5331d6aee | leaf-area-and-gap-fraction | 在稀疏或高度异质的林分中，简单均匀冠层假设可能偏差； | verified | https://doi.org/10.1016/j.agrformet.2003.08.001 | 保留 |
| C5ce82ad994ce13ea | leaf-area-and-gap-fraction | 结果应与独立地面观测比较，并区分有效 LAI 与真实 LAI。 | unverified | 待核验 | 改写 |
| Cd7407767561aa3f0 | leaf-area-and-gap-fraction | 单张 RGB 正射影像上的绿色像元比例不能直接命名为 LAI。 | unverified | 待核验 | 改写 |
| C656d83f146655f4c | lidar-forest-structure | LiDAR 多次回波和三维坐标可以提供比俯视 RGB 更直接的垂直结构信息，但“能穿透树冠”是概率性的：冠层密度、扫描角、航线和点密度决定可见地面及林下程度。 | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| C144eacdb7fd47bad | lidar-forest-structure | 没有足够地面回波时，DTM 仍可能偏差。 | unverified | 待核验 | 改写 |
| Cf58fd692fbe6ceca | lidar-forest-structure | 常见流程是检查坐标/高程基准和条带配准，过滤噪声，分类地面点，插值 DTM，以 DTM 归一化点高，再计算高度分位数、冠层密度、CHM 或单木候选。 | unverified | 待核验 | 改写 |
| C59e6d7e5b0cba821 | lidar-forest-structure | 单木分割可在 CHM 或三维点云上做； | unverified | 待核验 | 改写 |
| Ca792d73f81309f5b | lidar-forest-structure | 两者在重叠冠、下层木和稀疏回波中具有不同遗漏/拆分误差。 | unverified | 待核验 | 改写 |
| Ca1851d2d6f9fcfe1 | lidar-forest-structure | 输出树高、分层比例、候选树木和冠幅时，记录地面分类、归一化、点密度、阈值和边界处理。 | unverified | 待核验 | 改写 |
| C8b7e915dc84ac0ad | lidar-forest-structure | 机载点云若没有直接观测树干胸径，不能把胸径作为直接测量值； | unverified | 待核验 | 改写 |
| C45a788a93d45d373 | lidar-forest-structure | 需外业数据或经验证的模型推断。 | unverified | 待核验 | 改写 |
| Cd1474e2b07be8f95 | model-transfer-and-uncertainty | 同一航次相邻瓦片高度相关，随机切分通常高估跨地点泛化； | verified | https://doi.org/10.1111/ecog.02881 | 保留 |
| Cdc26f571d9dd30a9 | model-transfer-and-uncertainty | 测试划分应匹配实际部署目标（新地块、新季节或新传感器）。 | unverified | 待核验 | 改写 |
| C936b5562e1070474 | model-transfer-and-uncertainty | 在新林型、分辨率、光照或相机上使用模型前，检查输入分布和外部样本表现； | unverified | 待核验 | 改写 |
| C3ffd07abc848bc5c | model-transfer-and-uncertainty | 原论文或训练集准确率不是新地点的准确率。 | unverified | 待核验 | 改写 |
| Cc5833ae6699161b9 | model-transfer-and-uncertainty | 概率分数、像元熵或模型集成差异可作不确定性线索，但未经校准不能直接解释为错误概率。 | unverified | 待核验 | 改写 |
| Cc940c77c50f8bba2 | model-transfer-and-uncertainty | 汇报地图时标出未覆盖、云影/阴影、混合像元和低置信度区域； | unverified | 待核验 | 改写 |
| C4c950d7658c83451 | model-transfer-and-uncertainty | 在独立参考样本上按空间和类别分层计算误差。 | unverified | 待核验 | 改写 |
| C72324ae667d29c07 | multispectral-radiometry | 植被指数能帮助识别植被或描述冠层状态，但指数不是物种、健康或覆盖率的直接真值。 | unverified | 待核验 | 改写 |
| Cfe6ef273fcce3a30 | multispectral-radiometry | 计算前核实波段中心/带宽、顺序、曝光和产品单位； | unverified | 待核验 | 改写 |
| C63f9be950d879054 | multispectral-radiometry | DN、辐亮度、表观反射率和地表反射率不能混称。 | unverified | 待核验 | 改写 |
| Ca80e4e7ad224b91f | multispectral-radiometry | NDVI 要 Red 与 NIR； | unverified | 待核验 | 改写 |
| Cd6988fc3f4506fa4 | multispectral-radiometry | RGB 树冠分割不依赖 NDVI。 | unverified | 待核验 | 改写 |
| C352e3110868ee699 | multispectral-radiometry | 参考板和下行光照传感器提供辐射校正依据； | verified | https://doi.org/10.1002/ppj2.70005 | 保留 |
| Cfaa9de3effcc3bee | multispectral-radiometry | 云影、变光照、太阳/观测角、自动曝光及拼接可能让同一地物在影像中呈现不同数值。 | verified | https://doi.org/10.1002/ppj2.70005 | 保留 |
| C2a1c53efbceba31f | multispectral-radiometry | 单次参考板采样未必能消除航程中的光照变化。 | verified | https://doi.org/10.1002/ppj2.70005 | 保留 |
| Cec17fa4d696c2829 | multispectral-radiometry | 跨日期/相机比较前，检查同波段定义、校准流程、阴影处理和稳定地物的一致性。 | unverified | 待核验 | 改写 |
| C604c947dab18afad | multispectral-radiometry | 指数阈值须在当前目标与背景上验证； | unverified | 待核验 | 改写 |
| Cd475c5cc31acf28c | multispectral-radiometry | NDVI 高值可能来自草本或灌木，不能独立证明为乔木冠层。 | unverified | 待核验 | 改写 |
| Caec92a40c680e894 | multispectral-radiometry | 指数饱和、土壤背景和阴影可能使生物物理解释失真。 | unverified | 待核验 | 改写 |
| C7371de0437cf3339 | multitemporal-forest-change | 两个日期的像元差值既包含真实地表变化，也包含配准误差、拍摄视角、阴影、季节/物候、光照和处理流程差异。 | unverified | 待核验 | 改写 |
| Cfd0c6cf4b18c7844 | multitemporal-forest-change | 先把研究区、分辨率、栅格网格、有效掩膜和类别定义统一，利用稳定地物检查几何与辐射一致性，再比较林冠掩膜、对象或结构指标。 | unverified | 待核验 | 改写 |
| C2672380f7c06d281 | multitemporal-forest-change | 先分别评估每期分类/分割误差； | unverified | 待核验 | 改写 |
| C15c7ac9390fae4b9 | multitemporal-forest-change | 变化区往往集中在边界和阴影，应抽样复核“减少”“增加”和“不变”三类。 | unverified | 待核验 | 改写 |
| C4322ebdf9b4d1618 | multitemporal-forest-change | 对树冠消失可称“疑似林冠损失”，不可仅凭两期影像判定是砍伐、火灾、病害或季节性落叶。 | unverified | 待核验 | 改写 |
| Ce1af094e46589167 | multitemporal-forest-change | 变化面积以两期共同有效且可比较的研究区为分母，并报告缺测区。 | unverified | 待核验 | 改写 |
| Cd7d169705df17427 | ndvi-and-vegetation-indices | 本指南只约束 NDVI/植被指数计算。 | unverified | 待核验 | 改写 |
| Cfc1c8c2b540cd9ac | ndvi-and-vegetation-indices | RGB 影像缺少 NIR 时不能计算 NDVI， 但仍可用颜色、纹理和标注进行可见树冠分割及覆盖率估计； | unverified | 待核验 | 改写 |
| C5b114ccc1cd03316 | ndvi-and-vegetation-indices | 见 `rgb-canopy-cover`。 | unverified | 待核验 | 改写 |
| C4d599629b8b7eb08 | ndvi-and-vegetation-indices | NDVI 使用 `(NIR - Red) / (NIR + Red)`。 | verified | https://www.usgs.gov/landsat-missions/landsat-normalized-difference-vegetation-index | 保留 |
| C34e2c23327f8f0dd | ndvi-and-vegetation-indices | 红光与近红外波段必须由波段描述、XMP/EXIF 或维护者材料确定，不能按波段序号猜测。 | unverified | 待核验 | 改写 |
| C59b2e5f457b85f29 | ndvi-and-vegetation-indices | 波段顺序存疑时先检查波段描述再计算。 | unverified | 待核验 | 改写 |
| C8b21cac85897e9ed | ndvi-and-vegetation-indices | 若产品记录了比例因子和偏移量，应按产品定义还原各波段量值后再解释指数。 | unverified | 待核验 | 改写 |
| C7d0aae6ab3109080 | ndvi-and-vegetation-indices | 两波段具有相同的纯乘法尺度时，该尺度会在 NDVI 比值中抵消； | verified | https://www.usgs.gov/landsat-missions/landsat-normalized-difference-vegetation-index | 保留 |
| C85f4ee128509b566 | ndvi-and-vegetation-indices | 非零偏移量、 不同尺度、非反射率输入或有符号/无效值仍可能改变结果。 | unverified | 待核验 | 改写 |
| C5fd1357b66e875de | ndvi-and-vegetation-indices | 不能仅凭整数存储 断言 NDVI 会超出 [-1, 1]。 | verified | https://www.usgs.gov/landsat-missions/landsat-normalized-difference-vegetation-index | 保留 |
| C38ae27a323930ee4 | ndvi-and-vegetation-indices | 换算参数应来自产品元数据或可核查说明。 | unverified | 待核验 | 改写 |
| Cc627f76e15b6eb43 | ndvi-and-vegetation-indices | 分母为零的像元必须判为无效，而不是产生无穷大或 NaN 后被静默改写。 | unverified | 待核验 | 改写 |
| Ca0362c8883f24427 | ndvi-and-vegetation-indices | 输出栅格必须继承输入的坐标系、仿射网格与掩膜：NoData 区域在输出中仍然是 NoData。 | unverified | 待核验 | 改写 |
| C5a9f3001e73bb8eb | ndvi-and-vegetation-indices | 统计口径要明确：统计量只在有效像元上计算，并同时报告有效像元数与缺测比例， 否则同一份数据可以得出互相矛盾的“平均值”。 | unverified | 待核验 | 改写 |
| C716863d7c7470b5d | ndvi-and-vegetation-indices | 交付报告中的统计量必须与交付栅格一致，并且可以从栅格重新计算出来。 | unverified | 待核验 | 改写 |
| C7f3033bf5af1b714 | ndvi-and-vegetation-indices | 只报告一个数字而不说明它的分母、掩膜约定和波段来源，不构成可核对的结论。 | unverified | 待核验 | 改写 |
| C750478a3952a27e6 | photogrammetry-in-forest | 重叠影像通过 SfM 求取相机姿态，密集匹配恢复可见表面； | unverified | 待核验 | 改写 |
| C5fc9934654b58eb7 | photogrammetry-in-forest | 正射图是几何校正后的影像，DSM 表示可见表面高程，DTM 表示地面高程，CHM 是匹配基准下的相对冠层高度。 | unverified | 待核验 | 改写 |
| C72e8f64330d1c234 | photogrammetry-in-forest | 正射图本身不含可靠的三维高度。 | unverified | 待核验 | 改写 |
| C8892896951b685b1 | photogrammetry-in-forest | 林冠纹理重复、风致枝叶运动、阴影、曝光差异和航线重叠不足会削弱匹配； | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| Caa4217d85c85db6a | photogrammetry-in-forest | 密林下的地面不可见时，影像点云很难单独恢复真实 DTM。 | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| Cbc2bd577e4e5729c | photogrammetry-in-forest | 可使用独立可信地形模型，但要核查获取时点、网格、水平/垂直基准和误差。 | unverified | 待核验 | 改写 |
| C5c53d11d47652052 | photogrammetry-in-forest | 光学重建主要描述可见冠层表面，不自动给出林下结构。 | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| Cc7293983da324d2b | photogrammetry-in-forest | 检查成果时分开评估：影像连接与覆盖、控制点和独立检查点残差、正射接缝/重影、点云空洞、DTM 地面点来源、DSM/DTM 对齐及 CHM 负值。 | unverified | 待核验 | 改写 |
| C2006f5ada91bbdd6 | photogrammetry-in-forest | 只有元数据完整不能证明定位或高度精度。 | unverified | 待核验 | 改写 |
| C40f9e1e8d8990d94 | prosail-applicability | PROSPECT 描述叶片光学性质，SAIL 描述冠层方向反射； | verified | https://doi.org/10.1016/j.rse.2008.01.026 | 保留 |
| C5360923a67a56e14 | prosail-applicability | 耦合后的 PROSAIL 可正演叶片生化与冠层结构参数对光谱的影响。 | unverified | 待核验 | 改写 |
| C5dcdbfa918bd0bee | prosail-applicability | 反演依赖波段响应、太阳/观测几何、背景、参数范围和先验； | unverified | 待核验 | 改写 |
| C72cfeaaabe346523 | prosail-applicability | 多个参数组合可能产生相近光谱，低维宽波段数据尤其不能保证唯一解。 | verified | https://doi.org/10.1016/j.rse.2008.01.026 | 保留 |
| C3a2b31369f4d35ed | prosail-applicability | 正演、LUT 生成和反演是不同任务。 | unverified | 待核验 | 改写 |
| C50b7c58a0b8a9b01 | prosail-applicability | LUT 中的参数取值范围是建模假设，不能把反演值直接称为外业实测。 | unverified | 待核验 | 改写 |
| C48e37591bf55200e | prosail-applicability | 异质林冠、阴影、木质组分和复杂背景可能偏离模型假设； | unverified | 待核验 | 改写 |
| Cb190f22f51bb5742 | prosail-applicability | 应做敏感性分析、留出独立验证数据并报告可辨识性。 | unverified | 待核验 | 改写 |
| C654fdef66d567e68 | raster-alignment-and-zonal-statistics | 两个栅格参与同一计算前，必须确认：坐标系相同、仿射网格相同（原点与像元大小）、 尺寸相同。 | unverified | 待核验 | 改写 |
| C098b841b1642af4a | raster-alignment-and-zonal-statistics | 任一不同都需要一次**有记录**的重采样。 | unverified | 待核验 | 改写 |
| Cc37b5f452067baf1 | raster-alignment-and-zonal-statistics | 重采样方式须与变量含义和目标分辨率匹配：连续量可选双线性、三次或面积平均等； | verified | https://gdal.org/en/stable/programs/gdalwarp.html | 保留 |
| Ce8b0341a2d650a75 | raster-alignment-and-zonal-statistics | 类别量通常选最近邻或众数，不能用双线性插出虚假的类别编码。 | verified | https://gdal.org/en/stable/programs/gdalwarp.html | 保留 |
| Cda25e491333c0c3a | raster-alignment-and-zonal-statistics | 重采样会改变连续量， 因此要记录方法，不能把插值值当作原始观测。 | unverified | 待核验 | 改写 |
| Ce6041684c2421a4b | raster-alignment-and-zonal-statistics | 地理坐标系（EPSG:4326 一类）的像元大小单位是度，不是米。 | unverified | 待核验 | 改写 |
| Cd7b16890fc15655a | raster-alignment-and-zonal-statistics | 没有执行有依据的投影换算时， 不得把像元大小标成米，也不得直接用度数计算面积。 | unverified | 待核验 | 改写 |
| Ca806b38208327817 | raster-alignment-and-zonal-statistics | NoData 值、alpha 通道、内部掩膜与外部掩膜都可以表示无效区域。 | verified | https://rasterio.readthedocs.io/en/latest/topics/masks.html | 保留 |
| Cfa07b61a652579d4 | raster-alignment-and-zonal-statistics | 转换或重采样后要 重新确认掩膜语义，避免把 NoData 当作 0 参与统计。 | unverified | 待核验 | 改写 |
| C07ef293f90373314 | raster-alignment-and-zonal-statistics | 注意两套约定：rasterio 掩膜数组以 `True` 表示无效，GDAL 有效数据掩膜以非零表示有效。 | verified | https://rasterio.readthedocs.io/en/latest/topics/masks.html | 保留 |
| Cd6ceb1962c7a54e1 | raster-alignment-and-zonal-statistics | 统计只使用落在分区内的有效像元； | unverified | 待核验 | 改写 |
| C0f54cf14b29c755c | raster-alignment-and-zonal-statistics | 一个分区内有效像元过少时，该分区的统计量不构成结论，应单独标记，而不是与其它分区混算； | unverified | 待核验 | 改写 |
| C97092b8d14f511d8 | raster-alignment-and-zonal-statistics | 面积由像元大小与投影单位推导，并在报告中写明单位； | unverified | 待核验 | 改写 |
| C03e7d959cc7abe2a | raster-alignment-and-zonal-statistics | 边界像元是否计入（全包含、中心点包含）会改变结果，必须选定并记录一种口径。 | unverified | 待核验 | 改写 |
| C2adcf1aa7145552c | raster-alignment-and-zonal-statistics | 分区统计的输出应能追溯到输入的栅格版本、掩膜定义和分区定义。 | unverified | 待核验 | 改写 |
| C87c9f64ed755ccc7 | recomputation-and-input-lineage | 只要参与计算的输入发生了变化，旧结果就不再对应当前输入，必须重新执行。 | unverified | 待核验 | 改写 |
| Cfa919090145e6a05 | recomputation-and-input-lineage | 输入变化以**内容身份**判断，而不是文件名或修改时间：同名文件内容不同仍是变化， 不同名文件内容相同仍可复用。 | unverified | 待核验 | 改写 |
| C1dd096aab4fe72f5 | recomputation-and-input-lineage | 记录每个输入的内容身份（大小与哈希）； | unverified | 待核验 | 改写 |
| C1c4bbd0a18276bc1 | recomputation-and-input-lineage | 比较当前输入与产出该结果时的输入身份； | unverified | 待核验 | 改写 |
| C44c661e001a930ce | recomputation-and-input-lineage | 身份不同就必须重算，并在新结论中引用新的输入身份。 | unverified | 待核验 | 改写 |
| Cd9513c7f2760d800 | recomputation-and-input-lineage | 代码没变就认为结果没变。 | unverified | 待核验 | 改写 |
| C6e3bdb8869d77ad3 | recomputation-and-input-lineage | 输入变了、代码不变时，结果必须是新的。 | unverified | 待核验 | 改写 |
| C216b39d394f17d8c | recomputation-and-input-lineage | 结果文件存在就当成已完成的证据。 | unverified | 待核验 | 改写 |
| C76cca3e81176c34c | recomputation-and-input-lineage | 文件存在只说明曾经写过一次。 | unverified | 待核验 | 改写 |
| C5580b77889380d3b | recomputation-and-input-lineage | 重算后仍然引用旧输入的名称或旧参数，使新结论无法对应新输入。 | unverified | 待核验 | 改写 |
| Cab02925dadac03e7 | recomputation-and-input-lineage | 用修改时间做判断：复制、解压和同步都会改变修改时间而不改变内容。 | unverified | 待核验 | 改写 |
| C21b2bc95c102139b | recomputation-and-input-lineage | 重算后的报告必须说明：上一版结论对应的输入身份、本次输入身份、以及本次执行确实 发生过的证据（新的作业或执行记录）。 | unverified | 待核验 | 改写 |
| C1e0056953a2b254c | recomputation-and-input-lineage | 只声明“已更新”而没有执行证据，不构成重算。 | unverified | 待核验 | 改写 |
| C78e7235f2d03775d | regeneration-and-seedlings | 幼苗是否可见取决于冠幅相对像元大小、背景、阴影和上层遮挡； | verified | https://doi.org/10.3390/rs18173027 | 保留 |
| C9f1ba60e07b8dcdd | regeneration-and-seedlings | 林下幼苗不能由俯视 RGB 的“未检测”推断为不存在。 | unverified | 待核验 | 改写 |
| C297c9ec41b05d7dd | regeneration-and-seedlings | UAV RGB/多光谱可以提供可见幼苗候选、空间分布和局部密度； | verified | https://doi.org/10.3390/rs18173027 | 保留 |
| C683d2b14545c6147 | regeneration-and-seedlings | 需要独立样地估计漏检率与误检率。 | unverified | 待核验 | 改写 |
| Ce9610748f6a1161f | regeneration-and-seedlings | “成活率”要求定义初始个体队列并在后续时相逐株匹配或使用有设计的样地调查； | unverified | 待核验 | 改写 |
| C3fed1471d662b0cb | regeneration-and-seedlings | 两个日期的绿色像元数量之比不是成活率。 | unverified | 待核验 | 改写 |
| C0b5c3a4b85bc1fc5 | regeneration-and-seedlings | 更新评价须说明天然更新与人工造林、目标树种、物候、地块边界和观察日期。 | unverified | 待核验 | 改写 |
| Cc1d4fa9ce656be0b | rgb-canopy-cover | 高分辨率 RGB 正射影像可以提取**影像上可见的树冠投影**并估算其覆盖率； | verified | https://doi.org/10.1016/j.jag.2022.102686 | 保留 |
| C14c21a5631474143 | rgb-canopy-cover | NIR、DSM、DTM 不是这一路线的必要输入。 | verified | https://doi.org/10.1016/j.jag.2022.102686 | 保留 |
| Cb3d897d48805d82f | rgb-canopy-cover | 它们能帮助区分植被或提供高度约束，但缺失不能作为拒绝 RGB 树冠任务的理由。 | unverified | 待核验 | 改写 |
| Cfafa09ed246b9975 | rgb-canopy-cover | 需要把“树冠”与“所有绿色植被”分开：草地、灌木、农作物、阴影和绿色人工物可造成混淆。 | unverified | 待核验 | 改写 |
| C7614781ae8f3bbc7 | rgb-canopy-cover | 若无地面或高度证据，结果应称为“RGB 可见树冠候选掩膜及覆盖率估计”。 | unverified | 待核验 | 改写 |
| C2a99519f9f3f71fe | rgb-canopy-cover | 先读取波段、分辨率、CRS、仿射变换、有效掩膜并查看有代表性的原分辨率局部图； | unverified | 待核验 | 改写 |
| C378bcce71bd4d759 | rgb-canopy-cover | 确定研究区分母，排除图外背景与无效像元。 | unverified | 待核验 | 改写 |
| Cd7ebb89391354599 | rgb-canopy-cover | 无标签时，可从 RGB 色彩空间、可见光植被指数（如 ExG）、纹理、对象大小和形状建立可解释的初步候选； | unverified | 待核验 | 改写 |
| Cdc054bab0306352d | rgb-canopy-cover | 阈值从当前影像样本或直方图确定，不能照搬通用常数。 | unverified | 待核验 | 改写 |
| C4b46059b8112ccc9 | rgb-canopy-cover | 裸地、阴影、草地和灌木需专门检查。 | unverified | 待核验 | 改写 |
| Ceaaa277202bdd894 | rgb-canopy-cover | 有少量人工标注时，优先比较简单阈值/对象规则与监督分类； | unverified | 待核验 | 改写 |
| C595841c89ac1711a | rgb-canopy-cover | 有足量且跨场景的树冠标注时可用语义或实例分割。 | unverified | 待核验 | 改写 |
| C74e6690977d515f2 | rgb-canopy-cover | 语义掩膜回答覆盖面积，实例分割才尝试区分相邻单木。 | verified | https://doi.org/10.1109/MGRS.2024.3479871 | 保留 |
| C06efda15a0ec3f89 | rgb-canopy-cover | 保留原始 RGB、候选掩膜、叠加预览和方法/阈值记录。 | unverified | 待核验 | 改写 |
| C7811b5817a73c4fb | rgb-canopy-cover | 对边界、阴影、地物混淆区抽样复核，必要时修正或标注不确定区。 | unverified | 待核验 | 改写 |
| C3e0d6e1541eeb545 | rgb-canopy-cover | 在面积适用的投影坐标系或等面积坐标系下，以树冠掩膜与研究区交集的面积除以研究区有效面积； | unverified | 待核验 | 改写 |
| C9a9b9cacf6bf95c7 | rgb-canopy-cover | 若为同一规则网格且像元面积一致，可用有效树冠像元数除以研究区有效像元数。 | unverified | 待核验 | 改写 |
| Ca506bbadccda649d | rgb-canopy-cover | 报告分子、分母、掩膜定义、像元面积/坐标系、被排除区域和百分比。 | unverified | 待核验 | 改写 |
| C1d4851a63cadd84e | rgb-canopy-cover | 像元尺寸不能从栅格宽高推断，需读仿射变换； | unverified | 待核验 | 改写 |
| Cdcdad4569bd5cb26 | rgb-canopy-cover | 经纬度坐标下不能把度直接当米计算面积。 | unverified | 待核验 | 改写 |
| C1a1ecdaa5ed1ea0d | rgb-canopy-cover | 没有独立标注时，可以给出**方法条件下的估计值**和预览，但不能声称已验证的准确率，也不能把 RGB 绿色掩膜直接写成林木总株数、叶面积指数或真实三维林冠闭合度。 | unverified | 待核验 | 改写 |
| Cf3338b406ec8ed75 | rgb-canopy-cover | 若掩膜明显包含草地，应报告“植被覆盖候选”并继续改进树冠筛选。 | unverified | 待核验 | 改写 |
| C3973acdb7b031e19 | runtime-environment-and-dependencies | 工作区里没有文件，不构成需要安装依赖的证据。 | unverified | 待核验 | 改写 |
| Caa365c8b8de47b9a | runtime-environment-and-dependencies | 判断一个包是否可用，只能在真实的 运行镜像里导入它。 | unverified | 待核验 | 改写 |
| C059b385a350e498d | runtime-environment-and-dependencies | `environment_check` 会启动一个短时容器，在与真实作业相同的镜像和 `PYTHONPATH` 下导入指定模块，返回是否可导入、版本、所属发行包与来源路径。 | unverified | 待核验 | 改写 |
| C5a37a6101da63a9d | runtime-environment-and-dependencies | 这是环境事实， 不要用“工作区没有 requirements.txt”或“目录为空”来推断。 | unverified | 待核验 | 改写 |
| C7b31aef5f9348555 | runtime-environment-and-dependencies | `dependency_install` 提交一个持久作业。 | unverified | 待核验 | 改写 |
| Ce54d3d4a9e7221f4 | runtime-environment-and-dependencies | 容器到达成功终态（`state=succeeded`、退出码为 0）； | unverified | 待核验 | 改写 |
| Cad35c52e1bbe8f5f | runtime-environment-and-dependencies | 请求的发行包确实出现在依赖目录的已安装清单里； | unverified | 待核验 | 改写 |
| Cdb726be9c3badcf0 | runtime-environment-and-dependencies | 依赖这些包的模块通过导入检查。 | unverified | 待核验 | 改写 |
| C553f12547919bcd1 | runtime-environment-and-dependencies | 安装结果未确认前，不要启动依赖它的代码。 | unverified | 待核验 | 改写 |
| Cc7a95a1598b8c286 | runtime-environment-and-dependencies | 部分安装会让后续每次执行都不可核对。 | unverified | 待核验 | 改写 |
| Cd0329b0f4cb330f3 | runtime-environment-and-dependencies | `blocked_by`：执行请求被拒绝，**没有启动任何东西**。 | unverified | 待核验 | 改写 |
| Cff287e6deb17a91c | runtime-environment-and-dependencies | 按返回的 `suggested_tool` 先解决被阻塞的条件（通常是 `job_wait`、`dependency_install` 或 `environment_check`）。 | unverified | 待核验 | 改写 |
| C832d8de01fe5e68d | runtime-environment-and-dependencies | `resource_busy`：执行槽位被占用，**本次没有提交任何东西**，稍后重试即可。 | unverified | 待核验 | 改写 |
| Ceb893f07c98a8029 | runtime-environment-and-dependencies | 两者都与“提交结果未知”不同：不要把它们当作可能已经运行的作业去核对。 | unverified | 待核验 | 改写 |
| Ce0acd58d46068c2c | runtime-environment-and-dependencies | 默认保留一个执行槽位。 | unverified | 待核验 | 改写 |
| C3a0b18b506870684 | runtime-environment-and-dependencies | 槽位被占用时应等待正在运行的作业结束。 | unverified | 待核验 | 改写 |
| C4ea5c850e6f3604a | runtime-environment-and-dependencies | 不要为了腾出槽位而取消一个正常的安装作业——那会留下写了一半的依赖目录。 | unverified | 待核验 | 改写 |
| Cc2ced3af9f098d48 | runtime-environment-and-dependencies | 取消只应来自用户的明确要求，或者作业自身已经失败或超时。 | unverified | 待核验 | 改写 |
| C0786803db7dc8b27 | sensor-method-selection | 先确定目标量，再选输入与方法； | unverified | 待核验 | 改写 |
| C853d4059c1e3848d | sensor-method-selection | 传感器名称本身不保证结果质量。 | unverified | 待核验 | 改写 |
| Cb3d3b2ee4e63df38 | sensor-method-selection | \| 输入 \| 可优先尝试 \| 主要边界 \| | unverified | 待核验 | 改写 |
| C00175d025c8bf0cf | sensor-method-selection | \| RGB 正射 \| 可见树冠/林隙、颜色纹理、对象分割、可见症状 \| 无 NIR 指数；草灌与树冠可能混淆；单期 RGB 难以证明病因 \| | verified | https://doi.org/10.3390/rs12061046 | 保留 |
| C150bb706032d8249 | sensor-method-selection | \| 多光谱/高光谱 \| 反射率、物种/生理差异、植被指数 \| 要核实波段响应、辐射校准、阴影和时相 \| | verified | https://doi.org/10.3390/rs12061046 | 保留 |
| C42e98f5d075e1c42 | sensor-method-selection | \| 热红外 \| 冠层温度与相对热异常 \| 受天气、时刻、发射率、混合像元影响，不直接等于水分胁迫或病害 \| | verified | https://doi.org/10.3390/rs12061046 | 保留 |
| C29d36dab3f64a7b0 | sensor-method-selection | \| 影像摄影测量 \| 表层点云/DSM、正射、可见上层结构 \| 稠密冠层下地面和下层难恢复；高度依赖可信 DTM \| | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| Ced2d97c5454b1171 | sensor-method-selection | \| LiDAR \| 三维点云、冠层高度与垂直结构、一定条件下的地面回波 \| 点密度/扫描几何/遮挡影响结果；仍需分类、配准和验证 \| | verified | https://doi.org/10.1029/2021JG006586 | 保留 |
| Cebdc8f86c2b7e576 | sensor-method-selection | 同一目标可有多条路线。 | unverified | 待核验 | 改写 |
| C485f4136e9750776 | sensor-method-selection | 例如树冠覆盖可由 RGB 分割、多光谱分类或高度约束分割得到； | unverified | 待核验 | 改写 |
| Cb2040160a7b53345 | sensor-method-selection | 只有树高/三维结构问题才必须有相应高程信息。 | unverified | 待核验 | 改写 |
| C6184c5679b567cf4 | sensor-method-selection | 缺某传感器时先评估现有输入能否得到**定义更窄但有用的结果**，并说明输出所代表的对象。 | unverified | 待核验 | 改写 |
| C9d375a7f7c1ed5c2 | supervised-classification-and-validation | 特征矩阵的每一行必须能追溯到一个样本单位（像元、地块或样地），标签必须来自 独立来源，而不是由特征本身推导。 | unverified | 待核验 | 改写 |
| C171afa7268d56d53 | supervised-classification-and-validation | 类别标签要检查类别分布与稀有类； | unverified | 待核验 | 改写 |
| C30195828e603be59 | supervised-classification-and-validation | 回归标签要 检查量纲与异常值，不能把它们静默裁剪。 | unverified | 待核验 | 改写 |
| Ca708bf555711d281 | supervised-classification-and-validation | 标签缺失的样本要显式剔除并记录剔除数量，不能用 0 或众数填充后当作真实标签。 | unverified | 待核验 | 改写 |
| C592f29236f906c9f | supervised-classification-and-validation | 按像元随机划分会在空间自相关数据上产生乐观偏差：相邻像元的特征几乎相同， 训练集与测试集互相“看见”对方。 | verified | https://doi.org/10.1111/ecog.02881 | 保留 |
| C9ba2552a65b16f60 | supervised-classification-and-validation | 因此必须按地块或空间分组划分，并保证同一分组 不跨越训练集与测试集。 | unverified | 待核验 | 改写 |
| C571d59f3c5c7bd1e | supervised-classification-and-validation | 划分必须在任何依赖全局统计的步骤（标准化、缺失值填充、特征选择、超参数搜索） **之前**完成； | verified | https://scikit-learn.org/stable/common_pitfalls.html | 保留 |
| C6704662196340ed4 | supervised-classification-and-validation | 在完整数据上拟合这些步骤再切分，会把测试集信息带入训练。 | unverified | 待核验 | 改写 |
| C1a383724a5727a3a | supervised-classification-and-validation | 指标必须在**独立测试集**上由评估代码自行计算，不接受模型口述的数字， 也不接受在训练集上重算得到的数字。 | unverified | 待核验 | 改写 |
| C34a8448099012047 | supervised-classification-and-validation | 分类：多数类基线； | unverified | 待核验 | 改写 |
| C8ed9c67b5b49a0d4 | supervised-classification-and-validation | 回归：训练集均值基线。 | unverified | 待核验 | 改写 |
| C96ee5b2f6ec8b29c | supervised-classification-and-validation | 只有明显优于基线时，模型才提供了信息。 | unverified | 待核验 | 改写 |
| C1e0ed6d34a3f5601 | supervised-classification-and-validation | 同时报告训练集与测试集指标， 两者差距过大提示过拟合或泄漏。 | verified | https://scikit-learn.org/stable/common_pitfalls.html | 保留 |
| C419e40a8c3980b1a | supervised-classification-and-validation | 分类报告至少包含每类精确率/召回率/F1 与混淆矩阵，而不是只有一个总体准确率； | unverified | 待核验 | 改写 |
| C37e86716e5d4d500 | supervised-classification-and-validation | 类别不平衡时，总体准确率会被多数类主导。 | unverified | 待核验 | 改写 |
| C69503590799d6f99 | supervised-classification-and-validation | 固定并记录随机种子、特征列表、划分方式、模型超参数与库版本。 | unverified | 待核验 | 改写 |
| C59eb0c69d8318158 | supervised-classification-and-validation | 没有这些记录，同一份代码无法复现同一组指标。 | unverified | 待核验 | 改写 |
| C1269ad6b8ce7ebe1 | thermal-forest-observation | 热红外记录辐射温度相关信号，解释为冠层温度时需考虑发射率、反射辐射、距离和相机校准； | unverified | 待核验 | 改写 |
| C5243e5b03e499669 | thermal-forest-observation | 不同飞行时刻的温度不能无条件比较。 | unverified | 待核验 | 改写 |
| Cbe80b1fe1f6668f4 | thermal-forest-observation | 热异常可作为水分胁迫、病害或火情的筛查线索，但受太阳角、风、空气湿度、土壤/天空混合像元和树冠遮挡影响。 | unverified | 待核验 | 改写 |
| C65571bdecae01d8d | thermal-forest-observation | 优先在相近天气与时间窗口采集，并使用非异常对照区或地面测温核查； | unverified | 待核验 | 改写 |
| C7fe8b39bb4689022 | thermal-forest-observation | 在复杂林冠中报告热异常候选而非病因确诊。 | unverified | 待核验 | 改写 |
| C2b2027431a56a240 | thermal-forest-observation | 若目标是林火监测，还需区分活动火点、余热、暖地表与传感器饱和，不能把单个高温像元直接认定为火灾。 | verified | https://doi.org/10.3390/s16081310 | 保留 |
| C02503655bc3ee865 | tool-result-reference-semantics | `relative_path`：`source_id` 授权目录内的源文件路径。 | unverified | 待核验 | 改写 |
| C9f49dd96e2d95dd3 | tool-result-reference-semantics | 它**只**对该授权有意义， 不是 `asset_id`。 | unverified | 待核验 | 改写 |
| C941320db8a55ef1a | tool-result-reference-semantics | `asset_id`：Runtime 管理的附件或产出资产。 | unverified | 待核验 | 改写 |
| C6fa65f214d93d110 | tool-result-reference-semantics | 只有它才能传给 `inspect_file`、 `inspect_raster`、`preview_image`、`artifacts_inspect` 的 `scope="asset"`。 | unverified | 待核验 | 改写 |
| C9d6619b4ee1012da | tool-result-reference-semantics | `source_id`：一次只读目录授权的标识，配合 `folder_path` 使用。 | unverified | 待核验 | 改写 |
| Ce4161c5f66e44a39 | tool-result-reference-semantics | `job_id`：一个持久后台作业。 | unverified | 待核验 | 改写 |
| Cdb2e1b44e868f844 | tool-result-reference-semantics | 它指代“已经开始的一次执行”，不代表结果。 | unverified | 待核验 | 改写 |
| Cfea057ac0c84ffc1 | tool-result-reference-semantics | `result_id`：一个被截断的工具结果的完整副本，用 `tool_result_read` 读取。 | unverified | 待核验 | 改写 |
| C911870d76a35680f | tool-result-reference-semantics | 把 `relative_path` 当作 `asset_id` 传给检查工具，会得到“资产不存在”，并浪费一次调用。 | unverified | 待核验 | 改写 |
| Cf0e182fb9b793488 | tool-result-reference-semantics | 正确做法是：需要源文件内容时用 `source_id` + `folder_path` 再检查一次， 或者只使用盘点结果中已经给出的元数据。 | unverified | 待核验 | 改写 |
| C474e133dd55cb54e | tool-result-reference-semantics | 已经完成盘点的目录，其结果通常就是该目录的完整有界观察。 | unverified | 待核验 | 改写 |
| Cca4eb39b643a121e | tool-result-reference-semantics | 用户只要求盘点或质量报告时， 应直接基于该结果作答，而不是对每个文件再逐个调用检查工具。 | unverified | 待核验 | 改写 |
| C57468516e152289a | tool-result-reference-semantics | `job_id` 出现在结果里只表示提交成功。 | unverified | 待核验 | 改写 |
| Cba99394e45d2a083 | tool-result-reference-semantics | 要取得输出必须等待终态。 | unverified | 待核验 | 改写 |
| C2842abb91564fea9 | tool-result-reference-semantics | `result_id` 出现在结果里只表示原始结果已被保存，不代表内容被读过。 | unverified | 待核验 | 改写 |
| C182169861b692e2d | tree-species-mapping | 树种识别依赖可区分的冠层颜色、纹理、物候、光谱或结构，以及可信树种标签。 | unverified | 待核验 | 改写 |
| C3955bde4e92846bf | tree-species-mapping | RGB 可在合适物候和高空间分辨率下作为输入； | unverified | 待核验 | 改写 |
| C71c0ea307fe5e76a | tree-species-mapping | 多光谱/高光谱可增加光谱信息，LiDAR 可增加结构信息，但更多波段不自动保证更高准确率。 | unverified | 待核验 | 改写 |
| Cf7d5ff6afb07f1d5 | tree-species-mapping | 先确定分类单位是像元、树冠对象还是样地。 | unverified | 待核验 | 改写 |
| Cb2df0fcafe4f4156 | tree-species-mapping | 训练标签应来自独立外业或可核查的专家标注； | unverified | 待核验 | 改写 |
| C028e5b8c1ba5f187 | tree-species-mapping | 相邻像元/同一树冠不可跨训练和测试集。 | verified | https://doi.org/10.1111/ecog.02881 | 保留 |
| Cf4737b1d40b6e8bd | tree-species-mapping | 跨林型、季节、传感器或地区时要用外部样地重评估，不把原地点精度外推。 | unverified | 待核验 | 改写 |
| C89f54f408ebda654 | tree-species-mapping | 易混树种可合并到可辨类别，但须明确报告分类层级和未知类。 | unverified | 待核验 | 改写 |
| Cc3b6c61c060e9461 | tree-species-mapping | 多季节影像可能利用物候差异改善可分性，前提是配准和辐射/季节一致性可接受。 | verified | https://www.sciencedirect.com/science/article/pii/S1574954122002655 | 保留 |
| C336d6c4518973264 | tree-species-mapping | 无标签时只能做探索性聚类或提出候选，不能宣称确认树种。 | unverified | 待核验 | 改写 |
| Cd65173b8c5a4f8da | uav-dataset-roles | 主航线影像； | unverified | 待核验 | 改写 |
| C658631bcd8d1a3c4 | uav-dataset-roles | 起飞前参考板与起飞后参考板； | unverified | 待核验 | 改写 |
| C239eccd4e15c2239 | uav-dataset-roles | 已派生的 RGB / CIR 成果； | unverified | 待核验 | 改写 |
| Ccd3cf7a9fab61b08 | uav-dataset-roles | 已有的正射、DSM、DTM、CHM； | unverified | 待核验 | 改写 |
| C4c37104ca512925d | uav-dataset-roles | 无法归类的文件。 | unverified | 待核验 | 改写 |
| C93dde17a70711d20 | uav-dataset-roles | 参考板是辐射定标证据，不是林地覆盖。 | unverified | 待核验 | 改写 |
| C12a3c7f8fd5dd8e4 | uav-dataset-roles | 它与主航线影像位于同一父目录，并不使它可以 计入主航线影像数量或摄影测量曝光组。 | unverified | 待核验 | 改写 |
| C771a9dded8aa894e | uav-dataset-roles | 相机不同、日期不同、地理范围不同或波段组合不同，都属于不同的采集批次。 | unverified | 待核验 | 改写 |
| C0e7e08d55e2f6120 | uav-dataset-roles | 多光谱同步曝光应按相机元数据（例如 CaptureUUID）分组； | unverified | 待核验 | 改写 |
| C9f64b65e29ec24cd | uav-dataset-roles | 文件数量相等本身不能证明 波段一一对应。 | unverified | 待核验 | 改写 |
| C7d423789c225284d | uav-dataset-roles | 波段角色必须来自 XMP/EXIF、产品波段描述或维护者材料，**不能由波段数量推断**。 | unverified | 待核验 | 改写 |
| C658348a93c77bbff | uav-dataset-roles | DJI Mavic 3M 的 Green/Red/RedEdge/NIR（约 560±16、650±16、730±16、860±26 nm） 只对 Mavic 3M 成立，不得套用到其他相机（例如 FC6360）。 | verified | https://www.dji.com/support/product/mavic-3-m | 保留 |
| Cd4e02b6d0b530feb | uav-dataset-roles | GPS 字段存在只说明存在一个坐标。 | unverified | 待核验 | 改写 |
| C7365e987b262349a | uav-dataset-roles | 存在 RTK 字段或数值状态码，不等于固定解、 厘米级精度或正确基准。 | unverified | 待核验 | 改写 |
| C5ae18dc2f408fddc | uav-dataset-roles | 要报告定位精度，必须同时具备：厂商状态码定义、固定解状态、 基站或网络校正来源、坐标系，以及独立检查点的残差。 | unverified | 待核验 | 改写 |
| Cf847a9449225021d | uav-dataset-roles | 厂商给出的 RTK 指标是设备在声明条件下的规格，不是任意一张影像的实测误差。 | unverified | 待核验 | 改写 |
| C828dfe10db79a656 | uav-flight-design | 飞行设计从目标产物和最小可辨目标出发，记录计划的地面采样距离、航向/旁向重叠与相机姿态； | unverified | 待核验 | 改写 |
| C9257ecf1fb6c1486 | uav-flight-design | 标称飞行高度本身不等于实际 GSD。 | unverified | 待核验 | 改写 |
| Cfbd501d6f71b0cc5 | uav-flight-design | 复杂植被和起伏地形增加遮挡及匹配难度，应检查地形起伏造成的实际离地高度变化、影像覆盖缺口和航线间连接，而非把某个重叠百分比当作通用保证。 | verified | https://docs.opendronemap.org/flying/ | 保留 |
| Ce3e0c3198f3a2c96 | uav-flight-design | 风致树冠运动、运动模糊、快速变化的云影会影响匹配与光谱一致性； | unverified | 待核验 | 改写 |
| C4841e52b16ba78b8 | uav-flight-design | 检查原片清晰度和不同时段光照。 | unverified | 待核验 | 改写 |
| Ca399b2c75867fda8 | uav-flight-design | OpenDroneMap 的重叠建议是规划参考，不是精度证明； | verified | https://docs.opendronemap.org/flying/ | 保留 |
| Caabe6cb892b9151c | uav-flight-design | 成果质量仍需检查控制点/检查点、匹配和成图结果。 | unverified | 待核验 | 改写 |
| Cdc880a443f52726d | ndvi-and-vegetation-indices | 若数据以整数存储，必须先按记录的比例因子与偏移量转换为反射率；未做换算会让 NDVI 落在不可能的范围之外。 | wrong | https://www.usgs.gov/landsat-missions/landsat-normalized-difference-vegetation-index （公式可代数验证共同乘法尺度抵消） | 改写 |
| Ccb11a26f2ac85cef | raster-alignment-and-zonal-statistics | 连续量只能用双线性或三次重采样，类别量只能用最近邻。 | wrong | https://gdal.org/en/stable/programs/gdalwarp.html | 改写 |
| Cbca9c017604533e2 | geospatial-product-qa | 接缝、空洞、重影等异常都只能在目视检查中发现。 | wrong | https://doi.org/10.3390/rs12223831 | 改写 |
