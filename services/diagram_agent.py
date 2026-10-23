# -*- coding: utf-8 -*-
"""
Pi-Agent 物理与数学图形智能矫正 Agent 框架 (Harness v2)
(Pi Diagram Agent for LaTeX / TikZ Physical & Mathematical Graphics)

基于轻量开放的 Pi-Agent 架构设计，包含：
1. 状态与记忆管理 (AgentMemory / AgentState)
2. 领域工具集 (Tools: inspect, validate, compile_with_log, repair_generate, apply_to_latex)
3. 领域技能与插件扩展系统 (Skill Registry & Dynamic Intent Matching & Plugin Loader)
4. 多模态/语音转写服务 (Audio Transcription via Configured AI API)
5. 物理与数学图元专业自洽协议 (TikZ Physical & Geometric Constraints)
6. 自主感知-推理-修复-校验-编译闭环 (Autonomous ReAct & Self-Healing Loop)

Harness v2 强化（相对 v1）：
- 接入 `services/diagram_validate` 确定性校验层（结构/语法/语义/物理/几何/可读性六类）；
- 编译改为保留真实 XeLaTeX 日志，并用 `utils/tex_log_tools` 解析为「行号 + 类别 + 中文建议」；
- 每一轮执行「确定性修复 → 静态校验 → 编译 → 日志诊断 → 可选视觉校验」，并把问题回灌模型；
- 保留历史最佳候选，最终给出 score/grade/gate/validation/fixes/trace，质量门禁未通过时禁止自动同步；
- 结构类硬失败跳过无意义编译；确定性可修问题直接修复，不浪费模型轮次。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import re
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv

from .ai_service import (
    DEFAULT_LLM_READ_TIMEOUT,
    normalize_chat_completions_url,
    post_chat_completion,
    resolve_llm_timeouts,
)
from .agent_skills import SkillRegistry, get_default_skill_registry
from .diagram_validate import (
    apply_extra_issues,
    build_repair_instructions,
    format_report,
    structural_failure,
    validate_diagram,
)

logger = logging.getLogger(__name__)


# ===================== 编译错误类别（与 utils.tikz_ops 契约对齐） =====================
# utils.tikz_ops.compile_tikz 会返回 ERROR_KIND_DRIVER_CRASH == "DRIVER_CRASH"：
# TeX 已排版完成，但 PDF 输出驱动（xdvipdfmx）在 Windows 上崩溃（如 0xc0000417 /
# returncode -1073740777）。这类失败不是 TikZ 语法错误，必须与环境故障区分开。
# 这里按字符串契约接线，并保留本地常量：即使 T1 尚未落地也不会 ImportError。
ERROR_KIND_OK = "OK"
ERROR_KIND_COMPILE_ERROR = "COMPILE_ERROR"
ERROR_KIND_UNKNOWN_ERROR = "UNKNOWN_ERROR"
ERROR_KIND_DRIVER_CRASH = "DRIVER_CRASH"
# LLM 调用层失败类别（可读的 result["error_kind"]，用于 UI/日志区分网络问题与代码问题）
ERROR_KIND_LLM_TIMEOUT = "LLM_TIMEOUT"
ERROR_KIND_LLM_CONNECTION = "LLM_CONNECTION"
ERROR_KIND_LLM_ERROR = "LLM_ERROR"

# 输出驱动崩溃的日志签名（宽进严出：只在确实拿到崩溃特征时归为 DRIVER_CRASH）
_DRIVER_CRASH_LOG_PATTERNS = (
    re.compile(r"driver\s+return\s+code", re.IGNORECASE),
    re.compile(r"generating\s+output\s*[;:；]", re.IGNORECASE),
    re.compile(r"xdvipdfmx\.exe", re.IGNORECASE),
    re.compile(r"-1073740777|3221226519|0xc0000417", re.IGNORECASE),
)

DRIVER_CRASH_USER_MESSAGE = (
    "编译器输出驱动 xdvipdfmx 崩溃（Windows 运行时环境故障，如 0xC0000417），"
    "通常与图形代码无关，不是 TikZ 语法错误。已自动重试；请再点一次「🔄 重新编译」，"
    "若反复出现请简化图形（减少路径/曲线控制点、|坐标|≤30、避免生僻 Unicode 字符）或更新 TeX Live。"
)


def detect_driver_crash_kind(compile_log: str) -> str:
    """从 xelatex 原始日志识别「PDF 输出驱动崩溃」，命中返回 DRIVER_CRASH，否则返回空串。

    这是 utils.tex_log_tools.detect_driver_crash（T1 契约）的轻量兜底：当 T1 尚未落地、
    或上层拿到的是旧式 COMPILE_ERROR 时，仍能识别出「不是 TikZ 语法错误」的环境故障。
    """
    text = str(compile_log or "")
    if not text:
        return ""
    for pattern in _DRIVER_CRASH_LOG_PATTERNS:
        try:
            if pattern.search(text):
                return ERROR_KIND_DRIVER_CRASH
        except Exception:  # pragma: no cover - 正则防御
            continue
    return ""


def normalize_compile_error_kind(
    error_kind: Any,
    compile_ok: bool = False,
    compile_log: str = "",
) -> str:
    """统一编译错误类别，兼容 T1 落地前后的差异。

    - 编译成功 → "OK"；
    - 已有明确类别（DRIVER_CRASH / TIMEOUT / MISSING_XELATEX / ...）→ 原样保留；
    - 旧式 COMPILE_ERROR / UNKNOWN_ERROR / 空值 → 若日志命中驱动崩溃签名则升级为 DRIVER_CRASH。
    """
    kind = str(error_kind or "").strip()
    if compile_ok:
        return kind or ERROR_KIND_OK
    if kind and kind.upper() not in {ERROR_KIND_COMPILE_ERROR, ERROR_KIND_UNKNOWN_ERROR, "UNKNOWN"}:
        return kind.upper()
    return detect_driver_crash_kind(compile_log) or kind.upper() or ERROR_KIND_COMPILE_ERROR


def is_driver_crash_kind(error_kind: Any) -> bool:
    """判断 error_kind 是否为「输出驱动崩溃」（字符串契约，大小写不敏感）。"""
    return str(error_kind or "").strip().upper() == ERROR_KIND_DRIVER_CRASH


# ===================== LLM 调用失败（超时/连接/其它）的友好化 =====================
# 用户可见的错误文案必须是「中文 + 可操作」，不能把 requests 的原始英文异常
# （如 ``HTTPConnectionPool(host='127.0.0.1', port=8045): Read timed out.``）直接抛给用户。
LLM_TIMEOUT_ERROR_TEMPLATE = (
    "AI 网关 {gateway} 响应超时（等待 {read_seconds} 秒）。"
    "常见原因：本地 AI 网关（Antigravity Tools）卡死/未启动、上游网络拥塞。"
    "请重启该网关后重试；也可在 .env 设置 AI_READ_TIMEOUT=300（默认 240）并重启应用。"
)
LLM_CONNECTION_ERROR_TEMPLATE = (
    "无法连接 AI 网关 {gateway}（连接被拒绝或网络不可达）。"
    "常见原因：本地 AI 网关（Antigravity Tools）未启动/已退出、AI_BASE_URL 配置错误。"
    "请确认网关已启动后重试，并检查 .env 中的 AI_BASE_URL。"
)
LLM_GENERIC_ERROR_TEMPLATE = "Agent 调用异常：{reason}（原始：{exc_type}）"
_LLM_EXCEPTION_REASON_ZH = {
    "timeout": "AI 网关卡顿或上游网络拥塞，请求超时",
    "connection": "无法连接 AI 网关或网络中断",
    "sslerror": "AI 网关 TLS/证书校验失败",
    "proxyerror": "通过代理访问 AI 网关失败",
    "jsondecodeerror": "AI 返回内容不是合法 JSON",
    "httperror": "AI 网关返回 HTTP 错误",
    "chunkedencodingerror": "AI 响应流被中断",
    "invalidurl": "AI_BASE_URL 地址格式无效",
    "valueerror": "AI 返回内容格式不符合预期",
    "keyerror": "AI 返回内容缺少必要字段",
    "attributeerror": "AI 返回内容结构异常",
}


def classify_llm_error_kind(exc: BaseException) -> str:
    """把 LLM 调用异常归类为可读 error_kind（LLM_TIMEOUT / LLM_CONNECTION / LLM_ERROR）。"""
    try:
        if isinstance(exc, requests.exceptions.Timeout):
            return ERROR_KIND_LLM_TIMEOUT
        if isinstance(exc, requests.exceptions.ConnectionError):
            return ERROR_KIND_LLM_CONNECTION
    except Exception:  # pragma: no cover - 防御：requests 异常类型不可用时退回通用类别
        pass
    return ERROR_KIND_LLM_ERROR


def is_llm_network_exception(exc: BaseException) -> bool:
    """是否为网络层失败（超时或连接失败）——这类失败可先尝试「去图纯文字」重试。"""
    return classify_llm_error_kind(exc) in (ERROR_KIND_LLM_TIMEOUT, ERROR_KIND_LLM_CONNECTION)


def _llm_gateway_label(base_url: str) -> str:
    """从 AI_BASE_URL 提取可读的网关标识（如 ``127.0.0.1:8045``），绝不抛异常。"""
    text = str(base_url or "").strip()
    if not text:
        return "（未配置 AI_BASE_URL）"
    try:
        if "://" in text:
            netloc = urlsplit(text).netloc
        else:
            netloc = text.split("/", 1)[0]
        return netloc or text
    except Exception:  # pragma: no cover - 防御
        return text


def format_timeout_seconds(seconds: Any) -> str:
    """把超时秒数格式化为人类可读字符串（240.0 → '240'，12.5 → '12.5'）。"""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "240"
    if not math.isfinite(value):
        return "240"
    return str(int(value)) if value == int(value) else f"{value:g}"


def _coerce_llm_timeout_override(value: Any) -> Optional[Tuple[float, float]]:
    """校验显式传入的 LLM 超时覆盖值 (connect, read)；非法返回 None（回退 env/默认）。

    显式覆盖优先于环境变量，用于调用方（如测试、上层批处理）在个别慢请求上临时放宽超时。
    """
    if value is None:
        return None
    try:
        connect, read = value
        connect = float(connect)
        read = float(read)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(connect) and math.isfinite(read)):
        return None
    if connect <= 0 or read <= 0:
        return None
    return connect, read


def _llm_exception_reason_zh(exc: BaseException) -> str:
    """把异常类型映射为简短中文原因（绝不回传原始英文异常文本）。"""
    try:
        class_names = [cls.__name__.lower() for cls in type(exc).__mro__]
    except Exception:  # pragma: no cover - 防御
        class_names = []
    for name in class_names:
        for key, reason in _LLM_EXCEPTION_REASON_ZH.items():
            if key in name:
                return reason
    return "调用 AI 服务失败"


def describe_llm_call_error(
    exc: BaseException,
    base_url: str = "",
    read_timeout: Any = None,
) -> Tuple[str, str]:
    """生成 (error_kind, 中文可操作错误文案)。"""
    kind = classify_llm_error_kind(exc)
    gateway = _llm_gateway_label(base_url)
    if kind == ERROR_KIND_LLM_TIMEOUT:
        try:
            seconds = read_timeout if read_timeout is not None else resolve_llm_timeouts()[1]
        except Exception:  # pragma: no cover - 防御
            seconds = DEFAULT_LLM_READ_TIMEOUT
        return kind, LLM_TIMEOUT_ERROR_TEMPLATE.format(
            gateway=gateway, read_seconds=format_timeout_seconds(seconds)
        )
    if kind == ERROR_KIND_LLM_CONNECTION:
        return kind, LLM_CONNECTION_ERROR_TEMPLATE.format(gateway=gateway)
    return kind, LLM_GENERIC_ERROR_TEMPLATE.format(
        reason=_llm_exception_reason_zh(exc),
        exc_type=type(exc).__name__,
    )


def _replace_first_user_message(
    messages: List[Dict[str, Any]],
    index: int,
    text: str,
) -> List[Dict[str, Any]]:
    """返回一份新 messages：把首条本轮 user 消息替换为纯文字（不就地改写已发请求）。"""
    replaced = list(messages)
    if 0 <= index < len(replaced):
        replaced[index] = {"role": "user", "content": text}
    return replaced


def _build_llm_error_result(
    exc: BaseException,
    base_url: str,
    read_timeout: Any,
    current_question_latex: str,
    reference_images: List[Dict[str, Any]],
    image_input_degraded: bool,
    image_fallback_status: Optional[int],
    image_fallback_reason: str,
    prompt_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造 LLM 调用失败（含超时/连接失败）的中文可操作返回结构。"""
    error_kind, error_text = describe_llm_call_error(exc, base_url, read_timeout=read_timeout)
    if image_fallback_reason in (ERROR_KIND_LLM_TIMEOUT, ERROR_KIND_LLM_CONNECTION):
        error_text += "（已尝试去掉参考图片、仅用文字重试，仍然失败。）"
    return {
        "ok": False,
        "error": error_text,
        "error_kind": error_kind,
        "agent_message": None,
        "updated_latex": current_question_latex,
        "reference_image_count": len(reference_images),
        "image_input_degraded": image_input_degraded,
        "image_fallback_status": image_fallback_status,
        "image_fallback_reason": image_fallback_reason,
        "prompt_metrics": dict(prompt_metrics or {}),
    }


