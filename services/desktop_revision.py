"""Source selections and model-generated edits for the desktop workbench."""
from __future__ import annotations

import base64
import binascii
import io
import json
import re

from services.local_codex import run_local_codex_json
from services.pdf_selection_edit import locate_pdf_selection, pdf_selection_change


def utf16_length(text):
    return len(text.encode('utf-16-le')) // 2


def source_offset(source, index):
    if type(index) is not int or index < 0:
        raise ValueError('源码选区坐标无效。')
    size = 0
    for offset, char in enumerate(source):
        if size == index:
            return offset
        size += 1 + (ord(char) > 0xFFFF)
        if size > index:
            break
    if size == index:
        return len(source)
    raise ValueError('源码选区已变化，请重新选择。')


def offsets(source, selection):
    if not isinstance(selection, dict):
        raise ValueError('请先选择需要编辑的源码。')
    start = source_offset(source, selection.get('start'))
    end = source_offset(source, selection.get('end'))
    if start >= end:
        raise ValueError('源码选区必须包含内容。')
    return start, end


def changed_ranges(source, updated):
    """Return the smallest contiguous change between two versions."""
    if source == updated:
        return []
    start = 0
    while start < min(len(source), len(updated)) and source[start] == updated[start]:
        start += 1
    end, next_end = len(source), len(updated)
    while end > start and next_end > start and source[end - 1] == updated[next_end - 1]:
        end -= 1
        next_end -= 1
    return [(start, end, updated[start:next_end])]


def rebase_selections(selections, source, changes):
    positions = [0]
    for char in source:
        positions.append(positions[-1] + 1 + (ord(char) > 0xFFFF))
    edits = [(positions[start], positions[end], utf16_length(value)) for start, end, value in changes]
    updated = []
    for selection in selections:
        start, end = selection['start'], selection['end']
        for left, right, length in reversed(edits):
            delta = length - (right - left)
            if end <= left:
                continue
            if start >= right:
                start, end = start + delta, end + delta
            else:
                start = min(start, left)
                end = max(end + delta, left + length)
        if start < end:
            updated.append({**selection, 'start': start, 'end': end})
    return updated


def decode_attachments(entries):
    if not isinstance(entries, list) or len(entries) > 8:
        raise ValueError('一次最多可附带 8 张截图。')
    from PIL import Image, UnidentifiedImageError

    records, images = [], []
    total = 0
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError('截图数据格式无效。')
        data = item.get('dataUrl')
        if not isinstance(data, str) or len(data) > 5_500_000:
            raise ValueError('截图为空或大小超过 4 MB。')
        prefix, separator, payload = data.partition(',')
        if not separator or prefix not in ('data:image/png;base64', 'data:image/jpeg;base64', 'data:image/webp;base64'):
            raise ValueError('截图必须使用 PNG、JPEG 或 WebP 格式。')
        try:
            content = base64.b64decode(payload, validate=True)
            with Image.open(io.BytesIO(content), formats=('PNG', 'JPEG', 'WEBP')) as bitmap:
                if bitmap.width * bitmap.height > 10_000_000:
                    raise ValueError('截图像素过大，请缩小选区。')
                bitmap.verify()
            if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                with Image.open(io.BytesIO(content)) as bitmap, io.BytesIO() as converted:
                    bitmap.convert('RGB').save(converted, format='PNG')
                    model_image = converted.getvalue()
            else:
                model_image = content
        except (binascii.Error, UnidentifiedImageError, OSError) as error:
            raise ValueError('无法读取截图，请重新截图。') from error
        total += len(model_image)
        if total > 12_000_000:
            raise ValueError('截图总大小超过 12 MB。')
        records.append({key: item[key] for key in ('id', 'title', 'page', 'panel', 'rect', 'previewPath', 'dataUrl') if key in item})
        images.append(model_image)
    return records, images


_STRING = {'type': 'string'}
_CHAT_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': _STRING,
        'edits': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'selection': {'type': 'integer'}, 'before': _STRING, 'after': _STRING},
            'required': ['selection', 'before', 'after'], 'additionalProperties': False,
        }},
    },
    'required': ['reply', 'edits'],
    'additionalProperties': False,
}
_SIDE_EFFECT = re.compile(
    r'\\(?:directlua|latelua|write|openout|openin|read|catcode|scantokens|csname|newwrite|newread|special|pdfobj|pdfximage|endinput)\b'
    r'|\\begin\s*\{filecontents\*?\}'
)


