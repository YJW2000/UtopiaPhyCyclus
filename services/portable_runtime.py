"""Resolve bundled tools without requiring a machine-wide installation."""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
import shutil


def tex_runtime_path(root: Path) -> Path:
    """XeTeX's Windows file access needs an ASCII physical runtime path."""
    original = root / 'runtimes/texlive'
    if str(original).isascii():
        return original
    override = os.environ.get('UTOPIA_RUNTIME_CACHE')
    candidates = [Path(override)] if override else [
        Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'UtopiaPhyCyclus/runtime-cache',
        Path(os.environ.get('PUBLIC', 'C:/Users/Public'))/'Documents/UtopiaPhyCyclus/runtime-cache',
    ]
    base = next((p for p in candidates if str(p).isascii()), None)
    if base is None:
        raise RuntimeError('TeX runtime needs an ASCII cache path. Extract the application to an ASCII folder.')
    manifest = root / 'runtime-manifest.json'
    fingerprint = hashlib.sha256(manifest.read_bytes() if manifest.exists() else b'texlive-2026').hexdigest()[:16]
    target = base / ('texlive-' + fingerprint)
    marker = target / '.utopia-complete'
    if marker.exists() and marker.read_text(encoding='ascii') == fingerprint:
        return target
    print('[SETUP] Preparing the bundled TeX runtime for this extraction path...', flush=True)
    # Cache only executable resources; documentation and source remain in the ZIP.
    shutil.copytree(original, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('doc', 'source', 'font-cache'))
    marker.write_text(fingerprint, encoding='ascii')
    return target


def configure(root: Path | str) -> dict[str, str]:
    root = Path(root).resolve()
    runtimes = root / 'runtimes'
    tools = {}
    search = []
    tex = tex_runtime_path(root) if (runtimes / 'texlive/bin/windows/xelatex.exe').is_file() else runtimes / 'texlive'
    if (tex / 'bin/windows/xelatex.exe').is_file():
        search.append(str(tex / 'bin/windows'))
        tools['xelatex'] = str(tex / 'bin/windows/xelatex.exe')
        for key, value in {
            'TEXMFCNF': os.pathsep.join([str(tex), str(tex / 'texmf-dist/web2c')]), 'TEXMFROOT': str(tex),
            'TEXMFHOME': str(tex / 'texmf-local'),
            'TEXMFLOCAL': str(tex / 'texmf-local'),
            'TEXMFSYSVAR': str(tex / 'texmf-var'),
            'TEXMFSYSCONFIG': str(tex / 'texmf-config'),
            'TEXMFVAR': str(root / '.runtime/texmf-var'),
            'TEXMFCONFIG': str(root / '.runtime/texmf-config'),
            'FONTCONFIG_FILE': str(tex / 'fonts.conf'),
        }.items():
            os.environ[key] = value
    node = runtimes / 'node/node.exe'
    if node.is_file():
        search.append(str(node.parent))
        tools['node'] = str(node)
    native = runtimes / 'whisper'
    if (native / 'node_modules/@fugood/whisper.node/package.json').is_file():
        os.environ['UTOPIA_WHISPER_LIB'] = str(root / 'services/whisper_runtime.mjs')
        os.environ['UTOPIA_WHISPER_MODULE_DIR'] = str(native)
        # CPU operation works without a separately installed CUDA/Vulkan driver.
        os.environ.setdefault('UTOPIA_WHISPER_GPU', '0')
    model = runtimes / 'models/ggml-small.bin'
    if model.is_file():
        os.environ.setdefault('DSH_EARS_MODEL', str(model))
        tools['whisper_model'] = str(model)
    if search:
        previous = os.environ.get('PATH', '')
        os.environ['PATH'] = os.pathsep.join(search + [previous])
    return tools
