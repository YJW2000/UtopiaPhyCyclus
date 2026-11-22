import type { GuideLesson } from './guideTypes'

export const environmentLessons: GuideLesson[] = [
  {
    id: 'agent',
    title: '设置本机 Agent',
    summary: '图片识别、原卷重建和题目助手通过所选 Agent 调用模型。本机连接沿用该 CLI 自己的账户和模型。',
    steps: [
      '打开「设置 → 模型连接」，点击「重新检测」。可以选择 Codex、Claude Code、GitHub Copilot、Cursor、OpenCode 或 Gemini CLI。',
      '所需 Agent 显示「未检测到」时，点击该行的「一键安装」，按 Windows 提示完成安装。若安装失败，查看提示后选择「重试安装」或「安装说明」。',
      '显示「已检测到」后，在「默认连接」中选中它。按该行登录提示操作。若有「复制登录命令」，将命令粘贴到 Windows 终端，完成该 CLI 的登录或模型配置。',
      '做图片识别、PDF 原卷重建或带截图的改题时，选支持图片且已配置视觉模型的连接。Cursor 在本应用中可用于纯文本任务。',
    ],
    example: {
      title: '发一条测试消息',
      text: '在「TeX录入」打开演示题，选顶部「分屏」。在右侧「题目助手」发送「请用一句话说明这道题主要考查什么，不修改题目。」',
    },
    check: '设置中显示「当前使用」及所选 Agent，演示题聊天里收到题目助手回复。再用一张小题截图试一次，即可单独验证图片能力。',
    tips: [
      '「已检测到」表示应用找到了 CLI。若消息提示登录、模型配置或额度问题，请在对应 CLI 中处理后重新发送。',
      '已有自定义模型服务时，可以选择「自定义 API → 打开 API 设置」，填写地址、模型和密钥。图片功能取决于该模型的视觉能力。',
    ],
    action: { page: 'settings', label: '打开模型连接设置' },
  },
  {
    id: 'git',
    title: '安装并验证 Git',
    summary: 'Git 可用于自己管理题库文件的版本，也是 Cursor CLI 安装流程会检查的工具。本地浏览、保存和索引维护由题库应用处理。',
    steps: [
      '打开「设置 → 本地工具」，找到 Git。检测结果会显示工具的版本和位置。',
      '显示「未检测到」时，点击 Git 行里的「一键安装」。桌面版通过 WinGet 安装 Git for Windows，安装后会重新检测。',
      '如果使用官方安装包手动安装，请选择可在终端调用 Git 的安装选项。新开一个 Windows 终端运行版本检查，再回到「设置 → 模型连接」点「重新检测」。',
    ],
    example: {
      title: '确认终端能找到 Git',
      text: '在 PowerShell 输入 git --version。若题库已经纳入 Git 管理，可以在该仓库目录运行 git status，查看当前分支和哪些文件有变动。',
    },
    check: '终端显示 git version 和版本号，设置中的 Git 行显示「已检测到」。',
    tips: [
      '终端提示找不到 git 时，先关掉旧终端再开一个新窗口。仍找不到时，用 Git 行的「安装说明」检查 Git for Windows 与系统 PATH。',
      '如需练习版本管理，可在终端进入自己选定的练习目录，运行 git init git-practice，再运行 cd git-practice。首次提交前，用 git config user.name "你的名字" 和 git config user.email "你的邮箱" 设置此练习仓库的提交身份。这些命令在本机执行。',
    ],
    action: { page: 'settings', label: '检查本地工具' },
  },
  {
    id: 'latex',
    title: '配置 LaTeX 与 PDF 编译',
    summary: 'XeLaTeX 把 LaTeX 源码排成 PDF。SyncTeX 为预览区域和源码之间的定位提供信息。',
    steps: [
      '打开「设置 → 本地工具」，查看「LaTeX（XeLaTeX）」。显示「应用内置」或「已检测到」时，可以先试编译。',
      '显示「未检测到」时，点击「一键安装」，由 WinGet 安装 MiKTeX。也可从「安装说明」手动安装，再点「重新检测」。',
      '打开演示题，点「编译 PDF」。可通过「并排 → LaTeX ＋ PDF 预览」核对公式、中文和版式。',
    ],
    example: {
      title: '验证公式能正确排版',
      text: '用演示题完成一次重新编译。在 PDF 预览中核对中英文、上下标和公式，随后用「更多操作 → 导出 PDF」保存一份练习文件。',
    },
    check: '重新编译成功，PDF 预览能完整显示题目。需要按预览框选定位源码时，同时检查 LaTeX 工具行是否提示已检测到 SyncTeX。',
    tips: [
      '出现「LaTeX 编译失败」时，点「编译日志」查看第一处报错。提示找不到 .sty 时检查所需宏包，提示找不到图片时核对文件名和引用位置。',
      '源码修改后，先点「编译 PDF」再重新框选。如果预览区域无法定位，可以切到「LaTeX」选中文字后点「标记选段」。',
    ],
    action: { demo: true, label: '用演示题试编译' },
  },
]
