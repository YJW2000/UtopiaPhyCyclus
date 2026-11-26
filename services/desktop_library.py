"""Question bank operations for the desktop shell. Results carry paths relative to chapters."""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path, PureWindowsPath
import re
import shutil
from threading import Lock
import uuid

from services.desktop_model import read_settings, selected_provider
from services.desktop_tool_paths import fresh_tool_path
from services.file_service import atomic_write_text
from services.local_agent import LOCAL_AGENTS, agent_status, run_agent_json
from services.tex_import_service import _mask_comments, parse_tex_questions, read_tex
from utils import question_index
from utils.core_config import PAPER_TYPES, SUBJECTS
from utils.image_assets import copy_graphics
from utils.theme_palettes import SKINS, normalize_skin, theme_tokens


_FIELDS = ['题目ID', '相对文件路径', '年份', '试卷名称', '原卷题号', '知识板块', '题型', '标签', '题干', '最后修改时间']
_NAMES = ['id', 'path', 'year', 'paper', 'number', 'subject', 'type', 'tags', 'stem', 'modified']
_COLUMNS = ','.join('"' + field + '"' if field != '题干' else 'substr("题干",1,320)' for field in _FIELDS)
_FILTERS = {'subject': '知识板块', 'type': '题型', 'year': '年份', 'paperType': '试卷类型'}
_PAPER_FIELDS = ('年份', '试卷类型', '试卷名称')
_PAPER_COLUMNS = ', '.join(f'"{field}"' for field in _PAPER_FIELDS)
_QUESTION_NUMBER = 'TRIM("原卷题号")'
_PAPER_ORDER = f'''"年份" DESC, "试卷名称", "试卷类型",
    CASE WHEN {_QUESTION_NUMBER} <> '' AND {_QUESTION_NUMBER} NOT GLOB '*[^0-9]*'
         THEN CAST({_QUESTION_NUMBER} AS INTEGER) END ASC NULLS LAST, rowid ASC'''
_ENTRY_TIME = 'COALESCE(NULLIF("初次录入的时间", \'\'), "最后修改时间")'
# Multi-module metadata stores the primary module first, as in the chapter folders.
_MODULE = """COALESCE(NULLIF(TRIM(SUBSTR("知识板块", 1,
    INSTR(REPLACE("知识板块", ',', '，') || '，', '，') - 1)), ''), '未分类')"""
_SAMPLE_ROOT = Path(__file__).resolve().parents[1] / 'desktop' / 'sample'
_ANSWER_BLOCK = re.compile(r'\\begin\s*\{(answer|solutions?)\}.*?\\end\s*\{\1\}', re.S)


def exam_question_files(chapters, paths, included=()):
    if not isinstance(paths, list):
        raise ValueError('请选择有效的题目。')
    chapters = chapters.resolve()
    seen = {(chapters / name).resolve() for name in included}
    files = {}
    for name in paths:
        if not isinstance(name, str) or not name or '\x00' in name or PureWindowsPath(name).anchor:
            raise ValueError('题目路径必须位于当前题库。')
        path = (chapters / name.replace('\\', '/')).resolve()
        if not path.is_relative_to(chapters):
            raise ValueError('题目路径必须位于当前题库。')
        if path in seen:
            continue
        if path.suffix.lower() != '.tex' or not path.is_file():
            raise ValueError('题目已移动或文件路径无效。')
        seen.add(path)
        files[path.relative_to(chapters).as_posix()] = path
    return files


def exam_question_source(paths, library, destination, title, answers, *, first_number=1):
    bodies = []
    for index, path in enumerate(paths, first_number):
        source = read_tex(path)
        masked = _mask_comments(source)
        if not answers:
            for block in reversed(list(_ANSWER_BLOCK.finditer(masked))):
                source = source[:block.start()] + source[block.end():]
            masked = _mask_comments(source)
        source = copy_graphics(source, destination / 'images', library, path.parent)
        if r'\begin{document}' in masked or r'\begin{problem}' not in masked:
            defaults = dict(year='待填写', paper_type='M', paper=title, number=str(index), subject=path.parent.name)
            source = '\n\n'.join(question.content for question in parse_tex_questions(source, path.name, defaults))
        bodies.append(source.strip())
    return '\n\n'.join(bodies)


