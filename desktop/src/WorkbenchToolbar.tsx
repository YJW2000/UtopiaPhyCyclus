import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { PdfOutlineItem } from './EditorPdf'
import './workbench-toolbar.css'

const icons = {
  file: 'M6 3h8l4 4v14H6zM14 3v5h4m-9 4h6m-6 4h6',
  changes: 'M6 3h8l4 4v14H6zM14 3v5h4m-9 5h6m-3-3v6',
  outline: 'M8 6h12M8 12h12M8 18h12M4 6h.1M4 12h.1M4 18h.1',
  up: 'm6 15 6-6 6 6', down: 'm6 9 6 6 6-6', left: 'm15 6-6 6 6 6', right: 'm9 6 6 6-6 6',
  minus: 'M5 12h14', plus: 'M5 12h14m-7-7v14', play: 'm8 5 11 7-11 7z',
  chat: 'M4 5h16v11H9l-5 4zm4 5h8m-8 3h6', panel: 'M3 5h18v14H3zm12 0v14m3-9 1.5 2-1.5 2',
  task: 'M12 5H5v14h14v-7m-9 3 2.8-.5L21 6.3 17.7 3 9.5 11z',
  more: 'M5 12h.1m6.9 0h.1m6.9 0h.1', export: 'M8 8H5v12h14V8h-3m-4 6V3M8 7l4-4 4 4',
  bookmarks: 'M7 4h10v16l-5-4-5 4z', bookmark: 'M6 4h9v15l-4.5-3-4.5 3zm12 5h4m-2-2v4',
  close: 'm6 6 12 12M6 18 18 6', expand: 'M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5',
  back: 'm12 6-6 6 6 6M6 12h13', split: 'M3 5h18v14H3zM12 5v14',
  source: 'm9 6-6 6 6 6m6-12 6 6-6 6m-2-14-2 16', sync: 'M19 8a7 7 0 0 0-12-2L4 9m0 0V4m0 5h5m-4 7a7 7 0 0 0 12 2l3-3m0 0v5m0-5h-5',
  find: 'M15 15l5 5m-3-9a6 6 0 1 1-12 0 6 6 0 0 1 12 0z',
  magic: 'm4 17 11-11 3 3L7 20zm8-8 3 3m3-8V2m2 4h2M4 6h4M6 4v4',
  mark: 'm4 15 2.5-1 9-9 3.5 3.5-9 9L6 19zm8.5-7.5 3.5 3.5M4 21h16',
  region: 'M4 8V4h4m4 0h1m3 0h4v4m0 4v1m0 3v4h-4m-4 0h-1m-3 0H4v-4m0-4v-1',
  note: 'M5 4h14v10l-6 6H5zm8 16v-6h6M8 8h8m-8 4h4',
  select: 'm5 3 14 9-7 1-3 7z',
  hand: 'M7 12V6a1.5 1.5 0 0 1 3 0v5-7a1.5 1.5 0 0 1 3 0v7-5a1.5 1.5 0 0 1 3 0v6-2a1.5 1.5 0 0 1 3 0v5c0 3.5-2 6-5.5 6h-1c-2 0-3.5-.7-5-2.8L4 13.5c-.8-1.2.5-2.5 1.6-1.6L7 13',
  crop: 'M7 3v14h14M3 7h14v14',
  undo: 'm8 5-5 5 5 5m-5-5h10a6 6 0 0 1 6 6v3', redo: 'm16 5 5 5-5 5m5-5H11a6 6 0 0 0-6 6v3',
  save: 'M5 4h12l2 2v14H5zM8 4v5h8V4M8 20v-7h8v7', refresh: 'M19 11a7 7 0 0 0-12-4L4 10m0 0V5m0 5h5m-4 3a7 7 0 0 0 12 4l3-3m0 0v5m0-5h-5',
} as const

export type WorkbenchIconName = keyof typeof icons

export interface WorkbenchAction {
  id: string
  label: string
  icon?: WorkbenchIconName
  text?: boolean
  disabled?: boolean
  pressed?: boolean
  onClick: () => void
}

interface PdfControls {
  page: number
  pageCount: number
  outline: PdfOutlineItem[]
  zoom: number
  fit: 'width' | 'page' | 'custom'
  onPage: (page: number) => void
  onZoom: (zoom: number | 'width' | 'page') => void
}

interface ToolbarProps {
  view: 'pdf' | 'changes' | 'source'
  onPdf: () => void
  onChanges: () => void
  changeCount?: number
  pdf?: PdfControls
  status?: string
  title: string
  actions?: WorkbenchAction[]
  fileActions?: WorkbenchAction[]
  layoutActions?: WorkbenchAction[]
  compile: WorkbenchAction
  chat: WorkbenchAction
  newTask: WorkbenchAction
}

