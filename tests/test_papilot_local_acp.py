import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from services import local_agent_acp
from services.local_agent_papilot_acp import papilot_acp_command


def test_deepseek_desktop_shim_uses_bundled_cli_and_local_policy(tmp_path):
    install = tmp_path / 'DeepSeek Harness'
    launcher = install / 'resources/runtime/cli/bin/dsh.cmd'
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    electron = install / 'DeepSeek Harness.exe'
    electron.touch()
    asar = install / 'resources/app.asar'
    asar.touch()
    directory = tmp_path / 'job'
    directory.mkdir()
    env = {'DSH_PERMISSION_MODE': 'danger-full-access'}

    command = papilot_acp_command('deepseek', str(launcher), directory, env)

    assert command[:3] == [str(electron), '--expose-internals', str(
        asar / 'dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js')]
    assert command[3:5] == ['acp', '--patch']
    assert env == {'DSH_PERMISSION_MODE': 'read-only', 'ELECTRON_RUN_AS_NODE': '1'}
    policy = {entry['id']: entry for entry in json.loads((directory / command[5]).read_text())}
    assert policy['sandbox-policy']['config']['mode'] == 'read-only'
    assert policy['approval']['config']['policy'] == 'never'
    assert all(policy[plugin]['disabled'] for plugin in ('tool-pwsh', 'tool-fs', 'tool-web', 'mcp-resources'))


def test_qwen_sets_restricted_cli_and_workdir_options(tmp_path):
    command = papilot_acp_command('qwen', 'qwen.cmd', tmp_path, {})

    assert command[:6] == ['qwen.cmd', '--acp', '--approval-mode', 'plan', '--extensions', 'none']
    settings = json.loads((tmp_path / '.qwen/settings.json').read_text())
    assert settings['disableAllHooks'] is True
    assert {'Bash', 'Read', 'Edit', 'WebFetch'} <= set(settings['permissions']['deny'])


@pytest.mark.parametrize('provider', ['grok', 'kimi', 'qwen', 'deepseek', 'unsupported-kimi'])
def test_papilot_acp_protocol(provider, tmp_path):
    fake = tmp_path / 'acp_cli.py'
    fake.write_text(r'''
import json
import sys
import time

provider = sys.argv[1]

def receive(method):
    request = json.loads(sys.stdin.readline())
    assert request.get('method') == method, request
    return request

def send(**message):
    print(json.dumps({'jsonrpc': '2.0', **message}), flush=True)

def update(text, session='test-session'):
    send(method='session/update', params={'sessionId': session, 'update': {
        'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text},
    }})

init = receive('initialize')
assert init['params']['clientCapabilities'] == {}
send(id=init['id'], result={
    'protocolVersion': 1, 'agentCapabilities': {'promptCapabilities': {'image': provider != 'deepseek'}},
    'authMethods': [{'id': 'cached_token'}] if provider == 'grok' else [],
})
if provider == 'grok':
    auth = receive('authenticate')
    assert auth['params'] == {'methodId': 'cached_token', '_meta': {'headless': True}}
    send(id=auth['id'], result={})
session = receive('session/new')
assert session['params']['mcpServers'] == []
if provider == 'grok':
    assert session['params']['_meta'] == {'yoloMode': False, 'autoMode': False}
result = {'sessionId': 'test-session'}
if provider == 'kimi':
    result['modes'] = {'currentModeId': 'auto', 'availableModes': [{'id': 'plan'}]}
elif provider in {'qwen', 'deepseek'}:
    mode = 'plan' if provider == 'qwen' else 'read-only'
    config_id = 'mode' if provider == 'qwen' else 'permission_mode'
    result['configOptions'] = [dict(id=config_id, currentValue='auto', options=[{'value': mode}])]
send(id=session['id'], result=result)
if provider == 'unsupported-kimi':
    assert not sys.stdin.readline(), 'The worker must exit before sending a prompt'
    sys.exit(0)
if provider == 'kimi':
    request = receive('session/set_mode')
    assert request['params']['modeId'] == 'plan'
    send(id=request['id'], result={})
elif provider in {'qwen', 'deepseek'}:
    request = receive('session/set_config_option')
    assert request['params'] == {'sessionId': 'test-session', 'configId': config_id, 'value': mode}
    send(id=request['id'], result={'configOptions': [{'id': config_id, 'currentValue': mode}]})
prompt = receive('session/prompt')
assert len(prompt['params']['prompt']) == (1 if provider == 'deepseek' else 2)
send(id=123, method='session/request_permission', params={'options': [
    {'kind': 'allow_once', 'optionId': 'allow'}, {'kind': 'reject_once', 'optionId': 'deny'},
]})
assert receive(None)['result']['outcome']['optionId'] == 'deny'
update('ignored', 'different-session')
update('{"reply":')
if provider == 'grok':
    send(id=prompt['id'], result={'stopReason': 'end_turn'})
    time.sleep(0.05)
    update('"ok"}')
else:
    update('"ok"}')
    send(id=prompt['id'], result={'stopReason': 'end_turn'})
sys.stdin.read()
''', encoding='utf-8')
    prompt = [{'type': 'text', 'text': 'Read the inputs'}]
    if provider != 'deepseek':
        prompt.append({'type': 'image', 'data': 'aW1hZ2U=', 'mimeType': 'image/png'})
    env = {name: value for name, value in os.environ.items() if name != 'XAI_API_KEY'}
    command = [sys.executable, '-B', '-X', 'utf8', str(Path(local_agent_acp.__file__))]
    payload = {'provider': 'kimi' if provider == 'unsupported-kimi' else provider,
               'command': [sys.executable, str(fake), provider], 'prompt': prompt}

    result = subprocess.run(command, cwd=tmp_path, input=json.dumps(payload).encode(),
                            capture_output=True, env=env, timeout=10)

    if provider == 'unsupported-kimi':
        assert result.returncode == 1
        assert b'does not support the required plan mode' in result.stderr
    else:
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout) == {'reply': 'ok'}
