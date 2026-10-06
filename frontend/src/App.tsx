import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowUpRight, Check, ChevronDown, CircleHelp, File, FolderOpen,
  Leaf, MessageSquare, MoreHorizontal, Pause, Play, Plus, RefreshCw, Send, Settings,
  ShieldCheck, Square, Terminal, X } from 'lucide-react';

type Task = {task_id: string; objective: string; status: string; state_version: number;
  spec_revision: number; model_profile: string; input_refs: string[]; artifact_ids: string[];
  config_snapshot?: {harness_mode?: string;context_classification?:string};
  messages: {id: string; role: string; text: string; limitations?: string[]}[];
  blockers: {code: string; message: string}[]; action_count: number;
  last_usage?: {requests: number; input_tokens: number; output_tokens: number}};
type Resource = {resource_id: string; name: string; locator: string; kind: string; classification: string};
type Artifact = {artifact_id: string; name: string; size_bytes: number; media_type: string;
  preview_url: string; download_url: string; sha256: string};
type Event = {sequence: number; event_id: string; type: string; payload: Record<string, any>; at: string};
type Candidate = {candidate_sha256:string; state_version:number; candidate:{spec_revision:number; answer:string;
  limitations:string[]; outputs:{artifact_id:string;name:string;sha256:string}[]}};
type Note = {memory_id:string;text:string;tags:string[]};
type Approval = {approval_id:string;spec_revision:number;request_sha256:string;status:string;expires_at:string;
  request:{kind:string;value:string;url:string;destinations:string[];sources:{resource_id:string;version:string}[]}};
const approvalLabels:Record<string,string> = {pending:'待审批',approved:'已批准，待执行',consumed:'已使用',rejected:'已拒绝',revoked:'已撤销',expired:'已过期'};
type Assignment = {assignment_id:string;role_id:string;status:string;model_requests:number;actions:number;
  effective_input_refs:string[];effective_artifact_refs:string[];request:{objective:string};
  result?:{summary:string;artifact_ids:string[];limitations:string[];checks:{criterion:string;status:string;observation:string}[]} | null;
  blocker?:{message:string}|null};
const roleNames:Record<string,string> = {environment:'环境检查',data:'数据准备',code_experiment:'代码实验',quality:'质量复核'};
const assignmentLabels:Record<string,string> = {queued:'等待开始',running:'运行中',waiting:'等待恢复',succeeded:'已返回',partial:'部分结果',failed:'失败',cancelled:'已取消'};
const labels: Record<string,string> = {created:'等待运行', active:'运行中', paused:'已暂停',
  completed:'已完成', failed:'失败', cancelled:'已取消', waiting_user:'待确认', waiting_job:'处理中', publishing:'正在封存'};
const requestId = () => crypto.randomUUID();
async function api<T>(path: string, payload?: object, method = 'POST'): Promise<T> {
  const reply = await fetch(path, payload ? {method, headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)} : {});
  const data = await reply.json();
  if (!reply.ok) throw new Error(data.error?.message || `请求失败 (${reply.status})`);
  return data;
}

