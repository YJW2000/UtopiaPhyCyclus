# -*- coding: utf-8 -*-
"""本地 whisper 语音识别（移植 DSH 的 dsh-ears 插件：常驻进程 + 模型只加载一次）。

与 dsh-ears 相同的性能路径：
- Node 侧用 `@fugood/whisper.node`（whisper.cpp，Vulkan/CPU）跑 ggml 模型；
- 模型在服务启动时加载一次并常驻，之后每次识别只做推理；
- Python 通过 127.0.0.1 的 HTTP 与常驻服务通信。

对比旧实现（每次识别 spawn 一个 node 进程、重新加载 487MB 模型，冷启动 4~5s），
常驻服务把稳态耗时压到 0.3~0.8s，与 dsh-ears 实测（9.5s 音频 ≈ 0.4~0.6s）一致。

若运行时缺失或服务启动失败，调用方可回退到 `services/whisper_bridge.mjs` 的一次性识别。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

_APP_ROOT = Path(__file__).resolve().parent.parent
_SERVER = _APP_ROOT / "services" / "whisper_server.mjs"
_BRIDGE = _APP_ROOT / "services" / "whisper_bridge.mjs"
_HOME = Path.home()
_EARS_ROOT = _HOME / ".dsh" / "plugins" / "dsh-ears"
_EARS_LIB = _EARS_ROOT / "lib" / "transcribe.js"

_LOCK = threading.Lock()
_STATE: dict = {"proc": None, "port": None, "starting": False}
_WARM_THREAD_STARTED = False


def _model_path() -> Optional[Path]:
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = []
    env_model = os.environ.get("DSH_EARS_MODEL", "")
    if env_model:
        candidates.append(Path(env_model))
    if local_appdata:
        candidates.append(Path(local_appdata) / "dsh-ears" / "whisper" / "ggml-small.bin")
        candidates.append(Path(local_appdata) / "dsh-ears" / "whisper" / "ggml-tiny.bin")
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def availability() -> Tuple[bool, str]:
    """返回 (是否可用, 说明)。"""
    node = shutil.which("node")
    if not node:
        return False, "未找到 Node.js（本地 whisper 需要 node）"
    if not _SERVER.is_file() and not _BRIDGE.is_file():
        return False, "缺少 whisper 服务脚本"
    if not _EARS_LIB.is_file():
        return False, "未找到 DSH 的 dsh-ears 插件转写库"
    model = _model_path()
    if model is None:
        return False, "未找到 whisper 模型 ggml-small.bin"
    return True, f"本地 whisper 就绪（{model.name}）"


def _port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_json(url: str, payload: Optional[bytes] = None, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(url, data=payload, method="POST" if payload is not None else "GET")
    if payload is not None:
        req.add_header("Content-Type", "application/octet-stream")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return json.load(resp)


def _start_server(wait_ready: float = 90.0) -> bool:
    """启动常驻 whisper 服务（若已在运行则直接复用）。"""
    if _STATE["proc"] is not None and _STATE["proc"].poll() is None and _STATE["port"]:
        if _port_open(_STATE["port"]):
            return True

    node = shutil.which("node")
    model = _model_path()
    if node is None or model is None or not _SERVER.is_file() or not _EARS_LIB.is_file():
        return False

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        [
            node,
            str(_SERVER),
            str(_EARS_LIB),
            str(model),
            os.environ.get("DSH_EARS_LANG", "zh"),
            str(os.getpid()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    _STATE["proc"] = proc
    _STATE["port"] = None

    deadline = time.time() + wait_ready
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        line = proc.stdout.readline() if proc.stdout else b""
        if not line:
            continue
        text = line.decode("utf-8", errors="replace").strip()
        if text.startswith("READY "):
            try:
                port = int(text.split(" ", 1)[1])
            except ValueError:
                continue
            if _port_open(port):
                _STATE["port"] = port
                print(f"[MC_WHISPER] 常驻服务已就绪 port={port}", flush=True)
                return True
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    _STATE["proc"] = None
    return False


def _ensure_server() -> Optional[int]:
    with _LOCK:
        if _STATE["port"] and _STATE["proc"] is not None and _STATE["proc"].poll() is None:
            if _port_open(_STATE["port"]):
                return _STATE["port"]
        ok = _start_server()
        return _STATE["port"] if ok else None


def warm_up_async() -> None:
    """后台预热：应用启动时调用一次，让模型提前加载好（不阻塞首屏）。"""
    global _WARM_THREAD_STARTED
    if _WARM_THREAD_STARTED:
        return
    _WARM_THREAD_STARTED = True

    def _run():
        try:
            ok, _ = availability()
            if ok:
                _ensure_server()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, name="mc-whisper-warmup", daemon=True).start()


def shutdown() -> None:
    proc = _STATE.get("proc")
    if proc is not None:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        _STATE["proc"] = None
        _STATE["port"] = None


atexit.register(shutdown)


def _transcribe_once(pcm_bytes: bytes, language: str, timeout: int) -> Tuple[bool, str]:
    """兜底：一次性 node 进程（冷启动慢，但依赖更少）。"""
    node = shutil.which("node")
    model = _model_path()
    if node is None or model is None or not _BRIDGE.is_file():
        return False, "本地 whisper 不可用"
    try:
        proc = subprocess.run(
            [node, str(_BRIDGE), str(_EARS_LIB), str(model), language],
            input=pcm_bytes,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return False, "本地 whisper 识别超时"
    except Exception as exc:  # noqa: BLE001
        return False, f"本地 whisper 启动失败：{exc}"
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    if not stdout:
        return False, "本地 whisper 无输出"
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except Exception:  # noqa: BLE001
        return False, "本地 whisper 输出解析失败"
    if payload.get("ok") and str(payload.get("text") or "").strip():
        return True, str(payload["text"]).strip()
    return False, str(payload.get("error") or "本地 whisper 未识别出文字")


def transcribe_pcm16(pcm_bytes: bytes, language: str = "zh", timeout: int = 180) -> Tuple[bool, str]:
    """用本地 whisper 识别 PCM16 / 16kHz / 单声道音频（走常驻服务）。"""
    ok, message = availability()
    if not ok:
        return False, message
    if not pcm_bytes:
        return False, "音频为空"

    started = time.time()
    port = _ensure_server()
    if port:
        try:
            payload = _http_json(
                f"http://127.0.0.1:{port}/transcribe?lang={urllib.parse.quote(language)}",
                payload=pcm_bytes,
                timeout=timeout,
            )
            text = str(payload.get("text") or "").strip()
            if payload.get("ok") and text:
                print(
                    f"[MC_WHISPER] 常驻服务识别 OK 耗时={time.time() - started:.2f}s "
                    f"推理={payload.get('elapsedMs')}ms 音频={payload.get('durationSeconds')}s",
                    flush=True,
                )
                return True, text
            if payload.get("ok"):
                return False, "只听到噪声，请再说一次。"
            return False, str(payload.get("error") or "本地 whisper 未识别出文字")
        except Exception as exc:  # noqa: BLE001
            print(f"[MC_WHISPER] 常驻服务调用失败，回退一次性识别：{exc}", flush=True)
            shutdown()

    return _transcribe_once(pcm_bytes, language, timeout)
