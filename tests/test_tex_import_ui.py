"""Exercise the independent source/PDF workspace without writing the bank."""
import unittest
import uuid
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


SCRIPT = r'''
import streamlit as st
from unittest.mock import patch
import fitz
import utopia_phycyclus_app as app
st.sidebar.radio('录入模式', ['单题录入','批量试题录入','同卷试题录入'], key='tex_workspace_mode')

class Upload:
    name = '试题.tex'
    def getvalue(self):
        return st.session_state.get('test_upload', '').encode('utf-8')

def uploader(*args, **kwargs):
    return [Upload()] if kwargs.get('key') == 'tex_workspace_uploads' and st.session_state.get('test_upload') else None

def compile_preview(*args):
    if st.session_state.get('test_compile_fail'):
        return dict(
            ok=False,
            error='编译失败：TikZ 选项 “meterblue” 未定义（源码第 3 行）。',
            log="preview.tex:170: Package pgfkeys Error: I do not know the key '/tikz/meterblue'",
            diagnosis=('编译失败：TikZ 选项 “meterblue” 未定义（源码第 3 行）。\\n'
                       '修复：改用模板已定义的颜色，或先 \\\\definecolor{meterblue}{RGB}{0,120,190}。'),
        )
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), 'Local PDF preview')
        return dict(ok=True, pdf=pdf.tobytes(), log='Output written on preview.pdf', engine='xelatex')

def save_questions(questions):
    st.session_state['test_saved'] = [(q.year,q.paper,q.number,q.content) for q in questions]
    return []

def classify(*args):
    return dict(subject='机械振动与机械波', knowledge_points='波速公式，波形图', physics_methods='图像分析法', tags='易错题', difficulty='3')

def assess(question, *, force=False, source_dir=None):
    from dataclasses import replace
    st.session_state.setdefault('test_assess_calls', []).append((question.number, force))
    meta, body = app.parse_meta_data(question.content)
    if question.number == st.session_state.get('test_fail_assess'):
        raise ValueError('当前题证据响应无效')
    subject = '机械振动与机械波'
    body = app.replace_problem_header(body, question.year, question.paper_type, question.paper, question.number, subject)
    meta.setdefault('知识点', '波速公式，波形图')
    meta.setdefault('难度星级', '3.0')
    meta['测试评级依据'] = '已更新' if '新解析' in body else '首轮'
    return replace(question, content=app.inject_meta_data(body, meta), subject=subject)

def ai_split(question, expected, start, config):
    return [question]

with patch('streamlit.file_uploader', side_effect=uploader), patch.object(app, 'compile_tex_preview', side_effect=compile_preview), patch.object(app, '_save_tex_workspace_questions', side_effect=save_questions), patch.object(app, 'classify_question', side_effect=classify), patch.object(app, 'split_question_with_ai', side_effect=ai_split), patch.object(app, '_assess_batch_question', side_effect=assess, create=True), patch.object(app, '_render_batch_assessment', create=True), patch.object(app, '_mark_batch_overrides', side_effect=lambda q, fields: q, create=True):
    app.page_tex_entry()
'''


