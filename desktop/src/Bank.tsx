import { useEffect, useMemo, useState } from 'react'
import type { BatchResultRow, useSolutionBatch } from './useSolutionBatch'
import './browse-modules.css'

export interface Question {
  id: string
  path: string
  year: string
  paper: string
  number: string
  subject: string
  module?: string
  type: string
  tags: string
  stem: string
  entered?: string
}
interface PaperGroup {
  key: string
  year: string
  paper: string
  paperType: string
  paperTypeName?: string
  count: number
}
export interface Summary {
  total: number
  papers: number
  subjects: Record<string, number>
  modules: { name: string; count: number }[]
  types: Record<string, number>
  years: string[]
  paperGroups: PaperGroup[]
  recent: Question[]
}
export const emptySummary: Summary = {
  total: 0,
  papers: 0,
  subjects: {},
  modules: [],
  types: {},
  years: [],
  paperGroups: [],
  recent: [],
}
export function questionTitle(row: Question) {
  return (
    [row.year, row.paper, row.number && '第 ' + row.number + ' 题']
      .filter(Boolean)
      .join(' · ') || row.path
  )
}
interface RowActions {
  open: (row: Question) => void
  add: (row: Question) => void
  basket: Question[]
}

interface QuestionSelection {
  paths: ReadonlySet<string>
  disabled: boolean
  select: (questions: Question[], checked: boolean) => void
}
type BrowseBatch = ReturnType<typeof useSolutionBatch> & { openResult: (row: BatchResultRow) => void }
const browseModes = [
  { value: 'module', label: '按知识板块浏览' },
  { value: 'paper', label: '按试卷浏览' },
  { value: 'entry', label: '按录入顺序浏览' },
] as const
type BrowseMode = typeof browseModes[number]['value']
type SearchResult = { rows: Question[]; total: number; page: number; pages: number }
const emptyResults: SearchResult = { rows: [], total: 0, page: 1, pages: 1 }

function groupQuestionsByModule(rows: Question[]) {
  const groups = new Map<string, Question[]>()
  for (const row of rows) {
    const module = row.module ?? '未分类'
    const group = groups.get(module) ?? []
    group.push(row)
    groups.set(module, group)
  }
  return Array.from(groups)
}

