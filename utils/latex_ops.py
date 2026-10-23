import os
import re
import html
from .core_config import CHAPTERS_DIR, BASE_DIR
from .file_ops import ensure_dir
from .tikz_ops import get_tikz_image_b64
from services.file_service import atomic_write_text, backup_existing_file
from services.tex_import_service import repair_problem_headers

# ================= 版式类命令清理 =================
# 批量录入时模型常在每道题末尾补一个 \newpage（原本用于分隔题目），
# 但它会残留在题目正文里，预览/组卷时表现为一行多余的 "\newpage" 文本。
_LAYOUT_ONLY_LINE_RE = re.compile(
    r"^\s*\\(?:newpage|clearpage|cleardoublepage|pagebreak)\b\s*(?:\[[^\]]*\])?\s*$"
)
_LAYOUT_CMD_RE = re.compile(
    r"\\(?:newpage|clearpage|cleardoublepage|pagebreak)\b\s*(?:\[[^\]]*\])?\s*"
)


def strip_layout_commands(content: str) -> str:
    """移除正文中残留的分页命令（\\newpage 等），保留注释行不动。"""
    if not content:
        return content or ""
    if "\\newpage" not in content and "\\clearpage" not in content and "\\pagebreak" not in content:
        return content

    lines = []
    for line in content.splitlines():
        if line.lstrip().startswith("%"):
            lines.append(line.rstrip())
            continue
        if _LAYOUT_ONLY_LINE_RE.match(line):
            continue
        lines.append(_LAYOUT_CMD_RE.sub("", line).rstrip())

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text

# ================= Meta Data 处理 =================
def parse_meta_data(content):
    """
    解析文件头部的 % === Begin Label Data === 注释块
    返回 (meta_dict, clean_content)
    """
    meta = {}
    # 支持旧版的 Meta Data 以及新版的 Label Data (匹配 End Label Data 或 End  Label Data)
    pattern = r'%(?: === Meta Data ===| === Begin Label Data ===)\n(.*?)%(?: === End Meta ===| === End\s+Label Data ===)\n'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        lines = match.group(1).split('\n')
        for line in lines:
            if line.startswith('% '):
                parts = line[2:].split(':', 1)
                if len(parts) == 2:
                    meta[parts[0].strip()] = parts[1].strip()
    
    # 剔除 Meta 块后的纯净内容
    clean_content = re.sub(pattern, '', content, flags=re.DOTALL).lstrip()
    return meta, clean_content

def inject_meta_data(content, meta_dict):
    """
    将 meta_dict 转换为注释块并注入到内容头部
    """
    _, clean_content = parse_meta_data(content)
    
    meta_str = "% === Begin Label Data ===\n"
    for k, v in meta_dict.items():
        meta_str += f"% {k}: {v}\n"
    meta_str += "% === End  Label Data ===\n\n"
    
    return meta_str + clean_content


def get_editor_height(content):
    line_count = content.count('\n') + 2
    # 每行大约 26 像素，设置最小高度 150，最大高度 800
    base_height = max(150, min(800, line_count * 26))
    
    # 只有当包含 TikZ 时，才额外增加高度
    tikz_count = content.count(r'\begin{tikzpicture}')
    if tikz_count > 0:
        return base_height + tikz_count * 300
    return base_height


def _tikz_image_markdown(tikz_code, source_tex_path=None, target_png_path=None):
    b64, err = get_tikz_image_b64(
        tikz_code,
        BASE_DIR,
        source_tex_path=source_tex_path,
        target_png_path=target_png_path,
    )
    if b64:
        return f"\n\n<div style='text-align: center;'><img src='data:image/png;base64,{b64}' style='max-width:100%; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin: 10px 0;'></div>\n\n"

    safe_code = html.escape(tikz_code)
    if err == "MISSING_PYMUPDF":
        return (
            "\n\n> ⚠️ **提示**：检测到 TikZ 绘图。请在终端运行 `pip install pymupdf` 安装依赖后，即可渲染为图片预览。\n\n"
            f"<details><summary>查看 TikZ 源码</summary><div style='white-space: pre-wrap; font-family: inherit; background: #ffffff; border: 1px solid #e1e4e8; border-radius: 8px; padding: 10px; margin-top: 8px;'>{safe_code}</div></details>\n\n"
        )
    safe_err = html.escape(str(err))
    return (
        f"\n\n> ⚠️ **TikZ 编译失败** (`{safe_err}`)。请检查 LaTeX 语法或本地环境。\n\n"
        f"<details><summary>查看 TikZ 源码</summary><div style='white-space: pre-wrap; font-family: inherit; background: #ffffff; border: 1px solid #e1e4e8; border-radius: 8px; padding: 10px; margin-top: 8px;'>{safe_code}</div></details>\n\n"
    )


