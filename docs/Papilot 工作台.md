# PDF 校订

桌面版在当前文稿内集成 [Papilot 0.14.1](https://github.com/PapilotAI/papilot/tree/a1d4dfffd5284e1bbbd3d36649ff82a5de897b31) 的 PDF 标注、Agent 适配器和审阅引擎。

## 从题库编辑一道题

1. 在「全局浏览」顶部筛选题目。从左侧选题后，右侧直接打开该题 PDF。打开已有 TeX 或完成截图识别也会直接进入校订。重建原卷或编好试卷后，点「PDF 校订」。
2. 在 PDF 上选中文字，用浮动工具写行内批注或加删除线。也可用左侧工具栏的魔法笔和框选工具标出公式或配图，写明修改要求后发给所选 Agent。
3. 打开「改动」逐项审阅、采用或放弃提案。再次打开文稿时可继续之前的批注和 Git 历史。
4. 点「LaTeX 源码」同步和查看校订后的草稿，完成后用「保存原题」或「另存入库」保存。手工修改源码后，可点「返回 PDF 校订」继续批注。如果草稿与校订稿同时有新的源码改动，应用会保留两个版本并提示先核对。

## 使用项目功能

- 当前文稿的校订区可浏览项目源文件、诊断 LaTeX 编译错误、定位源码、搜索 PDF、查看目录和书签、导出带批注的 PDF。
- 每个 Agent 编辑任务可以独立审阅。工作台提供对比、部分采用、充当审稿人的 Agent、任务历史与反馈、模型及权限选项、协作、技能、记忆和使用记录。模型需要在对应的本地 Agent 或 Writer 中完成登录和配置。
- Git 面板可查看工作树和版本历史，支持暂存、提交、还原、处理冲突和同步。本地审阅和本地提交可以独立进行。拉取或推送使用项目已有的 Git 远端和凭据。
- Overleaf 登录和双向同步、Zotero 引文库和 PDF、论文检索及远程访问均使用 Papilot 本身的流程。这些功能依赖相应的账号或服务。手机访问使用官方 Tailscale Serve 配置，并在启动桌面应用前指定 `PAPILOT_PUBLIC_ORIGIN` 和 `PAPILOT_TAILSCALE_LOGIN`。[Papilot 使用指南](https://github.com/PapilotAI/papilot/blob/a1d4dfffd5284e1bbbd3d36649ff82a5de897b31/docs/guide.md)
- Windows 运行环境可以在校订区的设置中检测和切换。WSL 需要已安装的发行版和其中的工具。Papilot 上游的 Typeless 键盘桥接面向 macOS。
