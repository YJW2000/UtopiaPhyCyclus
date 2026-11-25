import { useEffect, useRef, useState } from 'react'
import type { PapilotBounds } from './bridge'
import { errorMessage } from './editorContract'

import './papilot-workspace.css'

type WorkspaceStatus = { phase: 'loading' | 'ready' } | { phase: 'error'; message: string }

function nativeBounds(element: HTMLElement): PapilotBounds {
  const rect = element.getBoundingClientRect()
  const left = Math.max(0, rect.left)
  const top = Math.max(0, rect.top)
  const right = Math.min(window.innerWidth, rect.right)
  const bottom = Math.min(window.innerHeight, rect.bottom)
  const x = Math.ceil(left)
  const y = Math.ceil(top)
  return { x, y, width: Math.max(0, Math.floor(right) - x), height: Math.max(0, Math.floor(bottom) - y) }
}

export default function PapilotWorkspace() {
  const slot = useRef<HTMLDivElement>(null)
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<WorkspaceStatus>({ phase: 'loading' })

  useEffect(() => {
    const element = slot.current
    if (!element) return
    let active = true
    let started = false
    let failed = false
    let frame = 0
    let previous: PapilotBounds | undefined

    const fail = (error: unknown) => {
      if (!active || failed) return
      failed = true
      setStatus({ phase: 'error', message: errorMessage(error) })
      void window.utopia.papilotHide().catch(() => {})
    }

    const resize = () => {
      frame = 0
      if (!active || failed) return
      const bounds = nativeBounds(element)
      if (!started && (!bounds.width || !bounds.height)) return
      if (previous?.x === bounds.x && previous.y === bounds.y && previous.width === bounds.width && previous.height === bounds.height) return
      previous = bounds
      if (!started) {
        started = true
        void window.utopia.papilotShow(bounds).then(() => {
          if (active && !failed) setStatus({ phase: 'ready' })
        }, fail)
      } else {
        void window.utopia.papilotBounds(bounds).catch(fail)
      }
    }
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(resize)
    }

    const observer = new ResizeObserver(schedule)
    observer.observe(element)
    window.addEventListener('resize', schedule)
    document.addEventListener('scroll', schedule, true)
    schedule()

    return () => {
      active = false
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', schedule)
      document.removeEventListener('scroll', schedule, true)
      if (started && !failed) void window.utopia.papilotHide().catch(() => {})
    }
  }, [attempt])

  return <div ref={slot} className="papilot-workspace" role="region" aria-label="项目工作台" aria-busy={status.phase === 'loading'}>
    {status.phase === 'loading' && <p className="papilot-workspace-status" role="status">正在打开项目工作台…</p>}
    {status.phase === 'error' && <div className="papilot-workspace-status papilot-workspace-error" role="alert">
      <strong>项目工作台启动失败</strong>
      <p>{status.message}</p>
      <button type="button" onClick={() => {
        setStatus({ phase: 'loading' })
        setAttempt(value => value + 1)
      }}>重试</button>
    </div>}
  </div>
}
