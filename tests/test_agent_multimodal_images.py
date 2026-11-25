# -*- coding: utf-8 -*-
"""Agent 多模态参考图片（reference_images）测试。

背景：用户可以随修改指令附带参考图（课本插图/截图），Agent 需要以 OpenAI 兼容的
多模态 content（image_url / data URL）把图片随「本轮用户消息」发给模型；若网关/模型
不支持图片输入（返回非 200），本轮自动去掉图片、仅用文字重试一次。

本测试全部使用 unittest.mock 伪造 LLM 与编译层：
- 不联网（不真实调用 .env 配置的 API）；
- 不编译（不调用 xelatex）；
- 不依赖 Streamlit 运行时。
"""

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.diagram_agent import (
    REFERENCE_IMAGES_DEFAULT_INSTRUCTION,
    AgentMessage,
    PiDiagramAgent,
    _build_reference_image_content,
    _normalize_reference_images,
)

# 1x1 透明 PNG（67 字节），用于构造合法的 data URL
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_DATA_URL = "data:image/png;base64," + PNG_B64


def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


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


def _http_error(status_code: int = 400, text: str = "Unsupported content type: image_url") -> MagicMock:
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


class TestNormalizeReferenceImages(unittest.TestCase):
    """_normalize_reference_images 的边界/容错（绝不抛异常）。"""

    def test_accepts_valid_png_and_generates_defaults(self):
        images = _normalize_reference_images([{"data_url": PNG_DATA_URL}])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mime"], "image/png")
        self.assertEqual(images[0]["name"], "reference-1.png")
        self.assertEqual(images[0]["data_url"], PNG_DATA_URL)

    def test_preserves_name_and_supports_jpeg_webp(self):
        jpeg_url = _data_url("image/jpeg", b"fake-jpeg-bytes" * 8)
        webp_url = _data_url("image/webp", b"fake-webp-bytes" * 8)
        images = _normalize_reference_images([
            {"name": "课本插图", "data_url": jpeg_url},
            {"name": "截图2.png", "data_url": webp_url},
        ])
        self.assertEqual([item["mime"] for item in images], ["image/jpeg", "image/webp"])
        self.assertEqual(images[0]["name"], "课本插图")
        self.assertEqual(images[1]["name"], "截图2.png")
        # 缺省 name 统一为 reference-N.png（与契约/UI 回传桥一致）
        defaulted = _normalize_reference_images([{"data_url": jpeg_url}])
        self.assertEqual(defaulted[0]["name"], "reference-1.png")

    def test_uppercase_data_url_header_normalized(self):
        images = _normalize_reference_images([{"data_url": "data:IMAGE/PNG;base64," + PNG_B64}])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mime"], "image/png")

    def test_jpg_alias_normalized_to_jpeg(self):
        jpg_url = "data:image/jpg;base64," + base64.b64encode(b"jpg-alias-bytes" * 8).decode()
        images = _normalize_reference_images([{"data_url": jpg_url}])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mime"], "image/jpeg")

    def test_rejects_bad_base64(self):
        bad = [
            {"data_url": "data:image/png;base64,@@@not-base64@@@"},
            {"data_url": "data:image/png;base64,"},
            {"data_url": "data:image/png;base64,abc"},  # 非法 padding / 长度
        ]
        self.assertEqual(_normalize_reference_images(bad), [])

    def test_rejects_unsupported_mime(self):
        bad_urls = [
            "data:image/gif;base64," + base64.b64encode(b"gif-bytes").decode(),
            "data:text/plain;base64," + base64.b64encode(b"text").decode(),
            "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode(),
            "http://example.com/a.png",
            PNG_B64,  # 缺 data URL 头
        ]
        images = _normalize_reference_images([{"data_url": url} for url in bad_urls])
        self.assertEqual(images, [])

    def test_rejects_oversize_each(self):
        images = _normalize_reference_images([{"data_url": PNG_DATA_URL}], max_bytes_each=10)
        self.assertEqual(images, [])

    def test_respects_max_images(self):
        many = [{"data_url": PNG_DATA_URL} for _ in range(6)]
        images = _normalize_reference_images(many, max_images=4)
        self.assertEqual(len(images), 4)
        self.assertEqual([item["name"] for item in images],
                         ["reference-1.png", "reference-2.png", "reference-3.png", "reference-4.png"])

    def test_respects_total_bytes(self):
        # 单张 67 字节；max_total=100 → 只收第一张
        images = _normalize_reference_images(
            [{"data_url": PNG_DATA_URL}, {"data_url": PNG_DATA_URL}],
            max_bytes_each=100,
            max_total=100,
        )
        self.assertEqual(len(images), 1)

    def test_drops_malformed_entries(self):
        raw = [
            None,
            "not-a-dict",
            42,
            {"data_url": 123},
            {"name": "no-data-url.png"},
            {},
            {"name": "x.png", "data_url": "https://example.com/x.png"},
            {"name": "good.png", "data_url": PNG_DATA_URL},
        ]
        images = _normalize_reference_images(raw)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["name"], "good.png")

    def test_non_list_container_returns_empty(self):
        for value in (None, "", "abc", 123, {"data_url": PNG_DATA_URL}):
            self.assertEqual(_normalize_reference_images(value), [])

    def test_never_raises_on_exotic_input(self):
        weird = [{"data_url": PNG_DATA_URL}, object(), {"data_url": b"bytes"}]
        for value in (weird, [{"data_url": PNG_DATA_URL}], None):
            self.assertIsInstance(_normalize_reference_images(value), list)


