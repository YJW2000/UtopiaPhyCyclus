"""tests/test_tikz_compile_crash.py — DRIVER_CRASH（PDF 输出驱动崩溃）识别/重试/兜底测试。

背景（2026-10-20 用户实测）：xelatex 已排版完成，但输出驱动 xdvipdfmx.exe 在
MSVCR100.dll 中崩溃（0xC0000417 / STATUS_INVALID_CRUNTIME_PARAMETER），日志尾部：

    Error -1073740777 (driver return code) generating output;
    file tikz.pdf may not be valid.

日志里**没有** `!` LaTeX 错误，旧实现只能报「未从日志中解析出明确的 LaTeX 错误」。
本文件全部用 ``unittest.mock`` 伪造 ``subprocess.run`` / ``shutil.which``，**不触发真实编译**；
真实编译的回归覆盖在 tests/test_tikz_ops_diagnostic.py 与 _repro_fig.py。
"""

import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import tex_log_tools as tlt  # noqa: E402
from utils.tikz_ops import (  # noqa: E402
    DEFAULT_LUALATEX_FALLBACK_TIMEOUT,
    ERROR_KIND_COMPILE_ERROR,
    ERROR_KIND_DRIVER_CRASH,
    ERROR_KIND_OK,
    ERROR_KIND_TIMEOUT,
    LUALATEX_FALLBACK_MARKER,
    TikzCompileResult,
    _fallback_timeout,
    _legacy_error_message,
    compile_tikz,
    get_tikz_image_b64,
)

CACHE_ROOT = os.path.join(ROOT, ".tikz_cache")

VALID_TIKZ = r"\begin{tikzpicture}\draw[->,thick] (0,0)--(2,0);\node at (1,0.5) {$F=ma$};\end{tikzpicture}"
INVALID_TIKZ = r"\begin{tikzpicture}\draw (0,0)--(2,0)\end{tikzpicture}"

#: 真实崩溃日志（Windows 实测格式；uuid 是 summarize 清洗后的显示效果）
CRASH_LOG = (
    "This is XeTeX, Version 3.141592653-2.6-0.999995 (TeX Live 2026)\n"
    "entering extended mode\n"
    "(./deadbeefdeadbeefdeadbeefdeadbeef.tex\n"
    "LaTeX2e <2025-11-01>\n"
    "[1] [2])\n"
    "Error -1073740777 (driver return code) generating output;\n"
    "file deadbeefdeadbeefdeadbeefdeadbeef.pdf may not be valid.\n"
    "Transcript written on deadbeefdeadbeefdeadbeef.log.\n"
)
CRASH_STDOUT = CRASH_LOG.encode("utf-8", "replace")

SYNTAX_LOG = (
    "./abc.tex:30: Missing $ inserted.\n"
    "l.30 \\begin{tikzpicture}\\node {F_\n"
    "No pages of output.\n"
).encode("utf-8", "replace")


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


def _which_xelatex_only(name):
    """只把 xelatex 当可用引擎，禁用 lualatex 兜底（让调用次数可精确断言）。"""
    return "xelatex" if name == "xelatex" else None


def _which_both(name):
    return name if name in ("xelatex", "lualatex") else None


def _fake_success_run(command, **kwargs):
    """伪造一次成功编译：在 cwd 下生成同名 PDF，供后续 PDF→PNG 使用。"""
    cwd = kwargs.get("cwd") or os.getcwd()
    tex_name = command[-1]
    pdf_path = os.path.join(cwd, tex_name[:-4] + ".pdf")
    import fitz

    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), "mock compile")
        document.save(pdf_path)
    finally:
        document.close()
    return _FakeCompleted(0, b"Output written on mock.pdf\n")


