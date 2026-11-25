# -*- coding: utf-8 -*-
"""Pi-Agent Harness v2 自愈闭环测试（LLM 与编译均 mock，不依赖网络/xelatex）。"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.diagram_agent import AgentMessage, DiagramTools, PiDiagramAgent
from services.agent_skills import SkillRegistry

QUESTION_INCLINE = r"""\begin{problem}{2024}{G}{全国卷}{1}{平衡力学}
如图所示，质量为 $m$ 的滑块静止在倾角为 $30^\circ$ 的斜面上，求滑块受到的摩擦力大小。
\begin{tikzpicture}
\draw[thick] (0,0) -- (4,0) -- (4,2) -- cycle;
\end{tikzpicture}
\end{problem}"""

TIKZ_FORCE_INVALID = r"""\begin{tikzpicture}
\node at (0,0) {$F_N$};
\end{tikzpicture}"""

TIKZ_FORCE_VALID = r"""\begin{tikzpicture}
\coordinate (O) at (0,0);
\coordinate (B) at (5,0);
\coordinate (A) at (5,3);
\draw[thick] (O) -- (B) -- (A) -- cycle;
\draw (1,0) arc (0:30:1);
\node at (1.35,0.32) {$30^\circ$};
\fill[gray!60] (2.5,1.5) circle (0.12);
\draw[thick,-latex] (2.5,1.5) -- (2.5,0.3) node[below] {$mg$};
\draw[thick,-latex] (2.5,1.5) -- (1.9,2.54) node[above left] {$F_N$};
\draw[thick,-latex] (2.5,1.5) -- (3.54,2.1) node[right] {$f$};
\end{tikzpicture}"""


def _reply(tikz: str, thought: str = "分析", explanation: str = "已修正插图") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{
            "message": {
                "content": f"===THOUGHT===\n{thought}\n===TIKZ===\n{tikz}\n===EXPLANATION===\n{explanation}"
            }
        }]
    }
    return resp


def _compile_ok(b64: str = "fake_png"):
    return SimpleNamespace(
        ok=True, b64_png=b64, log="", stdout="", error_kind="OK", error_line=None, duration_s=0.01,
    )


def _compile_fail(log: str = "main.tex:7: Package tikz Error: Giving up on this path. Did you forget a semi colon?."):
    return SimpleNamespace(
        ok=False, b64_png=None, log=log, stdout="", error_kind="COMPILE_ERROR", error_line=7, duration_s=0.01,
    )


class HarnessTestBase(unittest.TestCase):
    def setUp(self):
        self.agent = PiDiagramAgent()
        self.api_cfg = patch.object(
            self.agent, "_get_api_config",
            return_value={"api_key": "k", "base_url": "https://api.local/v1", "model_name": "m"},
        )
        self.api_cfg.start()
        self.addCleanup(self.api_cfg.stop)

    def run_agent(self, replies, compiles, visual=None, max_attempts=2):
        sequence = replies if len(replies) > 1 else replies * 10
        chat = MagicMock()
        chat.side_effect = [(reply, "url") for reply in sequence]
        visual_mock = MagicMock(return_value=visual or {
            "ok": True, "score": 90, "issues": [], "summary": "ok", "skipped": True,
        })
        compile_mock = MagicMock()
        compile_mock.side_effect = compiles if len(compiles) > 1 else compiles * 10
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch("services.diagram_agent.PiDiagramAgent._visual_validate", visual_mock):
            result = self.agent.run_turn(
                user_instruction="把斜面倾角改成30度并画出受力分析",
                current_question_latex=QUESTION_INCLINE,
                max_self_healing_attempts=max_attempts,
            )
        return result, chat, compile_mock, visual_mock


class TestHarnessSelfHealing(HarnessTestBase):
    def test_validation_feedback_triggers_reflection_and_recovers(self):
        result, chat, compile_mock, _visual = self.run_agent(
            replies=[_reply(TIKZ_FORCE_INVALID, "第一版"), _reply(TIKZ_FORCE_VALID, "第二版")],
            compiles=[_compile_ok()],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(result["trace"]), 2)
        self.assertEqual(result["quality_gate"], "pass")
        self.assertTrue(result["gate_passed"])
        self.assertTrue(result["compile_ok"])
        self.assertEqual(result["validation"]["errors"], [])
        # 第二次调用必须携带确定性校验反思
        second_messages = chat.call_args_list[1][0][2]["messages"]
        last_user = second_messages[-1]["content"]
        self.assertIn("确定性校验", last_user)
        self.assertIn("必须修复", last_user)
        self.assertIn("受力", last_user)
        # 第一轮问题应记录在 trace 中
        self.assertIn("force-no-arrow", result["trace"][0]["errors"])

    def test_compile_log_diagnosis_fed_back(self):
        simple_code = r"""\begin{tikzpicture}
