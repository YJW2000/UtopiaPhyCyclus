# Pi-Agent Harness v2 接口契约（物理插图矫正 AGENT 定向强化）

> 本文件是并行开发的唯一接口契约。所有模块必须严格按此契约实现；实现方不得擅自修改字段名/语义。
> 版本：v2（2026-10-19）。负责人：Lead。若确需变更，先通知 Lead 并同步更新本文件。

## 0. 强化目标

编译成功 ≠ 物理图正确。Harness v2 在现有「LLM 生成 + xelatex 编译 + 重试」之上，补齐四层能力：

1. **真实编译诊断**：保留 xelatex 原始日志，结构化解析致命错误（文件:行号 + 类别 + 修复建议），回灌自愈，而不是只回一句 `COMPILE_ERROR`。
2. **确定性校验层**：结构 / TikZ 语法 / 语义 / 物理图元 / 数值几何 / 可读性六类检查，可解释、可复现、可打分。
3. **校验-矫正闭环**：输出 → 确定性自动修复 → 静态校验 → 编译 → 日志诊断 → 可选视觉校验 → 反思重生成；保留历史最佳候选，最终质量门禁，禁止“编译通过但物理不对”静默成功。
4. **技能/插件扩展**：内置审计技能 + 插件目录动态加载外部 skill；接入 TeX Live 自带开源工具（texlogsieve / chktex / lacheck）作为可选的确定性诊断增强。

## 1. 确定性校验器 `services/diagram_validate.py`

### 1.1 `DiagramIssue`

```python
@dataclass
class DiagramIssue:
    level: str        # "error" | "warning" | "info"
    code: str         # 稳定机器码，见 1.4；形如 "structure.brace" / "physics.force-no-arrow"
    message: str      # 面向用户与 LLM 的中文说明
    hint: str = ""    # 中文修复建议
    category: str = "structure"  # structure|syntax|semantics|physics|geometry|readability|compile
    fixable: bool = False        # 是否可由确定性 autofix 修复
    # 方法: to_dict() -> dict; as_prompt_line() -> str
```

### 1.2 公共函数（保持向后兼容）

```python
normalize_diagram(tikz: str) -> tuple[str, list[str]]
autofix_diagram(tikz: str, question_text: str = "", user_instruction: str = "") -> tuple[str, list[str]]
validate_diagram(
    tikz: str,
    question_text: str = "",
    user_instruction: str = "",
    compile_log: str = "",      # 可选：原始 xelatex 日志；非空时追加 compile.* 问题
) -> dict
format_report(validation: dict, title: str = "自动校验报告") -> str
build_repair_instructions(validation: dict) -> str   # 回灌给 LLM 的“必须修复清单”
detect_context(question_text: str, user_instruction: str = "") -> list[str]
```

`validate_diagram` 返回报告（所有字段必须存在）：

```python
{
  "ok": bool,               # errors 为空
  "score": int,             # 0..100，见 1.3
  "grade": str,             # A/B/C/D/F
  "gate": str,              # "pass" | "needs_review" | "fail"
  "errors": list[dict],     # DiagramIssue.to_dict()
  "warnings": list[dict],
  "infos": list[dict],
  "issues": list[dict],     # 全部问题（errors+warnings+infos）
  "contexts": list[str],    # force/incline/magnetic/optics/circuit/spring/pulley/...
  "autofixes": list[str],   # 本次 normalize+autofix 已执行的修复说明
  "code": str,              # 自动修复后的完整 tikzpicture 代码（可能与输入不同）
  "metrics": dict,          # 数值几何/图元统计，见 1.5
  "checks_run": list[str],  # 例如 ["structure","syntax","physics","geometry","readability","compile"]
  "summary": str,           # 一行摘要，如 "得分 86/100 · 1 项警告 · 门禁通过"
  "tips": list[str],        # 给 LLM 的 top-3 修复提示（按严重度排序）
}
```

### 1.3 评分与门禁

- `score = max(0, 100 - 20 * len(errors) - 6 * len(warnings))`
- `grade`: score>=90 → A；>=80 → B；>=70 → C；>=60 → D；否则 F
- `gate`:
  - `"fail"`：存在 error，或存在 `compile.*` error；
  - `"needs_review"`：无 error 但 warnings>=2 或 score<80；
  - `"pass"`：其余。