class _BaseCrashCase(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(CACHE_ROOT, "_test_tikzcrash_" + uuid.uuid4().hex[:10])
        os.makedirs(self.base, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _cache_files(self):
        """返回 .tikz_cache 下遗留的**文件**（忽略 lualatex 兜底的持久字体缓存目录）。"""
        compile_dir = os.path.join(self.base, ".tikz_cache")
        if not os.path.isdir(compile_dir):
            return []
        return sorted(
            name for name in os.listdir(compile_dir) if os.path.isfile(os.path.join(compile_dir, name))
        )

    def _names(self, command):
        try:
            return os.path.basename(str(command[0])).lower()
        except (TypeError, IndexError):
            return ""


class TestDriverCrashRecognition(_BaseCrashCase):
    def test_result_dataclass_new_optional_fields(self):
        result = TikzCompileResult(ok=False)
        self.assertIsNone(result.driver_exit_code)
        self.assertEqual(result.compile_retries, 0)
        # 既有构造位置参数用法不得被破坏
        legacy = TikzCompileResult(True, "b64", "log", "stdout", ERROR_KIND_OK, 7, 1.5)
        self.assertEqual(legacy.error_line, 7)
        self.assertEqual(legacy.compile_retries, 0)

    def test_driver_crash_detected_with_signed_exit_code(self):
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch.dict(
            os.environ, {"TIKZ_COMPILE_DRIVER_RETRIES": "0"}
        ), patch("utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, CRASH_STDOUT)):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertFalse(result.ok)
        self.assertIsNone(result.b64_png)
        self.assertEqual(result.error_kind, ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(result.driver_exit_code, -1073740777)
        self.assertEqual(result.compile_retries, 0)
        self.assertIsNone(result.error_line)  # 没有 ! LaTeX 错误行
        self.assertIn("driver return code", result.log)
        self.assertGreaterEqual(result.duration_s, 0.0)
        self.assertEqual(self._cache_files(), [])

    def test_unsigned_windows_exception_code_detected(self):
        # subprocess 在 Windows 上可能返回无符号 0xC0000417 == 3221226519
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch.dict(
            os.environ, {"TIKZ_COMPILE_DRIVER_RETRIES": "0"}
        ), patch("utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(0xC0000417, b"")):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertEqual(result.error_kind, ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(result.driver_exit_code, -1073740777)

    def test_driver_crash_when_xetex_reports_zero(self):
        # 极端情况：驱动崩溃但 xetex 返回 0（PDF 实际无效）→ 仍按 DRIVER_CRASH 处理
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch.dict(
            os.environ, {"TIKZ_COMPILE_DRIVER_RETRIES": "0", "TIKZ_COMPILE_LUALATEX_FALLBACK": "0"}
        ), patch("utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(0, CRASH_STDOUT)):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(result.driver_exit_code, -1073740777)

    def test_syntax_error_not_retried_and_not_driver_crash(self):
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch(
            "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, SYNTAX_LOG)
        ) as mocked:
            result = compile_tikz(INVALID_TIKZ, self.base)
        self.assertEqual(mocked.call_count, 1)  # 非驱动类失败严禁重试
        self.assertEqual(result.error_kind, ERROR_KIND_COMPILE_ERROR)
        self.assertIsNone(result.driver_exit_code)
        self.assertEqual(result.compile_retries, 0)
        self.assertIn("Missing $ inserted", result.log)
        self.assertEqual(self._cache_files(), [])

    def test_timeout_not_retried(self):
        timeout_error = subprocess.TimeoutExpired(cmd="xelatex", timeout=5)
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch(
            "utils.tikz_ops.subprocess.run", side_effect=timeout_error
        ) as mocked:
            result = compile_tikz(INVALID_TIKZ, self.base, timeout=5)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result.error_kind, ERROR_KIND_TIMEOUT)
        self.assertEqual(result.compile_retries, 0)


class TestDriverCrashRetry(_BaseCrashCase):
    def test_first_crash_then_retry_succeeds(self):
        calls = {"count": 0}

        def fake_run(command, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                self.assertIn("xelatex", self._names(command))
                return _FakeCompleted(1, CRASH_STDOUT)
            return _fake_success_run(command, **kwargs)

        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertEqual(calls["count"], 2)
        self.assertTrue(result.ok, msg=result.log[-500:])
        self.assertEqual(result.error_kind, ERROR_KIND_OK)
        self.assertIsNotNone(result.b64_png)
        self.assertEqual(result.compile_retries, 1)
        self.assertGreater(result.duration_s, 0.0)
        self.assertEqual(self._cache_files(), [])

    def test_retry_uses_fresh_uuid_and_temp_files(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append(command[-1])
            if len(seen) == 1:
                return _FakeCompleted(1, CRASH_STDOUT)
            return _fake_success_run(command, **kwargs)

        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertTrue(result.ok)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])  # 每次重试都用全新 uuid 文件名
        self.assertEqual(self._cache_files(), [])

    def test_continuous_crash_keeps_last_log(self):
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch.dict(
            os.environ, {"TIKZ_COMPILE_LUALATEX_FALLBACK": "0"}
        ), patch(
            "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, CRASH_STDOUT)
        ) as mocked:
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertEqual(mocked.call_count, 2)  # 默认额外重试 1 次
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(result.compile_retries, 1)
        self.assertEqual(result.driver_exit_code, -1073740777)
        self.assertIn("driver return code", result.log)
        self.assertNotIn(LUALATEX_FALLBACK_MARKER, result.log)
        self.assertEqual(self._cache_files(), [])

    def test_retry_limit_env_is_clamped(self):
        cases = [("9", 4), ("abc", 2), ("-3", 1), ("2", 3)]
        for raw, expected_calls in cases:
            with self.subTest(TIKZ_COMPILE_DRIVER_RETRIES=raw):
                with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch.dict(
                    os.environ,
                    {"TIKZ_COMPILE_DRIVER_RETRIES": raw, "TIKZ_COMPILE_LUALATEX_FALLBACK": "0"},
                ), patch(
                    "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, CRASH_STDOUT)
                ) as mocked:
                    result = compile_tikz(VALID_TIKZ, self.base)
                self.assertEqual(mocked.call_count, expected_calls)
                self.assertEqual(result.compile_retries, expected_calls - 1)
                self.assertEqual(result.error_kind, ERROR_KIND_DRIVER_CRASH)


class TestLegacyCompatibility(_BaseCrashCase):
    def test_legacy_error_message_maps_driver_crash_to_compile_error(self):
        self.assertEqual(
            _legacy_error_message(TikzCompileResult(ok=False, error_kind=ERROR_KIND_DRIVER_CRASH)),
            "COMPILE_ERROR",
        )

    def test_get_tikz_image_b64_returns_legacy_compile_error(self):
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_xelatex_only), patch.dict(
            os.environ, {"TIKZ_COMPILE_DRIVER_RETRIES": "0", "TIKZ_COMPILE_LUALATEX_FALLBACK": "0"}
        ), patch("utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, CRASH_STDOUT)):
            b64, error = get_tikz_image_b64(VALID_TIKZ, self.base)
        self.assertIsNone(b64)
        self.assertEqual(error, "COMPILE_ERROR")


class TestLualatexFallback(_BaseCrashCase):
    def test_fallback_timeout_relaxes_only_for_default(self):
        with patch.dict(os.environ, {"TIKZ_COMPILE_TIMEOUT": "30"}):
            self.assertGreaterEqual(_fallback_timeout(None), DEFAULT_LUALATEX_FALLBACK_TIMEOUT)
        # 显式 timeout 必须被尊重（不能因为兜底就把调用方语义拉长）
        self.assertAlmostEqual(_fallback_timeout(12.5), 12.5)

    def test_lualatex_fallback_rescues_after_driver_crash(self):
        commands = []
        envs = []

        def fake_run(command, **kwargs):
            commands.append(command)
            envs.append(kwargs.get("env"))
            if len(commands) <= 2:  # xelatex 首次 + 1 次重试都崩溃
                return _FakeCompleted(1, CRASH_STDOUT)
            return _fake_success_run(command, **kwargs)

        with patch("utils.tikz_ops.shutil.which", side_effect=_which_both), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertEqual(len(commands), 3)
        self.assertIn("lualatex", self._names(commands[-1]))
        # 兜底必须给 luaotfload 可写字体缓存目录（否则 luaotfload 直接退出）
        self.assertTrue(envs[-1])
        self.assertIn(self.base, envs[-1].get("TEXMFVAR", ""))
        # 必须传绝对路径：相对路径会被 luaotfload 相对 cwd 解析到错误位置
        self.assertTrue(os.path.isabs(envs[-1].get("TEXMFVAR", "")))
        self.assertEqual(envs[-1].get("TEXMFVAR"), envs[-1].get("TEXMFCACHE"))
        self.assertTrue(result.ok, msg=result.log[-500:])
        self.assertEqual(result.error_kind, ERROR_KIND_OK)
        self.assertIsNotNone(result.b64_png)
        self.assertEqual(result.compile_retries, 1)
        self.assertIn(LUALATEX_FALLBACK_MARKER, result.log)

    def test_lualatex_fallback_can_be_disabled_by_env(self):
        with patch("utils.tikz_ops.shutil.which", side_effect=_which_both), patch.dict(
            os.environ, {"TIKZ_COMPILE_LUALATEX_FALLBACK": "0"}
        ), patch(
            "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, CRASH_STDOUT)
        ) as mocked:
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result.error_kind, ERROR_KIND_DRIVER_CRASH)
        self.assertNotIn(LUALATEX_FALLBACK_MARKER, result.log)

    def test_lualatex_fallback_failure_keeps_crash_result(self):
        def fake_run(command, **kwargs):
            return _FakeCompleted(1, CRASH_STDOUT)

        with patch("utils.tikz_ops.shutil.which", side_effect=_which_both), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ) as mocked:
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertEqual(mocked.call_count, 3)  # xelatex ×2 + lualatex ×1
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_DRIVER_CRASH)
        self.assertEqual(result.driver_exit_code, -1073740777)
        self.assertIn("driver return code", result.log)
        self.assertIn(LUALATEX_FALLBACK_MARKER, result.log)
        self.assertEqual(self._cache_files(), [])


class TestDriverCrashLogDiagnostics(unittest.TestCase):
    """tex_log_tools 的驱动崩溃诊断（T1 契约：category 稳定，message/hint 可操作）。"""

    def test_detect_driver_crash_returns_structured_result(self):
        detected = tlt.detect_driver_crash(CRASH_LOG)
        self.assertIsInstance(detected, dict)
        self.assertEqual(detected["error_kind"], "DRIVER_CRASH")
        self.assertEqual(detected["exit_code"], -1073740777)
        self.assertIn("xdvipdfmx", detected["message"])
        self.assertTrue(detected["hint"].strip())

    def test_detect_driver_crash_hex_code_only(self):
        detected = tlt.detect_driver_crash("Windows event: xdvipdfmx.exe crashed, code 0xc0000417")
        self.assertEqual(detected["exit_code"], -1073740777)

    def test_detect_driver_crash_ignores_normal_logs(self):
        normal_logs = [
            None,
            "",
            "./x.tex:6: Missing $ inserted.\nl.6 \\node {F_",
            "Output written on tikz.pdf (1 page).\nTranscript written on tikz.log.",
            "xdvipdfmx:warning: Object @page.1 already defined.",  # 健康日志里的驱动警告
            "TIMEOUT_60S",
        ]
        for raw in normal_logs:
            self.assertIsNone(tlt.detect_driver_crash(raw), msg=repr(raw))

    def test_detect_driver_crash_never_raises(self):
        for raw in (12345, b"\xff\xfe\x00binary", ["x"], object(), "0xc0000417" * 5000):
            self.assertIsInstance(tlt.detect_driver_crash(raw), (dict, type(None)))

    def test_summarize_no_longer_says_unparsable_error(self):
        summary = tlt.summarize_tex_log(CRASH_LOG, source=r"C:\tmp\abc.tex")
        self.assertLessEqual(len(summary), 4000)
        self.assertNotIn("未从日志中解析出明确的 LaTeX 错误", summary)
        self.assertIn("xdvipdfmx", summary)
        self.assertIn("不是 TikZ 语法错误", summary)
        self.assertIn("建议", summary)
        self.assertIn("abc.tex", summary)
        self.assertNotIn(r"C:\tmp", summary)

    def test_diagnose_prioritizes_driver_crash(self):
        diagnosis = tlt.diagnose_tex_error(CRASH_LOG)
        self.assertIn("DRIVER_CRASH", diagnosis)
        self.assertIn("修复建议", diagnosis)
        self.assertIn("不是 TikZ 语法错误", diagnosis)
        self.assertNotIn("未能从日志中识别结构化错误类别", diagnosis)

    def test_extract_category_stays_in_stable_contract(self):
        errors = tlt.extract_tex_errors(CRASH_LOG)
        self.assertIsInstance(errors, list)
        for error in errors:
            self.assertEqual(set(error.keys()), {"category", "line", "message", "hint", "context"})
            self.assertIn(error["category"], tlt.ERROR_CATEGORIES)
        # 短崩溃日志会兜底成一条 unknown，但 message/hint 必须是可操作的驱动崩溃说明
        if errors:
            self.assertEqual(errors[0]["category"], "unknown")
            self.assertIn("不是 TikZ 语法错误", errors[0]["message"])
            self.assertIn("输出驱动", errors[0]["hint"])

    def test_crash_message_and_hint_are_chinese(self):
        detected = tlt.detect_driver_crash(CRASH_LOG)
        for text in (detected["message"], detected["hint"]):
            self.assertTrue(any("\u4e00" <= ch <= "\u9fff" for ch in text))


if __name__ == "__main__":
    unittest.main()
