from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from services import local_agent_workbuddy as workbuddy
from services import local_agent_acp


def test_vision_acp_detects_a_stalled_startup(tmp_path):
    worker = str(Path(local_agent_acp.__file__))
    payload = {
        'provider': 'agy',
        'command': [sys.executable, '-c', 'import sys; sys.stdin.read()'],
        'prompt': [],
        'startupTimeout': 0.2,
    }
    reply = subprocess.run([sys.executable, '-B', '-X', 'utf8', worker], cwd=tmp_path,
                           input=json.dumps(payload), text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert reply.returncode == 1
    assert 'ACP startup timed out' in reply.stderr


def test_workbuddy_does_not_use_ordinary_path_launchers(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    for name in ('UTOPIA_WORKBUDDY_BIN', 'PAPILOT_WORKBUDDY_BIN'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(workbuddy, '_registered_launcher', lambda _: '')
    launcher = tmp_path / 'workbuddy-launch.exe'
    launcher.touch(mode=0o700)
    assert workbuddy.find_workbuddy_launcher(str(tmp_path)) is None

    monkeypatch.setenv('PAPILOT_WORKBUDDY_BIN', str(launcher))
    assert workbuddy.find_workbuddy_launcher() == str(launcher)

    monkeypatch.setenv('UTOPIA_WORKBUDDY_BIN', 'workbuddy-launch.exe')
    assert workbuddy.find_workbuddy_launcher() is None

    monkeypatch.delenv('UTOPIA_WORKBUDDY_BIN')
    monkeypatch.setattr(workbuddy, '_registered_launcher',
                        lambda name: f'"{launcher}"' if name == 'UTOPIA_WORKBUDDY_BIN' else '')
    assert workbuddy.find_workbuddy_launcher() == str(launcher)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Papilot-managed launcher')
def test_workbuddy_reuses_papilot_managed_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    for name in ('UTOPIA_WORKBUDDY_BIN', 'PAPILOT_WORKBUDDY_BIN'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(workbuddy, '_registered_launcher', lambda _: '')
    launcher = tmp_path / 'Papilot/agents/launchers/workbuddy.cmd'
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    assert workbuddy.find_workbuddy_launcher() == str(launcher)

    monkeypatch.setenv('UTOPIA_WORKBUDDY_BIN', str(tmp_path / 'missing.cmd'))
    assert workbuddy.find_workbuddy_launcher() is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows ACP batch launcher')
def test_workbuddy_batch_launcher_talks_standard_acp_in_a_disposable_directory(tmp_path):
    fake = tmp_path / 'stub.py'
    fake.write_text(r'''
import json
import os
from pathlib import Path
import sys
import time

assert '--agent' not in sys.argv and '--tools=' in sys.argv, sys.argv
assert '--strict-mcp-config' in sys.argv and '--no-session-persistence' in sys.argv
assert os.environ['CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS'] == '1'
assert json.loads(Path('mcp.json').read_text()) == {'mcpServers': {}}
assert '*' in json.loads(Path('settings.json').read_text())['permissions']['deny']

def read(method):
    result = json.loads(sys.stdin.readline())
    assert result['method'] == method and '_meta' not in result['params']
    return result

def send(**parts):
    print(json.dumps({'jsonrpc': '2.0', **parts}), flush=True)

init = read('initialize')
send(id=init['id'], result={'protocolVersion': 1, 'agentCapabilities': {'promptCapabilities': {'image': True}}})
session = read('session/new')
assert session['params']['mcpServers'] == []
send(id=session['id'], result={'sessionId': 'workbuddy-synthetic'})
turn = read('session/prompt')
assert turn['params']['prompt'][1]['data'] == 'YWJj'
send(id=10, method='session/request_permission', params={'options': [{'optionId': 'no', 'kind': 'reject_once'}]})
assert json.loads(sys.stdin.readline())['result']['outcome']['optionId'] == 'no'
send(method='session/update', params={'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': '{"reply":"ok"}'}}})
send(id=turn['id'], result={'stopReason': 'end_turn'})
# The real launcher owns a persistent server which stays alive after EOF.
sys.stdin.read()
time.sleep(15)
''', encoding='utf-8')
    launcher = tmp_path / 'WorkBuddy Launcher.cmd'
    launcher.write_text('@echo off\n"%UTOPIA_TEST_PYTHON%" -B -X utf8 "%UTOPIA_TEST_STUB%" %*\n', encoding='ascii')
    workspace = tmp_path / 'task'
    workspace.mkdir()
    env = dict(os.environ)
    env.update(UTOPIA_TEST_PYTHON=sys.executable, UTOPIA_TEST_STUB=str(fake))
    command = workbuddy.workbuddy_acp_command(str(launcher), workspace, env)
    prompt = [{'type': 'text', 'text': 'fixture'}, {'type': 'image', 'mimeType': 'image/png', 'data': 'YWJj'}]
    reply = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(local_agent_acp.__file__))],
                           cwd=workspace, env=env, input=json.dumps(dict(provider='workbuddy', command=command, prompt=prompt)),
                           text=True, encoding='utf-8', capture_output=True, timeout=15)
    assert reply.returncode == 0, reply.stderr
    assert json.loads(reply.stdout) == {'reply': 'ok'}
    workbuddy.cleanup_workbuddy_directory(workspace)
    assert not workspace.exists()
