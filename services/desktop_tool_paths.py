"""Find desktop dependencies installed after the Python service started."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


_PAPILOT_MANAGED_AGENTS = {
    'pi': 'pi/node_modules/.bin/pi.cmd',
    'agy': 'antigravity/agy.exe',
    'qoder': 'qoder/node_modules/.bin/qodercli.cmd',
    'codebuddy': 'launchers/codebuddy.cmd',
    'workbuddy': 'launchers/workbuddy.cmd',
}


def _registered_paths():
    import winreg

    for hive, name in (
        (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
        (winreg.HKEY_CURRENT_USER, 'Environment'),
    ):
        try:
            with winreg.OpenKey(hive, name) as key:
                value, kind = winreg.QueryValueEx(key, 'Path')
            if kind == winreg.REG_EXPAND_SZ:
                value = winreg.ExpandEnvironmentStrings(value)
        except OSError:
            continue
        if isinstance(value, str):
            yield value


def registered_tool_variable(name):
    """Read a Windows user or system setting updated since the app started."""
    if os.name != 'nt':
        return ''
    import winreg

    for hive, key_name in (
        (winreg.HKEY_CURRENT_USER, 'Environment'),
        (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
    ):
        try:
            with winreg.OpenKey(hive, key_name) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return winreg.ExpandEnvironmentStrings(value) if kind == winreg.REG_EXPAND_SZ else value
    return ''


def configured_agent_command(suffix):
    for prefix in ('UTOPIA', 'PAPILOT'):
        name = f'{prefix}_{suffix}_BIN'
        value = os.environ.get(name, '').strip() or registered_tool_variable(name).strip()
        if value:
            return value.strip('"')
    return None


def resolve_agent_command(command, tool_path):
    if not Path(command).is_absolute() and any(separator in command for separator in ('/', '\\', ':')):
        return None
    return shutil.which(command, path=tool_path)


def managed_papilot_agent(agent_id):
    relative = _PAPILOT_MANAGED_AGENTS.get(agent_id)
    if os.name != 'nt' or not relative:
        return None
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    launcher = local / 'Papilot/agents' / relative
    return str(launcher) if launcher.is_absolute() and launcher.is_file() else None


def _tex_live_bins():
    root = Path((os.environ.get('SystemDrive') or 'C:') + '/texlive')
    try:
        years = sorted((entry for entry in root.iterdir()
                        if len(entry.name) == 4 and entry.name.isascii() and entry.name.isdigit() and entry.is_dir()),
                       reverse=True)
    except OSError:
        return
    for year in years:
        yield year / 'bin/windows'


def fresh_tool_path() -> str:
    """Keep the inherited bundled-runtime order and include newly registered PATH entries."""
    inherited = os.environ.get('PATH', '')
    home = Path.home()
    agent_defaults = [home / directory for directory in ('.local/bin', '.codex/bin', '.grok/bin', '.kimi-code/bin')]
    if os.name != 'nt':
        directories = [directory for directory in inherited.split(os.pathsep) if directory]
        return os.pathsep.join(dict.fromkeys([*directories, *map(str, agent_defaults)]))
    local = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData/Local')
    roaming = Path(os.environ.get('APPDATA') or home / 'AppData/Roaming')
    defaults = [*agent_defaults, roaming / 'npm', local / 'cursor-agent', local / 'agy/bin', local / 'codebuddy/bin',
                local / 'Programs/DeepSeek Harness/resources/runtime/cli/bin',
                local / 'Microsoft/WindowsApps', local / 'Microsoft/WinGet/Links']
    programs = [os.environ.get(name) for name in ('PROGRAMW6432', 'ProgramFiles', 'ProgramFiles(x86)')]
    for base in [*(Path(value) for value in programs if value), local / 'Programs']:
        defaults.extend(base / suffix for suffix in ('Git/cmd', 'Git/bin', 'MiKTeX/miktex/bin/x64',
                                                      'MiKTeX/miktex/bin', 'nodejs'))
    defaults.extend(_tex_live_bins())
    directories = [path.strip().strip('"') for value in (inherited, *_registered_paths())
                   for path in value.split(os.pathsep)]
    kept = {}
    for directory in [*directories, *map(str, defaults)]:
        if os.path.isabs(directory):
            kept.setdefault(os.path.normcase(os.path.normpath(directory)), directory)
    return os.pathsep.join(kept.values())


def find_desktop_tool(command: str) -> str | None:
    if os.name == 'nt' and os.environ.get('UTOPIA_DESKTOP_RESOURCES'):
        return shutil.which(command, path=fresh_tool_path())
    return shutil.which(command)
