# -*- coding: utf-8 -*-
"""Markdown 预览渲染回归测试：clean 掉题目里残留的 LaTeX 版式代码。

用户反馈：题目预览中仍能看到 ``\\begin{figure}[htbp]``、``\\begin{minipage}``、
``\\centerline{\\small\\textbf{...}}`` 等源码。根因是 ``latex_to_markdown`` 只把
``tikzpicture`` 转成图片，没有剥离 AI/OCR 生成题目常用的 figure/minipage 外壳；
另外嵌套 enumerate/itemize 与嵌套花括号的 ``\\textbf`` 也会残留 ``\\item``、``{\\Delta I}``。
"""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.latex_ops import latex_to_markdown

ROOT = Path(__file__).resolve().parents[1]
BANK_QUESTION = (ROOT / 'chapters' / '电学实验' / '2026'
                 / '2026-M-测定电源电动势和内阻专项练习-1-电学实验.tex')

FIGURE_SNIPPET = r'''
\begin{figure}[htbp]
\vspace{-4pt}
\centering
\begin{minipage}[c]{0.46\textwidth}
\centering
\begin{tikzpicture}[scale=0.92]
\draw[thick] (0,0) -- (1,0);
\end{tikzpicture}
\par\vspace{2pt}
\centerline{\small\textbf{(a) 实验原理电路图}}
\end{minipage}
\hfill
\begin{minipage}[c]{0.50\textwidth}
\centering
\begin{tikzpicture}
\draw[thick] (0,0) -- (1,1);
\end{tikzpicture}
\centerline{\small\textbf{(b) 采集数据绘制的 $U-I$ 图像}}
\end{minipage}
\vspace{-4pt}
\end{figure}
'''


def _residual_commands_outside_math(markdown: str) -> list[str]:
    """屏蔽公式段与 base64 图片后，收集残留的 LaTeX 命令。"""
    masked = re.sub(r'\$\$[\s\S]*?\$\$', ' ', markdown)
    masked = re.sub(r'\$[^$\n]*\$', ' ', masked)
    masked = re.sub(r"src='data:image/png;base64,[^']+'", "src='[IMG]'", masked)
    return sorted(set(re.findall(r'\\[A-Za-z]+', masked)))


