# 物理插图矫正 AGENT · Harness v2 定向强化说明

> 日期：2026-10-19 ｜ 仓库：UtopiaPhyCyclus（MIT）
> 接口契约：[harness_v2_contract.md](harness_v2_contract.md) ｜ 开源调研：[open_source_skills_research.md](open_source_skills_research.md)
> 技能包：[.agents/skills/physics-tikz-harness/SKILL.md](../.agents/skills/physics-tikz-harness/SKILL.md)

## 1. 为什么需要 Harness v2

原版 Pi-Agent 的闭环是「LLM 生成 → `xelatex` 编译 → 失败就重试」。它有三个硬伤：

1. **编译成功 ≠ 物理图正确**：箭头缺失、斜面角度画错、磁场方向没标、电路没闭合，这些都能编译通过；
2. **失败反馈太弱**：`utils/tikz_ops.get_tikz_image_b64()` 失败只返回一个 `COMPILE_ERROR` 字符串，
   真实日志被丢弃，模型只能盲猜；
3. **没有质量门禁**：最后一版无论好坏都会写回题目源码，用户可能保存下比原来更差的图。

Harness v2 在这三层之上补齐了「**确定性校验 → 真实日志诊断 → 校验-矫正闭环 → 质量门禁**」，
并接入了可扩展的技能/插件体系与开源 TeX 工具。

## 2. 架构总览

```
用户指令/语音
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PiDiagramAgent.run_turn()  (services/diagram_agent.py)               │
│                                                                     │
│  ① 技能匹配 + 前置提炼（SkillRegistry: 意图/力学/电磁/光学/验收/医生） │
│  ② LLM 生成（三段式 THOUGHT/TIKZ/EXPLANATION）                        │
│  ③ 确定性净化（技能 post_process → diagram_validate.autofix_diagram） │
│  ④ 六类静态校验（diagram_validate.validate_diagram）                  │
│  ⑤ 真实编译（tikz_ops.compile_tikz，保留 xelatex 原始日志）            │
│  ⑥ 日志诊断（tex_log_tools: 文件:行号 + 类别 + 中文修复建议）          │
│  ⑦ 视觉校验（多模态模型审图，可选、一次、失败即跳过）                   │
│  ⑧ 反思重生成（校验清单 + 编译诊断 + 视觉问题 + 上一版代码）            │
│  ⑨ 保留历史最佳候选 → 质量门禁 → should_autosync                     │
└─────────────────────────────────────────────────────────────────────┘
      │
      ▼
UI（services/diagram_agent_ui.py）：质量徽章 / 校验报告 / 自动修复记录 / 编译器日志
```

## 3. 确定性校验层（`services/diagram_validate.py`）

### 3.1 六类检查

| 类别 | 主要检查 | 代表机器码 |
|---|---|---|
| 结构 structure | tikzpicture 环境、环境/花括号/`$` 配平、空图、多图、围栏残留 | `empty` `missing-env` `unbalanced-env` `brace` `math-balance` `multiple-env` |
| 语法 syntax | 路径语句分号（**按语句而非按行**）、node 花括号、具名坐标先定义后引用、未加载库/环境、文档级命令、未加载宏包命令（mhchem/siunitx/physics）、悬空 style | `statement-end` `node-brace` `undefined-coordinate` `unknown-library` `unsupported-env` `header-leak` `missing-package-command` `dangling-style` |
| 语义 semantics | node 标签数学符号 `$...$`、`$` 配对、数学模式内中文须 `\text{}`、Unicode 数学符号 | `node-math` `node-unbalanced-math` `cjk-math` `unicode-math` |
| 物理 physics | 受力箭头+力标注、斜面斜线/角度弧/角度值、垂直纸面磁场 ⊙/⊗、光路箭头+法线、电路闭合回路+结点、弹簧 coil、滑轮圆+切线、电场线/等势线、波形曲线 | `force-no-arrow` `force-no-label` `force-arrow-short` `incline-missing` `incline-no-angle` `incline-angle-mismatch` `magnetic-symbol` `optics-no-arrow` `optics-no-normal` `circuit-open` `circuit-junction` `spring-symbol` `pulley-no-circle` `pulley-no-tangent` `field-no-arrow` `wave-symbol` |
| 几何 geometry | 数值倾角比对（题干 30° vs 图中实际）、角度弧与参考线一致、退化线段、坐标量级 | `incline-angle-mismatch` `angle-arc-mismatch` `degenerate-segment` `out-of-bounds` |
| 可读性 readability | 绝对定位标注重叠（考虑 above/below/left/right 与 scope 位移） | `label-overlap` |
| 编译 compile | 仅当传入 `compile_log` 时合并：真实日志类别 + 行号 | `compile.missing-math` `compile.missing-semicolon` `compile.no-shape-named` … |

