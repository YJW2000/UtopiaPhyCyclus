import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from 'react'
import logo from '../../static/sidebar.webp'
import { Browse, Dashboard, emptySummary, Paper } from './Bank'
import type { Question, Summary } from './Bank'
import ContextChat from './ContextChat'
import FileInfoDialog from './FileInfoDialog'
import type { QuestionFileInfo } from './FileInfoDialog'
import PaneDivider from './PaneDivider'
import SettingsConnections from './SettingsConnections'
import type { ClipboardImage } from './bridge'
import type { EditorDocument } from './editorContract'

const WORKBENCH_MIN = 400
const BROWSE_MIN = 170
const WORKBENCH_FRACTION = 0.82
const Editor = lazy(() => import('./Editor'))
const ArchitectureGuide = lazy(() => import('./ArchitectureGuide'))
const PapilotWorkspace = lazy(() => import('./PapilotWorkspace'))
const pages = {
  stats: ['📊', '数据统计'],
  entry: ['📝', '图片识别'],
  tex: ['📄', 'TeX录入'],
  pdf: ['📑', 'PDF录入'],
  browse: ['🔍', '全局浏览'],
  papilot: ['🖋️', 'Papilot 工作台'],
  exam: ['🖨️', '组卷排版'],
  search: ['🔎', '题目查找'],
  intro: ['📘', '架构介绍'],
  manual: ['📖', '规范指南'],
  settings: ['⚙️', '设置'],
} as const
type Page = keyof typeof pages
type LibraryCheck = { integrity: string; total: number; missing: string[] }
type MaintenanceCounts = { added: number; updated: number; missing: number; skipped: number }
type MaintenanceRun = { finishedAt: string } & (
  | { ok: true; result: MaintenanceCounts }
  | { ok: false; error?: string }
)
type MaintenanceStatus = { startup: MaintenanceRun | null; shutdown: MaintenanceRun | null }
type Prefs = {
  skin: string
  mode: string
  library: string
  provider: string
  tokens: Record<string, string>
  skins: Record<string, { name: string; accent: string }>
}
const call = window.utopia.call
const errorText = (e: unknown) =>
  String(e instanceof Error ? e.message : e).replace(
    /^Error invoking remote method '[^']+': Error: /,
    ''
  )
const filename = (path: string) => path.split(/[\\/]/).pop() || path

async function readImageFile(file: File): Promise<ClipboardImage> {
  const jpeg = file.type === 'image/jpeg' || (!file.type && /\.jpe?g$/i.test(file.name))
  const png = file.type === 'image/png' || (!file.type && /\.png$/i.test(file.name))
  if (!jpeg && !png) throw new Error('请选择 PNG 或 JPG 图片。')
  const type = jpeg ? 'jpeg' : 'png'
  const data = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const body = String(reader.result).split(',')[1]
      if (body) resolve(body)
      else reject(new Error('图片文件为空。'))
    }
    reader.onerror = () => reject(new Error('无法读取图片文件。'))
    reader.readAsDataURL(file)
  })
  return { name: file.name || `剪贴板图片.${type}`, type, data }
}

function pasteGoesToControl(target: EventTarget | null) {
  if (!(target instanceof Element)) return false
  return Boolean(target.closest('.context-chat, input, textarea, select, [role="textbox"]')) ||
    (target instanceof HTMLElement && target.isContentEditable)
}

