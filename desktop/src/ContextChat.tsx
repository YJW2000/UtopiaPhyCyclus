import { useEffect, useRef, useState } from 'react'
interface Action {
  action: string
  ids?: string[]
  query?: string
}
interface Turn extends Partial<Action> {
  role: string
  text: string
}
export default function ContextChat({
  name,
  storageKey,
  context,
  apply,
  close,
}: {
  name: string
  storageKey: string
  context: object
  apply: (a: Action) => void
  close: () => void
}) {
  const [turns, setTurns] = useState<Turn[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(storageKey) || '[]')
    } catch {
      return []
    }
  })
  const [input, setInput] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  const [width, setWidth] = useState(() =>
    Math.min(
      520,
      Math.max(230, Number(localStorage.getItem('chat.width')) || 300)
    )
  )
  const scroll = useRef<HTMLDivElement>(null),
    alive = useRef(true),
    task = useRef(''),
    drag = useRef(false)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [turns, busy])
  function store(items: Turn[]) {
    if (alive.current) setTurns(items)
    try {
      localStorage.setItem(storageKey, JSON.stringify(items.slice(-60)))
    } catch {
      /* storage may be full */
    }
  }
  async function submit() {
    if (!input.trim() || busy) return
    const message = input.trim(),
      next = [...turns, { role: 'user', text: message }]
    setBusy(true)
    setError('')
    setInput('')
    store(next)
    try {
      const job = await window.utopia.call('context.chat', {
        message,
        history: turns.slice(-12),
        context,
      })
      task.current = job.taskId
      let res = job
      while (job.taskId) {
        const state = await window.utopia.call('task.get', { id: job.taskId })
        if (state.status === 'failed' || state.status === 'cancelled')
          throw new Error(state.error || '已停止生成。')
        if (state.status === 'succeeded') {
          res = state.result
          break
        }
        await new Promise((resolve) => setTimeout(resolve, 350))
      }
      store([
        ...next,
        {
          role: 'assistant',
          text: res.reply,
          action: res.action,
          query: res.query,
          ids: res.ids,
        },
      ])
    } catch (e) {
      if (alive.current) {
        setInput(message)
        setError(String(e))
      }
      store(turns)
    } finally {
      task.current = ''
      if (alive.current) setBusy(false)
    }
  }
  function resize(n: number) {
    const size = Math.min(520, Math.max(230, n))
    setWidth(size)
    localStorage.setItem('chat.width', String(size))
  }
  return (
    <aside className="context-chat" style={{ width }}>
      <div
        className="chat-resize"
        role="separator"
        aria-label="调整对话宽度"
        tabIndex={0}
        aria-orientation="vertical"
        onPointerDown={(e) => {
          drag.current = true
          e.currentTarget.setPointerCapture(e.pointerId)
        }}
        onPointerMove={(e) => {
          if (drag.current) resize(window.innerWidth - e.clientX)
        }}
        onPointerUp={() => {
          drag.current = false
        }}
        onLostPointerCapture={() => {
          drag.current = false
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault()
            resize(width + (e.key === 'ArrowLeft' ? 12 : -12))
          }
        }}
      />
      <header>
        <strong>✦　题库助手</strong>
        <button onClick={close} aria-label="收起题库助手">
          ›
        </button>
      </header>
      <div className="context-history" ref={scroll}>
        {!turns.length && (
          <div className="chat-greeting">
            <b>✦</b>
            <h2>想了解哪些题目？</h2>
            <p>可以根据当前「{name}」中的题目、统计或已选试卷提问。</p>
          </div>
        )}
        {turns.map((t, i) => (
          <article key={i}>
            <small>{t.role === 'user' ? '你' : '题库助手'}</small>
            <p>{t.text}</p>
            {t.action && t.action !== 'none' && (
              <button
                onClick={() =>
                  apply({ action: t.action!, ids: t.ids, query: t.query })
                }
              >
                {t.action === 'add' ? '选入建议题目' : '按建议搜索'}
              </button>
            )}
          </article>
        ))}
        {busy && <p role="status">助手正在处理…</p>}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        {error && <small role="alert">{error}</small>}
        <textarea
          placeholder="询问题库助手…"
          aria-label="发送给题库助手"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault()
              void submit()
            }
          }}
        />
        {busy ? (
          <button
            type="button"
            onClick={() => {
              if (task.current)
                void window.utopia.call('task.cancel', { id: task.current })
            }}
          >
            停止
          </button>
        ) : (
          <button className="primary" disabled={!input.trim()}>
            发送 ↑
          </button>
        )}
      </form>
    </aside>
  )
}
