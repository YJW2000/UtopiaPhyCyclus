"""Maintain the searchable index from the selected library at open and close."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from services.tex_import_service import decode_tex
from utils import question_index as index
from utils.index_rebuild import row_from_tex
from utils.index_schema import CSV_HEADERS


def _raise_scan_error(error):
    raise error


def _question_paths(chapters):
    for folder, directories, files in os.walk(chapters, onerror=_raise_scan_error):
        directory = Path(folder)
        directories[:] = sorted(
            name for name in directories
            if '相关图' not in name and not (directory / name).is_symlink()
            and (directory / name).resolve() == directory / name
        )
        for name in sorted(files):
            if name.lower().endswith('.tex') and not name.lower().startswith('content_'):
                yield directory / name


def maintain_library(root: Path):
    """Refresh changed index rows without editing or removing question files."""
    root = Path(root).resolve()
    chapters = (root / 'chapters').resolve(strict=True)
    csv_path = root / 'utils' / '题库索引表.csv'
    if not chapters.is_dir():
        raise ValueError('当前题库缺少 chapters 目录。')

    with index.connection(csv_path, bootstrap_from_tex=False) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        integrity = conn.execute('PRAGMA quick_check').fetchone()[0]
        if integrity != 'ok':
            raise RuntimeError(f'题库索引完整性检查失败：{integrity}')

        conn.execute('''CREATE TABLE IF NOT EXISTS question_source_state (
            path_key TEXT PRIMARY KEY REFERENCES questions(path_key) ON UPDATE CASCADE ON DELETE CASCADE,
            modified_ns INTEGER NOT NULL, size INTEGER NOT NULL)''')
        indexed = {row[0]: (row[1], row[2]) for row in conn.execute('''
            SELECT q.path_key, s.modified_ns, s.size
            FROM questions q LEFT JOIN question_source_state s USING(path_key)''')}
        seen = set()
        new_rows = []
        added = updated = skipped = 0

        for path in _question_paths(chapters):
            relative = str(path.relative_to(chapters))
            key = index._key(relative)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    skipped += 1
                    continue
                if (info.st_mtime_ns, info.st_size) == indexed.get(key):
                    continue
                source_stamp = (key, info.st_mtime_ns, info.st_size)
                content = decode_tex(path.read_bytes())
                current = path.lstat()
                if (current.st_ino, current.st_mtime_ns, current.st_size) != (
                    info.st_ino, info.st_mtime_ns, info.st_size
                ):
                    skipped += 1
                    continue
            except (OSError, ValueError):
                skipped += 1
                continue

            old = conn.execute('SELECT * FROM questions WHERE path_key=?', (key,)).fetchone()
            row = row_from_tex(content, relative, info, dict(old) if old else {})
            if not row['题干']:
                # Do not index auxiliary TeX files or overwrite a working row with an incomplete edit.
                skipped += 1
                continue
            if not old:
                row['题目ID'] = row['题目ID'].strip()
                new_rows.append((row, source_stamp))
                continue

            row.update((field, old[field]) for field in CSV_HEADERS[:8])
            if any(row[field] != old[field] for field in row if field != '最后修改时间'):
                index._put(conn, row, activity=False)
                updated += 1
            _record_stamp(conn, source_stamp)

        # Honor IDs embedded in new source files before allocating IDs for files without one.
        for row, source_stamp in sorted(new_rows, key=lambda entry: not entry[0]['题目ID']):
            ident = row['题目ID']
            if ident and conn.execute('SELECT 1 FROM questions WHERE "题目ID"=?', (ident,)).fetchone():
                skipped += 1
                continue
            if not ident:
                row['题目ID'] = str(conn.execute(
                    'SELECT COALESCE(MAX(CAST("题目ID" AS INTEGER)),0)+1 FROM questions').fetchone()[0])
            index._put(conn, row, activity=False)
            _record_stamp(conn, source_stamp)
            added += 1

        if added or updated:
            index._bump(conn)
        conn.execute('PRAGMA optimize')
        return {'integrity': integrity, 'added': added, 'updated': updated,
                'missing': len(indexed.keys() - seen), 'skipped': skipped}


def _record_stamp(conn, stamp):
    conn.execute('''INSERT INTO question_source_state(path_key, modified_ns, size) VALUES(?, ?, ?)
        ON CONFLICT(path_key) DO UPDATE SET modified_ns=excluded.modified_ns, size=excluded.size''', stamp)