> 关键实现细节：注释掩码保留字符偏移；花括号/语句扫描器区分「跨行语句」与「真正漏分号」；
> 支持 `(x,y)`、极坐标 `(30:1.5)`、具名坐标、`calc` 表达式；`scope` 的 `xshift/yshift/shift`
> 会累计到坐标上（并排双图不再误判标签重叠）；正则全部在掩码文本上运行，避免注释干扰。

### 3.2 确定性自动修复（`autofix_diagram`）

- 去 Markdown 围栏、去 `\documentclass/\usepackage/begin{document}` 文档头；
- 补 `\begin{tikzpicture}` / `\end{tikzpicture}` 包裹与缺失闭合；
- **按语句**补路径命令结尾分号（跨行语句只在语句真正结束处补，不会破坏多行 `\draw`）；
- 为 node 标签补 `$...$` 数学模式（支持嵌套花括号标签，如 `F_{N}`）；
- Unicode 数学符号转 LaTeX：`θ→\theta`、`°→^\circ`、`×→\times`、`⊙→\odot` …（中文+符号混排时内联 `$...$`）；
- 修正 TikZ 语法区域内的全角标点（node 文本内的中文标点保留）；
- 幂等：对已修复代码再次运行不会产生新改动；
- 可观测：每个已自动修复的问题会以 info 级 issue 同时出现在报告里（`fence` / `header-leak` /
  `missing-env` / `unbalanced-env` / `statement-end` / `node-math` / `unicode-math` /
  `fullwidth-punctuation`），不影响评分，但 UI 与统计可以按机器码观察。

### 3.3 评分与质量门禁

```
score = max(0, 100 - 20×错误数 - 6×警告数)
grade = A(≥90) / B(≥80) / C(≥70) / D(≥60) / F
gate  = fail（存在 error 或 compile.* error，或编译未成功）
      | needs_review（无 error 但 warnings ≥ 2 或 score < 80）
      | pass（其余）
```

`gate == "fail"` 时 Agent **禁止自动同步**；`needs_review` 允许同步但 UI 会提示人工复核。
**编译级硬条件**：`compile_ok == False`（含缺 xelatex / 缺 PyMuPDF / 超时 / 编译错误）时，
即使静态校验通过也会强制 `gate = "fail"` 且 `should_autosync = False` —— 没有完成编译验收的图
不允许自动写回，但用户仍可在 UI 中手动「仍要应用此图」。

## 4. 真实编译诊断（`utils/tex_log_tools.py` + `utils/tikz_ops.py`）

### 4.1 `compile_tikz()`：不再丢弃日志

```python
@dataclass
class TikzCompileResult:
    ok: bool
    b64_png: str | None
    log: str              # 真实 xelatex 日志（≤20000 字符）
    stdout: str
    error_kind: str       # OK|COMPILE_ERROR|TIMEOUT|MISSING_XELATEX|MISSING_PYMUPDF|IO_ERROR|UNKNOWN_ERROR
    error_line: int | None
    duration_s: float
```

- `xelatex -interaction=nonstopmode -halt-on-error -file-line-error`；
- **中文路径兼容（实测坑）**：本机 xelatex 打不开含中文的**绝对路径**（工作区路径含「乌托邦…」），
  因此以 `.tikz_cache` 为 `cwd`、命令行只传相对 ASCII UUID 文件名，绝不拼接中文绝对路径；
- 失败时保留 `.log` + stdout 合并清洗后的日志；
- `get_tikz_image_b64()` 签名/缓存语义保持不变（内部复用 `compile_tikz`）。

### 4.2 日志行号换算（对模型友好）

xelatex 日志里的行号是 **standalone 文档绝对行号**（TikZ 片段前有 27/28 行模板），
直接回灌模型会让它对着不存在的行号改代码。因此：

