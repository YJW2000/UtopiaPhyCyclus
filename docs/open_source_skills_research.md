# 开源物理 LaTeX/TikZ 校验与技能资产调研（Harness v2）

> 调研人：research-scout（共享任务 task-1） · 日期：2026-10-19
> 对应契约：`docs/harness_v2_contract.md` 第 5 节「技能与插件 / 开源接入」
> 仓库许可：MIT（UtopiaPhyCyclus） · 本机环境：Windows + TeX Live 2026（`D:\texlive\2026\bin\windows` 已在 PATH）+ Python 3.12.9（`.venv`）
>
> 方法：① 全网检索（中英文，web_search）；② 对每个候选的 URL / License / 活跃度用 GitHub REST API、PyPI JSON、CTAN 包页、Debian Sources 等**逐一核验**（只写核验到的信息）；③ 本机真实执行工具命令并记录输出（详见 `.backups/probe/tex_tools_verification_2026-10-19.txt`）。
> 凡无法核实的条目在文中明确标注「未核实」，不纳入接入建议。

---

## 1. 结论速览（给 Lead 的 TL;DR）

1. **本机 TeX Live 2026 自带 5 个候选工具全部可用**，且都是**外部进程**形态：`chktex 1.7.9`、`lacheck (TeX Live) 1.30`、`texlogsieve 1.6.1`、`texloganalyser 0.11`、`latexindent 3.24.7`。其中**只有 `texlogsieve` 值得进入 Harness v2 主链路**（作为可选日志过滤器），`chktex` 作为可选 lint 增强；其余价值低或不适合主链路（见 §5）。
2. **`texlogsieve` 的退出码不可用于判定编译成败**：实测无论日志好坏退出码均为 0，必须解析 stdout 摘要文本（`There were errors during processing` / `No important messages to show`）。这是接入时最容易踩的坑。
3. **`chktex` 的告警输出到 stderr、退出码 0（干净）/2（有 warning）**，机器可读格式用 `-v0`（`file:line:col:msgid:message`）。但 chktex **不理解 TikZ 图形语义**，对纯图形代码几乎零产出；只适合补充文本/数学/标签层面 lint。
4. **LLM→TikZ 论文/工具（DeTikZify、AutomaTikZ、DaTikZ、TIKZero、GeoTikzBridge、EASE 2025 TikZ 基准）许可多为 Apache-2.0 或论文 CC 协议，但全部是重量级（GPU/模型/数据集），没有可直接落地的轻量代码**。它们的价值在**验证 rubric 与评测指标思路**（编译成功率、元素清单、视觉相似度），只作「仅参考」。
5. **MCP server 与 Skill 市场均无值得直接接入的现成资产**：`tikz-mcp`（MIT，2 stars）能力等价于项目已有 `compile_preview`；`texflow-mcp`（MIT）是文档级 MCP，架构不匹配；SkillsMP 的 `latex-doctor`（仓库 MIT）是通用 .tex 清理工作流，可借鉴其「编译→分类→再编译」结构，但内容与物理 TikZ 无关。
6. **`pylatexenc`（MIT）在当前会话装不上**：网络可达（pip 已从清华镜像解析到 pylatexenc 2.11），但 DSH 文件沙箱拒绝 pip 解包/rename 临时文件（`OSError: [Errno 13] Permission denied`）；连试 2 次 install + 1 次 download 均失败。**结论：只能作为 try-import 可选增强，不得成为硬依赖**（与契约 5.3 一致）。
7. **本项目 `.agents/skills/developing-with-streamlit` 是悬空符号链接**：指向 `.venv\Lib\site-packages\streamlit\.agents\skills\developing-with-streamlit`，但本机 streamlit 为 1.37.1，无 `.agents` 目录。本次**未改动该符号链接**；新建的 `physics-tikz-harness` 采用独立目录 + 通用 Agent Skill 格式（YAML frontmatter）。
8. **重要工程坑（实测）**：xelatex 无法打开**含中文字符的绝对路径**（报 `! I can't find file 'C:/.../乌托邦...'` → `Emergency stop`，退出码 1）。本项目工作区路径含中文，因此 `compile_tikz()` 必须 `cwd=base_dir` + **相对 ASCII 文件名**调用 xelatex（文件名含中文时先复制为 ASCII 临时名），否则会在中文工作区稳定误报编译失败。此为接入与测试时必须覆盖的用例。

