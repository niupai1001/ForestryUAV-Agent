import { useEffect, useRef, useState } from 'react';
import MemoryPanel from './MemoryPanel';
import KnowledgePanel from './KnowledgePanel';
import JobsPanel from './JobsPanel';
import LidarPanel from './LidarPanel';
import SourceBrowser from './SourceBrowser';
import { api } from './api';
import { ArrowDownToLine, ArrowUpRight, Check, ChevronDown, CircleHelp, File, FolderOpen,
  Leaf, MessageSquare, Pause, Play, Plus, RefreshCw, Send, Settings,
  ShieldCheck, Square, Terminal, X } from 'lucide-react';

type Task = {task_id: string; objective: string; status: string; state_version: number;
  spec_revision: number; model_profile: string; input_refs: string[]; artifact_ids: string[];
  requested_method_id?:string|null; constraints?:{allowed_capabilities?:string[]|null}; cancellation_requested?:boolean;
  config_snapshot?: {harness_mode?: string;context_classification?:string;science_image?:string};
  messages: {id: string; role: string; text: string; limitations?: string[]}[];
  blockers: {code: string; message: string}[]; action_count: number;
  last_usage?: {requests: number; input_tokens: number; output_tokens: number}};
type Resource = {resource_id: string; name: string; locator: string; kind: string; classification: string};
type Artifact = {artifact_id: string; name: string; size_bytes: number; media_type: string;
  preview_url: string; download_url: string; sha256: string};
type Event = {sequence: number; event_id: string; type: string; payload: Record<string, any>; at: string};
type Candidate = {candidate_sha256:string; state_version:number; candidate:{spec_revision:number; answer:string;
  limitations:string[]; outputs:{artifact_id:string;name:string;sha256:string}[]}};
type Approval = {approval_id:string;spec_revision:number;request_sha256:string;status:string;expires_at:string;
  request:{kind:string;value:string;url:string;metadata_urls?:string[];max_bytes?:number;seconds?:number;operation?:string;max_projects?:number;max_requests?:number;
    destinations:string[];sources:{resource_id:string;version:string}[]}};
const approvalLabels:Record<string,string> = {pending:'待审批',approved:'已批准，待执行',consumed:'已使用',rejected:'已拒绝',revoked:'已撤销',expired:'已过期'};
type Assignment = {assignment_id:string;role_id:string;status:string;model_requests:number;actions:number;
  effective_input_refs:string[];effective_artifact_refs:string[];request:{objective:string};
  result?:{summary:string;artifact_ids:string[];limitations:string[];checks:{criterion:string;status:string;observation:string}[]} | null;
  blocker?:{message:string}|null};
type Plan = {task_id:string;spec_revision:number;turn_id:string|null;authority:string;acceptance:string;
  items:{id:string;content:string;status:string;active_form:string}[]};
const planLabels:Record<string,string> = {pending:'待处理',in_progress:'进行中',completed:'助手标记完成',cancelled:'已取消'};
const roleNames:Record<string,string> = {environment:'环境检查',data:'数据准备',code_experiment:'代码实验',quality:'质量复核'};
const assignmentLabels:Record<string,string> = {queued:'等待开始',running:'运行中',waiting:'等待恢复',succeeded:'已返回',partial:'部分结果',failed:'失败',cancelled:'已取消'};
const labels: Record<string,string> = {created:'等待运行', active:'运行中', paused:'已暂停',
  completed:'已完成', failed:'失败', cancelled:'已取消', waiting_user:'待确认', waiting_job:'处理中', publishing:'正在封存'};
const requestId = () => crypto.randomUUID();
const capabilityLabels:Record<string,string> = {'fs.inputs':'查看任务输入','fs.list':'列出源文件','fs.read':'读取源文件',
  'fs.write_workspace':'写入产物','artifact.inspect':'检查产物','artifact.read':'读回产物','action.inspect':'查看动作证据',
  'document.read':'阅读文档','knowledge.index':'建立知识索引','knowledge.search':'检索知识','memory.read':'读取长期记忆',
  'environment.inspect':'检查本机环境','environment.prepare':'准备隔离依赖','environment.acquire':'获取公开依赖','code.execute':'执行受限代码','job.observe':'观察作业','role.delegate':'委派角色',
  'task.plan':'跟踪任务计划'};
const fileCapabilities = ['fs.inputs','fs.list','fs.read','fs.write_workspace','artifact.inspect','artifact.read',
  'action.inspect','document.read','knowledge.index','knowledge.search'];
type Scope = 'full'|'files'|'answer'|'custom';
const scopeCapabilities = (scope:Scope,custom:string[]) => scope==='full'?null:scope==='files'?fileCapabilities:scope==='answer'?[]:custom;
const scopeName = (caps?:string[]|null) => caps==null?'通用能力':caps.length===0?'仅回答':
  caps.length===fileCapabilities.length&&caps.every(c=>fileCapabilities.includes(c))?'文件与报告':`自定义 ${caps.length} 项`;

function taskPreferences():{harness:string;scope:Scope;caps:string[]} {
  const defaults = {harness:'H2',scope:'full' as Scope,caps:[] as string[]};
  try {
    const saved=JSON.parse(localStorage.getItem('forestry.task-preferences')||'null');
    if(!saved||!['H0','H1','H2'].includes(saved.harness)||!['full','files','answer','custom'].includes(saved.scope)
      ||!Array.isArray(saved.caps)||!saved.caps.every((cap:unknown)=>typeof cap==='string'&&cap in capabilityLabels))return defaults;
    return {...saved,caps:[...new Set<string>(saved.caps)]};
  }catch{return defaults;}
}

