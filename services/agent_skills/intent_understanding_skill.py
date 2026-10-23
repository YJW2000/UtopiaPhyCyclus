# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能：自然语言意图理解与几何动作映射 (tikz-intent-parser)

专注于将用户的口语化需求（“斜面倾角太陡”、“多画了个向上的力”、“弹簧改成竖直”、“把轨迹改成虚线抛物线”）
精准解析为标准几何与物理图元变换动作 (Geometric & Physical Action Space)。
"""

import re
from typing import List, Dict, Any, Tuple
from .base_skill import BaseDiagramSkill


class IntentUnderstandingSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "tikz-intent-parser"

    @property
    def name(self) -> str:
        return "意图解析与几何动作映射"

    @property
    def description(self) -> str:
        return "将用户口语化的修改需求（倾角、受力增删、弹簧朝向、轨迹线型等）结构化映射为几何与物理图元指令。"

    @property
    def tags(self) -> List[str]:
        return ["intent", "nlp", "geometry", "action-mapping"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        # 该技能作为通用意图解析器，凡是包含修改动作词的均高分激活
        keywords = ["改", "调", "换", "加", "补", "删", "去掉", "变", "倾角", "角度", "方向", "受力", "虚线", "实线", "大", "小", "水平", "竖直"]
        matched_count = sum(1 for kw in keywords if kw in user_instruction)
        if matched_count >= 1:
            return True, min(1.0, 0.5 + matched_count * 0.15)
        return True, 0.5

    def pre_process(self, user_instruction: str, current_tikz: str) -> Dict[str, Any]:
        """从用户指令中提取关键几何动作与参数"""
        extracted_actions = []

        # 1. 角度调整检测 (e.g. 30度, 45°, 调小倾角)
        angle_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:度|°|deg)", user_instruction)
        if angle_match:
            deg = float(angle_match.group(1))
            extracted_actions.append(f"【目标角度变更】: 设定关键几何倾角/偏转角为 {deg}°。")
        elif "倾角调小" in user_instruction or "倾角改小" in user_instruction or "减小倾角" in user_instruction:
            extracted_actions.append("【倾角缩减】: 将斜面倾角缩小（推荐调整至 30° 左右），同时保持滑块重心贴合斜面并随之逆时针/顺时针旋转。")
        elif "倾角调大" in user_instruction or "倾角改大" in user_instruction or "增大倾角" in user_instruction:
            extracted_actions.append("【倾角增大】: 将斜面倾角增大（推荐调整至 45°~60°），同步重算斜边斜率与滑块旋转角度。")

        # 2. 受力分析增删反转检测
        if any(w in user_instruction for w in ["删", "去掉", "多画", "不需要"]) and any(w in user_instruction for w in ["力", "箭头", "支持力", "重力", "摩擦力", "拉力"]):
            extracted_actions.append("【删除多余受力矢量】: 识别并剔除用户指出的冗余力箭头，保留平衡所需的有效分力。")
        if any(w in user_instruction for w in ["补", "加", "漏"]) and any(w in user_instruction for w in ["力", "支持力", "重力", "摩擦力", "分力"]):
            extracted_actions.append("【补充物理受力矢量】: 在受力物体质心处添加对应力箭头，注意箭头指向、大小比例及 $F_N, G, f$ 等下标数学标注。")
        if "反了" in user_instruction or "方向相反" in user_instruction:
            extracted_actions.append("【矢量方向翻转】: 将对应力的方向旋转 180°，核验沿运动趋势的反向阻力或同向动力。")

        # 3. 弹簧/滑轮/构件方向转换
        if "水平" in user_instruction and "弹簧" in user_instruction:
            extracted_actions.append("【弹簧方向修正】: 弹簧采用水平布置，一端固定在竖直墙壁，另一端连接物体。")
        elif "竖直" in user_instruction and "弹簧" in user_instruction:
            extracted_actions.append("【弹簧方向修正】: 弹簧采用竖直悬挂或支撑布置，注意线圈波形起点与终点 Y 轴坐标。")

        # 4. 线型与轨迹修饰
        if "虚线" in user_instruction:
            extracted_actions.append("【线型切换】: 辅助线、抛物线运动轨迹、垂直参考线应添加 [dashed] 样式。")
        if "抛物线" in user_instruction or "平抛" in user_instruction:
            extracted_actions.append("【运动轨迹曲线】: 平抛运动应采用 parabola 或二次贝塞尔曲线，标注初速度 $v_0$ 箭头。")

        return {"detected_actions": extracted_actions}

    def get_system_instructions(self) -> str:
        return """【意图解析与几何动作映射指引 (Intent-to-Geometry Skill)】:
1. 角度调整联动原理：
   - 当改变倾角 θ 时，严禁只改标注数字！必须同时联动修改几何三角形顶角坐标：
     底边为 (0,0) -- (L*cos θ, 0)，对边为 (L*cos θ, 0) -- (L*cos θ, L*sin θ)，斜边从 (0,0) 到 (L*cos θ, L*sin θ)。
   - 斜面上的物块必须添加 `[rotate=θ]` 或沿斜面方向计算位移，确保物块底面与斜面严密贴合，不可悬空或穿模！

2. 受力图元增删与共点性：
   - 所有力矢量必须以被分析物体的质心 (Center of Mass) 为唯一起点出发。
   - 力箭头统一使用 `[-{Latex[length=3mm]}, thick]` 或 `[-latex, thick]`，并在箭头末端外侧使用 `node[above/below/left/right] {$F$}` 放置正规数学符号。

3. 构件连接切线性：
   - 细绳绕过滑轮时，必须切于滑轮边缘，不要从圆心直接穿出。
   - 弹簧必须使用装饰宏包样式：`[decorate, decoration={coil, aspect=0.6, segment length=3mm, amplitude=2mm}]`。
"""