# ===================== 参考图片（多模态输入） =====================
# 用户可随修改指令附带参考图（课本插图/截图）。这些图以 OpenAI 兼容的
# image_url（data URL）形式只随「本轮用户消息」发送：
#   - 不写入 history 重放（history 仍只回放文字，避免重复计费/超长）；
#   - 不写入 compile_log 等日志/返回字段（隐私与体积）；
#   - 网关/模型不支持图片（非 200）时自动去掉图片、仅用文字重试一次。

REFERENCE_IMAGE_ALLOWED_MIMES = ("image/png", "image/jpeg", "image/webp")
_REFERENCE_IMAGE_DATA_URL_RE = re.compile(
    r"^data:(image/(?:png|jpe?g|webp));base64,", re.IGNORECASE
)
REFERENCE_IMAGES_NOTE_TEMPLATE = (
    "【用户附带的参考图片】共 {count} 张，已随本消息提供；"
    "请先理解图片中的图形结构/标注/方向，再据此修改当前 TikZ 插图。"
)
REFERENCE_IMAGES_DEGRADED_NOTE = (
    "（注：当前模型/网关不支持图片输入，参考图片已省略；"
    "请仅依据题干文字与用户的修改指令完成本次矫正。）"
)
REFERENCE_IMAGES_DEGRADED_NOTICE = "（注：当前模型不支持图片输入，已仅用文字继续。）"
REFERENCE_IMAGES_TIMEOUT_NOTICE = (
    "（注：带参考图片的请求超时，已自动去掉图片、改用纯文字重试；"
    "若仍失败请检查本地 AI 网关，或调大 .env 的 AI_READ_TIMEOUT。）"
)
REFERENCE_IMAGES_CONNECTION_NOTICE = (
    "（注：带参考图片的请求连接失败，已自动去掉图片、改用纯文字重试。）"
)
REFERENCE_IMAGES_DEFAULT_INSTRUCTION = "请参考我提供的参考图片修正当前插图。"


def reference_images_degraded_notice(reason: str) -> str:
    """按图片降级原因返回面向用户的说明文案。"""
    if reason == ERROR_KIND_LLM_TIMEOUT:
        return REFERENCE_IMAGES_TIMEOUT_NOTICE
    if reason == ERROR_KIND_LLM_CONNECTION:
        return REFERENCE_IMAGES_CONNECTION_NOTICE
    return REFERENCE_IMAGES_DEGRADED_NOTICE


def _normalize_reference_images(
    reference_images: Optional[List[Dict[str, Any]]],
    max_images: int = 4,
    max_bytes_each: int = 3_000_000,
    max_total: int = 8_000_000,
) -> List[Dict[str, Any]]:
    """校验并规范化用户随指令附带的参考图片（绝不抛异常）。

    返回 ``[{"name", "mime", "data_url"}]``。以下输入一律被静默丢弃（而不是抛错）：
    非 list/tuple 容器、非 dict 元素、``data_url`` 不是
    ``data:image/(png|jpeg|webp);base64,`` 前缀、base64 解码失败、
    单张超过 ``max_bytes_each``、累计超过 ``max_total``、数量超过 ``max_images``。
    缺省 ``name`` 统一生成 ``reference-N.png``，``mime`` 由 data URL 头解析。
    """
    try:
        if not isinstance(reference_images, (list, tuple)):
            return []
        try:
            max_images = int(max_images)
            max_bytes_each = int(max_bytes_each)
            max_total = int(max_total)
        except (TypeError, ValueError):
            max_images, max_bytes_each, max_total = 4, 3_000_000, 8_000_000
        if max_images <= 0 or max_bytes_each <= 0 or max_total <= 0:
            return []

        normalized: List[Dict[str, Any]] = []
        total_bytes = 0
        for item in reference_images:
            if len(normalized) >= max_images:
                break
            if not isinstance(item, dict):
                continue
            data_url = item.get("data_url")
            if not isinstance(data_url, str):
                continue
            data_url = data_url.strip()
            match = _REFERENCE_IMAGE_DATA_URL_RE.match(data_url)
            if not match:
                continue
            mime = match.group(1).strip().lower()
            if mime == "image/jpg":  # 兼容部分浏览器/剪贴板的 jpg 别名
                mime = "image/jpeg"
            if mime not in REFERENCE_IMAGE_ALLOWED_MIMES:
                continue
            payload = re.sub(r"\s+", "", data_url[match.end():])
            if not payload:
                continue
            try:
                raw = base64.b64decode(payload, validate=True)
            except Exception:
                continue
            if not raw or len(raw) > max_bytes_each:
                continue
            if total_bytes + len(raw) > max_total:
                continue
            total_bytes += len(raw)
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                # 契约：缺省文件名统一为 reference-N.png（与 UI 回传桥一致）
                name = f"reference-{len(normalized) + 1}.png"
            normalized.append({
                "name": name.strip()[:120],
                "mime": mime,
                "data_url": data_url,
            })
        return normalized
    except Exception:  # pragma: no cover - 防御：任何异常都退化为「无参考图」
        return []


def _build_reference_image_content(
    context_prompt: str,
    images: List[Dict[str, Any]],
) -> Any:
    """构造 OpenAI 兼容的多模态 user content。

    没有图片时原样返回纯字符串（保持既有行为与测试不变）；有图片时返回
    ``[{"type": "text", "text": ...}, {"type": "image_url", "image_url": {"url": ..., "detail": "high"}}, ...]``，
    并在 text 末尾追加参考图数量说明。
    """
    if not images:
        return context_prompt
    content: List[Dict[str, Any]] = [{
        "type": "text",
        "text": str(context_prompt) + "\n\n" + REFERENCE_IMAGES_NOTE_TEMPLATE.format(count=len(images)),
    }]
    for image in images:
        data_url = str(image.get("data_url") or "")
        if not data_url:
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url, "detail": "high"},
        })
    if len(content) == 1:
        return context_prompt
    return content


def _parse_message_images(value: Any) -> List[Dict[str, Any]]:
    """容错解析 AgentMessage.images（旧会话无此键 / 坏数据都不抛异常）。

    只保留「dict 且 data_url 为 data:image/... 字符串」的元素；
    非 list 容器、非 dict 元素、缺 data_url 的元素全部丢弃。
    """
    if not isinstance(value, list):
        return []
    images: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        data_url = item.get("data_url")
        if not isinstance(data_url, str):
            continue
        data_url = data_url.strip()
        if not data_url.lower().startswith("data:image/"):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = f"reference-{len(images) + 1}.png"
        mime = item.get("mime")
        if not isinstance(mime, str) or not mime.strip():
            mime = "image/png"
        images.append({
            "name": name.strip()[:120],
            "mime": mime.strip().lower(),
            "data_url": data_url,
        })
    return images


# ===================== 上下文体积优化（①③④⑨） =====================
# 用户可在 .env 调整上下文预算；非法/越界值一律回退默认并 clamp，绝不抛异常：
#   PI_AGENT_HISTORY_MESSAGES   回放历史消息条数上限（默认 4，clamp 0..8；0 = 不回放）
#   PI_AGENT_HISTORY_MAX_CHARS  回放历史字符预算（默认 4000，clamp 1000..20000）
#   PI_AGENT_SKILL_PROMPT_MAX   注入 prompt 的技能数（默认 2，clamp 1..3；
#                               技能流水线 / activated_skills badge 仍用最多 3 个）
DEFAULT_HISTORY_MESSAGES = 4
DEFAULT_HISTORY_MAX_CHARS = 4000
DEFAULT_SKILL_PROMPT_MAX = 2
HISTORY_MESSAGES_ENV = "PI_AGENT_HISTORY_MESSAGES"
HISTORY_MAX_CHARS_ENV = "PI_AGENT_HISTORY_MAX_CHARS"
SKILL_PROMPT_MAX_ENV = "PI_AGENT_SKILL_PROMPT_MAX"

