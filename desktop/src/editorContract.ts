export type PdfPanel = 'original' | 'preview' | 'before' | 'after'

export interface EditorAttachment {
  id: string
  title: string
  dataUrl: string
  page?: number
  panel?: PdfPanel
  rect?: [number, number, number, number]
  previewPath?: string
}

export interface EditorSelection {
  id: string
  start: number
  end: number
  label: string
}

export interface EditorMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  content?: string
  attachments?: EditorAttachment[]
  proposalId?: string
}

export interface EditorProposal {
  id: string
  before: string
  after: string
  baseSource: string
  source: string
  scope: 'document' | 'selection'
  selection?: Pick<EditorSelection, 'start' | 'end'>
  changes?: { start: number; end: number; before: string; after: string }[]
  status: string
  beforePath?: string | null
  afterPath?: string | null
}

export interface EditorDocument {
  id: string
  title: string
  source: string
  pdfPath?: string | null
  previewPath?: string | null
  previewCurrent?: boolean
  chapter?: string
  originalPage?: number
  pdfPageCount?: number
  sourcePath?: string | null
  selections?: EditorSelection[]
  activeSelectionId?: string | null
  messages?: EditorMessage[]
  proposals?: EditorProposal[]
  createdAt?: string
  updatedAt?: string
  revision?: number
  canUndo?: boolean
}

export interface EditorTask<T> {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  result?: T
  error?: string
  message?: string
  progress?: { message: string; page?: number; total?: number }
  log?: string
}

export interface EditorCompileResult {
  previewPath: string
  source: string
  revision?: number
  log?: string
}

export async function editorCall<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  return await window.utopia.call(method, params) as T
}

export async function pollEditorTask<T>(id: string, signal: AbortSignal, onProgress?: (progress: string) => void): Promise<EditorTask<T>> {
  while (!signal.aborted) {
    const task = await editorCall<EditorTask<T>>('task.get', { id })
    if (task.status === 'succeeded' || task.status === 'failed' || task.status === 'cancelled') return task
    if (task.progress?.message) onProgress?.(task.progress.message)
    await new Promise<void>(resolve => setTimeout(resolve, 350))
  }
  return { id, status: 'cancelled' }
}

export function errorMessage(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).replace(/^Error invoking remote method '[^']+': Error: /, '')
}
