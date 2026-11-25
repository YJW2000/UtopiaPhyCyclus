import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent, PointerEvent } from 'react'
import { EditorChat, EditorModal } from './EditorChat'
import { EditorPdf } from './EditorPdf'
import type { PdfAnnotationAction, PdfFit, PdfNavigation, PdfPosition, PdfTool, PdfViewState } from './EditorPdf'
import PaneDivider from './PaneDivider'
import { WorkbenchBookmarks, WorkbenchFind, WorkbenchIcon, WorkbenchRail, WorkbenchToolbar } from './WorkbenchToolbar'
import type { WorkbenchAction } from './WorkbenchToolbar'
import { editorCall, errorMessage, pollEditorTask } from './editorContract'
import type { EditorAttachment, EditorCompileResult, EditorDocument, EditorMessage, EditorProposal, EditorSelection, PdfPanel } from './editorContract'
import type { ProviderId } from './settingsContract'
import './editor.css'

export type { EditorDocument } from './editorContract'

type Mode = 'review' | 'split' | 'chat'
type View = 'preview' | 'source' | 'original' | 'compare'
type Sheet = 'library' | 'recognize'
type Utility = 'find' | 'bookmarks' | 'notes'

interface Props {
  document: EditorDocument
  initialCompileError?: string
  docked?: boolean
  onExpand?: () => void
  onEditFileInfo?: (document: EditorDocument) => void
  onOpenPapilot?: () => void
  reviewProposalId?: string
  onChange: (document: EditorDocument) => void
  onQuestionSaved?: (document: EditorDocument) => void
  onClose: () => void
}

interface Draft {
  source: string
  title: string
  selections: EditorSelection[]
  activeSelectionId: string | null
}

interface Layout {
  mode: Mode
  view: View
  pair?: Exclude<View, 'compare'>
  sync: boolean
  chatWidth: number
  documentWidth: number
  pdf: Record<PdfPanel, PdfPosition>
  pdfFit?: Partial<Record<PdfPanel, PdfFit>>
  sourceScroll: number
  compareText: boolean
}

interface ChatRun { controller: AbortController; id?: string; message: EditorMessage; fillSolution: boolean }
interface CompareState { id: string; before?: string; after?: string; loading: boolean; error?: string }
interface BankExport { files: { file: string; status: 'success' | 'skip' | 'error'; message: string }[] }

const questionKinds = ['单项选择题', '多项选择题', '实验题', '计算题']
const pdfTools = [
  ['select', '选取文字', 'select'], ['hand', '抓手拖动', 'hand'], ['crop', '框选 PDF', 'region'],
  ['magic', '魔法笔', 'magic'], ['mark', '高亮标记', 'mark'], ['note', 'PDF 批注', 'note'],
  ['trim', '裁剪页边距', 'crop'],
] as const
const activeChatDrafts = new Map<string, { prompt: string; attachments: EditorAttachment[] }>()
const chatPanelId = 'workbench-chat-pane'
const chatMinWidth = 280
const chatMaxWidth = 680
const chatMaxFraction = 0.52

function documentDraft(document: EditorDocument): Draft {
  return { source: document.source ?? '', title: document.title, selections: document.selections ?? [], activeSelectionId: document.activeSelectionId ?? null }
}

function parseStored<T>(key: string): T | undefined {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? undefined } catch { return undefined }
}

