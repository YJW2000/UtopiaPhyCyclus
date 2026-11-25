import { useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import './pdf-magic-pen.css'

export type InkPoint = { x: number; y: number }
export type InkRect = [number, number, number, number]
export type PdfMark = { id: string; page: number; points: InkPoint[]; note: string; kind?: 'note' | 'mark' }

const QUICK_NOTES = [
  ['核对公式', '核对标出区域的物理公式、符号和上下标，只修正有误的地方。'],
  ['核对单位', '核对标出区域的数值、单位及量纲，只修正有误的地方。'],
  ['修正文句', '修正标出区域中的错字或表述，保持物理含义和其他内容不变。'],
  ['核对题图', '核对标出区域的题图、方向与标注，只修正有误的图形或说明。'],
] as const

const MAX_PATHS = 4
const MAX_MARKS = 40
export const MAX_INK_POINTS = 240
const NOTE_LIMIT = 300

type MarkStore = { path: string; marks: PdfMark[] }[]

function isPoint(point: unknown): point is InkPoint {
  if (!point || typeof point !== 'object') return false
  const { x, y } = point as InkPoint
  return Number.isFinite(x) && x >= 0 && x <= 1 && Number.isFinite(y) && y >= 0 && y <= 1
}

export function readMarks(key: string | undefined): MarkStore {
  if (!key) return []
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? '[]')
    if (!Array.isArray(value)) return []
    return value.slice(-MAX_PATHS).filter(entry => entry && typeof entry.path === 'string' && Array.isArray(entry.marks))
      .map(entry => ({
        path: entry.path,
        marks: entry.marks.slice(-MAX_MARKS).filter((mark: PdfMark) => mark && typeof mark.id === 'string' &&
          Number.isInteger(mark.page) && mark.page > 0 && typeof mark.note === 'string' && mark.note.length <= NOTE_LIMIT && Array.isArray(mark.points) &&
          mark.points.length > 0 && mark.points.length <= MAX_INK_POINTS && mark.points.every(isPoint)),
      }))
  } catch {
    return []
  }
}

export function saveMarks(key: string | undefined, path: string, store: MarkStore, marks: PdfMark[]): MarkStore {
  const next = store.filter(item => item.path !== path)
  if (marks.length) next.push({ path, marks: marks.slice(-MAX_MARKS) })
  if (next.length > MAX_PATHS) next.shift()
  try {
    if (key) localStorage.setItem(key, JSON.stringify(next))
  } catch {
    // Keep current annotations available if the profile's quota is full.
  }
  return next
}

export function inkBounds(points: InkPoint[], width: number, height: number): InkRect {
  const xs = points.map(point => point.x), ys = points.map(point => point.y)
  // Give a tap or short underline enough text context for source mapping.
  const padX = 18 / width, padY = 14 / height
  return [Math.max(0, Math.min(...xs) - padX), Math.max(0, Math.min(...ys) - padY),
    Math.min(1, Math.max(...xs) + padX), Math.min(1, Math.max(...ys) + padY)]
}

export function inkSnapshot(context: CanvasRenderingContext2D, points: InkPoint[], bounds: InkRect, highlight = false) {
  const [left, top, right, bottom] = bounds
  const renderPoint = ({ x, y }: InkPoint): [number, number] => [
    (x - left) * context.canvas.width / (right - left), (y - top) * context.canvas.height / (bottom - top),
  ]
  const start = renderPoint(points[0])
  context.strokeStyle = context.fillStyle = highlight ? 'rgba(255, 198, 38, .5)' : '#b54128'
  context.lineWidth = highlight ? 10 : Math.max(2, Math.min(4, context.canvas.width / 100))
  context.lineCap = 'round'
  context.lineJoin = 'round'
  context.beginPath()
  if (points.length === 1) {
    context.arc(...start, context.lineWidth * 1.7, 0, Math.PI * 2)
    context.fill()
    return
  }
  context.moveTo(...start)
  for (const point of points.slice(1)) context.lineTo(...renderPoint(point))
  context.stroke()
}

