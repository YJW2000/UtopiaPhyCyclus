export type GuidePage = 'entry' | 'tex' | 'pdf' | 'browse' | 'search' | 'exam' | 'settings' | 'papilot'

export type GuideAction = { label: string } & ({ page: GuidePage } | { demo: true })

export type GuideLesson = {
  id: string
  title: string
  summary: string
  steps: string[]
  example: { title: string; text: string; copyLabel?: string }
  check: string
  tips?: string[]
  action?: GuideAction
}
