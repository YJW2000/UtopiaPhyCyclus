import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from services import browse_preferences as prefs


SCRIPT = '''
import streamlit as st
from unittest.mock import patch
import utopia_phycyclus_app as app
from services.browse_preferences import prepare_browse_mode
route = st.radio('Test route', ['browse', 'other'], key='test_route')
def show(ctx, *args):
    st.write('display-page:' + str(ctx['current_page']))
    if ctx['display_data']:
        st.write('first-row:' + ctx['display_data'][0]['文件名称'])
rows=[{'文件名称':str(i), '初次录入的时间':f'{i:03d}'} for i in range(25)]
def browse(page, size, newest=True, scope=None):
    ordered=sorted(rows,key=lambda row:row['初次录入的时间'],reverse=newest)
    pages=max(1,(len(rows)+size-1)//size)
    page=max(1,min(page,pages))
    return dict(rows=ordered[(page-1)*size:page*size],total=len(rows),page=page,pages=pages)
if route=='browse':
    with patch.object(app.question_index, 'browse_page', side_effect=browse), patch.object(app, 'render_time_browse_list', side_effect=show):
        app.page_browse()
'''


class BrowseNavigationTests(unittest.TestCase):
    def test_view_selectors_persist_without_editor_text_or_credentials(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            state={'browse_subject':'电学实验','browse_year_电学实验':'2026',
                   'browse_file_select_电学实验_2026':'example.tex',
                   'browse_question_page_电学实验_2026_regular':2,
                   'paper_type':'M','paper_year':'2026','paper_name':'试卷',
                   'paper_view_mode':'单题选择模式','selected_q_idx_2026_试卷':3,
                   'api_key':'DO-NOT-SAVE','editor_text':'DO-NOT-SAVE'}
            prefs.save_browse_mode('按试卷浏览')
            view=prefs.remember_browse_view(state)
            self.assertNotIn('api_key',view)
            self.assertNotIn('editor_text',view)
            fresh={}
            prefs.prepare_browse_view(fresh)
            for key,value in view.items():
                self.assertEqual(fresh[key],value)
            fresh.pop('paper_name')
            prefs.prepare_browse_view(fresh)
            self.assertEqual(fresh['paper_name'],'试卷')
            fresh['paper_name']='新试卷'
            prefs.prepare_browse_view(fresh)
            self.assertEqual(fresh['paper_name'],'新试卷')
            prefs.remember_browse_view(fresh)
            self.assertEqual(prefs.load_browse_mode(),'按试卷浏览')
            self.assertNotIn('DO-NOT-SAVE',prefs.PREFERENCES_PATH.read_text(encoding='utf-8'))

    def test_invalid_view_preferences_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            prefs.PREFERENCES_PATH.write_text(json.dumps({'view_state':{'api_key':'secret','paper_name':[],'browse_subject':True,'selected_q_idx_a':-1,'selected_q_idx_b':'bad','browse_question_page_a':'bad'}}),encoding='utf-8')
            state={}
            prefs.prepare_browse_view(state)
            self.assertEqual(state,{'global_browse_view':{}})

    def test_position_restores_after_leaving_switching_mode_and_restarting(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            prefs.save_browse_mode('按录入顺序浏览')
            at=AppTest.from_string(SCRIPT, default_timeout=40).run()
            at.radio(key='time_sort_order').set_value('最早录入在最前').run()
            at.slider(key='time_max_show').set_value(5).run()
            for _ in range(2):
                at.button(key='time_browse_next').click().run()

            def check_position(app):
                self.assertFalse(app.exception)
                self.assertEqual(app.radio(key='time_sort_order').value, '最早录入在最前')
                self.assertEqual(app.slider(key='time_max_show').value, 5)
                self.assertEqual(app.session_state['time_browse_page'], 3)
                self.assertIn('first-row:10', [v.value for v in app.markdown])

            check_position(at)
            at.radio(key='test_route').set_value('other').run()
            at.radio(key='test_route').set_value('browse').run()
            check_position(at)
            # Unmount only the sort/size controls by choosing another browse mode.
            at.radio(key='browse_mode').set_value('按知识板块浏览').run()
            self.assertFalse(at.exception)
            at.radio(key='browse_mode').set_value('按录入顺序浏览').run()
            check_position(at)
            check_position(AppTest.from_string(SCRIPT, default_timeout=40).run())
            self.assertEqual(prefs.load_browse_preferences(), dict(mode='按录入顺序浏览', sort_order='最早录入在最前', page_size=5, page=3))

    def test_saved_page_is_clamped_when_question_count_changes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            prefs.PREFERENCES_PATH.write_text(json.dumps(dict(mode='按录入顺序浏览', sort_order='最早录入在最前', page_size=5, page=8)), encoding='utf-8')
            at=AppTest.from_string(SCRIPT, default_timeout=40).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['time_browse_page'], 5)
            self.assertEqual(prefs.load_browse_preferences()['page'], 5)
            at=AppTest.from_string(SCRIPT.replace('range(25)', 'range(6)'), default_timeout=40).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['time_browse_page'], 2)
            self.assertEqual(prefs.load_browse_preferences()['page'], 2)

    def test_legacy_file_validation_merge_and_no_repeated_writes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            prefs.save_browse_mode('按录入顺序浏览')
            self.assertEqual(prefs.load_browse_preferences(), dict(mode='按录入顺序浏览', sort_order='最新录入在最前', page_size=10, page=1))
            state={}
            prefs.prepare_time_browse(state)
            state.update(time_sort_order='最早录入在最前', time_max_show=7, time_browse_page=3)
            prefs.remember_time_browse_position(state)
            prefs.save_browse_mode('按试卷浏览')
            self.assertEqual(prefs.load_browse_preferences(), dict(mode='按试卷浏览', sort_order='最早录入在最前', page_size=7, page=3))
            with patch.object(prefs, 'atomic_write_text') as writer:
                prefs.remember_time_browse_position(state)
                writer.assert_not_called()
            # Other workflows sharing temporary keys cannot change this position.
            state.update(time_sort_order='最新录入在最前', time_max_show=20, time_browse_page=1)
            prefs.prepare_time_browse(state)
            self.assertEqual((state['time_sort_order'],state['time_max_show'],state['time_browse_page']), ('最早录入在最前',7,3))
            for bad in (None, True, '99', -1, 1.5):
                prefs.PREFERENCES_PATH.write_text(json.dumps(dict(page=bad, page_size=bad, sort_order=bad)), encoding='utf-8')
                result=prefs.load_browse_preferences()
                self.assertEqual((result['page'],result['page_size'],result['sort_order']), (1,10,'最新录入在最前'))

    def test_index_read_error_does_not_erase_saved_position(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            saved=dict(mode='按录入顺序浏览', sort_order='最早录入在最前', page_size=5, page=3)
            prefs.PREFERENCES_PATH.write_text(json.dumps(saved), encoding='utf-8')
            script=SCRIPT.replace("side_effect=browse", "side_effect=OSError('index temporarily locked')")
            # The test-only list renderer should tolerate an unavailable index.
            at=AppTest.from_string(script, default_timeout=40).run()
            self.assertFalse(at.exception)
            self.assertTrue(at.error)
            self.assertEqual(prefs.load_browse_preferences(), saved)

    def test_paging_preserves_mode_and_renders_new_rows(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            prefs.save_browse_mode('按录入顺序浏览')
            at=AppTest.from_string(SCRIPT, default_timeout=40).run()
            self.assertFalse(at.exception)
            at.button(key='time_browse_next').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.radio(key='browse_mode').value, '按录入顺序浏览')
            self.assertEqual(at.session_state['time_browse_page'], 2)
            self.assertIn('first-row:14', [v.value for v in at.markdown])
            at.button(key='time_browse_next').click().run()
            self.assertEqual(at.session_state['time_browse_page'], 3)
            self.assertTrue(at.button(key='time_browse_next').disabled)
            at.button(key='time_browse_prev').click().run()
            self.assertEqual(at.session_state['time_browse_page'], 2)
            at.slider(key='time_max_show').set_value(5).run()
            self.assertEqual(at.session_state['time_browse_page'], 1)
            at.button(key='time_browse_next').click().run()
            at.radio(key='time_sort_order').set_value('最早录入在最前').run()
            self.assertEqual(at.session_state['time_browse_page'], 1)

    def test_each_mode_survives_unmount_and_new_session(self):
        # Exercise the actual page with an empty index: no AI calls or question writes.
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            script=SCRIPT.replace('range(25)', 'range(0)')
            at=AppTest.from_string(script, default_timeout=40).run()
            for mode in prefs.BROWSE_MODES:
                at.radio(key='browse_mode').set_value(mode).run()
                self.assertFalse(at.exception)
                self.assertEqual(prefs.load_browse_mode(), mode)
                at.radio(key='test_route').set_value('other').run()
                at.radio(key='test_route').set_value('browse').run()
                self.assertFalse(at.exception)
                self.assertEqual(at.radio(key='browse_mode').value, mode)
                fresh=AppTest.from_string(script, default_timeout=40).run()
                self.assertFalse(fresh.exception)
                self.assertEqual(fresh.radio(key='browse_mode').value, mode)

    def test_invalid_preferences_fall_back_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(prefs, 'PREFERENCES_PATH', Path(folder)/'browse.json'):
            for data in ('bad json', '[]', json.dumps({'mode':'unknown'})):
                prefs.PREFERENCES_PATH.write_text(data, encoding='utf-8')
                self.assertEqual(prefs.load_browse_mode(), prefs.BROWSE_MODES[0])


if __name__=='__main__':
    unittest.main()
