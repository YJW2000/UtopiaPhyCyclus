import { useEffect, useState } from 'react'

export interface Question {
  id: string
  path: string
  year: string
  paper: string
  number: string
  subject: string
  type: string
  tags: string
  stem: string
}
export interface Summary {
  total: number
  papers: number
  subjects: Record<string, number>
  types: Record<string, number>
  years: string[]
  recent: Question[]
}
export const emptySummary: Summary = {
  total: 0,
  papers: 0,
  subjects: {},
  types: {},
  years: [],
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
export function QuestionList({
  rows,
  open,
  add,
  basket,
}: { rows: Question[] } & RowActions) {
  return (
    <div className="question-list">
      {rows.map((row) => (
        <article key={row.path}>
          <button className="question-link" onClick={() => open(row)}>
            <strong>{questionTitle(row)}</strong>
            <span>{row.stem || row.path}</span>
            <small>
              {[row.subject, row.type, row.tags].filter(Boolean).join('　·　')}
            </small>
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
export function Dashboard({
  summary,
  ready,
  open,
  add,
  basket,
  browse,
  demo,
}: {
  summary: Summary
  ready: boolean
  browse: () => void
  demo: () => void
} & RowActions) {
  const subjects = Object.entries(summary.subjects).slice(0, 6)
  return (
    <>
      <div className="page-head">
        <h1>📊 数据统计</h1>
        <button onClick={demo}>打开演示题 ↗</button>
      </div>
      <div className="metrics">
        {[
          ['题库总量', summary.total, '题'],
          ['收录试卷', summary.papers, '份'],
          ['知识板块', Object.keys(summary.subjects).length, '个'],
        ].map(([label, n, unit]) => (
          <div className="mc-card" key={label}>
            <small>{label}</small>
            <strong>
              {ready ? Number(n).toLocaleString() : '…'} <em>{unit}</em>
            </strong>
          </div>
        ))}
      </div>
      {subjects.length > 0 && (
        <section className="mc-card">
          <h2>知识板块分布</h2>
          <div className="distribution">
            {subjects.map(([label, n]) => (
              <div key={label}>
                <span>{label || '未分类'}</span>
                <i>
                  <b style={{ width: (100 * n) / subjects[0][1] + '%' }} />
                </i>
                <small>{n}</small>
              </div>
            ))}
          </div>
        </section>
      )}
      <section className="mc-card">
        <div className="section-head">
          <h2>最近更新</h2>
          <button className="text-action" onClick={browse}>
            浏览题库 →
          </button>
        </div>
        <QuestionList rows={summary.recent} {...{ open, add, basket }} />
        {ready && !summary.total && (
          <p>题库为空。可以从 PDF 或 TeX 录入第一道题。</p>
        )}
      </section>
    </>
  )
}
export function Browse({
  summary,
  query,
  setQuery,
  search,
  onRows,
  ...actions
}: {
  summary: Summary
  query: string
  setQuery: (q: string) => void
  search: boolean
  onRows: (rows: Question[]) => void
} & RowActions) {
  const [subject, setSubject] = useState(''),
    [type, setType] = useState(''),
    [year, setYear] = useState(''),
    [mode, setMode] = useState('exact'),
    [page, setPage] = useState(1)
  const [data, setData] = useState<{
      rows: Question[]
      total: number
      pages: number
    } | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false)
  useEffect(() => {
    setPage(1)
  }, [query, subject, type, year, mode])
  useEffect(() => {
    let live = true
    const timeout = setTimeout(
      async () => {
        setLoading(true)
        try {
          const result = await window.utopia.call('library.search', {
            query,
            subject,
            type,
            year,
            mode: search ? mode : 'exact',
            page,
            pageSize: 20,
          })
          if (live) {
            setData(result)
            onRows(result.rows)
            setError('')
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
  }, [query, subject, type, year, page, mode, search, onRows])
  return (
    <>
      <h1>{search ? '🔎 题目查找' : '🔍 全局浏览'}</h1>
      <section className="mc-card">
        <input
          className="search-field"
          aria-label="搜索题库"
          type="search"
          placeholder="搜索题干、知识点或试卷名称"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="filters">
          {(
            [
              [subject, setSubject, Object.keys(summary.subjects), '知识板块'],
              [type, setType, Object.keys(summary.types), '题型'],
              [year, setYear, summary.years, '年份'],
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
          <h2>检索结果</h2>
          <small>
            {loading ? '检索中…' : data ? '共 ' + data.total + ' 题' : ''}
          </small>
        </div>
        {error && <p role="alert">{error}</p>}
        <QuestionList rows={data?.rows || []} {...actions} />
        {data && !data.total && <p>没有匹配题目，可调整关键词或筛选条件。</p>}
        {data && data.pages > 1 && (
          <div className="pager">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
              上一页
            </button>
            <span>
              {page} / {data.pages}
            </span>
            <button
              disabled={page >= data.pages}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </button>
          </div>
        )}
      </section>
    </>
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
