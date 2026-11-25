import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'
import type { RefProxy, TextContent, TextItem } from 'pdfjs-dist/types/src/display/api'
import type { InkRect } from './PdfMagicPen'

export type PdfTool = 'select' | 'hand' | 'crop' | 'magic' | 'note' | 'mark' | 'trim'
export type PdfFit = 'width' | 'page' | 'custom'
export type PdfOutlineItem = { id: string; title: string; page: number; depth: number }
export type PdfViewNote = { id: string; page: number; label: string }
export type PdfViewState = {
  pageCount: number
  outline: PdfOutlineItem[]
  matches: number
  searching: boolean
  notes: PdfViewNote[]
  canUndo: boolean
  canRedo: boolean
}

interface OutlineNode { title: string; dest: string | unknown[] | null; items: OutlineNode[] }

async function destinationPage(document: PDFDocumentProxy, target: OutlineNode['dest']) {
  try {
    const destination = typeof target === 'string' ? await document.getDestination(target) : target
    const ref: unknown = destination?.[0]
    if (typeof ref === 'number') return ref + 1
    if (ref && typeof ref === 'object' && 'num' in ref && 'gen' in ref) {
      const pageRef = ref as RefProxy
      return document.cachedPageNumber(pageRef) ?? (await document.getPageIndex(pageRef) + 1)
    }
  } catch { /* Ignore bookmarks that point to a deleted page. */ }
  return 0
}

export async function readPdfOutline(document: PDFDocumentProxy): Promise<PdfOutlineItem[]> {
  async function visit(nodes: OutlineNode[], level: number, prefix: string): Promise<PdfOutlineItem[]> {
    const entries: PdfOutlineItem[] = []
    for (const [index, node] of nodes.entries()) {
      const id = `${prefix}${index}`
      const children = level < 10 ? await visit(node.items, level + 1, `${id}.`) : []
      const destination = await destinationPage(document, node.dest)
      const page = destination >= 1 && destination <= document.numPages ? destination : children[0]?.page
      if (page) entries.push({ id, title: node.title, page, depth: level })
      entries.push(...children)
    }
    return entries
  }
  return visit((await document.getOutline()) ?? [], 0, '')
}

// Share the extracted page text between search and the selectable PDF.js layer.
const textCache = new WeakMap<PDFPageProxy, Promise<TextContent>>()
export function readPdfText(page: PDFPageProxy) {
  let pending = textCache.get(page)
  if (!pending) {
    pending = page.getTextContent()
    textCache.set(page, pending)
    void pending.catch(() => textCache.delete(page))
  }
  return pending
}

export type PdfMatch = { page: number; rects: InkRect[] }
type TextRun = { start: number; end: number; glyphs: number[]; count: number; item: TextItem }
type IndexedPage = { text: string; runs: TextRun[] }
const indexCache = new WeakMap<PDFPageProxy, Promise<IndexedPage>>()

const searchGlyph = (glyph: string) => glyph.normalize('NFKC').toLowerCase().replace(/\s/gu, '')
export const normalizePdfQuery = (query: string) => Array.from(query, searchGlyph).join('')

export const FULL_PAGE: InkRect = [0, 0, 1, 1]

function validTrim(value: unknown): value is InkRect {
  return Array.isArray(value) && value.length === 4 && value.every(edge => typeof edge === 'number' && edge >= 0 && edge <= 1) &&
    value[2] - value[0] >= 0.2 && value[3] - value[1] >= 0.2
}

export function readPdfTrim(key: string | undefined, path: string | null | undefined): InkRect {
  if (key && path) try {
    const entries: unknown = JSON.parse(localStorage.getItem(key) ?? '[]')
    const found = Array.isArray(entries) ? entries.find(entry => entry?.path === path) : undefined
    if (validTrim(found?.rect)) return found.rect
  } catch { /* A profile may have disabled storage. */ }
  return FULL_PAGE
}

export function savePdfTrim(key: string | undefined, path: string | null | undefined, rect: InkRect) {
  if (!key || !path || !validTrim(rect)) return
  try {
    const entries: unknown = JSON.parse(localStorage.getItem(key) ?? '[]')
    const remainder = Array.isArray(entries) ? entries.filter(entry => entry?.path !== path && validTrim(entry?.rect)).slice(-3) : []
    if (rect !== FULL_PAGE) remainder.push({ path, rect })
    localStorage.setItem(key, JSON.stringify(remainder))
  } catch { /* Keep the current preview usable if persistence fails. */ }
}

function indexPage(page: PDFPageProxy) {
  let pending = indexCache.get(page)
  if (!pending) {
    pending = readPdfText(page).then(content => {
      const runs: TextRun[] = []
      let text = ''
      for (const item of content.items) {
        if (!('str' in item)) continue
        const chars = Array.from(item.str)
        const start = text.length
        const glyphs: number[] = []
        chars.forEach((char, index) => {
          const glyph = searchGlyph(char)
          text += glyph
          for (let unit = 0; unit < glyph.length; unit++) glyphs.push(index)
        })
        if (text.length > start) runs.push({ start, end: text.length, glyphs, count: chars.length, item })
      }
      return { text, runs }
    })
    indexCache.set(page, pending)
    void pending.catch(() => indexCache.delete(page))
  }
  return pending
}

function runRect(run: TextRun, start: number, end: number, viewport: ReturnType<PDFPageProxy['getViewport']>): InkRect {
  const [a, b, c, d, x, y] = run.item.transform
  const length = Math.hypot(a, b) || 1
  const fontHeight = Math.hypot(c, d) || length
  let from = run.glyphs[Math.max(0, start - run.start)] / run.count
  let to = (run.glyphs[Math.min(run.glyphs.length - 1, end - run.start - 1)] + 1) / run.count
  if (run.item.dir === 'rtl') [from, to] = [1 - to, 1 - from]
  const advance = Math.abs(run.item.width) || fontHeight * run.count / 2
  const coordinates = [from, to].flatMap(fraction => [-0.15, 0.9].map(ascender =>
    viewport.convertToViewportPoint(
      x + a / length * advance * fraction - b / length * fontHeight * ascender,
      y + b / length * advance * fraction + a / length * fontHeight * ascender,
    ),
  ))
  const xs = coordinates.map(point => point[0] / viewport.width)
  const ys = coordinates.map(point => point[1] / viewport.height)
  return [Math.max(0, Math.min(...xs)), Math.max(0, Math.min(...ys)), Math.min(1, Math.max(...xs)), Math.min(1, Math.max(...ys))]
}

export async function findPdfMatches(document: PDFDocumentProxy, query: string, cancelled: () => boolean) {
  const matches: PdfMatch[] = []
  for (let pageNumber = 1; pageNumber <= document.numPages && !cancelled(); pageNumber++) {
    try {
      const page = await document.getPage(pageNumber)
      if (cancelled()) break
      const { text, runs } = await indexPage(page)
      const viewport = page.getViewport({ scale: 1 })
      let start = text.indexOf(query)
      let firstRun = 0
      while (start >= 0 && !cancelled()) {
        const end = start + query.length
        while (runs[firstRun]?.end <= start) firstRun++
        const rects: InkRect[] = []
        for (let index = firstRun; index < runs.length && runs[index].start < end; index++) {
          rects.push(runRect(runs[index], start, end, viewport))
        }
        matches.push({ page: pageNumber, rects })
        start = text.indexOf(query, end)
      }
    } catch { /* Continue searching the pages that provide readable text. */ }
  }
  return matches
}