function storeValue(key: string, value?: unknown) {
  try {
    if (value === undefined) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch { /* The mounted editor still retains the state. */ }
}

function dockLayout(layout: Layout, document: EditorDocument): Layout {
  return { ...layout, mode: 'review', view: document.pdfPath && !document.source ? 'original' : 'preview', pair: undefined }
}

function initialLayout(document: EditorDocument, layoutKey: string, docked: boolean): Layout {
  const position = (page = 1): PdfPosition => ({ page, offset: 0, zoom: 1 })
  const imageSource = document.inputKind === 'image' && Boolean(document.source.trim())
  const layout: Layout = parseStored<Layout>(layoutKey) ?? {
    mode: imageSource ? 'review' : 'split',
    view: imageSource ? 'source' : document.pdfPath && !document.source ? 'original' : 'preview',
    pair: imageSource && document.previewPath ? 'preview' : undefined, sync: false, chatWidth: 385, documentWidth: 50,
    pdf: { preview: position(), original: position(document.originalPage), before: position(), after: position() }, sourceScroll: 0, compareText: false,
  }
  return docked ? dockLayout(layout, document) : layout
}

export default function Editor(props: Props) {
  return <Workbench key={props.document.id} {...props} />
}

export { Editor }

function Workbench({ document, initialCompileError, docked = false, onExpand, onEditFileInfo, onOpenPapilot, reviewProposalId, onChange, onQuestionSaved, onClose }: Props) {
  const draftKey = `utopia-workbench-draft-${document.id}`
  const layoutKey = `utopia-workbench-${docked ? 'dock-layout' : 'layout'}-${document.id}`
  const bookmarkKey = `utopia-workbench-bookmarks-${document.id}`
  const historyKey = `utopia-workbench-history-${document.id}`
  const agentKey = `utopia-workbench-agent-${document.id}`
  const [record, setRecord] = useState(document)
  const [draft, setDraft] = useState(() => {
    const cached = parseStored<{ baseRevision?: number; draft: Draft }>(draftKey)
    return cached && cached.baseRevision === document.revision ? cached.draft : documentDraft(document)
  })
  const [layout, setLayout] = useState(() => initialLayout(document, layoutKey, docked))
  const [preview, setPreview] = useState(() => ({ path: document.previewPath, source: document.previewCurrent === false ? undefined : document.source }))
  const [compiling, setCompiling] = useState(false)
  const [papilotBusy, setPapilotBusy] = useState(false)
  const [reconstructing, setReconstructing] = useState(false)
  const [recognitionProgress, setRecognitionProgress] = useState('')
  const [exporting, setExporting] = useState(false)
  const [savingQuestion, setSavingQuestion] = useState(false)
  const [saveState, setSaveState] = useState('')
  const [notice, setNotice] = useState(initialCompileError ? 'LaTeX 编译失败' : '')
  const [compileLog, setCompileLog] = useState(initialCompileError ?? '')
  const [showLog, setShowLog] = useState(false)
  const [navigation, setNavigation] = useState<Partial<Record<PdfPanel, PdfNavigation>>>({})
  const [focusedPdf, setFocusedPdf] = useState<PdfPanel>()
  const [tool, setTool] = useState<PdfTool>('select')
  const [pdfViews, setPdfViews] = useState<Partial<Record<PdfPanel, PdfViewState>>>({})
  const [annotationCommands, setAnnotationCommands] = useState<Partial<Record<PdfPanel, PdfAnnotationAction>>>({})
  const [utility, setUtility] = useState<Utility>()
  const [find, setFind] = useState({ query: '', index: 0 })
  const [bookmarks, setBookmarks] = useState(() => parseStored<Partial<Record<PdfPanel, number[]>>>(bookmarkKey) ?? {})
  const [historyAfter, setHistoryAfter] = useState(() => parseStored<string>(historyKey))
  const [showHistory, setShowHistory] = useState(false)
  const [agent, setAgent] = useState(() => {
    const saved = parseStored<ProviderId | 'api'>(agentKey)
    return saved === 'api' ? undefined : saved
  })
  const [locateMode, setLocateMode] = useState(false)
  const [locatingSource, setLocatingSource] = useState(false)
  const [attachments, setAttachments] = useState<EditorAttachment[]>(() => activeChatDrafts.get(document.id)?.attachments ?? [])
  const [prompt, setPrompt] = useState(() => activeChatDrafts.get(document.id)?.prompt ?? '')
  const [chatStatus, setChatStatus] = useState('')
  const [proposalBusy, setProposalBusy] = useState(false)
  const [pending, setPending] = useState<EditorMessage>()
  const [proposalId, setProposalId] = useState<string>()
  const [compare, setCompare] = useState<CompareState>()
  const [sheet, setSheet] = useState<Sheet>()
  const [sheetBusy, setSheetBusy] = useState(false)
  const [sheetError, setSheetError] = useState('')
  const [chapters, setChapters] = useState<string[]>([])
  const [sourceRange, setSourceRange] = useState({ start: 0, end: 0 })
  const [renaming, setRenaming] = useState(false)
  const sourceArea = useRef<HTMLTextAreaElement>(null)
  const chatPane = useRef<HTMLElement>(null)
  const lineNumbers = useRef<HTMLDivElement>(null)
  const sourceScroll = useRef(layout.sourceScroll)
  const latestDraft = useRef(draft)
  const latestRecord = useRef(record)
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve())
  const lifecycle = useRef<AbortController>(null)
  const chatRun = useRef<ChatRun>(null)
  const compareRun = useRef<AbortController>(null)
  const compileRun = useRef(false)
  const queuedCompileSource = useRef<string>(undefined)
  const recognitionRun = useRef(false)
  const wasDocked = useRef(docked)
  const navigationStamp = useRef(0)
  const imageInput = record.inputKind === 'image'
  const originalLabel = imageInput ? '原图' : '原卷'
  const recognitionLabel = imageInput ? '开始识别图片' : '识别原卷'
  const changed = useMemo(() => JSON.stringify(draft) !== JSON.stringify(documentDraft(record)), [draft, record])
  const sourceLineCount = useMemo(() => draft.source.split('\n').length, [draft.source])
  const sourceLines = useMemo(() => Array.from({ length: sourceLineCount }, (_, index) => <div key={index}>{index + 1}</div>), [sourceLineCount])
  const selected = draft.selections.find(item => item.id === draft.activeSelectionId)
  const proposals = record.proposals ?? []
  const proposal = proposals.find(item => item.id === proposalId) ?? proposals.at(-1)
  const pendingSolution = proposals.find(item => item.kind === 'solution' && item.status === 'pending' && item.baseSource === draft.source)
  const fillingSolution = Boolean(pending && chatRun.current?.fillSolution)
  const librarySaveDisabled = !draft.source.trim() || savingQuestion || fillingSolution || Boolean(pendingSolution)
  const preciseCaptures = preview.source === draft.source
    ? attachments.filter(item => item.panel === 'preview' && item.previewPath === preview.path && item.rect && item.page)
    : []
  const messages = record.messages ?? []
  const boundary = historyAfter ? messages.findIndex(message => message.id === historyAfter) : -1
  const taskMessages = showHistory ? messages : messages.slice(boundary + 1)
  const taskProposals = boundary < 0 || showHistory ? proposals : proposals.filter(item => taskMessages.some(message => message.proposalId === item.id))
  const pdfPanels: PdfPanel[] = layout.view === 'compare'
    ? layout.compareText || !proposal ? [] : ['before', 'after']
    : [layout.view, layout.pair].filter((view): view is 'preview' | 'original' => view === 'preview' || view === 'original')
  const activePdf = focusedPdf && pdfPanels.includes(focusedPdf) ? focusedPdf : pdfPanels[0]
  const pdfView = activePdf ? pdfViews[activePdf] : undefined
  const mainPdfView = record.pdfPath && !draft.source.trim() ? 'original' : 'preview'
  const rememberPdfView = useMemo(() => Object.fromEntries((['preview', 'original', 'before', 'after'] as const).map(panel => [
    panel, (view: PdfViewState) => setPdfViews(previous => ({ ...previous, [panel]: view })),
  ])) as Record<PdfPanel, (view: PdfViewState) => void>, [])

  const attachSource = useCallback((node: HTMLTextAreaElement | null) => {
    if (node) {
      node.scrollTop = sourceScroll.current
      if (lineNumbers.current) lineNumbers.current.scrollTop = sourceScroll.current
    }
    sourceArea.current = node
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    lifecycle.current = controller
    return () => {
      controller.abort()
      compareRun.current?.abort()
      const lastRun = chatRun.current
      lastRun?.controller.abort()
      if (lastRun?.id) void editorCall('task.cancel', { id: lastRun.id }).catch(() => {})
    }
  }, [])

  function accept(next: EditorDocument, replaceDraft = false) {
    if (lifecycle.current?.signal.aborted || next.id !== document.id) return
    if (next.revision !== undefined && latestRecord.current.revision !== undefined && next.revision < latestRecord.current.revision) return
    latestRecord.current = next
    setRecord(next)
    if (replaceDraft) {
      const updated = documentDraft(next)
      latestDraft.current = updated
      setDraft(updated)
      storeValue(draftKey)
    }
    onChange(next)
  }

  useEffect(() => {
    const previous = latestRecord.current
    if (document === previous || document.revision === undefined || (previous.revision ?? -1) >= document.revision) return
    // The file-info dialog persists the draft before submitting its own change.
    if (JSON.stringify(latestDraft.current) !== JSON.stringify(documentDraft(previous))) return
    accept(document, true)
    setPreview({ path: document.previewPath, source: document.previewCurrent === false ? undefined : document.source })
    if (docked && document.source !== previous.source && document.previewCurrent === false) {
      if (compileRun.current) queuedCompileSource.current = document.source
      else void compileCurrent(document.source)
    }
  }, [document])

  function updateDraft(next: Draft) {
    latestDraft.current = next
    setDraft(next)
    storeValue(draftKey, { baseRevision: latestRecord.current.revision, draft: next })
  }

  function persist() {
    const request = saveQueue.current.catch(() => {}).then(async () => {
      const snapshot = latestDraft.current
      if (JSON.stringify(snapshot) === JSON.stringify(documentDraft(latestRecord.current))) return latestRecord.current
      setSaveState('保存中…')
      const next = await editorCall<EditorDocument>('workspace.save', { id: document.id, ...snapshot, revision: latestRecord.current.revision })
      accept(next)
      if (latestDraft.current === snapshot) {
        const savedDraft = documentDraft(next)
        latestDraft.current = savedDraft
        setDraft(savedDraft)
        storeValue(draftKey)
      }
      else storeValue(draftKey, { baseRevision: next.revision, draft: latestDraft.current })
      setSaveState('')
      return next
    }).catch(error => {
      setSaveState('保存失败')
      setNotice(errorMessage(error))
      throw error
    })
    saveQueue.current = request
    return request
  }

  useEffect(() => {
    if (!changed) return
    const timer = setTimeout(() => { void persist().catch(() => {}) }, 550)
    return () => clearTimeout(timer)
  }, [draft, changed])

  useEffect(() => {
    const timer = setTimeout(() => storeValue(layoutKey, layout), 130)
    return () => clearTimeout(timer)
  }, [layout, layoutKey])

  useEffect(() => {
    activeChatDrafts.set(document.id, { prompt, attachments })
  }, [document.id, prompt, attachments])

  useEffect(() => {
    if (!initialCompileError && (!document.previewPath || (docked && document.previewCurrent === false)) && document.source.trim()) {
      void compileCurrent()
    }
  }, [document.id])

  useEffect(() => {
    if (docked && !wasDocked.current) setLayout(previous => dockLayout(previous, latestRecord.current))
    wasDocked.current = docked
  }, [docked])

  useEffect(() => {
    const key = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void persist().catch(() => {})
      }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [document.id])

  async function runTask<T>(method: string, params: Record<string, unknown>, signal = lifecycle.current?.signal, onProgress?: (progress: string) => void) {
    const job = await editorCall<{ taskId: string }>(method, { id: document.id, ...params })
    const task = await pollEditorTask<T>(job.taskId, signal ?? new AbortController().signal, onProgress)
    if (docked && method === 'workspace.compile' && signal?.aborted) {
      void editorCall('task.cancel', { id: job.taskId }).catch(() => {})
    }
    if (task.status !== 'succeeded' || task.result === undefined) {
      throw new Error([task.error || task.message || (task.status === 'cancelled' ? '操作已停止' : '操作失败'), task.log].filter(Boolean).join('\n\n'))
    }
    return task.result
  }

  async function compileCurrent(source = latestDraft.current.source) {
    if (compileRun.current || !source.trim()) return
    compileRun.current = true
    setCompiling(true)
    setNotice('')
    setCompileLog('')
    try {
      await persist()
      const result = await runTask<EditorCompileResult>('workspace.compile', { source })
      if (!queuedCompileSource.current) setPreview({ path: result.previewPath, source: result.source ?? source })
      accept(await editorCall<EditorDocument>('workspace.open', { id: document.id }))
    } catch (error) {
      const log = errorMessage(error)
      setCompileLog(log)
      setNotice('LaTeX 编译失败')
    } finally {
      compileRun.current = false
      setCompiling(false)
      const queued = queuedCompileSource.current
      queuedCompileSource.current = undefined
      if (queued && !lifecycle.current?.signal.aborted) void compileCurrent(queued)
    }
  }

  useEffect(() => {
    if (layout.view !== 'compare' || layout.compareText || !proposal) return
    if (proposal.beforePath && proposal.afterPath) return
    if (compare?.id !== proposal.id) void comparePdfs(proposal)
  }, [layout.view, layout.compareText, proposal?.id])

  function changeSource(source: string) {
    const previous = latestDraft.current
    const selections = adjustSelections(previous.source, source, previous.selections)
    updateDraft({ ...previous, source, selections, activeSelectionId: selections.some(item => item.id === previous.activeSelectionId) ? previous.activeSelectionId : null })
  }

  function markSource() {
    const { start, end } = sourceRange
    if (start === end) return
    const previous = latestDraft.current
    const startLine = previous.source.slice(0, start).split('\n').length
    const endLine = previous.source.slice(0, end).split('\n').length
    const selection = previous.selections.find(item => item.start === start && item.end === end) ?? {
      id: crypto.randomUUID(), start, end, label: startLine === endLine ? `源码第 ${startLine} 行` : `源码第 ${startLine}–${endLine} 行`,
    }
    const selections = previous.selections.some(item => item.id === selection.id) ? previous.selections : [...previous.selections, selection]
    updateDraft({ ...previous, selections, activeSelectionId: selection.id })
  }

  function selectSource(id: string | null) {
    const previous = latestDraft.current
    updateDraft({ ...previous, activeSelectionId: id })
    const match = previous.selections.find(item => item.id === id)
    if (match && sourceArea.current) {
      sourceArea.current.focus()
      sourceArea.current.setSelectionRange(match.start, match.end)
    }
  }

  function handleSourceKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Tab' || event.shiftKey) return
    event.preventDefault()
    const { selectionStart, selectionEnd } = event.currentTarget
    const source = latestDraft.current.source
    changeSource(`${source.slice(0, selectionStart)}  ${source.slice(selectionEnd)}`)
    requestAnimationFrame(() => sourceArea.current?.setSelectionRange(selectionStart + 2, selectionStart + 2))
  }

  function addCapture(item: EditorAttachment) {
    const capture = item.panel === 'preview' && preview.path ? { ...item, previewPath: preview.path } : item
    setAttachments(previous => previous.length >= 8 ? previous : [...previous, capture])
    if (attachments.length >= 8) setChatStatus('每轮最多可附带 8 张截图或参考图片。')
    if (layout.mode === 'review') setLayout(previous => ({ ...previous, mode: 'split' }))
  }

  function annotatePdf(item: EditorAttachment, note: string) {
    addCapture(item)
    if (attachments.length < 8) {
      setPrompt(previous => previous.trim() ? `${previous.trimEnd()}\n${note}` : note)
    }
  }

  async function attachFiles(files: File[]) {
    try {
      const added = await Promise.all(files.filter(file => /image\/(?:png|jpeg|webp)/.test(file.type)).map(readAttachment))
      if (attachments.length + added.length > 8) setChatStatus('每轮最多可附带 8 张截图或参考图片。')
      setAttachments(previous => [...previous, ...added].slice(0, 8))
    } catch (error) { setChatStatus(errorMessage(error)) }
  }

  async function requestAgent(method: 'workspace.chat' | 'workspace.fill_solution', selectionOnly = false) {
    if (chatRun.current) return
    const fillSolution = method === 'workspace.fill_solution'
    const text = fillSolution ? '补充答案与解析' : prompt.trim() || '根据所附截图检查当前题目，并给出需要的修改。'
    const pendingMessage: EditorMessage = { id: crypto.randomUUID(), role: 'user', text, attachments: fillSolution ? [] : attachments }
    const run: ChatRun = { controller: new AbortController(), message: pendingMessage, fillSolution }
    chatRun.current = run
    if (fillSolution) setLayout(previous => previous.mode === 'review' ? { ...previous, mode: 'split' } : previous)
    setPending(pendingMessage)
    setChatStatus('正在准备…')
    try {
      await persist()
      if (run.controller.signal.aborted) return
      const source = latestDraft.current.source
      const params: Record<string, unknown> = { id: document.id, source, revision: latestRecord.current.revision }
      if (!fillSolution) {
        setShowHistory(false)
        const screenshots = preview.source === source ? preciseCaptures.map(({ page, rect, panel, previewPath }) => ({ page, rect, panel, previewPath })) : []
        Object.assign(params, { prompt: text, attachments, scope: selectionOnly && (selected || screenshots.length) ? 'selection' : 'document' })
        if (boundary >= 0) params.historyAfter = historyAfter
        if (agent) params.agent = agent
        if (selectionOnly && selected) params.selection = { start: selected.start, end: selected.end }
        if (selectionOnly && screenshots.length) params.pdfSelections = screenshots
      }
      const job = await editorCall<{ taskId: string }>(method, params)
      run.id = job.taskId
      if (run.controller.signal.aborted) { await editorCall('task.cancel', { id: job.taskId }); return }
      if (!fillSolution) {
        setPrompt(value => value === prompt ? '' : value)
        setAttachments(items => items.filter(item => !attachments.some(sent => sent.id === item.id)))
      }
      setChatStatus(fillSolution ? 'Agent 正在补充答案与解析…' : '正在生成回复…')
      const task = await pollEditorTask<EditorDocument>(job.taskId, run.controller.signal, setChatStatus)
      if (run.controller.signal.aborted) return
      if (task.status === 'succeeded' && task.result) {
        accept(task.result)
        setChatStatus('')
      } else {
        setChatStatus(task.error || task.message || (task.status === 'cancelled' ? '已停止生成' : '发送失败'))
        if (!fillSolution) {
          setPrompt(value => value || prompt)
          setAttachments(items => items.length ? items : attachments)
        }
        accept(await editorCall<EditorDocument>('workspace.open', { id: document.id }))
      }
    } catch (error) {
      if (!run.controller.signal.aborted) {
        setChatStatus(errorMessage(error))
        if (!fillSolution) {
          setPrompt(value => value || prompt)
          setAttachments(items => items.length ? items : attachments)
        }
      }
    } finally {
      if (chatRun.current === run) { chatRun.current = null; setPending(undefined) }
    }
  }

  async function stopChat() {
    const run = chatRun.current
    if (!run) return
    run.controller.abort()
    chatRun.current = null
    setPending(undefined)
    setChatStatus('已停止生成')
    if (!run.fillSolution) {
      setPrompt(value => value || run.message.text)
      setAttachments(items => items.length ? items : run.message.attachments ?? [])
    }
    if (run.id) {
      try {
        await editorCall('task.cancel', { id: run.id })
        accept(await editorCall<EditorDocument>('workspace.open', { id: document.id }))
      }
      catch (error) { setChatStatus(errorMessage(error)) }
    }
  }

  async function updateProposal(operation: 'apply' | 'dismiss' | 'undo', item?: EditorProposal) {
    if (proposalBusy) return
    setProposalBusy(true)
    try {
      await persist()
      const next = await editorCall<EditorDocument>(`workspace.${operation}`, {
        id: document.id,
        revision: latestRecord.current.revision,
        ...(item ? { proposalId: item.id } : {}),
        ...(operation === 'apply' ? { source: latestDraft.current.source } : {}),
      })
      accept(next, operation !== 'dismiss')
      setChatStatus({ apply: '已采用提案', dismiss: '已放弃提案', undo: '已撤销上次修改' }[operation])
      if (operation !== 'dismiss') {
        setPreview({ path: next.previewPath, source: next.previewCurrent === false ? undefined : next.source })
        if (!next.previewPath) void compileCurrent(next.source)
      }
    } catch (error) { setChatStatus(errorMessage(error)); setNotice(errorMessage(error)) }
    finally { setProposalBusy(false) }
  }

  function compareProposal(item: EditorProposal, revealChat = false) {
    setProposalId(item.id)
    changeView('compare')
    if (revealChat) setLayout(previous => ({ ...previous, mode: 'split' }))
  }

  useEffect(() => {
    if (!reviewProposalId) return
    const item = document.proposals?.find(candidate => candidate.id === reviewProposalId)
    if (!item) return
    const noLocalEdits = JSON.stringify(latestDraft.current) === JSON.stringify(documentDraft(latestRecord.current))
    accept(document, noLocalEdits && document.source !== latestRecord.current.source)
    compareProposal(item, true)
  }, [reviewProposalId])

  async function comparePdfs(item: EditorProposal) {
    compareRun.current?.abort()
    const controller = new AbortController()
    compareRun.current = controller
    setCompare({ id: item.id, loading: true, before: item.beforePath ?? undefined, after: item.afterPath ?? undefined })
    try {
      const render = async (target: 'before' | 'after', source: string, existing?: string | null) => existing || (await runTask<EditorCompileResult>('workspace.compile', { source, target }, controller.signal)).previewPath
      const [before, after] = await Promise.allSettled([render('before', item.baseSource, item.beforePath), render('after', item.source, item.afterPath)])
      if (controller.signal.aborted) return
      const errors = [before, after].flatMap(result => result.status === 'rejected' ? [errorMessage(result.reason)] : [])
      setCompare({ id: item.id, loading: false, before: before.status === 'fulfilled' ? before.value : undefined, after: after.status === 'fulfilled' ? after.value : undefined, error: errors.join('\n') })
    } catch (error) {
      if (!controller.signal.aborted) setCompare({ id: item.id, loading: false, error: errorMessage(error) })
    }
  }

  function changeView(view: View) {
    setLocateMode(false)
    setAnnotationCommands({})
    setUtility(undefined)
    setFind({ query: '', index: 0 })
    setLayout(previous => ({ ...previous, mode: previous.mode === 'chat' ? 'split' : previous.mode, view, pair: undefined }))
  }

  function changePair(view: 'source' | 'original') {
    setLocateMode(false)
    setAnnotationCommands({})
    setUtility(undefined)
    setFind({ query: '', index: 0 })
    setFocusedPdf('preview')
    setLayout(previous => ({ ...previous, mode: previous.mode === 'chat' ? 'split' : previous.mode, view, pair: 'preview' }))
  }

  function focusChat() {
    setLayout(previous => previous.mode === 'review' ? { ...previous, mode: 'split' } : previous)
    requestAnimationFrame(() => chatPane.current?.querySelector('textarea')?.focus())
  }

  function choosePdfTool(next: PdfTool) {
    setLocateMode(false)
    setTool(next)
  }

  function armSourceLocate() {
    if (locateMode) {
      choosePdfTool('select')
      return
    }
    if (!preview.path || preview.source !== draft.source) {
      setNotice('请先编译当前 LaTeX，再在 PDF 中框选要定位的内容。')
      return
    }
    setNotice('')
    changeView('preview')
    setFocusedPdf('preview')
    setTool('crop')
    setLocateMode(true)
  }

  async function locateSource(capture: EditorAttachment) {
    if (!locateMode) {
      addCapture(capture)
      return
    }
    if (capture.panel !== 'preview' || !capture.page || !capture.rect || !preview.path) return
    const source = latestDraft.current.source
    choosePdfTool('select')
    setLocatingSource(true)
    try {
      const saved = await persist()
      const span = await runTask<{ start: number; end: number }>('workspace.locate', {
        revision: saved.revision, previewPath: preview.path, page: capture.page, rect: capture.rect,
      })
      if (lifecycle.current?.signal.aborted) return
      if (latestDraft.current.source !== source) throw new Error('源码已修改，请重新编译后框选。')
      changePair('source')
      requestAnimationFrame(() => {
        const editor = sourceArea.current
        if (!editor) return
        editor.focus()
        editor.setSelectionRange(span.start, span.end)
        setSourceRange(span)
        const lineHeight = Number.parseFloat(getComputedStyle(editor).lineHeight)
        editor.scrollTop = (source.slice(0, span.start).split('\n').length - 1) * lineHeight - editor.clientHeight / 3
      })
    } catch (error) { setNotice(errorMessage(error)) }
    finally { setLocatingSource(false) }
  }

  function startNewTask() {
    const last = messages.at(-1)
    setHistoryAfter(last?.id)
    storeValue(historyKey, last?.id)
    setShowHistory(false)
    setChatStatus('')
    focusChat()
  }

  function jumpToPage(panel: PdfPanel, page: number) {
    const count = pdfViews[panel]?.pageCount
    if (!count || !Number.isFinite(page)) return
    const target = Math.max(1, Math.min(count, Math.round(page)))
    setNavigation(previous => ({ ...previous, [panel]: { page: target, stamp: ++navigationStamp.current } }))
    setPdfPosition(panel, target, 0, true)
  }

  function changeZoom(panel: PdfPanel, zoom: number | 'width' | 'page') {
    const fit = typeof zoom === 'number' ? 'custom' : zoom
    const factor = typeof zoom === 'number' ? zoom : 1
    setLayout(previous => ({
      ...previous,
      pdfFit: { ...previous.pdfFit, [panel]: fit },
      pdf: { ...previous.pdf, [panel]: { ...previous.pdf[panel], zoom: factor } },
    }))
  }

  function toggleBookmark(panel: PdfPanel, page: number) {
    const pages = bookmarks[panel] ?? []
    const updated = { ...bookmarks, [panel]: pages.includes(page) ? pages.filter(item => item !== page) : [...pages, page].sort((a, b) => a - b) }
    setBookmarks(updated)
    storeValue(bookmarkKey, updated)
  }

  function controlAnnotation(type: 'undo' | 'redo') {
    if (activePdf) setAnnotationCommands(previous => ({ ...previous, [activePdf]: { type, stamp: ++navigationStamp.current } }))
  }

  function setPdfPosition(panel: PdfPanel, page: number, offset: number, fromScroll: boolean) {
    setLayout(previous => {
      if (previous.pdf[panel].page === page && Math.abs(previous.pdf[panel].offset - offset) < 0.002) return previous
      return { ...previous, pdf: { ...previous.pdf, [panel]: { ...previous.pdf[panel], page, offset } } }
    })
    if (!fromScroll || !layout.sync) return
    const peers: (View | PdfPanel | undefined)[] = layout.view === 'compare' ? ['before', 'after'] : [layout.view, layout.pair]
    const target = peers.find(name => name && name !== panel && ['preview', 'original', 'before', 'after'].includes(name)) as PdfPanel | undefined
    if (!target) return
    const originalOffset = Math.max(0, (record.originalPage ?? 1) - 1)
    const targetPage = target === 'original' ? page + originalOffset : panel === 'original' ? page - originalOffset : page
    setNavigation(previous => ({ ...previous, [target]: { page: Math.max(1, targetPage), offset, stamp: ++navigationStamp.current } }))
  }

  function handleDocumentResize(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    const divider = event.currentTarget
    const parent = divider.parentElement
    if (!parent) return
    event.preventDefault()
    divider.setPointerCapture(event.pointerId)
    const move = (moveEvent: globalThis.PointerEvent) => {
      const bounds = parent.getBoundingClientRect()
      if (bounds.width > 0) {
        const width = (moveEvent.clientX - bounds.left) * 100 / bounds.width
        setLayout(previous => ({ ...previous, documentWidth: Math.max(22, Math.min(78, width)) }))
      }
    }
    const stop = () => { divider.removeEventListener('pointermove', move); divider.removeEventListener('lostpointercapture', stop) }
    divider.addEventListener('pointermove', move)
    divider.addEventListener('lostpointercapture', stop)
  }

  function documentDivider() {
    return <div className="wb-divider" role="separator" aria-label="调整两栏宽度" aria-orientation="vertical" tabIndex={0} onPointerDown={handleDocumentResize} onKeyDown={event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      event.preventDefault()
      const direction = event.key === 'ArrowLeft' ? -1 : 1
      setLayout(previous => ({ ...previous, documentWidth: Math.max(22, Math.min(78, previous.documentWidth + direction * 3)) }))
    }} />
  }

  function renderPdf(panel: PdfPanel, path: string | null | undefined, label: string, empty: string, waiting = false) {
    const currentTool = panel === activePdf ? tool : 'select'
    return <>
      {pdfPanels.length > 1 && <div className="wb-panel-caption" data-active={panel === activePdf || undefined} onClick={() => setFocusedPdf(panel)}><button type="button" className="wb-btn" aria-pressed={panel === activePdf}>{label}</button></div>}
      <EditorPdf path={path} label={label} panel={panel} position={layout.pdf[panel]} fit={layout.pdfFit?.[panel]} navigation={navigation[panel]} pending={waiting} empty={empty}
        hideToolbar tool={currentTool} onToolChange={choosePdfTool} crop={currentTool === 'crop'} onCropToggle={() => choosePdfTool(tool === 'crop' ? 'select' : 'crop')}
        search={utility === 'find' && panel === activePdf ? find : undefined} onViewState={rememberPdfView[panel]} onFocusPanel={() => setFocusedPdf(panel)}
        onCapture={capture => void locateSource(capture)} annotationKey={record.id} onAnnotation={annotatePdf} annotationAction={annotationCommands[panel]}
        onNavigate={(page, offset, fromScroll) => setPdfPosition(panel, page, offset, fromScroll)}
        onZoom={zoom => changeZoom(panel, zoom)} />
    </>
  }

  function renderView(view: Exclude<View, 'compare'>) {
    if (view === 'preview') return renderPdf('preview', preview.path, 'PDF 预览', draft.source.trim() ? '点击「编译 PDF」生成预览。' : `录入 LaTeX 或识别${originalLabel}后即可编译 PDF。`, compiling)
    if (view === 'original') return renderPdf('original', record.pdfPath, originalLabel, '这道题没有附带原卷 PDF。')
    return <section className="wb-source" aria-label="LaTeX 源码">
      <div className="wb-pdf-tools"><span className="wb-pdf-label">LaTeX 源码</span><div className="wb-pdf-actions">
        {!!draft.selections.length && <select aria-label="已标记源码选段" value={draft.activeSelectionId ?? ''} onChange={event => selectSource(event.target.value || null)}>
          <option value="">已标记选段</option>{draft.selections.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>}
        <button type="button" className="wb-btn" disabled={sourceRange.start === sourceRange.end} onClick={markSource}>标记选段</button>
      </div></div>
      <div className="wb-source-editor">
        <div className="wb-line-rail" aria-hidden="true" ref={lineNumbers}>{sourceLines}</div>
        <textarea aria-label="编辑 LaTeX 源码" spellCheck={false} wrap="off" value={draft.source} placeholder="在此录入或修改 LaTeX 源码…"
          ref={attachSource}
          onScroll={event => {
            const top = event.currentTarget.scrollTop
            sourceScroll.current = top
            if (lineNumbers.current) lineNumbers.current.scrollTop = top
            setLayout(previous => previous.sourceScroll === top ? previous : { ...previous, sourceScroll: top })
          }}
          onSelect={event => setSourceRange({ start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd })}
          onChange={event => changeSource(event.target.value)} onKeyDown={handleSourceKey} />
      </div>
    </section>
  }

  function renderComparison() {
    if (!proposal) return <div className="wb-empty"><WorkbenchIcon name="split" size={32} /><p>有修改提案后，可在这里查看修改前后的对照。</p></div>
    const currentComparison = compare?.id === proposal.id ? compare : undefined
    return <div className="wb-comparison">
      <div className="wb-comparison-tools">
        <select aria-label="选择修改提案" value={proposal.id} onChange={event => setProposalId(event.target.value)}>{proposals.map((item, index) => <option key={item.id} value={item.id}>提案 {index + 1}</option>)}</select>
        <button type="button" className="wb-btn" aria-pressed={layout.compareText} onClick={() => setLayout(previous => ({ ...previous, compareText: true }))}>源码对照</button>
        <button type="button" className="wb-btn" aria-pressed={!layout.compareText} onClick={() => {
          setLayout(previous => ({ ...previous, compareText: false }))
          if (!currentComparison?.loading && !(currentComparison?.before && currentComparison?.after) && !(proposal.beforePath && proposal.afterPath)) void comparePdfs(proposal)
        }}>PDF 对照</button>
        {!layout.compareText && <label className="wb-sync"><input type="checkbox" checked={layout.sync} onChange={event => setLayout(previous => ({ ...previous, sync: event.target.checked }))} /> 联动翻页</label>}
        {proposal.status === 'pending' && <div className="wb-comparison-resolve" aria-label="审阅修改提案">
          <button type="button" className="wb-btn" disabled={proposalBusy || Boolean(pending)} onClick={() => void updateProposal('dismiss', proposal)}>放弃</button>
          <button type="button" className="wb-btn wb-primary" disabled={proposalBusy || Boolean(pending) || proposal.baseSource !== draft.source}
            title={proposal.baseSource !== draft.source ? '源码已修改，请重新生成修改提案。' : undefined}
            onClick={() => void updateProposal('apply', proposal)}>采用</button>
        </div>}
      </div>
      {!layout.compareText && currentComparison?.error && <div className="wb-notice" role="status">部分对照预览编译失败。 <button type="button" className="wb-btn" onClick={() => { setCompileLog(currentComparison.error ?? ''); setShowLog(true) }}>编译日志</button></div>}
      <div className="wb-document-panes">
        <div className="wb-document-panel" style={{ flexBasis: `${layout.documentWidth}%` }}>{layout.compareText ? <DiffSource label="修改前" text={proposal.changes?.map(item => item.before).join('\n\n') ?? proposal.before} side="before" /> : renderPdf('before', currentComparison?.before || proposal.beforePath, '修改前', '此版本没有 PDF 预览。', currentComparison?.loading)}</div>
        {documentDivider()}
        <div className="wb-document-panel wb-document-panel-grow">{layout.compareText ? <DiffSource label="修改后" text={proposal.changes?.map(item => item.after).join('\n\n') ?? proposal.after} side="after" /> : renderPdf('after', currentComparison?.after || proposal.afterPath, '修改后', '此版本没有 PDF 预览。', currentComparison?.loading)}</div>
      </div>
    </div>
  }

  function openSheet(kind: Sheet) {
    setSheetError('')
    setSheet(kind)
    if (kind === 'library') void editorCall<string[]>('workspace.chapters').then(setChapters).catch(error => setSheetError(errorMessage(error)))
  }

  async function recognize(firstPage: number, lastPage: number) {
    if (recognitionRun.current) return
    recognitionRun.current = true
    setReconstructing(true)
    setSheet(undefined)
    setRecognitionProgress('')
    setNotice('')
    try {
      await persist()
      const next = await runTask<EditorDocument>('workspace.reconstruct', { firstPage, lastPage }, undefined, setRecognitionProgress)
      const sourceChanged = next.source !== latestRecord.current.source
      const userEdited = latestDraft.current.source !== latestRecord.current.source
      accept(next, sourceChanged && !userEdited)
      setNotice('识别完成')
      if (sourceChanged && !userEdited) {
        setLayout(previous => ({ ...previous, view: imageInput ? 'source' : 'original', pair: 'preview' }))
        setNavigation({ original: { page: next.originalPage ?? firstPage, stamp: ++navigationStamp.current }, preview: { page: 1, stamp: ++navigationStamp.current } })
        void compileCurrent(next.source)
      } else if (next.proposals?.length) {
        const newest = next.proposals.at(-1)
        if (newest) compareProposal(newest)
      }
    } catch (error) {
      setNotice(errorMessage(error))
    } finally {
      recognitionRun.current = false
      setReconstructing(false)
    }
  }

  function startRecognition() {
    if (imageInput) void recognize(1, 1)
    else openSheet('recognize')
  }

  async function submitSheet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (sheetBusy) return
    const data = new FormData(event.currentTarget)
    if (sheet === 'recognize') {
      const firstPage = Number(data.get('firstPage'))
      const lastPage = Number(data.get('lastPage'))
      if (!Number.isInteger(firstPage) || !Number.isInteger(lastPage) || lastPage < firstPage) {
        setSheetError('请填写有效的页码范围。')
        return
      }
      await recognize(firstPage, lastPage)
      return
    }
    setSheetBusy(true)
    setSheetError('')
    try {
      await persist()
      const tags = String(data.get('tags') ?? '').split(/[,，、]/).map(value => value.trim()).filter(Boolean)
      const result = await runTask<BankExport>('workspace.export', { source: latestDraft.current.source, chapter: data.get('chapter'), title: data.get('title'), kind: data.get('kind'), tags })
      const failures = result.files.filter(item => item.status !== 'success')
      if (!failures.length) {
        setSheet(undefined)
        setNotice(`已另存 ${result.files.length} 道题到题库`)
      } else {
        setSheetError([`已另存 ${result.files.length - failures.length} 道题。`, ...failures.map(item => `${item.file}　${item.message}`)].join('\n'))
      }
    } catch (error) {
      setSheetError(errorMessage(error))
    } finally { setSheetBusy(false) }
  }

  async function exportTex() {
    try {
      const path = await window.utopia.saveFile('tex', `${latestDraft.current.title}.tex`)
      if (!path) return
      await persist()
      await editorCall('workspace.export_tex', { id: document.id, path, source: latestDraft.current.source })
      setNotice('已导出 LaTeX')
    } catch (error) { setNotice(errorMessage(error)) }
  }

  async function saveQuestion() {
    setSavingQuestion(true)
    setNotice('')
    setCompileLog('')
    try {
      const saved = await persist()
      const next = await editorCall<EditorDocument>('workspace.save_question', { id: document.id, revision: saved.revision })
      accept(next)
      onQuestionSaved?.(next)
      setNotice('已保存原题')
    } catch (error) { setNotice(errorMessage(error)) }
    finally { setSavingQuestion(false) }
  }

  async function exportPdf() {
    try {
      const path = await window.utopia.saveFile('pdf', `${latestDraft.current.title}.pdf`)
      if (!path) return
      setExporting(true)
      await persist()
      await runTask('workspace.export_pdf', { path, source: latestDraft.current.source })
      setNotice('已导出 PDF')
    } catch (error) { setNotice(errorMessage(error)) }
    finally { setExporting(false) }
  }

  async function close() {
    try { await persist(); storeValue(layoutKey, layout); onClose() }
    catch { /* Keep the editor open with its failed save visible. */ }
  }

  async function editFileInfo() {
    try { onEditFileInfo?.(await persist()) }
    catch { /* The save status already shows the error. */ }
  }

  async function transferPapilot(open: boolean) {
    if (papilotBusy) return
    setPapilotBusy(true)
    setNotice('')
    try {
      const saved = await persist()
      const next = open
        ? await window.utopia.papilotOpenQuestion(saved.id, saved.revision)
        : await editorCall<EditorDocument>('workspace.papilot_import', { id: saved.id, revision: saved.revision })
      if (open) {
        accept(next)
        onOpenPapilot?.()
      } else {
        const sourceChanged = next.source !== saved.source
        accept(next, sourceChanged)
        setNotice(sourceChanged ? '已将 Papilot 项目的改动导入题库草稿。' : 'Papilot 项目没有新的源码改动。')
        if (sourceChanged) void compileCurrent(next.source)
      }
    } catch (error) { setNotice(errorMessage(error)) }
    finally { setPapilotBusy(false) }
  }

  const pdfReady = Boolean(pdfView?.pageCount) && layout.mode !== 'chat'
  const buildUtilityAction = (id: Utility, label: string, icon: WorkbenchAction['icon']): WorkbenchAction => ({
    id, label, icon, pressed: utility === id, disabled: !pdfReady && (id !== 'notes' || !draft.selections.length),
    onClick: () => setUtility(value => value === id ? undefined : id),
  })
  const toolbarActions = [buildUtilityAction('notes', '查看批注和标记', 'note')]
  const pdfActions: WorkbenchAction[] = pdfTools.map(([id, label, icon]) => ({
    id, label, icon, disabled: !pdfReady, pressed: !locateMode && tool === id, onClick: () => choosePdfTool(id),
  }))
  pdfActions.push(
    buildUtilityAction('find', '搜索 PDF', 'find'),
    { id: 'locate-source', label: '同步定位源码', icon: 'sync', disabled: locatingSource || !draft.source.trim(), pressed: locateMode, onClick: armSourceLocate },
    { id: 'show-source', label: '查看 LaTeX 源码', icon: 'source', pressed: layout.view === 'source', onClick: () => changeView('source') },
  )
  const layoutActions: WorkbenchAction[] = [
    { id: 'layout-pdf', label: '仅看题目', icon: 'file', pressed: layout.mode === 'review', onClick: () => setLayout(previous => ({ ...previous, mode: 'review' })) },
    { id: 'layout-split', label: '题目与聊天分栏', icon: 'split', pressed: layout.mode === 'split', onClick: () => setLayout(previous => ({ ...previous, mode: 'split' })) },
    { id: 'layout-chat', label: '仅看聊天', icon: 'chat', pressed: layout.mode === 'chat', onClick: () => setLayout(previous => ({ ...previous, mode: 'chat' })) },
    { id: 'source', label: '查看 LaTeX 源码', icon: 'source', pressed: layout.view === 'source' && !layout.pair, onClick: () => changeView('source') },
    { id: 'original', label: `查看${originalLabel}`, icon: 'file', pressed: layout.view === 'original' && !layout.pair, disabled: !record.pdfPath, onClick: () => changeView('original') },
    { id: 'source-pdf', label: 'LaTeX ＋ PDF 预览', icon: 'split', pressed: layout.view === 'source' && !!layout.pair, onClick: () => changePair('source') },
    { id: 'original-pdf', label: `${originalLabel} ＋ 重建 PDF`, icon: 'split', pressed: layout.view === 'original' && !!layout.pair, disabled: !record.pdfPath, onClick: () => changePair('original') },
    ...(layout.pair ? [{ id: 'single-document', label: '关闭并排', onClick: () => changeView(layout.view) }] : []),
    ...(layout.view === 'original' && layout.pair === 'preview' ? [{
      id: 'sync', label: '联动翻页', icon: 'sync' as const, pressed: layout.sync,
      onClick: () => setLayout(previous => ({ ...previous, sync: !previous.sync })),
    }] : []),
  ]
  const fileActions: WorkbenchAction[] = [
    { id: 'rename', label: '编辑题目标题', onClick: () => setRenaming(true) },
    ...(onOpenPapilot ? [{ id: 'papilot', label: '在 Papilot 工作台打开', disabled: papilotBusy || !draft.source.trim(), onClick: () => void transferPapilot(true) }] : []),
    ...(record.papilotProject ? [{ id: 'papilot-import', label: '从 Papilot 导入改动', disabled: papilotBusy, onClick: () => void transferPapilot(false) }] : []),
    ...(onEditFileInfo ? [{ id: 'file-info', label: '修改文件信息', onClick: () => void editFileInfo() }] : []),
    { id: 'save', label: savingQuestion ? '正在保存…' : record.questionPath ? '保存原题' : '另存入库', icon: 'save',
      disabled: librarySaveDisabled || Boolean(record.questionPath && pending), onClick: () => { if (record.questionPath) void saveQuestion(); else openSheet('library') } },
    ...(record.questionPath ? [{ id: 'save-copy', label: '另存入库', disabled: librarySaveDisabled, onClick: () => openSheet('library') }] : []),
    ...(record.pdfPath ? [{ id: 'recognize', label: reconstructing ? '正在识别…' : recognitionLabel, disabled: reconstructing, onClick: startRecognition }] : []),
    ...(draft.source.trim() ? [{ id: 'fill-solution', label: pendingSolution ? '审阅答案与解析' : fillingSolution ? '正在补充…' : '补充答案与解析',
      disabled: !pendingSolution && (Boolean(pending) || reconstructing || savingQuestion),
      onClick: () => { if (pendingSolution) compareProposal(pendingSolution, true); else void requestAgent('workspace.fill_solution') } }] : []),
    { id: 'export-pdf', label: exporting ? '正在导出…' : '导出 PDF', icon: 'export', disabled: exporting || !draft.source.trim(), onClick: () => void exportPdf() },
    { id: 'export-tex', label: '导出 LaTeX', icon: 'export', disabled: !draft.source.trim(), onClick: () => void exportTex() },
    { id: 'undo-proposal', label: '撤销上次采用', icon: 'undo', disabled: !record.canUndo || proposalBusy, onClick: () => void updateProposal('undo') },
    ...(docked && onExpand ? [{ id: 'expand', label: '展开编辑工作区', icon: 'expand' as const, onClick: onExpand }] : []),
  ]
  if (record.pdfPath && !draft.source.trim()) {
    toolbarActions.unshift({ id: 'recognize', label: reconstructing ? '正在识别…' : recognitionLabel, icon: 'refresh', disabled: reconstructing, onClick: startRecognition })
  }

  return <div className={`wb-workbench wb-mode-${layout.mode}${docked ? ' wb-docked' : ''}`}>
    <WorkbenchToolbar title={`${draft.title} · ${saveState || (changed ? '保存中…' : '工作稿已保存')}`} view={layout.view === 'compare' ? 'changes' : activePdf ? 'pdf' : 'source'}
      onPdf={() => changeView(mainPdfView)} onChanges={() => changeView('compare')} changeCount={proposals.filter(item => item.status === 'pending').length}
      pdf={activePdf && layout.mode !== 'chat' ? {
        page: layout.pdf[activePdf].page, pageCount: pdfView?.pageCount ?? 0, outline: pdfView?.outline ?? [],
        zoom: layout.pdf[activePdf].zoom, fit: layout.pdfFit?.[activePdf] ?? (layout.pdf[activePdf].zoom === 1 ? 'width' : 'custom'),
        onPage: page => jumpToPage(activePdf, page), onZoom: zoom => changeZoom(activePdf, zoom),
      } : undefined}
      status={saveState || (changed ? '保存中…' : undefined)} actions={toolbarActions} fileActions={fileActions} layoutActions={layoutActions}
      compile={{ id: 'compile', label: compiling ? '正在编译…' : '编译 PDF', disabled: compiling || !draft.source.trim(), onClick: () => void compileCurrent() }}
      chat={{ id: 'chat', label: layout.mode === 'review' ? '展开题目助手' : '收起题目助手', pressed: layout.mode !== 'review', onClick: () => {
        if (layout.mode === 'review') focusChat()
        else setLayout(previous => ({ ...previous, mode: 'review' }))
      } }}
      newTask={{ id: 'new-task', label: '新任务', disabled: Boolean(pending), onClick: startNewTask }} />
    {notice && <div className="wb-notice" role="status"><span>{notice}</span>{compileLog && <button type="button" className="wb-btn" onClick={() => setShowLog(true)}>编译日志</button>}<button type="button" className="wb-icon" aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}
    {(locateMode || locatingSource) && <div className="wb-notice" role="status">{locatingSource ? '正在定位对应的 LaTeX 源码…' : '在 PDF 上框选内容，查看对应的 LaTeX 源码。'}
      {locateMode && <button type="button" className="wb-icon" aria-label="取消定位源码" onClick={() => choosePdfTool('select')}>×</button>}
    </div>}
    {reconstructing && <div className="wb-notice" role="status">{recognitionProgress || (imageInput ? '正在识别图片…' : '正在识别所选页面，可继续查看原卷。')}</div>}
    <div className="wb-body">
      <WorkbenchRail actions={pdfActions}
        history={[
          { id: 'undo-mark', label: '撤销 PDF 标记', icon: 'undo', disabled: !pdfReady || !pdfView?.canUndo, onClick: () => controlAnnotation('undo') },
          { id: 'redo-mark', label: '重做 PDF 标记', icon: 'redo', disabled: !pdfReady || !pdfView?.canRedo, onClick: () => controlAnnotation('redo') },
          { id: 'task-history', label: showHistory ? '返回当前任务' : '查看聊天历史', icon: 'chat', disabled: boundary < 0, pressed: showHistory, onClick: () => { setShowHistory(previous => !previous); focusChat() } },
        ]}
        bookmarks={[
          buildUtilityAction('bookmarks', '页面书签', 'bookmarks'),
          { id: 'close', label: docked ? '关闭题目详情' : '返回', icon: docked ? 'close' : 'back', onClick: () => void close() },
        ]} />
      <main className="wb-document" hidden={layout.mode === 'chat'}>
        {utility === 'find' && activePdf && <div className="wb-document-utility wb-document-find">
          <WorkbenchFind query={find.query} index={pdfView?.matches ? find.index % pdfView.matches : 0} count={pdfView?.matches ?? 0} loading={pdfView?.searching}
            onQuery={query => setFind({ query, index: 0 })} onClose={() => setUtility(undefined)} onNavigate={delta => {
              const count = pdfView?.matches ?? 0
              if (count) setFind(previous => ({ ...previous, index: (previous.index + delta + count) % count }))
            }} />
        </div>}
        {utility === 'bookmarks' && activePdf && pdfReady && <div className="wb-document-utility">
          <WorkbenchBookmarks pages={(bookmarks[activePdf] ?? []).filter(page => page <= (pdfView?.pageCount ?? 0))} page={layout.pdf[activePdf].page}
            onPage={page => jumpToPage(activePdf, page)} onToggle={() => toggleBookmark(activePdf, layout.pdf[activePdf].page)} onClose={() => setUtility(undefined)} />
        </div>}
        {utility === 'notes' && <section className="wb-document-utility wb-note-list" aria-label="批注和标记">
          <header><strong>批注和标记</strong><button type="button" className="wb-icon" aria-label="关闭批注" onClick={() => setUtility(undefined)}>×</button></header>
          {activePdf && pdfView?.notes.map(note => <button key={note.id} type="button" className="wb-btn" onClick={() => {
            jumpToPage(activePdf, note.page)
            setAnnotationCommands(previous => ({ ...previous, [activePdf]: { type: 'open', id: note.id, stamp: ++navigationStamp.current } }))
            setUtility(undefined)
          }}>第 {note.page} 页　{note.label}</button>)}
          {draft.selections.map(selection => <button key={selection.id} type="button" className="wb-btn" onClick={() => {
            changeView('source')
            requestAnimationFrame(() => selectSource(selection.id))
          }}>{selection.label}</button>)}
          {!pdfView?.notes.length && !draft.selections.length && <p>还没有批注。可用左侧的 PDF 批注、高亮标记和魔法笔添加。</p>}
        </section>}
        {layout.view === 'compare' ? renderComparison() : layout.pair ? <div className="wb-document-panes">
          <div className="wb-document-panel" style={{ flexBasis: `${layout.documentWidth}%` }}>{renderView(layout.view)}</div>
          {documentDivider()}
          <div className="wb-document-panel wb-document-panel-grow">{renderView(layout.pair)}</div>
        </div> : renderView(layout.view)}
      </main>
      {layout.mode === 'split' && <PaneDivider open panelId={chatPanelId} label="题目助手"
        width={layout.chatWidth} minWidth={chatMinWidth} maxWidth={chatMaxWidth} maxFraction={chatMaxFraction}
        onToggle={() => setLayout(previous => ({ ...previous, mode: previous.mode === 'split' ? 'review' : 'split' }))}
        onWidthChange={width => setLayout(previous => ({ ...previous, chatWidth: width }))} />}
      <aside ref={chatPane} id={chatPanelId} className="wb-chat-pane" hidden={layout.mode === 'review'} style={layout.mode === 'split' ? {
        width: `clamp(min(${chatMinWidth}px, ${chatMaxFraction * 100}%), ${layout.chatWidth}px, min(${chatMaxWidth}px, ${chatMaxFraction * 100}%))`,
      } : undefined}>
        <EditorChat messages={taskMessages} proposals={taskProposals} source={draft.source} attachments={attachments} selection={selected} prompt={prompt} status={chatStatus}
          agent={agent} onAgent={id => { setAgent(id); storeValue(agentKey, id) }}
          pending={pending} busy={Boolean(pending)} hasPdfSelection={preciseCaptures.length > 0} onPrompt={setPrompt} onAttach={files => void attachFiles(files)}
          onRemove={id => setAttachments(previous => previous.filter(item => item.id !== id))} onClearSelection={() => selectSource(null)} onSend={scope => void requestAgent('workspace.chat', scope)}
          onStop={() => void stopChat()} onApply={item => void updateProposal('apply', item)} onDismiss={item => void updateProposal('dismiss', item)} onCompare={compareProposal} />
      </aside>
    </div>
    {renaming && <EditorModal title="编辑题目标题" onClose={() => setRenaming(false)}>
      <form className="wb-form" onSubmit={event => {
        event.preventDefault()
        const title = String(new FormData(event.currentTarget).get('title') ?? '').trim()
        if (!title) return
        updateDraft({ ...latestDraft.current, title })
        setRenaming(false)
        void persist().catch(() => {})
      }}>
        <label>题目标题<input name="title" defaultValue={draft.title} required autoFocus /></label>
        <div className="wb-form-actions"><button type="button" className="wb-btn" onClick={() => setRenaming(false)}>取消</button><button type="submit" className="wb-btn wb-primary">保存标题</button></div>
      </form>
    </EditorModal>}
    {showLog && <EditorModal title="LaTeX 编译日志" onClose={() => setShowLog(false)}><pre className="wb-log">{compileLog}</pre></EditorModal>}
    {sheet && <EditorModal title={sheet === 'library' ? '另存入题库' : '识别原卷'} onClose={() => { if (!sheetBusy) setSheet(undefined) }}>
      <form className="wb-form" onSubmit={event => void submitSheet(event)}>
        {sheet === 'library' ? <>
          <label>题目标题<input name="title" defaultValue={draft.title} required /></label>
          <label>知识板块<select name="chapter" required defaultValue={record.chapter ?? ''}><option value="" disabled>{chapters.length ? '请选择题库小节' : '正在读取题库…'}</option>{chapters.map(chapter => <option key={chapter}>{chapter}</option>)}</select></label>
          <label>题型<select name="kind" required defaultValue=""><option value="" disabled>请选择题型</option>{questionKinds.map(kind => <option key={kind}>{kind}</option>)}</select></label>
          <label>标签<input name="tags" placeholder="用逗号分隔，可留空" /></label>
        </> : <div className="wb-page-range">
          <label>起始页<input name="firstPage" type="number" min={1} max={record.pdfPageCount} defaultValue={layout.pdf.original.page} required /></label>
          <label>结束页<input name="lastPage" type="number" min={1} max={record.pdfPageCount} defaultValue={layout.pdf.original.page} required /></label>
        </div>}
        {sheetError && <p className="wb-form-error" role="alert">{sheetError}</p>}
        <div className="wb-form-actions"><button type="button" className="wb-btn" disabled={sheetBusy} onClick={() => setSheet(undefined)}>取消</button><button type="submit" className="wb-btn wb-primary" disabled={sheetBusy}>{sheetBusy ? '处理中…' : sheet === 'library' ? '另存入库' : '开始识别'}</button></div>
      </form>
    </EditorModal>}
  </div>
}