class TestBuildReferenceImageContent(unittest.TestCase):
    def test_no_images_keeps_plain_string(self):
        content = _build_reference_image_content("提示词", [])
        self.assertIsInstance(content, str)
        self.assertEqual(content, "提示词")

    def test_images_build_openai_multimodal_array(self):
        images = _normalize_reference_images([
            {"name": "a.png", "data_url": PNG_DATA_URL},
            {"name": "b.png", "data_url": _data_url("image/jpeg", b"jpeg-bytes" * 16)},
        ])
        content = _build_reference_image_content("提示词", images)
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("【用户附带的参考图片】共 2 张", content[0]["text"])
        image_parts = [part for part in content if part.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 2)
        self.assertEqual(image_parts[0]["image_url"]["url"], images[0]["data_url"])
        self.assertEqual(image_parts[0]["image_url"]["detail"], "high")


class RunTurnTestBase(unittest.TestCase):
    def setUp(self):
        self.agent = PiDiagramAgent()
        cfg = patch.object(
            self.agent,
            "_get_api_config",
            return_value={"api_key": "k", "base_url": "https://api.local/v1", "model_name": "m"},
        )
        cfg.start()
        self.addCleanup(cfg.stop)

    def run_agent(
        self,
        chat_side_effect,
        reference_images=None,
        max_attempts=0,
        history=None,
        instruction="磁感线应改成均向下弯曲，自然平滑",
        compile_result=None,
    ):
        """执行 run_turn，返回 (result, captured_payloads, compile_mock)。

        captured_payloads 是每次真正发给 LLM 的 payload 的深拷贝快照——降级逻辑会就地
        改写 messages，若不快照就看不到第一次请求真正带了图片。
        """
        responses = list(chat_side_effect)
        captured = []

        def _fake_post(base_url, headers, payload, timeout=None):
            # 模拟 requests.post(json=payload) 的「调用时即序列化」语义
            captured.append(json.loads(json.dumps(payload, ensure_ascii=False)))
            if not responses:
                raise AssertionError("unexpected extra post_chat_completion call")
            return responses.pop(0)

        chat = MagicMock(side_effect=_fake_post)
        compile_mock = MagicMock(return_value=compile_result or _compile_ok())
        kwargs = {}
        if reference_images is not None:
            kwargs["reference_images"] = reference_images
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", compile_mock), \
             patch(
                 "services.diagram_agent.PiDiagramAgent._visual_validate",
                 return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""},
             ):
            result = self.agent.run_turn(
                user_instruction=instruction,
                current_question_latex=QUESTION_INCLINE,
                history=history or [],
                max_self_healing_attempts=max_attempts,
                **kwargs,
            )
        return result, captured, compile_mock


