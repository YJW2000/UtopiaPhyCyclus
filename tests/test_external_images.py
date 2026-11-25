"""External files must survive save, move, preview, Agent edits and export."""
import base64
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from PIL import Image, ImageDraw

from services.diagram_agent import AgentMessage, DiagramTools, PiDiagramAgent
from services.diagram_validate import structural_failure, validate_diagram
from services.question_image_ui import insert_image_code
from services.tex_compile_service import compile_tex_preview
from utils import latex_ops, tikz_ops
from utils.image_assets import (copy_graphics, graphics_fingerprint, graphics_references,
                                package_question_images, resolve_image, save_uploaded_image)

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = dict(year='2026', paper_type='M', paper='图片测试', number='1', subject='光学')


def png_bytes(color='white'):
    image = Image.new('RGB', (240, 120), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((15, 15, 225, 105), outline='navy', width=4)
    draw.line((20, 100, 215, 25), fill='red', width=5)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class ExternalImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / '.tikz_cache' / ('external-test-' + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.upload = save_uploaded_image(png_bytes(), '实验 装置_1.png', self.directory)
        self.code = r'\includegraphics[width=5cm]{%s}' % self.upload

    def tearDown(self):
        target = self.directory.resolve()
        assert target.parent == (ROOT / '.tikz_cache').resolve() and target.name.startswith('external-test-')
        shutil.rmtree(target, ignore_errors=True)

    def test_upload_preserves_bytes_and_same_name_does_not_overwrite(self):
        self.assertEqual((self.directory / self.upload).read_bytes(), png_bytes())
        repeated = save_uploaded_image(png_bytes(), '实验 装置_1.png', self.directory)
        other = save_uploaded_image(png_bytes('yellow'), '实验 装置_1.png', self.directory)
        self.assertEqual(repeated, self.upload)
        self.assertNotEqual(other, self.upload)
        self.assertEqual((self.directory / self.upload).read_bytes(), png_bytes())
        self.assertEqual((self.directory / other).read_bytes(), png_bytes('yellow'))

    def test_save_and_move_keeps_images_in_question_directory(self):
        question = '\\begin{problem}{2026}{M}{测试}{1}{光学}\n前文\n' + self.code + '\n后文\\end{problem}'
        old_dir = self.directory / 'chapters' / '光学' / '2026'
        saved = package_question_images(question, '原题.tex', old_dir, self.directory)
        ref = graphics_references(saved)[0]
        image = resolve_image(ref['path'], self.directory)
        self.assertEqual(image.parent.name, '原题 相关图')
        self.assertIn('前文\n' + ref['code'] + '\n后文', saved)
        moved = package_question_images(saved, '新题.tex', self.directory / 'chapters' / '电场' / '2026', self.directory)
        image.unlink()
        target = resolve_image(graphics_references(moved)[0]['path'], self.directory)
        self.assertEqual(target.parent.name, '新题 相关图')
        self.assertEqual(target.read_bytes(), png_bytes())

    def test_export_colliding_names_are_self_contained(self):
        first = self.directory / 'a' / '装置.png'
        second = self.directory / 'b' / '装置.png'
        for target, color in ((first, 'white'), (second, 'yellow')):
            target.parent.mkdir()
            target.write_bytes(png_bytes(color))
        source = r'\includegraphics{a/装置.png} between \includegraphics{b/装置.png}'
        exported = copy_graphics(source, self.directory / 'paper' / 'images', self.directory)
        shutil.rmtree(first.parent)
        shutil.rmtree(second.parent)
        refs = graphics_references(exported)
        self.assertNotEqual(refs[0]['path'], refs[1]['path'])
        for ref, color in zip(refs, ('white', 'yellow')):
            target = resolve_image(ref['path'], self.directory / 'paper')
            self.assertEqual(target.read_bytes(), png_bytes(color))

    def test_paper_bundle_copies_images_for_all_three_versions(self):
        import utopia_phycyclus_app as app
        source = r'\begin{problem}{2026}{M}{测试}{1}{光学}题干' + self.code + r'\end{problem}\begin{answer}' + self.code + r'\end{answer}'
        with patch.object(app, 'APP_ROOT', str(self.directory)):
            outputs = app._write_exam_bundle('图片试卷', str(self.directory / 'exports'),
                                            r'\begin{document}', source, r'\end{document}', [source], '练习类模板')
        self.assertEqual(set(outputs), {'题目', '答案', '题目（含答案）'})
        for path in outputs.values():
            document = Path(path).read_text(encoding='utf-8')
            refs = graphics_references(document)
            self.assertTrue(refs)
            for ref in refs:
                self.assertTrue(ref['path'].startswith('images/'))
                self.assertEqual(resolve_image(ref['path'], Path(path).parent).read_bytes(), png_bytes())

    def test_comments_and_detokenize_and_graphicspath(self):
        source = r'% \includegraphics{missing.png}' + '\n' + self.code.replace('{' + self.upload + '}', r'{\detokenize{' + self.upload + '}}')
        self.assertEqual(len(graphics_references(source)), 1)
        output = copy_graphics(source, self.directory / 'export' / 'images', self.directory)
        self.assertTrue(output.startswith(r'% \includegraphics{missing.png}'))
        path = resolve_image('实验 装置_1', self.directory, source=r'\graphicspath{{.runtime/question-images/}}')
        self.assertEqual(path, (self.directory / self.upload).resolve())

    def test_markdown_shows_image_between_prose_without_tex_leaking(self):
        with patch.object(latex_ops, 'BASE_DIR', str(self.directory)):
            markdown = latex_ops.latex_to_markdown('前段\n' + self.code + '\n后段')
        self.assertIn('data:image/png;base64,', markdown)
        self.assertNotIn(r'\includegraphics', markdown)
        self.assertLess(markdown.index('前段'), markdown.index('<img'))
        self.assertLess(markdown.index('<img'), markdown.index('后段'))

    def test_image_file_changes_invalidate_preview_cache(self):
        before = graphics_fingerprint(self.code, self.directory)
        (self.directory / self.upload).write_bytes(png_bytes('yellow'))
        self.assertNotEqual(before, graphics_fingerprint(self.code, self.directory))

    def test_validator_accepts_external_image_and_does_not_invent_tikz_physics_errors(self):
        report = validate_diagram(self.code, '斜面上的物体受重力作用', resource_dir=str(self.directory))
        self.assertEqual(report['gate'], 'pass', report['issues'])
        self.assertNotIn(r'\begin{tikzpicture}', report['code'])
        missing = validate_diagram(r'\includegraphics{missing.png}', resource_dir=str(self.directory))
        self.assertEqual(missing['gate'], 'fail')
        self.assertTrue(structural_failure(missing))

    def test_parser_and_apply_support_replace_and_insert(self):
        agent = PiDiagramAgent(base_dir=str(self.directory))
        _, code, _ = agent._clean_response('===TIKZ===\n' + self.code + '\n===EXPLANATION===已插入')
        self.assertEqual(code, self.code)
        _, fallback, _ = agent._clean_response('图片：' + self.code)
        self.assertEqual(fallback, self.code)
        original = r'\begin{problem}前文' + self.code + r'\begin{choices}\choice{A}\end{choices}\end{problem}'
        replacement = r'\includegraphics[width=4cm]{new.png}'
        self.assertNotIn(self.code, DiagramTools.apply_tikz_to_latex(original, replacement))
        inserted = DiagramTools.apply_tikz_to_latex(original, replacement, insert_new=True)
        self.assertIn(self.code, inserted)
        self.assertLess(inserted.index(replacement), inserted.index(r'\begin{choices}'))
        self.assertTrue(AgentMessage.from_dict(AgentMessage('assistant', '', insert_new=True).to_dict()).insert_new)

    def test_manual_marker_and_answer_position(self):
        original = '题干\n% 插图位置\n后文'
        self.assertEqual(insert_image_code(original, self.code, '标记位置（% 插图位置）'), original.replace('% 插图位置', self.code))
        with self.assertRaises(ValueError):
            insert_image_code('没有标记', self.code, '标记位置（% 插图位置）')
        original = r'\begin{solutions}过程\end{solutions}'
        updated = insert_image_code(original, self.code, '解析末尾')
        self.assertLess(updated.index(self.code), updated.index(r'\end{solutions}'))

    def test_unbracketed_magnetic_symbols_follow_user_convention(self):
        code = r'\begin{tikzpicture}\node at (0,0) {$\times$};\node at (1,0) {$\bullet$};\end{tikzpicture}'
        report = validate_diagram(code, '磁场方向垂直纸面向里和向外')
        self.assertNotIn('magnetic-symbol', [issue['code'] for issue in report['issues']])

    def test_agent_inserts_uploaded_image_with_real_filename(self):
        data_url = 'data:image/png;base64,' + base64.b64encode(png_bytes()).decode('ascii')
        agent = PiDiagramAgent(base_dir=str(self.directory))
        original = r'\begin{problem}题干\begin{tikzpicture}\draw (0,0)--(1,0);\end{tikzpicture}\end{problem}'
        captured = []

        def chat(_base, _headers, payload, timeout=None):
            captured.append(json.dumps(payload, ensure_ascii=False))
            response = '===ACTION===insert\n===THOUGHT===保留原图并插入上传图片\n===TIKZ===\n' + self.code + '\n===EXPLANATION===已插入原图'
            class Reply:
                status_code = 200
                def json(self):
                    return {'choices': [{'message': {'content': response}}]}
            return Reply(), ''

        with patch.object(agent, '_get_api_config', return_value=dict(api_key='test', base_url='http://test', model_name='test')), \
             patch('services.diagram_agent.post_chat_completion', side_effect=chat), \
             patch.object(DiagramTools, 'compile_with_log', return_value=tikz_ops.TikzCompileResult(ok=True, b64_png='preview')):
            result = agent.run_turn('插入上传的原图', original, max_self_healing_attempts=0, visual_check=False,
                                    reference_images=[dict(name='实验 装置_1.png', data_url=data_url)])
        self.assertTrue(result['should_autosync'], result['validation'])
        self.assertTrue(result['agent_message'].insert_new)
        self.assertIn(r'\begin{tikzpicture}', result['updated_latex'])
        self.assertIn(self.code, result['updated_latex'])
        self.assertIn(self.upload, captured[0])

    def test_upload_control_updates_editor_at_marker(self):
        from streamlit.testing.v1 import AppTest
        script = '''
import streamlit as st
from unittest.mock import patch
from services.question_image_ui import render_question_image_upload
class Upload:
    name = '原图.png'
    def getvalue(self): return st.session_state['image_data']
with patch('streamlit.file_uploader', return_value=[Upload()]):
    render_question_image_upload('source', 'fixture', st.session_state['root'])
st.text_area('源码', key='source')
'''
        app = AppTest.from_string(script, default_timeout=30)
        app.session_state['source'] = '前文\n% 插图位置\n后文'
        app.session_state['image_data'] = png_bytes()
        app.session_state['root'] = str(self.directory)
        app.run()
        app.selectbox(key='fixture_image_position').select('标记位置（% 插图位置）').run()
        app.button(key='fixture_image_insert').click().run()
        self.assertFalse(app.exception)
        source = app.session_state['source']
        self.assertIn(r'\includegraphics[width=0.65\linewidth]', source)
        self.assertNotIn('% 插图位置', source)
        self.assertEqual(resolve_image(graphics_references(source)[0]['path'], self.directory).read_bytes(), png_bytes())

    def test_agent_update_after_source_widget_is_applied_on_rerun(self):
        from streamlit.testing.v1 import AppTest
        script = '''
import streamlit as st
from services.question_image_ui import apply_pending_diagram_update, queue_diagram_update
apply_pending_diagram_update('source')
st.text_area('源码', key='source')
if st.button('应用 Agent 插图', key='apply'):
    queue_diagram_update('source', st.session_state['updated'])
'''
        app = AppTest.from_string(script, default_timeout=30)
        app.session_state['source'] = '旧源码'
        app.session_state['updated'] = '前文\n' + self.code + '\n后文'
        app.run()
        app.button(key='apply').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_area(key='source').value, app.session_state['updated'])

    @unittest.skipUnless(shutil.which('xelatex'), 'No XeLaTeX')
    def test_real_agent_preview_image_and_mixed_tikz(self):
        for code in (self.code, r'\begin{tikzpicture}\draw[->] (0,0)--(2,0);\end{tikzpicture}\quad ' + self.code):
            result = tikz_ops.compile_tikz(code, str(self.directory))
            self.assertTrue(result.ok, result.log[-2500:])
            self.assertTrue(result.b64_png)

    @unittest.skipUnless(shutil.which('xelatex'), 'No XeLaTeX')
    def test_real_question_and_export_compile_with_separate_images(self):
        question = r'\begin{problem}{2026}{M}{图片测试}{1}{光学}题干前段。' + self.code + r'题干后段。\end{problem}\begin{answer}略\end{answer}'
        saved = package_question_images(question, '单题.tex', self.directory / 'chapters' / '光学' / '2026', self.directory)
        result = compile_tex_preview(saved, str(ROOT), DEFAULTS, resource_dir=str(self.directory))
        self.assertTrue(result['ok'], result.get('log', '')[-2500:])
        exported = copy_graphics(saved, self.directory / 'paper' / 'images', self.directory)
        shutil.rmtree(self.directory / 'chapters')
        result = compile_tex_preview(exported, str(ROOT), DEFAULTS, resource_dir=str(self.directory / 'paper'))
        self.assertTrue(result['ok'], result.get('log', '')[-2500:])
        import fitz
        with fitz.open(stream=result['pdf'], filetype='pdf') as document:
            self.assertGreater(len(document[0].get_images()), 0)


if __name__ == '__main__':
    unittest.main()