- `TikzCompileResult.source_line_offset` 记录模板前缀行数；
- Agent 用 `_relativize_tex_log(log, offset, source)` 把 `main.tex:29:` / `l.29`
  换算成 **片段内行号**（只换算落在片段范围内的行号，范围外保持原样）；
- 反思消息会注明「以下行号已换算为你的 TikZ 片段内行号」，可直接对照上一版代码定位。

### 4.2 `tex_log_tools`：日志 → 结构化诊断

- `extract_tex_errors(log)`：解析 `文件:行号: 消息`、`! LaTeX Error:`、`! Package pgf/pgfkeys Error:`、
  `l.NNN`、`Runaway argument` 等，输出 `{category, line, message, hint, context}`；
- 覆盖类别：`missing-math / missing-semicolon / undefined-control-sequence / no-shape-named /
  missing-library / missing-package / missing-file / runaway-argument / extra-brace /
  dimension-too-large / undefined-coordinate / pgfkeys-unknown-key / capacity-exceeded /
  emergency-stop / font-missing / timeout / unknown`；
- `summarize_tex_log()`：给模型看的短摘要（行号+类别+中文建议+上下文）；
- `diagnose_tex_error()`：面向用户/模型的中文诊断；TikZ Doctor 技能优先调用它；
- 可选外部工具（仅外部进程调用，GPL 工具不复制源码、不打包）：
  `filter_with_texlogsieve()`（摘要文本判定，注意其好坏日志退出码都是 0）、
  `run_chktex_lint()`（合并 stdout+stderr，默认关闭，`PI_AGENT_CHKTEX=1` 才启用）。

## 5. 校验-矫正闭环（`services/diagram_agent.py`）

### 5.1 单轮流水线

1. 技能匹配（意图/力学/电磁/光学/验收审计/错误医生 + 插件技能）与前处理动作提炼；
2. LLM 生成三段式回复；
3. 技能后处理 + 校验器 autofix（确定性可修问题直接修，不浪费模型轮次）；
4. 静态校验；**结构类硬失败不编译**，直接进入反思；
5. 编译；失败时用真实日志诊断；
6. 编译成功后复验 + 可选视觉校验（一次，异常跳过）；
7. 未通过则组装反思消息（四件套：校验必须修复清单 + 编译诊断摘要 + 视觉问题 + 上一版代码），
   进入下一轮；轮次用尽后**按 gate → score → compile_ok → 错误数**保留历史最佳候选，
   绝不用更差的最后一次覆盖最佳候选。

### 5.2 返回值与门禁

`run_turn()` 新增字段：`validation`、`validation_score`、`quality_gate`、`gate_passed`、
`should_autosync`、`fixes_applied`、`attempts`、`trace`、`visual_check`、`compile_log`、
`compile_error_kind`；`AgentMessage` 同步持久化这些字段（旧会话 dict 反序列化自动补默认值）。

`trace` 逐轮记录：`compile_ok / compile_error_kind / score / gate / errors / warnings /
fixes / visual_* / elapsed_s`，便于复盘每次自愈发生了什么。

### 5.3 开关（环境变量）

| 变量 | 默认 | 作用 |
|---|---|---|
| `PI_AGENT_VALIDATION` | `1` | `0` 关闭确定性校验（降级为旧行为） |
| `PI_AGENT_VISUAL_CHECK` | `1` | `0` 关闭多模态视觉审图 |
| `PI_AGENT_DISABLE_PLUGINS` | `0` | `1` 关闭外部技能插件加载 |
| `PI_AGENT_CHKTEX` | `0` | `1` 启用 chktex 文本 lint（仅 info） |
| `TIKZ_COMPILE_TIMEOUT` | `60` | xelatex 单次编译超时（秒） |

## 6. 技能与插件

### 6.1 内置技能

- `physics-diagram-auditor`（新增）：注入六类验收 rubric；前处理提炼目标角度与必须出现的物理图元；
  提供一张可编译的「斜面+受力分析」规范范式；
- `tikz-error-doctor`（升级）：后处理统一委托 `autofix_diagram()`（修复旧版按行补分号会破坏多行语句的问题）；
  诊断优先走真实日志工具，旧字符串映射保留兜底。

