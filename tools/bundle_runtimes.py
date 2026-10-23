"""Prepare a relocatable, allowlisted TeX/Node/Whisper payload for Windows x64."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / '.release-work/runtime-assets/windows-x64'
TEX_PACKAGES = '''texlive.infra kpathsea xetex luatex pdftex dvipdfmx latex latex-bin
latexconfig l3kernel l3packages l3backend fontspec unicode-math ctex fandol tex-gyre
tex-gyre-math amsmath amsfonts standalone preview xkeyval pgf pgfplots circuitikz
siunitx tasks exam-zh tcolorbox pgfornament shapepar fontawesome5 wrapfig titlesec
tocloft tools enumitem geometry hyperref etoolbox lastpage xcolor colortbl float
caption mdframed multirow pdfpages booktabs varwidth adjustbox environ filehook
etex-pkg psnfss amscls diagbox fancyhdr mathtools tkz-euclide tikzpagenodes
pgfopts linegoal listings needspace ninecolors pdfcol tabularray tikzfill
ulem wrapstuff xpatch zref xits asana-math'''.split()
TEX_PACKAGES += '''auxhook bigintcalc bitset cjk cm eso-pic etexcmds fontname
gettitlestring graphics graphics-cfg graphics-def hycolor ifoddpage iftex infwarerr
intcalc kvdefinekeys kvoptions kvsetkeys latex-fonts ltxcmds pdfescape pdflscape
pdftexcmds pict2e refcount rerunfilecheck stringenc translations trimspaces
uniquecounter url xecjk zapfding zhnumber'''.split()


def copy(source, destination):
    if destination.is_file() and destination.stat().st_size == source.stat().st_size and destination.stat().st_mtime_ns == source.stat().st_mtime_ns:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def prepare_tex(source):
    records = {block.splitlines()[0][5:]: block for block in
               (source / 'tlpkg/texlive.tlpdb').read_text(encoding='utf-8').split('\n\n')
               if block.startswith('name ')}
    pending, selected = list(TEX_PACKAGES), set()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in records:
            raise RuntimeError(f'Missing TeX package: {name}')
        selected.add(name)
        for line in records[name].splitlines():
            if line.startswith('depend '):
                dependency = line[7:].replace('.ARCH', '.windows')
                if '/' not in dependency and ':' not in dependency:
                    pending.append(dependency)
        if name + '.windows' in records:
            pending.append(name + '.windows')
    tex = TARGET / 'runtimes/texlive'
    paths = set()
    for name in sorted(selected):
        section = ''
        for line in records[name].splitlines():
            if line.startswith(('runfiles ', 'binfiles ', 'docfiles ', 'srcfiles ')):
                section = line.split()[0]
            elif line.startswith(' ') and section:
                relative = line.strip().split()[0]
                if (source / relative).is_file():
                    paths.add(relative)
            elif not line.startswith(' '):
                section = ''
    for relative in sorted(paths):
        original = (source / relative).resolve()
        if not original.is_relative_to(source.resolve()):
            raise ValueError(f'TeX file escapes the official installation: {relative}')
        copy(original, tex / relative)
    # Prebuilt formats and map files are generated installation artifacts that
    # are not part of individual tlpdb runfile lists. No user texmf-local is copied.
    for relative in ['texmf-var/fonts/map']:
        if (source / relative).is_dir():
            shutil.copytree(source / relative, tex / relative, dirs_exist_ok=True)
    for relative in ['texmf-var/web2c/xetex/xelatex.fmt', 'texmf-var/web2c/pdftex/pdflatex.fmt',
                     'texmf-var/web2c/luahbtex/lualatex.fmt', 'texmf-config/web2c/updmap.cfg',
                     'texmf-config/web2c/fmtutil.cnf']:
        if (source / relative).is_file():
            copy(source / relative, tex / relative)
    for name in ['LICENSE.TL', 'LICENSE.CTAN', 'release-texlive.txt', 'README']:
        copy(source / name, tex / name)
    (tex / 'texmf.cnf').write_text('''% UtopiaPhyCyclus portable overrides. Other settings use TeX Live defaults.
TEXMFHOME = $TEXMFROOT/texmf-local
TEXMFLOCAL = $TEXMFROOT/texmf-local
TEXMFSYSVAR = $TEXMFROOT/texmf-var
TEXMFSYSCONFIG = $TEXMFROOT/texmf-config
OSFONTDIR = $TEXMFROOT/texmf-dist/fonts/opentype//;$TEXMFROOT/texmf-dist/fonts/truetype//
''', encoding='utf-8')
    (tex / 'fonts.conf').write_text('''<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir prefix="relative">texmf-dist/fonts/opentype</dir>
  <dir prefix="relative">texmf-dist/fonts/truetype</dir>
  <cachedir prefix="relative">font-cache</cachedir>
  <config><rescan><int>0</int></rescan></config>
</fontconfig>
''', encoding='utf-8')
    local_config = tex / 'texmf-local/tex/latex/ctex/ctexopts.cfg'
    local_config.parent.mkdir(parents=True, exist_ok=True)
    local_config.write_text('% UtopiaPhyCyclus portable CTeX defaults; upstream files are unchanged.\n'
                            '\\ExplSyntaxOn\n\\ctex_set:nn { option } { fontset = fandol }\n', encoding='utf-8')
    (tex / 'README.UtopiaPhyCyclus.txt').write_text(
        'UtopiaPhyCyclus portable subset, based on TeX Live 2026.\n'
        'This is a selected package set, not the complete TeX Live distribution.\n'
        'Official package contents are unchanged. Portable configuration and a\n'
        'preinstalled package selection are maintained by YJW2000.\n'
        'Upstream: https://tug.org/texlive/ ; licenses: LICENSE.TL and each package.\n'
        'Package source files and original documentation are included. Engine sources:\n'
        'https://tug.org/texlive/svn/ ; https://tug.org/texlive/Contents/live/\n', encoding='utf-8')
    (tex / 'tlpkg').mkdir(exist_ok=True)
    (tex / 'tlpkg/texlive.tlpdb').write_text('\n\n'.join(records[n] for n in sorted(selected)) + '\n', encoding='utf-8')
    # Regenerate filename databases with relative entries after selecting packages.
    for tree in ['texmf-dist', 'texmf-var', 'texmf-config', 'texmf-local']:
        base = tex / tree
        base.mkdir(exist_ok=True)
        lines = ['% ls-R -- filename database for kpathsea; do not change this line.']
        for directory in sorted([base, *[p for p in base.rglob('*') if p.is_dir()]]):
            relative = directory.relative_to(base).as_posix()
            lines += ['', ('./' if relative == '.' else './' + relative) + ':']
            lines += sorted(p.name for p in directory.iterdir())
        (base / 'ls-R').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'TeX Live: {len(selected)} packages, {len(paths)} official files', flush=True)
    return sorted(selected)


def prepare_speech(plugin, model, node, licenses):
    whisper = TARGET / 'runtimes/whisper'
    whisper.mkdir(parents=True, exist_ok=True)
    (whisper / 'package.json').write_text('{"name":"utopia-local-speech","private":true}\n', encoding='utf-8')
    # Only public MIT packages are copied. The private dsh-ears plugin is not bundled.
    versions = {}
    for name in ['whisper.node', 'node-whisper-win32-x64']:
        source = plugin / 'node_modules/@fugood' / name
        meta = json.loads((source / 'package.json').read_text(encoding='utf-8'))
        if meta.get('license') != 'MIT':
            raise RuntimeError(f'Unexpected native speech license: {name}')
        shutil.copytree(source, whisper / 'node_modules/@fugood' / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('node_modules', '.git', '.cache'))
        versions[name] = meta['version']
    copy(node, TARGET / 'runtimes/node/node.exe')
    copy(model, TARGET / 'runtimes/models/ggml-small.bin')
    for file in licenses.iterdir():
        if file.is_file():
            copy(file, TARGET / 'runtimes/licenses' / file.name)
    print('Speech: portable Node, CPU Whisper and multilingual small model', flush=True)
    return versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--texlive', type=Path, required=True)
    parser.add_argument('--speech-plugin', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--licenses', type=Path, required=True)
    args = parser.parse_args()
    TARGET.mkdir(parents=True, exist_ok=True)
    packages = prepare_tex(args.texlive)
    speech = prepare_speech(args.speech_plugin, args.model, args.node, args.licenses)
    files = []
    for file in sorted(TARGET.rglob('*')):
        if file.is_file() and file.name != 'runtime-manifest.json':
            with file.open('rb') as content:
                sha = hashlib.file_digest(content, 'sha256').hexdigest()
            files.append({'path': file.relative_to(TARGET).as_posix(),
                          'bytes': file.stat().st_size, 'sha256': sha})
    manifest = {'architecture': 'AMD64', 'texlive': {'release': '2026', 'packages': packages},
                'speech_packages': speech, 'model': 'ggml-small (multilingual)', 'files': files}
    (TARGET / 'runtime-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Runtime payload: {len(files)} files; {sum(f["bytes"] for f in files)/1048576:.1f} MiB', flush=True)


if __name__ == '__main__':
    main()
