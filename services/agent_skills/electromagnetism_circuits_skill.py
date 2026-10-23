# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能：电磁学与电路规范图元技能 (electromagnetism-circuits-diagram)

涵盖高中物理电磁场与电路实验经典图元：
1. 匀强磁场点/叉点阵分布与边界
2. 带电粒子在电磁场中的偏转轨迹与回旋圆弧
3. 规范实验室电路元件（电源长短极、电阻矩形、电容双平行板、电表圈号、实心节点）
"""

from typing import List, Dict, Any, Tuple
from .base_skill import BaseDiagramSkill


class ElectromagnetismCircuitsSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "electromagnetism-circuits-diagram"

    @property
    def name(self) -> str:
        return "电磁学与电路规范图元技能"

    @property
    def description(self) -> str:
        return "提供匀强电磁场阵列、带电粒子偏转圆弧轨迹以及规范电路原理图的标准 TikZ 范式。"

    @property
    def tags(self) -> List[str]:
        return ["physics", "electromagnetism", "magnetic-field", "circuits", "particles"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        kw = ["磁场", "电场", "电荷", "粒子", "偏转", "电路", "电阻", "电容", "电表", "滑动变阻器", "电源", "开关", "安培力", "洛伦兹力", "感应", "电磁"]
        text = user_instruction + " " + question_context
        hits = sum(1 for k in kw if k in text)
        if hits >= 2:
            return True, min(1.0, 0.65 + hits * 0.1)
        elif hits == 1:
            return True, 0.6
        return False, 0.0

    def get_system_instructions(self) -> str:
        return """【电磁学与电路规范图元技能规范 (Electromagnetism & Circuits Skill)】:
1. 磁场图元规范：
   - “垂直纸面向里”的磁场统一使用 `\\times` 或 `\\otimes`：
     `\\foreach \\x in {1,2,3,4} \\foreach \\y in {1,2,3} \\node at (\\x,\\y) {$\\times$};`
   - “垂直纸面向外”的磁场统一使用圆点 `\\cdot` 或 `\\odot`。
   - 区域边界必须使用实线或虚线清晰框定，标注磁感应强度 $B$ 与电场强度 $E$（带右向或下向箭头）。

2. 粒子轨迹规范：
   - 粒子带电性质必须标明：带正电粒子画 `\\node[circle, draw, inner sep=1pt] at (...) {$+$};`，负电粒子画 `$- $`。
   - 偏转圆弧必须与入射速度方向相切，并严格符合左手定则（带负电粒子四指指反方向）。
   - 圆心坐标与半径必须几何自洽，不可凭空画不对称的圆弧。

3. 规范电路图绘制（严禁粗糙几何块）：
   - 直流电源：由长细线（正极）与短粗线（负极）构成，例如：
     `\\draw (0,0) -- (1,0); \\draw (1,-0.4) -- (1,0.4); \\draw[thick] (1.3,-0.25) -- (1.3,0.25); \\draw (1.3,0) -- (2.3,0);`
   - 电阻：标准绘制为窄矩形 `\\draw (2, -0.2) rectangle (3.2, 0.2);`，中心或上方标注 $R$。
   - 电容：两条间距为 2mm~3mm 的等长平行线。
   - 电表：圆圈内填大写正体字母 `\\mathrm{V}` 或 `\\mathrm{A}`。
   - 导线 T 型连接处必须标记实心圆点 `\\fill (x,y) circle (2pt);`，单纯十字交叉无连接时严禁加点！