async function readPlan(taskId:string):Promise<Plan|null> {
  const reply=await fetch(`/tasks/${taskId}/plan`);
  if(reply.status===404)return null;
  const data=await reply.json();
  if(!reply.ok)throw new Error(data.error?.message||'计划读取失败');
  return data;
}

export default function App() {
  const [tasks,setTasks] = useState<Task[]>([]), [active,setActive] = useState<Task|null>(null);
  const [resources,setResources] = useState<Resource[]>([]), [selected,setSelected] = useState<string[]>([]);
  const [artifacts,setArtifacts] = useState<Artifact[]>([]), [events,setEvents] = useState<Event[]>([]);
  const [assignments,setAssignments] = useState<Assignment[]>([]), [harness,setHarness] = useState(()=>taskPreferences().harness);
  const [plan,setPlan] = useState<Plan|null>(null);
  const [approvals,setApprovals] = useState<Approval[]>([]);
  const [text,setText] = useState(''), [error,setError] = useState(''), [busy,setBusy] = useState(false);
  const [settings,setSettings] = useState<{version:number; default_model:string; models:Record<string,any>}|null>(null);
  const [showSettings,setShowSettings] = useState(false), [showSource,setShowSource] = useState(false);
  const [showKnowledge,setShowKnowledge] = useState(false), [showNavigation,setShowNavigation] = useState(false);
  const [scope,setScope] = useState<Scope>(()=>taskPreferences().scope), [customCaps,setCustomCaps] = useState<string[]>(()=>taskPreferences().caps);
  const [currentScopeCaps,setCurrentScopeCaps] = useState<string[]|null>(null);
  const [amending,setAmending] = useState<Task|null>(null), [amendObjective,setAmendObjective] = useState('');
  const [amendInputs,setAmendInputs] = useState<string[]>([]), [amendCaps,setAmendCaps] = useState<string[]|null>(null);
  const [amendAvailableInputs,setAmendAvailableInputs] = useState<string[]>([]);
  const [path,setPath] = useState('');
  const [diagnostics,setDiagnostics] = useState<any>(null), [panel,setPanel] = useState<'sources'|'artifacts'|'trace'|'roles'|'plan'|'jobs'|'lidar'>('artifacts');
  const [preview,setPreview] = useState<Artifact|null>(null), [previewText,setPreviewText] = useState('');
  const [candidate,setCandidate] = useState<Candidate|null>(null), [acceptComment,setAcceptComment] = useState('');
  const [showDiagnostics,setShowDiagnostics] = useState(false);
  const uploadInput=useRef<HTMLInputElement>(null), draftId=useRef(requestId());
  const currentSession=useRef('');
  const sessionKey=active?.task_id||draftId.current;
  currentSession.current=sessionKey;
  const seen = useRef(new Set<string>()), input = useRef<HTMLTextAreaElement>(null);

  const guarded = async (work:()=>Promise<void>) => {setError(''); try {await work();} catch(e) {setError(String((e as Error).message));}};
  const sessionResources = resources.filter(resource=>(active?active.input_refs:selected).includes(resource.resource_id));
  const amendmentResources = resources.filter(resource=>amendAvailableInputs.includes(resource.resource_id));
  const editable = !active||!['failed','cancelled','publishing'].includes(active.status);
  useEffect(()=>{localStorage.setItem('forestry.task-preferences',JSON.stringify({harness,scope,caps:customCaps}));},[harness,scope,customCaps]);
  const openSettings = () => {setCurrentScopeCaps(active?.constraints?.allowed_capabilities??null);setShowSettings(true);};
  const refresh = async () => {setTasks(await api<Task[]>('/tasks'));};
  useEffect(()=>{void guarded(async()=>{await api('/session'); await refresh();
    setSettings(await api('/settings'));
    const saved = localStorage.getItem('forestry.task');
    if(saved) try {setActive(await api<Task>(`/tasks/${saved}`));} catch {localStorage.removeItem('forestry.task');}
  });},[]);
  useEffect(()=>{
    if(!active) {setArtifacts([]); setEvents([]); setAssignments([]); setApprovals([]); setPlan(null); return;}
    localStorage.setItem('forestry.task',active.task_id);
    seen.current = new Set(); setEvents([]);
    setPlan(null);
    const id = active.task_id;
    let current=true, loading=false, reloadPending=false;
    const loadDetails=async()=>{
      if(loading){reloadPending=true;return;}
      loading=true;
      try{
        do{
          reloadPending=false;
          const [state,files,roles,pending,currentPlan,sources]=await Promise.all([
            api<Task>(`/tasks/${id}`),api<Artifact[]>(`/tasks/${id}/artifacts`),
            api<Assignment[]>(`/tasks/${id}/assignments`),api<Approval[]>(`/tasks/${id}/approvals`),readPlan(id),api<Resource[]>(`/tasks/${id}/sources`)]);
          if(!current)return;
          setActive(state);setResources(sources);setArtifacts(files);setAssignments(roles);setApprovals(pending);setPlan(currentPlan);await refresh();
        }while(current&&reloadPending);
      }finally{loading=false;}
    };
    const source = new EventSource(`/tasks/${id}/events`);
    source.onmessage = message => {
      if(!current)return;
      const event:Event = JSON.parse(message.data);
      if(seen.current.has(event.event_id)) return;
      seen.current.add(event.event_id); setEvents(previous => [...previous,event].slice(-500));
      if(event.type !== 'model.text') void guarded(loadDetails);
    };
    void guarded(loadDetails);
    return ()=>{current=false;source.close();};
  },[active?.task_id]);
  const selectTask = (task:Task|null) => {if(busy)return;setArtifacts([]);setAssignments([]);setEvents([]);setApprovals([]);setPlan(null);setResources([]);setActive(task);};
  const newTask = () => {if(busy)return;selectTask(null);draftId.current=requestId();setSelected([]); setText(''); setPanel('artifacts'); localStorage.removeItem('forestry.task'); input.current?.focus();};
  const send = async () => {
    if(!text.trim() || busy) return;
    setBusy(true);
    await guarded(async()=>{
      let result:Task;
      if(active) result = await api(`/tasks/${active.task_id}/messages`,{request_id:requestId(),
        expected_spec_revision:active.spec_revision,text});
      else result = await api('/tasks',{request_id:requestId(),objective:text,input_refs:selected,
        harness_mode:harness,context_classification:'private',requested_metrics:[],requested_method_id:null,
        constraints:scope==='full'?{}:{allowed_capabilities:scopeCapabilities(scope,customCaps)},output_preferences:{report_language:'zh'}});
      setActive(await api<Task>(`/tasks/${result.task_id}`)); setText(''); await refresh();
    }); setBusy(false);
  };
  const control = async (operation:string) => {
    if(!active) return;
    await guarded(async()=>{const current = await api<Task>(`/tasks/${active.task_id}`);
      const result = await api<Task>(`/tasks/${active.task_id}/${operation}`,{request_id:requestId(),expected_state_version:current.state_version});
      setActive({...current,...result}); await refresh();});
  };
  const openAmendment = () => {
    if(!active)return;
    setAmending(active);setAmendObjective(active.objective);setAmendInputs([...active.input_refs]);
    setAmendAvailableInputs([...active.input_refs]);
    setAmendCaps(active.constraints?.allowed_capabilities??null);
  };
  const saveAmendment = async () => {
    if(!amending||busy)return;
    setBusy(true);await guarded(async()=>{
      await api(`/tasks/${amending.task_id}/amendments`,{request_id:requestId(),expected_spec_revision:amending.spec_revision,
        objective:amendObjective,input_refs:amendInputs,requested_method_id:amending.requested_method_id??null,
        constraints:amendCaps===null?{}:{allowed_capabilities:amendCaps}});
      setActive(await api<Task>(`/tasks/${amending.task_id}`));setAmending(null);await refresh();
    });setBusy(false);
  };
  const addSource = async () => {
    if(busy)return;
    setBusy(true);
    await guarded(async()=>{
      const source = await api<Resource>('/resources/register',{request_id:requestId(),path,
        classification:'private',read_only:true});
      await selectSourceFile(source); setPath('');
    });setBusy(false);
  };
  const selectSourceFile = async (source:Resource) => {
    if(currentSession.current!==sessionKey)return;
    if(active) {
      const current=await api<Task>(`/tasks/${active.task_id}`);
      await api(`/tasks/${active.task_id}/sources`,{request_id:requestId(),expected_spec_revision:current.spec_revision,add_refs:[source.resource_id]});
      const [state,sources]=await Promise.all([api<Task>(`/tasks/${active.task_id}`),api<Resource[]>(`/tasks/${active.task_id}/sources`)]);
      if(currentSession.current!==sessionKey)return;
      setActive(state);setResources(sources);
    }else {
      setResources(old=>[...old.filter(r=>r.resource_id!==source.resource_id),source]);
      setSelected(old=>[...new Set([...old,source.resource_id])]);
    }
    setShowSource(false);
  };
  const removeSource = async (id:string) => {
    if(busy)return;
    setBusy(true);
    try {
    if(active) {
      const current=await api<Task>(`/tasks/${active.task_id}`);
      await api(`/tasks/${active.task_id}/sources`,{request_id:requestId(),expected_spec_revision:current.spec_revision,remove_refs:[id]});
      setActive(await api<Task>(`/tasks/${active.task_id}`));
      setResources(await api<Resource[]>(`/tasks/${active.task_id}/sources`));
    }else {setSelected(old=>old.filter(r=>r!==id));setResources(old=>old.filter(r=>r.resource_id!==id));}
    }finally{setBusy(false);}
  };
  const pickLocal = async (directory:boolean) => {
    if(busy||!editable)return;
    setBusy(true);
    await guarded(async()=>{
      const files=await api<Resource[]>('/resources/pick',{request_id:requestId(),directory});
      for(const file of files)await selectSourceFile(file);
    });
    setBusy(false);
  };
  const uploadFiles = async (files:FileList|File[]) => {
    if(busy||!editable)return;
    setBusy(true);
    const uploadDraft=draftId.current;
    await guarded(async()=>{
      for(const file of Array.from(files)) {
        if(currentSession.current!==sessionKey)return;
        if(file.size>128*1024**2)throw new Error('文件超过128 MiB，请通过“添加文件”中的本地路径引用添加。');
        const params=new URLSearchParams({filename:file.name,draft_id:uploadDraft,request_id:requestId()});
        const response=await fetch(`/uploads?${params}`,{method:'POST',body:file});
        const result=await response.json();
        if(!response.ok)throw new Error(result.error?.message||'文件上传失败');
        await selectSourceFile(result);
      }
    });
    setBusy(false);if(uploadInput.current)uploadInput.current.value='';
  };
  const openPreview = async (artifact:Artifact) => {
    setPreview(artifact); setPreviewText('');
    if(!artifact.media_type.startsWith('image/') && artifact.media_type !== 'text/html') {
      await guarded(async()=>{const r = await fetch(artifact.preview_url); if(!r.ok) throw new Error('产物读取失败');
        setPreviewText((await r.text()).slice(0,100000));});
    }
  };
  const saveCurrentScope = async () => {
    if(!active||!editable||active.status==='completed'||busy)return;
    setBusy(true);await guarded(async()=>{
      await api(`/tasks/${active.task_id}/amendments`,{request_id:requestId(),expected_spec_revision:active.spec_revision,
        objective:active.objective,input_refs:active.input_refs,requested_method_id:active.requested_method_id??null,
        constraints:currentScopeCaps===null?{}:{allowed_capabilities:currentScopeCaps}});
      setActive(await api<Task>(`/tasks/${active.task_id}`));setShowSettings(false);await refresh();
    });setBusy(false);
  };
  const live = active?.status === 'active' || active?.status === 'created';
  const cancelling = !!active?.cancellation_requested && active.status !== 'cancelled';
  const currentStatus = cancelling?'正在取消':active?.status==='waiting_user'&&active.blockers.length===0?'已回复':labels[active?.status||''];
  const directoryInputNote = '目录以只读引用添加，不复制整个目录。可浏览目录并选择需要处理的文件。';
  const selectedDirectory = resources.some(r=>r.kind==='directory'&&(active?active.input_refs:selected).includes(r.resource_id));
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
    setBusy(true); await guarded(async()=>{setActive({...active,...await api<Task>(`/tasks/${active.task_id}/acceptance`,{
      request_id:requestId(),expected_state_version:candidate.state_version,
      expected_spec_revision:candidate.candidate.spec_revision,candidate_sha256:candidate.candidate_sha256,
      verdict,comment:acceptComment})}); setCandidate(null); await refresh();}); setBusy(false);
  };
  const tools = events.filter(e=>e.type === 'action.started' || e.type === 'action.observed');
  const liveText = events.filter(e=>e.type === 'model.text' && !e.payload.assignment_id).map(e=>e.payload.text).join('');

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon"><Leaf size={23}/></div><div><strong>林遥</strong><span>ForestryUAV Agent</span></div><span className="version">V1</span></div>
      <button className="new-task" disabled={busy} onClick={newTask}><Plus size={18}/>新建任务</button>
      <div className="section-caption">工作空间</div>
      <button className="nav-item selected" onClick={()=>input.current?.focus()}><MessageSquare size={18}/>Agent 工作台</button>
      <button className="nav-item" onClick={()=>setPanel('sources')}><FolderOpen size={18}/>会话数据源<span className="small-count">{sessionResources.length}</span></button>
      <button className="nav-item" onClick={()=>setShowKnowledge(true)}><File size={18}/>本地知识</button>
      <div className="section-caption history-title">最近任务<button title="刷新任务" onClick={()=>void guarded(refresh)}><RefreshCw size={13}/></button></div>
      <div className="history">{tasks.length===0?<p className="muted empty-small">任务会保存在这台电脑上</p>:tasks.map(task=><button key={task.task_id}
        disabled={busy} className={`history-item ${active?.task_id===task.task_id?'current':''}`} onClick={()=>selectTask(task)}>
        <span className={`status-dot ${task.status}`}/><span>{task.objective}</span></button>)}</div>
      <div className="sidebar-bottom"><button className="nav-item" onClick={openSettings}><Settings size={18}/>设置</button></div>
    </aside>
    <div className="main-shell">
      <header><div className="breadcrumb">工作空间<span>/</span><strong>Agent 工作台</strong></div>
        <div className="header-right"><button className="compact-navigation" aria-label="打开任务与资源" onClick={()=>setShowNavigation(true)}><FolderOpen size={17}/>任务与资源</button><button title="环境设置" onClick={openSettings}><Settings size={17}/></button></div></header>
      <main>
        <div className="workspace-heading"><div><h1>从一个目标，开始工作。</h1><p>让 Agent 查阅文件、整理证据并交付可检查的结果。</p></div></div>
        <div className="workspace-grid"><section className="conversation-card">
          <div className="card-heading"><div className="heading-icon"><MessageSquare size={17}/></div><strong>{active?'当前任务':'新的任务'}</strong>
            <span className={`task-status ${active?.status||''}`}>{active?currentStatus:'准备开始'}</span></div>
          <div className="conversation-content">
            {!active?<><div className="welcome-emblem"><Leaf size={32}/></div><h2>你希望完成什么？</h2><p className="welcome-description">描述目标，上传或拖入需要处理的文件。</p>
              <div className="suggestions">{[
                ['整理文件与证据','阅读已选文件，提取重点，并保存一份有来源说明的中文 Markdown 报告。',FolderOpen],
                ['检查数据内容','读取已选 CSV 文件，检查字段和缺失值，保存检查结果，不要编造未读取的数据。',File],
                ['编写处理脚本','阅读已选数据文件，编写可复现的数据处理 Python 脚本；执行后保存脚本与结果，无法完成时说明原因。',Terminal]
              ].map(([title,prompt,Icon])=>{const Symbol=Icon as typeof File; return <button key={String(title)} onClick={()=>{setText(String(prompt));input.current?.focus();}}><Symbol size={17}/><span>{String(title)}</span><ArrowUpRight size={14}/></button>;})}</div>
              </>:
              <><div className="user-message"><span className="message-label">你的目标</span><p>{active.objective}</p>
                {editable&&active.status!=='completed'&&showDiagnostics&&<button className="scope-edit" disabled={busy} onClick={openAmendment}>修改目标与输入</button>}</div>
                {active.messages.map(message=><div key={message.id} className={`message ${message.role}`}><span className="message-label">{message.role==='assistant'?'林遥 Agent':'你的补充'}</span><div className="message-text">{message.text}</div>
                  {message.limitations?.length?<div className="limitations">{message.limitations.map((l,i)=><p key={i}>{l}</p>)}</div>:null}</div>)}
                {live&&<div className="running"><div className="pulse-ring"/><div><strong>Agent 正在工作</strong><p>{tools.length?'正在处理文件与生成结果…':'正在连接本地模型…'}</p></div></div>}
                {live&&liveText&&<pre className="stream-text">{liveText}</pre>}
                {active.blockers.filter(b=>b.code!=='ACCEPTANCE_REQUIRED').map((b,i)=><div key={i} className="blocker"><CircleHelp size={16}/><span>{b.message}</span></div>)}
                {approvals.some(a=>a.request.kind==='environment.acquire'&&['pending','approved'].includes(a.status))&&<section className="network-approvals" aria-label="网络请求审批"><h3>网络请求审批</h3>
                  <p>核对完整载荷、目的地和来源版本，每次批准仅用于这一项请求。依赖下载成功仍须经过容器安装与结果检查。</p>
                  {approvals.filter(a=>a.request.kind==='environment.acquire'&&['pending','approved'].includes(a.status)).map(record=><details key={record.approval_id} open={record.status==='pending'} className="network-approval">
                    <summary>{record.request.operation==='resolve'?'依赖信息获取':'依赖文件获取'} · {approvalLabels[record.status]||record.status}</summary>
                    <dl><dt>完整载荷</dt><dd>{record.request.value}</dd><dt>实际 GET 地址</dt><dd>{record.request.url}</dd>
                      {record.request.metadata_urls&&<><dt>全部官方元数据地址</dt><dd>{record.request.metadata_urls.join('\n')}</dd>
                        <dt>下载总量 / 总时限</dt><dd>{(record.request.max_bytes??0)/1024**2} MiB / {record.request.seconds} 秒</dd></>}
                      {record.request.operation==='resolve'&&<><dt>解析范围</dt><dd>包含传递依赖，仅获取元数据；最多 {record.request.max_projects} 个项目 / {record.request.max_requests} 次请求 / {(record.request.max_bytes??0)/1024**2} MiB / {record.request.seconds} 秒。精确 wheel 下载另行审批。</dd></>}
                      <dt>允许目的地</dt><dd>{record.request.destinations.join('\n')}</dd><dt>规范修订 / 到期</dt><dd>{record.spec_revision} / {new Date(record.expires_at).toLocaleString()}</dd>
                      <dt>请求 SHA256</dt><dd>{record.request_sha256}</dd><dt>来源版本</dt><dd>{record.request.sources.length?JSON.stringify(record.request.sources,null,2):'无本地输入来源'}</dd></dl>
                    <div className="approval-actions">{record.status==='pending'&&record.spec_revision===active.spec_revision&&<>
                      <button disabled={busy||Date.parse(record.expires_at)<=Date.now()} onClick={()=>void resolveApproval(record,'approve')}>批准这项请求</button>
                      <button disabled={busy||Date.parse(record.expires_at)<=Date.now()} onClick={()=>void resolveApproval(record,'reject')}>拒绝</button></>}
                      {['pending','approved','consumed'].includes(record.status)&&record.spec_revision===active.spec_revision&&<button disabled={busy} onClick={()=>void resolveApproval(record,'revoke')}>撤销授权</button>}</div>
                  </details>)}</section>}
                {showDiagnostics&&active.last_usage&&<div className="usage">{active.last_usage.requests} 次模型请求 · {active.last_usage.input_tokens.toLocaleString()} 输入 token · {active.action_count} 次动作</div>}
              </>}
          </div>
          <div className="composer" onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();void uploadFiles(event.dataTransfer.files);}}>
            {sessionResources.length>0&&<div className="source-chips" aria-label="本会话输入">{sessionResources.map(r=><button key={r.resource_id}
              disabled={!editable||active?.status==='completed'||busy} aria-label={`移除 ${r.name}`} onClick={()=>void guarded(()=>removeSource(r.resource_id))}>
              <File size={13}/>{r.name}{r.kind==='directory'?'（目录）':''}<X size={12}/></button>)}</div>}
            {selectedDirectory&&<p className="queue-note" role="note">{directoryInputNote}</p>}
            <textarea ref={input} value={text} onChange={e=>setText(e.target.value)} placeholder={active?'补充信息或提出后续问题…':'描述你的目标，例如：阅读这份数据，整理重点并生成报告…'}
              onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();void send();}}}/>
            {live&&<p className="queue-note">补充消息会依次处理。</p>}
            <div className="composer-footer"><button className="source-button" onClick={()=>setShowSource(true)}><Plus size={16}/>添加文件</button><input ref={uploadInput} type="file" multiple hidden aria-label="上传文件" onChange={event=>{if(event.target.files)void uploadFiles(event.target.files);}}/><div><span className="key-hint">Ctrl + Enter 发送</span><button className="send-button" title="发送任务" disabled={!text.trim()||busy||active?.cancellation_requested||['failed','cancelled','publishing'].includes(active?.status||'')} onClick={()=>void send()}><Send size={16}/></button></div></div>
          </div>
        </section>
        <aside className="inspector"><section className="inspector-card">
          <div className="inspector-tabs">{active?.config_snapshot?.science_image&&<button className={panel==='lidar'?'active':''} onClick={()=>setPanel('lidar')}>林业结果</button>}<button className={panel==='sources'?'active':''} onClick={()=>setPanel('sources')}>数据源<span>{sessionResources.length}</span></button><button className={panel==='artifacts'?'active':''} onClick={()=>setPanel('artifacts')}>产物<span>{artifacts.length}</span></button>{showDiagnostics&&<><button className={panel==='trace'?'active':''} onClick={()=>setPanel('trace')}>执行记录</button><button className={panel==='jobs'?'active':''} onClick={()=>setPanel('jobs')}>作业</button><button className={panel==='roles'?'active':''} onClick={()=>setPanel('roles')}>委派<span>{assignments.length}</span></button></>}<button className={panel==='plan'?'active':''} onClick={()=>setPanel('plan')}>进度</button></div>
          {panel==='sources'?<section className="session-source-list" aria-label="会话数据源列表"><p>{active?'此会话选择的只读输入。':'发送目标后，这些输入将保存到新会话。'}</p>
            {sessionResources.length?sessionResources.map(resource=><article key={resource.resource_id}><strong><FolderOpen size={16}/>{resource.name}{resource.kind==='directory'?'（目录）':''}</strong><small>只读引用</small><p>{resource.locator}</p>{editable&&<button disabled={busy} onClick={()=>void guarded(()=>removeSource(resource.resource_id))}>从本会话移除</button>}</article>):<div className="inspector-empty"><FolderOpen size={26}/><strong>此会话还没有数据源</strong><p>添加需要处理的文件或目录。</p></div>}
            <button className="primary" onClick={()=>setShowSource(true)}>{editable?'添加或浏览数据源':'浏览会话数据源'}</button>
          </section>:panel==='artifacts'?<div className="artifact-list">{active?.status==='waiting_user'&&active.messages.some(m=>m.role==='assistant')&&artifacts.length>0&&!approvals.some(a=>['pending','approved'].includes(a.status)&&a.spec_revision===active.spec_revision)&&<button onClick={()=>void guarded(async()=>{setCandidate(await api(`/tasks/${active.task_id}/acceptance-candidate`));setAcceptComment('');})}>保存交付快照</button>}{artifacts.length?artifacts.map(a=><div className="artifact-item" key={a.artifact_id}><div className="file-icon"><File size={20}/></div><div><button onClick={()=>void openPreview(a)}>{a.name}</button><span>{(a.size_bytes/1024).toFixed(1)} KB</span></div><a title="下载产物" href={a.download_url}><ArrowDownToLine size={15}/></a></div>):<div className="inspector-empty"><div><FolderOpen size={26}/></div><strong>结果会出现在这里</strong><p>真实生成的报告、代码与表格<br/>支持预览和下载。</p></div>}</div>:
            panel==='trace'?<div className="trace-list">{tools.length?tools.map(e=><details key={e.event_id}><summary><span className={e.type==='action.observed'?'observed':'started'}/>{e.payload.tool}<small>{e.type==='action.observed'?'已返回':'调用'}</small></summary><pre>{JSON.stringify(e.payload.arguments||e.payload.result,null,2)}</pre></details>):<div className="inspector-empty"><Terminal size={26}/><p>工具调用后显示真实参数和结果</p></div>}</div>:
            panel==='lidar'?<LidarPanel taskId={active?.task_id||''} artifacts={artifacts} onError={setError}/>:panel==='jobs'?<JobsPanel key={active?.task_id||'empty'} taskId={active?.task_id||''} refreshKey={events.filter(e=>e.type.startsWith('job.')).slice(-1)[0]?.event_id||''} onError={setError}/>:panel==='plan'?<div className="plan-list"><p className="muted">助手的工作安排与自报进度；完成状态仍须实际检查和验收。</p>{plan?.items.length?<ol>{plan.items.map(item=><li key={item.id} data-status={item.status}><strong>{item.content}</strong><span>{planLabels[item.status]||item.status}</span></li>)}</ol>:<p className="empty-small">当前回合尚未保存计划。</p>}</div>:
            <div className="assignment-list">{assignments.length?assignments.map(a=><details key={a.assignment_id} className="assignment-item"><summary><strong>{roleNames[a.role_id]||a.role_id}</strong><span>{assignmentLabels[a.status]||a.status}</span></summary>
              <p>{a.request.objective}</p><small>{a.model_requests} 次模型请求 · {a.actions} 次动作 · {a.effective_input_refs.length} 个授权输入</small>
              {a.blocker&&<p className="limitations">{a.blocker.message}</p>}{a.result&&<><p>{a.result.summary}</p>
                {a.result.checks.map((c,i)=><p className="role-check" key={i}>{c.status==='passed'?'通过':c.status==='failed'?'失败':'未知'} · {c.criterion}<br/>{c.observation}</p>)}
                {a.result.artifact_ids.map(id=>{const artifact=artifacts.find(f=>f.artifact_id===id);return artifact?<button key={id} onClick={()=>void openPreview(artifact)}><File size={13}/>{artifact.name}</button>:null;})}
                {a.result.limitations.map((l,i)=><p className="limitations" key={i}>{l}</p>)}</>}
              <p className="muted">独立结果供协调者检查与整合，任务仍须由你验收。</p></details>):<div className="inspector-empty"><ShieldCheck size={26}/><p>需要独立工作时才会委派。<br/>这里显示实际范围、状态和结果。</p></div>}</div>}
        </section>
        {active&&<section className="control-card"><div><span className={`status-dot ${active.status}`}/><strong>{currentStatus}</strong></div><div className="task-controls">
          <button disabled={!live||cancelling} onClick={()=>void control('pause')}><Pause size={14}/>暂停</button>
          <button disabled={active.status!=='paused'||!!active.cancellation_requested} onClick={()=>void control('resume')}><Play size={14}/>恢复</button>
          <button disabled={!!active.cancellation_requested||['cancelled','completed','failed','publishing'].includes(active.status)} onClick={()=>void control('cancel')}><Square size={13}/>取消</button></div>
          {cancelling&&<p className="control-policy">{cancelling?'已请求取消，正在确认所有作业停止；任务不会继续执行。':'暂停停止新操作，已启动的作业继续运行。取消会停止作业，确认后才显示“已取消”。修改目标后，旧结果仅作历史保留。'}</p>}</section>}
        </aside></div>
        {error&&<div className="error-banner" role="alert"><CircleHelp size={17}/>{error}<button onClick={()=>setError('')}><X size={16}/></button></div>}

      </main>
    </div>
    {showNavigation&&<div className="modal-backdrop"><nav className="modal compact-navigation-panel" role="dialog" aria-label="任务与资源">
      <div className="modal-heading"><h2>任务与资源</h2><button aria-label="关闭任务与资源" onClick={()=>setShowNavigation(false)}><X size={20}/></button></div>
      <button className="new-task" disabled={busy} onClick={()=>{newTask();setShowNavigation(false);}}><Plus size={18}/>新建任务</button>
      <button className="nav-item" onClick={()=>{setShowNavigation(false);setPanel('sources');}}><FolderOpen size={18}/>会话数据源<span className="small-count">{sessionResources.length}</span></button>
      <button className="nav-item" onClick={()=>{setShowNavigation(false);setShowKnowledge(true);}}><File size={18}/>本地知识</button>
      <div className="section-caption history-title">最近任务<button aria-label="刷新最近任务" onClick={()=>void guarded(refresh)}><RefreshCw size={13}/></button></div>
      <div className="history">{tasks.length===0?<p className="muted">任务会保存在这台电脑上</p>:tasks.map(task=><button key={task.task_id}
        disabled={busy} className={`history-item ${active?.task_id===task.task_id?'current':''}`} onClick={()=>{selectTask(task);setShowNavigation(false);}}>
        <span className={`status-dot ${task.status}`}/><span>{task.objective}</span></button>)}</div>
    </nav></div>}
    {amending&&<div className="modal-backdrop"><div className="modal scope-modal" role="dialog" aria-label="修改任务规范">
      <div className="modal-heading"><h2>修改任务规范</h2><button disabled={busy} onClick={()=>setAmending(null)}><X size={20}/></button></div>
      <p>保存为修订 {amending.spec_revision+1}，暂停当前执行并保留历史。核对已登记动作后，使用“恢复”继续。补充消息不会修改工具范围。</p>
      <label>任务目标<textarea aria-label="修订任务目标" value={amendObjective} onChange={e=>setAmendObjective(e.target.value)} maxLength={32000}/></label>
      <fieldset><legend>本次授权输入</legend><div className="capability-grid">{amendmentResources.map(r=><label key={r.resource_id}><input type="checkbox" checked={amendInputs.includes(r.resource_id)} disabled={amending.config_snapshot?.context_classification==='public'&&r.classification!=='public'} onChange={e=>setAmendInputs(old=>e.target.checked?[...old,r.resource_id]:old.filter(id=>id!==r.resource_id))}/>{r.name}{r.kind==='directory'?'（目录）':''}</label>)}</div></fieldset>
      {resources.some(r=>r.kind==='directory'&&amendInputs.includes(r.resource_id))&&<p role="note">{directoryInputNote}</p>}
      <p>工具权限在设置中管理；本次保存保留当前权限。</p>
      <div className="modal-actions"><button disabled={busy} onClick={()=>setAmending(null)}>返回</button><button className="primary" disabled={busy||!amendObjective.trim()} onClick={()=>void saveAmendment()}>保存规范修订</button></div>
    </div></div>}
    {showSource&&<div className="modal-backdrop"><div className="modal source-modal" role="dialog" aria-label="会话数据源"><div className="modal-heading"><h2>会话文件</h2><button aria-label="关闭本地数据源" onClick={()=>setShowSource(false)}><X size={20}/></button></div>
      {editable&&<><button className="primary" disabled={busy} onClick={()=>uploadInput.current?.click()}>选择文件上传</button><p>也可以把文件拖入输入框。每个文件上限128 MiB。</p><button disabled={busy} onClick={()=>void pickLocal(false)}>选择大文件引用</button><button disabled={busy} onClick={()=>void pickLocal(true)}>选择目录引用</button>
        <details><summary>大文件或目录：添加本地路径引用</summary><p>保持文件在原位置，适用于大型点云与目录。</p><label>本地路径<input value={path} onChange={e=>setPath(e.target.value)} placeholder="C:\数据\样地文件"/></label><button className="primary" disabled={busy||!path.trim()} onClick={()=>void addSource()}>添加只读引用</button></details></>}
      <SourceBrowser key={active?.task_id||'draft'} resources={sessionResources} activeTask={!!active} allowRegistration={editable&&!busy} onRegistered={selectSourceFile}/></div></div>}
    {showKnowledge&&<div className="modal-backdrop"><div className="modal settings-modal"><div className="modal-heading"><h2>本地知识与来源</h2><button aria-label="关闭本地知识" onClick={()=>setShowKnowledge(false)}><X size={20}/></button></div>
      <KnowledgePanel resources={sessionResources} tasks={tasks} onError={setError}/></div></div>}
    {showSettings&&<div className="modal-backdrop"><div className="modal settings-modal" role="dialog" aria-label="Agent 设置"><div className="modal-heading"><h2>模型与执行环境</h2><button aria-label="关闭设置" onClick={()=>setShowSettings(false)}><X size={20}/></button></div><p>后续新任务使用选定模型，当前任务保留自己的配置快照。</p><label>默认本地模型<select value={settings?.default_model||'small'} onChange={e=>void guarded(async()=>{
      if(!settings)return; await api('/settings',{request_id:requestId(),expected_version:settings.version,default_model:e.target.value},'PUT');setSettings(await api('/settings'));})}>
      <option value="small">Qwen3.5 · 4B</option><option value="large">Qwen3.8 · 27B</option></select></label>
      <details className="agent-preferences"><summary>Agent 高级设置</summary><h3>新会话默认设置</h3><p>默认使用完整能力，由 Agent 在授权范围内选择工具。以下偏好保存在此浏览器，不改变已有会话。</p>
        <label>工作方式<select aria-label="工作方式" value={harness} onChange={event=>setHarness(event.target.value)}><option value="H2">H2 · 按需独立复核</option><option value="H1">H1 · 单个助手持续处理</option><option value="H0">H0 · 基础工具对照</option></select></label>
        <label>工具权限上限<select aria-label="工具权限上限" value={scope} onChange={event=>setScope(event.target.value as Scope)}><option value="full">全部已启用能力</option><option value="files">文件与报告</option><option value="answer">仅回答</option><option value="custom">自定义</option></select></label>
        {scope==='custom'&&<div className="capability-grid">{Object.entries(capabilityLabels).map(([cap,label])=><label key={cap}><input type="checkbox" checked={customCaps.includes(cap)} onChange={event=>setCustomCaps(old=>event.target.checked?[...old,cap]:old.filter(value=>value!==cap))}/>{label}</label>)}</div>}
        {active&&<section className="current-task-settings"><h3>当前会话</h3><p>工作方式：{active.config_snapshot?.harness_mode||'H1'}（创建时固定）</p><label>本会话工具范围<select aria-label="本会话工具范围" disabled={!editable||active?.status==='completed'||busy} value={currentScopeCaps===null?'full':scopeName(currentScopeCaps)==='文件与报告'?'files':currentScopeCaps.length===0?'answer':'custom'} onChange={event=>setCurrentScopeCaps(scopeCapabilities(event.target.value as Scope,currentScopeCaps??[]))}><option value="full">全部已启用能力</option><option value="files">文件与报告</option><option value="answer">仅回答</option><option value="custom">自定义</option></select></label>
          {currentScopeCaps!==null&&<div className="capability-grid">{Object.entries(capabilityLabels).map(([cap,label])=><label key={cap}><input type="checkbox" disabled={!editable||active?.status==='completed'||busy} checked={currentScopeCaps.includes(cap)} onChange={event=>setCurrentScopeCaps(old=>event.target.checked?[...(old??[]),cap]:(old??[]).filter(value=>value!==cap))}/>{label}</label>)}</div>}
          <p>保存权限变更会暂停本会话并保留历史，核对后可显式恢复。</p><button disabled={!editable||busy||JSON.stringify(currentScopeCaps?.slice().sort()??null)===JSON.stringify(active.constraints?.allowed_capabilities?.slice().sort()??null)} onClick={()=>void saveCurrentScope()}>保存本会话工具范围</button></section>}
      </details>
      <label className="checkbox"><input type="checkbox" checked={showDiagnostics} onChange={e=>{setShowDiagnostics(e.target.checked);if(!e.target.checked)setPanel('artifacts');}}/>显示执行诊断面板</label>
      <button className="probe-button" onClick={()=>void guarded(async()=>setDiagnostics(await api('/models/profiles')))}><RefreshCw size={15}/>重新探测环境</button>
      {diagnostics&&<div className="diagnostics">{Object.entries(diagnostics.models||{}).map(([key,value]:[string,any])=><div key={key}><span>{value.profile.served_name}</span><b>{value.status==='available_not_tool_verified'?'模型已安装':value.status}</b></div>)}
        <div><span>受限代码执行</span><b>{diagnostics.capabilities?.restricted_code?.status==='available'?'容器服务可用':'容器服务不可用'}</b></div><div><span>系统内存</span><b>{(diagnostics.ram_bytes/1024**3).toFixed(1)} GiB</b></div></div>}
      <MemoryPanel tasks={tasks} onError={setError}/><div className="modal-info"><ShieldCheck size={16}/>只连接本机模型。没有自动云端回退。</div></div></div>}
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
