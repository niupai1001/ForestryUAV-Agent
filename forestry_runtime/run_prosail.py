
"""最小 PROSAIL 反演：正射反射率 -> LUT -> LAI/Cab/RMSE。"""
from pathlib import Path
import os
import sys

CONDA_SHARE = Path(sys.prefix) / "Library" / "share"
if CONDA_SHARE.exists():
    os.environ.setdefault("PROJ_DATA", str(CONDA_SHARE / "proj"))
    os.environ.setdefault("GDAL_DATA", str(CONDA_SHARE / "gdal"))

import numpy as np
from PIL import Image
import prosail
import rasterio
from scipy.spatial import cKDTree


INPUT_TIF = Path(
    r"E:\UAV_WORKSPACE\jobs\uav_20220730160802_fc6360_f143a7cc"
    r"\odm_orthophoto\odm_orthophoto.tif"
)
OUTPUT_DIR = INPUT_TIF.parent.parent / "prosail_simple"

# 实际影像中的波段顺序为 1=Red，2=Green，4=NIR，5=RedEdge，6=Alpha。
# 反演时统一按照 Red、Green、RedEdge、NIR 的顺序组织观测光谱。
BANDS = [1, 2, 5, 4]
ALPHA_BAND = 6

LUT_SIZE = 500
SEED = 20260914
MIN_NDVI = 0.20

# PROSAIL 的观测几何参数。
# SOLAR_ZENITH 对应太阳天顶角 tts，VIEW_ZENITH 对应观测天顶角 tto，
# RELATIVE_AZIMUTH 对应太阳与传感器之间的相对方位角 psi。
SOLAR_ZENITH = 54.02
VIEW_ZENITH = 0.0
RELATIVE_AZIMUTH = 0.0


# PROSAIL 参数的先验范围。
# LAI 和 Cab 是本次反演的目标参数，其余参数并不是待输出结果，
# 但它们同样会改变冠层反射率，因此在构建 LUT 时必须允许它们变化，
# 否则模型会把其他因素造成的光谱变化错误归因到 LAI 或 Cab 上。
RANGES = {
    "n": (1.2, 2.2),          # PROSPECT 叶片结构参数
    "cab": (20.0, 70.0),      # 叶绿素 a+b 含量，μg/cm²
    "car": (4.0, 18.0),       # 类胡萝卜素含量
    "cbrown": (0.0, 0.5),     # 棕色色素含量
    "cw": (0.005, 0.035),     # 叶片等效水厚度
    "cm": (0.004, 0.020),     # 叶片干物质含量
    "lai": (0.3, 7.0),        # 叶面积指数
    "lidfa": (30.0, 70.0),    # 叶倾角分布参数
    "hspot": (0.01, 0.30),    # 冠层热点参数
    "rsoil": (0.5, 1.5),      # 土壤背景亮度系数
    "psoil": (0.0, 1.0),      # 干湿土壤混合参数
}


