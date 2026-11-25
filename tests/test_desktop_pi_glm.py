import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import requests

from services import local_agent, local_agent_glm, local_agent_pi


SCHEMA = {
    'type': 'object', 'properties': {'reply': {'type': 'string'}},
    'required': ['reply'], 'additionalProperties': False,
}


@pytest.mark.parametrize('vision', [True, False])
def test_pi_uses_configured_model_and_checks_image_input(tmp_path, monkeypatch, vision):
    marker = tmp_path / 'prompt-received'
    cli = tmp_path / 'pi_stub.py'
    cli.write_text('''
import json
from pathlib import Path
import sys

def receive():
    return json.loads(sys.stdin.readline())

def emit(**event):
    print(json.dumps(event), flush=True)

state = receive()
assert state == {'id': 'state', 'type': 'get_state'}
emit(type='response', id='state', success=True, data={'model': {'input': sys.argv[1:]}})
line = sys.stdin.readline()
if line:
    Path('prompt-received').touch()
    task = json.loads(line)
    assert '图中有什么' in task['message']
    assert task['images'] == [{'type': 'image', 'mimeType': 'image/png', 'data': 'cGl4ZWxz'}]
    emit(type='response', id='prompt', success=True, data={'disposition': 'started'})
    emit(type='message_end', message={'role': 'assistant', 'stopReason': 'stop',
         'content': [{'type': 'text', 'text': '{"reply":"old"}'}]})
    emit(type='agent_end')
    emit(type='message_end', message={'role': 'assistant', 'stopReason': 'stop',
         'content': [{'type': 'thinking', 'thinking': 'hidden'}, {'type': 'text', 'text': '{"reply":"ok"}'}]})
    emit(type='agent_settled')
sys.stdin.read()
''', encoding='utf-8')
    invoke = local_agent._invoke

    def invoke_stub(command, directory, payload, env, timeout):
        request = json.loads(payload)
        assert Path(command[-1]).name == 'local_agent_pi.py'
        assert set(request['command'][1:]) == {
            '--mode', 'rpc', '--no-session', '--no-tools', '--no-mcp', '--no-extensions',
            '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-approve',
        }
        request['command'] = [sys.executable, '-X', 'utf8', str(cli), *(['image'] if vision else ['text'])]
        return invoke(command, directory, json.dumps(request).encode(), env, timeout)

    monkeypatch.setattr(local_agent, '_invoke', invoke_stub)
    if vision:
        result = local_agent_pi.pi_request('pi', tmp_path, '图中有什么', SCHEMA, [b'pixels'], os.environ, 6)
        assert json.loads(result) == {'reply': 'ok'}
        assert marker.exists()
    else:
        with pytest.raises(local_agent.AgentResponseError, match='does not support image input'):
            local_agent_pi.pi_request('pi', tmp_path, '图中有什么', SCHEMA, [b'pixels'], os.environ, 6)
        assert not marker.exists()


def test_glm_uses_fixed_endpoints_and_keeps_the_key_out_of_child_payload(monkeypatch):
    monkeypatch.delenv('ZHIPU_API_KEY', raising=False)
    monkeypatch.setenv('ZAI_API_KEY', 'international-test-key')
    monkeypatch.setattr(local_agent_glm, 'registered_tool_variable', lambda name: 'test-key' if name == 'ZHIPU_API_KEY' else '')
    assert local_agent_glm.glm_configured()

    def run(command, *, input, **options):
        assert command[-2] == 'services.local_agent_glm'
        request = json.loads(input)
        assert request['model'] == 'glm-5.3'
        assert '小球下落' in request['messages'][0]['content']
        assert 'test-key' not in input.decode('utf-8')
        assert options['kill_tree_on_timeout']
        return subprocess.CompletedProcess(command, 0, b'```json\n{"reply":"ok"}\n```', b'')

    monkeypatch.setattr(local_agent_glm, 'run', run)
    assert local_agent_glm.glm_request('小球下落', SCHEMA, []) == {'reply': 'ok'}
    with pytest.raises(ValueError, match='只支持文本'):
        local_agent_glm.glm_request('小球下落', SCHEMA, [b'pixels'])


@pytest.mark.parametrize('regional,status,result', [(True, 200, 0), (False, 429, 3), (True, 302, 1)])
def test_glm_child_requests_the_matching_official_endpoint(monkeypatch, capsysbinary, regional, status, result):
    monkeypatch.setattr(local_agent_glm, 'registered_tool_variable', lambda name: '')
    monkeypatch.delenv('ZHIPU_API_KEY', raising=False)
    monkeypatch.delenv('ZAI_API_KEY', raising=False)
    monkeypatch.setenv('ZHIPU_API_KEY' if regional else 'ZAI_API_KEY', 'test-key')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"model":"glm-5.3"}'))

    def post(url, *, headers, json, timeout, allow_redirects):
        hostname = 'open.bigmodel.cn' if regional else 'api.z.ai'
        assert url == f'https://{hostname}/api/paas/v4/chat/completions'
        assert headers == {'Authorization': 'Bearer test-key'}
        assert json == {'model': 'glm-5.3'}
        assert timeout == (10, 5)
        assert not allow_redirects
        response = requests.Response()
        response.status_code = status
        response.headers['Location'] = 'https://example.com/'
        response._content = b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
        return response

    monkeypatch.setattr(requests, 'post', post)
    assert local_agent_glm._request_child(5) == result
    assert capsysbinary.readouterr().out == (b'{}' if status == 200 else b'')
