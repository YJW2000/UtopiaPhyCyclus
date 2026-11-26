import { useEffect, useState } from 'react'
import { questionTitle } from './Bank'
import type { Question } from './Bank'
import { EditorModal } from './EditorChat'
import { editorCall, errorMessage } from './editorContract'

interface Props {
  paths: string[]
  basket: Question[]
  onAdd: (paths: string[]) => Promise<void>
  onClose: () => void
}

type Results = { rows: Question[]; page: number; pages: number }
const pathKey = (path: string) => path.replace(/\\/g, '/').toLowerCase()

export default function ExamQuestions({ paths, basket, onAdd, onClose }: Props) {
  const inPaper = new Set(paths.map(pathKey))
  const available = basket.filter(row => !inPaper.has(pathKey(row.path)))
  const [term, setTerm] = useState('')
  const [request, setRequest] = useState({ query: '', page: 1 })
  const [results, setResults] = useState<Results>()
  const [searchError, setSearchError] = useState('')
  const [selected, setSelected] = useState<Map<string, string>>(() => new Map())
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')

  useEffect(() => {
    let active = true
    setResults(undefined)
    setSearchError('')
    void editorCall<Results>('library.search', { ...request, pageSize: 12, mode: 'fuzzy' })
      .then(result => { if (active) setResults(result) })
      .catch(error => { if (active) setSearchError(errorMessage(error)) })
    return () => { active = false }
  }, [request])

  function select(rows: Question[], checked: boolean) {
    setSelected(previous => {
      const next = new Map(previous)
      for (const row of rows) {
        const key = pathKey(row.path)
        if (checked) next.set(key, row.path)
        else next.delete(key)
      }
      return next
    })
  }

  async function add() {
    if (adding || !selected.size) return
    setAdding(true)
    setAddError('')
    try {
      await onAdd([...selected.values()])
    } catch (error) {
      setAddError(errorMessage(error))
      setAdding(false)
    }
  }

  return <EditorModal title="添加题目" onClose={() => { if (!adding) onClose() }}>
    <div className="wb-exam-questions">
      <form className="wb-exam-search" role="search" onSubmit={event => {
        event.preventDefault()
        setRequest({ query: term.trim(), page: 1 })
      }}>
        <input type="search" aria-label="搜索题库" placeholder="输入题干、知识点或试卷名称" value={term} onChange={event => setTerm(event.target.value)} autoFocus disabled={adding} />
        <button type="submit" className="wb-btn" disabled={adding}>搜索</button>
      </form>
      {available.length > 0 && <button type="button" className="wb-btn wb-exam-basket" disabled={adding || available.every(row => selected.has(pathKey(row.path)))}
        onClick={() => select(available, true)}>勾选篮中待加入的 {available.length} 道题</button>}
      <div className="wb-exam-results" aria-label="题库搜索结果">
        {searchError ? <p className="wb-form-error" role="alert">{searchError}</p> : !results ? <p role="status">正在读取题库…</p> : !results.rows.length ? <p>没有匹配的题目。</p> :
          results.rows.map(row => {
            const key = pathKey(row.path)
            const included = inPaper.has(key)
            return <label className="wb-exam-question" key={row.path}>
              <input type="checkbox" aria-label={questionTitle(row)} checked={included || selected.has(key)} disabled={adding || included} onChange={event => select([row], event.target.checked)} />
              <span><strong>{questionTitle(row)}</strong><small>{row.stem || row.subject}</small></span>
              {included && <small>已在试卷中</small>}
            </label>
          })}
      </div>
      {results && results.pages > 1 && <div className="wb-exam-pages">
        <button type="button" className="wb-btn" disabled={adding || results.page <= 1} onClick={() => setRequest({ ...request, page: results.page - 1 })}>上一页</button>
        <span>{results.page} / {results.pages}</span>
        <button type="button" className="wb-btn" disabled={adding || results.page >= results.pages} onClick={() => setRequest({ ...request, page: results.page + 1 })}>下一页</button>
      </div>}
      {addError && <p className="wb-form-error" role="alert">{addError}</p>}
      <div className="wb-exam-footer">
        <span aria-live="polite">已勾选 {selected.size} 道题</span>
        <button type="button" className="wb-btn" disabled={adding || !selected.size} onClick={() => setSelected(new Map())}>清空勾选</button>
        <button type="button" className="wb-btn wb-primary" disabled={adding || !selected.size} onClick={() => void add()}>{adding ? '正在加入…' : '加入试卷'}</button>
      </div>
    </div>
  </EditorModal>
}
