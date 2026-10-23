"""Frozen entry point. Software resources stay beside the executable."""
import argparse
import multiprocessing
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import urllib.request
import webbrowser


def main():
    multiprocessing.freeze_support()
    root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ['STREAMLIT_GLOBAL_DEVELOPMENT_MODE'] = 'false'
    # App maintenance launches sys.executable with a script or -m module.
    if len(sys.argv) > 1 and sys.argv[1] == '-m':
        module = sys.argv[2]
        sys.argv = [module, *sys.argv[3:]]
        runpy.run_module(module, run_name='__main__', alter_sys=True)
        return
    if len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
        script = Path(sys.argv[1]).resolve()
        if not script.is_relative_to(root):
            raise SystemExit('Maintenance scripts must be inside the application directory.')
        sys.argv = [str(script), *sys.argv[2:]]
        runpy.run_path(str(script), run_name='__main__')
        return
    parser = argparse.ArgumentParser(description='UtopiaPhyCyclus 1.0.0')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        import fitz
        import streamlit
        import requests
        from services.diagram_validate import validate_diagram
        from utils.core_config import SUBJECTS, BASE_DIR
        if Path(BASE_DIR).resolve() != root:
            raise RuntimeError(f'Application resources resolved outside the portable root: {BASE_DIR}')
        print(f'OK: Streamlit {streamlit.__version__}; PyMuPDF {fitz.VersionBind}; MuPDF {fitz.VersionFitz}; {len(SUBJECTS)} subjects')
        return
    from services.launcher import available_port
    port = args.port or available_port()
    (root / 'chapters').mkdir(exist_ok=True)
    if not args.no_browser:
        def open_when_ready():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(160):
                try:
                    with opener.open(f'http://127.0.0.1:{port}/_stcore/health', timeout=.5) as response:
                        if response.status == 200:
                            webbrowser.open(f'http://127.0.0.1:{port}')
                            return
                except OSError:
                    pass
                time.sleep(.25)
        threading.Thread(target=open_when_ready, daemon=True).start()
    from streamlit.web import cli
    sys.argv = ['streamlit', 'run', str(root / 'utopia_phycyclus_app.py'),
                f'--server.port={port}', '--server.address=127.0.0.1',
                '--server.headless=true', '--server.fileWatcherType=none',
                '--browser.gatherUsageStats=false', '--global.developmentMode=false']
    raise SystemExit(cli.main())


if __name__ == '__main__':
    main()
