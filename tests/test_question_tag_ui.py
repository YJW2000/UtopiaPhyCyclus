"""Exercise the editable chips and per-question type controls without bank writes."""
import unittest
from pathlib import Path
import tempfile
from streamlit.testing.v1 import AppTest


TAG_SCRIPT = '''
import streamlit as st
import utopia_phycyclus_app as app
app._render_free_tag_editor('test', 'chips', '易错题，压轴题')
st.session_state['result'] = app._free_tag_editor_value('test', 'chips')
'''

ENTRY_SCRIPT = '''
import utopia_phycyclus_app as app
app.page_entry()
'''

CARD_SCRIPT = '''
import os
from pathlib import Path
import streamlit as st
from unittest.mock import patch
import utopia_phycyclus_app as app
path = st.session_state['fixture_path']
content = Path(path).read_text(encoding='utf-8')
st.session_state['test_hash'] = app._question_key('meta', path)
def update(path, values):
    st.session_state['saved_meta'] = values
with patch.object(app, 'render_question_header'), patch.object(app, 'render_question_preview'), patch.object(app, 'render_ai_solution_image_ocr_section'), patch.object(app, 'render_ai_solution_panel'), patch.object(app, 'apply_meta_rename_and_update', return_value=path), patch.object(app, 'update_question_meta_fields', side_effect=update), patch.object(app.time, 'sleep'):
    app.render_browse_question_editor_card('题目', content, path, 'test_card')
'''


class QuestionTagUITests(unittest.TestCase):
    def test_browse_card_saves_chips_and_manual_type(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        path = Path(fixture.name) / '2026-M-标签测试-1-电场.tex'
        path.write_text(r'\begin{problem}{2026}{M}{标签测试}{1}{电场}求电场强度。\end{problem}', encoding='utf-8')
        at = AppTest.from_string(CARD_SCRIPT, default_timeout=30)
        at.session_state['fixture_path'] = str(path)
        at.run()
        fhash = at.session_state['test_hash']
        at.button(key=f'test_card_edit_meta_{fhash}').click().run()
        self.assertFalse(at.exception)
        tag_key = f'test_card_custom_tag_selection_{fhash}'
        at.text_input(key=tag_key + '_new').set_value('标签甲，标签乙').run()
        at.multiselect(key=tag_key).set_value(['标签乙']).run()
        at.selectbox(key=f'test_card_question_type_{fhash}').set_value('计算题').run()
        at.button(key=f'test_card_edit_meta_{fhash}').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['saved_meta']['标签'], '标签乙')
        self.assertEqual(at.session_state['saved_meta']['题型'], '计算题')
        self.assertIn('保存文件信息', at.button(key=f'test_card_edit_meta_{fhash}').label)

    def test_chips_add_remove_all_and_readd(self):
        at = AppTest.from_string(TAG_SCRIPT).run(timeout=30)
        self.assertFalse(at.exception)
        key = 'test_custom_tag_selection_chips'
        at.multiselect(key=key).set_value(['压轴题']).run()
        self.assertEqual(at.session_state['result'], '压轴题')
        at.text_input(key=key + '_new').set_value('自定义甲，自定义乙').run()
        self.assertEqual(at.multiselect(key=key).value, ['压轴题', '自定义甲', '自定义乙'])
        at.multiselect(key=key).set_value([]).run()
        self.assertEqual(at.session_state['result'], '')
        at.text_input(key=key + '_new').set_value('重新添加').run()
        self.assertEqual(at.session_state['result'], '重新添加')
        self.assertFalse(at.exception)
        self.assertFalse(any('删除此标签' in button.label for button in at.button))

    def test_single_requires_explicit_type(self):
        at = AppTest.from_string(ENTRY_SCRIPT).run(timeout=30)
        self.assertFalse(at.exception)
        self.assertIsNone(at.selectbox(key='entry_question_type').value)
        save = next(button for button in at.button if button.label == '💾 保存题目')
        save.click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('选择题型' in toast.value for toast in at.get('toast')))

    def test_batch_and_same_paper_have_independent_types(self):
        for mode in ('批量试题录入', '同卷试题录入'):
            with self.subTest(mode=mode):
                at = AppTest.from_string(ENTRY_SCRIPT).run(timeout=30)
                source = '\\begin{problem}{2026}{M}{卷}{1}{电场}题干\\begin{choices}\\choice{甲}\\end{choices}\\end{problem}'
                source += '\\begin{answer}A\\end{answer}'
                second = source.replace('{1}', '{2}').replace('{answer}A', '{answer}BC')
                at.session_state['batch_content'] = '---2026-M-卷-1-电场.tex---\n' + source + '\n---2026-M-卷-2-电场.tex---\n' + second
                at.session_state['entry_mode_sidebar'] = mode
                at.run()
                self.assertFalse(at.exception)
                self.assertEqual(at.selectbox(key='batch_item_question_type_0').value, '单项选择题')
                self.assertEqual(at.selectbox(key='batch_item_question_type_1').value, '多项选择题')
                at.selectbox(key='batch_item_question_type_0').set_value('实验题').run()
                self.assertFalse(at.exception)
                self.assertIn('% 题型: 实验题', at.session_state['batch_content'])
                self.assertIn('% 题型: 多项选择题', at.session_state['batch_content'])


if __name__ == '__main__':
    unittest.main()
