# -*- coding: utf-8 -*-
"""
Pi-Agent 物理插图矫正AGENT 对话胶囊组件
(Modern AI Agent Prompt Capsule & Voice Input)

设计参考主流现代 AI Agent (如 Codex / Cursor / Claude / Gemini)：
1. 单体暖陶圆角胶囊容器 (Unified Warm Capsule: #FFFDF9 / 暖陶描边, border-radius 16px)
2. 顶部无边框输入区域：小字占位符与舒适的自然语言交互
3. 底部内嵌工具栏 (纯 CSS Flexbox 绝对水平，拒绝竖向挤裂)：
   - 左侧：+ · ⚡ 自动写回源码 (小字轻量状态)
   - 右侧：⚡ [模型全称] · 🎙️ 语音图标 · 🔄 · 🧹 · ↑ 暖陶圆形发送按钮
4. 双引擎语音：顶层原生语音直通 + API 音频高精转写
"""

import os
import re
import html
import json
import copy
import base64
import time
import inspect
import hashlib
import logging
from typing import Optional, List, Dict, Any, Callable
import streamlit as st
import streamlit.components.v1 as components
from utils.component_theme import theme_agent_capsule

from .diagram_agent import (
    AgentMessage,
    DiagramTools,
    DRIVER_CRASH_USER_MESSAGE,
    ERROR_KIND_DRIVER_CRASH,
    PiDiagramAgent,
    detect_driver_crash_kind,
    is_driver_crash_kind,
    normalize_compile_error_kind,
)
from utils.agent_voice import agent_voice_bridge
from services.local_whisper import transcribe_pcm16 as _whisper_pcm16

logger = logging.getLogger(__name__)

class _DummyColumn:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def __getattr__(self, name):
        return getattr(st, name)

def _safe_columns(spec, **kwargs):
    """防止 Streamlit 列嵌套越界导致崩溃"""
    try:
        return st.columns(spec, **kwargs)
    except Exception:
        count = len(spec) if isinstance(spec, (list, tuple)) else int(spec)
        return [_DummyColumn() for _ in range(count)]


# ===================== 参考图片辅助（粘贴/拖拽/选择 → 回传桥 → Agent 多模态） =====================
# 数据流：
#   胶囊 iframe（HTML/JS）收集图片 → emit({"kind":"text","text":...,"images":[...]})
#   → utils/agent_voice 回传桥 → Python `_extract_bridge_images` 校验
#   → AgentMessage(role="user", images=...) + run_turn(reference_images=...)
# 字符串常量与纯函数放在模块级，便于单测与 node --check 自检（f-string 内不直接写新 JS）。
_DEFAULT_IMAGE_INSTRUCTION = "请参考我粘贴的图片修正当前插图。"

_MAX_CAPSULE_IMAGES = 4
_MAX_IMAGE_BYTES = 3_000_000
_MAX_TOTAL_IMAGE_BYTES = 8_000_000
# 与前端单张 dataURL 上限（约 2.5MB 字符）保持一致的宽松上限，用于会话还原时快速过滤
_MAX_DATA_URL_CHARS = 2_600_000

_IMAGE_DATA_URL_RE = re.compile(
    r"^data:(image/(?:png|jpe?g|webp));base64,([A-Za-z0-9+/=]+)$",
    re.IGNORECASE,
)
_IMAGE_MIME_ALIASES = {"image/jpg": "image/jpeg"}

_IMAGE_MAGIC_CHECKS = {
    "image/png": lambda raw: raw.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda raw: raw.startswith(b"\xff\xd8\xff"),
    "image/webp": lambda raw: raw.startswith(b"RIFF") and raw[8:12] == b"WEBP",
}

# 胶囊内「参考图缩略图行」样式：无图时整行 display:none，不占高度，保持 iframe height=115 的原有视觉
_CAPSULE_ATTACH_CSS = """
      .agent-capsule {
        position: relative;
      }
      /* 有参考图时收紧 padding/gap/textarea，让缩略图行塞进固定的 115px iframe */
      .agent-capsule.has-images {
        padding: 8px 14px 8px 14px;
        gap: 3px;
      }
      .agent-capsule.has-images .prompt-textarea {
        min-height: 32px;
        height: 32px;
      }
      .agent-capsule.has-images .capsule-toolbar {
        padding-top: 2px;
      }
      .agent-capsule.drag-over {
        border-color: #CC785C;
        background: #FFF7EF;
        box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.22);
      }
      .attach-row {
        display: none;
        align-items: center;
        gap: 8px;
        padding-top: 3px;
        min-height: 27px;
        max-height: 30px;
        overflow: hidden;
      }
      .agent-capsule.has-images .attach-row {
        display: flex;
      }
      .attach-thumbs {
        display: flex;
        align-items: center;
        gap: 8px;
        flex: 0 0 auto;
      }
      .attach-thumb-wrap {
        position: relative;
        width: 24px;
        height: 24px;
        flex: 0 0 auto;
      }
      .attach-thumb {
        width: 24px;
        height: 24px;
        object-fit: cover;
        border-radius: 6px;
        border: 1px solid rgba(214, 202, 185, 0.95);
        background: #ffffff;
        display: block;
      }
      .attach-remove {
        position: absolute;
        top: -5px;
        right: -5px;
        width: 13px;
        height: 13px;
        padding: 0;
        border: none;
        border-radius: 50%;
        background: #26231E;
        color: #FFFDF9;
        font-size: 9px;
        line-height: 13px;
        text-align: center;
        cursor: pointer;
        box-shadow: 0 1px 3px rgba(38, 35, 30, 0.35);
      }
      .attach-remove:hover {
        background: #B56345;
      }
      .attach-hint {
        font-size: 11px;
        color: #756E63;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .plus-icon.has-images {
        color: #CC785C;
      }
      .plus-btn {
        background: transparent;
        border: none;
        padding: 0;
        font-family: inherit;
      }
      .capsule-file-input {
        display: none;
      }
      .capsule-toast {
        display: none;
        position: absolute;
        top: 6px;
        right: 14px;
        max-width: 80%;
        padding: 4px 9px;
        border-radius: 8px;
        background: #26231E;
        color: #FFFDF9;
        font-size: 11.5px;
        line-height: 1.3;
        z-index: 5;
        pointer-events: none;
        box-shadow: 0 6px 16px rgba(38, 35, 30, 0.28);
      }
"""

# 胶囊内参考图片 JS（粘贴 / 拖拽 / 「+」选择 / 缩略图 / 测试钩子）。
# 使用原始字符串避免正则转义告警；不含 f-string 占位符。
_CAPSULE_ATTACH_JS = r"""
        // ---------- 参考图片：粘贴 / 拖拽 / 「+」选择 ----------
        const MC_MAX_IMAGES = 4;
        const MC_MAX_DATA_URL_CHARS = 2600000;
        const MC_IMAGE_DATA_URL_RE = /^data:image\/(png|jpeg|webp);base64,/i;
        const MC_PICK_IMAGE_RE = /^image\/(png|jpeg|webp)$/i;
        let mcAttachedImages = [];
        let mcToastTimer = null;

        function mcCapsule() {
          return document.querySelector(".agent-capsule");
        }

        function flashCapsuleToast(message) {
          const el = document.getElementById("capsuleToast");
          if (!el) return;
          el.textContent = message;
          el.style.display = "block";
          if (mcToastTimer) clearTimeout(mcToastTimer);
          mcToastTimer = setTimeout(function () {
            el.style.display = "none";
          }, 2600);
        }

        function updateAttachUi() {
          const capsule = mcCapsule();
          const row = document.getElementById("attachRow");
          const thumbs = document.getElementById("attachThumbs");
          const hint = document.getElementById("attachHint");
          const plus = document.getElementById("plusBtn");
          if (!capsule || !row || !thumbs) return;
          if (!mcAttachedImages.length) {
            capsule.classList.remove("has-images");
            row.style.display = "none";
            thumbs.innerHTML = "";
            if (hint) hint.textContent = "";
            if (plus) plus.classList.remove("has-images");
            return;
          }
          capsule.classList.add("has-images");
          row.style.display = "flex";
          thumbs.innerHTML = "";
          mcAttachedImages.forEach(function (item, idx) {
            const wrap = document.createElement("span");
            wrap.className = "attach-thumb-wrap";
            const img = document.createElement("img");
            img.className = "attach-thumb";
            img.src = item.data_url;
            img.alt = item.name || ("参考图" + (idx + 1));
            img.title = item.name || ("参考图" + (idx + 1));
            const removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "attach-remove";
            removeBtn.textContent = "×";
            removeBtn.title = "移除这张参考图";
            removeBtn.addEventListener("click", function (ev) {
              ev.preventDefault();
              ev.stopPropagation();
              mcAttachedImages.splice(idx, 1);
              updateAttachUi();
            });
            wrap.appendChild(img);
            wrap.appendChild(removeBtn);
            thumbs.appendChild(wrap);
          });
          if (hint) hint.textContent = "🖼 参考图 ×" + mcAttachedImages.length;
          if (plus) plus.classList.add("has-images");
        }

        function mcGuessMime(dataUrl) {
          const m = /^data:(image\/[a-z0-9.+-]+);base64,/i.exec(dataUrl || "");
          return m ? m[1].toLowerCase() : "";
        }

        function addAttachedImage(dataUrl, name) {
          if (typeof dataUrl !== "string" || !MC_IMAGE_DATA_URL_RE.test(dataUrl)) {
            flashCapsuleToast("⚠️ 仅支持 PNG / JPEG / WebP 图片");
            return false;
          }
          if (mcAttachedImages.length >= MC_MAX_IMAGES) {
            flashCapsuleToast("⚠️ 最多 4 张参考图");
            return false;
          }
          if (dataUrl.length > MC_MAX_DATA_URL_CHARS) {
            flashCapsuleToast("⚠️ 单张图片过大（上限约 2.5MB），已跳过");
            return false;
          }
          mcAttachedImages.push({
            name: name || ("参考图" + (mcAttachedImages.length + 1)),
            mime: mcGuessMime(dataUrl),
            data_url: dataUrl,
          });
          updateAttachUi();
          return true;
        }

        function readImageFileAsDataUrl(file) {
          return new Promise(function (resolve, reject) {
            try {
              const reader = new FileReader();
              reader.onload = function () { resolve(String(reader.result || "")); };
              reader.onerror = function () { reject(reader.error || new Error("read failed")); };
              reader.readAsDataURL(file);
            } catch (err) {
              reject(err);
            }
          });
        }

        // 压缩：最长边 > 1280px 等比缩放，输出 JPEG q=0.82；<300KB 的 PNG 保留原 dataURL
        function compressImageDataUrl(dataUrl, mime) {
          return new Promise(function (resolve) {
            try {
              const lowerMime = String(mime || "").toLowerCase();
              if (lowerMime === "image/png" && dataUrl.length < 300 * 1024) {
                resolve(dataUrl);
                return;
              }
              const img = new Image();
              img.onload = function () {
                try {
                  let w = img.naturalWidth || img.width || 0;
                  let h = img.naturalHeight || img.height || 0;
                  if (!w || !h) { resolve(dataUrl); return; }
                  const longest = Math.max(w, h);
                  if (longest > 1280) {
                    const scale = 1280 / longest;
                    w = Math.max(1, Math.round(w * scale));
                    h = Math.max(1, Math.round(h * scale));
                  }
                  const canvas = document.createElement("canvas");
                  canvas.width = w;
                  canvas.height = h;
                  const ctx = canvas.getContext("2d");
                  if (!ctx) { resolve(dataUrl); return; }
                  ctx.fillStyle = "#ffffff";
                  ctx.fillRect(0, 0, w, h);
                  ctx.drawImage(img, 0, 0, w, h);
                  const out = canvas.toDataURL("image/jpeg", 0.82);
                  resolve(out && out.length > 64 ? out : dataUrl);
                } catch (err) {
                  resolve(dataUrl);
                }
              };
              img.onerror = function () { resolve(dataUrl); };
              img.src = dataUrl;
            } catch (err) {
              resolve(dataUrl);
            }
          });
        }

        function ingestImageFiles(files) {
          if (!files || !files.length) return;
          const picked = [];
          for (let i = 0; i < files.length; i++) {
            const file = files[i];
            if (file && MC_PICK_IMAGE_RE.test(String(file.type || ""))) picked.push(file);
          }
          if (!picked.length) {
            flashCapsuleToast("⚠️ 请选择 PNG / JPEG / WebP 图片");
            return;
          }
          const room = MC_MAX_IMAGES - mcAttachedImages.length;
          if (room <= 0) {
            flashCapsuleToast("⚠️ 最多 4 张参考图");
            return;
          }
          if (picked.length > room) {
            flashCapsuleToast("⚠️ 最多 4 张参考图，多余的已忽略");
          }
          picked.slice(0, room).forEach(function (file) {
            readImageFileAsDataUrl(file)
              .then(function (dataUrl) { return compressImageDataUrl(dataUrl, file.type); })
              .then(function (finalUrl) {
                if (finalUrl) addAttachedImage(finalUrl, file.name || "");
              })
              .catch(function (err) {
                console.error("image ingest failed", err);
                flashCapsuleToast("⚠️ 图片读取失败，请换一张试试");
              });
          });
        }

        function extractClipboardImageFiles(clipboardData) {
          const out = [];
          if (!clipboardData) return out;
          const items = clipboardData.items ? Array.prototype.slice.call(clipboardData.items) : [];
          items.forEach(function (item) {
            if (!item || item.kind !== "file") return;
            if (!/^image\//i.test(String(item.type || ""))) return;
            const file = item.getAsFile ? item.getAsFile() : null;
            if (file) out.push(file);
          });
          return out;
        }

        // 测试钩子（供 CDP / 验收脚本注入 dataURL 模拟粘贴）
        window.__mcCapsuleAttachImages = function (urls) {
          const list = Array.isArray(urls) ? urls : (urls ? [urls] : []);
          list.forEach(function (url, idx) {
            addAttachedImage(String(url || ""), "注入参考图" + (idx + 1));
          });
          return mcAttachedImages.map(function (item) { return item.name; });
        };
        window.__mcCapsuleImages = function () {
          return mcAttachedImages.map(function (item) {
            return { name: item.name, mime: item.mime, data_url: item.data_url };
          });
        };
        window.__mcCapsuleClearImages = function () {
          mcAttachedImages = [];
          updateAttachUi();
          return true;
        };

        // ---------- 事件接线 ----------
        const mcPlusBtn = document.getElementById("plusBtn");
        if (mcPlusBtn) {
          mcPlusBtn.addEventListener("click", function (ev) {
            ev.preventDefault();
            const input = document.getElementById("fileInput");
            if (input) input.click();
          });
        }
        const mcFileInput = document.getElementById("fileInput");
        if (mcFileInput) {
          mcFileInput.addEventListener("change", function () {
            ingestImageFiles(mcFileInput.files);
            mcFileInput.value = "";
          });
        }
        const mcPromptArea = document.getElementById("promptArea");
        if (mcPromptArea) {
          mcPromptArea.addEventListener("paste", function (e) {
            const files = extractClipboardImageFiles(e.clipboardData);
            if (!files.length) return;  // 纯文本粘贴保持浏览器默认行为
            e.preventDefault();
            ingestImageFiles(files);
          });
        }
        document.addEventListener("paste", function (e) {
          if (e.defaultPrevented) return;
          if (e.target && e.target.id === "promptArea") return;
          const files = extractClipboardImageFiles(e.clipboardData);
          if (!files.length) return;
          e.preventDefault();
          ingestImageFiles(files);
        });
        const mcCapsuleEl = mcCapsule();
        if (mcCapsuleEl) {
          ["dragenter", "dragover"].forEach(function (evt) {
            mcCapsuleEl.addEventListener(evt, function (e) {
              e.preventDefault();
              e.stopPropagation();
              mcCapsuleEl.classList.add("drag-over");
            });
          });
          ["dragleave", "dragend"].forEach(function (evt) {
            mcCapsuleEl.addEventListener(evt, function (e) {
              e.preventDefault();
              mcCapsuleEl.classList.remove("drag-over");
            });
          });
          mcCapsuleEl.addEventListener("drop", function (e) {
            e.preventDefault();
            e.stopPropagation();
            mcCapsuleEl.classList.remove("drag-over");
            ingestImageFiles(e.dataTransfer ? e.dataTransfer.files : null);
          });
        }
        // 阻止把图片拖进 iframe 后整页导航成图片
        document.addEventListener("dragover", function (e) { e.preventDefault(); });
        document.addEventListener("drop", function (e) { e.preventDefault(); });
        updateAttachUi();
"""