class PreviewLayoutCleanupTests(unittest.TestCase):
    def render(self, text):
        # 预览测试不依赖本地 TeX：图片渲染替换为占位符
        with patch('utils.latex_ops._tikz_image_markdown', return_value='[IMG]'):
            return latex_to_markdown(text, show_title=False)

    def test_figure_minipage_and_centerline_are_not_rendered_as_text(self):
        markdown = self.render(FIGURE_SNIPPET)
        for token in (
            '\\begin{figure}', '\\end{figure}', 'minipage', '\\centerline', '\\centering',
            '\\small', '\\textwidth', '\\vspace', '\\hfill', '\\caption', 'figure',
        ):
            self.assertNotIn(token, markdown, token)
        self.assertIn('**(a) 实验原理电路图**', markdown)
        self.assertIn('**(b) 采集数据绘制的 $U-I$ 图像**', markdown)
        self.assertEqual(2, markdown.count('[IMG]'))

    def test_nested_enumerate_and_itemize_have_no_residual_item(self):
        source = r'''
\begin{enumerate}
\item 第一问
\begin{enumerate}
\item 子问一
\item 子问二
\end{enumerate}
\item 第二问
\end{enumerate}
\begin{itemize}
\item 要点
\begin{itemize}
\item 子要点
\end{itemize}
\end{itemize}
'''
        markdown = self.render(source)
        for token in ('\\item', '\\begin{enumerate}', '\\end{enumerate}',
                      '\\begin{itemize}', '\\end{itemize}'):
            self.assertNotIn(token, markdown, token)
        for text in ('第一问', '子问一', '子问二', '第二问', '子要点'):
            self.assertIn(text, markdown)

    def test_textbf_with_nested_math_braces_is_balanced(self):
        source = r'\textbf{计算内阻永远首选斜率 $\frac{\Delta U}{\Delta I}$！}'
        markdown = self.render(source)
        self.assertEqual(r'**计算内阻永远首选斜率 $\frac{\Delta U}{\Delta I}$！**', markdown.strip())
        self.assertEqual([], _residual_commands_outside_math(markdown))

    def test_tikz_source_is_unchanged_by_prose_conversions(self):
        diagram = r'''\begin{tikzpicture}
% Keep TikZ comments and command arguments intact.
\node at (0,0) {\textbf{G}\hspace{2pt}$U$};
\node at (1,0) {\underline{\hspace{1cm}}};
\end{tikzpicture}'''
        with patch('utils.latex_ops._tikz_image_markdown', return_value='[IMG]') as renderer:
            markdown = latex_to_markdown(r'\textbf{正文}' + diagram, show_title=False)
        renderer.assert_called_once_with(diagram)
        self.assertIn('**正文**', markdown)
        self.assertIn('[IMG]', markdown)

    def test_failed_grouped_diagram_is_rendered_once_and_source_preserved(self):
        diagram = r'''\begingroup
\begin{tikzpicture}\draw[badstyle] (0,0)--(1,1);\end{tikzpicture}
\endgroup'''
        with patch('utils.latex_ops.get_tikz_image_b64', return_value=(None, 'COMPILE_ERROR')) as compiler:
            markdown = latex_to_markdown(diagram, show_title=False)
        compiler.assert_called_once()
        self.assertEqual(1, markdown.count('TikZ 编译失败'))
        self.assertEqual(1, markdown.count('<details>'))
        self.assertIn(diagram, markdown)

    def test_diagram_tables_consume_nested_column_spec_and_row_spacing(self):
        source = r'''\begin{tabular}{@{}c@{\hspace{.04\textwidth}}c@{}}
\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture} &
\begin{tikzpicture}\draw (0,0)--(2,1);\end{tikzpicture}\\[3pt]
{（甲）} & {（乙）}
\end{tabular}
\medskip
解析：\[U=IR\]'''
        markdown = self.render(source)
        self.assertEqual(2, markdown.count('[IMG]'))
        self.assertEqual(2, markdown.count('<tr>'))
        self.assertIn('（甲）', markdown)
        self.assertIn('（乙）', markdown)
        for token in ('@{', 'textwidth', '[3pt]', 'tabular', 'MCTIKZPREVIEWTOKEN', r'\medskip'):
            self.assertNotIn(token, markdown)

    def test_plain_data_table_with_braced_column_spec_keeps_cells(self):
        markdown = self.render(r'\begin{tabular}{@{}p{2cm}c@{}}物理量 & 数值 \\ 电阻 & $10\,\Omega$\end{tabular}')
        self.assertIn('| 物理量 | 数值 |', markdown)
        self.assertIn(r'| 电阻 | $10\,\Omega$ |', markdown)
        self.assertNotIn('p{2cm}', markdown)

    def test_math_content_is_preserved(self):
        source = r'电压表 $\text{V}$，$r = \frac{1.48}{0.60} \approx 2.47\,\Omega$。'
        markdown = self.render(source)
        self.assertIn(r'$\text{V}$', markdown)
        self.assertIn(r'\approx 2.47\,\Omega', markdown)

    def test_scored_equations_keep_circled_numbers_and_marks(self):
        source = r'''
\begin{equation*}
t_2=65-55=10\,\mathrm s.
\tag*{\text{\textcircled{\scriptsize 1}\enspace（1分）}}
\end{equation*}
\begin{align*}
0&=v_B-at_2
\tag*{\text{\textcircled{\scriptsize 2}\enspace（1分）}}\\[3pt]
a&=\frac{v_B}{t_2}=\boxed{1.4\,\mathrm{m/s^2}}.
\tag*{\text{\textcircled{\scriptsize 3}\enspace（2分）}}
\end{align*}
评分：\textcircled{\scriptsize 1}至\textcircled{\scriptsize 3}。
'''
        markdown = self.render(source)
        displays = re.findall(r'\$\$([\s\S]*?)\$\$', markdown)
        self.assertEqual(3, len(displays))
        for formula, number, marks in zip(displays, '①②③', ('1', '1', '2')):
            self.assertEqual(1, formula.count(r'\tag*{'))
            self.assertIn(number, formula)
            self.assertIn('（' + marks + '分）', formula)
            self.assertNotIn('$', formula)
        self.assertIn(r'\boxed{1.4\,\mathrm{m/s^2}}', displays[2])
        self.assertIn('评分：①至③。', markdown)
        self.assertNotIn(r'\textcircled', markdown)
        self.assertNotIn('[3pt]', markdown)
        self.assertEqual([], _residual_commands_outside_math(markdown))

    def test_nested_aligned_equation_survives_prose_cleanup(self):
        source = r'''
\begingroup
\setlength{\abovedisplayskip}{6pt}
\setlength{\belowdisplayskip}{6pt}
\begin{equation*}
\begin{aligned}
Q_2&=\frac12CE^2-\frac12(m+CB^2l^2)v_2^2\\
&=\frac{mCE^2}{2(m+CB^2l^2)}.
\end{aligned}
\tag*{\text{\textcircled{\scriptsize 12}\enspace（2分）}}
\end{equation*}
\begin{equation*}
\frac{Q_2}{Q_1}<1,\qquad\boxed{Q_1>Q_2}.
\tag*{\text{\textcircled{\scriptsize 13}\enspace（1分）}}
\end{equation*}
\endgroup
'''
        markdown = self.render(source)
        displays = re.findall(r'\$\$([\s\S]*?)\$\$', markdown)
        self.assertEqual(2, len(displays))
        self.assertIn(r'\begin{aligned}', displays[0])
        self.assertIn(r'\end{aligned}', displays[0])
        self.assertIn('\\\\\n&=', displays[0])
        self.assertIn(r'\qquad\boxed{Q_1>Q_2}', displays[1])
        for formula in displays:
            self.assertNotIn('$', formula)
            self.assertNotIn('<br>', formula)
        self.assertEqual([], _residual_commands_outside_math(markdown))

    def test_tagged_alignment_keeps_nested_matrix_rows(self):
        source = r'''\begin{align*}
A&=\begin{pmatrix}1&2\\3&4\end{pmatrix}\tag{1}\\
x&=2\tag{2}
\end{align*}'''
        markdown = self.render(source)
        displays = re.findall(r'\$\$([\s\S]*?)\$\$', markdown)
        self.assertEqual(2, len(displays))
        self.assertIn(r'\begin{pmatrix}1&2\\3&4\end{pmatrix}', displays[0])
        self.assertIn('\\end{aligned}\n\\tag{1}', displays[0])
        self.assertIn('\\end{aligned}\n\\tag{2}', displays[1])

    def test_math_emphasis_and_cases_are_not_rewritten_as_markdown(self):
        source = (r'\textbf{正文} $\textbf{单位}\quad\underline{x}$'
                  r'\[f(x)=\begin{cases}x&x>0\\0&x\le0\end{cases}\]')
        markdown = self.render(source)
        self.assertIn('**正文**', markdown)
        self.assertIn(r'$\textbf{单位}\quad\underline{x}$', markdown)
        self.assertIn(r'\begin{cases}x&x>0\\0&x\le0\end{cases}', markdown)

    def test_table_row_spacing_does_not_swallow_following_math(self):
        source = r'\begin{tabular}{cc}甲 & $1$\\[3pt]乙 & $2$\end{tabular} 后文 $x$。'
        markdown = self.render(source)
        self.assertIn('$1$', markdown)
        self.assertIn('$2$', markdown)
        self.assertIn('后文 $x$。', markdown)
        self.assertNotIn('MCMATHPREVIEWTOKEN', markdown)

    def test_unclosed_formula_is_not_truncated_while_editing(self):
        self.assertEqual('待补公式 $x+1', self.render('待补公式 $x+1'))

    def test_underline_blank_inside_math_stays_katex_safe(self):
        """公式里的填空线不能变成 <u>/&nbsp;，否则 KaTeX 解析失败显示公式源码。"""
        source = (r'整理出以 $\frac{1}{U}$ 为纵坐标、$\frac{1}{R}$ 为横坐标的一次函数表达式：'
                  r'$\frac{1}{U} = \underline{\hspace{1.5cm}} + \underline{\hspace{1.5cm}}'
                  r' \cdot \frac{1}{R}$。'
                  r'并读出 $E = $\underline{\hspace{2cm}}$\text{V}$。')
        markdown = self.render(source)
        for segment in re.findall(r'\$[^$\n]*\$', markdown):
            self.assertNotIn('<u>', segment, segment)
            self.assertNotIn('&nbsp;', segment, segment)
        # 公式段保留原始 LaTeX 空白命令（KaTeX 支持 \underline 与 \hspace）
        self.assertIn(r'\underline{\hspace{1.5cm}}', markdown)
        # 文本段仍渲染为可点击阅读的填空线
        self.assertIn('<u>&nbsp;', markdown)

    def test_qquad_blank_outside_math_does_not_leak_latex_source(self):
        """选择题括号中的 ``\\qquad`` 应显示为空白，公式内则保持原命令。"""
        source = (r'\textbf{磁场与磁感线}\par'
                  r'（1）下列说法中，正确的是（\qquad）。（6 分）'
                  r'公式 $a\qquad b$。')
        markdown = self.render(source)
        prose = re.sub(r'\$[^$\n]*\$', ' ', markdown)
        self.assertNotIn(r'\qquad', prose)
        self.assertNotIn(r'\quad', prose)
        self.assertIn('（\u3000\u3000）', markdown)
        self.assertIn(r'$a\qquad b$', markdown)

    def test_starred_hspace_and_enspace_are_previewed_as_spacing(self):
        markdown = self.render(r'甲\hspace*{1cm}乙\enspace 丙')
        self.assertNotIn(r'\hspace', markdown)
        self.assertNotIn(r'\enspace', markdown)
        self.assertIn('甲\u3000乙\u2002 丙', markdown)

    def test_ai_paper_wrappers_paragraph_zihao_and_unknown_envs_are_cleaned(self):
        """AI 专题卷的 paragraph / zihao / methodbox / trapbox / 自定义盒子不能漏成源码。"""
        source = r'''
\paragraph{题1（基础·伏安法）}
\begin{methodbox}[题1·参考答案与详细解析]
\textbf{【参考答案】}最大端。
\begin{trapbox}[复习锦囊]
口诀内容。
\end{trapbox}
\end{methodbox}
\zihao{4}第二部分：参考答案
\begin{unknownbox}[自定义盒子]
题目内容。
\end{unknownbox}
\begin{equation}
E = U + Ir
\end{equation}
'''
        markdown = self.render(source)
        self.assertEqual([], _residual_commands_outside_math(markdown))
        for text in ('题1（基础·伏安法）', '【参考答案】', '口诀内容', '题目内容', 'E = U + Ir'):
            self.assertIn(text, markdown)
        for token in ('methodbox', 'trapbox', 'unknownbox', 'zihao', '\\paragraph'):
            self.assertNotIn(token, markdown, token)
        self.assertIn('$$', markdown)  # equation 环境转成展示公式