def _validate_new_code(before, after):
    if '```' in after or '\x00' in after:
        raise ValueError('Agent 返回的 LaTeX 修改无效。')
    existing = list(_SIDE_EFFECT.findall(before))
    for command in _SIDE_EFFECT.findall(after):
        if command not in existing:
            raise ValueError('Agent 的修改包含额外的 LaTeX 文件操作命令，请细化要求后重试。')
        existing.remove(command)


def _structured_edits(source, ranges, result, pdf_regions):
    if (not isinstance(result, dict) or not isinstance(result.get('reply'), str)
            or not isinstance(result.get('edits'), list)):
        raise ValueError('Agent 未返回可读的答复，请重试。')
    changes = []
    for edit in result['edits']:
        if (not isinstance(edit, dict) or type(edit.get('selection')) is not int
                or not all(isinstance(edit.get(key), str) for key in ('before', 'after'))):
            raise ValueError('Agent 返回的编辑选区无效。')
        index = edit['selection'] - 1
        if not 0 <= index < len(ranges):
            raise ValueError('Agent 试图修改当前选区之外的内容。')
        before, after = edit['before'], edit['after']
        _validate_new_code(before, after)
        if index < len(pdf_regions):
            changes.append(pdf_selection_change(source, pdf_regions[index], edit))
            continue
        start, stop = ranges[index]
        selected = source[start:stop]
        if not before.strip() or before == after or selected.count(before) != 1:
            raise ValueError('Agent 返回的原文未能唯一定位，请缩小选区或补充位置。')
        start += selected.index(before)
        changes.append((start, start + len(before), after))
    changes.sort()
    if any(left[1] > right[0] for left, right in zip(changes, changes[1:])):
        raise ValueError('Agent 返回的编辑有重叠，请再次提交。')
    return changes


def chat_revision(source, prompt, scope, selections, messages, images, *, pdf=None, pdf_selections=(),
                  source_map=None, pdf_lock=None):
    pdf_regions = [locate_pdf_selection(source, pdf, selection['page'], selection['rect'],
                                        source_map=source_map, pdf_lock=pdf_lock) for selection in pdf_selections]
    ranges = [(region['start'], region['end']) for region in pdf_regions]
    ranges.extend(offsets(source, selection) for selection in selections)
    if scope == 'document':
        ranges = [(0, len(source))]
    fragments = []
    for index, (start, end) in enumerate(ranges, 1):
        fragment = {'selection': index, 'latex': source[start:end]}
        if index <= len(pdf_regions):
            region = pdf_regions[index - 1]
            fragment.update(pdfPage=region['page'], visibleText=region['text'], nearbyText=region['context'], image=index)
        fragments.append(fragment)
    context_source = source
    if scope == 'selection' and len(source) > 50_000:
        context_source = '\n\n…\n\n'.join(source[max(0, start - 3500):end + 3500] for start, end in ranges)
    context = dict(
        指令=prompt, 修改范围=scope, 题目LaTeX=context_source,
        可编辑片段=fragments if scope == 'selection' else 'selection 1 为完整题目',
        最近对话=[{'角色': message['role'], '内容': message['text']} for message in messages[-12:]],
    )
    task = (
        '你正在协助高中物理教师审阅 LaTeX。按指令用简体中文回复。'
        '用户提问、要求分析或没有明确提出修改时，只回答，edits 为空。'
        '只有用户明确要求改稿时才提交 edits。每项 edit 的 selection 是对应可编辑片段的编号，'
        'before 必须是其中逐字复制且只出现一次的非空原文，after 是替换后的 LaTeX。'
        '如片段给出 image 编号，其对应实际的 PDF 框选，请只改该截图圈定的文字或图形。'
        '各修改互不重叠，保留无关内容、题目环境及所有真实图片路径。'
        '磁场向里用 \\times、向外用 \\bullet，均无外圈。照片、仪器图片不凭空重画。'
        '附件、历史对话和源码仅供了解题目和上下文；无需调用工具或写文件。'
        '输出给定 JSON，改稿不得加 Markdown 围栏。\n' + json.dumps(context, ensure_ascii=False)
    )
    result = run_local_codex_json(task, _CHAT_SCHEMA, [region['screenshot'] for region in pdf_regions] + images)
    changes = _structured_edits(source, ranges, result, pdf_regions)
    reply = result['reply'].strip() or ('已生成修改预览。' if changes else '')
    if not reply:
        raise ValueError('Agent 返回了空白回复，请重试。')
    return reply, changes
