"""xelatex 编译日志采集与结构化诊断工具（Harness v2 契约第 2 节）。

设计约束（见 docs/harness_v2_contract.md §2）：

- 只使用 Python 标准库，不引入任何第三方依赖；
- 所有公共函数对任意输入（None / bytes / 非字符串 / 超长日志 / 乱码）都不得抛异常；
- ``texlogsieve`` / ``chktex`` / ``lacheck`` / ``latexindent`` 仅作为可选外部进程调用，
  未安装或调用失败时优雅降级（返回原始日志 / 空列表）；
- Windows 日志编码按 ``utf-8 → cp936 → gb18030 → latin-1`` 依次容错解码。

category 稳定值（契约 §2 规定的 16 类 + Lead P0 增补 ``missing-semicolon``）：

``missing-math, undefined-control-sequence, no-shape-named, missing-library,
missing-package, missing-file, runaway-argument, extra-brace,
dimension-too-large, undefined-coordinate, pgfkeys-unknown-key,
capacity-exceeded, emergency-stop, font-missing, timeout, unknown``
并额外提供 ``missing-semicolon``（``Did you forget a semi colon?``，TikZ 最常见错误，
由 Lead 2026-10-19 明确要求新增；其余 16 类保持契约稳定值不变）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

MAX_LOG_CHARS = 20000
DEFAULT_SUMMARY_CHARS = 4000

#: 契约 §2 规定的 16 个稳定 category + Lead P0 增补的 missing-semicolon。
ERROR_CATEGORIES = (
    "missing-math",
    "undefined-control-sequence",
    "no-shape-named",
    "missing-library",
    "missing-package",
    "missing-file",
    "missing-semicolon",
    "runaway-argument",
    "extra-brace",
    "dimension-too-large",
    "undefined-coordinate",
    "pgfkeys-unknown-key",
    "capacity-exceeded",
    "emergency-stop",
    "font-missing",
    "timeout",
    "unknown",
)

#: 每个 category 的中文修复建议（契约要求 hint 为中文）。
CATEGORY_HINTS = {
    "missing-math": (
        "数学符号未进入数学模式：把含 ^、_、\\theta、\\circ 等符号的标签用 $...$ 包裹"
        "（例如 $F_N$、$F=10\\,\\mathrm{N}$）；公式内中文改用 \\text{中文}。"
    ),
    "missing-semicolon": (
        "检查补全 \\draw/\\node/\\path/\\fill 语句末尾的 ';'（跨行语句只在真正结束处补，"
        "不要把 ; 加到每一行）；从 l.NNN 指示位置向前找最近一条未结束的 TikZ 语句。"
    ),
    "undefined-control-sequence": (
        "存在未定义命令：优先检查命令拼写与大小写；TikZ 关键字需拼写正确；"
        "若缺宏包/库，改用模板已预加载的宏包与 \\usetikzlibrary 清单（不要新增 \\usepackage）。"
    ),
    "no-shape-named": (
        "引用了未定义的节点/坐标：先用 \\coordinate (A) at (x,y); 或 \\node (A) ... 定义，"
        "再在 \\draw/\\path 中引用 (A)；注意名称大小写与花括号。"
    ),
    "missing-library": (
        "TikZ 库未加载或不存在：确认库名拼写正确；本模板只预载契约 §1.6 列出的库，"
        "未预载的库需要 Lead 同步更新模板后再用 \\(\\usepackage/\\usetikzlibrary\\)。"
    ),
    "missing-package": (
        "宏包/环境未加载：改用模板已加载的宏包（amsmath、amssymb、xcolor、tikz、pgfplots、ctex）；"
        "若为环境未定义（如 circuitikz），改用纯 TikZ 画法，不要新增 \\usepackage。"
    ),
    "missing-file": (
        "找不到被引用的文件：检查 \\input / \\include / \\includegraphics 的路径与文件名"
        "（Windows 下注意大小写与扩展名），或移除该引用。"
    ),
    "runaway-argument": (
        "参数未正确闭合：检查缺失的右花括号 }、未闭合的 $ 或 \\end{...}；"
        "从 l.NNN 指示位置向前逐层查找最近一个未闭合结构。"
    ),
    "extra-brace": (
        "花括号/数学模式不配对：删除多余的 }，或补齐缺失的 { 与 $；"
        "若提示 node label text，请检查 \\node 文本的花括号是否成对。"
    ),
    "dimension-too-large": (
        "尺寸/坐标超出 TeX 上限：把坐标量级缩小到 ±30 左右，统一使用 cm 单位，"
        "检查是否误用了 pt/mm 或比例失控。"
    ),
    "undefined-coordinate": (
        "坐标语法错误或坐标未定义：检查 (x,y)、+(dx,dy)、++(...) 写法；"
        "具名坐标必须先定义后引用，可先改用显式数值坐标。"
    ),
    "pgfkeys-unknown-key": (
        "TikZ 选项/键不存在：检查选项拼写（如 -> 与 -latex、midway 与 pos=0.5）；"
        "自定义 style 必须先用 \\tikzset 定义，或改用模板预定义 style（dot/inc/edge/aux/...）。"
    ),
    "capacity-exceeded": (
        "TeX 容量溢出：通常是 \\foreach 嵌套过深、递归宏或超长路径；"
        "请拆图、简化循环或减少一次性绘制的元素数量。"
    ),
    "emergency-stop": (
        "编译被紧急中止：这通常只是最终表现，请先修复日志中它之前的第一条真正错误"
        "（常见为缺文件、缺宏包或未定义命令）。"
    ),
    "font-missing": (
        "字体缺失或无法加载：中文文案交给 ctex 模板按需加载，不要手写 \\setCJKmainfont；"
        "若确需字体，确认系统已安装（fc-list 可查），或改用 TeX Live 自带字体。"
    ),
    "timeout": (
        "编译超时：图形过于复杂或首次加载字体较慢；可简化图形/减少 \\foreach，"
        "或调大环境变量 TIKZ_COMPILE_TIMEOUT（单位秒）。"
    ),
    "unknown": (
        "未能自动归类：请结合日志上下文中 file:line 与 l.NNN 附近的内容手动定位；"
        "常见原因包括漏分号、花括号不配对、节点文本含非法字符。"
    ),
}

#: 缺分号是 TikZ 最常见错误（Lead P0 新增 missing-semicolon 类别）。
#: 真实日志（实测）: ``./bad2.tex:7: Package tikz Error: Giving up on this path.
#: Did you forget a semi colon?.``（TeX 日志 79 列换行会把 semicolon 劈开）。
_SEMICOLON_RE = re.compile(
    r"(?:Giving up on this path|forget a semi\s*colon|Did you forget a semicolon)",
    re.IGNORECASE,
)

#: node 文本花括号错位，归入 extra-brace 但给出更具体的 hint。
_NODE_BRACE_RE = re.compile(r"node must have a \(possibly empty\) label text", re.IGNORECASE)
_NODE_BRACE_HINT = (
    "\\node 的标签文本花括号不闭合或位置错误：写成 \\node at (x,y) {标签};，"
    "确保 { 与 } 成对且分号在 } 之后。"
)

#: PDF 输出驱动崩溃签名（Windows 实测：xdvipdfmx.exe 在 MSVCR100.dll 中
#: 以 0xC0000417 / STATUS_INVALID_CRUNTIME_PARAMETER 崩溃，xelatex 日志尾部为
#: ``Error -1073740777 (driver return code) generating output; file ... may not be valid.``）。
#: 宽进严出：命中任一特征即视为环境层崩溃，绝不误当成 TikZ 语法错误来「修代码」。
_DRIVER_CRASH_PATTERNS = (
    re.compile(r"driver\s+return\s+code", re.IGNORECASE),
    re.compile(r"generating\s+output\s*[;:：；]", re.IGNORECASE),
    re.compile(r"xdvipdfmx\.exe", re.IGNORECASE),
    re.compile(r"-1073740777|3221226519|0xc0000417", re.IGNORECASE),
    re.compile(r"STATUS_INVALID_CRUNTIME_PARAMETER", re.IGNORECASE),
)

#: 从 ``Error -1073740777 (driver return code) generating output;`` 中提取退出码。
_DRIVER_EXIT_CODE_RE = re.compile(r"error\s+(-?\d+)\s*\(driver\s+return\s+code\)", re.IGNORECASE)
#: 兼容只拿到 Windows 异常码（0xC0000417）的日志/事件文本。
_DRIVER_HEX_CODE_RE = re.compile(r"0x([0-9a-f]{8})\b", re.IGNORECASE)

#: 驱动崩溃的中文可操作建议（契约：不是 TikZ 语法错误，图形代码通常无需修改）。
#: 2026-10-20 minidump 取证 + 现场实验已确证第一原因：TMP/TEMP 不可写（低完整性/权限限制）。
DRIVER_CRASH_HINT = (
    "这是 TeX 的输出驱动（xdvipdfmx.exe）在生成 PDF 时崩溃，不是 TikZ 语法错误，图形源码通常无需修改。"
    "第一位已证实原因：临时目录不可写（TMP/TEMP 被低完整性/权限限制，或 %TEMP% 被安全软件锁定）——"
    "xdvipdfmx 创建「%TMP%\\dvipdfmx.N」失败后未判空即写入，触发 Windows 运行时错误 0xC0000417，任何文档都会踩到。"
    "utils.tikz_ops.compile_tikz 与书稿预览编译已自动给编译子进程注入工作区内的专用可写 TMP/TEMP/TMPDIR，"
    "无需手工修改系统 TMP；若仍反复出现：先重试重新编译，再简化图形"
    "（减少曲线控制点/路径数量、|坐标| 控制在 ±30 内）、避免生僻 Unicode 字符、更新 TeX Live。"
)

#: 消息级分类规则（顺序 = 优先级；越具体的越靠前）。
_MESSAGE_PATTERNS = (
    ("no-shape-named", re.compile(r"No shape named|Unknown shape|shape named .* is known", re.IGNORECASE)),
    (
        "undefined-coordinate",
        re.compile(
            r"Cannot parse this coordinate|Undefined coordinate|Unknown coordinate|coordinate .* undefined",
            re.IGNORECASE,
        ),
    ),
    (
        "missing-library",
        re.compile(
            r"I did not find the tikz library|I do not know the library|I don't know the library"
            r"|tikzlibrary[^\s']*\.code\.tex|library .* not found",
            re.IGNORECASE,
        ),
    ),
    (
        "pgfkeys-unknown-key",
        re.compile(r"I do not know the key|Unknown key|unknown option|key .* (?:unknown|undefined)", re.IGNORECASE),
    ),
    ("dimension-too-large", re.compile(r"Dimension too large", re.IGNORECASE)),
    ("runaway-argument", re.compile(r"Runaway argument|File ended while scanning use of", re.IGNORECASE)),
    (
        "missing-package",
        re.compile(
            r"Environment .* undefined|File `[^']*\.(?:sty|cls)' not found|Package .* not found"
            r"|I can't find file `[^']*\.(?:sty|cls)'",
            re.IGNORECASE,
        ),
    ),
    (
        "missing-file",
        re.compile(r"File `[^']*' not found|I can't find file `|LaTeX Error: File .* not found", re.IGNORECASE),
    ),
    (
        "font-missing",
        re.compile(
            r"not loadable|Metric \(TFM\)|fontspec|Font .* (?:not found|cannot be found|does not contain)"
            r"|installed font not found",
            re.IGNORECASE,
        ),
    ),
    ("capacity-exceeded", re.compile(r"TeX capacity exceeded|capacity exceeded", re.IGNORECASE)),
    ("emergency-stop", re.compile(r"Emergency stop", re.IGNORECASE)),
    (
        "extra-brace",
        re.compile(
            r"Too many \}|Extra \}|forgotten \\endgroup|forgotten \$|Missing \} inserted"
            r"|Missing close brace|Missing \\end\{|ended by",
            re.IGNORECASE,
        ),
    ),
    (
        "missing-math",
        re.compile(
            r"Missing \$ inserted|\$ inserted|Missing math mode|math mode|Math formula deleted"
            r"|Display math should end",
            re.IGNORECASE,
        ),
    ),
    ("undefined-control-sequence", re.compile(r"Undefined control sequence|Undefined command", re.IGNORECASE)),
    ("timeout", re.compile(r"\bTIMEOUT(?:_\d+(?:\.\d+)?S?)?\b|\btimed? out\b|Time limit exceeded", re.IGNORECASE)),
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_FATAL_SUMMARY_RE = re.compile(r"^!\s*==>")
_CLASSIC_RE = re.compile(r"^!\s*(?!==>)(?P<msg>\S.*)$")
_FILE_LINE_RE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\r\n:]*?\.(?:tex|sty|cls|ltx|cfg|def|pgf|tikz|code))"
    r":(?P<line>\d+):\s*(?P<msg>.*)$",
    re.IGNORECASE,
)
_LNN_RE = re.compile(r"^l\.(\d+)")
_PROGRESS_RE = re.compile(r"^(?:[|/\-\\]\s*)+$")
_UUID_FILE_RE = re.compile(r"\b[0-9a-fA-F]{16,}\.(?P<ext>tex|log|aux|pdf|png)\b")
_PATH_WITH_EXT_RE = re.compile(
    r"(?:[A-Za-z]:)?(?:[\\/][^\s\\/:*?\"<>|]+)*\.?[\\/]"
    r"([^\s\\/:*?\"<>|]+\.(?:tex|log|aux|pdf|png|sty|cls|def|cfg))"
)

#: 出现在错误块附近、但不属于错误上下文的 TeX 样板行。
_CONTEXT_NOISE_PREFIXES = (
    "See the ",
    "Type  H",
    "Type H",
    "Type X",
    "or enter new name",
    "Enter file name",
    "For immediate help",
    "For additional information",
    "Here is how much",
    "...",
    "==>",
    "<recently read>",
    "<to be read again>",
    "<argument>",
    "<inserted text>",
    "<*>",
    "No pages of output",
    "Transcript written",
    "Overfull \\",
    "Underfull \\",
    "LaTeX Warning",
    "Package Warning",
)

_EXTERNAL_TOOL_NAMES = ("texlogsieve", "chktex", "lacheck", "latexindent")

#: 本机 TeX Live 兜底目录：PATH 未配置时仍能探测到工具（Lead 2026-10-19 情报）。
_TEXLIVE_FALLBACK_DIR = r"D:\texlive\2026\bin\windows"

#: chktex 默认关闭，需显式打开（Lead 约定；issue 只作 info 级，不拉低评分）。
_CHKTEX_ENV_FLAG = "PI_AGENT_CHKTEX"


def _find_tool(name):
    """探测外部工具：优先 shutil.which，失败则兜底本机 TeX Live 目录。"""
    try:
        path = shutil.which(name)
        if path:
            return path
    except Exception:  # pragma: no cover - 防御
        pass
    try:
        for candidate in (name, name + ".exe"):
            path = os.path.join(_TEXLIVE_FALLBACK_DIR, candidate)
            if os.path.isfile(path):
                return path
    except Exception:  # pragma: no cover - 防御
        pass
    return None


# ---------------------------------------------------------------------------
# 基础：解码 / 清洗 / 截断
# ---------------------------------------------------------------------------


def _decode_bytes(data):
    """按 utf-8 → cp936 → gb18030 → latin-1 依次尝试解码 Windows 下混合编码日志。"""
    for encoding in ("utf-8", "cp936", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return data.decode("latin-1", "replace")
    except Exception:  # pragma: no cover - 理论不可达
        return ""


def _to_text(raw):
    """把任意输入安全地转成文本；绝不抛异常。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray, memoryview)):
        try:
            return _decode_bytes(bytes(raw))
        except Exception:  # pragma: no cover - 防御
            return ""
    try:
        return str(raw)
    except Exception:  # pragma: no cover - 防御
        return ""


