# -*- coding: utf-8 -*-
"""
Pi-Agent 领域技能基础规范 (Base Skill Interface)

遵循开源 Agent 插件与技能规范（类似 Claude Skill / Semantic Kernel Plugin）：
1. 技能元数据 (name, description, tags)
2. 动态激活条件 (match_intent)
3. 领域推理指引与规范提示 (get_system_instructions)
4. 经典图元代码范式 (get_canonical_snippets)
5. 确定性前置/后置修复器 (pre_process, post_process)
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple


class BaseDiagramSkill(ABC):
    """图形矫正 Agent 领域技能基类"""

    @property
    @abstractmethod
    def skill_id(self) -> str:
        """技能唯一标识符"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """技能展示名称"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """技能用途描述"""
        pass

    @property
    def tags(self) -> List[str]:
        """技能关联标签"""
        return []

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        """
        判断用户指令与题目是否匹配该技能。
        返回: (is_matched, confidence_score [0.0 ~ 1.0])
        """
        return False, 0.0

    def get_system_instructions(self) -> str:
        """返回该技能注入到 Agent 的领域专业提示词与约束规则"""
        return ""

    def get_canonical_snippets(self) -> List[Dict[str, str]]:
        """
        返回该技能包含的高质量、可编译 TikZ 规范代码模版：
        [{'name': '模版名称', 'description': '适用场景', 'code': '\\begin{tikzpicture}...\\end{tikzpicture}'}]
        """
        return []

    def pre_process(self, user_instruction: str, current_tikz: str) -> Dict[str, Any]:
        """在调用大模型前执行的意图预处理或规则变换"""
        return {}

    def post_process_tikz(self, tikz_code: str) -> Tuple[str, List[str]]:
        """
        对 LLM 输出的 TikZ 代码执行确定性规则净化与修复。
        返回: (repaired_tikz_code, list_of_applied_fixes)
        """
        return tikz_code, []
