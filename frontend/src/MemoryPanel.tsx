import { useEffect, useState } from 'react';

type Content = {text:string;tags:string[];scope_task_ids:string[];source_note:string;
  conditions:string;limitations:string;expires_at:string|null};
type Note = Content & {memory_id:string;version:number;active:boolean;created_at:string;updated_at?:string};
type Notes = {notes:Note[];next:number;has_more:boolean};
type History = {versions:{note:Note;user_event_id:string}[];next:number;has_more:boolean};
const empty:Content = {text:'',tags:[],scope_task_ids:[],source_note:'',conditions:'',limitations:'',expires_at:null};
const contentOf=(note:Note):Content=>({text:note.text,tags:note.tags,scope_task_ids:note.scope_task_ids,
  source_note:note.source_note,conditions:note.conditions,limitations:note.limitations,expires_at:note.expires_at});

async function request<T>(path:string,payload?:object,method='POST'):Promise<T> {
  const response=await fetch(path,payload?{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error?.message||`请求失败 (${response.status})`);
  return data;
}

export default function MemoryPanel({tasks,onError}:{tasks:{task_id:string;objective:string}[];onError:(message:string)=>void}) {
  const [listing,setListing]=useState<Notes>({notes:[],next:0,has_more:false});
  const [archived,setArchived]=useState(false), [busy,setBusy]=useState(false);
  const [editing,setEditing]=useState<Note|null>(null), [draft,setDraft]=useState<Content>(empty);
  const [preview,setPreview]=useState(false), [history,setHistory]=useState<History|null>(null);
  const [historyId,setHistoryId]=useState<string|null>(null);
  const guard=async(work:()=>Promise<void>)=>{setBusy(true);onError('');try{await work();}catch(error){onError((error as Error).message);}finally{setBusy(false);}};
  const load=async(offset=0)=>{
    const next=await request<Notes>(`/memory?include_inactive=${archived}&offset=${offset}`);
    setListing(current=>offset?{...next,notes:[...current.notes,...next.notes]}:next);
  };
  useEffect(()=>{void guard(()=>load());},[archived]);
  const reset=()=>{setEditing(null);setDraft({...empty});setPreview(false);};
  const save=async()=>{
    if(editing)await request(`/memory/${editing.memory_id}`,{...draft,request_id:crypto.randomUUID(),expected_version:editing.version},'PATCH');
    else await request('/memory',{...draft,request_id:crypto.randomUUID()});
    reset();setHistory(null);await load();
  };
  const showHistory=async(id:string,offset=0)=>{
    const next=await request<History>(`/memory/${id}/versions?offset=${offset}`);
    setHistory(current=>offset&&current?{...next,versions:[...current.versions,...next.versions]}:next);setHistoryId(id);
  };
  const choices=[...new Set([...tasks.map(task=>task.task_id),...draft.scope_task_ids])];
  const scopeLabel=(note:Content)=>note.scope_task_ids.length?note.scope_task_ids.map(id=>tasks.find(task=>task.task_id===id)?.objective||id).join('；'):'所有后续私密任务';
  const details=(note:Content)=><dl className="memory-details">
    <dt>适用范围</dt><dd>{scopeLabel(note)}</dd>
    <dt>来源与依据</dt><dd>{note.source_note||'用户直接提供，未填写来源说明'}</dd>
    <dt>适用条件</dt><dd>{note.conditions||'未声明'}</dd>
    <dt>局限</dt><dd>{note.limitations||'未声明'}</dd>
    <dt>有效期</dt><dd>{note.expires_at?new Date(note.expires_at).toLocaleString():'未设到期时间'}</dd>
  </dl>;
  return <section className="memory-panel"><h3>你保存的长期记忆</h3>
    <p>保存前可预览，更正和停用保留历史版本。助手只读取当前有效且适用于任务的记忆。来源说明由你提供，未自动验证。</p>
    {preview?<div className="memory-preview" role="region" aria-label="记忆保存预览">
      <h4>{editing?`更正版本 ${editing.version} → ${editing.version+1}`:'保存新记忆'}</h4>
      <p>{draft.text}</p>{details(draft)}
      <div className="memory-actions"><button disabled={busy} onClick={()=>setPreview(false)}>返回编辑</button>
        <button className="primary" disabled={busy} onClick={()=>void guard(save)}>{editing?'明确保存更正':'明确保存这条记忆'}</button></div>
    </div>:<div className="memory-editor">
      {editing&&<p>正在更正版本 {editing.version}。保存时会检查版本，避免覆盖其他修改。</p>}
      <label>希望长期记住的内容<textarea value={draft.text} maxLength={8000} onChange={e=>setDraft({...draft,text:e.target.value})}/></label>
      <label>来源与依据<input value={draft.source_note} maxLength={2000} onChange={e=>setDraft({...draft,source_note:e.target.value})}/></label>
      <label>适用条件<input value={draft.conditions} maxLength={2000} onChange={e=>setDraft({...draft,conditions:e.target.value})}/></label>
      <label>局限<input value={draft.limitations} maxLength={2000} onChange={e=>setDraft({...draft,limitations:e.target.value})}/></label>
      <label>到期时间（可选）<input type="datetime-local" value={draft.expires_at?new Date(new Date(draft.expires_at).getTime()-new Date(draft.expires_at).getTimezoneOffset()*60000).toISOString().slice(0,16):''}
        onChange={e=>setDraft({...draft,expires_at:e.target.value?new Date(e.target.value).toISOString():null})}/></label>
      <details><summary>适用任务：{draft.scope_task_ids.length?`已选 ${draft.scope_task_ids.length} 项`:'所有后续私密任务'}</summary>
        <p>不勾选时适用于所有私密任务；勾选后只供所选任务读取。记忆内容不能授予文件或联网权限。</p>
        {choices.map(id=><label className="checkbox" key={id}><input type="checkbox" checked={draft.scope_task_ids.includes(id)}
          onChange={e=>setDraft({...draft,scope_task_ids:e.target.checked?[...draft.scope_task_ids,id]:draft.scope_task_ids.filter(value=>value!==id)})}/>{tasks.find(task=>task.task_id===id)?.objective||id}</label>)}
      </details>
      <div className="memory-actions"><button className="primary" disabled={busy||!draft.text.trim()} onClick={()=>setPreview(true)}>预览{editing?'更正':'记忆'}</button>
        {editing&&<button disabled={busy} onClick={reset}>取消更正</button>}</div>
    </div>}
    <label className="checkbox"><input type="checkbox" checked={archived} disabled={busy} onChange={e=>setArchived(e.target.checked)}/>显示已停用和过期记录</label>
    <div className="memory-list">{listing.notes.map(note=><div key={note.memory_id}>
      <div><span>{note.text}</span><p>版本 {note.version} · {!note.active?'已停用':note.expires_at&&Date.parse(note.expires_at)<=Date.now()?'已过期':'有效'}</p>{details(note)}
        <div className="memory-actions"><button disabled={busy} onClick={()=>void guard(()=>showHistory(note.memory_id))}>查看历史</button>
          {note.active&&<><button disabled={busy} onClick={()=>{setEditing(note);setDraft(contentOf(note));setPreview(false);}}>更正</button>
          <button title="停用这条记忆" disabled={busy} onClick={()=>void guard(async()=>{
            await request(`/memory/${note.memory_id}`,{request_id:crypto.randomUUID(),expected_version:note.version},'DELETE');
            if(editing?.memory_id===note.memory_id)reset();await load();if(historyId===note.memory_id)await showHistory(note.memory_id);
          })}>停用</button></>}</div>
      </div></div>)}</div>
    {listing.has_more&&<button disabled={busy} onClick={()=>void guard(()=>load(listing.next))}>加载更多记忆</button>}
    {history&&<div className="memory-history" role="region" aria-label="记忆版本历史"><h4>记忆版本历史</h4>
      <button onClick={()=>{setHistory(null);setHistoryId(null);}}>收起历史</button>
      {history.versions.map(({note,user_event_id})=><details key={note.version}><summary>版本 {note.version} · {note.active?'保存／更正':'已停用'}</summary>
        <p>{note.text}</p>{details(note)}<small>用户操作记录：{user_event_id}</small></details>)}
      {history.has_more&&<button disabled={busy} onClick={()=>void guard(()=>showHistory(historyId!,history.next))}>加载更早版本</button>}
    </div>}
  </section>;
}