def _normalize_text(text, strip_trailing=True):
    """统一换行/去 ANSI 控制序列/去控制字符/压缩连续空行。

    ``strip_trailing=False`` 时保留行尾空格：解析 79 列换行消息时，行尾是否有空格
    决定了上下两行拼接时是否需要补空格（TeX 在单词中间换行不会保留空格）。
    """
    if not isinstance(text, str):
        text = _to_text(text)
    try:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
        text = _ANSI_RE.sub("", text)
        text = _CONTROL_RE.sub("", text)
        normalized = []
        blank_run = 0
        for line in text.split("\n"):
            if strip_trailing:
                line = line.rstrip()
            if line.strip():
                blank_run = 0
                normalized.append(line)
            else:
                blank_run += 1
                if blank_run <= 1:
                    normalized.append("")
        return "\n".join(normalized).strip("\n")
    except Exception:  # pragma: no cover - 防御
        return ""


_IMPORTANT_LOG_LINE_RE = re.compile(
    r"(?:^!|(?:\.tex|main|tikz):\d+:|^l\.\d+:|Emergency stop|No pages of output|"
    r"Output written|Runaway argument|Undefined control sequence|Missing \$|"
    r"Dimension too large|TeX capacity exceeded|Did you forget a semi)"
)


def _truncate(text, max_chars):
    """截断到 <= max_chars；超长时保留头/尾，并**优先保留中段的致命错误行**。

    Harness v2.1（F11）：仅保留头+尾会在超大日志中丢掉中段真正的 `!`/`file:line:`
    致命错误，导致 compile.* 漏判；这里把中段匹配致命错误模式的行额外抽取保留。
    """
    if not isinstance(text, str):
        text = _to_text(text)
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = MAX_LOG_CHARS
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker = "\n... [日志已截断，原始长度 %d 字符] ...\n" % len(text)
    if len(marker) >= limit:
        return text[:limit]
    budget = limit - len(marker)
    head_len = min(4000, max(0, budget // 3))
    middle_budget = max(0, min(3200, budget // 3))
    important: list = []
    important_len = 0
    for line in text[head_len:].splitlines():
        if not _IMPORTANT_LOG_LINE_RE.search(line):
            continue
        if important_len + len(line) + 1 > middle_budget:
            break
        important.append(line)
        important_len += len(line) + 1
    important_block = "\n".join(important)
    tail_len = max(0, budget - head_len - important_len - 80)
    tail = text[-tail_len:] if tail_len > 0 else ""
    parts = [text[:head_len], marker]
    if important_block:
        parts.append("... [中段保留的致命错误行] ...\n")
        parts.append(important_block)
        parts.append("\n... [尾部日志] ...\n")
    parts.append(tail)
    result = "".join(parts)
    return result[:limit]


def _tail(text, limit):
    if not isinstance(text, str):
        text = _to_text(text)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 500
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1):] if limit > 1 else text[-1:]


def _strip_progress_noise(text):
    """移除 texlogsieve 心跳字符（/ - \\ |）单独成行的噪声。"""
    try:
        lines = [line for line in text.split("\n") if not _PROGRESS_RE.match(line.strip())]
        return "\n".join(lines)
    except Exception:  # pragma: no cover - 防御
        return text


def _strip_path_noise(text):
    """摘要场景：把临时目录绝对路径/uuid 文件名替换成短文件名，降低回灌噪声。"""
    if not isinstance(text, str) or not text:
        return ""
    try:
        text = _UUID_FILE_RE.sub(r"tikz.\g<ext>", text)
        text = _PATH_WITH_EXT_RE.sub(lambda match: match.group(1), text)
        return text
    except Exception:  # pragma: no cover - 防御
        return text


def clean_tex_log(raw: str, max_chars: int = 20000) -> str:
    """清洗 xelatex 原始日志并截断到 ``max_chars`` 字符。

    - 兼容 ``str`` / ``bytes`` / ``None`` / 其它对象；
    - 统一换行（CRLF/CR → LF），去 ANSI 转义与控制字符，压缩连续空行；
    - 超长时保留头 + 尾（尾部通常包含真正的致命错误）。
    """
    try:
        text = _normalize_text(_to_text(raw))
        text = _strip_progress_noise(text)
        return _truncate(text, max_chars)
    except Exception:  # pragma: no cover - 防御
        try:
            return _truncate(_to_text(raw), max_chars)
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# 解析：错误块 / 错误列表 / 分类
# ---------------------------------------------------------------------------


def _block_start(line):
    """判断一行是否是错误块起点。

    返回 ``("file", match)`` / ``("classic", match)`` / ``("runaway", None)`` / ``None``。
    """
    if not line:
        return None
    stripped = line.strip()
    if not stripped or _FATAL_SUMMARY_RE.match(stripped):
        return None
    match = _FILE_LINE_RE.match(stripped)
    if match and match.group("msg").strip():
        return ("file", match)
    if stripped.startswith("!"):
        match = _CLASSIC_RE.match(stripped)
        if match and match.group("msg").strip():
            return ("classic", match)
    # TeX 的 "Runaway argument?" 行本身不带 "!"，真正的 "!" 在其后的 "File ended" 行
    if stripped.startswith("Runaway argument"):
        return ("runaway", None)
    return None


def _unwrap_message(lines, index, first_message, kind=None):
    """处理 TeX 日志 79 列换行：把错误消息的续行拼回来。

    返回 ``(extra_text, message_end_index)``。TeX 在单词中间换行时不保留空格，
    行尾有空格才是真正的词间换行——因此用「上一行是否以空格结尾」决定拼接分隔符。
    """
    chunks = []
    previous_line = lines[index]
    previous_text = first_message
    j = index + 1
    while j < len(lines) and j <= index + 6:
        raw_line = lines[j]
        stripped = raw_line.strip()
        if not stripped:
            break
        if _block_start(raw_line) is not None:
            break
        if stripped.startswith(_CONTEXT_NOISE_PREFIXES):
            break
        # Runaway argument? 的下一行是未闭合的参数片段，即使无缩进也属于消息
        if kind == "runaway":
            chunks.append(" " + stripped)
            previous_line = raw_line
            previous_text = stripped
            j += 1
            continue
        # 前一行已经是完整句子，且本行没有缩进 → 视为上下文而非消息续行
        if previous_text.rstrip().endswith((".", "?", "!", ":", "。")) and not raw_line.startswith((" ", "\t")):
            break
        separator = " " if previous_line.endswith((" ", "\t")) else ""
        chunks.append(separator + stripped)
        previous_line = raw_line
        previous_text = stripped
        j += 1
    return "".join(chunks), j


def _clean_message(message):
    message = re.sub(r"\s+", " ", message or "").strip()
    message = re.sub(r"^!+\s*", "", message)
    # TeX 79 列换行可能把关键词劈成两半，顺手粘回去，便于回灌 LLM
    message = re.sub(r"\bsemi\s+colon\b", "semicolon", message, flags=re.IGNORECASE)
    if len(message) > 500:
        message = message[:497].rstrip() + "..."
    return message


def _find_line_number(lines, index, next_index):
    """经典 ``!`` 格式错误通常用随后的 ``l.NNN`` 标明源码行号。"""
    limit = min(len(lines), next_index, index + 16)
    for k in range(index + 1, limit):
        stripped = lines[k].strip()
        if not stripped:
            continue
        match = _LNN_RE.match(stripped)
        if match:
            return int(match.group(1))
    return None


def _is_context_noise(stripped):
    return stripped.startswith(_CONTEXT_NOISE_PREFIXES)


def _build_context(lines, index, message_end, next_index):
    selected = []
    for k in range(max(0, index - 2), index):
        stripped = lines[k].strip()
        if stripped and not _is_context_noise(stripped):
            selected.append(lines[k].rstrip())
    selected.append(lines[index].rstrip())
    for k in range(index + 1, message_end):
        if k < len(lines):
            selected.append(lines[k].rstrip())
    limit = min(len(lines), next_index, message_end + 12)
    k = message_end
    while k < limit:
        stripped = lines[k].strip()
        if stripped and not _is_context_noise(stripped):
            selected.append(lines[k].rstrip())
        k += 1
    context = "\n".join(selected)
    context = _CONTROL_RE.sub("", context)
    if len(context) > 800:
        context = context[:799] + "…"
    return context


def _classify_message(message):
    """消息级分类；返回 (category, 中文 hint)。"""
    text = message or ""
    if _NODE_BRACE_RE.search(text):
        return "extra-brace", _NODE_BRACE_HINT
    if _SEMICOLON_RE.search(text):
        return "missing-semicolon", CATEGORY_HINTS["missing-semicolon"]
    if any(pattern.search(text) for pattern in _DRIVER_CRASH_PATTERNS):
        # 契约要求 16 类稳定 category 不变：驱动崩溃仍记为 unknown，
        # 但 hint 必须明确「不是 TikZ 语法错误」并给出可操作建议。
        return "unknown", DRIVER_CRASH_HINT
    for category, pattern in _MESSAGE_PATTERNS:
        if pattern.search(text):
            return category, CATEGORY_HINTS[category]
    return "unknown", CATEGORY_HINTS["unknown"]


def _parse_error_blocks(text):
    """把清洗后的日志解析为错误块列表。

    内部 dict 含契约 §2 的 5 个字段，外加私有 ``_start``（日志行下标，仅用于去重）。
    """
    lines = text.split("\n")
    total = len(lines)
    starts = []
    for index, line in enumerate(lines):
        hit = _block_start(line)
        if hit is not None:
            starts.append((index, hit[0], hit[1]))
    if not starts:
        return []

    blocks = []
    for position, (index, kind, match) in enumerate(starts):
        next_index = starts[position + 1][0] if position + 1 < len(starts) else total
        if kind == "runaway":
            first_message = "Runaway argument?"
            line_number = None
        else:
            first_message = match.group("msg").strip()
            line_number = int(match.group("line")) if kind == "file" else None
        extra_text, message_end = _unwrap_message(lines, index, first_message, kind=kind)
        full_message = _clean_message(first_message + extra_text)
        if not full_message:
            continue
        category, hint = _classify_message(full_message)
        if line_number is None:
            line_number = _find_line_number(lines, index, next_index)
        blocks.append(
            {
                "category": category,
                "line": line_number,
                "message": full_message,
                "hint": hint,
                "context": _build_context(lines, index, message_end, next_index),
                "_start": index,
            }
        )
    # Runaway argument? 的后续 "! File ended while scanning use of ..." 是同一错误的
    # 后半段，避免重复计数。
    filtered = []
    for block in blocks:
        if (
            block["category"] in ("runaway-argument", "unknown", "emergency-stop")
            and block["message"].lower().startswith(("file ended while scanning", "runaway argument"))
            and filtered
            and filtered[-1]["category"] == "runaway-argument"
            and block["_start"] - filtered[-1]["_start"] <= 6
        ):
            # 同一错误的 "File ended" 后半段：补上它解析到的 l.NNN 行号
            if filtered[-1]["line"] is None and isinstance(block.get("line"), int):
                filtered[-1]["line"] = block["line"]
            continue
        filtered.append(block)
    return filtered


def extract_tex_errors(raw: str) -> list[dict]:
    """从 xelatex 原始日志中提取结构化错误列表。

    返回项固定为 ``{"category", "line", "message", "hint", "context"}``：

    - ``category``：契约 §2 的 16 个稳定类别之一，或 Lead P0 增补的 ``missing-semicolon``；
    - ``line``：日志中的行号（``int``）或 ``None``；
    - ``message``：单行化的真实错误消息；
    - ``hint``：中文修复建议；
    - ``context``：日志上下文（含 ``l.NNN`` 源码行），最长 800 字符。

    同时兼容 ``-file-line-error`` 的 ``file:line: message`` 格式与经典的
    ``! LaTeX Error: ...`` / ``! Package pgf Error: ...`` / ``l.NNN`` 格式。
    任何输入都不会抛异常，无法解析时返回 ``[]``。
    """
    try:
        text = _normalize_text(_to_text(raw), strip_trailing=False)
        if not text.strip():
            return []
        # 超长日志只保留尾部 2M 字符，避免病态输入拖垮解析（错误通常在尾部）
        if len(text) > 2_000_000:
            text = text[-2_000_000:]
        blocks = _parse_error_blocks(text)
        if not blocks:
            # 成功日志不应被当作错误；只有「像失败/特定短标记」的不可解析文本才兜底报错
            category, hint = _classify_message(_truncate(text, 4000))
            looks_like_success = "Output written on" in text and "No pages of output" not in text
            if category == "unknown" and (looks_like_success or len(text) > 1200):
                return []
            first_line = next((line.strip() for line in text.split("\n") if line.strip()), "")
            message = _clean_message(_one_line(first_line, 200))
            if any(pattern.search(text) for pattern in _DRIVER_CRASH_PATTERNS):
                # 驱动崩溃没有 `!` LaTeX 错误行，首行往往只是进度/横幅文本；
                # 这里把 message 换成明确说明（category 仍为稳定值 unknown）。
                message = _driver_crash_message(text)
            return [
                {
                    "category": category,
                    "line": None,
                    "message": message,
                    "hint": hint,
                    "context": _tail(text, 300),
                }
            ]
        results = []
        seen = set()
        for block in blocks:
            key = (block["category"], block["line"], block["message"][:120])
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "category": block["category"],
                    "line": block["line"],
                    "message": block["message"],
                    "hint": block["hint"],
                    "context": block["context"],
                }
            )
            if len(results) >= 20:
                break
        return results
    except Exception:  # pragma: no cover - 防御：契约要求绝不抛异常
        return []


