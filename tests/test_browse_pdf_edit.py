"""Card-level draft, view switching, and conflict handling for PDF edits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


CARD = '''
from pathlib import Path
from unittest.mock import patch
import streamlit as st
import utopia_phycyclus_app as app
import services.pdf_edit_ui as pdf_ui

path = st.session_state['question_path']
def compile_preview(source, project, defaults, engine, folder):
    assert folder == str(Path(path).parent)
    st.session_state['compiles'] = st.session_state.get('compiles', []) + [source]
    return {'ok': True, 'pdf': source.encode(), 'source_map': {}, 'engine': engine}

def mark(pdf, *, document_id, **kwargs):
    return {'page': 1, 'rect': [0.1, 0.2, 0.5, 0.35], 'document_id': document_id, 'selection_id': 'region'}

with patch.object(app, 'compile_tex_preview', side_effect=compile_preview), patch.object(pdf_ui, 'st_pdf_selection', side_effect=mark), patch.object(pdf_ui, 'edit_pdf_selection', side_effect=lambda source, *args, **kwargs: source.replace('2 kg', '4 kg')), patch.object(app, 'render_ai_solution_panel'), patch.object(app, 'render_question_header'):
    app.render_browse_question_editor_card('题目', '', path, 'bank')
'''


class BrowsePdfEditTests(unittest.TestCase):
    def setUp(self):
        import utopia_phycyclus_app as app
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'question.tex'
        self.original = r'\begin{problem}{2026}{M}{试卷}{1}{力学}小球质量为 2 kg。\end{problem}'
        self.path.write_text(self.original, encoding='utf-8')
        self.at = AppTest.from_string(CARD, default_timeout=30)
        self.at.session_state['question_path'] = str(self.path)
        self.text_key = 'bank_edit_' + app._question_key('text', str(self.path))
        self.save_key = 'bank_tex_edit_' + app._question_key('meta', str(self.path))
        self.at.run()
        self.assertFalse(self.at.exception)

    def set_view(self, view):
        self.at.radio(key=self.text_key + '_view').set_value(view).run()
        self.assertFalse(self.at.exception)

    def apply_pdf_edit(self):
        self.set_view('PDF 框选编辑')
        self.at.text_area[0].set_value('把小球质量改为 4 kg。')
        next(button for button in self.at.button if button.label == '生成修改预览').click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.original)
        self.set_view('LaTeX 源码')
        self.set_view('PDF 框选编辑')
        self.assertEqual(len(self.at.session_state['compiles']), 2)
        self.at.button(key=self.text_key + '_pdf_edit_accept').click().run()
        self.assertFalse(self.at.exception)

    def test_review_then_save_from_pdf_after_editing_source(self):
        import utopia_phycyclus_app as app
        self.assertFalse(self.at.text_area)
        self.assertNotIn('compiles', self.at.session_state.filtered_state)
        self.apply_pdf_edit()
        self.assertEqual(len(self.at.session_state['compiles']), 2)
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.original)
        self.set_view('LaTeX 源码')
        source = self.at.text_area(key=self.text_key)
        self.assertIn('4 kg', source.value)
        source.set_value(source.value.replace('4 kg', '5 kg')).run()
        self.set_view('PDF 框选编辑')
        self.assertIn('5 kg', self.at.session_state['compiles'][-1])
        with patch.object(app, '_duplicate_save_confirmation', return_value=True), patch.object(app, 'record_operation'), patch.object(app, '_invalidate_semantic_for_file'), patch.object(app, '_update_csv_index_for_content_change') as index:
            self.at.button(key=self.save_key).click().run()
        self.assertFalse(self.at.exception)
        index.assert_called_once()
        self.assertIn('5 kg', self.path.read_text(encoding='utf-8'))

    def test_concurrent_change_keeps_disk_and_draft(self):
        import utopia_phycyclus_app as app
        self.apply_pdf_edit()
        updated = self.original.replace('2 kg', '8 kg')
        self.path.write_text(updated, encoding='utf-8')
        with patch.object(app, '_duplicate_save_confirmation', return_value=True):
            self.at.button(key=self.save_key).click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(self.at.button(key=self.save_key).disabled)
        self.assertEqual(self.path.read_text(encoding='utf-8'), updated)
        self.set_view('LaTeX 源码')
        self.assertIn('4 kg', self.at.text_area(key=self.text_key).value)
        self.at.button(key=self.text_key + '_reload').click().run()
        self.assertIn('8 kg', self.at.text_area(key=self.text_key + '_readonly').value)