def _replace_latex_linebreaks_outside_math(s):
    s = s or ""
    out = []
    i = 0
    in_inline_math = False
    in_display_math = False
    in_paren_math = False
    in_bracket_math = False

    while i < len(s):
        if s.startswith("<details", i):
            end = s.find("</details>", i)
            if end != -1:
                end += len("</details>")
                out.append(s[i:end])
                i = end
                continue

        if s.startswith(r"\[", i):
            in_bracket_math = True
            out.append(s[i:i + 2])
            i += 2
            continue
        if s.startswith(r"\]", i):
            in_bracket_math = False
            out.append(s[i:i + 2])
            i += 2
            continue
        if s.startswith(r"\(", i):
            in_paren_math = True
            out.append(s[i:i + 2])
            i += 2
            continue
        if s.startswith(r"\)", i):
            in_paren_math = False
            out.append(s[i:i + 2])
            i += 2
            continue

        if s.startswith("$$", i) and not in_inline_math:
            in_display_math = not in_display_math
            out.append("$$")
            i += 2
            continue

        if s[i] == "$" and not in_display_math and (i == 0 or s[i - 1] != "\\"):
            in_inline_math = not in_inline_math
            out.append(s[i])
            i += 1
            continue

        in_math = in_inline_math or in_display_math or in_paren_math or in_bracket_math
        if not in_math and s.startswith(r"\\", i):
            next_pos = i + 2
            if next_pos < len(s) and s[next_pos].isascii() and s[next_pos].isalpha():
                out.append(s[i])
                i += 1
                continue

            end = next_pos
            if end < len(s) and s[end] == "[":
                close = s.find("]", end + 1)
                if close != -1:
                    end = close + 1
            while end < len(s) and s[end] in " \t":
                end += 1
            out.append("<br>\n")
            i = end
            continue

        for command in (r"\newline", r"\linebreak"):
            if not in_math and s.startswith(command, i):
                next_pos = i + len(command)
                if next_pos == len(s) or not (s[next_pos].isascii() and s[next_pos].isalpha()):
                    out.append("<br>\n")
                    i = next_pos
                    break
        else:
            out.append(s[i])
            i += 1
            continue
        continue

    return "".join(out)


def _iter_latex_preview_segments(s):
    s = s or ""
    i = 0
    while i < len(s):
        if s.startswith("<details", i):
            end = s.find("</details>", i)
            if end != -1:
                end += len("</details>")
                yield "protected", s[i:end]
                i = end
                continue

        math_start = None
        math_end_token = None
        if s.startswith("$$", i):
            math_start = i + 2
            math_end_token = "$$"
        elif s.startswith(r"\[", i):
            math_start = i + 2
            math_end_token = r"\]"
        elif s.startswith(r"\(", i):
            math_start = i + 2
            math_end_token = r"\)"
        elif s[i] == "$" and (i == 0 or s[i - 1] != "\\"):
            math_start = i + 1
            math_end_token = "$"

        if math_start is not None:
            end = s.find(math_end_token, math_start)
            if end == -1:
                yield "math", s[i:]
                return
            end += len(math_end_token)
            yield "math", s[i:end]
            i = end
            continue

        next_positions = [p for p in (
            s.find("$$", i + 1),
            s.find(r"\[", i + 1),
            s.find(r"\(", i + 1),
            s.find("<details", i + 1),
        ) if p != -1]

        single_dollar = -1
        j = i + 1
        while j < len(s):
            j = s.find("$", j)
            if j == -1:
                break
            if (j == 0 or s[j - 1] != "\\") and not s.startswith("$$", j):
                single_dollar = j
                break
            j += 1
        if single_dollar != -1:
            next_positions.append(single_dollar)

        end = min(next_positions) if next_positions else len(s)
        yield "text", s[i:end]
        i = end


def _circled_text_value(value):
    value = (value or "").strip()
    if value.isdigit():
        n = int(value)
        if n == 0:
            return chr(0x24EA)
        if 1 <= n <= 20:
            return chr(0x2460 + n - 1)
    return f"({value})"


def _circled_html(value):
    safe_value = html.escape((value or "").strip())
    return f'<span style="display:inline-block; width:1.2em; height:1.2em; line-height:1.2em; text-align:center; border-radius:50%; border:1px solid currentColor; font-size:0.85em;">{safe_value}</span>'


