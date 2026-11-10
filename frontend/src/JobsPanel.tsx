import { useEffect, useState } from 'react';
import { api } from './api';
import './JobsPanel.css';

type Budget={cpu_threads:number;ram_bytes:number;temporary_bytes:number;output_bytes:number|null};
type Job={job_id:string;status:string;kind:string;resource_request?:Budget;legacy_container_limits_unverified?:boolean;
  readable_name?:string;request_turn_id?:string;created_at?:string;cancel_requested?:boolean;artifact_ids?:string[];
  delivery?:{state:string;consumer_status?:string|null;failure?:{code:string;message:string}|null}|null;
  blocker?:{code:string;message:string};admission?:{decision:string;effective_cpu_threads?:number;
    missing:string[];required:{ram_bytes?:number;disk_bytes?:number};
    profile:{observed_at:string;source?:string;host_ram_available?:number;executor_ram_available?:number;workspace_disk_free?:number}};
  output_bytes?:number;resource_stop_requested?:{message:string}};
type Listing={jobs:Job[];next:number;has_more:boolean};
const bytes=(value?:number|null)=>value==null?'未记录':`${(value/2**20).toLocaleString(undefined,{maximumFractionDigits:1})} MiB`;
const statuses:Record<string,string>={queued:'排队',creating:'准备容器',running:'运行中',collecting:'封存产物',succeeded:'执行完成',failed:'失败',cancelled:'已取消',timed_out:'超时'};
const terminal=new Set(['succeeded','failed','cancelled','timed_out']);
const deliveryLabels:Record<string,string>={pending:'完成回执未读',presented:'已查看回执',scheduled:'后续分析已排队',claimed:'正在接回结果',
  consumed:'结果已读回',skipped:'已跳过后续分析',needs_attention:'后续分析需要处理'};

export default function JobsPanel({taskId,refreshKey,onError,artifacts=[],messages=[]}:{taskId:string;refreshKey:string;onError:(message:string)=>void;
  artifacts?:{artifact_id:string;name:string}[];messages?:{turn_id?:string;role:string;text:string}[]}) {
  const [listing,setListing]=useState<Listing>({jobs:[],next:0,has_more:false});
  const [busy,setBusy]=useState(false);
  const [logs,setLogs]=useState<Record<string,string>>({});
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
  const cancel=async(job:Job)=>{
    setBusy(true);
    try{const updated=await api<Job>(`/tasks/${taskId}/jobs/${job.job_id}/cancel`,{request_id:crypto.randomUUID()});
      setListing(old=>({...old,jobs:old.jobs.map(value=>value.job_id===job.job_id?updated:value)}));
    }catch(error){onError((error as Error).message);}finally{setBusy(false);}
  };
  const showLogs=async(job:Job)=>{
    try{const value=await api<unknown>(`/tasks/${taskId}/jobs/${job.job_id}/logs`);
      setLogs(old=>({...old,[job.job_id]:JSON.stringify(value,null,2)}));
    }catch(error){onError((error as Error).message);}
  };
  const deliveryAction=async(job:Job,action:'present'|'skip')=>{
    setBusy(true);
    try{const delivery=await api<Job['delivery']>(`/tasks/${taskId}/jobs/${job.job_id}/delivery`,{request_id:crypto.randomUUID(),action});
      setListing(old=>({...old,jobs:old.jobs.map(value=>value.job_id===job.job_id?{...value,delivery}:value)}));
    }catch(error){onError((error as Error).message);}finally{setBusy(false);}
  };
  return <section className="jobs-panel" aria-label="作业资源与状态">
    <button disabled={busy} onClick={()=>void load()}>刷新作业</button>
    {!listing.jobs.length&&<p>当前任务还没有隔离作业。</p>}
    {listing.jobs.map(job=><article key={job.job_id}>
      <h3>{statuses[job.status]||job.status} · {job.readable_name||(job.kind==='environment.prepare'?'准备依赖':'后台作业')}</h3>
      <p>来源：{messages.find(message=>message.role==='user'&&message.turn_id===job.request_turn_id)?.text||
        (job.request_turn_id?`消息 ${job.request_turn_id}`:'历史记录未保存来源消息')}
        {job.created_at&&<> · <time dateTime={job.created_at}>{new Date(job.created_at).toLocaleString()}</time></>}</p>
      {!terminal.has(job.status)&&<button disabled={busy||job.cancel_requested} onClick={()=>void cancel(job)}>
        {job.cancel_requested?'已请求取消，等待执行器确认':'取消作业'}</button>}
      {!!job.artifact_ids?.length&&<ul aria-label="作业结果">{job.artifact_ids.map(ref=><li key={ref}>
        <a href={`/artifacts/${ref}/content?download=true`}>{artifacts.find(value=>value.artifact_id===ref)?.name||`结果 ${ref}`}</a>
      </li>)}</ul>}
      {job.delivery&&<div className="job-delivery" role="status">
        <p>{deliveryLabels[job.delivery.state]||job.delivery.state}</p>
        {job.delivery.failure&&<p>后续分析未完成：{job.delivery.failure.message}。原作业结果保留，可继续对话处理。</p>}
        {job.delivery.state==='pending'&&<button disabled={busy} onClick={()=>void deliveryAction(job,'present')}>标记已查看</button>}
        {!['consumed','skipped'].includes(job.delivery.state)&&job.delivery.consumer_status!=='running'&&
          <button disabled={busy} onClick={()=>void deliveryAction(job,'skip')}>跳过后续分析</button>}
      </div>}
      {job.blocker&&<p className="limitations" role="status">{job.blocker.message}（{job.blocker.code}）</p>}
      {job.resource_stop_requested&&<p>资源停止原因：{job.resource_stop_requested.message}。仅确认容器停止后才显示终态。</p>}
      <details><summary>日志与执行详情</summary><small>作业 {job.job_id}</small>
      <p>资源不足时等待，同一个作业保留原预算。以下是最近一次准入或等待的读数。</p>
      <button onClick={()=>void showLogs(job)}>查看日志</button>
      {logs[job.job_id]&&<pre>{logs[job.job_id]}</pre>}
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
      </details>
    </article>)}
    {listing.has_more&&<button disabled={busy} onClick={()=>void load(listing.next)}>加载更早作业</button>}
  </section>;
}
