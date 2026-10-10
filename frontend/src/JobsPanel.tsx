import { useEffect, useState } from 'react';
import { api } from './api';
import './JobsPanel.css';

type Budget={cpu_threads:number;ram_bytes:number;temporary_bytes:number;output_bytes:number|null};
type Job={job_id:string;status:string;kind:string;resource_request?:Budget;legacy_container_limits_unverified?:boolean;
  blocker?:{code:string;message:string};admission?:{decision:string;effective_cpu_threads?:number;
    missing:string[];required:{ram_bytes?:number;disk_bytes?:number};
    profile:{observed_at:string;source?:string;host_ram_available?:number;executor_ram_available?:number;workspace_disk_free?:number}};
  output_bytes?:number;resource_stop_requested?:{message:string}};
type Listing={jobs:Job[];next:number;has_more:boolean};
const bytes=(value?:number|null)=>value==null?'未记录':`${(value/2**20).toLocaleString(undefined,{maximumFractionDigits:1})} MiB`;
const statuses:Record<string,string>={queued:'排队',creating:'准备容器',running:'运行中',collecting:'封存产物',succeeded:'成功',failed:'失败',cancelled:'已取消',timed_out:'超时'};

export default function JobsPanel({taskId,refreshKey,onError}:{taskId:string;refreshKey:string;onError:(message:string)=>void}) {
  const [listing,setListing]=useState<Listing>({jobs:[],next:0,has_more:false});
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    let current=true;
    if(!taskId){setListing({jobs:[],next:0,has_more:false});return;}
    setBusy(true);
    api<Listing>(`/tasks/${taskId}/jobs`).then(value=>{if(current)setListing(value);})
      .catch(error=>{if(current)onError((error as Error).message);}).finally(()=>{if(current)setBusy(false);});
    return ()=>{current=false;};
  },[taskId,refreshKey]);
  const load=async(offset=0)=>{
    setBusy(true);
    try{const value=await api<Listing>(`/tasks/${taskId}/jobs?offset=${offset}`);
      setListing(old=>offset?{...value,jobs:[...old.jobs,...value.jobs]}:value);
    }catch(error){onError((error as Error).message);}finally{setBusy(false);}
  };
  return <section className="jobs-panel" aria-label="作业资源与状态">
    <button disabled={busy} onClick={()=>void load()}>刷新作业</button>
    <p>资源不足时等待，同一个作业保留原预算。资源快照显示最近一次准入或等待的读数。</p>
    {!listing.jobs.length&&<p>当前任务还没有隔离作业。</p>}
    {listing.jobs.map(job=><article key={job.job_id}>
      <h3>{statuses[job.status]||job.status} · {job.kind==='environment.prepare'?'准备依赖':'代码运行'}</h3>
      <small>作业 {job.job_id}</small>
      {job.blocker&&<p className="limitations" role="status">{job.blocker.message}（{job.blocker.code}）</p>}
      {job.resource_stop_requested&&<p>资源停止原因：{job.resource_stop_requested.message}。仅确认容器停止后才显示终态。</p>}
      {job.legacy_container_limits_unverified?<p>历史作业未保存完整资源预算；这里的限额不能作为原容器配置证明。</p>:
        job.resource_request&&<dl><dt>CPU 上限 / 实际准入</dt><dd>{job.resource_request.cpu_threads} / {job.admission?.effective_cpu_threads??'待准入'}</dd>
          <dt>容器内存上限</dt><dd>{bytes(job.resource_request.ram_bytes)}</dd>
          <dt>内存临时盘上限</dt><dd>{bytes(job.resource_request.temporary_bytes)}（计入容器内存）</dd>
          <dt>总输出预算</dt><dd>{bytes(job.resource_request.output_bytes)}</dd>
          {job.output_bytes!=null&&<><dt>实际封存输出</dt><dd>{bytes(job.output_bytes)}</dd></>}
        </dl>}
      {job.admission&&<details><summary>查看资源快照</summary><dl>
        <dt>测量时间</dt><dd>{job.admission.profile.observed_at}</dd>
        <dt>测量来源</dt><dd>{job.admission.profile.source||'工作盘'}</dd>
        <dt>宿主机可用内存</dt><dd>{bytes(job.admission.profile.host_ram_available)}</dd>
        <dt>执行机可用内存</dt><dd>{bytes(job.admission.profile.executor_ram_available)}</dd>
        <dt>工作盘空闲</dt><dd>{bytes(job.admission.profile.workspace_disk_free)}</dd>
        <dt>所需内存 / 磁盘</dt><dd>{bytes(job.admission.required.ram_bytes)} / {bytes(job.admission.required.disk_bytes)}</dd>
      </dl></details>}
    </article>)}
    {listing.has_more&&<button disabled={busy} onClick={()=>void load(listing.next)}>加载更早作业</button>}
    <p>单文件写入有硬限制；总输出由定期检查监测，检查间隔内可能超出预算。当前通用执行器不向作业提供 GPU。</p>
  </section>;
}
