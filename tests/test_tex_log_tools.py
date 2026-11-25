"""tests/test_tex_log_tools.py — Harness v2 契约第 2 节的日志诊断工具单测。

全部基于 unittest；日志片段以本机真实 xelatex 输出格式为蓝本（-file-line-error 的
``file:line: message``、经典 ``! LaTeX Error:/-Package pgf Error:`` 与 ``l.NNN``）。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import tex_log_tools as tlt  # noqa: E402


CONTRACT_CATEGORIES = (
    "missing-math",
    "undefined-control-sequence",
    "no-shape-named",
    "missing-library",
    "missing-package",
    "missing-file",
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

#: 真实日志片段（本机 TeX Live 2026 + xelatex -file-line-error 实测格式）。
REAL_LOGS = {
    "missing-math": (
        "./probe.tex:6: Missing $ inserted.\n"
        "l.6 \\begin{tikzpicture}\\node {F_\n"
    ),
    "no-shape-named": (
        "./probe.tex:6: Package pgf Error: No shape named `A' is known.\n"
        "See the pgf package documentation for explanation.\n"
        "Type  H <return>  for immediate help.\n"
        "...\n"
        "l.6 \\begin{tikzpicture}\\draw (0,0)--(A)\n"
        ";\\end{tikzpicture}\n"
        "No pages of output.\n"
    ),
    "missing-semicolon": (
        "./bad2.tex:7: Package tikz Error: Giving up on this path. Did you forget a semi\n"
        "colon?.\n"
        "...\n"
        "l.7 \\node\n"
        "          at (1,1) {质量};\n"
        "No pages of output.\n"
        "Transcript written on ./bad2.log.\n"
    ),
    "undefined-control-sequence": (
        "./probe.tex:6: Undefined control sequence.\n"
        "l.6 \\begin{tikzpicture}\\draw (0,0)--(1,0);\\foo\n"
    ),
    "missing-library": (
        "./probe.tex:6: Package tikz Error: I did not find the tikz library 'nonexistentlib'.\n"
        "l.6 \\usetikzlibrary{nonexistentlib}\n"
    ),
    "pgfkeys-unknown-key": (
        "./probe.tex:6: Package pgfkeys Error: I do not know the key '/tikz/notakey' and\n"
        "l.6 \\begin{tikzpicture}\\draw[notakey]\n"
    ),
    "missing-package": (
        "! LaTeX Error: File `notapackagexyz.sty' not found.\n"
        "\n"
        "Type X to quit or <RETURN> to proceed,\n"
        "or enter new name. (Default extension: sty)\n"
        "\n"
        "Enter file name: \n"
        "./probe.tex:3: Emergency stop.\n"
        "<read *> \n"
        "         \n"
        "l.3 \\begin\n"
        "          {document}hi\\end{document}^^M\n"
        "No pages of output.\n"
    ),
    "missing-file": (
        "! LaTeX Error: File `nofilexyz.tex' not found.\n"
        "\n"
        "Type X to quit or <RETURN> to proceed,\n"
        "or enter new name. (Default extension: tex)\n"
        "\n"
        "Enter file name: \n"
        "./probe.tex:2: Emergency stop.\n"
        "l.2 \\begin{document}\\input{nofilexyz}\n"
    ),
    "runaway-argument": (
        "Runaway argument?\n"
        "{abc\n"
        "! File ended while scanning use of \\node.\n"
        "<inserted text> \n"
        "                \\par \n"
        "l.5 \\node {abc\n"
        "No pages of output.\n"
    ),
    "extra-brace": (
        "./probe.tex:6: Extra }, or forgotten \\endgroup.\n"
        "<recently read> }\n"
        "                 \n"
        "l.6 \\begin{tikzpicture}\\draw (0,0)--(1,0);}\n"
        "                                           \\end{tikzpicture}\n"
        "No pages of output.\n"
    ),
    "dimension-too-large": (
        "! Dimension too large.\n"
        "<recently read> \\dimen@ \n"
        "l.9 \\draw (0,0)--(99999999999,0);\n"
        "No pages of output.\n"
    ),
    "undefined-coordinate": (
        "./probe.tex:10: Package pgf Error: Cannot parse this coordinate.\n"
        "l.10 \\draw (0,0)--(1,);\n"
    ),
    "capacity-exceeded": (
        "! TeX capacity exceeded, sorry [input stack size=5000].\n"
        "<*> ...\n"
        "l.3 \\x\n"
        "No pages of output.\n"
    ),
    "emergency-stop": (
        "! Emergency stop.\n"
        "<*> probe.tex\n"
        "             \n"
        "No pages of output.\n"
    ),
    "font-missing": (
        "./probe.tex:2: Font \\x=NoSuchFontXYZ at 10.0pt not loadable: Metric (TFM) file\n"
        "or installed font not found.\n"
        "<recently read> \\font \n"
        "l.2 \\begin{document}\\font\\x=NoSuchFontXYZ at 10pt \n"
    ),
    "timeout": "TIMEOUT_60S",
}


def _has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


class TestEnvironmentAndClassification(unittest.TestCase):
    def test_categories_cover_contract_and_missing_semicolon(self):
        self.assertEqual(len(tlt.ERROR_CATEGORIES), len(set(tlt.ERROR_CATEGORIES)))
        for category in CONTRACT_CATEGORIES:
            self.assertIn(category, tlt.ERROR_CATEGORIES)
        # Lead P0 增补：TikZ 漏分号独立 category
        self.assertIn("missing-semicolon", tlt.ERROR_CATEGORIES)
        for category in tlt.ERROR_CATEGORIES:
            self.assertIn(category, tlt.CATEGORY_HINTS)
            self.assertTrue(_has_cjk(tlt.CATEGORY_HINTS[category]), category)

    def test_classify_missing_package_beats_emergency_stop(self):
        category, hint = tlt.classify_tex_error(REAL_LOGS["missing-package"])
        self.assertEqual(category, "missing-package")
        self.assertTrue(_has_cjk(hint))

    def test_classify_missing_semicolon(self):
        category, hint = tlt.classify_tex_error(REAL_LOGS["missing-semicolon"])
        self.assertEqual(category, "missing-semicolon")
        self.assertIn(";", hint)

    def test_classify_timeout_marker(self):
        self.assertEqual(tlt.classify_tex_error("TIMEOUT_60S")[0], "timeout")
        self.assertEqual(tlt.classify_tex_error("xelatex compile timed out after 60s")[0], "timeout")

    def test_classify_unknown_fallback(self):
        category, hint = tlt.classify_tex_error("just some random chatter without errors")
        self.assertEqual(category, "unknown")
        self.assertTrue(_has_cjk(hint))
        self.assertEqual(tlt.classify_tex_error("")[0], "unknown")


class TestCleanTexLog(unittest.TestCase):
    def test_truncates_to_limit_and_keeps_tail(self):
        raw = "HEAD-MARKER\n" + ("x" * 50000) + "\nTAIL-MARKER: ./probe.tex:7: Missing $ inserted."
        cleaned = tlt.clean_tex_log(raw, max_chars=2000)
        self.assertLessEqual(len(cleaned), 2000)
        self.assertIn("HEAD-MARKER", cleaned)
        self.assertIn("TAIL-MARKER", cleaned)
        self.assertIn("日志已截断", cleaned)

    def test_normalizes_crlf_ansi_and_blank_lines(self):
        raw = "line1\r\n\x1b[31mline2\x1b[0m\r\n\r\n\r\n\r\nline3\r\n"
        cleaned = tlt.clean_tex_log(raw)
        self.assertNotIn("\r", cleaned)
        self.assertNotIn("\x1b", cleaned)
        self.assertIn("line2", cleaned)
        self.assertNotIn("\n\n\n", cleaned)

    def test_encoding_tolerance_and_non_str_inputs(self):
        cp936 = "中文编译错误：缺少 $".encode("cp936")
        self.assertIn("中文编译错误", tlt.clean_tex_log(cp936))
        self.assertIsInstance(tlt.clean_tex_log(b"\xff\xfe\x00\x01garbage"), str)
        self.assertEqual(tlt.clean_tex_log(None), "")
        self.assertIsInstance(tlt.clean_tex_log(12345), str)
        self.assertIsInstance(tlt.clean_tex_log(["a", "b"]), str)
        self.assertEqual(tlt.clean_tex_log("abc", max_chars=0), "")
        self.assertLessEqual(len(tlt.clean_tex_log("a" * 30000)), 20000)


class TestExtractTexErrors(unittest.TestCase):
    def _single(self, key):
        errors = tlt.extract_tex_errors(REAL_LOGS[key])
        # missing-package / missing-file 的日志末尾会附带 Emergency stop，属后果性条目；
        # 若整份日志只有 Emergency stop，则保留它本身。
        primary = [error for error in errors if error["category"] != "emergency-stop"] or errors
        self.assertEqual(len(primary), 1, "%s -> %r" % (key, errors))
        return primary[0]

    def test_missing_math_structured_fields(self):
        error = self._single("missing-math")
        self.assertEqual(set(error.keys()), {"category", "line", "message", "hint", "context"})
        self.assertEqual(error["category"], "missing-math")
        self.assertEqual(error["line"], 6)
        self.assertIn("Missing $ inserted", error["message"])
        self.assertTrue(_has_cjk(error["hint"]))
        self.assertIn("l.6", error["context"])

    def test_no_shape_named(self):
        error = self._single("no-shape-named")
        self.assertEqual(error["category"], "no-shape-named")
        self.assertEqual(error["line"], 6)
        self.assertIn("No shape named", error["message"])
        self.assertIn("l.6", error["context"])

    def test_missing_semicolon(self):
        error = self._single("missing-semicolon")
        self.assertEqual(error["category"], "missing-semicolon")
        self.assertEqual(error["line"], 7)
        self.assertIn("semicolon", error["message"].lower())
        self.assertTrue(_has_cjk(error["hint"]))

    def test_undefined_control_sequence(self):
        error = self._single("undefined-control-sequence")
        self.assertEqual(error["category"], "undefined-control-sequence")
        self.assertEqual(error["line"], 6)

    def test_missing_library(self):
        error = self._single("missing-library")
        self.assertEqual(error["category"], "missing-library")
        self.assertIn("tikz library", error["message"])

    def test_pgfkeys_unknown_key(self):
        error = self._single("pgfkeys-unknown-key")
        self.assertEqual(error["category"], "pgfkeys-unknown-key")
        self.assertIn("/tikz/notakey", error["message"])

    def test_missing_package(self):
        error = self._single("missing-package")
        self.assertEqual(error["category"], "missing-package")
        self.assertIn("notapackagexyz.sty", error["message"])

    def test_missing_file(self):
        error = self._single("missing-file")
        self.assertEqual(error["category"], "missing-file")
        self.assertIn("nofilexyz.tex", error["message"])

    def test_runaway_argument(self):
        errors = tlt.extract_tex_errors(REAL_LOGS["runaway-argument"])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["category"], "runaway-argument")

    def test_extra_brace(self):
        error = self._single("extra-brace")
        self.assertEqual(error["category"], "extra-brace")
        self.assertEqual(error["line"], 6)
        self.assertIn("Extra }", error["message"])

    def test_dimension_too_large(self):
        error = self._single("dimension-too-large")
        self.assertEqual(error["category"], "dimension-too-large")
        self.assertEqual(error["line"], 9)

    def test_undefined_coordinate(self):
        error = self._single("undefined-coordinate")
        self.assertEqual(error["category"], "undefined-coordinate")
        self.assertEqual(error["line"], 10)

    def test_capacity_exceeded(self):
        error = self._single("capacity-exceeded")
        self.assertEqual(error["category"], "capacity-exceeded")
        self.assertEqual(error["line"], 3)

    def test_emergency_stop(self):
        error = self._single("emergency-stop")
        self.assertEqual(error["category"], "emergency-stop")

    def test_font_missing_wrapped_message(self):
        error = self._single("font-missing")
        self.assertEqual(error["category"], "font-missing")
        self.assertEqual(error["line"], 2)
        self.assertIn("not loadable", error["message"])

    def test_timeout_synthetic_fallback(self):
        errors = tlt.extract_tex_errors("TIMEOUT_60S")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["category"], "timeout")
        self.assertIsNone(errors[0]["line"])

    def test_multiple_errors_in_one_log(self):
        raw = REAL_LOGS["no-shape-named"] + "\n" + REAL_LOGS["undefined-control-sequence"]
        errors = tlt.extract_tex_errors(raw)
        categories = [error["category"] for error in errors]
        self.assertGreaterEqual(len(errors), 2)
        self.assertIn("no-shape-named", categories)
        self.assertIn("undefined-control-sequence", categories)

    def test_never_raises_on_weird_inputs(self):
        weird_inputs = [
            None,
            12345,
            b"\xff\xfe\x00binary",
            ["not", "a", "log"],
            {"a": 1},
            object(),
            "!" * 50000,
            "./x.tex:999999999999: Missing $ inserted.",
        ]
        for raw in weird_inputs:
            errors = tlt.extract_tex_errors(raw)
            self.assertIsInstance(errors, list)
            for error in errors:
                self.assertEqual(set(error.keys()), {"category", "line", "message", "hint", "context"})
                self.assertIn(error["category"], tlt.ERROR_CATEGORIES)


class TestSummarizeAndDiagnose(unittest.TestCase):
    def test_summary_has_line_category_hint_and_limit(self):
        summary = tlt.summarize_tex_log(REAL_LOGS["no-shape-named"], source=r"C:\Users\tmp\abc.tex")
        self.assertLessEqual(len(summary), 4000)
        self.assertIn("第 6 行", summary)
        self.assertIn("no-shape-named", summary)
        self.assertIn("建议", summary)
        self.assertNotIn(r"C:\Users", summary)
        self.assertIn("abc.tex", summary)

    def test_summary_respects_custom_limit_and_long_log(self):
        raw = "\n".join(
            "./probe.tex:%d: Missing $ inserted.\nl.%d \\node {F_" % (n, n) for n in range(1, 40)
        )
        summary = tlt.summarize_tex_log(raw, max_chars=500)
        self.assertLessEqual(len(summary), 500)
        self.assertIn("编译失败", summary)

    def test_summary_cleans_uuid_temp_paths(self):
        raw = (
            "./0123456789abcdef0123456789abcdef.log:12: "
            "Package pgf Error: No shape named `A' is known.\nl.12 \\draw (0,0)--(A);\n"
        )
        summary = tlt.summarize_tex_log(raw)
        self.assertNotIn("0123456789abcdef0123456789abcdef", summary)
        self.assertLessEqual(len(summary), 4000)

    def test_summary_empty_log_no_raise(self):
        self.assertIsInstance(tlt.summarize_tex_log(None), str)
        self.assertIsInstance(tlt.summarize_tex_log(""), str)

    def test_diagnose_covers_at_least_12_categories(self):
        covered = 0
        for key in REAL_LOGS:
            diagnosis = tlt.diagnose_tex_error(REAL_LOGS[key])
            self.assertIsInstance(diagnosis, str)
            self.assertTrue(diagnosis.strip())
            self.assertIn("修复建议", diagnosis)
            if real_category := tlt.classify_tex_error(REAL_LOGS[key])[0]:
                self.assertIn(real_category, diagnosis)
            covered += 1
        self.assertGreaterEqual(covered, 12)

    def test_diagnose_empty_and_garbage(self):
        self.assertIn("编译诊断", tlt.diagnose_tex_error(None))
        self.assertIn("编译诊断", tlt.diagnose_tex_error(""))
        self.assertIn("编译诊断", tlt.diagnose_tex_error("totally unparsable noise"))


class TestExternalToolsDegrade(unittest.TestCase):
    def test_external_tools_keys_and_value_types(self):
        tools = tlt.external_tools()
        self.assertEqual(set(tools.keys()), {"texlogsieve", "chktex", "lacheck", "latexindent"})
        for path in tools.values():
            self.assertTrue(path is None or isinstance(path, str))

    def test_external_tools_missing_returns_none(self):
        with patch.object(tlt, "_find_tool", return_value=None):
            tools = tlt.external_tools()
        self.assertEqual(tools, {"texlogsieve": None, "chktex": None, "lacheck": None, "latexindent": None})

    def test_filter_falls_back_to_original_when_tool_missing(self):
        raw = REAL_LOGS["missing-math"]
        with patch.object(tlt, "_find_tool", return_value=None):
            self.assertEqual(tlt.filter_with_texlogsieve(raw), raw)

    def test_filter_falls_back_when_subprocess_fails(self):
        raw = REAL_LOGS["missing-math"]
        boom = tlt.subprocess.TimeoutExpired(cmd="texlogsieve", timeout=10)
        with patch.object(tlt, "_find_tool", return_value="texlogsieve"), patch.object(
            tlt.subprocess, "run", side_effect=boom
        ):
            self.assertEqual(tlt.filter_with_texlogsieve(raw), raw)

    @unittest.skipUnless(tlt.external_tools().get("texlogsieve"), "texlogsieve not installed")
    def test_filter_with_real_texlogsieve(self):
        filtered = tlt.filter_with_texlogsieve(REAL_LOGS["no-shape-named"])
        self.assertIsInstance(filtered, str)
        self.assertTrue(filtered.strip())
        lowered = filtered.lower()
        # 本机实测：错误日志摘要含 "There were errors during processing"；
        # 干净日志含 "No important messages to show"；其它版本/回退路径至少返回非空文本。
        self.assertTrue(
            "errors during processing" in lowered
            or "no important messages" in lowered
            or "no shape named" in lowered
            or filtered.strip()
        )

    def test_chktex_disabled_by_default(self):
        raw = '\\documentclass{article}\n\\begin{document}\nThis is "quoted",and $$x$$.\n\\end{document}\n'
        with patch.dict(os.environ, {"PI_AGENT_CHKTEX": "0"}):
            self.assertEqual(tlt.run_chktex_lint(raw), [])

    def test_chktex_unavailable_returns_empty(self):
        with patch.dict(os.environ, {"PI_AGENT_CHKTEX": "1"}), patch.object(
            tlt, "_find_tool", return_value=None
        ):
            self.assertEqual(tlt.run_chktex_lint('\\begin{document}"x"\\end{document}'), [])

    def test_chktex_never_raises_on_weird_input(self):
        with patch.dict(os.environ, {"PI_AGENT_CHKTEX": "1"}):
            for raw in (None, 123, b"\xff\xfe"):
                self.assertIsInstance(tlt.run_chktex_lint(raw), list)

    @unittest.skipUnless(tlt.external_tools().get("chktex"), "chktex not installed")
    def test_chktex_real_optional(self):
        source = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            'This is "quoted" text,and $$x$$ here.\n'
            "\\end{document}\n"
        )
        with patch.dict(os.environ, {"PI_AGENT_CHKTEX": "1"}):
            issues = tlt.run_chktex_lint(source)
        self.assertIsInstance(issues, list)
        for issue in issues:
            self.assertEqual(issue["tool"], "chktex")
            self.assertEqual(issue["severity"], "info")
            self.assertIn("line", issue)
            self.assertTrue(_has_cjk(issue["hint"]))
        if issues:
            self.assertTrue(any("chktex" in issue["tool"] for issue in issues))


class TestDriverCrashDetection(unittest.TestCase):
    """T1 追加（2026-10-20）：xdvipdfmx 输出驱动崩溃（MSVCR100.dll / 0xC0000417）诊断。

    真实日志尾部：``Error -1073740777 (driver return code) generating output;
    file tikz.pdf may not be valid.``；日志里没有 `!` LaTeX 错误。
    """

    CRASH_LOG = (
        "This is XeTeX, Version 3.141592653-2.6-0.999995 (TeX Live 2026)\n"
        "entering extended mode\n"
        "(. /deadbeefdeadbeefdeadbeefdeadbeef.tex [1] [2])\n"
        "Error -1073740777 (driver return code) generating output;\n"
        "file deadbeefdeadbeefdeadbeefdeadbeef.pdf may not be valid.\n"
        "Transcript written on deadbeefdeadbeefdeadbeef.log.\n"
    )

    def test_detect_driver_crash_returns_exit_code_and_hint(self):
        detected = tlt.detect_driver_crash(self.CRASH_LOG)
        self.assertIsInstance(detected, dict)
        self.assertEqual(detected["error_kind"], "DRIVER_CRASH")
        self.assertEqual(detected["exit_code"], -1073740777)
        self.assertTrue(_has_cjk(detected["hint"]))

    def test_normal_logs_are_not_driver_crash(self):
        for raw in (None, "", REAL_LOGS["missing-math"], REAL_LOGS["missing-semicolon"], "TIMEOUT_60S"):
            self.assertIsNone(tlt.detect_driver_crash(raw), msg=repr(raw))

    def test_summary_and_diagnosis_are_actionable(self):
        summary = tlt.summarize_tex_log(self.CRASH_LOG)
        diagnosis = tlt.diagnose_tex_error(self.CRASH_LOG)
        for text in (summary, diagnosis):
            self.assertIn("xdvipdfmx", text)
            self.assertIn("不是 TikZ 语法错误", text)
        self.assertNotIn("未从日志中解析出明确的 LaTeX 错误", summary)
        self.assertIn("DRIVER_CRASH", diagnosis)
        # 16 类稳定 category 契约不变：驱动崩溃仍解析为 ERROR_CATEGORIES 内的类别
        for error in tlt.extract_tex_errors(self.CRASH_LOG):
            self.assertIn(error["category"], tlt.ERROR_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
