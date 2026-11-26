import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { StatisticsChart, type StatisticsChartTheme } from './StatisticsChart'
import './statistics-activity.css'

type Counts = Record<string, number>

const dateFormat = new Intl.DateTimeFormat('sv-SE')
const monthFormat = new Intl.DateTimeFormat('zh-CN', { month: 'short' })
const numberFormat = new Intl.NumberFormat('zh-CN')
const weekdays = ['日', '一', '二', '三', '四', '五', '六']
const hoursPerDay = 24
const activityDays = 7
const todayIndex = activityDays - 1
const timelineEnd = activityDays * hoursPerDay - 1
const timeMarkers = [
  { hour: 6, height: 0, icon: '🌅' },
  { hour: 12, height: 1.2, icon: '☀️' },
  { hour: 18, height: 0, icon: '🌆' },
  { hour: 21, height: -0.7, icon: '🌙' },
]

function calendarDays() {
  const date = new Date()
  date.setDate(date.getDate() - 364)
  const firstWeekday = date.getDay()
  return Array.from({ length: 365 }, (_, index) => {
    const day = {
      date: dateFormat.format(date),
      weekday: date.getDay(),
      week: Math.floor((index + firstWeekday) / 7),
      month: index === 0 || date.getDate() === 1 ? monthFormat.format(date) : null,
    }
    date.setDate(date.getDate() + 1)
    return day
  })
}

function activityLevel(count: number) {
  if (!count) return 0
  if (count <= 2) return 1
  if (count <= 5) return 2
  if (count <= 10) return 3
  return 4
}

function timeLabel(hour: number, minute = '00') {
  return `${String(hour).padStart(2, '0')}:${minute}`
}

type Hour = {
  date: string
  label: string
  hour: number
  count: number
  position: number
  height: number
}

function activityOptions(timeline: Hour[], startHour: number, theme: StatisticsChartTheme): Record<string, unknown> {
  const maxCount = Math.max(1, ...timeline.map(point => point.count))
  const activity = timeline.map(({ position, height, count, date, label, hour }) => ({
    value: [position, height],
    symbolSize: 12 + 16 * count / maxCount,
    itemStyle: { color: count ? theme.accent : theme.muted, opacity: count ? 0.4 + 0.6 * count / maxCount : 0.23 },
    tooltip: {
      formatter: `${label} · ${date}\n${timeLabel(hour)}–${timeLabel(hour, '59')} · ${numberFormat.format(count)} 次活动`,
    },
  }))
  const icons = timeline.flatMap(point => timeMarkers
    .filter(marker => marker.hour === point.hour)
    .map(marker => ({
      value: [point.position, marker.height],
      label: { formatter: marker.icon },
    })))
  const boundaries = timeline.filter(point => point.hour === 0).map(point => ({
    xAxis: point.position,
    lineStyle: { color: theme.accent, opacity: 0.3 },
    label: {
      show: true,
      formatter: point.label,
      position: 'insideEndTop',
      distance: 3,
      color: theme.text,
      fontWeight: 700,
      backgroundColor: theme.surface,
      borderRadius: 4,
      padding: [2, 5],
    },
  }))
  const fixedWindow = {
    xAxisIndex: 0,
    filterMode: 'none',
    zoomLock: true,
    minValueSpan: hoursPerDay - 1,
    maxValueSpan: hoursPerDay - 1,
    startValue: startHour,
    endValue: startHour + hoursPerDay - 1,
  }

  return {
    animationDurationUpdate: 280,
    grid: { top: 25, left: 15, right: 15, bottom: 48, containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      max: timelineEnd,
      minInterval: 3,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        formatter: (position: number) => timeLabel(position % hoursPerDay),
        color: theme.muted,
        fontSize: 11,
        fontFamily: 'monospace',
        hideOverlap: true,
      },
    },
    yAxis: { type: 'value', min: -1.25, max: 1.5, show: false },
    dataZoom: [
      {
        ...fixedWindow,
        type: 'inside',
        zoomOnMouseWheel: false,
        moveOnMouseWheel: false,
      },
      {
        ...fixedWindow,
        type: 'slider',
        bottom: 8,
        height: 18,
        showDetail: false,
        borderColor: theme.border,
        fillerColor: theme.surface,
        backgroundColor: theme.border,
        handleStyle: { color: theme.accent, borderColor: theme.accent },
        moveHandleStyle: { color: theme.accent },
        dataBackground: { lineStyle: { color: theme.accent }, areaStyle: { color: theme.accent, opacity: 0.18 } },
      },
    ],
    series: [
      {
        name: '时段',
        type: 'line',
        data: timeline.map(point => [point.position, point.height]),
        smooth: true,
        symbol: 'none',
        silent: true,
        lineStyle: { color: theme.accent, opacity: 0.42, width: 2 },
        markLine: {
          symbol: 'none',
          silent: true,
          lineStyle: { type: 'dashed' },
          label: { show: false },
          data: [{ yAxis: 0, lineStyle: { color: theme.accent, opacity: 0.3 } }, ...boundaries],
        },
      },
      { name: '活动次数', type: 'scatter', data: activity, z: 4, emphasis: { scale: 1.2 } },
      {
        name: '时段提示',
        type: 'scatter',
        data: icons,
        symbolSize: 0,
        silent: true,
        label: { show: true, position: 'top', distance: 14, fontSize: 16 },
      },
    ],
    tooltip: {
      trigger: 'item',
      renderMode: 'richText',
      confine: true,
      backgroundColor: theme.surface,
      borderColor: theme.border,
      textStyle: { color: theme.text },
    },
  }
}

