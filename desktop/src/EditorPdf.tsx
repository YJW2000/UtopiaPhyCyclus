import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent } from 'react'
import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy, RenderTask, TextLayer } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { errorMessage } from './editorContract'
import type { EditorAttachment, PdfPanel } from './editorContract'
import { inkBounds, InkOverlay, inkSnapshot, MagicNote, MAX_INK_POINTS, readMarks, saveMarks } from './PdfMagicPen'
import type { InkPoint, InkRect, PdfMark } from './PdfMagicPen'
import { findPdfMatches, FULL_PAGE, normalizePdfQuery, readPdfOutline, readPdfText, readPdfTrim, savePdfTrim } from './pdfReading'
import type { PdfFit, PdfMatch, PdfOutlineItem, PdfTool, PdfViewState } from './pdfReading'
import './pdf-reading.css'

export type { PdfFit, PdfOutlineItem, PdfTool, PdfViewNote, PdfViewState } from './pdfReading'

export interface PdfPosition { page: number; offset: number; zoom: number }
export interface PdfNavigation { page: number; offset?: number; stamp: number }
export type PdfAnnotationAction = { type: 'undo' | 'redo' | 'clear'; stamp: number } | { type: 'open'; id: string; stamp: number }

interface PdfProps {
  path?: string | null
  label: string
  panel: PdfPanel
  position: PdfPosition
  navigation?: PdfNavigation
  pending?: boolean
  empty: string
  crop: boolean
  onCropToggle: () => void
  onCapture: (capture: EditorAttachment) => void
  annotationKey?: string
  onAnnotation?: (capture: EditorAttachment, note: string) => void
  onNavigate: (page: number, offset: number, fromScroll: boolean) => void
  onZoom: (zoom: number) => void
  hideToolbar?: boolean
  tool?: PdfTool
  onToolChange?: (tool: PdfTool) => void
  fit?: PdfFit
  search?: { query: string; index: number }
  onViewState?: (state: PdfViewState) => void
  onFocusPanel?: () => void
  annotationAction?: PdfAnnotationAction
}

interface PdfReader { path: string; document: PDFDocumentProxy; aspect: number }

let pdfLibrary: Promise<typeof import('pdfjs-dist')> | undefined
const pdfAssets = new URL('./pdfjs/', location.href)

function loadPdf() {
  pdfLibrary ??= import('pdfjs-dist').then(library => {
    library.GlobalWorkerOptions.workerSrc = workerUrl
    return library
  })
  return pdfLibrary
}

const PAGE_GAP = 16
const DEFAULT_ASPECT = Math.sqrt(2)
const NO_MARKS: PdfMark[] = []
const NO_MATCHES: PdfMatch[] = []
const NO_OUTLINE: PdfOutlineItem[] = []

interface MarkDraft {
  path: string
  mark: PdfMark
  anchor: InkPoint
  capture?: EditorAttachment
}
interface AnnotationHistory { path?: string | null; key?: string; past: PdfMark[][]; future: PdfMark[][] }

