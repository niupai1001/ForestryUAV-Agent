# 工作点云到冠层实例交付

1.50新增实际可用流程：复用原PDAL地形/HAG与精确OriginalZ工作云 → 0.5米p2r CHM → LMF种子与整架次协调 → Dalponte冠层实例 → 原始高程分割LAZ、树顶/树冠GPKG、ID/UID映射、指标表与中文报告。科学状态为partial；这不是现场准确性、边界分块或完整阶段2验收。

真实flight02交付：48个高冠层候选实例，共同有效分析面积2317.5平方米；主LAZ保留1,156,172个原始点和原始高程Z，38,025点分配实例，1,118,147点以treeID=0保留。实例是操作定义的候选对象，不是完整林木清查。原PDAL计算、输入与科学镜像直接复用，没有模型实验或基础流程重跑。

## 操作

沿用[科学镜像配置](LIDAR-TERRAIN-zh.md)。新Task可指定`lidar.p2r_lmf_dalponte.v1`；已有任务须明确修订方法及输入。未冻结科学镜像的旧Task不能自动取得科学执行权限。每个文件均须先登记为只读资源并选入本Task，输入映射JSON保存“文件角色→资源ID”，不是任意路径。

```powershell
uv run --locked forestry-agent lidar-canopy --config config/app.science.example.yaml --task TASK_ID --stage chm --inputs workspace/chm-inputs.json --revision 1 --request-id canopy-chm
```

| 阶段 | 必需输入角色 | 可复用产物 |
|---|---|---|
| chm | working、terrain_quality、terrain_support | chm-p2r-raw.tif、chm-p2r.tif、canopy-support.tif、stage-result.json |
| seeds | chm、chm_record | temporary-seeds.gpkg、accepted-seeds.gpkg、tree-id-mapping.csv、stage-result.json |
| instances | working、original、hag、chm、chm_record、seeds、seed_record | segmented.laz、crown-labels.tif、trees.gpkg、trees.csv、metrics.csv/JSON、instance-quality.json、中文报告 |

`chm_record`和`seed_record`指各自上游stage-result.json。后续阶段显式登记上级文件并保存输入修订，再把`--stage`和`--revision`换成对应值。使用原作业观察/日志/下载入口，不自动验收Task。新原生工具`process_lidar_canopy`使用同一协议及Jobs，不另建执行器。

三个阶段各有独立持久Job，可接着使用已完成阶段；同Task修订/配方/镜像的等价调用返回原Job，包括原失败。源版本变化要求明确修订，不自动重算；即使任务修订新增了其它输入，已正确的上游产品仍应直接选入下游。阶段内部失败须先检查产物，不能仅因失败状态重算已完成计算。

## 配方与身份

注册配方`config/methods/lidar-lidr-canopy.yaml`固定lidR 4.2.3：p2r(subcircle=0, na.fill=NULL)，0.5米共同网格；LMF圆窗直径5米、hmin=2米；Dalponte th_tree=2、th_seed=.45、th_cr=.55，最大树冠直径10米显式解析为20像素。改变填洞/平滑/参数需独立配方版本，不静默混用DSM−DTM与p2r CHM。接口依据官方[lidR手册](https://r-lidar.r-universe.dev/lidR/doc/manual.html)。

CHM沿用已记录地形网格范围和CRS，只在有地形支持且实际采样的像元保留值，缺失不填洞。整架次种子按栅格像元、高度/XY稳定顺序协调，检查正int32范围；UID绑定源与CHM身份及像元。接受种子GPKG的图层固定为accepted_seeds，不猜图层。

Dalponte仅计算一次，其栅格供点标签与面积共同使用。工作云保留负地面残差，点标签只给受支持树冠投影内且HAG≥2米的点；其它点为0。主LAZ重读检查全部原始点、原始高程、HAG与精确ID；源分类/额外oracle字段不作为预测。树冠面积为0.5米分割像元面积，四邻域缺失或外缘接触标记裁剪。CSV/GPKG精确保存整数treeID到文本tree_uid；零支持面积指标为不可用，不交付零密度。

上游记录绑定工作云、CHM、种子文件SHA、镜像与配方。新真实流程的独立读回核对了原始高程、点库存、ID/UID、共同网格、支持、面积与计数/密度；它证明文件与软件不变量，不是独立现场精度。

## 已保留失败与当前范围

最初R读取在计算前因资源ID挂载没有LAS后缀失败；符号链接被rlas normalizePath解析回原ID，仍被拒读；Windows绑定卷又拒绝临时副本chmod。三份原失败保留，修复只改容器内临时文件入口；首次完成CHM后没有重算。临时副本由R读取后移除，原挂载只读。sf先建立PROJ搜索上下文，实际R坐标/栅格/GPKG正常；codetools启动诊断保留，没有重新安装镜像。

当前入口为完整支持架次，最多500万点和100万0.5米CHM像元；超限要求分块，不抽稀换简单数据。独立ROI边缘权重、缓冲分块/跨块ID、≥100GB和现场实例基准仍未交付。没有物种/生物量推断。工作台新增林业结果视图显示现成产物、预览、实例指标及下载；本轮未运行原生模型，不能计首次自动成功率。原始RGB/ODM和阶段3保持待完成。