- **Agent 层附加硬条件（v2.1）**：`compile_ok == False` 时最终 `quality_gate` 一律降为 `"fail"`、
  `should_autosync = False`（未完成编译级验收的图不允许自动写回，用户仍可手动应用）。
- `ok == (len(errors) == 0)`，注意 `ok` 不等价于 `gate=="pass"`。

### 1.3.1 自动修复的可观测性（v2.1）

`autofix_diagram()` 修复掉的问题（围栏/文档头/缺失环境/缺分号/裸数学符号/Unicode 符号/全角标点）
会以 **info 级 issue** 的形式同时出现在 `issues/infos` 中（对应机器码 `fence`、`header-leak`、
`missing-env`、`unbalanced-env`、`statement-end`、`node-math`、`unicode-math`、
`fullwidth-punctuation`），便于 UI 展示与按 code 统计；info 不影响 score/gate。

### 1.4 检查项（code 清单，实现应覆盖下列全部）

结构 `structure.*`：
- `missing-env`（缺少 tikzpicture）、`unbalanced-env`、`env-balance`（其它环境）、`brace`、`math-balance`（$ 奇数）、`empty`、`empty-picture`、`header-leak`（documentclass/usepackage 泄漏）、`fence`（Markdown 围栏）。

TikZ 语法/语义 `syntax.*`：
- `statement-end`：`\draw/\fill/\path/\node/\coordinate/\clip/\shade` 语句缺少结尾 `;`（按语句而非按行判断：跨行语句合法，不得误报）。
- `node-brace`：node 文本花括号不闭合。
- `undefined-coordinate`：引用了未定义的具名坐标（如 `(A)` 未定义；允许内置 `(current page)` 等白名单，以及 `(x,y)` 数值坐标、`+(...)`/`++(...)` 相对坐标）。
- `unknown-library`：`\usetikzlibrary` 引用了模板未预载的库（模板清单见 1.6）。
- `unsupported-env`：使用了模板未加载的图形环境（circuitikz 等）。
- `forbidden-command`：`\documentclass`/`\usepackage`/`\input`/`\include` 等文档级命令。
- `dangling-style`：`\tikzset` 未定义却使用的自定义 style；允许模板预定义 style（`dot/inc/edge/aux/...`，见 1.6）与 `every ...`。
- `missing-semicolon-after-foreach` 可并入 statement-end。

数学模式 `semantics.*`：
- `node-math`：node 标签含 `^ _ \circ \theta` 等数学符号却未包 `$...$`（fixable=True）。
- `node-unbalanced-math`：标签内 `$` 不配对。
- `bare-math-text`：`\node` 文本里出现裸 `\alpha/\mu/\omega` 等（同 node-math 合并处理）。
- `cjk-math`：数学模式内直接嵌中文（如 `$质量$`），建议 `\text{质量}`（warning）。

物理 `physics.*`（依据题干/指令关键词）：
- `force-no-arrow`、`force-no-label`（error/warning；受力场景必须有箭头与力标注）。
- `force-arrow-short`：力箭头长度 < 0.5cm（坐标距离近似）→ warning。
- `incline-no-angle`：斜面场景有斜线但无角度弧/角度标注 → warning。
- `incline-angle-mismatch`：指令/题干出现明确角度（如 30°、45°、θ=37°），而图中斜线实际倾角偏差 > 8° → warning（可修复提示）。
- `magnetic-symbol`：垂直纸面磁场缺 ⊙/⊗ → warning。
- `circuit-open`：电路场景闭合回路检查失败（路径连通性近似，允许主干导线）→ error/warning。
- `circuit-junction`：导线交叉/连接处缺实心结点（`circle`/`dot`/`fill` 点）→ warning。
- `optics-no-arrow`、`optics-no-normal`（法线/角度标记）。
- `spring-symbol`、`pulley-no-circle`、`pulley-no-tangent`。
- `label-unit-missing`：数值标签缺单位（如 `5` 应为 `5\,\mathrm{m/s}`）→ info（可对力/长度标签做轻量检查）。