---

## 2. 本机能力核验（真实执行，2026-10-19）

完整原始记录见 `.backups/probe/tex_tools_verification_2026-10-19.txt`，探针文件同目录。摘要：

| 工具 | 路径 | 版本（实测输出） | License（来源） | 典型命令 / 关键行为 |
|---|---|---|---|---|
| chktex | `D:\texlive\2026\bin\windows\chktex.exe` | `ChkTeX v1.7.9` | GPL-2+（CTAN 包页） | `chktex -v0 file.tex`：输出 `file:line:col:msgid:msg`；**告警走 stderr**；退出码 0=干净、2=有 warning；不识别 TikZ 语义 |
| lacheck | `...\lacheck.exe` | `LaCheck (TeX Live) 1.30` | GPL-1+（`--help` 自带声明；CTAN 标 GPL） | `lacheck file.tex`：**全部是 warning，退出码恒 0**；只做 LaTeX 一致性检查，缺分号不报 |
| texlogsieve | `...\texlogsieve.exe` | `texlogsieve 1.6.1`（2021–2026 Nelson Lago） | GPL-3+（CTAN 包页） | `texlogsieve --no-color --no-heartbeat --only-summary job.log`：好坏日志**退出码均 0**；好日志输出 `No important messages to show`；坏日志输出 `** There were errors during processing! ...` |
| texloganalyser | `...\texloganalyser.exe` | `0.11`（2006–2020 Thomas van Oudenhove） | BSD（CTAN 包页 + `--version` 自带声明） | `texloganalyser job.log`：面向 warning 统计；实测对含致命错误的日志报 `The log contained 0 warnings.`，**不适合作为主诊断器** |
| latexindent | `...\latexindent.exe` | `3.24.7, 2025-08-15` | GPL-3.0（GitHub cmhughes/latexindent.pl） | 无 `-w` 时格式化结果写 stdout；`-s -w` 静默原地写回 |
| xelatex | `...\xelatex.exe` | `XeTeX 3.141592653-2.6-0.999998 (TeX Live 2026)` | —（TeX Live） | `xelatex -interaction=nonstopmode -halt-on-error -file-line-error x.tex` |

**真实编译诊断样例**（`bad2.tex`：`\draw` 缺结尾分号）：

```text
./bad2.tex:7: Package tikz Error: Giving up on this path. Did you forget a semi
colon?.
...
No pages of output.
EXIT=1
```

→ 启用 `-file-line-error` 后可得 `文件:行号: 消息`；`Package tikz Error: ... Did you forget a semicolon?` 是判定 `syntax.statement-end` 的**权威信号**（建议在 `tex_log_tools` 中映射为稳定类别 `missing-semicolon`，见 §5）。

其他核验事实：

- `tlmgr` 在本沙箱不可用（`Cannot open HKCU for writing at .../TLWinGoo.pm`，注册表写被禁），不影响 TeX 二进制调用；版本判定一律用各工具 `--version`。
- **pylatexenc 安装可行性**：`.venv\Scripts\python.exe -m pip install pylatexenc --timeout 15 --retries 1`（镜像 `pypi.tuna.tsinghua.edu.cn`）能解析到 `pylatexenc-2.11`，但解包阶段失败：`OSError: [Errno 13] Permission denied: '...\Temp\dsh-...\pip-unpack-...\pylatexenc-2.11-py2.py3-none-any.whl'`；重定向 TMP/TEMP 到 `.backups\tmp` 后同样失败；`pip download` 也失败（EXIT=2，无产物）。**网络可用、安装被沙箱阻断**，不是「网络不可用」；正常用户终端里 `pip install pylatexenc` 预计可行，但本会话无法验证。
- `.venv` 的 site-packages 本身**可写**（写入测试通过），失败点仅在 pip 临时文件解包/rename。
- **技能包脚本已实测**：`.agents/skills/physics-tikz-harness/scripts/check_tex_tools.ps1`（6 工具版本自检，EXIT=0）与 `diagnose_tex.ps1`（good.tex 编译成功 EXIT=0 / bad2.tex 失败 EXIT=1 并给出 `./bad2.tex:7: ...` 错误行 + texlogsieve 摘要）均可运行；脚本为纯 ASCII（Windows PowerShell 5.1 对无 BOM 的 UTF-8 .ps1 会按 ANSI 解析导致语法错误，故刻意保持 ASCII）。

