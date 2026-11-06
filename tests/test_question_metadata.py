from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.question_import_ai_service import classify_question
from services.question_metadata import normalize_question_metadata
from utils.latex_ops import inject_meta_data, parse_meta_data


EDITOR_SCRIPT = '''
from pathlib import Path
from unittest.mock import patch
import streamlit as st
import utopia_phycyclus_app as app
app._apply_pending_browse_selection()
root = Path(st.session_state['fixture_root'])
path = next(root.rglob('*.tex'))
st.session_state['current_path'] = str(path)
with patch.object(app, 'CHAPTERS_DIR', str(root)), patch.object(app, 'update_csv_index_for_edit'), patch('utils.csv_ops.update_csv_index_for_edit'), patch.object(app, 'record_operation'), patch.object(app, '_invalidate_semantic_for_file'), patch.object(app, 'render_ai_solution_panel'), patch.object(app, 'render_ai_solution_image_ocr_section'):
    app.render_browse_question_editor_card(path.stem, path.read_text(encoding='utf-8'), str(path), 'metadata_test')
'''


class MetadataTests(unittest.TestCase):
    def test_one_star_does_not_erase_knowledge_or_other_difficulties(self):
        for difficulty in ('1', '1.0', 1):
            metadata = {'ID': '42', '标签': '基础，AI原创,基础，原创', '难度星级': difficulty,
                        '物理方法': '比例法', '知识点': '磁感应强度'}
            saved = normalize_question_metadata(metadata)
            self.assertEqual(saved['标签'], '基础，原创')
            self.assertEqual(saved['物理方法'], '')
            self.assertEqual(saved['知识点'], metadata['知识点'])
            self.assertEqual(saved['ID'], '42')
            self.assertEqual(metadata['物理方法'], '比例法')
        for difficulty in ('', '0', '0.5', '1.5', '2', 'unknown'):
            self.assertEqual(normalize_question_metadata({'难度星级': difficulty, '物理方法': '比例法'})['物理方法'], '比例法')

    def test_import_and_manual_metadata_injection_enforce_preferences(self):
        body = r'\begin{problem}{2026}{M}{卷}{1}{磁场运动}原题干\end{problem}'
        content = inject_meta_data(body, {'难度星级': '1.0', '物理方法': '比例法', '标签': '磁场，AI原创'})
        metadata, clean = parse_meta_data(content)
        self.assertEqual(clean, body)
        self.assertEqual(metadata['物理方法'], '')
        self.assertEqual(metadata['标签'], '磁场')
        with patch('services.question_import_ai_service._request_json', return_value={
            'subjects': ['磁场运动'], 'difficulty': 1, 'tags': ['基础', 'AI原创'],
            'physics_methods': ['比例法'], 'knowledge_points': ['半圆轨迹'],
        }):
            result = classify_question(body, ['磁场运动'], {})
        self.assertEqual(result['tags'], '基础')
        self.assertEqual(result['physics_methods'], '')
        self.assertEqual(result['knowledge_points'], '半圆轨迹')

    def test_paper_selection_tracks_renumbered_question_after_sort_changes(self):
        import utopia_phycyclus_app as app
        state = {'browse_mode': '按试卷浏览', 'paper_view_mode': '单题选择模式',
                 'paper_type': 'M', 'paper_year': '2026', 'paper_name': '旧卷', 'selected_q_idx_2026_旧卷': 0}
        path = str(Path('chapters/磁场运动/2025/2025-G-新卷-10-磁场运动.tex').resolve())
        with patch.object(app.st, 'session_state', state), patch.object(app, 'get_questions_by_paper_and_type',
                return_value=[{'path': 'another.tex'}, {'path': path}]):
            app._keep_renamed_question_selected('old.tex', path)
            self.assertEqual(state['paper_name'], '旧卷')
            app._apply_pending_browse_selection()
        self.assertEqual((state['paper_year'], state['paper_name'], state['paper_type']), ('2025', '新卷', 'G'))
        self.assertEqual(state['selected_q_idx_2025_新卷'], 1)

    def test_editor_stays_open_across_repeated_saves_and_rename(self):
        from streamlit.testing.v1 import AppTest
        import utopia_phycyclus_app as app
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'chapters'
            directory = root / '磁场运动' / '2026'
            directory.mkdir(parents=True)
            path = directory / '2026-M-测试卷-1-磁场运动.tex'
            body = r'\begin{problem}{2026}{M}{测试卷}{1}{磁场运动}原题干\end{problem}'
            path.write_text(inject_meta_data(body, {'ID': '42', '题型': '计算题', '难度星级': '1', '标签': '基础', '物理方法': ''}), encoding='utf-8')
            at = AppTest.from_string(EDITOR_SCRIPT, default_timeout=30)
            for name, value in dict(fixture_root=str(root), browse_mode='按知识板块浏览', browse_subject='磁场运动',
                                    browse_year_磁场运动='2026', browse_file_select_磁场运动_2026=path.name).items():
                at.session_state[name] = value
            at.run()
            key = app._question_key('meta', str(path))
            at.button(key=f'metadata_test_edit_meta_{key}').click().run()
            methods = at.text_input(key=f'metadata_test_physics_methods_{key}')
            self.assertTrue(methods.disabled)
            for points in ('第一次修改', '第二次修改'):
                at.text_input(key=f'metadata_test_knowledge_points_{key}').set_value(points).run()
                at.button(key=f'metadata_test_edit_meta_{key}').click().run()
                self.assertFalse(at.exception)
                self.assertEqual(at.text_input(key=f'metadata_test_knowledge_points_{key}').value, points)
                self.assertIn('保存文件信息', at.button(key=f'metadata_test_edit_meta_{key}').label)
                self.assertEqual(parse_meta_data(path.read_text(encoding='utf-8'))[0]['知识点'], points)
            at.text_input(key=f'metadata_test_meta_paper_{key}').set_value('重命名卷').run()
            at.text_input(key=f'metadata_test_meta_num_{key}').set_value('10').run()
            at.multiselect(key=f'metadata_test_tag_select_{key}').set_value(['电场']).run()
            at.button(key=f'metadata_test_edit_meta_{key}').click().run()
            self.assertFalse(at.exception)
            new_path = root / '电场' / '2026' / '2026-M-重命名卷-10-电场.tex'
            self.assertTrue(new_path.exists())
            self.assertFalse(path.exists())
            self.assertEqual(at.session_state['browse_subject'], '电场')
            self.assertEqual(at.session_state['browse_file_select_电场_2026'], new_path.name)
            new_key = app._question_key('meta', str(new_path))
            self.assertEqual(at.text_input(key=f'metadata_test_meta_paper_{new_key}').value, '重命名卷')
            self.assertIn('保存文件信息', at.button(key=f'metadata_test_edit_meta_{new_key}').label)
            at.text_input(key=f'metadata_test_knowledge_points_{new_key}').set_value('重命名后继续修改').run()
            at.button(key=f'metadata_test_edit_meta_{new_key}').click().run()
            self.assertFalse(at.exception)
            metadata, clean = parse_meta_data(new_path.read_text(encoding='utf-8'))
            self.assertEqual(metadata['知识点'], '重命名后继续修改')
            self.assertEqual(metadata['ID'], '42')
            self.assertIn('原题干', clean)


if __name__ == '__main__':
    unittest.main()
