"""Use the official GLM API for text-only desktop requests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from services.desktop_tool_paths import registered_tool_variable
from services.local_process import run


_ENDPOINTS = {
    'ZHIPU_API_KEY': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    'ZAI_API_KEY': 'https://api.z.ai/api/paas/v4/chat/completions',
}


def _credentials():
    for name, endpoint in _ENDPOINTS.items():
        key = os.environ.get(name, '').strip() or registered_tool_variable(name).strip()
        if key:
            return endpoint, key
    return None


def glm_configured():
    return _credentials() is not None


def glm_request(prompt, schema, images, *, timeout=180):
    from services.local_agent import AgentResponseError, _decode_output, _structured_prompt

    if images:
        raise ValueError('GLM 5.3 只支持文本。请在设置中选用支持图片的 Agent 再试。')
    if not glm_configured():
        raise RuntimeError('请先完成本机 GLM 凭据配置。')
    payload = {
        'model': 'glm-5.3',
        'max_tokens': 32768,
        'messages': [{'role': 'user', 'content': _structured_prompt(prompt, schema)}],
    }
    try:
        result = run([sys.executable, '-B', '-X', 'utf8', '-m', 'services.local_agent_glm', str(timeout)],
                     cwd=Path(__file__).resolve().parents[1], input=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                     capture_output=True, timeout=timeout + 12, kill_tree_on_timeout=True,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'GLM 在 {timeout} 秒内未完成，请稍后重试。') from None
    except OSError:
        raise RuntimeError('GLM 请求启动失败，请稍后重试。') from None
    if result.returncode:
        message = {
            2: 'GLM API 授权或余额不可用，请检查密钥和账户。',
            3: 'GLM 的调用额度暂时受限，请稍后重试。',
            4: 'GLM 未返回完整结果，请缩短输入后重试。',
        }.get(result.returncode, 'GLM 请求失败，请检查网络和 API 配置。')
        raise RuntimeError(message)
    try:
        return _decode_output(result.stdout.decode('utf-8'), schema)
    except (AgentResponseError, TypeError, ValueError):
        raise RuntimeError('GLM 未返回有效的结构化结果，请重试。') from None


def _request_child(timeout):
    import requests

    credentials = _credentials()
    if not credentials:
        return 2
    endpoint, key = credentials
    try:
        response = requests.post(endpoint, headers={'Authorization': f'Bearer {key}'},
                                 json=json.load(sys.stdin), timeout=(10, timeout), allow_redirects=False)
        if response.status_code in (401, 402, 403):
            return 2
        if response.status_code == 429:
            return 3
        if not 200 <= response.status_code < 300:
            return 1
        choice = response.json()['choices'][0]
        if choice.get('finish_reason') == 'length':
            return 4
        answer = choice['message'].get('content')
        if not isinstance(answer, str) or not answer.strip():
            return 4
    except (requests.RequestException, AttributeError, TypeError, ValueError, KeyError, IndexError):
        return 1
    sys.stdout.buffer.write(answer.encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(_request_child(float(sys.argv[1])))