def build_lut():
    """
    构建 PROSAIL 查找表 LUT。

    反演前先随机生成大量可能的植被状态，然后使用 PROSAIL 正向模型计算
    每一种状态理论上应该产生什么样的冠层反射光谱。这样就建立了：

        PROSAIL 参数 -> 模拟反射率

    的映射。

    后续真正反演影像时再反过来做：

        实际反射率 -> 最相似的 LUT 光谱 -> 对应的 LAI/Cab

    当前 LUT 中同时随机变化叶片生化参数、冠层结构参数和土壤参数。
    最终虽然只反演 LAI 和 Cab，但其他参数的变化能够让 LUT 覆盖更多真实
    植被状态，减少把光谱差异全部错误解释为 LAI/Cab 差异的问题。

    返回
    ----
    reflectance : ndarray, shape (LUT_SIZE, 4)
        每一行是一组 PROSAIL 参数对应的模拟四波段反射率，
        顺序为 Red、Green、RedEdge、NIR。

    lai : ndarray, shape (LUT_SIZE,)
        每条模拟光谱对应的 LAI。

    cab : ndarray, shape (LUT_SIZE,)
        每条模拟光谱对应的 Cab。
    """
    rng = np.random.default_rng(SEED)

    # 在给定先验范围内对所有 PROSAIL 参数进行独立均匀随机采样。
    # 因此第 i 个位置上的所有参数共同组成第 i 个虚拟冠层状态。
    p = {
        name: rng.uniform(low, high, LUT_SIZE)
        for name, (low, high) in RANGES.items()
    }

    # prosail.run_prosail 输出 400~2500 nm、约 1 nm 间隔的连续光谱。
    # 实际无人机只有几个离散多光谱波段，因此需要把连续模拟光谱进一步
    # 转换成与无人机传感器可比较的 Red、Green、RedEdge 和 NIR 波段。
    wavelength = np.arange(400, 2501)

    # 当前没有使用传感器真实光谱响应函数 SRF，而是用矩形窗口近似各波段。
    # 即认为窗口范围内所有波长权重相同，最后直接计算平均反射率。
    #
    # Red:     650 ± 16 nm
    # Green:   560 ± 16 nm
    # RedEdge: 730 ± 16 nm
    # NIR:     860 ± 26 nm
    #
    # 正式实验中更合理的做法是使用具体相机厂家提供的 SRF，
    # 对 PROSAIL 连续光谱进行加权积分，而不是简单求窗口平均值。
    response = [
        (wavelength >= center - half_width) &
        (wavelength <= center + half_width)
        for center, half_width in [
            (650, 16),
            (560, 16),
            (730, 16),
            (860, 26),
        ]
    ]

    reflectance = np.empty((LUT_SIZE, 4), dtype="float32")

    for i in range(LUT_SIZE):
        # PROSAIL = PROSPECT + SAIL：
        #
        # PROSPECT 根据 n、Cab、Car、Cbrown、Cw、Cm 等参数模拟叶片尺度
        # 的光学性质；SAIL 再结合 LAI、叶倾角、热点效应、土壤背景以及
        # 太阳-传感器几何，将叶片尺度信息扩展为冠层尺度反射率。
        #
        # 因此这里得到的 spectrum 并不是单片叶片反射率，
        # 而是指定冠层状态和观测几何下的模拟冠层光谱。
        spectrum = prosail.run_prosail(
            p["n"][i],
            p["cab"][i],
            p["car"][i],
            p["cbrown"][i],
            p["cw"][i],
            p["cm"][i],
            p["lai"][i],
            p["lidfa"][i],
            p["hspot"][i],
            SOLAR_ZENITH,
            VIEW_ZENITH,
            RELATIVE_AZIMUTH,
            ant=0.0,
            prospect_version="D",
            typelidf=2,
            factor="SDR",
            rsoil=p["rsoil"][i],
            psoil=p["psoil"][i],
        )

        # 将 400~2500 nm 的连续模拟光谱转换为与无人机影像一致的四维光谱。
        # LUT 的每一行最终都是：
        #
        # [Red, Green, RedEdge, NIR]
        #
        # 后续真实影像中的每个像元也会组织成完全相同的四维形式，
        # 两者才可以直接计算光谱距离。
        reflectance[i] = [
            spectrum[band].mean()
            for band in response
        ]

    return reflectance, p["lai"], p["cab"]