def _meter_html(meter, index):
    r"""在题目预览中显示 OCR 常用的 ``\circledV{1}``/``\circledA{1}`` 电表标记。"""
    safe_meter = html.escape((meter or "").upper())
    safe_index = html.escape((index or "").strip())
    label = safe_meter + (f'<sub style="font-size:0.65em;">{safe_index}</sub>' if safe_index else "")
    return (
        '<span style="display:inline-flex; align-items:center; justify-content:center; '
        'min-width:1.45em; height:1.45em; padding:0 0.08em; vertical-align:-0.18em; '
        'border-radius:50%; border:1px solid currentColor; font-size:0.82em; '
        f'line-height:1; box-sizing:border-box;">{label}</span>'
    )


def _replace_circled_for_preview(s):
    def replace_math(segment):
        segment = re.sub(
            r'\\circled([AV])\{([^{}]*)\}',
            lambda m: r'\text{' + m.group(1) + r'}_{' + m.group(2).strip() + '}',
            segment,
        )
        segment = re.sub(
            r'\\text\s*\{\s*\\circled\{([^{}]+)\}\s*\}',
            lambda m: r'\text{' + _circled_text_value(m.group(1)) + '}',
            segment,
        )
        return re.sub(
            r'\\circled\{([^{}]+)\}',
            lambda m: r'\text{' + _circled_text_value(m.group(1)) + '}',
            segment,
        )

    out = []
    for kind, segment in _iter_latex_preview_segments(s):
        if kind == "math":
            out.append(replace_math(segment))
        elif kind == "text":
            segment = re.sub(
                r'\\circled([AV])\{([^{}]*)\}',
                lambda m: _meter_html(m.group(1), m.group(2)),
                segment,
            )
            out.append(re.sub(r'\\circled\{([^{}]+)\}', lambda m: _circled_html(m.group(1)), segment))
        else:
            out.append(segment)
    return "".join(out)


def _replace_spacing_outside_math(s):
    out = []
    for kind, segment in _iter_latex_preview_segments(s):
        if kind == "text":
            # LaTeX 的版式控制命令在 Markdown 预览中不应作为正文显示。
            # 竖直间距在预览中用段落间隔表示，\noindent 则直接移除。
            segment = re.sub(r'\\noindent\b[ \t]*', '', segment)
            segment = re.sub(r'\\(?:smallskip|medskip|bigskip)\b[ \t]*', '\n\n', segment)
            segment = re.sub(r'\\(?:vspace|addvspace)\*?\s*\{[^{}]*\}[ \t]*', '\n\n', segment)
            segment = re.sub(
                r'\\vskip\s+(?:[-+]?\d*\.?\d+\s*)?(?:pt|pc|in|bp|cm|mm|dd|cc|sp|em|ex)\b[ \t]*',
                '\n\n',
                segment,
            )
            segment = re.sub(r'\\par\b[ \t]*', '\n\n', segment)
            segment = re.sub(r'\\hspace\{.*?\}', "\u3000", segment)
            segment = segment.replace(r'\quad', "\u3000")
            segment = segment.replace(r'\hfill', "\u3000")
        out.append(segment)
    return "".join(out)


