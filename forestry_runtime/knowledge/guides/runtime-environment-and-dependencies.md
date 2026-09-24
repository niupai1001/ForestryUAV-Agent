---
id: runtime-environment-and-dependencies
title: 运行环境、依赖安装与执行准入
summary: 如何确认一个包真的可用、安装何时才算完成，以及 blocked_by 与 resource_busy 的含义。
tags: [环境, 依赖, 安装, 包, package, dependency, install, import, pip, 槽位, slot, busy, blocked]
applies_to: [environment_check, dependency_install, code_run, job_wait, job_status]
version: 1
---

# 运行环境、依赖安装与执行准入

## 判断一个包是否可用

工作区里没有文件，不构成需要安装依赖的证据。判断一个包是否可用，只能在真实的
运行镜像里导入它。

`environment_check` 会启动一个短时容器，在与真实作业相同的镜像和 `PYTHONPATH`
下导入指定模块，返回是否可导入、版本、所属发行包与来源路径。这是环境事实，
不要用“工作区没有 requirements.txt”或“目录为空”来推断。

## 安装何时才算完成

`dependency_install` 提交一个持久作业。只有同时满足以下条件，安装才算成功：

1. 容器到达成功终态（`state=succeeded`、退出码为 0）；
2. 请求的发行包确实出现在依赖目录的已安装清单里；
3. 依赖这些包的模块通过导入检查。

安装结果未确认前，不要启动依赖它的代码。部分安装会让后续每次执行都不可核对。

## blocked_by 与 resource_busy

- `blocked_by`：执行请求被拒绝，**没有启动任何东西**。按返回的 `suggested_tool`
  先解决被阻塞的条件（通常是 `job_wait`、`dependency_install` 或 `environment_check`）。
- `resource_busy`：执行槽位被占用，**本次没有提交任何东西**，稍后重试即可。

两者都与“提交结果未知”不同：不要把它们当作可能已经运行的作业去核对。

## 槽位

默认保留一个执行槽位。槽位被占用时应等待正在运行的作业结束。
不要为了腾出槽位而取消一个正常的安装作业——那会留下写了一半的依赖目录。
取消只应来自用户的明确要求，或者作业自身已经失败或超时。