class TestRunTurnWithImages(RunTurnTestBase):
    def test_first_user_content_is_multimodal_with_image_url(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_reply(), "url")],
            reference_images=[{"name": "课本图.png", "data_url": PNG_DATA_URL}],
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["compile_ok"])
        self.assertEqual(result["reference_image_count"], 1)
        self.assertFalse(result["image_input_degraded"])

        self.assertEqual(len(captured), 1)
        payload = captured[0]
        last_message = payload["messages"][-1]
        self.assertEqual(last_message["role"], "user")
        self.assertIsInstance(last_message["content"], list)
        parts = last_message["content"]

        text_parts = [part for part in parts if part.get("type") == "text"]
        self.assertEqual(len(text_parts), 1)
        self.assertIn("磁感线应改成均向下弯曲，自然平滑", text_parts[0]["text"])
        self.assertIn("【用户附带的参考图片】", text_parts[0]["text"])
        self.assertIn("共 1 张", text_parts[0]["text"])

        image_parts = [part for part in parts if part.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0]["image_url"]["url"], PNG_DATA_URL)
        self.assertEqual(image_parts[0]["image_url"]["detail"], "high")

        # 图片数据不得写入 trace / compile_log 等日志字段
        trace_json = json.dumps(result.get("trace") or [], ensure_ascii=False)
        self.assertNotIn(PNG_B64, trace_json)
        self.assertNotIn(PNG_B64, result.get("compile_log") or "")

    def test_multiple_images_all_attached(self):
        second_url = _data_url("image/jpeg", b"jpeg-reference-bytes" * 16)
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_reply(), "url")],
            reference_images=[
                {"name": "a.png", "data_url": PNG_DATA_URL},
                {"name": "b.jpg", "data_url": second_url},
            ],
        )
        self.assertEqual(result["reference_image_count"], 2)
        content = captured[0]["messages"][-1]["content"]
        image_urls = [
            part["image_url"]["url"] for part in content if part.get("type") == "image_url"
        ]
        self.assertEqual(image_urls, [PNG_DATA_URL, second_url])
        self.assertIn("共 2 张", content[0]["text"])

    def test_no_images_keeps_plain_string_content(self):
        result, captured, _ = self.run_agent(chat_side_effect=[(_reply(), "url")])
        self.assertEqual(result["reference_image_count"], 0)
        self.assertFalse(result["image_input_degraded"])
        content = captured[0]["messages"][-1]["content"]
        self.assertIsInstance(content, str)
        self.assertNotIn("【用户附带的参考图片】", content)
        self.assertIn("磁感线应改成均向下弯曲，自然平滑", content)

    def test_history_replay_does_not_include_images(self):
        history = [
            AgentMessage(
                role="user",
                content="上一轮：把斜面角度改小",
                images=[{"name": "old.png", "mime": "image/png", "data_url": PNG_DATA_URL}],
            ),
            AgentMessage(role="assistant", content="已调整"),
        ]
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_reply(), "url")],
            reference_images=[{"data_url": PNG_DATA_URL}],
            history=history,
        )
        messages = captured[0]["messages"]
        # history 中的用户消息只回放文字
        replayed_user = [m for m in messages if m["role"] == "user" and isinstance(m["content"], str)]
        self.assertTrue(any(m["content"] == "上一轮：把斜面角度改小" for m in replayed_user))
        # 整个请求里只有「本轮用户消息」是多模态数组
        multimodal = [m for m in messages if isinstance(m["content"], list)]
        self.assertEqual(len(multimodal), 1)
        self.assertIs(multimodal[0], messages[-1])
        self.assertEqual(result["reference_image_count"], 1)

    def test_image_only_instruction_uses_default_text(self):
        _result, captured, _ = self.run_agent(
            chat_side_effect=[(_reply(), "url")],
            reference_images=[{"data_url": PNG_DATA_URL}],
            instruction="",
        )
        content = captured[0]["messages"][-1]["content"]
        self.assertIn(REFERENCE_IMAGES_DEFAULT_INSTRUCTION, content[0]["text"])

    def test_invalid_images_are_filtered_and_no_multimodal_content(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_reply(), "url")],
            reference_images=[
                {"data_url": "data:image/gif;base64," + base64.b64encode(b"gif").decode()},
                {"data_url": "https://example.com/x.png"},
            ],
        )
        self.assertEqual(result["reference_image_count"], 0)
        content = captured[0]["messages"][-1]["content"]
        self.assertIsInstance(content, str)