export function EditorPdf({ path, label, panel, position, navigation, pending, empty, crop: cropRequested, onCropToggle, onCapture, annotationKey, onAnnotation, onNavigate, onZoom,
  hideToolbar, tool, onToolChange, fit, search, onViewState, onFocusPanel, annotationAction }: PdfProps) {
  const [loaded, setReader] = useState<PdfReader>()
  const reader = loaded?.path === path ? loaded : undefined
  const [failure, setFailure] = useState('')
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [skip, setSkip] = useState(1)
  const [pageDraft, setPageDraft] = useState<string | null>(null)
  const [localMagic, setMagic] = useState(false)
  const activeTool = tool ?? (cropRequested ? 'crop' : localMagic ? 'magic' : 'select')
  const crop = activeTool === 'crop'
  const magic = activeTool === 'magic'
  const trimming = activeTool === 'trim'
  const [markDraft, setMarkDraft] = useState<MarkDraft>()
  const [openMarkId, setOpenMarkId] = useState<string>()
  const [annotationError, setAnnotationError] = useState('')
  const marksKey = annotationKey ? `utopia.pdf-marks.${annotationKey}.${panel}` : undefined
  const trimKey = annotationKey ? `utopia.pdf-trim.${annotationKey}.${panel}` : undefined
  const savedTrim = useMemo(() => readPdfTrim(trimKey, path), [trimKey, path])
  const [trimEntry, setTrimEntry] = useState<{ key?: string; path?: string | null; rect: InkRect }>()
  const savedRect = trimEntry && trimEntry.path === path && trimEntry.key === trimKey ? trimEntry.rect : savedTrim
  const trim = trimming ? FULL_PAGE : savedRect
  const trimWidth = trim[2] - trim[0]
  const trimHeight = trim[3] - trim[1]
  const [markStore, setMarkStore] = useState(() => readMarks(marksKey))
  const previousMarksKey = useRef(marksKey)
  const marks = markStore.find(item => item.path === path)?.marks ?? NO_MARKS
  const pageMarks = marks.filter(mark => mark.page === position.page)
  const [storedHistory, setHistory] = useState<AnnotationHistory>({ past: [], future: [] })
  const history = storedHistory.path === path && storedHistory.key === marksKey ? storedHistory : { path, key: marksKey, past: [], future: [] }
  const appliedAnnotation = useRef(0)
  const [outlineResult, setOutline] = useState<{ reader: PdfReader; entries: PdfOutlineItem[] }>()
  const [searchResult, setSearchResult] = useState<{ reader: PdfReader; query: string; matches: PdfMatch[] }>()
  const query = normalizePdfQuery(search?.query ?? '')
  const outline = outlineResult && outlineResult.reader === reader ? outlineResult.entries : NO_OUTLINE
  const searchReady = searchResult && searchResult.reader === reader && searchResult.query === query
  const foundMatches = searchReady ? searchResult.matches : NO_MATCHES
  const matches = useMemo(() => trim === FULL_PAGE ? foundMatches : foundMatches.filter(match => match.rects.some(([left, top, right, bottom]) =>
    left < trim[2] && right > trim[0] && top < trim[3] && bottom > trim[1])), [foundMatches, trim])
  const searching = Boolean(reader && query && !searchReady)
  const matchIndex = matches.length ? ((search?.index ?? 0) % matches.length + matches.length) % matches.length : -1
  const currentMatch = matches[matchIndex]
  const matchesByPage = useMemo(() => {
    const pages = new Map<number, PdfMatch[]>()
    for (const match of matches) {
      const found = pages.get(match.page)
      if (found) found.push(match)
      else pages.set(match.page, [match])
    }
    return pages
  }, [matches])
  const notes = useMemo(() => marks.map(mark => ({ id: mark.id, page: mark.page, label: mark.note || '高亮标记' })), [marks])
  const scroller = useRef<HTMLDivElement>(null)
  const pan = useRef<{ id: number; x: number; y: number; left: number; top: number } | undefined>(undefined)
  const navigate = useRef(onNavigate)
  const reportView = useRef(onViewState)
  const positionRef = useRef(position)
  const appliedNavigation = useRef(0)
  const expectedScroll = useRef<number | null>(null)
  const pageInput = useRef<HTMLInputElement>(null)
  navigate.current = onNavigate
  reportView.current = onViewState
  positionRef.current = position

  useLayoutEffect(() => {
    if (previousMarksKey.current === marksKey) return
    previousMarksKey.current = marksKey
    setMarkStore(readMarks(marksKey))
    setMarkDraft(undefined)
    setOpenMarkId(undefined)
    setMagic(false)
  }, [marksKey])

  useEffect(() => {
    let active = true
    let task: PDFDocumentLoadingTask | undefined
    setReader(undefined)
    setFailure('')
    if (path) {
      void (async () => {
        const library = await loadPdf()
        const url = /^(?:https?:|blob:|data:|utopia-asset:)/.test(path) ? path : await window.utopia.asset(path)
        if (!active) return
        task = library.getDocument({
          url,
          cMapUrl: new URL('cmaps/', pdfAssets).href,
          cMapPacked: true,
          standardFontDataUrl: new URL('standard_fonts/', pdfAssets).href,
          wasmUrl: new URL('wasm/', pdfAssets).href,
          isEvalSupported: false,
        })
        const document = await task.promise
        const firstPage = await document.getPage(1)
        const viewport = firstPage.getViewport({ scale: 1 })
        if (active) setReader({ path, document, aspect: viewport.height / viewport.width || DEFAULT_ASPECT })
      })().catch(error => { if (active) setFailure(errorMessage(error)) })
    }
    return () => {
      active = false
      void task?.destroy().catch(() => {})
    }
  }, [path])

  useEffect(() => {
    if (!reader) return
    let active = true
    void readPdfOutline(reader.document).then(entries => {
      if (active) setOutline({ reader, entries })
    }).catch(() => { /* The workbench provides a page list when bookmarks are unavailable. */ })
    return () => { active = false }
  }, [reader])

  useEffect(() => {
    if (!reader || !query) return
    let active = true
    const timer = setTimeout(() => {
      void findPdfMatches(reader.document, query, () => !active).then(found => {
        if (active) setSearchResult({ reader, query, matches: found })
      })
    }, 150)
    return () => { active = false; clearTimeout(timer) }
  }, [reader, query])

  useLayoutEffect(() => {
    const element = scroller.current
    if (!element) return
    const update = () => {
      if (element.clientWidth && element.clientHeight) setSize({ width: element.clientWidth, height: element.clientHeight })
    }
    const resize = new ResizeObserver(update)
    resize.observe(element)
    update()
    return () => resize.disconnect()
  }, [])

  const count = reader?.document.numPages ?? 0
  const pageAspect = (reader?.aspect ?? DEFAULT_ASPECT) * trimHeight / trimWidth
  const fitWidth = Math.min(930, size.width - 36)
  const pageWidth = Math.max(100, trimming || fit === 'page' ? Math.min(fitWidth, (size.height - 30) / pageAspect) : fitWidth * (fit === 'width' ? 1 : position.zoom))
  const pageHeight = Math.ceil(pageWidth * pageAspect)
  const step = pageHeight + PAGE_GAP
  const [rasterWidth, setRasterWidth] = useState(pageWidth)

  useEffect(() => {
    reportView.current?.({ pageCount: count, outline, matches: matches.length, searching, notes, canUndo: history.past.length > 0, canRedo: history.future.length > 0 })
  }, [count, outline, matches.length, searching, notes, history.past.length, history.future.length])

  useEffect(() => {
    // Scale the published bitmap during drag, then render at the settled size.
    const timer = setTimeout(() => setRasterWidth(pageWidth), 140)
    return () => clearTimeout(timer)
  }, [pageWidth])

  function jump(page: number, offset = 0) {
    const element = scroller.current
    if (!element || !count || !Number.isFinite(page)) return
    const safePage = Math.min(count, Math.max(1, Math.round(page)))
    const target = (safePage - 1) * step + offset * step
    const y = Math.min(target, element.scrollHeight - element.clientHeight)
    expectedScroll.current = Math.max(0, y)
    element.scrollTop = y
    setSkip(safePage)
    navigate.current(safePage, offset, false)
  }

  useLayoutEffect(() => {
    if (navigation && navigation.stamp !== appliedNavigation.current && count) {
      appliedNavigation.current = navigation.stamp
      jump(navigation.page, navigation.offset)
      return
    }
    jump(positionRef.current.page, trimming ? 0 : positionRef.current.offset)
  }, [reader, step, size.height, navigation?.stamp, trimming])

  useLayoutEffect(() => {
    if (!currentMatch) return
    const y = Math.min(...currentMatch.rects.map(rect => rect[1]))
    const offset = Number.isFinite(y) ? Math.max(0, ((y - trim[1]) / trimHeight * pageHeight - size.height * 0.25) / step) : 0
    jump(currentMatch.page, offset)
  }, [currentMatch, trim])

  function scrolled() {
    const top = scroller.current?.scrollTop ?? 0
    if (expectedScroll.current !== null) {
      const same = Math.abs(top - expectedScroll.current) <= 2
      expectedScroll.current = null
      if (same) return
    }
    const page = Math.min(count, Math.floor((top + 3) / step) + 1)
    if (!page) return
    setSkip(page)
    navigate.current(page, Math.max(0, (top - (page - 1) * step) / step), true)
  }

  const first = Math.max(1, skip - 1)
  const last = Math.min(count, skip + Math.ceil(size.height / step) + 2)

  function commitPage() {
    if (pageDraft && count) {
      const chosenPage = Number(pageDraft)
      if (Number.isFinite(chosenPage)) {
        jump(chosenPage)
        navigate.current(Math.min(count, Math.max(1, Math.round(chosenPage))), 0, true)
      }
    }
    setPageDraft(null)
  }

  function stepPage(page: number) {
    if (!count) return
    jump(page)
    navigate.current(page, 0, true)
  }

  function attachment(page: number, dataUrl: string, rect: InkRect, annotated = false): EditorAttachment {
    return { id: crypto.randomUUID(), title: `${label} · 第 ${page} 页${annotated ? '批注' : ''}`, page, panel, dataUrl, rect }
  }

  function openMark(mark: PdfMark, anchor: InkPoint, capture?: EditorAttachment) {
    if (!path) return
    setAnnotationError('')
    setMarkDraft({ path, mark, anchor, capture })
  }

  function updateMarks(next: PdfMark[]) {
    if (!path) return
    setMarkStore(saveMarks(marksKey, path, markStore, next))
    setHistory({ path, key: marksKey, past: [...history.past.slice(-39), marks], future: [] })
  }

  useLayoutEffect(() => {
    if (!annotationAction || annotationAction.stamp === appliedAnnotation.current || !path) return
    appliedAnnotation.current = annotationAction.stamp
    const action = annotationAction.type
    if (action === 'undo' && history.past.length) {
      setMarkStore(saveMarks(marksKey, path, markStore, history.past.at(-1)!))
      setHistory({ ...history, past: history.past.slice(0, -1), future: [marks, ...history.future] })
    } else if (action === 'redo' && history.future.length) {
      setMarkStore(saveMarks(marksKey, path, markStore, history.future[0]))
      setHistory({ ...history, past: [...history.past, marks], future: history.future.slice(1) })
    } else if (action === 'clear' && marks.length) {
      updateMarks(NO_MARKS)
    } else if (annotationAction.type === 'open') {
      const mark = marks.find(item => item.id === annotationAction.id)
      if (mark) {
        const offset = Math.max(0, ((mark.points[0].y - trim[1]) / trimHeight * pageHeight - size.height * 0.4) / step)
        jump(mark.page, offset)
        setOpenMarkId(mark.id)
      }
    }
  }, [annotationAction?.stamp])

  function saveMark(note: string, includeInChat: boolean) {
    if (!markDraft || markDraft.path !== path) return
    const { mark, capture } = markDraft
    if (includeInChat && (!capture || !onAnnotation)) {
      setAnnotationError('当前 PDF 页还未绘制完成，稍后可重新打开批注并加入对话。')
      return
    }
    updateMarks(marks.some(item => item.id === mark.id)
      ? marks.map(item => item.id === mark.id ? { ...mark, note } : item)
      : [...marks, { ...mark, note }])
    setMarkDraft(undefined)
    if (includeInChat && capture) onAnnotation?.(capture, `第 ${mark.page} 页批注：${note}`)
  }

  function chooseTool(next: PdfTool) {
    setMarkDraft(undefined)
    if (tool) onToolChange?.(next)
    else {
      if (crop !== (next === 'crop')) onCropToggle()
      setMagic(next === 'magic')
    }
    scroller.current?.focus({ preventScroll: true })
  }

  function changeTrim(rect: InkRect) {
    if (rect[2] - rect[0] < 0.2 || rect[3] - rect[1] < 0.2) return
    const next = rect.every((edge, index) => edge === FULL_PAGE[index]) ? FULL_PAGE : rect
    savePdfTrim(trimKey, path, next)
    setTrimEntry({ key: trimKey, path, rect: next })
    if (trimming) chooseTool('select')
  }

  function endPan(event: PointerEvent<HTMLDivElement>) {
    if (pan.current?.id !== event.pointerId) return
    pan.current = undefined
    delete event.currentTarget.dataset.panning
  }

  return <section className="wb-pdf" aria-label={label}>
    {!hideToolbar && <div className="wb-pdf-tools">
      <span className="wb-pdf-label">{label}</span>
      {count > 0 && <>
        <div className="wb-page-controls">
          <button type="button" className="wb-icon" title="上一页" aria-label={`${label}上一页`} disabled={position.page <= 1} onClick={() => stepPage(position.page - 1)}>‹</button>
          <input ref={pageInput} aria-label={`${label}页码`} inputMode="numeric" value={pageDraft ?? Math.min(count, position.page)}
            onChange={event => setPageDraft(event.target.value.replace(/[^0-9]/g, ''))}
            onFocus={event => event.target.select()}
            onBlur={commitPage}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); pageInput.current?.blur() } }} />
          <span>/ {count}</span>
          <button type="button" className="wb-icon" title="下一页" aria-label={`${label}下一页`} disabled={position.page >= count} onClick={() => stepPage(position.page + 1)}>›</button>
        </div>
        <div className="wb-pdf-actions">
          <button type="button" className="wb-icon" title="缩小" aria-label={`${label}缩小`} disabled={position.zoom <= 0.55} onClick={() => onZoom(Math.max(0.55, position.zoom - 0.15))}>−</button>
          <button type="button" className="wb-zoom" title="适应宽度" onClick={() => onZoom(1)}>{Math.round(position.zoom * 100)}%</button>
          <button type="button" className="wb-icon" title="放大" aria-label={`${label}放大`} disabled={position.zoom >= 2.5} onClick={() => onZoom(Math.min(2.5, position.zoom + 0.15))}>+</button>
          <button type="button" className="wb-btn wb-crop-toggle" aria-pressed={crop} title="拖动框选 PDF 区域，添加为聊天截图" onClick={() => chooseTool(crop ? 'select' : 'crop')}>
            <CropIcon /> <span>{crop ? '退出框选' : '框选截图'}</span>
          </button>
          {annotationKey && <button type="button" className="wb-btn wb-magic-toggle" aria-pressed={magic}
            aria-keyshortcuts="Q" title="在 PDF 上点一下或划过需要修改的位置（Q）" onClick={() => chooseTool(magic ? 'select' : 'magic')}>
            <PenIcon /> <span>魔法笔</span>
          </button>}
        </div>
      </>}
    </div>}
    {magic && count > 0 && <div className="wb-magic-controls">
      <span role="status">点一下或划过 PDF，添加批注</span>
      <button type="button" className="wb-btn" disabled={!pageMarks.length}
        onClick={() => updateMarks(marks.filter(mark => mark.id !== pageMarks.at(-1)?.id))}>撤销本页最后一笔</button>
      <button type="button" className="wb-btn" disabled={!pageMarks.length}
        onClick={() => updateMarks(marks.filter(mark => mark.page !== position.page))}>清除本页批注</button>
    </div>}
    {!!count && (trimming || savedRect !== FULL_PAGE) && <div className="wb-magic-controls wb-trim-controls">
      <span role="status">{trimming ? '拖动框选要保留的内容。裁剪适用于当前 PDF 的所有页面。' : '已裁剪预览页边距'}</span>
      {trimming && <button type="button" className="wb-btn" onClick={() => chooseTool('select')}>取消裁剪</button>}
      {savedRect !== FULL_PAGE && <button type="button" className="wb-btn" onClick={() => changeTrim(FULL_PAGE)}>恢复页边距</button>}
    </div>}
    <div className={`wb-pdf-scroll${crop || trimming ? ' wb-pdf-cropping' : activeTool === 'hand' ? ' wb-pdf-panning' : activeTool === 'select' ? ' wb-pdf-selecting' : ' wb-pdf-marking'}`}
      ref={scroller} onScroll={scrolled} tabIndex={0} aria-label={`${label}滚动区`}
      onFocus={onFocusPanel} onPointerDown={event => {
        onFocusPanel?.()
        if (activeTool !== 'hand' || !event.isPrimary || event.button !== 0 || event.pointerType === 'touch' || (event.target as Element).closest('button')) return
        const node = event.currentTarget
        if (event.target === node) return
        event.preventDefault()
        node.focus({ preventScroll: true })
        node.setPointerCapture(event.pointerId)
        pan.current = { id: event.pointerId, x: event.clientX, y: event.clientY, left: node.scrollLeft, top: node.scrollTop }
        node.dataset.panning = 'true'
      }} onPointerMove={event => {
        const start = pan.current
        if (!start || start.id !== event.pointerId) return
        event.currentTarget.scrollLeft = start.left + start.x - event.clientX
        event.currentTarget.scrollTop = start.top + start.y - event.clientY
      }} onPointerUp={endPan} onPointerCancel={endPan} onLostPointerCapture={endPan} onKeyDown={event => {
      if (event.key === 'Escape') {
        if (activeTool !== 'select') chooseTool('select')
      }
      if (annotationKey && count && event.key.toLowerCase() === 'q' && !event.ctrlKey && !event.altKey && !event.metaKey && event.target === event.currentTarget) {
        event.preventDefault(); chooseTool(magic ? 'select' : 'magic')
      }
      if (event.key === 'PageDown' || event.key === 'PageUp') {
        event.preventDefault()
        stepPage(Math.min(count, Math.max(1, position.page + (event.key === 'PageDown' ? 1 : -1))))
      }
    }}>
      {!count && <div className="wb-empty">
        <PdfIcon />
        <p>{failure ? `无法打开 PDF，${failure}` : path || pending ? '正在载入 PDF…' : empty}</p>
      </div>}
      {reader && count > 0 && <div className="wb-pdf-stack" style={{ '--wb-paper-width': `${pageWidth}px`, height: count * step + Math.max(PAGE_GAP, size.height - step) } as CSSProperties}>
        {Array.from({ length: Math.max(0, last - first + 1) }, (_, index) => first + index).map(page => <PdfPage
          document={reader.document} key={`${reader.path}:${page}`} page={page} width={pageWidth} height={pageHeight} top={(page - 1) * step}
          rasterWidth={rasterWidth} rasterHeight={Math.ceil(rasterWidth * pageAspect)}
          tool={activeTool} trim={trim} selectable={page >= skip && page <= skip + Math.ceil(size.height / step)}
          marks={marks.filter(mark => mark.page === page)}
          matches={matchesByPage.get(page) ?? NO_MATCHES} currentMatch={currentMatch}
          openMarkId={openMarkId}
          onCapture={(dataUrl, rect) => onCapture(attachment(page, dataUrl, rect))}
          onTrim={changeTrim}
          onMark={(mark, anchor, capture) => {
            setOpenMarkId(undefined)
            openMark(mark, anchor, capture && attachment(page, capture.dataUrl, capture.rect, true))
          }}
          onHighlight={mark => updateMarks([...marks, mark])}
        />)}
      </div>}
    </div>
    {markDraft && markDraft.path === path && <MagicNote key={markDraft.mark.id} anchor={markDraft.anchor} initial={markDraft.mark.note} plain={Boolean(markDraft.mark.kind)}
      error={annotationError} onClose={() => {
        setMarkDraft(undefined)
        scroller.current?.focus({ preventScroll: true })
      }} onSave={saveMark} />}
  </section>
}

