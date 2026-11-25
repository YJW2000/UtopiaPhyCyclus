"""Make and read an isolated Papilot paper for an editable Utopia question."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import re
import shutil
from tempfile import TemporaryDirectory
from typing import Iterable

from services.tex_compile_service import build_preview_document
from services.tex_import_service import read_tex
from utils.image_assets import IMAGE_SUFFIXES, _uncomment, graphics_references, resolve_image


_ENTRY = 'main.tex'
_QUESTION = 'question.tex'
_MANIFEST = 'project.json'
_ORIGINAL = 'original.tex'
_ENGINE_LINE = '% !TeX program = xelatex\n'
_ENTRY_COMMANDS = re.compile(
    r'\\(?P<command>input|include|subfile|includepdf|bibliography|addbibresource|'
    r'documentclass|LoadClass(?:WithOptions)?|usepackage|RequirePackage(?:WithOptions)?)\*?\s*'
    r'(?:\[[^\]]*\]\s*)?\{(?P<value>[^{}]*)\}')
_CLASS = re.compile(r'(?m)^\s*\\documentclass\b')
_ENGINE = re.compile(r'(?im)^%\s*!\s*TEX\s+(?:TS-)?program\s*=')
_INPUT_ENCODING = re.compile(r'(?im)^(\s*\\XeTeXinputencoding\s*)"[^"]+"')
_SOURCE_SUFFIX = {
    'input': '.tex', 'include': '.tex', 'subfile': '.tex', 'includepdf': '.pdf',
    'bibliography': '.bib', 'addbibresource': '.bib', 'documentclass': '.cls',
    'LoadClass': '.cls', 'LoadClassWithOptions': '.cls', 'usepackage': '.sty',
    'RequirePackage': '.sty', 'RequirePackageWithOptions': '.sty',
}
_LIST_COMMANDS = {'bibliography', 'usepackage', 'RequirePackage', 'RequirePackageWithOptions'}
_NO_SUFFIX_COMMANDS = _LIST_COMMANDS | {'include', 'documentclass', 'LoadClass', 'LoadClassWithOptions'}
_TEXT_SUFFIXES = {'.tex', '.cls', '.sty', '.bib', '.bbl', '.def', '.cfg', '.tikz'}
_RESOURCE_SUFFIXES = _TEXT_SUFFIXES | set(IMAGE_SUFFIXES) | {'.eps', '.svg', '.csv', '.dat', '.txt'}
_PRIVATE_NAMES = {'.git', '.env', 'node_modules'}


def _digest(payload: str | bytes) -> str:
    return hashlib.sha256(payload.encode('utf-8') if isinstance(payload, str) else payload).hexdigest()


def _filename(value: str, fallback: str = '题目') -> str:
    # Keep the generated title and dependency paths usable by Windows TeX tools.
    value = re.sub(r'[<>:"/\\|?*{}%#\x00-\x1f]', '_', value).strip(' .')[:48].rstrip(' .')
    if not value or PureWindowsPath(value).is_reserved() or value.casefold() in _PRIVATE_NAMES:
        return fallback
    return value


def _normalized_tex(source: str) -> str:
    text = source.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    return _INPUT_ENCODING.sub(r'\1"UTF-8"', text)


@dataclass(frozen=True)
class _Reference:
    start: int
    end: int
    command: str
    name: str

    def path_spelling(self, path: str) -> str:
        return path.removesuffix(_SOURCE_SUFFIX[self.command]) if self.command in _NO_SUFFIX_COMMANDS else path


def _references(source: str) -> list[_Reference]:
    refs = [_Reference(ref['path_start'], ref['path_end'], 'graphics', ref['path'])
            for ref in graphics_references(source)]
    for match in _ENTRY_COMMANDS.finditer(_uncomment(source)):
        value = match['value']
        parts = re.finditer(r'[^,]+', value) if match['command'] in _LIST_COMMANDS else [re.match(r'.*', value)]
        for part in parts:
            if not part:
                continue
            name = part[0].strip().strip('"')
            if name and not re.search(r'[\\{}%#\x00-\x1f]', name):
                start = match.start('value') + part.start()
                refs.append(_Reference(start, start + len(part[0]), match['command'], name))
    return sorted(refs, key=lambda ref: ref.start, reverse=True)


def _owned(path: Path, root: Path) -> bool:
    if not path.is_relative_to(root):
        return False
    return all(part.casefold() not in _PRIVATE_NAMES and not part.casefold().startswith('.env.')
               for part in path.relative_to(root).parts)


class _Assets:
    def __init__(self, roots: Iterable[Path], support: Path | None = None):
        self.roots = tuple(dict.fromkeys(path.resolve() for path in roots if path.is_dir()))
        self.support = support
        self.files: dict[str, str | bytes] = {}
        self.entries: dict[Path, str] = {}

    def resolve(self, ref: _Reference, source: str, directory: Path) -> Path | None:
        bases = tuple(dict.fromkeys((directory, *self.roots)))
        spelling = Path(ref.name)
        choices = (spelling,)
        if not spelling.suffix and ref.command != 'graphics':
            choices += (spelling.with_suffix(_SOURCE_SUFFIX[ref.command]),)
        if ref.command == 'graphics':
            # Reuse the question editor's extension and graphicspath rules.
            for base in bases:
                try:
                    found = resolve_image(ref.name, base, directory, source)
                except FileNotFoundError:
                    continue
                return self.checked(found)
        else:
            for base in bases:
                for choice in choices:
                    candidate = base / choice
                    if candidate.is_file():
                        return self.checked(candidate)
        if spelling.is_absolute() or PureWindowsPath(ref.name).drive or '..' in spelling.parts:
            raise ValueError(f'无法从项目资源目录复制 LaTeX 引用：{ref.name}')
        return None  # A distribution package or a missing relative file can be diagnosed by TeX.

    def checked(self, path: Path) -> Path:
        file = path.resolve(strict=True)
        if file.suffix.lower() not in _RESOURCE_SUFFIXES or not any(_owned(file, root) for root in self.roots):
            raise ValueError(f'LaTeX 引用超出可复制的项目资源：{path.name}')
        return file

    def add(self, file: Path) -> str:
        existing = self.entries.get(file)
        if existing:
            return existing
        stem = _filename(file.stem, 'resource')
        suffix = file.suffix.lower()
        if self.support is not None and file.parent == self.support and file.name in ('UtopiaPhyCyclus_book.cls', 'warm-physics.sty'):
            relative = file.name
        else:
            relative = f'assets/{_digest(file.as_posix())[:16]}/{stem}{suffix}'
        # Reserve before rewriting nested TeX, so repeated/cyclic inputs do not recurse forever.
        self.entries[file] = relative
        self.files[relative] = (self.rewrite(_normalized_tex(read_tex(file)), file.parent)
                                if suffix in _TEXT_SUFFIXES else file.read_bytes())
        return relative

    def rewrite(self, source: str, directory: Path) -> str:
        original = source
        for ref in _references(original):
            file = self.resolve(ref, original, directory)
            if file is not None:
                target = ref.path_spelling(self.add(file))
                source = source[:ref.start] + target + source[ref.end:]
        return source


def _safe_file(folder: Path, name: str) -> Path:
    file = folder / name
    if file.is_symlink() or not file.is_file() or file.resolve().parent != folder.resolve():
        raise ValueError(f'找不到受管理的 Papilot 文件：{name}')
    return file


def _project(root: Path) -> tuple[Path, dict]:
    if root.resolve() != root.parent.resolve() / root.name:
        raise ValueError('Papilot 项目目录无效。')
    manifest = json.loads(_safe_file(root, _MANIFEST).read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise ValueError('Papilot 项目记录无效。')
    folder_name = manifest.get('folder')
    if not isinstance(folder_name, str) or folder_name != _filename(folder_name):
        raise ValueError('Papilot 项目记录中的目录无效。')
    folder = root / folder_name
    if not folder.is_dir() or folder.resolve() != root.resolve() / folder_name:
        raise ValueError('找不到受管理的 Papilot 项目目录。')
    return folder, manifest


def _write_files(folder: Path, files: dict[str, str | bytes]) -> None:
    for relative, data in files.items():
        output = folder / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)


def prepare_question_project(root: Path, source: str, title: str, project_dir: Path,
                             resource_dirs: Iterable[Path], defaults: dict) -> dict[str, str]:
    """Copy a draft and its literal dependencies. A prepared paper is always reopened in place."""
    if root.exists() or root.is_symlink():
        folder, record = _project(root)
        _safe_file(folder, _ENTRY)
        return {'folder': str(folder), 'baseline': record['baseline']}
    if not source.strip():
        raise ValueError('请先录入或识别题目 LaTeX。')
    root.parent.mkdir(parents=True, exist_ok=True)
    project_dir = project_dir.resolve()
    resource_dirs = tuple(path.resolve() for path in resource_dirs if path.is_dir())
    primary = resource_dirs[0] if resource_dirs else project_dir
    assets = _Assets((*resource_dirs, project_dir), project_dir)

    standalone = bool(_CLASS.search(_uncomment(source)))
    if standalone:
        body = _normalized_tex(source)
        engine_added = not _ENGINE.search(body)
        main = (_ENGINE_LINE if engine_added else '') + assets.rewrite(body, primary)
    else:
        # Reuse the bank's class/preamble and parsing. An incomplete fragment can
        # still be opened to repair the syntax in Papilot.
        try:
            preview = build_preview_document(source, str(project_dir), defaults)
            preamble, _, content = preview.partition(r'\begin{document}')
            body = content.removesuffix('\n\\end{document}\n').removeprefix('\n\\pagestyle{empty}\n')
        except ValueError:
            preview = build_preview_document(r'\relax', str(project_dir), defaults)
            preamble, _, _ = preview.partition(r'\begin{document}')
            body = source
        preamble = assets.rewrite(_normalized_tex(preamble), project_dir)
        engine_added = not _ENGINE.search(preamble)
        main = ((_ENGINE_LINE if engine_added else '') + preamble + '\\begin{document}\n'
                '\\pagestyle{empty}\n\\input{question.tex}\n\\end{document}\n')
        assets.files[_QUESTION] = assets.rewrite(_normalized_tex(body), primary).rstrip('\n') + '\n'
    assets.files[_ENTRY] = main

    name = _filename(title)
    if name.casefold() in (_MANIFEST, _ORIGINAL, 'imports'):
        name += ' 题目'
    baseline = _digest(source)
    record = {'version': 1, 'folder': name, 'baseline': baseline, 'standalone': standalone,
              'engineAdded': engine_added, 'files': {key: _digest(value) for key, value in assets.files.items()}}
    with TemporaryDirectory(prefix='.papilot-question-', dir=root.parent) as temporary:
        staged = Path(temporary) / 'project'
        staged.mkdir()
        _write_files(staged / name, assets.files)
        _write_files(staged, {_MANIFEST: json.dumps(record, ensure_ascii=False), _ORIGINAL: source})
        try:
            staged.rename(root)
        except OSError:
            if not root.exists():
                raise
            folder, record = _project(root)
            _safe_file(folder, _ENTRY)
            return {'folder': str(folder), 'baseline': record['baseline']}
    return {'folder': str(root / name), 'baseline': baseline}


def _same_file(folder: Path, relative: str, checksum: str) -> bool:
    candidate = folder / relative
    if not candidate.is_file() or candidate.is_symlink() or not _owned(candidate.resolve(), folder.resolve()):
        return False
    payload = candidate.read_bytes()
    if candidate.suffix.lower() in _TEXT_SUFFIXES:
        payload = payload.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    return _digest(payload) == checksum


def _absolute_references(source: str, assets: _Assets, destination: Path) -> str:
    for ref in _references(source):
        candidate = ref.name if ref.name in assets.files else ref.name + _SOURCE_SUFFIX.get(ref.command, '')
        if candidate in assets.files:
            target = ref.path_spelling((destination / candidate).as_posix())
            source = source[:ref.start] + target + source[ref.end:]
    return source


def read_question_project(root: Path) -> str:
    """Return reviewed source and freeze its referenced files outside the editable paper."""
    folder, record = _project(root)
    original = _safe_file(root, _ORIGINAL).read_bytes().decode('utf-8')
    if _digest(original) != record.get('baseline'):
        raise ValueError('Papilot 项目导入基线记录已损坏。')
    files = record.get('files')
    if not isinstance(files, dict) or _ENTRY not in files:
        raise ValueError('Papilot 项目文件记录无效。')
    if all(_same_file(folder, name, checksum) for name, checksum in files.items()):
        return original

    # Only edits to the wrapper require importing a full document. Normal PDF
    # edits to the stem, figures or solution retain the question-fragment shape.
    standalone = record.get('standalone') or not _same_file(folder, _ENTRY, files[_ENTRY])
    current = read_tex(_safe_file(folder, _ENTRY if standalone else _QUESTION))
    if not current.strip():
        raise ValueError('Papilot 题目源码没有可同步的内容。')
    if standalone and record.get('engineAdded'):
        current = current.removeprefix(_ENGINE_LINE)
    assets = _Assets((folder,))
    source = assets.rewrite(current, folder)
    if not assets.files:
        return source
    fingerprint = _digest(json.dumps([source, sorted((name, _digest(value)) for name, value in assets.files.items())], ensure_ascii=False))
    imports = root / 'imports'
    imports.mkdir(exist_ok=True)
    if imports.resolve() != root.resolve() / 'imports':
        raise ValueError('Papilot 资源同步目录无效。')
    destination = imports / fingerprint[:32]
    if not destination.exists():
        snapshot = {name: _absolute_references(value, assets, destination) if isinstance(value, str) else value
                    for name, value in assets.files.items()}
        with TemporaryDirectory(prefix='.papilot-assets-', dir=imports) as temporary:
            staged = Path(temporary) / 'snapshot'
            _write_files(staged, snapshot)
            try:
                staged.rename(destination)
            except OSError:
                if not destination.exists():
                    raise
    if destination.resolve() != imports.resolve() / destination.name:
        raise ValueError('Papilot 资源副本目录无效。')
    return _absolute_references(source, assets, destination)
