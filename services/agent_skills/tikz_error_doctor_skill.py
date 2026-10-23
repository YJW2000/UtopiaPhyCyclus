# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能：TikZ 编译自愈与诊断专家技能 (tikz-error-doctor)

Harness v2 升级：
1. 后置修复统一委托给 `services.diagram_validate.autofix_diagram()` 的确定性流水线
   （去围栏/去文档头/补 tikzpicture/补路径语句分号/补 node 数学模式/Unicode 数学符号/
   全角标点/补全未闭合环境），避免旧版「按行补分号」在多行语句上造成二次破坏；
2. 编译错误诊断优先使用 `utils.tex_log_tools.diagnose_tex_error()`（真实日志 + 行号），
   旧版字符串映射保留为无日志工具时的兜底。
"""

import logging
import re
from typing import List, Tuple

from .base_skill import BaseDiagramSkill

logger = logging.getLogger(__name__)


class TikzErrorDoctorSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "tikz-error-doctor"

    @property
    def name(self) -> str:
        return "TikZ 语法自愈与诊断专家"

    @property
    def description(self) -> str:
        return "自动诊断并纠治 TikZ 常见的语法崩溃陷阱（裸露数学符号、漏分号、坐标缺失、环境不平衡等）。"

    @property
    def tags(self) -> List[str]:
        return ["diagnostic", "doctor", "compiler-self-healing", "syntax-repair"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        # 凡是涉及报错、编译失败、乱码或未显示的，高分激活；且作为基础兜底技能常驻
        kw = ["报错", "编译", "失败", "无法显示", "乱码", "红字", "修复", "检查", "语法"]
        if any(k in user_instruction for k in kw):
            return True, 0.95
        # 兜底常驻支持
        return True, 0.4

    def get_system_instructions(self) -> str:
        return """【TikZ 编译防灾自愈专家指南 (TikZ Doctor Skill)】:
1. 节点标签数学模式防崩规则（TeX 编译致命杀手）：
   - 在 \\node 或 node[...] { ... } 中，所有含下划线 _、上标 ^、度数 \\circ、希腊字母或变量符号的，必须严格放入 $...$！
   - 错误写法示范：`node {30^\\circ}`, `node {F_N}`, `node {v_0}` -> 必然导致 LaTeX 报 "Missing $ inserted" 崩溃！
   - 正确写法示范：`node {$30^\\circ$}`, `node {$F_N$}`, `node {$v_0$}`。
   - 不要使用 Unicode 数学符号（θ、°、×、⊙），一律写成 $\\theta$、$30^\\circ$、$\\times$、$\\odot$。

2. 语句末尾分号不可漏：
   - 每个 \\draw、\\fill、\\path、\\node、\\coordinate 指令必须以英文分号 `;` 结尾！
   - 跨行书写的语句只在语句真正结束时写分号，不要在中间行末尾补分号。

3. 坐标引用自洽性：
   - 若使用了 `\\coordinate (A) at (...);`，后续连线必须确保 `(A)` 已经定义，严禁引用不存在的坐标名。
   - 涉及两点间比例坐标计算（例如 `($(A)!0.5!(B)$)`），必须依赖 calc 宏包（模板已加载）。

4. 只使用模板已加载的库/宏包：
   - 库：calc, positioning, intersections, arrows.meta, quotes, angles, patterns,
     decorations.pathmorphing, decorations.pathreplacing, decorations.markings 等；
   - 禁止使用 \\ce/\\pu（mhchem）、\\SI/\\qty（siunitx）、\\vb/\\dv（physics）等未加载宏包命令；
   - 禁止 \\begin{circuitikz}，电路请用基本图元绘制。

