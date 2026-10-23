# 第三方组件与源码

## MathCyclus

本项目基于 JinLingxi（槿灵兮）及贡献者开发的 MathCyclus 数学题库助手。
原作许可为 MIT，完整署名与许可见 LICENSE、NOTICE.md。
上游：https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant

## PyMuPDF / MuPDF

PyMuPDF 1.26.7 / MuPDF 1.26.12，Copyright Artifex Software, Inc.。
双许可：GNU Affero General Public License version 3 或 Artifex 商业许可。
本项目选择开源 AGPL-3.0 发行路径，包含该依赖的组合应用按 AGPL-3.0 提供。
本项目代码保留其 MIT 授权，第三方原有许可、版权及附加声明保持有效。

完整 AGPL 文本位于 COPYING-AGPL-3.0。本项目完整可修改源码、构建脚本及安装说明同时在本仓库、源码 ZIP、sdist 和便携包中提供，发行时与二进制提供同等访问。

- PyMuPDF 精确版本源码：https://github.com/pymupdf/PyMuPDF/tree/1.26.7
- PyMuPDF 源码归档与 SHA-256：dependency-sources.json 中的 PyMuPDF 1.26.7 条目。
- MuPDF 对应源码：https://mupdf.com/downloads/archive/mupdf-1.26.12-source.tar.gz
- PyMuPDF 构建说明：https://pymupdf.readthedocs.io/en/latest/installation.html
- 本应用构建方式：docs/发行与安装.md、tools/release.py 和 requirements-build.txt。

上述第三方源码归档由相应上游托管。分发者应持续保证这些对应源码可访问；如上游迁移，请更新为相同内容的可用地址或同时镜像源码。依赖未作修改。

## 其他运行时依赖

- Streamlit 1.37.1：Apache-2.0。
- Requests 2.32.3：Apache-2.0。
- python-dotenv 0.21.0：BSD-3-Clause。
- Pillow 10.4.0：HPND / Pillow 许可及其附带图像库许可。
- Apache ECharts 5.5.0：Apache-2.0，static/echarts-5.5.0.min.js 保留上游注释，另见 static/THIRD_PARTY_NOTICES.md。
- CPython：Python Software Foundation License；Windows 便携版由 CPython 3.12.9 构建。
- PyInstaller：GPL-2.0-or-later，附带允许分发构建程序的 bootloader exception；不是将应用改为 GPL 的依据。

精确版本、传递依赖、源码归档和校验值列于 dependency-sources.json；从已安装发行版复制的原始许可全文列于 licenses/，冻结运行时还保留相应 dist-info 元数据。

## 可选外部软件

TeX Live / MiKTeX、Node.js、dsh-ears、whisper 模型和第三方 AI 服务均未捆绑。语音桥接脚本仅调用用户自行安装的运行库。
用户导入的题目及图片不适用本项目软件许可；本仓库和发行版不分发内部题库。
