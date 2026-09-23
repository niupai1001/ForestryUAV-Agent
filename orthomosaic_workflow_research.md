# 无人机影像到正射影像：本机固定工作流

更新时间：2026-09-03

## 1. 数据审计结论

输入根目录：`E:\Download\0无人机数据分享`

数据中存在两个独立航次，不能放进同一个摄影测量工程：

| 工程 | RGB 航次 | 多光谱航次 |
|---|---:|---:|
| 目录 | `RGB影像` | `多光谱影像` |
| 相机 | DJI FC300XW | DJI M3M |
| 日期 | 2020-07-31 | 2024-07-18 |
| 影像 | 120 × JPG | 80 × 16-bit TIFF |
| 影像尺寸 | 4000 × 3000 | 2592 × 1944 |
| GPS 完整率 | 120/120 | 80/80 |
| 相对航高（中位数） | 69.9 m | 60.0 m |
| 云台俯仰（中位数） | -90.0° | -89.95° |
| 估算 GSD | 2.99 cm/px | 2.74 cm/px |
| 覆盖范围（近似） | 73.1 × 82.1 m | 87.1 × 40.1 m |
| 航点间距（中位数） | 3.95 m | 10.70 m |
| 光谱情况 | 普通 RGB | Green/Red/RedEdge/NIR，各 20 张 |

多光谱的 20 组曝光均完整；80 张影像均有 Irradiance、镜头渐晕和畸变元数据，并记录了 RTK 标志。RGB 与多光谱航次的时间、相机和地理位置均不同，因此只能分别生成两幅正射成果。完整机器可读审计见 `artifacts/uav_dataset_audit.json`。

## 2. 软件与参数选择

本机安装 OpenDroneMap 3.6.2 到 `E:\AI\ODM`。处理工程位于 `E:\UAV_Projects`，源影像只读，通过 NTFS 硬链接暂存；当硬链接不可用时才复制。

通用参数：

- 正射分辨率 3 cm/px，与两组数据的估算 GSD 相符；
- DSM 分辨率 6 cm/px；
- 生成自动边界、正射裁切线、COG 和金字塔；
- 跳过本任务不需要的纹理三维模型；
- 启用磁盘空间优化，最大并发 4；
- 先处理 RGB，再处理多光谱，避免约 37 GB 剩余空间被两个工程同时占用。

RGB 专用参数：启用 rolling-shutter 校正。FC300XW 的官方规格说明使用电子快门；无人机连续飞行时，卷帘快门会给几何模型带来潜在形变。

M3M 专用参数：四个多光谱波段必须放在同一工程，使用 `radiometric-calibration=camera` 和 `primary-band=Green`。该方案利用影像内的曝光、黑电平、渐晕及辐照度元数据进行相机级辐射归一化；不在缺少可靠太阳传感器工作流验证时擅自使用实验性的 `camera+sun`。

## 3. 精度边界

GeoTIFF 有坐标不等于具备测绘级绝对精度。RGB 航次只有嵌入式 GPS，当前没有地面控制点；其绝对位置不应宣称达到厘米级。M3M 文件含 RTK 相关元数据，但在缺少坐标系、基站/改正源、固定解记录和独立检查点的情况下，也不能只根据标签宣称厘米级成果。

若任务需要可量化的绝对精度，应补充至少 5 个均匀分布的实测 GCP（四角和中心优先），并另设不参与平差的检查点。每个 GCP 应在多张影像上清晰可见。

## 4. 固定 Skill 与 MCP 架构

Skill：`C:\Users\lemon\.codex\skills\uav-orthomosaic`

MCP Server：`C:\Users\lemon\.codex\skills\uav-orthomosaic\scripts\mcp_server.py`

MCP 已注册为 `uav_orthomosaic`，固定调用顺序为：

1. `audit_uav_dataset`：只读审计；
2. 根据相机、日期、位置和波段拆分工程，并说明精度假设；
3. `start_orthomosaic_processing`：启动一个有防重复机制的后台任务；
4. `get_orthomosaic_status`：轮询状态；
5. `get_orthomosaic_log`：只在停滞或失败时读取日志；
6. `validate_orthomosaic_outputs`：用 GDAL 检查 CRS、仿射变换、尺寸、像元、波段和 DSM；
7. `list_orthomosaic_projects`：列出本机项目，防止重复运行。

该 MCP 的输入源目录始终只读；启动工具不提供删除参数，也不会默认覆盖已有成果。首次添加 MCP 后需重启 Codex，使新服务器出现在工具列表中。

## 5. 成果验收

自动验收至少包括：

