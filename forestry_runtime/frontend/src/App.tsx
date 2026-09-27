import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'

import { request, RequestError, jsonBody } from './client'
import { ConversationPane } from './components/ConversationPane'
import { ScorecardButton } from './components/ScorecardButton'
import { SettingsPanel } from './components/SettingsPanel'
import { TracePane } from './components/TracePane'
import { deliveredArtifacts } from './eventViews'
import { useRunStream } from './hooks/useRunStream'
import type {
  Asset, Grant, InstructionPreview, KnowledgeSource, PendingUserMessage, Project,
  ProjectInstruction, ProjectMemory, Run, Session,
} from './types'

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [chatId, setChatId] = useState<string>('')
  const [run, setRun] = useState<Run | null>(null)
  const [assets, setAssets] = useState<Asset[]>([])
  const [grants, setGrants] = useState<Grant[]>([])
  const [workspace, setWorkspace] = useState('')
  const [prompt, setPrompt] = useState('')
  const [grantPath, setGrantPath] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState('')
  const [memories, setMemories] = useState<ProjectMemory[]>([])
  const [memoryDraft, setMemoryDraft] = useState('')
  const [knowledgeSources, setKnowledgeSources] = useState<KnowledgeSource[]>([])
  const [knowledgeKind, setKnowledgeKind] = useState<'local' | 'url'>('local')
  const [knowledgeLocator, setKnowledgeLocator] = useState('')
  const [instruction, setInstruction] = useState<ProjectInstruction | null>(null)
  const [instructionDraft, setInstructionDraft] = useState('')
  const [instructionPreview, setInstructionPreview] = useState<InstructionPreview | null>(null)
  const [showPreview, setShowPreview] = useState(false)
  const [pendingMessages, setPendingMessages] = useState<PendingUserMessage[]>([])
  const [pollEpoch, setPollEpoch] = useState(0)
  const selectedRef = useRef('')
  const pendingSendRef = useRef<{ content: string; inputId: string } | null>(null)

  const loadSessions = async (preferred?: string) => {
    const data = await request<{ sessions: Session[] }>('/sessions')
    setSessions(data.sessions)
    const requested = preferred || selectedRef.current
    const next = requested && data.sessions.some(item => item.chat_id === requested)
      ? requested
      : data.sessions[0]?.chat_id || ''
    selectedRef.current = next
    setChatId(next)
    return data.sessions
  }

  const selectChat = async (id: string) => {
    setNotice('')
    try {
      const items = await loadSessions(id)
      if (!items.some(item => item.chat_id === id)) {
        setNotice('该任务已被删除或清理，已切换到当前可用任务。')
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const createChat = async () => {
    const id = crypto.randomUUID()
    await request('/sessions', undefined, jsonBody({ chat_id: id }))
    selectedRef.current = id
    setChatId(id)
    await loadSessions(id)
  }

  const { events, turns } = useRunStream(chatId, run, pollEpoch, {
    onRun: setRun,
    onConfirmed: confirmed => {
      setPendingMessages(previous => previous.filter(item => !confirmed.has(item.inputId)))
    },
    onArtifactsChanged: () => {
      request<{ assets: Asset[] }>('/assets', chatId)
        .then(data => setAssets(data.assets)).catch(() => undefined)
    },
    onMissing: () => { void loadSessions() },
    onNotice: setNotice,
  })

  useEffect(() => {
    request<{ projects: Project[] }>('/projects')
      .then(data => setProjects(data.projects))
      .catch(error => setNotice(error instanceof Error ? error.message : String(error)))
    loadSessions().then(items => {
      if (!items.length) void createChat()
    }).catch(error => setNotice(error.message))
  }, [])

  useEffect(() => {
    let stopped = false
    selectedRef.current = chatId
    const selected = sessions.find(item => item.chat_id === chatId)
    setRun(selected?.run || null)
    const selectedProject = selected?.project?.id || ''
    setProjectId(selectedProject)
    if (selectedProject) {
      Promise.all([
        request<{ memories: ProjectMemory[] }>(`/projects/${selectedProject}/memories`),
        request<{ sources: KnowledgeSource[] }>(`/projects/${selectedProject}/knowledge/sources`),
        request<ProjectInstruction>(`/projects/${selectedProject}/instruction`),
      ])
        .then(([memoryData, sourceData, instructionData]) => {
          if (!stopped) {
            setMemories(memoryData.memories)
            setKnowledgeSources(sourceData.sources)
            setInstruction(instructionData)
            setInstructionDraft(instructionData.content)
            setInstructionPreview(null)
          }
        })
        .catch(error => { if (!stopped) setNotice(error instanceof Error ? error.message : String(error)) })
    } else {
      setMemories([])
      setKnowledgeSources([])
      setInstruction(null)
      setInstructionDraft('')
      setInstructionPreview(null)
    }
    if (!chatId) return
    Promise.all([
      request<{ assets: Asset[] }>('/assets', chatId),
      request<{ workspace: string; grants: Grant[] }>('/workspace', chatId),
    ]).then(([fileData, workspaceData]) => {
      if (stopped) return
      setAssets(fileData.assets)
      setWorkspace(workspaceData.workspace)
      setGrants(workspaceData.grants)
    }).catch(error => {
      if (stopped) return
      if (error instanceof RequestError && [404, 410].includes(error.status)) {
        setNotice('该任务已被删除或清理，正在刷新任务列表。')
        void loadSessions()
      } else {
        setNotice(error instanceof Error ? error.message : String(error))
      }
    })
    return () => { stopped = true }
  }, [chatId])

  const visiblePending = useMemo(
    () => pendingMessages.filter(item => item.chatId === chatId),
    [chatId, pendingMessages],
  )

  const deliveries = useMemo(() => deliveredArtifacts(events), [events])

  const send = async (event: FormEvent) => {
    event.preventDefault()
    const content = prompt.trim()
    if (!content || !chatId) return
    setBusy(true)
    setNotice('')
    const pending = pendingSendRef.current?.content === content
      ? pendingSendRef.current
      : { content, inputId: `input_${crypto.randomUUID()}` }
    pendingSendRef.current = pending
    setPendingMessages(previous => [
      ...previous.filter(item => item.inputId !== pending.inputId),
      { chatId, inputId: pending.inputId, content },
    ])
    setPrompt('')
    try {
      const assetIds = assets.map(item => item.id)
      const current = sessions.find(item => item.chat_id === chatId)?.run
        || (run?.chat_id === chatId ? run : null)
      const result = current
        ? await request<Run>(`/runs/${current.id}/messages`, chatId, jsonBody({ content, asset_ids: assetIds, input_id: pending.inputId }))
        : await request<Run>('/runs', chatId, jsonBody({ messages: [{ role: 'user', content, input_id: pending.inputId }], asset_ids: assetIds, use_tools: true }))
      pendingSendRef.current = null
      if (selectedRef.current === chatId) {
        setRun(result)
        setPollEpoch(value => value + 1)
      }
      void loadSessions().catch(error => {
        setNotice(error instanceof Error ? error.message : String(error))
      })
    } catch (error) {
      setPendingMessages(previous => previous.filter(item => item.inputId !== pending.inputId))
      setPrompt(previous => previous || content)
      setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  const control = async (action: 'pause' | 'cancel') => {
    if (!run) return
    try {
      const updated = await request<Run>(`/runs/${run.id}/${action}`, chatId, { method: 'POST' })
      setRun(updated)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const deleteChat = async (id = chatId) => {
    if (!id || !window.confirm('删除这个任务及其 Workspace、事件和产物记录？此操作不可撤销。')) return
    setBusy(true)
    setNotice('')
    try {
      await request(`/sessions/${id}`, undefined, { method: 'DELETE' })
      if (id === chatId) {
        selectedRef.current = ''
        setChatId('')
      }
      const remaining = await loadSessions(id === chatId ? undefined : chatId)
      if (!remaining.length) await createChat()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  const upload = async (files: FileList | null) => {
    if (!files || !chatId) return
    setBusy(true)
    try {
      for (const file of Array.from(files)) {
        const data = new FormData()
        const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
        data.set('relative_path', relative)
        data.set('file', file)
        await request('/workspace/files', chatId, { method: 'POST', body: data })
      }
      setNotice(`已上传 ${files.length} 个文件到 Workspace`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  const addGrant = async () => {
    if (!grantPath.trim()) return
    try {
      await request('/workspace/grants', chatId, jsonBody({
        path: grantPath.trim(), access: 'read', basis: '用户通过本地 React 工作台显式授权',
      }))
      const data = await request<{ grants: Grant[] }>('/workspace', chatId)
      setGrants(data.grants)
      setGrantPath('')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const chooseProject = async (next: string) => {
    if (!chatId) return
    await request(`/sessions/${chatId}/project`, undefined, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: next || null }),
    })
    setProjectId(next)
    if (next) {
      const [memoryData, sourceData, instructionData] = await Promise.all([
        request<{ memories: ProjectMemory[] }>(`/projects/${next}/memories`),
        request<{ sources: KnowledgeSource[] }>(`/projects/${next}/knowledge/sources`),
        request<ProjectInstruction>(`/projects/${next}/instruction`),
      ])
      setMemories(memoryData.memories)
      setKnowledgeSources(sourceData.sources)
      setInstruction(instructionData)
      setInstructionDraft(instructionData.content)
      setInstructionPreview(null)
    } else {
      setMemories([])
      setKnowledgeSources([])
      setInstruction(null)
      setInstructionDraft('')
      setInstructionPreview(null)
    }
    await loadSessions(chatId)
  }

  const loadInstruction = async (target?: string) => {
    const id = target || projectId
    if (!id) return
    const data = await request<ProjectInstruction>(`/projects/${id}/instruction`)
    setInstruction(data)
    setInstructionDraft(data.content)
    setInstructionPreview(null)
  }

  const saveInstruction = async () => {
    if (!projectId || !instruction) return
    try {
      const updated = await request<ProjectInstruction>(
        `/projects/${projectId}/instruction`, undefined, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: instructionDraft, expected_revision: instruction.revision,
          }),
        },
      )
      setInstruction(updated)
      setInstructionDraft(updated.content)
      setInstructionPreview(null)
      setNotice(`项目指令已保存为 r${updated.revision}，将在下一个用户 Turn 生效。`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const resetInstruction = async () => {
    if (!projectId) return
    if (!window.confirm('恢复默认项目指令？当前内容会作为历史版本保留。')) return
    try {
      const updated = await request<ProjectInstruction>(
        `/projects/${projectId}/instruction/reset`, undefined, { method: 'POST' },
      )
      setInstruction(updated)
      setInstructionDraft(updated.content)
      setInstructionPreview(null)
      setNotice(`已恢复默认项目指令（r${updated.revision}）。`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const previewInstruction = async () => {
    if (!projectId) return
    try {
      const data = await request<InstructionPreview>(
        `/projects/${projectId}/instruction/preview`, undefined,
        jsonBody({ content: instructionDraft }),
      )
      setInstructionPreview(data)
      setShowPreview(true)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const loadKnowledgeSources = async () => {
    if (!projectId) return
    const data = await request<{ sources: KnowledgeSource[] }>(`/projects/${projectId}/knowledge/sources`)
    setKnowledgeSources(data.sources)
  }

  const addKnowledgeSource = async () => {
    const locator = knowledgeLocator.trim()
    if (!projectId || !locator) return
    try {
      const source = await request<KnowledgeSource>(
        `/projects/${projectId}/knowledge/sources`, undefined,
        jsonBody({ kind: knowledgeKind, locator }),
      )
      setKnowledgeSources(value => [source, ...value.filter(item => item.id !== source.id)])
      setKnowledgeLocator('')
      window.setTimeout(() => { void loadKnowledgeSources() }, 1200)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  const createProject = async () => {
    const name = window.prompt('项目名称')?.trim()
    if (!name) return
    const project = await request<Project>('/projects', undefined, jsonBody({ name }))
    setProjects(value => [project, ...value])
    await chooseProject(project.id)
  }

  const addMemory = async () => {
    const content = memoryDraft.trim()
    if (!projectId || !content || !window.confirm('将这条内容保存为项目长期记忆？后续对话会使用它。')) return
    const item = await request<ProjectMemory>(`/projects/${projectId}/memories`, undefined, jsonBody({ content, confirmed: true }))
    setMemories(value => [item, ...value])
    setMemoryDraft('')
  }

  const editMemory = async (item: ProjectMemory) => {
    const content = window.prompt('编辑项目长期记忆', item.content)?.trim()
    if (!projectId || !content || content === item.content) return
    if (!window.confirm('确认保存这次记忆修改？修改会产生新版本。')) return
    try {
      const updated = await request<ProjectMemory>(`/projects/${projectId}/memories/${item.id}`, undefined, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, confirmed: true }),
      })
      setMemories(value => value.map(memory => memory.id === item.id ? updated : memory))
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  return <main className="shell">
    <aside className="sidebar">
      <header className="brand"><span>⌁</span><div><strong>Forestry Agent</strong><small>PydanticAI · Sandbox</small></div></header>
      <ScorecardButton />
      <button className="primary" onClick={() => void createChat()}>＋ 新建任务</button>
      <nav className="sessions">
        {sessions.map(item => <div key={item.chat_id} className={`sessionRow ${item.chat_id === chatId ? 'selected' : ''}`}>
          <button className="sessionSelect" onClick={() => void selectChat(item.chat_id)}>
            <span>{item.title}</span><small>{item.run?.state ?? ''}</small>
          </button>
          <button className="sessionDelete" disabled={busy} title="删除任务" aria-label={`删除任务：${item.title}`} onClick={() => void deleteChat(item.chat_id)}>×</button>
        </div>)}
      </nav>
      <section><h3>Workspace</h3><code className="path">{workspace || '尚未创建'}</code>
        <label className="upload">上传文件<input type="file" multiple onChange={event => void upload(event.target.files)} /></label>
        <label className="upload">上传文件夹<input type="file" multiple {...{ webkitdirectory: '' }} onChange={event => void upload(event.target.files)} /></label>
      </section>
      <section><h3>项目记忆</h3>
        <div className="inline"><select value={projectId} onChange={event => void chooseProject(event.target.value)}><option value="">不绑定项目</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select><button onClick={() => void createProject()}>新建</button></div>
        {projectId && <><div className="inline"><input value={memoryDraft} onChange={event => setMemoryDraft(event.target.value)} placeholder="需确认后保存" /><button onClick={() => void addMemory()}>记住</button></div><ul className="grants">{memories.map(item => <li key={item.id}><span>{item.content}<small>v{item.version}</small></span><button onClick={() => void editMemory(item)}>编辑</button><button onClick={async () => { await request(`/projects/${projectId}/memories/${item.id}`, undefined, { method: 'DELETE' }); setMemories(value => value.filter(memory => memory.id !== item.id)) }}>删除</button></li>)}</ul></>}
      </section>
      {projectId && <section><h3>项目指令</h3>
        <div className="sectionActions instructionMeta">
          <span>生效版本 r{instruction?.revision ?? '—'}{instruction?.is_default ? ' · 默认' : ''}</span>
          <span className={instruction && !instruction.within_budget ? 'overBudget' : ''}>
            约 {instruction?.estimated_tokens ?? 0} / {instruction?.budget_tokens ?? 0} tokens
          </span>
        </div>
        <textarea
          className="instructionEditor" rows={8} value={instructionDraft}
          aria-label="项目指令内容"
          onChange={event => setInstructionDraft(event.target.value)}
          placeholder="填写项目目标、沟通偏好、交付规范与项目约定"
        />
        <div className="sectionActions">
          <button onClick={() => void saveInstruction()} disabled={!instruction || instructionDraft === instruction.content}>保存</button>
          <button onClick={() => void previewInstruction()}>预览组装</button>
          <button onClick={() => void resetInstruction()}>恢复默认</button>
          <button onClick={() => void loadInstruction()}>重新载入</button>
        </div>
        <p className="hint">保存后在下一个用户 Turn 生效；已开始的 Turn 继续使用当前生效的版本。</p>
        {showPreview && instructionPreview && <div className="instructionPreview">
          <div className="sectionActions">
            <strong>下一个 Turn 的组装预览</strong>
            <button onClick={() => setShowPreview(false)}>收起</button>
          </div>
          <p className="hint">
            草稿约 {instructionPreview.draft_tokens} / {instructionPreview.budget_tokens} tokens
            {instructionPreview.draft_within_budget ? '' : '（超出预算，保存会被拒绝）'}；
            不含任务事实共约 {instructionPreview.assembled_tokens_excluding_runtime_facts} tokens。
          </p>
          <ol className="layers">{instructionPreview.layers.map(layer => <li key={layer.layer}>
            <div className="sectionActions">
              <strong>{layer.title}</strong>
              <span>{layer.source}</span>
              <span>{layer.tokens === null ? '按请求组装' : `${layer.tokens} tokens`}</span>
              {layer.entries ? <span>{layer.entries.length} 条指南</span> : null}
            </div>
            {layer.entries && <ul className="grants">{layer.entries.map(entry => <li key={entry.id}>
              <span>{entry.title}<small>{entry.id} · {entry.summary}</small></span>
            </li>)}</ul>}
            {layer.content
              ? <pre className="layerText">{layer.content}</pre>
              : <p className="hint">{layer.note}</p>}
          </li>)}</ol>
        </div>}
      </section>}
      {projectId && <section><h3>项目知识来源</h3>
        <div className="inline knowledgeInput"><select value={knowledgeKind} onChange={event => setKnowledgeKind(event.target.value as 'local' | 'url')}><option value="local">本地</option><option value="url">URL</option></select><input value={knowledgeLocator} onChange={event => setKnowledgeLocator(event.target.value)} placeholder={knowledgeKind === 'local' ? '维护者配置目录内的路径' : 'https://…'} /><button onClick={() => void addKnowledgeSource()}>索引</button></div>
        <div className="sectionActions"><button onClick={() => void loadKnowledgeSources()}>刷新状态</button></div>
        <ul className="grants knowledgeSources">{knowledgeSources.map(item => <li key={item.id}><span>{item.locator}<small>{item.kind} · {item.state}{item.version ? ` · v${item.version.slice(0, 12)}` : ''}{item.error ? ` · ${item.error}` : ''}</small></span><button onClick={async () => { await request(`/projects/${projectId}/knowledge/sources/${item.id}`, undefined, { method: 'DELETE' }); setKnowledgeSources(value => value.filter(source => source.id !== item.id)) }}>删除</button></li>)}</ul>
      </section>}
      <section><h3>只读目录授权</h3><div className="inline"><input value={grantPath} onChange={event => setGrantPath(event.target.value)} placeholder="E:\\data\\project" /><button onClick={() => void addGrant()}>授权</button></div>
        <ul className="grants">{grants.filter(item => item.active).map(item => <li key={item.id}><span>{item.host_path}</span><button onClick={async () => {
          await request(`/workspace/grants/${item.id}`, chatId, { method: 'DELETE' })
          setGrants(value => value.filter(grant => grant.id !== item.id))
        }}>撤销</button></li>)}</ul>
      </section>
      <SettingsPanel chatId={chatId} />
      <button className="danger ghost" disabled={busy} onClick={() => void deleteChat()}>删除当前任务</button>
    </aside>

    <ConversationPane
      run={run} events={events} artifacts={deliveries} pending={visiblePending} notice={notice}
      prompt={prompt} busy={busy} onPrompt={setPrompt} onSend={send}
      onControl={action => { void control(action) }}
    />
    <TracePane events={events} turns={turns} assets={assets} chatId={chatId} />
  </main>
}