export function WorkbenchIcon({ name, size = 16 }: { name: WorkbenchIconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round"><path d={icons[name]} /></svg>
}

function Action({ action, text = false, className = '', children }: { action: WorkbenchAction; text?: boolean; className?: string; children?: ReactNode }) {
  return <button type="button" className={`wb-chrome-btn ${className}`} aria-label={action.label} title={action.label} disabled={action.disabled} aria-pressed={action.pressed} onClick={action.onClick}>
    {action.icon && <WorkbenchIcon name={action.icon} />}{text && <span>{action.label}</span>}{children}
  </button>
}

function Menu({ name, icon, label, disabled = false, children }: { name: string; icon?: WorkbenchIconName; label?: string; disabled?: boolean; children: (close: () => void) => ReactNode }) {
  const root = useRef<HTMLDivElement>(null)
  const control = useRef<HTMLButtonElement>(null)
  const [opened, setOpened] = useState(false)
  const open = opened && !disabled
  const close = () => { setOpened(false); control.current?.focus() }

  useEffect(() => {
    if (!open) return
    const dismiss = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpened(false) }
    window.addEventListener('pointerdown', dismiss, true)
    return () => window.removeEventListener('pointerdown', dismiss, true)
  }, [open])

  return <div ref={root} className="wb-chrome-menu" onKeyDown={event => { if (event.key === 'Escape' && open) { event.stopPropagation(); close() } }}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpened(false) }}>
    <button ref={control} type="button" className="wb-chrome-btn" title={name} aria-label={name} aria-expanded={open} disabled={disabled} onClick={() => setOpened(value => !value)}>
      {icon && <WorkbenchIcon name={icon} />}{label && <span>{label}</span>}{label && <WorkbenchIcon name="down" size={12} />}
    </button>
    {open && <div aria-label={name} className="wb-chrome-popover">{children(close)}</div>}
  </div>
}

function ActionMenu({ name, icon, items }: { name: string; icon: WorkbenchIconName; items: WorkbenchAction[] }) {
  return <Menu name={name} icon={icon}>{close => items.map(item => <Action key={item.id} text={true} action={{ ...item, onClick: () => { close(); item.onClick() } }} />)}</Menu>
}

const zoomLevels = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3]

function PdfControls({ pdf }: { pdf?: PdfControls }) {
  const [draft, setDraft] = useState<string>()
  const cancel = useRef(false)
  const page = pdf?.page ?? 1
  const count = pdf?.pageCount ?? 0
  const zoom = pdf?.zoom ?? 1
  const fit = pdf?.fit ?? 'width'
  const scale = fit === 'width' ? '适合宽度' : fit === 'page' ? '适合页面' : `${Math.round(zoom * 100)}%`
  const outline = pdf?.outline ?? []

  function confirmPage() {
    const value = Number(draft)
    if (!cancel.current && draft?.trim() && Number.isFinite(value)) pdf?.onPage(Math.min(count, Math.max(1, Math.round(value))))
    cancel.current = false
    setDraft(undefined)
  }

  return <>
    <Menu name="PDF 目录" icon="outline" label="目录" disabled={!count}>{close => outline.length ? outline.map(item =>
      <button key={item.id} type="button" className="wb-chrome-btn wb-chrome-outline-item" style={{ paddingInlineStart: `${10 + Math.min(item.depth, 8) * 13}px` }} onClick={() => { close(); pdf?.onPage(item.page) }}><span>{item.title}</span><small>{item.page}</small></button>
    ) : <form className="wb-chrome-page-list" onSubmit={event => { event.preventDefault(); const value = new FormData(event.currentTarget).get('page'); close(); pdf?.onPage(Number(value)) }}>
      <label>转到页码<input name="page" type="number" required min={1} max={count} defaultValue={page} /></label><button type="submit" className="wb-chrome-btn">跳转</button>
    </form>}</Menu>
    <div className="wb-chrome-spacer" />
    <div className="wb-chrome-page-controls" role="group" aria-label="PDF 页码">
      <Action action={{ id: 'previous', label: '上一页', icon: 'up', disabled: !count || page <= 1, onClick: () => pdf?.onPage(page - 1) }} />
      <input aria-label="跳转到页码" inputMode="numeric" size={3} disabled={!count} value={draft ?? (count ? page : '–')} onChange={event => setDraft(event.target.value)} onBlur={confirmPage}
        onKeyDown={event => { if (event.key === 'Escape') { cancel.current = true; event.currentTarget.blur() } else if (event.key === 'Enter') event.currentTarget.blur() }} />
      <span aria-label={`共 ${count} 页`}>/ {count || '–'}</span>
      <Action action={{ id: 'next', label: '下一页', icon: 'down', disabled: !count || page >= count, onClick: () => pdf?.onPage(page + 1) }} />
    </div>
    <div className="wb-chrome-zoom" role="group" aria-label="PDF 缩放">
      <Action action={{ id: 'zoom-out', label: '缩小', icon: 'minus', disabled: !count || zoom <= zoomLevels[0], onClick: () => pdf?.onZoom(Math.max(zoomLevels[0], zoom / 1.2)) }} />
      <Menu name="PDF 缩放选项" label={scale} disabled={!count}>{close => <>
        <Action text={true} action={{ id: 'fit-width', label: '适合宽度', pressed: fit === 'width', onClick: () => { close(); pdf?.onZoom('width') } }} />
        <Action text={true} action={{ id: 'fit-page', label: '适合页面', pressed: fit === 'page', onClick: () => { close(); pdf?.onZoom('page') } }} />
        {zoomLevels.map(level => <Action key={level} text={true} action={{ id: `zoom-${level}`, label: `${level * 100}%`, pressed: fit === 'custom' && Math.abs(zoom - level) < 0.001, onClick: () => { close(); pdf?.onZoom(level) } }} />)}
      </>}</Menu>
      <Action action={{ id: 'zoom-in', label: '放大', icon: 'plus', disabled: !count || zoom >= zoomLevels.at(-1)!, onClick: () => pdf?.onZoom(Math.min(3, zoom * 1.2)) }} />
    </div>
  </>
}

