import { useEffect, useRef, useState } from 'react'
import { environmentLessons } from './guideEnvironment'
import { lessonScreenshots, type GuideScreenshot } from './guideScreenshots'
import type { GuideAction, GuideLesson, GuidePage } from './guideTypes'
import { workflowLessons } from './guideWorkflows'
import './architecture-guide.css'

const groups = [
  { id: 'guide-setup', title: '配置工作环境', lessons: environmentLessons },
  { id: 'guide-workflows', title: '常用功能与教程', lessons: workflowLessons },
]

function Lesson({
  lesson, run, enlarge, saveRef,
}: {
  lesson: GuideLesson
  run: (action: GuideAction) => void
  enlarge: (screenshot: GuideScreenshot) => void
  saveRef: (element: HTMLDetailsElement | null) => void
}) {
  const { title, summary, steps, example, check, tips, action } = lesson
  const [copyStatus, setCopyStatus] = useState('')

  async function copyExample() {
    try {
      await navigator.clipboard.writeText(example.text)
      setCopyStatus('已复制')
    } catch {
      setCopyStatus('可选中示例文字，按 Ctrl+C 复制。')
    }
  }
  return (
    <details className="guide-lesson" ref={saveRef}>
      <summary>
        <strong>{title}</strong>
        <span>{summary}</span>
      </summary>
      <div className="guide-lesson-body">
        <ol>
          {steps.map((step, index) => {
            const screenshot = lessonScreenshots[lesson.id]?.[index + 1]
            return (
              <li key={step}>
                {step}
                {screenshot && (
                  <figure className="guide-screenshot" style={{ maxWidth: screenshot.previewWidth }}>
                    <button
                      type="button"
                      aria-label={`放大截图 · ${screenshot.title}`}
                      onClick={() => enlarge(screenshot)}
                    >
                      <img src={screenshot.src} alt={screenshot.alt} loading="lazy" decoding="async" />
                      <span aria-hidden="true">放大查看 ↗</span>
                    </button>
                    <figcaption>{screenshot.title}</figcaption>
                  </figure>
                )}
              </li>
            )
          })}
        </ol>
        <div className="guide-example">
          <h3>{example.title}</h3>
          <p className={example.copyLabel ? 'guide-example-source' : undefined}>{example.text}</p>
          {example.copyLabel && (
            <div className="guide-actions">
              <button onClick={() => void copyExample()}>{example.copyLabel}</button>
              <span role="status">{copyStatus}</span>
            </div>
          )}
        </div>
        <p><strong>完成后检查</strong>　{check}</p>
        {tips?.length ? <ul>{tips.map((tip) => <li key={tip}>{tip}</li>)}</ul> : null}
        {action && <button onClick={() => run(action)}>{action.label}</button>}
      </div>
    </details>
  )
}

export default function ArchitectureGuide({
  navigate, demo,
}: {
  navigate: (page: GuidePage) => void
  demo: () => void
}) {
  const lessons = useRef(new Map<string, HTMLDetailsElement>())
  const screenshotDialog = useRef<HTMLDialogElement>(null)
  const [screenshot, setScreenshot] = useState<GuideScreenshot | null>(null)

  useEffect(() => {
    if (screenshot) screenshotDialog.current?.showModal()
  }, [screenshot])

  function jumpTo(id: string) {
    const lesson = lessons.current.get(id)
    if (!lesson) return
    lesson.open = true
    lesson.querySelector('summary')?.focus({ preventScroll: true })
    lesson.scrollIntoView({ block: 'start' })
  }

  function run(action: GuideAction) {
    if ('page' in action) navigate(action.page)
    else demo()
  }

  return (
    <div className="architecture-guide">
      <h1>📘 架构介绍</h1>
      <section className="mc-card guide-overview" aria-labelledby="guide-overview-title">
        <h2 id="guide-overview-title">从原题到试卷</h2>
        <p>从截图、PDF 或 TeX 开始，在工作台校对题干、公式和配图。确认入库后即可检索和选题，再把选题篮里的题按顺序排成试卷。</p>
        <ol className="guide-flow" aria-label="题库工作流">
          <li>录入原题</li>
          <li>校对文稿</li>
          <li>确认入库</li>
          <li>检索组卷</li>
        </ol>
        <div className="guide-actions">
          <button className="primary" onClick={demo}>打开「力与运动」演示题</button>
          <button onClick={() => jumpTo('agent')}>配置本地 Agent</button>
        </div>
        <dl className="guide-structure">
          <div><dt>工作文稿</dt><dd>保存导入材料、TeX、预览和编辑记录。可从录入页的「最近文稿」继续。</dd></div>
          <div><dt>本地题库</dt><dd>正式题目与相关图保存在题库文件夹，SQLite 索引用于查找、筛选和组卷。已保存的工作文稿需确认入库才会出现在题库中。</dd></div>
          <div><dt>Agent 与排版</dt><dd>Agent 协助识别和提出修改，PDF 工作台供你对照审阅，LaTeX 将确认后的源码编译成预览或可打印 PDF。</dd></div>
        </dl>
      </section>
      <section className="mc-card guide-section" aria-labelledby="guide-manual">
        <h2 id="guide-manual">规范指南</h2>
        <ul>
          <li>题目按主知识板块和年份保存在 <code>chapters</code> 下，文件名为「年份-试卷类别-试卷名称-题号-知识板块.tex」。多个板块用中文逗号分隔。</li>
          <li>每题用一个 <code>problem</code> 环境，五个参数与文件名对应。选择题用 <code>choices</code> 和 <code>{String.raw`\choice{{选项内容}}`}</code>，答案和解析分别放入 <code>answer</code> 和 <code>solutions</code> 环境。</li>
          <li>用 <code>tikzpicture</code> 绘制示意图。照片等外部图片放入该题的「相关图」目录，用 <code>{String.raw`\includegraphics`}</code> 在对应位置引用。</li>
        </ul>
        <button onClick={() => jumpTo('tex-intake')}>查看 TeX 示例</button>
      </section>
      <nav className="mc-card guide-contents" aria-label="教程目录">
        <p>选择教程跳到操作步骤。点击截图可放大查看。</p>
        {groups.map(({ id, title, lessons: items }) => (
          <div key={id}>
            <h2>{title}</h2>
            <ul>
              {items.map(({ id: lessonId, title: lessonTitle }) => (
                <li key={lessonId}><button onClick={() => jumpTo(lessonId)}>{lessonTitle}</button></li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      {groups.map(({ id, title, lessons: items }) => (
        <section key={id} className="mc-card guide-section" aria-labelledby={id}>
          <h2 id={id}>{title}</h2>
          {items.map((lesson) => (
            <Lesson
              key={lesson.id}
              lesson={lesson}
              run={run}
              enlarge={setScreenshot}
              saveRef={(element) => {
                if (element) lessons.current.set(lesson.id, element)
                else lessons.current.delete(lesson.id)
              }}
            />
          ))}
        </section>
      ))}
      <dialog
        ref={screenshotDialog}
        className="guide-screenshot-dialog"
        style={screenshot?.previewWidth ? { width: screenshot.previewWidth * 2 } : undefined}
        aria-labelledby="guide-screenshot-title"
        onClose={() => setScreenshot(null)}
      >
        <div className="guide-screenshot-header">
          <h2 id="guide-screenshot-title">{screenshot?.title}</h2>
          <button type="button" onClick={() => screenshotDialog.current?.close()}>关闭</button>
        </div>
        {screenshot && <img src={screenshot.src} alt={screenshot.alt} />}
      </dialog>
    </div>
  )
}