def classify_tex_error(raw: str) -> tuple[str, str]:
    """把日志（或单条错误消息）归类为 category 并给出中文修复建议。

    有结构化错误块时优先返回**根因**类别（跳过 ``emergency-stop`` / ``unknown``
    这类后果性条目）；没有错误块时退化为全文正则扫描。
    """
    try:
        text = _normalize_text(_to_text(raw), strip_trailing=False)
        if not text.strip():
            return "unknown", "日志为空，无法分类；请确认 xelatex 已安装且命令包含 -file-line-error。"
        blocks = _parse_error_blocks(text)
        if blocks:
            root_blocks = [b for b in blocks if b["category"] not in ("emergency-stop", "unknown")]
            chosen = root_blocks[0] if root_blocks else blocks[0]
            return chosen["category"], chosen["hint"]
        category, hint = _classify_message(_truncate(text, 4000))
        return category, hint
    except Exception:  # pragma: no cover - 防御
        return "unknown", CATEGORY_HINTS["unknown"]


# ---------------------------------------------------------------------------
# 输出驱动崩溃检测（DRIVER_CRASH）：TeX 排版完成、生成 PDF 时输出驱动崩溃
# ---------------------------------------------------------------------------


def _normalize_driver_exit_code(value):
    """把 Windows 异常退出码归一化为有符号 int（3221226519 → -1073740777）。"""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    if code > 0x7FFFFFFF:
        code -= 0x100000000
    return code


