# 原始UAV点云到地形与HAG

1.49新增`lidar.smrf_idw.v1`：已授权原始LAS与有来源声明→现有Jobs→真实PDAL分类/地形/支持/HAG→独立离地高度工作文件→中文部分交付报告。通用Python执行仍使用原镜像；科学镜像只由用户配置、在新Task冻结。没有第二执行器、自动安装或Agent联网搜索。

实际交付使用同一份flight02原始输入，1,156,172点，源SHA保持。输出包括1米对齐DTM/DSM、格内地面计数、最近地面距离、支持掩膜、DSM−DTM、保留原始整数Z的HAG LAS及另将Z定义为有效HAG的R工作LAS。原分类/额外标签隔离，SourcePointId映射原源索引；OriginalZ直接复制原源高程。科学结果明确为partial，不表示单木、独立精度或整个阶段2完成。

## 配置与入口

当前开发镜像ID：`sha256:eae1217fee43492a6b837fcdac35f493b790dcb1b8a7aa3dd51990d64114a6a8`，PDAL 2.9.3、Python 3.12.15、R 4.4.3、lidR 4.2.3。实际包锁在`config/science/conda-linux-64.lock.txt`；micromamba官方2.9.0-0二进制SHA256为`366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3`。已有镜像直接复用，不因为继续开发而重新构建。

`config/science/Dockerfile`是后续从实际包锁重建的配方，尚未执行发布重建验证。当前产品证据绑定已有镜像；跨设备安装在阶段3验收。

在自己的应用配置中保留通用`code_image`，增加上述`science_image`，然后启动当前源码服务。新Task冻结该镜像；旧Task未冻结时拒绝科学提交，需要明确创建新Task，不能因配置变化自动扩大旧授权。8769主服务本轮没有重载或增加此配置；新API/native工具由实际应用的独立持久状态交付。领域UI尚未扩展。

用户分别登记原始LAS与声明JSON为只读资源，在新Task选入两者，可指定`requested_method_id: lidar.smrf_idw.v1`。未知方法拒绝，已有指定方法不被替换。授权声明格式见[点云输入检查](CLOUD-INPUTS-zh.md)。

```powershell
uv run --locked forestry-agent lidar-terrain --config config/app.science.example.yaml --task TASK_ID --cloud-ref CLOUD_RESOURCE_ID --declaration-ref DECLARATION_RESOURCE_ID --revision 1 --request-id terrain-one
```

CLI提交后返回持久job_id，使用现有工作台作业/日志/产物页或Jobs API观察；不自动验收Task。Agent在科学Task中可调用`process_lidar_terrain`，再用原`wait_job`。同任务/修订/代码配方/镜像的等价提交返回原Job及原producer action，包含原失败，不自动重试。源文件版本变化时拒绝旧结果复用，要求明确保存任务修订，不自动重算。具体源码修复、明确输入/方法修订才形成新身份。

## 当前科学条件与范围

- 文件必须为可解析LAS、米制投影XY；声明须明确Z为米制高程及垂直基准，且无水平CRS冲突。LAZ头检查已提供，但本完整入口尚未验证LAZ解压。已归一化或未知条件拒绝，不重复归一化。
- 当前为整架次bootstrap：最多500万点、100万1米格网像元。超限不自动抽稀或换简单数据；分块/边界和≥100GB仍待交付。
- 保留有效回波号/总数，SMRF使用last/only；无效回波拒绝，不伪造。withheld排除，原classification与oracle extra dims不用于预测。新的统计离群配方mean_k=8、multiplier=3先标噪声7，SMRF忽略它，DSM排除它。
- SMRF初值cell=1、scalar=1.25、slope=.15、threshold=.5、window=18；不是已验证最佳参数。DTM使用1米IDW、radius=√2、window_size=0；DSM使用同网格binmode格内max。
- 支持bootstrap要求本格至少一个新地面点、格中心最近地面距离≤√2米，并有有限DTM值。保留raw DTM与诊断；支持不足的DTM/CHM写NoData=-9999，不填洞。DSM不是林冠分类。
- PDAL HAG保留原Z，NoData=-9999，zero_ground=false，负残差不抹平。R工作文件另设置Z=有效非噪声HAG，并保留OriginalZ/SourcePointId；不是重新地形归一化。

PDAL选项依据官方[SMRF](https://pdal.io/en/stable/stages/filters.smrf.html)、[GDAL writer](https://pdal.io/en/stable/stages/writers.gdal.html)及[HAG DEM](https://pdal.io/en/stable/stages/filters.hag_dem.html)。共同网格、支持和原Z/点映射均必须实际检查，非NoData本身不证明支持。

## 已有结果复用与失败保留

首次实际Job在分类前因laspy标量赋值失败，无完成科学阶段。修复数组赋值及容器PROJ/GDAL数据路径后，在原Task执行真实计算。地形与HAG已生成后，LAS1.2的Extra Bytes字段因读取器默认忽略而丢失SourcePointId；原失败保持。

仅对这个具体新问题修复传输：新清洗文件用LAS1.4，PDAL读取显式`use_eb_vlr=true`。现有计算文件不重算；在原Task明确授权这些产物并保存修订，再通过独立字段全量点序核对补回编号。四个保留点的无计算传输核验只用于这个新字段问题，没有重复SMRF/地表/HAG。恢复前后七份地形/支持栅格字节一致。

复用接口要求`resume_job_id`及完整`resume_refs`（文件名→已登记资源ID），只接受本Task同镜像失败Job的已发布对应产物，原源/声明/方法保持，容器核验原SHA；其它授权文件也不能冒充旧产品。需要的文件名集合见`lidar_terrain.RESUME_FILES`。

```powershell
uv run --locked forestry-agent lidar-terrain --config config/app.science.example.yaml --task TASK_ID --cloud-ref CLOUD_RESOURCE_ID --declaration-ref DECLARATION_RESOURCE_ID --revision 2 --request-id terrain-postprocess --resume-job FAILED_JOB_ID --resume-refs workspace/authorized-resume-refs.json
```

实际独立审查另发现OriginalZ因PDAL头偏移转写差5.6843×10⁻¹⁴米。未放宽原字段判据：只修元数据，从原源按点编号逐点精确复制，保留工作Z与全部已正确计算。原严格不等值记录保留，最终只复核此项，已有通过项复用。

实际Task `67ac3172-547b-449f-82d1-28d437a31e83`：1073175个新地面点；71×56矩形网格中2380格有声明支持；1134887个有效非噪声HAG点。分母是整架次矩形网格，不是独立样地ROI。内部地面残差不是独立精度。最终证据`workspace/evidence/lidar-terrain-final-20261012-v1.json`绑定原失败、真实计算、复用后处理、元数据修正及最新工作文件；0模型请求，不称首次自动成功。容器仍断网、非root、只读根、只读输入、cap-drop ALL、6GiB/128pids。

下一项：复用最新工作云，实现真实lidR CHM/检测/分割与ID/未分配语义，处理保留的R/PROJ启动诊断，补分块/边界、指标/交付验证与同一UI。原始RGB/ODM、独立科学精度和阶段3正式评估/规模/发布仍未完成。
