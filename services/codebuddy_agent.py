"""Prepare a text and image session with Tencent's local CodeBuddy Code CLI."""
from __future__ import annotations

import json
from pathlib import Path
import shutil


def codebuddy_acp_command(executable, directory, env):
    command = [executable]
    if executable.lower().endswith(('.cmd', '.bat')):
        folder = Path(executable).parent
        modules = folder.parent if folder.name == '.bin' else folder / 'node_modules'
        entry = modules / '@tencent-ai/codebuddy-code/bin/codebuddy'
        local_node = folder / 'node.exe'
        node = str(local_node) if local_node.is_file() else shutil.which('node', path=env['PATH'])
        if not node or not entry.is_file():
            raise OSError('CodeBuddy requires its native executable or the official npm package with Node.js.')
        # A batch wrapper can stop before its Node process releases the workspace.
        command = [node, str(entry)]
    return [*command, '--acp', '--agent', 'cli', *restricted_acp_args(directory, env)]


def restricted_acp_args(directory, env):
    settings = {
        'disableAllHooks': True,
        'permissions': {'deny': ['*', 'mcp__*'], 'disableBypassPermissionsMode': 'disable'},
    }
    (directory / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    (directory / 'mcp.json').write_text('{"mcpServers":{}}', encoding='utf-8')
    env.update(
        DISABLE_AUTOUPDATER='1',
        CODEBUDDY_DISABLE_AUTO_MEMORY='1',
        CODEBUDDY_DISABLE_PARENT_MEMORY='1',
        CODEBUDDY_DISABLE_CRON='1',
        CODEBUDDY_SKIP_BUILTIN_MARKETPLACE='1',
        CODEBUDDY_AUTO_UPDATE_THIRD_PARTY_MARKETPLACES='0',
    )
    env.pop('CODEBUDDY_IS_SANDBOX', None)
    return ['--permission-mode', 'dontAsk',
            '--tools=', '--strict-mcp-config', '--mcp-config', 'mcp.json',
            '--setting-sources=project,local', '--settings', 'settings.json', '--no-session-persistence']