def _driver_exit_code_from_text(text):
    """从日志文本提取有符号驱动退出码；提取不到返回 None。"""
    match = _DRIVER_EXIT_CODE_RE.search(text or "")
    if match:
        return _normalize_driver_exit_code(match.group(1))
    hex_match = _DRIVER_HEX_CODE_RE.search(text or "")
    if hex_match:
        try:
            return _normalize_driver_exit_code(int(hex_match.group(1), 16))
        except (TypeError, ValueError):
            return None
    return None


def _driver_crash_message(text):
    """生成驱动崩溃的单行明确消息（不是 TikZ 语法错误）。"""
    exit_code = _driver_exit_code_from_text(text)
    match = _DRIVER_EXIT_CODE_RE.search(text or "")
    detail = _one_line(match.group(0), 200) if match else "日志命中输出驱动崩溃签名"
    if exit_code is not None:
        return (
            "PDF 输出驱动 xdvipdfmx.exe 崩溃（Windows 运行时错误，驱动退出码 %d）：%s；"
            "TeX 已排版完成，这不是 TikZ 语法错误。" % (exit_code, detail)
        )
    return (
        "PDF 输出驱动 xdvipdfmx.exe 崩溃（Windows 运行时错误）：%s；"
        "TeX 已排版完成，这不是 TikZ 语法错误。" % detail
    )