可读性 `readability.*`：
- `label-overlap`：不同 node 坐标距离 < 0.15 且文本不同 → warning。
- `text-collision`：node 文本与线段端点/其它节点过近（可选，info）。
- `out-of-bounds`：坐标绝对值 > 30 → info（提示可能比例失控）。

几何 `geometry.*`（数值一致性，metrics 同步输出）：
- `angle-arc-mismatch`：角度弧标注的起止角与两条参考线实际夹角偏差 > 8° → warning。
- `arrow-direction`：力箭头方向缺少（有 force label 无箭头）→ 并入 force-no-arrow。
- `degenerate-segment`：`(x1,y1) -- (x2,y2)` 两点重合 → warning。

编译 `compile.*`（仅当 `compile_log` 非空时，经 `utils/tex_log_tools.py` 解析）：
- `compile.fatal`（error）、`compile.warning`、`compile.undefined-control-sequence`、`compile.no-shape-named` 等，message 带行号与建议；若日志无法解析，至少输出 `compile.fatal` 摘要。

### 1.5 `metrics` 字段（供 UI 与诊断使用，缺失键用默认值）

```python
{
  "lines": int,
  "draw_commands": int,
  "node_count": int,
  "arrow_count": int,
  "labeled_nodes": int,
  "incline_angle_deg": float | None,   # 图中检测到的最显著斜线倾角
  "expected_angle_deg": float | None,  # 从指令/题干解析到的目标角度
  "angle_delta_deg": float | None,
  "closed_paths": int,
  "magnetic_symbols": int,
  "has_dashed": bool,
  "compile_error_count": int,
}
```

### 1.6 模板环境常量（与 `utils/tikz_ops.py` 一致，若不同步以 `tikz_ops` 为准）

预加载库：`patterns, calc, positioning, intersections, arrows, shapes.geometric, through, decorations.pathmorphing, arrows.meta, quotes, mindmap, shapes.symbols, shapes.arrows, automata, angles, 3d, trees, shadows, shapes.callouts, decorations.pathreplacing, decorations.markings`。
预加载宏包：`amsmath, amssymb, xcolor, tikz, pgfplots, ctex(按需)`。
预定义 style：`cube, hide, hidden, sec, section, cut, aux, sph, plane, edge, dot, inc, inner`。

## 2. 编译诊断 `utils/tex_log_tools.py`（新增）

```python
def clean_tex_log(raw: str, max_chars: int = 20000) -> str
def extract_tex_errors(raw: str) -> list[dict]
    # 每项: {"category": str, "line": int|None, "message": str, "hint": str, "context": str}
def classify_tex_error(raw: str) -> tuple[str, str]   # (category, 中文修复建议)
def summarize_tex_log(raw: str, source: str | None = None, max_chars: int = 4000) -> str
def diagnose_tex_error(raw: str) -> str                # 中文多行诊断，至少覆盖 12 类
def external_tools() -> dict[str, str | None]          # {"texlogsieve": path|None, "chktex": ..., "lacheck": ..., "latexindent": ...}
def filter_with_texlogsieve(raw: str) -> str           # 可选外部过滤，失败原样返回
def run_chktex_lint(tex_source: str, timeout: int = 20) -> list[dict]  # 可选；不可用时返回 []
```

必须覆盖的类别（category 稳定值）：
`missing-math, undefined-control-sequence, no-shape-named, missing-library, missing-package, missing-file, runaway-argument, extra-brace, dimension-too-large, undefined-coordinate, pgfkeys-unknown-key, capacity-exceeded, emergency-stop, font-missing, timeout, unknown`。

日志格式参考：`-file-line-error` 输出 `file:line: message`；另需解析 `! LaTeX Error: ...`、`! Package pgf Error: ...`、`l.NNN` 等经典格式。Windows 编码需容错（utf-8 / cp936 / latin-1 依次尝试）。

## 3. 编译接口 `utils/tikz_ops.py`（新增，保持 `get_tikz_image_b64` 兼容）