def _strip_preview_layout_wrappers(s):
    """去掉 AI / OCR 题目里常见的版式包装，避免预览把 LaTeX 代码当正文显示。

    AI 生成的物理题常把插图写成
    ``\\begin{figure}[htbp]\\centering\\begin{minipage}[c]{0.46\\textwidth}\\centering
    \\begin{tikzpicture}...\\end{tikzpicture}\\centerline{\\small\\textbf{(a) ...}}\\end{minipage}...``。
    ``tikzpicture`` 会被转成图片，但 figure/minipage/centerline 等外壳此前会原样漏成
    文字（用户反馈「题目中残留 tex 代码」）。这里只剥离版式壳，保留正文与图片。
    """
    s = s or ""
    # figure 浮动体：环境壳与位置参数
    s = re.sub(r'\\begin\{figure\*?\}(?:\[[^\]]*\])?', '\n\n', s)
    s = re.sub(r'\\end\{figure\*?\}', '\n\n', s)
    # minipage：可选对齐参数 + 可选宽度参数
    s = re.sub(r'\\begin\{minipage\}(?:\[[^\]]*\])?\s*(?:\{[^{}]*\})?', '\n\n', s)
    s = re.sub(r'\\end\{minipage\}', '\n\n', s)
    # \centerline{...} / \caption{...} → 普通段落（括号配平，兼容标题里 $\frac{1}{2}$ 等嵌套花括号）
    s = re.sub(r'\\caption\s*\[[^\]]*\]', r'\\caption', s)
    s = _replace_braced_command(s, 'centerline', lambda inner: '\n\n' + inner.strip() + '\n\n')
    s = _replace_braced_command(s, 'caption', lambda inner: '\n\n*' + inner.strip() + '*\n\n')
    # AI 专题卷的段落小标题：\paragraph{题1（基础…）} → 加粗标题
    s = re.sub(r'\\(paragraph|subparagraph)\s*\[[^\]]*\]', r'\\\1', s)
    s = _replace_braced_command(s, 'paragraph', lambda inner: '\n\n**' + inner.strip() + '**\n\n')
    s = _replace_braced_command(s, 'subparagraph', lambda inner: '\n\n**' + inner.strip() + '**\n\n')
    # ctex 字号命令（\zihao{3} 等）在预览里没有意义
    s = re.sub(r'\\zihao\{[^{}]*\}\s*', '', s)
    # AI 专题卷常用的 tcolorbox 盒子：去壳，标题保留为加粗行
    s = re.sub(
        r'\\begin\{(?:methodbox|trapbox)\}(?:\[([^\]]*)\])?',
        lambda m: ('\n\n**【%s】**\n\n' % m.group(1).strip()) if m.group(1) else '\n\n',
        s,
    )
    s = re.sub(r'\\end\{(?:methodbox|trapbox)\}', '\n\n', s)
    # 颜色开关：\color{darkblue} 这类在预览里没有意义（保留 \textcolor 的内容由后续逻辑处理）
    s = re.sub(r'\\color\{[^{}]*\}\s*', '', s)
    # 纯版式开关（字号/对齐/加粗切换/浮动位置）不应出现在预览文本里。
    # 用 (?![A-Za-z@]) 而不是 \b：命令后面紧跟中文时（如 \bfseries伏安…）
    # \b 在「s」与中文之间不成立，会漏删。
    s = re.sub(
        r'\\(?:centering|raggedright|raggedleft|hfill|vfill|par|'
        r'tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge|'
        r'bfseries|mdseries|itshape|slshape|scshape|upshape|rmfamily|sffamily|ttfamily)'
        r'(?![A-Za-z@])[ \t]*',
        '',
        s,
    )
    s = re.sub(r'\\label\{[^{}]*\}\s*', '', s)
    return s


def _replace_braced_command(text, command, formatter):
    """把 ``\\command{...}`` 替换为 ``formatter(inner)``，正确处理嵌套花括号。

    早期预览用非贪婪正则处理 ``\\textbf{...}``，遇到 ``\\textbf{... $\\frac{1}{2}$ ...}``
    这类参数里还有花括号的命令时会被截断，预览里残留 ``{\\Delta I}`` 等源码。
    """
    text = text or ""
    token = '\\' + command + '{'
    out = []
    index = 0
    length = len(text)
    while index < length:
        start = text.find(token, index)
        if start < 0:
            out.append(text[index:])
            break
        out.append(text[index:start])
        cursor = start + len(token)
        depth = 1
        while cursor < length and depth:
            char = text[cursor]
            if char == '\\':
                cursor += 2
                continue
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
            cursor += 1
        if depth:
            out.append(text[start:])
            break
        out.append(formatter(text[start + len(token):cursor - 1]))
        index = cursor
    return ''.join(out)


