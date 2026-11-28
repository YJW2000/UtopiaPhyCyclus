import type { ProviderId } from './settingsContract'

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
  kind?: 'solution'
  selection?: Pick<EditorSelection, 'start' | 'end'>
  changes?: { start: number; end: number; before: string; after: string }[]
  status: string
  beforePath?: string | null
  afterPath?: string | null
}

export interface EditorDocument {
  papilotProject?: boolean
  id: string
  title: string
  source: string
  pdfPath?: string | null
  previewPath?: string | null
  previewCurrent?: boolean
  chapter?: string
  inputKind?: 'image'
  recognitionAgent?: ProviderId
  recognitionModel?: string
  exam?: { paths: string[]; answers: boolean }
  originalPage?: number
  pdfPageCount?: number
  sourcePath?: string | null
  questionPath?: string
  selections?: EditorSelection[]
  activeSelectionId?: string | null
  messages?: EditorMessage[]
  proposals?: EditorProposal[]
  createdAt?: string
  updatedAt?: string
  revision?: number
  canUndo?: boolean
}

export interface EditorTaskProgress {
  message: string
  page?: number
  total?: number
}

export interface EditorTask<T, P extends EditorTaskProgress = EditorTaskProgress> {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  result?: T
  error?: string
  message?: string
  progress?: P
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

export async function pollEditorTask<T, P extends EditorTaskProgress = EditorTaskProgress>(
  id: string,
  signal: AbortSignal,
  onProgress?: (message: string, progress: P) => void,
): Promise<EditorTask<T, P>> {
  while (!signal.aborted) {
    const task = await editorCall<EditorTask<T, P>>('task.get', { id })
    if (task.status === 'succeeded' || task.status === 'failed' || task.status === 'cancelled') return task
    if (task.progress?.message) onProgress?.(task.progress.message, task.progress)
    await new Promise<void>(resolve => setTimeout(resolve, 350))
  }
  return { id, status: 'cancelled' }
}

export async function reconstructEditorDocument(
  params: Record<string, unknown>,
  signal: AbortSignal,
  onProgress: (message: string) => void,
): Promise<EditorDocument> {
  signal.throwIfAborted()
  const { taskId } = await editorCall<{ taskId: string }>('workspace.reconstruct', params)
  let cancellation: Promise<unknown> | undefined
  function cancel() {
    cancellation ??= editorCall('task.cancel', { id: taskId }).catch(() => {})
  }
  if (signal.aborted) cancel()
  else signal.addEventListener('abort', cancel, { once: true })
  try {
    const task = await pollEditorTask<EditorDocument>(taskId, signal, message => {
      if (!signal.aborted) onProgress(message)
    })
    if (task.status !== 'succeeded' || !task.result) {
      throw new Error([task.error || task.message || '识别已停止。', task.log].filter(Boolean).join('\n\n'))
    }
    signal.throwIfAborted()
    return task.result
  } finally {
    signal.removeEventListener('abort', cancel)
    await cancellation
  }
}

export function errorMessage(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).replace(/^Error invoking remote method '[^']+': Error: /, '')
}