---

## 3. 候选矩阵

决策口径：**采用** = 进入实现建议；**可选** = 可接入但必须优雅降级、默认关闭或非关键路径；**仅参考** = 只借鉴思路/rubric，不引入代码/依赖；**不采用** = 许可、依赖或能力不匹配。

### 3.1 TikZ/LaTeX 静态校验与 lint

| 名称 | URL | License | 能力 | 依赖重量 | 接入方式 | 决策 |
|---|---|---|---|---|---|---|
| chktex | https://ctan.org/pkg/chktex （主页 https://www.nongnu.org/chktex/） | GPL-2+ | TeX 文本层 typo/数学/标签 lint；不解析 TikZ 语义 | 极轻（外部 exe） | subprocess：`chktex -v0 tmp.tex`，合并 stdout+stderr 解析 | **可选**（`run_chktex_lint`） |
| lacheck | https://ctan.org/pkg/lacheck | GPL | LaTeX 一致性检查，仅 warning，无 TikZ 感知 | 极轻（外部 exe） | subprocess `lacheck tmp.tex` | 不采用 |
| texlogsieve | https://ctan.org/pkg/texlogsieve | GPL-3+ | 日志过滤 + 摘要；TeX 换行/多行消息处理优于同类 | 极轻（texlua 脚本，TeX Live 自带） | subprocess stdin 喂日志：`--no-color --no-heartbeat --only-summary` | **采用**（`filter_with_texlogsieve`，可选外部进程） |
| texloganalyser | https://ctan.org/pkg/texloganalyser | BSD | 日志元素提取/ warning 统计；对致命错误不敏感（实测） | 极轻（Perl，TeX Live 自带） | subprocess | 不采用（价值低于 texlogsieve） |
| latexindent | https://github.com/cmhughes/latexindent.pl （CTAN https://ctan.org/pkg/latexindent） | GPL-3.0 | LaTeX 格式化/重排 | 中（Perl，TeX Live 自带） | `latexindent -s -w file.tex` | 可选（仅开发期格式化，不进主链路——会改写候选代码，风险高于收益） |
| pylatexenc | https://pypi.org/project/pylatexenc/ （MIT 证据：https://sources.debian.org/src/python-pylatexenc/2.10-2/LICENSE.txt/） | MIT | LaTeX 词法/环境/节点解析，可增强花括号与环境配对判断 | 轻（纯 Python，~1MB） | `try: from pylatexenc.latexwalker import LatexWalker` 失败则回退正则 | **可选**（try-import 增强，当前沙箱装不上） |
| plasTeX | https://github.com/plastex/plastex （PyPI https://pypi.org/project/plasTeX/） | PyPI classifier 标 MIT，GitHub API 标 `NOASSERTION/Other`（**需人工核对**） | 完整 LaTeX 文档 → DOM，可做语义级解析 | 重（Jinja2 / Pillow / Unidecode） | pip 依赖 + 构建 latexcontext | 不采用（重、许可需核、TikZ 无原生支持） |
| latexwalker | 检索未核实（GitHub Search API `q=latexwalker` 返回 0 条；PyPI 元数据抓取失败） | 未核实 | 未核实 | — | — | 不采用（无可核实来源） |

### 3.2 LLM→TikZ 生成 / 自校正 / 评测 harness

