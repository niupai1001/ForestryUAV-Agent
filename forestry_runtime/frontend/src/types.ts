export type Run = {
  id: string
  chat_id: string
  state: string
  model_calls: number
  final?: Record<string, unknown> | null
  created_at: number
  updated_at: number
  state_version?: number
  recovery_reason?: string | null
}

export type Project = { id: string; name: string }
export type ProjectMemory = { id: string; content: string; version: number }
export type KnowledgeSource = {
  id: string
  kind: 'local' | 'url'
  locator: string
  state: string
  version?: string | null
  error?: string | null
}
export type Session = { chat_id: string; title: string; run: Run | null; project?: Project | null }
export type RuntimeEvent = { seq: number; type: string; [key: string]: unknown }
export type PendingUserMessage = { chatId: string; inputId: string; content: string }
export type ConversationRow = {
  role: 'user' | 'assistant'
  text: string
  thinking: string
  pending?: boolean
}
export type Asset = {
  id: string
  name: string
  size: number
  media_type?: string
  artifact_kind?: string
  sealed_at?: number | null
  verification?: { status?: string; [key: string]: unknown }
}
export type Grant = { id: string; host_path: string; access: string; active: boolean }
export type Turn = {
  id: string
  ordinal: number
  agent_run_id: string
  state: string
  checkpoint_state: string
}

export const terminalStates = new Set([
  'completed', 'failed', 'canceled', 'cancel_incomplete', 'paused',
])