export function QuestionList({
  rows,
  previewPath,
  showPaperNumber,
  open,
  add,
  basket,
  selection,
}: { rows: Question[]; previewPath?: string; showPaperNumber?: boolean; selection?: QuestionSelection } & RowActions) {
  return (
    <div className="question-list">
      {rows.map((row) => (
        <article key={row.path}>
          {selection && <input
            type="checkbox"
            aria-label={`勾选 ${questionTitle(row)} 用于批量补充`}
            checked={selection.paths.has(row.path)}
            disabled={selection.disabled}
            onChange={event => selection.select([row], event.target.checked)}
          />}
          <button className="question-link" aria-current={previewPath === row.path || undefined}
            aria-label={showPaperNumber ? questionTitle(row) : undefined} onClick={() => open(row)}>
            <strong title={showPaperNumber ? questionTitle(row) : undefined}>
              {showPaperNumber && row.number ? `第 ${row.number} 题` : questionTitle(row)}
            </strong>
            <span>{row.stem || row.path}</span>
            <small>
              {[row.subject, row.type, row.tags].filter(Boolean).join('　·　')}
            </small>
            {row.entered && <small>录入于 {row.entered}</small>}
          </button>
          <button
            className="quiet"
            onClick={() => add(row)}
            disabled={basket.some((r) => r.path === row.path)}
          >
            {basket.some((r) => r.path === row.path) ? '已选入' : '＋ 选入'}
          </button>
        </article>
      ))}
    </div>
  )
}
export function Statistics() {
  const [url, setUrl] = useState('')
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    void window.utopia.legacy('stats').then((url) => {
      if (active) setUrl(url)
    }).catch(() => {
      if (active) setFailed(true)
    })
    return () => { active = false }
  }, [attempt])

  return (
    <div className="main-scroll embedded">
      {url ? <iframe title="数据统计" src={url} /> : (
        <div className="page-content">
          <h1>📊 数据统计</h1>
          {failed ? <>
            <p role="alert">无法加载数据统计。</p>
            <button onClick={() => {
              setFailed(false)
              setAttempt((current) => current + 1)
            }}>重试</button>
          </> : <p role="status">正在加载数据统计…</p>}
        </div>
      )}
    </div>
  )
}
export function Browse({
  summary,
  query,
  setQuery,
  search,
  onRows,
  revision,
  previewPath,
  batch,
  ...actions
}: {
  summary: Summary
  query: string
  setQuery: (q: string) => void
  search: boolean
  onRows: (rows: Question[]) => void
  revision: number
  previewPath?: string
  batch?: BrowseBatch
} & RowActions) {
  const [subject, setSubject] = useState(''),
    [type, setType] = useState(''),
    [year, setYear] = useState(''),
    [mode, setMode] = useState('exact'),
    [page, setPage] = useState(1)
  const [browseMode, setBrowseMode] = useState<BrowseMode>(() => {
    const saved = localStorage.getItem('browse.mode')
    return browseModes.find(({ value }) => value === saved)?.value ?? 'module'
  })
  const [paperType, setPaperType] = useState(''),
    [paperYear, setPaperYear] = useState<string>(),
    [paper, setPaper] = useState('')
  const [entryOrder, setEntryOrder] = useState('desc'),
    [entryPageSize, setEntryPageSize] = useState(10)
  const [data, setData] = useState<(SearchResult & { requestKey: string }) | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false)
  const moduleBrowse = !search && browseMode === 'module'
  const paperBrowse = !search && browseMode === 'paper'
  const entryBrowse = !search && browseMode === 'entry'
  const paperGroups = summary.paperGroups ?? emptySummary.paperGroups
  const paperTypes = useMemo(() => new Map(paperGroups.filter(group => group.paperType)
    .map(group => [group.paperType, group.paperTypeName || group.paperType])), [paperGroups])
  const papersForType = useMemo(() => paperGroups.filter(group => !paperType || group.paperType === paperType),
    [paperGroups, paperType])
  const paperYears = useMemo(() => [...new Set(papersForType.map(group => group.year))], [papersForType])
  const selectedPaperYear = paperYear !== undefined && paperYears.includes(paperYear) ? paperYear : paperYears[0]
  const papersForYear = useMemo(() => papersForType.filter(group => group.year === selectedPaperYear),
    [papersForType, selectedPaperYear])
  const selectedPaper = papersForYear.find(group => group.key === paper) ?? papersForYear[0]
  const selectedPaperKey = paperBrowse ? selectedPaper?.key : undefined
  const pageSize = entryBrowse ? entryPageSize : 20
  const canSearch = !paperBrowse || selectedPaperKey !== undefined
  const requestKey = JSON.stringify([query, subject, type, year, page, search, mode, browseMode, selectedPaperKey, entryOrder, pageSize, revision])
  useEffect(() => {
    setPage(1)
  }, [query, subject, type, year, mode, browseMode, selectedPaperKey, entryOrder, pageSize])
  useEffect(() => {
    if (!canSearch) {
      setLoading(false)
      setError('')
      onRows([])
      return
    }
    let live = true
    const timeout = setTimeout(
      async () => {
        setLoading(true)
        setError('')
        try {
          const result = await window.utopia.call<SearchResult>('library.search', {
            query,
            ...(search ? { subject } : { browseMode }),
            ...(moduleBrowse ? { module: subject } : {}),
            ...(paperBrowse ? { paper: selectedPaperKey } : { year }),
            ...(entryBrowse ? { entryOrder } : {}),
            type,
            mode: search ? mode : 'exact',
            page,
            pageSize,
          })
          if (live) {
            setData({ ...result, requestKey })
            onRows(result.rows)
          }
        } catch (e) {
          if (live) setError(String(e))
        } finally {
          if (live) setLoading(false)
        }
      },
      query ? 125 : 0
    )
    return () => {
      live = false
      clearTimeout(timeout)
    }
  }, [query, subject, type, year, page, mode, search, onRows, requestKey, browseMode, moduleBrowse,
    paperBrowse, entryBrowse, selectedPaperKey, entryOrder, pageSize, canSearch])
  const currentData = !canSearch ? emptyResults : data?.requestKey === requestKey ? data : null
  const resultRows = currentData?.rows ?? []
  const visiblePage = currentData?.page ?? page
  const groupedRows = moduleBrowse && !subject ? groupQuestionsByModule(resultRows) : null
  const selectedPaths = batch && new Set(batch.selected.map(row => row.path))
  const running = batch?.run?.status === 'running'
  const selectionDisabled = running || loading || !currentData
  const pageSelection = resultRows.filter(row => selectedPaths?.has(row.path)).length
  const allSelected = resultRows.length > 0 && pageSelection === resultRows.length
  const selection = batch && selectedPaths && { paths: selectedPaths, disabled: selectionDisabled, select: batch.select }
  return (
    <div className={search ? undefined : 'module-browser'}>
      <h1>{search ? '🔎 题目查找' : '🔍 全局浏览'}</h1>
      {!search && <div className="browse-modes" role="radiogroup" aria-label="浏览方式">
        {browseModes.map(({ value, label }) => (
          <label key={value}>
            <input type="radio" name="browse-mode" value={value} checked={browseMode === value} onChange={() => {
              setBrowseMode(value)
              localStorage.setItem('browse.mode', value)
            }} />
            {label}
          </label>
        ))}
      </div>}
      <section className="mc-card">
        <input
          className="search-field"
          aria-label="搜索题库"
          type="search"
          placeholder="搜索题干、知识点或试卷名称"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {moduleBrowse && <div className="browse-modules" role="group" aria-label="知识板块">
          <button type="button" aria-pressed={!subject} onClick={() => setSubject('')}>全部板块</button>
          {summary.modules?.map(({ name, count }) => (
            <button type="button" key={name} aria-pressed={subject === name} title={`${name} · ${count} 题`} onClick={() => setSubject(name)}>
              {name}
            </button>
          ))}
        </div>}
        {paperBrowse && <div className="browse-settings" role="group" aria-label="试卷筛选">
          <label>
            试卷类型
            <select value={paperType} onChange={event => {
              setPaperType(event.target.value)
              setPaperYear(undefined)
              setPaper('')
            }}>
              <option value="">全部试卷类型</option>
              {[...paperTypes].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label>
            年份
            <select value={selectedPaperYear ?? ''} disabled={!paperYears.length} onChange={event => {
              setPaperYear(event.target.value)
              setPaper('')
            }}>
              {!paperYears.length && <option value="">暂无年份</option>}
              {paperYears.map(value => <option key={value} value={value}>{value || '未标年份'}</option>)}
            </select>
          </label>
          <label>
            选择试卷
            <select value={selectedPaper?.key ?? ''} disabled={!papersForYear.length} onChange={event => setPaper(event.target.value)}>
              {!papersForYear.length && <option value="">暂无试卷</option>}
              {papersForYear.map(group => (
                <option key={group.key} value={group.key}>
                  {group.paper || '未命名试卷'}{!paperType && group.paperTypeName && `（${group.paperTypeName}）`}
                </option>
              ))}
            </select>
          </label>
        </div>}
        {entryBrowse && <div className="browse-settings" role="group" aria-label="录入顺序设置">
          <label>
            排序方式
            <select value={entryOrder} onChange={event => setEntryOrder(event.target.value)}>
              <option value="desc">最新录入在最前</option>
              <option value="asc">最早录入在最前</option>
            </select>
          </label>
          <label>
            每页题数
            <select value={entryPageSize} onChange={event => setEntryPageSize(Number(event.target.value))}>
              {[5, 10, 15, 20].map(size => <option key={size} value={size}>{size} 题</option>)}
            </select>
          </label>
        </div>}
        <div className="filters">
          {(
            [
              ...(search ? [[subject, setSubject, Object.keys(summary.subjects), '知识板块'] as const] : []),
              [type, setType, Object.keys(summary.types), '题型'],
              ...(!paperBrowse ? [[year, setYear, summary.years, '年份'] as const] : []),
            ] as const
          ).map(([v, set, items, name]) => (
            <select
              key={name}
              aria-label={name}
              value={v}
              onChange={(e) => set(e.target.value)}
            >
              <option value="">全部{name}</option>
              {items.filter(Boolean).map((i) => (
                <option key={i}>{i}</option>
              ))}
            </select>
          ))}
          {search && (
            <select
              aria-label="匹配方式"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="exact">精确查找</option>
              <option value="fuzzy">模糊查找</option>
            </select>
          )}
        </div>
      </section>
      <section className="mc-card">
        <div className="section-head">
          <h2>{search ? '检索结果' : paperBrowse ? selectedPaper?.paper || '题目' : moduleBrowse && subject ? subject : '题目'}</h2>
          <small>
            {loading || (!currentData && !error) ? '检索中…' : currentData ? '共 ' + currentData.total + ' 题' : ''}
          </small>
        </div>
        {error && <p role="alert">{error}</p>}
        {batch && <div className="solution-batch-actions">
          <label>
            <input type="checkbox" checked={allSelected} disabled={selectionDisabled || !resultRows.length}
              ref={input => { if (input) input.indeterminate = pageSelection > 0 && !allSelected }}
              onChange={event => batch.select(resultRows, event.target.checked)} />
            选择本页题目
          </label>
          <span aria-live="polite">已勾选 {batch.selected.length} 题</span>
          <button className="text-action" disabled={running || !batch.selected.length} onClick={() => batch.select(batch.selected, false)}>清空选择</button>
          <button className="primary" disabled={running || !batch.selected.length} onClick={() => void batch.start()}>批量补充答案与解析</button>
        </div>}
        {batch?.run && <SolutionBatchResults batch={batch} />}
        {groupedRows ? groupedRows.map(([module, groupedQuestions]) => (
          <section className="browse-question-group" key={module}>
            <h3>{module}</h3>
            <QuestionList rows={groupedQuestions} previewPath={previewPath} selection={selection} {...actions} />
          </section>
        )) : <QuestionList rows={resultRows} previewPath={previewPath} showPaperNumber={paperBrowse} selection={selection} {...actions} />}
        {currentData && !currentData.total && <p>{canSearch ? '没有匹配题目，可调整关键词或筛选条件。' : '题库中暂无可浏览的试卷。'}</p>}
        {currentData && currentData.pages > 1 && (
          <div className="pager">
            <button disabled={visiblePage <= 1} onClick={() => setPage(visiblePage - 1)}>
              上一页
            </button>
            <span>
              {visiblePage} / {currentData.pages}
            </span>
            <button
              disabled={visiblePage >= currentData.pages}
              onClick={() => setPage(visiblePage + 1)}
            >
              下一页
            </button>
          </div>
        )}
      </section>
    </div>
  )
}

