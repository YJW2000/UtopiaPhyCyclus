import { useEffect, useRef, useState } from 'react'
import type { PapilotBounds } from './bridge'
import { errorMessage } from './editorContract'

import './papilot-workspace.css'

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

export default function PapilotWorkspace({ onError }: { onError: (message: string) => void }) {
  const slot = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)

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
      void window.utopia.papilotHide().catch(() => {})
      onError(errorMessage(error))
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
          if (active && !failed) setReady(true)
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
  }, [onError])

  return <div ref={slot} className="papilot-workspace" role="region" aria-label="PDF 校订" aria-busy={!ready}>
    {!ready && <p className="papilot-workspace-status" role="status">正在打开 PDF 校订…</p>}
  </div>
}
