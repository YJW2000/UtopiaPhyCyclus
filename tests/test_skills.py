# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock

from services.agent_skills import (
    SkillRegistry,
    get_default_skill_registry,
    IntentUnderstandingSkill,
    PhysicsMechanicsSkill,
    ElectromagnetismCircuitsSkill,
    OpticsGeometrySkill,
    TikzErrorDoctorSkill,
)
from services.diagram_agent import PiDiagramAgent, AgentMessage


class TestDiagramSkills(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry()

    def test_registry_initialization(self):
        skills = self.registry.list_all_skills()
        skill_ids = [s.skill_id for s in skills]
        self.assertIn("tikz-intent-parser", skill_ids)
        self.assertIn("physics-mechanics-diagram", skill_ids)
        self.assertIn("electromagnetism-circuits-diagram", skill_ids)
        self.assertIn("optics-geometry-diagram", skill_ids)
        self.assertIn("tikz-error-doctor", skill_ids)

    def test_intent_understanding_skill_matching_and_extraction(self):
        skill = self.registry.get_skill("tikz-intent-parser")
        self.assertIsNotNone(skill)

        # 匹配倾角与受力
        matched, score = skill.match_intent("把斜面倾角调小至30度，滑块加上支持力", "高中物理题目")
        self.assertTrue(matched)
        self.assertGreaterEqual(score, 0.5)

        # 前置提炼动作
        pre_info = skill.pre_process("把斜面的倾角改小至30度，滑块去掉向上的拉力", "")
        actions = pre_info.get("detected_actions", [])
        self.assertTrue(any("30.0°" in act or "30°" in act for act in actions))
        self.assertTrue(any("删除" in act or "冗余" in act for act in actions))

    def test_physics_mechanics_skill_matching(self):
        skill = self.registry.get_skill("physics-mechanics-diagram")
        self.assertIsNotNone(skill)

        matched, score = skill.match_intent("滑块在斜面上滑动，弹簧被压缩", "平衡力学题目")
        self.assertTrue(matched)
        self.assertGreaterEqual(score, 0.6)

        snippets = skill.get_canonical_snippets()
        self.assertGreaterEqual(len(snippets), 1)
        self.assertIn(r"\begin{tikzpicture}", snippets[0]["code"])

    def test_electromagnetism_circuits_skill_matching(self):
        skill = self.registry.get_skill("electromagnetism-circuits-diagram")
        self.assertIsNotNone(skill)

        matched, score = skill.match_intent("滑动变阻器滑片向左移动，电流表和电压表读数变化", "电路题目")
        self.assertTrue(matched)
        self.assertGreaterEqual(score, 0.6)

    def test_optics_geometry_skill_matching(self):
        skill = self.registry.get_skill("optics-geometry-diagram")
        self.assertIsNotNone(skill)

        matched, score = skill.match_intent("一束单色光射向三棱镜，发生折射", "光学题目")
        self.assertTrue(matched)
        self.assertGreaterEqual(score, 0.6)

    def test_tikz_error_doctor_post_processing(self):
        doctor = self.registry.get_skill("tikz-error-doctor")
        self.assertIsNotNone(doctor)

        # 1. 测试自动给裸露的 node 数学符号包裹 $...$
        buggy_code = r"""\begin{tikzpicture}
\draw (0,0) -- (2,0) node[right] {30^\circ};
\draw (0,0) -- (0,2) node[above] {F_N};
\end{tikzpicture}"""
        repaired, fixes = doctor.post_process_tikz(buggy_code)
        self.assertIn(r"{$30^\circ$}", repaired)
        self.assertIn(r"{$F_N$}", repaired)
        self.assertTrue(len(fixes) > 0)

        # 2. 测试自动补齐遗漏的分号
        missing_semicolon = r"""\begin{tikzpicture}
\draw (0,0) -- (2,0)
\node at (1,1) {$A$}
\end{tikzpicture}"""
        repaired_semi, semi_fixes = doctor.post_process_tikz(missing_semicolon)
        self.assertIn(r"\draw (0,0) -- (2,0);", repaired_semi)
        self.assertIn(r"\node at (1,1) {$A$};", repaired_semi)

    def test_tikz_error_doctor_compiler_diagnostics(self):
        doctor = self.registry.get_skill("tikz-error-doctor")
        # 诊断 Missing $（真实日志解析工具优先，兼容旧字符串映射）
        diag1 = doctor.diagnose_compiler_error("! LaTeX Error: Missing $ inserted.")
        self.assertIn("Missing $", diag1)
        self.assertTrue(("公式" in diag1) or ("数学" in diag1) or ("$" in diag1))

        # 诊断 Undefined control sequence
        diag2 = doctor.diagnose_compiler_error(r"! Undefined control sequence. \l_tmpa_tl")
        self.assertTrue(("控制序列" in diag2) or ("未定义命令" in diag2))

        # 诊断 Dimension too large
        diag3 = doctor.diagnose_compiler_error("! Dimension too large.")
        self.assertTrue(("尺寸溢出" in diag3) or ("尺寸" in diag3) or ("坐标量级" in diag3))

        # 诊断 TikZ 缺分号（v2 新增类别）
        diag4 = doctor.diagnose_compiler_error(
            "./main.tex:7: Package tikz Error: Giving up on this path. Did you forget a semi colon?."
        )
        self.assertTrue(("分号" in diag4) or ("semi colon" in diag4.lower()))

    def test_registry_match_and_augmentation(self):
        matched = self.registry.match_relevant_skills(
            user_instruction="请把斜面倾角调小至30度，受力分析增加摩擦力",
            question_context="物块静止在斜面上，求支持力与摩擦力。",
            threshold=0.4,
            max_skills=3,
        )
        activated_ids = [s.skill_id for s, _ in matched]
        self.assertIn("tikz-intent-parser", activated_ids)
        self.assertIn("physics-mechanics-diagram", activated_ids)

        # 检查增强提示词生成
        prompt_aug = self.registry.build_prompt_augmentation([s for s, _ in matched])
        self.assertIn("Activated Domain Skills", prompt_aug)
        self.assertIn("物理力学标准图元技能", prompt_aug)

    @patch("services.diagram_agent.post_chat_completion")
    @patch("services.diagram_agent.DiagramTools.compile_with_log")
    @patch("services.diagram_agent.PiDiagramAgent._visual_validate")
    def test_agent_run_turn_with_skills_metadata(self, mock_visual, mock_compile, mock_chat):
        from types import SimpleNamespace

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": """===THOUGHT===
识别到斜面倾角与受力分析需求，激活力学技能。
===TIKZ===
\\begin{tikzpicture}
\\draw (0,0) -- (4,0);
\\end{tikzpicture}
===EXPLANATION===
已通过技能完成斜面与受力更新。"""
                }
            }]
        }
        mock_chat.return_value = (mock_resp, "url")
        mock_compile.return_value = SimpleNamespace(
            ok=True, b64_png="fake_preview_b64", log="", stdout="",
            error_kind="OK", error_line=None, duration_s=0.01,
        )
        mock_visual.return_value = {"ok": True, "score": 90, "issues": [], "summary": "ok", "skipped": False}

        agent = PiDiagramAgent()
        with patch.object(agent, "_get_api_config", return_value={"api_key": "k", "base_url": "https://api/v1", "model_name": "m"}):
            res = agent.run_turn(
                user_instruction="把斜面倾角改成30度",
                current_question_latex=r"\begin{problem}{2024}{G}{全国}{1}{平衡力学}题干\end{problem}",
            )

        self.assertTrue(res["ok"])
        self.assertIn("activated_skills", res)
        self.assertTrue(len(res["activated_skills"]) > 0)
        self.assertTrue(any("意图解析" in s for s in res["activated_skills"]))
        # Harness v2：验收技能与质量门禁字段
        self.assertIn("validation", res)
        self.assertIn("quality_gate", res)
        self.assertIn("trace", res)


if __name__ == "__main__":
    unittest.main()
