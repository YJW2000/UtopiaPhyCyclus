import base64
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
import time

import pymupdf
import pytest

from services import desktop_library, local_agent
from services.desktop_jobs import DesktopJobs
from services.desktop_model import selected_provider
from services.desktop_workspace import DesktopWorkspace


SCHEMA = {'type': 'object', 'properties': {'reply': {'type': 'string'}},
          'required': ['reply'], 'additionalProperties': False}


def _wait(jobs, task):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = jobs.get(task)
        if result['status'] not in ('queued', 'running'):
            return result
        time.sleep(0.025)
    pytest.fail('Agent job did not finish')


def test_preferences_report_current_agent_and_route_context_chat(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    handlers = desktop_library.create_library_handlers(tmp_path, state)
    monkeypatch.setattr(local_agent, '_find_agent', lambda agent, path: 'copilot.exe' if agent == 'copilot' else None)
    assert handlers['ui.load']({})['provider'] == 'codex'
    assert handlers['ui.save']({'provider': 'copilot'})['provider'] == 'copilot'
    with pytest.raises(ValueError):
        handlers['ui.save']({'provider': 'arbitrary-executable'})
    status = handlers['ui.providers']({})
    assert status['effectiveProvider'] == 'copilot'
    assert {item['id'] for item in status['agents']} == set(local_agent.LOCAL_AGENTS)
    assert next(item for item in status['agents'] if item['id'] == 'copilot')['installed']
    assert not next(item for item in status['agents'] if item['id'] == 'cursor')['images']

    def context_agent(provider, prompt, schema, images):
        assert provider == 'copilot'
        assert '力学题' in prompt
        assert images == []
        return {'reply': '已找到', 'action': 'none', 'ids': [], 'query': ''}

    monkeypatch.setattr(desktop_library, 'run_agent_json', context_agent)
    assert handlers['context.chat']({'message': '力学题'})['reply'] == '已找到'


def test_saved_agent_handles_editor_chat_and_pdf_reconstruction(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    state = tmp_path / 'state'
    (library / 'chapters').mkdir(parents=True)
    workspace = DesktopWorkspace(library, state)
    settings = desktop_library.create_library_handlers(library, state)['ui.save']
    original = tmp_path / 'original.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), 'Question 1')
        pdf.save(original)

    calls = []

    def agent(provider, prompt, schema, images, *, timeout):
        calls.append((provider, images))
        if images:
            return {'status': 'ok', 'fragments': [
                {'question_id': 'q1', 'number': '1', 'kind': 'stem', 'latex': '小球下落。'},
            ], 'figures': []}
        return {'reply': '小球受重力。', 'edits': []}

    monkeypatch.setattr(local_agent, 'run_agent_json', agent)
    try:
        settings({'provider': 'cursor'})
        doc = workspace.create({'title': '草稿', 'source': '小球下落。'})
        answer = _wait(workspace.jobs, workspace.chat({'id': doc['id'], 'prompt': '小球受什么力？'}))
        assert answer['status'] == 'succeeded', answer
        imported = workspace.create({'path': str(original)})
        with pytest.raises(ValueError, match='只支持文本'):
            workspace.reconstruct({'id': imported['id']})

        settings({'provider': 'gemini'})
        answer = _wait(workspace.jobs, workspace.reconstruct({'id': imported['id']}))
        assert answer['status'] == 'succeeded', answer
        assert '小球下落' in answer['result']['source']
        assert calls[0] == ('cursor', [])
        assert calls[1][0] == 'gemini' and calls[1][1][0].startswith(b'\x89PNG')
    finally:
        workspace.jobs.close()


def test_queued_provider_keeps_its_submission_selection(tmp_path, monkeypatch):
    monkeypatch.setenv('UTOPIA_DESKTOP_STATE', str(tmp_path))
    settings = tmp_path / 'preferences.json'
    settings.write_text('{"provider":"claude"}')
    blockers = Event()
    jobs = DesktopJobs()
    try:
        for _ in range(2):
            jobs.submit(lambda job: blockers.wait(5))
        task = jobs.submit(lambda job: job.publish(selected_provider), provider=selected_provider())
        settings.write_text('{"provider":"opencode"}')
        blockers.set()
        assert _wait(jobs, task)['result'] == 'claude'
        assert selected_provider() == 'opencode'
    finally:
        blockers.set()
        jobs.close()


def test_claude_sends_structured_stdin_without_real_tools(monkeypatch):
    monkeypatch.setattr(local_agent, '_find_agent', lambda agent, path: 'claude.exe')
    monkeypatch.setenv('AI_API_KEY', 'do-not-inherit')

    def cli(args, *, cwd, input, env, **options):
        assert args[0] == 'claude.exe'
        assert args[args.index('--tools') + 1] == ''
        assert '--strict-mcp-config' in args
        assert json.loads(args[args.index('--json-schema') + 1]) == SCHEMA
        assert not any('含 & 的指令' in arg for arg in args)
        assert 'AI_API_KEY' not in env
        blocks = json.loads(input)['message']['content']
        assert '含 & 的指令' in blocks[0]['text']
        assert base64.b64decode(blocks[1]['source']['data']) == b'pixels'
        return subprocess.CompletedProcess(args, 0, b'{"type":"result","structured_output":{"reply":"ok"}}\n', b'')

    monkeypatch.setattr(local_agent, 'run', cli)
    assert local_agent.run_agent_json('claude', '含 & 的指令', SCHEMA, [b'pixels']) == {'reply': 'ok'}


@pytest.mark.parametrize('provider', ['copilot', 'cursor', 'gemini', 'opencode'])
def test_acp_agents_send_native_images_and_decline_tool_access(tmp_path, monkeypatch, provider):
    script = tmp_path / 'stub.py'
    script.write_text('''
import base64
import json
import sys

def get():
    return json.loads(sys.stdin.readline())

def send(**parts):
    print(json.dumps(dict(jsonrpc='2.0', **parts)), flush=True)

hello = get()
assert hello['method'] == 'initialize' and hello['params']['clientCapabilities'] == {}
send(id=hello['id'], result=dict(protocolVersion=1, agentCapabilities={'promptCapabilities': {'image': True}}))
session = get()
assert session['method'] == 'session/new' and session['params']['mcpServers'] == []
cursor = sys.argv[1] == 'cursor'
send(id=session['id'], result=dict(sessionId='example'))
if cursor:
    mode = get()
    assert mode['method'] == 'session/set_mode' and mode['params']['modeId'] == 'ask'
    send(id=mode['id'], result={})
task = get()
assert task['method'] == 'session/prompt'
if cursor:
    assert len(task['params']['prompt']) == 1
else:
    assert base64.b64decode(task['params']['prompt'][1]['data']) == b'pixels'
send(id=10, method='session/request_permission', params={})
assert get()['result']['outcome']['outcome'] == 'cancelled'
send(id=11, method='fs/write_text_file', params={})
assert get()['error']['code'] == -32601
for text in ('{"reply":', '"ok"}'):
    send(method='session/update', params={'sessionId': 'example', 'update': {
        'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text},
    }})
send(id=task['id'], result={'stopReason': 'end_turn'})
sys.stdin.read()
''', encoding='utf-8')
    real_run = local_agent.run
    monkeypatch.setattr(local_agent, '_find_agent', lambda agent, path: f'{provider}.exe')

    def cli(args, *, cwd, input, env, **options):
        if args[-1] == '--version':
            return subprocess.CompletedProcess(args, 0, b'2.3.2\n', b'')
        assert Path(args[-1]).name == 'local_agent_acp.py'
        request = json.loads(input)
        assert request['command'][0] == f'{provider}.exe'
        assert request['provider'] == provider
        if provider == 'gemini':
            assert 'decision = "deny"' in (cwd / 'utopia-policy.toml').read_text()
        elif provider == 'opencode':
            config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
            assert {'action': 'tool.use', 'resource': '*', 'effect': 'deny'} in config['policies']
        elif provider == 'copilot':
            assert env['COPILOT_ALLOW_ALL'] == 'false'
        elif provider == 'cursor':
            assert request['command'][1:] == ['acp']
            deny = json.loads((cwd / '.cursor/cli.json').read_text())['permissions']['deny']
            assert 'Write(**)' in deny and 'Shell(*)' in deny
        request['command'] = [sys.executable, str(script), provider]
        return real_run(args, cwd=cwd, input=json.dumps(request).encode(), env=env, **options)

    monkeypatch.setattr(local_agent, 'run', cli)
    images = [] if provider == 'cursor' else [b'pixels']
    assert local_agent.run_agent_json(provider, 'read the attachment', SCHEMA, images) == {'reply': 'ok'}


def test_cursor_rejects_image_job_and_requires_valid_json(monkeypatch):
    monkeypatch.setattr(local_agent, '_find_agent', lambda agent, path: 'cursor-agent.exe')
    calls = []

    def cli(args, *, cwd, **options):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b'{"reply": 7}', b'')

    monkeypatch.setattr(local_agent, 'run', cli)
    with pytest.raises(ValueError, match='只支持文本'):
        local_agent.run_agent_json('cursor', 'read the image', SCHEMA, [b'pixels'])
    assert not calls
    with pytest.raises(RuntimeError, match='结构化结果'):
        local_agent.run_agent_json('cursor', 'read this', SCHEMA, [])
