"""Collect per-question evidence and apply a versioned batch assessment to TeX."""
import base64
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from requests import RequestException

from services.ai_service import resolve_llm_timeouts
from services.batch_assessment import EVIDENCE_INSTRUCTION, RUBRIC_VERSION, score_evidence
from services.question_import_ai_service import _request_json
from services.tex_import_service import _header, _mask_comments
from utils.image_assets import PROJECT_ROOT, graphics_references, model_image_png, resolve_image
from utils.latex_ops import inject_meta_data, parse_meta_data


AUTO_FIELDS = {'难度星级': 'difficulty', '知识点': 'knowledge_points', '物理方法': 'physics_methods',
               '标签': 'tags', '知识板块': 'subject'}
DETAIL = '评分明细'
SIGNATURE = '评分签名'
VERSION = '评分版本'
STATUS = '评分状态'
MANUAL = '评分人工字段'
_SOLUTIONS = re.compile(r'\\begin\{(solutions?)\}(?:\s*\[[^\]]*\])?(.*?)\\end\{\1\}', re.S)
_PROBLEM = re.compile(r'\\begin\{problem\}(?:\s*\[[^\]]*\])?')


def assessment_detail(meta):
    try:
        data = json.loads(meta.get(DETAIL, ''))
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict) or not all(isinstance(data.get(key), str) for key in AUTO_FIELDS.values()):
        return {}
    if data.get('status') not in ('正式', '暂评', '待评') or not isinstance(data.get('parts'), list):
        return {}
    return data


def mark_manual_fields(question, fields):
    meta, _ = parse_meta_data(question.content)
    marked = set(meta.get(MANUAL, '').split('，')).union(fields)
    meta[MANUAL] = '，'.join(field for field in AUTO_FIELDS if field in marked)
    return replace(question, content=inject_meta_data(question.content, meta))


def manual_difficulty(value):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        stars = float(value)
    except ValueError:
        stars = -1
    if stars not in {i / 2 for i in range(13)}:
        raise ValueError('难度星级需在 0.5–6 之间，步长为 0.5，留空表示待评。')
    return value if stars else ''


def selected_subjects(value, subjects):
    names = [s.strip() for s in re.split('[,，]', value) if s.strip() and s.strip() != '未分类']
    for name in names:
        if name not in subjects:
            raise ValueError(f'“{name}”不属于题库知识板块，请从候选板块中选择。')
    return '，'.join(dict.fromkeys(names)) or '未分类'


