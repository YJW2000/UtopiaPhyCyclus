import { useEffect, useRef, useState } from 'react'
import echartsUrl from '../../static/echarts-5.5.0.min.js?url'

export interface StatisticsChartTheme {
  surface: string
  text: string
  muted: string
  border: string
  accent: string
  palette: string[]
}

interface Chart {
  setOption(option: object, replace: boolean): void
  on(event: string, handler: (event: unknown) => void): void
  resize(): void
  dispose(): void
}

interface ECharts {
  init(element: HTMLElement, theme: null, options: { locale: string }): Chart
}

let loading: Promise<ECharts> | undefined
function loadCharts(): Promise<ECharts> {
  if (loading) return loading
  loading = new Promise<ECharts>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = echartsUrl
    script.onload = () => {
      const library = (window as Window & { echarts?: ECharts }).echarts
      if (library) resolve(library)
      else reject(new Error('无法初始化图表'))
    }
    script.onerror = () => reject(new Error('无法读取图表资源'))
    document.head.appendChild(script)
  }).catch(error => {
    loading = undefined
    throw error
  })
  return loading
}

export function StatisticsChart({ option, label, className = '', onDataZoom }: {
  option: Record<string, unknown>
  label: string
  className?: string
  onDataZoom?: (event: unknown) => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const instance = useRef<Chart | null>(null)
  const latest = useRef({ option, onDataZoom })
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    latest.current = { option, onDataZoom }
  }, [option, onDataZoom])
  useEffect(() => {
    instance.current?.setOption(option, true)
  }, [option])
  useEffect(() => {
    const element = host.current!
    let active = true
    let observer: ResizeObserver | undefined
    void loadCharts().then(library => {
      if (!active) return
      const chart = library.init(element, null, { locale: 'ZH' })
      instance.current = chart
      chart.setOption(latest.current.option, true)
      chart.on('datazoom', event => latest.current.onDataZoom?.(event))
      observer = new ResizeObserver(() => chart.resize())
      observer.observe(element)
    }).catch(() => { if (active) setError(true) })
    return () => {
      active = false
      observer?.disconnect()
      instance.current?.dispose()
      instance.current = null
    }
  }, [attempt])

  return <>
    <div className={`statistics-chart ${className}`} role="img" aria-label={label} ref={host} hidden={error} />
    {error && <p role="alert">无法显示图表。 <button onClick={() => {
      setError(false)
      setAttempt(value => value + 1)
    }}>重新加载</button></p>}
  </>
}
