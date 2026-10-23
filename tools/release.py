"""Allowlisted empty-bank releases. Never archive the working directory or Git history."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.release-work'
SOURCE = WORK / 'source'
DIST = ROOT / 'dist'
VERSION = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
FILES = [
    '.gitignore', 'README.md', 'LICENSE', 'NOTICE.md', 'COPYING-AGPL-3.0',
    'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'VERSION', 'env.example',
    'requirements.txt', 'requirements-build.txt', 'requirements-lock.txt', 'dependency-sources.json',
    'utopia_phycyclus_app.py', 'UtopiaPhyCyclus_book.cls',
    'ocr_prompt.txt', '启动乌托邦高中物理题库助手.bat', '启动乌托邦高中物理题库助手.py',
    '.streamlit/config.toml', 'docs/发行与安装.md', 'docs/harness_v2_contract.md',
    'docs/物理插图矫正AGENT_Harness强化说明.md',
    'fig/UtopiaPhyCyclus_logo.png', 'fig/UtopiaPhyCyclus_sisyphus_icon.ico',
    'fig/UtopiaPhyCyclus_sisyphus_icon.png', 'fig/utopia_phycyclus_workflow.png',
    'tools/release.py', 'tools/portable_entry.py', 'tools/package_entry.py',
    'tools/UtopiaPhyCyclus.spec', 'tools/fetch_release_licenses.py',
    'tools/verify_release.py',
]
TREES = {
    'services': {'.py', '.mjs', '.json'},
    'utils': {'.py', '.html'},
    'static': {'.js', '.png', '.webp', '.md'},
    'Test Paper Group/主题模板': {'.tex', '.cls'},
    'licenses': {'.txt', '.md'},
}
DENIED_PARTS = {'.env', '.backups', '.runtime', '.tikz_cache', '.git', '.venv',
                '__pycache__', 'deliverables', '导出文件', 'tests', '题库索引表.csv',
                'log.csv', '.api_models_cache.json', '用户群.png'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def selected_files(root=ROOT):
    result = set(FILES)
    for name, suffixes in TREES.items():
        result.update(p.relative_to(root).as_posix() for p in (root / name).rglob('*')
                      if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts)
    return sorted(result)


def safe_output(path):
    path = path.resolve()
    if not path.is_relative_to(WORK.resolve()) or path == WORK.resolve():
        raise ValueError(f'Unsafe build output: {path}')
    return path


def private_hashes():
    # Only content fingerprints are retained in memory, never filenames or data in public reports.
    files = list((ROOT / 'chapters').rglob('*'))
    files += [ROOT / '.env', ROOT / 'utils' / '题库索引表.csv']
    files += list((ROOT / 'utils').glob('*.sqlite3'))
    return {digest(p.read_bytes()) for p in files if p.is_file() and p.stat().st_size > 32}


def local_secrets():
    path = ROOT / '.env'
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        value = value.strip().strip('\"\'')
        if any(word in key.upper() for word in ['KEY', 'TOKEN', 'PASSWORD', 'SECRET']) and len(value) >= 12:
            result.append(value.encode())
    return result


def inspect_entry(name, data, private):
    if any(secret in data for secret in local_secrets()):
        raise ValueError(f'Local credential content matched: {name}')
    parts = Path(name).parts
    if any(p in DENIED_PARTS for p in parts):
        raise ValueError(f'Private/runtime path: {name}')
    if 'chapters' in parts and Path(name).name != 'README.md':
        raise ValueError(f'Question bank content: {name}')
    if Path(name).suffix.lower() in {'.csv', '.sqlite3', '.db', '.pdf', '.lnk'}:
        raise ValueError(f'Data artifact: {name}')
    if len(data) > 32 and digest(data) in private:
        raise ValueError(f'Private content fingerprint matched: {name}')
    if Path(name).suffix.lower() in {'.py', '.txt', '.md', '.json', '.toml', '.html', '.mjs', '.tex', '.cls', '.example'}:
        text = data.decode('utf-8-sig', errors='replace')
        if re.search(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', text):
            raise ValueError(f'Credential-like content: {name}')
        if Path(name).suffix == '.tex' and re.search(r'\\begin\{(?:problem|question)\}', text):
            raise ValueError(f'Question instance in template: {name}')


def prepare():
    files = selected_files()
    private = private_hashes()
    for name in files:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing or symlinked release source: {name}')
        inspect_entry(name, path.read_bytes(), private)
    if SOURCE.exists():
        # Preserve a publication checkout across rebuilds; replace only selected files.
        extras = [p for p in SOURCE.rglob('*') if p.is_file() and '.git' not in p.parts
                  and p.relative_to(SOURCE).as_posix() not in set(files + ['chapters/README.md', 'release-manifest.json'])]
        for path in extras:
            safe_output(path).unlink()
    SOURCE.mkdir(parents=True, exist_ok=True)
    for name in files:
        target = SOURCE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    (SOURCE / 'chapters').mkdir(exist_ok=True)
    (SOURCE / 'chapters' / 'README.md').write_text(
        '# 本地题库\n\n发行版不含任何题目。请在工作台导入自己的题目。\n'
        '题目、图片、答案及 content_*.tex 汇总只保存在本机，不应提交 GitHub。\n', encoding='utf-8')
    # The public repository itself uses the same privacy policy.
    manifest = [{'path': p.relative_to(SOURCE).as_posix(), 'sha256': digest(p.read_bytes()), 'bytes': p.stat().st_size}
                for p in sorted(SOURCE.rglob('*')) if p.is_file() and '.git' not in p.parts and p.name != 'release-manifest.json']
    (SOURCE / 'release-manifest.json').write_text(json.dumps({'version': VERSION, 'empty_bank': True, 'files': manifest}, ensure_ascii=False, indent=2), encoding='utf-8')
    audit_tree(SOURCE, private)
    print(f'Prepared {len(manifest)} reviewed software files at {SOURCE}')


def audit_tree(root, private=None):
    private = private_hashes() if private is None else private
    count = 0
    for p in root.rglob('*'):
        if not p.is_file() or '.git' in p.parts:
            continue
        name = p.relative_to(root).as_posix()
        # Third-party runtime distributions legitimately contain caches and licenses.
        if name.startswith('_internal/'):
            if '.env' in p.parts or digest(p.read_bytes()) in private:
                raise ValueError(f'Private content in frozen runtime: {name}')
        else:
            inspect_entry(name, p.read_bytes(), private)
        count += 1
    return count


def zip_tree(folder, target, prefix):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in sorted(folder.rglob('*')):
            if file.is_file() and '.git' not in file.parts and '__pycache__' not in file.parts:
                z.write(file, prefix + '/' + file.relative_to(folder).as_posix())


def run(*args, cwd=None):
    subprocess.run([str(a) for a in args], cwd=cwd or ROOT, check=True)


def build(skip_frozen=False):
    for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            name, version = line.strip().split('==')
            if importlib.metadata.version(name) != version:
                raise RuntimeError(f'Build dependency mismatch: install {name}=={version} in the build environment')
    prepare()
    DIST.mkdir(exist_ok=True)
    project = safe_output(WORK / 'p')
    if project.exists():
        shutil.rmtree(project)
    package = project / 'utopia_phycyclus'
    package.mkdir(parents=True)
    shutil.copytree(SOURCE, package / '_app', ignore=shutil.ignore_patterns('.git', '__pycache__'))
    (package / '__init__.py').write_text(f'__version__ = "{VERSION}"\n', encoding='utf-8')
    shutil.copy2(ROOT / 'tools/package_entry.py', package / '__main__.py')
    shutil.copy2(SOURCE / 'README.md', project / 'README.md')
    shutil.copy2(SOURCE / 'LICENSE', project / 'LICENSE')
    dependencies = [line for line in (SOURCE / 'requirements.txt').read_text(encoding='utf-8').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
    (project / 'pyproject.toml').write_text(f'''[build-system]
requires = ["setuptools==80.9.0", "wheel==0.45.1"]
build-backend = "setuptools.build_meta"
[project]
name = "utopia-phycyclus"
version = "{VERSION}"
description = "Local high school physics question bank; empty-bank distribution"
readme = "README.md"
requires-python = ">=3.10,<3.13"
license = "MIT"
authors = [{{name = "YJW2000"}}]
dependencies = {json.dumps(dependencies)}
[project.urls]
Repository = "https://github.com/YJW2000/UtopiaPhyCyclus"
[project.scripts]
utopia-phycyclus = "utopia_phycyclus.__main__:main"
[tool.setuptools.packages.find]
include = ["utopia_phycyclus"]
[tool.setuptools.package-data]
utopia_phycyclus = ["_app/**/*", "_app/.gitignore", "_app/.streamlit/config.toml"]
''', encoding='utf-8')
    (project / 'MANIFEST.in').write_text('recursive-include utopia_phycyclus/_app *\nglobal-exclude *.pyc\nglobal-exclude __pycache__\n', encoding='utf-8')
    run(sys.executable, '-m', 'build', '--no-isolation', '--outdir', DIST, project)
    # Execute the spec from the reviewed copy, never from the private workspace.
    if not skip_frozen:
        run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--distpath', WORK / 'frozen', '--workpath', WORK / 'pyinstaller',
            SOURCE / 'tools/UtopiaPhyCyclus.spec')
    portable = WORK / 'frozen/UtopiaPhyCyclus'
    if not (portable / 'UtopiaPhyCyclus.exe').is_file():
        raise RuntimeError('No compiled executable; run build first')
    allowed = {p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob('*') if p.is_file() and '.git' not in p.parts}
    for file in portable.rglob('*'):
        if file.is_file():
            relative = file.relative_to(portable).as_posix()
            if not relative.startswith('_internal/') and relative != 'UtopiaPhyCyclus.exe' and relative not in allowed:
                safe_output(file).unlink()
    shutil.copytree(SOURCE, portable, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    for folder in ['services', 'utils']:
        # Modules bundled for analysis still resolve resources using __file__.
        # Mirror only reviewed resources into _internal for those frozen modules.
        shutil.copytree(SOURCE / folder, portable / '_internal' / folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(SOURCE / 'ocr_prompt.txt', portable / '_internal/ocr_prompt.txt')
    audit_tree(portable)
    zip_tree(SOURCE, DIST / f'UtopiaPhyCyclus-{VERSION}-source.zip', f'UtopiaPhyCyclus-{VERSION}')
    zip_tree(portable, DIST / f'UtopiaPhyCyclus-{VERSION}-windows-x64.zip', f'UtopiaPhyCyclus-{VERSION}')
    shutil.copy2(SOURCE / 'release-manifest.json', DIST / 'release-manifest.json')
    shutil.copy2(SOURCE / 'dependency-sources.json', DIST / 'dependency-sources.json')
    checksums = [f'{digest(p.read_bytes())}  {p.name}' for p in sorted(DIST.iterdir())
                 if p.is_file() and p.name != 'SHA256SUMS.txt']
    (DIST / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    audit()


def assemble():
    """Refresh software/data archives without recompiling an unchanged frozen entry."""
    build(skip_frozen=True)


def audit():
    private = private_hashes()
    n = audit_tree(SOURCE, private)
    archives = []
    for archive in sorted(DIST.iterdir() if DIST.exists() else []):
        if archive.suffix in {'.zip', '.whl'}:
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    # Drop archive wrapper and installed-package wrapper for path checks.
                    if '/_internal/' in name:
                        data = z.read(info)
                        if digest(data) in private:
                            raise ValueError(f'Private content in {archive.name}: {name}')
                    else:
                        inspect_entry(name, z.read(info), private)
            archives.append(archive.name)
        elif archive.name.endswith('.tar.gz'):
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        inspect_entry(member.name, tar.extractfile(member).read(), private)
            archives.append(archive.name)
    print(json.dumps({'source_files_checked': n, 'archives_checked': archives,
                      'private_content_matches': 0, 'question_files': 0}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'build', 'assemble', 'audit'])
    globals()[parser.parse_args().command]()
