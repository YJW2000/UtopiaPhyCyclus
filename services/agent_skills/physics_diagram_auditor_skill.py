# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能：物理插图验收与质量审计技能 (physics-diagram-auditor)

Harness v2 新增技能，职责是把「编译成功 ≠ 物理图正确」写成模型可执行的验收清单，
并在前处理阶段把目标角度、必须出现的物理图元提炼成动作提示，降低模型漏画/画错的概率。

该技能不修改代码（确定性修复由 tikz-error-doctor 与 diagram_validate.autofix 负责），
只负责：
1. 注入六类验收 rubric（结构/语法/语义/物理/几何/可读性 + 质量门禁）；
2. 从用户指令/题干中提炼目标角度与必须出现的物理图元；
3. 提供一张可编译的物理示意图规范范式。
"""

import re
from typing import Any, Dict, List, Tuple

from .base_skill import BaseDiagramSkill


class PhysicsDiagramAuditorSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "physics-diagram-auditor"

    @property
    def name(self) -> str:
        return "物理插图验收与质量审计"

    @property
    def description(self) -> str:
        return "按结构/语法/语义/物理图元/数值几何/可读性六类清单验收物理 TikZ 插图，并提炼目标角度与必备图元。"

    @property
    def tags(self) -> List[str]:
        return ["audit", "validation", "physics", "quality-gate", "rubric"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        kw = [
            "插图", "图形", "示意图", "tikz", "TikZ", "受力", "光路", "电路", "磁场",
            "斜面", "滑轮", "弹簧", "校验", "检查", "修正", "重画", "改图", "标注",
        ]
        if any(k in (user_instruction or "") for k in kw):
            return True, 0.82
        # 常驻兜底：所有插图修正都应经过验收口径
        return True, 0.36

    def get_system_instructions(self) -> str:
        return """【物理插图验收与质量审计技能 (Physics Diagram Auditor)】:
你的输出会在编译前后被确定性校验器逐项验收，评分公式为 `100 - 20×错误数 - 6×警告数`，
错误未清零即门禁失败。请按以下六类清单自检后再输出：

1. 结构：恰好一个 \\begin{tikzpicture}...\\end{tikzpicture}；花括号、$、环境均配平；不夹带
   \\documentclass/\\usepackage/\\begin{document}；不输出 Markdown 围栏。
2. TikZ 语法：每条 \\draw/\\fill/\\filldraw/\\path/\\node/\\coordinate/\\clip 语句以 `;` 结束
   （跨行语句只在真正结束处补分号）；node 花括号闭合；具名坐标先 \\coordinate 定义后引用；
   只使用模板预载库；不使用 circuitikz 环境。
3. 数学语义：node 文本中的 ^ _ \\circ \\theta 等数学符号必须在 $...$ 内；数学模式中中文写
   \\text{中文}；不要用 Unicode 数学符号（θ、°、×、⊙），改写成 $\\theta$、$30^\\circ$ 等。
4. 物理图元（按题干场景逐项核对）：
   - 受力分析：力箭头（-latex/->）+ 力符号标注（$mg$、$F_N$、$f$）；
   - 斜面：斜线 + 角度弧/角度标注，倾角与题干给定角度一致（误差 ≤ 8°）；
   - 垂直纸面磁场：\\odot（向外）/ \\otimes（向里）；
   - 光路：光线箭头 + 虚线法线 + 入射/折射角弧；
   - 电路：闭合回路，导线连接处加实心结点（\\fill (x,y) circle (1.5pt);）；
   - 弹簧：decorations.pathmorphing 的 coil；滑轮：圆 + 与圆相切的绳段；
   - 电场：电场线箭头；等势线用虚线；波形用 sin/cos/plot 曲线。
5. 数值几何：角度弧的起止角与参考线实际夹角一致；避免零长度/重合线段；坐标量级建议
   |x|,|y| ≤ 10（最大不超过 30）。
6. 可读性：绝对定位的标注不要重叠；受力箭头长度 ≥ 0.8 坐标单位；文字不要压线。

质量门禁：errors == 0 且 warnings < 2 视为通过；存在 1 条警告或得分 < 80 需复核；门禁未通过时
不会自动写回题目源码。请优先修复 error 级问题，再处理 warning 级问题。"""

    def pre_process(self, user_instruction: str, current_tikz: str) -> Dict[str, Any]:
        actions: List[str] = []
        instruction = user_instruction or ""
        # 目标角度提炼
        for pattern in (
            r"(?:倾角|角度|夹角)\s*(?:为|是|约|调(?:小|大)?至|改(?:为|成|至))?\s*(\d+(?:\.\d+)?)\s*(?:°|度|∘)",
            r"[θΘ]\s*[=＝]\s*(\d+(?:\.\d+)?)",
            r"(\d+(?:\.\d+)?)\s*(?:°|度|∘)",
        ):
            m = re.search(pattern, instruction)
            if m:
                try:
                    angle = float(m.group(1))
                except ValueError:
                    continue
                if 1.0 <= angle <= 89.0:
                    actions.append(
                        f"目标角度：{angle:g}°（图中斜线实际倾角需与之一致，允许误差 ≤ 8°）"
                    )
                    break
        # 必备物理图元提炼
        element_rules = (
            (("受力", "支持力", "摩擦力", "拉力", "重力"), "验收：至少画出受力箭头，并为每个力加 node 标注（如 $mg$、$F_N$、$f$）"),
            (("斜面", "倾角"), "验收：斜面斜线 + 角度弧/角度标注齐全"),
            (("磁场", "洛伦兹", "安培力"), "验收：垂直纸面磁场用 \\odot/\\otimes 标注方向"),
            (("光线", "入射", "反射", "折射"), "验收：光线方向箭头 + 虚线法线 + 角度弧"),
            (("电路", "电阻", "电源", "电流表", "电压表"), "验收：闭合回路，元件用基本图元，连接处加实心结点"),
            (("弹簧", "劲度系数"), "验收：弹簧用 coil 装饰绘制"),
            (("滑轮", "轻绳"), "验收：滑轮为圆，绳段与圆周相切"),
            (("电场", "等势"), "验收：电场线带箭头，等势线用虚线"),
        )
        haystack = f"{instruction}\n{current_tikz or ''}"
        for keywords, action in element_rules:
            if any(k in instruction for k in keywords):
                actions.append(action)
            elif any(k in haystack for k in keywords):
                continue
        return {"detected_actions": actions}

    def get_canonical_snippets(self) -> List[Dict[str, str]]:
        return [
            {
                "name": "斜面 + 受力分析规范范式",
                "description": "30° 斜面、质心小球、重力/支持力/摩擦力的标准画法与角度弧标注",
                "code": r"""\begin{tikzpicture}[scale=1, >=latex]
\coordinate (O) at (0,0);
\coordinate (B) at (6,0);
\coordinate (A) at (6,3.46);
\draw[thick] (O) -- (B) -- (A) -- cycle;
\draw (1,0) arc (0:30:1);
\node at (1.35,0.32) {$30^\circ$};
\fill[gray!60] (3,1.73) circle (0.18);
\node[above right] at (3,1.73) {$m$};
\draw[thick,-latex] (3,1.73) -- (3,0.53) node[below] {$mg$};
\draw[thick,-latex] (3,1.73) -- (2.4,2.77) node[above left] {$F_N$};
\draw[thick,-latex] (3,1.73) -- (4.04,2.33) node[right] {$f$};
\end{tikzpicture}""",
            }
        ]
