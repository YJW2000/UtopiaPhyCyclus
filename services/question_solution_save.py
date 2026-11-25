"""Save a reviewed answer supplement to its original bank question and index."""

from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE
from pathlib import Path

from services.file_service import atomic_write_bytes, backup_existing_file
from services.question_solution_service import _is_empty, _question_regions
from services.tex_import_service import _mask_comments
from utils import question_index


class SolutionSaveConflict(ValueError):
    """The original question changed after the reviewed draft was generated."""


def _decode_source(data):
    if data.startswith((BOM_UTF32_LE, BOM_UTF32_BE)):
        raise ValueError('TeX 文件使用了 UTF-32，请转换为 UTF-8 后重试。')
    for marker, encoding in ((BOM_UTF8, 'utf-8'), (BOM_UTF16_LE, 'utf-16-le'), (BOM_UTF16_BE, 'utf-16-be')):
        if data.startswith(marker):
            source = data[len(marker):].decode(encoding)
            if '\x00' in source:
                raise ValueError('原题文件包含二进制内容。')
            return source, encoding, marker
    if b'\x00' in data:
        raise ValueError('原题文件包含二进制内容。')
    try:
        return data.decode('utf-8'), 'utf-8', b''
    except UnicodeDecodeError:
        return data.decode('gb18030'), 'gb18030', b''


def solution_source_text(data: bytes) -> str:
    """Decode a bank question without translating line endings or including its encoding marker."""
    return _decode_source(data)[0]


def _managed_path(path, chapters):
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = chapters / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise SolutionSaveConflict('原题已移动或删除。') from error
    if (not resolved.is_relative_to(chapters) or not resolved.is_file() or resolved.suffix != '.tex'
            or resolved.name.startswith('content_') or ' 图' in resolved.name
            or any('相关图' in part for part in resolved.relative_to(chapters).parts)):
        raise ValueError('请选择题库目录中的原题文件。')
    return resolved


def _content_parts(source):
    masked = _mask_comments(source)
    _, _, problems, blocks = _question_regions(masked)
    if len(problems) > 1:
        raise ValueError('单个题库文件只能保存一道题的补充解答。')
    answers, remainder, offset = {'answer': [], 'solutions': []}, [], 0
    for block in blocks:
        remainder.append(source[offset:block.start])
        offset = block.end
        if not _is_empty(masked[block.body_start:block.body_end]):
            key = 'answer' if block.name == 'answer' else 'solutions'
            answers[key].append(source[block.body_start:block.body_end].strip())
    remainder.append(source[offset:])
    # Newly inserted environments may add line breaks between otherwise identical source chunks.
    surrounding = ''.join(remainder).replace('\r', '').replace('\n', '')
    return surrounding, answers


def _require_original(path, expected):
    try:
        current = path.read_bytes()
    except FileNotFoundError as error:
        raise SolutionSaveConflict('原题已移动或删除。') from error
    if current != expected:
        raise SolutionSaveConflict('原题已修改，请重新生成并审阅。')


def save_reviewed_solution(path, base: bytes, updated: str, *, chapters_dir) -> None:
    """Write only the approved supplement if the original bytes still match the review."""
    chapters = Path(chapters_dir).resolve()
    path = _managed_path(path, chapters)
    _require_original(path, base)
    original, encoding, marker = _decode_source(base)
    if marker:
        updated = updated.removeprefix('\ufeff')
    payload = marker + updated.encode(encoding)
    if payload == base:
        return
    old_source, old_fields = _content_parts(original)
    new_source, new_fields = _content_parts(updated)
    if (old_source != new_source or any(old_fields[key] and old_fields[key] != new_fields[key] for key in old_fields)
            or not any(new_fields[key] and not old_fields[key] for key in old_fields)):
        raise ValueError('补充稿必须保留原题、标签和已有解答，并补上至少一项缺失内容。')

    index_directory = chapters.parent / 'utils'
    index = index_directory / '题库索引表.csv'
    indexed = {key: '\n\n'.join(_mask_comments(body).strip() for body in bodies)
               for key, bodies in new_fields.items()}
    backup_existing_file(str(path), project_dir=chapters.parent)
    atomic_write_bytes(str(path), payload, before_replace=lambda: _require_original(path, base))
    try:
        question_index.update_solution_fields(
            path.relative_to(chapters).as_posix(), indexed['answer'], indexed['solutions'], csv_path=index,
        )
    except Exception:
        try:
            atomic_write_bytes(str(path), base, before_replace=lambda: _require_original(path, payload))
        except SolutionSaveConflict:
            pass  # A concurrent writer has already replaced or moved the file.
        raise
