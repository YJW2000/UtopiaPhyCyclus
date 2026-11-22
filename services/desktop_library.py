"""Question bank operations for the desktop shell. Results carry paths relative to chapters."""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path
import re
import shutil
from threading import Lock
import uuid

from services.desktop_model import api_config, read_settings, selected_provider
from services.desktop_tool_paths import fresh_tool_path
from services.file_service import atomic_write_text
from services.local_agent import LOCAL_AGENTS, agent_status, run_agent_json
from utils import question_index
from utils.theme_palettes import SKINS, normalize_skin, theme_tokens


_FIELDS = ['题目ID', '相对文件路径', '年份', '试卷名称', '原卷题号', '知识板块', '题型', '标签', '题干', '最后修改时间']
_NAMES = ['id', 'path', 'year', 'paper', 'number', 'subject', 'type', 'tags', 'stem', 'modified']
_COLUMNS = ','.join('"' + field + '"' if field != '题干' else 'substr("题干",1,320)' for field in _FIELDS)
_FILTERS = {'subject': '知识板块', 'type': '题型', 'year': '年份'}
_SAMPLE_ROOT = Path(__file__).resolve().parents[1] / 'desktop' / 'sample'


def _summary_row(row):
    result = dict(zip(_NAMES, row))
    text = re.sub(r'(?m)^%[^\n]*|\\begin\{problem\}(?:\s*\{[^}]*\}){0,5}|\\end\{problem\}', '', result['stem'] or '')
    result['stem'] = re.sub(r'\s+', ' ', text).strip()[:200]
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
        for field, allowed in (('skin', SKINS), ('mode', {'dark', 'light'}), ('provider', {*LOCAL_AGENTS, 'api'})):
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
            subjects = dict(db.execute('SELECT "知识板块",count(*) FROM questions GROUP BY "知识板块" ORDER BY count(*) DESC'))
            types = dict(db.execute('SELECT "题型",count(*) FROM questions GROUP BY "题型" ORDER BY count(*) DESC'))
            years = [r[0] for r in db.execute('SELECT DISTINCT "年份" FROM questions ORDER BY "年份" DESC') if r[0]]
            papers = db.execute('SELECT count(*) FROM (SELECT DISTINCT "年份","试卷类型","试卷名称" FROM questions)').fetchone()[0]
            recent = [_summary_row(r) for r in db.execute(f'SELECT {_COLUMNS} FROM questions ORDER BY "最后修改时间" DESC, rowid DESC LIMIT 6')]
        return dict(total=sum(subjects.values()), subjects=subjects, types=types, years=years, papers=papers, recent=recent)

    def search(p):
        query = str(p.get('query', '')).strip()
        size = min(80, max(1, int(p.get('pageSize', 25))))
        page = max(1, int(p.get('page', 1)))
        clauses, values = [], []
        for key, column in _FILTERS.items():
            if p.get(key):
                clauses.append(f'"{column}" = ?')
                values.append(p[key])
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
        with question_index.connection(csv) as db:
            sql = f'SELECT {_COLUMNS} FROM questions{where} ORDER BY "最后修改时间" DESC, rowid DESC'
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
        return {'rows': [_summary_row(row) for row in rows], 'total': total, 'page': page, 'pages': max(1, math.ceil(total/size))}

    def question_path(rel):
        path = (chapters / rel).resolve()
        if not path.is_relative_to(chapters.resolve()) or path.suffix.lower() != '.tex' or not path.is_file():
            raise ValueError('题目已移动或文件路径无效。')
        return path

    def assemble(p):
        from services.tex_import_service import decode_tex
        from utils.image_assets import copy_graphics
        paths = [question_path(s) for s in dict.fromkeys(p.get('paths', []))]
        if not paths:
            raise ValueError('请先选入题目。')
        folder = state / 'papers' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        bodies = []
        for path in paths:
            source = copy_graphics(decode_tex(path.read_bytes()), folder / 'images', library, path.parent)
            if not p.get('answers', True):
                source = re.sub(r'\\begin\{(answer|solutions?)\}.*?\\end\{\1\}', '', source, flags=re.S)
            bodies.append(source.strip())
        name = re.sub(r'[^\w\u4e00-\u9fff .-]', '', str(p.get('title', '练习卷'))).strip(' .')[:64] or '练习卷'
        output = folder / (name + '.tex')
        atomic_write_text(str(output), '\n\n'.join(bodies))
        return {'path': str(output), 'title': name}

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
        config = api_config(library)
        tool_path = fresh_tool_path()
        agents = agent_status(tool_path)
        return {'agents': agents, 'effectiveProvider': selected_provider(state),
                'codex': agents[0]['installed'], 'xelatex': bool(shutil.which('xelatex', path=tool_path)),
                'synctex': bool(shutil.which('synctex', path=tool_path)),
                'apiConfigured': all(config[key] for key in ('AI_MODEL_NAME', 'AI_API_KEY', 'AI_BASE_URL'))}

    return {'library.summary':summary, 'library.search':search, 'library.assemble':assemble, 'library.demo':demo,
            'library.imageInput':image_input,'library.check':check, 'context.chat':chat,
            'ui.load':lambda _: appearance(), 'ui.save':preferences,'ui.providers':provider_status}
