# -*- coding: utf-8 -*-
"""物理 LaTeX 插图校验器 v2（Pi-Agent Harness 的确定性校验层）

编译成功 ≠ 物理图正确。本模块在 `xelatex` 编译之外，补一层**可解释、可复现**的
确定性静态校验，专门针对高中物理插图，并被 Agent 自愈回路与 UI 共同使用。

v2 相比 v1 的强化：
1. 检查面从 3 类扩展到 6 类：结构 / TikZ 语法 / 数学模式语义 / 物理图元 /
   数值几何 / 可读性，并支持合并真实编译日志（compile.*）。
2. 采用「保留偏移的注释掩码 + 花括号平衡扫描 + 路径语句扫描」替代易误报的
   纯正则，能正确识别跨行语句、嵌套花括号标签、calc 坐标与具名坐标。
3. 数值几何一致性：从题干/指令解析目标倾角，从路径线段与角度弧反推图中实际
   角度并比对；检测退化线段与角度弧不一致。
4. 物理图元检查扩展到受力/斜面/磁场/光路/电路/弹簧/滑轮/电场/波/碰撞等场景，
   并检查电路闭合回路、结点、滑轮切线与力箭头长度。
5. 确定性自动修复增强：去 Markdown 围栏 / 去文档头 / 补 tikzpicture 包裹 /
   补路径语句分号 / 补 node 数学模式 / 补全未闭合环境 / 修正全角标点 / 全角括号。
6. 统一报告：score/grade/gate/errors/warnings/metrics/autofixes/checks_run/tips，
   既直接展示给用户，也通过 `build_repair_instructions` 回灌给模型自愈。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ===================== 模板能力（与 utils/tikz_ops.py 的 standalone 模板保持一致） =====================

TEMPLATE_LOADED_LIBRARIES = {
    "patterns", "calc", "positioning", "intersections", "arrows",
    "shapes.geometric", "through", "decorations.pathmorphing", "arrows.meta",
    "quotes", "mindmap", "shapes.symbols", "shapes.arrows", "automata",
    "angles", "3d", "trees", "shadows", "shapes.callouts",
    "decorations.pathreplacing", "decorations.markings",
}
TEMPLATE_LOADED_PACKAGES = {"amsmath", "amssymb", "xcolor", "tikz", "pgfplots", "ctex"}
TEMPLATE_DEFINED_STYLES = {
    "cube", "hide", "hidden", "sec", "section", "cut", "aux", "sph",
    "plane", "edge", "dot", "inc", "inner",
}

# 模板未加载、但 LLM 高频误用的宏包命令（一旦出现必然编译失败）
_MISSING_PACKAGE_COMMANDS = {
    "\\ce": "mhchem 宏包（模板未加载），改用 \\mathrm{} 手写化学式",
    "\\pu": "mhchem 宏包（模板未加载），改用 \\mathrm{} 手写物理量单位",
    "\\SI": "siunitx 宏包（模板未加载），改用 $数值\\,\\mathrm{单位}$",
    "\\si": "siunitx 宏包（模板未加载），改用 \\mathrm{单位}",
    "\\qty": "siunitx 宏包（模板未加载），改用 $数值\\,\\mathrm{单位}$",
    "\\unit": "siunitx 宏包（模板未加载），改用 \\mathrm{单位}",
    "\\num": "siunitx 宏包（模板未加载），改用纯数字",
    "\\vb": "physics 宏包（模板未加载），改用 \\mathbf{}",
    "\\va": "physics 宏包（模板未加载），改用 \\vec{}",
    "\\vu": "physics 宏包（模板未加载），改用 \\hat{}",
    "\\dv": "physics 宏包（模板未加载），改用 \\frac{d}{dx}",
    "\\pdv": "physics 宏包（模板未加载），改用 \\frac{\\partial}{\\partial x}",
    "\\grad": "physics 宏包（模板未加载），改用 \\nabla",
    "\\curl": "physics 宏包（模板未加载），改用 \\nabla\\times",
    "\\abs": "physics 宏包（模板未加载），改用 |...|",
    "\\norm": "physics 宏包（模板未加载），改用 \\|...\\|",
    "\\eval": "physics 宏包（模板未加载）",
}

# ===================== 物理场景关键词 =====================

_CTX_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "force": ("受力", "受几个力", "支持力", "摩擦力", "拉力", "推力", "重力",
              "浮力", "弹力", "合力", "分力", "正交分解", "平衡状态", "共点力"),
    "incline": ("斜面", "倾角", "斜坡", "斜劈", "光滑斜面", "粗糙斜面"),
    "magnetic": ("磁场", "磁感应强度", "磁感线", "磁通量", "安培力", "洛伦兹力",
                 "通电导线", "通电直导线", "匀强磁场"),
    "optics": ("光线", "入射", "反射", "折射", "全反射", "透镜", "光路", "平面镜",
               "三棱镜", "棱镜", "临界角", "干涉", "衍射", "双缝"),
    "circuit": ("电路", "电阻", "电源", "电流表", "电压表", "滑动变阻器", "电键",
                "开关", "电动势", "并联", "串联", "电容器", "内阻"),
    "spring": ("弹簧", "劲度系数", "弹性势能", "形变量", "伸长量", "压缩量"),
    "pulley": ("滑轮", "定滑轮", "动滑轮", "绳端", "绳自由端", "绕过", "轻绳"),
    "field": ("电场", "电场线", "等势线", "等势面", "点电荷", "匀强电场", "电势"),
    "wave": ("波形", "简谐波", "波峰", "波谷", "横波", "纵波", "波长", "波速",
             "振动图像", "波动图像", "多普勒"),
    "gravity": ("卫星", "行星", "万有引力", "轨道", "宇宙速度", "地球同步"),
    "collision": ("碰撞", "弹性碰撞", "完全非弹性", "动量守恒", "动量定理"),
    "thermo": ("气缸", "活塞", "绝热", "等温", "等压", "理想气体", "热力学"),
}

# ===================== 通用正则 =====================

_PATH_COMMANDS = (
    "draw", "fill", "filldraw", "shade", "shadedraw", "path", "node",
    "coordinate", "clip", "matrix",
)
_PATH_CMD_RE = re.compile(r"\\(?:" + "|".join(_PATH_COMMANDS) + r")(?![A-Za-z@])")
_PATH_DRAW_COMMANDS = {"draw", "fill", "filldraw", "shade", "shadedraw", "path", "clip"}

_ARROW_RE = re.compile(
    r"(?:-latex|-Latex|-Stealth|-stealth|->>?|<-|>=?\s*(?:latex|Latex|stealth|Stealth)"
    # arrows.meta 带参箭头：-{Latex[length=2mm]} / -{Stealth[scale=1.2]}
    r"|-{\s*(?:Latex|Stealth|latex|stealth)(?![A-Za-z])"
    r"|\\arrow\s*\{|\\arrow\b)"
)
_MAGNETIC_RE = re.compile(r"(?:\\odot|\\otimes|\\bigodot|\\bigotimes|\\varotimes|\\varodot)")
_CIRCUIT_ENV_RE = re.compile(r"\\begin\{circuitikz\}|\\usepackage\s*\{?circuitikz")
_DOC_HEADER_RE = re.compile(r"\\(?:documentclass|usepackage)\b")
_FORBIDDEN_COMMANDS_RE = re.compile(r"\\(?:input|include|includeonly|bibliography)\b")
_LIBRARY_USE_RE = re.compile(r"\\usetikzlibrary\s*\{([^}]*)\}")
_ENV_RE = re.compile(r"\\(begin|end)\s*\{([A-Za-z*]+)\}")
_ARC_RE = re.compile(r"\barc\s*\(\s*(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)")
_NUM_LINE_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)\s*--\s*"
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
)
_CIRCLE_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)\s*circle\s*"
    r"(?:\[[^\]]*\])?\s*\(\s*(-?\d+(?:\.\d+)?)\s*(?:pt|cm|mm)?\s*\)"
)
_DOT_NODE_RE = re.compile(
    r"(?:\\fill(?:draw)?\s*(?:\[[^\]]*\])?\s*\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
    r"\s*circle\s*\([^)]*\))"
)
_COORD_PAIR_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)\s*--\s*"
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
)
_EXPECTED_ANGLE_RES = (
    re.compile(r"(?:倾角|角度|夹角)\s*(?:为|是|约|调(?:小|大)?至|改(?:为|成|至))?\s*(\d+(?:\.\d+)?)\s*(?:°|度|∘)"),
    re.compile(r"[θΘ]\s*[=＝]\s*(\d+(?:\.\d+)?)"),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:°|度|∘)"),
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_UNICODE_MATH_MAP = {
    "θ": r"\theta", "Θ": r"\Theta", "α": r"\alpha", "β": r"\beta", "γ": r"\gamma",
    "δ": r"\delta", "Δ": r"\Delta", "ε": r"\varepsilon", "λ": r"\lambda",
    "μ": r"\mu", "ω": r"\omega", "Ω": r"\Omega", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "φ": r"\varphi", "Φ": r"\Phi", "η": r"\eta",
    "°": r"^\circ", "×": r"\times", "÷": r"\div", "≈": r"\approx", "≠": r"\neq",
    "≤": r"\leq", "≥": r"\geq", "→": r"\rightarrow", "←": r"\leftarrow",
    "∞": r"\infty", "√": r"\sqrt", "⊥": r"\perp", "∥": r"\parallel",
    "∠": r"\angle", "⊙": r"\odot", "⊗": r"\otimes", "∑": r"\sum",
    "∫": r"\int", "±": r"\pm",
}
_UNICODE_MATH_RE = re.compile("[" + "".join(re.escape(ch) for ch in _UNICODE_MATH_MAP) + "]")
_MATH_TOKEN_RE = re.compile(
    r"(?:\^|_|\\circ\b|\\theta\b|\\alpha\b|\\beta\b|\\gamma\b|\\omega\b|\\mu\b|"
    r"\\Delta\b|\\lambda\b|\\vec\b|\\frac\b|\\sqrt\b|\\times\b|\\pm\b|\\geq?\b|\\leq?\b|"
    r"\\mathrm\b|\\mathbf\b|\\overrightarrow\b|\\text\b|\\sin\b|\\cos\b|\\tan\b)"
)

# TikZ 常用合法选项（用于悬空 style 检查的白名单）
_COMMON_TIKZ_KEYS = {
    "draw", "fill", "filldraw", "shade", "pattern", "pattern color", "path picture",
    "thick", "thin", "very thick", "very thin", "ultra thick", "ultra thin", "semithick",
    "dashed", "dotted", "densely dashed", "densely dotted", "loosely dashed", "dash pattern",
    "dash phase", "line width", "line cap", "line join", "miter limit", "rounded corners",
    "sharp corners", "solid", "opacity", "fill opacity", "draw opacity", "text opacity",
    "color", "text", "font", "align", "anchor", "inner sep", "outer sep", "minimum size",
    "minimum width", "minimum height", "shape", "aspect", "segment length", "amplitude",
    "mirror", "decorate", "decoration", "postaction", "preaction", "transform shape",
    "transform canvas", "rotate", "scale", "shift", "xshift", "yshift", "xscale", "yscale",
    "above", "below", "left", "right", "above left", "above right", "below left", "below right",
    "midway", "pos", "sloped", "allow upside down", "auto", "swap", "inner xsep", "inner ysep",
    "outer xsep", "outer ysep", "name path", "name intersections", "of", "by", "total",
    "out", "in", "looseness", "bend left", "bend right", "bend angle", "controls",
    "on grid", "node distance", "start angle", "end angle", "radius", "delta angle",
    "every node", "every path", "every picture", "every scope", "remember picture",
    "overlay", "execute at begin node", "execute at end node", "matrix", "column sep",
    "row sep", "nodes", "cell picture", "anchor", "label", "label distance", "pin",
    "pin distance", "pin edge", "circle", "ellipse", "rectangle", "coordinate",
    "circular glow", "circular drop shadow", "copy shadow", "drop shadow", "blur shadow",
    "fill opacity", "text height", "text depth", "text width", "inner sep", "outer sep",
    "line width", "step", "domain", "samples", "variable", "smooth", "tension", "mark",
    "mark size", "mark options", "only marks", "const plot", "ybar", "xbar", "bar width",
    "axis", "axis lines", "grid", "legend", "title", "xlabel", "ylabel", "ymin", "ymax",
    "xmin", "xmax", "ytick", "xtick", "enlarge x limits", "enlarge y limits", "compat",
    ">=latex", ">=stealth", ">={latex}", ">={Stealth}", "->", "<-", "<->",
    "line cap=round", "line join=round", "line cap=butt", "line join=miter",
}
_COMMON_TIKZ_KEYS_WORDS = {k.lower() for k in _COMMON_TIKZ_KEYS}


# ===================== 数据结构 =====================


@dataclass
class DiagramIssue:
    """单条校验问题"""

    level: str  # "error" | "warning" | "info"
    code: str
    message: str
    hint: str = ""
    category: str = "structure"  # structure|syntax|semantics|physics|geometry|readability|compile
    fixable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "category": self.category,
            "fixable": self.fixable,
        }

    def as_prompt_line(self) -> str:
        hint = f"（修复建议：{self.hint}）" if self.hint else ""
        return f"- [{self.level}] {self.message}{hint}"


# ===================== 基础扫描工具 =====================


def _skip_control_sequence(s: str, i: int) -> int:
    """从反斜杠处跳过一个 TeX 控制序列，返回其后位置。"""
    n = len(s)
    j = i + 1
    if j < n and s[j].isalpha():
        while j < n and (s[j].isalpha() or s[j] == "@"):
            j += 1
    else:
        j = min(n, j + 1)
    return j


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _mask_comments(code: str) -> str:
    """把 TeX 行注释替换为等长空格（保留换行与偏移），便于安全扫描。"""
    n = len(code)
    out = list(code)
    i = 0
    while i < n:
        ch = code[i]
        if ch == "\\":
            i = _skip_control_sequence(code, i)
            continue
        if ch == "%":
            while i < n and code[i] not in "\r\n":
                out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def _strip_comments(code: str) -> str:
    return re.sub(r"(?<!\\)%[^\n]*", "", code or "")


def _line_of(code: str, offset: int) -> int:
    try:
        return code.count("\n", 0, max(0, int(offset))) + 1
    except Exception:
        return 1


def _strip_fences(code: str) -> Tuple[str, List[str]]:
    fixes: List[str] = []
    cleaned = code or ""
    if "```" in cleaned:
        cleaned = re.sub(r"^\s*```(?:latex|tex|tikz)?\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.replace("```", "")
        fixes.append("移除了 Markdown 代码围栏")
    return cleaned.strip(), fixes


def _looks_like_tikz_body(code: str) -> bool:
    return bool(re.search(
        r"\\(?:draw|coordinate|node|fill|path|foreach|clip|shade|tikzset)\b", code or ""
    ))


def _parse_group(s: str, i: int, open_ch: str, close_ch: str) -> Optional[Tuple[int, int, int]]:
    """解析 s[i] == open_ch 的平衡分组，返回 (内容起点, 内容终点, 下一位置)。"""
    if i >= len(s) or s[i] != open_ch:
        return None
    depth = 0
    j = i
    n = len(s)
    while j < n:
        ch = s[j]
        if ch == "\\":
            j = _skip_control_sequence(s, j)
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return (i + 1, j, j + 1)
        j += 1
    return None


def _brace_balance(code: str) -> Tuple[int, int]:
    """返回 (最终深度, 首次负深度位置)；忽略转义花括号与注释。"""
    masked = _mask_comments(code)
    depth = 0
    first_negative = -1
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "\\":
            i = _skip_control_sequence(masked, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0 and first_negative < 0:
                first_negative = i
        i += 1
    return depth, first_negative


# ===================== 规范化与自动修复 =====================


def normalize_diagram(tikz: str) -> Tuple[str, List[str]]:
    """确定性修复：去围栏、去文档头、补 tikzpicture 包裹、补全环境、去首尾空白。"""
    code, fixes = _strip_fences(tikz or "")

    header_lines = []
    kept = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("\\documentclass") or stripped.startswith("\\usepackage"):
            header_lines.append(stripped)
            continue
        if stripped in ("\\begin{document}", "\\end{document}"):
            header_lines.append(stripped)
            continue
        kept.append(line)
    if header_lines:
        code = "\n".join(kept).strip()
        fixes.append(f"移除了 {len(header_lines)} 行不属于插图片段的文档头")

    if code and "\\begin{tikzpicture}" not in code and _looks_like_tikz_body(code):
        code = "\\begin{tikzpicture}[line cap=round, line join=round]\n" + code + "\n\\end{tikzpicture}"
        fixes.append("补齐了缺失的 tikzpicture 环境包裹")
    elif "\\begin{tikzpicture}" in code:
        begin_count = code.count("\\begin{tikzpicture}")
        end_count = code.count("\\end{tikzpicture}")
        if begin_count > end_count:
            code = code.rstrip() + "\n" + ("\\end{tikzpicture}\n" * (begin_count - end_count))
            fixes.append(f"补齐了 {begin_count - end_count} 个缺失的 \\end{{tikzpicture}}")

    return code.strip(), fixes


def _scan_statements(masked: str) -> List[Dict[str, Any]]:
    """扫描顶层 TikZ 路径语句，返回 [{command,start,end,terminated,content_start}]。"""
    stmts: List[Dict[str, Any]] = []
    i = 0
    n = len(masked)
    while i < n:
        if masked[i] != "\\":
            i += 1
            continue
        m = _PATH_CMD_RE.match(masked, i)
        if not m:
            i = _skip_control_sequence(masked, i)
            continue
        start = i
        command = m.group(0)[1:]
        i = m.end()
        depth = 0
        terminated = False
        end = n
        while i < n:
            ch = masked[i]
            if ch == "\\":
                if depth == 0 and masked.startswith("\\end{", i):
                    end = i
                    break
                if depth == 0 and _PATH_CMD_RE.match(masked, i):
                    end = i
                    break
                i = _skip_control_sequence(masked, i)
                continue
            if ch == "$":
                j = i + 1
                while j < n:
                    if masked[j] == "\\":
                        j = _skip_control_sequence(masked, j)
                        continue
                    if masked[j] == "$":
                        j += 1
                        break
                    j += 1
                i = j
                continue
            if ch in "{([":
                depth += 1
                i += 1
                continue
            if ch in "})]":
                if depth > 0:
                    depth -= 1
                i += 1
                continue
            if ch == ";" and depth == 0:
                terminated = True
                i += 1
                end = i
                break
            i += 1
        stmts.append({
            "command": command,
            "start": start,
            "end": end,
            "terminated": terminated,
            "content_start": m.end(),
        })
        i = max(i, end)
    return stmts


def _iter_node_texts(original: str, masked: Optional[str] = None) -> List[Dict[str, Any]]:
    """扫描所有 node 文本（支持嵌套花括号、选项、具名坐标与 at 坐标）。"""
    masked = _mask_comments(original) if masked is None else masked
    nodes: List[Dict[str, Any]] = []
    last_end = -1
    for m in re.finditer(r"(?<![A-Za-z@])node(?![A-Za-z@])", masked):
        if m.start() < last_end:
            continue
        j = _skip_ws(masked, m.end())
        n = len(masked)
        option_texts: List[str] = []
        anchor_text = ""
        # 任意顺序消费 [选项] / (具名坐标) / at (坐标)
        while j < n:
            j = _skip_ws(masked, j)
            if j < n and masked[j] == "[":
                g = _parse_group(masked, j, "[", "]")
                if not g:
                    break
                option_texts.append(masked[g[0]:g[1]])
                j = g[2]
                continue
            if j < n and masked.startswith("at", j) and (
                j + 2 >= n or not (masked[j + 2].isalpha() or masked[j + 2] == "@")
            ):
                j = _skip_ws(masked, j + 2)
                if j < n and masked[j] == "(":
                    g = _parse_group(masked, j, "(", ")")
                    if g:
                        anchor_text = masked[g[0]:g[1]].strip()
                        j = g[2]
                        continue
                break
            if j < n and masked[j] == "(":
                g = _parse_group(masked, j, "(", ")")
                if not g:
                    break
                j = g[2]
                continue
            break
        j = _skip_ws(masked, j)
        if j < n and masked[j] == "{":
            g = _parse_group(masked, j, "{", "}")
            if g:
                nodes.append({
                    "start": g[0],
                    "end": g[1],
                    "text": original[g[0]:g[1]],
                    "node_start": m.start(),
                    "options": ",".join(option_texts),
                    "anchor": anchor_text,
                })
                last_end = g[2]
            else:
                nodes.append({
                    "start": j + 1,
                    "end": n,
                    "text": original[j + 1:],
                    "node_start": m.start(),
                    "options": ",".join(option_texts),
                    "anchor": anchor_text,
                    "unbalanced": True,
                })
                last_end = n
    return nodes


def _span_contains(spans: List[Tuple[int, int]], pos: int) -> bool:
    for s, e in spans:
        if s <= pos < e:
            return True
        if s > pos:
            break
    return False


def _fix_fullwidth_syntax(code: str) -> Tuple[str, int]:
    """修正 TikZ 语法区域中的全角标点（node 文本内保留原样）。"""
    masked = _mask_comments(code)
    nodes = _iter_node_texts(code, masked)
    spans = sorted((n["start"], n["end"]) for n in nodes)
    table = {"，": ",", "；": ";", "（": "(", "）": ")"}
    out = list(code)
    count = 0
    for i, ch in enumerate(code):
        if ch in table and not _span_contains(spans, i):
            out[i] = table[ch]
            count += 1
    return "".join(out), count


def _insert_missing_semicolons(code: str) -> Tuple[str, int]:
    masked = _mask_comments(code)
    stmts = _scan_statements(masked)
    end_env = code.find("\\end{tikzpicture}")
    inserts: List[int] = []
    for st in stmts:
        if st["terminated"]:
            continue
        body = masked[st["content_start"]:st["end"]]
        if not body.strip():
            continue
        # 数学模式不配平时语句边界不可判定，强行补分号会产出畸形代码 → 跳过
        if len(re.findall(r"(?<!\\)\$", body)) % 2 != 0:
            continue
        pos = st["end"]
        # 绝不允许把分号补到 \end{tikzpicture} 之后
        if end_env >= 0:
            pos = min(pos, end_env)
        while pos > st["content_start"] and code[pos - 1].isspace():
            pos -= 1
        if pos <= st["content_start"]:
            continue
        if end_env >= 0 and pos > end_env:
            pos = end_env
        inserts.append(pos)
    for pos in sorted(set(inserts), reverse=True):
        code = code[:pos] + ";" + code[pos:]
    return code, len(set(inserts))


def _wrap_node_math(code: str) -> Tuple[str, int]:
    """把 node 标签里裸写的 ^ _ \\circ \\theta 等数学符号包进 $...$。"""
    masked = _mask_comments(code)
    nodes = _iter_node_texts(code, masked)
    repls: List[Tuple[int, int, str]] = []
    for node in nodes:
        if node.get("unbalanced"):
            continue
        text = node["text"]
        stripped = text.strip()
        if not stripped or "$" in stripped or "\\(" in stripped:
            continue
        if not _MATH_TOKEN_RE.search(stripped):
            continue
        leading = text[: len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()):]
        repls.append((node["start"], node["end"], f"{leading}${stripped}${trailing}"))
    for start, end, new in reversed(repls):
        code = code[:start] + new + code[end:]
    return code, len(repls)


def _fix_unicode_math_labels(code: str) -> Tuple[str, int]:
    """把 node 标签里的 Unicode 数学符号（θ、°、×、⊙…）转成 LaTeX 数学命令。"""
    masked = _mask_comments(code)
    nodes = _iter_node_texts(code, masked)
    repls: List[Tuple[int, int, str]] = []
    fixed = 0
    for node in nodes:
        if node.get("unbalanced"):
            continue
        text = node["text"]
        if not _UNICODE_MATH_RE.search(text):
            continue
        if "$" in text:
            # 已在数学模式内：直接替换符号为命令
            new_text = _UNICODE_MATH_RE.sub(lambda m: _UNICODE_MATH_MAP[m.group(0)], text)
        elif _CJK_RE.search(text):
            # 中文 + 数学符号混排：逐个符号内联成 $...$
            new_text = _UNICODE_MATH_RE.sub(
                lambda m: f"${_UNICODE_MATH_MAP[m.group(0)]}$", text
            )
        else:
            new_text = _UNICODE_MATH_RE.sub(lambda m: _UNICODE_MATH_MAP[m.group(0)], text)
            stripped = new_text.strip()
            leading = new_text[: len(new_text) - len(new_text.lstrip())]
            trailing = new_text[len(new_text.rstrip()):]
            new_text = f"{leading}${stripped}${trailing}"
        if new_text != text:
            repls.append((node["start"], node["end"], new_text))
            fixed += 1
    for start, end, new in reversed(repls):
        code = code[:start] + new + code[end:]
    return code, fixed


def autofix_diagram(tikz: str, question_text: str = "", user_instruction: str = "") -> Tuple[str, List[str]]:
    """确定性自动修复（不依赖大模型），返回 (修复后代码, 修复说明列表)。"""
    del question_text, user_instruction  # 保留签名，当前修复与题干无关
    code, fixes = normalize_diagram(tikz)
    try:
        code, n_full = _fix_fullwidth_syntax(code)
        if n_full:
            fixes.append(f"修正了 {n_full} 处 TikZ 语法区域内的全角标点")
        code, n_semi = _insert_missing_semicolons(code)
        if n_semi:
            fixes.append(f"为 {n_semi} 条路径语句补齐了遗漏的分号 ;")
        code, n_math = _wrap_node_math(code)
        if n_math:
            fixes.append(f"为 {n_math} 处节点标签补齐了 $...$ 数学模式")
        code, n_uni = _fix_unicode_math_labels(code)
        if n_uni:
            fixes.append(f"将 {n_uni} 处节点标签中的 Unicode 数学符号转为 LaTeX 命令")
    except Exception:  # 自动修复必须永不抛出
        pass
    return code.strip(), fixes


# ===================== 场景识别 =====================


def detect_context(question_text: str, user_instruction: str = "") -> List[str]:
    """从题干与用户指令中识别涉及的物理场景。"""
    haystack = f"{question_text or ''}\n{user_instruction or ''}"
    contexts = []
    for name, keywords in _CTX_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            contexts.append(name)
    return contexts


def _extract_expected_angle(question_text: str, user_instruction: str) -> Optional[float]:
    haystack = f"{user_instruction or ''}\n{question_text or ''}"
    for pattern in _EXPECTED_ANGLE_RES:
        for m in pattern.finditer(haystack):
            try:
                angle = float(m.group(1))
            except (TypeError, ValueError, IndexError):
                continue
            if 1.0 <= angle <= 89.0:
                return round(angle, 1)
    return None


# ===================== 坐标 / 线段 / 节点分析 =====================

_NUM_PAIR_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_POLAR_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)\s*$")
_NAME_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:\.[A-Za-z0-9 ]+)?$")


def _parse_coord_content(content: str, named: Dict[str, Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """解析一个坐标括号内容：数值 (x,y)、极坐标 (angle:r) 或具名坐标 (A)。"""
    content = (content or "").strip()
    if not content:
        return None
    m = _NUM_PAIR_RE.match(content)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = _POLAR_RE.match(content)
    if m:
        angle = math.radians(float(m.group(1)))
        radius = float(m.group(2))
        return (radius * math.cos(angle), radius * math.sin(angle))
    m = _NAME_RE.match(content)
    if m and m.group(1) in named:
        return named[m.group(1)]
    return None


def _parse_scope_options(options: str) -> Tuple[float, float, bool]:
    """解析 scope 选项里的 xshift/yshift/shift，返回 (dx, dy, 是否含 scale/rotate 类变换)。"""
    dx = dy = 0.0
    tricky = bool(re.search(r"\b(?:scale|rotate|transform canvas|xscale|yscale)\b", options or ""))
    for m in re.finditer(r"(xshift|yshift)\s*=\s*([-+]?\d+(?:\.\d+)?)\s*(?:cm|mm|pt|em|ex)?", options or ""):
        value = float(m.group(2))
        if m.group(1) == "xshift":
            dx += value
        else:
            dy += value
    for m in re.finditer(r"shift\s*=\s*\{\s*\(([^()]*)\)\s*\}", options or ""):
        pair = _NUM_PAIR_RE.match(m.group(1))
        if pair:
            dx += float(pair.group(1))
            dy += float(pair.group(2))
    return dx, dy, tricky


def _scope_regions(masked: str) -> List[Dict[str, Any]]:
    """扫描 scope 区域并累计其平移偏移（用于处理并排双图等布局）。"""
    regions: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    pattern = re.compile(r"\\(begin|end)\s*\{scope\}(?:\[([^\]]*)\])?")
    for m in pattern.finditer(masked):
        if m.group(1) == "begin":
            options = m.group(2) or ""
            dx, dy, tricky = _parse_scope_options(options)
            if stack:
                dx += stack[-1]["dx"]
                dy += stack[-1]["dy"]
                tricky = tricky or stack[-1]["tricky"]
            stack.append({"start": m.end(), "dx": dx, "dy": dy, "tricky": tricky})
        elif stack:
            item = stack.pop()
            item["end"] = m.start()
            regions.append(item)
    for item in stack:
        item["end"] = len(masked)
        regions.append(item)
    return regions


def _scope_offset_at(masked: str, pos: int) -> Tuple[float, float, bool]:
    """返回 pos 所在最内层 scope 的累计 (dx, dy, tricky)。"""
    best: Tuple[float, float, bool] = (0.0, 0.0, False)
    best_start = -1
    for region in _scope_regions(masked):
        if region["start"] <= pos < region["end"] and region["start"] >= best_start:
            best_start = region["start"]
            best = (region["dx"], region["dy"], region["tricky"])
    return best


def _collect_named_points(masked: str) -> Dict[str, Tuple[float, float]]:
    """收集由 \\coordinate / coordinate 定义的具名坐标数值位置。"""
    points: Dict[str, Tuple[float, float]] = {}
    patterns = (
        re.compile(
            r"\\coordinate\s*(?:\[[^\]]*\])?\s*\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)\s*at\s*"
            r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
        ),
        re.compile(
            r"(?<![A-Za-z@\\])coordinate\s*\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)\s*at\s*"
            r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
        ),
        re.compile(
            r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)\s*coordinate\s*"
            r"\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)"
        ),
    )
    for idx, pattern in enumerate(patterns):
        for m in pattern.finditer(masked):
            try:
                if idx == 2:
                    name, x, y = m.group(3), float(m.group(1)), float(m.group(2))
                else:
                    name = m.group(1)
                    x, y = float(m.group(2)), float(m.group(3))
            except (TypeError, ValueError):
                continue
            dx, dy, _tricky = _scope_offset_at(masked, m.start())
            points.setdefault(name, (x + dx, y + dy))
    return points


def _statement_options(masked: str, st: Dict[str, Any]) -> Tuple[str, int]:
    """返回 (选项内容, 选项后的位置)。"""
    j = _skip_ws(masked, st["content_start"])
    if j < len(masked) and masked[j] == "[":
        g = _parse_group(masked, j, "[", "]")
        if g:
            return masked[g[0]:g[1]], g[2]
    return "", j


def _statement_points(
    masked: str,
    st: Dict[str, Any],
    named: Dict[str, Tuple[float, float]],
) -> List[Tuple[str, Any]]:
    """提取语句中顶层坐标序列：('num',(x,y)) / ('name',name) / ('rel',None)。"""
    _, body_start = _statement_options(masked, st)
    end = st["end"]
    offset_x, offset_y, _tricky = _scope_offset_at(masked, st["start"])
    points: List[Tuple[str, Any]] = []
    i = body_start
    bracket_depth = 0
    brace_depth = 0
    while i < end:
        ch = masked[i]
        if ch == "\\":
            i = _skip_control_sequence(masked, i)
            continue
        if ch == "$":
            j = i + 1
            while j < end:
                if masked[j] == "\\":
                    j = _skip_control_sequence(masked, j)
                    continue
                if masked[j] == "$":
                    j += 1
                    break
                j += 1
            i = j
            continue
        if ch == "[":
            bracket_depth += 1
            i += 1
            continue
        if ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
            i += 1
            continue
        if ch == "{":
            brace_depth += 1
            i += 1
            continue
        if ch == "}":
            brace_depth = max(0, brace_depth - 1)
            i += 1
            continue
        if ch == "(" and bracket_depth == 0 and brace_depth == 0:
            g = _parse_group(masked, i, "(", ")")
            if not g:
                i += 1
                continue
            content = masked[g[0]:g[1]].strip()
            i = g[2]
            if content.startswith("+"):
                points.append(("rel", None))
                continue
            point = _parse_coord_content(content, named)
            if point is not None:
                points.append(("num", (point[0] + offset_x, point[1] + offset_y)))
                continue
            m = _NAME_RE.match(content)
            if m:
                points.append(("name", m.group(1)))
                continue
            points.append(("other", None))
            continue
        i += 1
    return points


def _arrow_style_names(masked: str) -> List[str]:
    """收集通过 \\tikzset/\\tikzstyle 定义的、包含箭头样式的自定义 style 名。"""
    names: List[str] = []
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9_-]*)\s*/\s*\.style\s*=\s*\{([^{}]*)\}", masked):
        if _ARROW_RE.search(m.group(2)) and m.group(1) not in names:
            names.append(m.group(1))
    for m in re.finditer(r"\\tikzstyle\s*\{\s*([A-Za-z][A-Za-z0-9_-]*)\s*\}\s*=\s*\[([^\]]*)\]", masked):
        if _ARROW_RE.search(m.group(2)) and m.group(1) not in names:
            names.append(m.group(1))
    return names


def _statement_has_arrow(masked: str, st: Dict[str, Any], arrow_styles: List[str]) -> bool:
    """判断一条路径语句是否真正绘制了箭头（排除 picture/\\tikzset 级默认样式）。"""
    options, _ = _statement_options(masked, st)
    if _ARROW_RE.search(options):
        return True
    for name in arrow_styles:
        if re.search(r"(?<![A-Za-z-])" + re.escape(name) + r"(?![A-Za-z-])", options):
            return True
    content = masked[st["start"]:st["end"]]
    return bool(re.search(r"\\arrow\s*\{", content))


def _collect_geometry(
    masked: str,
    stmts: List[Dict[str, Any]],
    named: Dict[str, Tuple[float, float]],
) -> Tuple[List[Dict[str, Any]], int, bool]:
    """收集线段与闭合路径计数。返回 (segments, closed_paths, has_relative)。"""
    segments: List[Dict[str, Any]] = []
    closed = 0
    has_relative = False
    arrow_styles = _arrow_style_names(masked)
    for st in stmts:
        if st["command"] not in _PATH_DRAW_COMMANDS:
            continue
        body = masked[st["content_start"]:st["end"]]
        # 矩形是元件符号本身，不代表导线回路，不能用来判断电路闭合
        if _RECT_RE.search(body):
            continue
        is_arrow = _statement_has_arrow(masked, st, arrow_styles)
        points = _statement_points(masked, st, named)
        if any(kind == "rel" for kind, _ in points):
            has_relative = True
        numeric = [(idx, val) for idx, (kind, val) in enumerate(points) if kind == "num"]
        for k in range(len(numeric) - 1):
            (i1, p1), (i2, p2) = numeric[k], numeric[k + 1]
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            segments.append({
                "p1": p1, "p2": p2, "length": length,
                "order": len(segments), "arrow": is_arrow,
            })
        # 闭合路径：cycle 关键字连接末点与首点
        content = masked[st["start"]:st["end"]]
        if re.search(r"(?<![A-Za-z])cycle(?![A-Za-z])", content) and len(numeric) >= 2:
            p1 = numeric[-1][1]
            p2 = numeric[0][1]
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            segments.append({
                "p1": p1, "p2": p2, "length": length,
                "order": len(segments), "arrow": is_arrow,
            })
            closed += 1
    return segments, closed, has_relative


_RECT_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)\s*rectangle\s*"
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
)


def _segment_angle_deg(p1: Tuple[float, float], p2: Tuple[float, float]) -> Optional[float]:
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    if abs(dx) < 1e-9:
        return 90.0
    return math.degrees(math.atan2(abs(dy), abs(dx)))


def _detect_incline(
    stmts: List[Dict[str, Any]],
    masked: str,
    named: Dict[str, Tuple[float, float]],
) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """检测图中最可能的斜面倾角。

    Harness v2.1：
    - 排除箭头线段（力/速度箭头常比斜边更长，会污染检测）；
    - 若图中存在角度弧，优先选取与弧角最接近的斜线（误差 ≤ 8°），否则取最长斜线。
    """
    segments, _closed, _rel = _collect_geometry(masked, stmts, named)
    arcs = _detect_arc_angles(masked)
    candidates: List[Dict[str, Any]] = []
    for seg in segments:
        if seg.get("arrow"):
            continue
        if seg["length"] < 0.8:
            continue
        angle = _segment_angle_deg(seg["p1"], seg["p2"])
        if angle is None or not (5.0 <= angle <= 85.0):
            continue
        candidates.append({**seg, "angle": angle})
    if not candidates:
        return None, None
    for target in sorted(set(arcs)):
        close = [c for c in candidates if abs(c["angle"] - target) <= 8.0]
        if close:
            best = max(close, key=lambda c: c["length"])
            return round(best["angle"], 1), best
    best = max(candidates, key=lambda c: c["length"])
    return round(best["angle"], 1), best


def _detect_arc_angles(masked: str) -> List[float]:
    angles: List[float] = []
    for m in _ARC_RE.finditer(masked):
        try:
            start, end = float(m.group(1)), float(m.group(2))
        except ValueError:
            continue
        delta = abs(end - start) % 360.0
        if delta > 180.0:
            delta = 360.0 - delta
        if 3.0 <= delta <= 90.0:
            angles.append(round(delta, 1))
    return angles


def _node_positions(
    original: str,
    masked: str,
    named: Optional[Dict[str, Tuple[float, float]]] = None,
) -> List[Dict[str, Any]]:
    """只返回具有显式 at 锚点（绝对定位）的 node 位置，避免误判附着标签。"""
    named = named or _collect_named_points(masked)
    positions: List[Dict[str, Any]] = []
    for node in _iter_node_texts(original, masked):
        if node.get("unbalanced"):
            continue
        text = node["text"].strip()
        if not text:
            continue
        anchor = node.get("anchor") or ""
        pos = _parse_coord_content(anchor, named) if anchor else None
        tricky = False
        if pos is not None:
            dx, dy, tricky = _scope_offset_at(masked, node["node_start"])
            pos = (pos[0] + dx, pos[1] + dy)
        positions.append({
            "text": text, "pos": pos, "options": node.get("options") or "", "tricky": tricky,
        })
    return positions


def _count_arrows(masked: str, stmts: List[Dict[str, Any]]) -> Tuple[int, float]:
    """返回 (箭头语句数, 力箭头最大长度)。

    只统计**真正绘制箭头**的路径语句：picture 级 `[>=Stealth]` 只是默认样式，
    不能据此认为图中已有箭头（否则会漏报 force-no-arrow / optics-no-arrow）。
    """
    count = 0
    max_len = 0.0
    named = _collect_named_points(masked)
    arrow_styles = _arrow_style_names(masked)
    for st in stmts:
        if not _statement_has_arrow(masked, st, arrow_styles):
            continue
        count += 1
        points = [v for kind, v in _statement_points(masked, st, named) if kind == "num"]
        for k in range(len(points) - 1):
            length = math.hypot(
                points[k + 1][0] - points[k][0], points[k + 1][1] - points[k][1]
            )
            max_len = max(max_len, length)
    return count, max_len


def _has_zigzag_spring(masked: str, stmts: List[Dict[str, Any]]) -> bool:
    """识别「规则折线」画法的弹簧（多段短线段、上下/左右交替拐折）。"""
    named = _collect_named_points(masked)
    for st in stmts:
        if st["command"] not in _PATH_DRAW_COMMANDS:
            continue
        points = [v for kind, v in _statement_points(masked, st, named) if kind == "num"]
        if len(points) < 5:
            continue
        lengths = [
            math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
            for i in range(len(points) - 1)
        ]
        if not lengths or max(lengths) > 1.6 or min(lengths) < 0.04:
            continue
        deltas = [points[i + 1][1] - points[i][1] for i in range(len(points) - 1)]
        signs = [1 if d > 1e-6 else (-1 if d < -1e-6 else 0) for d in deltas]
        alternations = sum(1 for a, b in zip(signs, signs[1:]) if a * b < 0)
        if alternations >= 3:
            return True
    return False


def _has_manual_magnetic_symbol(
    masked: str,
    circles: List[Tuple[float, float, float]],
    segments: List[Dict[str, Any]],
) -> bool:
    """识别自绘的 ⊗（圆 + 内部交叉线）或 ⊙（圆 + 实心点）。"""
    dots: List[Tuple[float, float]] = []
    for m in _DOT_NODE_RE.finditer(masked):
        try:
            dots.append((float(m.group(1)), float(m.group(2))))
        except (TypeError, ValueError):
            continue
    for cx, cy, radius in circles:
        if radius <= 0:
            continue
        inner = []
        for seg in segments:
            if math.hypot(seg["p1"][0] - cx, seg["p1"][1] - cy) > radius * 1.08:
                continue
            if math.hypot(seg["p2"][0] - cx, seg["p2"][1] - cy) > radius * 1.08:
                continue
            if radius * 0.8 <= seg["length"] <= radius * 2.4:
                inner.append(seg)
        # 用有符号方向角（mod 180）区分交叉两笔的斜率（45° vs 135°）
        angles = set()
        for s in inner:
            dx = s["p2"][0] - s["p1"][0]
            dy = s["p2"][1] - s["p1"][1]
            angles.add(round(math.degrees(math.atan2(dy, dx)) % 180.0, 0))
        if len(inner) >= 2 and len(angles) >= 2:
            return True
        if any(
            math.hypot(dot[0] - cx, dot[1] - cy) <= max(0.06, radius * 0.3)
            for dot in dots
        ):
            return True
    return False


def _has_normal_marker(masked: str, arc_angles: List[float]) -> bool:
    """判断光路图是否已画法线/角度标记（虚线、细实线、角度弧或角度标注均算）。"""
    if "dashed" in masked or arc_angles:
        return True
    if re.search(r"(?<![A-Za-z])(?:法线|normal|入射角|反射角|折射角)(?![A-Za-z])", masked):
        return True
    if re.search(r"\$(?:i|r|\\alpha|\\beta|\\theta|\\gamma|n)\$", masked):
        return True
    if re.search(r"(?<![A-Za-z])thin(?![A-Za-z])", masked):
        return True
    return False


def _find_force_labels(original: str, masked: str) -> List[str]:
    labels = []
    for node in _iter_node_texts(original, masked):
        text = (node.get("text") or "").strip()
        if not text:
            continue
        if re.search(
            r"(?:\\vec\b|\\overrightarrow\b|重力|支持力|摩擦力|拉力|推力|"
            r"浮力|弹力|安培力|洛伦兹力|电场力|合力|分力|"
            r"(?<![A-Za-z\\])(?:F|f|N|G|T|mg)(?![A-Za-z]))",
            text,
        ):
            labels.append(text)
    return labels


# ===================== 各类检查 =====================


def _check_structure(code: str) -> List[DiagramIssue]:
    issues: List[DiagramIssue] = []
    masked = _mask_comments(code)

    if not masked.strip():
        issues.append(DiagramIssue("error", "empty", "插图代码为空", "请输出完整的 tikzpicture 代码"))
        return issues

    begin_count = len(re.findall(r"\\begin\{tikzpicture\}", masked))
    end_count = len(re.findall(r"\\end\{tikzpicture\}", masked))
    if begin_count == 0:
        issues.append(DiagramIssue("error", "missing-env", "缺少 \\begin{tikzpicture}", "补全 tikzpicture 环境后再提交"))
    elif begin_count != end_count:
        issues.append(DiagramIssue("error", "unbalanced-env", "tikzpicture 环境未闭合", "补齐 \\end{tikzpicture}"))
    if begin_count > 1:
        issues.append(DiagramIssue("warning", "multiple-env", f"检测到 {begin_count} 个 tikzpicture 环境",
                                   "一次只提交一个 tikzpicture 环境"))

    stack: List[str] = []
    for m in _ENV_RE.finditer(masked):
        kind, name = m.group(1), m.group(2)
        if name == "tikzpicture":
            continue
        if kind == "begin":
            stack.append(name)
        elif stack and stack[-1] == name:
            stack.pop()
        elif name in stack:
            stack.remove(name)
            issues.append(DiagramIssue(
                "error", "env-balance", f"环境 {name} 嵌套顺序错误",
                f"检查 \\begin/{{\\end}} 的配对顺序", category="structure",
            ))
    if stack:
        issues.append(DiagramIssue(
            "error", "env-balance", f"环境 {stack[-1]} 未闭合",
            f"补全 \\end{{{stack[-1]}}}", category="structure",
        ))

    depth, first_negative = _brace_balance(code)
    if depth != 0 or first_negative >= 0:
        line = _line_of(code, first_negative if first_negative >= 0 else max(0, len(code) - 1))
        issues.append(DiagramIssue(
            "error", "brace", f"花括号不配平（约第 {line} 行，最终深度 {depth}）",
            "检查 { 与 } 的配对（注意 \\{、\\} 转义）", category="structure",
        ))

    dollars = len(re.findall(r"(?<!\\)\$", masked))
    if dollars % 2 != 0:
        issues.append(DiagramIssue("error", "math-balance", "数学模式 $ 数量为奇数", "补全或删除未闭合的 $...$"))

    if begin_count >= 1 and len(masked.strip()) < 40:
        issues.append(DiagramIssue("error", "empty-picture", "tikzpicture 内容几乎为空", "请绘制完整图形"))

    if "```" in code:
        issues.append(DiagramIssue("warning", "fence", "代码中仍残留 Markdown 围栏", "只输出纯 TikZ 代码"))

    return issues


def _check_syntax(code: str) -> List[DiagramIssue]:
    issues: List[DiagramIssue] = []
    masked = _mask_comments(code)

    if _CIRCUIT_ENV_RE.search(masked):
        issues.append(DiagramIssue(
            "error", "unsupported-env", "使用了模板未加载的 circuitikz 环境",
            "改用 tikz 基本图元（lines/rectangle/circle）绘制电路元件", category="syntax",
        ))

    for m in _LIBRARY_USE_RE.finditer(masked):
        for lib in (x.strip() for x in m.group(1).split(",")):
            if lib and lib not in TEMPLATE_LOADED_LIBRARIES:
                issues.append(DiagramIssue(
                    "warning", "unknown-library", f"片段内引用了模板未预载的库 {lib}",
                    "改用已加载库的等价图元（模板已含 calc/arrows.meta/patterns 等）",
                    category="syntax",
                ))

    for m in _FORBIDDEN_COMMANDS_RE.finditer(masked):
        issues.append(DiagramIssue(
            "error", "forbidden-command", f"禁止在插图片段中使用 {m.group(0)}",
            "仅输出 tikzpicture 环境，不要引入外部文件", category="syntax",
        ))

    if _DOC_HEADER_RE.search(masked):
        issues.append(DiagramIssue(
            "error", "header-leak", "插图片段中仍残留文档头命令",
            "移除 \\documentclass / \\usepackage，只保留 tikzpicture", category="syntax",
        ))

    tokens_lower = masked.lower()
    for command, hint in _MISSING_PACKAGE_COMMANDS.items():
        # 这些宏包命令必然带参数（{ 或 [），据此排除 \foreach 变量等误报
        if re.search(re.escape(command.lower()) + r"\s*[{\[]", tokens_lower):
            issues.append(DiagramIssue(
                "error", "missing-package-command", f"使用了模板未加载的命令 {command}（{hint}）",
                f"删除 {command} 或改用模板已支持的标准命令", category="syntax",
            ))

    # 路径语句缺少分号
    stmts = _scan_statements(masked)
    missing = [st for st in stmts if not st["terminated"] and masked[st["content_start"]:st["end"]].strip()]
    for st in missing[:3]:
        line = _line_of(code, st["start"])
        issues.append(DiagramIssue(
            "error", "statement-end", f"第 {line} 行的 \\{st['command']} 语句缺少结尾分号 ;",
            "每条 \\draw/\\fill/\\path/\\node/\\coordinate 语句都必须以 ; 结束",
            category="syntax", fixable=True,
        ))
    if len(missing) > 3:
        issues.append(DiagramIssue(
            "error", "statement-end", f"另有 {len(missing) - 3} 条路径语句缺少结尾分号 ;",
            "逐条检查路径命令是否以 ; 结束", category="syntax", fixable=True,
        ))

    # node 文本花括号不闭合
    for node in _iter_node_texts(code, masked):
        if node.get("unbalanced"):
            line = _line_of(code, node["node_start"])
            issues.append(DiagramIssue(
                "error", "node-brace", f"第 {line} 行 node 文本的花括号未闭合",
                "检查 node {...} 的 { 与 } 配对", category="syntax",
            ))

    # 悬空自定义 style（仅在代码里出现过 \\tikzset/\\tikzstyle 时检查，避免误报）
    if re.search(r"\\(?:tikzset|tikzstyle)\b", masked):
        defined = set(TEMPLATE_DEFINED_STYLES)
        for m in re.finditer(r"\\(?:tikzset|tikzstyle)\s*\{([^}]*)\}", masked):
            for style in re.findall(r"([A-Za-z][A-Za-z0-9_-]*)\s*(?:/\s*\.style|/\s*\.append style|/\s*\.code)", m.group(1)):
                defined.add(style)
            for style in re.findall(r"\\tikzstyle\s*\{\s*([A-Za-z][A-Za-z0-9_-]*)\s*\}", m.group(0)):
                defined.add(style)
        used_custom: set = set()
        for st in stmts:
            options, _ = _statement_options(masked, st)
            for word in re.findall(r"(?<![A-Za-z0-9_\\/-])([A-Za-z][A-Za-z0-9_-]{2,})(?![A-Za-z0-9_\\/-])", options):
                if word in defined or word.lower() in _COMMON_TIKZ_KEYS_WORDS:
                    continue
                if any(word in key for key in _COMMON_TIKZ_KEYS_WORDS):
                    continue
                used_custom.add(word)
        for word in sorted(used_custom)[:2]:
            issues.append(DiagramIssue(
                "info", "dangling-style", f"选项 {word} 疑似自定义 style 但未见定义",
                "确认 style 已在 \\tikzset 中定义，或改用标准 TikZ 选项",
                category="syntax",
            ))

    # 引用未定义具名坐标（存在 \\foreach/\\def 等动态命名时跳过，避免误报）
    if not re.search(r"\\(?:foreach|def|newcommand|renewcommand|pgfmathsetmacro|let)\b|#[1-9]", masked):
        defined_names: Dict[str, int] = {}
        for pattern in (
            re.compile(r"\\coordinate\s*(?:\[[^\]]*\])?\s*\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)"),
            re.compile(r"\\node\s*(?:\[[^\]]*\])?\s*\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)"),
            re.compile(r"(?<![A-Za-z@\\])coordinate\s*\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)"),
            re.compile(r"(?<![A-Za-z@\\])node\s*(?:\[[^\]]*\])?\s*\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)"),
        ):
            for m in pattern.finditer(masked):
                defined_names.setdefault(m.group(1), m.start())
        whitelist = {"current page", "current bounding box", "pic"}
        refs = re.findall(
            r"\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:\.[A-Za-z0-9 ]+)?\s*\)", masked
        )
        undefined: List[str] = []
        for name in refs:
            if name in defined_names or name in whitelist:
                continue
            if name not in undefined:
                undefined.append(name)
        if undefined:
            shown = "、".join(f"({x})" for x in undefined[:3])
            issues.append(DiagramIssue(
                "warning", "undefined-coordinate", f"图中引用了未定义的具名坐标 {shown}",
                "先用 \\coordinate (A) at (x,y); 定义，再在连线中引用",
                category="syntax",
            ))

    return issues


def _check_semantics(code: str) -> List[DiagramIssue]:
    issues: List[DiagramIssue] = []
    masked = _mask_comments(code)
    for node in _iter_node_texts(code, masked):
        if node.get("unbalanced"):
            continue
        text = node["text"]
        if not text.strip():
            continue
        dollars = len(re.findall(r"(?<!\\)\$", text))
        if dollars % 2 != 0:
            issues.append(DiagramIssue(
                "error", "node-unbalanced-math", f"节点标签 $ 不配对：{text.strip()[:40]}",
                "补全或删除 node 文本中未闭合的 $...$",
                category="semantics", fixable=True,
            ))
            continue
        if dollars == 0 and "\\(" not in text and _MATH_TOKEN_RE.search(text):
            issues.append(DiagramIssue(
                "error", "node-math", f"节点标签中的数学符号未包裹 $...$：{text.strip()[:40]}",
                "写成 node {$F_N$} / node {$30^\\circ$}",
                category="semantics", fixable=True,
            ))
        elif dollars == 0 and _UNICODE_MATH_RE.search(text) and not _CJK_RE.search(text):
            issues.append(DiagramIssue(
                "warning", "unicode-math", f"节点标签含 Unicode 数学符号：{text.strip()[:40]}",
                "改用 LaTeX 命令，如 $\\theta$、$30^\\circ$、$\\times$",
                category="semantics", fixable=True,
            ))
        for m in re.finditer(r"\$([^$]*)\$", text):
            inner = re.sub(r"\\text\s*\{[^{}]*\}", "", m.group(1))
            if _CJK_RE.search(inner):
                issues.append(DiagramIssue(
                    "warning", "cjk-math", f"数学模式内直接出现中文：{inner.strip()[:30]}",
                    "中文请用 \\text{中文} 包裹", category="semantics",
                ))
                break
    return issues


def _check_physics(code: str, question_text: str, user_instruction: str) -> Tuple[List[DiagramIssue], Dict[str, Any]]:
    issues: List[DiagramIssue] = []
    masked = _mask_comments(code)
    haystack = f"{question_text or ''}\n{user_instruction or ''}"
    contexts = detect_context(question_text, user_instruction)
    stmts = _scan_statements(masked)
    named = _collect_named_points(masked)
    segments, closed_paths, has_relative = _collect_geometry(masked, stmts, named)
    arrow_count, max_arrow_len = _count_arrows(masked, stmts)
    force_labels = _find_force_labels(code, masked)
    has_arrow = arrow_count > 0
    detected_angle, incline_seg = _detect_incline(stmts, masked, named)
    arc_angles = _detect_arc_angles(masked)
    expected_angle = _extract_expected_angle(question_text, user_instruction)
    angle_delta = (
        round(abs(detected_angle - expected_angle), 1)
        if detected_angle is not None and expected_angle is not None
        else None
    )
    labels = [
        n["text"].strip()
        for n in _iter_node_texts(code, masked)
        if n.get("text", "").strip()
    ]
    magnetic_symbols = len(_MAGNETIC_RE.findall(masked))

    if "force" in contexts:
        if not has_arrow:
            issues.append(DiagramIssue(
                "error", "force-no-arrow", "题干涉及受力分析，但图中没有任何力的箭头",
                "至少为重力/支持力/摩擦力/拉力画出带箭头的向量，例如 \\draw[thick,-latex] (A) -- (B);",
                category="physics",
            ))
        elif not force_labels:
            issues.append(DiagramIssue(
                "warning", "force-no-label", "受力图有箭头但没有力符号标注",
                "给每个力加 node 标注，如 node[right] {$F_N$}、node[below] {$mg$}",
                category="physics",
            ))
        if has_arrow and 0 < max_arrow_len < 0.5:
            issues.append(DiagramIssue(
                "warning", "force-arrow-short", f"力箭头过短（最长约 {max_arrow_len:.2f} 个坐标单位）",
                "箭头长度建议 ≥ 0.8 个坐标单位，便于阅读",
                category="physics",
            ))

    if "incline" in contexts:
        if incline_seg is None and not arc_angles:
            issues.append(DiagramIssue(
                "warning", "incline-missing", "题干涉及斜面/倾角，但图中未见倾斜线段或角度弧",
                "用非水平线段表示斜面，并用 arc 标出倾角 θ", category="physics",
            ))
        elif incline_seg is not None and not arc_angles and not any(
            re.search(r"(\d+\s*(?:°|\\circ)|\\theta|\\alpha)", label) for label in labels
        ):
            issues.append(DiagramIssue(
                "warning", "incline-no-angle", "斜面已画出，但没有角度弧或角度标注",
                "用 \\draw (P) arc (0:θ:r); 标出倾角，并用 node 标注 $\\theta$ 或具体度数",
                category="physics",
            ))
        if angle_delta is not None and angle_delta > 8.0:
            issues.append(DiagramIssue(
                "warning", "incline-angle-mismatch",
                f"图中斜线倾角约 {detected_angle}°，与题干/指令要求的 {expected_angle}° 偏差 {angle_delta}°",
                f"按几何关系重画斜面，使其与水平方向夹角约为 {expected_angle}°",
                category="physics",
            ))

    if "magnetic" in contexts:
        # 注意：数学乘号 ×（如 2×10^6）不能作为「垂直纸面」判据
        vertical_query = (
            "垂直纸面" in haystack
            or any(sym in haystack for sym in ("⊙", "⊗"))
            or ("磁" in haystack and any(k in haystack for k in ("向里", "向外")))
        )
        circles = _collect_circles(masked, named)
        manual_symbol = _has_manual_magnetic_symbol(masked, circles, segments)
        if vertical_query and magnetic_symbols == 0 and not manual_symbol:
            issues.append(DiagramIssue(
                "warning", "magnetic-symbol", "题干涉及垂直纸面的磁场，但图中缺少 ⊙/⊗ 符号",
                "用 \\odot 表示垂直纸面向外，\\otimes 表示向里（也可用圆 + 叉 / 圆 + 实心点自绘），并配箭头说明方向",
                category="physics",
            ))
        if manual_symbol:
            magnetic_symbols = max(magnetic_symbols, 1)

    if "optics" in contexts:
        if not has_arrow:
            issues.append(DiagramIssue(
                "error", "optics-no-arrow", "光路图缺少光线传播方向箭头",
                "在入射/反射/折射光线上加 -latex 箭头（或用 decorate markings）",
                category="physics",
            ))
        elif not _has_normal_marker(masked, arc_angles):
            issues.append(DiagramIssue(
                "warning", "optics-no-normal", "光路图未画法线或角度标记",
                "用法线（虚线或细实线）标出，并用角度弧/标注标注入射角、反射角或折射角",
                category="physics",
            ))

    if "circuit" in contexts:
        if not re.search(r"rectangle|circle|circuitikz", masked):
            issues.append(DiagramIssue(
                "warning", "circuit-shape", "电路图未见矩形/圆形元件，可能只是导线示意",
                "用 rectangle 表示电阻/电源，circle 表示电表，交叉连接处加实心点",
                category="physics",
            ))
        if len(segments) >= 4 and not has_relative and not re.search(r"rectangle|circle", masked):
            if _count_cycles(segments) == 0:
                issues.append(DiagramIssue(
                    "warning", "circuit-open", "电路导线未形成闭合回路，可能缺少连接线段",
                    "检查电源、开关、电阻是否串联成闭合回路", category="physics",
                ))
        if len(segments) >= 2 and not has_relative:
            junctions = _count_undotted_junctions(segments, masked)
            if junctions:
                # 「交叉不相连」是合法画法；仅在题干提示需要连接/结点时才升级为 warning
                not_connected_hint = any(
                    k in haystack for k in ("不相连", "不连接", "不相接", "交叉不相连", "桥式")
                )
                level = "info" if not_connected_hint else "warning"
                message = (
                    f"约 {junctions} 处导线交叉缺少实心结点（若为交叉不相连则无需补点）"
                    if level == "info" else
                    f"约 {junctions} 处导线交叉/连接缺少实心结点"
                )
                issues.append(DiagramIssue(
                    level, "circuit-junction", message,
                    "电气上相连处加 \\fill (x,y) circle (1.5pt); 交叉不相连则无需加点",
                    category="physics",
                ))

    if "spring" in contexts:
        has_spring = bool(re.search(r"coil|decoration|zigzag|snake|saw", masked)) or _has_zigzag_spring(masked, stmts)
        if not has_spring:
            issues.append(DiagramIssue(
                "warning", "spring-symbol", "题干涉及弹簧，但图中未见弹簧符号",
                "用 decorations.pathmorphing 的 coil 装饰，或规则折线绘制弹簧", category="physics",
            ))

    if "pulley" in contexts:
        circles = _collect_circles(masked, named)
        if not circles:
            issues.append(DiagramIssue(
                "warning", "pulley-no-circle", "题干涉及滑轮，但图中未见圆形滑轮",
                "用 circle 画滑轮，并画出与滑轮相切的绳段", category="physics",
            ))
        elif segments and not _has_tangent(circles, segments):
            issues.append(DiagramIssue(
                "warning", "pulley-no-tangent", "滑轮附近未见与圆相切的绳段",
                "轻绳应与滑轮边缘相切，画线时端点落在圆周切点附近", category="physics",
            ))

    if "field" in contexts:
        if not has_arrow:
            issues.append(DiagramIssue(
                "warning", "field-no-arrow", "电场图缺少表示电场方向的箭头",
                "在电场线上加 -latex 箭头，方向由正电荷受力方向决定", category="physics",
            ))
        if ("等势" in haystack) and "dashed" not in masked:
            issues.append(DiagramIssue(
                "warning", "field-no-equipotential", "题干涉及等势线，但图中未见虚线等势线",
                "用 dashed 绘制等势线，并与电场线垂直", category="physics",
            ))

    if "wave" in contexts and not re.search(r"(?:sin|cos|plot|declare function|smooth)", masked):
        issues.append(DiagramIssue(
            "warning", "wave-symbol", "波形图未见正弦/余弦曲线或 plot 绘图",
            "用 \\draw plot[domain=...,samples=...] (\\x, {sin(\\x r)}); 绘制波形",
            category="physics",
        ))

    if "gravity" in contexts and not re.search(r"circle|ellipse|shade", masked):
        issues.append(DiagramIssue(
            "info", "orbit-symbol", "万有引力/轨道题未见圆形天体或轨道",
            "用 circle 画天体，dashed 画轨道", category="physics",
        ))

    if "collision" in contexts and not has_arrow:
        issues.append(DiagramIssue(
            "info", "collision-no-velocity", "碰撞题建议用箭头标出碰前/碰后速度方向",
            "用 -latex 箭头表示速度矢量", category="physics",
        ))

    if "thermo" in contexts and not re.search(r"rectangle|cylinder|plot", masked):
        issues.append(DiagramIssue(
            "info", "thermo-symbol", "热学题未见气缸/活塞等示意图形",
            "用 rectangle 画气缸，粗线段画活塞", category="physics",
        ))

    metrics = {
        "incline_angle_deg": detected_angle,
        "expected_angle_deg": expected_angle,
        "angle_delta_deg": angle_delta,
        "arrow_count": arrow_count,
        "max_arrow_len": round(max_arrow_len, 2),
        "magnetic_symbols": magnetic_symbols,
        "closed_paths": _count_cycles(segments) if segments and not has_relative else closed_paths,
        "force_labels": len(force_labels),
    }
    return issues, metrics


def _count_cycles(segments: List[Dict[str, Any]]) -> int:
    """用并查集粗略判断线段图是否存在环（电路闭合回路）。"""
    parent: Dict[Tuple[int, int], Tuple[int, int]] = {}

    def key(point: Tuple[float, float]) -> Tuple[int, int]:
        return (int(round(point[0] * 20)), int(round(point[1] * 20)))

    def find(x: Tuple[int, int]) -> Tuple[int, int]:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cycles = 0
    for seg in segments:
        a, b = key(seg["p1"]), key(seg["p2"])
        if a == b:
            continue
        ra, rb = find(a), find(b)
        if ra == rb:
            cycles += 1
        else:
            parent[ra] = rb
    return cycles


def _count_undotted_junctions(segments: List[Dict[str, Any]], masked: str) -> int:
    """统计导线内部交叉但缺少实心结点的情况（最多报告 3 处）。"""
    dots: List[Tuple[float, float]] = []
    for m in _DOT_NODE_RE.finditer(masked):
        try:
            dots.append((float(m.group(1)), float(m.group(2))))
        except (TypeError, ValueError):
            continue
    for m in re.finditer(
        r"(?:\\fill(?:draw)?|node)\s*\[?[^\]]*(?:dot|circle[^\]]*fill)[^\]]*\]?\s*"
        r"(?:at\s*)?\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)",
        masked,
    ):
        try:
            dots.append((float(m.group(1)), float(m.group(2))))
        except (TypeError, ValueError):
            continue
    if len(segments) > 120:
        return 0
    count = 0
    limit = len(segments)
    for i in range(limit):
        s1 = segments[i]
        for j in range(i + 1, limit):
            s2 = segments[j]
            if _share_endpoint(s1, s2):
                continue
            point = _segment_intersection(s1, s2)
            if point is None:
                continue
            if _dist2(point, s1["p1"]) < 0.01 or _dist2(point, s1["p2"]) < 0.01:
                continue
            if _dist2(point, s2["p1"]) < 0.01 or _dist2(point, s2["p2"]) < 0.01:
                continue
            if any(_dist2(point, d) < 0.04 for d in dots):
                continue
            count += 1
            if count >= 3:
                return count
    return count


def _share_endpoint(s1: Dict[str, Any], s2: Dict[str, Any]) -> bool:
    for p in (s1["p1"], s1["p2"]):
        for q in (s2["p1"], s2["p2"]):
            if _dist2(p, q) < 1e-6:
                return True
    return False


def _dist2(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _segment_intersection(s1: Dict[str, Any], s2: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    x1, y1 = s1["p1"]
    x2, y2 = s1["p2"]
    x3, y3 = s2["p1"]
    x4, y4 = s2["p2"]
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
    if 0.02 < t < 0.98 and 0.02 < u < 0.98:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def _collect_circles(
    masked: str,
    named: Dict[str, Tuple[float, float]],
) -> List[Tuple[float, float, float]]:
    """收集圆（支持数值圆心与具名坐标圆心）。"""
    circles: List[Tuple[float, float, float]] = []
    for m in _CIRCLE_RE.finditer(masked):
        try:
            circles.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
        except (TypeError, ValueError):
            continue
    for m in _CIRCLE_NAMED_RE.finditer(masked):
        name = m.group(1)
        try:
            radius = float(m.group(2))
        except (TypeError, ValueError):
            continue
        if name in named:
            circles.append((named[name][0], named[name][1], radius))
    return circles


_CIRCLE_NAMED_RE = re.compile(
    r"\(\s*([A-Za-z][A-Za-z0-9_-]*)\s*\)\s*circle\s*(?:\[[^\]]*\])?\s*"
    r"\(\s*(-?\d+(?:\.\d+)?)\s*(?:pt|cm|mm)?\s*\)"
)


def _has_tangent(circles: List[Tuple[float, float, float]], segments: List[Dict[str, Any]]) -> bool:
    for cx, cy, r in circles:
        for seg in segments:
            if _point_segment_distance((cx, cy), seg["p1"], seg["p2"]) - r < 0.12:
                return True
    return False


def _point_segment_distance(point: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    px, py = point
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _check_geometry(
    code: str,
    question_text: str,
    user_instruction: str,
    physics_metrics: Dict[str, Any],
) -> List[DiagramIssue]:
    issues: List[DiagramIssue] = []
    masked = _mask_comments(code)
    stmts = _scan_statements(masked)
    named = _collect_named_points(masked)
    segments, _closed, _rel = _collect_geometry(masked, stmts, named)

    for seg in segments:
        if 1e-9 < seg["length"] < 0.02:
            issues.append(DiagramIssue(
                "warning", "degenerate-segment", "存在两个端点几乎重合的线段，渲染后可能不可见",
                "检查坐标是否写错，或删除退化线段", category="geometry",
            ))
            break

    arc_angles = _detect_arc_angles(masked)
    detected = physics_metrics.get("incline_angle_deg")
    contexts = detect_context(question_text, user_instruction)
    if "incline" in contexts and arc_angles and detected is not None:
        best_delta = min(abs(a - detected) for a in arc_angles)
        if best_delta > 8.0:
            issues.append(DiagramIssue(
                "warning", "angle-arc-mismatch",
                f"角度弧标注约 {min(arc_angles, key=lambda a: abs(a - detected))}°，"
                f"而斜线实际倾角约 {detected}°，二者不一致",
                "角度弧的起止角应与两条参考线的实际夹角一致", category="geometry",
            ))

    # 坐标数值过大
    coords = [(float(a), float(b)) for a, b in _NUM_TUPLE_RE.findall(masked)]
    if coords and any(abs(x) > 30 or abs(y) > 30 for x, y in coords):
        issues.append(DiagramIssue(
            "info", "out-of-bounds", "存在绝对值大于 30 的坐标，图形可能比例失控",
            "将坐标等比例缩放到常用区间（如 [-10, 10]）", category="geometry",
        ))
    return issues


_NUM_TUPLE_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


_DIRECTION_KEYS = ("above", "below", "left", "right")
_ANCHOR_DIRECTION = {
    "north": "above", "south": "below", "east": "right", "west": "left",
    "north east": "above right", "north west": "above left",
    "south east": "below right", "south west": "below left",
}


def _direction_signature(options: str) -> Tuple[str, ...]:
    """把 above/below/left/right 与 anchor=north/south/east/west 归一化为方向签名。"""
    options = options or ""
    signature: List[str] = []
    for key in _DIRECTION_KEYS:
        if re.search(r"(?<![A-Za-z-])" + key + r"(?![A-Za-z-])", options):
            signature.append(key)
    anchor = re.search(r"anchor\s*=\s*([A-Za-z ]+)", options)
    if anchor:
        value = " ".join(anchor.group(1).split()).lower()
        direction = _ANCHOR_DIRECTION.get(value)
        if direction:
            signature.append(direction)
    return tuple(sorted(set(signature)))


def _check_readability(code: str, question_text: str = "", user_instruction: str = "") -> List[DiagramIssue]:
    del question_text, user_instruction
    issues: List[DiagramIssue] = []
    masked = _mask_comments(code)
    positions = _node_positions(code, masked)
    reported = 0
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            p1, p2 = positions[i], positions[j]
            if p1["pos"] is None or p2["pos"] is None:
                continue
            if p1.get("tricky") or p2.get("tricky"):
                continue
            if p1["text"] == p2["text"]:
                continue
            # 同一锚点但方向选项/对齐方式不同（above/below/left/right、anchor=north/south…）通常不会重叠
            dir1 = _direction_signature(p1["options"])
            dir2 = _direction_signature(p2["options"])
            if dir1 and dir2 and dir1 != dir2:
                continue
            if _dist2(p1["pos"], p2["pos"]) < 0.08 ** 2:
                issues.append(DiagramIssue(
                    "warning", "label-overlap",
                    f"标注「{p1['text']}」与「{p2['text']}」位置几乎重合，渲染后可能叠在一起",
                    "错开节点坐标或使用 above/below/left/right 偏移", category="readability",
                ))
                reported += 1
                break
        if reported >= 3:
            break
    return issues


def _check_compile_log(compile_log: str) -> Tuple[List[DiagramIssue], int]:
    """把真实 xelatex 日志合并进校验报告。"""
    if not compile_log:
        return [], 0
    try:
        from utils.tex_log_tools import extract_tex_errors  # 延迟导入，避免循环依赖
        errors = extract_tex_errors(compile_log)
    except Exception:
        errors = []
    issues: List[DiagramIssue] = []
    if not errors:
        if re.search(r"^!|:\d+:|Emergency stop", compile_log, re.MULTILINE):
            issues.append(DiagramIssue(
                "error", "compile.fatal", "XeLaTeX 编译失败（日志无法进一步解析）",
                "根据日志末尾的 fatal error 修改后重试", category="compile",
            ))
        return issues, len(issues)
    fatal_kinds = {
        "missing-math", "missing-semicolon", "undefined-control-sequence", "no-shape-named",
        "missing-library", "missing-package", "missing-file", "runaway-argument", "extra-brace",
        "dimension-too-large", "undefined-coordinate", "pgfkeys-unknown-key",
        "capacity-exceeded", "emergency-stop", "font-missing", "timeout", "unknown",
    }
    for err in errors[:8]:
        category = str(err.get("category") or "unknown")
        line = err.get("line")
        message = str(err.get("message") or "编译错误").strip()
        hint = str(err.get("hint") or "根据编译日志修改对应命令后重试")
        line_prefix = f"第 {line} 行：" if line else ""
        level = "error" if category in fatal_kinds else "warning"
        issues.append(DiagramIssue(
            level,
            f"compile.{category}",
            f"编译错误 {line_prefix}{message}" if level == "error" else f"编译提示 {line_prefix}{message}",
            hint,
            category="compile",
        ))
    return issues, sum(1 for i in issues if i.level == "error")


def _compute_metrics(code: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    masked = _mask_comments(code)
    stmts = _scan_statements(masked)
    nodes = _iter_node_texts(code, masked)
    named = _collect_named_points(masked)
    segments, closed, has_relative = _collect_geometry(masked, stmts, named)
    arrow_count, max_arrow_len = _count_arrows(masked, stmts)
    detected_angle, _seg = _detect_incline(stmts, masked, named)
    metrics: Dict[str, Any] = {
        "lines": code.count("\n") + 1,
        "draw_commands": sum(1 for st in stmts if st["command"] in _PATH_DRAW_COMMANDS),
        "node_count": len(nodes),
        "labeled_nodes": sum(1 for n in nodes if (n.get("text") or "").strip()),
        "arrow_count": arrow_count,
        "max_arrow_len": round(max_arrow_len, 2),
        "incline_angle_deg": detected_angle,
        "expected_angle_deg": None,
        "angle_delta_deg": None,
        "closed_paths": closed if has_relative else _count_cycles(segments),
        "magnetic_symbols": len(_MAGNETIC_RE.findall(masked)),
        "has_dashed": "dashed" in masked,
        "compile_error_count": 0,
    }
    if extra:
        metrics.update(extra)
    return metrics


# ===================== 主入口 =====================


def _dedupe(issues: List[DiagramIssue]) -> List[DiagramIssue]:
    seen = set()
    result = []
    for issue in issues:
        key = (issue.level, issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _score_issues(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """由 issue 字典列表计算 errors/warnings/infos/score/grade/gate。"""
    errors = [i for i in issues if i.get("level") == "error"]
    warnings = [i for i in issues if i.get("level") == "warning"]
    infos = [i for i in issues if i.get("level") == "info"]
    score = max(0, 100 - 20 * len(errors) - 6 * len(warnings))
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"
    if errors:
        gate = "fail"
    elif len(warnings) >= 2 or score < 80:
        gate = "needs_review"
    else:
        gate = "pass"
    return {
        "errors": errors, "warnings": warnings, "infos": infos,
        "score": score, "grade": grade, "gate": gate,
    }


def apply_extra_issues(
    validation: Dict[str, Any],
    extra_issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """在既有校验报告上追加外部问题（如视觉校验），并重算评分/门禁。"""
    report = dict(validation or {})
    issues: List[Dict[str, Any]] = list(report.get("issues") or [])
    known = {(i.get("level"), i.get("code"), i.get("message")) for i in issues}
    for item in extra_issues or []:
        key = (item.get("level"), item.get("code"), item.get("message"))
        if key in known:
            continue
        known.add(key)
        issues.append(item)
    computed = _score_issues(issues)
    report.update(computed)
    report["issues"] = issues
    report["ok"] = not computed["errors"]
    gate_cn = {"pass": "通过", "needs_review": "需复核", "fail": "未通过"}.get(computed["gate"], computed["gate"])
    report["summary"] = (
        f"得分 {computed['score']}/100（{computed['grade']}）· "
        f"{len(computed['errors'])} 错误 / {len(computed['warnings'])} 警告 · 门禁{gate_cn}"
    )
    if computed["errors"]:
        tips = []
        for item in computed["errors"][:3]:
            hint = f"（建议：{item['hint']}）" if item.get("hint") else ""
            tips.append(f"{item['message']}{hint}")
        report["tips"] = tips
    return report


def structural_failure(validation: Dict[str, Any]) -> bool:
    """判断校验结果是否属于「不值得编译」的结构性失败。"""
    codes = {i.get("code") for i in (validation.get("errors") or [])}
    return bool(codes & {
        "empty", "missing-env", "unbalanced-env", "brace", "math-balance", "empty-picture",
        "structure.empty", "structure.missing-env", "structure.unbalanced-env",
        "structure.brace", "structure.math-balance", "structure.empty-picture",
    })


_AUTOFIX_INFO_CODES = (
    ("Markdown 围栏", "fence", "structure", "已自动移除 Markdown 代码围栏"),
    ("文档头", "header-leak", "structure", "已自动移除文档头（documentclass/usepackage）"),
    ("tikzpicture 环境包裹", "missing-env", "structure", "已自动补齐 tikzpicture 环境包裹"),
    ("缺失的 \\end{tikzpicture}", "unbalanced-env", "structure", "已自动补齐缺失的 \\end{tikzpicture}"),
    ("分号", "statement-end", "syntax", "已自动为路径语句补充分号"),
    ("数学模式", "node-math", "semantics", "已自动为节点标签补齐 $...$ 数学模式"),
    ("Unicode", "unicode-math", "semantics", "已自动把 Unicode 数学符号转为 LaTeX 命令"),
    ("全角", "fullwidth-punctuation", "syntax", "已自动修正 TikZ 语法区域内的全角标点"),
)


def _autofix_infos(autofixes: List[str]) -> List[DiagramIssue]:
    """把确定性自动修复记录映射为 info 级 issue，使契约机器码可被观测/统计。"""
    issues: List[DiagramIssue] = []
    for fix in autofixes or []:
        for keyword, code, category, message in _AUTOFIX_INFO_CODES:
            if keyword in fix:
                issues.append(DiagramIssue(
                    "info", code, f"{message}（{fix}）",
                    "该问题已由确定性修复解决，无需模型重复处理",
                    category=category, fixable=True,
                ))
                break
    return issues


def validate_diagram(
    tikz: str,
    question_text: str = "",
    user_instruction: str = "",
    compile_log: str = "",
) -> Dict[str, Any]:
    """执行完整校验，返回可直接展示/回灌给模型的报告字典（见契约 1.2）。"""
    original = tikz if isinstance(tikz, str) else ""
    code, autofixes = autofix_diagram(original, question_text, user_instruction)
    if not code:
        code = ""

    issues: List[DiagramIssue] = _autofix_infos(autofixes)
    checks_run: List[str] = []
    try:
        structure_issues = _check_structure(code)
        issues.extend(structure_issues)
        checks_run.append("structure")
    except Exception:
        structure_issues = []
    try:
        syntax_issues = _check_syntax(code)
        issues.extend(syntax_issues)
        checks_run.append("syntax")
    except Exception:
        syntax_issues = []
    try:
        semantics_issues = _check_semantics(code)
        issues.extend(semantics_issues)
        checks_run.append("semantics")
    except Exception:
        semantics_issues = []
    try:
        physics_issues, physics_metrics = _check_physics(code, question_text, user_instruction)
        issues.extend(physics_issues)
        checks_run.append("physics")
    except Exception:
        physics_issues, physics_metrics = [], {}
    try:
        geometry_issues = _check_geometry(code, question_text, user_instruction, physics_metrics)
        issues.extend(geometry_issues)
        checks_run.append("geometry")
    except Exception:
        geometry_issues = []
    try:
        readability_issues = _check_readability(code)
        issues.extend(readability_issues)
        checks_run.append("readability")
    except Exception:
        readability_issues = []
    compile_error_count = 0
    if compile_log:
        try:
            compile_issues, compile_error_count = _check_compile_log(compile_log)
            issues.extend(compile_issues)
            checks_run.append("compile")
        except Exception:
            compile_issues = []

    issues = _dedupe(issues)
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order.get(i.level, 3), i.code))
    issue_dicts = [i.to_dict() for i in issues]
    computed = _score_issues(issue_dicts)
    errors = computed["errors"]
    warnings = computed["warnings"]
    infos = computed["infos"]
    score = computed["score"]
    grade = computed["grade"]
    gate = computed["gate"]

    contexts = detect_context(question_text, user_instruction)
    metrics = _compute_metrics(code, {**physics_metrics, "compile_error_count": compile_error_count})

    tip_items = errors[:3] if errors else warnings[:3]
    tips = []
    for item in tip_items:
        hint = f"（建议：{item['hint']}）" if item.get("hint") else ""
        tips.append(f"{item['message']}{hint}")

    gate_cn = {"pass": "通过", "needs_review": "需复核", "fail": "未通过"}.get(gate, gate)
    summary = (
        f"得分 {score}/100（{grade}）· {len(errors)} 错误 / {len(warnings)} 警告 · 门禁{gate_cn}"
    )

    return {
        "ok": not errors,
        "score": score,
        "grade": grade,
        "gate": gate,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "issues": issue_dicts,
        "contexts": contexts,
        "autofixes": autofixes,
        "code": code,
        "metrics": metrics,
        "checks_run": checks_run,
        "summary": summary,
        "tips": tips,
    }


# ===================== 报告格式化 =====================

_LEVEL_ICONS = {"error": "[错误]", "warning": "[警告]", "info": "[提示]"}
_CONTEXT_CN = {
    "force": "受力分析", "incline": "斜面/倾角", "magnetic": "磁场", "optics": "光路",
    "circuit": "电路", "spring": "弹簧", "pulley": "滑轮", "field": "电场",
    "wave": "机械波", "gravity": "万有引力/天体", "collision": "碰撞/动量",
    "thermo": "热学",
}


def format_report(validation: Dict[str, Any], title: str = "自动校验报告") -> str:
    """把校验结果格式化为可展示/可回灌的文字报告。"""
    validation = validation or {}
    errors = validation.get("errors") or []
    warnings = validation.get("warnings") or []
    infos = validation.get("infos") or []
    score = validation.get("score", 100)
    grade = validation.get("grade", "")
    gate = validation.get("gate", "")
    contexts = validation.get("contexts") or []
    autofixes = validation.get("autofixes") or []
    metrics = validation.get("metrics") or {}

    gate_cn = {"pass": "通过", "needs_review": "需复核", "fail": "未通过"}.get(gate, gate)
    lines = [f"【{title}】得分 {score}/100（{grade}）· 门禁：{gate_cn}"]
    if contexts:
        lines.append("识别到的物理场景：" + "、".join(_CONTEXT_CN.get(c, c) for c in contexts))
    if autofixes:
        lines.append(f"[自动修复] 确定性自动修复（{len(autofixes)} 项）：")
        for fix in autofixes:
            lines.append(f"  · {fix}")
    if errors:
        lines.append(f"[错误] 必须修复的错误（{len(errors)} 项）：")
        for item in errors:
            hint = f"（修复建议：{item['hint']}）" if item.get("hint") else ""
            lines.append(f"  · {item['message']}{hint}")
    if warnings:
        lines.append(f"[警告] 建议改进（{len(warnings)} 项）：")
        for item in warnings:
            hint = f"（修复建议：{item['hint']}）" if item.get("hint") else ""
            lines.append(f"  · {item['message']}{hint}")
    if infos:
        lines.append(f"[提示] 提示（{len(infos)} 项）：")
        for item in infos:
            lines.append(f"  · {item['message']}")
    angle = metrics.get("incline_angle_deg")
    expected = metrics.get("expected_angle_deg")
    if angle is not None or expected is not None:
        parts = []
        if angle is not None:
            parts.append(f"图中倾角≈{angle}°")
        if expected is not None:
            parts.append(f"目标角度={expected}°")
        if metrics.get("angle_delta_deg") is not None:
            parts.append(f"偏差={metrics.get('angle_delta_deg')}°")
        lines.append("[几何] 数值几何：" + "，".join(parts))
    if not errors and not warnings:
        lines.append("[通过] 结构、物理图元与标注检查全部通过。")
    return "\n".join(lines)


def build_repair_instructions(validation: Dict[str, Any]) -> str:
    """把校验报告转成回灌给模型的「必须修复清单」反思提示。"""
    validation = validation or {}
    errors = validation.get("errors") or []
    warnings = validation.get("warnings") or []
    score = validation.get("score", 100)
    gate = validation.get("gate", "")
    parts: List[str] = [
        f"确定性校验结果：得分 {score}/100，门禁状态 {gate}。",
    ]
    if errors:
        parts.append("【必须修复的错误（不修复将不能通过质量门禁）】")
        for idx, item in enumerate(errors, 1):
            hint = f" 修复建议：{item['hint']}" if item.get("hint") else ""
            parts.append(f"{idx}. {item['message']}{hint}")
    if warnings:
        parts.append("【建议修复的警告】")
        for idx, item in enumerate(warnings, 1):
            hint = f" 修复建议：{item['hint']}" if item.get("hint") else ""
            parts.append(f"{idx}. {item['message']}{hint}")
    if not errors and not warnings:
        parts.append("校验已通过，可直接输出修正后的代码。")
    parts.append(
        "请只输出修正后的完整 \\begin{tikzpicture}...\\end{tikzpicture} 代码"
        "（保留 ===THOUGHT=== / ===TIKZ=== / ===EXPLANATION=== 三段结构），不要解释。"
    )
    return "\n".join(parts)


__all__ = [
    "DiagramIssue",
    "TEMPLATE_LOADED_LIBRARIES",
    "TEMPLATE_LOADED_PACKAGES",
    "TEMPLATE_DEFINED_STYLES",
    "normalize_diagram",
    "autofix_diagram",
    "detect_context",
    "validate_diagram",
    "structural_failure",
    "apply_extra_issues",
    "format_report",
    "build_repair_instructions",
]