def latex_to_markdown(content, show_title=True):
    """简单的 LaTeX 转 Markdown 用于预览"""
    content = repair_problem_headers(content)
    # 兼容历史数据：预览时也剔除残留的 \newpage 等分页命令
    content = strip_layout_commands(content)
    content = re.sub(r'(?m)(?<!\\)%[^\n]*', '', content)
    content = re.sub(r'\\noindent\b\s*', '', content)
    content = re.sub(r'\\textbf\{([^{}]*)\}', r'**\1**', content)
    content = re.sub(r"(?<!\$)\$([^$\n]*?)\$(?!\$)", lambda m: "$" + m.group(1).strip() + "$", content)
    content = re.sub(r"\$\$\s*([\s\S]*?)\s*\$\$", lambda m: "$$\n" + m.group(1).strip() + "\n$$", content)
    # 处理批量模式下的多题分割线 ---xxx.tex---
    if "---" in content:
        # 将分割线替换为 Markdown 分隔符和文件名标题
        content = re.sub(r'---(.*?\.tex)---', r'\n\n---\n### 📄 \1\n', content)
        
    # 提取 problem / question 环境参数
    # 使用 re.finditer 处理多题情况
    problem_header_pat = r'\\begin\{problem\}(?:\[[^\]]*\])?\s*\{(.*?)\}\s*\{(.*?)\}\s*\{(.*?)\}\s*\{(.*?)\}\s*\{(.*?)\}'
    for match in reversed(list(re.finditer(problem_header_pat, content))):
        year, ptype, name, num, subj = match.groups()
        if show_title:
            header = f"**【{year}  {name}，{num}】**\n\n"
            content = content[:match.start()] + header + content[match.end():]
        else:
            # 将原有的 \begin{problem}... 标签直接去掉，不重复添加 Markdown header
            content = content[:match.start()] + content[match.end():]
    
    # 移除可能存在的 \begin{problem} / \end{problem}（兜底：即使参数解析失败也尽量剔除头部）
    content = re.sub(r'\\begin\{problem\}(?:\[[^\]]*\])?(?:\s*\{[^\}]*\}){0,5}', '', content)
    content = content.replace(r'\end{problem}', '')

    # 移除可能存在的 \begin{question} / \end{question}
    content = re.sub(r'\\begin\{question\}(?:\[[^\]]*\])?(?:\s*\{[^\}]*\}){0,5}', '', content)
    content = content.replace(r'\end{question}', '')
    
    # 处理 answer 环境
    content = re.sub(r'\\begin\{answer\}', '\n\n**【答案】**\n', content)
    content = re.sub(r'\\end\{answer\}', '', content)
    
    # 处理 solution/solutions 环境
    # 优先匹配带参数的 \begin{solutions}[另解]
    content = re.sub(r'\\begin\{solutions?\}\[(.*?)\]', r'\n\n**【\1】**\n', content)
    # 再匹配没有参数的普通解答
    content = re.sub(r'\\begin\{solutions?\}', '\n\n**【解答】**\n', content)
    content = re.sub(r'\\end\{solutions?\}', '', content)
    
    # 清理批量模式下的 Label Data
    content = re.sub(r'%(?: === Meta Data ===| === Begin Label Data ===)\r?\n([\s\S]*?)%(?: === End Meta ===| === End\s+Label Data ===)\r?\n', '', content, flags=re.DOTALL)

    
    # 处理 choices 环境 (A. B. C. D. 样式)
    def replace_choices_env(match):
        # match.group(1) 可能是可选项 [2] 等，match.group(2) 才是内部内容
        content_inner = match if isinstance(match, str) else match.group(2)
        
        # 兼容 \choice 和 \item 两种选项标记
        # 将 \choice 统一替换为 \item 进行后续分割
        content_inner = re.sub(r'\\choice', r'\\item', content_inner)
        
        # 按 \item 分割
        parts = re.split(r'\\item', content_inner)
        
        new_inner = parts[0] # 保留第一个 \item 前的内容（通常是空的或换行）
        choice_idx = 0
        
        for p in parts[1:]:
            # 生成标号 A. B. C. ... 并应用类似 Times New Roman 的正体样式
            letter = chr(65 + choice_idx)
            label = f'<span style="font-family: \'Times New Roman\', Times, serif;">{letter}.</span>'
            choice_idx += 1
            
            # 清理括号
            p = p.strip()
            if p.startswith("{{") and p.endswith("}}"):
                p = p[2:-2]
            elif p.startswith("{") and p.endswith("}"):
                p = p[1:-1]
            
            new_inner += f"\n{label} {p}\n"
            
        return new_inner

    # 兼容带有参数的 \begin{choices}[2] 等情况
    content = re.sub(r'\\begin\{choices\}(\[.*?\])?(.*?)\\end\{choices\}', replace_choices_env, content, flags=re.DOTALL)
    
    # 移除残留的标签（以防万一）
    content = content.replace(r'\begin{choices}', '').replace(r'\end{choices}', '')
    
    # 处理填空题下划线：只在文本段转成 <u> 空白；公式段保留 \underline{\hspace{...}}
    # 交给 KaTeX 渲染。否则 <u>/&nbsp; 混进 $...$ 会让整段公式解析失败
    # （用户反馈的“中间公式乱码”）。
    def _underline_to_html(segment):
        segment = re.sub(
            r'\\underline\{\\hspace\{[^{}]*\}\}',
            '<u>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'
            '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</u>',
            segment,
        )
        return re.sub(r'\\underline\{(.*?)\}', r'<u>\1</u>', segment, flags=re.DOTALL)

    content = ''.join(
        _underline_to_html(segment) if kind == 'text' else segment
        for kind, segment in _iter_latex_preview_segments(content)
    )
    
    # 处理剩下的 \hspace{...} 为全角空格，但不破坏数学公式内的空白命令
    content = _replace_spacing_outside_math(content)
    
    # 1. 替换被抽离的 \input{... 相关图/...} 命令为渲染图片
    def replace_input_tikz(match):
        input_path = match.group(1)
        if "相关图" in input_path or "图" in input_path:
            full_path = os.path.join(BASE_DIR, input_path)
            if not full_path.endswith('.tex'):
                full_path += '.tex'
                
            png_path = full_path.replace('.tex', '.png')
            
            if os.path.exists(full_path):
                with open(full_path, 'r', encoding='utf-8') as f:
                    tikz_code = f.read()
                
                return _tikz_image_markdown(tikz_code, source_tex_path=full_path, target_png_path=png_path)
            else:
                return f"\n> ⚠️ 找不到引用的图片文件: `{input_path}`\n"
        return match.group(0)

    content = re.sub(r'\\input\{(.*?)\}', replace_input_tikz, content)

    # 2. 替换题库中遗留的内联 TikZ 代码块
    def replace_inline_tikz(match):
        tikz_code = match.group(0)
        return _tikz_image_markdown(tikz_code)

    content = re.sub(
        r'\\begingroup(?:(?!\\endgroup)[\s\S])*?\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}(?:(?!\\endgroup)[\s\S])*?\\endgroup',
        replace_inline_tikz,
        content,
        flags=re.DOTALL,
    )
    content = re.sub(r'\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}', replace_inline_tikz, content, flags=re.DOTALL)
    content = re.sub(
        r'\\begingroup(?:(?!\\endgroup)[\s\S])*?\\(?:draw|coordinate|node|fill|path|foreach)\b(?:(?!\\endgroup)[\s\S])*?\\endgroup',
        replace_inline_tikz,
        content,
        flags=re.DOTALL,
    )

    # TikZ 已转成图片后再剥离 figure/minipage/centerline 等版式外壳，
    # 避免提前删除 TikZ 内部的 \small 等字号命令影响出图效果。
    content = _strip_preview_layout_wrappers(content)

    # TikZ 必须先渲染成图片，再做 Markdown 强调替换。否则图中
    # \textbf{A} 会被改成 **A**，随后被 LaTeX 当成普通星号绘制。
    # 使用括号配平解析，兼容 \textbf{... $\frac{1}{2}$ ...} 这类嵌套写法。
    content = _replace_braced_command(content, 'textbf', lambda inner: '**' + inner + '**')
    content = _replace_braced_command(content, 'emph', lambda inner: '*' + inner + '*')
    content = _replace_braced_command(content, 'textit', lambda inner: '*' + inner + '*')

    # 处理 \circled{} 及电表专用的 \circledV{} / \circledA{}。
    content = _replace_circled_for_preview(content)
    
    # 尝试处理表格 tabular -> Markdown Table
    def replace_tabular(match):
        table_content = match.group(1)
        table_content = table_content.replace(r'\hline', '')
        rows = [row.strip() for row in table_content.split(r'\\') if row.strip()]
        
        md_rows = []
        for row in rows:
            cols = [col.strip() for col in row.split('&')]
            md_row = '| ' + ' | '.join(cols) + ' |'
            md_rows.append(md_row)
            
        if not md_rows:
            return ""
            
        col_count = md_rows[0].count('|') - 1
        if col_count > 0:
            sep_row = '|' + '---|' * col_count
            md_rows.insert(1, sep_row)
            
        return '\n' + '\n'.join(md_rows) + '\n'

    content = re.sub(r'\\begin\{tabular\}\{.*?\}\s*(.*?)\s*\\end\{tabular\}', replace_tabular, content, flags=re.DOTALL)
    
    content = content.replace(r'\begin{center}', '').replace(r'\end{center}', '')

    def _replace_list_env(text, env_name, converter):
        """由内向外转换列表环境，兼容嵌套 enumerate/itemize（避免残留 \\item）。"""
        pattern = re.compile(
            r'\\begin\{' + env_name + r'\}(\[[^\]]*\])?'
            r'((?:(?!\\begin\{' + env_name + r'\}|\\end\{' + env_name + r'\})[\s\S])*?)'
            r'\\end\{' + env_name + r'\}'
        )
        for _ in range(6):
            text, replaced = pattern.subn(lambda m: converter(m.group(1), m.group(2)), text)
            if not replaced:
                break
        return text

    def _enumerate_to_markdown(options, inner):
        if options and re.search(r'\\(?:Alph|alph)\*', options):
            return replace_choices_env(inner)
        inner = re.sub(r'\\item\[(.*?)\]', r'\n\1 ', inner)
        inner = re.sub(r'\\item\s+', r'\n1. ', inner)
        return inner + '\n'

    def _itemize_to_markdown(_options, inner):
        inner = re.sub(r'\\item\[(.*?)\]', r'\n- \1 ', inner)
        inner = re.sub(r'\\item\s+', r'\n- ', inner)
        return inner + '\n'

    content = _replace_list_env(content, 'enumerate', _enumerate_to_markdown)
    content = _replace_list_env(content, 'itemize', _itemize_to_markdown)
    
    def replace_align(match):
        content_inner = match.group(1)
        content_inner = content_inner.replace('&', '')
        lines = content_inner.split(r'\\')
        formatted_lines = []
        for line in lines:
            line = line.strip()
            if line:
                formatted_lines.append(f"$\n{line}\n$")
        return '\n\n'.join(formatted_lines) + '\n'

    content = re.sub(r'\\begin\{align\*?\}(.*?)\\end\{align\*?\}', replace_align, content, flags=re.DOTALL)

    content = re.sub(r'\\\[(.*?)\\\]', r'$$\n\1\n$$', content, flags=re.DOTALL)
    content = re.sub(r'\\\((.*?)\\\)', r'$\1$', content, flags=re.DOTALL)
    content = _replace_latex_linebreaks_outside_math(content)

    def _wrap_boxed_outside_math(s: str) -> str:
        s = s or ""
        out = []
        i = 0
        in_inline = False
        in_display = False
        while i < len(s):
            if s.startswith("$$", i) and not in_inline:
                in_display = not in_display
                out.append("$$")
                i += 2
                continue
            if s[i] == "$" and not in_display:
                in_inline = not in_inline
                out.append("$")
                i += 1
                continue
            if (not in_inline) and (not in_display) and s.startswith(r"\boxed{", i):
                j = i + len(r"\boxed{")
                depth = 1
                while j < len(s) and depth > 0:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    j += 1
                if depth == 0:
                    boxed_full = s[i:j]
                    inner = boxed_full[len(r"\boxed{"):-1].strip()
                    if inner.startswith("$") and inner.endswith("$") and inner.count("$") == 2:
                        inner = inner[1:-1].strip()
                        boxed_full = r"\boxed{" + inner + "}"
                    out.append("$" + boxed_full + "$")
                    i = j
                    continue
            out.append(s[i])
            i += 1
        return "".join(out)

    content = _wrap_boxed_outside_math(content)
    content = re.sub(r"\$\$\s*([。．\.，,；;])", r"$$\n\1", content)
    content = re.sub(r"([。．\.，,；;])\s*\$\$", r"\1\n$$", content)
    content = re.sub(r"[ \t]*\$\$[ \t]*", "$$", content)
    content = re.sub(r"(?<!\n)\$\$", r"\n$$", content)
    content = re.sub(r"\$\$(?!\n)", r"$$\n", content)
    # 兜底：公式环境与未知自定义环境不能把 TeX 源码漏进预览
    content = re.sub(
        r'\\begin\{equation\*?\}([\s\S]*?)\\end\{equation\*?\}',
        lambda m: '$$\n' + m.group(1).strip() + '\n$$',
        content,
    )
    content = re.sub(r'\\begin\{[A-Za-z@*]+\}(?:\[[^\]]*\])?', '\n\n', content)
    content = re.sub(r'\\end\{[A-Za-z@*]+\}', '\n\n', content)
    content = re.sub(r'\\item\s*', '\n- ', content)
    content = content.replace("```", "")
    content = re.sub(r"(?m)^(?:\t+| {4,})", "", content)
    # 版式命令（\hfill 等）可能留下只有全角空格的行，预览中不应显示
    content = re.sub(r"(?m)^[\u3000\s]+$", "", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    
    return content

def generate_filename(year, p_type, name, number, subject):
    return f"{year}-{p_type}-{name}-{number}-{subject}.tex"

def extract_tags_from_fpath(fpath):
    basename = os.path.basename(fpath).replace('.tex', '')
    parts = basename.split('-')
    if len(parts) >= 5:
        return parts[4].split("，")
    return []

def update_file_tags(old_fpath, new_tags_list):
    new_subj_str = "，".join(new_tags_list)
    
    old_filename = os.path.basename(old_fpath)
    old_dir = os.path.dirname(old_fpath)
    old_basename = old_filename.replace('.tex', '')
    
    parts = old_basename.split('-')
    if len(parts) >= 5:
        year = parts[0]
        ptype = parts[1]
        name = parts[2]
        num = parts[3]
    else:
        return False
        
    new_filename = generate_filename(year, ptype, name, num, new_subj_str)
    primary_subj = new_tags_list[0]
    new_save_dir = os.path.join(CHAPTERS_DIR, primary_subj, year)
    ensure_dir(new_save_dir)
    new_fpath = os.path.join(new_save_dir, new_filename)
    
    with open(old_fpath, "r", encoding="utf-8") as f:
        content = f.read()
        
    def replace_subj(match):
        return f"\\begin{{problem}}{{{match.group(1)}}}{{{match.group(2)}}}{{{match.group(3)}}}{{{match.group(4)}}}{{{new_subj_str}}}"
    
    content = re.sub(r'\\begin\{problem\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}', replace_subj, content, count=1)
    
    final_content = extract_and_replace_tikz(content, new_filename, new_save_dir)
    
    atomic_write_text(new_fpath, final_content, backup=os.path.exists(new_fpath))
        
    if os.path.abspath(old_fpath) != os.path.abspath(new_fpath):
        try:
            backup_existing_file(old_fpath)
            os.remove(old_fpath)
        except:
            pass
        old_tikz_dir = os.path.join(old_dir, f"{old_basename} 相关图")
        if os.path.exists(old_tikz_dir):
            import shutil
            try:
                shutil.rmtree(old_tikz_dir)
            except:
                pass
                
    return True

def extract_and_replace_tikz(content, filename, save_dir):
    r"""
    提取LaTeX内容中的TikZ代码块，保存为独立文件到“相关图”文件夹中。
    注意：修改后，主文件中不再替换为 \input{}，而是保留原始的 \begin{tikzpicture}...\end{tikzpicture} 代码，
    但仍会在后台生成对应的独立 .tex 副本，以便后续有其他需要。
    清理多余的旧文件。
    """
    if "相关图" in save_dir or "相关图" in filename:
        return content

    base_name = filename.replace('.tex', '')
    tikz_dir_name = f"{base_name} 相关图"
    tikz_dir_path = os.path.join(save_dir, tikz_dir_name)
    
    pattern = r'(\\input\{[^}]*?相关图/[^}]+\})|(\\begingroup(?:(?!\\endgroup)[\s\S])*?\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}(?:(?!\\endgroup)[\s\S])*?\\endgroup)|(\\begin\{tikzpicture\}.*?\\end\{tikzpicture\})'
    matches = list(re.finditer(pattern, content, re.DOTALL))
    
    if not matches:
        return content
        
    ensure_dir(tikz_dir_path)
    
    new_content = ""
    last_end = 0
    match_count = 0
    
    for match in matches:
        match_count += 1
        tikz_code = ""
        
        if match.group(1):
            input_str = match.group(1)
            m_path = re.search(r'\\input\{([^}]+)\}', input_str)
            if m_path:
                rel_path = m_path.group(1)
                if not rel_path.endswith('.tex'):
                    rel_path += '.tex'
                abs_path = os.path.join(BASE_DIR, rel_path)
                if os.path.exists(abs_path):
                    with open(abs_path, 'r', encoding='utf-8') as f:
                        tikz_code = f.read()
                else:
                    tikz_code = "% ⚠️ 找不到原文件内容\n"
        else:
            tikz_code = match.group(2) or match.group(3)
            
        tikz_filename = f"{base_name} 图{match_count}.tex"
        tikz_file_path = os.path.join(tikz_dir_path, tikz_filename)
        previous = None
        if os.path.isfile(tikz_file_path):
            with open(tikz_file_path, 'r', encoding='utf-8') as diagram_file:
                previous = diagram_file.read()
        if previous != tikz_code:
            atomic_write_text(tikz_file_path, tikz_code, backup=previous is not None)
            
        new_content += content[last_end:match.start()]
        new_content += tikz_code
        last_end = match.end()
        
    new_content += content[last_end:]
    return new_content