```python
@dataclass
class TikzCompileResult:
    ok: bool
    b64_png: str | None = None
    log: str = ""            # 原始 xelatex 日志（截断到 <= 20000 字符）
    stdout: str = ""
    error_kind: str = ""     # OK|COMPILE_ERROR|TIMEOUT|MISSING_XELATEX|MISSING_PYMUPDF|IO_ERROR|UNKNOWN_ERROR
    error_line: int | None = None
    duration_s: float = 0.0
    # v2.1 追加（向后兼容）：模板前缀行数，fragment_line = doc_line - source_line_offset
    source_line_offset: int = 0

def compile_tikz(tikz_code: str, base_dir: str, timeout: float | None = None,
                 target_png_path: str | None = None) -> TikzCompileResult
```

- 复用现有 standalone 模板、CJK 判断、`TIKZ_COMPILE_TIMEOUT` 环境变量、临时文件清理逻辑。
- 失败时**保留日志**（现有实现丢弃日志是本轮重点修复项）。
- `error_line` 语义为**日志中的文档行号**（standalone 文档绝对行号）；Agent 侧会用
  `source_line_offset` 把日志行号换算成 **TikZ 片段内行号**再回灌模型/展示（见第 4 节）。

## 4. `services/diagram_agent.py`（Harness 主循环）

### 4.1 `AgentMessage` 扩展字段（全部带默认值，`to_dict/from_dict` 必须兼容旧会话数据）

```python
validation: dict = {}          # 最终校验报告（见 1.2）
validation_score: int = 0
quality_gate: str = ""         # pass|needs_review|fail
fixes_applied: list[str] = []
attempts: int = 0
compile_log: str = ""
compile_error_kind: str = ""
visual_check: dict = {}        # {"ok":bool,"score":int,"issues":[...],"summary":str,"skipped":bool}
```

### 4.2 `run_turn` 返回字段（向后兼容旧字段）

```python
{
  "ok": bool, "error": str | None,
  "agent_message": AgentMessage,
  "updated_latex": str,
  "tikz_code": str,
  "preview_b64": str | None,
  "compile_ok": bool,
  "compile_error": str,          # 面向用户的短错误（保持旧语义）
  "compile_log": str,            # 原始日志摘要
  "compile_error_kind": str,
  "validation": dict,
  "validation_score": int,
  "quality_gate": str,           # pass|needs_review|fail
  "gate_passed": bool,           # quality_gate == "pass"
  "should_autosync": bool,       # gate != "fail" 且 tikz 非空
  "fixes_applied": list[str],
  "attempts": int,
  "trace": list[dict],
  "visual_check": dict,
  "activated_skills": list[str],
}
```

### 4.3 自愈循环（伪代码，实现须保持语义）

```
best = None                                  # 按 (compile_ok, gate 优先级, score, -errors, -warnings) 选优
for attempt in 0..max_self_healing_attempts:
    1. 调 LLM（首轮：系统提示 + 技能增强 + 上下文；后续轮：追加反思消息）
    2. _clean_response → skill post-processing（确定性净化）
    3. autofix + validate_diagram；若 errors 均 fixable → 自动修复后再 validate
    4. 若结构类 error 严重（missing-env/brace 等）→ 本轮直接记失败，进入反思（不编译）
       否则 compile_tikz；若失败 → tex_log_tools 诊断 → 在反思消息中同时给「静态报告 + 编译诊断 + 上下文片段」
    5. 编译成功 → 用 compile_log="" 重新 validate（或验证已通过）→ 可选视觉校验（仅一次）
    6. 记录 trace；更新 best；若 gate == "pass"（且 compile_ok）且视觉校验未否决 → break
    7. 若 attempt 用尽 → 取 best 作为最终结果；不得用更差的最后一次覆盖 best
最终：updated_latex 仅在 final_tikz 非空时写回；should_autosync = gate != "fail" 且 compile_ok。
编译失败且日志为空/无法解析时，Agent 追加 `compile.fatal` / `compile.unavailable` 等 issue，
保证门禁不会把「未完成编译验收」误判为通过。
```

