from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image

from services.local_codex import run_local_codex_json
from services.pdf_import_images import crop_pdf_region
from services.pdf_selection_edit import edit_pdf_selection, _editable_range
from services.tex_compile_service import cleanup_preview_cache, compile_tex_preview


def text_rect(page, text):
    bounds = page.search_for(text)[0] + (-1, -1, 1, 1)
    bounds = bounds * page.rotation_matrix
    return [bounds.x0 / page.rect.width, bounds.y0 / page.rect.height,
            bounds.x1 / page.rect.width, bounds.y1 / page.rect.height]


def sample_pdf():
    with pymupdf.open() as document:
        for _ in range(2):
            page = document.new_page(width=200, height=120)
            page.insert_text((12, 30), "Mass: 2 kg. Speed: 5 m/s.")
        rect = text_rect(document[1], "2 kg")
        return document.tobytes(), rect


class SelectionEditTests(unittest.TestCase):
    def test_checkout_cleanup_keeps_the_active_preview_and_removes_idle_one(self):
        with TemporaryDirectory() as folder:
            cache = Path(folder) / ".tikz_cache" / "tex-entry"
            idle = cache / "preview-idle"
            idle.mkdir(parents=True)
            external_run = subprocess.run
            def run(args, **kwargs):
                directories = [entry for entry in cache.glob("preview-*") if entry != idle and entry.is_dir()]
                self.assertEqual(len(directories), 1)
                # First a different Python/Streamlit process, then a rerun in
                # this same process. Both must leave the live build alone.
                external_run([sys.executable, "-c", "import sys; from services.tex_compile_service import "
                              "cleanup_preview_cache; cleanup_preview_cache(sys.argv[1])", folder], check=True)
                self.assertEqual(cleanup_preview_cache(folder), 0)
                self.assertTrue(directories[0].is_dir())
                self.assertFalse(idle.exists())
                (directories[0] / "preview.pdf").write_bytes(b"mock pdf")
                return subprocess.CompletedProcess(args, 0, "", "")
            with patch("services.tex_compile_service.shutil.which", return_value="xelatex"), patch(
                    "services.tex_compile_service.subprocess.run", run):
                result = compile_tex_preview(r"\documentclass{article}\begin{document}a\end{document}", folder, {})
            self.assertTrue(result["ok"])
            self.assertFalse(list(cache.iterdir()))

    def test_literal_fallback_changes_only_selected_parameter(self):
        pdf, rect = sample_pdf()
        code = "Mass: 2 kg. Speed: 5 m/s."
        with patch("services.pdf_selection_edit.run_local_codex_json", return_value={"before": "2", "after": "3"}):
            self.assertEqual(edit_pdf_selection(code, pdf, 2, rect, "增加质量"), code.replace("2", "3"))

        with patch("services.pdf_selection_edit.run_local_codex_json", return_value={"before": "5", "after": "9"}):
            with self.assertRaisesRegex(ValueError, "之外"):
                edit_pdf_selection(code, pdf, 2, rect, "增加质量")
        with patch("services.pdf_selection_edit.run_local_codex_json") as agent:
            with self.assertRaisesRegex(ValueError, "唯一"):
                edit_pdf_selection(code + "\n" + code, pdf, 2, rect, "增加质量")
            agent.assert_not_called()

    def test_no_agent_call_for_invalid_stale_or_blank_selection(self):
        pdf, rect = sample_pdf()
        with patch("services.pdf_selection_edit.run_local_codex_json") as agent:
            for page, box in [(0, rect), (1, [0, 0, 2, 1]), (1, [0, 0, .02, .02])]:
                with self.subTest(page=page, box=box), self.assertRaises(ValueError):
                    edit_pdf_selection("draft", pdf, page, box, "edit")
            with self.assertRaisesRegex(ValueError, "源码已变化"):
                edit_pdf_selection("draft", pdf, 1, rect, "edit", source_map={"source_hash": b"old"})
            agent.assert_not_called()

    def test_captured_environment_narrows_math_and_refuses_duplicates(self):
        source = "\\begin{problem}\n质量 $m=2\\,\\mathrm{kg}$。\n\\end{problem}\n"
        start, end = _editable_range(source, [2], "m = 2 kg")
        self.assertEqual(source[start:end], source.splitlines()[1])
        with self.assertRaisesRegex(ValueError, "多次"):
            _editable_range(source.replace("质量", "质量质量"), [2], "质量")

    @unittest.skipUnless(shutil.which("xelatex") and shutil.which("synctex"), "requires XeLaTeX/SyncTeX")
    def test_inverse_search_disambiguates_identical_pages(self):
        source = "\\documentclass{article}\n\\begin{document}\nMass: 2 kg.\n\\newpage\nMass: 2 kg.\n\\end{document}"
        with TemporaryDirectory() as folder:
            result = compile_tex_preview(source, folder, {})
        self.assertTrue(result["ok"], result.get("error"))
        with pymupdf.open(stream=result["pdf"]) as document:
            rect = text_rect(document[1], "2 kg")
        with patch("services.pdf_selection_edit.run_local_codex_json", return_value={"before": "2", "after": "3"}):
            updated = edit_pdf_selection(source, result["pdf"], 2, rect, "改为 3 kg", source_map=result["source_map"])
        self.assertEqual(updated, source.replace("\\newpage\nMass: 2 kg.", "\\newpage\nMass: 3 kg."))

    def test_cli_uses_stdin_login_and_isolated_images(self):
        seen = []
        def run(args, *, cwd, input, **kwargs):
            self.assertIn("--sandbox", args)
            self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
            self.assertEqual(input.decode(), "含 & 的指令")
            self.assertNotIn("含 & 的指令", args)
            self.assertEqual((cwd / "image-2.png").read_bytes(), b"two")
            self.assertEqual(json.loads((cwd / "schema.json").read_text()), {"type": "object"})
            (cwd / "result.json").write_text('{"ok":true}')
            seen.append(cwd)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        with patch("services.local_codex.shutil.which", return_value="codex"), patch("services.local_codex.subprocess.run", run):
            self.assertEqual(run_local_codex_json("含 & 的指令", {"type": "object"}, [b"one", b"two"]), {"ok": True})
        self.assertFalse(seen[0].exists())
        with patch("services.local_codex._find_codex", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "codex login"):
                run_local_codex_json("test", {}, [])

    @unittest.skipUnless(shutil.which("xelatex") and shutil.which("synctex"), "requires XeLaTeX/SyncTeX")
    def test_real_bank_fragment_without_main_tex_hits_text_choices_tikz_and_solution(self):
        source = r"""\begin{problem}{2026}{M}{测试}{1}{力学}
小球的质量为 $m=2\,\mathrm{kg}$。
若水平拉力恒定，求加速度。
\begin{choices}
\choice{静止不动}\choice{加速运动}
\end{choices}
\begin{tikzpicture}
\draw[->] (0,0)--(2,0) node[right]{原点};
\end{tikzpicture}
\end{problem}
\begin{answer}
正确选项是乙。
\end{answer}
\begin{solutions}
第一步，由牛顿定律列方程。
第二步，代入速度参数。
\end{solutions}"""
        with TemporaryDirectory() as folder:
            shutil.copy(Path(__file__).resolve().parents[1] / "UtopiaPhyCyclus_book.cls", folder)
            result = compile_tex_preview(source, folder, {})
        self.assertTrue(result["ok"], result.get("error"))
        with pymupdf.open(stream=result["pdf"]) as document:
            for label, line in [("小球的质量为", 1), ("求加速度", 2), ("加速运动", 4),
                                ("原点", 7), ("正确选项是乙", 11), ("代入速度参数", 15)]:
                rect = text_rect(document[0], label)
                def edit(prompt, schema, images):
                    data = json.loads(prompt.split("\n", 1)[1])
                    self.assertIn(source.splitlines()[line], data["可编辑的原始LaTeX"])
                    self.assertNotIn(r"\end{document}", data["可编辑的原始LaTeX"])
                    self.assertTrue(images[0].startswith(b"\x89PNG"))
                    return {"before": label, "after": "替换标签"}
                with self.subTest(label=label), patch("services.pdf_selection_edit.run_local_codex_json", edit):
                    updated = edit_pdf_selection(source, result["pdf"], 1, rect, "替换标签", source_map=result["source_map"])
                    self.assertEqual(updated, source.replace(label, "替换标签"))


class PdfImageCropTests(unittest.TestCase):
    def test_rotated_scanned_page_preserves_source_density_and_display_orientation(self):
        # Four source pixels per PDF point. The top half of the rotated view
        # corresponds to the red left half of this scan.
        scan = Image.new("RGB", (400, 200), "red")
        scan.paste("blue", (200, 0, 400, 200))
        stream = BytesIO()
        scan.save(stream, format="PNG")
        with pymupdf.open() as doc:
            page = doc.new_page(width=100, height=50)
            page.insert_image(page.rect, stream=stream.getvalue())
            page.set_rotation(90)
            pdf = doc.tobytes()
        cropped = Image.open(BytesIO(crop_pdf_region(pdf, 1, [0, 0, 1, .5])))
        self.assertEqual(cropped.size, (200, 200))
        self.assertEqual(cropped.getpixel((50, 50)), (255, 0, 0))
        with self.assertRaises(ValueError):
            crop_pdf_region(pdf, 2, [0, 0, 1, 1])


if __name__ == "__main__":
    unittest.main()
