import os
import hashlib
import base64
import re
import subprocess
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass

from .diagram_palette import as_tex_definitions
from .file_ops import ensure_dir
from .tex_log_tools import clean_tex_log, detect_driver_crash, extract_tex_errors


DEFAULT_TIKZ_COMPILE_TIMEOUT = 60
MAX_COMPILE_LOG_CHARS = 20000

# error_kind 稳定值（契约第 3 节）
ERROR_KIND_OK = "OK"
ERROR_KIND_COMPILE_ERROR = "COMPILE_ERROR"
#: TeX 已排版完成，但生成 PDF 的输出驱动（xdvipdfmx.exe）崩溃（Windows 运行时故障，
#: 如 0xC0000417 / returncode -1073740777）。这不是 TikZ 语法错误，compile_tikz 会对它
#: 自动重试，并（可选）用 lualatex 兜底。
ERROR_KIND_DRIVER_CRASH = "DRIVER_CRASH"
ERROR_KIND_TIMEOUT = "TIMEOUT"
ERROR_KIND_MISSING_XELATEX = "MISSING_XELATEX"
ERROR_KIND_MISSING_PYMUPDF = "MISSING_PYMUPDF"
ERROR_KIND_IO_ERROR = "IO_ERROR"
ERROR_KIND_UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass
class TikzCompileResult:
    """单次 TikZ → PNG 编译的结构化结果（Harness v2 契约第 3 节）。

    - ``log``：真实 xelatex 日志（优先读 .log 文件，其次 stdout），截断到 <= 20000 字符；
    - ``stdout``：xelatex 控制台输出（同样截断到 <= 20000 字符）；
    - ``error_kind``：OK / COMPILE_ERROR / DRIVER_CRASH / TIMEOUT / MISSING_XELATEX /
      MISSING_PYMUPDF / IO_ERROR / UNKNOWN_ERROR；
    - ``error_line``：从日志解析出的第一条致命错误的行号（日志中的文档行号），无则 None；
    - ``duration_s``：本次编译总耗时（秒）。
    """

    ok: bool
    b64_png: str | None = None
    log: str = ""
    stdout: str = ""
    error_kind: str = ""
    error_line: int | None = None
    duration_s: float = 0.0
    #: standalone 模板中 TikZ 代码之前的前缀行数；用于把日志里的
    #: 「文档行号」换算成「TikZ 片段内行号」（fragment_line = doc_line - offset）。
    source_line_offset: int = 0
    #: DRIVER_CRASH 时记录有符号的输出驱动退出码（如 -1073740777）；其它情况为 None。
    driver_exit_code: int | None = None
    #: 因 DRIVER_CRASH 自动重试的额外编译次数（0 表示未重试；1 表示重试 1 次）。
    compile_retries: int = 0


def _looks_like_tikz_body(code):
    return bool(re.search(r"\\(?:draw|coordinate|node|fill|path|foreach|clip|shade|def)\b", code or ""))


def _normalize_tikz_code(tikz_code):
    code = (tikz_code or "").strip()
    code = re.sub(r"^\s*```(?:latex|tex|tikz)?\s*", "", code)
    code = re.sub(r"\s*```\s*$", "", code)
    if r"\begin{tikzpicture}" not in code and _looks_like_tikz_body(code):
        code = "\\begin{tikzpicture}[line cap=round,line join=round]\n" + code + "\n\\end{tikzpicture}"
    return code


def _repair_common_tikz_math_labels(tikz_code):
    """修复 OCR 常见的 TikZ 数学标签，避免 ^、_ 在文本模式中导致编译失败。"""
    def repair_node_label(match):
        prefix, label = match.groups()
        stripped = label.strip()
        if (
            stripped
            and "$" not in stripped
            and r"\(" not in stripped
            and re.search(r"(?:\^|_|\\circ\b)", stripped)
        ):
            leading = label[:len(label) - len(label.lstrip())]
            trailing = label[len(label.rstrip()):]
            return f"{prefix}{{{leading}${stripped}${trailing}}}"
        return match.group(0)

    return re.sub(r"(node(?:\[[^\]]*\])?\s*)\{([^{}\n]*)\}", repair_node_label, tikz_code)