def _normalize_image_mime(mime: Any) -> str:
    """把 image/jpg 等别名归一化；仅接受 png/jpeg/webp。"""
    value = str(mime or "").strip().lower()
    value = _IMAGE_MIME_ALIASES.get(value, value)
    return value if value in _IMAGE_MAGIC_CHECKS else ""


def _image_mime_from_data_url(data_url: Any) -> str:
    match = _IMAGE_DATA_URL_RE.match(str(data_url or ""))
    return _normalize_image_mime(match.group(1)) if match else ""


def _decode_image_data_url(data_url: Any) -> Optional[tuple]:
    """解析并校验 dataURL → (mime, raw_bytes)；非法（格式/编码/magic bytes）返回 None。"""
    try:
        match = _IMAGE_DATA_URL_RE.match(str(data_url or ""))
        if not match:
            return None
        mime = _normalize_image_mime(match.group(1))
        if not mime:
            return None
        raw = base64.b64decode(match.group(2), validate=True)
        if not raw or not _IMAGE_MAGIC_CHECKS[mime](raw):
            return None
        return mime, raw
    except Exception:
        return None


def _make_image_thumb_url(
    mime: str,
    raw: bytes,
    max_edge: int = 96,
    quality: int = 70,
) -> str:
    """用 PIL 生成 96px JPEG 缩略图 dataURL（失败返回 ""，绝不抛）。

    历史会话只保留该缩略图（`thumb_url`），从而 session_state 不再保存原图 dataURL。
    PIL 未安装 / 图片损坏 / 编码失败时均返回空串，调用方回退原图。
    """
    try:
        from io import BytesIO

        from PIL import Image  # 延迟导入：PIL 属于可选依赖，缺失时不影响主流程

        with Image.open(BytesIO(raw)) as img:
            if getattr(img, "n_frames", 1) > 1:
                img.seek(0)
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                working = background
            else:
                working = img.convert("RGB")
            thumb = working.copy()
            thumb.thumbnail((max_edge, max_edge), Image.LANCZOS)
            buffer = BytesIO()
            thumb.save(buffer, format="JPEG", quality=quality)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return "data:image/jpeg;base64," + encoded if encoded else ""
    except Exception:  # pragma: no cover - PIL 缺失/图片损坏时静默降级
        logger.debug("参考图片缩略图生成失败，回退原图", exc_info=True)
        return ""


def _normalize_message_images(value: Any, max_images: int = _MAX_CAPSULE_IMAGES) -> List[Dict[str, str]]:
    """会话消息 images 字段的轻量规范化（不重新解码 base64，避免每次 rerun 的额外开销）。

    兼容三种形态：只有 data_url（本轮发送前的完整图）、只有 thumb_url（历史瘦身后）、两者都有。
    非法 / 超大 / 全部字段缺失的项被丢弃，不抛异常。
    """
    images: List[Dict[str, str]] = []
    if not isinstance(value, (list, tuple)):
        return images
    for item in value:
        if len(images) >= max_images:
            break
        if not isinstance(item, dict):
            continue
        data_url = str(item.get("data_url") or "")
        thumb_url = str(item.get("thumb_url") or "")
        if data_url and (len(data_url) > _MAX_DATA_URL_CHARS or not _image_mime_from_data_url(data_url)):
            data_url = ""
        if thumb_url and (len(thumb_url) > 200_000 or not thumb_url.startswith("data:image/")):
            thumb_url = ""
        if not data_url and not thumb_url:
            continue
        name = str(item.get("name") or "").strip()[:160] or f"reference-{len(images) + 1}.png"
        mime = _normalize_image_mime(item.get("mime")) or _image_mime_from_data_url(data_url)
        entry: Dict[str, str] = {"name": name, "mime": mime or "image/jpeg"}
        if data_url:
            entry["data_url"] = data_url
        if thumb_url:
            entry["thumb_url"] = thumb_url
        images.append(entry)
    return images


def _image_display_src(item: Any) -> str:
    """历史渲染取图优先级：thumb_url（96px，历史瘦身后仅存它）→ data_url。"""
    if not isinstance(item, dict):
        return ""
    thumb_url = str(item.get("thumb_url") or "")
    if thumb_url.startswith("data:image/"):
        return thumb_url
    data_url = str(item.get("data_url") or "")
    return data_url if data_url.startswith("data:image/") else ""


def _extract_bridge_images(
    payload: Any,
    max_images: int = _MAX_CAPSULE_IMAGES,
    max_bytes: int = _MAX_IMAGE_BYTES,
    max_total_bytes: int = _MAX_TOTAL_IMAGE_BYTES,
) -> List[Dict[str, str]]:
    """校验回传桥 payload 中的参考图片；非法项过滤，任何异常返回 []，绝不抛出。

    校验项：mime 白名单（png/jpeg/webp，jpg 归一化为 jpeg）、base64 可解码、
    解码后 magic bytes 匹配、单张 / 总大小 / 数量上限。
    校验通过后额外用 PIL 生成 96px JPEG 缩略图（``thumb_url``，失败留空），
    供历史瘦身时只保留缩略图、不再塞大图 dataURL。
    """
    try:
        if not isinstance(payload, dict):
            return []
        raw_images = payload.get("images")
        if not isinstance(raw_images, (list, tuple)):
            return []
        images: List[Dict[str, str]] = []
        total_bytes = 0
        for item in raw_images:
            if len(images) >= max_images:
                break
            try:
                if not isinstance(item, dict):
                    continue
                decoded = _decode_image_data_url(item.get("data_url"))
                if decoded is None:
                    continue
                mime, raw = decoded
                if len(raw) > max_bytes:
                    continue
                if total_bytes + len(raw) > max_total_bytes:
                    continue
                total_bytes += len(raw)
                name = str(item.get("name") or "").strip()[:160] or f"reference-{len(images) + 1}.png"
                entry: Dict[str, str] = {
                    "name": name,
                    "mime": mime,
                    "data_url": str(item.get("data_url")),
                    # 缩略图生成失败时留空串（调用方回退原图），绝不因此拒收图片
                    "thumb_url": _make_image_thumb_url(mime, raw),
                }
                images.append(entry)
            except Exception:  # pragma: no cover - 单张图片异常不影响其它图片
                logger.debug("参考图片校验失败，已跳过", exc_info=True)
                continue
        return images
    except Exception:  # pragma: no cover - 极端异常兜底
        logger.warning("参考图片解析失败，按无图处理", exc_info=True)
        return []


def _compact_history_image_payloads(history: Any) -> Any:
    """历史写回 session_state 前剥离 images 里的原图 ``data_url``。

    只保留 ``name`` / ``mime`` / ``thumb_url``（96px 缩略图），避免每轮对话都把
    数 MB 的 base64 原图留在 session_state 里；本轮发给 Agent 的完整图片由
    ``run_turn(reference_images=...)`` 的入参单独携带，不受影响。
    极端情况下缩略图生成失败（无 ``thumb_url``）时保留原图，否则历史将无法显示该图。
    兼容 AgentMessage 与 dict 两种历史元素；异常静默（返回原 history）。
    """
    try:
        for msg in history or []:
            try:
                images = _msg_attr(msg, "images", [])
                if not isinstance(images, (list, tuple)) or not images:
                    continue
                compacted: List[Dict[str, str]] = []
                for item in images:
                    if not isinstance(item, dict):
                        continue
                    entry: Dict[str, str] = {
                        "name": str(item.get("name") or "").strip()[:160],
                        "mime": str(item.get("mime") or "").strip(),
                    }
                    thumb_url = str(item.get("thumb_url") or "")
                    if thumb_url.startswith("data:image/"):
                        entry["thumb_url"] = thumb_url
                    else:
                        # 无缩略图时保留原图兜底，保证历史仍能显示（正常路径不会走到这里）
                        data_url = str(item.get("data_url") or "")
                        if data_url.startswith("data:image/"):
                            entry["data_url"] = data_url
                    compacted.append(entry)
                if isinstance(msg, dict):
                    msg["images"] = compacted
                else:
                    _set_msg_field(msg, "images", compacted)
            except Exception:  # pragma: no cover - 单条消息异常不影响其它消息
                logger.debug("历史图片瘦身跳过一条消息", exc_info=True)
                continue
        return history
    except Exception:  # pragma: no cover - 极端异常兜底
        logger.debug("历史图片瘦身失败，已忽略", exc_info=True)
        return history


def _resolve_text_bridge_payload(payload: Any) -> tuple:
    """解析 kind=="text" 回传 payload → (instruction, images, send_trigger)。

    只有图片没有文字时，自动补一句默认指令，保证 Agent 知道要「参考图片修正」。
    """
    images = _extract_bridge_images(payload)
    text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text and images:
        text = _DEFAULT_IMAGE_INSTRUCTION
    return text, images, bool(text or images)


def _msg_images(msg: Any) -> List[Dict[str, str]]:
    """防御式读取消息 images（AgentMessage 尚未扩展该字段时安全返回 []）。"""
    return _normalize_message_images(_msg_attr(msg, "images", []))


