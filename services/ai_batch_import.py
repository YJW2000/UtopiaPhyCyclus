"""Prepare individual AI batch drafts for review and consistent bank paths."""
from dataclasses import replace
import re

from services.tex_import_service import _header, parse_tex_questions, repair_problem_headers
from utils.latex_ops import inject_meta_data, parse_meta_data


EDITABLE_LABELS = ("知识板块", "知识点", "难度星级", "物理方法", "标签")


def draft_question(item, defaults, *, filename_edited=False, paper_override=None):
    content = item["content"]
    if not content.strip():
        raise ValueError("题目内容为空。")
    filename = item["filename"].strip()
    if re.search(r'[/\\\n\r]', filename):
        raise ValueError("文件名中不能含有路径或换行。")
    questions = parse_tex_questions(content, filename, defaults)
    if len(questions) != 1:
        raise ValueError("此草稿包含多道题，请重新检测分题。")
    question = questions[0]
    if filename_edited:
        parts = filename.removesuffix(".tex").split("-")
        if len(parts) < 5 or not parts[0].isdigit() or not all(parts):
            raise ValueError("文件名需包含年份、类别、卷名、题号和板块。")
        question = replace(question, year=parts[0], paper_type=parts[1], paper="-".join(parts[2:-2]),
                           number=parts[-2], subject=parts[-1])
    if paper_override:
        question = replace(question, year=paper_override[0], paper_type=paper_override[1], paper=paper_override[2])

    # Filename normalization also applies to the header, index fields and directory.
    year, category, paper, number, subject = question.filename[:-4].split("-")
    canonical = replace(question, year=year, paper_type=category, paper=paper, number=number, subject=subject)
    source = repair_problem_headers(question.content)
    begin = re.search(r"\\begin\{problem\}", source)
    _, end = _header(source, begin.end())
    header = r"\begin{problem}" + "".join("{" + value + "}" for value in (year, category, paper, number, subject))
    source = source[:begin.start()] + header + source[end:]
    # The upload parser drops bank-local IDs. An editor round trip must preserve
    # the original label block until the new record receives its bank-local ID.
    meta, _ = parse_meta_data(content)
    if meta:
        source = inject_meta_data(source, meta)
    return replace(canonical, content=source)


def draft_labels(question):
    meta, _ = parse_meta_data(question.content)
    return {field: question.subject if field == "知识板块" else meta.get(field, "") for field in EDITABLE_LABELS}