def _image_message(data, name, page):
    data = model_image_png(data, name, page)
    return {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')}}


def _assessment_input(clean, project_dir, source_dir):
    # Exclude source, number, former subject and metadata from the model's evidence.
    start = _PROBLEM.search(_mask_comments(clean))
    if not start:
        raise ValueError('缺少独立的 problem 题目环境，请重新分题。')
    _, end = _header(clean, start.end())
    source = _mask_comments(clean[:start.start()] + clean[end:]).replace(r'\end{problem}', '')
    # Older questions can store their TikZ illustration as a separate TeX file.
    def include_diagram(match):
        relative = Path(match[1])
        if relative.suffix.lower() not in ('', '.tex') or relative.is_absolute():
            return match[0]
        path = relative.with_suffix('.tex')
        for directory in (source_dir, project_dir):
            root = Path(directory or project_dir).resolve()
            target = (root / path).resolve()
            if target.is_relative_to(root) and target.is_file():
                contents = target.read_text(encoding='utf-8-sig')
                if r'\begin{tikzpicture}' in contents:
                    return _mask_comments(contents)
        return match[0]
    source = re.sub(r'\\input\{([^{}]+)\}', include_diagram, source)
    images, image_ids, chunks = [], {}, []
    pos = 0
    for ref in graphics_references(source):
        page_arg = re.search(r'\bpage\s*=\s*(\d+)', ref['code'])
        page = max(1, int(page_arg[1])) if page_arg else 1
        try:
            path = resolve_image(ref['path'], project_dir, source_dir, clean)
            key = (str(path), page)
        except (OSError, ValueError):
            path = None
            key = (ref['path'], page)
        if key not in image_ids:
            data = path.read_bytes() if path is not None else None
            image_ids[key] = len(images) + 1
            images.append((str(path or ref['path']), page, data))
        number = image_ids[key]
        # Stable across copying/renaming the same graphic into its bank directory.
        chunks.extend((source[pos:ref['path_start']], f'图{number}', source[ref['path_end']:ref['end']]))
        pos = ref['end']
    chunks.append(source[pos:])
    source = '\n'.join(line.strip() for line in ''.join(chunks).splitlines() if line.strip())
    explanations = [m[2] for m in _SOLUTIONS.finditer(source)]
    if not explanations:
        labelled = re.search(r'(?:【(?:解析|解答|详解)】|\\textbf\{(?:解析|解答|详解)[：:]?\})\s*[：:]?(.+)', source, re.S)
        if labelled:
            explanations.append(labelled[1])
    has_solution = any(text.strip().strip('。.') not in ('', '略', '解析略', '详略', '暂无解析', '待补充') for text in explanations)
    signature = hashlib.sha256(json.dumps([RUBRIC_VERSION, source, [
        (page, hashlib.sha256(data).hexdigest() if data is not None else 'missing')
        for _, page, data in images]], ensure_ascii=False).encode('utf-8')).hexdigest()
    return signature, source, has_solution, images


def _evidence_messages(source, images):
    if not images:
        return source
    parts = [{'type': 'text', 'text': source}]
    for index, (name, page, data) in enumerate(images, 1):
        try:
            if data is None:
                raise ValueError('缺少图片')
            message = _image_message(data, name, page)
        except (ValueError, OSError, RuntimeError, IndexError):
            parts.append({'type': 'text', 'text': f'图{index}不可读。若此图是解题的必要条件，请说明依据不足。'})
        else:
            parts.extend(({'type': 'text', 'text': f'图{index}'}, message))
    return parts


def assessment_signature(question, *, project_dir=PROJECT_ROOT, source_dir=None):
    _, clean = parse_meta_data(question.content)
    return _assessment_input(clean, project_dir, source_dir)[0]


def _extract_and_score(config, source, images, subjects, has_solution):
    instruction = EVIDENCE_INSTRUCTION + '\n有效知识板块：' + '，'.join(s for s in subjects if s != '未分类')
    messages = _evidence_messages(source, images)
    for attempt in range(2):
        try:
            evidence = _request_json(config, instruction, messages, timeout=resolve_llm_timeouts())
        except RequestException as exc:
            raise ValueError('评分服务连接失败或超时，请重试。') from exc
        try:
            return score_evidence(evidence, subjects, has_solution=has_solution)
        except ValueError as exc:
            if attempt:
                raise
            instruction += ('\n请根据题面重新核对上次输出中的证据。程序校验发现：' + str(exc)
                            + '。只使用实际参与的物理规律；同一知识点内部的推导和纯几何关系不构成跨知识点耦合。')


def assess_batch_question(question, subjects, config, *, cache=None, force=False,
                          project_dir=PROJECT_ROOT, source_dir=None):
    meta, clean = parse_meta_data(question.content)
    previous = assessment_detail(meta)
    signature, source, has_solution, images = _assessment_input(clean, project_dir, source_dir)
    if not force and previous and meta.get(VERSION) == RUBRIC_VERSION and meta.get(SIGNATURE) == signature:
        result = previous
    elif not force and cache is not None and signature in cache:
        result = cache[signature]
    else:
        result = _extract_and_score(config, source, images, subjects, has_solution)
        if cache is not None:
            cache[signature] = result
            if len(cache) > 256:
                del cache[next(iter(cache))]

    manual = set(meta.get(MANUAL, '').split('，'))
    subject = question.subject
    for field, key in AUTO_FIELDS.items():
        value = subject if field == '知识板块' else meta.get(field, '')
        prior_value = previous.get(key, '')
        if field == '知识板块':
            prior_value = prior_value or '未分类'
        if previous and str(value) != str(prior_value):
            manual.add(field)
        if field not in manual:
            value = result[key]
        elif field == '难度星级':
            value = manual_difficulty(value)
        if field == '知识板块':
            subject = selected_subjects(value, subjects)
        else:
            meta[field] = value
    meta.update({DETAIL: json.dumps(result, ensure_ascii=False, separators=(',', ':')),
                 SIGNATURE: signature, VERSION: RUBRIC_VERSION, STATUS: result['status']})
    if any(field in manual for field in AUTO_FIELDS):
        meta[MANUAL] = '，'.join(field for field in AUTO_FIELDS if field in manual)
    match = _PROBLEM.search(_mask_comments(clean))
    _, end = _header(clean, match.end())
    fields = (question.year, question.paper_type, question.paper, question.number, subject)
    clean = clean[:match.end()] + ''.join('{' + f + '}' for f in fields) + clean[end:]
    return replace(question, subject=subject, content=inject_meta_data(clean, meta))