def _build_user_agent_message(instruction: str, images: Optional[List[Dict[str, Any]]] = None) -> Any:
    """构造带参考图片的用户消息；AgentMessage 尚未支持 images 参数时用 setattr 补齐。"""
    normalized = _normalize_message_images(images or [])
    try:
        return AgentMessage(role="user", content=instruction, images=copy.deepcopy(normalized))
    except TypeError:
        msg = AgentMessage(role="user", content=instruction)
        _set_msg_field(msg, "images", copy.deepcopy(normalized))
        return msg


def _run_turn_with_reference_images(
    agent: Any,
    reference_images: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """调用 ``PiDiagramAgent.run_turn``；Agent 侧尚未支持 ``reference_images`` 时自动省略该参数。"""
    images = _normalize_message_images(reference_images or [])
    accepts_reference_images = False
    try:
        params = inspect.signature(agent.run_turn).parameters
        accepts_reference_images = "reference_images" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
    except (TypeError, ValueError):  # pragma: no cover - 不可内省的可调用对象
        accepts_reference_images = False
    if images and accepts_reference_images:
        kwargs["reference_images"] = images
    return agent.run_turn(**kwargs)


def _render_user_images(msg: Any) -> None:
    """在对话历史中渲染用户消息附带的参考图缩略图（最多 4 张，限高 90px）。

    优先展示历史瘦身后保留的 96px ``thumb_url``，旧消息回退原图 ``data_url``。
    """
    images = _msg_images(msg)
    if not images:
        return
    cells = "".join(
        "<img src='{src}' alt='{name}' title='{name}' "
        "style='height:90px;max-width:180px;object-fit:contain;border-radius:8px;"
        "border:1px solid rgba(214, 202, 185, 0.9);background:#fff;padding:2px;' />".format(
            src=_image_display_src(item), name=html.escape(item["name"], quote=True)
        )
        for item in images
        if _image_display_src(item)
    )
    if not cells:
        return
    st.markdown(
        "<div style='display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 2px 0;'>" + cells + "</div>",
        unsafe_allow_html=True,
    )


# ===================== 现代化 Agent 输入胶囊 (HTML / CSS / JS 原生实现) =====================

def _build_modern_agent_capsule_html(
    target_bridge_id: str,
    target_btn_id: str,
    model_name: str,
) -> str:
    """构建胶囊 HTML（纯函数，便于单测与 node --check 校验；不触碰 Streamlit 运行时）。"""
    bridge_id_json = json.dumps(target_bridge_id)
    btn_id_json = json.dumps(target_btn_id)
    model_name_safe = html.escape(model_name.split("/")[-1].split(":")[-1])

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8" />
    <style>
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        padding: 0;
        background: transparent;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      }}
      .agent-capsule {{
        background: #FFFDF9;
        border: 1px solid rgba(214, 202, 185, 0.85);
        border-radius: 16px;
        padding: 12px 14px 10px 14px;
        display: flex;
        flex-direction: column;
        gap: 6px;
        transition: border-color 0.2s, box-shadow 0.2s;
      }}
      .agent-capsule:focus-within {{
        border-color: #CC785C;
        box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.18);
      }}
      .prompt-textarea {{
        background: transparent;
        border: none;
        outline: none;
        color: #26231E;
        font-size: 13.5px;
        line-height: 1.5;
        resize: none;
        width: 100%;
        min-height: 48px;
        font-family: inherit;
        padding: 0;
        margin: 0;
      }}
      .prompt-textarea::placeholder {{
        color: #A89F91;
      }}
      .capsule-toolbar {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-top: 4px;
        user-select: none;
      }}
      .toolbar-left {{
        display: flex;
        align-items: center;
        gap: 8px;
      }}
      .plus-icon {{
        color: #A89F91;
        font-size: 18px;
        font-weight: 300;
        cursor: pointer;
        line-height: 1;
        transition: color 0.15s;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 20px;
        height: 20px;
      }}
      .plus-icon:hover {{
        color: #CC785C;
      }}
      .sync-toggle-label {{
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 12px;
        color: #756E63;
        cursor: pointer;
        white-space: nowrap;
      }}
      .sync-toggle-label input {{
        accent-color: #CC785C;
        cursor: pointer;
        margin: 0;
      }}
      .toolbar-right {{
        display: flex;
        align-items: center;
        gap: 7px;
      }}
      .model-pill {{
        background: #F5EFE6;
        color: #26231E;
        border: 1px solid rgba(214, 202, 185, 0.9);
        border-radius: 8px;
        padding: 3px 8px;
        font-size: 11.5px;
        font-weight: 500;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        white-space: nowrap;
        cursor: default;
      }}
      .model-pill span.bolt {{
        color: #CC785C;
        font-size: 11px;
      }}
      .icon-tool-btn {{
        background: transparent;
        border: none;
        color: #756E63;
        font-size: 14px;
        cursor: pointer;
        border-radius: 6px;
        padding: 4px 6px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        transition: all 0.15s;
        line-height: 1;
      }}
      .icon-tool-btn:hover {{
        background: #EDE5D8;
        color: #26231E;
      }}
      .icon-tool-btn.recording {{
        color: #ef4444;
        background: rgba(239, 68, 68, 0.15);
        animation: mic-pulse 1.3s infinite;
      }}
      @keyframes mic-pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }}
        70% {{ box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }}
      }}
      .send-btn-circle {{
        background: #CC785C;
        color: #FFFDF9;
        border: none;
        border-radius: 50%;
        width: 28px;
        height: 28px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        font-size: 14px;
        font-weight: 700;
        transition: all 0.15s ease;
        box-shadow: 0 4px 12px rgba(204, 120, 92, 0.32);
      }}
      .send-btn-circle:hover {{
        background: #B56345;
        transform: scale(1.05);
      }}
      .send-btn-circle:active {{
        transform: scale(0.95);
      }}
      {_CAPSULE_ATTACH_CSS}
    </style>
    </head>
    <body>
      <div class="agent-capsule">
        <div class="capsule-toast" id="capsuleToast"></div>
        <div class="attach-row" id="attachRow">
          <div class="attach-thumbs" id="attachThumbs"></div>
          <span class="attach-hint" id="attachHint"></span>
        </div>
        <textarea id="promptArea" class="prompt-textarea" rows="1" placeholder="和 物理插图矫正AGENT 一起工作（输入修改要求，或直接粘贴 / 拖拽参考图片...）"></textarea>
        <input id="fileInput" class="capsule-file-input" type="file" accept="image/png,image/jpeg,image/webp" multiple />
        <div class="capsule-toolbar">
          <div class="toolbar-left">
            <button id="plusBtn" class="plus-icon plus-btn" type="button" title="添加参考图片（也可直接粘贴 / 拖拽图片）">+</button>
            <label class="sync-toggle-label" title="开启后，生成并验证成功的插图将自动同步写入源码">
              <input type="checkbox" id="syncCheck" checked onchange="handleSyncChange(this.checked)" />
              <span>⚡ 自动写回源码</span>
            </label>
          </div>
          <div class="toolbar-right">
            <span class="model-pill" title="当前配置模型: {model_name_safe}">
              <span class="bolt">⚡</span>
              <span>{model_name_safe}</span>
            </span>
            <button id="micBtn" class="icon-tool-btn" type="button" onclick="toggleVoice()" title="点击语音口述，文字将自动填入输入框">
              🎙️
            </button>
            <button id="sendBtn" class="send-btn-circle" type="button" onclick="handleSend()" title="发送指令 (或按 Enter / Ctrl+Enter)">
              ↑
            </button>
          </div>
        </div>
      </div>

      <script>
        const targetBridgeId = {bridge_id_json};
        const targetBtnId = {btn_id_json};

        document.getElementById("promptArea").addEventListener("keydown", function(e) {{
          if (e.key === "Enter" && !e.shiftKey) {{
            e.preventDefault();
            handleSend();
          }}
        }});

        function handleSend() {{
          const area = document.getElementById("promptArea");
          const val = (area.value || "").trim();
          const images = window.__mcCapsuleImages ? window.__mcCapsuleImages() : [];
          // 允许「只有图片、没有文字」发送；两样都没有时忽略
          if (!val && !images.length) return;
          try {{
            const bus = ensureAgentBus();
            if (bus && bus.emit({{ kind: "text", text: val, images: images }})) {{
              area.value = "";
              if (window.__mcCapsuleClearImages) window.__mcCapsuleClearImages();
              area.focus();
            }}
          }} catch (e) {{
            console.error("send via bus failed", e);
          }}
        }}

        function handleSyncChange(checked) {{
          try {{
            const pDoc = window.parent.document;
            const chk = pDoc.querySelector('input[data-testid*="auto_sync"]');
            if (chk && chk.checked !== checked) {{
              chk.click();
            }}
          }} catch(e) {{}}
        }}

        // ---------- 父页面「语音 + 回传」引擎 ----------
        // 1) 浏览器只在顶层文档里授予麦克风权限，所以录音逻辑必须注入父页面执行；
        // 2) 识别结果/文本通过 utils/agent_voice 自定义组件（setComponentValue）回传 Python，
        //    这是 Streamlit 官方通道，比伪造 input 事件可靠。
        const AGENT_BUS_SRC = function () {{
          if (window.__mcAgentBus) return;
          const doc = document;
          const MAX_SECONDS = 60;
          const SAMPLE_RATE = 16000;

          let pill = null;
          function ensurePill() {{
            if (pill && pill.isConnected) return pill;
            pill = doc.createElement("div");
            pill.id = "mc-voice-pill";
            pill.style.cssText = [
              "position:fixed", "right:18px", "bottom:18px", "z-index:2147483000",
              "display:none", "align-items:center", "gap:8px",
              "padding:9px 15px", "border-radius:9999px",
              "background:#26231E", "color:#FFFDF9",
              "font:600 12.5px/1.2 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif",
              "box-shadow:0 12px 30px rgba(38,35,30,.28)", "pointer-events:none"
            ].join(";");
            doc.body.appendChild(pill);
            return pill;
          }}
          function setPill(state, text) {{
            window.__mcVoiceState = state;
            const p = ensurePill();
            if (!state || state === "idle") {{
              p.style.display = "none";
              p.textContent = "";
            }} else {{
              p.style.display = "inline-flex";
              p.style.background = "#26231E";
              p.textContent = text;
            }}
          }}
          function showToast(message, danger) {{
            const p = ensurePill();
            p.style.display = "inline-flex";
            p.style.background = danger ? "#B56345" : "#26231E";
            p.textContent = message;
            setTimeout(function () {{
              if (window.__mcVoiceState === "idle" || !window.__mcVoiceState) {{
                p.style.display = "none";
                p.style.background = "#26231E";
              }}
            }}, 5600);
          }}

          function findBridgeIframe(originEl) {{
            const frames = Array.from(doc.querySelectorAll("iframe"));
            const isBridge = (f) => (f.getAttribute("src") || "").indexOf("agent_voice") !== -1;
            let idx = frames.indexOf(originEl);
            if (idx === -1) return frames.find(isBridge) || null;
            for (let i = idx + 1; i < frames.length; i++) {{
              if (isBridge(frames[i])) return frames[i];
            }}
            for (let i = idx - 1; i >= 0; i--) {{
              if (isBridge(frames[i])) return frames[i];
            }}
            return null;
          }}

          function emit(payload, originEl) {{
            const frame = findBridgeIframe(originEl || window.__mcOriginFrame);
            if (!frame || !frame.contentWindow) return false;
            try {{
              const sink = frame.contentWindow.__mcAgentEmit;
              if (typeof sink !== "function") return false;
              payload.id = String(Date.now()) + "-" + Math.random().toString(36).slice(2, 8);
              sink(payload);
              return true;
            }} catch (err) {{
              console.error("emit failed", err);
              return false;
            }}
          }}

          function toBase64(bytes) {{
            let binary = "";
            const chunk = 0x8000;
            for (let i = 0; i < bytes.length; i += chunk) {{
              binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
            }}
            return btoa(binary);
          }}

          function floatToPcm16Base64(chunks, total) {{
            const out = new Int16Array(total);
            let offset = 0;
            for (const chunk of chunks) {{
              for (let i = 0; i < chunk.length; i++) {{
                let s = chunk[i];
                if (s > 1) s = 1;
                else if (s < -1) s = -1;
                out[offset++] = s < 0 ? s * 0x8000 : s * 0x7FFF;
              }}
            }}
            return toBase64(new Uint8Array(out.buffer));
          }}

          const bus = {{
            state: "idle",
            _ctx: null,
            _stream: null,
            _proc: null,
            _source: null,
            _chunks: [],
            _frames: 0,
            _timer: null,

            emit: function (payload) {{ return emit(payload); }},

            toggleMic: function () {{
              if (this.state === "recording") this.stopRecording();
              else if (this.state === "starting") return;
              else this.startRecording();
            }},

            startRecording: async function () {{
              if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
                showToast("⚠️ 当前浏览器不支持麦克风，请用 Chrome / Edge 打开 http://localhost", true);
                return;
              }}
              this.state = "starting";
              setPill("starting", "🎙️ 正在启动麦克风…");
              try {{
                const stream = await navigator.mediaDevices.getUserMedia({{
                  audio: {{ channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }}
                }});
                const Ctx = window.AudioContext || window.webkitAudioContext;
                const ctx = new Ctx({{ sampleRate: SAMPLE_RATE }});
                if (ctx.state === "suspended") {{ try {{ await ctx.resume(); }} catch (e) {{}} }}
                const source = ctx.createMediaStreamSource(stream);
                const proc = ctx.createScriptProcessor(4096, 1, 1);
                const chunks = [];
                let frames = 0;
                proc.onaudioprocess = function (ev) {{
                  const data = ev.inputBuffer.getChannelData(0);
                  chunks.push(new Float32Array(data));
                  frames += data.length;
                }};
                source.connect(proc);
                proc.connect(ctx.destination);
                this._ctx = ctx;
                this._stream = stream;
                this._source = source;
                this._proc = proc;
                this._chunks = chunks;
                this._frames = function () {{ return frames; }};
                this.state = "recording";
                setPill("recording", "🔴 录音中… 再次点击 🎙️ 结束（最长 60 秒）");
                this._timer = setTimeout(() => this.stopRecording(), MAX_SECONDS * 1000);
              }} catch (err) {{
                const name = (err && err.name) || "";
                let msg = "无法打开麦克风：" + ((err && err.message) || name);
                if (name === "NotAllowedError" || name === "SecurityError") {{
                  msg = "🔇 麦克风被拒绝：请点地址栏的麦克风图标允许权限";
                }} else if (name === "NotFoundError") {{
                  msg = "🔇 未检测到麦克风设备";
                }}
                this.state = "idle";
                setPill("idle");
                showToast("⚠️ " + msg, true);
              }}
            }},

            stopRecording: function () {{
              if (this._timer) {{
                clearTimeout(this._timer);
                this._timer = null;
              }}
              const ctx = this._ctx;
              const proc = this._proc;
              const source = this._source;
              const stream = this._stream;
              const chunks = this._chunks || [];
              const total = typeof this._frames === "function" ? this._frames() : 0;
              this.state = "idle";
              this._ctx = null;
              this._proc = null;
              this._source = null;
              this._stream = null;

              try {{ if (proc) proc.disconnect(); }} catch (e) {{}}
              try {{ if (source) source.disconnect(); }} catch (e) {{}}
              try {{ if (ctx) ctx.close(); }} catch (e) {{}}
              try {{ if (stream) stream.getTracks().forEach((t) => t.stop()); }} catch (e) {{}}

              if (!total || total < SAMPLE_RATE * 0.35) {{
                setPill("idle");
                showToast("⚠️ 录音太短，请说完后再点一次结束", true);
                return;
              }}
              setPill("transcribing", "⏳ 正在识别语音…");
              try {{
                const b64 = floatToPcm16Base64(chunks, total);
                const ok = emit({{ kind: "pcm16", rate: SAMPLE_RATE, pcm: b64 }});
                if (!ok) {{
                  setPill("idle");
                  showToast("⚠️ 语音回传通道未就绪，请刷新页面后重试", true);
                }} else {{
                  setPill("idle");
                }}
              }} catch (e) {{
                setPill("idle");
                showToast("⚠️ 音频编码失败", true);
              }}
            }}
          }};

          window.__mcAgentBus = bus;
          window.__mcVoiceState = "idle";
        }};

        function ensureAgentBus() {{
          const P = window.parent;
          if (!P.__mcAgentBus) {{
            const s = P.document.createElement("script");
            s.textContent = "(" + AGENT_BUS_SRC.toString() + ")();";
            P.document.head.appendChild(s);
            P.__mcOriginFrame = window.frameElement;
          }} else if (P.__mcOriginFrame !== window.frameElement) {{
            P.__mcOriginFrame = window.frameElement;
          }}
          return P.__mcAgentBus;
        }}

        function toggleVoice() {{
          try {{
            const P = window.parent;
            P.__mcOriginFrame = window.frameElement;
            ensureAgentBus();
            P.__mcAgentBus.toggleMic();
          }} catch (e) {{
            console.error("voice bus error", e);
          }}
        }}

        // 麦克风按钮状态跟随父页面录音状态
        setInterval(function() {{
          const btn = document.getElementById("micBtn");
          if (!btn) return;
          const P = window.parent;
          const st = (P.__mcAgentBus && P.__mcAgentBus.state) || "idle";
          const recording = st === "recording" || st === "starting";
          if (btn.classList.contains("recording") !== recording) {{
            btn.classList.toggle("recording", recording);
            btn.title = recording ? "点击结束录音" : "点击语音录入（使用麦克风）";
          }}
        }}, 300);

