# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = [], [], []
for name in ['streamlit', 'pymupdf', 'fitz']:
    d, b, h = collect_all(name)
    datas += d
    binaries += b
    hiddenimports += h
for name in ['streamlit', 'requests', 'python-dotenv', 'Pillow', 'pymupdf']:
    datas += copy_metadata(name, recursive=True)
# Project modules load from the external, editable source tree. Analyze their imports
# as hidden modules too, so third-party dependencies are present in the frozen runtime.
for folder in ['services', 'utils']:
    for file in (root / folder).rglob('*.py'):
        if '__pycache__' not in file.parts:
            hiddenimports.append('.'.join(file.relative_to(root).with_suffix('').parts).removesuffix('.__init__'))

a = Analysis([str(root / 'tools' / 'portable_entry.py')], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=['pytest', 'streamlit.testing', 'tkinter', 'matplotlib', 'scipy', 'IPython'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='UtopiaPhyCyclus',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, icon=str(root / 'fig' / 'UtopiaPhyCyclus_sisyphus_icon.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='UtopiaPhyCyclus')
