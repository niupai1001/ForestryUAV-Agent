import { useEffect, useState } from 'react';
import { File, FolderOpen } from 'lucide-react';
import { api } from './api';
import './SourceBrowser.css';

type Resource = {resource_id:string;name:string;locator:string;kind:string;classification:string};
type Selection = {id:string;path:string;kind:string;cursor:number;version?:string};
type Entries = {entries:{name:string;kind:string;size_bytes?:number|null;readable?:boolean}[];next:number;has_more:boolean};
type Preview = {text:string;source_version:string;unit:string;has_more:boolean;next_page:number|null};

export default function SourceBrowser({resources,activeTask,allowRegistration=true,onRegistered}:{resources:Resource[];activeTask:boolean;allowRegistration?:boolean;
  onRegistered:(resource:Resource)=>Promise<void>}) {
  const [selection,setSelection] = useState<Selection|null>(null);
  const [entries,setEntries] = useState<Entries|null>(null), [preview,setPreview] = useState<Preview|null>(null);
  const [error,setError] = useState(''), [loading,setLoading] = useState(false), [registering,setRegistering] = useState(false);
  const resource = resources.find(r=>r.resource_id===selection?.id);
  useEffect(()=>{if(selection&&!resources.some(value=>value.resource_id===selection.id))setSelection(null);},[resources,selection?.id]);
  const selectRoot = (id:string) => {
    const root=resources.find(r=>r.resource_id===id);
    setSelection(root?{id,path:'',kind:root.kind,cursor:0}:null);
  };
  useEffect(()=>{
    if(!selection)return;
    let current=true;
    setLoading(true);setError('');setEntries(null);setPreview(null);
    const query=new URLSearchParams({relative_path:selection.path});
    if(selection.kind==='directory')query.set('cursor',String(selection.cursor));
    else {
      query.set('page',String(selection.cursor));
      if(selection.version)query.set('expected_version',selection.version);
    }
    void (async()=>{
      try {
        const result=await api<Entries|Preview>(`/resources/${selection.id}/${selection.kind==='directory'?'children':'preview'}?${query}`);
        if(current) {
          if('entries' in result)setEntries(result);else setPreview(result);
        }
      }catch(e){if(current)setError((e as Error).message);}
      finally{if(current)setLoading(false);}
    })();
    return ()=>{current=false;};
  },[selection]);
  const parent = () => {
    if(!selection||!resource)return;
    setSelection({...selection,path:selection.path.split('/').slice(0,-1).join('/'),kind:resource.kind,cursor:0,version:undefined});
  };
  const registerFile = async () => {
    if(!selection||registering)return;
    setRegistering(true);setError('');
    try {
      const result=await api<Resource>(`/resources/${selection.id}/files`,{
        request_id:crypto.randomUUID(),relative_path:selection.path});
      await onRegistered(result);
    }catch(e){setError((e as Error).message);}
    finally{setRegistering(false);}
  };
  return <section className="source-browser" aria-label="浏览已授权来源">
    <h3>浏览会话数据源</h3>
    {!resources.length&&<p>此会话还未添加数据源。</p>}
    <label>选择来源<select aria-label="浏览来源" value={selection?.id||''} disabled={registering||!resources.length} onChange={e=>selectRoot(e.target.value)}>
      <option value="">请选择此会话的文件或目录</option>
      {resources.map(r=><option key={r.resource_id} value={r.resource_id}>{r.name}{r.kind==='directory'?'（目录）':''}</option>)}
    </select></label>
    {resource&&selection&&<>
      <div className="source-location"><span>{resource.name}{selection.path?` / ${selection.path}`:''}</span>
        <small>{resource.classification==='private'?'私密 · 只读':'公开 · 只读'}</small></div>
      <div className="source-browser-actions">
        {selection.path&&<button disabled={registering||loading} onClick={parent}>返回上级</button>}
        <button disabled={registering||loading} onClick={()=>setSelection({...selection,cursor:0,version:undefined})}>刷新来源</button>
      </div>
      {loading&&<p role="status">正在读取本地来源…</p>}
      {error&&<p role="alert">{error}</p>}
      {entries&&<>
        <ul className="source-entries">{entries.entries.map(entry=><li key={entry.name}>
          <button disabled={entry.readable===false||entry.kind==='symlink'||registering}
            onClick={()=>setSelection({id:selection.id,path:[selection.path,entry.name].filter(Boolean).join('/'),kind:entry.kind,cursor:0})}>
            {entry.kind==='directory'?<FolderOpen size={15}/>:<File size={15}/>}<span>{entry.name}</span>
          </button><small>{entry.kind==='directory'?'目录':entry.kind==='symlink'?'链接不可浏览':`${entry.size_bytes??0} 字节`}</small>
        </li>)}</ul>
        {!entries.entries.length&&<p>此目录没有文件。</p>}
        <div className="source-browser-actions">
          {selection.cursor>0&&<button disabled={registering} onClick={()=>setSelection({...selection,cursor:0})}>回到目录开头</button>}
          {entries.has_more&&<button disabled={registering} onClick={()=>setSelection({...selection,cursor:entries.next})}>下一页文件</button>}
        </div>
      </>}
      {preview&&<>
        <small>源版本：{preview.source_version} · {preview.unit}</small>
        <pre aria-label="源文件预览">{preview.text||'（空文件）'}</pre>
        <div className="source-browser-actions">
          {selection.cursor>0&&<button disabled={registering} onClick={()=>setSelection({...selection,cursor:0,version:undefined})}>回到第一页</button>}
          {preview.has_more&&preview.next_page!==null&&<button disabled={registering} onClick={()=>setSelection({...selection,cursor:preview.next_page!,version:preview.source_version})}>继续预览</button>}
        </div>
      </>}
      {allowRegistration&&resource.kind==='directory'&&selection.kind==='file'&&<div className="source-browser-actions">
        <button className="primary" disabled={registering||loading} onClick={()=>void registerFile()}>
          {registering?'正在登记…':activeTask?'登记单文件并准备规范修订':'登记并选择单文件'}</button>
      </div>}
    </>}
  </section>;
}