def detect_driver_crash(raw: str) -> dict | None:
    """检测「TeX 排版完成、PDF 输出驱动崩溃」签名（宽进严出）。

    典型日志（Windows 实测）：:

        Error -1073740777 (driver return code) generating output;
        file tikz.pdf may not be valid.

    对应 Windows 事件日志为 xdvipdfmx.exe 在 MSVCR100.dll 中 0xC0000417
    （STATUS_INVALID_CRUNTIME_PARAMETER）崩溃。这类失败**不是** TikZ 语法错误，
    上层应重试而不是让模型改代码。

    返回 ``{"error_kind": "DRIVER_CRASH", "exit_code": int | None,
    "message": str, "hint": str}``；未命中返回 ``None``。任何输入都不抛异常。
    ``exit_code`` 为有符号驱动退出码（如 ``-1073740777``），无法解析时为 ``None``。
    """
    try:
        text = clean_tex_log(raw, max_chars=MAX_LOG_CHARS)
        if not text:
            return None
        if not any(pattern.search(text) for pattern in _DRIVER_CRASH_PATTERNS):
            return None
        return {
            "error_kind": "DRIVER_CRASH",
            "exit_code": _driver_exit_code_from_text(text),
            "message": _driver_crash_message(text),
            "hint": DRIVER_CRASH_HINT,
        }
    except Exception:  # pragma: no cover - 防御：契约要求绝不抛异常
        return None


