import unittest
import csv
import runpy
import tempfile
from pathlib import Path
from unittest.mock import patch
from services.question_type_service import infer_question_type, require_question_type
from utils.latex_ops import inject_meta_data
from utils.csv_ops import _parse_tex_content


class QuestionTypeTests(unittest.TestCase):
    def test_choice_answers_in_common_formats(self):
        for answer, expected in [(r'$A$', '单项选择题'), ('BD', '多项选择题'),
                                 (r'\boxed{\text{答案：ACD}}', '多项选择题'),
                                 (r'\textbf{A、C}', '多项选择题')]:
            with self.subTest(answer=answer):
                content = r'\begin{problem}实验情景\begin{choices}\choice{甲}\choice{乙}\end{choices}\end{problem}'
                content += r'\begin{answer}' + answer + r'\end{answer}'
                self.assertEqual(infer_question_type(content, '电学实验'), expected)

    def test_enumerated_options_and_embedded_answer(self):
        content = r'\begin{problem}波形图\begin{enumerate}[label=\Alph*.]\item 甲\item 乙\end{enumerate}\textbf{解析：}\boxed{\text{答案：BC}}\end{problem}'
        self.assertEqual(infer_question_type(content), '多项选择题')

    def test_non_choice_experiment_and_calculation(self):
        self.assertEqual(infer_question_type(r'\begin{problem}某同学利用装置测量电阻的实验，填写数据\end{problem}'), '实验题')
        self.assertEqual(infer_question_type(r'\begin{problem}求小球落地速度\underline{\quad}\end{problem}'), '计算题')

    def test_manual_type_survives_content_edit_and_index_parse(self):
        content = inject_meta_data(r'\begin{problem}新题干\end{problem}', {'题型': '实验题'})
        self.assertEqual(_parse_tex_content(content, '模拟卷')[1], '实验题')

    def test_missing_type_blocks_creation_before_any_write(self):
        from services.question_service import create_question_file
        with patch('services.question_service.os.path.exists', return_value=False), patch('services.question_service.atomic_write_text') as write:
            with self.assertRaisesRegex(ValueError, '选择题型'):
                create_question_file('new.tex', r'\begin{problem}题干\end{problem}', '2026', 'M', '卷', '1', '电场')
            write.assert_not_called()
        for value in (None, '', '选择题'):
            with self.assertRaises(ValueError):
                require_question_type(value)

    def test_rebuilding_index_keeps_manual_type_and_id(self):
        import utils.core_config as config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chapters = root / 'chapters'
            chapters.mkdir()
            # A manual experiment tag must survive even when the stem changes.
            filename = '2026-M-卷-1-电场.tex'
            content = inject_meta_data(r'\begin{problem}{2026}{M}{卷}{1}{电场}修改后题干\end{problem}', {'ID': '42', '题型': '实验题'})
            (chapters / filename).write_text(content, encoding='utf-8')
            index = root / 'index.csv'
            with patch.object(config, 'CHAPTERS_DIR', str(chapters)), patch.object(config, 'CSV_INDEX_PATH', str(index)):
                runpy.run_path('utils/init_sqlite_index.py', run_name='__main__')
                runpy.run_path('utils/init_sqlite_index.py', run_name='__main__')
            from utils.question_index import read_rows
            rows = read_rows(csv_path=str(index))
            self.assertFalse(index.exists(), 'SQLite rebuild must not recreate the old CSV')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['题目ID'], '42')
            self.assertEqual(rows[0]['题型'], '实验题')


if __name__ == '__main__':
    unittest.main()
