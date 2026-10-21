# 多光谱正射与PROSAIL操作

在升级后的工作台**新建会话**，发送航片文件夹/明确本地目录，说明“拼接多光谱影像，再做PROSAIL LAI反演”。已有正射或LUT可直接发送/提供路径，Agent应先检查并复用，不重建正确结果。旧会话的科学镜像身份保持冻结，升级不会静默替换其运行环境。

当前迁移入口支持≤200张同一航次顶层航片，不递归混合不同航次；超过范围明确提示，不自动抽样。拼接需要足够重叠；文件分组完整不能保证重建一定成功。

影像文件经只读引用进入计算作业，模型只收到有界文件信息。TIF/TIFF/DNG不会自动作为视觉消息解码；普通PNG/JPEG仍可正常看图。科学反演读取原始GeoTIFF像素；预览只用于观察，≤1024边长，不替代原始数据。

PROSAIL需要有依据的反射率定标、波段映射、参数先验、太阳/观测几何和传感器响应。Agent可以从资料和影像元数据构建声明，缺少依据时应说明缺项，不能把原始DN除以任意常数当作反射率。当前旧方法使用矩形光谱响应近似、种子固定的均匀LUT、按波段标准差归一化单最近邻和可选NDVI掩膜。LAI/Cab结果是这些假设下的模型估计；spectral_RMSE是光谱残差。

输出在会话产物中：正射/DSM及ODM日志、LUT、LAI/Cab/光谱残差GeoTIFF、LAI预览、输入/参数/镜像/质量JSON。完成后回收终态容器和已封存工作副本；保留有用产物与失败记录，删除会话时按已有生命周期规则清理会话所属数据，用户原目录保持。

如ODM在后期失败，Agent应先检查已生成产品，使用同会话`resume_job_id`复用已封存重建/网格检查点。恢复不替换镜像、源版本或方法参数；不重置失败记录、不从头重跑。

开发者入口：

```text
POST /tasks/{task_id}/imagery
forestry-agent imagery --config config/app.local.yaml --task <会话ID> --request request.json
```

拼接请求（文件夹及声明均须已发送至本会话）：

```json
{
  "request_id": "flight-orthomosaic-1",
  "expected_spec_revision": 1,
  "operation": "orthomosaic",
  "input_refs": {"flight": "已发送文件夹的资源ID"},
  "declaration_ref": "ODM声明JSON的资源或本会话产物ID",
  "seconds": 14400
}
```

ODM声明：

```json
{
  "processing_mode": "multispectral",
  "source_note": "本航次元数据与分组检查记录的来源",
  "radiometric_calibration": "camera",
  "dsm": true
}
```

操作角色：`inspect`只需`raster`；`build_lut`输入角色为空、需参数声明；`invert`需`raster`和`lut`以及反射率/波段声明。传入的是文件引用，绝不是Base64、像素数组或全文。资源ID供内部路由与开发者API使用，普通用户不需要手填。

配置示例见[固定镜像配置](../config/app.imagery.example.yaml)。本机复用旧PROSAIL镜像；跨设备要准备该镜像或建立另行核验的环境，不能把示例摘要存在视为另一台设备已安装。

当前交付边界与真实运行证据见[迁移记录](IMAGERY-PROSAIL-MIGRATION-20261021-zh.md)。联网搜索已取消，点云后续开发暂缓，领域峰值资源/吞吐评估取消。