def read_orthophoto():
    """
    读取实际无人机正射反射率，并建立参与 PROSAIL 反演的植被掩膜。

    PROSAIL 模拟的是物理意义上的反射率，因此这里输入影像原则上也必须是
    已完成辐射定标的地表/冠层反射率，而不是相机原始 DN 值。

    当前通过三层条件限制参与反演的像元：
    1. Alpha 波段有效；
    2. 四个反射率波段均为有限值且位于 0~1；
    3. NDVI >= MIN_NDVI，只对明显具有植被特征的像元进行反演。

    返回的 reflectance 最后一维固定为：
        [Red, Green, RedEdge, NIR]

    这必须和 build_lut() 生成 LUT 的波段顺序完全一致。
    """
    with rasterio.open(INPUT_TIF) as src:
        reflectance = np.moveaxis(
            src.read(BANDS).astype("float32"),
            0,
            -1
        )
        alpha = src.read(ALPHA_BAND)
        profile = src.profile.copy()
        descriptions = tuple(
            src.descriptions[index - 1]
            for index in BANDS
        )

    if descriptions != ("Red", "Green", "RedEdge", "NIR"):
        raise ValueError(f"波段顺序不符合预期：{descriptions}")

    # 首先排除正射影像边缘、NoData、NaN/Inf 以及不符合反射率范围的像元。
    valid = (alpha > 0) & np.all(np.isfinite(reflectance), axis=2)
    valid &= np.all(
        (reflectance >= 0) & (reflectance <= 1),
        axis=2
    )

    # 使用 Red 和 NIR 计算 NDVI，只保留具有一定植被信号的区域。
    # 这里的 NDVI 阈值主要是反演前掩膜，而不是 PROSAIL 模型的一部分。
    red = reflectance[:, :, 0]
    nir = reflectance[:, :, 3]

    ndvi = np.divide(
        nir - red,
        nir + red,
        out=np.zeros_like(red),
        where=(nir + red) != 0
    )

    valid &= ndvi >= MIN_NDVI

    return reflectance, valid, profile


def invert(reflectance, valid, lut_reflectance, lut_lai, lut_cab):
    """
    使用 LUT 最近邻匹配反演 LAI 和 Cab。

    对每个真实植被像元，都有一个四波段观测向量：

        y = [Red, Green, RedEdge, NIR]

    LUT 中每条记录也有一个 PROSAIL 模拟光谱：

        y_i = [Red_i, Green_i, RedEdge_i, NIR_i]

    当前方法在所有 LUT 光谱中寻找与实际像元最接近的那一条：

        i* = argmin distance(y, y_i)

    然后直接把第 i* 条 LUT 对应的 LAI 和 Cab 作为该像元的反演结果。

    这属于最基础的 LUT 单最优解反演。它没有显式求解 PROSAIL 的逆函数，
    而是通过大量正向模拟结果近似逆映射。
    """
    pixels = reflectance[valid]

    # 不同波段在 LUT 中的变化幅度可能差异明显。
    # 如果直接计算原始四波段欧氏距离，变化范围较大的波段可能主导匹配结果。
    #
    # 这里使用每个波段在 LUT 中的标准差作为尺度进行归一化：
    #
    #     x'_b = x_b / std_b
    #
    # 相当于让四个波段按照各自天然变化范围参与距离计算。
    # 注意：这并不代表这种权重一定最优，只是一种简单的标准化策略。
    scale = lut_reflectance.std(axis=0)
    scale[scale == 0] = 1

    # 将所有 LUT 光谱建立为 KDTree。
    # 本质上 KDTree 只是加速最近邻查找，并不会改变 LUT 反演的物理含义。
    # 如果 LUT 扩展到几万甚至更多记录，它比逐像元遍历整个 LUT 高效得多。
    tree = cKDTree(lut_reflectance / scale)

    # 对每一个真实像元寻找标准化光谱空间中的最近邻。
    #
    # match[j] 表示第 j 个实际像元最接近 LUT 中的第几条记录。
    # 当前 query 默认 k=1，因此只选择一个最优 LUT 样本。
    _, match = tree.query(
        pixels / scale,
        workers=-1
    )

    # 根据最近邻索引重新取得对应的 PROSAIL 模拟光谱，
    # 用于评价实际观测和最终选中 LUT 光谱之间的拟合程度。
    modeled = lut_reflectance[match]

    # RMSE 使用原始反射率而不是标准化后的反射率计算：
    #
    # RMSE = sqrt(mean((R_obs - R_sim)^2))
    #
    # 这里每个像元只有四个波段，因此 RMSE 表示这四个波段整体上的
    # 光谱拟合误差。RMSE 越小说明 PROSAIL LUT 中存在与该像元更相似的状态；
    # RMSE 较大则可能意味着 LUT 参数范围不充分、输入反射率异常、
    # 阴影/混合像元影响较强，或 PROSAIL 对该目标的描述能力有限。
    rmse = np.sqrt(
        np.mean(
            (pixels - modeled) ** 2,
            axis=1
        )
    )

    # 将最近邻 LUT 样本对应的参数重新映射回原始影像空间。
    #
    # result[0]：LAI
    # result[1]：Cab
    # result[2]：四波段光谱 RMSE
    #
    # 非植被或其他无效区域保持 NaN。
    result = np.full(
        (3, *valid.shape),
        np.nan,
        dtype="float32"
    )

    result[0][valid] = lut_lai[match]
    result[1][valid] = lut_cab[match]
    result[2][valid] = rmse

    return result


