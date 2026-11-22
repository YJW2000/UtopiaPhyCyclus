"""The TeX workspace keeps its source, preview and saved picture references in sync."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


WORKSPACE = """
from unittest.mock import patch
import streamlit as st
import utopia_phycyclus_app as app
import services.pdf_edit_ui as editor

def compile_draft(source, *args, **kwargs):
    if source == 'fail':
        return dict(ok=False, error='test compile failure', log='')
    return dict(ok=True, pdf=source.encode(), engine='xelatex', source_map=None)

def select(pdf, *, document_id, disabled=False, **kwargs):
    if not disabled:
        return dict(page=1, rect=[0.1, 0.1, 0.3, 0.3], selection_id='region', document_id=document_id)

with patch.object(app, 'read_csv_index', return_value=[]), patch.object(app, 'compile_tex_preview', side_effect=compile_draft), patch.object(editor, 'st_pdf_selection', side_effect=select), patch.object(editor, 'edit_pdf_selection', return_value=st.session_state['candidate']):
    app.page_tex_entry()
"""


class PdfWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.at = AppTest.from_string(WORKSPACE, default_timeout=30)
        self.at.session_state['candidate'] = 'new mass'
        self.at.run()
        self.at.text_area(key='tex_workspace_source').set_value('old mass').run()
        self.at.button(key='tex_workspace_compile').click().run()
        self.assertFalse(self.at.exception)

    def submit(self):
        next(field for field in self.at.text_area if '希望怎样修改' in field.label).set_value('change the mass')
        next(b for b in self.at.button if b.label == '生成修改预览').click().run()
        self.assertFalse(self.at.exception)

    def test_accept_refreshes_both_source_and_pdf(self):
        self.submit()
        self.assertEqual(self.at.text_area(key='tex_workspace_source').value, 'old mass')
        self.assertTrue(self.at.button(key='tex_workspace_save').disabled)
        self.at.button(key='tex_pdf_edit_0_accept').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_area(key='tex_workspace_source').value, 'new mass')
        self.assertFalse(self.at.button(key='tex_workspace_save').disabled)
        self.assertEqual(self.at.session_state['tex_workspace_documents'][0]['preview']['pdf'], b'new mass')

    def test_failed_compile_and_stale_proposal_leave_source_intact(self):
        self.at.session_state['candidate'] = 'fail'
        self.submit()
        self.assertTrue(self.at.error)
        self.assertEqual(self.at.text_area(key='tex_workspace_source').value, 'old mass')
        self.at.session_state['candidate'] = 'new mass'
        self.submit()
        self.at.text_area(key='tex_workspace_source').set_value('manual correction').run()
        self.assertFalse(any(b.label == '采用修改' for b in self.at.button))
        self.assertFalse(any(b.label == '生成修改预览' for b in self.at.button))
        self.assertEqual(self.at.session_state['tex_workspace_documents'][0]['source'], 'manual correction')


class PdfSaveTests(unittest.TestCase):
    def test_save_packages_imported_figure_with_its_question(self):
        import utopia_phycyclus_app as app
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / 'crops'
            resources.mkdir()
            Image.new('RGB', (30, 40), 'white').save(resources / 'figure.png')
            source = (r'\begin{problem}{2026}{M}{测试}{1}{电场}如图，分析受力。' '\n'
                      r'\includegraphics{figure.png}\end{problem}' '\n'
                      r'\begin{problem}{2026}{M}{测试}{2}{电场}另一道题。\end{problem}')
            def write_file(path, content, *args):
                Path(path).write_text(content, encoding='utf-8')
            with patch.object(app, 'CHAPTERS_DIR', str(root / 'bank')), patch.object(app, 'create_question_file', side_effect=write_file), patch.object(app, 'clear_statistics_cache'), patch('utils.csv_ops.get_next_id', return_value='1'):
                logs = app._save_pdf_questions(source, {}, str(resources))
            self.assertEqual([item['status'] for item in logs], ['success', 'success'])
            question = Path(logs[0]['path'])
            body = question.read_text(encoding='utf-8')
            self.assertIn('计算题', body)
            from utils.image_assets import graphics_references
            path = graphics_references(body)[0]['path']
            self.assertIn('相关图', path)
            self.assertEqual((question.parent / path).read_bytes(), (resources / 'figure.png').read_bytes())
