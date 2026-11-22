import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import logo from '../../static/sidebar.webp'
import { Browse, Dashboard, emptySummary, Paper } from './Bank'
import type { Question, Summary } from './Bank'
import ContextChat from './ContextChat'
import type { EditorDocument } from './editorContract'

const Editor = lazy(() => import('./Editor'))
const pages = {
  stats: ['📊', '数据统计'],
  entry: ['📝', '智能录题'],
  tex: ['📄', 'TeX录入'],
  pdf: ['📑', 'PDF录入'],
  browse: ['🔍', '全局浏览'],
  exam: ['🖨️', '组卷排版'],
  tools: ['🛠️', '维护工具'],
  search: ['🔎', '题目查找'],
  intro: ['📘', '架构介绍'],
  manual: ['📖', '规范指南'],
  settings: ['⚙️', '设置'],
} as const
type Page = keyof typeof pages
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
    [summary, setSummary] = useState<Summary>(emptySummary),
    [ready, setReady] = useState(false)
  const [doc, setDoc] = useState<EditorDocument | null>(null),
    [editing, setEditing] = useState(false),
    [recent, setRecent] = useState<EditorDocument[]>([])
  const [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [chat, setChat] = useState(true),
    [collapsed, setCollapsed] = useState(
      () => localStorage.getItem('nav.compact') === 'yes'
    )
  const [query, setQuery] = useState(''),
    [rows, setRows] = useState<Question[]>([]),
    [basket, setBasket] = useState<Question[]>([])
  const [legacy, setLegacy] = useState(''),
    [legacyPending, setLegacyPending] = useState(false),
    [check, setCheck] = useState<any>(),
    [providers, setProviders] = useState<any>()
  const routeGeneration = useRef(0),
    fileInput = useRef<HTMLInputElement>(null)
  const [paste, setPaste] = useState(''),
    [recentLoading, setRecentLoading] = useState(false)
  function theme(p: Prefs) {
    setPrefs(p)
    for (const [k, v] of Object.entries(p.tokens))
      document.documentElement.style.setProperty(k, v)
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
    if (page === 'settings')
      void call('ui.providers')
        .then(setProviders)
        .catch((e: unknown) => setError(errorText(e)))
  }, [page, editing, drafts])
  function navigate(next: Page) {
    routeGeneration.current++
    setPage(next)
    setEditing(false)
    setLegacy('')
    setLegacyPending(false)
    setBusy('')
    setError('')
    if (next === 'stats') void summaryLoad()
  }
  async function work(label: string, op: () => Promise<void>) {
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
  }
  function openWorkspace(params: object, route: Page, id?: string) {
    performance.clearMarks('document-open')
    performance.mark('document-open')
    const token = routeGeneration.current
    void work('正在打开题目…', async () => {
      const result = await call<EditorDocument>(
        id ? 'workspace.open' : 'workspace.create',
        id ? { id } : params
      )
      if (token !== routeGeneration.current) return
      setDoc(result)
      setEditing(true)
      setPage(route)
      setLegacy('')
    })
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
  const open = (row: Question) =>
    openWorkspace({ questionPath: row.path }, 'browse')
  function openLegacy(route: Page | 'tags') {
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
  async function image(file: File) {
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result).split(',')[1])
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(file)
    })
    const res = await call('library.imageInput', {
      name: file.name,
      type: file.type === 'image/jpeg' ? 'jpeg' : 'png',
      data,
    })
    openWorkspace({ pdfPath: res.path, title: file.name }, 'entry')
  }
  const actionProps = { open, add, basket }
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
                  onClick={() => {
                    setEditing(true)
                    setLegacy('')
                  }}
                >
                  返回当前文稿
                </button>
              )}
              {!chat && (
                <button onClick={() => setChat(true)}>✦ 题库助手</button>
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
        {doc && (
          <div
            className="editor-mount"
            style={{ display: editing ? 'flex' : 'none' }}
          >
            <Suspense fallback={<p>正在载入编辑器…</p>}>
              <Editor
                key={doc.id}
                document={doc}
                onChange={setDoc}
                onClose={() => {
                  setEditing(false)
                  void summaryLoad()
                }}
              />
            </Suspense>
          </div>
        )}
        {!editing && (
          <div className="workspace-body">
            <div className={'main-scroll' + (legacy ? ' embedded' : '')}>
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
                        <button onClick={demo}>打开演示题 ↗</button>
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
                              : '从截图或扫描图片建立文稿，在原图与重建结果之间校对。'}
                        </p>
                        <button
                          className="primary"
                          onClick={() =>
                            page === 'entry'
                              ? fileInput.current?.click()
                              : void choose(page === 'pdf' ? 'pdf' : 'tex')
                          }
                        >
                          {page === 'entry'
                            ? '选择图片'
                            : page === 'pdf'
                              ? '选择 PDF 文件'
                              : '选择 TeX 文件'}
                        </button>
                        <input
                          ref={fileInput}
                          hidden
                          type="file"
                          accept="image/jpeg,image/png"
                          onChange={(e) => {
                            const f = e.target.files?.[0]
                            if (f) void work('正在读取图片…', () => image(f))
                            e.target.value = ''
                          }}
                        />
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
                            onClick={() =>
                              openWorkspace({}, r.pdfPath ? 'pdf' : 'tex', r.id)
                            }
                          >
                            <span>
                              {r.pdfPath ? '📑' : '📄'}　{r.title}
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
                  {page === 'tools' && (
                    <>
                      <h1>🛠️ 维护工具</h1>
                      <section className="mc-card">
                        <h2>题库完整性</h2>
                        <p>检查索引及其引用的题目文件。</p>
                        <button
                          onClick={() =>
                            void work('正在校验…', async () =>
                              setCheck(await call('library.check'))
                            )
                          }
                        >
                          检查题库
                        </button>
                        {check && (
                          <p>
                            索引 {check.integrity} · 已检查 {check.total} 题 ·
                            缺少 {check.missing.length} 个文件
                          </p>
                        )}
                        {check?.missing?.length > 0 && (
                          <pre>{check.missing.join('\n')}</pre>
                        )}
                      </section>
                      <button onClick={() => openLegacy('tags')}>
                        管理题目标签
                      </button>
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
                      <section className="mc-card">
                        <h2>模型连接</h2>
                        <select
                          aria-label="模型连接"
                          value={prefs?.provider || 'codex'}
                          onChange={(e) =>
                            void work('正在保存…', async () =>
                              theme(
                                await call('ui.save', {
                                  provider: e.target.value,
                                })
                              )
                            )
                          }
                        >
                          <option value="codex">本机 Codex</option>
                          <option value="api">自定义 API</option>
                        </select>
                        <p>
                          {prefs?.provider === 'api'
                            ? providers?.apiConfigured
                              ? '已读取当前题库的 API 配置。'
                              : '可在 API 设置中填写地址、模型和密钥。'
                            : providers?.codex
                              ? '已找到本机 Codex，使用已有登录。'
                              : '使用本机 Codex 登录。若未安装，请先安装并登录 Codex。'}
                        </p>
                        {prefs?.provider === 'api' && (
                          <button onClick={() => openLegacy('settings')}>
                            打开 API 设置
                          </button>
                        )}
                      </section>
                      <section className="mc-card">
                        <h2>本地题库</h2>
                        <p className="filepath">{prefs?.library}</p>
                        <button
                          onClick={() =>
                            void work('正在打开题库…', async () => {
                              if (await window.utopia.chooseLibrary())
                                location.reload()
                            })
                          }
                        >
                          选择题库文件夹
                        </button>
                      </section>
                    </>
                  )}
                  {(page === 'intro' || page === 'manual') && (
                    <>
                      <h1>{pages[page].join(' ')}</h1>
                      <section className="mc-card">
                        <h2>
                          {page === 'intro'
                            ? '题库与工作文稿'
                            : '从原卷到可编辑题目'}
                        </h2>
                        <p>
                          {page === 'intro'
                            ? '题库保存在本机文件夹。工作文稿保留独立的源码、排版结果和对话，确认入库后即可检索、选题和组卷。'
                            : '在 PDF 录入打开文件，选择要重建的页码。框选题干或图像并在右侧提出修改，检查排版后的候选版本，再应用修改。'}
                        </p>
                        <button onClick={demo}>打开演示题</button>
                      </section>
                    </>
                  )}
                  {page !== 'settings' && (
                    <button
                      className="advanced-link"
                      disabled={legacyPending}
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
                              tools: '打开完整维护工具',
                              intro: '阅读项目架构介绍',
                              manual: '阅读完整规范指南',
                              stats: '查看详细统计',
                            } as Record<string, string>
                          )[page]}{' '}
                      ↗
                    </button>
                  )}
                </div>
              )}
            </div>
            {chat && (
              <ContextChat
                key={(prefs?.library || '') + page}
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
                close={() => setChat(false)}
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
                          ids.has(r.id) &&
                          !saved.has(r.path) &&
                          saved.add(r.path)
                      ),
                    ])
                  }
                }}
              />
            )}
          </div>
        )}
      </main>
    </div>
  )
}
