# -*- coding: utf-8 -*-
"""驱动崩溃（DRIVER_CRASH）恢复 / 「🔄 重新编译」状态保护测试。

背景：xdvipdfmx.exe 在 Windows 上瞬时崩溃（0xC0000417，MSVCR100.dll）时，
xelatex 日志里没有 ``!`` 开头的 LaTeX 错误，只有 ``driver return code`` 一类输出。
本测试文件用 unittest.mock 伪造 LLM 与编译层，不依赖网络、真实 xelatex 或 Streamlit 运行时。

覆盖：
1. ``_compile_failure_issues`` 的 ``compile.driver-crash`` 分支（中文可操作 hint）；
2. 旧式 ``COMPILE_ERROR`` + 崩溃日志的兼容升级；
3. ``run_turn``：首次 DRIVER_CRASH → 第二轮 OK 的恢复路径（attempts>=2）；
4. ``run_turn``：连续 DRIVER_CRASH 不抛异常、保留 error_kind、给出环境故障说明；
5. ``_build_reflection``：驱动崩溃时不再堆砌语法修复清单；
6. ``_short_compile_error`` / ``_render_gate_notice`` 的面向用户中文文案；
7. ``_compile_tikz_with_log`` / ``_normalize_compile_result`` 的 error_kind 透传；
8. 纯函数 ``_should_preserve_after_recompile_failure``：只有「原本已成功 + 本次驱动崩溃」
   才保留消息既有可用状态。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.diagram_agent import (
    AgentMessage,
    DiagramTools,
    DRIVER_CRASH_USER_MESSAGE,
    ERROR_KIND_DRIVER_CRASH,
    PiDiagramAgent,
    _as_compile_fields,
    _compile_failure_issues,
    detect_driver_crash_kind,
    is_driver_crash_kind,
    normalize_compile_error_kind,
)

# 与真实事件日志一致的驱动崩溃签名（xetex 日志尾部）
CRASH_LOG = (
    "This is XeTeX, Version 3.141592653-2.6-0.999995 (TeX Live 2023)\n"
    "!  ==> Fatal error occurred, no output PDF file produced!\n"
    "Error -1073740777 (driver return code) generating output;\n"
    "  file tikz.pdf may not be valid.\n"
)

SYNTAX_ERROR_LOG = "./abcd1234.tex:7: ! Package tikz Error: Giving up on this path. Did you forget a semi colon?\n"

QUESTION_INCLINE = r"""\begin{problem}{2024}{G}{全国卷}{1}{平衡力学}
如图所示，质量为 $m$ 的滑块静止在倾角为 $30^\circ$ 的斜面上，求滑块受到的摩擦力大小。
\begin{tikzpicture}
\draw[thick] (0,0) -- (4,0) -- (4,2) -- cycle;
\end{tikzpicture}
\end{problem}"""

TIKZ_VALID = r"""\begin{tikzpicture}
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


def _compile_ok(b64: str = "recovered_png") -> SimpleNamespace:
    return SimpleNamespace(
        ok=True, b64_png=b64, log="", stdout="", error_kind="OK",
        error_line=None, duration_s=0.01, source_line_offset=0,
        driver_exit_code=None, compile_retries=1,
    )


def _compile_driver_crash(
    log: str = CRASH_LOG,
    retries: int = 1,
    exit_code: int = -1073740777,
) -> SimpleNamespace:
    return SimpleNamespace(
        ok=False, b64_png=None, log=log, stdout="",
        error_kind=ERROR_KIND_DRIVER_CRASH, error_line=None, duration_s=0.02,
        source_line_offset=0, driver_exit_code=exit_code, compile_retries=retries,
    )


class DriverCrashTestBase(unittest.TestCase):
    def setUp(self):
        self.agent = PiDiagramAgent()
        cfg = patch.object(
            self.agent,
            "_get_api_config",
            return_value={"api_key": "k", "base_url": "https://api.local/v1", "model_name": "m"},
        )
        cfg.start()
        self.addCleanup(cfg.stop)

    def run_agent(self, replies, compiles, max_attempts=1):
        sequence = replies if len(replies) > 1 else replies * 6
        chat = MagicMock()
        chat.side_effect = [(reply, "url") for reply in sequence]
        compile_mock = MagicMock()
        compile_mock.side_effect = compiles if len(compiles) > 1 else compiles * 6
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock):
            result = self.agent.run_turn(
                user_instruction="磁感线应改成均向下弯曲，自然平滑",
                current_question_latex=QUESTION_INCLINE,
                max_self_healing_attempts=max_attempts,
                visual_check=False,
            )
        return result, chat, compile_mock


