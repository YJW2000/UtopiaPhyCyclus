# Windows 兼容性与验证范围

## Windows x64 完整便携版

目标系统为 Windows 10/11 x64。程序内置 CPython、Python 依赖、裁剪版 TeX Live 2026、Fandol 中文字体、TeX Gyre 字体、Node.js、CPU Whisper 运行库及 multilingual small 模型。无需安装 Python、TeX、Node、DSH、CUDA 或 Vulkan，也无需下载模型。AI 功能使用用户自己的 API 配置。

TeX Live 是针对项目模板、中文、TikZ、PGFPlots、电路图等选取的宏包集合，完整清单在 `runtime-manifest.json`。它不是包含所有 CTAN 宏包的完整 TeX Live；自行编写、依赖额外宏包的任意 TeX 文档不属于预装覆盖范围。

中文解压路径会触发 XeTeX 的 Windows 路径限制。启动器会自动在用户的 ASCII 缓存路径准备 TeX 执行资源，不需要安装或修改系统 PATH。缓存只含软件资源；题目与 API 配置仍保存在解压目录。首次准备缓存需要额外磁盘空间与时间。

## x32 / x86

当前版本不提供标称完整兼容的 32 位独立发行包：

1. Streamlit 现用的 PyArrow 25.0.0 没有官方 Windows win32 wheel；本项目的自定义组件与表格依赖该数据层。直接更换 32 位 Python 无法解决。
2. TeX Live 从 2023 起只提供 64 位 Windows 二进制。官方给 32 位系统的方案是 TeX Live 2022；回退需要重新适配和验证宏包、模板与编译器组合。
3. 当前 @fugood/whisper.node 的 Windows 原生包只提供 x64/ARM64，没有 ia32 CPU 包。

PyMuPDF 1.26.7 本身存在 win32 wheel；它不是这里的主要阻碍。真正的 x86 版本需要维护另一套依赖、组件传输及语音/TeX 后端，不能把 x64 文件改名后作为 x32 发布。

核验入口：
- https://pypi.org/pypi/pyarrow/25.0.0/json
- https://tug.org/texlive/windows.html
- https://github.com/mybigday/whisper.node#platform-support

## 测试方法与限制

构建验收会清除 PATH 中外部 Python、TeX、Node 的路径，使用独立 HOME/USERPROFILE/APPDATA/LOCALAPPDATA，清除 AI 配置和模型环境变量，再运行编译后的 exe。

`UtopiaPhyCyclus.exe --self-test` 检查冻结 Python 依赖、中文/公式/TikZ/电路宏包、三个空白组卷模板的实际 PDF 编译、中文文本提取及两次真实离线语音转写。结果保存在 `.runtime/selftest/report.json`。另验证 HTTP 健康状态和真实 Streamlit WebSocket 会话。

仓库附带 `tools/windows_sandbox_test.ps1`，可在具备 Windows Sandbox 的电脑上生成断网的干净 Windows 自检配置。本次构建主机没有 Windows Sandbox 可执行程序，因此本机的隔离进程验证不能描述成已经在全新 Windows 虚拟机上实测通过。它也不能保证所有 Windows 版本、处理器、驱动和企业策略组合都兼容。