| 名称 | URL / 标识 | License | 能力 | 依赖重量 | 决策 |
|---|---|---|---|---|---|
| DeTikZify | https://github.com/potamides/DeTikZify | Apache-2.0（GitHub API；**模型权重许可未能核验**，HF 页面本网络不可达） | MLLM 草图/文本→TikZ 程序合成与迭代优化；1.8k stars，最近推送 2026-09-17 | 重（GPU/模型权重） | 仅参考（自校正循环 + 元素清单思路） |
| DaTikZ | https://github.com/potamides/DaTikZ | Apache-2.0（仓库） | 大规模 TikZ 数据集（训练/评测语料） | 中（数据体积） | 仅参考（few-shot 语料来源，需另核数据许可） |
| AutomaTikZ | https://github.com/potamides/AutomaTikZ ；ICLR 2024 proceedings: https://proceedings.iclr.cc/paper_files/paper/2024/hash/f7641940c7dd9e5de58c20e39586eb64-Abstract-Conference.html | Apache-2.0（仓库） | 文本→TikZ 生成与评测框架 | 中 | 仅参考（评测思想） |
| TikZilla | https://github.com/NL2G/TikZilla | **仓库未声明 License**（GitHub API `license: null`）；论文 arXiv:2603.03072 | 高质量数据 + RL 的 text→TikZ | 重 | 不采用（无许可证禁止复用代码） |
| TIKZero | ICCV 2025 论文：https://openaccess.thecvf.com/content/ICCV2025/papers/Belouadi_TikZero_Zero-Shot_Text-Guided_Graphics_Program_Synthesis_ICCV_2025_paper.pdf | 论文版权归作者（页面可访问） | 零样本 text-guided 图形程序合成 | — | 仅参考 |
| GeoTikzBridge | CVPR 2026 supplemental: https://openaccess.thecvf.com/content/CVPR2026/supplemental/Sun_GeoTikzBridge_Advancing_Multimodal_CVPR_2026_supplemental.pdf | 论文 | 几何感知的多模态→TikZ | — | 仅参考 |
| EASE 2025 TikZ 基准（"LLM Code Customization with Visual Results: A Benchmark on TikZ"） | https://theses.hal.science/IRISA_SET/hal-05049250v2 ；DOI https://dl.acm.org/doi/full/10.1145/3756681.3757003 | 论文 | Best-of-N + 视觉结果评测方法 | — | 仅参考（benchmark 指标设计） |
| Edit2TikZ | arXiv:2608.13441（检索所得，**未直接抓取页面**） | 未核实 | 科学图编辑基准 | — | 仅参考（待核实） |
| TikZBench / VTikZ / TeX-Doctor / geogenbench | 检索未能核实到可用的公开仓库/稳定入口（geogenbench 仅见 HF 数据集页 https://huggingface.co/datasets/meix/geogenbench ，License 未核实且本网络不可达） | 未核实 | — | — | 不采用（**不引入无法核实的依赖**） |

### 3.3 LaTeX/TikZ 的 MCP server / Agent 插件

| 名称 | URL | License | 能力 | 依赖重量 | 决策 |
|---|---|---|---|---|---|
| tikz-mcp | https://github.com/FlorianHuo/tikz-mcp | MIT（GitHub API） | 轻量 MCP server：编译 + 预览 TikZ | 轻（Python；2026-03 创建，2 stars，单人维护） | 仅参考（能力与项目 `DiagramTools.compile_preview` 重叠；若将来把 Pi-Agent 暴露为 MCP 工具，可借鉴其 tool schema） |
| texflow-mcp | https://github.com/aaronsb/texflow-mcp ；PyPI https://pypi.org/project/texflow-mcp/ | MIT（PyPI `license_expression`） | 文档模型（sections/figures/tables）→ XeLaTeX 编译的 MCP server；需 `fastmcp>=3.0`、`mistune` | 中 | 不采用（文档级架构与「TikZ 片段校验」不匹配，且引入新 MCP 运行时依赖） |
| overleaf-mcp-plus 等 | 检索出现但未核验 | 未核实 | — | — | 不采用/不纳入 |

### 3.4 Agent Skill 市场中的 LaTeX/TikZ 技能包

| 名称 | URL | License | 能力 | 决策 |
|---|---|---|---|---|
| SkillsMP `latex-doctor` | https://skillsmp.com/creators/alexander-m-dickerson/ai-asset-pricing/claude-skills-latex-doctor （源仓库 https://github.com/Alexander-M-Dickerson/ai-asset-pricing ） | 仓库 MIT（GitHub API） | 通用 `.tex` 清理：编译→错误分类→去注释→校验 section markers→再编译的六步工作流 | 仅参考（**只借鉴工作流骨架**；其内容不含物理图/几何校验，且不得整段复制，本项目 SKILL.md 全部原创撰写） |
| 本仓库 `.agents/skills/developing-with-streamlit` | 符号链接 → `.venv\Lib\site-packages\streamlit\.agents\skills\developing-with-streamlit` | 随 streamlit 分发 | **本机实测悬空**（streamlit 1.37.1 无 `.agents` 目录） | 不动该链接；新技能独立建目录 |

---

## 4. License 兼容性结论（仓库为 MIT）

