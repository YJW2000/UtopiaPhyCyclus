"""Split reference PDFs out of an audited ZIP without changing executable resources."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOC_ROOT = 'runtimes/texlive/texmf-dist/doc/'
LEGAL_NAME = re.compile(r'licen[cs]e|copying|copyright|notice|lppl|(?:^|[-_.])(gpl|ofl)(?:[-_.]|$)', re.I)


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def reference_paths(tlpdb: str) -> set[str]:
    """Only entries explicitly classified as documentation by TeX Live qualify."""
    paths, section = set(), ''
    for line in tlpdb.splitlines():
        if line.startswith(('runfiles ', 'binfiles ', 'docfiles ', 'srcfiles ')):
            section = line.split()[0]
        elif line.startswith(' ') and section == 'docfiles':
            paths.add('runtimes/texlive/' + line.strip().split()[0])
        elif not line.startswith(' '):
            section = ''
    return paths


def is_reference_pdf(path: str, documented: set[str]) -> bool:
    return (path in documented and path.startswith(DOC_ROOT)
            and path.lower().endswith('.pdf') and not LEGAL_NAME.search(path))


def copy_member(source, target, info) -> str:
    """Stream large models; preserve all uncompressed bytes and ZIP metadata."""
    cloned = copy.copy(info)
    cloned.compress_type = zipfile.ZIP_DEFLATED
    cloned._compresslevel = 6
    digest = hashlib.sha256()
    with source.open(info) as src, target.open(cloned, 'w', force_zip64=True) as dst:
        while data := src.read(1024 * 1024):
            digest.update(data)
            dst.write(data)
    return digest.hexdigest()


def build(source: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    validation = json.loads((source.parent / 'validation-report.json').read_text(encoding='utf-8'))
    base_sha = sha256(source)
    assert validation['passed'] and validation['package_sha256'] == base_sha, 'Unvalidated input ZIP'
    version = validation['version']
    prefix = f'UtopiaPhyCyclus-{version}/'
    main_name = f'UtopiaPhyCyclus-{version}-windows-x64-slim.zip'
    docs_name = f'UtopiaPhyCyclus-{version}-tex-reference.zip'
    output.mkdir(parents=True, exist_ok=True)
    main_path, docs_path = output / main_name, output / docs_name
    assert source not in {main_path, docs_path}, 'Never overwrite the original release'
    main_tmp, docs_tmp = main_path.with_suffix('.zip.tmp'), docs_path.with_suffix('.zip.tmp')
    report_path = output / 'slimming-report.json'
    assert not any(p.exists() for p in [main_path, docs_path, report_path]), 'Use a new output directory'
    with zipfile.ZipFile(source) as base:
        infos = [f for f in base.infolist() if not f.is_dir()]
        names = [f.filename for f in infos]
        assert len(names) == len(set(n.casefold() for n in names)), 'Duplicate ZIP paths'
        for name in names:
            assert name.startswith(prefix) and '\\' not in name and ':' not in name
            assert '..' not in PurePosixPath(name).parts, 'Unsafe ZIP member'
        runtime = json.loads(base.read(prefix + 'runtime-manifest.json'))
        runtime_files = {f['path']: f for f in runtime['files']}
        source_manifest = json.loads(base.read(prefix + 'release-manifest.json'))
        assert source_manifest['empty_bank'] is True and source_manifest['version'] == version
        source_files = {f['path']: f for f in source_manifest['files']}
        documented = reference_paths(base.read(prefix + 'runtimes/texlive/tlpkg/texlive.tlpdb').decode('utf-8'))
        moved = {p for p in runtime_files if is_reference_pdf(p, documented)}
        assert moved and all(p in documented for p in moved)
        print(f'Splitting {len(moved)} reference PDFs; executable resources stay unchanged', flush=True)
        expected, retained_hashes = {}, {}
        with zipfile.ZipFile(docs_tmp, 'w') as docs:
            for info in infos:
                relative = info.filename[len(prefix):]
                if relative in moved:
                    actual = copy_member(base, docs, info)
                    assert actual == runtime_files[relative]['sha256'], relative
                    expected[relative] = actual
            docs.writestr(prefix + 'tex-reference-manifest.json', encoded({
                'version': version, 'base_package_sha256': base_sha,
                'description': 'Unmodified optional reference PDFs; extract beside the application to restore them.',
                'files': [runtime_files[p] for p in sorted(moved)],
            }), compress_type=zipfile.ZIP_DEFLATED)
        docs_sha = sha256(docs_tmp)
        slim_runtime = dict(runtime)
        slim_runtime['files'] = [f for f in runtime['files'] if f['path'] not in moved]
        slim_runtime['reference_documentation'] = {
            'archive': docs_name, 'sha256': docs_sha, 'required_for_application': False,
            'base_package_sha256': base_sha, 'relocated_files': len(moved),
        }
        note = f'''# 精简便携包 {version}

此包的应用程序、Python 依赖、TeX 宏包、字体、编译器、Node、Whisper 和语音模型与完整包逐文件一致。
仅将 TeX Live 标记为 docfiles 的普通 PDF 手册移至可选附件；许可文件、源码及其他文档继续保留。
不改变题目编译、PDF 预览、SQLite 题库、AI 或离线语音的运行资源，也不需要联网补装依赖。

需要查阅这些手册时，下载 `{docs_name}` 并解压到与主包相同的上级目录，合并 `UtopiaPhyCyclus-{version}` 文件夹。
附件保留原始文件与路径。两包合并可恢复全部原文件；第三方版权、许可和源代码入口见 THIRD_PARTY_NOTICES.md。
已发布的完整原包及所有第三方文件仍可从 https://github.com/YJW2000/UtopiaPhyCyclus/releases/tag/v{version} 获取。

完整原包 SHA-256：`{base_sha}`
手册附件 SHA-256：`{docs_sha}`
本包仍为空题库，不包含个人 API key、题目或数据库。请备份并迁移自己的数据。
'''
        with zipfile.ZipFile(main_tmp, 'w') as main:
            for info in infos:
                relative = info.filename[len(prefix):]
                if relative in moved:
                    continue
                if relative == 'runtime-manifest.json':
                    main.writestr(info.filename, encoded(slim_runtime), compress_type=zipfile.ZIP_DEFLATED)
                    continue
                actual = copy_member(base, main, info)
                expected[relative] = actual
                entry = runtime_files.get(relative) or source_files.get(relative)
                if entry:
                    assert actual == entry['sha256'] and info.file_size == entry['bytes'], relative
                if relative.startswith('runtimes/'):
                    assert relative in runtime_files, 'Unlisted runtime: ' + relative
                    retained_hashes[relative] = actual
            main.writestr(prefix + 'SLIM-PACKAGE.md', note.encode('utf-8'), compress_type=zipfile.ZIP_DEFLATED)
        # Verify the produced archives, including exact restoration of every input
        # member. No test fixtures or local application data enter either archive.
        with zipfile.ZipFile(main_tmp) as main, zipfile.ZipFile(docs_tmp) as docs:
            assert main.testzip() is None and docs.testzip() is None
            actual_names = set(main.namelist()) | set(docs.namelist())
            assert actual_names == set(names) | {prefix + 'SLIM-PACKAGE.md', prefix + 'tex-reference-manifest.json'}
            for name in names:
                relative = name[len(prefix):]
                if relative == 'runtime-manifest.json':
                    assert json.loads(main.read(name)) == slim_runtime
                    continue
                archive = docs if relative in moved else main
                with archive.open(name) as member:
                    assert hashlib.file_digest(member, 'sha256').hexdigest() == expected[relative], relative
            assert set(retained_hashes) == set(runtime_files) - moved
            assert main.read(prefix + 'UtopiaPhyCyclus.exe') == base.read(prefix + 'UtopiaPhyCyclus.exe')
    report = {
        'version': version, 'base_package_sha256': base_sha, 'original_bytes': source.stat().st_size,
        'main_archive': main_name, 'main_bytes': main_tmp.stat().st_size, 'main_sha256': sha256(main_tmp),
        'reference_archive': docs_name, 'reference_bytes': docs_tmp.stat().st_size, 'reference_sha256': docs_sha,
        'saved_bytes': source.stat().st_size - main_tmp.stat().st_size,
        'saved_percent': round(100 * (1 - main_tmp.stat().st_size / source.stat().st_size), 2),
        'relocated_files': len(moved), 'unchanged_runtime_files': len(retained_hashes),
        'application_and_runtime_bytes_unchanged': True,
        'archives_verified': True, 'execution_tested': False,
        'removed_runtime_files': [], 'relocated_reference_pdfs': [runtime_files[p] for p in sorted(moved)],
    }
    main_tmp.replace(main_path)
    docs_tmp.replace(docs_path)
    report_path.write_bytes(encoded(report))
    (output / 'SHA256SUMS.txt').write_text(''.join(
        f'{sha256(p)}  {p.name}\n' for p in [main_path, docs_path, report_path]), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'relocated_reference_pdfs'}), flush=True)
    return report


def main():
    version = (ROOT / 'VERSION').read_text().strip()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / f'dist/{version}/UtopiaPhyCyclus-{version}-windows-x64.zip')
    parser.add_argument('--output-dir', type=Path, default=ROOT / f'dist/{version}-slim')
    args = parser.parse_args()
    build(args.source, args.output_dir)


if __name__ == '__main__':
    main()