interface ZoomRange {
  start?: number
  end?: number
  startValue?: number
  endValue?: number
}

export function StatisticsActivity({ daily, hourly, theme }: {
  daily: Counts
  hourly: Record<string, Counts>
  theme: StatisticsChartTheme
}) {
  const calendar = useMemo(calendarDays, [])
  const months = useMemo(() => {
    const labels = calendar.filter(day => day.month)
    return labels[1].week < 3 ? labels.slice(1) : labels
  }, [calendar])
  const heatmap = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const scrollArea = heatmap.current
    if (scrollArea) scrollArea.scrollLeft = scrollArea.scrollWidth
  }, [])
  const yearCount = calendar.reduce((total, day) => total + (daily[day.date] ?? 0), 0)
  const days = useMemo(() => calendar.slice(-activityDays).map(({ date }, index) => ({
    date,
    label: index === todayIndex ? '今天' : index === todayIndex - 1 ? '昨天' : date.slice(5),
  })), [calendar])
  const timeline = useMemo(() => days.flatMap(({ date, label }, day) =>
    Array.from({ length: hoursPerDay }, (_, hour) => ({
      date,
      label,
      hour,
      count: hourly[date]?.[String(hour).padStart(2, '0')] ?? 0,
      position: day * hoursPerDay + hour,
      height: Math.sin((hour - 6) / hoursPerDay * Math.PI * 2),
    }))), [days, hourly])
  const [selectedDay, setSelectedDay] = useState(todayIndex)
  const [windowPosition, setWindowPosition] = useState({ start: todayIndex * hoursPerDay })
  const option = useMemo(() => activityOptions(timeline, windowPosition.start, theme), [timeline, windowPosition, theme])
  const selectedDate = days[selectedDay].date
  const handleDataZoom = useCallback((event: unknown) => {
    if (!event || typeof event !== 'object') return
    const payload = event as ZoomRange & { batch?: ZoomRange[] }
    const range = payload.batch?.[0] ?? payload
    const center = typeof range.startValue === 'number' && typeof range.endValue === 'number'
      ? (range.startValue + range.endValue) / 2
      : ((range.start ?? NaN) + (range.end ?? NaN)) * timelineEnd / 200
    if (Number.isFinite(center)) {
      const day = Math.floor(Math.round(center) / hoursPerDay)
      setSelectedDay(Math.max(0, Math.min(todayIndex, day)))
    }
  }, [])

  return <>
    <section className="mc-card statistics-card statistics-heatmap">
      <h2>活跃指标</h2>
      <p className="statistics-activity-summary">最近 365 天 · {numberFormat.format(yearCount)} 次活动</p>
      <div ref={heatmap} className="statistics-heatmap-scroll" tabIndex={0} role="group" aria-label="每日活动，可横向滚动查看">
        <div className="statistics-heatmap-grid">
          {weekdays.map((day, index) => (
            <span key={day} className="statistics-heatmap-weekday" style={{ gridRow: index + 2 }}>
              {index % 2 ? day : ''}
            </span>
          ))}
          {calendar.map(({ date, week, weekday, month }) => (
            <span key={date} className="statistics-heatmap-cell" data-level={activityLevel(daily[date] ?? 0)}
              style={{ gridRow: weekday + 2, gridColumn: week + 2 }}
              title={`${date} · ${numberFormat.format(daily[date] ?? 0)} 次活动`} />
          ))}
          {months.map(({ date, week, month }) => (
            <span key={date} className="statistics-heatmap-month" style={{ gridColumn: week + 2 }}>{month}</span>
          ))}
        </div>
      </div>
      <div className="statistics-heatmap-legend" aria-label="颜色从浅到深表示活动从少到多">
        <span>少</span>
        {[0, 1, 2, 3, 4].map(level => <span key={level} className="statistics-heatmap-cell" data-level={level} />)}
        <span>多</span>
      </div>
    </section>
    <section className="mc-card statistics-card statistics-activity-curve">
      <h2>时段活动曲线</h2>
      <div className="statistics-activity-days" role="group" aria-label="活动日期">
        {days.map(({ date, label }, index) => (
          <button key={date} type="button" title={date} aria-pressed={selectedDay === index}
            onClick={() => {
              setSelectedDay(index)
              setWindowPosition({ start: index * hoursPerDay })
            }}>
            {label}
          </button>
        ))}
      </div>
      <p className="statistics-activity-summary">{selectedDate} · {numberFormat.format(daily[selectedDate] ?? 0)} 次活动</p>
      <StatisticsChart option={option} label={`${selectedDate} 的时段活动曲线，圆点大小表示活动次数`}
        className="statistics-hourly-curve" onDataZoom={handleDataZoom} />
    </section>
  </>
}