1. **GPL 系工具（chktex / lacheck / texlogsieve / latexindent）**：只允许**外部进程调用**（`subprocess.run` 调用用户 TeX Live 安装的可执行文件），不得复制/修改/内联其源码或文档内容进仓库，不得把二进制打包进仓库。理由：MIT 项目与 GPL 程序通过命令行/文件边界交互，属于独立程序聚合（arm's-length），不构成 GPL 意义上的衍生作品。落地要求：
   - 代码中只出现命令名/参数与简短原创说明，不出现 GPL 源码片段；
   - 全部调用**可选且优雅降级**（找不到工具返回空结果/原始日志），并加环境变量开关；
   - 文档注明工具名称、来源 URL 与 License，便于合规审计。
2. **BSD 系（texloganalyser）**：外部进程同样无冲突；即使改造/内联也仅需保留版权声明，但本项目不需要。
3. **MIT（pylatexenc、tikz-mcp、texflow-mcp、latex-doctor 源仓库）**：可改造/引用代码，但必须保留原版权与许可声明；本项目策略是**优先自研标准库实现**，这些只作参考或 try-import 依赖，不复制代码。
4. **Apache-2.0（DeTikZify / DaTikZ / AutomaTikZ）**：允许复用与修改，但需保留 LICENSE/NOTICE 并声明变更；由于依赖极重，本项目**不引入其代码**，只吸收不构成版权的「方法/指标思想」（思路、检查项不受版权保护）。模型权重许可与数据集许可**未能核实**，不得引用其数据进仓库。
5. **未声明 License（TikZilla 仓库）**：默认保留所有权利，**禁止复制任何代码/数据**；只在调研文档中记录名称与链接。
6. **论文（CC BY 类页面 / arXiv / OpenAccess）**：只提炼方法与指标，**不整段翻译或摘录**；本报告与 SKILL.md 均为原创文字，引用处给 URL。
7. **Skill 市场内容**：各条目的许可继承其源仓库；`latex-doctor` 虽为 MIT，本项目仍按「只借鉴结构、全部重写」处理，规避署名遗漏风险。

---

## 5. 精确接入建议（对齐契约第 2、4、5 节）

### 5.1 `utils/tex_log_tools.external_tools()` —— 工具探测

```python
# 建议实现要点（原创伪代码，不是外部源码）
import functools, shutil, os

_TEXLIVE_BIN = r"D:\texlive\2026\bin\windows"   # 仅作 shutil.which 失败后的兜底
_NAMES = ("texlogsieve", "chktex", "lacheck", "latexindent")

@functools.lru_cache(maxsize=1)
def external_tools() -> dict[str, str | None]:
    result = {}
    for name in _NAMES:
        path = shutil.which(name)
        if not path:
            cand = os.path.join(_TEXLIVE_BIN, name + ".exe")
            path = cand if os.path.isfile(cand) else None
        result[name] = path
    return result
```

- 必须在**首次调用时**做缓存，避免每轮编译都 `which`；
- 环境变量总开关建议 `PI_AGENT_EXTERNAL_TEXTOOLS=0` 时全部返回 None；
- 本机实测 5 个工具均在 PATH（`chktex/lacheck/texlogsieve/texloganalyser/latexindent`），兜底路径同样存在。

### 5.2 `utils/tex_log_tools.filter_with_texlogsieve(raw)` —— **最高优先级接入项**

实测关键陷阱：**texlogsieve 退出码永远是 0**，不能作为成功/失败判据。

```python
import subprocess

_SUMMARY_BAD = "There were errors during processing"
_SUMMARY_GOOD = "No important messages to show"

def filter_with_texlogsieve(raw: str) -> str:
    exe = external_tools().get("texlogsieve")
    if not exe or not raw.strip():
        return raw
    try:
        proc = subprocess.run(
            [exe, "--no-color", "--no-heartbeat", "--only-summary"],
            input=raw, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return raw
    out = (proc.stdout or "").strip()
    # 注意：不要用 proc.returncode 判断成败（实测恒为 0）
    if _SUMMARY_BAD in out:
        return out          # 有错误摘要，交给上层 extract_tex_errors
    if _SUMMARY_GOOD in out:
        return ""           # 无重要消息：可安全丢弃噪声
    return out or raw
```

- 建议语义：返回**过滤后的日志文本**；`raw` 无法处理时原样返回（契约要求「失败原样返回」）。
- 若上层希望保留原始日志用于 UI 展开，可让 `compile_tikz` 同时保存 `log`（原始）与 `filtered_log`；`validate_diagram(compile_log=...)` 用 `filtered_log` 提错误、UI 用 `log` 供人工查看。
- 本机验证命令与输出（可直接抄给测试）：

