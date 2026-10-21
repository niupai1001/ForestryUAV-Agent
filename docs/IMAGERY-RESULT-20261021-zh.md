# N盘多光谱影像迁移实跑报告

日期：2026-10-21。状态：真实正射/DSM及PROSAIL反演已完成并验读；探索性科学结果，未做现场精度验证。

## 实际交付

用户授权的N盘多光谱目录共80张DJI M3M影像、20个完整四波段拍摄组。现有持久Jobs完成正射/DSM和内置ODM报告，再复用已生成500条LUT，产生LAI、Cab、光谱RMSE及预览。全过程没有模型请求，原图像素未送入聊天上下文；这证明科学文件处理路径可用，不等同于真实批量视觉问答覆盖。

- [正射GeoTIFF](<C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1/workspace/imagery-migration-20261021/real-flight-runtime/tasks/7cbf2d4f-75b8-4439-8076-93b17943e0f1/artifacts/26b500d7-9b7c-4b6b-be00-b64ff051b1e0/odm_orthophoto.tif>)：3118×1888，5个波段，EPSG:32650，约5cm网格。
- [DSM](<C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1/workspace/imagery-migration-20261021/real-flight-runtime/tasks/7cbf2d4f-75b8-4439-8076-93b17943e0f1/artifacts/5e1716d1-15c9-4dcb-8d74-7d42c81c291b/dsm.tif>)，相同CRS及3118×1888网格。
- [LAI / Cab / spectral_RMSE GeoTIFF](<C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1/workspace/imagery-migration-20261021/real-flight-runtime/tasks/7cbf2d4f-75b8-4439-8076-93b17943e0f1/artifacts/c9027914-0a9c-45e6-b5a9-8c0dd2b41918/prosail_lai_cab_rmse.tif>)。
- [LAI预览](<C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1/workspace/imagery-migration-20261021/real-flight-runtime/tasks/7cbf2d4f-75b8-4439-8076-93b17943e0f1/artifacts/031051e5-df78-4065-99f8-dadf8b3dd1d2/lai_preview.png>)；颜色仅用于观察，不替代数值。
- [ODM内置处理报告](<C:/Users/yzh/WorkBuddy/Agent/forestry-uav-agent-v1/workspace/imagery-migration-20261021/real-flight-runtime/tasks/7cbf2d4f-75b8-4439-8076-93b17943e0f1/artifacts/9068c740-0539-496d-a99f-be9c0d495a1f/report.pdf>)。

## 方法与参数来源

太阳天顶角48.17096°来自旧NOAA元数据脚本，按拍摄地时间UTC+8假设；正射近天底观测0°，相对方位0°。均匀LUT500条，seed20260914，11个参数范围沿用旧run_prosail.py探索性先验；PROSPECT-D/SAIL、ant=0、typelidf=2、factor=SDR，alpha沿用库默认40。矩形SRF近似，不是厂家实测响应。ODM使用camera辐射归一化，未做面板或独立反射率验证。

实际波段为Red/Green/NIR/RedEdge/Alpha，其中Alpha无description；PROSAIL明确映射[1,2,4,3]至Red/Green/RedEdge/NIR，不沿用旧场景[1,2,5,4]。NDVI≥0.2；匹配使用旧版按LUT波段标准差缩放的单最近邻，不改成加权KNN。LUT SHA256：`b47b03105946d935af44abb7b93d7a09895d1fbb69c0ab7cd1aa08508a2fdd33`。

## 结果与验读

| 数量 | 最小 | 最大 | 均值 |
|---|---:|---:|---:|
| LAI（m²/m²） | 0.322389 | 6.988197 | 0.965441 |
| Cab（μg/cm²） | 20.948878 | 69.699705 | 68.655895 |
| 光谱RMSE（反射率比例） | 0.000593 | 0.078587 | 0.037918 |

满足输入筛选条件并有模型估计的像元4,935,083个。独立只读验读确认正射与反演的CRS/transform/宽高一致，反演为float32三波段和NaN无效值。发现GDAL对该浮点Alpha返回all_valid，因此另核对Alpha：透明区域692348像元，已有有效LAI落在透明区域的数量为0。**结果数值正确保留，没有为Alpha问题重跑反演**；后续代码补按GeoTIFF colorinterp识别未命名Alpha。

Cab均值68.656接近先验上界70，LAI预览空间变化较少。这是需要后续复核反射率归一化、参数先验、SRF和模型适用性的实际质量信号，不能把“流程完成”解释为LAI/Cab可靠精度已证明。本轮不改先验重建LUT、不反复试参数制造更好看的结果。光谱RMSE只衡量光谱拟合，不是现场LAI误差。

## 失败保留与恢复

- 原ODM Job `cddf2b58-84ab-4b04-865c-fbf1bcb14542`在纹理文件复制POSIX权限位时失败，重建/稠密云/网格封存。
- 恢复Job `295fc935-b35c-4ebb-b7a7-198b8cfe9d4b`复用上述成果，生成正确正射/DSM，内置报告复制静态图片时遇到同类权限问题；保持failed。
- Job `a10b8b0d-2a6d-4423-86af-c914755173c0`只从odm_report修复报告及收口，未重新计算科学产品，最终succeeded。
- 反演Job `f3fa2551-f5e5-4903-b3b4-cd52ee9c3ae2` succeeded，复用既有LUT。

兼容仅处理本作业project内目标文件的EPERM复制元数据错误，源限定本project或固定镜像/code静态资产；数据复制错误不屏蔽。Docker保持非root、断网、只读根和配额。恢复校验同会话、镜像、声明、源版本及封存文件SHA。首次80挂载造成Docker inspect超64KiB的问题也已修复，复用原容器首次启动。

## 范围与证据

点云剩余开发暂缓；联网搜索与领域峰值资源/吞吐评估取消。已有基础与点云结果保持冻结。模型300秒无首段输出仍是独立未解决问题。

证据目录：`workspace/imagery-migration-20261021`，包括元数据、数值契约、真实作业句柄、product-readback.json、alpha-readback.json；完整输入/命令/运行环境/失败及产物记录在real-flight-runtime中。当前任务ID `7cbf2d4f-75b8-4439-8076-93b17943e0f1`。这些为隔离验收工作区数据，原8769用户会话未参与计算或被删除。