interface PdfPageProps {
  document: PDFDocumentProxy
  page: number
  width: number
  height: number
  rasterWidth: number
  rasterHeight: number
  top: number
  tool: PdfTool
  trim: InkRect
  selectable: boolean
  marks: PdfMark[]
  matches: PdfMatch[]
  currentMatch?: PdfMatch
  openMarkId?: string
  onCapture: (dataUrl: string, rect: InkRect) => void
  onTrim: (rect: InkRect) => void
  onMark: (mark: PdfMark, anchor: InkPoint, capture?: PageCapture) => void
  onHighlight: (mark: PdfMark) => void
}

interface Rect extends InkPoint { width: number; height: number }
interface PageCapture { dataUrl: string; rect: InkRect }

function captureRegion(source: HTMLCanvasElement, rect: InkRect, mark?: PdfMark): PageCapture | undefined {
  const [left, top, right, bottom] = rect
  const widthPx = (right - left) * source.width
  const heightPx = (bottom - top) * source.height
  const scale = Math.min(1, 1800 / Math.max(widthPx, heightPx))
  const snapshot = window.document.createElement('canvas')
  snapshot.width = Math.max(1, Math.round(widthPx * scale))
  snapshot.height = Math.max(1, Math.round(heightPx * scale))
  const context = snapshot.getContext('2d')
  if (!context) return undefined
  context.drawImage(source, left * source.width, top * source.height, widthPx, heightPx, 0, 0, snapshot.width, snapshot.height)
  if (mark) inkSnapshot(context, mark.points, rect, mark.kind === 'mark')
  return { dataUrl: snapshot.toDataURL('image/jpeg', 0.91), rect }
}

