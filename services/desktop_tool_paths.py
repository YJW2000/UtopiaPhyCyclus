"""Find desktop dependencies installed after the Python service started."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


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
    if os.name != 'nt':
        return inherited
    home = Path.home()
    local = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData/Local')
    roaming = Path(os.environ.get('APPDATA') or home / 'AppData/Roaming')
    defaults = [home / '.local/bin', home / '.codex/bin', roaming / 'npm', local / 'cursor-agent',
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
