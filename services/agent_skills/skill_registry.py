# -*- coding: utf-8 -*-
"""
Pi-Agent 技能注册与调度中心 (Skill Registry & Dispatcher)

管理所有内置与扩展的领域技能：
1. 意图探测与技能动态匹配 (Dynamic Intent Matching)
2. 提示词增强拼装 (System Instruction & Snippet Augmentation)
3. 确定性前置意图提炼与后置代码自愈 (Pre/Post Processing Pipeline)
4. 编译错误智能诊断 (Diagnostic Dispatcher)
5. 外部技能插件动态加载 (Plugin Loader, Harness v2)

Harness v2 新增：
- 内置 `physics-diagram-auditor` 验收技能；
- 自动扫描 services/agent_skills/plugins/*/manifest.json 加载外部技能插件
  （PI_AGENT_DISABLE_PLUGINS=1 可关闭；单个插件失败不影响主流程）。
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from .base_skill import BaseDiagramSkill
from .intent_understanding_skill import IntentUnderstandingSkill
from .physics_mechanics_skill import PhysicsMechanicsSkill
from .electromagnetism_circuits_skill import ElectromagnetismCircuitsSkill
from .optics_geometry_skill import OpticsGeometrySkill
from .physics_diagram_auditor_skill import PhysicsDiagramAuditorSkill
from .tikz_error_doctor_skill import TikzErrorDoctorSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Pi-Agent 领域技能注册中心"""

    def __init__(self, load_plugin_skills: bool = True):
        self._skills: Dict[str, BaseDiagramSkill] = {}
        self.plugin_skills: List[str] = []
        # 默认注册核心技能集（验收技能必须存在，保证每次生成都有验收口径）
        self.register(IntentUnderstandingSkill())
        self.register(PhysicsMechanicsSkill())
        self.register(ElectromagnetismCircuitsSkill())
        self.register(OpticsGeometrySkill())
        self.register(PhysicsDiagramAuditorSkill())
        self.register(TikzErrorDoctorSkill())
        # 动态加载外部插件技能（失败只记日志）
        if load_plugin_skills:
            try:
                from .plugin_loader import load_plugins
                self.plugin_skills = load_plugins(self)
            except Exception as exc:  # pragma: no cover - 插件系统异常不应影响主流程
                logger.warning("plugin loader unavailable: %s", exc)

    def register(self, skill: BaseDiagramSkill) -> None:
        """注册技能"""
        self._skills[skill.skill_id] = skill

    def get_skill(self, skill_id: str) -> Optional[BaseDiagramSkill]:
        """按 ID 查询技能"""
        return self._skills.get(skill_id)

    def list_all_skills(self) -> List[BaseDiagramSkill]:
        """获取所有已注册技能"""
        return list(self._skills.values())

    def match_relevant_skills(
        self,
        user_instruction: str,
        question_context: str,
        threshold: float = 0.35,
        max_skills: int = 3,
    ) -> List[Tuple[BaseDiagramSkill, float]]:
        """
        根据用户指令与题目上下文，评估所有技能的相关度得分并排序返回
        """
        scored_skills = []
        for skill in self._skills.values():
            try:
                matched, score = skill.match_intent(user_instruction, question_context)
            except Exception:
                continue
            if matched and score >= threshold:
                scored_skills.append((skill, score))

        # 按得分从高到低排序
        scored_skills.sort(key=lambda x: x[1], reverse=True)
        return scored_skills[:max_skills]

    def build_prompt_augmentation(
        self,
        activated_skills: List[BaseDiagramSkill],
        include_snippets: bool = True,
    ) -> str:
        """
        将激活技能的规则指引与经典范例组装为注入到大模型的领域知识增强块
        """
        if not activated_skills:
            return ""

        sections = ["\n【Pi-Agent 动态加载的专业技能指引 (Activated Domain Skills)】:"]
        for skill in activated_skills:
            instr = skill.get_system_instructions().strip()
            if instr:
                sections.append(f"\n--- [技能: {skill.name} ({skill.skill_id})] ---\n{instr}")

            if include_snippets:
                snippets = skill.get_canonical_snippets()
                if snippets:
                    sections.append(f"\n* 技能 [{skill.name}] 标准代码范例参考：")
                    for snip in snippets[:1]:  # 选取最具代表性的 1 个规范范式，避免 Prompt 过长
                        sections.append(f"  // 范式: {snip['name']} ({snip.get('description', '')})\n{snip['code']}")

        return "\n".join(sections) + "\n"

    def run_pre_processing(
        self,
        user_instruction: str,
        current_tikz: str,
        activated_skills: List[BaseDiagramSkill],
    ) -> Dict[str, Any]:
        """汇总所有激活技能的前置提取结果（如解析出的几何动作、目标角度、增删项）"""
        combined: Dict[str, Any] = {"detected_actions": []}
        for skill in activated_skills:
            try:
                res = skill.pre_process(user_instruction, current_tikz)
            except Exception as exc:
                logger.warning("skill %s pre_process failed: %s", skill.skill_id, exc)
                continue
            if isinstance(res, dict) and "detected_actions" in res:
                combined["detected_actions"].extend(res["detected_actions"])
        return combined

    def run_post_processing(
        self,
        tikz_code: str,
        activated_skills: Optional[List[BaseDiagramSkill]] = None,
    ) -> Tuple[str, List[str]]:
        """
        执行所有激活技能的后置代码净化与自愈流水线（特别包含 tikz-error-doctor）
        """
        skills_to_run = list(activated_skills or self._skills.values())
        # 确保 tikz-error-doctor 必定运行在最后阶段
        doctor = self.get_skill("tikz-error-doctor")
        if doctor and doctor not in skills_to_run:
            skills_to_run.append(doctor)

        current_code = tikz_code
        all_fixes: List[str] = []

        for skill in skills_to_run:
            try:
                current_code, fixes = skill.post_process_tikz(current_code)
            except Exception as exc:
                logger.warning("skill %s post_process failed: %s", skill.skill_id, exc)
                continue
            all_fixes.extend(fixes or [])

        return current_code, all_fixes

    def diagnose_compiler_error(self, error_log: str) -> str:
        """调用错误诊断技能分析编译日志"""
        doctor = self.get_skill("tikz-error-doctor")
        if isinstance(doctor, TikzErrorDoctorSkill):
            return doctor.diagnose_compiler_error(error_log)
        return TikzErrorDoctorSkill.diagnose_compiler_error(error_log)


# 全局单例
_default_registry = None


def get_default_skill_registry() -> SkillRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = SkillRegistry()
    return _default_registry
