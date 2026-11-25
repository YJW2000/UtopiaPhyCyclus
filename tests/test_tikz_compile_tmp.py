"""tests/test_tikz_compile_tmp.py — TeX 编译子进程 TMP/TEMP/TMPDIR 注入测试。

背景（2026-10-20 已确证根因，minidump + 现场实验）：
xdvipdfmx 用 ``_tempnam(get_temp_dir(), "dvipdfmx.")`` 创建 PSTricks 临时文件，目录取 **TMP**
环境变量；TMP/TEMP 不可写（低完整性/权限限制）时 fopen 返回 NULL，随后**未判空**就
``fprintf(fp, ...)`` → MSVCR100 ``_invalid_parameter_noinfo`` → 0xC0000417，日志为
``Error -1073740777 (driver return code) generating output;``。任何文档（含最小 article）都会崩。

产品修复：``utils.tikz_ops._compile_tikz_once`` 与 ``services.tex_compile_service.compile_tex_preview``
给编译子进程注入工作区内专用可写 ``TMP``/``TEMP``/``TMPDIR``（绝对路径 + 写探针自检，
失败回退 ``tempfile.gettempdir()``，绝不阻断编译）。本文件全部 mock ``subprocess.run``，
**不触发真实编译**。
"""

import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import tex_compile_service  # noqa: E402
from utils import tikz_ops  # noqa: E402
from utils.tikz_ops import (  # noqa: E402
    DRIVER_TMP_DIR_NAME,
    ERROR_KIND_COMPILE_ERROR,
    _compile_tikz_fallback,
    _compile_tikz_once,
    _driver_tmp_env,
    _probe_writable_dir,
    compile_tikz,
)

CACHE_ROOT = os.path.join(ROOT, ".tikz_cache")
VALID_TIKZ = r"\begin{tikzpicture}\draw[->,thick] (0,0)--(2,0);\node at (1,1) {$F=ma$};\end{tikzpicture}"
SYNTAX_STDOUT = b"./abc.tex:30: Missing $ inserted.\nl.30 \\node {F_\n"
BROKEN_TMP = os.path.join(ROOT, ".runtime", "no_such_tmp_dir_for_test", "_drv_tmp")


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


