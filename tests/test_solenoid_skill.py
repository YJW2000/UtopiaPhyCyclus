# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from services.agent_skills import SkillRegistry


ROOT = Path(__file__).resolve().parents[1]


class TestSolenoidTikzSkill(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry()
        self.skill = self.registry.get_skill("draw-solenoid-tikz")

    def test_plugin_is_installed(self):
        self.assertIsNotNone(self.skill)
        self.assertIn("draw-solenoid-tikz", self.registry.plugin_skills)

    def test_solenoid_intent_activates_with_high_priority(self):
        matched = self.registry.match_relevant_skills(
            "按原图重画竖直螺线管，让下端直接接到电源正极",
            "题图含四匝线圈、滑动变阻器和电池",
        )
        self.assertTrue(matched)
        self.assertEqual(matched[0][0].skill_id, "draw-solenoid-tikz")
        self.assertEqual(matched[0][1], 1.0)

    def test_unrelated_diagram_does_not_activate(self):
        matched, score = self.skill.match_intent("把斜面倾角改为 30 度", "物块受力分析")
        self.assertFalse(matched)
        self.assertEqual(score, 0.0)

    def test_installed_skill_and_both_templates_are_injected(self):
        instructions = self.skill.get_system_instructions()
        self.assertIn("原图没有背面线时，禁止自行补背面虚线", instructions)
        self.assertIn("长极板 = 正极；短极板 = 负极", instructions)

        snippets = self.skill.get_canonical_snippets()
        self.assertEqual(len(snippets), 2)
        self.assertEqual(snippets[0]["name"], "水平铁芯线圈")
        self.assertEqual(snippets[1]["name"], "竖直通电螺线管")
        self.assertTrue(all(r"\begin{tikzpicture}" in item["code"] for item in snippets))

        augmentation = self.registry.build_prompt_augmentation([self.skill])
        self.assertIn("螺线管 TikZ 原图绘制", augmentation)
        self.assertIn("最后一匝", augmentation)
        self.assertIn(r"\begin{tikzpicture}", augmentation)

    def test_pre_processing_carries_connection_checks(self):
        result = self.skill.pre_process(
            "竖直螺线管不要画背面虚线，下端接电源正极",
            "",
        )
        actions = "\n".join(result["detected_actions"])
        self.assertIn("最下方横弧直接接外电路", actions)
        self.assertIn("不添加背面虚线", actions)
        self.assertIn("长极板为正极", actions)

    def test_ocr_prompt_contains_the_same_solenoid_contract(self):
        prompt = (ROOT / "ocr_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("【螺线管、线圈与绕组原图转写专项规则】", prompt)
        self.assertIn("$N$ 个顶部小弧、$N$ 根正面斜线、$N-1$ 个底部小弧", prompt)
        self.assertIn("长极板为正极、短极板为负极", prompt)
        self.assertIn("任何一项不确定时优先裁取原图", prompt)

    def test_release_allowlist_keeps_installed_skill_resources(self):
        from tools.release import selected_files

        selected = set(selected_files(ROOT))
        self.assertIn(
            "services/agent_skills/plugins/draw_solenoid_tikz/SKILL.md",
            selected,
        )
        self.assertIn(
            "services/agent_skills/plugins/draw_solenoid_tikz/assets/solenoid-patterns.tex",
            selected,
        )


if __name__ == "__main__":
    unittest.main()
