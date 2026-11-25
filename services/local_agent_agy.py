"""Start Google's Antigravity CLI using a tool-free ACP profile."""
from __future__ import annotations

import json


def agy_acp_command(executable, directory, env):
    # AGY can reuse Windows Credential Manager while reading customizations
    # from this request's temporary profile. No personal hooks or MCP are loaded.
    gemini = directory / 'gemini'
    settings = gemini / 'antigravity-cli'
    settings.mkdir(parents=True)
    (settings / 'settings.json').write_text(json.dumps({
        'agentMode': '',
        'toolPermission': 'request-review',
        'permissions': {
            'deny': [f'{action}(*)' for action in (
                'read_file', 'write_file', 'read_url', 'execute_url', 'command', 'mcp',
            )],
        },
    }), encoding='utf-8')
    agents = directory / '.agents/agents'
    agents.mkdir(parents=True)
    (agents / 'utopia-response.md').write_text(
        '---\n'
        'name: utopia-response\n'
        'description: Returns structured text from the supplied question and images.\n'
        'mainAgent: true\n'
        'subagent: false\n'
        'tools: []\n'
        'excludeDefaultComponents: true\n'
        'inheritMcp: false\n'
        'inheritCustomizations: false\n'
        'commandExecutionPolicy: "off"\n'
        '---\n'
        'Use only the supplied text and images to answer the request.\n',
        encoding='utf-8',
    )
    return [executable, 'acp', '--gemini-dir', str(gemini), '--agent', 'utopia-response',
            '--include-default-customizations=false', '--disable-telemetry',
            '--log-file', str(directory / 'agy.log')]
