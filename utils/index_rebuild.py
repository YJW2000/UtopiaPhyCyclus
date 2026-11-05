"""Explicit index rebuild from .tex files; never changes the source files."""
import datetime
import os
from collections import Counter

from utils.index_schema import CSV_HEADERS


def collect_rows(chapters_dir, previous_rows):
    from utils.csv_ops import _parse_tex_content
    from utils.question_index import _key
    by_path = {_key(row['相对文件路径']): row for row in previous_rows}
    names = Counter(row['文件名称'] for row in previous_rows)
    by_name = {row['文件名称']: row for row in previous_rows if names[row['文件名称']] == 1}
    rows = []
    for root, dirs, files in os.walk(chapters_dir):
        dirs[:] = sorted(d for d in dirs if '相关图' not in d)
        for filename in sorted(files):
            if not filename.endswith('.tex') or filename.startswith('content_'):
                continue
            path = os.path.join(root, filename)
            rel_path = os.path.relpath(path, chapters_dir)
            name = filename[:-4]
            parts = name.split('-', 4)
            parts += [''] * (5 - len(parts))
            year, paper_type, paper_name, number, subject = parts
            with open(path, encoding='utf-8') as handle:
                content = handle.read()
            tikz, kind, has_solution, stem, answer, solution, meta = _parse_tex_content(content, paper_name, subject)
            old = by_path.get(_key(rel_path), by_name.get(name, {}))
            stat = os.stat(path)
            stamp = lambda value: datetime.datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')
            row = {field: old.get(field, '') for field in CSV_HEADERS}
            row.update({
                '题目ID': meta.get('ID') or old.get('题目ID', ''),
                '文件名称': name, '相对文件路径': rel_path, '年份': year, '试卷类型': paper_type,
                '试卷名称': paper_name, '原卷题号': number, '知识板块': subject,
                '包含TikZ绘图': tikz, '题型': kind, '包含解析': has_solution,
                '初次录入的时间': old.get('初次录入的时间') or stamp(stat.st_ctime),
                '最后修改时间': stamp(stat.st_mtime), '题干': stem, '答案': answer, '解析': solution,
            })
            for field in ('标签', '知识点', '物理方法', '难度星级', '备注', '组卷引用次数',
                          '来源题目ID', '挖空类型', '生成时间'):
                row[field] = meta.get(field, old.get(field, ''))
            rows.append(row)
    reserved = [int(row.get('题目ID') or 0) for row in [*previous_rows, *rows]
                if str(row.get('题目ID') or '').isdigit()]
    next_id = max(reserved, default=0)
    for row in sorted(rows, key=lambda r: (r['初次录入的时间'], r['相对文件路径'])):
        if not row['题目ID']:
            next_id += 1
            row['题目ID'] = str(next_id)
    rows.sort(key=lambda r: int(r['题目ID']) if str(r['题目ID']).isdigit() else 999999999)
    return rows


def rebuild_index(chapters_dir=None, csv_path=None, *, export=False):
    from utils.core_config import CHAPTERS_DIR, CSV_INDEX_PATH
    from utils.question_index import read_rows, replace_rows, export_csv
    chapters_dir, csv_path = chapters_dir or CHAPTERS_DIR, csv_path or CSV_INDEX_PATH
    rows = collect_rows(chapters_dir, read_rows(csv_path=csv_path))
    replace_rows(rows, csv_path=csv_path)
    if export:
        export_csv(csv_path)
    return len(rows)
