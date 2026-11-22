"""Start an isolated Python desktop service or the embedded classic pages."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import runpy
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == 'tools' else HERE
sys.path.insert(0, str(ROOT))


def configure_runtime() -> None:
    from services.portable_runtime import configure

    resource_root = Path(os.environ['UTOPIA_DESKTOP_RESOURCES'])
    state = Path(os.environ['UTOPIA_DESKTOP_STATE'])
    if not str(resource_root).isascii() and not os.environ.get('UTOPIA_RUNTIME_CACHE'):
        candidates = [Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'UtopiaPhyCyclusDesktop' / 'runtime-cache',
                      Path(os.environ.get('PUBLIC', 'C:/Users/Public')) / 'Documents' / 'UtopiaPhyCyclusDesktop' / 'runtime-cache']
        for candidate in candidates:
            if not str(candidate).isascii():
                continue
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            os.environ['UTOPIA_RUNTIME_CACHE'] = str(candidate)
            break
    with contextlib.redirect_stdout(sys.stderr):
        configure(resource_root)
    if os.name == 'nt':
        from services.desktop_tool_paths import fresh_tool_path
        os.environ['NoDefaultCurrentDirectoryInExePath'] = '1'
        os.environ['PATH'] = fresh_tool_path()
    os.environ['TEXMFVAR'] = str(state / 'texmf-var')
    os.environ['TEXMFCONFIG'] = str(state / 'texmf-config')
    if 'UTOPIA_WHISPER_LIB' in os.environ:
        os.environ['UTOPIA_WHISPER_LIB'] = str(ROOT / 'services/whisper_runtime.mjs')


def main() -> None:
    mode = sys.argv.pop(1)
    if mode == 'maintenance':
        import json
        from services.library_maintenance import maintain_library

        # Indexing needs neither the portable TeX tree nor agent environment discovery.
        print(json.dumps(maintain_library(Path(os.environ['UTOPIA_DESKTOP_LIBRARY']))))
        return
    configure_runtime()
    if mode == 'rpc':
        runpy.run_module('services.desktop_rpc', run_name='__main__')
    elif mode == 'legacy':
        from streamlit.web import cli

        sys.argv = ['streamlit', 'run', str(ROOT / 'desktop_legacy.py'), *sys.argv[1:]]
        raise SystemExit(cli.main())
    else:
        raise ValueError(f'Unknown desktop service: {mode}')


if __name__ == '__main__':
    main()
