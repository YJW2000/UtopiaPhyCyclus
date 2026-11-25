import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image

from services.pdf_import_service import import_pdf, inspect_pdf
from services.tex_import_service import parse_tex_questions
from utils.image_assets import copy_graphics, graphics_references


def fixture_pdf():
    with pymupdf.open() as doc:
        doc.new_page(width=200, height=150)
        scan = Image.new('RGB', (400, 300), 'white')
        scan.paste((0, 0, 0), (100, 75, 300, 225))
        png = io.BytesIO()
        scan.save(png, format='PNG')
        page = doc.new_page(width=200, height=150)
        page.insert_image(page.rect, stream=png.getvalue())
        for line in ('Continue question 10.', 'Answers: 9 A, 10 B.'):
            page = doc.new_page(width=200, height=150)
            page.insert_text((10, 30), line)
        return doc.tobytes()


def fragment(qid, kind, body, number=''):
    return dict(question_id=qid, number=number, kind=kind, latex=body)


def page(*fragments, figures=()):
    return dict(status='ok', fragments=list(fragments), figures=list(figures))


class PdfImportTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.assets = Path(directory.name)
        self.pdf = fixture_pdf()

    def test_scanned_and_digital_pages_join_questions_solutions_and_original_figures(self):
        self.assertEqual(inspect_pdf(self.pdf), 4)
        diagram = r'\begin{tikzpicture}\draw[->] (0,0)--(2,0);\end{tikzpicture}'
        pages = [
            page(fragment('q1', 'stem', r'Read the meter: [[FIG:meter]].', '9'),
                 fragment('q2', 'stem', 'Consider this motion.\n' + diagram, '10'),
                 figures=[dict(id='meter', rect=[.25, .25, .75, .75])]),
            page(fragment('q2', 'stem', 'Find its acceleration.')),
            page(fragment('q1', 'answer', 'A', '9'), fragment('q2', 'answer', 'B', '10'),
                 fragment('q2', 'solutions', r'$a=\frac{F}{m}$.', '10')),
        ]
        progress = []
        with patch('services.pdf_import_service.run_local_codex_json', side_effect=pages) as agent:
            source = import_pdf(self.pdf, resource_dir=self.assets,
                                defaults=dict(year='2026', paper_type='G', paper='试卷', subject='力学'),
                                first_page=2, last_page=4, on_progress=lambda p, n: progress.append((p, n)))
        self.assertEqual(progress, [(2, 3), (3, 3), (4, 3)])
        for call in agent.call_args_list:
            self.assertEqual(len(call.args[2]), 1)
            self.assertTrue(call.args[2][0].startswith(b'\x89PNG'))
            self.assertIn('【内置技能：physics-image-to-latex】', call.args[0])
            self.assertIn('【原有题库识别提示词】', call.args[0])
            self.assertIn('印写分离', call.args[0])
            self.assertIn('[[FIG:f1]]', call.args[0])
        self.assertIn('Continue question 10.', agent.call_args_list[1].args[0])
        self.assertIn('Consider this motion.', agent.call_args_list[1].args[0])
        questions = parse_tex_questions(source, 'paper.tex', {})
        self.assertEqual([(q.year, q.paper, q.number) for q in questions],
                         [('2026', '试卷', '9'), ('2026', '试卷', '10')])
        self.assertIn('\\begin{answer}\nA', questions[0].content)
        self.assertIn(diagram, questions[1].content)
        self.assertIn('Find its acceleration.', questions[1].content)
        self.assertIn(r'$a=\frac{F}{m}$.', questions[1].content)
        ref, = graphics_references(questions[0].content)
        with Image.open(self.assets / ref['path']) as cropped:
            self.assertEqual(cropped.size, (200, 150))
            self.assertEqual(cropped.convert('RGB').getpixel((100, 75)), (0, 0, 0))
        bank_dir = self.assets / 'question'
        copied = copy_graphics(questions[0].content, bank_dir, source_dir=self.assets)
        self.assertTrue((self.assets / graphics_references(copied)[0]['path']).is_file())
        self.assertNotIn('[[FIG:', source)

    def test_invalid_or_missing_page_or_password(self):
        for data in (b'', b'invalid'):
            with self.assertRaises(ValueError):
                inspect_pdf(data)
        with pymupdf.open(stream=self.pdf, filetype='pdf') as doc:
            locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='private', owner_pw='owner')
        with self.assertRaisesRegex(ValueError, '加密'):
            inspect_pdf(locked)
        for first, last in [(0, 1), (2, 1), (1, 5), (True, 1)]:
            with self.assertRaisesRegex(ValueError, '页码范围'):
                import_pdf(self.pdf, resource_dir=self.assets, first_page=first, last_page=last)

    def test_bad_second_page_does_not_publish_first_page_images(self):
        first = page(fragment('q1', 'stem', '[[FIG:f]]'), figures=[dict(id='f', rect=[.1, .1, .9, .9])])
        marker = self.assets / 'previous.tex'
        marker.write_text('previous work', encoding='utf-8')
        with patch('services.pdf_import_service.run_local_codex_json',
                   side_effect=[first, RuntimeError('登录已失效')]), self.assertRaisesRegex(RuntimeError, '第 3 页.*登录'):
            import_pdf(self.pdf, resource_dir=self.assets, first_page=2, last_page=3)
        self.assertEqual(list(self.assets.iterdir()), [marker])

    def test_reject_missing_illustrations_file_commands_and_malformed_output(self):
        for body in ('[[FIG:missing]]', r'\includegraphics{/foreign/figure.png}', r'\write18{command}',
                     r'\input{external}', r'\begin{problem}unsupported', r'\begin{tikzpicture}'):
            with self.subTest(body=body), patch('services.pdf_import_service.run_local_codex_json',
                    return_value=page(fragment('q1', 'stem', body))), self.assertRaisesRegex(RuntimeError, '第 2 页'):
                import_pdf(self.pdf, resource_dir=self.assets, first_page=2, last_page=2)
        with patch('services.pdf_import_service.run_local_codex_json',
                   return_value=dict(status='no_questions', fragments=[], figures=[])), self.assertRaises(ValueError):
            import_pdf(self.pdf, resource_dir=self.assets, first_page=1, last_page=1)

    def test_missing_printed_numbers_use_supplied_start(self):
        result = page(fragment('q1', 'stem', 'First question'), fragment('q2', 'stem', 'Next question'))
        with patch('services.pdf_import_service.run_local_codex_json', return_value=result):
            source = import_pdf(self.pdf, resource_dir=self.assets, first_page=2, last_page=2,
                                defaults={'number': '14', 'paper': '试卷_1&2'})
        parsed = parse_tex_questions(source, 'paper.tex', {})
        self.assertEqual([q.number for q in parsed], ['14', '15'])
        self.assertEqual(parsed[0].paper, '试卷＿1＆2')

    def test_bank_title_from_first_ocr_line_replaces_unknown_defaults_only(self):
        results = page(
            fragment('q1', 'stem', r'\textbf{【2026 力与运动】}' + '\n光滑水平面上有一辆车。', '1'),
            fragment('q2', 'stem', r'\textbf{【2025~~期中试卷，2】}', '2'),
            fragment('q2', 'stem', '求下一时刻的速度。'),
            fragment('q3', 'stem', r'\textbf{【单项选择题】}' + '\n请选择正确结论。', '3'),
            fragment('q4', 'stem', r'\textbf{【2026 力与运动，9】}' + '\n阅读给出的引文。', '4'),
        )
        with patch('services.pdf_import_service.run_local_codex_json', return_value=results):
            source = import_pdf(self.pdf, resource_dir=self.assets, first_page=2, last_page=2)
            explicit = import_pdf(self.pdf, resource_dir=self.assets, first_page=2, last_page=2,
                                  defaults={'year': '2024', 'paper': '手填卷名'})
        parsed = parse_tex_questions(source, 'paper.tex', {})
        self.assertEqual([(item.year, item.paper) for item in parsed],
                         [('2026', '力与运动'), ('2025', '期中试卷'), ('待填写', 'PDF导入'), ('待填写', 'PDF导入')])
        self.assertIn('光滑水平面上有一辆车。', parsed[0].content)
        self.assertIn('求下一时刻的速度。', parsed[1].content)
        self.assertNotIn('【2026 力与运动】', parsed[0].content)
        self.assertNotIn('【2025~~期中试卷，2】', parsed[1].content)
        self.assertIn('【单项选择题】', parsed[2].content)
        self.assertIn('【2026 力与运动，9】', parsed[3].content)
        self.assertIn(r'\begin{problem}{2024}{M}{手填卷名}{1}{未分类}', explicit)
        self.assertIn('【2026 力与运动】', explicit)

    def test_invalid_defaults_precede_any_agent_call_and_numbers_cannot_collide(self):
        with patch('services.pdf_import_service.run_local_codex_json') as agent, self.assertRaises(ValueError):
            import_pdf(self.pdf, resource_dir=self.assets, defaults={'paper': 'two\nlines'})
        agent.assert_not_called()
        result = page(fragment('q1', 'stem', 'First', '1'), fragment('q2', 'stem', 'Second', '1'))
        with patch('services.pdf_import_service.run_local_codex_json', return_value=result), self.assertRaisesRegex(ValueError, '第 1 题'):
            import_pdf(self.pdf, resource_dir=self.assets, first_page=2, last_page=2)
