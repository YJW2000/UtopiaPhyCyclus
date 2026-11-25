import type { GuideLesson } from './guideTypes'

export const environmentLessons: GuideLesson[] = [
  {
    id: 'agent',
    title: '设置本机 Agent',
    summary: '配置图片识别、PDF 原卷重建和题目助手使用的模型。选用本机 CLI 时，沿用它自己的账号和模型配置。',
    steps: [
      '打开「设置 → 模型连接」并点击「重新检测」。列表包括 Codex、Claude Code、Pi、Grok Build、Kimi Code、GLM、DeepSeek Harness、Qwen Code 等。',
      '若该行显示「未检测到」，点击可用的「一键安装」，按 Windows 提示操作。安装结束后刷新检测结果。若显示失败，先读该行的提示，再用「重试安装」或「安装说明」继续。',
      '连接显示「已检测到」或「已配置」后，点名称左边的圆形选项将它设为默认连接。展开的详情会说明图片要求。如果出现「复制登录命令」，可粘贴到 PowerShell 执行，再按提示登录。',
      '做图片识别、PDF 原卷重建或带截图的改题时，选择已配置视觉模型、可接收图片的连接。',
      '回到「架构介绍」，点击“打开「力与运动」演示题”按钮。从顶部「工作台布局」选「题目与聊天分栏」，在右侧「题目助手」输入下面的测试消息并点击「发送」。',
    ],
    example: {
      title: '发一条测试消息',
      text: '请用一句话说明这道题主要考查什么。',
      copyLabel: '复制测试消息',
    },
    check: '「模型连接」显示「当前使用」及所选 Agent，「题目助手」能回复演示题的考点。要验证图片能力，可继续用一张清楚的单题截图尝试「图片识别」。',
    tips: [
      '「已检测到」表示应用找到了 CLI。若测试消息提示需要登录、配置模型或检查额度，在对应 CLI 中处理后重新发送。',
      '已有 Papilot 安装的 WorkBuddy ACP 启动器可自动检测。也可以将 UTOPIA_WORKBUDDY_BIN 或 PAPILOT_WORKBUDDY_BIN 指向其他已登录的 WorkBuddy ACP 启动器。',
    ],
    action: { page: 'settings', label: '打开模型连接设置' },
  },
  {
    id: 'git',
    title: '安装并验证 Git',
    summary: 'Git 用于跟踪 LaTeX 文件的改动，Cursor CLI 的安装也会检查它。',
    steps: [
      '打开左侧「设置」，向下滚到「本地工具」，找到「Git」一行。显示「已检测到」时，可以在这行查看应用使用的 Git 版本和可执行文件位置。',
      '显示「未检测到」时，点 Git 那一行的「一键安装」，按 Windows 提示完成 WinGet 安装。也可以点「安装说明」，使用 Git for Windows 的官方安装包，安装时允许从命令行调用 Git。',
      '新开一个 PowerShell 窗口，输入下面的版本检查命令并按 Enter。看到版本号后，回到应用的「设置 → 模型连接」，点击「重新检测」，再核对「本地工具」中的 Git 状态。',
    ],
    example: {
      title: '在 PowerShell 检查 Git 版本',
      text: 'git --version',
      copyLabel: '复制检查命令',
    },
    check: 'PowerShell 输出以 git version 开头，应用的「Git」行显示「已检测到」。',
    tips: [
      '若终端仍提示找不到 git，确认用的是安装后新开的窗口，再按「安装说明」检查 Git 是否已加入 PATH。终端能找到而应用仍显示「未检测到」时，可重新启动应用后再检查。',
      '题库已经纳入 Git 管理时，可在题库目录运行 git status 查看当前分支和有改动的文件。',
    ],
    action: { page: 'settings', label: '检查本地工具' },
  },
  {
    id: 'latex',
    title: '配置 LaTeX 与 PDF 编译',
    summary: '用内置排版测试和演示题验证中文、公式、单位与插图的 PDF 输出。',
    steps: [
      '打开「设置」，向下滚到「本地工具」。若「LaTeX（XeLaTeX）」显示「应用内置」或「已检测到」，点击同一行的「测试中文排版」，等待结果。',
      '若「LaTeX（XeLaTeX）」显示「未检测到」，可点「一键安装」通过 WinGet 安装 MiKTeX，也可点「安装说明」手动安装。回到「模型连接」点击「重新检测」，再到「本地工具」运行「测试中文排版」。',
      '点击本节的「用演示题试编译」，从顶部「工作台布局」选择「LaTeX ＋ PDF 预览」。点击三角形图标「编译 PDF」，在预览中检查中文题干、公式和单位。',
      '从右上角「更多操作」选择「导出 PDF」，在保存对话框中选定目录。打开刚保存的 PDF，核对题干、两小问和页边是否完整。',
    ],
    example: {
      title: '验证公式能正确排版',
      text: '在「力与运动」演示题中，找到题干里的质量和水平拉力。对照左侧 LaTeX 中的数值与单位，核对右侧 PDF 中相应的位置。',
    },
    check: '设置中提示「中文、物理公式、单位和插图均通过排版测试。」演示题编译后，预览和导出的 PDF 都能完整显示题目。',
    tips: [
      '出现「LaTeX 编译失败」时，点「编译日志」查看第一处报错。提示找不到 .sty 时检查所需宏包，提示找不到图片时核对文件名和引用位置。',
      '源码修改后，先点「编译 PDF」再重新框选。如果预览区域无法定位，可以切到「LaTeX」选中文字后点「标记选段」。',
    ],
    action: { demo: true, label: '用演示题试编译' },
  },
]
