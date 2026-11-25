"""Stage the files needed by the local Windows x64 desktop installer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlretrieve
import zipfile

from release import selected_files


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.release-work'
STAGE = WORK / 'desktop-preview'
PYTHON_VERSION = '3.12.10'
PYTHON_ARCHIVE = f'python-{PYTHON_VERSION}-embed-amd64.zip'
PYTHON_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ARCHIVE}'
PORTABLE = WORK / 'runtime-assets/windows-x64'
LOCK = ROOT / 'requirements-lock.txt'
DESKTOP = ROOT / 'desktop'
INSTALLERS = ROOT / 'dist/desktop-preview/installer'


def remove_stage_tree(directory: Path) -> None:
    resolved = directory.resolve()
    if resolved == STAGE.resolve() or not resolved.is_relative_to(STAGE.resolve()):
        raise ValueError(f'Path leaves desktop staging directory: {resolved}')
    if directory.exists():
        shutil.rmtree(resolved)


def stage_source() -> None:
    source = STAGE / 'app-backend'
    remove_stage_tree(source)
    approved_trees = ('services/', 'utils/', 'static/', 'fig/', 'Test Paper Group/主题模板/', 'licenses/')
    approved_files = {
        '.streamlit/config.toml', 'VERSION', 'README.md', 'LICENSE', 'NOTICE.md', 'COPYING-AGPL-3.0',
        'THIRD_PARTY_NOTICES.md', 'env.example', 'main.tex', 'UtopiaPhyCyclus_book.cls', 'ocr_prompt.txt',
        'utopia_phycyclus_app.py', 'desktop_legacy.py', 'tools/desktop_backend_entry.py',
        'desktop/sample/力与运动.tex', 'desktop/sample/力与运动.pdf',
    }
    files = approved_files | {file for file in selected_files() if file.startswith(approved_trees)}
    files.update({'static/UtopiaPhyCyclus-desktop.ico'})
    for relative in sorted(files):
        original = ROOT / relative
        if not original.resolve().is_relative_to(ROOT) or not original.is_file():
            raise ValueError(f'Missing or invalid app resource: {original}')
        destination = source / (original.name if relative.startswith('tools/') else relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    print(f'App code and public resources: {len(files)} files', flush=True)


def acquire_python(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / PYTHON_ARCHIVE
    if not archive.is_file():
        print(f'Downloading CPython {PYTHON_VERSION} (Windows embeddable x64)', flush=True)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            download = Path(temporary) / PYTHON_ARCHIVE
            urlretrieve(PYTHON_URL, download)
            with zipfile.ZipFile(download) as package:
                if package.testzip() or 'python312.dll' not in package.namelist():
                    raise RuntimeError('Invalid official CPython embeddable archive')
            download.replace(archive)
    return archive


def stage_python() -> None:
    target = STAGE / 'python'
    fingerprint = hashlib.sha256(PYTHON_URL.encode() + LOCK.read_bytes() + b'no-pip-entrypoints').hexdigest()
    if (target / '.desktop-runtime-version').is_file() and (target / '.desktop-runtime-version').read_text() == fingerprint:
        return
    archive = acquire_python(WORK / 'desktop-downloads')
    with tempfile.TemporaryDirectory(dir=STAGE, prefix='python-') as temporary:
        payload = Path(temporary) / 'python'
        with zipfile.ZipFile(archive) as package:
            package.extractall(payload)
        (payload / 'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../app-backend\nimport site\n', encoding='ascii')
        subprocess.run(
            [sys.executable, '-m', 'pip', '--disable-pip-version-check', 'install', '--only-binary=:all:',
             '--no-deps', '--no-compile', '--target', str(payload / 'Lib/site-packages'), '-r', str(LOCK)],
            check=True,
        )
        remove_stage_tree(payload / 'Lib/site-packages/bin')
        subprocess.run([str(payload / 'python.exe'), '-B', '-c',
                        'import sys, sqlite3, streamlit, pymupdf; print(sys.version, streamlit.__version__, pymupdf.VersionFitz)'],
                       check=True)
        (payload / '.desktop-runtime-version').write_text(fingerprint, encoding='ascii')
        remove_stage_tree(target)
        payload.replace(target)


def copy_runtime_asset(root: Path, name: str) -> None:
    source = root / name
    if not source.resolve().is_relative_to(root) or not source.is_file():
        raise ValueError(f'Invalid runtime resource: {source}')
    target = STAGE / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def stage_portable(root: Path, synctex: Path | None) -> None:
    root = root.resolve()
    manifest_path = root / 'runtime-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    source_files = {record['path']: record for record in manifest['files']}
    name = 'runtimes/texlive/bin/windows/synctex.exe'
    local_synctex = str(synctex) if synctex else shutil.which('synctex.exe')
    extra = Path(local_synctex).resolve() if name not in source_files and local_synctex else None
    if name not in source_files:
        if not extra or not extra.is_file():
            raise FileNotFoundError('SyncTeX is required. Pass --synctex with the TeX Live 2026 executable.')
        with extra.open('rb') as executable:
            source_files[name] = {'path': name, 'bytes': extra.stat().st_size,
                                  'sha256': hashlib.file_digest(executable, 'sha256').hexdigest()}
    manifest['files'] = sorted(source_files.values(), key=lambda record: record['path'])
    contents = json.dumps(manifest, ensure_ascii=False, indent=2)
    target_manifest = STAGE / manifest_path.name
    if target_manifest.is_file() and target_manifest.read_text(encoding='utf-8') == contents:
        if all((STAGE / relative).is_file() for relative in source_files):
            return
    remove_stage_tree(STAGE / 'runtimes')
    for relative in sorted(source_files):
        parts = Path(relative).parts
        if not parts or parts[0] != 'runtimes' or '..' in parts or any(part.startswith('.env') for part in parts):
            raise ValueError(f'Runtime manifest contains an unexpected file: {relative}')
        if relative == name and extra:
            shutil.copy2(extra, STAGE / name)
        else:
            copy_runtime_asset(root, relative)
    target_manifest.write_text(contents, encoding='utf-8')
    print(f'TeX Live, SyncTeX and speech: {len(source_files)} runtime files', flush=True)


def build_installer() -> None:
    subprocess.run(['node', 'node_modules/typescript/bin/tsc', '--noEmit'], cwd=DESKTOP, check=True)
    subprocess.run(['node', 'node_modules/vite/bin/vite.js', 'build'], cwd=DESKTOP, check=True)
    environment = os.environ.copy()
    environment.setdefault('ELECTRON_BUILDER_COMPRESSION_LEVEL', '1')
    version = json.loads((DESKTOP / 'package.json').read_text(encoding='utf-8'))['version']
    filename = f'UtopiaPhyCyclus-Desktop-Preview-{version}-windows-x64.exe'
    # NSIS executes a temporary uninstaller while building. Keep the executable
    # in the Windows temporary directory so it inherits normal integrity even
    # when the source checkout is marked Low Mandatory Level.
    with tempfile.TemporaryDirectory(prefix='UtopiaDesktopInstaller-') as output:
        subprocess.run(
            ['node', 'node_modules/electron-builder/cli.js', '--win', 'nsis', '--x64', '--publish', 'never',
             f'--config.directories.output={output}'], cwd=DESKTOP, env=environment, check=True,
        )
        INSTALLERS.mkdir(parents=True, exist_ok=True)
        destination = INSTALLERS / filename
        with tempfile.NamedTemporaryFile(dir=INSTALLERS, suffix='.partial', delete=False) as temporary:
            pending = Path(temporary.name)
        try:
            shutil.copy2(Path(output) / filename, pending)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
        print(f'Installer: {destination}', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--portable', type=Path, default=PORTABLE, help='Existing portable runtime staging root from tools/bundle_runtimes.py')
    parser.add_argument('--synctex', type=Path, help='Path to the official TeX Live 2026 SyncTeX executable')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--runtime-only', action='store_true', help='Prepare Python and TeX before the app source is ready')
    actions.add_argument('--installer', action='store_true', help='Build the renderer and local NSIS installer after staging')
    args = parser.parse_args()
    if os.name != 'nt' or platform.machine() != 'AMD64' or sys.version_info[:2] != (3, 12):
        parser.error('Build with Windows x64 and CPython 3.12')
    STAGE.mkdir(parents=True, exist_ok=True)
    stage_python()
    stage_portable(args.portable, args.synctex)
    if not args.runtime_only:
        stage_source()
    if args.installer:
        from bundle_papilot import stage_papilot

        stage_papilot(STAGE / 'papilot')
        build_installer()


if __name__ == '__main__':
    main()
