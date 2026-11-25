import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services import launcher
from utils import csv_ops, latex_ops, tikz_ops


class PerformanceStorageTests(unittest.TestCase):
    def test_index_reads_are_independent_and_committed_edits_are_visible(self):
        import csv
        from utils.index_schema import CSV_HEADERS
        from utils import question_index
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'index.csv'
            row=dict.fromkeys(CSV_HEADERS,'')
            row.update({'题目ID':'1','文件名称':'test','相对文件路径':'test.tex','题干':'old'})
            with path.open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=CSV_HEADERS); writer.writeheader(); writer.writerow(row)
            with patch.object(csv_ops,'CSV_INDEX_PATH',str(path)):
                first=csv_ops.read_csv_index()
                first[0]['题干']='poison'
                self.assertEqual(csv_ops.read_csv_index()[0]['题干'],'old')
                row['题干']='new value'
                question_index.put_row(row,csv_path=str(path))
                self.assertEqual(csv_ops.read_csv_index()[0]['题干'],'new value')

    def test_unchanged_diagram_does_not_write_or_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            source=r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}'
            latex_ops.extract_and_replace_tikz(source,'test.tex',folder)
            with patch.object(latex_ops,'atomic_write_text') as writer:
                latex_ops.extract_and_replace_tikz(source+'new prose','test.tex',folder)
                writer.assert_not_called()

    def test_tikz_template_configuration_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            source=r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}'
            calls=[]
            def compile_(code,base_dir,timeout=None,target_png_path=None):
                Path(target_png_path).write_bytes(b'png-data')
                calls.append(code)
                return tikz_ops.TikzCompileResult(ok=True,b64_png='cG5nLWRhdGE=')
            with patch.object(tikz_ops,'compile_tikz',side_effect=compile_):
                tikz_ops.get_tikz_image_b64(source,folder)
                tikz_ops.get_tikz_image_b64(source,folder)
                self.assertEqual(len(calls),1)
                with patch.object(tikz_ops,'_build_standalone_document',return_value='new-template'+source):
                    tikz_ops.get_tikz_image_b64(source,folder)
                self.assertEqual(len(calls),2)

    def test_launcher_marker_and_foreign_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'requirements.txt').write_text('example==1\n')
            marker=root/'deps.json'
            marker.write_text(json.dumps(dict(fingerprint=launcher.environment_fingerprint(root))))
            with patch.object(launcher.importlib.metadata,'version',return_value='1'):
                self.assertTrue(launcher.dependencies_ready(root,marker))
                (root/'requirements.txt').write_text('example==2\n')
                self.assertFalse(launcher.dependencies_ready(root,marker))
            self.assertIsNone(launcher.reusable_port(root,dict(root='other project')))


CARD_SCRIPT = '''
from pathlib import Path
from unittest.mock import patch
import streamlit as st
import utopia_phycyclus_app as app
path=st.session_state['fixture_path']
original=st.session_state['original']
def header(*a,**k): st.markdown('### Test question')
def index_update(*a): st.session_state['index_updates']=st.session_state.get('index_updates',0)+1
with patch.object(app,'render_question_header',side_effect=header), patch.object(app,'render_ai_solution_panel'), patch.object(app,'render_ai_solution_image_ocr_section'), patch.object(app,'_duplicate_save_confirmation',return_value=True), patch.object(app,'record_operation'), patch.object(app,'_update_csv_index_for_content_change',side_effect=index_update):
    app.render_browse_question_editor_card('Question', original, path, 'save_test')
'''


class SaveUITests(unittest.TestCase):
    def test_save_updates_preview_and_centers_without_agent_loading(self):
        from streamlit.testing.v1 import AppTest
        import utopia_phycyclus_app as app
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'question.tex'
            original=r'\begin{problem}{2026}{M}{test}{1}{电场}Old content\end{problem}'
            path.write_text(original,encoding='utf-8')
            at=AppTest.from_string(CARD_SCRIPT,default_timeout=30)
            at.session_state['fixture_path']=str(path)
            at.session_state['original']=original
            at.run()
            self.assertFalse(at.exception)
            key=app._question_key('meta',str(path))
            at.button(key=f'save_test_tex_edit_{key}').click().run()
            textkey='save_test_edit_'+app._question_key('text',str(path))
            updated=original.replace('Old content','Updated content')
            at.text_area(key=textkey).set_value(updated).run()
            with patch.object(app,'_update_csv_index_for_content_change') as update_index, patch.object(app,'record_operation'), patch.object(app,'_duplicate_save_confirmation',return_value=True):
                at.button(key=f'save_test_tex_edit_{key}').click().run()
                update_index.assert_called_once()
            self.assertFalse(at.exception)
            self.assertIn('Updated content',path.read_text(encoding='utf-8'))
            self.assertTrue(any('Updated content' in m.value for m in at.markdown))
            self.assertFalse(at.checkbox(key=f'browse_agent_open_{key}').value)


if __name__=='__main__':
    unittest.main()
