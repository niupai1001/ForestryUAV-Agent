# GABench 数据获取记录

获取时间：2026-09-26。仓库固定提交：

```
e8c64e883bbe45e2b94515c73941e7a0837320ae   (GeoX-Lab/GABench)
```

## 1. 网络事实与镜像选择

本机 `github.com` 与 `huggingface.co` 直连不可达。实际走通的通道：

| 用途 | 通道 | 结果 |
|---|---|---|
| 仓库克隆 | `https://gh-proxy.com/https://github.com/GeoX-Lab/GABench.git` | 成功 |
| LFS 批量接口 | `https://gh-proxy.com/https://github.com/GeoX-Lab/GABench.git/info/lfs/objects/batch` | 成功 |
| LFS 媒体下载 | batch 返回的签名 `href`，必要时前缀 `gh-proxy.com` | 成功 |

克隆必须带 `GIT_LFS_SKIP_SMUDGE=1`。原因：`.gitattributes` 把
`tif / shp / geojson / csv / png / prj …` 全部纳入 LFS，直接 smudge 会在
下载 30 MB 级载荷时超时并被中断，留下半份工作树。正确做法是**先只取指针，
再逐文件按 oid 校验后落盘**。

取指针后由 `evaluation/grounded_v1/_fetch_lfs.py` 逐文件解析
`oid sha256:` 与 `size`，调 LFS batch API 取签名链接，下载后校验
**SHA-256 与 size 双等**才写入；任一不等即原样保留指针文件并报错退出。

## 2. 已落盘文件（SHA-256 与 LFS oid 逐位一致）

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `dataset/Elevation.tif` | 31,468,285 | `0fdbce8937a025c0df1f6541103028458f51b21cd6bf5755b79199f0264071d4` |
| `dataset/ruggedness.tif` | 31,461,518 | `200bcfb4aeb2a4ef8522a6c4e68424ff4273192e249e61abd73451188bf886f7` |
| `dataset/roads.geojson` | 14,959,869 | `0b29c97f5dc6e08950d7cfd9c36cc614cb3418d1c28454d9956e9ce2e4ae80f6` |
| `dataset/deforestedArea.geojson` | 21,033,068 | `fb69e7ae0cbc23984d742a8782c56bb28e6f9875e46ff6614dcfd477fd478577` |
| `dataset/result/deforestation_rate.csv` | 46 | `0c8743077fbce1f30feed39a7480038c19015ea887e2a11d162ffa443a0fe127` |
| `benchmark/benchmark.csv` | 193,385 | `74cb8d877a6f507899b78eb4a8442a9e54f9bad52ddd93f488cfb4e479770e6c` |

`dataset/ruggedness.tif` 是**已发布的参考产物**（ID 12 的输出栅格），
`dataset/result/deforestation_rate.csv` 是 ID 9 的已发布参考值。二者只用于
独立复算比对，不作为题目输入交给 Agent。

## 3. 两道保留题的题面与参考值

取自 `benchmark/benchmark.csv`（57 题全量清单）。

### ID 12｜Raster Spatial Analysis

- 输入：`dataset/Elevation.tif`（1 个文件）
- 交付：`ruggedness.png`；参考图层 `rugged_Elevation.tif`
- 复算：3×3 邻域极差（`generic_filter(size=(3,3))` 的 `nanmax - nanmin`）
- 判据：网格（宽高/仿射/CRS）与 `Elevation.tif` 一致，逐像元整数相等，容差 0
- **约定：255 不是 nodata。** 该文件未声明 nodata，参考产物把 255 当普通高程
  参与计算；把它当 nodata 会得到不同结果

### ID 9｜Vector Spatial Analysis

- 输入：`dataset/roads.geojson`、`dataset/deforestedArea.geojson`
- 交付：`deforestation_rate.csv` 的 `percentage_deforestation`
- 复算（与 `benchmark.csv` 的工具链一致）：
  EPSG:4326 → 重投影 EPSG:32723 → buffer 5500 m → dissolve → 面积 A_buf →
  以 deforestedArea 裁剪 → 面积 A_def → 比值 A_def / A_buf
- 参考中间量：`A_buf = 179792795540.404 m²`、`A_def = 86176671033.82933 m²`
- 参考值：`0.4793110356552816`

## 4. 不采用的候选

`gabench-42` 已被剔除，理由见 `evaluation/grounded_v1/GABENCH_LEDGER.md` §4.3：
`point3d.prj` 为 Web Mercator、`buildings.prj` 为 British National Grid，两层
CRS 互不相容且流程从不重投影；5172 个顶点中 1237 个是 `-1.797e308` 哨兵值，
落在 2 m 阈值内的顶点数为 0，故 `visible=true` 与真实三维通视无关。
其输入文件**未**接入本题库的数据目录。
