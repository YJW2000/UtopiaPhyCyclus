# -*- coding: utf-8 -*-
import json
import math
import os
import re
from typing import Tuple, Any


# ===================== LLM 请求超时配置 =====================
# 背景：此前 diagram_agent 把 requests 超时写死为 (10, 90)。本地 AI 网关
# （如 Antigravity Tools，127.0.0.1:8045）在上游拥塞/自身卡死时，一次真实请求
# 可能耗时 157–173s，90s 会必然超时且用户无法调整。这里统一改为可配置：
#   AI_CONNECT_TIMEOUT（默认 10s，clamp 3..60）
#   AI_READ_TIMEOUT   （默认 240s，clamp 30..900）
DEFAULT_LLM_CONNECT_TIMEOUT = 10.0
DEFAULT_LLM_READ_TIMEOUT = 240.0
LLM_CONNECT_TIMEOUT_MIN = 3.0
LLM_CONNECT_TIMEOUT_MAX = 60.0
LLM_READ_TIMEOUT_MIN = 30.0
LLM_READ_TIMEOUT_MAX = 900.0


def _coerce_timeout_seconds(value: Any, default: float) -> float:
    """把环境变量值转成有限正数秒；任何非法值（空/非数字/NaN/Inf/<=0）都回退默认值。"""
    try:
        if value is None:
            return float(default)
        if isinstance(value, str) and not value.strip():
            return float(default)
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            return float(default)
        return seconds
    except (TypeError, ValueError, OverflowError):
        return float(default)


def resolve_llm_timeouts(
    default_connect: float = DEFAULT_LLM_CONNECT_TIMEOUT,
    default_read: float = DEFAULT_LLM_READ_TIMEOUT,
) -> Tuple[float, float]:
    """解析 LLM HTTP 请求超时（connect, read），单位秒。

    读取环境变量 ``AI_CONNECT_TIMEOUT`` / ``AI_READ_TIMEOUT``；缺失或非法时使用
    传入的默认值（默认 connect=10s、read=240s）。返回值再做安全 clamp：
    connect 限制在 3..60s，read 限制在 30..900s，避免误配置把客户端卡死。

    绝不抛异常；``default_*`` 也可让不同调用点保留原有默认（例如视觉校验 read=75s）。
    """
    try:
        connect = _coerce_timeout_seconds(os.getenv("AI_CONNECT_TIMEOUT"), default_connect)
    except Exception:  # pragma: no cover - 防御：读取环境变量本身异常
        connect = float(default_connect)
    try:
        read = _coerce_timeout_seconds(os.getenv("AI_READ_TIMEOUT"), default_read)
    except Exception:  # pragma: no cover - 防御
        read = float(default_read)
    connect = min(max(connect, LLM_CONNECT_TIMEOUT_MIN), LLM_CONNECT_TIMEOUT_MAX)
    read = min(max(read, LLM_READ_TIMEOUT_MIN), LLM_READ_TIMEOUT_MAX)
    return connect, read


def normalize_chat_completions_url(base_url: str) -> str:
    url = (base_url or "").rstrip("/")
    if "/v1" not in url and "/chat/completions" not in url:
        url += "/v1"
    if "/chat/completions" not in url:
        url += "/chat/completions"
    return url


def post_chat_completion(base_url: str, headers: dict, payload: dict, timeout):
    if base_url == 'local://codex':
        from services.desktop_model import legacy_completion
        return legacy_completion(payload, timeout), base_url
    import requests

    url = normalize_chat_completions_url(base_url)
    return requests.post(url, headers=headers, json=payload, timeout=timeout), url


def extract_json_obj_from_text(text: str):
    if text is None:
        raise ValueError("empty response")
    cleaned = str(text).replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise
        return json.loads(match.group(0))
