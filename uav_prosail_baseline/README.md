# 无人机正射工具库

当前范围只包含：航次读取与检查、正射处理、GeoTIFF 基础检查、预览生成。反射率解释、SRF 和 PROSAIL 反演仍是下一阶段，不与正射模块混合。

## PyCharm 运行

项目解释器使用：

```text
E:\UAV_WORKSPACE\runtime\python\Scripts\python.exe
```

日常只修改 `task_config.py` 中的 `INPUT_DIRECTORY`，然后运行 `run_task.py`。控制台只保留两类信息：

```text
[航次] FC6360 | 77 组 | multispectral | 原始影像目录
[完成] E:\UAV_WORKSPACE\jobs\任务名\result.json
```

完整 ODM 输出写入 `processing.log`，不再持续刷到 PyCharm 控制台。

## 统一工作区

所有原生程序和新任务统一放在纯英文路径：

```text
E:\UAV_WORKSPACE
├─ runtime
│  ├─ python
│  └─ ODM
├─ jobs
│  └─ <job_id>
│     ├─ images
│     ├─ job.json
│     ├─ input_manifest.json
│     ├─ processing.log
│     ├─ odm_orthophoto
│     ├─ odm_dem
│     ├─ odm_report
│     ├─ previews
│     └─ result.json
└─ legacy
```

原始航片可以位于中文目录，因为 Python 能读取；ODM、GDAL、PROJ、OpenCV 及其任务目录固定使用纯英文路径，以规避 Windows 原生库对 Unicode 路径处理不一致的问题。

旧任务已经迁入 `jobs`。旧状态和无法归并的预览保留在 `legacy`，没有删除原始航片或历史产物。

## 工具分层

- `uav_tools.metadata`：读取 EXIF/XMP、枚举影像、按 CaptureUUID 统计航片、判断 RGB 或多光谱。
- `uav_tools.project`：管理统一工作区、暂存影像和 JSON 状态。
- `uav_tools.odm`：唯一允许接触 `run.bat` 的本地 ODM 适配器；负责命令、后台进程、退出码和 GeoTIFF 检查。
- `uav_tools.preview`：按波段名称生成真彩色和假彩色预览。
- `uav_tools.agent_api`：面向 Agent/MCP 的字符串与 JSON 接口。
- `workflow.py`：只排列工具调用，不读取元数据、不拼 ODM 参数、不处理栅格。

面向 Agent/MCP 的稳定接口为：

| 接口 | 单一职责 |
| --- | --- |
| `inspect_flight` | 检查一个航次并返回普通字典 |
| `submit_orthomosaic` | 提交任务并立即返回 job_id |
| `orthomosaic_status` | 查询任务状态与已有产物 |
| `finalize_orthomosaic` | 检查 GeoTIFF、生成预览并写一个 result.json |

这些函数不打印内容，输入输出可以直接序列化为 JSON。迁移 MCP 时只需要注册薄薄的一层工具定义，不需要把 `workflow.py` 搬进 MCP。

## ODM 为什么仍会执行脚本

本地 ODM 的正式入口就是命令行程序。因此当前后端最终会启动 `run.bat`，但批处理路径只存在于 `uav_tools.odm`，用户、流程和未来 Agent 都不直接操作它。

后台工作进程会等待 ODM 真正退出并记录 `return_code`；任务完成不再仅凭“正射文件已经出现”或一个易失效的 PID 推断。

如果改为服务模式，可以使用 NodeODM REST API，并由 PyODM 从 Python 调用。届时替换 ODM 后端即可，上述四个工具接口和预览模块无需改变。

## 预定义参数

框架不再固定分辨率、并发数、主波段、点云质量、特征质量、裁边、COG 等参数。默认只表达当前任务的必要意图：

- 跳过不需要的完整 3D 模型；
- 多光谱航次使用 `radiometric_calibration=camera`，让 ODM 根据影像中的相机校正元数据输出校正值。

其余参数沿用当前 ODM 版本自身的默认值，例如主波段为 `auto`。需要实验对比时，通过 `odm_options` 覆盖，而不是修改工具代码：

```python
run_workflow(
    INPUT_DIRECTORY,
    WORKSPACE_ROOT,
    odm_options={
        "orthophoto_resolution": 4,
        "max_concurrency": 4,
        "primary_band": "Green",
    },
)
```

## 文件输出为什么还保留四个 JSON/日志

- `input_manifest.json`：记录实际参加处理的文件，保证可复现；
- `job.json`：记录任务状态、退出码和实际 ODM 参数，供本地流程或 Agent 查询；
- `processing.log`：ODM 原始日志，仅在失败时查看；
- `result.json`：唯一的最终摘要，包含产物路径、预览路径和基础质量信息。

不再单独生成 dataset summary、QA report 和 task result 三套重复报告。ODM 自己的 `report.pdf` 仍由 ODM 正常生成。
