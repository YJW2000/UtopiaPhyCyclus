# -*- coding: utf-8 -*-
"""物理插图矫正 Agent 插件：螺线管 TikZ 原图绘制。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from services.agent_skills.base_skill import BaseDiagramSkill


_PLUGIN_DIR = Path(__file__).resolve().parent
_SKILL_FILE = _PLUGIN_DIR / "SKILL.md"
_TEMPLATE_FILE = _PLUGIN_DIR / "assets" / "solenoid-patterns.tex"


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _skill_body() -> str:
    """读取已安装的 Codex Skill，并去掉仅供发现使用的 YAML front matter。"""
    text = _read_utf8(_SKILL_FILE).strip()
    if text.startswith("---"):
        _, separator, body = text[3:].partition("\n---")
        if separator:
            text = body.lstrip("\r\n")
    return text


class SolenoidTikzSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "draw-solenoid-tikz"

    @property
    def name(self) -> str:
        return "螺线管 TikZ 原图绘制"

    @property
    def description(self) -> str:
        return "按原图可见线绘制水平铁芯线圈、竖直螺线管及其电源、变阻器和接头。"

    @property
    def tags(self) -> List[str]:
        return [
            "physics",
            "electromagnetism",
            "solenoid",
            "coil",
            "circuit",
            "tikz",
            "plugin",
        ]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        text = f"{user_instruction or ''} {question_context or ''}".lower()
        if "螺线管" in text or "solenoid" in text:
            return True, 1.0
        if any(word in text for word in ("铁芯线圈", "线圈绕向", "绕组", "绕线", "coil")):
            return True, 0.97
        if "线圈" in text and any(
            word in text
            for word in ("电流", "电源", "正极", "负极", "磁场", "电磁", "铁芯", "变阻器", "接线")
        ):
            return True, 0.93
        if "电磁铁" in text:
            return True, 0.90
        return False, 0.0

    def get_system_instructions(self) -> str:
        body = _skill_body()
        if body:
            return body
        return """【螺线管 TikZ 原图绘制】
先核对原图可见线与电源极性；原图没有背面线时不得补背面虚线。水平铁芯采用顶弧—正面斜线—底弧，竖直螺线管采用正面横弧—左右侧弧。最后一匝与外接引线必须共用同一坐标；长极板为正极，短极板为负极。"""

    def get_canonical_snippets(self) -> List[Dict[str, str]]:
        source = _read_utf8(_TEMPLATE_FILE)
        pictures = re.findall(
            r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}",
            source,
            flags=re.DOTALL,
        )
        names = [
            ("水平铁芯线圈", "顶弧—正面斜线—底弧；末匝直接连接外引线"),
            ("竖直通电螺线管", "正面横弧—左右侧弧；下端与电源正极共用接头坐标"),
        ]
        return [
            {"name": name, "description": description, "code": code}
            for (name, description), code in zip(names, pictures)
        ]

    def pre_process(self, user_instruction: str, current_tikz: str) -> Dict[str, Any]:
        text = f"{user_instruction or ''} {current_tikz or ''}"
        actions = [
            "先核对原图的可见线、匝数、绕向、末端接头和电源极性，再选择水平或竖直螺线管模板"
        ]
        if "水平" in text or "铁芯" in text:
            actions.append("水平铁芯按顶弧—正面斜线—底弧绘制，最后一匝不额外补底弧")
        if "竖直" in text:
            actions.append("竖直螺线管按正面横弧—左右侧弧绘制，最下方横弧直接接外电路")
        if any(word in text for word in ("背面", "虚线", "可见线")):
            actions.append("原图省略背面线时，不添加背面虚线、半环或连续数学螺旋")
        if any(word in text for word in ("正极", "负极", "电源", "电池")):
            actions.append("确认长极板为正极、短极板为负极，并沿外电路追踪线圈电流")
        return {"detected_actions": actions}

    def post_process_tikz(self, tikz_code: str) -> Tuple[str, List[str]]:
        # 绕向与可见线必须依据原图判断，不能用字符串替换做危险的自动改写。
        return tikz_code, []


__all__ = ["SolenoidTikzSkill"]