def _tikz_compile_timeout():
    """Return a safe, configurable timeout for slower first-run TeX setups."""
    try:
        return max(15, int(os.getenv("TIKZ_COMPILE_TIMEOUT", DEFAULT_TIKZ_COMPILE_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_TIKZ_COMPILE_TIMEOUT


def _resolve_compile_timeout(timeout):
    """解析 compile_tikz 的 timeout 参数；None 时回退到 TIKZ_COMPILE_TIMEOUT 语义。"""
    if timeout is None:
        return float(_tikz_compile_timeout())
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return float(_tikz_compile_timeout())
    if value <= 0:
        return float(_tikz_compile_timeout())
    return value


#: Windows 异常退出码下界：>= 0xC0000000 视为进程/驱动异常崩溃。
_DRIVER_RETURN_CODE_MIN = 0xC0000000
#: 0xC0000000 的有符号形式（-1073741824）。
_DRIVER_RETURN_CODE_SIGNED_MIN = -0x40000000


def _signed_driver_exit_code(returncode):
    """把 Windows 异常退出码归一化为有符号 int；普通退出码（如 1）返回 None。

    Windows 下 subprocess 可能给出无符号值 ``3221226519``（0xC0000417），也可能
    已经是有符号值 ``-1073740777``；两种形式统一换算为后者。
    """
    try:
        code = int(returncode)
    except (TypeError, ValueError):
        return None
    if code >= _DRIVER_RETURN_CODE_MIN:
        return code - 0x100000000
    if _DRIVER_RETURN_CODE_SIGNED_MIN <= code < 0:
        return code
    return None


def _detect_driver_crash_signature(text):
    """调用 tex_log_tools.detect_driver_crash 的安全包装；异常时返回 None。"""
    try:
        return detect_driver_crash(text)
    except Exception:  # pragma: no cover - 防御
        return None


def _driver_crash_code(signature, returncode):
    """优先取日志中的有符号驱动退出码，其次取 returncode 归一化结果。"""
    if isinstance(signature, dict) and isinstance(signature.get("exit_code"), int):
        return signature["exit_code"]
    return _signed_driver_exit_code(returncode)


def _driver_retry_limit():
    """DRIVER_CRASH 允许的额外重试次数（默认 1；``TIKZ_COMPILE_DRIVER_RETRIES`` 覆盖，clamp 0..3）。"""
    raw = os.getenv("TIKZ_COMPILE_DRIVER_RETRIES")
    if raw is None or str(raw).strip() == "":
        return 1
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 1
    return max(0, min(3, value))


def _lualatex_fallback_enabled():
    """是否允许 DRIVER_CRASH 后用 lualatex 兜底（``TIKZ_COMPILE_LUALATEX_FALLBACK=0`` 可关闭）。"""
    flag = str(os.getenv("TIKZ_COMPILE_LUALATEX_FALLBACK", "1")).strip().lower()
    return flag not in ("0", "false", "no", "off")


#: 驱动子进程专用临时目录名（放在 .tikz_cache 下，与编译进程同一 token 可写）。
DRIVER_TMP_DIR_NAME = "_drv_tmp"
#: 写探针文件内容（只用于自检目录可写性，写完立即删除）。
_DRIVER_TMP_PROBE_CONTENT = b"tikz-ops-driver-tmp-probe"


def _utf8_env_path(path):
    """把目录规范为可安全放进子进程环境变量的**绝对路径**；不可用时返回 None。

    Windows 环境块是 Unicode，但 TeX 内部按 UTF-8/本地编码处理路径；这里只接受
    能 os.path.abspath 且能 UTF-8 编码的值，避免把相对路径或不可编码的字符串注入。
    """
    try:
        value = os.path.abspath(str(path))
        value.encode("utf-8")
        return value
    except (TypeError, ValueError, UnicodeEncodeError, OSError):
        return None


def _probe_writable_dir(directory):
    """创建目录并做一次「写探针文件 → 删除」自检；可写返回绝对路径，否则 None。

    绝不抛异常：目录创建/写入/删除任一失败都只表示「该目录不能给 dvipdfmx 用」。
    """
    try:
        absolute = os.path.abspath(str(directory))
        ensure_dir(absolute)
        probe = os.path.join(absolute, "write_probe_%s.tmp" % uuid.uuid4().hex)
        with open(probe, "wb") as handle:
            handle.write(_DRIVER_TMP_PROBE_CONTENT)
        os.remove(probe)
        return absolute
    except Exception:  # pragma: no cover - 防御：任何异常都只表示不可写
        return None


def _new_driver_tmp_dir(compile_dir):
    """本次编译独占的驱动临时目录：``<compile_dir>/_drv_tmp/<uuid>``（绝对路径）。"""
    try:
        return os.path.abspath(os.path.join(str(compile_dir), DRIVER_TMP_DIR_NAME, uuid.uuid4().hex))
    except Exception:  # pragma: no cover - 防御
        return None


def _driver_tmp_env(compile_dir):
    """返回注入给 TeX 子进程的 ``TMP`` / ``TEMP`` / ``TMPDIR``（绝对路径字典）。

    已确证根因（2026-10-20，minidump + 现场实验）：xdvipdfmx 用
    ``_tempnam(get_temp_dir(), "dvipdfmx.")`` 创建 PSTricks 临时文件，取 **TMP** 环境变量；
    当调用方进程完整性受限（如被沙箱以 Low 运行）而 %TEMP% 为 Medium 时，fopen 返回 NULL，
    dvipdfmx **未判空**就 fprintf → MSVCR100 ``_invalid_parameter_noinfo`` → 0xC0000417，
    任何文档（含最小 article）都会触发。

    这里把 TMP/TEMP/TMPDIR 指向**与被测编译进程同一 token 可写**的工作区目录
    （``<compile_dir>/_drv_tmp/<本次 uuid>``，并发编译互不影响），并先做写入自检；
    首选目录不可写时回退到 ``tempfile.gettempdir()``；两者都不可写时返回空字典
    （不注入，绝不阻断编译）。
    """
    candidates = [_new_driver_tmp_dir(compile_dir)]
    try:
        candidates.append(tempfile.gettempdir())
    except Exception:  # pragma: no cover - 防御
        pass
    for candidate in candidates:
        if not candidate:
            continue
        writable = _probe_writable_dir(candidate)
        if not writable:
            continue
        value = _utf8_env_path(writable)
        if not value:
            continue
        return {"TMP": value, "TEMP": value, "TMPDIR": value}
    return {}


def _driver_tmp_cleanup_dir(compile_dir, driver_env):
    """从注入的环境字典推导「本次独占、可安全删除」的驱动临时目录；不安全时返回 None。

    只允许删除 ``<compile_dir>/_drv_tmp/<uuid>`` 这种本次独占子目录；
    回退到 ``tempfile.gettempdir()`` 的系统临时目录**绝不清理**（避免误删）。
    """
    if not isinstance(driver_env, dict):
        return None
    chosen = driver_env.get("TMP")
    if not chosen:
        return None
    try:
        chosen = os.path.abspath(str(chosen))
        expected_parent = os.path.abspath(os.path.join(str(compile_dir), DRIVER_TMP_DIR_NAME))
        if os.path.dirname(chosen) != expected_parent:
            return None
        return chosen
    except Exception:  # pragma: no cover - 防御
        return None


def _cleanup_driver_tmp(compile_dir, driver_env):
    """best-effort 清理本次驱动临时目录；``_drv_tmp`` 父目录空了也删掉。绝不抛异常。"""
    try:
        directory = _driver_tmp_cleanup_dir(compile_dir, driver_env)
        if not directory:
            return
        shutil.rmtree(directory, ignore_errors=True)
        parent = os.path.dirname(directory)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except Exception:  # pragma: no cover - 防御
        pass


def standalone_line_offset(tikz_code):
    """返回 standalone 模板中 TikZ 片段首行之前的前缀行数。

    用于把 xelatex 日志里的「文档行号」（``error_line`` 语义）换算成
    「TikZ 片段内行号」：``fragment_line = document_line - offset``。
    无法计算时返回 0（调用方应保持文档行号）。
    """
    try:
        normalized = _normalize_tikz_code(tikz_code)
        document = _build_standalone_document(normalized)
        if not normalized:
            return 0
        prefix = document.split(normalized, 1)[0]
        return max(0, prefix.count("\n"))
    except Exception:
        return 0


def _build_standalone_document(tikz_code):
    """把 TikZ 片段包进 standalone 模板（与历史实现逐字一致，避免行为漂移）。"""
    # Loading ctex/fontspec can be expensive on the first TeX Live run.  Most
    # physics diagrams only contain mathematical labels, so load it only when
    # the actual TikZ source contains CJK characters.
    cjk_package = "\\usepackage{ctex}\n" if re.search(r"[\u3400-\u9fff]", tikz_code) else ""
    # AI/OCR 生成的 TikZ 常用未定义的语义色名（primaryblue/meterblue/...），
    # 未定义时 xelatex 会报 pgfkeys unknown key；集中在 \tikzset 之前注入别名。
    palette_definitions = as_tex_definitions()
    return f"""\\documentclass[tikz, border=2mm]{{standalone}}
{cjk_package}\\usepackage{{amsmath}}
\\usepackage{{amssymb}}
\\usepackage{{xcolor}}
\\usepackage{{tikz}}
\\usepackage{{pgfplots}}
\\pgfplotsset{{compat=1.16}}
\\usetikzlibrary{{patterns}}
\\usetikzlibrary{{calc,positioning,intersections,arrows}}
\\usetikzlibrary{{shapes.geometric,through,decorations.pathmorphing,arrows.meta,quotes,mindmap,shapes.symbols,shapes.arrows,automata,angles,3d,trees,shadows,shapes.callouts,decorations.pathreplacing,decorations.markings}}
\\definecolor{{structure}}{{RGB}}{{34,94,168}}
{palette_definitions}\\tikzset{{
  cube/.style={{thick}},
  hide/.style={{thick,dashed,dash pattern=on 3pt off 2pt,gray!70}},
  hidden/.style={{thick,dashed,dash pattern=on 4pt off 2.4pt}},
  sec/.style={{thick,red!70,dashed,dash pattern=on 3pt off 1.8pt}},
  section/.style={{thick,red!70,dashed,dash pattern=on 4pt off 2.4pt}},
  cut/.style={{thick,dashed,dash pattern=on 4pt off 2.4pt}},
  aux/.style={{thick,dashed,dash pattern=on 4pt off 2.4pt}},
  sph/.style={{thin,dashed,dash pattern=on 5pt off 2.5pt}},
  plane/.style={{draw=black!65,fill=gray!10,fill opacity=0.2}},
  edge/.style={{thick}},
  dot/.style={{circle,fill,inner sep=1pt}},
  inc/.style={{thick,->}},
  inner/.style={{thin,dashed,dash pattern=on 3pt off 2pt}}
}}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""


def _decode_output(raw):
    """解码子进程输出（Windows 下 xelatex 日志可能混用 utf-8/cp936）。"""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    for encoding in ("utf-8", "cp936", "gb18030", "latin-1"):
        try:
            return bytes(raw).decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return bytes(raw).decode("latin-1", "replace")
    except Exception:  # pragma: no cover - 防御
        return ""


def _read_text_file(path):
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return ""
    return _decode_output(data)


def _first_error_line(log_text):
    """返回日志中第一条带行号的错误位置，供 TikzCompileResult.error_line 使用。"""
    try:
        for error in extract_tex_errors(log_text):
            line = error.get("line")
            if isinstance(line, int) and line > 0:
                return line
    except Exception:  # pragma: no cover - 防御
        pass
    return None


def _collect_log(log_path, stdout_text):
    """优先使用 .log 文件内容（更完整），否则退回 stdout；统一清洗并截断。"""
    file_log = _read_text_file(log_path) if log_path and os.path.exists(log_path) else ""
    return clean_tex_log(file_log or stdout_text, MAX_COMPILE_LOG_CHARS)


def _png_to_b64(png_path):
    with open(png_path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("utf-8")


def _compile_tikz_once(
    tikz_code: str,
    base_dir: str,
    timeout: float | None = None,
    target_png_path: str | None = None,
    engine: str | None = None,
    extra_env: dict | None = None,
) -> TikzCompileResult:
    """把 TikZ 代码编译为 PNG，并返回结构化结果（Harness v2 契约第 3 节）。

    - 复用历史实现的 standalone 模板、CJK 按需加载、``TIKZ_COMPILE_TIMEOUT`` 与
      临时文件清理逻辑；
    - **不丢弃日志**：失败时 ``result.log`` 保留真实 xelatex 日志（<= 20000 字符），
      并填充 ``error_kind`` / ``error_line`` / ``duration_s``；
    - ``duration_s`` 记录总耗时（编译 + PDF→PNG），任何分支都会填充（含 finally）；
    - 中文路径兼容（P0）：以 ``base_dir/.tikz_cache`` 为 cwd，命令行只传相对 ASCII
      UUID 文件名（``<uuid>.tex``）。本机 xelatex 无法打开含中文的绝对路径参数，
      因此绝不把 base_dir/绝对路径拼进命令行；如需输出到其它目录，编译后再 copy；
    - 本函数不抛异常，失败统一通过 ``ok=False`` + ``error_kind`` 表达。

    这是**单次**编译（内部函数）：``engine`` 用于显式选择编译引擎（默认 xelatex，
    lualatex 兜底时传入其绝对路径）；``extra_env`` 追加子进程环境变量。自动重试、
    lualatex 兜底与总耗时汇总见 :func:`compile_tikz`。

    子进程环境总会注入工作区内的专用可写 ``TMP``/``TEMP``/``TMPDIR``
    （见 :func:`_driver_tmp_env`），以规避 dvipdfmx 因临时目录不可写而 0xC0000417 崩溃。
    """
    started = time.monotonic()
    result = TikzCompileResult(ok=False, error_kind=ERROR_KIND_UNKNOWN_ERROR)
    compile_dir = os.path.join(str(base_dir), ".tikz_cache")
    tex_path = None
    pdf_path = None
    temp_png_path = None
    aux_path = None
    log_path = None
    driver_env = {}
    try:
        normalized = _normalize_tikz_code(tikz_code)
        normalized = _repair_common_tikz_math_labels(normalized)
        ensure_dir(compile_dir)

        # 使用 uuid 确保并发写入时不会出现 [WinError 32] 进程冲突
        unique_id = uuid.uuid4().hex
        tex_path = os.path.join(compile_dir, f"{unique_id}.tex")
        pdf_path = os.path.join(compile_dir, f"{unique_id}.pdf")
        temp_png_path = os.path.join(compile_dir, f"{unique_id}.png")
        aux_path = os.path.join(compile_dir, f"{unique_id}.aux")
        log_path = os.path.join(compile_dir, f"{unique_id}.log")

        xelatex = engine or shutil.which("xelatex")
        if not xelatex:
            result.error_kind = ERROR_KIND_MISSING_XELATEX
            return result

        # 子进程环境：先注入驱动专用可写 TMP/TEMP/TMPDIR（根因修复），再合并 extra_env
        # （lualatex 兜底的 TEXMFVAR/TEXMFCACHE 等必须保留）。
        try:
            driver_env = _driver_tmp_env(compile_dir)
        except Exception:  # pragma: no cover - 防御：注入失败不阻断编译
            driver_env = {}
        merged_env = dict(driver_env)
        if extra_env:
            try:
                merged_env.update({str(key): str(value) for key, value in extra_env.items()})
            except Exception:  # pragma: no cover - 防御
                pass
        run_env = None
        if merged_env:
            try:
                run_env = dict(os.environ)
                run_env.update(merged_env)
            except Exception:  # pragma: no cover - 防御
                run_env = None

        try:
            document = _build_standalone_document(normalized)
            # 记录片段在 standalone 文档中的行偏移，供上层把日志行号换算成片段行号
            try:
                result.source_line_offset = max(0, document.split(normalized, 1)[0].count("\n"))
            except Exception:
                result.source_line_offset = 0
            with open(tex_path, "w", encoding="utf-8") as handle:
                handle.write(document)
        except OSError as exc:
            result.error_kind = ERROR_KIND_IO_ERROR
            result.log = clean_tex_log(f"写入临时 .tex 文件失败: {exc}", MAX_COMPILE_LOG_CHARS)
            result.stdout = result.log
            return result

        timeout_seconds = _resolve_compile_timeout(timeout)
        try:
            completed = subprocess.run(
                [
                    xelatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-file-line-error",
                    "-output-directory",
                    ".",
                    # P0：只传相对 ASCII 文件名；cwd 为编译目录，规避中文绝对路径问题
                    os.path.basename(tex_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                cwd=compile_dir,
                env=run_env,
            )
        except subprocess.TimeoutExpired as exc:
            partial = _decode_output(getattr(exc, "output", b"") or b"")
            result.stdout = clean_tex_log(partial, MAX_COMPILE_LOG_CHARS)
            result.log = _collect_log(log_path, partial)
            result.error_kind = ERROR_KIND_TIMEOUT
            return result

        stdout_text = _decode_output(completed.stdout or b"")
        result.stdout = clean_tex_log(stdout_text, MAX_COMPILE_LOG_CHARS)
        result.log = _collect_log(log_path, stdout_text)

        crash_signature = _detect_driver_crash_signature(
            (result.log or "") + "\n" + (result.stdout or "")
        )

        if completed.returncode != 0:
            result.error_line = _first_error_line(result.log)
            # 先识别「TeX 排版完成、PDF 输出驱动崩溃」：日志没有 `!` LaTeX 错误，
            # 图形源码通常无需修改，应自动重试而不是让模型改代码。
            if crash_signature is not None or _signed_driver_exit_code(completed.returncode) is not None:
                result.error_kind = ERROR_KIND_DRIVER_CRASH
                result.driver_exit_code = _driver_crash_code(crash_signature, completed.returncode)
            else:
                result.error_kind = ERROR_KIND_COMPILE_ERROR
            return result

        if crash_signature is not None:
            # 极端情况：xetex 吞掉驱动错误返回 0，但日志已写明 PDF 可能无效。
            result.error_kind = ERROR_KIND_DRIVER_CRASH
            result.driver_exit_code = _driver_crash_code(crash_signature, completed.returncode)
            return result

        # 编译成功 → PDF 转 PNG
        try:
            import fitz  # 需要 pip install pymupdf
        except ImportError:
            result.error_kind = ERROR_KIND_MISSING_PYMUPDF
            return result

        try:
            document = fitz.open(pdf_path)
            try:
                page = document.load_page(0)
                pix = page.get_pixmap(dpi=150)
                pix.save(temp_png_path)
            finally:
                document.close()
        except Exception as exc:
            result.error_kind = ERROR_KIND_UNKNOWN_ERROR
            result.log = clean_tex_log(
                (result.log + f"\nPDF→PNG 转换失败: {type(exc).__name__}: {exc}").strip(),
                MAX_COMPILE_LOG_CHARS,
            )
            return result

        if not temp_png_path or not os.path.exists(temp_png_path):
            result.error_kind = ERROR_KIND_UNKNOWN_ERROR
            return result

        if target_png_path:
            target_dir = os.path.dirname(os.path.abspath(target_png_path))
            if target_dir:
                ensure_dir(target_dir)
            try:
                shutil.copy2(temp_png_path, target_png_path)
            except Exception:
                pass  # 忽略并发覆盖 target 的错误，仍返回本次编译的 b64

        result.b64_png = _png_to_b64(temp_png_path)
        result.error_kind = ERROR_KIND_OK
        result.ok = True
        return result
    except Exception as exc:  # 兜底：契约要求失败时保留错误信息而不是抛出
        result.ok = False
        result.b64_png = None
        if result.error_kind not in (
            ERROR_KIND_COMPILE_ERROR,
            ERROR_KIND_DRIVER_CRASH,
            ERROR_KIND_TIMEOUT,
            ERROR_KIND_MISSING_XELATEX,
            ERROR_KIND_MISSING_PYMUPDF,
            ERROR_KIND_IO_ERROR,
        ):
            result.error_kind = ERROR_KIND_UNKNOWN_ERROR
        if not result.log:
            result.log = clean_tex_log(f"{type(exc).__name__}: {exc}", MAX_COMPILE_LOG_CHARS)
        return result
    finally:
        result.duration_s = round(max(0.0, time.monotonic() - started), 3)
        # 清理所有独占生成的临时文件，释放空间（target_png_path 不在清理列表）
        for temp_file in (tex_path, pdf_path, temp_png_path, aux_path, log_path):
            if not temp_file:
                continue
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
        # 清理本次注入的驱动临时目录（回退到系统 %TEMP% 时不会清理，避免误删）
        _cleanup_driver_tmp(compile_dir, driver_env)


#: lualatex 兜底成功时写入 result.log 的标记（便于上层/测试识别，不影响 error_kind）。
LUALATEX_FALLBACK_MARKER = "[lualatex 兜底]"

#: lualatex 兜底默认超时下限（秒）：首次运行 luaotfload 建字体缓存实测约 60-90 秒，
#: 默认 TIKZ_COMPILE_TIMEOUT=60 会误判超时；仅当调用方**未显式**指定 timeout 时放宽。
DEFAULT_LUALATEX_FALLBACK_TIMEOUT = 180


def _fallback_timeout(timeout):
    """解析 lualatex 兜底的超时：未显式指定时放宽到 >= 180s，显式指定时尊重调用方语义。"""
    default = max(float(_resolve_compile_timeout(None)), float(DEFAULT_LUALATEX_FALLBACK_TIMEOUT))
    if timeout is None:
        return default
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def _compile_tikz_fallback(tikz_code, base_dir, timeout, target_png_path):
    """DRIVER_CRASH 重试仍失败时尝试一次 lualatex（LuaTeX 自带 PDF 后端，绕过 xdvipdfmx）。

    只在已确认「xelatex 输出驱动崩溃」后调用，绝不用来掩盖语法错误；lualatex 不可用、
    被 ``TIKZ_COMPILE_LUALATEX_FALLBACK=0`` 关闭或准备失败时返回 None。
    实测必需：luaotfload 需要可写字体缓存目录，故把 TEXMFVAR/TEXMFCACHE **绝对路径**
    指向工作区（相对路径会被 luaotfload 相对 cwd 解析到错误位置）。
    """
    if not _lualatex_fallback_enabled():
        return None
    try:
        lualatex = shutil.which("lualatex")
    except Exception:  # pragma: no cover - 防御
        return None
    if not lualatex:
        return None
    try:
        cache_dir = os.path.join(str(base_dir), ".tikz_cache")
        ensure_dir(cache_dir)
        texmf_var = os.path.abspath(os.path.join(cache_dir, "_luaotfload_cache"))
        ensure_dir(texmf_var)
    except Exception:  # pragma: no cover - 防御
        return None
    try:
        return _compile_tikz_once(
            tikz_code,
            base_dir,
            timeout=_fallback_timeout(timeout),
            target_png_path=target_png_path,
            engine=lualatex,
            extra_env={"TEXMFVAR": texmf_var, "TEXMFCACHE": texmf_var},
        )
    except Exception:  # pragma: no cover - 防御
        return None


def compile_tikz(
    tikz_code: str,
    base_dir: str,
    timeout: float | None = None,
    target_png_path: str | None = None,
) -> TikzCompileResult:
    """把 TikZ 代码编译为 PNG（对外入口）：DRIVER_CRASH 自动重试 + 可选 lualatex 兜底。

    - 非驱动类失败（语法错误/缺宏包/超时等）**绝不重试**，保持既有语义与耗时；
    - 命中 ``error_kind == DRIVER_CRASH`` 时用**全新 uuid/临时文件**重跑，默认额外重试
      1 次；``TIKZ_COMPILE_DRIVER_RETRIES`` 可覆盖（clamp 到 0..3）；
    - 重试后仍崩溃且 lualatex 可用时做一次 lualatex 兜底（LuaTeX 直接输出 PDF，绕过
      xdvipdfmx）；成功则 ``error_kind=OK`` 并在 ``log`` 中写入 ``[lualatex 兜底]`` 标记，
      失败则保留最后一次 xelatex 崩溃日志与 ``driver_exit_code``；
    - ``compile_retries`` 为驱动崩溃重试次数，``duration_s`` 为含重试/兜底的总耗时；
    - 子进程总会注入工作区内专用可写 ``TMP``/``TEMP``/``TMPDIR``（已确证根因：
      xdvipdfmx 因 TMP 不可写而 0xC0000417 崩溃）；
    - 本函数不抛异常。
    """
    started = time.monotonic()
    retries = 0
    result = TikzCompileResult(ok=False, error_kind=ERROR_KIND_UNKNOWN_ERROR)
    try:
        result = _compile_tikz_once(
            tikz_code, base_dir, timeout=timeout, target_png_path=target_png_path
        )
        retry_limit = _driver_retry_limit()
        while result.error_kind == ERROR_KIND_DRIVER_CRASH and retries < retry_limit:
            retries += 1
            result = _compile_tikz_once(
                tikz_code, base_dir, timeout=timeout, target_png_path=target_png_path
            )
        if result.error_kind == ERROR_KIND_DRIVER_CRASH:
            fallback = _compile_tikz_fallback(tikz_code, base_dir, timeout, target_png_path)
            if fallback is not None and fallback.ok:
                fallback.log = (
                    "%s xelatex 输出驱动连续崩溃 %d 次，已改用 lualatex 编译成功。\n%s"
                    % (LUALATEX_FALLBACK_MARKER, retries + 1, fallback.log or "")
                ).strip()
                result = fallback
            elif fallback is not None:
                result.log = (
                    (result.log or "")
                    + "\n%s 改用 lualatex 兜底仍失败（error_kind=%s）。"
                    % (LUALATEX_FALLBACK_MARKER, fallback.error_kind or ERROR_KIND_UNKNOWN_ERROR)
                ).strip()
    except Exception as exc:  # 防御：契约要求 compile_tikz 不抛异常
        result.ok = False
        result.b64_png = None
        if result.error_kind != ERROR_KIND_DRIVER_CRASH:
            result.error_kind = ERROR_KIND_UNKNOWN_ERROR
        if not result.log:
            result.log = clean_tex_log(f"{type(exc).__name__}: {exc}", MAX_COMPILE_LOG_CHARS)
    finally:
        result.compile_retries = retries
        result.duration_s = round(max(0.0, time.monotonic() - started), 3)
    return result


def _legacy_error_message(result):
    """把结构化 error_kind 映射回 get_tikz_image_b64 的历史错误字符串。"""
    kind = result.error_kind or ERROR_KIND_UNKNOWN_ERROR
    if kind == ERROR_KIND_TIMEOUT:
        return f"TIMEOUT_{_tikz_compile_timeout()}S"
    if kind == ERROR_KIND_DRIVER_CRASH:
        # 保持 get_tikz_image_b64 旧契约：DRIVER_CRASH 仍映射为 COMPILE_ERROR
        # （上层再用 result.log / detect_driver_crash 区分环境故障）。
        return ERROR_KIND_COMPILE_ERROR
    if kind in (
        ERROR_KIND_COMPILE_ERROR,
        ERROR_KIND_MISSING_XELATEX,
        ERROR_KIND_MISSING_PYMUPDF,
        ERROR_KIND_IO_ERROR,
    ):
        return kind
    return ERROR_KIND_UNKNOWN_ERROR


def get_tikz_image_b64(tikz_code, base_dir, source_tex_path=None, target_png_path=None):
    """
    将 TikZ 代码编译为 PNG 图片并返回 base64 编码。
    如果提供了 source_tex_path 和 target_png_path，则使用目标路径作为缓存，并基于文件修改时间更新。
    否则使用基于代码哈希的全局缓存。

    兼容性（契约第 3 节）：签名、返回值 ``(b64, err)`` 与缓存语义保持不变；
    内部改为调用 :func:`compile_tikz`，失败时保留真实日志供上层诊断使用。
    """
    tikz_code = _normalize_tikz_code(tikz_code)
    # Key the compiled artifact by the complete standalone document/configuration,
    # not by the enclosing question's mtime (tags and prose do not change diagrams).
    cache_dir = os.path.join(base_dir, '.tikz_cache')
    ensure_dir(cache_dir)
    engine = shutil.which('xelatex') or 'missing'
    try:
        engine_token = str(os.stat(engine).st_mtime_ns)
    except OSError:
        engine_token = ''
    environment = repr(tuple((key, os.getenv(key, '')) for key in ('TEXINPUTS', 'TEXMFHOME', 'TEXMFCNF')))
    signature = _build_standalone_document(tikz_code) + engine + engine_token + environment + '|dpi=150|v2'
    code_hash = hashlib.sha256(signature.encode('utf-8')).hexdigest()
    requested_target = target_png_path
    target_png_path = os.path.join(cache_dir, f'{code_hash}.png')
    needs_compile = not os.path.exists(target_png_path)

    if not needs_compile and os.path.exists(target_png_path):
        try:
            with open(target_png_path, "rb") as f:
                cached_bytes = f.read()
                cached = base64.b64encode(cached_bytes).decode('utf-8')
            if requested_target and os.path.abspath(requested_target) != os.path.abspath(target_png_path):
                ensure_dir(os.path.dirname(os.path.abspath(requested_target)))
                previous = None
                if os.path.isfile(requested_target):
                    with open(requested_target, 'rb') as existing:
                        previous = existing.read()
                if previous != cached_bytes:
                    shutil.copy2(target_png_path, requested_target)
            return cached, None
        except Exception:
            # 如果读取缓存失败（可能正被占用），则强制重新编译
            needs_compile = True

    result = compile_tikz(tikz_code, base_dir, timeout=None, target_png_path=target_png_path)
    if result.ok and result.b64_png:
        if requested_target and os.path.abspath(requested_target) != os.path.abspath(target_png_path):
            ensure_dir(os.path.dirname(os.path.abspath(requested_target)))
            shutil.copy2(target_png_path, requested_target)
        return result.b64_png, None
    return None, _legacy_error_message(result)