```powershell
D:\texlive\2026\bin\windows\texlogsieve.exe --no-color --no-heartbeat --only-summary bad2.log
# -> ** There were errors during processing! Generated PDF is probably defective **
D:\texlive\2026\bin\windows\texlogsieve.exe --no-color --no-heartbeat --only-summary good.log
# -> No important messages to show
```

### 5.3 `utils/tex_log_tools.run_chktex_lint(tex_source)` —— 可选 lint

```python
import re, tempfile, subprocess, pathlib

_CHKTEX_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):(?P<num>\d+):(?P<msg>.*)$")

def run_chktex_lint(tex_source: str, timeout: int = 20) -> list[dict]:
    exe = external_tools().get("chktex")
    if not exe or not tex_source.strip():
        return []
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False,
                                         encoding="utf-8") as f:
            f.write(tex_source)
            tmp = pathlib.Path(f.name)
        try:
            proc = subprocess.run([exe, "-v0", str(tmp)],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout)
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")  # 告警走 stderr
        finally:
            tmp.unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError):
        return []
    issues = []
    for line in text.splitlines():
        m = _CHKTEX_RE.match(line.strip())
        if m:
            issues.append({
                "category": "syntax.lint",
                "code": f"lint.chktex-{m.group('num')}",
                "line": int(m.group("line")),
                "col": int(m.group("col")),
                "message": m.group("msg"),
                "hint": "chktex 文本层提示；TikZ 图形语义不在其检查范围",
            })
    return issues
```

- **退出码**：干净 0、有 warning 2，两者都按「检查完成」处理；其他码返回 `[]`；
- 实测 chktex 1.7.9 在 Windows 上**告警写 stderr**，必须合并两路输出；
- 对纯 TikZ 图形代码产出很少，建议默认**关闭**（如 `PI_AGENT_CHKTEX=1` 时启用），且只以 `info` 级别并入报告，避免拉低分数。

### 5.4 编译类别扩展：`missing-semicolon`（高价值、零依赖）

实测 `bad2.tex`（`\draw` 缺分号）的日志包含：

```text
./bad2.tex:7: Package tikz Error: Giving up on this path. Did you forget a semi
colon?.
```

建议在 `utils/tex_log_tools.extract_tex_errors()` / `classify_tex_error()` 中增加稳定类别（契约 2 的 15 类之外的可扩展项）：

```python
_PATTERNS_EXTRA = [
    (r"Did you forget a semi\s*colon", "missing-semicolon",
     "TikZ 语句缺少结尾分号：检查 \\draw/\\node/\\path/\\fill 等语句末尾是否漏写 ';'"),
    (r"No shape named", "no-shape-named", "引用了未定义的 \\node 名称或坐标"),
    (r"Unknown key .* in style", "pgfkeys-unknown-key", "使用了模板未定义的 TikZ style/key"),
    (r"Dimension too large", "dimension-too-large", "坐标或尺寸超出 TikZ 上限，检查数值单位/比例"),
]
```

此外 `-file-line-error` 的 `file:line: message` 格式已实测可用；Windows 中文路径/编码下注意日志 `l.7 \node at (1,1) {质量};` 可能乱码（cp936/utf-8 混杂），`clean_tex_log` 依次 `utf-8 → cp936 → latin-1` 解码即可。另注意：**xelatex 不能接收含中文的绝对路径**（实测报 `I can't find file`），`compile_tikz` 必须 `cwd=base_dir` + 相对 ASCII 文件名（详见 §2 第 8 条与技能包 `references/compile-errors.md`）。

### 5.5 其余候选的接入/不接入理由

- **`latexindent` 不进入主链路**：它会**改写**候选代码（重排缩进/换行），可能破坏 TikZ 语义或引入 diff；只建议在开发期格式化技能示例：`latexindent -s -w docs_example.tex`。
- **`lacheck` / `texloganalyser` 不接入**：实测对错误日志无有效输出，增加子进程开销但不增加信息量。
- **`pylatexenc` 可选增强**：在 `services/diagram_validate.py` 中

  ```python
  try:
      from pylatexenc.latexwalker import LatexWalker  # MIT, optional
      _HAS_PYLATEXENC = True
  except Exception:
      _HAS_PYLATEXENC = False
  ```

  有则用其 token 流做花括号/环境配对（比正则更稳），无则回退现有正则；**不得**写进 `requirements.txt` 作为硬依赖。当前沙箱安装被阻断（见 §2），因此在实现/测试环境中应默认走回退路径。
