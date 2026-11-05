"""Rebuildable SQLite index. The original .tex files remain the source documents.

CSV is imported once, without rewriting it. Routine mutations are transactions
affecting one question, its FTS entries and its activity events. No connections
are shared between Streamlit sessions or kept open across reruns.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
import csv
import datetime as dt
from functools import lru_cache
import hashlib
import json
import math
import os
import re
import sqlite3
import uuid

from utils.index_schema import CSV_HEADERS
from services.fuzzy_search_service import (
    SearchDocument, build_documents, metadata_terms, _query_terms, _RELATED,
    _grams, search as score_documents,
)

_ready = set()
_COLUMNS = ', '.join('"' + field + '"' for field in CSV_HEADERS)
_LIGHT_FIELDS = CSV_HEADERS[:20] + CSV_HEADERS[23:]
_LIGHT_COLUMNS = ', '.join('"' + field + '"' for field in _LIGHT_FIELDS)
_RESULT_FIELDS = ['题目ID', '文件名称', '相对文件路径', '初次录入的时间', '难度星级']
_RESULT_COLUMNS = ', '.join('"' + field + '"' for field in _RESULT_FIELDS)
_DUPLICATE_COLUMNS = ', '.join('"' + field + '"' for field in ['题目ID', '文件名称', '相对文件路径', '题型', '知识板块'])
_TIME = 'COALESCE(NULLIF("初次录入的时间", \'\'), "最后修改时间")'


def database_path(csv_path=None):
    if csv_path is None:
        from utils.core_config import CSV_INDEX_PATH
        csv_path = CSV_INDEX_PATH
    return os.path.join(os.path.dirname(os.path.abspath(csv_path)), 'question_index.sqlite3')


def _key(path):
    return os.path.normpath(str(path).replace('\\', '/')).replace('\\', '/').casefold()


def _connect(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


@contextmanager
def connection(csv_path=None):
    path = ensure_index(csv_path)
    with closing(_connect(path)) as conn:
        yield conn


def _scope(scope=None):
    return ('"试卷类型" = ?', [scope]) if scope else ('"试卷类型" <> ?', ['WK'])


def _gram_token(value):
    return 'g' + 'x'.join(format(ord(c), 'x') for c in value)


def _tokens(values, singles=True):
    grams = set()
    for value in values:
        grams.update(_grams(value))
        if singles:
            grams.update(value)
    return ' '.join(sorted(_gram_token(value) for value in grams))


def _prepare(row):
    from services.question_service import normalize_question_text, _row_stem
    row = {field: str(row.get(field, '') or '') for field in CSV_HEADERS}
    for field in ('题目ID', '试卷类型', '相对文件路径'):
        row[field] = row[field].strip()
    if not all(row[field] for field in CSV_HEADERS[:3]):
        raise ValueError('题库索引缺少题目ID、文件名称或相对文件路径，原索引未改动。')
    document = build_documents([row])[0]
    stem = normalize_question_text(row['题干']) if row['题干'] else _row_stem(row)
    return row, document, stem


def _schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS question_events(
            id INTEGER PRIMARY KEY, event_date TEXT, event_hour TEXT,
            event_type TEXT, relative_path TEXT, created_at REAL);
        CREATE INDEX IF NOT EXISTS events_day ON question_events(event_date, event_type);
    ''')
    fields = ', '.join('"' + field + '" TEXT NOT NULL DEFAULT \'\'' for field in CSV_HEADERS)
    conn.execute(f'''CREATE TABLE IF NOT EXISTS questions(
        rowid INTEGER PRIMARY KEY, path_key TEXT NOT NULL UNIQUE, {fields},
        normalized_stem TEXT NOT NULL, stem_hash TEXT NOT NULL, stem_length INTEGER NOT NULL,
        search_fields TEXT NOT NULL, fuzzy_tokens TEXT NOT NULL, stem_tokens TEXT NOT NULL)''')
    conn.executescript(f'''
        CREATE UNIQUE INDEX IF NOT EXISTS question_id ON questions("题目ID");
        CREATE INDEX IF NOT EXISTS question_name ON questions("文件名称");
        CREATE INDEX IF NOT EXISTS question_time ON questions(({_TIME}), rowid);
        CREATE INDEX IF NOT EXISTS question_type_time ON questions("试卷类型", ({_TIME}), rowid);
        CREATE INDEX IF NOT EXISTS regular_time_asc ON questions(({_TIME}) ASC, rowid ASC)
            WHERE "试卷类型" <> 'WK';
        CREATE INDEX IF NOT EXISTS regular_time_desc ON questions(({_TIME}) DESC, rowid ASC)
            WHERE "试卷类型" <> 'WK';
        CREATE INDEX IF NOT EXISTS question_type_time_desc ON questions("试卷类型", ({_TIME}) DESC, rowid ASC);
        CREATE INDEX IF NOT EXISTS question_kind ON questions("题型", "试卷类型");
        CREATE INDEX IF NOT EXISTS question_stem ON questions(stem_hash);
        CREATE INDEX IF NOT EXISTS question_stem_length ON questions(stem_length);
        CREATE INDEX IF NOT EXISTS question_numeric_id ON questions(CAST("题目ID" AS INTEGER));
        CREATE TABLE IF NOT EXISTS vocabulary(
            question_rowid INTEGER REFERENCES questions(rowid) ON DELETE CASCADE,
            term TEXT NOT NULL, PRIMARY KEY(question_rowid, term));
        CREATE INDEX IF NOT EXISTS vocabulary_term ON vocabulary(term);
        CREATE VIRTUAL TABLE IF NOT EXISTS question_fts USING fts5(
            fuzzy_tokens, stem_tokens, content='questions', content_rowid='rowid', detail=column);
        CREATE TRIGGER IF NOT EXISTS questions_ai AFTER INSERT ON questions BEGIN
            INSERT INTO question_fts(rowid, fuzzy_tokens, stem_tokens)
            VALUES(new.rowid, new.fuzzy_tokens, new.stem_tokens);
        END;
        CREATE TRIGGER IF NOT EXISTS questions_ad AFTER DELETE ON questions BEGIN
            INSERT INTO question_fts(question_fts, rowid, fuzzy_tokens, stem_tokens)
            VALUES('delete', old.rowid, old.fuzzy_tokens, old.stem_tokens);
        END;
        CREATE TRIGGER IF NOT EXISTS questions_au AFTER UPDATE ON questions BEGIN
            INSERT INTO question_fts(question_fts, rowid, fuzzy_tokens, stem_tokens)
            VALUES('delete', old.rowid, old.fuzzy_tokens, old.stem_tokens);
            INSERT INTO question_fts(rowid, fuzzy_tokens, stem_tokens)
            VALUES(new.rowid, new.fuzzy_tokens, new.stem_tokens);
        END;
    ''')


def _event(conn, event_type, row):
    if row.get('试卷类型') == 'WK':
        return
    now = dt.datetime.now()
    conn.execute('''INSERT INTO question_events(event_date, event_hour, event_type, relative_path, created_at)
                    VALUES(?,?,?,?,?)''',
                 (now.date().isoformat(), f'{now.hour:02}', event_type, row['相对文件路径'], now.timestamp()))
    if row.get('包含TikZ绘图') == '是':
        conn.execute('''INSERT INTO question_events(event_date, event_hour, event_type, relative_path, created_at)
                        VALUES(?,?,?,?,?)''',
                     (now.date().isoformat(), f'{now.hour:02}', event_type.replace('question', 'tikz'),
                      row['相对文件路径'], now.timestamp()))


def _bump(conn):
    conn.execute("UPDATE index_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def _put(conn, row, *, old_path=None, activity=True):
    row, document, stem = _prepare(row)
    path_key = _key(row['相对文件路径'])
    old_key = _key(old_path) if old_path else path_key
    old = conn.execute(f'SELECT rowid, {_COLUMNS} FROM questions WHERE path_key=?', (old_key,)).fetchone()
    if old and all(old[field] == row[field] for field in CSV_HEADERS):
        return row['题目ID']
    values = [row[field] for field in CSV_HEADERS] + [
        stem, hashlib.sha256(stem.encode('utf-8')).hexdigest() if stem else '', len(stem),
        json.dumps(document.fields, ensure_ascii=False, separators=(',', ':')),
        _tokens(value for _, value, _ in document.fields), _tokens([stem], singles=False),
    ]
    columns = _COLUMNS + ', normalized_stem, stem_hash, stem_length, search_fields, fuzzy_tokens, stem_tokens'
    if old:
        assignments = ', '.join(column.strip() + '=?' for column in columns.split(','))
        conn.execute(f'UPDATE questions SET path_key=?, {assignments} WHERE rowid=?', [path_key, *values, old['rowid']])
        rowid = old['rowid']
        conn.execute('DELETE FROM vocabulary WHERE question_rowid=?', (rowid,))
    else:
        placeholders = ','.join('?' for _ in values)
        rowid = conn.execute(f'INSERT INTO questions(path_key,{columns}) VALUES(?,{placeholders})', [path_key, *values]).lastrowid
    conn.executemany('INSERT INTO vocabulary(question_rowid,term) VALUES(?,?)',
                     ((rowid, term) for term in metadata_terms(row)))
    if activity:
        # Usage counters alone do not count as a content edit.
        ignored = {'初次录入的时间', '最后修改时间', '组卷引用次数'}
        if not old or any(old[f] != row[f] for f in CSV_HEADERS if f not in ignored):
            _event(conn, 'modified_question' if old else 'new_question', row)
    return row['题目ID']


def ensure_index(csv_path=None):
    if csv_path is None:
        from utils.core_config import CSV_INDEX_PATH
        csv_path = CSV_INDEX_PATH
    path = database_path(csv_path)
    if os.path.exists(path) and (path, os.stat(path).st_ino) in _ready:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with closing(_connect(path)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        _schema(conn)
        conn.execute('BEGIN IMMEDIATE')
        try:
            if conn.execute("SELECT value FROM index_meta WHERE key='schema_version'").fetchone() is None:
                # All records, FTS data and the migration marker commit together.
                rows = []
                if os.path.isfile(csv_path) and os.path.getsize(csv_path):
                    with open(csv_path, encoding='utf-8-sig', newline='') as handle:
                        rows = list(csv.DictReader(handle))
                else:
                    from utils.index_rebuild import collect_rows
                    from utils.core_config import CHAPTERS_DIR
                    rows = collect_rows(CHAPTERS_DIR, [])
                keys = [_key(row.get('相对文件路径', '')) for row in rows]
                if len(set(keys)) != len(keys):
                    raise ValueError('旧索引包含重复文件路径，迁移已取消，CSV 和题目文件未改动。')
                for row in rows:
                    _put(conn, row, activity=False)
                legacy = os.path.join(os.path.dirname(path), 'local_stats.sqlite3')
                if os.path.isfile(legacy):
                    with closing(sqlite3.connect(legacy)) as previous:
                        exists = previous.execute("SELECT 1 FROM sqlite_master WHERE name='activity_events'").fetchone()
                        if exists:
                            conn.executemany('''INSERT INTO question_events(event_date,event_hour,event_type,relative_path,created_at)
                                                VALUES(?,?,?,?,?)''', previous.execute(
                                'SELECT event_date,event_hour,event_type,relative_path,created_at FROM activity_events'))
                conn.executemany('INSERT INTO index_meta(key,value) VALUES(?,?)',
                                 [('schema_version', '2'), ('generation', uuid.uuid4().hex), ('revision', '1')])
            else:
                # Early local indexes used detail=none. Column-scoped FTS
                # queries (both fuzzy search and the save duplicate guard)
                # require detail=column. CREATE IF NOT EXISTS cannot upgrade
                # an already-created virtual table, so rebuild it atomically.
                fts_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='question_fts'").fetchone()[0]
                if not re.search(r'detail\s*=\s*column\b', fts_sql, re.IGNORECASE):
                    conn.execute('DROP TABLE question_fts')
                    conn.execute('''CREATE VIRTUAL TABLE question_fts USING fts5(
                        fuzzy_tokens, stem_tokens, content='questions', content_rowid='rowid', detail=column)''')
                    conn.execute("INSERT INTO question_fts(question_fts) VALUES('rebuild')")
                    _bump(conn)
                conn.execute("UPDATE index_meta SET value='2' WHERE key='schema_version'")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    _ready.add((path, os.stat(path).st_ino))
    return path


def cache_token(csv_path=None):
    with connection(csv_path) as conn:
        meta = dict(conn.execute("SELECT key,value FROM index_meta WHERE key IN ('generation','revision')"))
    return database_path(csv_path), meta['generation'], int(meta['revision'])


def read_rows(*, csv_path=None, fields=None, scope='all'):
    columns = _COLUMNS if fields is None else ','.join('"' + f + '"' for f in fields if f in CSV_HEADERS)
    where, params = ('1', []) if scope == 'all' else _scope(scope)
    with connection(csv_path) as conn:
        return [dict(row) for row in conn.execute(f'SELECT {columns} FROM questions WHERE {where} ORDER BY rowid', params)]


def get_row(relative_path=None, *, question_id=None, csv_path=None):
    field, value = ('path_key', _key(relative_path)) if relative_path is not None else ('"题目ID"', str(question_id))
    with connection(csv_path) as conn:
        row = conn.execute(f'SELECT {_COLUMNS} FROM questions WHERE {field}=?', (value,)).fetchone()
        return dict(row) if row else None


def next_id(csv_path=None):
    with connection(csv_path) as conn:
        return conn.execute('SELECT COALESCE(MAX(CAST("题目ID" AS INTEGER)),0)+1 FROM questions').fetchone()[0]


def put_row(row, *, old_path=None, csv_path=None):
    with connection(csv_path) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if not str(row.get('题目ID', '') or '').strip():
            row = dict(row, 题目ID=str(conn.execute('SELECT COALESCE(MAX(CAST("题目ID" AS INTEGER)),0)+1 FROM questions').fetchone()[0]))
        result = _put(conn, row, old_path=old_path)
        _bump(conn)
        return result


def delete_row(relative_path, *, csv_path=None):
    with connection(csv_path) as conn, conn:
        result = conn.execute('DELETE FROM questions WHERE path_key=?', (_key(relative_path),))
        if result.rowcount:
            _bump(conn)
        return result.rowcount


def replace_rows(rows, *, csv_path=None):
    """Explicit bulk rebuild/import, including deletes, in a single transaction."""
    rows = list(rows)
    keys = [_key(row.get('相对文件路径', '')) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('题库索引包含重复文件路径，原索引未改动。')
    with connection(csv_path) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        # Stage keys rather than a huge NOT IN parameter list.
        conn.execute('CREATE TEMP TABLE kept_paths(path_key TEXT PRIMARY KEY)')
        conn.executemany('INSERT INTO kept_paths VALUES(?)', ((key,) for key in keys))
        conn.execute('DELETE FROM questions WHERE path_key NOT IN (SELECT path_key FROM kept_paths)')
        for row in rows:
            _put(conn, row)
        _bump(conn)


def browse_page(page=1, page_size=10, *, newest=True, scope=None, csv_path=None):
    page_size = max(1, min(int(page_size), 100))
    where, params = _scope(scope)
    with connection(csv_path) as conn:
        # Count and page share one consistent read snapshot.
        conn.execute('BEGIN')
        count = conn.execute(f'SELECT COUNT(*) FROM questions WHERE {where}', params).fetchone()[0]
        page = max(1, min(int(page), max(1, math.ceil(count / page_size))))
        direction = 'DESC' if newest else 'ASC'
        rows = [dict(row) for row in conn.execute(
            f'SELECT {_LIGHT_COLUMNS} FROM questions WHERE {where} ORDER BY {_TIME} {direction}, rowid ASC LIMIT ? OFFSET ?',
            [*params, page_size, (page - 1) * page_size])]
    return {'rows': rows, 'total': count, 'page': page, 'pages': max(1, math.ceil(count / page_size))}


_EXACT_FIELDS = {
    '题目类型': '题型', '题目内容': '题干', '解答内容': '解析', '难度星级': '难度星级',
    '标签': '标签', '其他自由标签': '标签', '知识点': '知识点', '物理方法': '物理方法', '备注': '备注',
}


def exact_rows(filters, *, scope=None, csv_path=None, light=False):
    where, params = _scope(scope)
    clauses = [where]
    for kind, query in filters:
        query = str(query or '').strip()
        if not query:
            continue
        if kind == '全文内容':
            columns = ('题干', '答案', '解析', '标签', '知识点', '物理方法', '备注')
            # Preserve the old newline-separated field boundary semantics.
            expression = " || char(10) || ".join('"' + f + '"' for f in columns)
            clauses.append(f'instr(({expression}), ?) > 0')
        elif kind in _EXACT_FIELDS:
            field = _EXACT_FIELDS[kind]
            clauses.append(f'"{field}" = ?' if kind == '题目类型' else f'instr("{field}", ?) > 0')
        else:
            clauses.append('0')
            continue
        params.append(query)
    columns = _LIGHT_COLUMNS if light else _COLUMNS
    with connection(csv_path) as conn:
        return [dict(row) for row in conn.execute(f'SELECT {columns} FROM questions WHERE ' + ' AND '.join(clauses) + ' ORDER BY rowid', params)]


@lru_cache(maxsize=4)
def _vocabulary(token, scope):
    where, params = _scope(scope)
    with closing(_connect(token[0])) as conn:
        return tuple(row[0] for row in conn.execute(
            f'''SELECT DISTINCT term FROM vocabulary WHERE question_rowid IN
                (SELECT rowid FROM questions WHERE {where})''', params))


def fuzzy_search(query, *, scope=None, csv_path=None):
    terms = _query_terms(query, vocabulary=_vocabulary(cache_token(csv_path), scope))
    if not terms:
        return []
    # A superset of every possible literal/related/approximate match. The same
    # scorer as before then determines relevance; no top-k truncation of recall.
    tokens = set()
    for term in terms:
        for word in (term, *_RELATED.get(term, ())):
            tokens.update(_grams(word) if len(word) > 1 else [word])
    expression = 'fuzzy_tokens : (' + ' OR '.join(_gram_token(t) for t in sorted(tokens)) + ')'
    where, params = _scope(scope)
    with connection(csv_path) as conn:
        documents = (SearchDocument({field: row[field] for field in _RESULT_FIELDS}, json.loads(row['search_fields']))
                     for row in conn.execute(f'''SELECT {_RESULT_COLUMNS},search_fields FROM questions
                         WHERE rowid IN (SELECT rowid FROM question_fts WHERE question_fts MATCH ?)
                         AND {where}''', [expression, *params]))
        return score_documents(documents, query, terms=terms)


def duplicate_candidates(normalized, threshold, *, exact_only=False, csv_path=None):
    where = 'stem_hash=?'
    params = [hashlib.sha256(normalized.encode('utf-8')).hexdigest()]
    if not exact_only:
        ratio = threshold / (2 - threshold)
        where = 'stem_length BETWEEN ? AND ?'
        params = [math.ceil(len(normalized) * ratio), math.floor(len(normalized) / ratio)]
        if len(normalized) >= 4 and threshold >= 0.8:
            expression = 'stem_tokens : (' + ' OR '.join(_gram_token(g) for g in sorted(_grams(normalized))) + ')'
            where += ' AND rowid IN (SELECT rowid FROM question_fts WHERE question_fts MATCH ?)'
            params.append(expression)
    with connection(csv_path) as conn:
        return [dict(row) for row in conn.execute(f'SELECT {_DUPLICATE_COLUMNS}, normalized_stem AS _normalized_stem FROM questions WHERE {where}', params)]


def statistics(*, csv_path=None):
    """Read small metadata columns and existing events; never resync all bodies."""
    stats = dict(total_questions=0, total_tikz=0, total_difficulty=0.0, difficulty_count=0,
                 subject_counts={}, type_counts={}, difficulty_dist={}, tag_counts={},
                 today_new_questions=0, today_mod_questions=0, today_new_tikz=0, today_mod_tikz=0,
                 daily_activity={}, hourly_activity_by_day={})
    with connection(csv_path) as conn:
        for row in conn.execute('''SELECT "包含TikZ绘图","知识板块","题型","难度星级","标签"
                                  FROM questions WHERE "试卷类型" <> 'WK' '''):
            stats['total_questions'] += 1
            stats['total_tikz'] += row[0] == '是'
            subject = row[1].split('，')[0] if row[1] else '未分类'
            stats['subject_counts'][subject] = stats['subject_counts'].get(subject, 0) + 1
            kind = row[2] or '未知'
            stats['type_counts'][kind] = stats['type_counts'].get(kind, 0) + 1
            if row[3] and row[3].replace('.', '', 1).isdigit():
                value = float(row[3])
                stats['total_difficulty'] += value
                stats['difficulty_count'] += 1
                label = '0-2星 (基础)' if value <= 2 else ('3-4星 (中档)' if value <= 4 else '5-6星 (压轴)')
                stats['difficulty_dist'][label] = stats['difficulty_dist'].get(label, 0) + 1
            for tag in row[4].split('，'):
                if tag.strip():
                    stats['tag_counts'][tag.strip()] = stats['tag_counts'].get(tag.strip(), 0) + 1
        today = dt.date.today().isoformat()
        counts = dict(conn.execute('SELECT event_type,COUNT(*) FROM question_events WHERE event_date=? GROUP BY event_type', (today,)))
        for key, event in (('today_new_questions', 'new_question'), ('today_mod_questions', 'modified_question'),
                           ('today_new_tikz', 'new_tikz'), ('today_mod_tikz', 'modified_tikz')):
            stats[key] = counts.get(event, 0)
        stats['daily_activity'] = dict(conn.execute('SELECT event_date,COUNT(*) FROM question_events GROUP BY event_date'))
        for day, hour, count in conn.execute('SELECT event_date,event_hour,COUNT(*) FROM question_events GROUP BY event_date,event_hour'):
            stats['hourly_activity_by_day'].setdefault(day, {f'{h:02}': 0 for h in range(24)})[hour] = count
    return stats


def export_csv(csv_path=None):
    from utils.core_config import CSV_INDEX_PATH
    from services.file_service import atomic_write_csv_rows
    csv_path = csv_path or CSV_INDEX_PATH
    atomic_write_csv_rows(csv_path, CSV_HEADERS, read_rows(csv_path=csv_path), backup=True)