export function WorkbenchToolbar({ view, onPdf, onChanges, changeCount, pdf, status, title, actions, fileActions, layoutActions, compile, chat, newTask }: ToolbarProps) {
  return <header className="wb-chrome" aria-label="题目工作台工具栏" title={title}>
    <div className="wb-chrome-views" role="group" aria-label="查看内容">
      <Action text={true} action={{ id: 'pdf', label: 'PDF', icon: 'file', pressed: view === 'pdf', onClick: onPdf }} />
      <Action text={true} action={{ id: 'changes', label: '改动', icon: 'changes', pressed: view === 'changes', onClick: onChanges }}>
        {Boolean(changeCount) && <small className="wb-chrome-badge" aria-label={`${changeCount} 项待审阅`}>{changeCount}</small>}
      </Action>
    </div>
    <PdfControls pdf={pdf} />
    <div className="wb-chrome-actions">
      <Action className="wb-chrome-compile" action={{ icon: 'play', ...compile }} />
      {actions?.map(item => <Action key={item.id} action={item} text={item.text} />)}
    </div>
    <div className="wb-chrome-actions wb-chrome-task">
      <Action action={{ icon: 'panel', ...chat }} />
      <Action text={true} action={{ icon: 'task', ...newTask }} />
      {layoutActions?.length ? <ActionMenu name="工作台布局" icon="split" items={layoutActions} /> : null}
      {fileActions?.length ? <ActionMenu name="更多操作" icon="more" items={fileActions} /> : null}
    </div>
    {status && <span className="wb-chrome-status" role="status">{status}</span>}
  </header>
}

export function WorkbenchRail({ actions, history, bookmarks }: { actions: WorkbenchAction[]; history?: WorkbenchAction[]; bookmarks?: WorkbenchAction[] }) {
  return <nav className="wb-chrome-rail" aria-label="PDF 工具">
    <div>{actions.map(item => <Action key={item.id} action={item} />)}</div>
    <div className="wb-chrome-rail-bottom">
      {history?.map(item => <Action key={item.id} action={item} />)}
      {bookmarks?.map(item => <Action key={item.id} action={item} />)}
    </div>
  </nav>
}

export function WorkbenchFind({ query, onQuery, index, count, loading, onNavigate, onClose }: { query: string; onQuery: (query: string) => void; index: number; count: number; loading?: boolean; onNavigate: (delta: number) => void; onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { input.current?.focus(); input.current?.select() }, [])
  return <div className="wb-chrome-find" role="search" aria-label="搜索 PDF" onKeyDown={event => { if (event.key === 'Escape') onClose() }}>
    <WorkbenchIcon name="find" /><input ref={input} type="search" aria-label="搜索 PDF 文字" placeholder="搜索 PDF 文字" value={query} onChange={event => onQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) onNavigate(event.shiftKey ? -1 : 1) }} />
    <span aria-live="polite">{loading ? '搜索中…' : query.trim() ? count ? `${index + 1} / ${count}` : '无匹配' : ''}</span>
    <Action action={{ id: 'match-previous', label: '上一个匹配', icon: 'up', disabled: !count, onClick: () => onNavigate(-1) }} />
    <Action action={{ id: 'match-next', label: '下一个匹配', icon: 'down', disabled: !count, onClick: () => onNavigate(1) }} />
    <Action action={{ id: 'match-close', label: '关闭搜索', icon: 'close', onClick: onClose }} />
  </div>
}

export function WorkbenchBookmarks({ pages, page, onPage, onToggle, onClose }: { pages: number[]; page: number; onPage: (page: number) => void; onToggle: () => void; onClose: () => void }) {
  return <section className="wb-chrome-bookmarks" aria-label="页面书签">
    <header><strong>页面书签</strong><Action action={{ id: 'bookmark-close', label: '关闭书签', icon: 'close', onClick: onClose }} /></header>
    <Action text={true} action={{ id: 'bookmark-toggle', label: pages.includes(page) ? '移除本页书签' : '添加本页书签', icon: 'bookmark', onClick: onToggle }} />
    <div>{pages.map(item => <button key={item} type="button" className="wb-chrome-btn" aria-current={item === page ? 'page' : undefined} onClick={() => onPage(item)}>第 {item} 页</button>)}</div>
  </section>
}
