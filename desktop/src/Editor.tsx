import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent, PointerEvent } from 'react'
import { EditorChat, EditorModal } from './EditorChat'
import { EditorPdf } from './EditorPdf'
import type { PdfNavigation, PdfPosition } from './EditorPdf'
import { editorCall, errorMessage, pollEditorTask } from './editorContract'
import type { EditorAttachment, EditorCompileResult, EditorDocument, EditorMessage, EditorProposal, EditorSelection, PdfPanel } from './editorContract'
import './editor.css'

export type { EditorDocument } from './editorContract'

type Mode = 'review' | 'split' | 'chat'
type View = 'preview' | 'source' | 'original' | 'compare'
type Sheet = 'library' | 'recognize'

interface Props {
  document: EditorDocument
  onChange: (document: EditorDocument) => void
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
  sourceScroll: number
  compareText: boolean
}

interface ChatRun { controller: AbortController; id?: string; message: EditorMessage }
interface CompareState { id: string; before?: string; after?: string; loading: boolean; error?: string }
interface BankExport { files: { file: string; status: 'success' | 'skip' | 'error'; message: string }[] }

const modes: [Mode, string, string][] = [['review', '审阅', 'file'], ['split', '分屏', 'split'], ['chat', '聊天', 'chat']]
const views: [View, string][] = [['preview', 'PDF 预览'], ['source', 'LaTeX'], ['original', '原卷'], ['compare', '修改对照']]
const questionKinds = ['单项选择题', '多项选择题', '实验题', '计算题']
const activeChatDrafts = new Map<string, { prompt: string; attachments: EditorAttachment[] }>()

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

function initialLayout(document: EditorDocument): Layout {
  const position = (page = 1): PdfPosition => ({ page, offset: 0, zoom: 1 })
  return parseStored<Layout>(`utopia-workbench-layout-${document.id}`) ?? {
    mode: 'split', view: document.pdfPath && !document.source ? 'original' : 'preview', sync: false, chatWidth: 385, documentWidth: 50,
    pdf: { preview: position(), original: position(document.originalPage), before: position(), after: position() }, sourceScroll: 0, compareText: false,
  }
}

export default function Editor(props: Props) {
  return <Workbench key={props.document.id} {...props} />
}

export { Editor }