### 6.2 插件加载器（新增）

```
services/agent_skills/plugins/<plugin_name>/
    manifest.json   # name/version/entry/class/enabled/license/description
    skill.py        # 继承 BaseDiagramSkill 的技能类
```

- `services/agent_skills/plugin_loader.py`：扫描 manifest → 动态导入 → 注册；单个插件失败只记日志；
- 已内置两个插件：`tex-diagnostics`（编译日志诊断指引）、`visual-qa`（多模态审图 rubric）；
- 自定义目录可用 `PI_AGENT_PLUGIN_DIR` 指向。

## 7. 开源资产接入与许可

详细候选矩阵与逐 URL 核验见 [open_source_skills_research.md](open_source_skills_research.md)。结论：

| 资产 | License | 接入方式 | 决策 |
|---|---|---|---|
| texlogsieve（TeX Live） | GPL-3+ | **外部进程** `filter_with_texlogsieve`，摘要文本判定 | 采用（可选增强） |
| chktex（TeX Live） | GPL-2+ | **外部进程** `run_chktex_lint`，默认关闭、仅 info | 可选 |
| lacheck / texloganalyser / latexindent | GPL/BSD | 不接入主链路（实测价值低或会改写候选代码） | 不采用 |
| pylatexenc | MIT | 仅 try-import 可选增强（本沙箱 pip 不可用） | 可选 |
| DeTikZify / TikZilla / AutomaTikZ / DaTikZ / VTikZ / GeoTikzBridge / EASE 2025 基准 | Apache-2.0/CC | 仅借鉴验证 rubric 与指标（编译成功、图元清单、视觉相似） | 仅参考 |
| tikz-mcp / texflow-mcp / SkillsMP latex-doctor | MIT | 能力与现有实现重叠或架构不匹配 | 仅参考 |

新增的 Agent Skill 包 [`.agents/skills/physics-tikz-harness/`](../.agents/skills/physics-tikz-harness/SKILL.md)
把上述能力固化成可复用的 7 步工作流、六类检查清单、编译错误排查表与 PowerShell 自检脚本；
它已被 DSH 会话 skill catalog 自动加载。

**许可合规**：GPL 工具只通过 `subprocess` 调用系统已安装二进制，不复制其源码/不打包二进制；
技能包与插件代码均为本项目原创（MIT），外部思路仅引用 URL。

## 8. 文件与接口变更清单

| 文件 | 变更 |
|---|---|
| `services/diagram_validate.py` | 重写为 v2 确定性校验层（六类检查/autofix/评分门禁/编译日志合并/`apply_extra_issues`/`structural_failure`） |
| `services/diagram_agent.py` | 重写为 Harness v2 自愈闭环（真实日志、视觉校验、最佳候选、门禁、trace、新结果字段） |
| `utils/tex_log_tools.py` | 新增：日志清洗/解析/分类/摘要/诊断/外部工具封装 |
| `utils/tikz_ops.py` | 新增 `TikzCompileResult` + `compile_tikz()`（保留真实日志；`get_tikz_image_b64` 兼容不变） |
| `services/agent_skills/physics_diagram_auditor_skill.py` | 新增验收技能 |
| `services/agent_skills/plugin_loader.py` | 新增插件加载器 |
| `services/agent_skills/plugins/{tex_diagnostics,visual_qa}/` | 新增两个插件 |
| `services/agent_skills/skill_registry.py` | 注册验收技能 + 插件加载 + 异常隔离 |
| `services/agent_skills/tikz_error_doctor_skill.py` | 后处理委托 autofix；诊断接入真实日志 |
| `services/diagram_agent_ui.py` | 质量徽章/校验报告/自动修复记录/编译器日志/门禁暂停自动同步/重编译走日志 |
| `tests/` | 新增 4 个测试文件（校验器、Harness 闭环、日志工具、编译诊断） |
| `.agents/skills/physics-tikz-harness/` | 新增项目级 Agent Skill 包（SKILL.md + references + scripts） |
| `docs/` | 新增本说明 + 接口契约 + 开源调研 |

## 9. 验收与测试

