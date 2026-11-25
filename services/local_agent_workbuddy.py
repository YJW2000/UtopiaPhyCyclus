"""Connect an explicitly configured WorkBuddy ACP launcher."""
from __future__ import annotations

import os
from pathlib import Path

from services.codebuddy_agent import restricted_acp_args


_LAUNCHER_KEYS = ('UTOPIA_WORKBUDDY_BIN', 'PAPILOT_WORKBUDDY_BIN')


def _registered_launcher(name):
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


def find_workbuddy_launcher(tool_path=None):
    # A WorkBuddy desktop install is not an ACP connection. In particular,
    # don't substitute codebuddy on PATH or probe the installed GUI executable.
    for name in _LAUNCHER_KEYS:
        value = os.environ.get(name, '').strip() or _registered_launcher(name).strip()
        if not value:
            continue
        launcher = Path(value.strip('"'))
        if not launcher.is_absolute() or not launcher.is_file():
            return None
        if os.name == 'nt':
            if launcher.suffix.lower() not in {'.exe', '.cmd', '.bat'}:
                return None
            if launcher.name.lower() == 'workbuddy.exe' and (launcher.parent / 'resources/app.asar').is_file():
                return None
        elif not os.access(launcher, os.X_OK):
            return None
        return str(launcher)
    return None


def workbuddy_acp_command(executable, directory, env):
    # The launcher owns sign-in and product selection. Send standard ACP
    # requests and give the process neither project files nor usable tools.
    env['CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS'] = '1'
    return [executable, '--acp', *restricted_acp_args(directory, env)]