"""

    def get_canonical_snippets(self) -> List[Dict[str, str]]:
        return [
            {
                "name": "磁场边界与带电粒子偏转轨迹",
                "description": "矩形匀强磁场、粒子入射、圆弧轨迹与出射角度标注",
                "code": r"""\begin{tikzpicture}[line cap=round, line join=round, >=latex, scale=1.1]
  % 磁场边界
  \draw[thick, dashed] (0, 0) rectangle (5, 3.5);
  \node[above right] at (0, 3.5) {$B$};

  % 磁场十字阵列
  \foreach \x in {0.8, 2.0, 3.2, 4.4} {
    \foreach \y in {0.7, 1.8, 2.9} {
      \node[gray!80] at (\x, \y) {$\times$};
    }
  }

  % 粒子入射 (在 (0, 1.0) 处垂直射入，带正电向上偏转)
  \coordinate (Enter) at (0, 1.0);
  \draw[->, very thick, blue] (-1.0, 1.0) -- (Enter) node[pos=0.5, above] {$v_0$};
  \fill[blue] (-1.0, 1.0) circle (2pt) node[left] {$+q$};

  % 偏转轨迹 (圆心在 (0, 3.0), 半径 R=2.0, 偏转角 60度)
  % 入射点处切线水平向右，偏转为从 270度 到 330度
  \draw[thick, blue, ->] (Enter) arc (270:330:2.0);
  \coordinate (Exit) at ({2.0*cos(330)}, {3.0 + 2.0*sin(330)});
  \draw[thick, blue, ->] (Exit) -- ++(60:1.0) node[right] {$v$};

  % 圆心与辅助半径
  \coordinate (Center) at (0, 3.0);
  \fill (Center) circle (1.5pt) node[left] {$O$};
  \draw[dashed, gray] (Center) -- (Enter) node[pos=0.5, left] {$R$};
  \draw[dashed, gray] (Center) -- (Exit) node[pos=0.5, above left] {$R$};
\end{tikzpicture}""",
            },
            {
                "name": "规范闭合直流电路原理图",
                "description": "包含电源、电键、电阻、滑动变阻器和电表的规范电路图",
                "code": r"""\begin{tikzpicture}[line cap=round, line join=round, >=latex, scale=1.0]
  % 回路外框
  \coordinate (A) at (0, 3);
  \coordinate (B) at (5, 3);
  \coordinate (C) at (5, 0);
  \coordinate (D) at (0, 0);

  % 上导线与开关
  \draw[thick] (A) -- (1.5, 3);
  \draw[thick] (1.5, 3) -- ++(30:0.8); % 开关断开
  \fill (1.5, 3) circle (2pt);
  \fill (2.2, 3) circle (2pt);
  \node[above] at (1.8, 3.4) {$S$};
  \draw[thick] (2.2, 3) -- (3.0, 3);

  % 定值电阻 R
  \draw[thick, fill=white] (3.0, 2.75) rectangle (4.2, 3.25);
  \node at (3.6, 3) {$R$};
  \draw[thick] (4.2, 3) -- (B);

  % 右侧电流表 A
  \draw[thick] (B) -- (5, 1.8);
  \draw[thick, fill=white] (5, 1.5) circle (0.35);
  \node at (5, 1.5) {$\mathrm{A}$};
  \draw[thick] (5, 1.2) -- (C);

  % 下支路：直流电源 (E, r)
  \draw[thick] (C) -- (3.0, 0);
  % 电源长短极
  \draw[thick] (3.0, -0.4) -- (3.0, 0.4); % 正极长线
  \draw[line width=2pt] (2.7, -0.25) -- (2.7, 0.25); % 负极短粗线
  \node[below] at (2.85, -0.45) {$E, r$};
  \draw[thick] (2.7, 0) -- (D);

  % 左侧导线连接
  \draw[thick] (D) -- (A);

  % 并联电压表 V 跨接在 R 两端
  \draw[thick] (3.0, 3) -- (3.0, 4.2) -- (3.3, 4.2);
  \fill (3.0, 3) circle (2pt);
  \draw[thick, fill=white] (3.6, 4.2) circle (0.35);
  \node at (3.6, 4.2) {$\mathrm{V}$};
  \draw[thick] (3.9, 4.2) -- (4.2, 4.2) -- (4.2, 3);
  \fill (4.2, 3) circle (2pt);
\end{tikzpicture}""",
            },
        ]
