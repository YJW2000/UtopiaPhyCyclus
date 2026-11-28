"""Remove indexed questions and preserve backups in the library's usual layout."""
from __future__ import annotations

from datetime import datetime
import logging
from pathlib import PureWindowsPath

from utils import question_index
from utils.batch_gen import update_chapter_contents


def _question_path(chapters, relative):
    if not isinstance(relative, str) or not relative or '\x00' in relative or PureWindowsPath(relative).anchor:
        raise ValueError('题目路径必须位于当前题库。')
    parts = relative.replace('\\', '/').split('/')
    if any(part in ('', '.', '..') or ':' in part or PureWindowsPath(part).is_reserved() for part in parts):
        raise ValueError('题目路径必须位于当前题库。')
    path = chapters.joinpath(*parts)
    if (path.suffix.lower() != '.tex' or path.name.lower().startswith('content_')
            or any('相关图' in part for part in parts[:-1]) or path.resolve() != path
            or (path.exists() and not path.is_file())):
        raise ValueError('只能删除当前题库中的普通题目文件。')
    return path


def _backup_moves(library, path, timestamp):
    if not path.is_file():
        # An orphaned folder can still be used by a question that has moved.
        return []
    folder = library / '.backups' / path.parent.relative_to(library)
    figure_dir = path.with_name(path.stem + ' 相关图')
    moves = [(path, folder / f'{path.stem}.{timestamp}{path.suffix}')]
    if figure_dir.resolve() != figure_dir:
        raise ValueError(f'相关图目录指向其他位置，无法删除：{figure_dir.name}')
    if figure_dir.is_dir():
        moves.append((figure_dir, folder / f'{figure_dir.name}.{timestamp}'))
    for _, backup in moves:
        if backup.resolve() != backup or backup.exists():
            raise ValueError('题库备份路径无效，请检查 .backups 目录。')
    return moves


def delete_questions(library, csv_path, paths):
    from services.operation_log import record_operation

    if not isinstance(paths, list) or not paths:
        raise ValueError('请选择要删除的题目。')
    chapters = library / 'chapters'
    selected = {}
    for relative in paths:
        path = _question_path(chapters, relative)
        selected.setdefault(question_index._key(path.relative_to(chapters)), (relative, path))

    recorded, moves, moved = [], [], []
    with question_index.connection(csv_path, bootstrap_from_tex=False) as db:
        db.execute('BEGIN IMMEDIATE')
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        for key, (_, candidate) in selected.items():
            row = db.execute('SELECT "题目ID", "相对文件路径" FROM questions WHERE path_key=?', (key,)).fetchone()
            if row is None:
                if candidate.is_file():
                    raise ValueError(f'题目已不在索引中，请刷新题库：{candidate.name}')
                # A retry can safely confirm a previous deletion.
                continue
            ident, relative = row
            path = _question_path(chapters, relative)
            backups = _backup_moves(library, path, timestamp)
            moves.extend(backups)
            recorded.append((ident, relative, path, backups[0][1] if backups else ''))

        try:
            for source, backup in moves:
                backup.parent.mkdir(parents=True, exist_ok=True)
                source.rename(backup)
                moved.append((source, backup))
            removed = question_index._delete_rows(db, (relative for _, relative, _, _ in recorded))
            if removed != len(recorded):
                raise RuntimeError('题库索引未能删除全部所选题目。')
            db.commit()
        except BaseException as error:
            failed = []
            for source, backup in reversed(moved):
                try:
                    if source.exists() or source.is_symlink() or source.resolve() != source:
                        raise FileExistsError(source)
                    backup.rename(source)
                except OSError:
                    failed.append(str(backup))
            db.rollback()
            if failed:
                raise RuntimeError('删除失败，部分文件请从备份恢复：' + '、'.join(failed)) from error
            raise

    topics = {path.relative_to(chapters).parts[0] for _, _, path, _ in recorded if path.parent != chapters}
    warnings, existing_topics = [], []
    for topic in sorted(topics):
        chapter_index = chapters / topic / f'content_{topic}.tex'
        try:
            if chapter_index.is_file():
                if chapter_index.resolve() != chapter_index:
                    raise ValueError('章节索引指向其他位置。')
                existing_topics.append(topic)
        except (OSError, RuntimeError, ValueError) as error:
            warnings.append(f'章节索引更新失败（{topic}）：{error}')
    if existing_topics:
        try:
            warnings.extend(update_chapter_contents(library, topics=existing_topics, report=logging.getLogger(__name__).debug))
        except (OSError, RuntimeError, ValueError) as error:
            warnings.append(f'章节索引更新失败：{error}')
    for ident, _, path, backup in recorded:
        record_operation('delete_question', path=str(path), related_path=str(backup), question_id=ident,
                         details='removed_index_rows=1')
    return {'deletedPaths': [relative for relative, _ in selected.values()], 'warnings': warnings}