class TestDriverCrashDetection(unittest.TestCase):
    def test_detect_driver_crash_from_log(self):
        self.assertEqual(detect_driver_crash_kind(CRASH_LOG), ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(detect_driver_crash_kind(SYNTAX_ERROR_LOG), "")
        self.assertEqual(detect_driver_crash_kind(""), "")
        self.assertTrue(is_driver_crash_kind("driver_crash"))  # 大小写不敏感
        self.assertFalse(is_driver_crash_kind("COMPILE_ERROR"))

    def test_normalize_upgrades_legacy_compile_error(self):
        self.assertEqual(
            normalize_compile_error_kind("COMPILE_ERROR", compile_ok=False, compile_log=CRASH_LOG),
            ERROR_KIND_DRIVER_CRASH,
        )
        # 明确的类别永远优先，不被日志误改
        self.assertEqual(
            normalize_compile_error_kind(ERROR_KIND_DRIVER_CRASH, compile_ok=False, compile_log=""),
            ERROR_KIND_DRIVER_CRASH,
        )
        self.assertEqual(
            normalize_compile_error_kind("TIMEOUT", compile_ok=False, compile_log=CRASH_LOG),
            "TIMEOUT",
        )
        self.assertEqual(normalize_compile_error_kind("", compile_ok=True), "OK")

    def test_compile_failure_issues_driver_crash_branch(self):
        issues = _compile_failure_issues(ERROR_KIND_DRIVER_CRASH, CRASH_LOG)
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["level"], "error")
        self.assertEqual(issue["code"], "compile.driver-crash")
        self.assertEqual(issue["category"], "compile")
        self.assertFalse(issue["fixable"])
        self.assertIn("xdvipdfmx", issue["message"])
        self.assertIn("不是 TikZ 语法错误", issue["message"])
        # hint 必须是可操作的中文建议
        self.assertIn("重新编译", issue["hint"])
        self.assertIn("简化图形", issue["hint"])

    def test_compile_failure_issues_upgrades_from_log_only(self):
        issues = _compile_failure_issues("COMPILE_ERROR", CRASH_LOG)
        self.assertEqual(issues[0]["code"], "compile.driver-crash")
        self.assertNotIn("未从日志中解析出明确的 LaTeX 错误", issues[0]["message"])

    def test_as_compile_fields_passes_driver_metadata(self):
        fields = _as_compile_fields(_compile_driver_crash())
        self.assertFalse(fields["ok"])
        self.assertEqual(fields["error_kind"], ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(fields["driver_exit_code"], -1073740777)
        self.assertEqual(fields["compile_retries"], 1)

    def test_as_compile_fields_legacy_tuple_detects_crash(self):
        fields = _as_compile_fields((False, None, CRASH_LOG))
        self.assertEqual(fields["error_kind"], ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(fields["compile_retries"], 0)


class TestDriverCrashReflection(unittest.TestCase):
    def setUp(self):
        self.agent = PiDiagramAgent()

    def test_reflection_explains_environment_not_syntax(self):
        validation = {
            "issues": [{
                "level": "error", "code": "compile.driver-crash",
                "message": "编译器输出驱动 xdvipdfmx 崩溃", "hint": "重试重新编译",
            }],
            "errors": [], "warnings": [],
        }
        reflection = self.agent._build_reflection(
            validation=validation,
            compile_ok=False,
            compile_log=CRASH_LOG,
            error_kind=ERROR_KIND_DRIVER_CRASH,
            visual={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""},
            previous_tikz="",
            question_context="斜面倾角 30 度",
        )
        self.assertIn("环境", reflection)
        self.assertIn("xdvipdfmx", reflection)
        self.assertIn("0xC0000417", reflection)
        self.assertIn("不是 TikZ 语法错误", reflection)
        # 要求给出更稳健的等效画法 / 原样重发，而不是堆砌语法修复清单
        self.assertIn("更稳健", reflection)
        self.assertIn("几乎原样重发", reflection)
        self.assertNotIn("确定性校验结果：得分", reflection)
        # 仍然保留下一步输出格式约束
        self.assertIn("===TIKZ===", reflection)

    def test_compile_error_reflection_still_uses_log_diagnosis(self):
        reflection = self.agent._build_reflection(
            validation={"issues": [], "errors": [], "warnings": []},
            compile_ok=False,
            compile_log=SYNTAX_ERROR_LOG,
            error_kind="COMPILE_ERROR",
            visual={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""},
            previous_tikz="",
            question_context="",
        )
        self.assertIn("XeLaTeX 编译诊断", reflection)
        self.assertIn("semi colon", reflection.lower())


class TestRunTurnDriverCrash(DriverCrashTestBase):
    def test_crash_then_recover_keeps_error_kind_and_retries(self):
        result, chat, compile_mock = self.run_agent(
            replies=[_reply(TIKZ_VALID, "第一版"), _reply(TIKZ_VALID, "第二版")],
            compiles=[_compile_driver_crash(), _compile_ok()],
            max_attempts=1,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["compile_ok"])
        self.assertGreaterEqual(result["attempts"], 2)
        self.assertEqual(compile_mock.call_count, 2)
        self.assertEqual(result["preview_b64"], "recovered_png")
        self.assertEqual(result["compile_error_kind"], "OK")
        self.assertFalse(result["compile_error"])

        # 第一轮 trace 必须保留 DRIVER_CRASH 与重试信息
        first_trace = result["trace"][0]
        self.assertEqual(first_trace["compile_error_kind"], ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(first_trace["driver_exit_code"], -1073740777)
        self.assertGreaterEqual(first_trace["compile_retries"], 1)

        # 第二轮反思必须解释「环境问题，不是语法错误」
        second_payload = chat.call_args_list[1][0][2]
        last_user = second_payload["messages"][-1]["content"]
        self.assertIn("环境", last_user)
        self.assertIn("不是 TikZ 语法错误", last_user)
        self.assertNotIn("未从日志中解析出明确的 LaTeX 错误", last_user)
        self.assertNotIn("【必须修复的错误", last_user)

    def test_persistent_crash_does_not_raise_and_keeps_state(self):
        result, _chat, compile_mock = self.run_agent(
            replies=[_reply(TIKZ_VALID)],
            compiles=[_compile_driver_crash()],
            max_attempts=0,
        )
        self.assertTrue(result["ok"])  # Agent 不抛异常/不整体失败
        self.assertFalse(result["compile_ok"])
        self.assertEqual(result["quality_gate"], "fail")
        self.assertFalse(result["should_autosync"])
        self.assertEqual(result["compile_error_kind"], ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(compile_mock.call_count, 1)
        msg = result["agent_message"]
        self.assertIsInstance(msg, AgentMessage)
        self.assertEqual(msg.compile_error_kind, ERROR_KIND_DRIVER_CRASH)
        self.assertIn("xdvipdfmx", msg.compile_error)
        self.assertNotIn("未从日志中解析出明确的 LaTeX 错误", msg.compile_error)
        # 最终说明中要有面向用户的环境故障解释
        self.assertIn("环境", msg.content)
        self.assertIn("不是插图代码的语法错误", msg.content)
        # 校验报告里必须出现可操作的 compile.driver-crash
        codes = [item.get("code") for item in (result["validation"].get("errors") or [])]
        self.assertIn("compile.driver-crash", codes)
        self.assertTrue(result["updated_latex"])  # 仍保留候选供手动应用


class TestUiDriverCrashHelpers(unittest.TestCase):
    def _ui(self):
        from services import diagram_agent_ui as ui

        return ui

    def test_short_compile_error_friendly_chinese(self):
        ui = self._ui()
        message = ui._short_compile_error({
            "ok": False,
            "error_kind": ERROR_KIND_DRIVER_CRASH,
            "error": "原始日志：Error -1073740777 (driver return code) generating output;",
            "log": CRASH_LOG,
        })
        self.assertIn("xdvipdfmx", message)
        self.assertIn("不是 TikZ 语法错误", message)
        self.assertIn("重新编译", message)
        self.assertIn("环境", message)

    def test_short_compile_error_keeps_other_branches(self):
        ui = self._ui()
        self.assertEqual(
            ui._short_compile_error({"ok": False, "error_kind": "COMPILE_ERROR", "error": "第一条错误"}),
            "第一条错误",
        )

    def test_normalize_compile_result_preserves_driver_crash(self):
        ui = self._ui()
        raw = SimpleNamespace(
            ok=False, b64_png=None, log=CRASH_LOG, error_kind="COMPILE_ERROR",
            driver_exit_code=-1073740777, compile_retries=1,
        )
        normalized = ui._normalize_compile_result(raw)
        self.assertEqual(normalized["error_kind"], ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(normalized["driver_exit_code"], -1073740777)
        self.assertEqual(normalized["compile_retries"], 1)
        # T1 已给出 DRIVER_CRASH 时原样透传
        raw2 = SimpleNamespace(
            ok=False, b64_png=None, log=CRASH_LOG,
            error_kind=ERROR_KIND_DRIVER_CRASH, compile_retries=1,
        )
        self.assertEqual(ui._normalize_compile_result(raw2)["error_kind"], ERROR_KIND_DRIVER_CRASH)

    def test_compile_tikz_with_log_passes_error_kind(self):
        ui = self._ui()
        fake_result = SimpleNamespace(
            ok=False, b64_png=None, log=CRASH_LOG,
            error_kind=ERROR_KIND_DRIVER_CRASH, compile_retries=1,
            driver_exit_code=-1073740777,
        )
        with patch.object(DiagramTools, "compile_with_log", return_value=fake_result):
            result = ui._compile_tikz_with_log("\\begin{tikzpicture}\\end{tikzpicture}", ".")
        self.assertEqual(result["error_kind"], ERROR_KIND_DRIVER_CRASH)
        self.assertFalse(result["ok"])
        self.assertEqual(result["log"], CRASH_LOG)

    def test_gate_notice_uses_environment_wording(self):
        ui = self._ui()
        fake_st = MagicMock()
        with patch.object(ui, "st", fake_st):
            ui._render_gate_notice("fail", 20, False, ERROR_KIND_DRIVER_CRASH)
        fake_st.error.assert_called_once()
        text = fake_st.error.call_args[0][0]
        self.assertIn("编译器输出驱动崩溃", text)
        self.assertIn("环境问题", text)
        self.assertNotIn("编译未通过", text)

        normal_st = MagicMock()
        with patch.object(ui, "st", normal_st):
            ui._render_gate_notice("fail", 20, False, "COMPILE_ERROR")
        normal_text = normal_st.error.call_args[0][0]
        self.assertIn("编译未通过", normal_text)


class TestRecompileStateProtection(unittest.TestCase):
    """「🔄 重新编译」在持续 DRIVER_CRASH 时不得清空既有成功状态（纯函数级覆盖）。"""

    def setUp(self):
        from services.diagram_agent_ui import _should_preserve_after_recompile_failure

        self.should_preserve = _should_preserve_after_recompile_failure

    def test_preserve_when_previously_ok_and_now_driver_crash(self):
        crash = {"ok": False, "error_kind": ERROR_KIND_DRIVER_CRASH, "log": CRASH_LOG}
        self.assertTrue(self.should_preserve(True, "old_preview_b64", crash))

    def test_no_preserve_for_normal_compile_error(self):
        syntax = {"ok": False, "error_kind": "COMPILE_ERROR", "log": SYNTAX_ERROR_LOG}
        self.assertFalse(self.should_preserve(True, "old_preview_b64", syntax))

    def test_no_preserve_when_message_was_never_successful(self):
        crash = {"ok": False, "error_kind": ERROR_KIND_DRIVER_CRASH, "log": CRASH_LOG}
        self.assertFalse(self.should_preserve(False, "old_preview_b64", crash))
        self.assertFalse(self.should_preserve(False, "", crash))
        self.assertFalse(self.should_preserve(True, "", crash))  # 没有可用预览图不算“可用结果”

    def test_no_preserve_for_successful_recompile(self):
        success = {"ok": True, "b64_png": "new_preview", "error_kind": "OK"}
        self.assertFalse(self.should_preserve(True, "old_preview_b64", success))

    def test_preserve_legacy_compile_error_with_crash_log(self):
        legacy = {"ok": False, "error_kind": "COMPILE_ERROR", "log": CRASH_LOG}
        self.assertTrue(self.should_preserve(True, "old_preview_b64", legacy))

    def test_message_state_is_not_mutated_when_preserved(self):
        """模拟按钮逻辑：preserve 为真时不应改写任何可用字段。"""
        from services import diagram_agent_ui as ui

        msg = AgentMessage(
            role="assistant",
            content="已修正磁感线",
            tikz_code=TIKZ_VALID,
            preview_b64="old_preview_b64",
            compile_ok=True,
            compile_error="",
            quality_gate="pass",
            validation={"issues": []},
            validation_score=96,
            compile_error_kind="OK",
        )
        crash_result = {"ok": False, "error_kind": ERROR_KIND_DRIVER_CRASH, "log": CRASH_LOG}
        preserve = ui._should_preserve_after_recompile_failure(
            _msg_attr(msg, "compile_ok", False),
            _msg_attr(msg, "preview_b64", ""),
            crash_result,
        )
        self.assertTrue(preserve)
        if not preserve:  # pragma: no cover - 防御
            ui._set_msg_field(msg, "compile_ok", False)
            ui._set_msg_field(msg, "quality_gate", "fail")
        # 保留路径下这些字段必须原样不变
        self.assertTrue(msg.compile_ok)
        self.assertEqual(msg.preview_b64, "old_preview_b64")
        self.assertEqual(msg.quality_gate, "pass")
        self.assertEqual(msg.validation_score, 96)
        self.assertEqual(msg.compile_error_kind, "OK")


def _msg_attr(msg, name, default=None):
    from services.diagram_agent_ui import _msg_attr as ui_msg_attr

    return ui_msg_attr(msg, name, default)


if __name__ == "__main__":
    unittest.main()