class TestImageFallback(RunTurnTestBase):
    def test_non_200_with_images_retries_text_only_and_succeeds(self):
        result, captured, compile_mock = self.run_agent(
            chat_side_effect=[(_http_error(400), "url"), (_reply(), "url")],
            reference_images=[{"data_url": PNG_DATA_URL}],
            max_attempts=0,
        )
        # 降级后成功，不抛异常
        self.assertTrue(result["ok"])
        self.assertTrue(result["compile_ok"])
        self.assertTrue(result["image_input_degraded"])
        self.assertEqual(result["image_fallback_status"], 400)
        self.assertEqual(result["reference_image_count"], 1)
        # 降级重试不消耗自愈轮次：1 次评估尝试、2 次 HTTP 调用
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(captured), 2)
        self.assertEqual(compile_mock.call_count, 1)

        first_payload, second_payload = captured
        self.assertIn("image_url", json.dumps(first_payload, ensure_ascii=False))
        self.assertNotIn("image_url", json.dumps(second_payload, ensure_ascii=False))
        self.assertNotIn(PNG_B64, json.dumps(second_payload, ensure_ascii=False))

        second_last = second_payload["messages"][-1]
        self.assertEqual(second_last["role"], "user")
        self.assertIsInstance(second_last["content"], str)
        self.assertIn("不支持图片输入", second_last["content"])
        self.assertIn("磁感线应改成均向下弯曲，自然平滑", second_last["content"])

        # 面向用户的说明里也要注明降级
        self.assertIn("不支持图片输入", result["agent_message"].content)
        self.assertTrue(result["trace"][0]["image_input_degraded"])
        self.assertEqual(result["trace"][0]["image_fallback_status"], 400)

    def test_degraded_success_not_counted_as_extra_self_healing_round(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_http_error(422), "url"), (_reply(), "url")],
            reference_images=[{"data_url": PNG_DATA_URL}],
            max_attempts=1,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["image_fallback_status"], 422)
        # 只发生 1 次「评估」，降级本身不算自愈轮次；否则会有第 3 次请求
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(captured), 2)

    def test_persistent_non_200_returns_existing_error_path(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_http_error(400), "url"), (_http_error(400), "url")],
            reference_images=[{"data_url": PNG_DATA_URL}],
            max_attempts=0,
        )
        self.assertFalse(result["ok"])
        self.assertIn("Agent 请求失败 [400]", result["error"])
        self.assertIsNone(result["agent_message"])
        self.assertEqual(len(captured), 2)
        self.assertTrue(result["image_input_degraded"])

    def test_no_images_non_200_does_not_retry(self):
        result, captured, _ = self.run_agent(
            chat_side_effect=[(_http_error(400), "url")],
            max_attempts=0,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(len(captured), 1)
        self.assertFalse(result["image_input_degraded"])

    def test_degradation_does_not_mutate_already_sent_payload(self):
        """降级重试不得就地改写第一次请求对象（否则日志/审计会失真）。"""
        chat = MagicMock(side_effect=[(_http_error(400), "url"), (_reply(), "url")])
        with patch("services.diagram_agent.post_chat_completion", chat), \
             patch("services.diagram_agent.DiagramTools.compile_with_log", MagicMock(return_value=_compile_ok())), \
             patch(
                 "services.diagram_agent.PiDiagramAgent._visual_validate",
                 return_value={"skipped": True, "ok": True, "score": 0, "issues": [], "summary": ""},
             ):
            result = self.agent.run_turn(
                user_instruction="磁感线应改成均向下弯曲，自然平滑",
                current_question_latex=QUESTION_INCLINE,
                max_self_healing_attempts=0,
                reference_images=[{"data_url": PNG_DATA_URL}],
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["image_input_degraded"])
        # 第一次真正发出的请求仍然是多模态数组（没有被降级逻辑事后改写）
        first_payload = chat.call_args_list[0][0][2]
        self.assertIsInstance(first_payload["messages"][-1]["content"], list)
        first_json = json.dumps(first_payload, ensure_ascii=False)
        self.assertIn("image_url", first_json)
        self.assertIn(PNG_B64, first_json)
        # 第二次请求已彻底去掉图片
        second_payload = chat.call_args_list[1][0][2]
        second_json = json.dumps(second_payload, ensure_ascii=False)
        self.assertNotIn("image_url", second_json)
        self.assertNotIn(PNG_B64, second_json)


class TestAgentMessageImages(unittest.TestCase):
    def test_default_images_is_empty_list(self):
        message = AgentMessage(role="user", content="看图修正")
        self.assertEqual(message.images, [])
        self.assertEqual(message.to_dict()["images"], [])

    def test_from_dict_missing_images(self):
        message = AgentMessage.from_dict({"role": "user", "content": "旧会话消息"})
        self.assertEqual(message.images, [])

    def test_from_dict_tolerates_malformed_images(self):
        message = AgentMessage.from_dict({
            "role": "user",
            "content": "带图",
            "images": [
                {"name": "a.png", "mime": "image/png", "data_url": PNG_DATA_URL},
                "not-a-dict",
                42,
                None,
                {"data_url": "https://example.com/x.png"},
                {"name": "b.png"},
                {"name": "c.png", "mime": "image/jpeg", "data_url": _data_url("image/jpeg", b"jpeg")},
            ],
        })
        self.assertEqual(len(message.images), 2)
        self.assertEqual(message.images[0]["data_url"], PNG_DATA_URL)
        self.assertEqual(message.images[0]["mime"], "image/png")
        self.assertEqual(message.images[1]["name"], "c.png")
        self.assertEqual(message.images[1]["mime"], "image/jpeg")

    def test_from_dict_non_list_images(self):
        for value in ("abc", 3, {"data_url": PNG_DATA_URL}, None):
            message = AgentMessage.from_dict({"role": "user", "content": "x", "images": value})
            self.assertEqual(message.images, [])

    def test_serialization_roundtrip_preserves_images(self):
        images = [{"name": "ref.png", "mime": "image/png", "data_url": PNG_DATA_URL}]
        message = AgentMessage(role="user", content="请参考图片", images=list(images))
        restored = AgentMessage.from_dict(message.to_dict())
        self.assertEqual(restored.images, images)
        self.assertEqual(restored.content, "请参考图片")


if __name__ == "__main__":
    unittest.main()
