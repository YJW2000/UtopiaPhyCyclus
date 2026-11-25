import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState } from 'react'
import logo from '../../static/sidebar.webp'
import { Browse, emptySummary, Paper, Statistics } from './Bank'
import type { Question, Summary } from './Bank'
import ContextChat from './ContextChat'
import FileInfoDialog from './FileInfoDialog'
import type { QuestionFileInfo } from './FileInfoDialog'
import ImageIntake from './ImageIntake'
import PaneDivider from './PaneDivider'
import SettingsConnections from './SettingsConnections'
import type { EditorDocument } from './editorContract'
import { useSolutionBatch } from './useSolutionBatch'
import type { BatchResultRow } from './useSolutionBatch'

const WORKBENCH_MIN = 400
const BROWSE_MIN = 170
const MODULE_BROWSER_MIN = 260
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
  settings: ['⚙️', '设置'],
} as const
type Page = keyof typeof pages
type LegacyPage = Exclude<Page, 'settings'> | 'manual' | 'tools' | 'tags'
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

export default function App() {
  const [page, setPage] = useState<Page>('stats'),
    [prefs, setPrefs] = useState<Prefs | null>(null),
    [summary, setSummary] = useState<Summary>(emptySummary)
  const [doc, setDoc] = useState<EditorDocument | null>(null),
    [editing, setEditing] = useState(false),
    [recent, setRecent] = useState<EditorDocument[]>([])
  const [imageIntakeBusy, setImageIntakeBusy] = useState(false)
  const [initialCompileError, setInitialCompileError] = useState<string>()
  const [workbenchQuestion, setWorkbenchQuestion] = useState<Question>()
  const [workbenchOpen, setWorkbenchOpen] = useState(false)
  const [workbenchWidth, setWorkbenchWidth] = useState(() => {
    const stored = Number(localStorage.getItem('browse.workbench.width'))
    return stored > 0 && Number.isFinite(stored) ? stored : 780
  })
  const [fileInfo, setFileInfo] = useState<{ path: string; document: EditorDocument }>()
  const [browseRevision, setBrowseRevision] = useState(0)
  const workbenchId = useId()
  const [reviewProposalId, setReviewProposalId] = useState<string>()
  const solutionBatch = useSolutionBatch()
  const [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [collapsed, setCollapsed] = useState(
      () => localStorage.getItem('nav.compact') === 'yes'
    )
  const [query, setQuery] = useState(''),
    [rows, setRows] = useState<Question[]>([]),
    [basket, setBasket] = useState<Question[]>([])
  const [legacy, setLegacy] = useState(''),
    [pendingLegacyPage, setPendingLegacyPage] = useState<LegacyPage | null>(null),
    [check, setCheck] = useState<LibraryCheck>()
  const legacyPending = pendingLegacyPage !== null
  const [maintenance, setMaintenance] = useState<MaintenanceStatus>(),
    [maintenanceError, setMaintenanceError] = useState('')
  const routeGeneration = useRef(0)
  const browseAutoOpen = useRef(false)
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
      .then((r: Summary) => setSummary(r))
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
    browseAutoOpen.current = next === 'browse' && !doc?.questionPath && !workbenchQuestion
    if (next === 'browse') {
      setWorkbenchOpen(true)
      if (page === 'browse' && browseAutoOpen.current) setBrowseRevision(revision => revision + 1)
    }
    setPage(next)
    setEditing(false)
    setReviewProposalId(undefined)
    setLegacy('')
    setPendingLegacyPage(null)
    setBusy('')
    setError('')
    mainScroll.current?.scrollTo(0, 0)
    if (next === 'stats' || next === 'browse') void summaryLoad()
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
  const showWorkspace = useCallback((result: EditorDocument, route: Page, reviewId?: string, question?: Question, compileError?: string) => {
    const docked = route === 'browse' || Boolean(question)
    setDoc(result)
    setInitialCompileError(compileError)
    setReviewProposalId(reviewId)
    setWorkbenchQuestion(question)
    setWorkbenchOpen(docked)
    setEditing(!docked)
    setPage(route)
    setLegacy('')
  }, [])
  const loadWorkspace = useCallback(async (params: object, route: Page, id?: string, reviewId?: string, question?: Question) => {
    performance.clearMarks('document-open')
    performance.mark('document-open')
    const token = routeGeneration.current
    const result = await call<EditorDocument>(
      id ? 'workspace.open' : 'workspace.create',
      id ? { id } : params
    )
    if (token === routeGeneration.current) showWorkspace(result, route, reviewId, question)
  }, [showWorkspace])
  const showBrowseRows = useCallback((results: Question[]) => {
    setRows(results)
    const first = results[0]
    if (!browseAutoOpen.current || !first) return
    browseAutoOpen.current = false
    void work('正在打开题目…', () => loadWorkspace({ questionPath: first.path }, 'browse', undefined, undefined, first))
  }, [loadWorkspace, work])
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
    browseAutoOpen.current = false
    routeGeneration.current++
    setFileInfo(undefined)
    void work('正在打开题目…', () => loadWorkspace({ questionPath: row.path }, page, undefined,
      undefined, row))
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
  function openBatchResult(row: BatchResultRow) {
    if (!row.documentId || !row.proposalId) return
    const { documentId, proposalId, question } = row
    browseAutoOpen.current = false
    routeGeneration.current++
    setFileInfo(undefined)
    void work('正在打开题目…', () => loadWorkspace({}, 'browse', documentId, proposalId, question))
  }
  function openLegacy(route: LegacyPage) {
    browseAutoOpen.current = false
    const token = ++routeGeneration.current
    setBusy('')
    setPendingLegacyPage(route)
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
        if (routeGeneration.current === token) setPendingLegacyPage(null)
      })
  }
  useEffect(() => window.utopia.onPapilotShortcut(navigate), [])
  const imageBusy = page === 'entry' && (imageIntakeBusy || Boolean(busy))
  const routeToken = routeGeneration.current
  const actionProps = { open, add, basket }
  const browseWorkbench = page === 'browse' && !legacy && !editing
  const questionPath = workbenchQuestion?.path ?? doc?.questionPath
  const docking = !legacy && Boolean(page === 'browse' ? questionPath : page === 'search' && workbenchQuestion)
  const rightVisible = !editing && (browseWorkbench || docking) && workbenchOpen
  const showEmptyWorkbench = browseWorkbench && !docking
  const showDockedEditor = rightVisible && docking
  const remainingWidth = browseWorkbench ? MODULE_BROWSER_MIN : BROWSE_MIN
  const maximumDock = `min(${WORKBENCH_FRACTION * 100}%, calc(100% - ${remainingWidth}px))`
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
              {doc && page !== 'stats' && !browseWorkbench && (
                <button
                  disabled={imageBusy}
                  onClick={() => {
                    if (page === 'browse' && (doc.questionPath || workbenchQuestion)) setWorkbenchOpen(true)
                    else setEditing(true)
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
          {!editing && page === 'stats' && <Statistics />}
          {!editing && page !== 'papilot' && page !== 'stats' && <>
            <div ref={mainScroll} className={'main-scroll' + (legacy ? ' embedded' : '')}>
              {legacy ? (
                <>
                  <div className="advanced-bar">
                    <span>{pages[page][1]}</span>
                    <button
                      onClick={() => {
                        if (page === 'browse') navigate('browse')
                        else setLegacy('')
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
                  {(page === 'browse' || page === 'search') && (
                    <Browse
                      key={page}
                      {...actionProps}
                      {...{ summary, query, setQuery }}
                      search={page === 'search'}
                      onRows={page === 'browse' ? showBrowseRows : setRows}
                      revision={browseRevision}
                      previewPath={docking ? questionPath : undefined}
                      batch={page === 'browse' ? { ...solutionBatch, openResult: openBatchResult } : undefined}
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
                      {page === 'entry' ? (
                        <ImageIntake key={routeToken}
                          disabled={Boolean(busy) || legacyPending}
                          onBusyChange={setImageIntakeBusy}
                          onSettings={() => navigate('settings')}
                          onOpen={(document, compileError) => {
                            if (routeToken !== routeGeneration.current) return
                            performance.clearMarks('document-open')
                            performance.mark('document-open')
                            showWorkspace(document, 'entry', undefined, undefined, compileError)
                          }} />
                      ) : (
                        <section className="mc-card intake">
                          <span aria-hidden="true">{pages[page][0]}</span>
                          <h2>{page === 'pdf' ? '打开原始 PDF' : '打开 LaTeX 文稿'}</h2>
                          <p>{page === 'pdf' ? '先阅读原卷，再选择页码重建可编辑题目。' : '载入文稿后，可在排版页上框选并修改。'}</p>
                          <button className="primary" onClick={() => void choose(page === 'pdf' ? 'pdf' : 'tex')}>
                            {page === 'pdf' ? '选择 PDF 文件' : '选择 TeX 文件'}
                          </button>
                        </section>
                      )}
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
                        openClassic={openLegacy}
                        pendingClassicPage={pendingLegacyPage === 'intro' || pendingLegacyPage === 'manual' ? pendingLegacyPage : null}
                      />
                    </Suspense>
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
                            }
                          )[page]}{' '}
                      ↗
                    </button>
                  )}
                </div>
              )}
            </div>
            {browseWorkbench || docking ? (
              <PaneDivider
                open={workbenchOpen}
                panelId={workbenchId}
                label="题目工作台"
                width={workbenchWidth}
                minWidth={WORKBENCH_MIN}
                maxFraction={WORKBENCH_FRACTION}
                remainingWidth={remainingWidth}
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
                rows: rows.slice(0, 20),
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
        {showEmptyWorkbench && (
          <section
            id={workbenchId}
            className="editor-mount browse-workbench browse-workbench-empty"
            aria-label="题目工作台"
            style={{ display: rightVisible ? 'flex' : 'none', width: dockSize }}
          >
            <div>
              <h2>题目工作台</h2>
              <p>从左侧选择一道题，查看 PDF 和 LaTeX 源码。</p>
            </div>
          </section>
        )}
        {doc && (
          <div
            id={showEmptyWorkbench ? undefined : workbenchId}
            className={'editor-mount' + (showDockedEditor ? ' browse-workbench' : '')}
            role={showDockedEditor ? 'region' : undefined}
            aria-label={showDockedEditor ? '题目工作台' : undefined}
            style={{ display: editing || showDockedEditor ? 'flex' : 'none', width: showDockedEditor ? dockSize : undefined }}
          >
            <Suspense fallback={<p>正在载入编辑器…</p>}>
              <Editor
                key={doc.id}
                document={doc}
                initialCompileError={initialCompileError}
                docked={docking && !editing}
                onExpand={rightVisible ? () => setEditing(true) : undefined}
                onEditFileInfo={questionPath && (workbenchQuestion || page === 'browse') ? (document) => setFileInfo({ path: questionPath, document }) : undefined}
                onOpenPapilot={() => navigate('papilot')}
                reviewProposalId={reviewProposalId}
                onChange={next => {
                  setDoc(next)
                  solutionBatch.trackReview(next)
                }}
                onQuestionSaved={next => solutionBatch.trackReview(next, true)}
                onClose={() => {
                  if (!editing) setWorkbenchOpen(false)
                  setEditing(false)
                  setReviewProposalId(undefined)
                  void summaryLoad()
                }}
              />
            </Suspense>
          </div>
        )}
        </div>
        {fileInfo && page !== 'stats' && (
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
