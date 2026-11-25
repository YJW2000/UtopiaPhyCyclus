# -*- coding: utf-8 -*-
"""插图语义色别名一致性测试。

AI/OCR 生成的 TikZ 常用 primaryblue / meterblue / accentorange 等语义色名。
这些名字必须同时存在于三处，否则编译仍会报 pgfkeys unknown key：
  1. ``utils/diagram_palette.py``（唯一数据源）
  2. ``UtopiaPhyCyclus_book.cls``（TeX 录入预览 / 题库组卷）
  3. ``utils/tikz_ops._build_standalone_document``（插图 Agent）
  4. ``utopia_phycyclus_app._build_question_image_tex``（题目图片导出）
"""
import re
import unittest
from pathlib import Path

from utils.diagram_palette import DIAGRAM_COLOR_ALIASES, as_tex_definitions
from utils.tikz_ops import _build_standalone_document

ROOT = Path(__file__).resolve().parents[1]

#: 用户实际报错时使用的三个颜色名（回归锚点，任何一个缺失都会让原始题目无法编译）
REQUIRED_ALIASES = ('primaryblue', 'meterblue', 'accentorange')


class DiagramPaletteSourceTests(unittest.TestCase):
    def test_aliases_are_well_formed(self):
        self.assertGreaterEqual(len(DIAGRAM_COLOR_ALIASES), len(REQUIRED_ALIASES))
        for name, value in DIAGRAM_COLOR_ALIASES.items():
            self.assertRegex(name, r'^[A-Za-z][A-Za-z0-9]*$', '色名必须是合法 xcolor 名称')
            rgb, meaning = value
            self.assertEqual(3, len(rgb), name)
            for channel in rgb:
                self.assertIsInstance(channel, int, name)
                self.assertGreaterEqual(channel, 0, name)
                self.assertLessEqual(channel, 255, name)
            self.assertTrue(str(meaning).strip(), name)
        for name in REQUIRED_ALIASES:
            self.assertIn(name, DIAGRAM_COLOR_ALIASES)

    def test_as_tex_definitions_are_pure_definecolor_lines(self):
        text = as_tex_definitions()
        self.assertTrue(text.endswith('\n'))
        lines = text.splitlines()
        self.assertEqual(len(DIAGRAM_COLOR_ALIASES), len(lines))
        for line in lines:
            self.assertRegex(line, r'^\\definecolor\{[A-Za-z][A-Za-z0-9]*\}\{RGB\}\{\d{1,3},\d{1,3},\d{1,3}\}$')
        for name, ((red, green, blue), _meaning) in DIAGRAM_COLOR_ALIASES.items():
            self.assertIn(
                r'\definecolor{%s}{RGB}{%d,%d,%d}' % (name, red, green, blue), lines, name)

    def test_cls_contains_identical_definitions(self):
        cls = (ROOT / 'UtopiaPhyCyclus_book.cls').read_text(encoding='utf-8')
        for line in as_tex_definitions().splitlines():
            self.assertIn(line, cls)
        # 同名颜色只定义一次，避免 cls 内出现互相覆盖
        for name in DIAGRAM_COLOR_ALIASES:
            self.assertEqual(1, cls.count(r'\definecolor{%s}' % name), name)

    def test_standalone_template_injects_palette_before_tikzset(self):
        document = _build_standalone_document(
            r'\begin{tikzpicture}\draw[meterblue] (0,0)--(1,0);\end{tikzpicture}')
        for line in as_tex_definitions().splitlines():
            self.assertIn(line, document)
        self.assertLess(document.index(r'\definecolor{meterblue}'),
                        document.index(r'\tikzset{'))

    def test_question_image_export_preamble_uses_palette(self):
        from utopia_phycyclus_app import _build_question_image_tex

        content = (r'\begin{problem}{2026}{M}{模拟卷}{1}{电学实验}'
                   r'\begin{tikzpicture}\draw[meterblue] (0,0)--(1,0);\end{tikzpicture}'
                   r'\end{problem}')
        document = _build_question_image_tex(content)
        for line in as_tex_definitions().splitlines():
            self.assertIn(line, document)
        # 颜色定义必须出现在正文与 \begin{document} 之前
        self.assertLess(document.index(r'\definecolor{meterblue}'),
                        document.index(r'\begin{document}'))


if __name__ == '__main__':
    unittest.main()
