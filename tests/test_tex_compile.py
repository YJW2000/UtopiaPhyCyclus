from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from services.tex_compile_service import build_preview_document, compile_tex_preview, render_pdf_page

ROOT = str(Path(__file__).resolve().parents[1])
DEFAULTS = dict(year='2026', paper_type='M', paper='测试卷', number='1', subject='电场')


class CompileTests(unittest.TestCase):
    def test_full_document_keeps_custom_preamble(self):
        source = r'\documentclass{article}\newcommand{\custom}{hello}\begin{document}\custom\end{document}'
        self.assertEqual(build_preview_document(source, ROOT, DEFAULTS), source)

    def test_fragments_use_bank_template_and_remove_file_separators(self):
        source = '---2026-M-测试卷-1-电场.tex---\n正文'
        document = build_preview_document(source, ROOT, DEFAULTS)
        self.assertIn(r'\documentclass[12pt,UTF-8]{UtopiaPhyCyclus_book}', document)
        self.assertIn(r'\begin{problem}{2026}{M}{测试卷}{1}{电场}', document)
        self.assertNotIn('---2026', document)
        self.assertNotIn(r'\includepdf', document)

    def test_missing_compiler_is_actionable(self):
        with patch('services.tex_compile_service.shutil.which', return_value=None):
            result = compile_tex_preview('题干', ROOT, DEFAULTS)
        self.assertFalse(result['ok'])
        self.assertIn('PATH', result['error'])

    @unittest.skipUnless(shutil.which('xelatex'), 'No local XeLaTeX')
    def test_real_bank_template_with_chinese_choices_and_tikz(self):
        source = r'''\begin{problem}{2026}{M}{模拟卷}{1}{电场}
测试中文、公式 $F=ma$ 和选项。
\begin{choices}\choice{$a=F/m$}\choice{$a=m/F$}\end{choices}
\begin{tikzpicture}\draw[->] (0,0)--(1,0);\end{tikzpicture}
\end{problem}
\begin{answer}A\end{answer}
\begin{solutions}[法一]由牛顿第二定律可得。\end{solutions}
\begin{solutions}[法二]另解。\end{solutions}'''
        # Resource directory differs from the project, exercising template lookup.
        result = compile_tex_preview(source, ROOT, DEFAULTS, resource_dir=str(Path(ROOT) / 'utils'))
        self.assertTrue(result['ok'], result.get('error', '') + result.get('log', '')[-2000:])
        png, pages = render_pdf_page(result['pdf'])
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertGreaterEqual(pages, 1)

    @unittest.skipUnless(shutil.which('xelatex'), 'No local XeLaTeX')
    def test_real_bank_template_supports_ai_semantic_colors(self):
        """AI/OCR 生成的语义色名（primaryblue/meterblue/accentorange）应能直接编译。

        回归用户报错：`I do not know the key '/tikz/meterblue'`。这些颜色名由
        utils/diagram_palette.py 统一维护，并同步在题库文档类中定义。
        """
        source = r'''\begin{problem}{2026}{M}{模拟卷}{1}{电学实验}
        某实验小组测量旧干电池的电动势和内阻，电路如图。
        \begin{tikzpicture}[scale=0.8, >=Stealth]
        \draw[thick, meterblue, fill=white] (0,0) circle (0.3cm);
        \node[meterblue] at (0,0) {\textbf{V}};
        \draw[thick, primaryblue] (0,1) -- (2,1);
        \filldraw[accentorange] (1,1) circle (2pt);
        \draw[thick, dashdotted] (0,-1) -- (2,-1);
        \end{tikzpicture}
        \end{problem}
        \begin{answer}略\end{answer}
        \begin{solutions}由闭合电路欧姆定律可得。\end{solutions}'''
        result = compile_tex_preview(source, ROOT, DEFAULTS)
        self.assertTrue(result['ok'], result.get('error', '') + result.get('log', '')[-2000:])
        png, pages = render_pdf_page(result['pdf'])
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertGreaterEqual(pages, 1)

    @unittest.skipUnless(shutil.which('xelatex'), 'No local XeLaTeX')
    def test_real_full_document_and_error_diagnostics(self):
        source = r'\documentclass{article}\begin{document}First\newpage Second\end{document}'
        result = compile_tex_preview(source, ROOT, DEFAULTS)
        self.assertTrue(result['ok'], result.get('log', '')[-2000:])
        _, pages = render_pdf_page(result['pdf'], 2)
        self.assertEqual(pages, 2)
        failed = compile_tex_preview(source.replace('First', r'\undefinedcommand'), ROOT, DEFAULTS)
        self.assertFalse(failed['ok'])
        self.assertNotIn('pdf', failed)
        self.assertIn('Undefined control', failed['log'])
        self.assertIn('preview.tex:1:', failed['log'])
        self.assertIn(r'\undefinedcommand', failed['log'])


if __name__ == '__main__':
    unittest.main()
