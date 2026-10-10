import { useEffect, useState } from 'react';
import { api } from './api';

type Citation={resource_id:string;relative_path:string;source_version:string;source_sha256:string;unit:string};
type Metadata={import_id:string;claim_kind:string;source_label:string;conditions:string;limitations:string};
type Import=Metadata&{citation:Citation;classification:string;status:string;source_status:string;has_more:boolean;next_page:number|null};
type Listing={imports:Import[];next:number;has_more:boolean};
type Content=Import&{document:{text:string;citation:Citation}};
type Search={matches:{chunk_id:string;text:string;citation:Citation;origin:string;imports:Metadata[]}[];
  source_notices:{import_id:string;status:string;source_status:string}[];candidate_limit_reached:boolean;legacy_text_chunks_filtered?:number;notice:string};
const kinds:Record<string,string>={expert_assertion:'专家意见',measurement:'测量记录',reference:'参考资料'};
const states:Record<string,string>={active:'当前导入',superseded:'已被新版替代',current:'来源版本一致',stale:'来源已改变，需重新导入',snapshot_invalid:'快照校验失败',unavailable:'来源不可用',reader_outdated:'读取方式已更新，需重新导入'};

export default function KnowledgePanel({resources,tasks,onError}:{resources:{resource_id:string;name:string;kind:string;classification:string}[];
  tasks:{task_id:string;objective:string}[];onError:(message:string)=>void}) {
  const [listing,setListing]=useState<Listing>({imports:[],next:0,has_more:false});
  const [resource,setResource]=useState(''),[relative,setRelative]=useState(''),[page,setPage]=useState(0);
  const [kind,setKind]=useState('expert_assertion'),[label,setLabel]=useState('');
  const [conditions,setConditions]=useState(''),[limitations,setLimitations]=useState('');
  const [busy,setBusy]=useState(false),[nextPage,setNextPage]=useState<number|null>(null);
  const [content,setContent]=useState<Content|null>(null),[result,setResult]=useState<Search|null>(null);
  const [taskId,setTaskId]=useState(''),[query,setQuery]=useState('');
  const guard=async(work:()=>Promise<void>)=>{setBusy(true);onError('');try{await work();}catch(error){onError((error as Error).message);}finally{setBusy(false);}};
  const load=async(offset=0)=>{
    const next=await api<Listing>(`/knowledge?offset=${offset}`);
    setListing(current=>offset?{...next,imports:[...current.imports,...next.imports]}:next);
  };
  useEffect(()=>{void guard(()=>load());},[]);
  const citation=(pointer:Citation)=><details className="knowledge-citation"><summary>来源引用：{resources.find(value=>value.resource_id===pointer.resource_id)?.name||pointer.resource_id} · {pointer.unit}</summary>
    <small>资源：{pointer.resource_id}<br/>路径：{pointer.relative_path||'已登记文件'}<br/>版本：{pointer.source_version}<br/>SHA-256：{pointer.source_sha256}</small></details>;
  const metadata=(value:Metadata)=><p>{kinds[value.claim_kind]} · {value.source_label||'未填写来源说明'}<br/>
    适用条件：{value.conditions||'未声明'}<br/>局限：{value.limitations||'未声明'}</p>;
  return <section className="knowledge-panel"><p>从已登记的只读来源明确导入，保留不可变来源快照和版本。分类与条件由你填写，导入不代表科学主张已验证。</p>
    <label>知识来源<select value={resource} onChange={event=>{setResource(event.target.value);setPage(0);setRelative('');setNextPage(null);}}>
      <option value="">选择已登记来源</option>{resources.map(value=><option key={value.resource_id} value={value.resource_id}>{value.name} · {value.classification==='private'?'私密':'公开'}</option>)}
    </select></label>
    {resources.find(value=>value.resource_id===resource)?.kind==='directory'&&<label>来源中的相对文件路径<input value={relative} onChange={event=>setRelative(event.target.value)} maxLength={4096}/></label>}
    <label>页／窗口（从 0 开始）<input type="number" min={0} value={page} onChange={event=>{setPage(Number(event.target.value));setNextPage(null);}}/></label>
    <label>资料分类<select value={kind} onChange={event=>setKind(event.target.value)}>{Object.entries(kinds).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
    <label>知识来源说明<input value={label} maxLength={1000} onChange={event=>setLabel(event.target.value)}/></label>
    <label>知识适用条件<input value={conditions} maxLength={2000} onChange={event=>setConditions(event.target.value)}/></label>
    <label>知识局限<input value={limitations} maxLength={2000} onChange={event=>setLimitations(event.target.value)}/></label>
    <button className="primary" disabled={busy||!resource||page<0} onClick={()=>void guard(async()=>{
      const imported=await api<Import>('/knowledge',{request_id:crypto.randomUUID(),resource_id:resource,relative_path:relative,page,
        claim_kind:kind,source_label:label,conditions,limitations});setNextPage(imported.next_page);setResult(null);await load();
    })}>明确导入本页／窗口</button>
    {nextPage!==null&&<button disabled={busy} onClick={()=>{setPage(nextPage);setNextPage(null);}}>准备下一页／窗口 {nextPage}</button>}
    <p>新任务选入该来源后可检索。导入保留意见冲突，不裁定哪一条正确；导入不会自动扩大任务的输入授权。</p>
    <h3>已导入来源</h3><button disabled={busy} onClick={()=>void guard(()=>load())}>刷新知识来源</button>
    <div className="knowledge-list">{listing.imports.map(value=><article key={value.import_id} data-import-id={value.import_id}>
      {metadata(value)}<p>{states[value.status]||value.status} · {states[value.source_status]||value.source_status}</p>{citation(value.citation)}
      <button disabled={busy} onClick={()=>void guard(async()=>setContent(await api<Content>(`/knowledge/${value.import_id}/content`)))}>查看来源快照</button>
    </article>)}</div>
    {listing.has_more&&<button disabled={busy} onClick={()=>void guard(()=>load(listing.next))}>加载更多来源</button>}
    {content&&<div role="region" aria-label="知识来源快照"><h4>来源快照</h4><button onClick={()=>setContent(null)}>收起快照</button>
      <p>{states[content.status]} · {states[content.source_status]}。旧版快照用于追溯，当前检索会过滤过期来源。</p>{citation(content.citation)}<pre>{content.document.text}</pre></div>}
    <h3>按任务授权范围检索</h3><label>检索任务<select value={taskId} onChange={event=>{setTaskId(event.target.value);setResult(null);}}><option value="">选择任务</option>
      {tasks.map(task=><option key={task.task_id} value={task.task_id}>{task.objective}</option>)}</select></label>
    <label>中文／英文检索词<input value={query} onChange={event=>setQuery(event.target.value)} maxLength={2000}/></label>
    <button disabled={busy||!taskId||!query.trim()} onClick={()=>void guard(async()=>setResult(await api<Search>(`/tasks/${taskId}/knowledge/search`,{query})))}>检索授权知识</button>
    {result&&<div role="region" aria-label="知识检索结果"><p>{result.notice||'只检索本任务授权来源。'}</p>
      {result.candidate_limit_reached&&<p>达到候选上限，请缩小检索范围；不能据此断言其他意见不存在。</p>}
      {!!result.legacy_text_chunks_filtered&&<p>已过滤旧版文本窗口索引，请重新读取或导入当前来源。</p>}
      {result.source_notices?.length>0&&<p>有 {result.source_notices.length} 条来源已改变、不可用或被替代，未作为当前建议返回。</p>}
      {!result.matches.length&&<p>没有当前授权范围内的有效匹配。</p>}
      {result.matches.map(match=><article key={match.chunk_id}><p>{match.text}</p>{citation(match.citation)}
        {match.imports?.length?match.imports.map(value=><div key={value.import_id}>{metadata(value)}</div>):<small>任务资料缓存，未作为可复用知识明确导入。</small>}
      </article>)}</div>}
  </section>;
}