function Workbench({ document, onChange, onClose }: Props) {
  const draftKey = `utopia-workbench-draft-${document.id}`
  const layoutKey = `utopia-workbench-layout-${document.id}`
  const [record, setRecord] = useState(document)
  const [draft, setDraft] = useState(() => {
    const cached = parseStored<{ baseRevision?: number; draft: Draft }>(draftKey)
    return cached && cached.baseRevision === document.revision ? cached.draft : documentDraft(document)
  })
  const [layout, setLayout] = useState(() => initialLayout(document))
  const [preview, setPreview] = useState(() => ({ path: document.previewPath, source: document.previewCurrent === false ? undefined : document.source }))
  const [compiling, setCompiling] = useState(false)
  const [reconstructing, setReconstructing] = useState(false)
  const [recognitionProgress, setRecognitionProgress] = useState('')
  const [exporting, setExporting] = useState(false)
  const [saveState, setSaveState] = useState('')
  const [notice, setNotice] = useState('')
  const [compileLog, setCompileLog] = useState('')
  const [showLog, setShowLog] = useState(false)
  const [cropPanel, setCropPanel] = useState<PdfPanel>()
  const [navigation, setNavigation] = useState<Partial<Record<PdfPanel, PdfNavigation>>>({})
  const [attachments, setAttachments] = useState<EditorAttachment[]>(() => activeChatDrafts.get(document.id)?.attachments ?? [])
  const [prompt, setPrompt] = useState(() => activeChatDrafts.get(document.id)?.prompt ?? '')
  const [chatStatus, setChatStatus] = useState('')
  const [pending, setPending] = useState<EditorMessage>()
  const [proposalId, setProposalId] = useState<string>()
  const [compare, setCompare] = useState<CompareState>()
  const [sheet, setSheet] = useState<Sheet>()
  const [sheetBusy, setSheetBusy] = useState(false)
  const [sheetError, setSheetError] = useState('')
  const [chapters, setChapters] = useState<string[]>([])
  const [sourceRange, setSourceRange] = useState({ start: 0, end: 0 })
  const [commandsOpen, setCommandsOpen] = useState(false)
  const [pairOpen, setPairOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const sourceArea = useRef<HTMLTextAreaElement>(null)
  const lineNumbers = useRef<HTMLDivElement>(null)
  const sourceScroll = useRef(layout.sourceScroll)
  const latestDraft = useRef(draft)
  const latestRecord = useRef(record)
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve())
  const lifecycle = useRef<AbortController>(null)
  const chatRun = useRef<ChatRun>(null)
  const compareRun = useRef<AbortController>(null)
  const compileRun = useRef(false)
  const navigationStamp = useRef(0)
  const changed = JSON.stringify(draft) !== JSON.stringify(documentDraft(record))
  const selected = draft.selections.find(item => item.id === draft.activeSelectionId)
  const proposals = record.proposals ?? []
  const proposal = proposals.find(item => item.id === proposalId) ?? proposals.at(-1)
  const preciseCaptures = preview.source === draft.source
    ? attachments.filter(item => item.panel === 'preview' && item.previewPath === preview.path && item.rect && item.page)
    : []

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
    if (!document.previewPath && document.source.trim()) void compileCurrent()
    // A workspace with a saved preview opens directly; later edits compile on demand.
  }, [document.id])

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
      setPreview({ path: result.previewPath, source: result.source ?? source })
      accept(await editorCall<EditorDocument>('workspace.open', { id: document.id }))
    } catch (error) {
      const log = errorMessage(error)
      setCompileLog(log)
      setNotice('LaTeX 编译失败')
    } finally {
      compileRun.current = false
      setCompiling(false)
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

  async function attachFiles(files: File[]) {
    try {
      const added = await Promise.all(files.filter(file => /image\/(?:png|jpeg|webp)/.test(file.type)).map(readAttachment))
      if (attachments.length + added.length > 8) setChatStatus('每轮最多可附带 8 张截图或参考图片。')
      setAttachments(previous => [...previous, ...added].slice(0, 8))
    } catch (error) { setChatStatus(errorMessage(error)) }
  }

  async function sendChat(selectionOnly: boolean) {
    if (chatRun.current) return
    const text = prompt.trim() || '根据所附截图检查当前题目，并给出需要的修改。'
    const pendingMessage: EditorMessage = { id: crypto.randomUUID(), role: 'user', text, attachments }
    const run = { controller: new AbortController(), message: pendingMessage } as ChatRun
    chatRun.current = run
    setPending(pendingMessage)
    setChatStatus('正在准备…')
    try {
      await persist()
      if (run.controller.signal.aborted) return
      const source = latestDraft.current.source
      const screenshots = preview.source === source ? preciseCaptures.map(({ page, rect, panel, previewPath }) => ({ page, rect, panel, previewPath })) : []
      const scope = selectionOnly && (selected || screenshots.length) ? 'selection' : 'document'
      const params: Record<string, unknown> = { id: document.id, prompt: text, source, attachments, scope, revision: latestRecord.current.revision }
      if (selectionOnly && selected) params.selection = { start: selected.start, end: selected.end }
      if (selectionOnly && screenshots.length) params.pdfSelections = screenshots
      const job = await editorCall<{ taskId: string }>('workspace.chat', params)
      run.id = job.taskId
      if (run.controller.signal.aborted) { await editorCall('task.cancel', { id: job.taskId }); return }
      setPrompt(value => value === prompt ? '' : value)
      setAttachments(items => items.filter(item => !attachments.some(sent => sent.id === item.id)))
      setChatStatus('正在生成回复…')
      const task = await pollEditorTask<EditorDocument>(job.taskId, run.controller.signal, setChatStatus)
      if (run.controller.signal.aborted) return
      if (task.status === 'succeeded' && task.result) {
        accept(task.result)
        setChatStatus('')
      } else {
        setChatStatus(task.error || task.message || (task.status === 'cancelled' ? '已停止生成' : '发送失败'))
        setPrompt(value => value || prompt)
        setAttachments(items => items.length ? items : attachments)
        accept(await editorCall<EditorDocument>('workspace.open', { id: document.id }))
      }
    } catch (error) {
      if (!run.controller.signal.aborted) {
        setChatStatus(errorMessage(error))
        setPrompt(value => value || prompt)
        setAttachments(items => items.length ? items : attachments)
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
    setPrompt(value => value || run.message.text)
    setAttachments(items => items.length ? items : run.message.attachments ?? [])
    if (run.id) {
      try {
        await editorCall('task.cancel', { id: run.id })
        accept(await editorCall<EditorDocument>('workspace.open', { id: document.id }))
      }
      catch (error) { setChatStatus(errorMessage(error)) }
    }
  }

  async function updateProposal(operation: 'apply' | 'dismiss' | 'undo', item?: EditorProposal) {
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
  }

  function compareProposal(item: EditorProposal) {
    setProposalId(item.id)
    setLayout(previous => ({ ...previous, mode: previous.mode === 'chat' ? 'split' : previous.mode, view: 'compare', pair: undefined }))
  }

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
    setPairOpen(false)
    setLayout(previous => ({ ...previous, view, pair: undefined }))
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

  function handleResize(event: PointerEvent<HTMLDivElement>, kind: 'chat' | 'document') {
    if (event.button !== 0) return
    const divider = event.currentTarget
    const parent = (kind === 'chat' ? root.current : divider.parentElement)?.getBoundingClientRect()
    if (!parent) return
    event.preventDefault()
    divider.setPointerCapture(event.pointerId)
    const move = (moveEvent: globalThis.PointerEvent) => setLayout(previous => kind === 'chat'
      ? { ...previous, chatWidth: Math.max(280, Math.min(parent.width * 0.53, parent.right - moveEvent.clientX)) }
      : { ...previous, documentWidth: Math.max(22, Math.min(78, (moveEvent.clientX - parent.left) * 100 / parent.width)) })
    const stop = () => { divider.removeEventListener('pointermove', move); divider.removeEventListener('lostpointercapture', stop) }
    divider.addEventListener('pointermove', move)
    divider.addEventListener('lostpointercapture', stop)
  }

  function divider(kind: 'chat' | 'document') {
    return <div className="wb-divider" role="separator" aria-label={kind === 'chat' ? '调整聊天栏宽度' : '调整两栏宽度'} aria-orientation="vertical" tabIndex={0} onPointerDown={event => handleResize(event, kind)} onKeyDown={event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      event.preventDefault()
      const direction = event.key === 'ArrowLeft' ? -1 : 1
      setLayout(previous => kind === 'chat' ? { ...previous, chatWidth: Math.max(280, Math.min((root.current?.clientWidth ?? 900) * 0.53, previous.chatWidth - direction * 24)) }
        : { ...previous, documentWidth: Math.max(22, Math.min(78, previous.documentWidth + direction * 3)) })
    }} />
  }

  function renderPdf(panel: PdfPanel, path: string | null | undefined, label: string, empty: string, waiting = false) {
    return <EditorPdf path={path} label={label} panel={panel} position={layout.pdf[panel]} navigation={navigation[panel]} pending={waiting} empty={empty} crop={cropPanel === panel}
      onCropToggle={() => setCropPanel(current => current === panel ? undefined : panel)} onCapture={addCapture}
      onNavigate={(page, offset, fromScroll) => setPdfPosition(panel, page, offset, fromScroll)}
      onZoom={zoom => setLayout(previous => ({ ...previous, pdf: { ...previous.pdf, [panel]: { ...previous.pdf[panel], zoom } } }))} />
  }

  function renderView(view: Exclude<View, 'compare'>) {
    if (view === 'preview') return renderPdf('preview', preview.path, 'PDF 预览', draft.source.trim() ? '点击「编译 PDF」生成预览。' : '录入 LaTeX 或识别原卷后即可编译 PDF。', compiling)
    if (view === 'original') return renderPdf('original', record.pdfPath, '原卷', '这道题没有附带原卷 PDF。')
    return <section className="wb-source" aria-label="LaTeX 源码">
      <div className="wb-pdf-tools"><span className="wb-pdf-label">LaTeX 源码</span><div className="wb-pdf-actions">
        {!!draft.selections.length && <select aria-label="已标记源码选段" value={draft.activeSelectionId ?? ''} onChange={event => selectSource(event.target.value || null)}>
          <option value="">已标记选段</option>{draft.selections.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>}
        <button type="button" className="wb-btn" disabled={sourceRange.start === sourceRange.end} onClick={markSource}>标记选段</button>
      </div></div>
      <div className="wb-source-editor">
        <div className="wb-line-rail" aria-hidden="true" ref={lineNumbers}>{Array.from({ length: draft.source.split('\n').length }, (_, index) => <div key={index}>{index + 1}</div>)}</div>
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
      </div>
      {!layout.compareText && currentComparison?.error && <div className="wb-notice" role="status">部分对照预览编译失败。 <button type="button" className="wb-btn" onClick={() => { setCompileLog(currentComparison.error ?? ''); setShowLog(true) }}>编译日志</button></div>}
      <div className="wb-document-panes">
        <div className="wb-document-panel" style={{ flexBasis: `${layout.documentWidth}%` }}>{layout.compareText ? <DiffSource label="修改前" text={proposal.changes?.map(item => item.before).join('\n\n') ?? proposal.before} side="before" /> : renderPdf('before', currentComparison?.before || proposal.beforePath, '修改前', '此版本没有 PDF 预览。', currentComparison?.loading)}</div>
        {divider('document')}
        <div className="wb-document-panel wb-document-panel-grow">{layout.compareText ? <DiffSource label="修改后" text={proposal.changes?.map(item => item.after).join('\n\n') ?? proposal.after} side="after" /> : renderPdf('after', currentComparison?.after || proposal.afterPath, '修改后', '此版本没有 PDF 预览。', currentComparison?.loading)}</div>
      </div>
    </div>
  }

  function openSheet(kind: Sheet) {
    setSheetError('')
    setSheet(kind)
    if (kind === 'library') void editorCall<string[]>('workspace.chapters').then(setChapters).catch(error => setSheetError(errorMessage(error)))
  }

  async function submitSheet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (sheetBusy) return
    const data = new FormData(event.currentTarget)
    setSheetBusy(true)
    setSheetError('')
    try {
      await persist()
      if (sheet === 'recognize') {
        const firstPage = Number(data.get('firstPage'))
        const lastPage = Number(data.get('lastPage'))
        if (!Number.isInteger(firstPage) || lastPage < firstPage) throw new Error('请填写有效的页码范围。')
        setReconstructing(true)
        setSheet(undefined)
        setRecognitionProgress('')
        const next = await runTask<EditorDocument>('workspace.reconstruct', { firstPage, lastPage }, undefined, setRecognitionProgress)
        const sourceChanged = next.source !== latestRecord.current.source
        const userEdited = latestDraft.current.source !== latestRecord.current.source
        accept(next, sourceChanged && !userEdited)
        setNotice('识别完成')
        if (sourceChanged && !userEdited) {
          setLayout(previous => ({ ...previous, view: 'original', pair: 'preview' }))
          setNavigation({ original: { page: next.originalPage ?? firstPage, stamp: ++navigationStamp.current }, preview: { page: 1, stamp: ++navigationStamp.current } })
          void compileCurrent(next.source)
        } else if (next.proposals?.length) {
          const newest = next.proposals.at(-1)
          if (newest) compareProposal(newest)
        }
      } else {
        const tags = String(data.get('tags') ?? '').split(/[,，、]/).map(value => value.trim()).filter(Boolean)
        const result = await runTask<BankExport>('workspace.export', { source: latestDraft.current.source, chapter: data.get('chapter'), title: data.get('title'), kind: data.get('kind'), tags })
        const failures = result.files.filter(item => item.status !== 'success')
        if (!failures.length) {
          setSheet(undefined)
          setNotice(`已另存 ${result.files.length} 道题到题库`)
        } else {
          setSheetError([`已另存 ${result.files.length - failures.length} 道题。`, ...failures.map(item => `${item.file}　${item.message}`)].join('\n'))
        }
      }
    } catch (error) {
      if (sheet === 'recognize') setNotice(errorMessage(error))
      setSheetError(errorMessage(error))
    } finally { setSheetBusy(false); setReconstructing(false) }
  }

  async function exportTex() {
    setCommandsOpen(false)
    try {
      const path = await window.utopia.saveFile('tex', `${latestDraft.current.title}.tex`)
      if (!path) return
      await persist()
      await editorCall('workspace.export_tex', { id: document.id, path, source: latestDraft.current.source })
      setNotice('已导出 LaTeX')
    } catch (error) { setNotice(errorMessage(error)) }
  }

  async function exportPdf() {
    setCommandsOpen(false)
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

  return <div className={`wb-workbench wb-mode-${layout.mode}`} ref={root}>
    <header className="wb-header">
      <div className="wb-document-title">
        <button type="button" className="wb-icon wb-back" onClick={() => void close()} aria-label="返回" title="返回"><WorkbenchIcon name="back" /></button>
        <div className="wb-title-block">
          <input aria-label="题目标题" value={draft.title} onChange={event => updateDraft({ ...latestDraft.current, title: event.target.value })} />
          <span role="status">{saveState || (changed ? '保存中…' : document.sourcePath?.endsWith('.tex') || draft.source ? '工作稿已保存' : record.pdfPath ? '原卷已载入' : '工作稿')}</span>
        </div>
      </div>
      <nav className="wb-mode-switch" aria-label="编辑工作区模式">
        {modes.map(([mode, title, icon]) => <button type="button" key={mode} aria-pressed={layout.mode === mode} onClick={() => setLayout(previous => ({ ...previous, mode }))}><WorkbenchIcon name={icon} /> <span>{title}</span></button>)}
      </nav>
      <div className="wb-header-actions">
        {record.pdfPath && !draft.source.trim() && <button type="button" className="wb-btn" disabled={reconstructing} onClick={() => openSheet('recognize')}>{reconstructing ? '正在识别…' : '识别原卷'}</button>}
        <button type="button" className="wb-btn" title={preview.path && preview.source !== draft.source ? '源码已有变化，重新编译 PDF' : '编译 PDF'} disabled={compiling || !draft.source.trim()} onClick={() => void compileCurrent()}>
          <WorkbenchIcon name="refresh" /> <span>{compiling ? '正在编译…' : '编译 PDF'}</span>
        </button>
        <button type="button" className="wb-btn wb-primary" disabled={!draft.source.trim()} onClick={() => openSheet('library')}><WorkbenchIcon name="save" /> <span>另存入库</span></button>
        <div className="wb-popover-anchor">
          <button type="button" className="wb-icon" aria-label="更多操作" aria-expanded={commandsOpen} onClick={() => setCommandsOpen(value => !value)}>⋯</button>
          {commandsOpen && <div className="wb-popover" onBlur={event => { if (!event.currentTarget.parentElement?.contains(event.relatedTarget)) setCommandsOpen(false) }}>
            {record.pdfPath && <button type="button" disabled={reconstructing} onClick={() => { setCommandsOpen(false); openSheet('recognize') }}>识别原卷</button>}
            <button type="button" disabled={exporting || !draft.source.trim()} onClick={() => void exportPdf()}>{exporting ? '正在导出…' : '导出 PDF'}</button>
            <button type="button" disabled={!draft.source.trim()} onClick={() => void exportTex()}>导出 LaTeX</button>
            <button type="button" disabled={!record.canUndo} onClick={() => { setCommandsOpen(false); void updateProposal('undo') }}>撤销上次采用</button>
          </div>}
        </div>
      </div>
    </header>
    {notice && <div className="wb-notice" role="status"><span>{notice}</span>{compileLog && <button type="button" className="wb-btn" onClick={() => setShowLog(true)}>编译日志</button>}<button type="button" className="wb-icon" aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}
    {reconstructing && <div className="wb-notice" role="status">{recognitionProgress || '正在识别所选页面，可继续查看原卷。'}</div>}
    <div className="wb-body">
      <main className="wb-document" hidden={layout.mode === 'chat'}>
        <div className="wb-document-tools">
          <nav className="wb-view-switch" aria-label="题目面板">
            {views.map(([id, title]) => <button type="button" key={id} aria-pressed={layout.view === id || layout.pair === id} onClick={() => changeView(id)}>{title}</button>)}
          </nav>
          <div className="wb-layout-tools">
            {layout.pair && [layout.view, layout.pair].every(view => view === 'preview' || view === 'original') && <label className="wb-sync"><input type="checkbox" checked={layout.sync} onChange={event => setLayout(previous => ({ ...previous, sync: event.target.checked }))} /> 联动翻页</label>}
            <div className="wb-popover-anchor">
              <button type="button" className="wb-btn" aria-expanded={pairOpen} onClick={() => setPairOpen(value => !value)}><WorkbenchIcon name="split" /> 并排 <span aria-hidden="true">⌄</span></button>
              {pairOpen && <div className="wb-popover" onBlur={event => { if (!event.currentTarget.parentElement?.contains(event.relatedTarget)) setPairOpen(false) }}>
                <button type="button" onClick={() => { setLayout(previous => ({ ...previous, view: 'source', pair: 'preview' })); setPairOpen(false) }}>LaTeX ＋ PDF 预览</button>
                <button type="button" onClick={() => { setLayout(previous => ({ ...previous, view: 'original', pair: 'preview' })); setPairOpen(false) }}>原卷 ＋ 重建 PDF</button>
                <button type="button" onClick={() => changeView('compare')}>修改前 ＋ 修改后</button>
                {layout.pair && <button type="button" onClick={() => changeView(layout.view)}>关闭并排</button>}
              </div>}
            </div>
          </div>
        </div>
        {layout.view === 'compare' ? renderComparison() : layout.pair ? <div className="wb-document-panes">
          <div className="wb-document-panel" style={{ flexBasis: `${layout.documentWidth}%` }}>{renderView(layout.view)}</div>
          {divider('document')}
          <div className="wb-document-panel wb-document-panel-grow">{renderView(layout.pair)}</div>
        </div> : renderView(layout.view)}
      </main>
      {layout.mode === 'split' && divider('chat')}
      <aside className="wb-chat-pane" hidden={layout.mode === 'review'} style={layout.mode === 'split' ? { width: `min(${layout.chatWidth}px, 52%)` } : undefined}>
        <EditorChat messages={record.messages ?? []} proposals={proposals} attachments={attachments} selection={selected} prompt={prompt} status={chatStatus}
          pending={pending} busy={Boolean(pending)} hasPdfSelection={preciseCaptures.length > 0} onPrompt={setPrompt} onAttach={files => void attachFiles(files)}
          onRemove={id => setAttachments(previous => previous.filter(item => item.id !== id))} onClearSelection={() => selectSource(null)} onSend={scope => void sendChat(scope)}
          onStop={() => void stopChat()} onApply={item => void updateProposal('apply', item)} onDismiss={item => void updateProposal('dismiss', item)} onCompare={compareProposal} />
      </aside>
    </div>
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

function WorkbenchIcon({ name, size = 16 }: { name: string; size?: number }) {
  const paths: Record<string, string> = {
    back: 'm12 6-6 6 6 6M6 12h13', file: 'M7 3h7l4 4v14H7zM14 3v5h4m-8 4h5m-5 4h5',
    split: 'M3 5h18v14H3zM12 5v14', chat: 'M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.7-3A7.5 7.5 0 1 1 20 11.5z',
    refresh: 'M19 11a7 7 0 0 0-12-4L4 10m0 0V5m0 5h5m-4 3a7 7 0 0 0 12 4l3-3m0 0v5m0-5h-5',
    save: 'M5 4h12l2 2v14H5zM8 4v5h8V4M8 20v-7h8v7',
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>
}
