import agentSettings from './guide-screenshots/settings-agent.webp'
import localTools from './guide-screenshots/settings-tools.webp'
import imageIntake from './guide-screenshots/image-intake.webp'
import texIntake from './guide-screenshots/tex-intake.webp'
import texPreview from './guide-screenshots/tex-preview.webp'
import pdfOriginal from './guide-screenshots/pdf-original.webp'
import pdfRange from './guide-screenshots/pdf-range.webp'
import visualEdit from './guide-screenshots/visual-edit.webp'
import papilot from './guide-screenshots/papilot.webp'
import saveQuestion from './guide-screenshots/save-question.webp'
import searchQuestion from './guide-screenshots/search-question.webp'
import examSettings from './guide-screenshots/exam-settings.webp'
import examExport from './guide-screenshots/exam-export.webp'

export type GuideScreenshot = {
  src: string
  title: string
  alt: string
  previewWidth?: number
}

const toolsScreenshot: GuideScreenshot = {
  src: localTools,
  title: '在本地工具里查看 Git 与 XeLaTeX，并测试中文排版',
  alt: '本地工具区显示 Git 和 LaTeX 的检测状态、版本，以及测试中文排版按钮。',
}

const texPreviewScreenshot: GuideScreenshot = {
  src: texPreview,
  title: '并排查看 LaTeX 源码与编译后的 PDF',
  alt: '力与运动演示题。左栏为带行号的源码，中栏为排版后的 PDF，顶部三角按钮用于编译 PDF。',
}

export const lessonScreenshots: Record<string, Partial<Record<number, GuideScreenshot>>> = {
  agent: {
    1: {
      src: agentSettings,
      title: '在模型连接中重新检测并选择默认 Agent',
      alt: '设置的模型连接区。右上是重新检测，默认连接列表中可选择 Agent 并查看登录提示。',
    },
  },
  git: { 1: toolsScreenshot },
  latex: { 1: toolsScreenshot, 3: texPreviewScreenshot },
  'image-intake': {
    1: {
      src: imageIntake,
      title: '通过剪贴板或图片文件录入',
      alt: '侧栏选中图片识别，录入卡片中有粘贴图片和选择图片两个按钮。',
    },
  },
  'tex-intake': {
    1: {
      src: texIntake,
      title: '选择 TeX 文件，或粘贴源码后打开工作台',
      alt: 'TeX 录入页中有选择 TeX 文件按钮，下方已展开粘贴 TeX 或新建文稿，源码框下有在工作台打开按钮。',
    },
    2: texPreviewScreenshot,
  },
  'pdf-intake': {
    1: {
      src: pdfOriginal,
      title: '打开原卷，在顶部查看和切换页码',
      alt: 'PDF录入工作台中打开了力与运动原卷，顶部有当前页码、总页数和翻页按钮。',
    },
    2: {
      src: pdfRange,
      title: '在识别原卷窗口填写起始页和结束页',
      alt: '识别原卷对话框，上方并列起始页和结束页字段，右下角是开始识别按钮。',
      previewWidth: 404,
    },
  },
  'visual-edit': {
    2: {
      src: visualEdit,
      title: '用左侧虚线框工具截取选区，在题目助手里写明修改要求',
      alt: '左侧框选 PDF 工具已选中，题目助手中有 PDF 截图和修改正方向表述的要求，已勾选仅修改所选区域。',
    },
  },
  'papilot-project': {
    2: {
      src: papilot,
      title: '在题目 PDF 上标记，右侧新任务列出待发送的批注',
      alt: '稿件页中央是演示 PDF 和批注标记，右侧新任务中有一条待发送批注。',
    },
  },
  'save-question': {
    2: {
      src: saveQuestion,
      title: '填写标题、知识板块、题型和标签后另存入库',
      alt: '另存入题库对话框依次显示题目标题、知识板块、题型、标签和另存入库按钮。',
      previewWidth: 404,
    },
  },
  'find-questions': {
    2: {
      src: searchQuestion,
      title: '用关键词和筛选条件检索，直接从结果选题',
      alt: '题目查找页上方有关键词输入框、知识板块、题型、年份和匹配方式，下方每条结果旁有选入按钮。',
    },
  },
  'assemble-paper': {
    2: {
      src: examSettings,
      title: '调整题序、填写试卷名称并生成预览',
      alt: '组卷排版页左侧显示试卷名称和包含答案与解析选项，下方是两道已选题目的上移、下移、移除以及生成试卷并预览按钮。',
    },
    3: {
      src: examExport,
      title: '校对生成的试卷，在更多操作中导出 PDF 或 LaTeX',
      alt: '组卷排版的 PDF 预览包含题干、答案和解析，右上更多操作菜单中有导出 PDF 和导出 LaTeX。',
    },
  },
}