export default function App() {
  const [tasks,setTasks] = useState<Task[]>([]), [active,setActive] = useState<Task|null>(null);
  const [resources,setResources] = useState<Resource[]>([]), [selected,setSelected] = useState<string[]>([]);
  const [artifacts,setArtifacts] = useState<Artifact[]>([]), [events,setEvents] = useState<Event[]>([]);
  const [assignments,setAssignments] = useState<Assignment[]>([]), [harness,setHarness] = useState('H2');
  const [approvals,setApprovals] = useState<Approval[]>([]), [publicTask,setPublicTask] = useState(false);
  const [text,setText] = useState(''), [error,setError] = useState(''), [busy,setBusy] = useState(false);
  const [settings,setSettings] = useState<{version:number; default_model:string; models:Record<string,any>}|null>(null);
  const [showSettings,setShowSettings] = useState(false), [showSource,setShowSource] = useState(false);
  const [path,setPath] = useState(''), [privateSource,setPrivateSource] = useState(true);
  const [diagnostics,setDiagnostics] = useState<any>(null), [panel,setPanel] = useState<'artifacts'|'trace'|'roles'>('artifacts');
  const [preview,setPreview] = useState<Artifact|null>(null), [previewText,setPreviewText] = useState('');
  const [candidate,setCandidate] = useState<Candidate|null>(null), [acceptComment,setAcceptComment] = useState('');
  const [notes,setNotes] = useState<Note[]>([]), [noteText,setNoteText] = useState('');
  const seen = useRef(new Set<string>()), input = useRef<HTMLTextAreaElement>(null);

  const guarded = async (work:()=>Promise<void>) => {setError(''); try {await work();} catch(e) {setError(String((e as Error).message));}};
  const refresh = async () => {setTasks(await api<Task[]>('/tasks'));};
  useEffect(()=>{if(showSettings) void guarded(async()=>{
    setNotes((await api<{notes:Note[]}>('/memory')).notes);
  });},[showSettings]);
  useEffect(()=>{void guarded(async()=>{await api('/session'); await refresh();
    setResources(await api<Resource[]>('/resources')); setSettings(await api('/settings'));
    const saved = localStorage.getItem('forestry.task');
    if(saved) try {setActive(await api<Task>(`/tasks/${saved}`));} catch {localStorage.removeItem('forestry.task');}
  });},[]);
  useEffect(()=>{
    if(!active) {setArtifacts([]); setEvents([]); setAssignments([]); setApprovals([]); return;}
    localStorage.setItem('forestry.task',active.task_id);
    seen.current = new Set(); setEvents([]);
    const id = active.task_id;
    const source = new EventSource(`/tasks/${id}/events`);
    source.onmessage = message => {
      const event:Event = JSON.parse(message.data);
      if(seen.current.has(event.event_id)) return;
      seen.current.add(event.event_id); setEvents(previous => [...previous,event].slice(-500));
      if(event.type !== 'model.text') void guarded(async()=>{
        setActive(await api<Task>(`/tasks/${id}`)); setArtifacts(await api<Artifact[]>(`/tasks/${id}/artifacts`));
        setAssignments(await api<Assignment[]>(`/tasks/${id}/assignments`)); await refresh();
        setApprovals(await api<Approval[]>(`/tasks/${id}/approvals`));
      });
    };
    void guarded(async()=>{setArtifacts(await api<Artifact[]>(`/tasks/${id}/artifacts`));
      setAssignments(await api<Assignment[]>(`/tasks/${id}/assignments`));
      setApprovals(await api<Approval[]>(`/tasks/${id}/approvals`));});
    return ()=>source.close();
  },[active?.task_id]);
  const newTask = () => {setActive(null); setSelected([]); setText(''); localStorage.removeItem('forestry.task'); input.current?.focus();};
  const send = async () => {
    if(!text.trim() || busy) return;
    setBusy(true);
    await guarded(async()=>{
      let result:Task;
      if(active) result = await api(`/tasks/${active.task_id}/messages`,{request_id:requestId(),
        expected_spec_revision:active.spec_revision,text});
      else result = await api('/tasks',{request_id:requestId(),objective:text,input_refs:selected,
        harness_mode:harness,context_classification:publicTask?'public':'private',requested_metrics:[],requested_method_id:null, constraints:{},output_preferences:{report_language:'zh'}});
      setActive(result); setText(''); await refresh();
    }); setBusy(false);
  };
  const control = async (operation:string) => {
    if(!active) return;
    await guarded(async()=>{const current = await api<Task>(`/tasks/${active.task_id}`);
      setActive(await api(`/tasks/${active.task_id}/${operation}`,{request_id:requestId(),expected_state_version:current.state_version})); await refresh();});
  };
  const addSource = async () => {
    await guarded(async()=>{
      const source = await api<Resource>('/resources/register',{request_id:requestId(),path,
        classification:privateSource?'private':'public',read_only:true});
      setResources(await api('/resources')); setSelected(old=>[...new Set([...old,source.resource_id])]);
      setShowSource(false); setPath('');
    });
  };
  const openPreview = async (artifact:Artifact) => {
    setPreview(artifact); setPreviewText('');
    if(!artifact.media_type.startsWith('image/') && artifact.media_type !== 'text/html') {
      await guarded(async()=>{const r = await fetch(artifact.preview_url); if(!r.ok) throw new Error('产物读取失败');
        setPreviewText((await r.text()).slice(0,100000));});
    }
  };
  const live = active?.status === 'active' || active?.status === 'created';
  const resolveApproval = async (record:Approval,verdict:'approve'|'reject'|'revoke') => {
    if(!active||busy)return;
    setBusy(true); await guarded(async()=>{
      await api(`/tasks/${active.task_id}/approvals/${record.approval_id}`,{request_id:requestId(),
        expected_spec_revision:active.spec_revision,request_sha256:record.request_sha256,verdict});
      setApprovals(await api(`/tasks/${active.task_id}/approvals`));
      setActive(await api(`/tasks/${active.task_id}`)); await refresh();
    }); setBusy(false);
  };
  const accept = async (verdict:'accept'|'request_changes') => {
    if(!active||!candidate)return;
    setBusy(true); await guarded(async()=>{setActive(await api(`/tasks/${active.task_id}/acceptance`,{
      request_id:requestId(),expected_state_version:candidate.state_version,
      expected_spec_revision:candidate.candidate.spec_revision,candidate_sha256:candidate.candidate_sha256,
      verdict,comment:acceptComment})); setCandidate(null); await refresh();}); setBusy(false);
  };
  const tools = events.filter(e=>e.type === 'action.started' || e.type === 'action.observed');
  const liveText = events.filter(e=>e.type === 'model.text' && !e.payload.assignment_id).map(e=>e.payload.text).join('');

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon"><Leaf size={23}/></div><div><strong>林遥</strong><span>ForestryUAV Agent</span></div><span className="version">V1</span></div>
      <button className="new-task" onClick={newTask}><Plus size={18}/>新建任务</button>
      <div className="section-caption">工作空间</div>
      <button className="nav-item selected" onClick={()=>input.current?.focus()}><MessageSquare size={18}/>Agent 工作台</button>
      <button className="nav-item" onClick={()=>setShowSource(true)}><FolderOpen size={18}/>本地数据源<span className="small-count">{resources.length}</span></button>
      <div className="section-caption history-title">最近任务<button title="刷新任务" onClick={()=>void guarded(refresh)}><RefreshCw size={13}/></button></div>
      <div className="history">{tasks.length===0?<p className="muted empty-small">任务会保存在这台电脑上</p>:tasks.map(task=><button key={task.task_id}
        className={`history-item ${active?.task_id===task.task_id?'current':''}`} onClick={()=>setActive(task)}>
        <span className={`status-dot ${task.status}`}/><span>{task.objective}</span></button>)}</div>
      <div className="sidebar-bottom"><div className="local-note"><ShieldCheck size={17}/><div>本地执行与存储<span>输入只读 · 联网请求受控</span></div></div>
        <button className="nav-item" onClick={()=>{setShowSettings(true); void guarded(async()=>setDiagnostics(await api('/models/profiles')));}}><Settings size={18}/>模型与环境设置</button>
        <div className="developer"><div className="avatar">L</div><div>本地开发者<span>独立开发版</span></div><MoreHorizontal size={17}/></div>
      </div>
    </aside>
    <div className="main-shell">
      <header><div className="breadcrumb">工作空间<span>/</span><strong>Agent 工作台</strong></div>
        <div className="header-right"><span className="local-pill"><span/> LOCAL ONLY</span><button title="环境设置" onClick={()=>setShowSettings(true)}><Settings size={17}/></button></div></header>
      <main>
        <div className="workspace-heading"><div><div className="eyebrow">YOUR LOCAL RESEARCH PARTNER</div><h1>从一个目标，开始工作。</h1><p>让 Agent 查阅文件、整理证据并交付可检查的结果。</p></div><span className="stage-pill"><span/>通用能力 · 开发中</span></div>
        <div className="workspace-grid"><section className="conversation-card">
          <div className="card-heading"><div className="heading-icon"><MessageSquare size={17}/></div><strong>{active?'当前任务':'新的任务'}</strong>
            <span className={`task-status ${active?.status||''}`}>{active?labels[active.status]:'准备开始'}</span></div>
          <div className="conversation-content">
            {!active?<><div className="welcome-emblem"><Leaf size={32}/></div><h2>你希望完成什么？</h2><p className="welcome-description">描述目标，并选择需要读取的本地文件。<br/>每一步执行、证据和产物都可以在这里检查。</p>
              <div className="suggestions">{[
                ['整理文件与证据','阅读已选文件，提取重点，并保存一份有来源说明的中文 Markdown 报告。',FolderOpen],
                ['检查数据内容','读取已选 CSV 文件，检查字段和缺失值，保存检查结果，不要编造未读取的数据。',File],
                ['编写处理脚本','阅读已选数据文件，编写可复现的数据处理 Python 脚本；当前代码执行未启用时请明确标注未运行。',Terminal]
              ].map(([title,prompt,Icon])=>{const Symbol=Icon as typeof File; return <button key={String(title)} onClick={()=>{setText(String(prompt));input.current?.focus();}}><Symbol size={17}/><span>{String(title)}</span><ArrowUpRight size={14}/></button>;})}</div>
              <div className="honesty-note"><CircleHelp size={15}/>当前可使用真实模型与文件工具；搜索、受限代码及林业流程逐阶段验收。</div></>:
              <><div className="user-message"><span className="message-label">你的目标</span><p>{active.objective}</p><div className="task-meta">修订 {active.spec_revision} · {active.model_profile==='large'?'Qwen3.8 · 27B':'Qwen3.5 · 4B'} · {active.config_snapshot?.harness_mode||'H1'} · {active.config_snapshot?.context_classification==='public'?'公开上下文':'私密上下文，外发需审批'}</div></div>
                {active.messages.map(message=><div key={message.id} className={`message ${message.role}`}><span className="message-label">{message.role==='assistant'?'林遥 Agent':'你的补充'}</span><div className="message-text">{message.text}</div>
                  {message.limitations?.length?<div className="limitations">{message.limitations.map((l,i)=><p key={i}>{l}</p>)}</div>:null}</div>)}
                {live&&<div className="running"><div className="pulse-ring"/><div><strong>Agent 正在工作</strong><p>{tools.length?`${tools.filter(e=>e.type==='action.started').length} 次工具调用已登记`:'正在连接本地模型…'}</p></div></div>}
                {live&&liveText&&<pre className="stream-text">{liveText}</pre>}
                {active.blockers.map((b,i)=><div key={i} className="blocker"><CircleHelp size={16}/><span>{b.message}</span></div>)}
                {approvals.length>0&&<section className="network-approvals" aria-label="网络请求审批"><h3>网络请求审批</h3>
                  <p>核对将发送的完整载荷、目的地和来源版本。每次批准仅用于这一项请求；网页内容仍需核验。</p>
                  {approvals.map(record=><details key={record.approval_id} open={record.status==='pending'} className="network-approval">
                    <summary>{record.request.kind==='web.search'?'搜索请求':'网页读取'} · {approvalLabels[record.status]||record.status}</summary>
                    <dl><dt>完整载荷</dt><dd>{record.request.value}</dd><dt>实际 GET 地址</dt><dd>{record.request.url}</dd>
                      <dt>允许目的地</dt><dd>{record.request.destinations.join('\n')}</dd><dt>规范修订 / 到期</dt><dd>{record.spec_revision} / {new Date(record.expires_at).toLocaleString()}</dd>
                      <dt>请求 SHA256</dt><dd>{record.request_sha256}</dd><dt>来源版本</dt><dd>{record.request.sources.length?JSON.stringify(record.request.sources,null,2):'无本地输入来源'}</dd></dl>
                    <div className="approval-actions">{record.status==='pending'&&record.spec_revision===active.spec_revision&&<>
                      <button disabled={busy||Date.parse(record.expires_at)<=Date.now()} onClick={()=>void resolveApproval(record,'approve')}>批准这项请求</button>
                      <button disabled={busy||Date.parse(record.expires_at)<=Date.now()} onClick={()=>void resolveApproval(record,'reject')}>拒绝</button></>}
                      {['pending','approved','consumed'].includes(record.status)&&record.spec_revision===active.spec_revision&&<button disabled={busy} onClick={()=>void resolveApproval(record,'revoke')}>撤销授权</button>}</div>
                  </details>)}</section>}
                {active.status==='waiting_user'&&!approvals.some(a=>a.spec_revision===active.spec_revision&&['pending','approved'].includes(a.status))&&<button className="primary" onClick={()=>void guarded(async()=>{
                  setCandidate(await api(`/tasks/${active.task_id}/acceptance-candidate`));setAcceptComment('');
                })}><Check size={15}/>检查并验收结果</button>}
                {active.status==='completed'&&<div className="honesty-note"><ShieldCheck size={15}/>结果已经你确认，交付清单已封存，可在产物中下载。</div>}
                {active.last_usage&&<div className="usage">{active.last_usage.requests} 次模型请求 · {active.last_usage.input_tokens.toLocaleString()} 输入 token · {active.action_count} 次动作</div>}
              </>}
          </div>
          <div className="composer">
            {!active&&<label className="context-choice"><input type="checkbox" checked={publicTask} onChange={e=>{
              setPublicTask(e.target.checked); if(e.target.checked)setSelected(old=>old.filter(id=>resources.find(r=>r.resource_id===id)?.classification==='public'));
            }}/>这项任务的目标、补充消息和输入均为公开信息
              <span>{publicTask?'允许发送公开搜索词和网页地址；本任务不能读取私密来源、长期记忆或本机环境。':'默认按私密上下文处理。搜索词或网页地址发送前，逐项核对并批准。'}</span></label>}
            {!active&&<label className="harness-choice">工作方式<select aria-label="工作方式" value={harness} onChange={e=>setHarness(e.target.value)}>
              <option value="H2">H2 · 按需独立复核</option><option value="H1">H1 · 单个助手持续处理</option><option value="H0">H0 · 基础工具对照</option></select>
              <span>{harness==='H2'?'助手可将独立工作交给专门角色，返回结果后统一整理。':harness==='H1'?'同一个助手完成工作，保留上下文整理与结果读回。':'用于基础对照，关闭角色委派、知识检索与自动上下文整理。'}</span></label>}
            {resources.length>0&&<div className="source-chips">{resources.map(r=><button key={r.resource_id}
              className={(active?active.input_refs:selected).includes(r.resource_id)?'checked':''} disabled={!!active||(publicTask&&r.classification!=='public')}
              onClick={()=>setSelected(old=>old.includes(r.resource_id)?old.filter(id=>id!==r.resource_id):[...old,r.resource_id])}>
              <FolderOpen size={13}/>{r.name}{(active?active.input_refs:selected).includes(r.resource_id)&&<Check size={12}/>}</button>)}</div>}
            <textarea ref={input} value={text} onChange={e=>setText(e.target.value)} placeholder={active?'补充信息或提出后续问题…':'描述你的目标，例如：阅读这份数据，整理重点并生成报告…'}
              onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();void send();}}}/>
            <div className="composer-footer"><button className="source-button" onClick={()=>setShowSource(true)}><Plus size={16}/>添加本地数据</button><div><span className="key-hint">Ctrl + Enter 发送</span><button className="send-button" title="发送任务" disabled={!text.trim()||busy||live} onClick={()=>void send()}><Send size={16}/></button></div></div>
          </div>
        </section>
        <aside className="inspector"><section className="inspector-card">
          <div className="inspector-tabs"><button className={panel==='artifacts'?'active':''} onClick={()=>setPanel('artifacts')}>产物<span>{artifacts.length}</span></button><button className={panel==='trace'?'active':''} onClick={()=>setPanel('trace')}>执行记录</button><button className={panel==='roles'?'active':''} onClick={()=>setPanel('roles')}>委派<span>{assignments.length}</span></button></div>
          {panel==='artifacts'?<div className="artifact-list">{artifacts.length?artifacts.map(a=><div className="artifact-item" key={a.artifact_id}><div className="file-icon"><File size={20}/></div><div><button onClick={()=>void openPreview(a)}>{a.name}</button><span>{(a.size_bytes/1024).toFixed(1)} KB · 校验和已检查</span></div><a title="下载产物" href={a.download_url}><ArrowDownToLine size={15}/></a></div>):<div className="inspector-empty"><div><FolderOpen size={26}/></div><strong>结果会出现在这里</strong><p>真实生成的报告、代码与表格<br/>支持版本检查、预览和下载。</p></div>}</div>:
            panel==='trace'?<div className="trace-list">{tools.length?tools.map(e=><details key={e.event_id}><summary><span className={e.type==='action.observed'?'observed':'started'}/>{e.payload.tool}<small>{e.type==='action.observed'?'已返回':'调用'}</small></summary><pre>{JSON.stringify(e.payload.arguments||e.payload.result,null,2)}</pre></details>):<div className="inspector-empty"><Terminal size={26}/><p>工具调用后显示真实参数和结果</p></div>}</div>:
            <div className="assignment-list">{assignments.length?assignments.map(a=><details key={a.assignment_id} className="assignment-item"><summary><strong>{roleNames[a.role_id]||a.role_id}</strong><span>{assignmentLabels[a.status]||a.status}</span></summary>
              <p>{a.request.objective}</p><small>{a.model_requests} 次模型请求 · {a.actions} 次动作 · {a.effective_input_refs.length} 个授权输入</small>
              {a.blocker&&<p className="limitations">{a.blocker.message}</p>}{a.result&&<><p>{a.result.summary}</p>
                {a.result.checks.map((c,i)=><p className="role-check" key={i}>{c.status==='passed'?'通过':c.status==='failed'?'失败':'未知'} · {c.criterion}<br/>{c.observation}</p>)}
                {a.result.artifact_ids.map(id=>{const artifact=artifacts.find(f=>f.artifact_id===id);return artifact?<button key={id} onClick={()=>void openPreview(artifact)}><File size={13}/>{artifact.name}</button>:null;})}
                {a.result.limitations.map((l,i)=><p className="limitations" key={i}>{l}</p>)}</>}
              <p className="muted">独立结果供协调者检查与整合，任务仍须由你验收。</p></details>):<div className="inspector-empty"><ShieldCheck size={26}/><p>需要独立工作时才会委派。<br/>这里显示实际范围、状态和结果。</p></div>}</div>}
        </section>
        <section className="guide-card"><span className="guide-label">工作方式</span><h3>可检查，才可交付。</h3><p>源文件保持只读。所有操作先登记，产物保留版本。执行成功与回答目标分别验收。</p><div><ShieldCheck size={16}/><span>权限与状态由应用控制</span></div></section>
        {active&&<section className="control-card"><div><span className={`status-dot ${active.status}`}/><strong>{labels[active.status]}</strong></div><div className="task-controls">
          <button disabled={!live} onClick={()=>void control('pause')}><Pause size={14}/>暂停</button>
          <button disabled={active.status!=='paused'} onClick={()=>void control('resume')}><Play size={14}/>恢复</button>
          <button disabled={['cancelled','completed','failed','publishing'].includes(active.status)} onClick={()=>void control('cancel')}><Square size={13}/>取消</button></div></section>}
        </aside></div>
        {error&&<div className="error-banner" role="alert"><CircleHelp size={17}/>{error}<button onClick={()=>setError('')}><X size={16}/></button></div>}
        <footer><span><ShieldCheck size={13}/>真实执行 · 有来源的证据 · 明确的局限</span><span>ForestryUAV-Agent / Independent V1</span></footer>
      </main>
    </div>
    {showSource&&<div className="modal-backdrop"><div className="modal"><div className="modal-heading"><h2>登记本地数据源</h2><button onClick={()=>setShowSource(false)}><X size={20}/></button></div><p>输入这台电脑上的绝对文件或目录路径。此操作授予只读访问，不上传或复制整个目录。</p><label>本地路径<input value={path} onChange={e=>setPath(e.target.value)} placeholder="C:\\数据\\样地文件"/></label><label className="checkbox"><input type="checkbox" checked={privateSource} onChange={e=>setPrivateSource(e.target.checked)}/>私密数据，禁止未经授权外传</label><div className="modal-actions"><button onClick={()=>setShowSource(false)}>返回</button><button className="primary" disabled={!path.trim()} onClick={()=>void addSource()}>登记只读资源</button></div></div></div>}
    {showSettings&&<div className="modal-backdrop"><div className="modal settings-modal"><div className="modal-heading"><h2>模型与执行环境</h2><button onClick={()=>setShowSettings(false)}><X size={20}/></button></div><p>后续新任务使用选定模型，当前任务保留自己的配置快照。</p><label>默认本地模型<select value={settings?.default_model||'small'} onChange={e=>void guarded(async()=>{
      if(!settings)return; await api('/settings',{request_id:requestId(),expected_version:settings.version,default_model:e.target.value},'PUT');setSettings(await api('/settings'));})}>
      <option value="small">Qwen3.5 · 4B</option><option value="large">Qwen3.8 · 27B</option></select></label>
      <button className="probe-button" onClick={()=>void guarded(async()=>setDiagnostics(await api('/models/profiles')))}><RefreshCw size={15}/>重新探测环境</button>
      {diagnostics&&<div className="diagnostics">{Object.entries(diagnostics.models||{}).map(([key,value]:[string,any])=><div key={key}><span>{value.profile.served_name}</span><b>{value.status==='available_not_tool_verified'?'权重可用 · 协议待验收':value.status}</b></div>)}
        <div><span>受限代码执行</span><b>{diagnostics.capabilities?.restricted_code?.status==='available'?'容器服务可用':'容器服务不可用'}</b></div><div><span>公开搜索</span><b>{diagnostics.capabilities?.search?.status}</b></div><div><span>系统内存</span><b>{(diagnostics.ram_bytes/1024**3).toFixed(1)} GiB</b></div></div>}
      <section className="memory-panel"><h3>你保存的长期记忆</h3><p>仅手动保存的内容会供后续任务读取。停用后不再检索，既有任务及授权审计历史保留。</p>
        <label>希望长期记住的内容<input value={noteText} onChange={e=>setNoteText(e.target.value)} maxLength={8000}/></label>
        <button className="primary" disabled={!noteText.trim()} onClick={()=>void guarded(async()=>{
          await api('/memory',{request_id:requestId(),text:noteText,tags:[]});setNoteText('');
          setNotes((await api<{notes:Note[]}>('/memory')).notes);
        })}>明确保存这条记忆</button>
        <div className="memory-list">{notes.map(note=><div key={note.memory_id}><span>{note.text}</span><button title="停用这条记忆" onClick={()=>void guarded(async()=>{
          await api(`/memory/${note.memory_id}`,{request_id:requestId()},'DELETE');
          setNotes((await api<{notes:Note[]}>('/memory')).notes);
        })}><X size={16}/></button></div>)}</div>
      </section><div className="modal-info"><ShieldCheck size={16}/>只连接本机模型。没有自动云端回退。</div></div></div>}
    {preview&&<div className="modal-backdrop"><div className="modal preview-modal"><div className="modal-heading"><h2>{preview.name}</h2><button onClick={()=>setPreview(null)}><X size={20}/></button></div>
      {preview.media_type==='text/html'?<iframe title={preview.name} sandbox="" src={preview.preview_url}/>:preview.media_type.startsWith('image/')?<img alt={preview.name} src={preview.preview_url}/>:<pre>{previewText||'正在读取…'}</pre>}
      <div className="preview-footer"><span>SHA-256 · {preview.sha256.slice(0,20)}…</span><a href={preview.download_url}><ArrowDownToLine size={15}/>下载完整文件</a></div></div></div>}
    {candidate&&<div className="modal-backdrop"><div className="modal preview-modal"><div className="modal-heading"><h2>验收本次结果</h2><button onClick={()=>setCandidate(null)}><X size={20}/></button></div>
      <p>请确认回答和文件是否满足你的目标。文件完整性已检查；你的确认会记录为用户验收，不代表已验证科学精度。</p>
      <pre>{candidate.candidate.answer}</pre>
      {candidate.candidate.limitations.map((l,i)=><p key={i}>{l}</p>)}
      <div className="artifact-list">{candidate.candidate.outputs.map(a=><div className="artifact-item" key={a.artifact_id}><File size={18}/><span>{a.name}</span><a href={`/artifacts/${a.artifact_id}/content?download=true`}><ArrowDownToLine size={16}/></a></div>)}</div>
      <label>验收意见或需要修改的内容<input value={acceptComment} onChange={e=>setAcceptComment(e.target.value)}/></label>
      <div className="modal-actions"><button disabled={busy} onClick={()=>void accept('request_changes')}>要求修改</button><button className="primary" disabled={busy} onClick={()=>void accept('accept')}>确认满足目标并封存</button></div>
    </div></div>}
  </div>;
}
