import { useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import './pane-divider.css'

interface Props {
  open: boolean
  panelId: string
  label: string
  width: number
  minWidth: number
  maxWidth?: number
  maxFraction: number
  onToggle: () => void
  onWidthChange: (width: number) => void
}

interface Drag {
  pointer: number
  x: number
  y: number
  width: number
  opened: boolean
  moved: boolean
}

export default function PaneDivider({
  open,
  panelId,
  label,
  width,
  minWidth,
  maxWidth = Infinity,
  maxFraction,
  onToggle,
  onWidthChange,
}: Props) {
  const rail = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const skipClick = useRef(false)
  const [containerWidth, setContainerWidth] = useState(0)

  useLayoutEffect(() => {
    const parent = rail.current?.parentElement
    if (!parent) return
    const measure = () => setContainerWidth(parent.clientWidth)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(parent)
    return () => observer.disconnect()
  }, [])

  const maximum = Math.floor(Math.min(maxWidth, containerWidth * maxFraction))
  const minimum = Math.min(minWidth, maximum)
  const clamp = (value: number) =>
    Math.min(maximum, Math.max(minimum, Math.round(value)))
  const actualWidth = open ? clamp(width) : 0
  const toggleLabel = (open ? '收起' : '展开') + label

  function start(event: PointerEvent<HTMLDivElement>) {
    if (!event.isPrimary || event.button !== 0 || drag.current) return
    // Capture on the original control so a simple click still reaches the button.
    const control = (event.target as Element).closest<HTMLElement>(
      'button,[role="separator"]'
    )
    if (!control) return
    skipClick.current = false
    drag.current = {
      pointer: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      width: actualWidth,
      opened: open,
      moved: false,
    }
    control.setPointerCapture(event.pointerId)
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    const active = drag.current
    if (!active || active.pointer !== event.pointerId) return
    const delta = active.x - event.clientX
    if (!active.moved && Math.abs(delta) < 4) {
      if (Math.abs(active.y - event.clientY) >= 4) skipClick.current = true
      return
    }
    active.moved = true
    skipClick.current = true
    if (!active.opened) {
      if (delta <= 0) return
      active.opened = true
      onToggle()
    }
    rail.current?.setAttribute('data-dragging', '')
    onWidthChange(clamp(active.width + delta))
  }

  function finish(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.pointer !== event.pointerId) return
    drag.current = null
    rail.current?.removeAttribute('data-dragging')
  }

  function keys(event: KeyboardEvent) {
    const step = event.shiftKey ? 64 : 24
    const widths: Record<string, number> = {
      ArrowLeft: clamp(actualWidth + step),
      ArrowRight: clamp(actualWidth - step),
      Home: minimum,
      End: maximum,
    }
    if (!Object.hasOwn(widths, event.key)) return
    event.preventDefault()
    if (!open) {
      if (event.key === 'ArrowRight' || event.key === 'Home') return
      onToggle()
      if (event.key === 'ArrowLeft') return
    }
    onWidthChange(widths[event.key])
  }

  return (
    <div
      ref={rail}
      className="pane-divider"
      data-collapsed={!open || undefined}
      onPointerDown={start}
      onPointerMove={move}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onKeyDown={keys}
      onClickCapture={(event) => {
        if (event.detail && skipClick.current) {
          event.preventDefault()
          event.stopPropagation()
        }
        skipClick.current = false
      }}
    >
      <div
        className="pane-divider-grip"
        role="separator"
        aria-label={'调整' + label + '宽度'}
        aria-orientation="vertical"
        aria-controls={panelId}
        aria-valuemin={open ? minimum : 0}
        aria-valuemax={maximum}
        aria-valuenow={actualWidth}
        aria-valuetext={open ? actualWidth + ' 像素' : '已收起'}
        tabIndex={open ? 0 : -1}
        title="左右拖动调整分栏宽度"
      />
      <button
        className="pane-divider-toggle"
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={toggleLabel}
        title={toggleLabel + ' · 左右拖动调整宽度'}
        onClick={onToggle}
      >
        <svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14">
          <path
            d={open ? 'm6 3 5 5-5 5' : 'm10 3-5 5 5 5'}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  )
}