5. 编译失败时优先阅读真实日志：
   - 关注 `文件名:行号: 错误信息` 与 `l.NNN`；常见错误：Missing $、No shape named、Undefined control sequence、
     Did you forget a semi colon、Dimension too large、Runaway argument、pgfkeys unknown key。"""

    def post_process_tikz(self, tikz_code: str) -> Tuple[str, List[str]]:
        """执行确定性语法自愈流水线（优先复用 diagram_validate.autofix_diagram）。"""
        if not tikz_code:
            return tikz_code, []

        try:
            from ..diagram_validate import autofix_diagram
            fixed, fixes = autofix_diagram(tikz_code)
            if fixed:
                return fixed, list(fixes or [])
        except Exception as exc:  # pragma: no cover - 仅在异常环境下回退
            logger.info("autofix_diagram unavailable, fallback to legacy doctor: %s", exc)

        # 兜底：最小化清理（不做按行补分号，避免破坏多行语句）
        applied_fixes: List[str] = []
        code = (tikz_code or "").strip()
        clean_code = re.sub(r"^```(?:latex|tex|tikz)?\s*", "", code, flags=re.MULTILINE)
        clean_code = re.sub(r"\s*```$", "", clean_code, flags=re.MULTILINE).strip()
        if clean_code != code:
            code = clean_code
            applied_fixes.append("清理了外层 Markdown 围栏代码")
        if "\\begin{tikzpicture}" in code and "\\end{tikzpicture}" not in code:
            code = code + "\n\\end{tikzpicture}"
            applied_fixes.append("补齐了缺失的 \\end{tikzpicture}")
        return code, applied_fixes

    @staticmethod
    def diagnose_compiler_error(error_log: str) -> str:
        """
        将系统编译器的原生报错解析为具体、立即可执行的针对性修复建议。
        Harness v2：优先使用真实日志解析工具，旧版字符串映射作为兜底。
        """
        if not error_log:
            return "编译失败，请检查整体语法规范与大括号匹配。"

        try:
            from utils.tex_log_tools import diagnose_tex_error
            advice = diagnose_tex_error(str(error_log))
            if advice:
                return advice
        except Exception:
            pass

        err = str(error_log).lower()
        if "missing $" in err:
            return "检测到公式环境缺失（Missing $ inserted）：请检查所有 \\node 文本，凡包含下划线 _、上标 ^、\\circ 符号或物理量的文字必须严格用 $ 包裹，如 node {$F_N$}。"
        elif "did you forget a semi" in err or "forget a semi colon" in err:
            return "检测到路径语句缺少分号（Did you forget a semi colon）：请给对应的 \\draw/\\fill/\\path/\\node/\\coordinate 语句末尾补 ';'，跨行语句只在真正结束处补。"
        elif "undefined control sequence" in err:
            return "检测到未定义的控制序列（Undefined control sequence）：可能使用了未导入的宏包或拼写错误的 TikZ 命令（如用了不存在的箭头样式或未支持的修饰词）。请改用标准命令。"
        elif "no shape named" in err or "cannot be found" in err:
            return "检测到坐标名称未定义或拼写错误：请核对连线中的具名坐标（例如 (A), (O)）是否在前面已通过 \\coordinate 或 \\node 定义。"
        elif "i do not know the key" in err or "pgfkeys" in err:
            return "检测到未定义的 TikZ 选项/样式（pgfkeys unknown key）：请删除未定义 style，改用模板预定义 style（dot/inc/edge/aux 等）或内联选项。"
        elif "environment" in err and "undefined" in err:
            return "检测到模板未加载的图形环境：请只用 tikzpicture 基本图元绘制，禁止 circuitikz 等未加载环境。"
        elif "runaway argument" in err:
            return "检测到 Runaway argument：通常是花括号、$ 或语句分号不配对；请从日志给出的 l.NNN 行向前检查未闭合结构。"
        elif "extra }" in err or "forgotten $" in err:
            return "检测到多余的 } 或遗漏的 $：请检查对应行附近的括号/数学模式配对。"
        elif "dimension too large" in err:
            return "检测到尺寸溢出（Dimension too large）：坐标数值或计算结果过大，请将坐标等比例缩小至 [-10, 10] 常用区间内。"
        elif "capacity exceeded" in err:
            return "检测到 TeX 容量溢出：可能是递归宏或超大 \\foreach 循环，请拆分图形或简化循环。"
        elif "not loadable" in err or "font" in err:
            return "检测到字体加载失败：中文请交给 ctex 模板处理，不要手写字体命令。"
        elif "timeout" in err:
            return "编译超时：代码中可能存在无限循环的 \\foreach 或极端复杂的图形渲染，请简化网格或曲线抽样率（samples）。"
        elif "emergency stop" in err:
            return "检测到 Emergency stop：请从日志中第一条 `!` 开头的致命错误开始修复。"
        else:
            return f"编译器反馈提示：{error_log[:300]}"
