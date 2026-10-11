import { useEffect, useState } from 'react';
import './LidarPanel.css';

type Artifact = {artifact_id:string;name:string;size_bytes:number;preview_url:string;download_url:string};
type Quality = {status:string;detected_instances:number;valid_area_m2:number;original_points:number;
  assigned_points:number;unassigned_points:number;limitations:string[]};
type Row = {treeID:string;tree_uid:string;apex_height_m:string;crown_area_m2:string;crown_clipped:string;accepted_instance:string};
const number=(value?:number,digits=0)=>value==null?'不可用':value.toLocaleString('zh-CN',{maximumFractionDigits:digits});
function csvRows(text:string):Row[] {
  const fields=(line:string)=>(line.match(/("(?:[^"]|"")*"|[^,]*)(,|$)/g)||[])
    .filter((v,i,a)=>v!==''||i<a.length-1).map(v=>v.replace(/,$/,'').replace(/^"|"$/g,'').replace(/""/g,'"'));
  const lines=text.trim().split(/\r?\n/),headers=fields(lines[0]||'');
  return lines.slice(1).map(line=>Object.fromEntries(fields(line).map((v,i)=>[headers[i],v])) as Row);
}
export default function LidarPanel({taskId,artifacts,onError}:{taskId:string;artifacts:Artifact[];onError:(message:string)=>void}) {
  const [quality,setQuality]=useState<Quality|null>(null),[rows,setRows]=useState<Row[]>([]);
  const [range,setRange]=useState<number[]|null>(null);
  const signature=artifacts.filter(a=>['instance-quality.json','trees.csv','冠层产品读回检查.json'].includes(a.name)).map(a=>a.artifact_id).join(',');
  const file=(name:string)=>artifacts.filter(a=>a.name===name).at(-1);
  useEffect(()=>{
    let current=true;setQuality(null);setRows([]);setRange(null);
    const read=async(name:string)=>{
      const artifact=file(name);if(!artifact)return null;
      if(artifact.size_bytes>131072)throw new Error(`${name} 超出结果视图读取范围，请下载完整文件。`);
      const response=await fetch(artifact.preview_url);if(!response.ok)throw new Error('林业结果读取失败');
      return response.text();
    };
    Promise.all([read('instance-quality.json'),read('trees.csv'),read('冠层产品读回检查.json')]).then(([q,t,a])=>{
      if(!current)return;
      setQuality(q?JSON.parse(q):null);setRows(t?csvRows(t):[]);setRange(a?JSON.parse(a).chm_preview_range_m:null);
    }).catch(error=>{if(current)onError((error as Error).message);});
    return()=>{current=false;};
  },[taskId,signature]);
  const links=['segmented.laz','chm-p2r.tif','canopy-support.tif','crown-labels.tif','trees.gpkg','tree-id-mapping.csv','trees.csv','metrics.csv','单木处理报告.md'];
  return <section className="lidar-panel" aria-label="林业点云结果">
    <h3>冠层候选实例</h3>
    <p className="limitations">部分交付；现场准确性尚未验证。候选实例不代表完整林木清查。</p>
    {!quality?<p>完成 CHM、种子协调和分割后，这里显示实际产物。已完成阶段应直接复用。</p>:<>
      <dl className="lidar-summary"><div><dt>检出候选实例</dt><dd>{number(quality.detected_instances)}</dd></div>
        <div><dt>有效分析面积</dt><dd>{number(quality.valid_area_m2,2)} m²</dd></div>
        <div><dt>保留原始点</dt><dd>{number(quality.original_points)}</dd></div>
        <div><dt>未分配点</dt><dd>{number(quality.unassigned_points)}</dd></div></dl>
      <p>分割点云保留原始高程；未分配点以 treeID=0 保留。分析面积采用采样与地形支持共同有效像元。</p>
    </>}
    {['冠层高度预览.png','冠层实例预览.png'].map(name=>{const a=file(name);return a?<figure key={a.artifact_id}>
      <a href={a.download_url}><img src={a.preview_url} alt={name.replace('.png','')} /></a><figcaption>{name.replace('.png','')}
        {name==='冠层高度预览.png'&&range&&<> · {number(range[0],2)}–{number(range[1],2)} m</>}
      </figcaption><small>北向上；透明区域为缺失或未分配。完整空间坐标见下载的 GeoTIFF。</small></figure>:null;})}
    {rows.length>0&&<><h4>实例指标</h4><div className="lidar-table-scroll"><table><thead><tr><th>ID</th><th>树顶高度 m</th><th>投影面积 m²</th><th>边缘裁剪</th></tr></thead>
      <tbody>{rows.filter(r=>r.accepted_instance==='TRUE').slice(0,200).map(r=><tr key={r.tree_uid} title={r.tree_uid}><td>{r.treeID}</td>
        <td>{number(Number(r.apex_height_m),2)}</td><td>{number(Number(r.crown_area_m2),2)}</td><td>{r.crown_clipped==='TRUE'?'是':'否'}</td></tr>)}</tbody></table></div>
      <p>仅展示前200个接受实例；整数 ID 与文本 UID 的完整映射、单位和局限在下载表中。</p></>}
    <h4>下载交付文件</h4><ul>{links.map(name=>{const a=file(name);return a?<li key={a.artifact_id}><a href={a.download_url}>{name}</a></li>:null;})}</ul>
    {quality&&quality.limitations?.length>0&&<details><summary>查看适用范围与局限</summary><ul>{quality.limitations.map((s,i)=><li key={i}>{s}</li>)}</ul></details>}
  </section>;
}
