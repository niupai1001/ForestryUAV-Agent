# 点云输入检查

1.48提供离线CLI入口：选择一个LAS/LAZ文件→只读检查头与有界样本→保留有来源的声明→导出独立JSON。当前不启动服务、模型或计算Job，不改变已有Task、授权与源文件。Agent工具/工作台集成及PDAL/lidR科学处理仍在阶段2继续开发。

```powershell
uv run --locked forestry-agent inspect-cloud --path C:/data/flight.las --output workspace/flight-inspection.json
uv run --locked forestry-agent inspect-cloud --path C:/data/flight.las --declared C:/data/declared.json --hash-file --output workspace/flight-inspection-with-source.json
```

`--output`要求新文件；不覆盖历史记录。省略输出参数时打印JSON。`--hash-file`额外流式读取完整源文件，计算SHA256；默认只保留文件大小/状态版本，有界元数据和最多4096个首批点，不能把抽样当作完整空间支持验证。LAZ目前只检查头/元数据，不解压点或验证压缩流。源版本变化、非法头/坐标/边界、超限元数据或不完整LAS点记录会拒绝。

可选声明示例：

```json
{
  "source_note": "具体发布者说明、版本或测量来源；该声明未由检查器独立验证",
  "horizontal_crs": "EPSG:2154",
  "vertical_ref": "EGM96",
  "z_unit": "m",
  "z_semantics": "elevation",
  "classification_semantics": "publisher_annotations"
}
```

文件观察与声明分别在`observed`/`declared`保存：水平CRS由文件VLR/EVLR解析；不能用水平米制推断Z单位或把绝对高程当离地高度。当前垂直基准与Z语义保持未知，另列声明值；水平CRS冲突显示为`crs_conflict`，不自动重投影或覆盖。额外维度明确列出并提示隔离可能的树木/语义真值。分类频数只描述首批点，不能把发布者分类直接作为算法预测或地面支持证明。地面支持和科学运行时继续标记`not_assessed`。

本入口复用成熟[laspy头/CRS解析](https://laspy.readthedocs.io/en/latest/api/laspy.header.html)，项目锁定laspy 2.7.0与pyproj 3.8.0。没有另写LAS解码器或新增容器执行器。

实际新流程已使用[杨树林UAV数据集](https://doi.org/10.57745/WAEYEX)版本2.0、Etalab 2.0许可的flight02原始LAS。开发前按完整LAS成员字节最小选择此架次，属于bootstrap，不作为阶段3留出或精度通过。发布者README声明EPSG:2154、EGM96；原归档不是原始RGB航片夹具，其正射产品不能替代ODM原始重建入口。

- 来源：官方文件749153，ZIP成员`01_UAV_LIDAR/01_LAS/20240124_UAV_flight02_447_120_6_0_triple.las`。
- 已取得整个成员39,311,550字节，1,156,172点，LAS 1.2 / point format 3；水平CRS与米制坐标轴来自实际文件头。
- 成员大小/CRC32`74a62fd1`符合原归档目录；本地完整SHA256`65a0df50e96628adec8add7036f0602e7b761d8ce6a0d77db8eaf5c2f67570a5`。采用有界HTTP Range取得成员，没有下载13GB整体归档，未验证整体MD5；CRC不等同于发布者密码学签名。
- 原字节、压缩范围、README、飞行元数据和预先选择记录保持；飞行CSV从已保存归档尾部复用，没有再次下载。
- 实际CLI输出`workspace/evidence/poplar-flight02-inspection-20261012-v1.json`；取得证据`poplar-flight02-acquired-20261012-v1.json`。原数据尚未授予Agent，也没有用于模型、科学产品计算或精度评分。

验证只因新增入口触发，范围见`cloud-input-verification-scope-20261012-v1.json`。未知条件保留、冲突/真值可见、截断点记录拒绝和超大头有界读取均已局部通过，实际CLI完成后停止；阶段1模块、模型、依赖及UI流程没有重跑。

下一项是固定PDAL/lidR运行时并复用现有Jobs，明确隔离原分类注释，执行地面分类/DTM/DSM/HAG，再检查空间支持。不能以输入检查替代科学流程、原始RGB夹具、树种/异速模型、领域UI或阶段3验收。
