from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


SCRIPT = '''
from pathlib import Path
from unittest.mock import patch
import streamlit as st
import utopia_phycyclus_app as app
from services import browse_preferences

root = Path(st.session_state['fixture_root'])

def browse(page, size, **kwargs):
    names = [('01.tex', '02.tex'), ('03.tex', '04.tex')][page - 1]
    rows = [{'相对文件路径': name, '文件名称': name} for name in names]
    return dict(rows=rows, total=4, page=page, pages=2)

def save(path, base, updated):
    assert Path(path).read_bytes() == base
    Path(path).write_bytes(updated.encode())

with patch.object(app, 'CHAPTERS_DIR', str(root)), \
     patch.object(app.question_index, 'browse_page', side_effect=browse), \
     patch.object(browse_preferences, 'PREFERENCES_PATH', root / 'view.json'), \
     patch.object(app, 'render_question_preview'), \
     patch.object(app, 'render_ai_solution_panel'), \
     patch.object(app, 'render_ai_solution_image_ocr_section'), \
     patch.object(app, '_save_browse_batch_solution', side_effect=save):
    app.page_browse()
'''


def test_global_browse_batch_keeps_results_and_saves_only_reviewed(tmp_path):
    path = tmp_path / 'ui.py'
    path.write_text(SCRIPT, encoding='utf-8')
    first = r'\begin{problem}{2026}{M}{题卷}{1}{电场}First\end{problem}\begin{answer}A\end{answer}'
    failed = r'\begin{problem}{2026}{M}{题卷}{2}{电场}Failed\end{problem}'
    third = r'\begin{problem}{2026}{M}{题卷}{3}{电场}Third\end{problem}'
    complete = r'\begin{problem}{2026}{M}{题卷}{4}{电场}Complete\end{problem}'
    sources = {f'{index:02}.tex': source for index, source in enumerate((first, failed, third, complete), 1)}
    for name, source in sources.items():
        (tmp_path / name).write_bytes(source.encode())

    ui = AppTest.from_file(str(path), default_timeout=25)
    ui.session_state['fixture_root'] = str(tmp_path)
    ui.session_state['browse_mode'] = '按录入顺序浏览'
    ui.run()
    assert not ui.exception
    ui.toggle(key='browse_solution_batch_enabled').set_value(True).run()

    def choices():
        return [widget for widget in ui.checkbox if widget.label == '加入批量补充']

    choices()[0].check().run()
    choices()[1].check().run()
    ui.button(key='time_browse_next').click().run()
    choices()[0].check().run()
    choices()[1].check().run()
    assert any('已选 4 道题' in message.value for message in ui.caption)

    solution = r'\begin{solutions}补充推导\end{solutions}'

    def generate(source, **kwargs):
        if source == failed:
            raise ValueError('缺少图中数据')
        return source if source == complete else source + solution

    with patch('services.batch_solution_ui.fill_missing_solution', side_effect=generate) as agent:
        ui.button(key='browse_solution_generate').click().run()
    assert not ui.exception
    assert [call.args[0] for call in agent.call_args_list] == [first, failed, third, complete]
    assert all((tmp_path / name).read_text(encoding='utf-8') == source for name, source in sources.items())
    assert any('补充失败 1 道' in message.value and '答案与解析已齐全 1 道' in message.value for message in ui.caption)
    assert ui.button(key='browse_solution_save').disabled

    reviewed = next(widget for widget in ui.checkbox if widget.label == '这道题已核对')
    reviewed.check().run()
    ui.button(key='browse_solution_save').click().run()
    assert not ui.exception
    assert (tmp_path / '01.tex').read_text(encoding='utf-8') == first + solution
    assert (tmp_path / '03.tex').read_text(encoding='utf-8') == third
    assert ui.button(key='browse_solution_save').disabled
