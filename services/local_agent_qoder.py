"""Launch Qoder over ACP using the local CLI's existing login."""

from pathlib import Path
import shutil


def qoder_acp_command(executable, directory, env):
    command = [executable]
    if executable.lower().endswith(('.cmd', '.bat')):
        # Invoke the npm CLI entry directly so Windows batch parsing cannot
        # discard the empty tool and setting-source arguments.
        folder = Path(executable).parent
        modules = folder.parent if folder.name == '.bin' else folder / 'node_modules'
        entry = modules / '@qoder-ai/qodercli/bundle/qodercli.js'
        node = shutil.which('node', path=env['PATH'])
        if not entry.is_file() or not node:
            raise OSError('Qoder requires its native executable or the official npm package with Node.js.')
        command = [node, str(entry)]

    (directory / 'qoder-settings.json').write_text('{"disableAllHooks":true}', encoding='utf-8')
    (directory / 'qoder-mcp.json').write_text('{"mcpServers":{}}', encoding='utf-8')
    return [*command, '--acp', '--permission-mode', 'dont_ask',
            '--tools', '', '--disallowed-tools', '*', '--setting-sources', '',
            '--settings', 'qoder-settings.json', '--strict-mcp-config', '--mcp-config', 'qoder-mcp.json']
