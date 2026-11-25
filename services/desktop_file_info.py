"""Read and edit the file information of one question in the active bank."""
from __future__ import annotations

import hashlib
from codecs import BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF8
import os
from pathlib import Path, PureWindowsPath
import re
from tempfile import mkstemp

from services.file_service import atomic_write_bytes, backup_existing_file
from services.tex_import_service import decode_tex, repair_problem_headers, replace_parsed_problem_header
from utils import question_index as index
from utils.core_config import PAPER_TYPES
from utils.image_assets import _uncomment, graphics_references, resolve_image
from utils.index_rebuild import row_from_tex
from utils.latex_ops import generate_filename, parse_meta_data


_FIELDS = ('year', 'paperType', 'paper', 'number', 'subject')
_COLUMNS = ('年份', '试卷类型', '试卷名称', '原卷题号', '知识板块')
_INVALID_COMPONENT = re.compile(r'[<>:"/\\|?*{}%#$^~&\[\]\x00-\x1f]')
_META = re.compile(
    r'(?m)^%(?: === Meta Data ===| === Begin Label Data ===)\n'
    r'(?P<body>[\s\S]*?)^%(?: === End Meta ===| === End\s+Label Data ===)(?=\n|$)'
)
_INPUT = re.compile(r'\\(?:input|include)\s*\{(?P<path>[^{}\\%#\x00-\x1f]+)\}')


def _question_path(library, relative):
    library = Path(library).resolve(strict=True)
    chapters = library / 'chapters'
    if not isinstance(relative, str) or not relative or '\x00' in relative or PureWindowsPath(relative).anchor:
        raise ValueError('题目路径必须位于当前题库。')
    parts = relative.replace('\\', '/').split('/')
    if any(part in ('', '.', '..') or ':' in part for part in parts):
        raise ValueError('题目路径必须位于当前题库。')
    path = chapters.joinpath(*parts)
    if path.suffix.lower() != '.tex' or not path.is_file() or path.resolve() != path:
        raise ValueError('题目文件不存在或路径不在当前题库内。')
    return library, chapters, path


def _file_info(relative, payload, source, row):
    parts = Path(relative).stem.split('-')
    fallback = [*parts[:2], '-'.join(parts[2:-2]), *parts[-2:]] if len(parts) >= 5 else [''] * 5
    fields = {field: row.get(column) or value for field, column, value in zip(_FIELDS, _COLUMNS, fallback)}
    meta, _ = parse_meta_data(source)
    return dict(path=relative, **fields, tags=meta.get('标签', row.get('标签', '')),
                id=row.get('题目ID') or meta.get('ID', ''), sourceHash=hashlib.sha256(payload).hexdigest())


def _get_row(conn, relative):
    found = conn.execute('SELECT * FROM questions WHERE path_key=?', (index._key(relative),)).fetchone()
    return dict(found) if found else {}


def _index_path(library):
    return library / 'utils' / '题库索引表.csv'


def read_file_info(library: Path, path: str) -> dict:
    library, chapters, file = _question_path(library, path)
    relative = file.relative_to(chapters).as_posix()
    payload = file.read_bytes()
    csv_path = _index_path(library)
    row = {}
    if Path(index.database_path(csv_path)).is_file() or csv_path.is_file():
        with index.connection(csv_path, bootstrap_from_tex=False) as conn:
            row = _get_row(conn, relative)
    return _file_info(relative, payload, decode_tex(payload), row)


def _updated_fields(fields, previous):
    if not isinstance(fields, dict) or fields.keys() - {*_FIELDS, 'tags'}:
        raise ValueError('文件信息字段无效。')
    updated = {field: previous[field] for field in (*_FIELDS, 'tags')}
    for field, incoming in fields.items():
        if not isinstance(incoming, str) or len(incoming) > (1000 if field == 'tags' else 120):
            raise ValueError('文件信息太长或格式无效。')
        if incoming == previous[field]:
            continue
        value = incoming.strip()
        if field == 'tags':
            if any(ord(char) < 32 for char in value):
                raise ValueError('标签不能包含控制字符。')
            updated[field] = '，'.join(dict.fromkeys(t.strip() for t in re.split(r'[,，]', value) if t.strip()))
            continue
        if not value or _INVALID_COMPONENT.search(value) or value.endswith('.') or PureWindowsPath(value).is_reserved():
            raise ValueError('文件信息含有不能用于题目文件名或 LaTeX 题头的字符。')
        if field == 'year' and not re.fullmatch(r'[0-9]{4}', value):
            raise ValueError('年份必须是四位数字。')
        if field == 'paperType':
            value = value.upper()
            if value not in PAPER_TYPES:
                raise ValueError('请选择有效的试卷类型。')
        if field == 'subject':
            values = [item.strip() for item in re.split(r'[,，]', value) if item.strip()]
            if not values or any(item.endswith('.') or PureWindowsPath(item).is_reserved() for item in values):
                raise ValueError('请选择有效的知识板块。')
            value = '，'.join(dict.fromkeys(values))
        updated[field] = value.replace('-', '－')
    return updated


def _with_tags(source, tags):
    tag_line = f'% 标签: {tags}'
    block = _META.search(source)
    if block is None:
        return f'% === Begin Label Data ===\n{tag_line}\n% === End  Label Data ===\n\n' + source
    body = block['body']
    tag = re.search(r'(?m)^%\s*标签\s*:[^\n]*', body)
    if tag:
        body = body[:tag.start()] + tag_line + body[tag.end():]
    else:
        body += tag_line + '\n'
    return source[:block.start('body')] + body + source[block.end('body'):]