def _paper_key(year, paper_type, paper):
    return json.dumps([year, paper_type, paper], ensure_ascii=False, separators=(',', ':'))


def _summary_row(row, names=_NAMES):
    result = dict(zip(names, row))
    text = re.sub(r'(?m)^%[^\n]*|\\begin\{problem\}(?:\s*\{[^}]*\}){0,5}|\\end\{problem\}', '', result['stem'] or '')
    result['stem'] = re.sub(r'\s+', ' ', text).strip()[:200]
    if 'paperType' in result:
        result['paperKey'] = _paper_key(result['year'], result['paperType'], result['paper'])
    return result


def create_library_handlers(library: Path, state_dir: Path):
    library, state = Path(library).resolve(), Path(state_dir).resolve()
    csv = str(library / 'utils' / '题库索引表.csv')
    chapters = library / 'chapters'
    lock = Lock()

    def appearance():
        from dotenv import dotenv_values
        saved = read_settings(state)
        try:
            old = json.loads((library / '.runtime/ui_preferences.json').read_text(encoding='utf-8'))
        except (ValueError, OSError):
            old = {}
        env = dotenv_values(library / '.env')
        skin = normalize_skin(saved.get('skin') or old.get('skin') or env.get('APP_THEME_SKIN'))
        mode = saved.get('mode') or old.get('mode') or env.get('APP_THEME_MODE')
        mode = 'dark' if mode == 'dark' else 'light'
        return {'skin': skin, 'mode': mode, 'tokens': theme_tokens(skin, mode),
                'skins': {k: {'name': v['name'], 'accent': v['accent']} for k, v in SKINS.items()},
                'provider': selected_provider(state), 'library': str(library)}

    def preferences(p):
        update = {}
        for field, allowed in (('skin', SKINS), ('mode', {'dark', 'light'}), ('provider', LOCAL_AGENTS)):
            if field in p:
                if not isinstance(p[field], str) or p[field] not in allowed:
                    raise ValueError('设置值无效。')
                update[field] = p[field]
        with lock:
            settings = {**read_settings(state), **update}
            atomic_write_text(str(state / 'preferences.json'), json.dumps(settings, ensure_ascii=False))
        return appearance()

    def summary(_):
        with question_index.connection(csv) as db:
            subjects, module_counts = {}, {}
            for subject, module, count in db.execute(f'''SELECT "知识板块", {_MODULE}, count(*)
                    FROM questions GROUP BY "知识板块" ORDER BY count(*) DESC'''):
                subjects[subject] = count
                module_counts[module] = module_counts.get(module, 0) + count
            types = dict(db.execute('SELECT "题型",count(*) FROM questions GROUP BY "题型" ORDER BY count(*) DESC'))
            years = [r[0] for r in db.execute('SELECT DISTINCT "年份" FROM questions ORDER BY "年份" DESC') if r[0]]
            paper_groups = [dict(key=_paper_key(year, paper_type, paper), year=year, paper=paper,
                                 paperType=paper_type, paperTypeName=PAPER_TYPES.get(paper_type, paper_type), count=count)
                            for year, paper_type, paper, count in db.execute(f'''SELECT {_PAPER_COLUMNS}, count(*)
                                FROM questions GROUP BY {_PAPER_COLUMNS} ORDER BY "年份" DESC, "试卷名称", "试卷类型"''')]
            recent = [_summary_row(r) for r in db.execute(f'SELECT {_COLUMNS} FROM questions ORDER BY "最后修改时间" DESC, rowid DESC LIMIT 6')]
        module_names = [*SUBJECTS, *sorted(module_counts.keys() - set(SUBJECTS))]
        modules = [dict(name=name, count=module_counts.get(name, 0)) for name in module_names]
        return dict(total=sum(subjects.values()), subjects=subjects, modules=modules, types=types, years=years,
                    papers=len(paper_groups), paperGroups=paper_groups, recent=recent)

    def statistics(_):
        return question_index.statistics(csv_path=csv)

    def search(p):
        query = str(p.get('query', '')).strip()
        size = min(80, max(1, int(p.get('pageSize', 25))))
        page = max(1, int(p.get('page', 1)))
        browse_mode = p.get('browseMode')
        group_modules = browse_mode == 'module' or (browse_mode is None and p.get('groupModules') is True)
        clauses, values = [], []
        for key, column in _FILTERS.items():
            if p.get(key):
                clauses.append(f'"{column}" = ?')
                values.append(p[key])
        if group_modules and p.get('module'):
            clauses.append(f'{_MODULE} = ?')
            values.append(p['module'])
        if browse_mode == 'paper' and p.get('paper'):
            try:
                paper = json.loads(p['paper'])
            except (TypeError, ValueError):
                raise ValueError('试卷筛选无效。') from None
            if not isinstance(paper, list) or len(paper) != len(_PAPER_FIELDS) or not all(isinstance(part, str) for part in paper):
                raise ValueError('试卷筛选无效。')
            clauses.extend(f'"{field}" = ?' for field in _PAPER_FIELDS)
            values.extend(paper)
        fuzzy_ids = None
        if query and p.get('mode') == 'fuzzy':
            scored = question_index.fuzzy_search(query, csv_path=csv)
            fuzzy_ids = {s['row']['题目ID']: s['score'] for s in scored}
        else:
            for word in query.split()[:12]:
                columns = ('试卷名称', '题型', '知识板块', '知识点', '标签', '题干', '答案', '解析', '文件名称')
                clauses.append('(' + ' OR '.join(f'instr("{c}",?)>0' for c in columns) + ')')
                values.extend([word] * len(columns))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        extra_columns, extra_names = [], []
        order = '"最后修改时间" DESC, rowid DESC'
        if group_modules:
            extra_columns, extra_names = [_MODULE], ['module']
        elif browse_mode == 'paper':
            extra_columns, extra_names = ['"试卷类型"'], ['paperType']
            order = _PAPER_ORDER
        elif browse_mode == 'entry':
            extra_columns, extra_names = [_ENTRY_TIME], ['entered']
            direction = 'ASC' if p.get('entryOrder') == 'asc' else 'DESC'
            order = f'{_ENTRY_TIME} {direction}, rowid ASC'
        select_columns = ', '.join([_COLUMNS, *extra_columns])
        names = [*_NAMES, *extra_names]
        with question_index.connection(csv) as db:
            db.execute('BEGIN')
            sql = f'SELECT {select_columns} FROM questions{where} ORDER BY {order}'
            if fuzzy_ids is None:
                total = db.execute('SELECT count(*) FROM questions' + where, values).fetchone()[0]
                page = min(page, max(1, math.ceil(total / size)))
                rows = db.execute(sql + ' LIMIT ? OFFSET ?', values + [size, (page - 1) * size]).fetchall()
            else:
                matches = sorted((r for r in db.execute(sql, values) if r[0] in fuzzy_ids),
                                 key=lambda r: fuzzy_ids[r[0]], reverse=True)
                total = len(matches)
                page = min(page, max(1, math.ceil(total / size)))
                rows = matches[(page - 1) * size:page * size]
        return {'rows': [_summary_row(row, names) for row in rows], 'total': total, 'page': page, 'pages': max(1, math.ceil(total/size))}

    def question_path(rel):
        path = (chapters / rel).resolve()
        if not path.is_relative_to(chapters.resolve()) or path.suffix.lower() != '.tex' or not path.is_file():
            raise ValueError('题目已移动或文件路径无效。')
        return path

    def assemble(p):
        files = exam_question_files(chapters, p.get('paths', []))
        if not files:
            raise ValueError('请先选入题目。')
        answers = bool(p.get('answers', True))
        name = re.sub(r'[^\w\u4e00-\u9fff .-]', '', str(p.get('title', '练习卷'))).strip(' .')[:64] or '练习卷'
        papers = state / 'papers'
        folder = papers / uuid.uuid4().hex
        folder.mkdir(parents=True)
        output = folder / (('_' if Path(name).is_reserved() else '') + name + '.tex')
        try:
            body = exam_question_source(files.values(), library, folder, name, answers)
            atomic_write_text(str(output), body)
        except BaseException:
            if folder.resolve().parent == papers.resolve():
                shutil.rmtree(folder, ignore_errors=True)
            raise
        return {'path': str(output), 'title': name, 'exam': {'paths': list(files), 'answers': answers}}

    def demo(_):
        folder = state / 'demo'
        folder.mkdir(parents=True, exist_ok=True)
        for name in ('力与运动.tex', '力与运动.pdf'):
            source = _SAMPLE_ROOT / name
            dest = folder / name
            if not dest.exists() or dest.read_bytes() != source.read_bytes():
                shutil.copyfile(source, dest)
        return {'path': str(folder / '力与运动.tex'), 'previewPath': str(folder / '力与运动.pdf'), 'title': '力与运动 · 演示题'}

    def image_input(p):
        import pymupdf
        data = base64.b64decode(p['data'], validate=True)
        with pymupdf.open(stream=data, filetype=p['type']) as image:
            pdf = image.convert_to_pdf()
        folder = state / 'uploads' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        name = re.sub(r'[^\w .-]', '_', Path(p.get('name') or '题目').stem).strip(' .')[:80] or '题目'
        if Path(name).is_reserved():
            name = '_' + name
        path = folder / (name + '.pdf')
        path.write_bytes(pdf)
        return {'path': str(path)}

    def check(_):
        with question_index.connection(csv) as db:
            integrity = db.execute('PRAGMA quick_check').fetchone()[0]
            rows = list(db.execute('SELECT "相对文件路径" FROM questions'))
        missing = []
        for (name,) in rows:
            try:
                question_path(name)
            except ValueError:
                missing.append(name)
        return {'integrity': integrity, 'total': len(rows), 'missing': missing}

    def chat(p, *, provider=None):
        context = p.get('context') or {}
        message = str(p.get('message') or '').strip()
        if not message:
            raise ValueError('请输入消息。')
        schema = {'type': 'object', 'properties': {'reply': {'type': 'string'}, 'action': {'type': 'string','enum':['none','search','add']},
                  'query': {'type':'string'}, 'ids': {'type':'array','items':{'type':'string'}}},
                  'required':['reply','action','query','ids'],'additionalProperties':False}
        material = {'当前页面及数据':context,'近几轮对话':(p.get('history') or [])[-12:], '本次请求':message}
        result = run_agent_json(provider or selected_provider(state), '你是乌托邦高中物理题库的教研助手。用中文简洁回答。根据提供的真实题库数据回答。'
                '需要筛选时 action=search、query 写搜索词；仅当用户要求加入试卷时可用 add，ids 仅填上下文中出现的题目 ID。'
                '操作是待确认建议，不可声称已经执行。其它情况 action=none。\n' + json.dumps(material, ensure_ascii=False), schema, [])
        return result

    def provider_status(_):
        tool_path = fresh_tool_path()
        agents = agent_status(tool_path)
        return {'agents': agents, 'effectiveProvider': selected_provider(state),
                'codex': agents[0]['installed'], 'xelatex': bool(shutil.which('xelatex', path=tool_path)),
                'synctex': bool(shutil.which('synctex', path=tool_path))}

    return {'library.summary':summary, 'library.statistics':statistics, 'library.search':search,
            'library.assemble':assemble, 'library.demo':demo,
            'library.imageInput':image_input,'library.check':check, 'context.chat':chat,
            'ui.load':lambda _: appearance(), 'ui.save':preferences,'ui.providers':provider_status}
