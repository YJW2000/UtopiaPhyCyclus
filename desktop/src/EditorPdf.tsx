import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent } from 'react'
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { errorMessage } from './editorContract'
import type { EditorAttachment, PdfPanel } from './editorContract'

export interface PdfPosition { page: number; offset: number; zoom: number }
export interface PdfNavigation { page: number; offset?: number; stamp: number }

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
  onNavigate: (page: number, offset: number, fromScroll: boolean) => void
  onZoom: (zoom: number) => void
}

interface PdfReader { document: PDFDocumentProxy; aspect: number }

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

export function EditorPdf({ path, label, panel, position, navigation, pending, empty, crop, onCropToggle, onCapture, onNavigate, onZoom }: PdfProps) {
  const [reader, setReader] = useState<PdfReader>()
  const [failure, setFailure] = useState('')
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [skip, setSkip] = useState(1)
  const [pageDraft, setPageDraft] = useState<string | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const navigate = useRef(onNavigate)
  const positionRef = useRef(position)
  const appliedNavigation = useRef(0)
  const expectedScroll = useRef<number | null>(null)
  const pageInput = useRef<HTMLInputElement>(null)
  navigate.current = onNavigate
  positionRef.current = position

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
        if (active) setReader({ document, aspect: viewport.height / viewport.width || DEFAULT_ASPECT })
      })().catch(error => { if (active) setFailure(errorMessage(error)) })
    }
    return () => {
      active = false
      void task?.destroy().catch(() => {})
    }
  }, [path])

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
  const pageWidth = Math.max(100, Math.min(930, size.width - 36) * position.zoom)
  const pageHeight = Math.ceil(pageWidth * (reader?.aspect ?? DEFAULT_ASPECT))
  const step = pageHeight + PAGE_GAP

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
    jump(positionRef.current.page, positionRef.current.offset)
  }, [reader, step, size.height, navigation?.stamp])

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

  return <section className="wb-pdf" aria-label={label}>
    <div className="wb-pdf-tools">
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
          <button type="button" className="wb-btn wb-crop-toggle" aria-pressed={crop} title="拖动框选 PDF 区域，添加为聊天截图" onClick={onCropToggle}>
            <CropIcon /> <span>{crop ? '退出框选' : '框选截图'}</span>
          </button>
        </div>
      </>}
    </div>
    <div className={`wb-pdf-scroll${crop ? ' wb-pdf-cropping' : ''}`} ref={scroller} onScroll={scrolled} tabIndex={0} aria-label={`${label}滚动区`} onKeyDown={event => {
      if (event.key === 'Escape' && crop) onCropToggle()
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
          document={reader.document} key={page} page={page} width={pageWidth} height={pageHeight} top={(page - 1) * step}
          crop={crop} onCapture={(dataUrl, rect) => onCapture({ id: globalThis.crypto.randomUUID(), title: `${label} · 第 ${page} 页`, page, panel, dataUrl, rect })}
        />)}
      </div>}
    </div>
  </section>
}

interface PdfPageProps {
  document: PDFDocumentProxy
  page: number
  width: number
  height: number
  top: number
  crop: boolean
  onCapture: (dataUrl: string, rect: [number, number, number, number]) => void
}

interface Point { x: number; y: number }
interface Rect extends Point { width: number; height: number }