# ---------------------------------------------------------------------------
# 摘要 / 诊断
# ---------------------------------------------------------------------------


def _source_label(source):
    if not source:
        return ""
    try:
        label = str(source).strip()
        if "\\" in label or "/" in label:
            label = os.path.basename(label)
        return "[%s] " % label if label else ""
    except Exception:  # pragma: no cover - 防御
        return ""


def _one_line(text, limit=400):
    if not isinstance(text, str):
        text = _to_text(text)
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def summarize_tex_log(raw: str, source: str | None = None, max_chars: int = 4000) -> str:
    """生成适合回灌给 LLM 的中文短摘要（<= ``max_chars`` 字符）。

    摘要包含：错误数量、行号、category、真实消息、中文建议，以及第一个错误的
    日志上下文；默认剥离临时目录绝对路径与 uuid 文件名等噪声。
    """
    try:
        # 从原始日志解析（保留行尾空格，79 列换行拼接更准确）；清洗文本用于尾部兜底
        errors = extract_tex_errors(raw)
        crash = detect_driver_crash(raw)
        text = clean_tex_log(raw, max_chars=MAX_LOG_CHARS)
        label = _source_label(source)
        lines = []
        real_errors = [error for error in errors if error.get("category") not in ("unknown", "emergency-stop")]
        if crash and not real_errors:
            # 输出驱动崩溃：日志里没有 `!` LaTeX 错误，必须给出环境故障结论而不是
            # 「未从日志中解析出明确的 LaTeX 错误」这类不可操作信息。
            exit_code = crash.get("exit_code")
            code_text = "，驱动退出码 %d" % exit_code if isinstance(exit_code, int) else ""
            lines.append(
                "%s编译失败：TeX 已排版完成，但生成 PDF 的输出驱动 xdvipdfmx.exe 崩溃"
                "（Windows 运行时错误%s），不是 TikZ 语法错误。" % (label, code_text)
            )
            lines.append("建议：%s" % crash.get("hint", DRIVER_CRASH_HINT))
            tail = _tail(text, 500)
            if tail.strip():
                lines.append("日志末尾（已清洗）：")
                lines.append(_strip_path_noise(tail))
            return _truncate("\n".join(lines), max_chars)
        if not errors:
            lines.append("%s未从日志中解析出明确的 LaTeX 错误。" % label)
            tail = _tail(text, 600)
            if tail.strip():
                lines.append("日志末尾（已清洗）：")
                lines.append(_strip_path_noise(tail))
            lines.append("建议：确认使用 -file-line-error 编译；若日志为空请检查 xelatex 是否可用。")
            return _truncate("\n".join(lines), max_chars)
        lines.append("%s编译失败：解析到 %d 个错误。" % (label, len(errors)))
        for number, error in enumerate(errors[:8], 1):
            line = error.get("line")
            position = "第 %d 行" % line if isinstance(line, int) else "行号未知"
            message = _strip_path_noise(_one_line(error.get("message", ""), 400))
            lines.append("%d. %s［%s］%s" % (number, position, error.get("category", "unknown"), message))
            lines.append("   建议：%s" % error.get("hint", CATEGORY_HINTS["unknown"]))
        if len(errors) > 8:
            lines.append("……另有 %d 个错误未展开。" % (len(errors) - 8))
        context = _strip_path_noise(errors[0].get("context") or "")
        if context.strip():
            lines.append("首个错误的日志上下文：")
            lines.append(_tail(context, 500))
        return _truncate("\n".join(lines), max_chars)
    except Exception:  # pragma: no cover - 防御
        try:
            return _truncate(_to_text(raw), max_chars)
        except Exception:
            return ""


