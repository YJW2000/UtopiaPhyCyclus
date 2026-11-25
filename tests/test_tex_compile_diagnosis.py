# -*- coding: utf-8 -*-
"""编译失败诊断（TeX 录入预览）单元测试。

覆盖本轮修复的根因：AI/OCR 生成的 TikZ 使用模板未定义的颜色名
（如 meterblue）时，xelatex 日志只报 pgfkeys unknown key 且行号指向
``\\end{document}``（figure 浮动体延后排版）。``diagnose_tex_compile_failure``
必须把日志翻译成带「源码真实行号 + 修复方法」的中文诊断，并随编译结果返回。
"""
import shutil
import unittest
from unittest.mock import patch

from services.tex_compile_service import (
    build_preview_document,
    compile_tex_preview,
    diagnose_tex_compile_failure,
)

ROOT = str(__import__('pathlib').Path(__file__).resolve().parents[1])
DEFAULTS = dict(year='2026', paper_type='M', paper='测试卷', number='1', subject='电学实验')

PGFKEYS_LOG = (
    "This is XeTeX, Version 3.141592653-2.6-0.999998 (TeX Live 2026) (preloaded format=xelatex)\n"
    "(./preview.tex\n"
    "LaTeX2e <2025-11-01>\n"
    "Package pgfplots notification 'compat/show suggested version=true'\n"
    "\n"
    "./preview.tex:170: Package pgfkeys Error: I do not know the key '/tikz/meterblue' and I am going to ignore it. Perhaps you misspelled it.\n"
    "\n"
    "l.170 \\end{document}\n"
    "No pages of output.\n"
)

GENERIC_LOG = (
    "./preview.tex:3: Undefined control sequence.\n"
    "l.3 \\undefinedcommand\n"
    "No pages of output.\n"
)


class _FakeCompleted:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


class UnknownTikzKeyDiagnosisTests(unittest.TestCase):
    def test_color_error_reports_real_source_line_and_fixes(self):
        source = '\n'.join([
            r'\begin{problem}{2026}{M}{模拟卷}{1}{电学实验}',
            r'题干文字。',
            r'\begin{tikzpicture}',
            r'\draw[thick, meterblue, fill=white] (0,0) circle (0.3cm);',
            r'\node[meterblue] at (0,0) {V};',
            r'\end{tikzpicture}',
            r'\end{problem}',
        ])
        diagnosis = diagnose_tex_compile_failure(source, PGFKEYS_LOG)
        # 必须指出颜色名与源码中的真实行号（而不是日志里骗人的 l.170 / \end{document}）
        self.assertIn('meterblue', diagnosis)
        self.assertIn('源码第 4 行', diagnosis)
        self.assertIn('共 2 处', diagnosis)
        self.assertIn(r'\definecolor{meterblue}{RGB}', diagnosis)
        self.assertIn('tikzset', diagnosis)
        self.assertIn('pgfkeys', diagnosis)
        # 首行要足够短，能直接作为 st.error 的摘要
        headline = diagnosis.splitlines()[0]
        self.assertLessEqual(len(headline), 240)
        self.assertIn('meterblue', headline)

    def test_unknown_style_key_without_source_hit_still_actionable(self):
        log = ("preview.tex:170: Package pgfkeys Error: I do not know the key "
               "'/tikz/unknownStyle' and I am going to ignore it.\n")
        diagnosis = diagnose_tex_compile_failure('外部 \\input 引入的源码', log)
        self.assertIn('unknownStyle', diagnosis)
        self.assertIn('未直接定位', diagnosis)
        self.assertIn('tikzset', diagnosis)

    def test_undefined_environment_reports_source_line_and_fixes(self):
        # AI 生成的专题卷正文常把 methodbox/trapbox 等 tcolorbox 盒子当普通环境
        source = '\n'.join([
            r'\paragraph{题1（基础）}',
            r'\begin{methodbox}[题1·参考答案与详细解析]',
            r'\textbf{【参考答案】}略。',
            r'\end{methodbox}',
        ])
        log = "./preview.tex:624: LaTeX Error: Environment methodbox undefined.\n"
        diagnosis = diagnose_tex_compile_failure(source, log)
        self.assertIn('methodbox', diagnosis)
        self.assertIn('源码第 2 行', diagnosis)
        self.assertIn('answer', diagnosis)          # 建议改用模板支持的环境
        self.assertIn('完整 document', diagnosis)   # 自带导言区的完整文档应整体粘贴
        self.assertLessEqual(len(diagnosis.splitlines()[0]), 240)

    def test_generic_error_is_summarized_in_chinese(self):
        diagnosis = diagnose_tex_compile_failure(
            r'\begin{problem}{2026}{M}{模拟卷}{1}{电场}题\end{problem}', GENERIC_LOG)
        self.assertIn('编译失败', diagnosis)
        self.assertIn('preview.tex', diagnosis)
        self.assertIn('可能与源码行号不一致', diagnosis)

    def test_generic_error_of_full_document_keeps_line_number_note(self):
        source = r'\documentclass{article}\begin{document}\undefinedcommand\end{document}'
        diagnosis = diagnose_tex_compile_failure(source, GENERIC_LOG)
        self.assertIn('行号对应你的完整文档源码', diagnosis)

    def test_diagnosis_never_raises_on_garbage(self):
        for log in (None, '', b'\xff\xfe', '任意乱码日志'):
            diagnosis = diagnose_tex_compile_failure('', log)
            self.assertIsInstance(diagnosis, str)
            self.assertTrue(diagnosis.strip())