def _resolve_input(name, library, parent):
    for base in (parent, library):
        candidate = base / name
        for file in (candidate,) if candidate.suffix else (candidate, candidate.with_suffix('.tex')):
            if file.is_file():
                return file.resolve()
    return None


def _rebase_assets(source, library, old_parent, new_parent):
    edits = []
    for ref in graphics_references(source):
        try:
            original = resolve_image(ref['path'], library, old_parent, source)
        except (OSError, ValueError):
            continue
        try:
            current = resolve_image(ref['path'], library, new_parent, source)
        except (OSError, ValueError):
            current = None
        if original != current:
            replacement = original.relative_to(library).as_posix() if original.is_relative_to(library) else original.as_posix()
            if resolve_image(replacement, library, new_parent, source) != original:
                replacement = original.as_posix()
            edits.append((ref['path_start'], ref['path_end'], replacement))
    for ref in _INPUT.finditer(_uncomment(source)):
        name = ref['path'].strip().strip('"')
        original = _resolve_input(name, library, old_parent)
        if original is not None and original != _resolve_input(name, library, new_parent):
            replacement = original.relative_to(library).as_posix() if original.is_relative_to(library) else original.as_posix()
            if _resolve_input(replacement, library, new_parent) != original:
                replacement = original.as_posix()
            edits.append((*ref.span('path'), replacement))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def _encode_source(source, original):
    bom, encoding = b'', 'utf-8'
    for signature, character_set in ((BOM_UTF8, 'utf-8'), (BOM_UTF16_LE, 'utf-16-le'), (BOM_UTF16_BE, 'utf-16-be')):
        if original.startswith(signature):
            bom, encoding = signature, character_set
            break
    if not bom:
        try:
            original.decode(encoding)
        except UnicodeDecodeError:
            encoding = 'gb18030'
    content = original[len(bom):].decode(encoding)
    ending = re.search(r'\r\n|\n|\r', content)
    if ending and ending[0] != '\n':
        source = source.replace('\n', ending[0])
    return bom + source.encode(encoding)


def _write_new_file(path, payload):
    descriptor, temporary = mkstemp(prefix='.metadata-', suffix='.write', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Unlike replace, publishing a new name must never clobber a file.
        if os.name == 'nt':
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_file_info(library: Path, path: str, fields: dict, expected_hash: str) -> dict:
    library, chapters, old_path = _question_path(library, path)
    relative = old_path.relative_to(chapters).as_posix()
    with index.connection(_index_path(library), bootstrap_from_tex=False) as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous_bytes = old_path.read_bytes()
        source = decode_tex(previous_bytes)
        indexed = _get_row(conn, relative)
        previous = _file_info(relative, previous_bytes, source, indexed)
        if not isinstance(expected_hash, str) or expected_hash != previous['sourceHash']:
            raise ValueError('原题文件已有新版本，请重新打开文件信息后重试。')
        updated_fields = _updated_fields(fields, previous)
        header_changed = any(updated_fields[field] != previous[field] for field in _FIELDS)
        if not header_changed and updated_fields['tags'] == previous['tags']:
            return previous

        new_path = old_path
        if header_changed:
            values = tuple(updated_fields[field] for field in _FIELDS)
            chapter = updated_fields['subject'].split('，', 1)[0]
            new_path = chapters / chapter / updated_fields['year'] / generate_filename(*values)
            if not new_path.is_relative_to(chapters) or new_path.resolve() != new_path:
                raise ValueError('目标目录不在当前题库内。')
            source = replace_parsed_problem_header(repair_problem_headers(source), values)
        if updated_fields['tags'] != previous['tags']:
            source = _with_tags(source, updated_fields['tags'])
        moved = new_path != old_path
        new_relative = new_path.relative_to(chapters).as_posix()
        if moved:
            existing = _get_row(conn, new_relative) if index._key(new_relative) != index._key(relative) else {}
            if new_path.exists() or existing:
                raise FileExistsError('目标题目文件或索引已存在，请调整文件信息。')
            source = _rebase_assets(source, library, old_path.parent, new_path.parent)
        payload = _encode_source(source, previous_bytes)

        def recheck_source():
            if old_path.read_bytes() != previous_bytes:
                raise ValueError('原题文件已有新版本，请重新打开文件信息后重试。')

        new_path.parent.mkdir(parents=True, exist_ok=True)
        if new_path.resolve() != new_path:
            raise ValueError('目标目录不在当前题库内。')
        backup_existing_file(str(old_path), project_dir=library)
        recheck_source()
        written = removed = False
        try:
            if moved:
                _write_new_file(new_path, payload)
            else:
                atomic_write_bytes(str(new_path), payload, before_replace=recheck_source)
            written = True
            row = row_from_tex(source, new_relative, new_path.stat(), indexed)
            row.update(zip(_COLUMNS, (updated_fields[field] for field in _FIELDS)))
            row['标签'] = updated_fields['tags']
            row['题目ID'] = previous['id'] or str(conn.execute(
                'SELECT COALESCE(MAX(CAST("题目ID" AS INTEGER)),0)+1 FROM questions').fetchone()[0])
            index._put(conn, row, old_path=relative)
            index._bump(conn)
            if moved:
                recheck_source()
                old_path.unlink()
                removed = True
            conn.commit()
        except Exception:
            conn.rollback()
            if removed:
                _write_new_file(old_path, previous_bytes)
            if written and new_path.is_file() and new_path.read_bytes() == payload:
                if moved:
                    new_path.unlink()
                else:
                    atomic_write_bytes(str(old_path), previous_bytes)
            raise
        return _file_info(new_relative, payload, source, row)