def diagnose_tex_error(raw: str) -> str:
    """生成中文多行编译诊断（覆盖契约 16 类 + missing-semicolon 的分类与修复建议）。

    输出结构：错误计数 + 主类别 → 每条错误的「类别 / 行号 / 消息 / 上下文 /
    中文修复建议」→ 可选外部工具可用性说明。
    """
    try:
        # 从原始日志解析（保留行尾空格）；清洗文本用于「无法解析」时的尾部兜底
        errors = extract_tex_errors(raw)
        crash = detect_driver_crash(raw)
        text = clean_tex_log(raw, max_chars=MAX_LOG_CHARS)
        real_errors = [error for error in errors if error.get("category") not in ("unknown", "emergency-stop")]
        if crash and not real_errors:
            exit_code = crash.get("exit_code")
            lines = [
                "编译诊断：DRIVER_CRASH —— TeX 已排版完成，但生成 PDF 的输出驱动 xdvipdfmx.exe 崩溃"
                "（Windows 运行时错误%s）。这不是 TikZ 语法错误，图形源码通常无需修改。"
                % ("，驱动退出码 %d" % exit_code if isinstance(exit_code, int) else ""),
                "判断依据：日志中没有以 `!` 开头的 LaTeX 错误，仅含 driver return code / "
                "generating output 等输出驱动崩溃签名。",
                "修复建议：%s" % crash.get("hint", DRIVER_CRASH_HINT),
                "可直接操作：重新编译即可（utils.tikz_ops.compile_tikz 已对 DRIVER_CRASH 自动重试 1 次，"
                "可用环境变量 TIKZ_COMPILE_DRIVER_RETRIES 调整，范围 0..3）。",
            ]
            if not text.strip():
                lines.append("日志为空：请确认 xelatex 已安装且编译命令包含 -file-line-error。")
            else:
                lines.append("日志末尾（已清洗）：")
                lines.append(_tail(_strip_path_noise(text), 800))
            return _truncate("\n".join(lines), 12000)
        if not errors:
            if not text.strip():
                return (
                    "编译诊断：日志为空。请确认已安装 XeLaTeX（shutil.which('xelatex')）"
                    "且编译命令包含 -file-line-error。"
                )
            return (
                "编译诊断：未能从日志中识别结构化错误类别。\n"
                "日志末尾（已清洗）：\n%s\n"
                "建议：检查日志末尾以 file:line 或 ! 开头的行；若为乱码请确认编码。"
                % _tail(_strip_path_noise(text), 800)
            )
        root_blocks = [b for b in errors if b.get("category") not in ("emergency-stop", "unknown")]
        primary = (root_blocks[0] if root_blocks else errors[0]).get("category", "unknown")
        lines = ["编译诊断：共发现 %d 个错误；主类别：%s。" % (len(errors), primary)]
        for number, error in enumerate(errors[:8], 1):
            line = error.get("line")
            position = "第 %d 行" % line if isinstance(line, int) else "行号未知"
            lines.append("%d. [%s] %s" % (number, error.get("category", "unknown"), position))
            lines.append("   消息：%s" % _one_line(error.get("message", ""), 400))
            context = _one_line(error.get("context") or "", 300)
            if context:
                lines.append("   日志上下文：%s" % context)
            lines.append("   修复建议：%s" % error.get("hint", CATEGORY_HINTS["unknown"]))
        if len(errors) > 8:
            lines.append("……另有 %d 个错误未展开（可用 summarize_tex_log 查看摘要）。" % (len(errors) - 8))
        tools = external_tools()
        available = [name for name, path in tools.items() if path]
        if available:
            lines.append("可选增强：检测到外部工具 %s，可用 filter_with_texlogsieve / run_chktex_lint 获取更多线索。" % "、".join(available))
        else:
            lines.append("可选增强：未检测到 texlogsieve/chktex/lacheck/latexindent，已使用内置解析（不影响基础诊断）。")
        return _truncate("\n".join(lines), 12000)
    except Exception as exc:  # pragma: no cover - 防御
        return "编译诊断：日志解析内部异常（已降级）：%s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# 可选外部工具（texlogsieve / chktex / lacheck / latexindent）
# ---------------------------------------------------------------------------


def external_tools() -> dict[str, str | None]:
    """探测可选外部 TeX 工具；返回 ``{name: path|None}``，绝不抛异常。

    探测顺序：``shutil.which`` → 本机 TeX Live 兜底目录
    ``D:\\texlive\\2026\\bin\\windows``（PATH 未配置时仍可发现）。
    """
    tools = {}
    for name in _EXTERNAL_TOOL_NAMES:
        try:
            tools[name] = _find_tool(name)
        except Exception:  # pragma: no cover - 防御
            tools[name] = None
    return tools


