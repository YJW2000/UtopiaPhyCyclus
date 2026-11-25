import json
from pathlib import Path

import pytest

from services import local_agent_qoder


def test_native_qoder_uses_acp_without_tools_or_user_extensions(tmp_path):
    command = local_agent_qoder.qoder_acp_command('qodercli.exe', tmp_path, {'PATH': ''})

    assert command[:4] == ['qodercli.exe', '--acp', '--permission-mode', 'dont_ask']
    assert command[command.index('--tools') + 1] == ''
    assert command[command.index('--disallowed-tools') + 1] == '*'
    assert command[command.index('--setting-sources') + 1] == ''
    assert '--strict-mcp-config' in command
    assert json.loads((tmp_path / 'qoder-settings.json').read_text()) == {'disableAllHooks': True}
    assert json.loads((tmp_path / 'qoder-mcp.json').read_text()) == {'mcpServers': {}}


@pytest.mark.parametrize('shim_dir', ['', 'node_modules/.bin'])
def test_npm_qoder_keeps_empty_flags_without_crossing_cmd(tmp_path, monkeypatch, shim_dir):
    shim = tmp_path / shim_dir / 'qoder.cmd'
    entry = tmp_path / 'node_modules/@qoder-ai/qodercli/bundle/qodercli.js'
    entry.parent.mkdir(parents=True)
    entry.touch()
    monkeypatch.setattr(local_agent_qoder.shutil, 'which', lambda command, path: 'node.exe')

    command = local_agent_qoder.qoder_acp_command(str(shim), tmp_path, {'PATH': ''})
    assert command[:2] == ['node.exe', str(entry)]
    assert command[command.index('--tools') + 1] == ''


def test_unknown_qoder_shim_does_not_run_with_relaxed_flags(tmp_path):
    with pytest.raises(OSError):
        local_agent_qoder.qoder_acp_command(str(Path(tmp_path, 'qoder.cmd')), tmp_path, {'PATH': ''})