# ① 提示词去重：题目用「旧 TikZ 已替换为占位符」的版本，修改后的图单独给 current_tikz
QUESTION_LATEX_CONTEXT_LABEL = "【当前题目 LaTeX（原插图已用占位符替换，修改后的图见下方 TikZ）】："

# ⑨ prompt 体积估算口径（粗略，仅用于 UI 展示/趋势观察，不参与任何门禁判断）
PROMPT_TOKEN_CHARS_RATIO = 1.6


def _read_clamped_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """读取整型环境变量并 clamp 到 [minimum, maximum]；缺失/非法值回退 default。"""
    try:
        raw = os.getenv(name)
    except Exception:  # pragma: no cover - 防御
        raw = None
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _message_field(message: Any, name: str, default: Any = "") -> Any:
    """防御式读取历史消息字段（兼容 AgentMessage 对象与旧会话 dict）。"""
    if isinstance(message, dict):
        value = message.get(name, default)
    else:
        try:
            value = getattr(message, name, default)
        except Exception:  # pragma: no cover - 极端对象异常兜底
            value = default
    return default if value is None else value


def _select_history_for_prompt(
    history: Optional[List[Any]],
    max_messages: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> List[Any]:
    """③ 按「最近条数 + 字符预算」选择要回放的历史消息（纯函数，绝不抛异常）。

    - 先取最近 ``max_messages``（默认读 PI_AGENT_HISTORY_MESSAGES，clamp 0..8）条；
    - 再从最新往前累计 ``len(content)``，加入该条会超过 ``max_chars``
      （默认读 PI_AGENT_HISTORY_MAX_CHARS，clamp 1000..20000）的旧消息丢弃；
    - 至少保留最新 1 条（即使单条本身超预算）；
    - 历史内容本身**不做任何压缩/截断**（仍回放原始文本，只决定回放哪些消息）。
    """
    try:
        if not history:
            return []
        if max_messages is None:
            max_messages = _read_clamped_int_env(
                HISTORY_MESSAGES_ENV, DEFAULT_HISTORY_MESSAGES, 0, 8
            )
        else:
            try:
                max_messages = max(0, min(8, int(max_messages)))
            except (TypeError, ValueError):
                max_messages = DEFAULT_HISTORY_MESSAGES
        if max_chars is None:
            max_chars = _read_clamped_int_env(
                HISTORY_MAX_CHARS_ENV, DEFAULT_HISTORY_MAX_CHARS, 1000, 20000
            )
        else:
            try:
                max_chars = max(0, min(20000, int(max_chars)))
            except (TypeError, ValueError):
                max_chars = DEFAULT_HISTORY_MAX_CHARS
        if max_messages <= 0:
            return []

        recent = list(history)[-max_messages:]
        selected_reversed: List[Any] = []
        used_chars = 0
        for message in reversed(recent):
            content = _message_field(message, "content", "")
            length = len(str(content))
            if selected_reversed and used_chars + length > max_chars:
                break
            selected_reversed.append(message)
            used_chars += length
        selected_reversed.reverse()
        return selected_reversed
    except Exception:  # pragma: no cover - 防御：任何异常都退化为「不回放历史」
        return []


def _content_char_count(content: Any) -> int:
    """计算一条 OpenAI content 的字符数；多模态数组只计 text，不含 data_url（图片体积）。"""
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += len(str(part.get("text") or ""))
        return total
    return len(str(content or ""))


def _estimate_prompt_tokens(chars: int) -> int:
    """粗略估算 token 数（中文约 1.6 字符/token）；仅用于展示，非精确计费口径。"""
    try:
        return max(1, round(int(chars) / PROMPT_TOKEN_CHARS_RATIO))
    except (TypeError, ValueError):
        return 1


def _compute_prompt_metrics(
    messages: Optional[List[Dict[str, Any]]] = None,
    history_messages: int = 0,
    skill_prompt_count: int = 0,
    activated_skill_count: int = 0,
    question_chars: int = 0,
    tikz_chars: int = 0,
) -> Dict[str, Any]:
    """⑨ 计算本轮 prompt 体积指标（字符 / 估算 token / 历史条数 / 技能数等）。"""
    try:
        chars = 0
        for message in messages or []:
            if isinstance(message, dict):
                chars += _content_char_count(message.get("content"))
        return {
            "chars": int(chars),
            "est_tokens": _estimate_prompt_tokens(chars),
            "history_messages": int(history_messages or 0),
            "skill_prompt_count": int(skill_prompt_count or 0),
            "activated_skill_count": int(activated_skill_count or 0),
            "question_chars": int(question_chars or 0),
            "tikz_chars": int(tikz_chars or 0),
        }
    except Exception:  # pragma: no cover - 防御：指标绝不影响主流程
        return {
            "chars": 0,
            "est_tokens": 1,
            "history_messages": int(history_messages or 0),
            "skill_prompt_count": int(skill_prompt_count or 0),
            "activated_skill_count": int(activated_skill_count or 0),
            "question_chars": int(question_chars or 0),
            "tikz_chars": int(tikz_chars or 0),
        }


# ===================== 系统提示词与领域规则 =====================

PI_AGENT_DIAGRAM_SYSTEM_PROMPT = """你是由 UtopiaPhyCyclus 内嵌的专业物理与数学插图智能矫正 Agent（基于 Pi-Agent 架构）。
你的任务：理解用户通过自然语言（或语音识别）提出的修改诉求，结合题目上下文与现有 TikZ 源码，精准输出修正后的完整 \\begin{tikzpicture}...\\end{tikzpicture} 代码以及简短明了的修改说明。

【物理与数学几何严谨性准则】
1. 几何与坐标系：
   - 默认采用标准笛卡尔坐标系（右为 +X，上为 +Y）。
   - 标注倾角（如斜面倾角 θ、力与水平夹角 α）时，必须以实际基准线与目标线之间的几何关系为准，角度弧必须用正确的起点角与终点角。
   - 优先使用具名坐标，例如 \\coordinate (O) at (0,0); \\coordinate (A) at (3,2); 并在后续连线、弧线和标注中复用。

2. 物理图元规范：
   - 受力分析：必须标明力的作用点与方向。力箭头建议使用 `-latex` 或 `->, >=latex`，并使用加粗线（如 `thick`）。
   - 斜面与地面：倾斜或水平支撑面必须画出阴影剖面线，可使用 `pattern=north east lines` 或画细斜线。
   - 弹簧：统一采用波浪弯曲装饰线（例如 `[decorate, decoration={coil, aspect=0.5, segment length=3mm, amplitude=2mm}]`）或规则折线。
   - 滑轮与轻绳：轻绳相切于滑轮边缘，滑轮用带圆心的圆形绘制。
   - 质点/小球：小球用 `\\fill (x,y) circle (0.15);` 或带描边绘制，并标出质心。
   - 电学电路与电磁场：点状“垂直纸面向外”用 `\\odot` 或带圈圆点，叉状“垂直纸面向里”用 `\\otimes` 或画叉；导线交叉且连接处必须点实心圆点。
   - 光学：入射光线、反射光线和折射光线必须在射线中间或适当位置绘制前进箭头；法线用虚线。
   - 运动轨迹：抛物线、圆弧运动轨迹推荐使用虚线 `dashed`。

3. TikZ 语法安全与编译保障（至关重要）：
   - 必须使用标准已支持的库：`calc, positioning, intersections, arrows.meta, quotes, angles, patterns, decorations.pathmorphing, decorations.pathreplacing, decorations.markings`。
   - 节点文本中的所有公式、角度符号、希腊字母或上下标必须严格放在 `$...$` 内，例如 `node[above] {$F_N$}`、`node[right] {$30^\\circ$}`、`node {$v_0$}`，严禁裸写数学符号！
   - 禁止使用 Unicode 数学符号（θ、°、×、⊙），一律写成 `$\\theta$`、`$30^\\circ$`、`$\\times$`、`$\\odot$`。
   - 每个 \\draw/\\fill/\\path/\\node/\\coordinate 语句必须以分号 `;` 结尾；跨行书写时只在语句真正结束时写分号。
   - 禁止输出 Markdown 外部代码块反引号（禁止 ```latex、``` 等），只输出纯代码及自然段落。
   - 严禁包含 \\documentclass、\\usepackage 等完整文档头，仅输出完整的 \\begin{tikzpicture}...\\end{tikzpicture} 环境。
   - 严禁使用模板未加载的宏包命令：\\ce/\\pu（mhchem）、\\SI/\\qty/\\si/\\unit/\\num（siunitx）、\\vb/\\va/\\dv/\\pdv/\\abs/\\norm（physics）；单位请写成 `$F=10\\,\\mathrm{N}$`。

4. Harness 验收标准（你的输出会被确定性校验器逐项检查，未通过会进入下一轮修复）：
   - 结构：恰好一个 tikzpicture 环境；花括号、$、环境均配平；
   - 语法：路径语句以 ; 结束；node 花括号闭合；具名坐标先定义后引用；不引用模板未预载库；
   - 语义：node 数学符号包 $...$；数学模式中文字必须写 \\text{中文}；
   - 物理：受力图有箭头且有力标注；斜面有角度弧或角度标注；垂直纸面磁场有 ⊙/⊗；光路有箭头与法线；电路闭合且连接处有实心点；弹簧用 coil；滑轮为圆且有切线；
   - 几何：题干给出角度（如 30°）时，图中斜线实际倾角应与之一致（误差 ≤ 8°）；
   - 可读性：标注不重叠，坐标量级适中（建议 |x|,|y| ≤ 10）。

【输出格式要求（严格遵守）】
请按以下三段结构输出，便于 Agent 准确提取：
===THOUGHT===
(此处简要说明你的物理和几何分析、坐标计算以及修改思路，2~4句话即可)
===TIKZ===
\\begin{tikzpicture}[...]
...
\\end{tikzpicture}
===EXPLANATION===
(此处向用户汇报本次修改的具体内容，例如：“已将斜面倾角由45度调小至30度，并将滑块的受力分析图调整为垂直斜面向上的支持力与沿斜面向下的重力分力。”)
"""


PI_AGENT_VISUAL_SYSTEM_PROMPT = """你是高中物理插图审图专家。你会看到一道物理题的题干、用户对插图的修改指令，以及当前 TikZ 编译后的渲染图。
请从物理正确性、图形完整性与可读性三个角度审图，重点判断：
1. 图中是否包含了题干/指令要求的物理图元（力箭头与标注、斜面与角度、光线与法线、磁场符号、电路元件与结点、弹簧、滑轮等）；
2. 图中标注的物理量与题干是否一致（角度、方向、连接关系、编号）；
3. 是否存在明显错位、遮挡、缺漏或与题意矛盾的画法。
只输出一个 JSON 对象（不要 Markdown 围栏、不要额外解释），格式：
{"match": true/false, "score": 0-100 的整数, "issues": ["具体问题1", "具体问题2"], "summary": "一句话结论"}
判断标准：图形与题意完全一致给 85-100；有小瑕疵但不影响理解给 60-84；有明显物理错误或缺失关键图元给 0-59，并在 issues 中逐条指出。"""


# ===================== 工具与执行层 =====================


def _as_compile_fields(result: Any) -> Dict[str, Any]:
    """把 compile_tikz 返回值或旧式三元组统一整理为字段字典。"""
    if isinstance(result, tuple):
        ok = bool(result[0]) if len(result) > 0 else False
        b64 = result[1] if len(result) > 1 else None
        err = result[2] if len(result) > 2 else None
        log = str(err or "")
        return {
            "ok": ok,
            "b64_png": b64,
            "log": log,
            "stdout": "",
            "error_kind": normalize_compile_error_kind("", compile_ok=ok, compile_log=log),
            "error_line": None,
            "duration_s": 0.0,
            "source_line_offset": 0,
            "driver_exit_code": None,
            "compile_retries": 0,
        }
    ok = bool(getattr(result, "ok", False))
    log = getattr(result, "log", "") or ""
    return {
        "ok": ok,
        "b64_png": getattr(result, "b64_png", None),
        "log": log,
        "stdout": getattr(result, "stdout", "") or "",
        "error_kind": normalize_compile_error_kind(
            getattr(result, "error_kind", ""), compile_ok=ok, compile_log=log
        ),
        "error_line": getattr(result, "error_line", None),
        "duration_s": float(getattr(result, "duration_s", 0.0) or 0.0),
        "source_line_offset": int(getattr(result, "source_line_offset", 0) or 0),
        # T1 契约新增字段；旧实现没有这两个属性时安全降级
        "driver_exit_code": getattr(result, "driver_exit_code", None),
        "compile_retries": int(getattr(result, "compile_retries", 0) or 0),
    }


def _relativize_tex_log(log: str, offset: int, source: str) -> str:
    """把 xelatex 日志里的文档行号换算成 TikZ 片段内行号（便于模型定位到自己的代码）。

    仅换算落在片段行范围内的行号；范围外或无法解析的一律保持原样。
    """
    if not log or offset <= 0:
        return log
    try:
        max_line = max(1, (source or "").count("\n") + 1)
    except Exception:
        max_line = 10000

    def _fix(match: re.Match) -> str:
        try:
            fragment_line = int(match.group(0)) - offset
        except (TypeError, ValueError):
            return match.group(0)
        if 1 <= fragment_line <= max_line:
            return str(fragment_line)
        return match.group(0)

    try:
        text = re.sub(r"(?<=:)\d+(?=:)", _fix, log)
        text = re.sub(r"(?<=l\.)\d+", _fix, text)
        return text
    except Exception:
        return log


def _tex_log_summarize(log: str, source: str = "", max_chars: int = 3000) -> str:
    try:
        from utils.tex_log_tools import summarize_tex_log
        return summarize_tex_log(log, source=source or None, max_chars=max_chars)
    except Exception:
        text = (log or "").strip()
        return text[-max_chars:] if text else ""


def _tex_log_diagnose(log: str) -> str:
    try:
        from utils.tex_log_tools import diagnose_tex_error
        return diagnose_tex_error(log)
    except Exception:
        return "编译失败，请检查 TikZ 语法、花括号与分号是否完整。"


class DiagramTools:
    """Pi Agent 专用图元工具箱"""

    @staticmethod
    def extract_tikz_blocks(latex_text: str) -> List[Dict[str, Any]]:
        """从 LaTeX 正文中提取所有 tikzpicture 块"""
        if not latex_text:
            return []
        pattern = r"(\\begin\{tikzpicture\}(?:\[.*?\])?[\s\S]*?\\end\{tikzpicture\})"
        blocks = []
        for match in re.finditer(pattern, latex_text):
            code = match.group(1).strip()
            blocks.append({
                "code": code,
                "start": match.start(),
                "end": match.end(),
            })
        return blocks

    @staticmethod
    def compile_preview(tikz_code: str, base_dir: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        验证并编译 TikZ 代码，生成 base64 预览图片（兼容旧接口）
        返回: (is_success, base64_image, error_message)
        """
        from utils.tikz_ops import get_tikz_image_b64
        try:
            b64_data, err = get_tikz_image_b64(tikz_code, base_dir=base_dir)
            if b64_data:
                return True, b64_data, None
            return False, None, err or "Compilation failed without specific error."
        except Exception as exc:
            return False, None, str(exc)

    @staticmethod
    def compile_with_log(tikz_code: str, base_dir: str) -> Any:
        """
        Harness v2 编译入口：返回带原始日志的编译结果（TikzCompileResult 或兼容对象）。
        若 utils.tikz_ops.compile_tikz 不可用，则回退旧接口。
        """
        try:
            from utils.tikz_ops import compile_tikz
            return compile_tikz(tikz_code, base_dir=base_dir)
        except Exception as exc:
            logger.info("compile_tikz unavailable, fallback to compile_preview: %s", exc)
            ok, b64, err = DiagramTools.compile_preview(tikz_code, base_dir)
            err_text = str(err or "")
            return SimpleNamespace(
                ok=ok, b64_png=b64, log=err_text, stdout="",
                error_kind=normalize_compile_error_kind("", compile_ok=ok, compile_log=err_text),
                error_line=None, duration_s=0.0,
                driver_exit_code=None, compile_retries=0,
            )

    @staticmethod
    def apply_tikz_to_latex(full_latex: str, new_tikz: str, target_index: int = 0) -> str:
        """
        将修正后的 TikZ 代码注入或替换回原 LaTeX 正文中
        """
        if not full_latex:
            return new_tikz

        existing_blocks = DiagramTools.extract_tikz_blocks(full_latex)
        if existing_blocks and 0 <= target_index < len(existing_blocks):
            target = existing_blocks[target_index]
            before = full_latex[:target["start"]]
            after = full_latex[target["end"]:]
            return before + new_tikz + after

        # 如果原题没有 tikzpicture，则优先插入在 \begin{problem} 内部题干末尾或 choices 前面
        if "\\begin{choices}" in full_latex:
            parts = full_latex.split("\\begin{choices}", 1)
            return parts[0].rstrip() + "\n\n" + new_tikz + "\n\n\\begin{choices}" + parts[1]
        elif "\\end{problem}" in full_latex:
            parts = full_latex.rsplit("\\end{problem}", 1)
            return parts[0].rstrip() + "\n\n" + new_tikz + "\n\\end{problem}" + parts[1]
        else:
            return full_latex.rstrip() + "\n\n" + new_tikz


# ===================== Agent 记忆与状态 =====================


class AgentMessage:
    def __init__(
        self,
        role: str,
        content: str,
        thought: str = "",
        tikz_code: str = "",
        preview_b64: str = "",
        compile_ok: bool = True,
        compile_error: str = "",
        activated_skills: Optional[List[str]] = None,
        validation: Optional[Dict[str, Any]] = None,
        validation_score: int = 0,
        quality_gate: str = "",
        fixes_applied: Optional[List[str]] = None,
        attempts: int = 0,
        compile_log: str = "",
        compile_error_kind: str = "",
        visual_check: Optional[Dict[str, Any]] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        prompt_metrics: Optional[Dict[str, Any]] = None,
    ):
        self.role = role  # "user" | "assistant" | "system"
        self.content = content
        self.thought = thought
        self.tikz_code = tikz_code
        self.preview_b64 = preview_b64
        self.compile_ok = compile_ok
        self.compile_error = compile_error
        self.activated_skills = activated_skills or []
        # Harness v2 校验/门禁字段（全部带默认值，兼容旧会话反序列化）
        self.validation = validation or {}
        self.validation_score = validation_score
        self.quality_gate = quality_gate
        self.fixes_applied = fixes_applied or []
        self.attempts = attempts
        self.compile_log = compile_log
        self.compile_error_kind = compile_error_kind
        self.visual_check = visual_check or {}
        # 多模态参考图（仅用户消息使用；元素形如 {"name","mime","data_url"}）
        # 只用于 UI 历史渲染/序列化，不参与 history 重放、不写入日志字段。
        self.images = images or []
        # ⑨ 本轮 prompt 体积指标（chars/est_tokens/history_messages/skill 数等）；
        # 旧会话缺省 {}，UI 防御式读取展示。
        self.prompt_metrics = prompt_metrics or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "thought": self.thought,
            "tikz_code": self.tikz_code,
            "preview_b64": self.preview_b64,
            "compile_ok": self.compile_ok,
            "compile_error": self.compile_error,
            "activated_skills": self.activated_skills,
            "validation": self.validation,
            "validation_score": self.validation_score,
            "quality_gate": self.quality_gate,
            "fixes_applied": self.fixes_applied,
            "attempts": self.attempts,
            "compile_log": self.compile_log,
            "compile_error_kind": self.compile_error_kind,
            "visual_check": self.visual_check,
            "images": self.images,
            "prompt_metrics": self.prompt_metrics,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentMessage":
        data = data or {}
        return cls(
            role=data.get("role", "assistant"),
            content=data.get("content", ""),
            thought=data.get("thought", ""),
            tikz_code=data.get("tikz_code", ""),
            preview_b64=data.get("preview_b64", ""),
            compile_ok=data.get("compile_ok", True),
            compile_error=data.get("compile_error", ""),
            activated_skills=data.get("activated_skills", []),
            validation=data.get("validation") or {},
            validation_score=int(data.get("validation_score") or 0),
            quality_gate=data.get("quality_gate", "") or "",
            fixes_applied=data.get("fixes_applied") or [],
            attempts=int(data.get("attempts") or 0),
            compile_log=data.get("compile_log", "") or "",
            compile_error_kind=data.get("compile_error_kind", "") or "",
            visual_check=data.get("visual_check") or {},
            # 旧会话没有 images 键 → []；坏数据（非 list/非 dict 元素）容错丢弃
            images=_parse_message_images(data.get("images")),
            # 旧会话没有 prompt_metrics 键 → {}；非 dict 数据容错丢弃
            prompt_metrics=(
                data.get("prompt_metrics")
                if isinstance(data.get("prompt_metrics"), dict)
                else {}
            ),
        )


def _empty_validation() -> Dict[str, Any]:
    return {
        "ok": True, "score": 100, "grade": "A", "gate": "pass",
        "errors": [], "warnings": [], "infos": [], "issues": [],
        "contexts": [], "autofixes": [], "code": "", "metrics": {},
        "checks_run": [], "summary": "校验已关闭（PI_AGENT_VALIDATION=0）", "tips": [],
    }


def _compile_failure_issues(error_kind: str, compile_log: str) -> List[Dict[str, Any]]:
    """编译失败但日志为空/无法解析时，补一条 compile.* 问题，保证质量门禁不误判。"""
    kind = normalize_compile_error_kind(error_kind, compile_ok=False, compile_log=compile_log)
    message_map = {
        "MISSING_XELATEX": ("warning", "compile.unavailable",
                            "当前环境未找到 xelatex，无法完成编译验证（不影响静态校验）",
                            "安装 TeX Live / 将 xelatex 加入 PATH 后可完成编译级验收"),
        "MISSING_PYMUPDF": ("warning", "compile.preview-unavailable",
                            "已编译但缺少 PyMuPDF，无法生成预览图",
                            "pip install pymupdf 后可恢复预览"),
        "TIMEOUT": ("error", "compile.timeout",
                    "XeLaTeX 编译超时，图形可能过于复杂或存在死循环",
                    "简化 \\foreach/曲线抽样率，或提高 TIKZ_COMPILE_TIMEOUT"),
        "IO_ERROR": ("error", "compile.io-error", "编译临时文件读写失败", "检查 .tikz_cache 目录权限与磁盘空间"),
        # 输出驱动崩溃：TeX 已排版完成、生成 PDF 时 xdvipdfmx 崩溃（环境故障，非语法错误）
        ERROR_KIND_DRIVER_CRASH: (
            "error", "compile.driver-crash",
            "编译器输出驱动 xdvipdfmx 崩溃（Windows 运行时错误，如 0xC0000417），不是 TikZ 语法错误",
            "请稍后重试「🔄 重新编译」（编译层已自动重试）；若反复出现：简化图形"
            "（减少路径/曲线控制点、|坐标|≤30、避免生僻 Unicode 字符）或更新 TeX Live",
        ),
        "COMPILE_ERROR": ("error", "compile.fatal", "XeLaTeX 编译失败，但未能从日志中解析出具体类别",
                          "根据原始编译器日志定位第一条致命错误"),
    }
    level, code, message, hint = message_map.get(
        kind, ("error", "compile.fatal", f"XeLaTeX 编译未通过（{kind}）", "检查 TikZ 语法与模板环境")
    )
    return [{
        "level": level, "code": code, "message": message, "hint": hint,
        "category": "compile", "fixable": False,
    }]


def _strip_tikz_for_context(latex_text: str) -> str:
    """去掉题干中的旧 TikZ 代码，避免旧图元干扰物理场景识别。"""
    if not latex_text:
        return ""
    return re.sub(
        r"\\begin\{tikzpicture\}(?:\[[^\]]*\])?[\s\S]*?\\end\{tikzpicture\}",
        "【此处为待修正插图】",
        latex_text,
    )


# ===================== Pi Diagram Agent 核心类 =====================


class PiDiagramAgent:
    """
    轻量自包含的物理/数学图形修正 Agent
    支持自然语言交互、多轮对话、技能动态匹配、语音转写、
    编译校验、确定性校验反馈、可选视觉校验以及自动重试自愈
    """

    def __init__(
        self,
        base_dir: Optional[str] = None,
        skill_registry: Optional[SkillRegistry] = None,
    ):
        self.base_dir = base_dir or os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        self.skill_registry = skill_registry or get_default_skill_registry()

    def _get_api_config(self) -> Dict[str, str]:
        """获取项目配置的 API 密钥与地址"""
        env_path = os.path.join(self.base_dir, ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
        api_key = os.getenv("AI_API_KEY", "").strip()
        base_url = os.getenv("AI_BASE_URL", "").strip()
        model_name = os.getenv("AI_MODEL_NAME") or os.getenv("AI_SOLVER_MODEL_NAME") or "qwen3.8-max"
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model_name": model_name,
        }

    def transcribe_audio(self, audio_bytes: bytes, filename: str = "voice.webm") -> Tuple[bool, str]:
        """
        使用项目中配置的 API 密钥与服务完成语音转写：
        1. 优先尝试 OpenAI 规范 /audio/transcriptions 接口
        2. 若为多模态模型 (如 Gemini / Qwen / GPT-4o)，直接通过多模态输入调用转写
        """
        import requests
        cfg = self._get_api_config()
        if not cfg["api_key"] or not cfg["base_url"]:
            return False, "未配置 API Key 或 Base URL，无法调用语音转写服务。"

        headers = {"Authorization": f"Bearer {cfg['api_key']}"}

        # 方式 1: 标准 OpenAI Whisper 兼容端点 /audio/transcriptions
        base = cfg["base_url"].rstrip("/")
        if base.endswith("/v1"):
            trans_url = f"{base}/audio/transcriptions"
        elif "/v1" not in base:
            trans_url = f"{base}/v1/audio/transcriptions"
        else:
            trans_url = f"{base.split('/v1')[0]}/v1/audio/transcriptions"

        try:
            files = {
                "file": (filename, io.BytesIO(audio_bytes), "audio/webm"),
            }
            data = {
                "model": "whisper-1",
                "language": "zh",
            }
            resp = requests.post(trans_url, headers=headers, files=files, data=data, timeout=30)
            if resp.status_code == 200:
                res_json = resp.json()
                text = res_json.get("text", "").strip()
                if text:
                    return True, text
        except Exception as e:
            logger.info(f"Standard Whisper transcript endpoint skipped: {e}")

        # 方式 2: 通用多模态大模型转写 (兼容 Gemini / Qwen / GPT-4o 等多模态通道)
        try:
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            payload = {
                "model": cfg["model_name"],
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "请仔细倾听这段语音，将其准确转写为纯文本汉字或英文指令。"
                            "这通常是高中物理或数学题目的图形修改要求（例如：‘把斜面倾角调小至30度，滑块添加支持力’）。"
                            "请直接输出识别到的转录文字，绝对不要添加任何额外解释、开场白、总结或引号。"
                        ),
                    }
                ],
                "temperature": 0.1,
            }
            resp, _ = post_chat_completion(cfg["base_url"], headers, payload, timeout=30)
            if resp.status_code == 200:
                res_json = resp.json()
                text = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if text:
                    return True, text
        except Exception as e:
            logger.warning(f"Multimodal transcription error: {e}")

        return False, "未能完成语音转写，建议直接在上方文本框打字输入。"

    def _clean_response(self, text: str) -> Tuple[str, str, str]:
        """
        从 LLM 回复中解析 THOUGHT, TIKZ, EXPLANATION 三个部分
        """
        thought = ""
        tikz_code = ""
        explanation = ""

        # 尝试标准分割标记
        if "===TIKZ===" in text:
            parts = text.split("===TIKZ===")
            if "===THOUGHT===" in parts[0]:
                thought = parts[0].replace("===THOUGHT===", "").strip()
            else:
                thought = parts[0].strip()

            rest = parts[1]
            if "===EXPLANATION===" in rest:
                sub_parts = rest.split("===EXPLANATION===")
                tikz_code = sub_parts[0].strip()
                explanation = sub_parts[1].strip()
            else:
                tikz_code = rest.strip()
        else:
            # 兼容正则匹配提取 \begin{tikzpicture}...\end{tikzpicture}
            m = re.search(r"(\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\})", text)
            if m:
                tikz_code = m.group(1).strip()
                # 剩余部分作为说明
                explanation = text.replace(tikz_code, "").strip()
            else:
                explanation = text

        # 清除可能残留的 markdown 标记
        tikz_code = re.sub(r"^```(?:latex|tex|tikz)?\s*", "", tikz_code, flags=re.MULTILINE)
        tikz_code = re.sub(r"\s*```$", "", tikz_code, flags=re.MULTILINE).strip()
        # 去掉 THOUGHT / EXPLANATION 标记残留
        explanation = explanation.replace("===EXPLANATION===", "").strip()
        thought = thought.replace("===THOUGHT===", "").strip()

        # 如果没有写开头结尾，但包含 tikz body，补齐
        if tikz_code and "\\begin{tikzpicture}" not in tikz_code:
            tikz_code = "\\begin{tikzpicture}[line cap=round, line join=round]\n" + tikz_code + "\n\\end{tikzpicture}"

        return thought, tikz_code, explanation

    # ---------- 视觉校验 ----------

    def _visual_enabled(self) -> bool:
        return os.getenv("PI_AGENT_VISUAL_CHECK", "1") != "0"

    def _visual_validate(
        self,
        preview_b64: str,
        question_text: str,
        user_instruction: str,
        timeout: Any = None,
    ) -> Dict[str, Any]:
        """用配置的多模态模型对渲染图做一次视觉审图；任何异常均按“跳过”处理。

        ``timeout`` 显式覆盖 (connect, read)；缺省时按 ``resolve_llm_timeouts(default_read=75)``
        取可配置超时（AI_CONNECT_TIMEOUT / AI_READ_TIMEOUT）。
        """
        skipped = {"ok": True, "score": 0, "issues": [], "summary": "视觉校验已跳过", "skipped": True}
        if not preview_b64:
            return skipped
        cfg = self._get_api_config()
        if not cfg["api_key"] or not cfg["base_url"]:
            return skipped
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg['api_key']}",
            }
            user_text = (
                f"【题目题干】\n{question_text[:3000]}\n\n"
                f"【用户对插图的修改指令】\n{user_instruction[:1000]}\n\n"
                "请审阅附图并输出规定格式的 JSON。"
            )
            payload = {
                "model": cfg["model_name"],
                "messages": [
                    {"role": "system", "content": PI_AGENT_VISUAL_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{preview_b64}"},
                            },
                        ],
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 700,
            }
            # 视觉校验沿用可配置超时：read 默认保留原 75s，但可被 AI_READ_TIMEOUT 或显式 timeout 覆盖
            visual_timeout = _coerce_llm_timeout_override(timeout) or resolve_llm_timeouts(default_read=75)
            resp, _ = post_chat_completion(
                cfg["base_url"], headers, payload, timeout=visual_timeout
            )
            if resp.status_code != 200:
                return skipped
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = self._parse_visual_json(content)
            if parsed is None:
                return skipped
            return {
                "ok": bool(parsed.get("match", True)),
                "score": int(parsed.get("score") or 0),
                "issues": [str(x)[:200] for x in (parsed.get("issues") or [])][:5],
                "summary": str(parsed.get("summary") or "")[:300],
                "skipped": False,
            }
        except Exception as exc:
            logger.info("visual validation skipped: %s", exc)
            return skipped

    @staticmethod
    def _parse_visual_json(content: str) -> Optional[Dict[str, Any]]:
        if not content:
            return None
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except Exception:
                return None
        return None

    def _visual_issues(self, visual: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把视觉校验结果转换为可合并进校验报告的 issue 字典。"""
        if not visual or visual.get("skipped"):
            return []
        issues: List[Dict[str, Any]] = []
        details = visual.get("issues") or []
        for idx, text in enumerate(details[:2]):
            issues.append({
                "level": "warning",
                "code": f"visual.mismatch-{idx + 1}",
                "message": f"视觉校验：{text}",
                "hint": "按题干物理关系核对并修正该处图形",
                "category": "visual",
                "fixable": False,
            })
        if not visual.get("ok") or (not details and int(visual.get("score") or 0) < 60):
            summary = visual.get("summary") or "图形与题意可能不一致"
            issues.append({
                "level": "warning",
                "code": "visual.overall",
                "message": f"视觉校验判定图形与题意不符（评分 {visual.get('score', 0)}）：{summary}",
                "hint": "重点核对物理图元是否齐全、方向与角度是否与题干一致",
                "category": "visual",
                "fixable": False,
            })
        return issues

    # ---------- 反思提示 ----------

    def _build_reflection(
        self,
        validation: Dict[str, Any],
        compile_ok: bool,
        compile_log: str,
        error_kind: str,
        visual: Dict[str, Any],
        previous_tikz: str,
        question_context: str,
        lines_are_fragment_relative: bool = False,
    ) -> str:
        driver_crash = (not compile_ok) and is_driver_crash_kind(error_kind)

        if driver_crash:
            # 输出驱动崩溃是环境故障：不要把 build_repair_instructions 的语法修复清单
            # 回灌给模型（会诱导模型去修根本不存在的语法错误）。
            parts: List[str] = [
                "你上一轮生成的插图没有通过 Harness 验收，但失败原因不是 TikZ 语法错误。",
                "【编译输出驱动崩溃（环境问题）】",
                "XeLaTeX 已完成排版，却在生成 PDF 时由输出驱动 xdvipdfmx 在 Windows 上崩溃"
                "（运行时错误，例如 0xC0000417）。这是编译环境的瞬时故障，不是你的代码语法问题，"
                "请不要为了“修语法”而大改图形或宏包。",
                "请给出一版更稳健、更简单的等效画法：减少曲线控制点与路径数量、"
                "所有 |坐标| 控制在 30 以内、避免生僻 Unicode 字符与复杂装饰；"
                "或者几乎原样重发上一版代码（只做极小的等价简化）。",
            ]
            other_issues = [
                item for item in (validation.get("issues") or [])
                if not str(item.get("code", "")).startswith("compile.")
            ]
            if other_issues:
                filtered = dict(validation or {})
                filtered["issues"] = other_issues
                filtered["errors"] = [
                    item for item in other_issues
                    if isinstance(item, dict) and item.get("level") == "error"
                ]
                filtered["warnings"] = [
                    item for item in other_issues
                    if isinstance(item, dict) and item.get("level") == "warning"
                ]
                parts.append("【与驱动崩溃无关、仍值得修复的确定性校验问题】")
                parts.append(build_repair_instructions(filtered))
        else:
            parts = ["你上一轮生成的插图没有通过 Harness 校验/编译，请根据以下诊断逐条修复后重试。"]
            if validation and validation.get("issues"):
                parts.append(build_repair_instructions(validation))
            if not compile_ok:
                parts.append("【XeLaTeX 编译诊断】")
                if lines_are_fragment_relative:
                    parts.append("（以下行号已换算为你的 TikZ 片段内行号，可直接对照上一版代码定位）")
                if compile_log:
                    parts.append(_tex_log_summarize(compile_log, source=previous_tikz))
                    parts.append("诊断建议：" + _tex_log_diagnose(compile_log))
                else:
                    parts.append(f"编译失败（{error_kind or '未知原因'}），请检查代码完整性。")

        if visual and not visual.get("skipped"):
            parts.append("【视觉审图发现的问题】")
            if visual.get("issues"):
                for idx, item in enumerate(visual["issues"][:4], 1):
                    parts.append(f"{idx}. {item}")
            parts.append(f"视觉评分：{visual.get('score', 0)}/100；结论：{visual.get('summary', '')}")
        if question_context:
            parts.append(f"【题目上下文（节选）】\n{question_context[:1500]}")
        if previous_tikz:
            parts.append("【上一版 TikZ 代码（供对照修改）】\n" + previous_tikz[:2500])
        parts.append(
            "请严格遵守：每条路径语句以 ; 结束；node 数学符号用 $...$；"
            "只输出包含 ===THOUGHT=== / ===TIKZ=== / ===EXPLANATION=== 三段的完整回复，"
            "TIKZ 段只放一个可编译的 \\begin{tikzpicture}...\\end{tikzpicture}。"
        )
        return "\n\n".join(parts)

    # ---------- 主循环 ----------

    @staticmethod
    def _candidate_rank(candidate: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
        gate_rank = {"pass": 2, "needs_review": 1, "fail": 0}.get(candidate.get("quality_gate") or "", -1)
        validation = candidate.get("validation") or {}
        errors = len(validation.get("errors") or [])
        warnings = len(validation.get("warnings") or [])
        return (
            # 已编译成功的候选永远优先（未编译的“静态 pass”不能压过已编译的 needs_review）
            1 if candidate.get("compile_ok") else 0,
            gate_rank,
            int(candidate.get("validation_score") or 0),
            -errors,
            -warnings,
        )

    def run_turn(
        self,
        user_instruction: str,
        current_question_latex: str,
        history: Optional[List[AgentMessage]] = None,
        max_self_healing_attempts: int = 2,
        visual_check: Optional[bool] = None,
        reference_images: Optional[List[Dict[str, Any]]] = None,
        llm_timeout: Optional[Tuple[float, float]] = None,
    ) -> Dict[str, Any]:
        """
        执行一轮 Agent 推理与图形矫正循环：
        1. 动态意图匹配激活领域技能 (Skill Intent Matching)
        2. 前置意图提炼 (Pre-processing: 动作与参数提取)
        3. 组装增强提示词并调用 LLM 生成
        4. 确定性后置净化 + 静态校验（可自动修复）
        5. 真实编译 + 日志诊断；可选视觉校验
        6. 反思重生成，保留历史最佳候选，输出质量门禁

        ``reference_images``：用户随指令附带的参考图列表（元素形如
        ``{"name","mime","data_url"}``）。仅随「本轮用户消息」以 OpenAI 兼容多模态
        content 发送；若网关/模型返回非 200（不支持 image_url）或请求超时/连接失败，
        本轮自动去掉图片、仅用文字重试一次（不消耗自愈轮次）。图片不写入 history
        重放与日志字段。

        ``llm_timeout``：显式覆盖 (connect, read) 秒数；缺省时按
        ``services.ai_service.resolve_llm_timeouts()`` 读取 ``AI_CONNECT_TIMEOUT`` /
        ``AI_READ_TIMEOUT``（read 默认 240s）。视觉校验默认 read=75s，同样可被覆盖。

        上下文体积优化（均可用 .env 调整，非法/越界值自动回退默认并 clamp）：
        - ① 题目正文使用「旧 TikZ 已替换为占位符」的版本，修改后的 TikZ 单独给出，
          避免旧图代码在 prompt 中重复两份；
        - ③ ``PI_AGENT_HISTORY_MESSAGES``（默认 4，0..8）与
          ``PI_AGENT_HISTORY_MAX_CHARS``（默认 4000，1000..20000）控制历史回放
          条数与字符预算（只决定回放哪些消息，历史内容本身不压缩）；
        - ④ ``PI_AGENT_SKILL_PROMPT_MAX``（默认 2，1..3）控制注入 prompt 的技能数；
          技能流水线 / activated_skills badge 仍使用最多 3 个；
        - ⑨ 返回 ``prompt_metrics``（chars/est_tokens/history_messages/skill 数等，
          多模态 content 只计 text，不含 data_url），并写入 trace 与 agent_message。
        """
        cfg = self._get_api_config()
        if not cfg["api_key"] or not cfg["base_url"]:
            return {
                "ok": False,
                "error": "未检测到完整的 AI API 配置，请在系统设置中配置 API Key 与 Base URL。",
                "agent_message": None,
                "updated_latex": current_question_latex,
            }

        # 参考图片：严格校验（绝不抛异常），只随本轮 user 消息发送
        normalized_images = _normalize_reference_images(reference_images)
        images_pending = bool(normalized_images)
        image_input_degraded = False
        image_fallback_status: Optional[int] = None
        image_fallback_reason: str = ""
        effective_instruction = str(user_instruction or "")
        if not effective_instruction.strip() and normalized_images:
            # 图片-only 发送（UI 侧也会补默认指令，这里再兜底一层）
            effective_instruction = REFERENCE_IMAGES_DEFAULT_INSTRUCTION

        history = history or []
        validation_enabled = os.getenv("PI_AGENT_VALIDATION", "1") != "0"
        allow_visual = self._visual_enabled() if visual_check is None else bool(visual_check)
        started = time.time()

        # 提取现有插图
        existing_blocks = DiagramTools.extract_tikz_blocks(current_question_latex)
        current_tikz = existing_blocks[0]["code"] if existing_blocks else ""
        question_context = _strip_tikz_for_context(current_question_latex)

        # 1. 动态意图匹配激活领域技能 (Skill Matching)
        scored_skills = self.skill_registry.match_relevant_skills(
            user_instruction=effective_instruction,
            question_context=current_question_latex,
            threshold=0.35,
            max_skills=3,
        )
        activated_skills = [s for s, _ in scored_skills]
        activated_skill_names = [f"{s.name} ({s.skill_id})" for s in activated_skills]
        # ④ 技能提示瘦身：prompt 只注入 top N（默认 2）；skill pipeline / badge
        # 仍使用完整 activated_skills（最多 3 个），不影响 pre/post processing。
        skill_prompt_limit = _read_clamped_int_env(
            SKILL_PROMPT_MAX_ENV, DEFAULT_SKILL_PROMPT_MAX, 1, 3
        )
        prompt_skills = activated_skills[:skill_prompt_limit]

        # 2. 执行技能前置提炼 (Pre-processing)
        pre_info = self.skill_registry.run_pre_processing(
            user_instruction=effective_instruction,
            current_tikz=current_tikz,
            activated_skills=activated_skills,
        )
        detected_actions = pre_info.get("detected_actions", [])

        # 3. 构造增强用户 Prompt
        # ① 提示词去重：题目正文用「旧 TikZ 已替换为占位符」的 question_context，
        #    修改后的 TikZ 单独放在下方，避免旧图代码在 prompt 里重复出现两份。
        context_prompt = f"{QUESTION_LATEX_CONTEXT_LABEL}\n{question_context}\n\n"
        if current_tikz:
            context_prompt += f"【当前题目中的 TikZ 插图源码】：\n{current_tikz}\n\n"
        else:
            context_prompt += "【说明】：当前题目源码中暂未包含任何 TikZ 图形。请根据题目题干描述及用户的指示全新生成符合要求的 TikZ 示意图。\n\n"

        if detected_actions:
            context_prompt += "【Skill 前置提炼的几何/物理动作目标】：\n"
            for act in detected_actions:
                context_prompt += f"- {act}\n"
            context_prompt += "\n"

        context_prompt += f"【用户本次的矫正指示（支持自然语言/语音转文字）】：\n{effective_instruction}\n"

        # 注入动态技能知识与规范（④ 只注入 top N，避免技能指引把 prompt 撑大）
        skill_prompt_aug = self.skill_registry.build_prompt_augmentation(prompt_skills)
        system_content = PI_AGENT_DIAGRAM_SYSTEM_PROMPT + "\n" + skill_prompt_aug

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
        ]

        # ③ 历史回放：最近 N 条 + 字符预算（内容本身不压缩，只决定回放哪些消息）；
        #    只回放文字，参考图片绝不进入 history 重放。
        selected_history = _select_history_for_prompt(history)
        for h in selected_history:
            messages.append({
                "role": str(_message_field(h, "role", "user") or "user"),
                "content": _message_field(h, "content", ""),
            })

        # 本轮用户消息：有参考图时用 OpenAI 兼容多模态 content；无图保持纯字符串（既有行为）
        first_user_index = len(messages)
        if normalized_images:
            user_content: Any = _build_reference_image_content(context_prompt, normalized_images)
        else:
            user_content = context_prompt
        messages.append({"role": "user", "content": user_content})
        degraded_user_text = context_prompt + "\n\n" + REFERENCE_IMAGES_DEGRADED_NOTE

        # ⑨ 本轮 prompt 体积指标：所有 messages 字符数之和（多模态 content 只计 text，
        #    不含 data_url），估算 token + 历史条数 + 技能数 + 题目/TikZ 字符数。
        prompt_metrics = _compute_prompt_metrics(
            messages=messages,
            history_messages=len(selected_history),
            skill_prompt_count=len(prompt_skills),
            activated_skill_count=len(activated_skills),
            question_chars=len(question_context),
            tikz_chars=len(current_tikz),
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        }

        total_attempts = max(1, int(max_self_healing_attempts) + 1)
        best: Optional[Dict[str, Any]] = None
        trace: List[Dict[str, Any]] = []
        visual_attempts = 0
        final_thought = ""
        final_expl = ""
        last_reply = ""

        # 可配置 LLM 超时：显式参数 > AI_CONNECT_TIMEOUT/AI_READ_TIMEOUT > 默认 (10, 240)
        llm_timeout_override = _coerce_llm_timeout_override(llm_timeout)
        llm_timeout = llm_timeout_override or resolve_llm_timeouts()

        for attempt in range(total_attempts):
            attempt_started = time.time()
            carried_images = images_pending
            resp = None
            llm_aborted = False
            # 单次评估尝试内最多 2 次 HTTP 调用：首次（可能带图）失败后，
            # 去掉参考图并用纯文字重试一次；降级重试不消耗自愈轮次。
            for _request_index in range(2):
                payload = {
                    "model": cfg["model_name"],
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 2800,
                }
                try:
                    resp, _ = post_chat_completion(
                        cfg["base_url"], headers, payload, timeout=llm_timeout
                    )
                except Exception as exc:
                    # 带参考图时请求超时/连接失败 → 去掉图片、纯文字重试一次
                    # （复用 image_input_degraded 语义，不消耗自愈轮次）
                    if (
                        carried_images
                        and not image_input_degraded
                        and is_llm_network_exception(exc)
                    ):
                        image_input_degraded = True
                        images_pending = False
                        image_fallback_status = None
                        image_fallback_reason = classify_llm_error_kind(exc)
                        messages = _replace_first_user_message(
                            messages, first_user_index, degraded_user_text
                        )
                        logger.info(
                            "reference images request failed (%s); retrying text-only",
                            image_fallback_reason,
                        )
                        continue
                    trace.append({
                        "attempt": attempt + 1, "phase": "llm-error",
                        "error": str(exc)[:200],
                        "error_kind": classify_llm_error_kind(exc),
                        "elapsed_s": round(time.time() - attempt_started, 2),
                    })
                    if best is not None:
                        llm_aborted = True
                    else:
                        return _build_llm_error_result(
                            exc,
                            cfg["base_url"],
                            llm_timeout[1],
                            current_question_latex,
                            normalized_images,
                            image_input_degraded,
                            image_fallback_status,
                            image_fallback_reason,
                            prompt_metrics=prompt_metrics,
                        )
                    break

                # 兼容性降级：带图请求返回非 200（网关/模型不支持 image_url）时，
                # 在同一轮内去掉图片、仅用文字重试一次（不消耗自愈轮次）。
                if carried_images and not image_input_degraded and resp.status_code != 200:
                    image_input_degraded = True
                    images_pending = False
                    image_fallback_reason = "http_status"
                    try:
                        image_fallback_status = int(resp.status_code)
                    except (TypeError, ValueError):
                        image_fallback_status = None
                    # 用新 list 替换首条 user 消息（不就地改写已发给模型的 payload，
                    # 避免历史请求对象/日志被「事后篡改」）
                    messages = _replace_first_user_message(
                        messages, first_user_index, degraded_user_text
                    )
                    logger.info(
                        "reference images rejected by model/gateway (HTTP %s); retrying text-only",
                        image_fallback_status,
                    )
                    continue
                break

            if llm_aborted or resp is None:
                break

            if resp.status_code != 200:
                if best is not None:
                    trace.append({
                        "attempt": attempt + 1, "phase": "llm-error",
                        "error": f"HTTP {resp.status_code}", "elapsed_s": round(time.time() - attempt_started, 2),
                    })
                    break
                return {
                    "ok": False,
                    "error": f"API 请求失败 [{resp.status_code}]: {resp.text[:300]}",
                    "error_kind": "LLM_HTTP_ERROR",
                    "agent_message": None,
                    "updated_latex": current_question_latex,
                    "reference_image_count": len(normalized_images),
                    "image_input_degraded": image_input_degraded,
                    "image_fallback_status": image_fallback_status,
                    "image_fallback_reason": image_fallback_reason,
                    "prompt_metrics": dict(prompt_metrics),
                }

            try:
                resp_json = resp.json()
            except Exception as exc:
                # 网关返回非 JSON（例如代理错误页）：按「其它异常」走友好错误路径，绝不抛给 UI
                trace.append({
                    "attempt": attempt + 1, "phase": "llm-error",
                    "error": str(exc)[:200],
                    "error_kind": classify_llm_error_kind(exc),
                    "elapsed_s": round(time.time() - attempt_started, 2),
                })
                if best is not None:
                    break
                return _build_llm_error_result(
                    exc,
                    cfg["base_url"],
                    llm_timeout[1],
                    current_question_latex,
                    normalized_images,
                    image_input_degraded,
                    image_fallback_status,
                    image_fallback_reason,
                    prompt_metrics=prompt_metrics,
                )
            last_reply = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            thought, tikz_code, expl = self._clean_response(last_reply)
            final_thought = thought or final_thought
            final_expl = expl or final_expl

            if not tikz_code:
                # 未提取出有效 tikz，视作纯文字交流
                trace.append({"attempt": attempt + 1, "phase": "no-tikz"})
                break

            # 4. 确定性后置净化（技能流水线 + 校验器 autofix）
            fixes_applied: List[str] = []
            try:
                tikz_code, post_fixes = self.skill_registry.run_post_processing(tikz_code, activated_skills)
                fixes_applied.extend(post_fixes or [])
            except Exception as exc:
                logger.warning("skill post-processing failed: %s", exc)

            validation: Dict[str, Any] = _empty_validation()
            if validation_enabled:
                try:
                    validation = validate_diagram(tikz_code, question_context, effective_instruction)
                    fixed_code = validation.get("code") or tikz_code
                    if fixed_code != tikz_code:
                        fixes_applied.extend(validation.get("autofixes") or [])
                    else:
                        fixes_applied.extend(validation.get("autofixes") or [])
                    tikz_code = fixed_code or tikz_code
                except Exception as exc:
                    logger.warning("validation failed: %s", exc)
                    validation = _empty_validation()

            # 5. 编译（结构性硬失败时跳过，节省一次编译）
            compile_ok = False
            preview_b64: Optional[str] = None
            compile_log = ""
            error_kind = ""
            compile_error = ""
            log_line_offset = 0
            driver_exit_code: Optional[int] = None
            compile_retries = 0
            if not (validation_enabled and structural_failure(validation)):
                compile_result = DiagramTools.compile_with_log(tikz_code, self.base_dir)
                fields = _as_compile_fields(compile_result)
                compile_ok = bool(fields["ok"])
                preview_b64 = fields["b64_png"]
                # 把日志中的文档行号换算成 TikZ 片段内行号（便于模型/用户直接定位）
                log_line_offset = int(fields.get("source_line_offset") or 0)
                compile_log = _relativize_tex_log(fields["log"] or "", log_line_offset, tikz_code)
                error_kind = fields["error_kind"] or ""
                driver_exit_code = fields.get("driver_exit_code")
                compile_retries = int(fields.get("compile_retries") or 0)
                if compile_ok:
                    compile_error = ""
                elif is_driver_crash_kind(error_kind):
                    # 环境故障：不要把「未从日志中解析出明确的 LaTeX 错误」这类原始摘要抛给用户
                    compile_error = DRIVER_CRASH_USER_MESSAGE
                else:
                    compile_error = _tex_log_diagnose(compile_log)[:400] if compile_log else (error_kind or "编译失败")
                if validation_enabled:
                    try:
                        validation = validate_diagram(
                            tikz_code, question_context, effective_instruction,
                            compile_log="" if compile_ok else compile_log,
                        )
                        if not compile_ok:
                            # 日志为空/无法解析时也要保证门禁不误判为通过
                            has_compile_issue = any(
                                str(item.get("code", "")).startswith("compile.")
                                for item in (validation.get("issues") or [])
                            )
                            has_driver_crash_issue = any(
                                str(item.get("code", "")) == "compile.driver-crash"
                                for item in (validation.get("issues") or [])
                            )
                            if is_driver_crash_kind(error_kind) and not has_driver_crash_issue:
                                # 驱动崩溃优先给出可操作的中文诊断（即使校验器已产出其它 compile.* 问题）
                                validation = apply_extra_issues(
                                    validation,
                                    _compile_failure_issues(error_kind, compile_log),
                                )
                            elif not has_compile_issue:
                                validation = apply_extra_issues(
                                    validation,
                                    _compile_failure_issues(error_kind, compile_log),
                                )
                    except Exception as exc:
                        logger.warning("post-compile validation failed: %s", exc)

            # 6. 可选视觉校验（仅编译成功后执行一次）
            visual: Dict[str, Any] = {"ok": True, "score": 0, "issues": [], "summary": "视觉校验已跳过", "skipped": True}
            if allow_visual and compile_ok and preview_b64 and visual_attempts == 0 and validation_enabled:
                visual_attempts += 1
                visual = self._visual_validate(
                    preview_b64, question_context, effective_instruction,
                    timeout=llm_timeout_override,
                )
                if not visual.get("skipped"):
                    extra = self._visual_issues(visual)
                    if extra:
                        try:
                            validation = apply_extra_issues(validation, extra)
                        except Exception as exc:
                            logger.warning("merge visual issues failed: %s", exc)

            score = int(validation.get("score") or 0)
            gate = validation.get("gate") or ("pass" if compile_ok else "fail")
            if not validation_enabled:
                gate = "pass" if compile_ok else "fail"
            # P0 硬条件：未编译成功的图没有通过「编译级验收」，一律不允许自动同步
            # （MISSING_XELATEX / MISSING_PYMUPDF / TIMEOUT / COMPILE_ERROR 等环境或代码问题）
            if not compile_ok:
                gate = "fail"

            candidate: Dict[str, Any] = {
                "thought": thought or final_thought,
                "tikz": tikz_code,
                "explanation": expl or final_expl or "已为您重新处理插图。",
                "preview_b64": preview_b64,
                "compile_ok": compile_ok,
                "compile_error": compile_error,
                "compile_log": _tex_log_summarize(compile_log, source=tikz_code) if compile_log else "",
                "compile_error_kind": error_kind,
                "driver_exit_code": driver_exit_code,
                "compile_retries": compile_retries,
                "validation": validation,
                "validation_score": score,
                "quality_gate": gate,
                "fixes_applied": list(dict.fromkeys(fixes_applied)),
                "visual": visual,
                "elapsed_s": round(time.time() - attempt_started, 2),
            }
            if best is None or self._candidate_rank(candidate) > self._candidate_rank(best):
                best = candidate

            trace.append({
                "attempt": attempt + 1,
                "phase": "evaluated",
                "compile_ok": compile_ok,
                "compile_error_kind": error_kind,
                "driver_exit_code": driver_exit_code,
                "compile_retries": compile_retries,
                "score": score,
                "gate": gate,
                "errors": [e.get("code") for e in (validation.get("errors") or [])],
                "warnings": [w.get("code") for w in (validation.get("warnings") or [])],
                "fixes": candidate["fixes_applied"],
                "visual_skipped": bool(visual.get("skipped", True)),
                "visual_ok": visual.get("ok") if not visual.get("skipped") else None,
                # 多模态参考图状态：本轮是否真正带图请求、是否因网关不支持而降级
                "images_attached": bool(carried_images),
                "image_input_degraded": image_input_degraded,
                "image_fallback_status": image_fallback_status,
                "image_fallback_reason": image_fallback_reason,
                "prompt_metrics": dict(prompt_metrics),
                "elapsed_s": candidate["elapsed_s"],
            })

            visual_ok = visual.get("skipped", True) or bool(visual.get("ok"))
            if compile_ok and gate == "pass" and visual_ok:
                break
            if attempt >= total_attempts - 1:
                break

            # 7. 组装反思消息，进入下一轮
            reflection = self._build_reflection(
                validation=validation,
                compile_ok=compile_ok,
                compile_log=compile_log,
                error_kind=error_kind,
                visual=visual,
                previous_tikz=tikz_code,
                question_context=question_context,
                lines_are_fragment_relative=log_line_offset > 0,
            )
            messages.append({"role": "assistant", "content": last_reply})
            messages.append({"role": "user", "content": reflection})

        if best is None:
            # 模型只返回了纯文字，没有可用插图
            text_only_content = final_expl or "已收到您的请求，但本次回复未包含可用的 TikZ 插图代码。"
            if image_input_degraded:
                text_only_content += reference_images_degraded_notice(image_fallback_reason)
            agent_msg = AgentMessage(
                role="assistant",
                content=text_only_content,
                thought=final_thought,
                tikz_code="",
                compile_ok=True,
                activated_skills=activated_skill_names,
                attempts=len(trace),
                prompt_metrics=dict(prompt_metrics),
            )
            return {
                "ok": True,
                "agent_message": agent_msg,
                "updated_latex": current_question_latex,
                "tikz_code": "",
                "preview_b64": None,
                "compile_ok": True,
                "compile_error": "",
                "compile_log": "",
                "compile_error_kind": "",
                "validation": {},
                "validation_score": 0,
                "quality_gate": "",
                "gate_passed": False,
                "should_autosync": False,
                "fixes_applied": [],
                "attempts": len(trace),
                "trace": trace,
                "visual_check": {},
                "activated_skills": activated_skill_names,
                "reference_image_count": len(normalized_images),
                "image_input_degraded": image_input_degraded,
                "image_fallback_status": image_fallback_status,
                "image_fallback_reason": image_fallback_reason,
                "prompt_metrics": dict(prompt_metrics),
                "elapsed_s": round(time.time() - started, 2),
            }

        final = best
        final_tikz = final["tikz"]
        updated_latex = (
            DiagramTools.apply_tikz_to_latex(current_question_latex, final_tikz)
            if final_tikz else current_question_latex
        )
        # 附上自愈/修复过程信息（若最终候选不是第一轮，说明经历过多次修正）
        explanation = final.get("explanation") or "已为您重新处理插图。"
        gate = final.get("quality_gate") or ""
        if gate == "fail" and len(trace) > 1:
            explanation += "（已尝试多轮自动修复，仍未完全通过质量门禁，请人工复核。）"
        if is_driver_crash_kind(final.get("compile_error_kind")) and not final.get("compile_ok"):
            # 面向用户的环境故障说明：明确不是图形代码问题，避免用户误以为矫正能力失效
            explanation += (
                "（本次失败是编译器输出驱动 xdvipdfmx 的瞬时环境崩溃（Windows 运行时故障，"
                "如 0xC0000417），不是插图代码的语法错误；当前候选图已保留，"
                "可稍后再点一次「🔄 重新编译」或直接手动应用。）"
            )
        if image_input_degraded:
            # 网关/模型不支持 image_url / 请求超时时的降级说明（图片已自动去掉，仅用文字继续）
            explanation += reference_images_degraded_notice(image_fallback_reason)

        agent_msg = AgentMessage(
            role="assistant",
            content=explanation,
            thought=final.get("thought") or "",
            tikz_code=final_tikz,
            preview_b64=final.get("preview_b64") or "",
            compile_ok=bool(final.get("compile_ok")),
            compile_error=final.get("compile_error") or "",
            activated_skills=activated_skill_names,
            validation=final.get("validation") or {},
            validation_score=int(final.get("validation_score") or 0),
            quality_gate=gate,
            fixes_applied=final.get("fixes_applied") or [],
            attempts=len(trace),
            compile_log=final.get("compile_log") or "",
            compile_error_kind=final.get("compile_error_kind") or "",
            visual_check=final.get("visual") or {},
            prompt_metrics=dict(prompt_metrics),
        )

        # 质量门禁 fail（含编译未成功）时禁止自动同步；用户仍可手动应用
        should_autosync = bool(final_tikz) and gate != "fail" and bool(final.get("compile_ok"))

        return {
            "ok": True,
            "agent_message": agent_msg,
            "updated_latex": updated_latex,
            "tikz_code": final_tikz,
            "preview_b64": final.get("preview_b64"),
            "compile_ok": bool(final.get("compile_ok")),
            "compile_error": final.get("compile_error") or "",
            "compile_log": agent_msg.compile_log,
            "compile_error_kind": agent_msg.compile_error_kind,
            "validation": agent_msg.validation,
            "validation_score": agent_msg.validation_score,
            "quality_gate": gate,
            "gate_passed": gate == "pass",
            "should_autosync": should_autosync,
            "fixes_applied": agent_msg.fixes_applied,
            "attempts": len(trace),
            "trace": trace,
            "visual_check": agent_msg.visual_check,
            "activated_skills": activated_skill_names,
            "reference_image_count": len(normalized_images),
            "image_input_degraded": image_input_degraded,
            "image_fallback_status": image_fallback_status,
            "image_fallback_reason": image_fallback_reason,
            "prompt_metrics": dict(prompt_metrics),
            "elapsed_s": round(time.time() - started, 2),
        }
