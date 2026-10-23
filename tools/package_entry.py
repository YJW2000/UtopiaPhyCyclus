"""Installed package launcher; keep user data out of site-packages."""
import argparse
import os
from pathlib import Path
import shutil
import sys


def main():
    parser = argparse.ArgumentParser(description='UtopiaPhyCyclus')
    parser.add_argument('--data-dir', type=Path, default=Path.home() / 'UtopiaPhyCyclus')
    args, streamlit_args = parser.parse_known_args()
    resources = Path(__file__).resolve().parent / '_app'
    target = args.data_dir.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    marker = target / '.utopia-version'
    version = (resources / 'VERSION').read_text(encoding='utf-8').strip()
    if marker.exists() and marker.read_text(encoding='utf-8').strip() != version:
        parser.error('This data directory belongs to a different version; choose a new --data-dir and migrate your chapters and .env.')
    if not marker.exists():
        if any(target.iterdir()):
            parser.error('--data-dir must be empty on first use; existing data will not be overwritten.')
        shutil.copytree(resources, target, dirs_exist_ok=True)
        marker.write_text(version, encoding='utf-8')
    os.chdir(target)
    sys.path.insert(0, str(target))
    (target / 'chapters').mkdir(exist_ok=True)
    from streamlit.web import cli
    sys.argv = ['streamlit', 'run', str(target / 'utopia_phycyclus_app.py'),
                '--server.address=127.0.0.1', '--browser.gatherUsageStats=false', *streamlit_args]
    raise SystemExit(cli.main())


if __name__ == '__main__':
    main()
