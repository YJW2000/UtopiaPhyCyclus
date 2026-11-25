"""OCR crop localization, original pixels, page ownership and app integration."""
from contextlib import ExitStack, nullcontext
import copy
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from PIL import Image, ImageDraw

from services.ocr_image_service import (
    OCR_CROP_INSTRUCTIONS, OcrCropError, crop_retry_messages,
    materialize_ocr_crops, prepare_ocr_images,
)
from services.tex_compile_service import compile_tex_preview
from services.tex_import_service import detect_tex_questions
from utils.image_assets import graphics_references, package_question_images, resolve_image


ROOT = Path(__file__).resolve().parents[1]


def worksheet(size=(2400, 1600), color='red'):
    image = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((size[0] // 4, size[1] // 4, size[0] * 3 // 4 - 1, size[1] * 3 // 4 - 1), fill=color)
    return image


class Reply:
    status_code = 200
    text = ''

    def __init__(self, content):
        self.content = content

    def json(self):
        return {'choices': [{'message': {'content': self.content}}]}


class OcrImageCropTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / '.tikz_cache' / ('ocr-crops-test-' + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.images = [worksheet()]

    def tearDown(self):
        target = self.directory.resolve()
        assert target.parent == (ROOT / '.tikz_cache').resolve() and target.name.startswith('ocr-crops-test-')
        shutil.rmtree(target, ignore_errors=True)

    def test_model_inputs_are_numbered_and_originals_are_not_downscaled(self):
        originals, parts = prepare_ocr_images(self.images + [worksheet((600, 400), 'blue')], 'OCR', 1200)
        self.assertEqual([image.size for image in originals], [(2400, 1600), (600, 400)])
        self.assertEqual(self.images[0].size, (2400, 1600))
        self.assertIn(OCR_CROP_INSTRUCTIONS, parts[0]['text'])
        self.assertIn('【原图 1】', parts[1]['text'])
        self.assertIn('【原图 2】', parts[3]['text'])
        import base64
        with Image.open(io.BytesIO(base64.b64decode(parts[2]['image_url']['url'].split(',')[1]))) as sent:
            self.assertEqual(sent.size, (1200, 800))

    def test_crop_uses_exact_original_pixels_and_preserves_inline_location(self):
        text = r'前文\UtopiaImageCrop{1}{250,250,750,750}后文'
        result = materialize_ocr_crops(text, self.images, self.directory, padding_ratio=0)
        self.assertEqual(result.crops[0]['pixel_box'], [600, 400, 1800, 1200])
        ref = graphics_references(result.latex)[0]
        self.assertEqual(result.latex, '前文' + ref['code'] + '后文')
        with Image.open(resolve_image(ref['path'], self.directory)) as crop:
            self.assertEqual(crop.size, (1200, 800))
            self.assertEqual(crop.tobytes(), self.images[0].crop((600, 400, 1800, 1200)).tobytes())
        self.assertNotIn('UtopiaImageCrop', result.latex)

    def test_multiple_questions_and_options_keep_their_page_and_placement(self):
        images = self.images + [worksheet((1600, 2400), 'blue')]
        text = (r'\begin{problem}{2026}{M}{测试}{1}{光学}第一题'
                r'\begin{choices}\choice{{\UtopiaImageCrop[0.9]{2}{250,250,750,750}}}\choice{{B}}\end{choices}\end{problem}'
                r'\begin{problem}{2026}{M}{测试}{2}{光学}第二题\UtopiaImageCrop{1}{250,250,750,750}\end{problem}')
        result = materialize_ocr_crops(text, images, self.directory, padding_ratio=0)
        questions = detect_tex_questions(result.latex, '试卷.tex', {})
        self.assertEqual(len(questions), 2)
        first_ref = graphics_references(questions[0].content)[0]
        self.assertIn(r'\choice{{' + first_ref['code'] + '}}', questions[0].content)
        self.assertIn('width=0.90', first_ref['code'])
        second_ref = graphics_references(questions[1].content)[0]
        for ref, color in ((first_ref, (0, 0, 255)), (second_ref, (255, 0, 0))):
            with Image.open(resolve_image(ref['path'], self.directory)) as crop:
                self.assertEqual(crop.getpixel((crop.width // 2, crop.height // 2)), color)

    def test_padding_retains_edges_and_never_crosses_image_bounds(self):
        result = materialize_ocr_crops(r'\UtopiaImageCrop{1}{0,0,1000,1000}', self.images, self.directory)
        self.assertEqual(result.crops[0]['pixel_box'], [0, 0, 2400, 1600])
        result = materialize_ocr_crops(r'\UtopiaImageCrop{1}{250,250,750,750}', self.images, self.directory)
        self.assertEqual(result.crops[0]['pixel_box'], [597, 397, 1803, 1203])

    def test_exif_orientation_matches_model_and_crop_source(self):
        original = worksheet((400, 200))
        original.getexif()[274] = 6
        originals, _ = prepare_ocr_images([original], 'OCR')
        self.assertEqual(originals[0].size, (200, 400))
        self.assertEqual(original.size, (400, 200))
        self.assertEqual(original.getexif()[274], 6)
        result = materialize_ocr_crops(r'\UtopiaImageCrop{1}{0,0,500,1000}', originals, self.directory, padding_ratio=0)
        self.assertEqual(result.crops[0]['size'], [100, 400])

    def test_transparency_is_read_and_saved_on_the_same_white_background(self):
        transparent = Image.new('RGBA', (100, 100), (255, 0, 0, 0))
        originals, _ = prepare_ocr_images([transparent], 'OCR')
        self.assertEqual(originals[0].getpixel((50, 50)), (255, 255, 255))

    def test_bad_localization_is_rejected_before_any_file_is_saved(self):
        valid = r'\UtopiaImageCrop{1}{250,250,750,750}'
        invalid_markers = [
            r'\UtopiaImageCrop{2}{250,250,750,750}', r'\UtopiaImageCrop{0}{250,250,750,750}',
            r'\UtopiaImageCrop{1}{750,250,250,750}', r'\UtopiaImageCrop{1}{0,0,1001,1000}',
            r'\UtopiaImageCrop{1}{0,0,0.75,0.75}', r'\UtopiaImageCrop{1}{NaN,0,750,750}',
            r'\UtopiaImageCrop[1.2]{1}{250,250,750,750}', r'\UtopiaImageCrop{1}{250,250,750}',
            r'\UtopiaImageCrop{1}{250,250,', '% 插图位置',
            '% ' + valid, r'\begin{tikzpicture}' + valid + r'\end{tikzpicture}',
            r'\includegraphics{imaginary-photo.png}',
        ]
        for invalid in invalid_markers:
            with self.subTest(marker=invalid), self.assertRaises(OcrCropError):
                materialize_ocr_crops(valid + '\n' + invalid, self.images, self.directory)
            self.assertFalse((self.directory / '.runtime').exists())

    def test_repeated_region_reuses_one_file(self):
        marker = r'\UtopiaImageCrop{1}{250,250,750,750}'
        result = materialize_ocr_crops(marker + '\n' + marker, self.images, self.directory)
        refs = graphics_references(result.latex)
        self.assertEqual(refs[0]['path'], refs[1]['path'])
        self.assertEqual(len(result.crops), 1)

    def test_tikz_only_response_is_unchanged_and_creates_no_images(self):
        text = r'\begin{tikzpicture}\draw (0,0)--(1,0);\end{tikzpicture}'
        result = materialize_ocr_crops(text, self.images, self.directory)
        self.assertEqual(result.latex, text)
        self.assertEqual(result.crops, [])
        self.assertFalse((self.directory / '.runtime').exists())

    def test_retry_preserves_numbered_image_messages_and_all_recognized_text(self):
        _, parts = prepare_ocr_images(self.images, 'OCR')
        messages = [{'role': 'user', 'content': parts}]
        retry = crop_retry_messages(messages, '第一题和第二题', OcrCropError('编号错误'))
        self.assertEqual(len(messages), 1)
        self.assertIs(retry[0], messages[0])
        self.assertEqual(retry[1]['content'], '第一题和第二题')
        self.assertIn('编号错误', retry[2]['content'])

    def _run_question_ocr(self, responses, images=None):
        import utopia_phycyclus_app as app
        captured = []
        replies = iter(responses)

        def complete(payload, timeout):
            captured.append(copy.deepcopy(payload))
            return Reply(next(replies))

        with ExitStack() as stack:
            stack.enter_context(patch.object(app, 'BASE_DIR', str(self.directory)))
            stack.enter_context(patch.object(app.st, 'spinner', side_effect=lambda *a, **k: nullcontext()))
            stack.enter_context(patch.object(app.st, 'toast'))
            state = stack.enter_context(patch.object(app.st, 'session_state', {}))
            stack.enter_context(patch.object(app, 'legacy_completion', side_effect=complete))
            result = app.ocr_image_to_latex(images=images or self.images, max_image_size=1200)
            return result, captured, dict(state)

    def test_question_recognition_automatically_saves_and_replaces_crop(self):
        text = r'\begin{problem}{2026}{M}{测试}{1}{光学}原图如下\UtopiaImageCrop{1}{250,250,750,750}\end{problem}'
        result, captured, state = self._run_question_ocr([text])
        self.assertFalse(result.startswith('❌'), result)
        ref = graphics_references(result)[0]
        self.assertTrue(resolve_image(ref['path'], self.directory).exists())
        self.assertIn('0 到 1000', captured[0]['messages'][0]['content'][0]['text'])
        self.assertNotIn('ocr_crop_failed_draft', state)
        self.assertEqual(len(captured), 1)

    def test_invalid_box_is_automatically_repaired_once(self):
        result, captured, _ = self._run_question_ocr([
            r'题干\UtopiaImageCrop{9}{250,250,750,750}', r'题干\UtopiaImageCrop{1}{250,250,750,750}',
        ])
        self.assertFalse(result.startswith('❌'), result)
        self.assertEqual(len(captured), 2)
        self.assertIn('只有 1 张原图', captured[1]['messages'][-1]['content'])
        self.assertEqual(len(captured[0]['messages']), 1)

    def test_persistent_crop_failure_retains_draft_instead_of_publishing_broken_tex(self):
        bad = r'题干\UtopiaImageCrop{9}{250,250,750,750}'
        result, captured, state = self._run_question_ocr([bad, bad])
        self.assertTrue(result.startswith('❌ 自动裁图失败'), result)
        self.assertIn(bad, result)
        self.assertEqual(state['ocr_crop_failed_draft'], bad)
        self.assertEqual(len(captured), 2)
        self.assertFalse((self.directory / '.runtime').exists())

    def test_solution_recognition_crops_inside_answer_and_solutions(self):
        import utopia_phycyclus_app as app
        fields = dict(answer_tex=r'\begin{answer}答案\UtopiaImageCrop{1}{250,250,750,750}\end{answer}',
                      solutions_tex=r'\begin{solutions}解析\UtopiaImageCrop{2}{250,250,750,750}\end{solutions}')
        with ExitStack() as stack:
            stack.enter_context(patch.object(app, 'BASE_DIR', str(self.directory)))
            stack.enter_context(patch.object(app.st, 'session_state', {}))
            stack.enter_context(patch.object(app, 'legacy_completion', return_value=Reply(json.dumps(fields))))
            result = app.ocr_solution_images_to_answer_solutions([self.images[0], worksheet(color='blue')])
        self.assertNotIn('error', result, result)
        for field in fields:
            self.assertNotIn('UtopiaImageCrop', result[field])
            self.assertEqual(len(graphics_references(result[field])), 1)

    def test_pdf_pages_are_cropped_using_their_own_rendered_coordinates(self):
        import fitz
        import utopia_phycyclus_app as app
        with fitz.open() as document:
            for color in ((1, 0, 0), (0, 0, 1)):
                page = document.new_page(width=400, height=600)
                page.draw_rect(fitz.Rect(100, 150, 300, 450), color=None, fill=color)
            pages, error = app.render_pdf_pages_to_images(document.tobytes(), [1, 2])
        self.assertFalse(error, error)
        result = materialize_ocr_crops(r'\UtopiaImageCrop{2}{250,250,750,750}', pages, self.directory)
        with Image.open(resolve_image(graphics_references(result.latex)[0]['path'], self.directory)) as crop:
            self.assertEqual(crop.getpixel((crop.width // 2, crop.height // 2)), (0, 0, 255))

    @unittest.skipUnless(shutil.which('xelatex'), 'No XeLaTeX')
    def test_cropped_question_survives_save_and_real_tex_compile(self):
        result = materialize_ocr_crops(r'Before \UtopiaImageCrop{1}{250,250,750,750} After', self.images, self.directory)
        saved = package_question_images(result.latex, '题目.tex', self.directory / 'chapters' / '光学' / '2026', self.directory)
        for crop in result.crops:
            (self.directory / crop['path']).unlink()
        document = r'\documentclass{article}\usepackage{graphicx}\begin{document}' + saved + r'\end{document}'
        compiled = compile_tex_preview(document, str(ROOT), {}, resource_dir=str(self.directory))
        self.assertTrue(compiled['ok'], compiled.get('log', '')[-2000:])
        import fitz
        with fitz.open(stream=compiled['pdf'], filetype='pdf') as pdf:
            self.assertGreater(len(pdf[0].get_images()), 0)
            self.assertIn('Before', pdf[0].get_text())
            self.assertIn('After', pdf[0].get_text())


if __name__ == '__main__':
    unittest.main()
