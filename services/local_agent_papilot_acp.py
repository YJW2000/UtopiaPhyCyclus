"""Launch additional Papilot-compatible CLIs for one-shot ACP replies."""

import json
from pathlib import Path


_GROK_DENIED_TOOLS = ('Bash', 'Edit', 'Read', 'Grep', 'MCPTool', 'WebFetch', 'WebSearch')
_QWEN_DENIED_TOOLS = (
    'Bash', 'Edit', 'Write', 'Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch',
    'task', 'agent', 'skill', 'tool_search', 'tool_call', 'enter_plan_mode', 'exit_plan_mode',
)
_DSH_DISABLED_PLUGINS = (
    'tool-plugin-manager', 'tool-working-directory', 'tool-bash', 'tool-pwsh', 'tool-jobs',
    'tool-fs', 'tool-fs-search', 'tool-skill', 'tool-agent-team', 'tool-subagent-control',
    'tool-subagent', 'tool-subagent-fork', 'tool-workflow', 'tool-todo', 'tool-goal',
    'tool-web', 'mcp-resources',
)


def papilot_acp_command(provider, executable, directory, env):
    if provider == 'grok':
        deny = [part for tool in _GROK_DENIED_TOOLS for part in ('--deny', tool)]
        return [executable, '--no-auto-update', '--no-subagents', '--no-memory', '--disable-web-search',
                *deny, 'agent', '--no-leader', 'stdio']

    if provider == 'kimi':
        env['KIMI_DISABLE_TELEMETRY'] = '1'
        return [executable, 'acp']

    if provider == 'qwen':
        settings = directory / '.qwen'
        settings.mkdir()
        (settings / 'settings.json').write_text(json.dumps({
            'general': {'enableAutoUpdate': False},
            'disableAllHooks': True,
            'permissions': {'deny': _QWEN_DENIED_TOOLS},
            'tools': {'exclude': _QWEN_DENIED_TOOLS, 'discoveryCommand': '', 'callCommand': ''},
        }), encoding='utf-8')
        return [executable, '--acp', '--approval-mode', 'plan', '--extensions', 'none',
                '--allowed-mcp-server-names', 'utopia-disabled', '--telemetry', 'false']

    if provider == 'deepseek':
        env['DSH_PERMISSION_MODE'] = 'read-only'
        patch = directory / 'utopia-dsh-policy.yml'
        patch.write_text(json.dumps([
            {'id': 'sandbox-policy', 'config': {'mode': 'read-only'}},
            {'id': 'approval', 'config': {'policy': 'never'}},
            {'id': 'permission', 'config': {'defaultPreset': 'read-only', 'presets': {
                'read-only': {'sandbox': 'read-only', 'approval': 'never'},
            }}},
            *[{'id': plugin, 'disabled': True} for plugin in _DSH_DISABLED_PLUGINS],
        ]), encoding='utf-8')

        # The Windows desktop launcher delegates to its embedded Electron/Node
        # runtime. Start that same entry point directly to own the ACP process.
        launcher = Path(executable)
        if launcher.name.lower() == 'dsh.cmd' and len(launcher.parents) >= 5:
            resources = launcher.parents[3]
            asar = resources / 'app.asar'
            electron = resources.parent / 'DeepSeek Harness.exe'
            if asar.is_file() and electron.is_file():
                env['ELECTRON_RUN_AS_NODE'] = '1'
                cli = asar / 'dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js'
                return [str(electron), '--expose-internals', str(cli), 'acp', '--patch', patch.name]
        return [executable, 'acp', '--patch', patch.name]

    raise ValueError(f'Unsupported Papilot ACP provider: {provider}')
