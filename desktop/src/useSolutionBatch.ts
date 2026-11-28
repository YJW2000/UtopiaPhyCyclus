import { useEffect, useRef, useState } from 'react'
import type { Question } from './Bank'
import { editorCall, errorMessage, pollEditorTask } from './editorContract'
import type { EditorDocument, EditorTaskProgress } from './editorContract'

interface BatchItem {
  questionPath: string
  status: 'ready' | 'skipped' | 'failed'
  message: string
  documentId?: string
  proposalId?: string
}

type ReviewStatus = 'adopted' | 'dismissed' | 'stale' | 'saved'
export type BatchResultRow = BatchItem & { question?: Question; review?: ReviewStatus }

interface BatchResult { items: BatchItem[] }
interface BatchProgress extends EditorTaskProgress {
  completed: number
  total: number
  items: BatchItem[]
}
interface ActiveBatch { controller: AbortController; id?: string; stopRequested: boolean }
interface BatchRun {
  status: 'running' | 'succeeded' | 'cancelled' | 'failed'
  completed: number
  total: number
  progress?: string
  error?: string
  stopping?: boolean
}

export function normalizedPath(path: string) {
  return path.replaceAll('\\', '/')
}

export function useSolutionBatch() {
  const [selected, setSelected] = useState<Question[]>([])
  const [results, setResults] = useState<BatchResultRow[]>([])
  const [run, setRun] = useState<BatchRun>()
  const active = useRef<ActiveBatch>(undefined)

  useEffect(() => () => active.current?.controller.abort(), [])

  function select(questions: Question[], checked: boolean) {
    if (active.current) return
    setSelected(previous => {
      if (!checked) {
        const removed = new Set(questions.map(question => question.path))
        return previous.filter(question => !removed.has(question.path))
      }
      const paths = new Set(previous.map(question => question.path))
      return [...previous, ...questions.filter(question => !paths.has(question.path) && paths.add(question.path))]
    })
  }

  function forgetQuestions(paths: ReadonlySet<string>) {
    setSelected(previous => previous.filter(question => !paths.has(normalizedPath(question.path))))
    setResults(previous => previous.filter(row => !paths.has(normalizedPath(row.questionPath))))
    if (results.length && results.every(row => paths.has(normalizedPath(row.questionPath)))) setRun(undefined)
  }

  function mergeResults(items: BatchItem[], questions: Map<string, Question>) {
    if (!items.length) return
    setResults(previous => {
      const byPath = new Map(previous.map(row => [normalizedPath(row.questionPath), row]))
      let changed = false
      for (const item of items) {
        const key = normalizedPath(item.questionPath)
        const latest = byPath.get(key)
        if (latest && latest.documentId === item.documentId && latest.proposalId === item.proposalId && latest.status === item.status && latest.message === item.message) continue
        changed = true
        byPath.set(key, {
          ...item,
          question: questions.get(key) ?? latest?.question,
          review: item.proposalId && item.proposalId === latest?.proposalId ? latest.review : undefined,
        })
      }
      return changed ? Array.from(byPath.values()) : previous
    })
  }

  async function cancel(activeRun: ActiveBatch) {
    if (!activeRun.id) return
    try {
      await editorCall('task.cancel', { id: activeRun.id })
    } catch (error) {
      if (active.current !== activeRun || activeRun.controller.signal.aborted) return
      activeRun.stopRequested = false
      setRun(previous => previous && { ...previous, stopping: false, error: errorMessage(error) })
    }
  }

  function stop() {
    const activeRun = active.current
    if (!activeRun || activeRun.stopRequested) return
    activeRun.stopRequested = true
    setRun(previous => previous && { ...previous, stopping: true, error: undefined })
    void cancel(activeRun)
  }

  async function start() {
    if (!selected.length || active.current) return
    const activeRun: ActiveBatch = { controller: new AbortController(), stopRequested: false }
    active.current = activeRun
    const questions = new Map(selected.map(question => [normalizedPath(question.path), question]))
    setRun({ status: 'running', completed: 0, total: questions.size, progress: '正在准备…' })
    try {
      const job = await editorCall<{ taskId: string }>('workspace.fill_solution_batch', { questionPaths: Array.from(questions.keys()) })
      activeRun.id = job.taskId
      if (activeRun.stopRequested) void cancel(activeRun)
      const task = await pollEditorTask<BatchResult, BatchProgress>(job.taskId, activeRun.controller.signal, (message, progress) => {
        if (activeRun.controller.signal.aborted) return
        mergeResults(progress.items ?? [], questions)
        setRun(previous => previous?.progress === message && previous.completed === progress.completed ? previous
          : { ...previous, status: 'running', completed: progress.completed, total: progress.total, progress: message })
      })
      if (activeRun.controller.signal.aborted) return
      const items = task.result?.items ?? task.progress?.items ?? []
      mergeResults(items, questions)
      const status = task.status === 'succeeded' || task.status === 'cancelled' ? task.status : 'failed'
      setRun({ status, completed: items.length, total: questions.size, error: status === 'failed' ? task.error || task.message || '批量补充失败' : undefined })
    } catch (error) {
      if (!activeRun.controller.signal.aborted) {
        setRun(previous => ({ status: 'failed', completed: previous?.completed ?? 0, total: questions.size, error: errorMessage(error) }))
      }
    } finally {
      active.current = undefined
    }
  }

  function trackReview(document: EditorDocument, saved = false) {
    if (!document.proposals?.length) return
    setResults(previous => {
      let changed = false
      const next = previous.map(row => {
        if (row.documentId !== document.id || row.review === 'saved') return row
        const proposal = document.proposals?.find(item => item.id === row.proposalId)
        if (!proposal) return row
        let review: ReviewStatus | undefined
        if (proposal.status === 'applied') review = saved ? 'saved' : 'adopted'
        else if (proposal.status === 'dismissed' || proposal.status === 'undone') review = 'dismissed'
        else if (proposal.baseSource !== document.source) review = 'stale'
        if (review === row.review) return row
        changed = true
        return { ...row, review }
      })
      return changed ? next : previous
    })
  }

  return { selected, select, results, run, start, stop, trackReview, forgetQuestions }
}