- **MCP server 不接入**：项目当前是 Streamlit 单体 + 内部 LLM 调用，没有 MCP host 运行时；`tikz-mcp` 能力与 `DiagramTools.compile_preview` 重复，`texflow-mcp` 会引入 `fastmcp` 新依赖（违反契约「不新增第三方依赖」）。仅作为将来「把 Pi-Agent 暴露为外部工具」的架构参考。
- **LLM→TikZ 论文只作 rubric 参考**：建议把以下几个不构成版权的评测维度写进 `_visual_validate` 提示词与 `tools/diagram_harness_benchmark.py` 指标：
  1. **编译成功率**（compile pass rate）；
  2. **图元清单一致性**（题干要求的元素是否都出现：力、角度、磁场符号、回路、法线……）；
  3. **几何/数值一致性**（角度、箭头方向、长度比例与题干一致）；
  4. **视觉相似/可读性**（Best-of-N 中选优；可选 CLIP/SSIM 类指标，但本项目只允许额外调用既有 LLM，不做新依赖）；
  5. **失败可解释性**（每条 error 必须带可执行修复建议，而不是只有 COMPILE_ERROR）。

### 5.6 建议优先级（给 Lead）

| 优先级 | 接入项 | 落点 | 工作量 | 收益 |
|---|---|---|---|---|
| P0 | `filter_with_texlogsieve` + `external_tools()` | `utils/tex_log_tools.py` | 小（~40 行） | 日志降噪，回灌反射消息更聚焦；本机已验证 |
| P0 | `missing-semicolon` 等日志类别映射 | `utils/tex_log_tools.classify_tex_error` | 小 | 把最典型 TikZ 错误变成可执行诊断 |
| P1 | `run_chktex_lint`（默认关闭） | `utils/tex_log_tools.py` | 小 | 可选文本层 lint；与契约 5.3 对齐 |
| P2 | 评测维度/rubric 文档化 | `tests/`、benchmark、`_visual_validate` 提示词 | 小 | 质量门禁更可解释 |
| 不接入 | lacheck / texloganalyser / latexindent / MCP / 论文代码 | — | — | 见 §4、§5.5 |

---

## 6. 参考 URL 清单（GitHub/PyPI/CTAN/Debian API 或页面核验；未直接核验者已标注）

CTAN / 工具：
- chktex 包页：https://ctan.org/pkg/chktex · 主页：https://www.nongnu.org/chktex/
- lacheck 包页：https://ctan.org/pkg/lacheck
- texlogsieve 包页：https://ctan.org/pkg/texlogsieve · 仓库：https://gitlab.com/lago/texlogsieve
- texloganalyser 包页：https://ctan.org/pkg/texloganalyser
- latexindent 仓库：https://github.com/cmhughes/latexindent.pl · CTAN：https://ctan.org/pkg/latexindent
- pylatexenc（PyPI）：https://pypi.org/project/pylatexenc/ · MIT 证据：https://sources.debian.org/src/python-pylatexenc/2.10-2/LICENSE.txt/
- plasTeX 仓库：https://github.com/plastex/plastex · PyPI：https://pypi.org/project/plasTeX/

LLM→TikZ / 评测：
- DeTikZify：https://github.com/potamides/DeTikZify
- DaTikZ：https://github.com/potamides/DaTikZ
- AutomaTikZ：https://github.com/potamides/AutomaTikZ · ICLR 2024：https://proceedings.iclr.cc/paper_files/paper/2024/hash/f7641940c7dd9e5de58c20e39586eb64-Abstract-Conference.html
- TikZilla（无 License）：https://github.com/NL2G/TikZilla
- TIKZero（ICCV 2025）：https://openaccess.thecvf.com/content/ICCV2025/papers/Belouadi_TikZero_Zero-Shot_Text-Guided_Graphics_Program_Synthesis_ICCV_2025_paper.pdf
- GeoTikzBridge（CVPR 2026）：https://openaccess.thecvf.com/content/CVPR2026/supplemental/Sun_GeoTikzBridge_Advancing_Multimodal_CVPR_2026_supplemental.pdf
- EASE 2025 TikZ 基准：https://theses.hal.science/IRISA_SET/hal-05049250v2 · https://dl.acm.org/doi/full/10.1145/3756681.3757003
- geogenbench（HF 数据集页，检索所得；**License 未核实、本网络不可达**）：https://huggingface.co/datasets/meix/geogenbench
- Edit2TikZ（仅检索记录，**未抓取页面**）：arXiv:2608.13441（不提供链接，避免误引）