def filter_with_texlogsieve(raw: str) -> str:
    """用 texlogsieve 过滤日志；不可用或调用失败时原样返回输入文本。

    - 按 Lead 约定调用 ``texlogsieve --no-color --no-heartbeat --only-summary``，
      通过 stdin/stdout（不落地临时文件、不复制 GPL 工具源码）；
    - texlogsieve 好坏日志退出码都是 0，这里不依赖退出码；超时（10s）/异常/空输出
      都回退为原文；``--only-summary`` 无输出时再退回不加该参数的普通过滤；
    - 日志本身的成败判定应解析返回摘要中的 "There were errors during processing"
      与 "No important messages to show"，见 docs/harness_v2_contract.md。
    """
    original = _to_text(raw)
    try:
        executable = _find_tool("texlogsieve")
        if not executable or not original.strip():
            return original
        payload = original
        if len(payload) > 2_000_000:
            payload = payload[-2_000_000:]
        data = payload.encode("utf-8", "replace")
        for extra_args in (["--only-summary"], []):
            try:
                completed = subprocess.run(
                    [executable, "--no-color", "--no-heartbeat"] + extra_args,
                    input=data,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=10.0,
                )
            except Exception:
                continue
            filtered = _strip_progress_noise(_normalize_text(_decode_bytes(completed.stdout or b"")))
            if filtered.strip():
                return filtered
        return original
    except Exception:  # pragma: no cover - 外部工具不可用/超时/权限问题
        return original


_CHKTEX_MACHINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<column>\d+):(?P<code>\d+):(?P<message>.*)$",
    re.MULTILINE,
)
_CHKTEX_VERBOSE_RE = re.compile(
    r"^(?P<kind>Warning|Error)\s+(?P<code>\d+)\s+in\s+.+?\s+line\s+(?P<line>\d+)\s*:\s*(?P<message>.*)$",
    re.MULTILINE | re.IGNORECASE,
)

_CHKTEX_HINTS = {
    1: "命令后存在多余空格，可能改变输出间距。",
    2: "检查此处的空格/换行是否符合预期。",
    5: "命令后的空格会被当作文本输出，建议删除。",
    8: "括号或引用格式建议规范化。",
    9: "引号应使用 ``...''，不要使用英文直引号 \"...\"。",
    13: "逗号/句号后建议补空格。",
    17: "数字与单位之间建议使用 \\, 或空格。",
    18: "引号格式：英文引号建议用 ``...''。",
    19: "数学定界符建议用 \\[...\\] 或 \\(...\\)，不要用 $$...$$。",
    26: "检查 \\begin/\\end 是否配对。",
    28: "检查 \\label/\\ref 周围的空格。",
    31: "检查是否直接使用了未转义的 _ 或 ^。",
    36: "节标题后建议补空格/换行。",
    39: "\\end{document} 之后不应再有内容。",
    40: "检查数学模式中 \\\\ 换行的用法。",
    45: "建议用 \\[...\\] 代替 $$...$$。",
}


def _parse_chktex_output(text):
    results = []
    seen = set()
    for match in _CHKTEX_MACHINE_RE.finditer(text or ""):
        code = int(match.group("code"))
        line = int(match.group("line"))
        try:
            column = int(match.group("column"))
        except (TypeError, ValueError):
            column = None
        message = match.group("message").strip()
        key = (code, line, message)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "tool": "chktex",
                "severity": "info",
                "code": code,
                "line": line,
                "column": column,
                "message": message,
                "hint": _CHKTEX_HINTS.get(code, "ChkTeX 静态 lint 告警（非致命，建议人工核对）。"),
                "context": "",
            }
        )
        if len(results) >= 100:
            return results
    if results:
        return results
    # 兜底：兼容 chktex 默认 verbose 输出格式
    for match in _CHKTEX_VERBOSE_RE.finditer(text or ""):
        code = int(match.group("code"))
        line = int(match.group("line"))
        message = match.group("message").strip()
        key = (code, line, message)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "tool": "chktex",
                "severity": "info",
                "code": code,
                "line": line,
                "column": None,
                "message": message,
                "hint": _CHKTEX_HINTS.get(code, "ChkTeX 静态 lint 告警（非致命，建议人工核对）。"),
                "context": "",
            }
        )
        if len(results) >= 100:
            break
    return results


def run_chktex_lint(tex_source: str, timeout: int = 20) -> list[dict]:
    """用 chktex 对 LaTeX/TikZ 源码做可选静态 lint。

    - 默认关闭：仅当环境变量 ``PI_AGENT_CHKTEX=1`` 时启用（Lead 约定）；
    - 通过 stdin 传入源码（``-p tikz_code.tex`` 提供伪文件名），解析 ``-v0``
      机器可读输出（告警在 stderr，本函数合并 stdout+stderr 后解析）；
    - 返回 issue 的 ``severity`` 固定为 ``"info"``，只作提示、不参与评分扣分；
    - chktex 未安装、超时或调用失败时返回 ``[]``，绝不抛异常。
    """
    try:
        if str(os.environ.get(_CHKTEX_ENV_FLAG, "")).strip().lower() not in ("1", "true", "yes", "on"):
            return []
        executable = _find_tool("chktex")
        if not executable:
            return []
        source = _to_text(tex_source)
        if not source.strip():
            return []
        try:
            timeout_seconds = max(1.0, min(float(timeout), 120.0))
        except (TypeError, ValueError):
            timeout_seconds = 20.0
        completed = subprocess.run(
            [executable, "-q", "-v0", "-p", "tikz_code.tex"],
            input=source.encode("utf-8", "replace"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        raw_output = (completed.stdout or b"") + b"\n" + (completed.stderr or b"")
        return _parse_chktex_output(_normalize_text(_decode_bytes(raw_output)))
    except Exception:  # pragma: no cover - 外部工具不可用/超时/权限问题
        return []


__all__ = [
    "ERROR_CATEGORIES",
    "CATEGORY_HINTS",
    "MAX_LOG_CHARS",
    "DRIVER_CRASH_HINT",
    "clean_tex_log",
    "extract_tex_errors",
    "classify_tex_error",
    "detect_driver_crash",
    "summarize_tex_log",
    "diagnose_tex_error",
    "external_tools",
    "filter_with_texlogsieve",
    "run_chktex_lint",
]