class CompileResultDiagnosisContractTests(unittest.TestCase):
    def test_compile_failure_returns_diagnosis_in_result(self):
        source = (r'\begin{problem}{2026}{M}{模拟卷}{1}{电学实验}'
                  '\n' + r'\draw[thick, meterblue] (0,0) -- (1,0);' + '\n'
                  r'\end{problem}')
        with patch('services.tex_compile_service.shutil.which', return_value='xelatex'), \
             patch('services.tex_compile_service.subprocess.run',
                   return_value=_FakeCompleted(1, PGFKEYS_LOG)):
            result = compile_tex_preview(source, ROOT, DEFAULTS)
        self.assertFalse(result['ok'])
        self.assertNotIn('pdf', result)
        self.assertIn('diagnosis', result)
        self.assertIn('meterblue', result['error'])
        self.assertIn('meterblue', result['diagnosis'])
        self.assertIn("I do not know the key", result['log'])
        # 原始日志仍然保留，便于高级用户查看全文
        self.assertIn('preview.tex:170', result['log'])

    def test_successful_compile_has_no_diagnosis(self):
        source = r'\begin{problem}{2026}{M}{模拟卷}{1}{电场}中文题\end{problem}'
        fake_ok = _FakeCompleted(0, 'Output written on preview.pdf')
        with patch('services.tex_compile_service.shutil.which', return_value='xelatex'), \
             patch('services.tex_compile_service.subprocess.run', return_value=fake_ok), \
             patch('services.tex_compile_service.Path.exists', return_value=True), \
             patch('pathlib.Path.read_bytes', return_value=b'%PDF-1.7'):
            result = compile_tex_preview(source, ROOT, DEFAULTS)
        self.assertTrue(result['ok'], result.get('error', ''))
        self.assertNotIn('diagnosis', result)


class PreviewDocumentUnchangedTests(unittest.TestCase):
    def test_fragment_still_uses_bank_template(self):
        document = build_preview_document('题面', ROOT, DEFAULTS)
        self.assertIn(r'\documentclass[12pt,UTF-8]{UtopiaPhyCyclus_book}', document)
        self.assertIn(r'\begin{problem}', document)


@unittest.skipUnless(shutil.which('xelatex'), 'No local XeLaTeX')
class UnknownColorEndToEndTests(unittest.TestCase):
    def test_unknown_color_still_fails_with_actionable_diagnosis(self):
        # 用一个刻意不存在的颜色名，确保诊断链路（而不是颜色别名）在真实编译中生效
        source = (r'\begin{problem}{2026}{M}{模拟卷}{1}{电场}题'
                  '\n' + r'\begin{tikzpicture}\draw[thick, definitelyunknowncolor] (0,0) -- (1,0);\end{tikzpicture}'
                  '\n' + r'\end{problem}')
        result = compile_tex_preview(source, ROOT, DEFAULTS)
        self.assertFalse(result['ok'])
        self.assertIn('definitelyunknowncolor', result['diagnosis'])
        self.assertIn('源码第', result['diagnosis'])


if __name__ == '__main__':
    unittest.main()
