"""tests/test_tikz_ops_diagnostic.py — 契约第 3 节 compile_tikz / TikzCompileResult 测试。

包含：
- mock subprocess 的失败/超时/缺依赖/截断等用例（不依赖本机 xelatex）；
- @unittest.skipUnless(shutil.which("xelatex")) 的真实编译成功 / 失败用例（保留真实日志）。

注意：测试在本仓库 .tikz_cache 下用 os.makedirs 创建隔离目录（不用 tempfile.TemporaryDirectory，
以兼容 Windows 沙箱对 mkdtemp 目录的写限制）。
"""

import os
from pathlib import Path
import sys
import shutil
import subprocess
import unittest
import uuid
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import tex_log_tools as tlt  # noqa: E402
from utils.tikz_ops import (  # noqa: E402
    ERROR_KIND_COMPILE_ERROR,
    ERROR_KIND_MISSING_PYMUPDF,
    ERROR_KIND_MISSING_XELATEX,
    ERROR_KIND_OK,
    ERROR_KIND_TIMEOUT,
    MAX_COMPILE_LOG_CHARS,
    TikzCompileResult,
    compile_tikz,
    get_tikz_image_b64,
)

CACHE_ROOT = os.path.join(ROOT, ".tikz_cache")
VALID_TIKZ = r"\begin{tikzpicture}\draw[->,thick] (0,0)--(2,0);\node at (1,0.5) {$F=ma$};\end{tikzpicture}"
INVALID_TIKZ = r"\begin{tikzpicture}\draw (0,0)--(2,0)\end{tikzpicture}"
NO_SHAPE_TIKZ = r"\begin{tikzpicture}\draw (0,0)--(A);\end{tikzpicture}"

FAILURE_STDOUT = b"./abc.tex:30: Missing $ inserted.\nl.30 \\node {F_\n"
FAILURE_LOG = (
    "./abc.tex:30: Missing $ inserted.\n"
    "l.30 \\begin{tikzpicture}\\node {F_\n"
    "No pages of output.\n"
).encode("utf-8", "replace")


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


