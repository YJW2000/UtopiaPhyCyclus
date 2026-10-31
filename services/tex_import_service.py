"""Parse TeX uploads without OCR, compilation, or filesystem side effects."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TexQuestion:
    content: str
    year: str
    paper_type: str
    paper: str
    number: str
    subject: str

    @property
    def filename(self) -> str:
        # Match the bank's five-field filename convention. Never accept paths.
        fields = (self.year, self.paper_type, self.paper, self.number, self.subject)
        safe = [re.sub(r'[<>:"/\\|?*\x00-\x1f{}\[\]]', '_', f).replace('-', '－').strip(' .') or '未分类' for f in fields]
        return '-'.join(safe) + '.tex'


def decode_tex(data: bytes) -> str:
    if b'\x00' in data and not data.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise ValueError('文件包含二进制内容，请上传 TeX 文本文件。')
    for encoding in (('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')):
        try:
            return data.decode(encoding).replace('\r\n', '\n').replace('\r', '\n')
        except UnicodeError:
            pass
    raise ValueError('无法解码文件，请使用 UTF-8 或 GB18030 编码。')


def _mask_comments(text: str) -> str:
    return re.sub(r'(?m)(?<!\\)%[^\n]*', lambda m: ' ' * len(m[0]), text)


def _header(text: str, pos: int) -> tuple[list[str], int]:
    start = pos
    fields = []
    for _ in range(5):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text) or text[pos] != '{':
            return [], start
        depth, end = 1, pos + 1
        while end < len(text) and depth:
            if text[end] in '{}' and text[end - 1] != '\\':
                depth += 1 if text[end] == '{' else -1
            end += 1
        if depth:
            raise ValueError('problem 参数存在未闭合的大括号。')
        fields.append(text[pos + 1:end - 1].strip())
        pos = end
    return fields, pos


_META = re.compile(r'%(?: === Meta Data ===| === Begin Label Data ===)\n.*?%(?: === End Meta ===| === End\s+Label Data ===)\n?', re.S)


def repair_problem_headers(text: str) -> str:
    """Repair legacy bare-year headers and repeated identical parameter sets."""
    text = re.sub(r'(\\begin\{problem\})\s*(\d{4})(?=\s*\{)', r'\1{\2}', text)
    for match in reversed(list(re.finditer(r'\\begin\{problem\}', _mask_comments(text)))):
        fields, end = _header(text, match.end())
        if not fields:
            continue
        while True:
            repeated, next_end = _header(text, end)
            if repeated != fields:
                break
            text = text[:end] + text[next_end:]
    return text


def numbered_question_boundaries(text: str) -> list[tuple[int, str]]:
    """Find top-level numbered headings, never option/subquestion environments."""
    pattern = re.compile(
        r'^\s*(?:%\s*第\s*(\d{1,3})\s*题'
        r'|(?:\\noindent\s*)?\\textbf\{\s*(\d{1,3})\s*[.．、]\s*\}'
        r'|(?:\\noindent\s*)?(\d{1,3})[.．、](?!\d)\s*\S)')
    stack, candidates, offset, in_display_math = [], [], 0, False
    for line in text.splitlines(keepends=True):
        match = pattern.match(line) if not stack and not in_display_math else None
        if match:
            number = next(group for group in match.groups() if group)
            if candidates and candidates[-1][1] == number:
                between = _mask_comments(text[candidates[-1][0]:offset])
                between = re.sub(r'\\(?:noindent|textbf\{\s*\d+[.．、]\s*\})', '', between).strip()
                if not between:
                    match = None
            if match:
                candidates.append((offset, number))
        for token in re.finditer(r'\\(begin|end)\{([^{}]+)\}', _mask_comments(line)):
            if token[1] == 'begin':
                stack.append(token[2])
            elif stack and stack[-1] == token[2]:
                stack.pop()
        for token in re.finditer(r'\\\[|\\\]|\$\$', _mask_comments(line)):
            in_display_math = not in_display_math if token[0] == '$$' else token[0] == r'\['
        offset += len(line)
    return candidates


def detect_tex_questions(text: str, filename: str, defaults: dict[str, str]) -> list[TexQuestion]:
    """Shared batch/paper detector: explicit environments, then numbered stems."""
    parsed = parse_tex_questions(text, filename, defaults)
    result = []
    for question in parsed:
        match = re.search(r'\\begin\{problem\}', question.content)
        _, header_end = _header(question.content, match.end())
        end = question.content.rfind(r'\end{problem}')
        body = question.content[header_end:end] + question.content[end + len(r'\end{problem}'):]
        boundaries = numbered_question_boundaries(body)
        if len(boundaries) < 2:
            result.append(question)
            continue
        prefix = _mask_comments(body[:boundaries[0][0]])
        prefix = re.sub(r'\\noindent\b', '', prefix).strip()
        if prefix:
            raise ValueError('第一道题号前还有正文，请先为这段正文补上题号，避免遗漏。')
        for index, (start, number) in enumerate(boundaries):
            stop = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(body)
            fragment = body[start:stop].strip()
            fields = dict(year=question.year, paper_type=question.paper_type, paper=question.paper,
                          number=number, subject=question.subject)
            header = r'\begin{problem}' + ''.join('{' + value + '}' for value in fields.values())
            labels = _META.findall(question.content[:match.start()])
            result.append(TexQuestion('\n'.join(labels) + '\n' + header + '\n' + fragment + '\n' + r'\end{problem}', **fields))
    return result


def parse_tex_questions(text: str, filename: str, defaults: dict[str, str]) -> list[TexQuestion]:
    """Split explicit problem blocks; one unstructured upload is one question.

    Supports standalone documents and ---filename.tex--- bank exports. Answers
    outside problem are attached to the preceding question. Nested choices and
    enumerate are deliberately never treated as question boundaries.
    """
    text = repair_problem_headers(text.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff'))
    delimiters = list(re.finditer(r'(?m)^---([^\n]+\.tex)---\s*$', text))
    if delimiters:
        result = []
        for i, match in enumerate(delimiters):
            end = delimiters[i + 1].start() if i + 1 < len(delimiters) else len(text)
            result.extend(parse_tex_questions(text[match.end():end], match[1], defaults))
        return result
    masked = _mask_comments(text)
    doc = re.search(r'\\begin\{document\}', masked)
    if doc:
        end = re.search(r'\\end\{document\}', masked[doc.end():])
        if not end:
            raise ValueError('缺少 \\end{document}。')
        text = text[doc.end():doc.end() + end.start()]
        masked = _mask_comments(text)
    if not masked.strip():
        raise ValueError('TeX 文件没有题目正文。')
    tokens = list(re.finditer(r'\\(begin|end)\{problem\}', masked))
    blocks = []
    opened = None
    for token in tokens:
        if token[1] == 'begin':
            if opened is not None:
                raise ValueError('problem 环境嵌套或上一题缺少 \\end{problem}。')
            opened = token
        else:
            if opened is None:
                raise ValueError('存在没有对应开始标记的 \\end{problem}。')
            blocks.append((opened, token))
            opened = None
    if opened is not None:
        raise ValueError('缺少 \\end{problem}。')
    base = filename.replace('\\', '/').rsplit('/', 1)[-1]
    segments = re.sub(r'(?i)\.tex$', '', base).split('-')
    fallback = dict(defaults)
    if len(segments) >= 5 and segments[0].isdigit():
        fallback.update(year=segments[0], paper_type=segments[1], paper='-'.join(segments[2:-2]), number=segments[-2], subject=segments[-1])
    if not blocks:
        if re.search(r'\\(?:documentclass|usepackage)\b', masked):
            raise ValueError('包含导言区但缺少 document 环境，请上传完整文档或题目片段。')
        blocks = [(None, None)]
    result = []
    previous_end = 0
    for index, (begin, end) in enumerate(blocks):
        values = dict(fallback)
        number = str(values.get('number', '1'))
        values['number'] = str(int(number) + index) if number.isdigit() else (number if index == 0 else f'{number}（{index + 1}）')
        if begin is None:
            body = text.strip()
            params = []
        else:
            params, header_end = _header(text, begin.end())
            tail_end = blocks[index + 1][0].start() if index + 1 < len(blocks) else len(text)
            tail = text[end.end():tail_end]
            # A label block immediately before the next problem belongs to it.
            next_meta = _META.search(tail) if index + 1 < len(blocks) else None
            if next_meta:
                tail = tail[:next_meta.start()]
            prefix = text[previous_end:begin.start()]
            labels = _META.findall(prefix)
            body = text[header_end:end.start()].strip()
            body += '\n\\end{problem}' + tail.rstrip()
            previous_end = end.end()
            if params:
                values.update(zip(('year', 'paper_type', 'paper', 'number', 'subject'), params))
            body = ('\n'.join(labels) + '\n' if labels else '') + '__HEADER__\n' + body
        for key in ('year', 'paper_type', 'paper', 'number', 'subject'):
            if values.get(key, '').strip() in ('', '?'):
                values[key] = fallback.get(key, '') or ('未分类' if key == 'subject' else '1' if key == 'number' else '待填写')
            if re.search(r'[<>:"/\\|*\x00-\x1f]', values[key]) or values[key] in ('.', '..'):
                raise ValueError(f'题目参数 {key} 含有不适合题库路径的字符，请先修改。')
        header = '\\begin{problem}' + ''.join('{' + values[k] + '}' for k in ('year', 'paper_type', 'paper', 'number', 'subject'))
        content = body.replace('__HEADER__', header, 1) if begin else header + '\n' + body + '\n\\end{problem}'
        # Uploaded IDs and usage counts belong to another bank; allocate on save.
        content = re.sub(r'(?m)^% (?:ID|组卷引用次数):[^\n]*\n?', '', content)
        result.append(TexQuestion(content.strip(), **values))
    return result


def join_tex_questions(questions: list[TexQuestion]) -> str:
    return '\n\n'.join(f'---{q.filename}---\n\n{q.content}' for q in questions)