function stroke(points: InkPoint[]) {
  const segments = points.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`)
  // A round-capped tiny segment paints a dot without stretching it with the page.
  if (points.length === 1) segments.push(`l0.000001 0`)
  return segments.join(' ')
}

export function InkOverlay({ marks, drawing, highlight, onPick }: {
  marks: PdfMark[]; drawing: InkPoint[]; highlight: boolean; onPick: (mark: PdfMark, anchor: InkPoint) => void
}) {
  if (!marks.length && !drawing.length) return null
  return <>
    <svg className="wb-ink-layer" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
      {marks.map(mark => <path key={mark.id} className={mark.kind === 'mark' ? 'wb-ink-highlight' : undefined} d={stroke(mark.points)} vectorEffect="non-scaling-stroke" />)}
      {drawing.length > 0 && <path className={`wb-ink-active${highlight ? ' wb-ink-highlight' : ''}`} d={stroke(drawing)} vectorEffect="non-scaling-stroke" />}
    </svg>
    {marks.filter(mark => mark.kind !== 'mark' || mark.note).map((mark, index) => {
      const { x, y } = mark.points.at(-1)!
      return <button key={mark.id} type="button" className="wb-ink-pin"
        title={mark.note} aria-label={`批注 ${index + 1}：${mark.note}`}
        style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
        onPointerDown={event => event.stopPropagation()}
        onClick={event => {
          const rect = event.currentTarget.getBoundingClientRect()
          onPick(mark, { x: rect.left + rect.width / 2, y: rect.bottom })
        }}>{index + 1}</button>
    })}
  </>
}

export function MagicNote({ anchor, initial, error, plain, onClose, onSave }: {
  anchor: InkPoint; initial?: string; error?: string; plain?: boolean; onClose: () => void;
  onSave: (note: string, includeInChat: boolean) => void
}) {
  const [note, setNote] = useState(initial ?? '')
  const dialog = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const element = dialog.current
    if (!element) return
    const gap = 12
    const maximumX = window.innerWidth - element.offsetWidth - gap
    const maximumY = window.innerHeight - element.offsetHeight - gap
    element.style.left = `${Math.max(gap, Math.min(anchor.x + gap, maximumX))}px`
    element.style.top = `${Math.max(gap, Math.min(anchor.y + gap, maximumY))}px`
    element.focus({ preventScroll: true })
  }, [anchor])

  const text = note.trim()
  const title = plain ? 'PDF 批注' : '魔法笔批注'
  return createPortal(<div className="wb-magic-shade" onClick={onClose}>
    <div className="wb-magic-note" ref={dialog} role="dialog" aria-label={title} tabIndex={-1}
      onClick={event => event.stopPropagation()} onKeyDown={event => {
        if (event.key === 'Escape') {
          event.preventDefault(); event.stopPropagation(); onClose()
        } else if (!plain && !/^(INPUT|TEXTAREA)$/.test((event.target as HTMLElement).tagName)) {
          const item = QUICK_NOTES[Number(event.key) - 1]
          if (item) { event.preventDefault(); setNote(item[1]) }
        }
      }}>
      <div className="wb-magic-note-heading"><strong>{title}</strong><button type="button" onClick={onClose} aria-label="关闭批注">×</button></div>
      {!plain && <div className="wb-magic-presets" aria-label="快捷批注">
        {QUICK_NOTES.map(([label, instruction]) => <button key={label} type="button" onClick={() => setNote(instruction)}>{label}</button>)}
      </div>}
      <textarea rows={3} maxLength={NOTE_LIMIT} aria-label="批注内容" placeholder="写下要核对或修改的内容…" value={note} onChange={event => setNote(event.target.value)} />
      {error && <p className="wb-magic-error" role="alert">{error}</p>}
      <div className="wb-magic-note-actions">
        <button type="button" onClick={() => onSave(text, false)} disabled={!text}>保存批注</button>
        <button type="button" className="wb-magic-primary" onClick={() => onSave(text, true)} disabled={!text}>加入对话</button>
      </div>
    </div>
  </div>, document.body)
}
