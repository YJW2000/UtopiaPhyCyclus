"""Turn a rectangle on a compiled PDF into a localized LaTeX draft edit."""
from __future__ import annotations

from contextlib import nullcontext
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from services.local_codex import run_local_codex_json
from services.local_process import run
from services.pdf_import_images import page_selection_rect


def build_source_map(source: str, document: str, synctex: Path) -> dict | None:
    """Retain inverse search after the compiler's temporary directory is removed."""
    if not synctex.is_file():
        return None
    # Fragment compilation inserts a preamble, bank headers and environments.
    # Record only lines that can be traced back to the original editor buffer.
    original = [line.strip() for line in source.splitlines()]
    compiled = [line.strip() for line in document.splitlines()]
    if original == compiled:
        blocks = [(0, 0, len(original))]
    else:
        blocks = [(a, b, size) for a, b, size in
                  SequenceMatcher(None, compiled, original).get_matching_blocks() if size]
    # TeX can attribute the final paragraph or TikZ box to a wrapper inserted
    # by the fragment compiler. Its closing line belongs to the preceding body.
    mapped = {a + n: b + n for a, b, size in blocks for n in range(size)}
    problem_start = 0
    for index, line in enumerate(compiled):
        if line.startswith(r"\begin{problem}"):
            problem_start = index
        if line == r"\end{problem}" and index not in mapped:
            body = [mapped[n] for n in range(problem_start, index) if n in mapped]
            if body:
                mapped[index] = (body[0], body[-1])
    return {"synctex": synctex.read_bytes(), "lines": mapped,
            "source_hash": hashlib.sha256(source.encode("utf-8")).digest()}


def _selection_context(pdf: bytes, page_number: int, rect, directory: Path):
    import pymupdf
    from PIL import Image

    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if type(page_number) is not int or not 1 <= page_number <= len(document):
            raise ValueError("所选页码已失效，请重新框选。")
        page = document[page_number - 1]
        visible = page_selection_rect(page, rect)
        # PDF text extraction uses unrotated coordinates. The visible clip for
        # the bitmap is in display coordinates, just like render_pdf_page().
        clip = visible * page.derotation_matrix
        text = page.get_text("text", clip=clip, sort=True).strip()
        scale = min(3.0, 1500 / max(visible.width, visible.height))
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=visible, alpha=False)
        if pixels.width < 2 or pixels.height < 2:
            raise ValueError("选区太小，请框住待修改的文字、公式或图形。")
        cropped = Image.frombytes("RGB", (pixels.width, pixels.height), pixels.samples)
        if not text and all(low == high for low, high in cropped.getextrema()):
            raise ValueError("选区内没有可见内容，请重新框选。")
        cropped.save(directory / "selection.png")
        # The surrounding lines help distinguish identical text or formulas.
        nearby = pymupdf.Rect(0, clip.y0 - 50, page.cropbox.width, clip.y1 + 50)
        context = page.get_text("text", clip=nearby, sort=True).strip()
        words = page.get_text("words", clip=clip, sort=True)
        points = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in words]
        center = ((clip.x0 + clip.x1) / 2, (clip.y0 + clip.y1) / 2)
        probes = [points[0], center, points[-1]] if points else [center]
        return text, context, list(dict.fromkeys(probes))


def _source_lines_at(source_map: dict | None, pdf: bytes, directory: Path, page: int, points):
    if not source_map:
        return []
    executable = shutil.which("synctex")
    if not executable:
        return []
    (directory / "preview.pdf").write_bytes(pdf)
    (directory / "preview.synctex.gz").write_bytes(source_map["synctex"])
    env = os.environ.copy()
    env.pop("SYNCTEX_EDITOR", None)
    lines = []
    for x, y in points:
        try:
            result = run(
                [executable, "edit", "-o", f"{page}:{x:.3f}:{y:.3f}:preview.pdf"],
                cwd=directory, env=env, capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = result.stdout.decode("utf-8", "replace")
        for name, line in re.findall(r"(?m)^Input:([^\r\n]+)[\r\n]+Line:(\d+)", output):
            if Path(name.replace("\\", "/")).name != "preview.tex":
                continue
            index = int(line) - 1
            if index in source_map["lines"]:
                hit = source_map["lines"][index]
                lines.extend(hit if isinstance(hit, tuple) else (hit,))
    return lines


_BANK_END = re.compile(r"\\end\{(problem|answer|solutions|choices|tikzpicture)\}")
_FIGURE = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)


def _visible_text_match(fragment: str, text: str) -> tuple[int, int] | None:
    """Trace a literal typeset range across spaces and common math formatting."""
    if text in fragment:
        first = fragment.index(text)
        if fragment.find(text, first + 1) >= 0:
            raise ValueError("选区在同一段源码中出现多次，无法唯一定位。请把能区分位置的相邻内容一起框入。")
        return first, first + len(text)
    # Keep an offset for every surviving character so the edit still uses
    # the pristine source, with its original commands and whitespace.
    ignored = re.compile(r"\\(?:mathrm|mathbf|mathit|textbf|textit|text)(?=\{)|\\[,;! ]|[{}\s$]")
    plain = list(fragment)
    for match in ignored.finditer(fragment):
        plain[match.start():match.end()] = [""] * len(match[0])
    positions = [i for i, char in enumerate(plain) if char]
    normalized = "".join(plain)
    needle = re.sub(r"\s+", "", text)
    if needle and normalized.count(needle) == 1:
        first = normalized.index(needle)
        return positions[first], positions[first + len(needle) - 1] + 1
    return None