{_CAPSULE_ATTACH_JS}
      </script>
    </body>
    </html>
    """


def render_modern_agent_capsule(
    target_bridge_id: str,
    target_btn_id: str,
    model_name: str,
    key_prefix: str = "agent_capsule",
) -> None:
    """
    渲染对标现代化 Agent 的提示词胶囊（Textarea + 内置左右工具栏），
    彻底解决多列布局在窄屏下文字被竖向挤爆的问题。

    支持 Ctrl+V 粘贴图片 / 拖拽图片 / 点「+」选择图片，随修改指令一起回传 Python。
    """
    html_code = _build_modern_agent_capsule_html(target_bridge_id, target_btn_id, model_name)
    html_code = theme_agent_capsule(html_code, st.session_state.get('app_theme_skin'), st.session_state.get('app_theme_mode', 'light'))
    # Resize only this iframe when a long model name or narrow column wraps the toolbar.
    html_code += '''<script>
    const capsuleResize = new ResizeObserver(() => parent.postMessage({
      isStreamlitMessage:true,type:'streamlit:setFrameHeight',height:Math.ceil(document.querySelector('.agent-capsule').getBoundingClientRect().height)+4
    },'*'));
    capsuleResize.observe(document.body);
    addEventListener('pagehide',()=>capsuleResize.disconnect(),{once:true});
    </script>'''
    components.html(html_code, height=115)


# ===================== Harness v2：质量门禁与确定性校验回显 =====================
# 说明：本区块只负责「回显」，不参与 Agent 决策。
# 所有 harness v2 新增字段（契约 docs/harness_v2_contract.md §4.1）一律通过
# getattr(msg, ..., default) 防御式读取，保证在 AgentMessage 尚未扩展完成时，
# 本模块依然可以导入并渲染旧版本会话数据。

# AgentMessage 的 harness v2 扩展字段及默认值
_HARNESS_V2_DEFAULTS: Dict[str, Any] = {
    "validation": {},
    "validation_score": 0,
    "quality_gate": "",
    "fixes_applied": [],
    "attempts": 0,
    "compile_log": "",
    "compile_error_kind": "",
    "visual_check": {},
}

_GATE_LABELS = {"pass": "通过", "needs_review": "需复核", "fail": "未通过", "": "未评估"}

# 质量门禁徽章配色（沿用面板标头的暖色 + 语义色，保持整页视觉一致）
_GATE_PILL_STYLES = {
    "pass": ("#14532d", "#86efac", "#22c55e"),
    "needs_review": ("#361b04", "#fde68a", "#d97706"),
    "fail": ("#3f1111", "#fca5a5", "#ef4444"),
    "": ("#F5EFE6", "#756E63", "rgba(214, 202, 185, 0.85)"),
}

# 与文件内既有「技能徽章」一致的暖陶小胶囊样式
_WARM_CHIP_STYLE = (
    "background:rgba(204,120,92,0.12);color:#B56345;"
    "border:1px solid rgba(204,120,92,0.35);padding:1px 7px;"
    "border-radius:6px;font-size:11px;white-space:nowrap;"
)

_CONTEXT_LABELS = {
    "force": "受力分析",
    "incline": "斜面/倾角",
    "magnetic": "磁场",
    "optics": "光路",
    "circuit": "电路",
    "spring": "弹簧",
    "pulley": "滑轮",
    "field": "电场",
    "wave": "机械波",
    "gravity": "万有引力/天体",
    "collision": "碰撞/动量",
    "thermo": "热学",
}


def _msg_attr(msg: Any, name: str, default: Any = None) -> Any:
    """防御式读取消息字段：兼容 AgentMessage 新字段缺失与 dict 形态的会话数据。"""
    if isinstance(msg, dict):
        value = msg.get(name, default)
    else:
        try:
            value = getattr(msg, name, default)
        except Exception:  # pragma: no cover - 极端对象异常兜底
            value = default
    return default if value is None else value


def _set_msg_field(msg: Any, name: str, value: Any) -> None:
    """防御式回写消息字段（AgentMessage 尚未扩展该字段时静默跳过）。"""
    try:
        setattr(msg, name, value)
    except Exception:  # pragma: no cover - 只读/冻结对象兜底
        logger.debug("无法回写 AgentMessage 字段 %s", name, exc_info=True)


def _restore_message(raw: Any) -> Any:
    """把 session_state 中的历史消息还原为 AgentMessage，并补齐 harness v2 字段。"""
    if not isinstance(raw, dict):
        return raw
    try:
        msg = AgentMessage.from_dict(raw)
    except Exception:  # pragma: no cover - 旧会话数据兼容兜底
        logger.warning("会话消息反序列化失败，使用最小字段重建", exc_info=True)
        msg = AgentMessage(
            role=raw.get("role", "assistant"),
            content=raw.get("content", ""),
            thought=raw.get("thought", ""),
            tikz_code=raw.get("tikz_code", ""),
            preview_b64=raw.get("preview_b64", ""),
            compile_ok=raw.get("compile_ok", True),
            compile_error=raw.get("compile_error", ""),
            activated_skills=raw.get("activated_skills", []),
        )
    # AgentMessage.from_dict 若尚未覆盖 v2 字段，这里从原始 dict 补回，避免回写丢失
    for name in _HARNESS_V2_DEFAULTS:
        if name in raw:
            _set_msg_field(msg, name, raw[name])
    # 参考图片字段：旧会话没有该键 → []；AgentMessage.from_dict 未覆盖时这里补回
    _set_msg_field(
        msg,
        "images",
        _normalize_message_images(raw.get("images") or _msg_attr(msg, "images", [])),
    )
    return msg


def _serialize_message(msg: Any) -> Dict[str, Any]:
    """序列化历史消息；AgentMessage.to_dict 未包含 v2 字段时自动补齐，避免重跑丢失。"""
    if isinstance(msg, dict):
        data: Dict[str, Any] = dict(msg)
    else:
        try:
            data = dict(msg.to_dict())
        except Exception as exc:  # pragma: no cover - 极端对象异常兜底
            logger.warning("会话消息序列化失败，使用最小字段：%s", exc)
            data = {
                "role": getattr(msg, "role", "assistant"),
                "content": getattr(msg, "content", ""),
                "thought": getattr(msg, "thought", ""),
                "tikz_code": getattr(msg, "tikz_code", ""),
                "preview_b64": getattr(msg, "preview_b64", ""),
                "compile_ok": getattr(msg, "compile_ok", True),
                "compile_error": getattr(msg, "compile_error", ""),
                "activated_skills": getattr(msg, "activated_skills", []) or [],
            }
    for name, default in _HARNESS_V2_DEFAULTS.items():
        if name not in data:
            data[name] = copy.deepcopy(_msg_attr(msg, name, default))
    # 多模态参考图片：AgentMessage.to_dict 尚未包含该键时也要落盘，保证会话可还原/显示缩略图
    data["images"] = copy.deepcopy(
        _normalize_message_images(_msg_attr(msg, "images", data.get("images") or []))
    )
    return data


# ===================== ⑦⑧⑨ 会话内存 / 题目隔离 / 上下文体积 =====================

_AGENT_HIST_LRU_KEY = "_agent_hist_lru"
_AGENT_HISTORY_KEEP_DEFAULT = 8
_AGENT_HISTORY_KEEP_MIN = 1
_AGENT_HISTORY_KEEP_MAX = 50

# \begin{problem}{年度}{类型}{来源}{题号}{章节} —— 5 组花括号（正则容错：组间允许空白/换行）
_PROBLEM_HEADER_RE = re.compile(
    r"\\begin\s*\{\s*problem\s*\}\s*"
    r"\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
    re.DOTALL,
)


def _resolve_history_keep_limit(keep: Any = _AGENT_HISTORY_KEEP_DEFAULT) -> int:
    """解析历史 LRU 上限：默认 8，clamp 到 1..50。

    规则：调用方显式传入非默认的 keep 时优先（便于单测/特殊页面覆盖）；
    使用默认值时读 env ``PI_AGENT_HISTORY_KEEP_QUESTIONS``（非法值回落 8）。
    """
    try:
        value: Any = _AGENT_HISTORY_KEEP_DEFAULT if keep is None else keep
        raw = str(os.getenv("PI_AGENT_HISTORY_KEEP_QUESTIONS", "") or "").strip()
        if raw and str(value).strip() == str(_AGENT_HISTORY_KEEP_DEFAULT):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = _AGENT_HISTORY_KEEP_DEFAULT
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = _AGENT_HISTORY_KEEP_DEFAULT
    return max(_AGENT_HISTORY_KEEP_MIN, min(_AGENT_HISTORY_KEEP_MAX, resolved))


def _safe_session_pop(session_state: Any, key: str) -> None:
    """兼容 dict / Streamlit SessionState 的删除操作，任何异常静默。"""
    try:
        session_state.pop(key, None)
        return
    except Exception:
        pass
    try:
        del session_state[key]
    except Exception:  # pragma: no cover - 只读或异常 session_state
        pass


def _prune_question_histories(
    session_state: Any,
    touched_hist_key: str,
    keep: Any = _AGENT_HISTORY_KEEP_DEFAULT,
) -> List[str]:
    """⑦ 题目历史 LRU：把当前题目移到最近使用位，超过 keep 时淘汰最旧题目对话。

    - ``session_state["_agent_hist_lru"]`` 保存 hist_key 列表（最近访问在尾部）；
    - ``keep`` 为 None 时读 env ``PI_AGENT_HISTORY_KEEP_QUESTIONS``（默认 8，clamp 1..50）；
    - 只 pop 被淘汰题目的 ``*_history`` 键，避免 session_state 无限膨胀；
    - 返回最新 LRU 顺序（异常时返回 []，绝不抛）。
    """
    try:
        if not isinstance(touched_hist_key, str) or not touched_hist_key:
            return []
        limit = _resolve_history_keep_limit(keep)
        raw_lru = session_state.get(_AGENT_HIST_LRU_KEY)
        order: List[str] = []
        if isinstance(raw_lru, (list, tuple)):
            for item in raw_lru:
                if isinstance(item, str) and item and item not in order:
                    order.append(item)
        if touched_hist_key in order:
            order.remove(touched_hist_key)
        order.append(touched_hist_key)
        while len(order) > limit:
            oldest = order.pop(0)
            _safe_session_pop(session_state, oldest)
        session_state[_AGENT_HIST_LRU_KEY] = order
        return order
    except Exception:  # pragma: no cover - 异常 session_state 兜底
        logger.debug("题目历史 LRU 维护失败，已忽略", exc_info=True)
        return []


def _question_history_fingerprint(content: Any) -> str:
    """⑧ 单题录入页对话隔离指纹：由 ``\\begin{problem}{..}{..}{..}{..}{..}`` 5 组花括号决定。

    返回 md5("problem:" + 5 个分组)[:10]；无 problem 头部返回 ""。
    刻意不回退到全文 hash：同一道题编辑正文时对话应保持连续，避免边输入边切会话。
    """
    try:
        match = _PROBLEM_HEADER_RE.search(str(content or ""))
        if not match:
            return ""
        groups = [str(group).strip() for group in match.groups()]
        payload = "problem:" + "\x1f".join(groups)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]
    except Exception:  # pragma: no cover - 极端输入兜底
        return ""


def _prompt_metrics_caption(msg: Any) -> str:
    """⑨ 本轮上下文体积 caption（无数据返回 ""）。字段缺失/异常一律按 0 处理。"""
    metrics = _as_dict(_msg_attr(msg, "prompt_metrics", {}))
    if not metrics:
        return ""
    chars = _as_int(metrics.get("chars", metrics.get("prompt_chars", 0)), 0)
    est_tokens = _as_int(
        metrics.get("est_tokens", metrics.get("estimated_tokens", metrics.get("tokens", 0))), 0
    )
    history_messages = _as_int(metrics.get("history_messages", 0), 0)
    skill_prompt_count = _as_int(metrics.get("skill_prompt_count", 0), 0)
    activated_skill_count = _as_int(metrics.get("activated_skill_count", 0), 0)
    if chars < 0:
        chars = 0
    if est_tokens < 0:
        est_tokens = 0
    return (
        f"🧮 本轮上下文 ≈ {chars / 1024:.1f} KB（约 {est_tokens} tokens）"
        f"· 历史 {history_messages} 条"
        f" · 注入技能 {skill_prompt_count}/{activated_skill_count} 个"
    )


def _render_prompt_metrics_caption(msg: Any) -> None:
    """⑨ 有 prompt_metrics 时渲染一行 caption；无数据不显示、不报错。"""
    caption = _prompt_metrics_caption(msg)
    if not caption:
        return
    try:
        st.caption(caption)
    except Exception:  # pragma: no cover - Streamlit 上下文缺失兜底
        logger.debug("prompt 体积 caption 渲染失败", exc_info=True)


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return default


def _as_str_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _normalize_gate(value: Any) -> str:
    gate = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if gate in {"pass", "passed", "ok", "通过"}:
        return "pass"
    if gate in {"needs_review", "need_review", "review", "warn", "warning", "需复核"}:
        return "needs_review"
    if gate in {"fail", "failed", "error", "reject", "未通过", "失败"}:
        return "fail"
    return ""


def _score_to_grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _derive_gate(validation: Dict[str, Any], score: int) -> str:
    """优先使用校验报告自带 gate，缺失时按契约 §1.3 的门禁规则推导。"""
    gate = _normalize_gate(validation.get("gate"))
    if gate:
        return gate
    if not validation:
        return ""
    errors = validation.get("errors") or []
    warnings = validation.get("warnings") or []
    if errors:
        return "fail"
    if len(warnings) >= 2 or _as_int(score) < 80:
        return "needs_review"
    return "pass"


def _resolve_score(msg: Any, validation: Optional[Dict[str, Any]] = None) -> int:
    validation = _as_dict(validation if validation is not None else _msg_attr(msg, "validation", {}))
    score = _as_int(_msg_attr(msg, "validation_score", 0), 0)
    if score <= 0:
        score = _as_int(validation.get("score", 0), 0)
    return max(0, min(100, score))


def _resolve_gate(msg: Any, validation: Optional[Dict[str, Any]] = None) -> str:
    gate = _normalize_gate(_msg_attr(msg, "quality_gate", ""))
    if gate:
        return gate
    validation = _as_dict(validation if validation is not None else _msg_attr(msg, "validation", {}))
    return _derive_gate(validation, _resolve_score(msg, validation))


def _collect_issues(validation: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for key, level in (("errors", "error"), ("warnings", "warning"), ("infos", "info")):
        for item in validation.get(key) or []:
            if isinstance(item, dict):
                entry = dict(item)
                entry.setdefault("level", level)
                issues.append(entry)
    if not issues:
        for item in validation.get("issues") or []:
            if isinstance(item, dict):
                issues.append(dict(item))
    return issues


def _has_compile_issue(validation: Dict[str, Any]) -> bool:
    return any(str(item.get("code", "")).startswith("compile.") for item in _collect_issues(validation))


def _normalize_validation_report(report: Any) -> Dict[str, Any]:
    """补全校验报告中的 UI 依赖字段（gate/grade/summary），不修改既有语义。"""
    data = _as_dict(report)
    if not data:
        return {}
    errors = [item for item in (data.get("errors") or []) if isinstance(item, dict)]
    warnings = [item for item in (data.get("warnings") or []) if isinstance(item, dict)]
    infos = [item for item in (data.get("infos") or []) if isinstance(item, dict)]
    score = max(0, min(100, _as_int(data.get("score", 0), 0)))
    gate = _normalize_gate(data.get("gate")) or _derive_gate(data, score)
    data["errors"] = errors
    data["warnings"] = warnings
    data["infos"] = infos
    if not data.get("issues"):
        data["issues"] = errors + warnings + infos
    data["score"] = score
    data["gate"] = gate
    if not data.get("grade"):
        data["grade"] = _score_to_grade(score)
    if not data.get("summary"):
        data["summary"] = (
            f"得分 {score}/100 · {len(errors)} 项错误 · {len(warnings)} 项警告"
            f" · 门禁{_GATE_LABELS.get(gate, '未评估')}"
        )
    return data


def _validate_tikz_code(
    tikz_code: str,
    question_text: str = "",
    user_instruction: str = "",
    compile_log: str = "",
) -> Dict[str, Any]:
    """调用确定性校验器；模块/参数不可用时优雅降级为空报告。"""
    try:
        from services.diagram_validate import validate_diagram as _validate
    except Exception:  # pragma: no cover - 校验器尚未落地时
        logger.info("services.diagram_validate 不可用，跳过确定性校验")
        return {}
    if not callable(_validate):
        return {}

    kwargs: Dict[str, Any] = {
        "question_text": question_text or "",
        "user_instruction": user_instruction or "",
    }
    try:
        params = inspect.signature(_validate).parameters
        accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        if "compile_log" in params or accepts_kwargs:
            kwargs["compile_log"] = compile_log or ""
    except (TypeError, ValueError):  # pragma: no cover - 内置函数等无签名情况
        pass

    try:
        report = _validate(tikz_code, **kwargs)
    except TypeError:
        # 兼容旧版 validate_diagram(tikz, question_text, user_instruction) 位置参数
        try:
            report = _validate(tikz_code, question_text or "", user_instruction or "")
        except Exception as exc:
            logger.warning("确定性校验执行失败（位置参数回退）：%s", exc)
            return {}
    except Exception as exc:
        logger.warning("确定性校验执行失败：%s", exc)
        return {}
    return _normalize_validation_report(report)


def _normalize_compile_result(raw: Any) -> Optional[Dict[str, Any]]:
    """把 TikzCompileResult / dict / 元组统一成 UI 可用的编译结果字典。"""
    if raw is None:
        return None
    ok: Any = None
    b64_img: Any = ""
    log: Any = ""
    error_kind: Any = ""
    error: Any = ""
    driver_exit_code: Any = None
    compile_retries: Any = 0
    if isinstance(raw, dict):
        ok = raw.get("ok")
        b64_img = raw.get("b64_png") or raw.get("b64") or raw.get("preview_b64") or ""
        log = raw.get("log", "")
        error_kind = raw.get("error_kind", "")
        error = raw.get("error", "")
        driver_exit_code = raw.get("driver_exit_code")
        compile_retries = raw.get("compile_retries", 0)
    elif isinstance(raw, (tuple, list)):
        if len(raw) < 2:
            return None
        ok, b64_img = raw[0], raw[1]
        error = raw[2] if len(raw) > 2 else ""
        log = raw[3] if len(raw) > 3 else ""
        error_kind = raw[4] if len(raw) > 4 else ""
    else:
        ok = getattr(raw, "ok", None)
        b64_img = getattr(raw, "b64_png", None) or getattr(raw, "b64", None) or ""
        log = getattr(raw, "log", "")
        error_kind = getattr(raw, "error_kind", "")
        error = getattr(raw, "error", "")
        driver_exit_code = getattr(raw, "driver_exit_code", None)
        compile_retries = getattr(raw, "compile_retries", 0) or 0
    if ok is None:
        ok = bool(b64_img)
    ok = bool(ok)
    # 关键：DRIVER_CRASH 不能被硬编码成 COMPILE_ERROR 覆盖（T1 契约 + 旧日志兜底识别）
    kind = normalize_compile_error_kind(error_kind, compile_ok=ok, compile_log=str(log or ""))
    try:
        retries = int(compile_retries or 0)
    except (TypeError, ValueError):
        retries = 0
    return {
        "ok": ok,
        "b64_png": b64_img or None,
        "log": str(log or ""),
        "error_kind": kind,
        "error": str(error or ""),
        "driver_exit_code": driver_exit_code,
        "compile_retries": retries,
    }


def _compile_tikz_with_log(tikz_code: str, base_dir: str) -> Dict[str, Any]:
    """优先使用 harness v2 的 DiagramTools.compile_with_log，缺失时回退 compile_preview。

    返回的 ``error_kind`` 会原样透传（含 DRIVER_CRASH），不会被硬编码成 COMPILE_ERROR。
    """
    compile_with_log = getattr(DiagramTools, "compile_with_log", None)
    if callable(compile_with_log):
        try:
            normalized = _normalize_compile_result(compile_with_log(tikz_code, base_dir))
            if normalized is not None:
                return normalized
        except Exception as exc:
            logger.warning("compile_with_log 调用失败，回退 compile_preview：%s", exc)

    try:
        ok, b64_img, err = DiagramTools.compile_preview(tikz_code, base_dir=base_dir)
    except Exception as exc:  # pragma: no cover - 编译工具异常兜底
        logger.warning("compile_preview 调用失败：%s", exc)
        return {
            "ok": False, "b64_png": None, "log": "", "error": str(exc),
            "error_kind": normalize_compile_error_kind("", compile_ok=False, compile_log=str(exc)),
            "driver_exit_code": None, "compile_retries": 0,
        }
    err_text = str(err or "")
    return {
        "ok": bool(ok),
        "b64_png": b64_img or None,
        "log": "",
        "error": "" if ok else (err_text or "编译失败"),
        "error_kind": normalize_compile_error_kind("", compile_ok=bool(ok), compile_log=err_text),
        "driver_exit_code": None,
        "compile_retries": 0,
    }


def _should_preserve_after_recompile_failure(
    previous_compile_ok: Any,
    previous_preview_b64: Any,
    compile_result: Dict[str, Any],
) -> bool:
    """「🔄 重新编译」失败时，是否必须保留消息既有的可用状态（纯函数，便于单测）。

    仅当以下条件同时成立才保留：
      1. 本次重新编译失败；
      2. 该消息原本已编译成功且带可用预览图（原本就是一条可应用的矫正结果）；
      3. 本次失败是输出驱动崩溃（DRIVER_CRASH：瞬时环境故障，与图形代码无关）。

    普通编译失败（语法错误等）仍按失败回写，避免把真正失效的图误当成可用。
    """
    if not isinstance(compile_result, dict) or compile_result.get("ok"):
        return False
    if not bool(previous_compile_ok):
        return False
    if not str(previous_preview_b64 or "").strip():
        return False
    if is_driver_crash_kind(compile_result.get("error_kind")):
        return True
    # 兼容旧式 COMPILE_ERROR：若日志仍是驱动崩溃签名，同样按环境故障处理
    return is_driver_crash_kind(detect_driver_crash_kind(str(compile_result.get("log") or "")))


def _short_compile_error(compile_result: Dict[str, Any]) -> str:
    """面向用户的短编译错误：驱动崩溃给专用中文说明，其余优先原始错误/日志摘要。"""
    if is_driver_crash_kind(compile_result.get("error_kind")):
        return DRIVER_CRASH_USER_MESSAGE
    error = str(compile_result.get("error") or "").strip()
    if error:
        return error[:600]
    log = str(compile_result.get("log") or "")
    if log:
        try:
            from utils.tex_log_tools import summarize_tex_log

            summary = str(summarize_tex_log(log, max_chars=400) or "").strip()
            if summary:
                return summary[:600]
        except Exception:  # pragma: no cover - 日志工具尚未落地时
            pass
        for line in log.splitlines():
            stripped = line.strip()
            if stripped.startswith("!") or "error" in stripped.lower():
                return stripped[:600]
    return str(compile_result.get("error_kind") or "编译失败")


def _parse_result_gate(result: Any, agent_msg: Any = None) -> str:
    result = _as_dict(result)
    gate = _normalize_gate(result.get("quality_gate"))
    if gate:
        return gate
    return _resolve_gate(agent_msg) if agent_msg is not None else ""


def _last_user_instruction(history: List[Any]) -> str:
    for item in reversed(history or []):
        role = item.get("role", "") if isinstance(item, dict) else getattr(item, "role", "")
        if role == "user":
            content = item.get("content", "") if isinstance(item, dict) else getattr(item, "content", "")
            return str(content or "")
    return ""


def _pick_recompile_target(history: List[Any], existing_blocks: List[Dict[str, Any]]) -> tuple:
    """定位重新编译要回写的 assistant 消息（最近一条），并确定待编译代码。"""
    target_idx: Optional[int] = None
    for idx in range(len(history) - 1, -1, -1):
        item = history[idx]
        role = item.get("role", "") if isinstance(item, dict) else getattr(item, "role", "")
        if role != "assistant":
            continue
        if target_idx is None:
            target_idx = idx
        tikz = item.get("tikz_code", "") if isinstance(item, dict) else getattr(item, "tikz_code", "")
        if tikz:
            target_idx = idx
            break
    current_code = existing_blocks[0]["code"] if existing_blocks else ""
    if not current_code and target_idx is not None:
        item = history[target_idx]
        current_code = (
            item.get("tikz_code", "") if isinstance(item, dict) else getattr(item, "tikz_code", "")
        ) or ""
    return target_idx, current_code


def _issues_html(items: List[Dict[str, Any]], icon: str, color: str, small: bool = False) -> str:
    rows: List[str] = []
    font_size = "12px" if small else "12.8px"
    for item in items:
        message = html.escape(str(item.get("message") or item.get("code") or ""))
        code = html.escape(str(item.get("code") or ""))
        hint = html.escape(str(item.get("hint") or ""))
        code_html = f" <span style='color:#A89F91;font-size:10.5px;'>{code}</span>" if code else ""
        rows.append(
            f"<div style='margin:5px 0 0 0;font-size:{font_size};color:{color};line-height:1.5;'>"
            f"{icon} <span>{message}</span>{code_html}</div>"
        )
        if hint:
            rows.append(
                f"<div style='margin:1px 0 0 18px;color:#756E63;font-size:11.5px;font-style:italic;'>"
                f"💡 {hint}</div>"
            )
    return "".join(rows)


def _metrics_caption(metrics: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, label in (
        ("draw_commands", "绘图命令"),
        ("node_count", "节点"),
        ("arrow_count", "箭头"),
        ("labeled_nodes", "带标注节点"),
        ("closed_paths", "闭合路径"),
        ("magnetic_symbols", "磁场符号"),
        ("lines", "行数"),
    ):
        if key in metrics and metrics[key] not in (None, ""):
            parts.append(f"{label} {metrics[key]}")
    for key, label, suffix in (
        ("incline_angle_deg", "图中倾角", "°"),
        ("expected_angle_deg", "目标角度", "°"),
        ("angle_delta_deg", "角度偏差", "°"),
    ):
        if key in metrics and metrics[key] is not None:
            parts.append(f"{label} {metrics[key]}{suffix}")
    compile_errors = _as_int(metrics.get("compile_error_count", 0), 0)
    if compile_errors:
        parts.append(f"编译错误 {compile_errors}")
    return ("📐 " + " · ".join(str(part) for part in parts)) if parts else ""


def _visual_chip_text_and_title(visual: Dict[str, Any]) -> tuple:
    if not visual:
        return "👁 视觉校验：未启用", ""
    title = html.escape(str(visual.get("summary") or ""), quote=True)
    if visual.get("skipped"):
        return "👁 视觉校验：已跳过", title
    visual_ok = bool(visual.get("ok", True))
    visual_score = _as_int(visual.get("score", 0), 0)
    text = "👁 视觉校验：" + ("通过" if visual_ok else "需关注")
    if visual_score:
        text += f" · {visual_score}"
    return text, title


def _quality_badge_html(
    gate: str,
    score: int,
    grade: str,
    compile_ok: bool,
    compile_error_kind: str,
    visual: Dict[str, Any],
    attempts: int,
) -> str:
    chips: List[str] = []

    pill_bg, pill_fg, pill_border = _GATE_PILL_STYLES.get(gate, _GATE_PILL_STYLES[""])
    gate_label = _GATE_LABELS.get(gate, "未评估")
    grade_html = f"（{html.escape(grade)}）" if grade else ""
    chips.append(
        f"<span style='background:{pill_bg};color:{pill_fg};border:1px solid {pill_border};"
        f"padding:1.5px 8px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap;'>"
        f"🧪 质量门禁：{gate_label}{grade_html}</span>"
    )

    chips.append(f"<span style='{_WARM_CHIP_STYLE}'>得分 {score}/100</span>")

    if compile_ok:
        chips.append(f"<span style='{_WARM_CHIP_STYLE}'>编译 ✅</span>")
    else:
        if is_driver_crash_kind(compile_error_kind):
            # 环境故障与语法错误分开表述，避免用户误以为 Agent 无法矫正图形
            kind = "输出驱动崩溃（环境）"
        else:
            kind = html.escape(compile_error_kind or "编译失败")
        chips.append(
            "<span style='background:rgba(239,68,68,0.10);color:#b91c1c;"
            "border:1px solid rgba(239,68,68,0.35);padding:1px 7px;border-radius:6px;"
            f"font-size:11px;white-space:nowrap;'>编译 ❌ {kind}</span>"
        )

    visual_text, visual_title = _visual_chip_text_and_title(visual)
    visual_title_attr = f" title='{visual_title}'" if visual_title else ""
    chips.append(f"<span style='{_WARM_CHIP_STYLE}'{visual_title_attr}>{visual_text}</span>")

    if attempts > 1:
        chips.append(f"<span style='{_WARM_CHIP_STYLE}'>🔁 自愈 {attempts} 轮</span>")

    return (
        "<div style='display:flex;flex-wrap:wrap;align-items:center;gap:6px;"
        "margin:2px 0 9px 0;'>" + "".join(chips) + "</div>"
    )


def _render_gate_notice(
    gate: str,
    score: int,
    compile_ok: bool,
    compile_error_kind: str = "",
) -> None:
    if gate == "fail":
        if is_driver_crash_kind(compile_error_kind):
            # 环境故障：措辞必须让用户明白「不是图形代码的问题」
            st.error(
                "🧪 编译器输出驱动崩溃（环境问题，非图形代码问题）：自动同步已暂停。"
                "这是 xdvipdfmx 在 Windows 上的瞬时崩溃（如 0xC0000417），插图代码本身通常没有问题；"
                "请稍后再点一次「🔄 重新编译」。若反复出现，可简化图形或更新 TeX Live；"
                "如确认图形可用，也可点击「⚠️ 仍要应用此图」手动写入源码。"
            )
            return
        reason = "编译未通过" if not compile_ok else "确定性校验未通过"
        st.error(
            f"🧪 {reason}：自动同步已暂停。请查看下方「🧪 确定性校验报告」"
            "与「📜 编译器日志」；如确认图形可用，可点击「⚠️ 仍要应用此图」手动写入源码。"
        )
    elif gate == "needs_review":
        st.warning(
            f"🧪 确定性校验需复核（得分 {score}/100）：自动同步仍会继续，"
            "建议先检查下方「🧪 确定性校验报告」中的警告。"
        )


def _render_quality_badges(msg: Any) -> str:
    """在 assistant 消息「修正说明」下方渲染质量徽章行与醒目门禁提示，返回 quality_gate。"""
    validation = _as_dict(_msg_attr(msg, "validation", {}))
    gate = _resolve_gate(msg, validation)
    score = _resolve_score(msg, validation)
    grade = str(validation.get("grade") or "").strip()
    visual = _as_dict(_msg_attr(msg, "visual_check", {}))
    compile_ok = bool(_msg_attr(msg, "compile_ok", True))
    compile_log = str(_msg_attr(msg, "compile_log", "") or "")
    fixes = _as_str_list(_msg_attr(msg, "fixes_applied", []))
    attempts = _as_int(_msg_attr(msg, "attempts", 0), 0)

    has_payload = bool(gate or validation or visual or compile_log or fixes or not compile_ok)
    if has_payload:
        st.markdown(
            _quality_badge_html(
                gate=gate,
                score=score,
                grade=grade,
                compile_ok=compile_ok,
                compile_error_kind=str(_msg_attr(msg, "compile_error_kind", "") or ""),
                visual=visual,
                attempts=attempts,
            ),
            unsafe_allow_html=True,
        )
        _render_gate_notice(
            gate,
            score,
            compile_ok,
            str(_msg_attr(msg, "compile_error_kind", "") or ""),
        )
    return gate


def _render_validation_report_expander(msg: Any, gate: str) -> None:
    validation = _as_dict(_msg_attr(msg, "validation", {}))
    if not validation:
        return
    errors = [item for item in (validation.get("errors") or []) if isinstance(item, dict)]
    warnings = [item for item in (validation.get("warnings") or []) if isinstance(item, dict)]
    infos = [item for item in (validation.get("infos") or []) if isinstance(item, dict)]
    score = _resolve_score(msg, validation)
    grade = str(validation.get("grade") or "").strip()
    report_gate = _normalize_gate(validation.get("gate")) or gate

    with st.expander("🧪 确定性校验报告", expanded=(gate == "fail")):
        header_bits = [f"得分 **{score}/100**"]
        if grade:
            header_bits.append(f"等级 **{html.escape(grade)}**")
        header_bits.append(f"门禁 **{_GATE_LABELS.get(report_gate, '未评估')}**")
        st.markdown(" · ".join(header_bits))

        summary = str(validation.get("summary") or "").strip()
        if summary:
            st.caption(summary)

        contexts = _as_str_list(validation.get("contexts"))
        if contexts:
            st.caption("识别到的物理场景：" + "、".join(_CONTEXT_LABELS.get(c, c) for c in contexts))

        if errors:
            st.markdown(_issues_html(errors, "❌", "#b91c1c"), unsafe_allow_html=True)
        if warnings:
            st.markdown(_issues_html(warnings, "⚠️", "#B45309"), unsafe_allow_html=True)
        if infos:
            st.markdown(_issues_html(infos[:6], "ℹ️", "#756E63", small=True), unsafe_allow_html=True)
            if len(infos) > 6:
                st.caption(f"另有 {len(infos) - 6} 条提示未显示。")

        if not errors and not warnings:
            st.markdown(
                "<div style='color:#15803d;font-size:12.8px;line-height:1.5;'>"
                "✅ 结构、TikZ 语法、物理图元与可读性检查全部通过。</div>",
                unsafe_allow_html=True,
            )

        checks_run = _as_str_list(validation.get("checks_run"))
        if checks_run:
            st.caption("已执行检查项：" + " · ".join(checks_run))

        metrics_caption = _metrics_caption(_as_dict(validation.get("metrics")))
        if metrics_caption:
            st.caption(metrics_caption)


def _render_fixes_expander(msg: Any) -> None:
    fixes = _as_str_list(_msg_attr(msg, "fixes_applied", []))
    if not fixes:
        return
    with st.expander(f"🛠️ 确定性自动修复记录（{len(fixes)} 项）", expanded=False):
        rows = "".join(
            f"<div style='margin:3px 0;font-size:12.5px;color:#26231E;line-height:1.5;'>"
            f"✅ {html.escape(item)}</div>"
            for item in fixes
        )
        st.markdown(rows, unsafe_allow_html=True)


def _render_compile_log_expander(msg: Any) -> None:
    compile_ok = bool(_msg_attr(msg, "compile_ok", True))
    compile_log = str(_msg_attr(msg, "compile_log", "") or "")
    error_kind = str(_msg_attr(msg, "compile_error_kind", "") or "")
    validation = _as_dict(_msg_attr(msg, "validation", {}))
    if compile_ok and not _has_compile_issue(validation):
        return

    with st.expander("📜 编译器日志", expanded=not compile_ok):
        meta_bits = ["编译 ✅" if compile_ok else "编译 ❌"]
        if error_kind:
            kind_label = (
                "DRIVER_CRASH（输出驱动崩溃，环境问题）"
                if is_driver_crash_kind(error_kind)
                else error_kind
            )
            meta_bits.append(f"错误类型：{html.escape(kind_label)}")
        st.caption(" · ".join(meta_bits))
        if compile_log:
            max_chars = 4000
            snippet = compile_log[:max_chars]
            if len(compile_log) > max_chars:
                snippet += "\n…（日志已截断，仅显示前 4000 字符）"
            st.code(snippet, language="text")
        else:
            st.caption("未捕获到 xelatex 原始日志。")


def _render_harness_expanders(msg: Any, gate: str) -> None:
    _render_prompt_metrics_caption(msg)
    _render_validation_report_expander(msg, gate)
    _render_fixes_expander(msg)
    _render_compile_log_expander(msg)


# ===================== Agent 主渲染函数 (现代化 Capsule 架构) =====================

def render_diagram_agent_dialog(
    content_state_key: str = "entry_content",
    key_prefix: str = "agent_diagram",
    base_dir: Optional[str] = None,
    use_expander: bool = False,
    expander_label: Optional[str] = None,
    expander_expanded: bool = False,
    target_file_path: Optional[str] = None,
    initial_content: Optional[str] = None,
    on_apply_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """
    渲染现代化 Agent 插图矫正胶囊与交互对话流
    """
    base_dir = base_dir or os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    agent = PiDiagramAgent(base_dir=base_dir)

    # 内容初始化（先读当前题目内容：⑧ 单题录入页需要按题目头部指纹隔离对话）
    if content_state_key not in st.session_state and initial_content is not None:
        st.session_state[content_state_key] = initial_content

    current_content = st.session_state.get(content_state_key, initial_content or "") or ""

    # 状态持久化键
    auto_sync_key = f"{key_prefix}_auto_sync"
    last_applied_tikz_key = f"{key_prefix}_last_applied_tikz"

    bridge_input_id = f"bridge_prompt_{key_prefix}"
    bridge_btn_id = f"bridge_btn_{key_prefix}"

    # ⑧ 单题录入页：按题目内容指纹隔离对话（换到另一道题=新对话；同题编辑正文=同一对话）。
    #    其它 key_prefix（如 browse_agent_*）保持 f"{key_prefix}_history"，行为完全不变。
    hist_key = f"{key_prefix}_history"
    hist_fingerprint = ""
    if key_prefix == "entry_single":
        hist_fingerprint = _question_history_fingerprint(current_content)
        if hist_fingerprint:
            hist_key = f"{key_prefix}_{hist_fingerprint}_history"

    if hist_key not in st.session_state:
        st.session_state[hist_key] = []
    if auto_sync_key not in st.session_state:
        st.session_state[auto_sync_key] = True

    # ⑦ 题目历史 LRU：只保留最近使用的 N 道题对话，释放旧题目占用的 session_state 内存
    _prune_question_histories(st.session_state, hist_key)

    existing_blocks = DiagramTools.extract_tikz_blocks(current_content)
    has_diagram = len(existing_blocks) > 0

    cfg = agent._get_api_config()
    model_display = cfg.get("model_name", "AI Agent")

    container_title = expander_label or (
        f"🤖 物理插图矫正AGENT · 已关联 {len(existing_blocks)} 处图形"
        if has_diagram
        else "🤖 物理插图矫正AGENT · 暂无插图"
    )

    wrapper_ctx = (
        st.expander(container_title, expanded=expander_expanded)
        if use_expander
        else st.container(border=True)
    )

    with wrapper_ctx:
        # 1. 微型标头（突出对话主题）
        st.markdown(
            f"""
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid rgba(214, 202, 185, 0.65);">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:1.02rem;">🤖</span>
                <span style="font-weight:600; color:var(--mc-text); font-size:0.92rem;">物理插图矫正AGENT</span>
                <span style="background:var(--mc-surface-soft); color:var(--mc-text); border:1px solid var(--mc-border); padding:1px 6px; border-radius:10px; font-size:11px;">Pi-Agent</span>
              </div>
              <div style="display:flex; align-items:center; gap:6px;">
                <span style="background:{'#14532d' if has_diagram else '#361b04'}; color:{'#86efac' if has_diagram else '#fde68a'}; border:1px solid {'#22c55e' if has_diagram else '#d97706'}; padding:1.5px 7px; border-radius:10px; font-size:11px; font-weight:500;">
                  {'🎨 已关联 TikZ' if has_diagram else '💡 对话全新生成'}
                </span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 2. 质量门禁一次性提示（run_turn 后 rerun 时显示，避免被 rerun 清掉）
        _pending_gate_notice = st.session_state.pop(f"{key_prefix}_gate_notice", None)
        if _pending_gate_notice:
            if isinstance(_pending_gate_notice, (tuple, list)) and len(_pending_gate_notice) >= 2:
                _notice_level, _notice_text = str(_pending_gate_notice[0]), str(_pending_gate_notice[1])
            else:
                _notice_level, _notice_text = "info", str(_pending_gate_notice)
            if _notice_level == "error":
                st.error(_notice_text)
            elif _notice_level == "warning":
                st.warning(_notice_text)
            else:
                st.info(_notice_text)

        # 3. 对话历史展示流（防御式还原 harness v2 扩展字段）
        history: List[AgentMessage] = [_restore_message(m) for m in st.session_state[hist_key]]

        if history:
            for m_idx, msg in enumerate(history):
                if msg.role == "user":
                    with st.chat_message("user", avatar="👤"):
                        st.markdown(f"**修改需求**：{msg.content}")
                        _render_user_images(msg)
                elif msg.role == "assistant":
                    with st.chat_message("assistant", avatar="🤖"):
                        # 激活领域技能徽章
                        if getattr(msg, "activated_skills", None):
                            skill_badges = " · ".join([
                                f"<span style='background:rgba(204,120,92,0.12);color:#B56345;border:1px solid rgba(204,120,92,0.35);padding:1px 6px;border-radius:4px;font-size:11px;'>"
                                f"🎯 {html.escape(s)}</span>"
                                for s in msg.activated_skills
                            ])
                            st.markdown(f"<div style='margin-bottom:8px;'>{skill_badges}</div>", unsafe_allow_html=True)

                        # Agent 推理展开
                        if msg.thought:
                            with st.expander("🧠 Agent 思考过程与坐标推导", expanded=False):
                                st.markdown(msg.thought)

                        # 说明
                        st.markdown(f"**修正说明**：{msg.content}")

                        # Harness v2：质量门禁徽章行 + 醒目提示（数据缺失时自动跳过）
                        quality_gate = _render_quality_badges(msg)

                        # 实时编译渲染图
                        if msg.preview_b64:
                            img_data = f"data:image/png;base64,{msg.preview_b64}"
                            st.markdown(
                                f"""<div style="text-align:center; background:#F5EFE6; padding:12px; border:1px solid rgba(214, 202, 185, 0.8); border-radius:10px; margin:8px 0;">
                                    <img src="{img_data}" style="max-width:100%; max-height:340px; background:#ffffff; border-radius:6px; padding:6px; box-shadow:0 6px 18px rgba(120,95,60,0.14); object-fit:contain;"/>
                                </div>""",
                                unsafe_allow_html=True,
                            )
                        elif msg.compile_error:
                            st.warning(f"⚠️ 编译提示: {msg.compile_error}")

                        # Harness v2：确定性校验报告 / 自动修复记录 / 编译器日志
                        _render_harness_expanders(msg, quality_gate)

                        # 源码
                        if msg.tikz_code:
                            with st.expander("💻 查看修正后的 TikZ 代码", expanded=False):
                                st.code(msg.tikz_code, language="latex")

                            # 单步应用按键（质量门禁未通过时自动同步已暂停，仅保留手动应用）
                            btn_label = "💾 保存至题目文件" if target_file_path else "✨ 应用此图到题目源码"
                            btn_help: Optional[str] = None
                            if quality_gate == "fail":
                                btn_label = "⚠️ 仍要应用此图"
                                btn_help = "该插图未通过确定性校验/质量门禁，自动同步已暂停；确认无误后可手动写入源码"
                            if st.button(
                                btn_label,
                                key=f"{key_prefix}_apply_btn_{m_idx}",
                                use_container_width=True,
                                help=btn_help,
                            ):
                                latest_base = st.session_state.get(content_state_key, initial_content or "")
                                updated = DiagramTools.apply_tikz_to_latex(latest_base, msg.tikz_code)
                                if not on_apply_callback:
                                    st.session_state[content_state_key] = updated
                                st.session_state[last_applied_tikz_key] = msg.tikz_code

                                if on_apply_callback:
                                    on_apply_callback(updated)
                                elif quality_gate == "fail":
                                    st.toast("已手动应用未通过校验的插图，请留意后续编译效果", icon="⚠️")
                                    st.rerun()
                                else:
                                    st.toast("已成功将修正后的插图写入题目源码！", icon="✅")
                                    st.rerun()

        # 4. 现代化 Agent 输入胶囊 (Unified Agent Prompt Capsule)
        render_modern_agent_capsule(
            target_bridge_id=bridge_input_id,
            target_btn_id=bridge_btn_id,
            model_name=model_display,
            key_prefix=key_prefix,
        )

        # 5. 轻量辅助工具（语音直接点胶囊里的 🎙️，不再需要音频文件转写弹层）
        c_sub1, c_sub2 = _safe_columns([1.2, 1.3], vertical_alignment="center")
        with c_sub1:
            if st.button("🔄 重新编译", key=f"{key_prefix}_recompile_btn", help="重新编译并渲染当前图", use_container_width=True):
                target_idx, current_code = _pick_recompile_target(history, existing_blocks)

                if not current_code:
                    st.toast("未检测到可编译的 TikZ 代码", icon="⚠️")
                else:
                    with st.spinner("⚡ 正在编译 TikZ 插图..."):
                        compile_result = _compile_tikz_with_log(current_code, base_dir)
                    compile_ok = bool(compile_result.get("ok"))
                    error_kind = str(compile_result.get("error_kind") or "")
                    driver_crash = is_driver_crash_kind(error_kind)

                    # 编译后立即做确定性校验；失败时把原始编译日志喂给校验器以产出 compile.* 问题
                    validation = _validate_tikz_code(
                        current_code,
                        question_text=st.session_state.get(content_state_key, initial_content or "") or "",
                        user_instruction=_last_user_instruction(history),
                        compile_log="" if compile_ok else str(compile_result.get("log") or ""),
                    )
                    score = _as_int(validation.get("score", 0), 0)
                    if compile_ok:
                        gate = _normalize_gate(validation.get("gate")) or _derive_gate(validation, score)
                        if not gate:
                            gate = "pass"
                    else:
                        # 编译失败时图形必然不可用：质量门禁直接判 fail（自动同步保持暂停）
                        gate = "fail"

                    target_msg = history[target_idx] if target_idx is not None else None
                    # 关键保护：瞬时「输出驱动崩溃」不得抹掉一条原本已编译成功、可应用的结果
                    preserve_previous = _should_preserve_after_recompile_failure(
                        previous_compile_ok=(
                            _msg_attr(target_msg, "compile_ok", False) if target_msg is not None else False
                        ),
                        previous_preview_b64=(
                            _msg_attr(target_msg, "preview_b64", "") if target_msg is not None else ""
                        ),
                        compile_result=compile_result,
                    )

                    # 回写最近一条 assistant 消息的 harness v2 字段并重跑渲染
                    if target_idx is not None and not preserve_previous:
                        _set_msg_field(target_msg, "compile_ok", compile_ok)
                        _set_msg_field(
                            target_msg,
                            "compile_error",
                            "" if compile_ok else _short_compile_error(compile_result),
                        )
                        _set_msg_field(target_msg, "compile_log", str(compile_result.get("log") or ""))
                        _set_msg_field(
                            target_msg,
                            "compile_error_kind",
                            "" if compile_ok else (error_kind or "COMPILE_ERROR"),
                        )
                        if compile_ok and compile_result.get("b64_png"):
                            _set_msg_field(target_msg, "preview_b64", compile_result["b64_png"])
                        if validation:
                            _set_msg_field(target_msg, "validation", validation)
                            _set_msg_field(target_msg, "validation_score", score)
                        _set_msg_field(target_msg, "quality_gate", gate)
                        # ⑦ 写回前瘦身：历史只保留 96px 缩略图，不再把原图 dataURL 留在 session_state
                        _compact_history_image_payloads(history)
                        st.session_state[hist_key] = [_serialize_message(m) for m in history]
                    # preserve_previous 时：不改写 compile_ok / preview_b64 / quality_gate / validation，
                    # 只显示 warning，用户仍可直接应用上次编译成功的结果

                    if compile_ok:
                        if validation:
                            gate_label = _GATE_LABELS.get(gate, "未评估")
                            st.toast(
                                f"重新编译成功 · 确定性校验得分 {score}/100（门禁：{gate_label}）",
                                icon="✅" if gate == "pass" else "⚠️",
                            )
                        else:
                            st.toast("编译成功！（确定性校验器不可用，未生成校验报告）", icon="✅")
                        st.rerun()
                    elif preserve_previous:
                        st.warning(
                            "🔄 本次重新编译遇到编译器输出驱动崩溃（xdvipdfmx / Windows 运行时故障，"
                            "如 0xC0000417）：这是瞬时环境问题，不是插图代码的语法错误；"
                            "已保留上次编译成功的预览图与质量门禁结果，你仍可直接应用上次结果，"
                            "或稍后再点一次「🔄 重新编译」。"
                        )
                    else:
                        # 失败时不 rerun：先给出即时错误，回写的编译日志会在下次交互后显示在消息内
                        short_error = _short_compile_error(compile_result)
                        if driver_crash:
                            st.warning(f"编译未通过（环境问题，非图形代码问题）: {short_error}")
                        else:
                            st.error(f"编译未通过: {short_error}")
        with c_sub2:
            if st.button("🧹 清空对话", key=f"{key_prefix}_clear_btn", help="清空当前 Agent 对话流", use_container_width=True):
                st.session_state[hist_key] = []
                st.rerun()

        # 6. 语音 / 文本回传桥（自定义组件：iframe -> Python 的官方通道）
        _bridge_payload = agent_voice_bridge(key=f"{key_prefix}_voice_bridge")
        instruction_bridge = ""
        bridge_images: List[Dict[str, Any]] = []
        send_trigger_clicked = False
        if isinstance(_bridge_payload, dict):
            _payload_id = str(_bridge_payload.get("id") or "")
            _last_id = st.session_state.get(f"{key_prefix}_last_bridge_id")
            if _payload_id and _payload_id != _last_id:
                st.session_state[f"{key_prefix}_last_bridge_id"] = _payload_id
                _kind = _bridge_payload.get("kind")
                if os.getenv("MC_DEBUG_BRIDGE", "") == "1":
                    print(
                        f"[MC_BRIDGE] kind={_kind} id={_payload_id} "
                        f"images={len(_bridge_payload.get('images') or [])}",
                        flush=True,
                    )
                if _kind == "text":
                    # 图片与文字一起回传；只有图片时补默认指令（_resolve_text_bridge_payload 纯函数）
                    instruction_bridge, bridge_images, send_trigger_clicked = _resolve_text_bridge_payload(
                        _bridge_payload
                    )
                elif _kind in ("pcm16", "audio"):
                    _lang = st.session_state.get("mc_voice_lang", "zh")
                    _recognized = ""
                    _errors = []
                    _whisper_elapsed = 0.0
                    if _kind == "pcm16":
                        try:
                            _pcm = base64.b64decode(_bridge_payload.get("pcm") or "", validate=False)
                        except Exception:
                            _pcm = b""
                        _rate = int(_bridge_payload.get("rate") or 16000)
                        print(f"[MC_VOICE] 收到 PCM16 bytes={len(_pcm)} rate={_rate}", flush=True)
                        if not _pcm:
                            st.error("🎙️ 语音数据为空，请重新录制。")
                        elif _pcm16_level(_pcm) < 120:
                            st.error("🎙️ 没有检测到说话声，请靠近麦克风再说一次。")
                        else:
                            with st.spinner("🎙️ 正在识别语音（本地 whisper）…"):
                                _t_whisper = time.time()
                                _ok_local, _local_text = _whisper_pcm16(_pcm, language=_lang)
                                _whisper_elapsed = time.time() - _t_whisper
                            if _ok_local and _is_whisper_noise(str(_local_text)):
                                _ok_local, _local_text = False, "只听到噪声，请再说一次。"
                            print(f"[MC_VOICE] 本地 whisper ok={_ok_local} text={str(_local_text)[:120]!r}", flush=True)
                            if _ok_local and str(_local_text).strip():
                                _recognized = str(_local_text).strip()
                            else:
                                _errors.append(str(_local_text))
                                # 兜底：包装成 WAV 交给已配置的多模态/whisper API
                                with st.spinner("🎙️ 本地识别不可用，改用在线转写…"):
                                    _wav = _pcm16_to_wav(_pcm, _rate)
                                    _ok_api, _api_text = agent.transcribe_audio(_wav, filename="mic_voice.wav")
                                print(f"[MC_VOICE] 在线转写 ok={_ok_api} text={str(_api_text)[:120]!r}", flush=True)
                                if _ok_api and str(_api_text).strip():
                                    _recognized = str(_api_text).strip()
                                else:
                                    _errors.append(str(_api_text))
                    else:
                        _mime = str(_bridge_payload.get("mime") or "audio/webm")
                        _ext = "webm"
                        if "ogg" in _mime:
                            _ext = "ogg"
                        elif "mp4" in _mime or "m4a" in _mime:
                            _ext = "m4a"
                        elif "wav" in _mime:
                            _ext = "wav"
                        try:
                            _audio_bytes = base64.b64decode(_bridge_payload.get("audio") or "", validate=False)
                        except Exception:
                            _audio_bytes = b""
                        print(f"[MC_VOICE] 收到音频 mime={_mime!r} bytes={len(_audio_bytes)}", flush=True)
                        if _audio_bytes:
                            with st.spinner("🎙️ 正在识别语音…"):
                                _ok_api, _api_text = agent.transcribe_audio(_audio_bytes, filename=f"mic_voice.{_ext}")
                            if _ok_api and str(_api_text).strip():
                                _recognized = str(_api_text).strip()
                            else:
                                _errors.append(str(_api_text))
                        else:
                            st.error("🎙️ 语音数据解码失败，请重新录制。")

                    if _recognized:
                        instruction_bridge = _recognized
                        send_trigger_clicked = True
                        st.toast(
                            f"🎙️ 语音识别完成（{_whisper_elapsed:.2f}s），已发送给 Agent",
                            icon="✅",
                        )
                    elif _errors:
                        st.error("🎙️ 语音识别失败：" + "；".join(e for e in _errors if e)[:300])

        # 检查是否有 pending 的音频转写结果
        if f"{key_prefix}_pending_prompt" in st.session_state:
            pending_val = st.session_state.pop(f"{key_prefix}_pending_prompt")
            instruction_bridge = pending_val
            send_trigger_clicked = True

        # 7. 处理执行 Agent 循环
        if send_trigger_clicked and (instruction_bridge.strip() or bridge_images):
            instruction = instruction_bridge.strip() or _DEFAULT_IMAGE_INSTRUCTION
            # 追加用户消息（含参考图片，对话历史里显示缩略图）
            user_msg = _build_user_agent_message(instruction, bridge_images)
            history.append(user_msg)
            # ⑦ 写回前瘦身：session_state 只留 96px thumb；原图仍在本轮 reference_images 里发给 Agent
            _compact_history_image_payloads(history)
            st.session_state[hist_key] = [_serialize_message(m) for m in history]

            # 启动 Agent 循环（仅当 Agent 侧已支持 reference_images 时才传入）
            with st.spinner(
                "🤖 物理插图矫正AGENT 正在推导几何关系并执行 XeLaTeX 编译自愈..."
                "（本地 AI 网关响应较慢时可能需要 1–4 分钟，请耐心等待，不要重复发送）"
            ):
                current_latex_input = st.session_state.get(content_state_key, initial_content or "")
                result = _run_turn_with_reference_images(
                    agent,
                    reference_images=bridge_images or None,
                    user_instruction=instruction,
                    current_question_latex=current_latex_input,
                    history=history[:-1],
                )

            if not result.get("ok"):
                st.error(result.get("error", "Agent 执行失败"))
                return

            agent_msg: AgentMessage = result["agent_message"]
            history.append(agent_msg)
            _compact_history_image_payloads(history)
            st.session_state[hist_key] = [_serialize_message(m) for m in history]

            # Harness v2 质量门禁：fail 时暂停自动同步，但保留消息内的手动应用按钮
            result_gate = _parse_result_gate(result, agent_msg)
            should_autosync = bool(result.get("should_autosync", True))
            if result_gate == "fail":
                should_autosync = False
                st.session_state[f"{key_prefix}_gate_notice"] = (
                    "error",
                    "🧪 确定性校验未通过：本次插图未通过质量门禁，自动同步已暂停。"
                    "请在对话中查看「🧪 确定性校验报告」；如确认图形可用，"
                    "可点击「⚠️ 仍要应用此图」手动写入源码。",
                )
            elif result_gate == "needs_review":
                st.session_state[f"{key_prefix}_gate_notice"] = (
                    "warning",
                    f"🧪 确定性校验需复核（得分 {_resolve_score(agent_msg)}/100）："
                    "已按质量门禁继续自动同步，建议在对话中检查校验报告。",
                )

            # 自动同步源码（用户开关 + harness 允许 + 存在更新内容，三者同时满足）
            if (
                st.session_state.get(auto_sync_key, True)
                and should_autosync
                and result.get("updated_latex")
            ):
                if not on_apply_callback:
                    st.session_state[content_state_key] = result["updated_latex"]
                st.session_state[last_applied_tikz_key] = result.get("tikz_code", "")
                if on_apply_callback:
                    on_apply_callback(result["updated_latex"])
                else:
                    st.toast("已自动同步修正后的插图至题目源码！", icon="✨")
                    st.rerun()
            else:
                if result_gate == "fail":
                    st.toast("自动同步已暂停，请在对话中人工确认后再应用此图", icon="⏸️")
                st.rerun()


_NOISE_PATTERNS = (
    "字幕製作", "字幕制作", "請不吝點贊", "请不吝点赞", "訂閱", "订阅", "打賞", "打赏",
    "谢谢观看", "謝謝觀看", "感谢观看", "感謝觀看", "amara.org", "subtitle", "Subtitles",
)


def _pcm16_level(pcm_bytes: bytes) -> float:
    """粗略估计音量（16 位 PCM 的平均绝对振幅），用于判断是否全是静音。"""
    try:
        import array
        import sys as _sys

        samples = array.array("h")
        samples.frombytes(pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)])
        if _sys.byteorder == "big":
            samples.byteswap()
        if not samples:
            return 0.0
        step = max(1, len(samples) // 8000)
        picked = samples[::step]
        return sum(abs(s) for s in picked) / max(1, len(picked))
    except Exception:
        return 999.0


def _is_whisper_noise(text: str) -> bool:
    """过滤 whisper 在静音/噪声上常见的「幻听」短语。"""
    clean = (text or "").strip()
    if not clean:
        return True
    lowered = clean.lower()
    for pattern in _NOISE_PATTERNS:
        if pattern.lower() in lowered:
            return True
    if len(clean) <= 1:
        return True
    return False