\draw (0,0) -- (2,0);
\end{tikzpicture}"""
        question = r"""\begin{problem}{2024}{G}{全国卷}{2}{运动的描述}小车做匀速直线运动。\end{problem}"""
        chat = MagicMock()
        chat.side_effect = [(_reply(simple_code, "v1"), "url"), (_reply(simple_code, "v2"), "url")]
        compile_mock = MagicMock()
        compile_mock.side_effect = [_compile_fail(), _compile_ok()]
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch("services.diagram_agent.PiDiagramAgent._visual_validate",
                   return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""}):
            result = self.agent.run_turn(
                user_instruction="重新画图",
                current_question_latex=question,
                max_self_healing_attempts=1,
            )
        self.assertEqual(result["attempts"], 2)
        second_messages = chat.call_args_list[1][0][2]["messages"]
        last_user = second_messages[-1]["content"]
        self.assertIn("编译诊断", last_user)
        self.assertIn("semi colon", last_user.lower())
        self.assertTrue(result["compile_ok"])

    def test_compile_failure_without_log_still_fails_gate(self):
        simple_code = r"""\begin{tikzpicture}
\draw (0,0) -- (2,0);
\end{tikzpicture}"""
        question = r"""\begin{problem}{2024}{G}{全国卷}{4}{运动的描述}小车匀速运动。\end{problem}"""
        chat = MagicMock(return_value=(_reply(simple_code, "v1"), "url"))
        compile_mock = MagicMock(return_value=SimpleNamespace(
            ok=False, b64_png=None, log="", stdout="",
            error_kind="COMPILE_ERROR", error_line=None, duration_s=0.01, source_line_offset=0,
        ))
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch("services.diagram_agent.PiDiagramAgent._visual_validate",
                   return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""}):
            result = self.agent.run_turn(
                user_instruction="重画",
                current_question_latex=question,
                max_self_healing_attempts=0,
            )
        self.assertEqual(result["quality_gate"], "fail")
        self.assertIn("compile.fatal", [e["code"] for e in result["validation"]["errors"]])
        self.assertFalse(result["should_autosync"])

    def test_missing_xelatex_fails_gate_and_blocks_autosync(self):
        simple_code = r"""\begin{tikzpicture}
\draw (0,0) -- (2,0);
\end{tikzpicture}"""
        question = r"""\begin{problem}{2024}{G}{全国卷}{5}{运动的描述}小车匀速运动。\end{problem}"""
        chat = MagicMock(return_value=(_reply(simple_code, "v1"), "url"))
        compile_mock = MagicMock(return_value=SimpleNamespace(
            ok=False, b64_png=None, log="", stdout="",
            error_kind="MISSING_XELATEX", error_line=None, duration_s=0.0, source_line_offset=0,
        ))
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch("services.diagram_agent.PiDiagramAgent._visual_validate",
                   return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""}):
            result = self.agent.run_turn(
                user_instruction="重画",
                current_question_latex=question,
                max_self_healing_attempts=0,
            )
        # F1（P0）：未编译成功即视为未通过编译级验收，门禁 fail 且不允许自动同步
        self.assertEqual(result["quality_gate"], "fail")
        self.assertFalse(result["should_autosync"])
        self.assertTrue(result["updated_latex"])  # 仍提供候选代码供人工手动应用
        self.assertIn("compile.unavailable", [w["code"] for w in result["validation"]["warnings"]])

    def test_reflection_uses_fragment_line_numbers(self):
        simple_code = r"""\begin{tikzpicture}
\draw (0,0) -- (2,0);
\end{tikzpicture}"""
        question = r"""\begin{problem}{2024}{G}{全国卷}{6}{运动的描述}小车匀速运动。\end{problem}"""
        chat = MagicMock()
        chat.side_effect = [(_reply(simple_code, "v1"), "url"), (_reply(simple_code, "v2"), "url")]
        fail_log = "./abc123.tex:29: ! LaTeX Error: Missing $ inserted.\nl.29 \\node {F_N}\n"
        compile_mock = MagicMock(side_effect=[
            SimpleNamespace(ok=False, b64_png=None, log=fail_log, stdout="",
                            error_kind="COMPILE_ERROR", error_line=29, duration_s=0.01,
                            source_line_offset=27),
            _compile_ok(),
        ])
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch("services.diagram_agent.PiDiagramAgent._visual_validate",
                   return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""}):
            self.agent.run_turn(
                user_instruction="重画",
                current_question_latex=question,
                max_self_healing_attempts=1,
            )
        last_user = chat.call_args_list[1][0][2]["messages"][-1]["content"]
        self.assertIn("第 2 行", last_user)
        self.assertIn("片段内行号", last_user)

    def test_best_candidate_retained_over_worse_last_attempt(self):
        # 第一轮：可编译但有两个可读性警告（needs_review）
        first_code = r"""\begin{tikzpicture}