export default function App() {
  const [page, setPage] = useState<Page>('stats'),
    [prefs, setPrefs] = useState<Prefs | null>(null),
    [summary, setSummary] = useState<Summary>(emptySummary),
    [ready, setReady] = useState(false)
  const [doc, setDoc] = useState<EditorDocument | null>(null),
    [editing, setEditing] = useState(false),
    [recent, setRecent] = useState<EditorDocument[]>([])
  const [workbenchQuestion, setWorkbenchQuestion] = useState<Question>()
  const [workbenchOpen, setWorkbenchOpen] = useState(false)
  const [workbenchWidth, setWorkbenchWidth] = useState(() => {
    const stored = Number(localStorage.getItem('browse.workbench.width'))
    return stored > 0 && Number.isFinite(stored) ? stored : 780
  })
  const [fileInfo, setFileInfo] = useState<{ path: string; document: EditorDocument }>()
  const [browseRevision, setBrowseRevision] = useState(0)
  const workbenchId = useId()
  const [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [collapsed, setCollapsed] = useState(
      () => localStorage.getItem('nav.compact') === 'yes'
    )
  const [query, setQuery] = useState(''),
    [rows, setRows] = useState<Question[]>([]),
    [basket, setBasket] = useState<Question[]>([])
  const [legacy, setLegacy] = useState(''),
    [legacyPending, setLegacyPending] = useState(false),
    [check, setCheck] = useState<LibraryCheck>()
  const [maintenance, setMaintenance] = useState<MaintenanceStatus>(),
    [maintenanceError, setMaintenanceError] = useState('')
  const routeGeneration = useRef(0),
    imageGeneration = useRef<number | null>(null),
    fileInput = useRef<HTMLInputElement>(null)
  const mainScroll = useRef<HTMLDivElement>(null)
  const [paste, setPaste] = useState(''),
    [recentLoading, setRecentLoading] = useState(false)
  function theme(p: Prefs) {
    setPrefs(p)
    for (const [k, v] of Object.entries(p.tokens))
      document.documentElement.style.setProperty(k, v)
    void window.utopia.tintWindowControls(p.tokens['--mc-text']).catch(() => {})
    localStorage.setItem('desktop.theme', JSON.stringify(p.tokens))
  }
  function summaryLoad() {
    return call('library.summary')
      .then((r: Summary) => {
        setSummary(r)
        setReady(true)
      })
      .catch((e: unknown) => setError(errorText(e)))
  }
  const drafts = useCallback(() => {
    setRecentLoading(true)
    return call('workspace.list')
      .then((r: any) =>
        setRecent(Array.isArray(r) ? r : r.documents || r.items || [])
      )
      .catch((e: unknown) => setError(errorText(e)))
      .finally(() => setRecentLoading(false))
  }, [])
  useEffect(() => {
    void call('ui.load')
      .then((p: Prefs) => {
        theme(p)
        try {
          setBasket(
            JSON.parse(localStorage.getItem('paper:' + p.library) || '[]')
          )
        } catch {
          setBasket([])
        }
      })
      .catch((e: unknown) => setError(errorText(e)))
    void summaryLoad()
  }, [])
  useEffect(() => {
    if (['tex', 'pdf', 'entry'].includes(page) && !editing) void drafts()
  }, [page, editing, drafts])
  useEffect(() => {
    const timer = setTimeout(() => localStorage.setItem('browse.workbench.width', String(workbenchWidth)), 150)
    return () => clearTimeout(timer)
  }, [workbenchWidth])
  const showingSettings = page === 'settings' && !editing && !legacy
  useEffect(() => {
    if (!showingSettings) return
    let active = true
    setMaintenanceError('')
    void call<MaintenanceStatus>('library.maintenanceStatus')
      .then((status) => {
        if (active) setMaintenance(status)
      })
      .catch((error: unknown) => {
        if (active) setMaintenanceError(errorText(error))
      })
    return () => { active = false }
  }, [showingSettings])
  function navigate(next: Page) {
    routeGeneration.current++
    setPage(next)
    setEditing(false)
    setLegacy('')
    setLegacyPending(false)
    setBusy('')
    setError('')
    mainScroll.current?.scrollTo(0, 0)
    if (next === 'stats') void summaryLoad()
  }
  const work = useCallback(async (label: string, op: () => Promise<void>) => {
    const token = routeGeneration.current
    setBusy(label)
    setError('')
    try {
      await op()
    } catch (e) {
      if (token === routeGeneration.current) setError(errorText(e))
    } finally {
      if (token === routeGeneration.current) setBusy('')
    }
  }, [])
  const loadWorkspace = useCallback(async (params: object, route: Page, id?: string, question?: Question) => {
    performance.clearMarks('document-open')
    performance.mark('document-open')
    const token = routeGeneration.current
    const result = await call<EditorDocument>(
      id ? 'workspace.open' : 'workspace.create',
      id ? { id } : params
    )
    if (token !== routeGeneration.current) return
    setDoc(result)
    setWorkbenchQuestion(question)
    setWorkbenchOpen(Boolean(question))
    setEditing(!question)
    setPage(route)
    setLegacy('')
  }, [])
  function openWorkspace(params: object, route: Page, id?: string) {
    void work('正在打开题目…', () => loadWorkspace(params, route, id))
  }
  async function choose(kind: 'tex' | 'pdf') {
    try {
      const path = await window.utopia.openFile(kind)
      if (path)
        openWorkspace(
          kind === 'pdf'
            ? { pdfPath: path, title: filename(path) }
            : { path, title: filename(path) },
          kind
        )
    } catch (e) {
      setError(errorText(e))
    }
  }
  function demo() {
    void work('正在打开演示题…', async () => {
      const sample = await call('library.demo')
      openWorkspace(sample, 'tex')
    })
  }
  function changeBasket(list: Question[]) {
    setBasket(list)
    if (prefs)
      localStorage.setItem('paper:' + prefs.library, JSON.stringify(list))
  }
  function add(row: Question) {
    if (!basket.some((r) => r.path === row.path)) changeBasket([...basket, row])
  }
  function open(row: Question) {
    if (page !== 'browse' && page !== 'search') {
      openWorkspace({ questionPath: row.path }, 'browse')
      return
    }
    routeGeneration.current++
    setFileInfo(undefined)
    void work('正在打开题目…', () => loadWorkspace({ questionPath: row.path }, page, undefined,
      row))
  }
  function savedFileInfo(file: QuestionFileInfo, document: EditorDocument) {
    const previousPath = fileInfo?.path
    const update = (question: Question) => question.path === previousPath ? {
      ...question, path: file.path, year: file.year, paper: file.paper,
      number: file.number, subject: file.subject, tags: file.tags,
    } : question
    setWorkbenchQuestion((question) => question && update(question))
    setDoc(document)
    changeBasket(basket.map(update))
    setFileInfo(undefined)
    setBrowseRevision((revision) => revision + 1)
    void summaryLoad()
  }
  function openLegacy(route: Page | 'tools' | 'tags') {
    const token = ++routeGeneration.current
    setLegacyPending(true)
    setError('')
    window.utopia
      .legacy(route)
      .then((url) => {
        if (routeGeneration.current === token) setLegacy(url)
      })
      .catch((e) => {
        if (routeGeneration.current === token) setError(errorText(e))
      })
      .finally(() => {
        if (routeGeneration.current === token) setLegacyPending(false)
      })
  }
  const importImage = useCallback((read: () => Promise<ClipboardImage | null>) => {
    const token = routeGeneration.current
    if (busy || imageGeneration.current === token) return
    imageGeneration.current = token
    void work('正在读取图片…', async () => {
      try {
        const image = await read()
        if (token !== routeGeneration.current) return
        if (!image) throw new Error('剪贴板中没有图片。请先复制图片或截图。')
        const { path } = await call<{ path: string }>('library.imageInput', image)
        if (token !== routeGeneration.current) return
        await loadWorkspace({ pdfPath: path, title: image.name, inputKind: 'image' }, 'entry')
      } finally {
        if (imageGeneration.current === token) imageGeneration.current = null
      }
    })
  }, [busy, loadWorkspace, work])
  useEffect(() => {
    if (page !== 'entry' || editing || legacy || legacyPending) return
    function shortcut(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.altKey || event.shiftKey ||
          !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'v' ||
          pasteGoesToControl(event.target)) return
      event.preventDefault()
      if (!event.repeat) importImage(window.utopia.readClipboardImage)
    }
    function pasteImage(event: ClipboardEvent) {
      if (event.defaultPrevented || pasteGoesToControl(event.target)) return
      const clip = event.clipboardData
      if (!clip) return
      const file = Array.from(clip.files).find(file => file.type.startsWith('image/')) ??
        Array.from(clip.items).find(item => item.kind === 'file' && item.type.startsWith('image/'))?.getAsFile()
      if (!file) return
      event.preventDefault()
      importImage(() => readImageFile(file))
    }
    window.addEventListener('keydown', shortcut)
    window.addEventListener('paste', pasteImage)
    return () => {
      window.removeEventListener('keydown', shortcut)
      window.removeEventListener('paste', pasteImage)
    }
  }, [editing, importImage, legacy, legacyPending, page])
  useEffect(() => window.utopia.onPapilotShortcut(navigate), [])
  const imageBusy = page === 'entry' && Boolean(busy)
  const actionProps = { open, add, basket }
  const docking = Boolean(doc && workbenchQuestion && !legacy && (page === 'browse' || page === 'search'))
  const rightVisible = !editing && docking && workbenchOpen
  const maximumDock = `min(${WORKBENCH_FRACTION * 100}%, calc(100% - ${BROWSE_MIN}px))`
  const dockSize = `clamp(min(${WORKBENCH_MIN}px, ${maximumDock}), ${workbenchWidth}px, ${maximumDock})`
  return (
    <div className={'app-shell' + (collapsed ? ' compact' : '')}>
      <aside className="app-nav">
        <div className="brand">
          <img src={logo} alt="乌托邦西西弗斯图标" />
          <div>
            <b>乌托邦</b>
            <strong>高中物理题库</strong>
          </div>
        </div>
        <nav aria-label="题库导航">
          {(Object.entries(pages) as [Page, readonly [string, string]][]).map(
            ([id, [icon, name]]) => (
              <button
                key={id}
                data-page={id}
                title={name}
                aria-current={page === id ? 'page' : undefined}
                onClick={() => navigate(id)}
              >
                <span>{icon}</span>
                <span>{name}</span>
              </button>
            )
          )}
        </nav>
        <button
          className="collapse-nav"
          aria-label={collapsed ? '展开导航' : '收起导航'}
          onClick={() => {
            setCollapsed(!collapsed)
            localStorage.setItem('nav.compact', collapsed ? 'no' : 'yes')
          }}
        >
          {collapsed ? '›' : '‹　收起导航'}
        </button>
      </aside>
      <main className="workspace">
        {!editing && (
          <header className="top-strip">
            <span>
              高中物理题库 <b> / </b> {pages[page][1]}
            </span>
            <div>
              {doc && (
                <button
                  disabled={imageBusy}
                  onClick={() => {
                    setEditing(true)
                    setLegacy('')
                  }}
                >
                  返回当前文稿
                </button>
              )}
            </div>
          </header>
        )}
        {error && (
          <div className="error-strip" role="alert">
            {error}
            <button aria-label="关闭提示" onClick={() => setError('')}>
              ×
            </button>
          </div>
        )}
        {busy && (
          <div className="busy-strip" role="status">
            {busy}
          </div>
        )}
        <div className={'workspace-body' + (rightVisible ? ' workspace-with-dock' : '')}>
          {!editing && page === 'papilot' && <Suspense fallback={<p>正在载入 Papilot 工作台…</p>}>
            <PapilotWorkspace />
          </Suspense>}
          {!editing && page !== 'papilot' && <>
            <div ref={mainScroll} className={'main-scroll' + (legacy ? ' embedded' : '')}>
              {legacy ? (
                <>
                  <div className="advanced-bar">
                    <span>{pages[page][1]}</span>
                    <button
                      onClick={() => {
                        setLegacy('')
                        void summaryLoad()
                      }}
                    >
                      返回工作台
                    </button>
                  </div>
                  <iframe
                    title={pages[page][1]}
                    src={legacy}
                    allow="microphone; clipboard-read"
                  />
                </>
              ) : (
                <div className="page-content">
                  {page === 'stats' && (
                    <Dashboard
                      {...actionProps}
                      {...{ summary, ready, demo }}
                      browse={() => navigate('browse')}
                    />
                  )}
                  {(page === 'browse' || page === 'search') && (
                    <Browse
                      {...actionProps}
                      {...{ summary, query, setQuery }}
                      search={page === 'search'}
                      onRows={setRows}
                      revision={browseRevision}
                      previewPath={docking ? workbenchQuestion?.path : undefined}
                    />
                  )}
                  {page === 'exam' && (
                    <Paper
                      basket={basket}
                      setBasket={changeBasket}
                      browse={() => navigate('browse')}
                      assemble={(title, answers) =>
                        void work('正在组卷…', async () => {
                          const r = await call('library.assemble', {
                            title,
                            answers,
                            paths: basket.map((r) => r.path),
                          })
                          openWorkspace(r, 'exam')
                        })
                      }
                    />
                  )}
                  {['tex', 'pdf', 'entry'].includes(page) && (
                    <>
                      <div className="page-head">
                        <h1>{pages[page].join(' ')}</h1>
                        <button disabled={imageBusy} onClick={demo}>打开演示题 ↗</button>
                      </div>
                      <section className="mc-card intake">
                        <span aria-hidden="true">{pages[page][0]}</span>
                        <h2>
                          {page === 'pdf'
                            ? '打开原始 PDF'
                            : page === 'tex'
                              ? '打开 LaTeX 文稿'
                              : '从题目图片录入'}
                        </h2>
                        <p>
                          {page === 'pdf'
                            ? '先阅读原卷，再选择页码重建可编辑题目。'
                            : page === 'tex'
                              ? '载入文稿后，可在排版页上框选并修改。'
                              : '按 Ctrl+V 粘贴截图，或选择 PNG、JPG 图片。打开后点击「开始识别图片」生成可编辑文稿。'}
                        </p>
                        {page === 'entry' ? (
                          <div className="intake-actions">
                            <button
                              className="primary"
                              disabled={imageBusy || legacyPending}
                              onClick={() => importImage(window.utopia.readClipboardImage)}
                            >
                              粘贴图片
                            </button>
                            <button disabled={imageBusy || legacyPending} onClick={() => fileInput.current?.click()}>
                              选择图片
                            </button>
                            <input
                              ref={fileInput}
                              hidden
                              type="file"
                              accept="image/jpeg,image/png"
                              onChange={(event) => {
                                const file = event.target.files?.[0]
                                event.target.value = ''
                                if (file) importImage(() => readImageFile(file))
                              }}
                            />
                          </div>
                        ) : (
                          <button className="primary" onClick={() => void choose(page === 'pdf' ? 'pdf' : 'tex')}>
                            {page === 'pdf' ? '选择 PDF 文件' : '选择 TeX 文件'}
                          </button>
                        )}
                      </section>
                      {page === 'tex' && (
                        <details className="mc-card">
                          <summary>粘贴 TeX 或新建文稿</summary>
                          <textarea
                            className="paste-tex"
                            aria-label="LaTeX 源码"
                            value={paste}
                            onChange={(e) => setPaste(e.target.value)}
                          />
                          <button
                            onClick={() =>
                              openWorkspace(
                                { source: paste, title: '未命名文稿' },
                                'tex'
                              )
                            }
                          >
                            在工作台打开
                          </button>
                        </details>
                      )}
                      <section className="mc-card">
                        <h2>最近文稿</h2>
                        {recent.map((r) => (
                          <button
                            key={r.id}
                            className="draft-link"
                            disabled={imageBusy}
                            onClick={() =>
                              openWorkspace({}, r.inputKind === 'image' ? 'entry' : r.pdfPath ? 'pdf' : 'tex', r.id)
                            }
                          >
                            <span>
                              {r.inputKind === 'image' ? pages.entry[0] : r.pdfPath ? '📑' : '📄'}　{r.title}
                            </span>
                            <small>继续编辑 →</small>
                          </button>
                        ))}
                        {!recent.length && (
                          <p>
                            {recentLoading
                              ? '正在读取…'
                              : '打开过的文稿会保存在这里，可继续编辑。'}
                          </p>
                        )}
                      </section>
                    </>
                  )}
                  {page === 'settings' && (
                    <>
                      <h1>⚙️ 设置</h1>
                      <section className="mc-card">
                        <h2>🎨 界面外观</h2>
                        <div className="skin-grid">
                          {Object.entries(prefs?.skins || {}).map(([id, s]) => (
                            <button
                              key={id}
                              aria-pressed={prefs?.skin === id}
                              onClick={() =>
                                void work('正在应用皮肤…', async () =>
                                  theme(await call('ui.save', { skin: id }))
                                )
                              }
                            >
                              <i style={{ background: s.accent }} />
                              {s.name}
                            </button>
                          ))}
                        </div>
                        <label>
                          外观模式　
                          <select
                            aria-label="外观模式"
                            value={prefs?.mode || 'light'}
                            onChange={(e) =>
                              void work('正在切换模式…', async () =>
                                theme(
                                  await call('ui.save', {
                                    mode: e.target.value,
                                  })
                                )
                              )
                            }
                          >
                            <option value="light">浅色</option>
                            <option value="dark">深色</option>
                          </select>
                        </label>
                      </section>
                      <SettingsConnections
                        provider={prefs?.provider}
                        saveProvider={async (provider) => {
                          const saved = await call<Prefs>('ui.save', { provider })
                          setPrefs((current) => current && { ...current, provider: saved.provider })
                        }}
                        openApi={() => openLegacy('settings')}
                        apiPending={legacyPending}
                      />
                      <section className="mc-card">
                        <h2>本地题库</h2>
                        <p className="filepath">{prefs?.library}</p>
                        <div className="settings-row-actions">
                          <button
                            disabled={Boolean(busy) || legacyPending}
                            onClick={() =>
                              void work('正在打开题库…', async () => {
                                if (await window.utopia.chooseLibrary())
                                  location.reload()
                              })
                            }
                          >
                            选择题库文件夹
                          </button>
                          <button
                            disabled={Boolean(busy) || legacyPending}
                            onClick={() =>
                              void work('正在校验…', async () =>
                                setCheck(await call<LibraryCheck>('library.check'))
                              )
                            }
                          >
                            检查题库
                          </button>
                          <button disabled={Boolean(busy) || legacyPending} onClick={() => openLegacy('tags')}>
                            管理题目标签
                          </button>
                          <button disabled={Boolean(busy) || legacyPending} onClick={() => openLegacy('tools')}>
                            打开题库工具箱
                          </button>
                        </div>
                        {check && (
                          <p role="status">
                            索引 {check.integrity} · 已检查 {check.total} 题 · 缺少 {check.missing.length} 个文件
                          </p>
                        )}
                        {check && check.missing.length > 0 && (
                          <pre>{check.missing.join('\n')}</pre>
                        )}
                        <h3>自动维护记录</h3>
                        {maintenanceError ? (
                          <p role="status">读取失败。{maintenanceError}</p>
                        ) : maintenance ? (
                          <ul>
                            {([
                              ['启动时', maintenance.startup],
                              ['上次退出时', maintenance.shutdown],
                            ] as const).map(([label, run]) => (
                              <li key={label}>
                                {label} · {run ? (
                                  <>
                                    <time dateTime={run.finishedAt}>
                                      {new Date(run.finishedAt).toLocaleString('zh-CN', { hour12: false })}
                                    </time>
                                    {run.ok
                                      ? ` · 完成 · 新增 ${run.result.added} · 更新 ${run.result.updated} · 缺失 ${run.result.missing} · 跳过 ${run.result.skipped}`
                                      : ` · 失败${run.error ? ` · ${run.error}` : ''}`}
                                  </>
                                ) : '暂无记录'}
                              </li>
                            ))}
                          </ul>
                        ) : <p role="status">正在读取…</p>}
                      </section>
                    </>
                  )}
                  {page === 'intro' && (
                    <Suspense fallback={<p role="status">正在载入教程…</p>}>
                      <ArchitectureGuide
                        navigate={navigate}
                        demo={demo}
                        openClassic={() => openLegacy('intro')}
                        classicPending={legacyPending}
                      />
                    </Suspense>
                  )}
                  {page === 'manual' && (
                    <>
                      <h1>{pages[page].join(' ')}</h1>
                      <section className="mc-card">
                        <h2>从原卷到可编辑题目</h2>
                        <p>
                          在 PDF 录入打开文件，选择要重建的页码。框选题干或图像并在右侧提出修改，检查排版后的候选版本，再应用修改。
                        </p>
                        <button onClick={demo}>打开演示题</button>
                      </section>
                    </>
                  )}
                  {page !== 'settings' && page !== 'intro' && (
                    <button
                      className="advanced-link"
                      disabled={legacyPending || imageBusy}
                      onClick={() => openLegacy(page)}
                    >
                      {legacyPending
                        ? '正在打开…'
                        : (
                            {
                              exam: '打开完整组卷与导出工具',
                              entry: '批量识别与同卷录入',
                              tex: '批量 TeX 录入',
                              pdf: '逐题审阅与批量入库',
                              browse: '打开完整题库管理',
                              search: '打开高级检索',
                              manual: '阅读完整规范指南',
                              stats: '查看详细统计',
                            }
                          )[page]}{' '}
                      ↗
                    </button>
                  )}
                </div>
              )}
            </div>
            {docking ? (
              <PaneDivider
                open={workbenchOpen}
                panelId={workbenchId}
                label="题目工作台"
                width={workbenchWidth}
                minWidth={WORKBENCH_MIN}
                maxFraction={WORKBENCH_FRACTION}
                remainingWidth={BROWSE_MIN}
                onToggle={() => setWorkbenchOpen((open) => !open)}
                onWidthChange={setWorkbenchWidth}
              />
            ) : <ContextChat
              name={pages[page][1]}
              storageKey={'chat:' + prefs?.library + ':' + page}
              context={{
                page: pages[page][1],
                query,
                total: summary.total,
                subjects: summary.subjects,
                rows: (page === 'stats' ? summary.recent : rows).slice(0, 20),
                basket: basket.map((r) => ({
                  id: r.id,
                  paper: r.paper,
                  number: r.number,
                })),
              }}
              apply={(a) => {
                if (a.action === 'search') {
                  setQuery(a.query || '')
                  navigate('search')
                } else if (a.action === 'add') {
                  const ids = new Set(a.ids)
                  const saved = new Set(basket.map((r) => r.path))
                  changeBasket([
                    ...basket,
                    ...[...rows, ...summary.recent].filter(
                      (r) =>
                        ids.has(r.id) && !saved.has(r.path) && saved.add(r.path)
                    ),
                  ])
                }
              }}
            />}
          </>}
        {doc && (
          <div
            id={workbenchId}
            className={'editor-mount' + (rightVisible ? ' browse-workbench' : '')}
            role={rightVisible ? 'region' : undefined}
            aria-label={rightVisible ? '题目工作台' : undefined}
            style={{ display: editing || rightVisible ? 'flex' : 'none', width: rightVisible ? dockSize : undefined }}
          >
            <Suspense fallback={<p>正在载入编辑器…</p>}>
              <Editor
                key={doc.id}
                document={doc}
                docked={docking && !editing}
                onExpand={rightVisible ? () => setEditing(true) : undefined}
                onEditFileInfo={workbenchQuestion ? (document) => setFileInfo({ path: workbenchQuestion.path, document }) : undefined}
                onOpenPapilot={() => navigate('papilot')}
                onChange={setDoc}
                onClose={() => {
                  if (!editing) setWorkbenchOpen(false)
                  setEditing(false)
                  void summaryLoad()
                }}
              />
            </Suspense>
          </div>
        )}
        </div>
        {fileInfo && (
          <FileInfoDialog
            key={fileInfo.path}
            {...fileInfo}
            onClose={() => setFileInfo(undefined)}
            onSaved={({ file, document }) => savedFileInfo(file, document)}
          />
        )}
      </main>
    </div>
  )
}
