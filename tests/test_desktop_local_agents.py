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
    monkeypatch.setenv('ZHIPU_API_KEY', 'fixture-glm-key')
    (tmp_path / '.env').write_text('AI_BASE_URL=http://127.0.0.1:1234/v1\nAI_API_KEY=fixture-key\n'
                                  'AI_MODEL_NAME=fixture-vision-model\n', encoding='utf-8')
    handlers = desktop_library.create_library_handlers(tmp_path, state)
    monkeypatch.setattr(local_agent, '_find_agent', lambda agent, path: 'copilot.exe' if agent == 'copilot' else None)
    assert handlers['ui.load']({})['provider'] == 'codex'
    (state / 'preferences.json').write_text('{"provider":"api"}', encoding='utf-8')
    assert handlers['ui.load']({})['provider'] == 'codex'
    assert handlers['ui.providers']({})['effectiveProvider'] == 'codex'
    assert handlers['ui.save']({'provider': 'copilot'})['provider'] == 'copilot'
    for unsupported in ('api', 'arbitrary-executable'):
        with pytest.raises(ValueError):
            handlers['ui.save']({'provider': unsupported})
    status = handlers['ui.providers']({})
    assert status['effectiveProvider'] == 'copilot'
    assert 'apiConfigured' not in status and 'apiModel' not in status
    assert 'fixture-key' not in json.dumps(status) and 'fixture-vision-model' not in json.dumps(status)
    glm = next(item for item in status['agents'] if item['id'] == 'glm')
    assert glm['installed'] and not glm['images']
    assert 'fixture-glm-key' not in json.dumps(status)
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
        for unsupported in ('api', 'arbitrary-executable'):
            with pytest.raises(ValueError, match='请选择可用的本机 Agent'):
                workspace.reconstruct({'id': imported['id'], 'agent': unsupported})

        answer = _wait(workspace.jobs, workspace.reconstruct({'id': imported['id'], 'agent': 'pi'}))
        assert answer['status'] == 'succeeded', answer
        assert '小球下落' in answer['result']['source']
        assert selected_provider(state) == 'cursor'

        settings({'provider': 'gemini'})
        with pytest.raises(ValueError, match='只支持文本'):
            workspace.reconstruct({'id': imported['id'], 'agent': 'cursor'})
        answer = _wait(workspace.jobs, workspace.reconstruct({'id': imported['id']}))
        assert answer['status'] == 'succeeded', answer
        assert '小球下落' in answer['result']['source']
        assert calls[0] == ('cursor', [])
        assert calls[1][0] == 'pi' and calls[1][1][0].startswith(b'\x89PNG')
        assert calls[2][0] == 'gemini' and calls[2][1][0].startswith(b'\x89PNG')
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


@pytest.mark.parametrize('provider', ['copilot', 'cursor', 'gemini', 'opencode', 'codebuddy', 'qoder', 'agy'])
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
        elif provider == 'codebuddy':
            assert '--tools=' in request['command'] and '--strict-mcp-config' in request['command']
            assert json.loads((cwd / 'settings.json').read_text())['disableAllHooks']
        elif provider == 'qoder':
            assert request['command'][request['command'].index('--tools') + 1] == ''
            assert request['command'][request['command'].index('--disallowed-tools') + 1] == '*'
            assert json.loads((cwd / 'qoder-settings.json').read_text())['disableAllHooks']
        elif provider == 'agy':
            command = request['command']
            assert '--include-default-customizations=false' in command
            assert '--dangerously-skip-permissions' not in command
            profile = Path(command[command.index('--gemini-dir') + 1])
            assert profile.parent == cwd
            policy = json.loads((profile / 'antigravity-cli/settings.json').read_text())
            assert set(policy['permissions']['deny']) == {
                'read_file(*)', 'write_file(*)', 'read_url(*)', 'execute_url(*)', 'command(*)', 'mcp(*)',
            }
            agent = (cwd / '.agents/agents/utopia-response.md').read_text()
            assert all(rule in agent for rule in ('tools: []', 'inheritMcp: false', 'inheritCustomizations: false'))
        request['command'] = [sys.executable, str(script), provider]
        return real_run(args, cwd=cwd, input=json.dumps(request).encode(), env=env, **options)

    monkeypatch.setattr(local_agent, 'run', cli)
    images = [] if provider == 'cursor' else [b'pixels']
    assert local_agent.run_agent_json(provider, 'read the attachment', SCHEMA, images) == {'reply': 'ok'}


def test_qoder_detects_native_alias_and_reports_matching_login_command(monkeypatch):
    monkeypatch.setattr(local_agent.shutil, 'which',
                        lambda binary, *, path: 'qodercli.exe' if binary == 'qodercli' else None)
    monkeypatch.setattr(local_agent, '_find_codex', lambda path: None)
    qoder = next(agent for agent in local_agent.agent_status('bin') if agent['id'] == 'qoder')
    assert qoder['installed'] and qoder['path'] == 'qodercli.exe'
    assert qoder['loginCommand'] == 'qodercli login'


def test_codebuddy_npm_starts_node_without_a_batch_process(tmp_path):
    from services.codebuddy_agent import codebuddy_acp_command

    modules = tmp_path / 'node_modules'
    binary_dir = modules / '.bin'
    binary_dir.mkdir(parents=True)
    node = binary_dir / 'node.exe'
    node.touch()
    entry = modules / '@tencent-ai/codebuddy-code/bin/codebuddy'
    entry.parent.mkdir(parents=True)
    entry.touch()
    workspace = tmp_path / 'job'
    workspace.mkdir()
    command = codebuddy_acp_command(str(binary_dir / 'codebuddy.cmd'), workspace, {'PATH': ''})
    assert command[:2] == [str(node), str(entry)]
    assert '--acp' in command
    assert '--setting-sources=project,local' in command


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