function PdfPage({ document, page, width, height, rasterWidth, rasterHeight, top, tool, trim, selectable, marks, matches, currentMatch, openMarkId, onCapture, onTrim, onMark, onHighlight }: PdfPageProps) {
  const paper = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const pointer = useRef<{ id: number; kind: PdfTool; points: InkPoint[] } | undefined>(undefined)
  const [selection, setSelection] = useState<Rect>()
  const [drawing, setDrawing] = useState<InkPoint[]>([])
  const [rendered, setRendered] = useState<{ pdfPage?: PDFPageProxy; natural?: ReturnType<PDFPageProxy['getViewport']>; error: string }>({ error: '' })
  const aspect = rendered.natural && rendered.natural.height / rendered.natural.width
  const trimWidth = trim[2] - trim[0]
  const trimHeight = trim[3] - trim[1]
  const displayedWidth = aspect ? Math.min(width / trimWidth, height / trimHeight / aspect) : width / trimWidth
  const displayedHeight = aspect ? displayedWidth * aspect : height / trimHeight
  const boxTool = tool === 'crop' || tool === 'trim'

  useEffect(() => {
    let active = true
    let render: RenderTask | undefined
    void document.getPage(page).then(pdfPage => {
      const node = canvas.current
      if (!active || !node) return undefined
      const natural = pdfPage.getViewport({ scale: 1 })
      const viewport = pdfPage.getViewport({ scale: Math.min(rasterWidth / natural.width / trimWidth, rasterHeight / natural.height / trimHeight) })
      const ratio = Math.min(Math.max(window.devicePixelRatio, 1), 2, 3000 / Math.max(viewport.width, viewport.height))
      const staged = window.document.createElement('canvas')
      staged.width = Math.max(1, Math.round(viewport.width * ratio))
      staged.height = Math.max(1, Math.round(viewport.height * ratio))
      render = pdfPage.render({ canvas: staged, viewport, transform: [ratio, 0, 0, ratio, 0, 0] })
      return render.promise.then(() => {
        if (!active) return
        const context = node.getContext('2d')
        if (!context) throw new Error('无法显示 PDF 页面。')
        node.width = staged.width
        node.height = staged.height
        context.drawImage(staged, 0, 0)
        staged.width = staged.height = 0
        setRendered(previous => previous.pdfPage === pdfPage ? previous : { pdfPage, natural, error: '' })
      })
    }).catch(error => {
      if (active) setRendered(previous => previous.pdfPage ? previous : { error: errorMessage(error) })
    })
    return () => { active = false; render?.cancel() }
  }, [document, page, rasterWidth, rasterHeight, trimWidth, trimHeight])

  function getPoint(event: PointerEvent<HTMLDivElement>): InkPoint {
    const area = event.currentTarget.getBoundingClientRect()
    return {
      x: Math.max(trim[0], Math.min(trim[2], (event.clientX - area.left) / Math.max(1, area.width))),
      y: Math.max(trim[1], Math.min(trim[3], (event.clientY - area.top) / Math.max(1, area.height))),
    }
  }

  function getSelection(event: PointerEvent<HTMLDivElement>): Rect | undefined {
    const start = pointer.current?.points[0]
    if (!start) return undefined
    const end = getPoint(event)
    return { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(start.x - end.x), height: Math.abs(start.y - end.y) }
  }

  function clearGesture() {
    pointer.current = undefined
    setSelection(undefined)
    setDrawing([])
  }

  function extendStroke(points: InkPoint[], next: InkPoint) {
    const last = points.at(-1)!
    if (Math.hypot(next.x - last.x, next.y - last.y) < 0.002) return points
    return [...points.slice(0, MAX_INK_POINTS - 1), next]
  }

  function pickMark(mark: PdfMark, anchor: InkPoint) {
    const source = canvas.current
    const rect = inkBounds(mark.points, displayedWidth, displayedHeight)
    onMark(mark, anchor, source && rendered.pdfPage ? captureRegion(source, rect, mark) : undefined)
  }

  const markToOpen = marks.find(mark => mark.id === openMarkId)
  useEffect(() => {
    const box = paper.current?.getBoundingClientRect()
    if (!markToOpen || !box || !rendered.pdfPage) return
    const { x, y } = markToOpen.points.at(-1)!
    pickMark(markToOpen, { x: box.left + x * box.width, y: box.top + y * box.height })
  }, [markToOpen, rendered.pdfPage])

  function startGesture(event: PointerEvent<HTMLDivElement>) {
    if (tool === 'select' || tool === 'hand' || !rendered.pdfPage || !event.isPrimary || event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    const points = [getPoint(event)]
    pointer.current = { id: event.pointerId, kind: tool, points }
    if (boxTool) setSelection(getSelection(event))
    else setDrawing(points)
  }

  function moveGesture(event: PointerEvent<HTMLDivElement>) {
    const active = pointer.current
    if (!active || active.id !== event.pointerId || active.kind !== tool) return
    if (boxTool) setSelection(getSelection(event))
    else if (tool !== 'note') {
      active.points = extendStroke(active.points, getPoint(event))
      setDrawing(active.points)
    }
  }

  function endGesture(event: PointerEvent<HTMLDivElement>) {
    const active = pointer.current
    if (!active || active.id !== event.pointerId) return
    const bounds = getSelection(event)
    clearGesture()
    if (active.kind !== tool) return
    if (tool === 'magic' || tool === 'note' || tool === 'mark') {
      const points = tool === 'note' ? active.points : extendStroke(active.points, getPoint(event))
      const mark: PdfMark = { id: crypto.randomUUID(), page, points, note: '', kind: tool === 'magic' ? undefined : tool }
      if (tool === 'mark') onHighlight(mark)
      else pickMark(mark, { x: event.clientX, y: event.clientY })
      return
    }
    const source = canvas.current
    if (!boxTool || !bounds || !source || bounds.width * displayedWidth < 8 || bounds.height * displayedHeight < 8) return
    const rect: InkRect = [bounds.x, bounds.y, Math.min(trim[2], bounds.x + bounds.width), Math.min(trim[3], bounds.y + bounds.height)]
    if (tool === 'trim') { onTrim(rect); return }
    const capture = captureRegion(source, rect)
    if (capture) onCapture(capture.dataUrl, capture.rect)
  }

  return <div className="wb-paper-slot" style={{ top, height }} aria-label={`第 ${page} 页`}>
    <div className="wb-paper-clip" style={{ width: displayedWidth * trimWidth, height: displayedHeight * trimHeight }}>
      <div ref={paper} className="wb-paper" style={{ width: displayedWidth, height: displayedHeight, left: -trim[0] * displayedWidth, top: -trim[1] * displayedHeight }}
        onPointerDown={startGesture} onPointerMove={moveGesture} onPointerUp={endGesture}
        onPointerCancel={clearGesture} onLostPointerCapture={clearGesture}>
        <canvas ref={canvas} style={{ visibility: rendered.pdfPage ? 'visible' : 'hidden' }} />
        {rendered.error && <p className="wb-page-error">{rendered.error}</p>}
        {tool === 'select' && selectable && rendered.pdfPage && rendered.natural && <PdfTextPlane pdfPage={rendered.pdfPage} viewport={rendered.natural} width={displayedWidth} />}
        {matches.flatMap((match, index) => match.rects.map(([left, y, right, bottom], segment) => <div key={`${index}:${segment}`}
          className={`wb-pdf-find-mark${match === currentMatch ? ' is-current' : ''}`} aria-hidden="true"
          style={{ left: `${left * 100}%`, top: `${y * 100}%`, width: `${Math.max(0, right - left) * 100}%`, height: `${Math.max(0, bottom - y) * 100}%` }} />))}
        <InkOverlay marks={marks} drawing={tool === 'magic' || tool === 'mark' ? drawing : []} highlight={tool === 'mark'} onPick={pickMark} />
        {boxTool && selection && <div className={`wb-crop-area${tool === 'trim' ? ' wb-trim-area' : ''}`}
          style={{ left: `${selection.x * 100}%`, top: `${selection.y * 100}%`, width: `${selection.width * 100}%`, height: `${selection.height * 100}%` }} />}
      </div>
    </div>
  </div>
}

function PdfTextPlane({ pdfPage, viewport, width }: { pdfPage: PDFPageProxy; viewport: ReturnType<PDFPageProxy['getViewport']>; width: number }) {
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = container.current
    if (!node) return
    let active = true
    let layer: TextLayer | undefined
    void Promise.all([loadPdf(), readPdfText(pdfPage)]).then(([library, textContentSource]) => {
      if (!active) return
      layer = new library.TextLayer({ textContentSource, container: node, viewport })
      return layer.render()
    }).catch(() => { /* Raster preview remains available when text extraction fails. */ })
    return () => {
      active = false
      layer?.cancel()
      node.replaceChildren()
    }
  }, [pdfPage, viewport])
  const style = { width: viewport.width, height: viewport.height, transform: `scale(${width / viewport.width})`, '--total-scale-factor': viewport.userUnit } as CSSProperties
  return <div className="wb-pdf-text-plane" style={style}><div className="wb-pdf-text-layer" ref={container} /></div>
}

function CropIcon() {
  return <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M8 3H4v5m12-5h4v5M4 16v5h4m12-5v5h-4M9 9h6v6H9z" /></svg>
}

function PenIcon() {
  return <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="m4 20 12-12 2 2L6 22Zm9-9 2 2m5-10v4m-2-2h4M8 4v4M6 6h4" /></svg>
}

function PdfIcon() {
  return <svg width="34" height="34" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8zm0 0v5h5m-9 4h4m-4 4h6" /></svg>
}
