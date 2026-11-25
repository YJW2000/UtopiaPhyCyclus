import unittest

from services.tex_import_service import decode_tex, parse_tex_questions, join_tex_questions


DEFAULTS = dict(year='2026', paper_type='G', paper='测试卷', number='3', subject='力学')


class TexImportTests(unittest.TestCase):
    def parse(self, text, filename='upload.tex'):
        return parse_tex_questions(text, filename, DEFAULTS)

    def test_document_keeps_answers_and_does_not_split_choices(self):
        source = r'''\documentclass{article}
\begin{document}
% \begin{problem} ignored comment
\begin{problem}{2025}{M}{A-B}{7}{电磁学}
$F=ma$\begin{choices}\choice A\choice B\end{choices}
\end{problem}
\begin{answer}A\end{answer}
\begin{solutions}[法一]第一种解法\end{solutions}
\begin{solutions}[法二]第二种解法\end{solutions}
\begin{problem}{2025}{M}{A-B}{8}{力学}第二题\end{problem}
\end{document}'''
        questions = self.parse(source)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].number, '7')
        self.assertEqual(questions[0].paper, 'A-B')
        self.assertIn('[法二]', questions[0].content)
        self.assertNotIn('第二题', questions[0].content)
        self.assertNotIn('document', questions[1].content)
        self.assertEqual(questions[0].filename, '2025-M-A－B-7-电磁学.tex')

    def test_labels_belong_to_correct_question_and_ids_reset(self):
        labels = '% === Begin Label Data ===\n% ID: 99\n% 标签: %s\n% === End  Label Data ===\n'
        source = labels.replace('%s', '第一题') + r'\begin{problem}题一\end{problem}' + '\n'
        source += labels.replace('%s', '第二题') + r'\begin{problem}题二\end{problem}'
        questions = self.parse(source)
        self.assertIn('% 标签: 第一题', questions[0].content)
        self.assertNotIn('第二题', questions[0].content)
        self.assertIn('% 标签: 第二题', questions[1].content)
        self.assertNotIn('% ID:', join_tex_questions(questions))
        self.assertEqual(questions[1].number, '4')

    def test_export_separator_and_filename_fallback(self):
        questions = self.parse('---2024-M-学校-模拟卷-12-力学.tex---\n题干\n---2024-G-试卷-13-电磁学.tex---\n第二题')
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].paper, '学校-模拟卷')
        self.assertEqual(questions[0].number, '12')
        self.assertEqual(questions[1].subject, '电磁学')

    def test_raw_fragment_is_one_question(self):
        questions = self.parse(r'\begin{enumerate}\item 子问一\item 子问二\end{enumerate}')
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].number, '3')
        self.assertIn(r'\item 子问二', questions[0].content)

    def test_bad_environment_rejected(self):
        for source in (r'\begin{problem}缺结束', r'\end{problem}', r'\begin{problem}\begin{problem}x\end{problem}', r'\documentclass{article}', '% comment only'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.parse(source)

    def test_encodings(self):
        for encoding in ('utf-8-sig', 'gb18030', 'utf-16'):
            self.assertEqual(decode_tex('物理题\r\n内容'.encode(encoding)), '物理题\n内容')
        with self.assertRaises(ValueError):
            decode_tex(b'abc\x00def')

    def test_filename_cannot_escape_bank(self):
        with self.assertRaises(ValueError):
            self.parse(r'\begin{problem}{../../2026}{G}{../卷}{1}{../../力学}正文\end{problem}')


if __name__ == '__main__':
    unittest.main()
