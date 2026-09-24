---
id: tool-result-reference-semantics
title: 工具结果中的引用语义
summary: relative_path、asset_id、source_id、job_id、result_id 各自的含义，以及把它们用错会怎样。
tags: [引用, reference, asset_id, source_id, job_id, result_id, relative_path, 路径, 工具, 结果]
applies_to: [inspect_uav_dataset, inspect_uav_products, inspect_file, inspect_raster, preview_image, artifacts_inspect, job_wait, tool_result_read]
version: 1
---

# 工具结果中的引用语义

## 各标识符的含义

- `relative_path`：`source_id` 授权目录内的源文件路径。它**只**对该授权有意义，
  不是 `asset_id`。
- `asset_id`：Runtime 管理的附件或产出资产。只有它才能传给 `inspect_file`、
  `inspect_raster`、`preview_image`、`artifacts_inspect` 的 `scope="asset"`。
- `source_id`：一次只读目录授权的标识，配合 `folder_path` 使用。
- `job_id`：一个持久后台作业。它指代“已经开始的一次执行”，不代表结果。
- `result_id`：一个被截断的工具结果的完整副本，用 `tool_result_read` 读取。

## 常见错误

把 `relative_path` 当作 `asset_id` 传给检查工具，会得到“资产不存在”，并浪费一次调用。
正确做法是：需要源文件内容时用 `source_id` + `folder_path` 再检查一次，
或者只使用盘点结果中已经给出的元数据。

已经完成盘点的目录，其结果通常就是该目录的完整有界观察。用户只要求盘点或质量报告时，
应直接基于该结果作答，而不是对每个文件再逐个调用检查工具。

`job_id` 出现在结果里只表示提交成功。要取得输出必须等待终态。
`result_id` 出现在结果里只表示原始结果已被保存，不代表内容被读过。
