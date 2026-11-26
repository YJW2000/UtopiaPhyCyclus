"""AI provider selection for the desktop client and its compatibility pages."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re


_job_provider = ContextVar('desktop_agent_provider', default=None)
_job_model = ContextVar('desktop_agent_model', default=None)
_MODEL_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:/@+-]{0,199}')


def read_settings(state=None):
    folder = state or os.environ.get('UTOPIA_DESKTOP_STATE')
    if folder:
        try:
            data = json.loads((Path(folder) / 'preferences.json').read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def selected_provider(state=None):
    from services.local_agent import LOCAL_AGENTS
    value = _job_provider.get() if state is None else None
    if value is None:
        value = read_settings(state).get('provider')
    return value if isinstance(value, str) and value in LOCAL_AGENTS else 'codex'


def requested_model(value, provider=None):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('模型 ID 无效。')
    value = value.strip()
    if not value:
        return None
    if not _MODEL_ID.fullmatch(value):
        raise ValueError('模型 ID 无效。请选择可用模型或输入 CLI 使用的模型 ID。')
    if provider == 'pi':
        source, _, model = value.partition('/')
        if not source or not model:
            raise ValueError('Pi 的模型 ID 需要使用「提供商/模型」格式。')
    return value


def selected_model():
    return _job_model.get()


@contextmanager
def using_provider(provider, model=None):
    token = _job_provider.set(provider)
    model_token = _job_model.set(model)
    try:
        yield
    finally:
        _job_model.reset(model_token)
        _job_provider.reset(token)


def legacy_completion(payload, timeout):
    """Let existing OpenAI-shaped text and OCR callers use the selected desktop provider."""
    from services.local_codex import run_local_codex_json
    from requests import Response
    turns, images = [], []
    for message in payload.get('messages', []):
        content = message.get('content', '')
        if isinstance(content, list):
            chunks = []
            for part in content:
                if part.get('type') == 'text':
                    chunks.append(part.get('text', ''))
                elif part.get('type') == 'image_url':
                    url = part['image_url']['url']
                    if not url.startswith('data:image/') or ';base64,' not in url:
                        raise ValueError('本地助手需要上传的图片文件。')
                    images.append(base64.b64decode(url.split(',', 1)[1], validate=True))
                    chunks.append(f'【附件 {len(images)}】')
            content = '\n'.join(chunks)
        turns.append({'role': message.get('role', 'user'), 'content': content})
    schema = {'type': 'object', 'properties': {'reply': {'type': 'string'}}, 'required': ['reply'], 'additionalProperties': False}
    seconds = timeout[-1] if isinstance(timeout, (list, tuple)) else timeout
    data = run_local_codex_json('按以下对话完成任务，把最终回答全文置于 reply（包括用户要求的 JSON 或 LaTeX）。\n'
                                + json.dumps(turns, ensure_ascii=False), schema, images, timeout=max(60, int(seconds or 180)))
    result = Response()
    result.status_code = 200
    result.encoding = 'utf-8'
    result._content = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': data['reply']}}]}, ensure_ascii=False).encode()
    return result
