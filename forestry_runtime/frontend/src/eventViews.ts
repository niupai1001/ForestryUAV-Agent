import type { ConversationRow, PendingUserMessage, RuntimeEvent } from './types'

export type TraceRow = {
  type: string
  label: string
  detail?: unknown
  state?: string
  diff?: { path: string; oldText: string; newText: string; replacements?: unknown }
}

export function eventText(event: RuntimeEvent, field = 'content'): string {
  const value = event[field]
  return typeof value === 'string' ? value : ''
}

export function conversationRows(events: RuntimeEvent[], pending: PendingUserMessage[]) {
  const rows: ConversationRow[] = []
  let assistant: ConversationRow | null = null
  const confirmedInputs = new Set(
    events.filter(event => event.type === 'user_message')
      .map(event => eventText(event, 'input_id')).filter(Boolean),
  )
  const ensureAssistant = () => {
    if (!assistant) {
      assistant = { role: 'assistant', text: '', thinking: '' }
      rows.push(assistant)
    }
    return assistant
  }
  for (const event of events) {
    if (event.type === 'user_message') {
      assistant = null
      rows.push({ role: 'user', text: eventText(event), thinking: '' })
    } else if (event.type === 'thinking') {
      ensureAssistant().thinking += eventText(event)
    } else if (event.type === 'message') {
      ensureAssistant().text += eventText(event)
    } else if (event.type === 'error') {
      ensureAssistant().text += `\n\n[${String(event.state || 'error')}] ${eventText(event)}`
    }
  }
  for (const item of pending) {
    if (!confirmedInputs.has(item.inputId)) {
      rows.push({ role: 'user', text: item.content, thinking: '', pending: true })
    }
  }
  return rows
}

export function traceRows(events: RuntimeEvent[]): TraceRow[] {
  const rows: TraceRow[] = []
  const starts = new Map<string, RuntimeEvent>()
  let thinking = ''
  const flush = () => {
    if (thinking.trim()) rows.push({ type: 'thinking', label: '模型原生 thinking', detail: thinking.trim() })
    thinking = ''
  }
  for (const event of events) {
    if (event.type === 'thinking') {
      thinking += eventText(event)
      continue
    }
    if (event.type === 'model_call') {
      flush()
      rows.push({ type: 'model', label: `模型调用 ${String(event.number || '')}`, detail: event })
    } else if (event.type === 'tool_start') {
      flush()
      starts.set(String(event.action_id || ''), event)
      rows.push({ type: 'action', label: String(event.name || 'tool'), detail: event.arguments, state: 'running' })
    } else if (event.type === 'tool_end') {
      flush()
      const start = starts.get(String(event.action_id || ''))
      const args = start?.arguments as Record<string, unknown> | undefined
      const result = event.result as { data?: Record<string, unknown> } | undefined
      const diff = event.name === 'fs_edit' && typeof args?.old === 'string' && typeof args?.new === 'string'
        ? { path: String(result?.data?.path || args.path || ''), oldText: args.old, newText: args.new, replacements: result?.data?.replacements }
        : undefined
      const succeeded = event.outcome_ok ?? event.ok
      rows.push({ type: 'result', label: `${String(event.name || 'tool')} 结果`, detail: event.result, state: succeeded === true ? 'ok' : succeeded === false ? 'failed' : '', diff })
    } else if (['job_status', 'job_reconciled'].includes(event.type)) {
      flush()
      rows.push({ type: 'job', label: String(event.job_id || '后台作业'), detail: event, state: String(event.state || '') })
    } else if (['recovery_resume', 'recovery_blocked', 'actions_reconciled'].includes(event.type)) {
      flush()
      rows.push({ type: 'recovery', label: event.type, detail: event, state: String(event.state || '') })
    } else if (event.type === 'run_state') {
      flush()
      rows.push({ type: 'state', label: 'Run 状态', detail: event.recovery_reason, state: String(event.state || '') })
    } else if (event.type === 'done') {
      flush()
      rows.push({ type: 'done', label: 'Turn 结束', detail: event, state: event.state ? String(event.state) : '' })
    }
  }
  flush()
  return rows
}