function DiffSource({ label, text, side }: { label: string; text: string; side: 'before' | 'after' }) {
  return <section className="wb-source" aria-label={label}>
    <div className="wb-pdf-tools"><span className="wb-pdf-label">{label}</span></div>
    <div className={`wb-diff wb-diff-${side}`} tabIndex={0}><pre>{text || '（空白）'}</pre></div>
  </section>
}

function adjustSelections(before: string, after: string, selections: EditorSelection[]) {
  if (!selections.length) return selections
  const shortest = Math.min(before.length, after.length)
  let first = 0
  while (first < shortest && before[first] === after[first]) first++
  let suffix = 0
  while (suffix < shortest - first && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++
  const delta = after.length - before.length
  const replacedEnd = before.length - suffix
  return selections.flatMap(item => {
    if (item.end <= first) return [item]
    if (item.start >= replacedEnd) return [{ ...item, start: item.start + delta, end: item.end + delta }]
    return []
  })
}

async function readAttachment(file: File): Promise<EditorAttachment> {
  const image = await createImageBitmap(file)
  const canvas = window.document.createElement('canvas')
  const scale = Math.min(1, 1800 / Math.max(image.width, image.height))
  canvas.width = Math.max(1, Math.round(image.width * scale))
  canvas.height = Math.max(1, Math.round(image.height * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('无法读取参考图片')
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
  image.close()
  return { id: crypto.randomUUID(), title: file.name, dataUrl: canvas.toDataURL('image/jpeg', 0.88) }
}
