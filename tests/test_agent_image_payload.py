# -*- coding: utf-8 -*-
"""参考图片（粘贴 / 拖拽 / 选择）回传与多模态接线测试（纯 mock，不依赖浏览器/网络）。

覆盖：
1. ``_extract_bridge_images``：合法 png/jpeg/webp、非法 mime、坏 base64、magic bytes 不符、
   超大、超数量、总大小上限、异常输入 → 过滤正确且绝不抛异常；
2. ``_resolve_text_bridge_payload``：图片-only 发送时默认指令生效；
3. ``_serialize_message`` / ``_restore_message`` 对 images 的往返一致与旧会话兼容；
4. ``_run_turn_with_reference_images``：run_turn 支持 reference_images 时透传，
   未支持时自动省略该参数（不抛 TypeError）；
5. ``_build_modern_agent_capsule_html``：生成的 HTML 含 paste 监听 / images 字段 /
   缩略图行 / 测试钩子（纯字符串断言，不需要浏览器）；
6. ``_render_user_images``：对话历史缩略图使用 data_url，仅对文字做 html.escape。
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

from services import diagram_agent_ui as ui
from services.diagram_agent import AgentMessage


def _png_bytes(size=16):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * max(0, size - 8)


def _jpeg_bytes(size=16):
    return b"\xff\xd8\xff\xe0" + b"\x00" * max(0, size - 4)


def _webp_bytes(size=16):
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * max(0, size - 12)


def _data_url(mime, raw):
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def _image(name, mime, raw):
    return {"name": name, "mime": mime, "data_url": _data_url(mime, raw)}


def _payload(*items):
    return {"kind": "text", "text": "", "images": list(items)}


class TestExtractBridgeImages(unittest.TestCase):
    def test_accepts_supported_formats(self):
        payload = _payload(
            _image("a.png", "image/png", _png_bytes()),
            _image("b.jpg", "image/jpeg", _jpeg_bytes()),
            _image("c.webp", "image/webp", _webp_bytes()),
        )
        images = ui._extract_bridge_images(payload)
        self.assertEqual(len(images), 3)
        self.assertEqual([item["mime"] for item in images], ["image/png", "image/jpeg", "image/webp"])
        self.assertEqual([item["name"] for item in images], ["a.png", "b.jpg", "c.webp"])
        self.assertTrue(images[0]["data_url"].startswith("data:image/png;base64,"))

    def test_jpg_alias_normalized_to_jpeg(self):
        payload = _payload(_image("a.jpg", "image/jpg", _jpeg_bytes()))
        images = ui._extract_bridge_images(payload)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mime"], "image/jpeg")

    def test_filters_invalid_mime(self):
        payload = _payload(
            {"name": "x.gif", "mime": "image/gif",
             "data_url": "data:image/gif;base64," + base64.b64encode(b"GIF89a").decode("ascii")},
            _image("ok.png", "image/png", _png_bytes()),
        )
        images = ui._extract_bridge_images(payload)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["name"], "ok.png")

    def test_filters_bad_base64(self):
        payload = _payload({"name": "bad", "mime": "image/png",
                            "data_url": "data:image/png;base64,@@@@not-base64@@@@"})
        self.assertEqual(ui._extract_bridge_images(payload), [])
        # 缺少 base64 声明
        self.assertEqual(ui._extract_bridge_images(_payload({"name": "no", "data_url": "data:image/png,"})), [])

    def test_filters_magic_bytes_mismatch(self):
        # 声明 png，内容却是 jpeg → 必须过滤
        payload = _payload(_image("fake.png", "image/png", _jpeg_bytes()))
        self.assertEqual(ui._extract_bridge_images(payload), [])
        # 声明 webp，内容却是 png
        payload = _payload(_image("fake.webp", "image/webp", _png_bytes()))
        self.assertEqual(ui._extract_bridge_images(payload), [])

    def test_filters_oversized_single_image(self):
        big = _image("big.png", "image/png", _png_bytes(256))
        images = ui._extract_bridge_images(_payload(big), max_bytes=64)
        self.assertEqual(images, [])
        # 上限内则保留
        ok = _image("ok.png", "image/png", _png_bytes(16))
        self.assertEqual(len(ui._extract_bridge_images(_payload(ok), max_bytes=64)), 1)

    def test_limits_count(self):
        payload = _payload(*[_image("p%d.png" % i, "image/png", _png_bytes()) for i in range(6)])
        images = ui._extract_bridge_images(payload)
        self.assertEqual(len(images), 4)
        self.assertEqual([item["name"] for item in images], ["p0.png", "p1.png", "p2.png", "p3.png"])

    def test_respects_total_bytes(self):
        # 每张 24 字节，总上限 40 → 只保留第一张
        payload = _payload(_image("a.png", "image/png", _png_bytes(24)),
                           _image("b.png", "image/png", _png_bytes(24)))
        images = ui._extract_bridge_images(payload, max_total_bytes=40)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["name"], "a.png")

    def test_name_fallback(self):
        no_name = {"data_url": _data_url("image/png", _png_bytes())}
        images = ui._extract_bridge_images(_payload(no_name))
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0]["name"])
        long_name = _image("名" * 400, "image/png", _png_bytes())
        self.assertLessEqual(len(ui._extract_bridge_images(_payload(long_name))[0]["name"]), 160)

    def test_never_raises_on_weird_payloads(self):
        weird = [
            None, "", 0, "text", [], {},
            {"images": "not-a-list"},
            {"images": None},
            {"images": [None, 1, "x", b"y"]},
            {"images": [{"data_url": 12345}]},
            {"images": [{"name": None, "data_url": None}]},
            {"images": [{"data_url": "data:image/png;base64,"}]},
        ]
        for payload in weird:
            with self.subTest(payload=repr(payload)[:60]):
                self.assertEqual(ui._extract_bridge_images(payload), [])


class TestTextBridgePayload(unittest.TestCase):
    def test_image_only_uses_default_instruction(self):
        payload = _payload(_image("a.png", "image/png", _png_bytes()))
        text, images, trigger = ui._resolve_text_bridge_payload(payload)
        self.assertEqual(text, ui._DEFAULT_IMAGE_INSTRUCTION)
        self.assertIn("参考", text)
        self.assertEqual(len(images), 1)
        self.assertTrue(trigger)

    def test_text_and_images(self):
        payload = {"kind": "text", "text": " 磁感线应改成均向下弯曲 ", "images": [_image("a.png", "image/png", _png_bytes())]}
        text, images, trigger = ui._resolve_text_bridge_payload(payload)
        self.assertEqual(text, "磁感线应改成均向下弯曲")
        self.assertEqual(len(images), 1)
        self.assertTrue(trigger)

    def test_text_only_still_works(self):
        text, images, trigger = ui._resolve_text_bridge_payload({"kind": "text", "text": "改磁感线"})
        self.assertEqual(text, "改磁感线")
        self.assertEqual(images, [])
        self.assertTrue(trigger)

    def test_invalid_images_only_does_not_trigger(self):
        payload = _payload({"name": "x.gif", "data_url": "data:image/gif;base64,R0lGODlh"})
        text, images, trigger = ui._resolve_text_bridge_payload(payload)
        self.assertEqual(text, "")
        self.assertEqual(images, [])
        self.assertFalse(trigger)
        self.assertFalse(ui._resolve_text_bridge_payload(None)[2])
        self.assertFalse(ui._resolve_text_bridge_payload({"kind": "text", "text": "   "})[2])


class TestMessageImageRoundTrip(unittest.TestCase):
    def _images(self):
        return [
            _image("课本图.png", "image/png", _png_bytes()),
            _image("截图.jpg", "image/jpeg", _jpeg_bytes()),
        ]

    def test_user_message_serialize_restore_roundtrip(self):
        images = self._images()
        msg = AgentMessage(role="user", content="按参考图修正磁感线")
        setattr(msg, "images", images)

        serialized = ui._serialize_message(msg)
        self.assertIn("images", serialized)
        self.assertEqual(serialized["images"], images)
        # 可以 JSON 序列化（会话落盘的前提）
        import json

        json.dumps(serialized)

        restored = ui._restore_message(serialized)
        self.assertEqual(restored.role, "user")
        self.assertEqual(restored.content, "按参考图修正磁感线")
        self.assertEqual(ui._msg_images(restored), images)
        # 二次序列化稳定
        self.assertEqual(ui._serialize_message(restored)["images"], images)

    def test_legacy_session_without_images(self):
        restored = ui._restore_message({"role": "user", "content": "旧会话消息"})
        self.assertEqual(ui._msg_images(restored), [])
        self.assertEqual(ui._serialize_message(restored)["images"], [])

    def test_assistant_message_has_empty_images(self):
        assistant = AgentMessage(role="assistant", content="已修正", tikz_code="\\begin{tikzpicture}\\end{tikzpicture}")
        serialized = ui._serialize_message(assistant)
        self.assertEqual(serialized["images"], [])
        self.assertEqual(ui._msg_images(assistant), [])

    def test_invalid_images_in_session_are_dropped(self):
        raw = {
            "role": "user",
            "content": "带坏图",
            "images": [{"name": "x.gif", "data_url": "data:image/gif;base64,R0lGODlh"}, "junk", None],
        }
        restored = ui._restore_message(raw)
        self.assertEqual(ui._msg_images(restored), [])

    def test_build_user_agent_message_keeps_images(self):
        images = self._images()
        msg = ui._build_user_agent_message("参考图片修正", images)
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "参考图片修正")
        self.assertEqual(ui._msg_images(msg), images)


class _CapturingAgent:
    """run_turn 接受 **kwargs：应收到 reference_images。"""

    def __init__(self):
        self.calls = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "agent_message": None}


class _LegacyAgent:
    """run_turn 尚未支持 reference_images：不得因新参数抛 TypeError。"""

    def __init__(self):
        self.calls = []

    def run_turn(self, user_instruction, current_question_latex, history=None):
        self.calls.append({
            "user_instruction": user_instruction,
            "current_question_latex": current_question_latex,
            "history": history,
        })
        return {"ok": True, "agent_message": None}


class TestRunTurnReferenceImages(unittest.TestCase):
    def _images(self):
        return [_image("a.png", "image/png", _png_bytes())]

    def test_passes_reference_images_when_supported(self):
        agent = _CapturingAgent()
        images = self._images()
        result = ui._run_turn_with_reference_images(
            agent,
            reference_images=images,
            user_instruction="参考图片修正",
            current_question_latex="\\begin{tikzpicture}\\end{tikzpicture}",
            history=[],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(agent.calls[0]["reference_images"], images)
        self.assertEqual(agent.calls[0]["user_instruction"], "参考图片修正")
        self.assertEqual(agent.calls[0]["current_question_latex"], "\\begin{tikzpicture}\\end{tikzpicture}")

    def test_omits_reference_images_when_agent_not_supporting(self):
        agent = _LegacyAgent()
        result = ui._run_turn_with_reference_images(
            agent,
            reference_images=self._images(),
            user_instruction="参考图片修正",
            current_question_latex="",
            history=[],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(agent.calls[0]["user_instruction"], "参考图片修正")
        self.assertNotIn("reference_images", agent.calls[0])

    def test_no_images_does_not_add_parameter(self):
        agent = _CapturingAgent()
        ui._run_turn_with_reference_images(
            agent,
            reference_images=None,
            user_instruction="纯文字",
            current_question_latex="",
            history=[],
        )
        self.assertNotIn("reference_images", agent.calls[0])

    def test_invalid_images_do_not_add_parameter(self):
        agent = _CapturingAgent()
        ui._run_turn_with_reference_images(
            agent,
            reference_images=[{"name": "x.gif", "data_url": "data:image/gif;base64,R0lGODlh"}],
            user_instruction="纯文字",
            current_question_latex="",
            history=[],
        )
        self.assertNotIn("reference_images", agent.calls[0])


class TestCapsuleHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = ui._build_modern_agent_capsule_html(
            "bridge_prompt_test", "bridge_btn_test", "vendor/some-model:latest"
        )

    def test_contains_paste_listener(self):
        self.assertIn('addEventListener("paste"', self.html)
        self.assertIn("clipboardData", self.html)
        self.assertIn("getAsFile", self.html)
        self.assertIn("preventDefault", self.html)

    def test_contains_drag_and_drop(self):
        self.assertIn('"dragover"', self.html)
        self.assertIn('"drop"', self.html)
        self.assertIn("dataTransfer", self.html)
        self.assertIn("drag-over", self.html)

    def test_contains_plus_and_file_input(self):
        self.assertIn('id="plusBtn"', self.html)
        self.assertIn('id="fileInput"', self.html)
        self.assertIn('accept="image/png,image/jpeg,image/webp"', self.html)
        self.assertIn('multiple', self.html)

    def test_contains_thumbnail_row_and_remove_buttons(self):
        self.assertIn('id="attachRow"', self.html)
        self.assertIn('id="attachThumbs"', self.html)
        self.assertIn("attach-thumb", self.html)
        self.assertIn("attach-remove", self.html)
        self.assertIn("参考图 ×", self.html)

    def test_send_payload_contains_images(self):
        self.assertIn('kind: "text", text: val, images: images', self.html)
        self.assertIn("MC_MAX_IMAGES = 4", self.html)
        self.assertIn("MC_MAX_DATA_URL_CHARS = 2600000", self.html)
        self.assertIn('toDataURL("image/jpeg", 0.82)', self.html)
        self.assertIn("1280", self.html)

    def test_has_test_hooks(self):
        self.assertIn("__mcCapsuleAttachImages", self.html)
        self.assertIn("__mcCapsuleImages", self.html)
        self.assertIn("__mcCapsuleClearImages", self.html)

    def test_placeholder_mentions_paste(self):
        self.assertIn("粘贴", self.html)
        self.assertIn("拖拽", self.html)

    def test_existing_behaviour_preserved(self):
        self.assertIn('id="promptArea"', self.html)
        self.assertIn('id="syncCheck"', self.html)
        self.assertIn('id="micBtn"', self.html)
        self.assertIn('id="sendBtn"', self.html)
        self.assertIn("handleSend", self.html)
        self.assertIn("toggleVoice", self.html)
        self.assertIn('"Enter"', self.html)

    def test_model_name_is_escaped(self):
        html = ui._build_modern_agent_capsule_html("b", "c", "bad<name>")
        self.assertIn("&lt;name&gt;", html)
        self.assertNotIn("<name>", html)


class TestRenderUserImages(unittest.TestCase):
    def test_renders_data_url_and_escapes_name(self):
        msg = AgentMessage(role="user", content="按图修正")
        setattr(msg, "images", [_image("<b>课本图</b>.png", "image/png", _png_bytes())])
        fake_st = MagicMock()
        with patch.object(ui, "st", fake_st):
            ui._render_user_images(msg)
        fake_st.markdown.assert_called_once()
        markup = fake_st.markdown.call_args[0][0]
        self.assertIn("data:image/png;base64,", markup)
        self.assertIn("&lt;b&gt;课本图&lt;/b&gt;.png", markup)
        self.assertNotIn("<b>课本图</b>.png", markup)
        self.assertTrue(fake_st.markdown.call_args[1].get("unsafe_allow_html"))

    def test_skips_when_no_images(self):
        fake_st = MagicMock()
        with patch.object(ui, "st", fake_st):
            ui._render_user_images(AgentMessage(role="user", content="无图"))
        fake_st.markdown.assert_not_called()

    def test_limits_to_four_images(self):
        msg = AgentMessage(role="user", content="多图")
        setattr(msg, "images", [_image("p%d.png" % i, "image/png", _png_bytes()) for i in range(6)])
        fake_st = MagicMock()
        with patch.object(ui, "st", fake_st):
            ui._render_user_images(msg)
        markup = fake_st.markdown.call_args[0][0]
        self.assertEqual(markup.count("<img "), 4)


if __name__ == "__main__":
    unittest.main()