function SolutionBatchResults({ batch }: { batch: BrowseBatch }) {
  const { run, results, openResult, stop } = batch
  if (!run) return null
  const processed = `已处理 ${run.completed} / ${run.total} 题`
  const statusLabels = { ready: '待审阅', skipped: '已跳过', failed: '生成失败' }
  const reviewLabels = { adopted: '已采用，待保存', dismissed: '已放弃', stale: '提案已过期', saved: '已保存原题' }
  return (
    <section className="solution-batch-results" aria-label="批量补充结果">
      <div className="solution-batch-actions">
        <h3>批量补充结果</h3>
        <span role="status">{run.status === 'cancelled' ? `已停止，${processed}` : processed}</span>
        {run.status === 'running' && <button disabled={run.stopping} onClick={stop}>{run.stopping ? '正在停止…' : '停止生成'}</button>}
      </div>
      {run.status === 'running' && <p role="status">{run.progress}</p>}
      {run.error && <p role="alert">{run.error}</p>}
      {!!results.length && <ul>
        {results.map(row => {
          const title = row.question ? questionTitle(row.question) : row.questionPath
          const status = row.review ? reviewLabels[row.review] : statusLabels[row.status]
          const action = row.review === 'adopted' ? '继续保存' : row.review === 'saved' || row.review === 'dismissed' ? '查看' : '审阅'
          return <li key={row.questionPath}>
            <div>
              <strong>{title}</strong>
              <span>{status}{row.status !== 'ready' && row.message && ` · ${row.message}`}</span>
            </div>
            {row.documentId && row.proposalId && <button aria-label={`${action} ${title}`} onClick={() => openResult(row)}>{action}</button>}
          </li>
        })}
      </ul>}
    </section>
  )
}
export function Paper({
  basket,
  setBasket,
  browse,
  assemble,
}: {
  basket: Question[]
  setBasket: (rows: Question[]) => void
  browse: () => void
  assemble: (title: string, answers: boolean) => void
}) {
  const [title, setTitle] = useState('高中物理练习卷'),
    [answers, setAnswers] = useState(true)
  function move(i: number, delta: number) {
    const copy = basket.slice()
    ;[copy[i], copy[i + delta]] = [copy[i + delta], copy[i]]
    setBasket(copy)
  }
  return (
    <>
      <div className="page-head">
        <h1>🖨️ 组卷排版</h1>
        <button onClick={browse}>＋ 选择题目</button>
      </div>
      <section className="mc-card">
        <h2>试卷设置</h2>
        <label className="field">
          试卷名称
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          <input
            type="checkbox"
            checked={answers}
            onChange={(e) => setAnswers(e.target.checked)}
          />{' '}
          包含答案与解析
        </label>
      </section>
      <section className="mc-card">
        <h2>已选题目 · {basket.length}</h2>
        {basket.map((r, i) => (
          <div className="paper-row" key={r.path}>
            <strong>
              {i + 1}. {questionTitle(r)}
            </strong>
            <button title="上移" disabled={!i} onClick={() => move(i, -1)}>
              ↑
            </button>
            <button
              title="下移"
              disabled={i === basket.length - 1}
              onClick={() => move(i, 1)}
            >
              ↓
            </button>
            <button
              onClick={() => setBasket(basket.filter((s) => s.path !== r.path))}
            >
              移除
            </button>
          </div>
        ))}
        {!basket.length && (
          <p>在「全局浏览」中点题目旁的「选入」，即可在这里调整顺序并排版。</p>
        )}
        <button
          className="primary"
          disabled={!basket.length || !title.trim()}
          onClick={() => assemble(title, answers)}
        >
          生成试卷并预览
        </button>
      </section>
    </>
  )
}