def save_result(result, profile):
    """
    保存 PROSAIL 反演结果。

    输出 GeoTIFF 包含三个波段：
        Band 1：LAI
        Band 2：Cab，单位 μg/cm²
        Band 3：实际光谱与最佳 LUT 光谱之间的 spectral RMSE

    RMSE 建议和 LAI/Cab 一起保留，因为它可以作为最基础的反演质量指标。
    如果某一区域得到异常高或异常低的 LAI，同时 RMSE 也明显偏高，
    则该参数结果本身就不应直接解释为可靠反演值。
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    output_tif = OUTPUT_DIR / "prosail_lai_cab_rmse.tif"

    profile.update(
        count=3,
        dtype="float32",
        nodata=np.nan,
        compress="deflate"
    )
    profile.pop("photometric", None)
    profile.pop("interleave", None)

    with rasterio.open(output_tif, "w", **profile) as dst:
        dst.write(result)
        for index, name in enumerate(
            ("LAI", "Cab_ug_cm2", "spectral_RMSE"),
            1
        ):
            dst.set_band_description(index, name)

    # 预览图只用于快速观察 LAI 空间分布，不参与任何 PROSAIL 计算，
    # 颜色也没有定量意义。真正分析时应使用输出 GeoTIFF 中的 LAI 数值。
    lai = np.nan_to_num(
        np.clip(result[0] / 7.0, 0, 1)
    )

    preview = np.stack(
        (
            255 * lai,
            255 * np.sqrt(lai),
            50 * (1 - lai)
        ),
        axis=2
    )

    preview[~np.isfinite(result[0])] = 0

    preview_path = OUTPUT_DIR / "lai_preview.png"
    Image.fromarray(
        preview.astype("uint8")
    ).save(preview_path)

    return output_tif, preview_path


def main():
    print("1. 生成 PROSAIL LUT")
    lut_reflectance, lut_lai, lut_cab = build_lut()

    print("2. 读取正射反射率")
    reflectance, valid, profile = read_orthophoto()
    print(
        "   影像数组：",
        reflectance.shape,
        "有效像元：",
        int(valid.sum())
    )

    print("3. 匹配 LUT，反演 LAI 和 Cab")
    result = invert(
        reflectance,
        valid,
        lut_reflectance,
        lut_lai,
        lut_cab
    )

    print("4. 保存结果")
    output_tif, preview = save_result(result, profile)

    # 保存 LUT，便于之后检查模拟光谱分布，或者直接重复使用该 LUT，
    # 避免每次反演都重新运行大量 PROSAIL 正向模拟。
    np.savez(
        OUTPUT_DIR / "prosail_lut.npz",
        reflectance=lut_reflectance,
        lai=lut_lai,
        cab=lut_cab
    )

    print("   GeoTIFF：", output_tif)
    print("   预览：", preview)


if __name__ == "__main__":
    main()