- `odm_orthophoto/odm_orthophoto.tif` 可被 GDAL 正常读取；
- 有有效宽高、坐标参考和非零地理变换；
- 像元大小与请求的 3 cm/px 相符；
- RGB 或多光谱波段数量符合预期；
- DSM 存在且可读取；
- 汇总处理警告并保存 `qa_report.json`。

人工 GIS 验收还应查看拼接缝、空洞、树冠/建筑扭曲、重影、边缘拉伸、曝光跳变和多光谱波段错位。自动元数据检查不能替代影像目视检查或独立地面检查点。

## 6. 本机实跑结果

两项工程均由 OpenDroneMap 3.6.2 完整运行并通过基础自动验收：

| 指标 | RGB：`rgb_20200731` | 多光谱：`m3m_20240718` |
|---|---:|---:|
| 重建影像/拍摄组 | 120/120 | 20/20（80 张、每组 4 波段） |
| 稀疏点 | 136,812 | 12,267 |
| 稠密点 | 13,841,762 | 1,189,137 |
| 报告平均 GSD | 2.4 cm | 2.9 cm |
| 正射像元 | 约 3 cm | 约 3 cm |
| 正射尺寸 | 6545 × 6083 | 5214 × 3148 |
| 坐标系 | WGS 84 / UTM zone 50N | WGS 84 / UTM zone 50N |
| 波段 | Red/Green/Blue/Alpha | Red/Green/NIR/RedEdge/Alpha |
| DSM | 已生成 | 已生成 |
| 基础 QA | 通过 | 通过 |

RGB 质量报告记录 100% 影像重建、约 0.97 px 平均重投影误差；多光谱报告记录 100% 拍摄组重建、约 0.74 px 平均重投影误差。多光谱 3 条警告均是断点续跑时复用已完成的 OpenSfM 文件，不是成果错误。

人工预览显示两幅正射主体连续、无明显内部空洞。RGB 林冠在外边界存在典型的 2.5D 边缘拉伸，建议用于林分统计前裁去低重叠边缘。多光谱中央覆盖区连续，但航线仅 20 个拍摄位置，边缘有效重叠明显低于中部；定量分析应限定在稳定覆盖区。

成果位置：

- RGB 正射：`E:\UAV_Projects\rgb_20200731\odm_orthophoto\odm_orthophoto.tif`
- RGB DSM：`E:\UAV_Projects\rgb_20200731\odm_dem\dsm.tif`
- RGB 报告：`E:\UAV_Projects\rgb_20200731\odm_report\report.pdf`
- 多光谱正射：`E:\UAV_Projects\m3m_20240718\odm_orthophoto\odm_orthophoto.tif`
- 多光谱 DSM：`E:\UAV_Projects\m3m_20240718\odm_dem\dsm.tif`
- 多光谱报告：`E:\UAV_Projects\m3m_20240718\odm_report\report.pdf`

本次在 Windows 原生版 ODM 中遇到一次日志解码异常：OpenSfM 子进程输出包含非 UTF-8 字节，ODM 的严格 UTF-8 解码触发 `UnicodeDecodeError`。已将 `E:\AI\ODM\opendm\system.py` 的子进程日志包装器设为 `errors="replace"`；它只影响日志容错，不修改摄影测量计算。升级或重装 ODM 后需检查该兼容补丁是否仍在。

## 7. 官方资料

- OpenDroneMap 多光谱指南：https://docs.opendronemap.org/multispectral/
- OpenDroneMap 安装与硬件：https://docs.opendronemap.org/installation/
- OpenDroneMap 输出结构：https://docs.opendronemap.org/outputs/
- OpenDroneMap GCP 指南：https://docs.opendronemap.org/gcp/
- OpenDroneMap 地理定位：https://docs.opendronemap.org/geo/
- 正射分辨率参数：https://docs.opendronemap.org/arguments/orthophoto-resolution/
- 辐射校正参数：https://docs.opendronemap.org/arguments/radiometric-calibration/
- 主波段参数：https://docs.opendronemap.org/arguments/primary-band/
- GPS 精度参数：https://docs.opendronemap.org/arguments/gps-accuracy/
- 磁盘优化参数：https://docs.opendronemap.org/arguments/optimize-disk-space/
- DJI Mavic 3M 规格：https://enterprise.dji.com/mavic-3-m/specs
- DJI Mavic 3M FAQ：https://enterprise.dji.com/mavic-3-m/faq
- DJI Phantom 3 4K / FC300XW 规格：https://www-v1.dji.com/phantom3-4k/spec.html
- MCP Python SDK：https://github.com/modelcontextprotocol/python-sdk
- Codex MCP 配置：https://developers.openai.com/codex/mcp