class TexImportUITests(unittest.TestCase):
    def app(self, upload=''):
        app = AppTest.from_string(SCRIPT, default_timeout=30)
        app.session_state['test_upload'] = upload
        app.run()
        self.assertFalse(app.exception)
        return app

    def edit(self, app, source):
        app.text_area(key='tex_workspace_source').set_value(source).run()
        self.assertFalse(app.exception)

    def test_upload_keeps_source_and_separate_drafts(self):
        source = r'\begin{problem}{2025}{M}{试卷}{9}{电场}题干\end{problem}\begin{solutions}[法二]解析\end{solutions}'
        app = self.app(source)
        app.session_state['entry_content'] = 'AI独立草稿'
        app.button(key='tex_workspace_load').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['tex_workspace_source'], source)
        self.assertEqual(app.session_state['entry_content'], 'AI独立草稿')

    def test_document_switch_preserves_edits(self):
        app = self.app()
        self.edit(app, '第一份草稿')
        app.button(key='tex_workspace_new').click().run()
        self.edit(app, '第二份草稿')
        app.selectbox(key='tex_workspace_selected').set_value(0).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['tex_workspace_source'], '第一份草稿')
        app.selectbox(key='tex_workspace_selected').set_value(1).run()
        self.assertEqual(app.session_state['tex_workspace_source'], '第二份草稿')

    def test_merge_ai_uses_widget_callback(self):
        app = self.app()
        self.edit(app, 'TeX草稿')
        app.session_state['entry_content'] = 'AI草稿'
        app.button(key='tex_workspace_merge_ai').click().run()
        self.assertFalse(app.exception)
        self.assertIn('TeX草稿', app.session_state['tex_workspace_source'])
        self.assertIn('AI草稿', app.session_state['tex_workspace_source'])

    def test_batch_ai_export_merge_preserves_existing_tex_question(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('批量试题录入').run()
        self.edit(app, r'\begin{problem}{2026}{M}{卷}{1}{电场}原TeX题\end{problem}')
        app.session_state['batch_content'] = '---2026-M-卷-2-电场.tex---\n' + r'\begin{problem}{2026}{M}{卷}{2}{电场}AI新题\end{problem}'
        app.button(key='tex_workspace_merge_ai').click().run()
        app.button(key='tex_workspace_detect').click().run()
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state['test_saved']), 2)
        self.assertIn('原TeX题', app.session_state['test_saved'][0][3])
        self.assertIn('AI新题', app.session_state['test_saved'][1][3])

    def test_compile_failure_retains_last_pdf_and_marks_stale(self):
        app = self.app()
        self.edit(app, '题干')
        app.button(key='tex_workspace_compile').click().run()
        self.assertFalse(app.exception)
        preview = app.session_state['tex_workspace_documents'][0]['preview']['pdf']
        self.edit(app, '修改后的题干')
        self.assertTrue(app.warning)
        app.session_state['test_compile_fail'] = True
        app.button(key='tex_workspace_compile').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertEqual(app.session_state['tex_workspace_documents'][0]['preview']['pdf'], preview)

    def test_compile_failure_shows_actionable_diagnosis(self):
        app = self.app()
        self.edit(app, r'\begin{tikzpicture}\draw[meterblue] (0,0)--(1,0);\end{tikzpicture}')
        app.session_state['test_compile_fail'] = True
        app.button(key='tex_workspace_compile').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        expander_labels = [e.label for e in app.expander]
        self.assertIn('🩺 编译诊断与修复建议', expander_labels)
        code_values = [c.value for c in app.code]
        self.assertTrue(any('meterblue' in (value or '') for value in code_values),
                        '诊断面板应展示未定义颜色名与修复建议')

    def test_single_rejects_multiple_questions(self):
        app = self.app()
        self.edit(app, r'\begin{problem}一\end{problem}\begin{problem}二\end{problem}')
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertNotIn('test_saved', app.session_state)

    def test_same_paper_overrides_uniform_fields_and_keeps_numbers(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('同卷试题录入').run()
        self.edit(app, r'\begin{problem}{2025}{M}{旧卷}{7}{电场}一\end{problem}\begin{problem}{2025}{M}{旧卷}{9}{电场}二\end{problem}')
        app.text_input(key='tex_workspace_paper').set_value('统一试卷').run()
        app.button(key='tex_workspace_detect').click().run()
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        saved = app.session_state['test_saved']
        self.assertEqual([q[2] for q in saved], ['7', '9'])
        self.assertTrue(all(q[0] == '2026' and q[1] == '统一试卷' for q in saved))

    def test_save_all_files(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('批量试题录入').run()
        self.edit(app, '一')
        app.button(key='tex_workspace_detect').click().run()
        app.button(key='tex_workspace_new').click().run()
        self.edit(app, '二')
        app.button(key='tex_workspace_detect').click().run()
        app.text_input(key='tex_review_1_1_0_number').set_value('2').run()
        app.selectbox(key='tex_workspace_save_scope').set_value('所有已载入文件').run()
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state['test_saved']), 2)

    def test_single_number_and_batch_range_labels(self):
        app = self.app()
        self.assertEqual(app.number_input(key='tex_workspace_number').label, '题号')
        self.assertFalse(any(t.label == '终止题号' for t in app.text_input))
        app.radio(key='tex_workspace_mode').set_value('批量试题录入').run()
        self.assertEqual(app.number_input(key='tex_workspace_number').label, '起始题号')
        self.assertEqual(app.text_input(key='tex_workspace_end_number').label, '终止题号')

    def test_detect_range_classify_and_manual_edit_before_saving(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('同卷试题录入').run()
        app.number_input(key='tex_workspace_number').set_value(8).run()
        app.text_input(key='tex_workspace_end_number').set_value('10').run()
        self.edit(app, '\\textbf{8.}\n题一\n\\textbf{9.}\n题二\n\\textbf{10.}\n题三')
        app.button(key='tex_workspace_detect').click().run()
        self.assertFalse(app.exception)
        questions = app.session_state['tex_workspace_documents'][0]['review_questions']
        self.assertEqual([q.number for q in questions], ['8', '9', '10'])
        self.assertTrue(all(q.subject == '机械振动与机械波' for q in questions))
        app.text_input(key='tex_review_0_1_0_points').set_value('手动修改知识点').run()
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state['test_saved']), 3)
        self.assertIn('手动修改知识点', app.session_state['test_saved'][0][3])

    def test_range_mismatch_blocks_saving(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('批量试题录入').run()
        app.text_input(key='tex_workspace_end_number').set_value('3').run()
        self.edit(app, r'\begin{problem}一\end{problem}\begin{problem}二\end{problem}')
        app.button(key='tex_workspace_detect').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        app.button(key='tex_workspace_save').click().run()
        self.assertNotIn('test_saved', app.session_state)

    def test_direct_save_and_edit_refresh_only_changed_question(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('批量试题录入').run()
        self.edit(app, r'\begin{problem}一\end{problem}\begin{problem}二\end{problem}')
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['test_assess_calls'], [('1', False), ('2', False)])
        self.assertTrue(all('% 题型: 计算题' in q[3] for q in app.session_state['test_saved']))
        app.text_input(key='tex_review_0_1_0_points').set_value('教师知识点').run()
        body = app.text_area(key='tex_review_0_1_0_content')
        body.set_value(body.value + r'\begin{solutions}新解析\end{solutions}').run()
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['test_assess_calls'], [('1', False), ('2', False), ('1', False)])
        self.assertIn('测试评级依据: 已更新', app.session_state['test_saved'][0][3])
        self.assertIn('知识点: 教师知识点', app.session_state['test_saved'][0][3])

    def test_multi_file_direct_save_keeps_failed_question_for_retry(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('同卷试题录入').run()
        self.edit(app, '第一题')
        app.button(key='tex_workspace_new').click().run()
        self.edit(app, '第二题')
        app.selectbox(key='tex_workspace_save_scope').set_value('所有已载入文件').run()
        app.session_state['test_fail_assess'] = '2'
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual([q[2] for q in app.session_state['test_saved']], ['1'])
        self.assertEqual(app.session_state['tex_workspace_save_logs'][0]['status'], 'error')
        app.session_state['test_fail_assess'] = ''
        app.button(key='tex_review_1_1_0_retry').click().run()
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['test_assess_calls'], [('1', False), ('2', False), ('2', True)])
        self.assertEqual([q[2] for q in app.session_state['test_saved']], ['1', '2'])

    def test_range_applies_to_all_files_total(self):
        app = self.app()
        app.radio(key='tex_workspace_mode').set_value('批量试题录入').run()
        self.edit(app, '题一')
        app.button(key='tex_workspace_new').click().run()
        self.edit(app, '题二')
        app.text_input(key='tex_workspace_end_number').set_value('2').run()
        app.selectbox(key='tex_workspace_save_scope').set_value('所有已载入文件').run()
        app.selectbox(key='tex_workspace_selected').set_value(0).run()
        app.button(key='tex_workspace_detect').click().run()
        app.selectbox(key='tex_workspace_selected').set_value(1).run()
        app.button(key='tex_workspace_detect').click().run()
        app.button(key='tex_workspace_save').click().run()
        self.assertFalse(app.exception)
        self.assertEqual([q[2] for q in app.session_state['test_saved']], ['1', '2'])

    def test_leaving_tex_entry_cleans_preview_cache(self):
        """退出录题界面后必须删除预览编译临时目录（用户明确要求）。"""
        entry = PROJECT_ROOT / '.tikz_cache' / 'tex-entry'
        entry.mkdir(parents=True, exist_ok=True)
        temp_dir = entry / ('preview-unittest-' + uuid.uuid4().hex[:10])
        temp_dir.mkdir()
        (temp_dir / 'preview.tex').write_text('x', encoding='utf-8')
        try:
            app = AppTest.from_file(str(PROJECT_ROOT / 'utopia_phycyclus_app.py'), default_timeout=90)
            app.session_state['main_sidebar_radio'] = '📄  TeX录入'
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(temp_dir.exists(), '进入录题页时只清扫旧目录，不应删除刚生成的目录')
            app.session_state['main_sidebar_radio'] = '📊  数据统计'
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(temp_dir.exists(), '离开 TeX 录入页后应删除预览临时编译目录')
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_sidebar_routes(self):
        app = AppTest.from_file('utopia_phycyclus_app.py', default_timeout=30)
        app.session_state['main_sidebar_radio'] = '📄\nTeX录入'
        app.run()
        self.assertFalse(app.exception)
        self.assertIn('📄 TeX 录入', [h.value for h in app.header])
        nav = app.radio(key='nav_group_a')
        target = next(option for option in nav.options if '图片识别' in option)
        nav.set_value(target).run()
        self.assertFalse(app.exception)
        self.assertIn('📝 图片识别', [h.value for h in app.header])
        self.assertFalse(any('TeX 文件直接录入' in e.label for e in app.expander))


if __name__ == '__main__':
    unittest.main()