\draw[thick,-latex] (0,0) -- (2,0);
\node at (0.2,0.8) {$F_1$};
\node at (0.22,0.81) {$F_2$};
\node at (1.0,0.8) {$F_3$};
\node at (1.02,0.81) {$F_4$};
\end{tikzpicture}"""
        # 第二轮：缺受力箭头（fail）
        second_code = TIKZ_FORCE_INVALID
        question = r"""\begin{problem}{2024}{G}{全国卷}{3}{平衡力学}物体受力分析。\end{problem}"""
        chat = MagicMock()
        chat.side_effect = [
            (_reply(first_code, "较好版本", "第一版可用"), "url"),
            (_reply(second_code, "较差版本", "第二版变差"), "url"),
        ]
        compile_mock = MagicMock(return_value=_compile_ok())
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch("services.diagram_agent.PiDiagramAgent._visual_validate",
                   return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""}):
            result = self.agent.run_turn(
                user_instruction="画出物体受力",
                current_question_latex=question,
                max_self_healing_attempts=1,
            )
        self.assertEqual(result["attempts"], 2)
        # 最终结果应保留第一轮较好候选，而不是最后一次更差结果
        self.assertEqual(result["quality_gate"], "needs_review")
        self.assertEqual(result["validation_score"], 88)
        self.assertIn("第一版可用", result["agent_message"].content)
        self.assertEqual(result["trace"][-1]["gate"], "fail")

    def test_gate_fail_blocks_autosync(self):
        result, chat, _compile, _visual = self.run_agent(
            replies=[_reply(TIKZ_FORCE_INVALID)],
            compiles=[_compile_ok()],
            max_attempts=1,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["quality_gate"], "fail")
        self.assertFalse(result["gate_passed"])
        self.assertFalse(result["should_autosync"])
        self.assertTrue(result["updated_latex"])  # 仍提供候选代码，由 UI 决定是否手动写入
        self.assertIn("force-no-arrow", [e["code"] for e in result["validation"]["errors"]])

    def test_no_tikz_reply_returns_text_turn(self):
        reply = MagicMock()
        reply.status_code = 200
        reply.json.return_value = {"choices": [{"message": {"content": "请问您希望修改哪一部分？"}}]}
        chat = MagicMock(return_value=(reply, "url"))
        with patch("services.diagram_agent.post_chat_completion", chat):
            result = self.agent.run_turn(
                user_instruction="嗯？",
                current_question_latex=QUESTION_INCLINE,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["tikz_code"], "")
        self.assertFalse(result["should_autosync"])
        self.assertIn("修改", result["agent_message"].content)

    def test_structural_failure_skips_compile(self):
        broken = r"""\begin{tikzpicture}
