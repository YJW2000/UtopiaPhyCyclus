import { useMemo } from 'react'
import { StatisticsChart, type StatisticsChartTheme } from './StatisticsChart'

type Counts = Record<string, number>

function slices(counts: Counts) {
  return Object.entries(counts).map(([name, value]) => ({ name, value }))
}

export function StatisticsDistributions({ subjects, types, difficulty, theme }: {
  subjects: Counts
  types: Counts
  difficulty: Counts
  theme: StatisticsChartTheme
}) {
  const bar = useMemo(() => {
    const rows = Object.entries(subjects).sort((a, b) => b[1] - a[1])
    return {
      color: theme.palette,
      tooltip: { trigger: 'axis', renderMode: 'richText', confine: true, axisPointer: { type: 'shadow' } },
      grid: { left: 8, right: 14, bottom: 10, top: 24, containLabel: true },
      xAxis: {
        type: 'category', data: rows.map(([name]) => name),
        axisLabel: { rotate: 30, color: theme.text, interval: 0, hideOverlap: true },
        axisLine: { lineStyle: { color: theme.border } },
      },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { color: theme.muted },
        splitLine: { lineStyle: { type: 'dashed', color: theme.border } } },
      series: [{ type: 'bar', name: '题目数', data: rows.map(([, count]) => count),
        colorBy: 'data', barWidth: '40%', itemStyle: { borderRadius: [6, 6, 0, 0] } }],
    }
  }, [subjects, theme])
  const pie = useMemo(() => {
    const legend = (counts: Counts, top: number) => ({
      type: 'scroll', top, left: 'center', width: '92%', data: Object.keys(counts),
      textStyle: { color: theme.text }, pageTextStyle: { color: theme.text }, pageIconColor: theme.accent,
    })
    const center = ['50%', '58%']
    return {
      color: theme.palette,
      tooltip: { trigger: 'item', renderMode: 'richText', confine: true, formatter: '{a}\n{b}：{c} 道（{d}%）' },
      legend: [legend(types, 0), legend(difficulty, 25)],
      series: [
        { name: '题型分布', type: 'pie', center, radius: [0, '40%'], selectedMode: 'single',
          showEmptyCircle: false, label: { position: 'inner', color: '#fff', fontWeight: 'bold' },
          labelLine: { show: false }, data: slices(types) },
        { name: '难度星级分布', type: 'pie', center, radius: ['55%', '75%'], showEmptyCircle: false,
          itemStyle: { borderRadius: 4, borderColor: theme.surface, borderWidth: 2 },
          label: { formatter: '{b|{b}}\n{c|{c}道}', rich: {
            b: { color: theme.text, fontWeight: 'bold', lineHeight: 20 },
            c: { color: theme.muted },
          } }, data: slices(difficulty) },
      ],
    }
  }, [types, difficulty, theme])

  return <>
    <section className="mc-card statistics-card">
      <h2>各知识板块题目分布</h2>
      {Object.keys(subjects).length ? <StatisticsChart option={bar} label="各知识板块题目数柱状图" /> : <p>暂无数据</p>}
    </section>
    <section className="mc-card statistics-card">
      <h2>题型占比与难度分布</h2>
      {Object.keys(types).length || Object.keys(difficulty).length
        ? <StatisticsChart option={pie} label="双环饼图，内圈为题型，外圈为难度星级" />
        : <p>暂无数据</p>}
    </section>
  </>
}
