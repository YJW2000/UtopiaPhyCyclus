# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能：高中物理力学标准图元技能 (physics-mechanics-diagram)

涵盖力学经典模型的高精度规范：
1. 斜面-滑块受力系统（支持角度参数联动与剖面剖线）
2. 弹簧-振子系统（标准 coil 装饰线与固定端）
3. 定滑轮/动滑轮-轻绳连接体（几何外切）
4. 平抛与圆周运动轨迹（虚线与法向/切向标注）
"""

from typing import List, Dict, Any, Tuple
from .base_skill import BaseDiagramSkill


class PhysicsMechanicsSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "physics-mechanics-diagram"

    @property
    def name(self) -> str:
        return "物理力学标准图元技能"

    @property
    def description(self) -> str:
        return "提供力学斜面、滑块受力分解、弹簧振子、滑轮轻绳及平抛运动的标准 TikZ 图元与严谨几何范式。"

    @property
    def tags(self) -> List[str]:
        return ["physics", "mechanics", "forces", "incline", "spring", "pulley"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        kw = ["斜面", "滑块", "物块", "受力", "弹簧", "滑轮", "小球", "重力", "支持力", "摩擦力", "拉力", "平抛", "圆周", "绳", "倾角", "平衡", "牛顿"]
        text = user_instruction + " " + question_context
        hits = sum(1 for k in kw if k in text)
        if hits >= 2:
            return True, min(1.0, 0.6 + hits * 0.1)
        elif hits == 1:
            return True, 0.6
        return False, 0.0

    def get_system_instructions(self) -> str:
        return """【物理力学标准图元技能规范 (Physics Mechanics Skill)】:
1. 斜面与物块受力规范：
   - 地面与斜面底面必须用阴影纹理标识固定或支撑：`[pattern=north east lines]` 或 `[draw=black, fill=gray!20]`。
   - 斜面三角形建议先定义顶角坐标：例如 `\\coordinate (O) at (0,0); \\coordinate (B) at (4,0); \\coordinate (A) at (4, 2.3);`。
   - 标注角度弧必须使用标准语法：`\\draw (0.8,0) arc (0:30:0.8) node[right, pos=0.5] {$\\theta$};`。
   - 受力分析图中，重力 $G$ 或 $mg$ 必须严格沿真垂直方向向下（-90°），支持力 $F_N$ 必须垂直于接触面向上，滑动摩擦力 $f$ 必须与相对运动趋势严格反向！

2. 弹簧模型规范：
   - 必须使用标准宏包装饰：
     `\\draw[thick, decorate, decoration={coil, aspect=0.6, segment length=3mm, amplitude=2mm}] (0,0) -- (3,0);`
   - 固定端必须画出竖直或水平墙壁并打阴影剖面线。
   - 弹簧与物块连接处画小实心圆点或端点对齐。

3. 滑轮与细绳规范：
   - 定滑轮圆心用 `(P)` 表示，细绳从圆心切线引出（例如 `(P) ++(180:R)` 和 `(P) ++(0:R)` 或 `++(-90:R)`），不可在视觉上悬空。
   - 悬挂物块画为矩形或质点小圆球。
"""

    def get_canonical_snippets(self) -> List[Dict[str, str]]:
        return [
            {
                "name": "斜面滑块与受力分析标准模板",
                "description": "倾角 30° 斜面、斜面阴影、斜面上滑块及三力平衡受力分解",
                "code": r"""\begin{tikzpicture}[line cap=round, line join=round, >=latex, scale=1.2]
  % 1. 斜面几何与地面阴影
  \coordinate (O) at (0,0);
  \coordinate (B) at (4,0);
  \coordinate (A) at (4, 2.309); % 30度: 4 * tan(30) ≈ 2.309
  \fill[gray!15] (O) -- (B) -- (A) -- cycle;
  \draw[thick] (O) -- (B) -- (A) -- cycle;
  \draw[thick] (-0.3, 0) -- (4.3, 0);
  \fill[pattern=north east lines] (-0.3, -0.2) rectangle (4.3, 0);

  % 倾角标注
  \draw (0.8, 0) arc (0:30:0.8) node[pos=0.6, right] {$\theta$};

  % 2. 斜面上的滑块 (中心在 (2, 1.155)，旋转 30 度)
  \begin{scope}[shift={(2.2, 1.27)}, rotate=30]
    \draw[thick, fill=blue!10] (-0.5, 0) rectangle (0.5, 0.6);
    \coordinate (C) at (0, 0.3); % 物块质心
    \fill (C) circle (1.5pt);

    % 受力分析 (在旋转坐标系或世界坐标系下)
    \draw[->, thick, red!80!black] (C) -- (0, 1.5) node[above] {$F_N$}; % 支持力垂直斜面
    \draw[->, thick, red!80!black] (C) -- (-1.2, 0.3) node[above left] {$f$}; % 摩擦力沿斜面向上
  \end{scope}

  % 重力竖直向下 (世界坐标)
  \draw[->, thick, red!80!black] (2.2, 1.27) ++(0, 0.3) -- ++(0, -1.5) node[below] {$G$};
\end{tikzpicture}""",
            },
            {
                "name": "水平弹簧振子与固定端标准模板",
                "description": "左侧固定墙面、水平弹簧与滑块",
                "code": r"""\begin{tikzpicture}[line cap=round, line join=round, >=latex, scale=1.1]
  % 墙壁与地面
  \fill[pattern=north east lines] (-0.3, 0) rectangle (0, 2);
  \draw[thick] (0, 0) -- (0, 2);
  \draw[thick] (0, 0) -- (5.5, 0);
  \fill[pattern=north east lines] (0, -0.2) rectangle (5.5, 0);

  % 弹簧 (装饰线)
  \draw[thick, decorate, decoration={coil, aspect=0.5, segment length=3mm, amplitude=2.5mm}] (0, 0.5) -- (2.6, 0.5);
  \node[above] at (1.3, 0.7) {$k$};

  % 滑块
  \draw[thick, fill=orange!20] (2.6, 0.02) rectangle (4.0, 0.98);
  \node at (3.3, 0.5) {$m$};

  % 速度或位移箭头
  \draw[->, thick, blue] (3.3, 1.2) -- (4.3, 1.2) node[right] {$v_0$};
\end{tikzpicture}""",
            },
        ]