\node at (0,0) {$F_N$;
\end{tikzpicture}"""
        result, chat, compile_mock, _visual = self.run_agent(
            replies=[_reply(broken, "坏结构"), _reply(TIKZ_FORCE_VALID, "修好")],
            compiles=[_compile_ok()],
        )
        self.assertEqual(compile_mock.call_count, 1)  # 第一轮结构硬失败不编译
        self.assertEqual(result["quality_gate"], "pass")
        first_trace = result["trace"][0]
        self.assertFalse(first_trace["compile_ok"])
        self.assertTrue(any("brace" in c or "math" in c for c in first_trace["errors"]))


class TestHarnessVisualCheck(HarnessTestBase):
    def test_visual_mismatch_merges_into_validation(self):
        visual = {
            "ok": False, "score": 40,
            "issues": ["图中缺少斜面角度弧", "法线未画成虚线"],
            "summary": "图形与题意不一致", "skipped": False,
        }
        result, _chat, _compile, visual_mock = self.run_agent(
            replies=[_reply(TIKZ_FORCE_VALID)],
            compiles=[_compile_ok()],
            visual=visual,
            max_attempts=0,
        )
        visual_mock.assert_called_once()
        self.assertFalse(result["visual_check"].get("skipped"))
        self.assertEqual(result["visual_check"]["score"], 40)
        visual_codes = [i["code"] for i in result["validation"]["issues"] if str(i["code"]).startswith("visual.")]
        self.assertTrue(visual_codes)
        self.assertEqual(result["quality_gate"], "needs_review")


class TestHarnessSchemaAndSkills(HarnessTestBase):
    def test_result_and_message_schema(self):
        result, _chat, _compile, _visual = self.run_agent(
            replies=[_reply(TIKZ_FORCE_VALID)],
            compiles=[_compile_ok()],
            max_attempts=0,
        )
        for key in (
            "ok", "agent_message", "updated_latex", "tikz_code", "preview_b64",
            "compile_ok", "compile_error", "compile_log", "compile_error_kind",
            "validation", "validation_score", "quality_gate", "gate_passed",
            "should_autosync", "fixes_applied", "attempts", "trace", "visual_check",
            "activated_skills",
        ):
            self.assertIn(key, result)
        msg = result["agent_message"]
        for attr in (
            "validation", "validation_score", "quality_gate", "fixes_applied",
            "attempts", "compile_log", "compile_error_kind", "visual_check",
        ):
            self.assertTrue(hasattr(msg, attr))
        restored = AgentMessage.from_dict(msg.to_dict())
        self.assertEqual(restored.validation_score, msg.validation_score)
        self.assertEqual(restored.quality_gate, msg.quality_gate)
        self.assertEqual(restored.compile_log, msg.compile_log)

    def test_agent_message_legacy_dict_compat(self):
        legacy = {
            "role": "assistant", "content": "旧会话", "thought": "",
            "tikz_code": "\\begin{tikzpicture}\\end{tikzpicture}",
            "preview_b64": "", "compile_ok": True, "compile_error": "",
            "activated_skills": [],
        }
        msg = AgentMessage.from_dict(legacy)
        self.assertEqual(msg.validation, {})
        self.assertEqual(msg.quality_gate, "")
        self.assertEqual(msg.fixes_applied, [])

    def test_auditor_skill_is_registered_and_augments_prompt(self):
        registry = SkillRegistry()
        auditor = registry.get_skill("physics-diagram-auditor")
        self.assertIsNotNone(auditor)
        instr = auditor.get_system_instructions()
        self.assertIn("质量门禁", instr)
        pre = auditor.pre_process("把斜面倾角调小至 37 度，受力分析增加摩擦力", "")
        joined = "\n".join(pre.get("detected_actions", []))
        self.assertIn("37", joined)

    def test_plugin_skills_loaded(self):
        registry = SkillRegistry()
        ids = [s.skill_id for s in registry.list_all_skills()]
        self.assertIn("latex-compile-diagnostics", ids)
        self.assertIn("diagram-visual-qa", ids)
        self.assertIn("latex-compile-diagnostics", registry.plugin_skills)

    def test_disable_plugins_env(self):
        import os
        from services.agent_skills.plugin_loader import load_plugins

        registry = SkillRegistry(load_plugin_skills=False)
        with patch.dict(os.environ, {"PI_AGENT_DISABLE_PLUGINS": "1"}):
            loaded = load_plugins(registry)
        self.assertEqual(loaded, [])


class TestCompileLogLineMapping(unittest.TestCase):
    """真实日志行号 → TikZ 片段内行号 的换算。"""

    def test_relativize_document_lines_to_fragment(self):
        from services.diagram_agent import _relativize_tex_log

        log = (
            "./abcd1234.tex:29: ! LaTeX Error: Missing $ inserted.\n"
            "l.29 \\node at (1,1) {F_N};\n"
            "./abcd1234.tex:40: ! Package pgf Error: No shape named `A' is known.\n"
        )
        source = "\\begin{tikzpicture}\n\\node at (1,1) {F_N};\n\\end{tikzpicture}"
        mapped = _relativize_tex_log(log, 27, source)
        self.assertIn(":2:", mapped)
        self.assertIn("l.2 ", mapped)
        # 超出片段行号范围的行保持原样（不产生 0/负行号）
        self.assertIn(":40:", mapped)

    def test_relativize_no_offset_keeps_log(self):
        from services.diagram_agent import _relativize_tex_log

        log = "./x.tex:29: ! LaTeX Error: Missing $ inserted."
        self.assertEqual(_relativize_tex_log(log, 0, ""), log)


class TestDiagramToolsCompilePath(HarnessTestBase):
    def test_compile_with_log_fallback_shape(self):
        with patch("services.diagram_agent.DiagramTools.compile_preview",
                   return_value=(True, "b64", None)):
            with patch.dict("sys.modules", {"utils.tikz_ops": None}):
                result = DiagramTools.compile_with_log("\\begin{tikzpicture}\\end{tikzpicture}", ".")
        self.assertTrue(result.ok)
        self.assertEqual(result.b64_png, "b64")

    def test_apply_tikz_replaces_existing_block(self):
        updated = DiagramTools.apply_tikz_to_latex(QUESTION_INCLINE, "\\begin{tikzpicture}\\end{tikzpicture}")
        self.assertIn("\\begin{tikzpicture}\\end{tikzpicture}", updated)
        self.assertEqual(updated.count("\\begin{tikzpicture}"), 1)


if __name__ == "__main__":
    unittest.main()
