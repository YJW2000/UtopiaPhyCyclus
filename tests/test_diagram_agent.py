# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from services.diagram_agent import PiDiagramAgent, DiagramTools, AgentMessage


def _fake_compile_ok(preview="fake_preview_b64"):
    return SimpleNamespace(
        ok=True, b64_png=preview, log="", stdout="",
        error_kind="OK", error_line=None, duration_s=0.01,
    )


def _fake_compile_fail(log="main.tex:7: ! Package tikz Error: Giving up on this path. Did you forget a semi colon?"):
    return SimpleNamespace(
        ok=False, b64_png=None, log=log, stdout="",
        error_kind="COMPILE_ERROR", error_line=7, duration_s=0.01,
    )


class TestDiagramAgent(unittest.TestCase):
    def setUp(self):
        self.sample_latex_with_tikz = r"""\begin{problem}{2024}{G}{全国卷}{1}{平衡力学}
如图所示，质量为 $m$ 的滑块静止在倾角为 $\theta$ 的斜面上。
\begin{tikzpicture}
\draw[thick] (0,0) -- (4,0) -- (4,2) -- cycle;
\draw[fill=gray!30] (2,1) rectangle (2.5,1.5);
\end{tikzpicture}
求滑块受到的摩擦力大小。
\end{problem}
\begin{answer}
$f = mg \sin\theta$
\end{answer}
\begin{solutions}
对滑块进行受力分析，沿斜面方向受力平衡，故 $f = mg \sin\theta$。
\end{solutions}"""

        self.sample_latex_without_tikz = r"""\begin{problem}{2024}{G}{全国卷}{2}{牛顿运动定律}
一辆小车在水平地面上做匀加速直线运动，已知加速度为 $a$。
\begin{choices}
\choice{{$A$}}
\choice{{$B$}}
\choice{{$C$}}
\choice{{$D$}}
\end{choices}
\end{problem}
\begin{answer}
A
\end{answer}
\begin{solutions}
略
\end{solutions}"""

    def test_extract_tikz_blocks(self):
        blocks = DiagramTools.extract_tikz_blocks(self.sample_latex_with_tikz)
        self.assertEqual(len(blocks), 1)
        self.assertIn(r"\begin{tikzpicture}", blocks[0]["code"])
        self.assertIn(r"\end{tikzpicture}", blocks[0]["code"])

        no_blocks = DiagramTools.extract_tikz_blocks(self.sample_latex_without_tikz)
        self.assertEqual(len(no_blocks), 0)

    def test_apply_tikz_to_latex_replace_existing(self):
        new_tikz = r"""\begin{tikzpicture}[scale=1.2]
\draw[thick] (0,0) -- (5,0) -- (5,2.5) -- cycle;
\node at (2.5,1) {新滑块};
\end{tikzpicture}"""
        updated = DiagramTools.apply_tikz_to_latex(self.sample_latex_with_tikz, new_tikz, target_index=0)
        self.assertIn("新滑块", updated)
        self.assertNotIn("2.5,1.5", updated)
        self.assertIn(r"\begin{problem}", updated)
        self.assertIn(r"\end{solutions}", updated)

    def test_apply_tikz_to_latex_insert_new(self):
        new_tikz = r"""\begin{tikzpicture}
\draw (0,0) rectangle (2,1);
\end{tikzpicture}"""
        updated = DiagramTools.apply_tikz_to_latex(self.sample_latex_without_tikz, new_tikz)
        self.assertIn(new_tikz, updated)
        # Should be placed before \begin{choices}
        self.assertTrue(updated.index(new_tikz) < updated.index(r"\begin{choices}"))

    def test_clean_response_structured(self):
        agent = PiDiagramAgent()
        raw_reply = """===THOUGHT===
滑块在斜面上受重力、垂直支持力与沿斜面摩擦力，调整倾角为30度。
===TIKZ===
\\begin{tikzpicture}
\\draw (0,0) -- (3,0);
\\end{tikzpicture}
===EXPLANATION===
已将斜面倾角更新为 30 度并补全受力分析。"""
        thought, tikz, expl = agent._clean_response(raw_reply)
        self.assertIn("调整倾角为30度", thought)
        self.assertIn("\\begin{tikzpicture}", tikz)
        self.assertIn("已将斜面倾角更新为 30 度", expl)

    def test_clean_response_fallback(self):
        agent = PiDiagramAgent()
        raw_reply = """```latex
\\begin{tikzpicture}
\\draw[->] (0,0) -- (1,1);
\\end{tikzpicture}
```
已经为您生成了受力示意图。"""
        thought, tikz, expl = agent._clean_response(raw_reply)
        self.assertIn("\\begin{tikzpicture}", tikz)
        self.assertNotIn("```", tikz)

    def test_agent_message_serialization(self):
        msg = AgentMessage(
            role="assistant",
            content="修复完成",
            thought="思考过程",
            tikz_code="\\begin{tikzpicture}\\end{tikzpicture}",
            preview_b64="fake_b64",
            compile_ok=True,
            compile_error="",
        )
        d = msg.to_dict()
        self.assertEqual(d["role"], "assistant")
        self.assertEqual(d["thought"], "思考过程")
        msg_restored = AgentMessage.from_dict(d)
        self.assertEqual(msg_restored.content, "修复完成")
        self.assertEqual(msg_restored.tikz_code, "\\begin{tikzpicture}\\end{tikzpicture}")

    @patch("services.diagram_agent.post_chat_completion")
    @patch("services.diagram_agent.DiagramTools.compile_with_log")
    @patch("services.diagram_agent.PiDiagramAgent._visual_validate")
    def test_agent_run_turn_success(self, mock_visual, mock_compile, mock_chat):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": """===THOUGHT===
根据用户语音指示调整斜面。
===TIKZ===
\\begin{tikzpicture}
\\draw (0,0) -- (2,0);
\\end{tikzpicture}
===EXPLANATION===
已根据您的语音需求完成斜面修正。"""
                }
            }]
        }
        mock_chat.return_value = (mock_resp, "url")
        mock_compile.return_value = _fake_compile_ok()
        mock_visual.return_value = {"ok": True, "score": 90, "issues": [], "summary": "ok", "skipped": False}

        agent = PiDiagramAgent()
        with patch.object(agent, "_get_api_config", return_value={"api_key": "test_key", "base_url": "https://api.test/v1", "model_name": "test_model"}):
            result = agent.run_turn(
                user_instruction="请把斜面调小一点",
                current_question_latex=self.sample_latex_with_tikz,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["compile_ok"])
        self.assertEqual(result["preview_b64"], "fake_preview_b64")
        self.assertIn("已根据您的语音需求完成斜面修正", result["agent_message"].content)
        self.assertIn("fake_preview_b64", result["agent_message"].preview_b64)
        # Harness v2 结果字段
        self.assertIn("validation", result)
        self.assertIn("quality_gate", result)
        self.assertIn("trace", result)
        self.assertGreaterEqual(result["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