MCP / Skill：
- tikz-mcp：https://github.com/FlorianHuo/tikz-mcp
- texflow-mcp：https://github.com/aaronsb/texflow-mcp · PyPI：https://pypi.org/project/texflow-mcp/
- SkillsMP latex-doctor：https://skillsmp.com/creators/alexander-m-dickerson/ai-asset-pricing/claude-skills-latex-doctor · 源仓库：https://github.com/Alexander-M-Dickerson/ai-asset-pricing

本机核验记录：
- `.backups/probe/tex_tools_verification_2026-10-19.txt`（工具版本、命令、退出码、pip 尝试记录）

---

## 7. 局限与待办

- HF（huggingface.co）在本网络不可达：DeTikZify/DaTikZ **模型与数据集的许可未能核验**，不得据此引入数据；仅记录仓库许可（Apache-2.0）。
- `latexwalker`、`VTikZ`、`TeX-Doctor`、`TikZBench` 未检索到可核实的公开入口，**不纳入**；如后续找到可信来源，可按本报告矩阵格式补录。
- `pylatexenc` 真实安装需在**非沙箱终端**执行一次 `pip install pylatexenc` 后复测；本会话的失败是沙箱限制，不是许可或网络问题。
- 本报告只记录调研与接入建议，未改动 `services/`、`utils/`、`tests/`、`tools/` 下任何文件；实际实现由对应任务负责人按契约完成。

---

## 8. 本仓库落地状态（Lead 回填，2026-10-19）

调研结论已在 Harness v2 实现中落地，对应关系如下（详见
[物理插图矫正AGENT_Harness强化说明](物理插图矫正AGENT_Harness强化说明.md) 与
[harness_v2_contract.md](harness_v2_contract.md)）：

| 调研结论 | 落地实现 | 位置 |
|---|---|---|
| texlogsieve 进主链路（外部进程、摘要文本判定） | `filter_with_texlogsieve()`、`external_tools()` | `utils/tex_log_tools.py` |
| 日志类别新增 `missing-semicolon` | `extract_tex_errors()` 第 17 类，含真实行号与中文 hint | `utils/tex_log_tools.py` |
| chktex 可选、默认关闭、仅 info | `run_chktex_lint()`，`PI_AGENT_CHKTEX=1` 才启用 | `utils/tex_log_tools.py` |
| 保留真实编译日志（原实现丢弃） | `TikzCompileResult` + `compile_tikz()`（含 `source_line_offset` 行号换算） | `utils/tikz_ops.py` |
| 中文路径坑（cwd + 相对 ASCII 文件名） | `compile_tikz()` 固定 `.tikz_cache` + UUID 相对文件名，已加注释 | `utils/tikz_ops.py` |
| 开源验证 rubric 思路（DeTikZify/TikZilla 等） | 六类验收检查 + `physics-diagram-auditor` 技能 + 视觉审图插件 | `services/diagram_validate.py`、`services/agent_skills/` |
| Agent Skill 包落地 | `.agents/skills/physics-tikz-harness/`（SKILL.md + references + scripts），已被 DSH 会话 catalog 加载 | `.agents/skills/` |
| pylatexenc 只能 try-import 可选 | 未引入硬依赖（当前实现纯标准库） | — |
| MCP / 市场 skill 不直接接入 | 以插件加载器 + 两个项目内插件替代 | `services/agent_skills/plugin_loader.py` |
| 外部工具许可合规 | GPL 工具只 `subprocess` 调用，未复制源码/未打包二进制 | 全仓库 |

外部工具来源（许可提醒）：chktex https://ctan.org/pkg/chktex · texlogsieve https://ctan.org/pkg/texlogsieve ·
lacheck https://ctan.org/pkg/lacheck · texloganalyser https://ctan.org/pkg/texloganalyser ·
latexindent https://github.com/cmhughes/latexindent.pl
