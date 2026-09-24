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
export type ProjectInstruction = {
  project_id: string
  content: string
  revision: number
  updated_by?: string | null
  budget_tokens: number
  estimated_tokens: number
  within_budget: boolean
  is_default: boolean
}
export type PromptLayer = {
  layer: string
  title: string
  source: string
  editable: boolean
  revision?: number
  is_default?: boolean
  draft?: boolean
  tokens: number | null
  budget_tokens?: number
  within_budget?: boolean
  content: string | null
  entries?: { id: string; title: string; summary: string; tags: string[] }[]
  note?: string
}
export type InstructionPreview = {
  active_revision: number
  is_default: boolean
  budget_tokens: number
  draft_tokens: number
  draft_within_budget: boolean
  layers: PromptLayer[]
  assembled_tokens_excluding_runtime_facts: number
  note: string
}
export type InstructionVersion = {
  revision: number
  origin: string
  actor?: string | null
  created_at: number
  content_chars: number
}
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
export type SettingsRow = {
  key: string
  kind: 'int' | 'float' | 'bool' | 'text'
  value: string
  default: string
  source: 'override' | 'env' | 'default'
  group: string
  help: string
  scope: 'per_run' | 'startup'
  applies: 'next_run' | 'restart_required'
  minimum: number | null
  maximum: number | null
  choices: string[]
}
export type SettingsGroup = { name: string; settings: SettingsRow[] }
export type SettingsPanelData = {
  groups: SettingsGroup[]
  override_count: number
}
export type SettingsSaveResult = SettingsPanelData & {
  applied: { key: string; value: string; source: string; applies: string; cleared: boolean }[]
}
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