class TikzCompileMockTests(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(CACHE_ROOT, "_test_tikzdiag_" + uuid.uuid4().hex[:10])
        os.makedirs(self.base, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _compile_dir_files(self):
        compile_dir = os.path.join(self.base, ".tikz_cache")
        if not os.path.isdir(compile_dir):
            return []
        return os.listdir(compile_dir)

    def test_result_dataclass_matches_contract_fields(self):
        self.assertEqual(
            [field.name for field in fields(TikzCompileResult)][:7],
            ["ok", "b64_png", "log", "stdout", "error_kind", "error_line", "duration_s"],
        )
        # Harness v2 集成追加字段（向后兼容，位于契约字段之后）：
        # 用于把 xelatex 日志的文档行号换算成 TikZ 片段内行号
        self.assertIn("source_line_offset", [field.name for field in fields(TikzCompileResult)])
        result = TikzCompileResult(ok=False)
        self.assertIsNone(result.b64_png)
        self.assertEqual(result.log, "")
        self.assertIsNone(result.error_line)
        self.assertEqual(result.duration_s, 0.0)
        self.assertEqual(result.source_line_offset, 0)

    def test_compile_error_keeps_real_log_and_parses_line(self):
        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, FAILURE_LOG)
        ):
            result = compile_tikz(INVALID_TIKZ, self.base)
        self.assertFalse(result.ok)
        self.assertIsNone(result.b64_png)
        self.assertEqual(result.error_kind, ERROR_KIND_COMPILE_ERROR)
        self.assertEqual(result.error_line, 30)
        self.assertGreaterEqual(result.duration_s, 0.0)
        self.assertIn("Missing $ inserted", result.log)
        self.assertIn("Missing $ inserted", result.stdout)
        errors = tlt.extract_tex_errors(result.log)
        self.assertTrue(errors)
        self.assertEqual(errors[0]["category"], "missing-math")
        # 失败后临时文件必须清理（target_png_path 未指定）
        self.assertEqual(self._compile_dir_files(), [])

    def test_timeout_captures_partial_log(self):
        timeout_error = subprocess.TimeoutExpired(cmd="xelatex", timeout=5, output=FAILURE_LOG[:120])
        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", side_effect=timeout_error
        ):
            result = compile_tikz(INVALID_TIKZ, self.base, timeout=5)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_TIMEOUT)
        self.assertGreaterEqual(result.duration_s, 0.0)
        self.assertIn("Missing $ inserted", result.log)
        self.assertEqual(self._compile_dir_files(), [])

    def test_missing_xelatex(self):
        with patch("utils.tikz_ops.shutil.which", return_value=None):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_MISSING_XELATEX)
        self.assertEqual(result.log, "")
        self.assertGreaterEqual(result.duration_s, 0.0)

    def test_missing_pymupdf_and_legacy_error_string(self):
        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(0, b"Output written on abc.pdf\n")
        ), patch.dict(sys.modules, {"fitz": None}):
            result = compile_tikz(VALID_TIKZ, self.base)
            b64, error = get_tikz_image_b64(NO_SHAPE_TIKZ, self.base)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_MISSING_PYMUPDF)
        self.assertIsNone(b64)
        # latex_ops.py 依赖该历史字符串
        self.assertEqual(error, "MISSING_PYMUPDF")

    def test_get_tikz_image_b64_legacy_compile_error(self):
        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, FAILURE_LOG)
        ):
            b64, error = get_tikz_image_b64(INVALID_TIKZ, self.base)
        self.assertIsNone(b64)
        self.assertEqual(error, "COMPILE_ERROR")

    def test_log_and_stdout_truncated_to_contract_limit(self):
        huge = b"./abc.tex:30: Missing $ inserted.\n" + b"X" * 50000 + b"\nTAIL_END"
        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", return_value=_FakeCompleted(1, huge)
        ):
            result = compile_tikz(INVALID_TIKZ, self.base)
        self.assertLessEqual(len(result.log), MAX_COMPILE_LOG_CHARS)
        self.assertLessEqual(len(result.stdout), MAX_COMPILE_LOG_CHARS)
        self.assertIn("Missing $ inserted", result.log)
        self.assertIn("TAIL_END", result.log)

    def test_timeout_parameter_and_env_override(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _FakeCompleted(1, FAILURE_LOG)

        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            compile_tikz(INVALID_TIKZ, self.base, timeout=12.5)
            self.assertAlmostEqual(captured["timeout"], 12.5)
            with patch.dict(os.environ, {"TIKZ_COMPILE_TIMEOUT": "25"}):
                compile_tikz(INVALID_TIKZ, self.base)
            self.assertAlmostEqual(captured["timeout"], 25.0)

    def test_mock_success_renders_png_and_copies_target(self):
        target = os.path.join(self.base, "preview.png")

        def fake_run(command, **kwargs):
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

        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            result = compile_tikz(VALID_TIKZ, self.base, target_png_path=target)
        self.assertTrue(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_OK)
        self.assertIsNotNone(result.b64_png)
        import base64

        self.assertTrue(base64.b64decode(result.b64_png).startswith(b"\x89PNG"))
        self.assertIn("Output written", result.log)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(self._compile_dir_files(), [])


@unittest.skipUnless(shutil.which("xelatex"), "No local XeLaTeX")
class TikzCompileRealTests(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(CACHE_ROOT, "_test_tikzdiag_live_" + uuid.uuid4().hex[:10])
        os.makedirs(self.base, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_real_compile_success_returns_png(self):
        target = os.path.join(self.base, "real_preview.png")
        result = compile_tikz(VALID_TIKZ, self.base, target_png_path=target)
        self.assertTrue(result.ok, msg=result.log[-2000:])
        self.assertEqual(result.error_kind, ERROR_KIND_OK)
        self.assertGreater(result.duration_s, 0.0)
        self.assertIsNotNone(result.b64_png)
        self.assertTrue(os.path.exists(target))
        import base64

        self.assertTrue(base64.b64decode(result.b64_png).startswith(b"\x89PNG"))
        self.assertEqual(tlt.extract_tex_errors(result.log), [])

    def test_multimeter_styles_compile_in_standalone_preview(self):
        source = r'''\begin{tikzpicture}
\draw[wire] (0,0)--(1,0);
\draw[comp] (1,-.1) rectangle (1.6,.1);
\draw[wire] (1.6,0)--(2,0);
\draw[fw] (2,0) circle (.25);
\node at (2,0) {$G$};
\end{tikzpicture}'''
        result = compile_tikz(source, self.base)
        self.assertTrue(result.ok, msg=result.log[-2000:])
        self.assertTrue(result.b64_png)
        self.assertEqual(tlt.extract_tex_errors(result.log), [])

    def test_real_compile_failure_keeps_log_and_diagnoses_missing_semicolon(self):
        result = compile_tikz(INVALID_TIKZ, self.base)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, ERROR_KIND_COMPILE_ERROR)
        self.assertGreater(result.duration_s, 0.0)
        self.assertTrue(result.log.strip())
        errors = tlt.extract_tex_errors(result.log)
        self.assertTrue(errors, msg=result.log[-2000:])
        self.assertEqual(errors[0]["category"], "missing-semicolon")
        self.assertIsInstance(result.error_line, int)
        self.assertGreater(result.error_line, 0)
        summary = tlt.summarize_tex_log(result.log, source="tikz")
        self.assertIn("missing-semicolon", summary)
        self.assertIn("建议", summary)
        diagnosis = tlt.diagnose_tex_error(result.log)
        self.assertIn("missing-semicolon", diagnosis)

    def test_real_compile_failure_undefined_shape(self):
        result = compile_tikz(NO_SHAPE_TIKZ, self.base)
        self.assertFalse(result.ok)
        errors = tlt.extract_tex_errors(result.log)
        self.assertTrue(errors, msg=result.log[-2000:])
        self.assertEqual(errors[0]["category"], "no-shape-named")

    def test_real_get_tikz_image_b64_cache_semantics(self):
        code = r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"
        first, first_error = get_tikz_image_b64(code, self.base)
        self.assertIsNone(first_error)
        self.assertTrue(first)
        second, second_error = get_tikz_image_b64(code, self.base)
        self.assertIsNone(second_error)
        self.assertEqual(first, second)
        import hashlib

        cached = list(Path(self.base, '.tikz_cache').glob('*.png'))
        self.assertEqual(len(cached), 1)


if __name__ == "__main__":
    unittest.main()