function PdfPage({ document, page, width, height, top, crop, onCapture }: PdfPageProps) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const pointer = useRef<Point | null>(null)
  const [selection, setSelection] = useState<Rect>()
  const [rendered, setRendered] = useState({ width, height, error: '', ready: false })

  useEffect(() => {
    let active = true
    let render: RenderTask | undefined
    setRendered(previous => ({ ...previous, ready: false, error: '' }))
    void document.getPage(page).then(pdfPage => {
      const node = canvas.current
      if (!active || !node) return undefined
      const natural = pdfPage.getViewport({ scale: 1 })
      const viewport = pdfPage.getViewport({ scale: Math.min(width / natural.width, height / natural.height) })
      const ratio = Math.min(Math.max(window.devicePixelRatio, 1), 2, 3000 / Math.max(viewport.width, viewport.height))
      node.width = Math.max(1, Math.round(viewport.width * ratio))
      node.height = Math.max(1, Math.round(viewport.height * ratio))
      render = pdfPage.render({ canvas: node, viewport, transform: [ratio, 0, 0, ratio, 0, 0] })
      return render.promise.then(() => {
        if (active) setRendered({ width: viewport.width, height: viewport.height, error: '', ready: true })
      })
    }).catch(error => {
      if (active) setRendered({ width, height, error: errorMessage(error), ready: false })
    })
    return () => { active = false; render?.cancel() }
  }, [document, page, width, height])

  function getPoint(event: PointerEvent<HTMLDivElement>): Point {
    const area = event.currentTarget.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(area.width, event.clientX - area.left)),
      y: Math.max(0, Math.min(area.height, event.clientY - area.top)),
    }
  }

  function getSelection(event: PointerEvent<HTMLDivElement>): Rect | undefined {
    const start = pointer.current
    if (!start) return undefined
    const end = getPoint(event)
    return { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(start.x - end.x), height: Math.abs(start.y - end.y) }
  }

  function endSelection(event: PointerEvent<HTMLDivElement>) {
    const bounds = getSelection(event)
    pointer.current = null
    setSelection(undefined)
    const source = canvas.current
    if (!bounds || !source || bounds.width < 8 || bounds.height < 8) return
    const scaleX = source.width / rendered.width
    const scaleY = source.height / rendered.height
    const widthPx = bounds.width * scaleX
    const heightPx = bounds.height * scaleY
    const scale = Math.min(1, 1800 / Math.max(widthPx, heightPx))
    const snapshot = window.document.createElement('canvas')
    snapshot.width = Math.max(1, Math.round(widthPx * scale))
    snapshot.height = Math.max(1, Math.round(heightPx * scale))
    const context = snapshot.getContext('2d')
    if (!context) return
    context.drawImage(source, bounds.x * scaleX, bounds.y * scaleY, widthPx, heightPx, 0, 0, snapshot.width, snapshot.height)
    onCapture(snapshot.toDataURL('image/jpeg', 0.91), [
      bounds.x / rendered.width, bounds.y / rendered.height,
      (bounds.x + bounds.width) / rendered.width, (bounds.y + bounds.height) / rendered.height,
    ])
  }

  return <div className="wb-paper-slot" style={{ top, height }} aria-label={`第 ${page} 页`}>
    <div className="wb-paper" style={{ width: rendered.width, height: rendered.height }}
      onPointerDown={event => {
        if (!crop || !rendered.ready || event.button !== 0) return
        event.preventDefault()
        event.currentTarget.setPointerCapture(event.pointerId)
        pointer.current = getPoint(event)
        setSelection(getSelection(event))
      }}
      onPointerMove={event => { if (pointer.current) setSelection(getSelection(event)) }}
      onPointerUp={endSelection}
      onPointerCancel={() => { pointer.current = null; setSelection(undefined) }}>
      <canvas ref={canvas} style={{ visibility: rendered.ready ? 'visible' : 'hidden' }} />
      {rendered.error && <p className="wb-page-error">{rendered.error}</p>}
      {crop && selection && <div className="wb-crop-area" style={{ left: selection.x, top: selection.y, width: selection.width, height: selection.height }} />}
    </div>
  </div>
}

function CropIcon() {
  return <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M8 3H4v5m12-5h4v5M4 16v5h4m12-5v5h-4M9 9h6v6H9z" /></svg>
}

function PdfIcon() {
  return <svg width="34" height="34" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8zm0 0v5h5m-9 4h4m-4 4h6" /></svg>
}
