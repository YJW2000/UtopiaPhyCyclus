"""Local TeX compilation for the source/PDF question-entry workspace."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from services.tex_import_service import parse_tex_questions
from utils.tex_log_tools import extract_tex_errors, summarize_tex_log
from utils.tikz_ops import _driver_tmp_env


#: TeX 录入预览编译目录名：``<项目>/.tikz_cache/tex-entry/preview-<uuid>/``
PREVIEW_CACHE_DIRNAME = 'tex-entry'
_PREVIEW_DIR_PREFIX = 'preview-'


def cleanup_preview_cache(project_dir: str, min_age_seconds: float = 0.0) -> int:
    """删除 TeX 录入预览留下的临时编译目录，返回实际清理的目录数。

    - 只处理 ``<project_dir>/.tikz_cache/tex-entry/preview-*``，绝不触碰其它缓存；
    - ``min_age_seconds > 0`` 时只删早于该年龄的目录（应用启动清扫用，
      避免误删其它浏览器会话正在编译的目录）；
    - 符号链接一律跳过，删除失败只忽略（权限受限时不抛异常）。
    """
    try:
        root = Path(project_dir).resolve() / '.tikz_cache' / PREVIEW_CACHE_DIRNAME
        if not root.is_dir():
            return 0
        root_resolved = root.resolve()
        now = time.time()
        removed = 0
        with os.scandir(str(root)) as entries:
            for entry in entries:
                try:
                    if not entry.name.startswith(_PREVIEW_DIR_PREFIX):
                        continue
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    child = Path(entry.path)
                    if child.resolve().parent != root_resolved:
                        continue
                    if min_age_seconds > 0:
                        age = now - entry.stat(follow_symlinks=False).st_mtime
                        if age < min_age_seconds:
                            continue
                    shutil.rmtree(child, ignore_errors=True)
                    if not child.exists():
                        removed += 1
                except OSError:
                    continue
        return removed
    except (OSError, TypeError, ValueError):
        return 0


#: TikZ/pgfkeys 未定义键的日志签名，例如
#: ``Package pgfkeys Error: I do not know the key '/tikz/meterblue'``。
_TIKZ_UNKNOWN_KEY_RE = re.compile(r"I do not know the key '(/tikz/([^']+))'", re.IGNORECASE)
#: ``LaTeX Error: Environment methodbox undefined.``（AI 生成的专题卷常用 tcolorbox 盒子环境）
_UNDEFINED_ENV_RE = re.compile(r"Environment\s+([A-Za-z@]+\*?)\s+undefined", re.IGNORECASE)
_FULL_DOCUMENT_RE = re.compile(r'(?m)^\s*\\documentclass\b')
_MAX_DIAGNOSIS_CHARS = 4000


def _word_occurrences(source: str, token: str) -> list[tuple[int, str]]:
    """返回 ``token`` 在源码中按整词出现的 ``(行号, 行文本)`` 列表（忽略空 token）。"""
    if not source or not token:
        return []
    pattern = re.compile(r'(?<![\w-])' + re.escape(token) + r'(?![\w-])')
    hits = []
    for number, line in enumerate(str(source).splitlines(), 1):
        if pattern.search(line):
            hits.append((number, line.strip()))
    return hits


def _one_line(text: str, limit: int = 240) -> str:
    for line in (text or '').splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ''


def _tikz_unknown_key_diagnosis(name: str, source: str) -> str:
    """为 ``/tikz/<name>`` 未定义键生成可操作的中文诊断（含源码行号与修复示例）。"""
    hits = _word_occurrences(source, name)
    if hits:
        where = '源码第 %d 行（以编辑框内源码为准）' % hits[0][0]
        if len(hits) > 1:
            where += '，共 %d 处' % len(hits)
        location = _one_line(hits[0][1], 200)
    else:
        where = '源码中未直接定位到（可能来自 \\input 的子文件）'
        location = ''
    lines = [
        '编译失败：TikZ 选项 “%s” 未定义（%s）。' % (name, where),
        '原因：题库模板没有定义这个颜色/选项，TikZ 会把它当成未知键报 pgfkeys 错误。'
        'figure 浮动体会延后到 \\end{document} 才排版，所以日志里的行号常常指向文件末尾，不能用它定位。',
        '修复（任选其一）：',
        '  1. 改用模板已定义的颜色：main、second、third、fourth、structure、primaryblue、meterblue、'
        'accentorange 等（完整清单见 UtopiaPhyCyclus_book.cls 颜色定义段），或 xcolor 基础色 '
        'red/blue/green/orange/cyan/gray/black/white；',
        '  2. 需要保留这个颜色名时，在题目前加一行定义，例如：\\definecolor{%s}{RGB}{0,120,190}；' % name,
        '  3. 如果它是自定义 TikZ 样式，先用 \\tikzset{%s/.style={...}} 定义再使用。' % name,
    ]
    if location:
        lines.append('定位行：%s' % location)
    return '\n'.join(lines)


def _undefined_environment_diagnosis(name: str, source: str) -> str:
    """为 ``Environment <name> undefined`` 生成中文诊断（含源码行号与模板支持说明）。"""
    line_no = None
    location = ''
    pattern = re.compile(re.escape('\\begin{%s}' % name))
    for number, line in enumerate(str(source or '').splitlines(), 1):
        if pattern.search(line):
            line_no = number
            location = _one_line(line, 200)
            break
    where = '源码第 %d 行（以编辑框内源码为准）' % line_no if line_no else '源码中未直接定位到（可能来自 \\input 的子文件）'
    lines = [
        '编译失败：LaTeX 环境 “%s” 未定义（%s）。' % (name, where),
        '原因：题库模板没有定义这个环境（AI 生成的专题卷常用 methodbox / trapbox 等 tcolorbox 盒子）。'
        '日志里的行号对应自动生成的 preview.tex，不是你的源码行号。',
        '修复（任选其一）：',
        '  1. 题目片段只保留模板已支持的环境：problem、answer、solutions、choices、enumerate、itemize 等，'
        '把自定义盒子里的内容改为普通段落（用 \\textbf{【…】} 作小标题）；',
        '  2. 若这是自带导言区与 \\newtcolorbox 定义的完整 document，请把整份文档源码一起粘贴/上传，'
        '完整文档会保留自己的导言区；',
        '  3. 批量 / 同卷录入时，每道题用 problem 环境包裹，答案与解析分别放进 answer / solutions。',
    ]
    if location:
        lines.append('定位行：%s' % location)
    return '\n'.join(lines)


def diagnose_tex_compile_failure(source: str, log: str) -> str:
    """把原始编译日志整理成中文诊断（优先识别未定义 TikZ 颜色/样式并给出源码行号）。

    该函数保证不抛异常：任何解析失败都退化为原始日志尾部。返回值适合直接在
    UI 中展示（多行纯文本）。
    """
    try:
        raw = str(log or '')
        source = str(source or '')
        for match in _TIKZ_UNKNOWN_KEY_RE.finditer(raw):
            return _tikz_unknown_key_diagnosis(match.group(2), source)[:_MAX_DIAGNOSIS_CHARS]
        environment = _UNDEFINED_ENV_RE.search(raw)
        if environment:
            return _undefined_environment_diagnosis(environment.group(1), source)[:_MAX_DIAGNOSIS_CHARS]
        errors = extract_tex_errors(raw)
        summary = summarize_tex_log(raw, source='preview.tex', max_chars=2600)
        if errors:
            head = '编译失败：解析到 %d 个错误。' % len(errors)
        else:
            head = '编译失败：未能从日志中解析出明确的 LaTeX 错误。'
        if _FULL_DOCUMENT_RE.search(source):
            note = '行号对应你的完整文档源码。'
        else:
            note = '行号对应自动生成的 preview.tex（题目片段会被套进题库模板），可能与源码行号不一致。'
        return (head + note + '\n' + summary)[:_MAX_DIAGNOSIS_CHARS]
    except Exception as exc:  # pragma: no cover - 防御：诊断绝不能再抛异常
        try:
            return ('编译失败；诊断生成异常（已降级）：%s: %s\n%s'
                    % (type(exc).__name__, exc, str(log or '')[-1200:]))[:_MAX_DIAGNOSIS_CHARS]
        except Exception:
            return '编译失败；诊断生成异常（已降级）。'


def build_preview_document(source: str, project_dir: str, defaults: dict) -> str:
    # Full documents keep their own class, packages and macro definitions.
    if re.search(r'(?m)^\s*\\documentclass\b', source):
        return source
    questions = parse_tex_questions(source, '草稿.tex', defaults)
    main = (Path(project_dir) / 'main.tex').read_text(encoding='utf-8')
    preamble = main.split(r'\begin{document}', 1)[0]
    body = '\n\n'.join(q.content for q in questions)
    return preamble + '\n\\begin{document}\n\\pagestyle{empty}\n' + body + '\n\\end{document}\n'


def compile_tex_preview(source: str, project_dir: str, defaults: dict,
                        engine: str = 'xelatex', resource_dir: str = '', timeout: int = 45) -> dict:
    executable = shutil.which(engine)
    if engine not in ('xelatex', 'lualatex', 'pdflatex') or not executable:
        return dict(ok=False, error=f'未找到本地 {engine} 编译器，请检查 TeX Live / MiKTeX 的 PATH。', log='')
    work_dir = Path(resource_dir or project_dir).resolve()
    if not work_dir.is_dir():
        return dict(ok=False, error='资源目录不存在。', log='')
    output = ''
    directory = None
    try:
        document = build_preview_document(source, project_dir, defaults)
        cache = Path(project_dir) / '.tikz_cache' / 'tex-entry'
        cache.mkdir(parents=True, exist_ok=True)
        # 每次编译独占唯一目录（旧 PDF 不可能误判成功）。这里**不用** tempfile.TemporaryDirectory：
        # 它以 POSIX mode 0o700 建目录，在受限 token / 特殊 ACL 环境（如本机 DSH 沙箱）下
        # 会继承不到可访问 ACE，导致目录连本进程都不可读写；唯一 uuid 目录 + finally
        # 尽力清理在各种权限环境下都更稳健。
        directory = cache / ('preview-' + uuid.uuid4().hex)
        directory.mkdir(parents=True, exist_ok=False)
        try:
            tex = directory / 'preview.tex'
            tex.write_text(document, encoding='utf-8')
            env = os.environ.copy()
            project_search = os.path.relpath(Path(project_dir).resolve(), work_dir).replace('\\', '/')
            env['TEXINPUTS'] = '.' + os.pathsep + project_search + os.pathsep + env.get('TEXINPUTS', '')
            # 根因修复（2026-10-20，与 utils.tikz_ops 同一根因）：xdvipdfmx 用
            # %TMP%\dvipdfmx.N 建 PSTricks 临时文件，TMP/TEMP 不可写时 fopen 返回 NULL
            # 后未判空即 fprintf → MSVCR100 0xC0000417。这里给子进程注入本次临时目录下
            # 的专用可写 TMP/TEMP/TMPDIR（绝对路径 + 写探针自检），覆盖不可写的继承值。
            try:
                env.update(_driver_tmp_env(directory))
            except Exception:
                pass
            if engine == 'lualatex':
                # 同一类根因：受限 token 下 luaotfload 需要可写的字体缓存目录，
                # 否则会 "no writeable cache path" 直接退出。
                try:
                    lua_cache = os.path.abspath(os.path.join(str(directory), '_luaotfload_cache'))
                    os.makedirs(lua_cache, exist_ok=True)
                    env['TEXMFVAR'] = lua_cache
                    env['TEXMFCACHE'] = lua_cache
                except OSError:
                    pass
            relative_tex = os.path.relpath(tex, work_dir).replace('\\', '/')
            relative_output = os.path.relpath(directory, work_dir).replace('\\', '/')
            for _ in range(2):
                completed = subprocess.run(
                    [executable, '-no-shell-escape', '-interaction=nonstopmode',
                     '-halt-on-error', '-file-line-error', '-synctex=1',
                     '-output-directory=' + relative_output, relative_tex],
                    cwd=work_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding='utf-8', errors='replace', timeout=timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
                output += completed.stdout or ''
                if completed.returncode:
                    diagnosis = diagnose_tex_compile_failure(source, output)
                    error = _one_line(diagnosis) or '编译失败，请查看日志中的文件名和行号。'
                    return dict(ok=False, error=error, log=output[-24000:], diagnosis=diagnosis)
            pdf = directory / 'preview.pdf'
            if not pdf.exists():
                return dict(ok=False, error='编译器未生成 PDF。', log=output[-24000:])
            return dict(ok=True, pdf=pdf.read_bytes(), log=output[-24000:], engine=engine)
        finally:
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b''
        if isinstance(partial, bytes):
            partial = partial.decode('utf-8', errors='replace')
        return dict(ok=False, error=f'本地编译超过 {timeout} 秒，已中断；源码仍保留。', log=(output + partial)[-24000:])
    except (ValueError, OSError) as exc:
        return dict(ok=False, error=str(exc), log=output[-24000:])


def render_pdf_page(pdf: bytes, page_number: int = 1, scale: float = 1.4) -> tuple[bytes, int]:
    import fitz
    with fitz.open(stream=pdf, filetype='pdf') as document:
        count = len(document)
        page = document[max(0, min(page_number - 1, count - 1))]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes('png'), count