- **单元/集成测试**：`tests/test_diagram_validate.py`（65 项）、`tests/test_diagram_agent_harness.py`（14 项）、
  `tests/test_tex_log_tools.py`、`tests/test_tikz_ops_diagnostic.py`；核心测试全绿；
  全量 `unittest discover` 的失败集合与本轮改动前基线**完全一致**（均为沙箱临时目录导致的既有 UI 测试环境失败，
  见 `.backups/baseline_tests.txt`）。
- **真实 xelatex 验收**：合法物理图编译成功并产出预览；含 `\node {F_N}` 的非法图返回真实日志，
  `tex_log_tools` 识别为 `missing-math` 且带行号；Agent 闭环在真实编译下完成「第一轮校验失败 → 反思 →
  第二轮通过（gate=pass, score=100）」。
- **Golden corpus 基准（独立验证者维护）**：`tests/fixtures/physics_tikz_corpus.py`
  （12 valid + 36 invalid + 5 autofix = 53 用例，覆盖 9 类物理场景、3 个真实题库片段、36 种机器码）
  + `tools/diagram_harness_benchmark.py`（CLI）+ `tests/test_harness_benchmark.py`（20 项）。
  独立验证结果（2026-10-19，`.backups/verifier_benchmark.json`）：

  | 指标 | 实际 | 阈值 | 状态 |
  |---|---|---|---|
  | valid 误报率 | **0.00%**（0/12） | == 0 | PASS |
  | invalid 检出率 | **100%**（36/36） | ≥ 95% | PASS |
  | autofix 成功率 | **100%**（5/5） | == 100% | PASS |
  | gate 准确率 | **100%**（53/53） | == 100% | PASS |
  | code 级 precision / recall | 81.82% / 100%（TP36 FP8 FN0） | 报告项 | — |
  | `validate_diagram` 耗时 | avg 1.2ms / median 0.45ms / p95 7.6ms | — | — |

  `--compile` 真实编译：6/6 valid 图编译成功（含 2 个真实题库片段）；2/2 invalid 图
  编译失败并正确归类（`missing-semicolon` doc30→片段3、`missing-math` doc29→片段2）。

- **对抗性审查（红队）**：独立验证者提出 13 条 findings（`.backups/verifier_findings.md`），
  其中 P0 1 条、P1 2 条、P2 4 条、P3/P4 6 条。本轮已修复全部可复现问题，包括：
  - **P0**：编译失败但日志无解析错误时门禁误判为 pass → 现在 `compile_ok=False` 强制 `gate=fail`、
    禁止自动同步，候选排序把 `compile_ok` 提到最前；
  - **P1**：`arrows.meta` 带参箭头 `-{Latex[length=2mm]}` 误报缺箭头；`[>=Stealth]` 默认样式导致漏报；
  - **P2**：题干乘号 `×` 误触发磁场符号；长力箭头污染倾角检测；细实线法线/`anchor=` 标签误报；
  - **P3/P4**：不相连导线交叉误报结点、规则折线弹簧与自绘 ⊗/⊙ 误报、autofix 分号越界、
    >20KB 日志截断丢失中段致命错误、契约机器码不可观测（现以 info issue 形式保留）、UI 场景标签补全。
  修复后重新运行同一套 findings 复现脚本：全部 13 条均不再复现（`.backups/verifier_findings_repro.json`）。

- **live LLM 冒烟**：本机代理/OAuth 通道当前不可用（`/v1/chat/completions` 对全部模型返回 503
  `Token refresh failed`），已标记 skipped；LLM 交互路径由 mock 测试覆盖，真实编译路径独立验证。

## 10. 已知限制与后续方向

1. 物理语义检查基于题干关键词 + 启发式图元检测，复杂/跨图题目可能漏检或误报；已用真实题库 46 个
   tikzpicture 块做误报回归（0 error，警告仅 2 条且 gate=pass）。
2. 视觉校验依赖多模态模型可用性，失败即跳过，不作为硬门禁；可后续引入「视觉问题二次修复」专用 prompt。
3. 未接入 mhchem/siunitx 等宏包（模板未加载）；如后续扩展模板，可同步放宽 `missing-package-command` 规则。
4. 插件加载器目前只支持进程内 Python 技能；MCP 工具接入留待后续（调研结论见第 7 节）。
5. 视觉校验/实时 LLM 的实际链路验证依赖用户的可用模型通道；本环境 503 属外部 OAuth 问题，与 Harness 实现无关。
