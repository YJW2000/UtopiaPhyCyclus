"""AI provider selection for the desktop client and its compatibility pages."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import sys


_job_provider = ContextVar('desktop_agent_provider', default=None)


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
    if state is None and _job_provider.get() is not None:
        return _job_provider.get()
    value = read_settings(state).get('provider')
    return value if isinstance(value, str) and (value == 'api' or value in LOCAL_AGENTS) else 'codex'


@contextmanager
def using_provider(provider):
    token = _job_provider.set(provider)
    try:
        yield
    finally:
        _job_provider.reset(token)


def api_config(library):
    from dotenv import dotenv_values
    config = dotenv_values(Path(library) / '.env')
    return {key: config.get(key) or os.environ.get(key, '') for key in
            ('AI_BASE_URL', 'AI_API_KEY', 'AI_MODEL_NAME', 'AI_SOLVER_MODEL_NAME')}


def request_api_json(prompt, schema, images, timeout):
    from services.local_process import run
    from services.ai_service import extract_json_obj_from_text
    config = api_config(os.environ['UTOPIA_DESKTOP_LIBRARY'])
    if not all(config[k] for k in ('AI_BASE_URL', 'AI_API_KEY', 'AI_MODEL_NAME')):
        raise ValueError('请在设置中填写 API 地址、模型和密钥，或选择本机 Agent。')
    content = [{'type': 'text', 'text': prompt + '\n请按这个 JSON Schema 返回对象：\n' + json.dumps(schema, ensure_ascii=False)}]
    for data in images:
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(data).decode()}})
    # HTTP requests can block inside TLS reads. An isolated child lets Stop release
    # the desktop worker immediately, including when the endpoint has stopped responding.
    payload = {'model': config['AI_MODEL_NAME'], 'messages': [{'role': 'user', 'content': content}]}
    result = run([sys.executable, '-B', '-X', 'utf8', '-m', 'services.desktop_model', str(timeout)],
                 cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=timeout+12,
                 input=json.dumps(payload, ensure_ascii=False).encode(), creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8','replace').strip() or '自定义 API 请求失败。')
    return extract_json_obj_from_text(result.stdout.decode('utf-8'))


def _request_child():
    import requests
    from services.ai_service import normalize_chat_completions_url
    config=api_config(os.environ['UTOPIA_DESKTOP_LIBRARY'])
    try:
        response=requests.post(normalize_chat_completions_url(config['AI_BASE_URL']),
                               headers={'Authorization':'Bearer '+config['AI_API_KEY']},
                               json=json.load(sys.stdin),timeout=(10,int(sys.argv[1])))
        response.raise_for_status()
        sys.stdout.buffer.write(response.json()['choices'][0]['message']['content'].encode('utf-8'))
    except requests.HTTPError as error:
        sys.exit('自定义 API 返回 HTTP '+str(error.response.status_code)+'，请检查模型、地址与凭据。')
    except (requests.RequestException,ValueError,KeyError,IndexError,AttributeError):
        sys.exit('自定义 API 未返回有效回复，请检查连接和模型配置。')


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


if __name__ == '__main__':
    _request_child()
