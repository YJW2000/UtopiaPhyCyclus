# -*- coding: utf-8 -*-
"""
插件技能：插图视觉复核 (diagram-visual-qa)

来源与许可：本项目原创实现（MIT）。审图维度参考 docs/open_source_skills_research.md
中 DeTikZify / TikZilla / VTikZ 等工作的评测思路（图形完整性、代码可编译性、与题意一致性），
仅借鉴指标维度，未复制任何 GPL/非许可兼容代码或数据。

能力：
1. 激活后向模型注入「渲染图视觉复核」的判据与 JSON 输出口径；
2. 与 PiDiagramAgent._visual_validate 的多模态审图步骤配合，把视觉问题转为可修复清单；
3. 不修改代码。
"""

from typing import Any, Dict, List, Tuple

from services.agent_skills.base_skill import BaseDiagramSkill


class VisualQAReviewSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "diagram-visual-qa"

    @property
    def name(self) -> str:
        return "插图视觉复核"

    @property
    def description(self) -> str:
        return "对编译后的渲染图做物理图元完整性/方向/角度一致性审图，输出可修复的问题清单。"

    @property
    def tags(self) -> List[str]:
        return ["visual-check", "multimodal", "review", "plugin"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        kw = ["像不像", "是否符合题意", "审图", "视觉", "读图", "图对不对", "看清楚",
              "画得对吗", "核对图", "图与题干"]
        if any(k in (user_instruction or "") for k in kw):
            return True, 0.8
        return False, 0.0

    def get_system_instructions(self) -> str:
        return """【插图视觉复核技能（插件）】:
当渲染图与题干一致性存疑时，按以下维度复核（视觉校验只做一次，异常按跳过处理，绝不阻塞主流程）：
1. 物理图元完整性：题干要求的力箭头/力标注、斜面与角度弧、光线与法线、磁场 ⊙/⊗、电路元件与
   实心结点、弹簧线圈、滑轮圆与切线、电场线箭头、波形曲线是否齐全；
2. 物理一致性：角度数值与题干是否一致（误差 ≤ 8°）；力的方向/作用点是否合理；串并联与连线关系
   是否与题干一致；光线传播方向箭头是否正确；
3. 可读性：标注是否互相遮挡、力箭头是否过短、文字是否压线、坐标系是否左右上下颠倒；
4. 输出口径（视觉校验用一个 JSON）：{"match": true/false, "score": 0-100,
   "issues": ["具体问题"], "summary": "一句话结论"}。
发现问题时，把每条问题改写成「应该怎么改」的可执行修复指令，再进入下一轮重生成。"""

    def pre_process(self, user_instruction: str, current_tikz: str) -> Dict[str, Any]:
        actions: List[str] = []
        if any(k in (user_instruction or "") for k in ("像不像", "对不对", "视觉", "审图", "核对")):
            actions.append("编译成功后做一次视觉复核：核对物理图元完整性与题干一致性，问题转成修复清单")
        return {"detected_actions": actions}

    def post_process_tikz(self, tikz_code: str) -> Tuple[str, List[str]]:
        return tikz_code, []
