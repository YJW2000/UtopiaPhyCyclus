"""Connect a configured or Papilot-managed WorkBuddy ACP launcher."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from time import sleep

from services.codebuddy_agent import restricted_acp_args
from services.desktop_tool_paths import managed_papilot_agent
from services.desktop_tool_paths import registered_tool_variable as _registered_launcher


_LAUNCHER_KEYS = ('UTOPIA_WORKBUDDY_BIN', 'PAPILOT_WORKBUDDY_BIN')


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
    return managed_papilot_agent('workbuddy')


def workbuddy_acp_command(executable, directory, env):
    # The launcher owns sign-in and product selection. Send standard ACP
    # requests and give the process neither project files nor usable tools.
    env['CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS'] = '1'
    return [executable, '--acp', *restricted_acp_args(directory, env)]


def cleanup_workbuddy_directory(directory):
    # Windows may release a descendant's current-directory handle shortly
    # after taskkill returns. Retry only that transient sharing violation.
    for delay in (0, 0.05, 0.1, 0.2, 0.4):
        if delay:
            sleep(delay)
        try:
            shutil.rmtree(directory)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            if getattr(error, 'winerror', None) != 32:
                return