约束：
- 不新增第三方依赖；所有新能力在无网络/无 xelatex 时必须优雅降级。
- 视觉校验：`_visual_validate(preview_b64, question_text, instruction) -> dict`，用配置的多模态模型（OpenAI 兼容 messages 中 `image_url` data URI），只解析 JSON；任何异常 → `{"ok": True, "score": 0, "issues": [], "summary": "视觉校验已跳过", "skipped": True}`，绝不阻塞。
- 开关：环境变量 `PI_AGENT_VISUAL_CHECK=0` 关闭；`PI_AGENT_VALIDATION=0` 关闭静态校验（兜底降级）；默认开启。
- 反思消息必须包含：确定性问题清单（1.2 `build_repair_instructions`）、编译诊断摘要（`summarize_tex_log`）、视觉校验问题（若有）、上一版代码片段。

### 4.4 保留接口

`DiagramTools.extract_tikz_blocks / apply_tikz_to_latex / compile_preview` 签名不变；新增 `DiagramTools.compile_with_log(tikz_code, base_dir) -> TikzCompileResult`（内部调用 `utils.tikz_ops.compile_tikz`）。

## 5. 技能与插件

### 5.1 内置新技能（`services/agent_skills/`）

- `PhysicsDiagramAuditorSkill`（skill_id `physics-diagram-auditor`）：注入「物理图验收 rubric」，`post_process_tikz` 调用确定性 autofix，`pre_process` 提取目标角度/增删图元动作。
- 扩展 `TikzErrorDoctorSkill.diagnose_compiler_error`：优先调用 `tex_log_tools.diagnose_tex_error`，旧字符串行为保留为兜底。

### 5.2 插件加载器（新增）

目录约定：`services/agent_skills/plugins/<plugin_name>/manifest.json` + `skill.py`。

```json
{"name": "tex-diagnostics", "version": "1.0.0", "entry": "skill.py",
 "class": "TexDiagnosticsSkill", "enabled": true, "description": "..."}
```

```python
# services/agent_skills/plugin_loader.py
def load_plugins(registry: SkillRegistry) -> list[str]   # 返回成功注册的 skill_id；单个插件失败仅记日志
```

`SkillRegistry.__init__` 末尾调用（`PI_AGENT_DISABLE_PLUGINS=1` 时跳过）。插件必须继承 `BaseDiagramSkill`，实现 `skill_id/name/description`；不得在 import 期执行网络/编译。

### 5.3 开源接入（研究结论记录在 `docs/open_source_skills_research.md`）

- 本机 TeX Live 已带：`texlogsieve`（GPLv3）、`chktex`（GPLv2+）、`lacheck`、`latexindent`、`texloganalyser`。**只以外部进程方式可选调用**，不复制其源码进仓库；未安装时优雅降级。
- Python 侧优先标准库实现；若 `pylatexenc`（MIT）安装成功，可作为可选解析增强，不得成为硬依赖。

## 6. UI `services/diagram_agent_ui.py`

- assistant 消息渲染新增：质量门禁徽章（得分/门禁/编译状态/视觉校验）、`🧪 确定性校验报告` 展开器（error/warning + hint）、`🛠️ 确定性自动修复记录`、编译失败时 `📜 编译器日志` 展开器。
- `quality_gate != "pass"` 时显示醒目提示；`gate == "fail"` 时**自动同步暂停**，仅保留手动「仍要应用此图」按钮。
- 「🔄 重新编译」按钮改用 `compile_with_log` + `validate_diagram`，回写消息的校验字段。
- 不修改本文件之外的 UI 逻辑。

## 7. 测试与验收

- 新增 `tests/test_diagram_validate.py`、`tests/test_tex_log_tools.py`、`tests/test_tikz_ops_diagnostic.py`、`tests/test_diagram_agent_harness.py`；全部基于 `unittest`（`.venv\Scripts\python.exe -m unittest ...`），不依赖网络与真实 xelatex（真实编译用例可 `skipUnless(shutil.which("xelatex"))`）。
- 现有 62 个测试中除已知环境相关失败外必须保持通过（基线见 `.backups/baseline_tests.txt`）。
- 真实编译验收：至少 3 个有效物理图编译成功；1 个语法错误图能拿到含行号的日志并诊断出类别。
- 基准语料与指标（检测率/误报率/自动修复率）由独立校验者补充：`tests/fixtures/physics_tikz_corpus.py` + `tools/diagram_harness_benchmark.py`。
