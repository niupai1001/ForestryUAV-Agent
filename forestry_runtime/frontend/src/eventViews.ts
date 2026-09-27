import type {
  ArtifactDelivery, ConversationRow, PendingUserMessage, RuntimeEvent,
} from './types'

export type TraceRow = {
  type: string
  label: string
  detail?: unknown
  state?: string
  diff?: { path: string; oldText: string; newText: string; replacements?: unknown }
}

export type DeliveredArtifact = { delivery: ArtifactDelivery; tool?: string; seq?: number }

// A delivery is described by the Runtime and can sit at several depths of a tool result
// (data.delivery, data.preview.delivery, data.artifacts[].delivery, …). The walk is
// depth-capped and cycle-guarded so one unexpected event shape cannot stop the pane
// from rendering everything else.
const DELIVERY_WALK_DEPTH = 4

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function isDelivery(value: unknown): value is ArtifactDelivery {
  const record = asRecord(value)
  if (!record || typeof record.asset_id !== 'string' || !record.asset_id) return false
  return asRecord(record.urls) !== null
}

function walkDeliveries(
  value: unknown, depth: number, seen: Set<object>, visit: (delivery: ArtifactDelivery) => void,
) {
  if (depth > DELIVERY_WALK_DEPTH) return
  if (Array.isArray(value)) {
    if (seen.has(value)) return
    seen.add(value)
    for (const item of value) walkDeliveries(item, depth + 1, seen, visit)
    return
  }
  const record = asRecord(value)
  if (!record || seen.has(record)) return
  seen.add(record)
  const nested = record.delivery
  if (isDelivery(nested)) visit(nested)
  else if (isDelivery(record)) visit(record)
  for (const [key, item] of Object.entries(record)) {
    if (key === 'delivery' || item === null || typeof item !== 'object') continue
    walkDeliveries(item, depth + 1, seen, visit)
  }
}

export function deliveredArtifacts(events: RuntimeEvent[]): DeliveredArtifact[] {
  const found: DeliveredArtifact[] = []
  const positions = new Map<string, number>()
  const add = (delivery: ArtifactDelivery, tool?: string, seq?: number) => {
    if (!delivery.asset_id) return
    const existing = positions.get(delivery.asset_id)
    if (existing === undefined) {
      positions.set(delivery.asset_id, found.length)
      const entry: DeliveredArtifact = { delivery }
      if (tool) entry.tool = tool
      if (seq !== undefined) entry.seq = seq
      found.push(entry)
      return
    }
    // The first occurrence keeps the position and the tool that produced the artifact;
    // a later, richer descriptor only fills in what the first one lacked.
    const entry = found[existing]
    if (!entry.delivery.checks && delivery.checks) entry.delivery = delivery
    if (!entry.tool && tool) entry.tool = tool
    if (entry.seq === undefined && seq !== undefined) entry.seq = seq
  }
  for (const event of events) {
    const tool = event.type === 'tool_end' && typeof event.name === 'string' ? event.name : undefined
    const seq = typeof event.seq === 'number' ? event.seq : undefined
    walkDeliveries(event, 0, new Set<object>(), delivery => add(delivery, tool, seq))
  }
  return found
}

function formatBytes(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return ''
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB']
  let size = value / 1024
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 10 || size % 1 === 0 ? 0 : 1)} ${units[unit]}`
}

export function artifactCaption(delivery: ArtifactDelivery): string {
  const parts = [delivery.name || delivery.asset_id || '未命名产物']
  if (delivery.media_type) parts.push(delivery.media_type)
  const size = formatBytes(delivery.size_bytes)
  if (size) parts.push(size)
  return parts.join(' · ')
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
      // A machine state is not the model speaking. When a guard rail pauses the turn,
      // the model is the one that has to say why and what is missing -- the Runtime
      // already asked it for exactly that in a closing, tool-free turn. Printing the
      // Runtime's own threshold text here put words in the model's mouth and made a
      // pause look like the Runtime had cut the conversation off instead.
      //
      // A terminal failure has no model turn to follow it, so there the message is
      // kept: it is the only account of what happened. The state itself is always
      // visible in the run header, so nothing is hidden by dropping the prefix.
      const terminal = ['failed', 'canceled', 'cancel_incomplete'].includes(String(event.state || ''))
      if (terminal) {
        ensureAssistant().text += `\n\n${eventText(event)}`
      }
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
    } else if (event.type === 'error') {
      // The machine's account of the turn: kept out of the conversation when the model
      // is the one explaining a pause, but never discarded -- this is where the guard's
      // own text, the blocker and any context budget ledger stay readable.
      flush()
      rows.push({ type: 'error', label: String(event.state || 'error'), detail: event, state: String(event.state || '') })
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
