# -*- coding: utf-8 -*-
"""LLM 调用超时可配置 + 超时/连接失败的友好错误与「去图纯文字」降级测试。

背景（2026-10-20 用户报障）：
    发送消息后 UI 显示
    ``Agent 调用异常: HTTPConnectionPool(host='127.0.0.1', port=8045): Read timed out. (read timeout=90)``
本地 AI 网关（Antigravity Tools，127.0.0.1:8045）卡死/上游拥塞时，一次真实请求可能耗时
157–173s；而 ``services/diagram_agent.py`` 把 requests 超时写死成 ``(10, 90)``，用户无法调整，
超时后还会把原始英文异常直接抛给 UI；并且「带参考图时请求超时」不会触发已有的文字降级。

本测试全部使用 unittest.mock 伪造 LLM 与编译层：
- 不联网（不真实调用 .env 配置的 API）；
- 不编译（不调用 xelatex）；
- 不依赖 Streamlit 运行时。

覆盖：
1. ``resolve_llm_timeouts``：默认 read=240、env 覆盖、clamp、非法值回退、各调用点默认 read；
2. ``run_turn``：ReadTimeout / ConnectionError → 中文可操作错误 + ``error_kind``，不抛异常；
3. 非超时的其它异常 → 统一「Agent 调用异常：<原因>（原始：<类型>）」；
4. 带参考图 + 首次超时 → 第二次请求已无 image_url 且成功降级（不消耗自愈轮次）；
5. 非超时 HTTP 500 仍走原有错误路径（不回归）；
6. 已有最佳候选时超时 → 返回最佳候选而不是把异常抛给 UI；
7. 视觉校验的 LLM 调用同样使用可配置超时（默认 read=75）。
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from services.ai_service import resolve_llm_timeouts
from services.diagram_agent import (
    ERROR_KIND_LLM_CONNECTION,
    ERROR_KIND_LLM_ERROR,
    ERROR_KIND_LLM_TIMEOUT,
    PiDiagramAgent,
    classify_llm_error_kind,
)

# 1x1 透明 PNG（67 字节），用于构造合法的 data URL
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_DATA_URL = "data:image/png;base64," + PNG_B64

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


def _reply(tikz: str = TIKZ_VALID, thought: str = "分析", explanation: str = "已修正插图") -> MagicMock:
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


def _http_error(status_code: int = 500, text: str = "internal server error") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _compile_ok(b64: str = "fake_preview") -> SimpleNamespace:
    return SimpleNamespace(
        ok=True, b64_png=b64, log="", stdout="", error_kind="OK",
        error_line=None, duration_s=0.01, source_line_offset=0,
        driver_exit_code=None, compile_retries=0,
    )


def _compile_fail(log: str = "main.tex:7: ! Package tikz Error: Giving up on this path.") -> SimpleNamespace:
    return SimpleNamespace(
        ok=False, b64_png=None, log=log, stdout="", error_kind="COMPILE_ERROR",
        error_line=7, duration_s=0.01, source_line_offset=0,
        driver_exit_code=None, compile_retries=0,
    )


class EnvTimeoutIsolationMixin(unittest.TestCase):
    """保证每个用例从「未配置 AI_READ_TIMEOUT/AI_CONNECT_TIMEOUT」的基线开始。"""

    _ENV_KEYS = ("AI_READ_TIMEOUT", "AI_CONNECT_TIMEOUT")

    def setUp(self):
        super().setUp()
        removed = {}
        for key in self._ENV_KEYS:
            if key in os.environ:
                removed[key] = os.environ.pop(key)
        self.addCleanup(lambda: os.environ.update(removed))


class TestResolveLlmTimeouts(EnvTimeoutIsolationMixin):
    def test_defaults_are_connect_10_read_240(self):
        self.assertEqual(resolve_llm_timeouts(), (10.0, 240.0))
        # read 默认从原先写死的 90s 提升到 240s
        self.assertGreaterEqual(resolve_llm_timeouts()[1], 240.0)

    def test_env_override(self):
        with patch.dict(os.environ, {"AI_CONNECT_TIMEOUT": "20", "AI_READ_TIMEOUT": "300"}):
            self.assertEqual(resolve_llm_timeouts(), (20.0, 300.0))

    def test_env_override_accepts_float_strings(self):
        with patch.dict(os.environ, {"AI_CONNECT_TIMEOUT": "12.5", "AI_READ_TIMEOUT": "120.5"}):
            self.assertEqual(resolve_llm_timeouts(), (12.5, 120.5))

    def test_clamps_low_values(self):
        with patch.dict(os.environ, {"AI_CONNECT_TIMEOUT": "1", "AI_READ_TIMEOUT": "5"}):
            self.assertEqual(resolve_llm_timeouts(), (3.0, 30.0))

    def test_clamps_high_values(self):
        with patch.dict(os.environ, {"AI_CONNECT_TIMEOUT": "999", "AI_READ_TIMEOUT": "99999"}):
            self.assertEqual(resolve_llm_timeouts(), (60.0, 900.0))

    def test_invalid_values_fall_back_to_defaults(self):
        for bad in ("abc", "", "NaN", "inf", "-5", "0"):
            with patch.dict(os.environ, {"AI_CONNECT_TIMEOUT": bad, "AI_READ_TIMEOUT": bad}):
                self.assertEqual(
                    resolve_llm_timeouts(), (10.0, 240.0),
                    msg=f"非法值 {bad!r} 应回退默认值",
                )

    def test_call_site_specific_defaults(self):
        # 视觉校验沿用原 read=75s 默认，但仍受 clamp 与 env 约束
        self.assertEqual(resolve_llm_timeouts(default_read=75), (10.0, 75.0))
        self.assertEqual(resolve_llm_timeouts(default_connect=5, default_read=120), (5.0, 120.0))

    def test_never_raises(self):
        for value in (None, "", "0", "abc"):
            with patch.dict(os.environ, {"AI_READ_TIMEOUT": value or ""}):
                self.assertIsInstance(resolve_llm_timeouts(), tuple)


class TestClassifyLlmErrorKind(unittest.TestCase):
    def test_timeout_variants(self):
        self.assertEqual(classify_llm_error_kind(requests.exceptions.ReadTimeout("x")), ERROR_KIND_LLM_TIMEOUT)
        self.assertEqual(classify_llm_error_kind(requests.exceptions.ConnectTimeout("x")), ERROR_KIND_LLM_TIMEOUT)
        self.assertEqual(classify_llm_error_kind(requests.exceptions.Timeout("x")), ERROR_KIND_LLM_TIMEOUT)

    def test_connection_error(self):
        self.assertEqual(
            classify_llm_error_kind(requests.exceptions.ConnectionError("refused")),
            ERROR_KIND_LLM_CONNECTION,
        )

    def test_other_exception(self):
        self.assertEqual(classify_llm_error_kind(RuntimeError("boom")), ERROR_KIND_LLM_ERROR)


class RunTurnTestBase(EnvTimeoutIsolationMixin):
    """run_turn 测试基类：mock LLM/编译，捕获每次请求的 payload 与 timeout。"""

    def setUp(self):
        super().setUp()
        self.agent = PiDiagramAgent()
        cfg = patch.object(
            self.agent,
            "_get_api_config",
            return_value={
                "api_key": "k",
                "base_url": "http://127.0.0.1:8045/v1",
                "model_name": "m",
            },
        )
        cfg.start()
        self.addCleanup(cfg.stop)

    def run_agent(
        self,
        chat_side_effect,
        reference_images=None,
        max_attempts=0,
        compile_result=None,
        compile_side_effect=None,
        llm_timeout=None,
    ):
        """执行 run_turn，返回 (result, captured, compile_mock)。

        ``chat_side_effect`` 的元素既可以是 (resp, url) 元组，也可以是异常实例
        （模拟 requests 网络异常）；``captured`` 保存每次真正发出的 payload 与 timeout。
        """
        responses = list(chat_side_effect)
        captured = []

        def _fake_post(base_url, headers, payload, timeout=None):
            # 模拟 requests.post(json=payload) 的「调用时即序列化」语义
            captured.append({
                "payload": json.loads(json.dumps(payload, ensure_ascii=False)),
                "timeout": timeout,
            })
            if not responses:
                raise AssertionError("unexpected extra post_chat_completion call")
            item = responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        chat = MagicMock(side_effect=_fake_post)
        if compile_side_effect is not None:
            compile_mock = MagicMock(side_effect=compile_side_effect)
        else:
            compile_mock = MagicMock(return_value=compile_result or _compile_ok())
        kwargs = {}
        if reference_images is not None:
            kwargs["reference_images"] = reference_images
        if llm_timeout is not None:
            kwargs["llm_timeout"] = llm_timeout
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch(
                 "services.diagram_agent.PiDiagramAgent._visual_validate",
                 return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""},
             ):
            result = self.agent.run_turn(
                user_instruction="磁感线应改成均向下弯曲，自然平滑",
                current_question_latex=QUESTION_INCLINE,
                max_self_healing_attempts=max_attempts,
                **kwargs,
            )
        return result, captured, compile_mock


class TestTimeoutFriendlyErrors(RunTurnTestBase):
    def test_read_timeout_returns_actionable_chinese_error(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[requests.exceptions.ReadTimeout(
                "HTTPConnectionPool(host='127.0.0.1', port=8045): Read timed out. (read timeout=90)"
            )],
        )
        self.assertFalse(result["ok"])
        self.assertIsNone(result["agent_message"])
        self.assertEqual(result["error_kind"], ERROR_KIND_LLM_TIMEOUT)
        error = result["error"]
        # 中文可操作文案：网关 + 等待秒数 + 处理建议
        self.assertIn("响应超时", error)
        self.assertIn("127.0.0.1:8045", error)
        self.assertIn("240", error)  # 默认 read=240 秒
        self.assertIn("重启", error)
        self.assertIn("Agent", error)
        self.assertNotIn("AI_READ_TIMEOUT=300", error)
        # 绝不把原始 requests 英文异常抛给用户
        self.assertNotIn("Read timed out", error)
        self.assertNotIn("HTTPConnectionPool", error)
        self.assertEqual(len(captured), 1)

    def test_error_message_uses_configured_read_timeout(self):
        with patch.dict(os.environ, {"AI_READ_TIMEOUT": "300"}):
            result, _, _ = self.run_agent(
                chat_side_effect=[requests.exceptions.ReadTimeout("timed out")],
            )
        self.assertIn("等待 300 秒", result["error"])

    def test_connection_error_returns_actionable_chinese_error(self):
        result, _, _ = self.run_agent(
            chat_side_effect=[requests.exceptions.ConnectionError(
                "HTTPConnectionPool(host='127.0.0.1', port=8045): Max retries exceeded"
            )],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], ERROR_KIND_LLM_CONNECTION)
        self.assertIn("无法连接 Agent", result["error"])
        self.assertIn("127.0.0.1:8045", result["error"])
        self.assertIn("登录状态", result["error"])
        self.assertNotIn("AI_BASE_URL", result["error"])
        self.assertNotIn("Max retries exceeded", result["error"])

    def test_generic_exception_uses_unified_chinese_message(self):
        result, _, _ = self.run_agent(chat_side_effect=[RuntimeError("boom-secret-stack")])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], ERROR_KIND_LLM_ERROR)
        self.assertIn("Agent 调用异常：", result["error"])
        self.assertIn("原始：RuntimeError", result["error"])
        self.assertNotIn("boom-secret-stack", result["error"])

    def test_http_500_keeps_existing_error_path(self):
        result, _, _ = self.run_agent(chat_side_effect=[(_http_error(500, "upstream down"), "url")])
        self.assertFalse(result["ok"])
        self.assertIn("Agent 请求失败 [500]", result["error"])
        self.assertIsNone(result["agent_message"])

    def test_request_uses_resolved_timeout(self):
        with patch.dict(os.environ, {"AI_CONNECT_TIMEOUT": "15", "AI_READ_TIMEOUT": "300"}):
            _result, captured, _ = self.run_agent(chat_side_effect=[(_reply(), "url")])
        self.assertEqual(captured[0]["timeout"], (15.0, 300.0))

    def test_default_request_timeout_is_read_240(self):
        _result, captured, _ = self.run_agent(chat_side_effect=[(_reply(), "url")])
        self.assertEqual(captured[0]["timeout"], (10.0, 240.0))

    def test_explicit_timeout_override_wins_over_env(self):
        with patch.dict(os.environ, {"AI_READ_TIMEOUT": "300", "AI_CONNECT_TIMEOUT": "15"}):
            _result, captured, _ = self.run_agent(
                chat_side_effect=[(_reply(), "url")],
                llm_timeout=(5, 600),
            )
        self.assertEqual(captured[0]["timeout"], (5.0, 600.0))

    def test_invalid_explicit_timeout_falls_back_to_resolved(self):
        _result, captured, _ = self.run_agent(
            chat_side_effect=[(_reply(), "url")],
            llm_timeout=("not-a-timeout",),
        )
        self.assertEqual(captured[0]["timeout"], (10.0, 240.0))

    def test_timeout_after_best_candidate_returns_best_instead_of_raising(self):
        # 第一次：编译失败 → 有 best（含可用候选代码）；第二次：请求超时 → 应返回 best 而不是抛异常
        result, captured, _ = self.run_agent(
            chat_side_effect=[
                (_reply(TIKZ_VALID), "url"),
                requests.exceptions.ReadTimeout("timed out"),
            ],
            max_attempts=1,
            compile_side_effect=[_compile_fail(), _compile_fail()],
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["compile_ok"])
        self.assertEqual(result["quality_gate"], "fail")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["trace"][-1]["phase"], "llm-error")
        self.assertEqual(len(captured), 2)


class TestImageTimeoutFallback(RunTurnTestBase):
    def test_first_timeout_with_images_degrades_to_text_and_succeeds(self):
        result, captured, compile_mock = self.run_agent(
            chat_side_effect=[
                requests.exceptions.ReadTimeout("read timed out"),
                (_reply(), "url"),
            ],
            reference_images=[{"name": "课本图.png", "data_url": PNG_DATA_URL}],
            max_attempts=0,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["compile_ok"])
        self.assertTrue(result["image_input_degraded"])
        self.assertEqual(result["image_fallback_reason"], ERROR_KIND_LLM_TIMEOUT)
        self.assertIsNone(result["image_fallback_status"])
        self.assertEqual(result["reference_image_count"], 1)
        # 降级重试不消耗自愈轮次：1 次评估尝试、2 次 HTTP 调用、1 次编译
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(captured), 2)
        self.assertEqual(compile_mock.call_count, 1)

        first_payload, second_payload = captured[0]["payload"], captured[1]["payload"]
        self.assertIn("image_url", json.dumps(first_payload, ensure_ascii=False))
        self.assertNotIn("image_url", json.dumps(second_payload, ensure_ascii=False))
        self.assertNotIn(PNG_B64, json.dumps(second_payload, ensure_ascii=False))
        second_last = second_payload["messages"][-1]
        self.assertEqual(second_last["role"], "user")
        self.assertIsInstance(second_last["content"], str)
        # 降级后的 user 消息是纯文字，并明确说明图片已省略
        self.assertIn("请仅依据题干文字", second_last["content"])
        self.assertIn("参考图片已省略", second_last["content"])

        # 面向用户的 explanation 必须注明「超时 + 改用纯文字重试」
        content = result["agent_message"].content
        self.assertIn("超时", content)
        self.assertIn("纯文字重试", content)
        self.assertTrue(result["trace"][0]["image_input_degraded"])
        self.assertEqual(result["trace"][0]["image_fallback_reason"], ERROR_KIND_LLM_TIMEOUT)

    def test_connection_failure_with_images_degrades_to_text_and_succeeds(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[
                requests.exceptions.ConnectionError("connection refused"),
                (_reply(), "url"),
            ],
            reference_images=[{"data_url": PNG_DATA_URL}],
            max_attempts=0,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["image_input_degraded"])
        self.assertEqual(result["image_fallback_reason"], ERROR_KIND_LLM_CONNECTION)
        self.assertNotIn("image_url", json.dumps(captured[1]["payload"], ensure_ascii=False))
        self.assertIn("连接失败", result["agent_message"].content)

    def test_persistent_timeout_with_images_returns_friendly_error(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[
                requests.exceptions.ReadTimeout("read timed out"),
                requests.exceptions.ReadTimeout("read timed out again"),
            ],
            reference_images=[{"data_url": PNG_DATA_URL}],
            max_attempts=0,
        )
        self.assertFalse(result["ok"])
        self.assertIsNone(result["agent_message"])
        self.assertEqual(result["error_kind"], ERROR_KIND_LLM_TIMEOUT)
        self.assertTrue(result["image_input_degraded"])
        self.assertEqual(result["image_fallback_reason"], ERROR_KIND_LLM_TIMEOUT)
        self.assertIn("响应超时", result["error"])
        # 明确告知用户已尝试去掉参考图重试，避免误以为「图片导致卡死」
        self.assertIn("已尝试去掉参考图片", result["error"])
        self.assertEqual(len(captured), 2)

    def test_timeout_without_images_does_not_retry(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[
                requests.exceptions.ReadTimeout("read timed out"),
                (_reply(), "url"),
            ],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], ERROR_KIND_LLM_TIMEOUT)
        self.assertFalse(result["image_input_degraded"])
        # 没有图片可降级 → 只发了一次请求，直接返回友好错误
        self.assertEqual(len(captured), 1)

    def test_non_200_image_degradation_still_uses_http_reason(self):
        """既有行为不回归：非 200 触发降级时 image_fallback_reason=http_status。"""
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_http_error(400, "Unsupported content type: image_url"), "url"), (_reply(), "url")],
            reference_images=[{"data_url": PNG_DATA_URL}],
            max_attempts=0,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["image_input_degraded"])
        self.assertEqual(result["image_fallback_status"], 400)
        self.assertEqual(result["image_fallback_reason"], "http_status")
        self.assertNotIn("image_url", json.dumps(captured[1]["payload"], ensure_ascii=False))
        self.assertIn("不支持图片输入", result["agent_message"].content)


class TestVisualValidateTimeout(EnvTimeoutIsolationMixin):
    def setUp(self):
        super().setUp()
        self.agent = PiDiagramAgent()
        cfg = patch.object(
            self.agent,
            "_get_api_config",
            return_value={"api_key": "k", "base_url": "http://127.0.0.1:8045/v1", "model_name": "m"},
        )
        cfg.start()
        self.addCleanup(cfg.stop)

    def _run_visual(self, timeout=None):
        captured = {}
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"match": true, "score": 88, "issues": [], "summary": "ok"}'
                }
            }]
        }

        def _fake_post(base_url, headers, payload, timeout=None):
            captured["timeout"] = timeout
            return resp, "url"

        with patch("services.diagram_agent.post_chat_completion", side_effect=_fake_post):
            out = self.agent._visual_validate("preview_b64", "题干", "指令", timeout=timeout)
        return out, captured

    def test_visual_validate_uses_resolved_timeout(self):
        with patch.dict(os.environ, {"AI_READ_TIMEOUT": "300"}):
            out, captured = self._run_visual()
        self.assertFalse(out.get("skipped"))
        self.assertEqual(captured["timeout"], (10.0, 300.0))

    def test_visual_validate_default_read_is_still_75(self):
        out, captured = self._run_visual()
        self.assertFalse(out.get("skipped"))
        self.assertEqual(captured["timeout"], (10.0, 75.0))

    def test_visual_validate_explicit_timeout_override(self):
        out, captured = self._run_visual(timeout=(4, 200))
        self.assertFalse(out.get("skipped"))
        self.assertEqual(captured["timeout"], (4.0, 200.0))


if __name__ == "__main__":
    unittest.main()
