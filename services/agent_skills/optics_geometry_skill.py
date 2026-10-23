# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能：光学与几何图元技能 (optics-geometry-diagram)

涵盖高中物理光学与数学几何图元规范：
1. 三棱镜折射光路图（法线虚线、入射角/折射角、中间光线箭头）
2. 平面镜与透镜成像（焦点、光心与物像关系）
3. 数学笛卡尔平面直角坐标系与函数曲线
"""

from typing import List, Dict, Any, Tuple
from .base_skill import BaseDiagramSkill


class OpticsGeometrySkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "optics-geometry-diagram"

    @property
    def name(self) -> str:
        return "光学与几何图元技能"

    @property
    def description(self) -> str:
        return "提供三棱镜折射、光线箭头、反射法线、透镜以及数学平面直角坐标系的标准 TikZ 范式。"

    @property
    def tags(self) -> List[str]:
        return ["physics", "optics", "math", "geometry", "coordinate-system"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        kw = ["光线", "折射", "反射", "棱镜", "透镜", "法线", "光路", "入射角", "全反射", "几何", "坐标系", "函数", "抛物线", "椭圆"]
        text = user_instruction + " " + question_context
        hits = sum(1 for k in kw if k in text)
        if hits >= 2:
            return True, min(1.0, 0.65 + hits * 0.1)
        elif hits == 1:
            return True, 0.6
        return False, 0.0

    def get_system_instructions(self) -> str:
        return """【光学与几何图元技能规范 (Optics & Geometry Skill)】:
1. 光路与箭头规范：
   - 传播光线必须在线段中间或末端绘制箭头，建议使用 `[->, >=latex, red!80!black, thick]`。
   - 法线必须严格垂直于界面，使用细虚线 `[dashed, thin, gray]`。
   - 入射角与折射角必须在光线与法线之间标注，不可跨越界面。
   - 三棱镜顶角为 $A$，光线在两侧界面的折射应符合折射定律 $n = \\frac{\\sin i}{\\sin r}$。

2. 数学坐标系与函数图像规范：
   - 坐标轴必须带箭头与变量名：`\\draw[->, thick] (-1, 0) -- (5, 0) node[right] {$x$};`。
   - 原点必须标有字母 $O$：`\\node[below left] at (0, 0) {$O$};`。
   - 刻度线与网格线必须规则轻量，避免粗实线混淆图形主体。
"""

    def get_canonical_snippets(self) -> List[Dict[str, str]]:
        return [
            {
                "name": "三棱镜单色光折射光路图",
                "description": "等边三棱镜、入射法线、内部折射光线与出射光线",
                "code": r"""\begin{tikzpicture}[line cap=round, line join=round, >=latex, scale=1.2]
  % 1. 三棱镜轮廓 (等边三角形)
  \coordinate (A) at (2.5, 3.5);
  \coordinate (B) at (0.5, 0);
  \coordinate (C) at (4.5, 0);
  \draw[thick, fill=cyan!8] (A) -- (B) -- (C) -- cycle;
  \node[above] at (A) {$A$};

  % 2. 入射光线射向 AB 边
  \coordinate (P1) at ($(A)!0.6!(B)$); % 入射点
  \coordinate (LightIn) at (-0.8, 1.0);
  \draw[->, very thick, red!80!black] (LightIn) -- (P1) node[pos=0.4, above] {入射光};

  % 入射面法线 (垂直于 AB)
  \draw[dashed, gray] (P1) ++(150:1.0) -- ++(-30:2.0) node[below right] {法线};

  % 3. 棱镜内部折射光线 (水平或向下偏折)
  \coordinate (P2) at ($(A)!0.6!(C)$); % 出射点
  \draw[->, very thick, red!80!black] (P1) -- (P2);

  % 出射面法线 (垂直于 AC)
  \draw[dashed, gray] (P2) ++(30:1.0) -- ++(-150:2.0);

  % 4. 出射光线偏折出棱镜
  \coordinate (LightOut) at (5.8, 0.4);
  \draw[->, very thick, red!80!black] (P2) -- (LightOut) node[pos=0.6, above] {出射光};
\end{tikzpicture}""",
            },
            {
                "name": "数学平面直角坐标系与函数曲线",
                "description": "带刻度、原点与抛物线轨迹的规范坐标系",
                "code": r"""\begin{tikzpicture}[line cap=round, line join=round, >=latex, scale=1.0]
  % 坐标网格与轴
  \draw[gray!25, thin, step=1.0] (-1.5, -1.0) grid (4.5, 3.5);
  \draw[->, thick] (-1.5, 0) -- (4.5, 0) node[right] {$x$};
  \draw[->, thick] (0, -1.0) -- (0, 3.5) node[above] {$y$};
  \node[below left] at (0, 0) {$O$};

  % 刻度标记
  \foreach \x in {1, 2, 3, 4} \draw (\x, 1pt) -- (\x, -2pt) node[below, font=\footnotesize] {$\x$};
  \foreach \y in {1, 2, 3} \draw (1pt, \y) -- (-2pt, \y) node[left, font=\footnotesize] {$\y$};

  % 抛物线曲线 y = -(x-2)^2 + 3
  \draw[very thick, blue, domain=0.27:3.73, samples=100] plot (\x, {-(\x - 2)*(\x - 2) + 3});
  \fill[red] (2, 3) circle (2pt) node[above, font=\footnotesize] {$(2, 3)$};
\end{tikzpicture}""",
            },
        ]