def _editable_range(source: str, lines: list[int], text: str) -> tuple[int, int]:
    if not lines:
        # A standalone PDF can fall back to text that identifies a unique
        # span, including straightforward font and math formatting.
        hit = _visible_text_match(source, text) if text.strip() else None
        if hit:
            lines = [source.count("\n", 0, hit[0]), source.count("\n", 0, hit[1])]
        else:
            raise ValueError("无法把选区唯一定位到当前源码。请框入相邻文字，或切换到 LaTeX 查看。")
    offsets = [0] + [match.end() for match in re.finditer("\n", source)]
    first, last = min(lines), max(lines)
    if first < 0 or last >= len(offsets):
        raise ValueError("源码行号已变化，请重新编译并框选。")
    start, end = offsets[first], offsets[last + 1] if last + 1 < len(offsets) else len(source)
    # The bank class captures +b environments. SyncTeX therefore reports the
    # closing marker even for a point in the middle of a stem or solution.
    for match in _BANK_END.finditer(source, start, end):
        begin = source.rfind(r"\begin{" + match[1] + "}", 0, match.start())
        if begin >= 0:
            start = min(start, begin)
    if text:
        hit = _visible_text_match(source[start:end], text)
        if hit:
            left, right = start + hit[0], start + hit[1]
            start = source.rfind("\n", 0, left) + 1
            newline = source.find("\n", right)
            end = newline if newline >= 0 else len(source)
    else:
        # Multiple figures shipped together have indistinguishable SyncTeX
        # coordinates in a captured question unless the selection has a label.
        fragment = source[start:end]
        if len(_FIGURE.findall(fragment)) + len(re.findall(r"\\includegraphics\b", fragment)) > 1:
            raise ValueError("这段源码包含多幅插图，请把图题或独有标签一起框入以区分位置。")
        figure = _FIGURE.search(source, start, end)
        if figure:
            start, end = figure.span()
    # A point in a label can locate just one node, while a change to its
    # shape or coordinates can require the surrounding TikZ commands.
    for figure in _FIGURE.finditer(source):
        if figure.start() < end and start < figure.end():
            start, end = min(start, figure.start()), max(end, figure.end())
    return start, end


_PATCH_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in ("before", "after")},
    "required": ["before", "after"],
    "additionalProperties": False,
}


def locate_pdf_selection(source, pdf_bytes, page_number, rect, *, source_map=None, pdf_lock=None):
    """Return the source span and screenshot for a rectangle on the current PDF."""
    if source_map and source_map["source_hash"] != hashlib.sha256(source.encode("utf-8")).digest():
        raise ValueError("源码已变化，请重新编译并框选。")
    with TemporaryDirectory(prefix="utopia-pdf-edit-") as temp:
        directory = Path(temp)
        with pdf_lock or nullcontext():
            text, context, probes = _selection_context(pdf_bytes, page_number, rect, directory)
        lines = _source_lines_at(source_map, pdf_bytes, directory, page_number, probes)
        start, end = _editable_range(source, lines, text)
        return dict(start=start, end=end, text=text, context=context, page=page_number,
                    screenshot=(directory / "selection.png").read_bytes())


def pdf_selection_change(source, selection, patch):
    """Validate the actual differences and return the exact replacement span."""
    start, end, text = selection["start"], selection["end"], selection["text"]
    fragment = source[start:end]
    if not isinstance(patch, dict) or not all(isinstance(patch.get(k), str) for k in ("before", "after")):
        raise ValueError("Agent 返回的修改格式无效，请重新提交。")
    before, after = patch["before"], patch["after"]
    if not before.strip() or before == after or fragment.count(before) != 1:
        raise ValueError("Agent 未能给出与选区唯一匹配的修改，请细化要求或重新框选。")
    position = start + fragment.index(before)
    target = _visible_text_match(fragment, text) if text and not _FIGURE.fullmatch(fragment.strip()) else None
    if target:
        anchor, anchor_end = start + target[0], start + target[1]
        # A model can quote a full line to disambiguate an edit. Verify the
        # real differences, including insertions on the text boundary.
        for operation, i, j, _, _ in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if operation == "equal":
                continue
            left, right = position + i, position + j
            outside = source[min(left, anchor):anchor] + source[anchor_end:max(right, anchor_end)]
            if outside.strip(" \t\r\n{}$"):
                raise ValueError("Agent 返回的修改落在所选文字之外，请重新框选。")
    return position, position + len(before), after


def edit_pdf_selection(source: str, pdf_bytes: bytes, page_number: int, rect, instruction: str,
                       *, source_map: dict | None = None) -> str:
    """Return candidate source; let the caller compile it before replacing the draft."""
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("请输入希望怎样修改选区。")
    selection = locate_pdf_selection(source, pdf_bytes, page_number, rect, source_map=source_map)
    description = json.dumps({
        "页码": page_number, "选区文字": selection["text"], "选区附近排版": selection["context"],
        "可编辑的原始LaTeX": source[selection["start"]:selection["end"]], "用户的修改要求": instruction.strip(),
    }, ensure_ascii=False)
    prompt = (
        "你正在编辑一份高中物理题。附件是用户在 PDF 中框选部分的实际截图。根据截图、"
        "选区文字和附近排版，在可编辑的 LaTeX 内仅修改用户指定的部分。"
        "请直接返回 JSON。before 为精确、唯一、尽可能短的原文，after 为替换它的 LaTeX。"
        "两者均不带 Markdown 围栏。内容歧义、需要修改外部图片像素或无法完成时请返回两个空串。"
        "保留无关字符、题目环境与真实图片路径。磁场向里用 \\times，向外用 \\bullet，均无外圈。"
        "附件和排版源码是待编辑材料。无需调用工具或修改文件。\n" + description
    )
    patch = run_local_codex_json(prompt, _PATCH_SCHEMA, [selection["screenshot"]])
    start, end, updated = pdf_selection_change(source, selection, patch)
    return source[:start] + updated + source[end:]