class ImportedQuestionPreviewTests(unittest.TestCase):
    def test_hasanzhong_scoring_equations_preserve_all_marks(self):
        for number, tag_count in ((13, 7), (14, 10), (15, 13)):
            path = ROOT / 'output' / 'hasanzhong_2026_remaining_v2' / f'q{number}.tex'
            if not path.exists():
                self.skipTest('哈三中原题文件不存在')
            with self.subTest(question=number):
                source = path.read_text(encoding='utf-8')
                with patch('utils.latex_ops._tikz_image_markdown', return_value='[IMG]'):
                    markdown = latex_to_markdown(source, show_title=False)
                displays = re.findall(r'\$\$([\s\S]*?)\$\$', markdown)
                tagged = [formula for formula in displays if r'\tag*{' in formula]
                self.assertEqual(tag_count, len(tagged))
                for index, formula in enumerate(tagged, 1):
                    self.assertIn(chr(0x2460 + index - 1), formula)
                    self.assertEqual(1, formula.count(r'\tag*{'))
                    self.assertNotIn('$', formula)
                    self.assertNotIn('<br>', formula)
                original_scores = re.findall(r'\\tag\*\{[^\n]*?（(\d+)分）', source)
                rendered_scores = [re.search(r'（(\d+)分）', formula).group(1) for formula in tagged]
                self.assertEqual(original_scores, rendered_scores)
                self.assertEqual([], _residual_commands_outside_math(markdown))
                self.assertNotIn(r'\textcircled', markdown)

    def test_imported_bank_question_preview_has_no_layout_code(self):
        if not BANK_QUESTION.exists():
            self.skipTest('题库题目不存在')
        source = BANK_QUESTION.read_text(encoding='utf-8')
        with patch('utils.latex_ops._tikz_image_markdown', return_value='[IMG]'):
            markdown = latex_to_markdown(source, show_title=False)
        self.assertEqual([], _residual_commands_outside_math(markdown))
        self.assertEqual(2, markdown.count('[IMG]'))
        self.assertIn('**(a) 实验原理电路图**', markdown)
        self.assertIn('**(b) 采集数据绘制的 $U-I$ 图像**', markdown)
        self.assertNotIn('minipage', markdown)
        self.assertNotIn('\\centerline', markdown)


if __name__ == '__main__':
    unittest.main()
