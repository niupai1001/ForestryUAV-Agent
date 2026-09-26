# OAM-TCD 数据获取记录

获取时间：2026-09-26。数据集 `restor/tcd`。

本机 `huggingface.co` 直连不可达，数据集的 tree/parquet **API 一律返回 403**；
只有 `resolve` 端点可用。两个分片都取自自动转换分支
`refs/convert/parquet`，经 `hf-mirror.com` 下载。

## 1. 测试分片（正式题库 12 题的来源）

```
https://hf-mirror.com/datasets/restor/tcd/resolve/refs%2Fconvert%2Fparquet/default/test/0000.parquet
```

| 项 | 值 |
|---|---|
| 落盘路径 | `data/oam_tcd/test-00000-of-00001.parquet` |
| 字节 | 334,303,139 |
| SHA-256 | `5c10ac4cb8afaf18dae5aad5b22cc86bb80977116a8bcd8c16d41ae48b9b13cf` |
| 行数 | 439（55 个 `oam_id`） |
| 许可 | 全部 CC-BY 4.0 |
| CRS | 全部 EPSG:3395 |

该摘要与 `oam.py` 的 `TEST_SHA256`、`tasks.grounded-v1.*.json` 的
`test_shard_sha256` 一致；不符时生成器直接拒绝运行。

## 2. 训练分片（开发题的来源）

```
https://hf-mirror.com/datasets/restor/tcd/resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet
```

| 项 | 值 |
|---|---|
| 落盘路径 | `data/oam_tcd/train-00000-of-00001.parquet` |
| 字节 | 464,097,497 |
| SHA-256 | `ec8fe0c79ed8286264625351c307331646c04717df186a25628fe96815bf2acb` |
| 行数 | 596（291 个 `oam_id`） |
| 许可 | 全部 CC-BY 4.0 |
| CRS | 全部 EPSG:3395 |

**291 个训练源与正式 12 个源完全不重叠**（训练/测试划分本身即按源切分），
开发题因此天然满足「来源不重叠」要求。生成脚本
`evaluation/grounded_v1/prepare_dev.py` 仍会在写出前断言交集为空。

## 3. 两个分片都不可进版本库

`forestry_runtime/.gitignore` 排除了 `data/`，两个分片合计约 760 MB 不入库。
换机后按上表 URL 重新下载并校验 SHA-256 即可；校验不通过就是取错了版本，
不要继续往下跑。
