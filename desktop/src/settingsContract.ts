export type AgentId = 'codex' | 'claude' | 'copilot' | 'opencode' | 'gemini' | 'cursor' | 'codebuddy' | 'qoder' | 'agy' | 'workbuddy'
export type ProviderId = AgentId | 'api'
export type EnvironmentToolId = AgentId | 'git' | 'latex'

export interface LocalAgent {
  id: AgentId
  label: string
  installed: boolean
  images: boolean
  setup?: string
  loginCommand?: string | null
}

export interface ProviderStatus {
  agents: LocalAgent[]
  apiConfigured: boolean
  effectiveProvider?: ProviderId | null
}

export interface EnvironmentTool {
  id: EnvironmentToolId
  label: string
  installed: boolean
  path: string | null
  version: string | null
  source: 'bundled' | 'system' | null
  canInstall: boolean
  installMethod: string | null
  helpUrl: string | null
  installing: boolean
  detail: string | null
  progress?: string | null
  syncTeX?: boolean | null
}

export interface EnvironmentStatus {
  tools: Record<EnvironmentToolId, EnvironmentTool>
}

export interface EnvironmentInstallResult {
  ok: boolean
  tool: EnvironmentToolId
  message: string
}