class _BaseTmpCase(unittest.TestCase):
    def setUp(self):
        self.base = os.path.join(CACHE_ROOT, "_test_tiktmp_" + uuid.uuid4().hex[:10])
        os.makedirs(self.base, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _compile_dir(self):
        return os.path.join(self.base, ".tikz_cache")


class TestDriverTmpEnv(_BaseTmpCase):
    """`_driver_tmp_env`：目录选择 / 绝对路径 / 写探针 / 回退语义。"""

    def test_injects_absolute_created_three_keys(self):
        env = _driver_tmp_env(self._compile_dir())
        self.assertEqual(set(env.keys()), {"TMP", "TEMP", "TMPDIR"})
        for key in ("TMP", "TEMP", "TMPDIR"):
            self.assertTrue(os.path.isabs(env[key]), msg=key)
        self.assertEqual(env["TMP"], env["TEMP"])
        self.assertEqual(env["TMP"], env["TMPDIR"])
        expected_parent = os.path.abspath(os.path.join(self._compile_dir(), DRIVER_TMP_DIR_NAME))
        self.assertEqual(os.path.dirname(env["TMP"]), expected_parent)
        self.assertTrue(os.path.isdir(env["TMP"]))
        # 探针文件写入后必须删除，不残留
        self.assertEqual([n for n in os.listdir(env["TMP"]) if n.startswith("write_probe_")], [])

    def test_each_compile_gets_unique_dir(self):
        first = _driver_tmp_env(self._compile_dir())
        second = _driver_tmp_env(self._compile_dir())
        self.assertNotEqual(first["TMP"], second["TMP"])

    def test_probe_failure_falls_back_to_system_tempdir(self):
        real_probe = tikz_ops._probe_writable_dir
        calls = []

        def fake_probe(directory):
            calls.append(directory)
            if len(calls) == 1:  # 首选 <compile_dir>/_drv_tmp/<uuid> 不可写
                return None
            return real_probe(directory)

        with patch.object(tikz_ops, "_probe_writable_dir", side_effect=fake_probe):
            env = _driver_tmp_env(self._compile_dir())
        self.assertEqual(len(calls), 2)
        self.assertEqual(env["TMP"], os.path.abspath(tempfile.gettempdir()))

    def test_all_probes_fail_returns_empty_without_exception(self):
        with patch.object(tikz_ops, "_probe_writable_dir", return_value=None):
            self.assertEqual(_driver_tmp_env(self._compile_dir()), {})

    def test_probe_writable_dir_never_raises(self):
        self.assertIsNone(_probe_writable_dir("\x00invalid-path"))
        with patch.object(tikz_ops, "ensure_dir", side_effect=OSError("boom")):
            self.assertIsNone(_probe_writable_dir(os.path.join(self.base, "_x")))


class TestCompileSubprocessEnv(_BaseTmpCase):
    """`_compile_tikz_once` 传给 `subprocess.run` 的 env：注入 TMP/TEMP/TMPDIR 并正确合并。"""

    def _run_once(self, extra_env=None):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs.get("env")
            env = kwargs.get("env") or {}
            captured["tmp_exists"] = bool(env.get("TMP")) and os.path.isdir(env["TMP"])
            captured["cwd"] = kwargs.get("cwd")
            return _FakeCompleted(1, SYNTAX_STDOUT)

        with patch("utils.tikz_ops.shutil.which", return_value="xelatex"), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            result = _compile_tikz_once(VALID_TIKZ, self.base, extra_env=extra_env)
        return captured, result

    def test_subprocess_env_has_injected_tmp(self):
        captured, result = self._run_once()
        env = captured["env"]
        self.assertIsNotNone(env)
        for key in ("TMP", "TEMP", "TMPDIR"):
            self.assertIsInstance(env.get(key), str, msg=key)
            self.assertTrue(os.path.isabs(env[key]), msg=key)
        self.assertEqual(env["TMP"], env["TEMP"])
        self.assertEqual(env["TMP"], env["TMPDIR"])
        self.assertIn(os.path.abspath(self.base), env["TMP"])
        self.assertIn(DRIVER_TMP_DIR_NAME, env["TMP"])
        self.assertTrue(captured["tmp_exists"], "注入的 TMP 目录在子进程启动时必须已创建")
        # 既有行为不变：语法错误 → COMPILE_ERROR，不重试
        self.assertEqual(result.error_kind, ERROR_KIND_COMPILE_ERROR)
        self.assertEqual(result.compile_retries, 0)
        # 编译结束后本次独占的驱动临时目录应被清理
        self.assertFalse(os.path.isdir(env["TMP"]))
        self.assertEqual(os.listdir(self._compile_dir()), [])

    def test_injected_tmp_overrides_broken_inherited_env(self):
        with patch.dict(
            os.environ, {"TMP": BROKEN_TMP, "TEMP": BROKEN_TMP, "TMPDIR": BROKEN_TMP}
        ):
            captured, _ = self._run_once()
        env = captured["env"]
        self.assertNotEqual(env["TMP"], BROKEN_TMP)
        self.assertTrue(captured["tmp_exists"], "注入的 TMP 在子进程启动时必须存在（已覆盖继承的坏值）")
        # 其余继承环境变量（PATH 等）仍保留
        self.assertIn("PATH", env)

    def test_extra_env_merges_and_keeps_texmfvar(self):
        lua_cache = os.path.join(self.base, "lua_cache")
        captured, _ = self._run_once(extra_env={"TEXMFVAR": lua_cache, "TEXMFCACHE": lua_cache})
        env = captured["env"]
        # lualatex 兜底的 TEXMFVAR/TEXMFCACHE 必须保留，且不被 TMP 注入覆盖
        self.assertEqual(env["TEXMFVAR"], lua_cache)
        self.assertEqual(env["TEXMFCACHE"], lua_cache)
        self.assertNotEqual(env["TMP"], lua_cache)
        self.assertTrue(captured["tmp_exists"])

    def test_compile_tikz_wrapper_also_injects_tmp(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeCompleted(1, SYNTAX_STDOUT)

        with patch("utils.tikz_ops.shutil.which", side_effect=lambda name: "xelatex" if name == "xelatex" else None), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            result = compile_tikz(VALID_TIKZ, self.base)
        self.assertEqual(result.error_kind, ERROR_KIND_COMPILE_ERROR)
        env = captured["env"]
        self.assertIn(DRIVER_TMP_DIR_NAME, env["TMP"])
        self.assertTrue(os.path.isabs(env["TMP"]))

    def test_lualatex_fallback_keeps_tmp_and_texmfvar(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs.get("env")
            return _FakeCompleted(1, SYNTAX_STDOUT)

        with patch("utils.tikz_ops.shutil.which", side_effect=lambda name: "lualatex" if name == "lualatex" else None), patch(
            "utils.tikz_ops.subprocess.run", side_effect=fake_run
        ):
            result = _compile_tikz_fallback(VALID_TIKZ, self.base, None, None)
        self.assertIsNotNone(result)
        env = captured["env"]
        self.assertIn(DRIVER_TMP_DIR_NAME, env["TMP"])
        self.assertIn("_luaotfload_cache", env["TEXMFVAR"])
        self.assertEqual(env["TEXMFVAR"], env["TEXMFCACHE"])


class TestTexCompileServiceEnv(_BaseTmpCase):
    """`services.tex_compile_service.compile_tex_preview` 的 env 注入（源码/PDF 预览路径）。"""

    def _fake_run(self, captured):
        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs.get("env")
            captured["cwd"] = kwargs.get("cwd")
            env = kwargs.get("env") or {}
            captured["tmp_exists"] = bool(env.get("TMP")) and os.path.isdir(env["TMP"])
            output_arg = next(c for c in command if c.startswith("-output-directory="))
            out_dir = Path(kwargs.get("cwd")) / output_arg.split("=", 1)[1]
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "preview.pdf").write_bytes(b"%PDF-1.4\n")
            return _FakeCompleted(0, "Output written on preview.pdf")

        return fake_run

    def test_preview_compile_injects_absolute_tmp(self):
        captured = {}
        source = r"\documentclass{article}\begin{document}Hi\end{document}"
        with patch("services.tex_compile_service.shutil.which", return_value="xelatex"), patch(
            "services.tex_compile_service.subprocess.run", side_effect=self._fake_run(captured)
        ):
            result = tex_compile_service.compile_tex_preview(source, self.base, {})
        self.assertTrue(result.get("ok"), msg=result)
        env = captured["env"]
        for key in ("TMP", "TEMP", "TMPDIR"):
            self.assertTrue(os.path.isabs(env[key]), msg=key)
        self.assertEqual(env["TMP"], env["TEMP"])
        self.assertEqual(env["TMP"], env["TMPDIR"])
        self.assertIn(os.path.abspath(self.base), env["TMP"])
        self.assertIn("tex-entry", env["TMP"])
        self.assertTrue(captured["tmp_exists"])
        # 既有 env 语义保持
        self.assertIn("TEXINPUTS", env)

    def test_preview_compile_overrides_broken_inherited_tmp(self):
        captured = {}
        source = r"\documentclass{article}\begin{document}Hi\end{document}"
        with patch.dict(
            os.environ, {"TMP": BROKEN_TMP, "TEMP": BROKEN_TMP, "TMPDIR": BROKEN_TMP}
        ), patch("services.tex_compile_service.shutil.which", return_value="xelatex"), patch(
            "services.tex_compile_service.subprocess.run", side_effect=self._fake_run(captured)
        ):
            result = tex_compile_service.compile_tex_preview(source, self.base, {})
        self.assertTrue(result.get("ok"), msg=result)
        self.assertNotEqual(captured["env"]["TMP"], BROKEN_TMP)
        self.assertTrue(captured["tmp_exists"])

    def test_preview_compile_env_injection_failure_does_not_break(self):
        """注入异常时不得影响既有错误返回结构（这里走"未找到编译器"分支验证结构不变）。"""
        with patch("services.tex_compile_service.shutil.which", return_value=None):
            result = tex_compile_service.compile_tex_preview("正文", self.base, {})
        self.assertFalse(result.get("ok"))
        self.assertIn("PATH", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
