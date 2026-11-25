"""Exercise built-in skill delivery through the classic image OCR routes."""
from contextlib import nullcontext
import json

from PIL import Image
import pytest

from services.ocr_image_service import OCR_CROP_INSTRUCTIONS


SKILL_MARKER = '【内置技能：physics-image-to-latex】'


@pytest.fixture
def ocr_app(monkeypatch, tmp_path):
    import utopia_phycyclus_app as app

    monkeypatch.setattr(app, '_read_library_environment', lambda: {
        'AI_OCR_PROMPT': r'备用识别要求\n保留第二行',
    })
    monkeypatch.setattr(app, 'ocr_prompt_file', tmp_path / 'ocr_prompt.txt')
    monkeypatch.setattr(app, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(app.st, 'spinner', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(app.st, 'toast', lambda *args, **kwargs: None)
    monkeypatch.setattr(app.st, 'session_state', {})
    monkeypatch.setenv('AI_API_KEY', 'obsolete-fixture-key')
    monkeypatch.setenv('AI_BASE_URL', 'https://ocr.invalid/v1')
    monkeypatch.setenv('AI_MODEL_NAME', 'obsolete-fixture-model')
    return app


def _capture_completion(app, monkeypatch, replies):
    class Reply:
        status_code = 200

        def json(self):
            return {'choices': [{'message': {'content': next(responses)}}]}

    payloads = []
    responses = iter(replies)

    def complete(payload, timeout):
        assert set(payload) == {'messages'}
        payloads.append(payload)
        return Reply()

    monkeypatch.setattr(app, 'legacy_completion', complete)
    return payloads


def test_question_ocr_sends_builtin_skill_and_hot_reloaded_prompt(ocr_app, monkeypatch):
    app = ocr_app
    latex = r'\begin{problem}{2026}{M}{测试}{1}{电路}题干\end{problem}'
    payloads = _capture_completion(app, monkeypatch, [latex, latex])
    images = [Image.new('RGB', (90, 140), 'white')]
    app.ocr_prompt_file.write_text('自定义识别要求', encoding='utf-8')
    assert app.ocr_image_to_latex(images) == latex
    app.ocr_prompt_file.unlink()
    assert app.ocr_image_to_latex(images) == latex

    custom, fallback = [payload['messages'][0]['content'] for payload in payloads]
    assert '自定义识别要求' in custom[0]['text']
    assert '备用识别要求' not in custom[0]['text']
    assert '备用识别要求\n保留第二行' in fallback[0]['text']
    for parts in (custom, fallback):
        assert parts[0]['text'].count(SKILL_MARKER) == 1
        assert OCR_CROP_INSTRUCTIONS in parts[0]['text']
        assert parts[2]['type'] == 'image_url'


def test_solution_ocr_keeps_its_answer_only_format(ocr_app, monkeypatch):
    app = ocr_app
    fields = {'answer_tex': r'\begin{answer}B\end{answer}', 'solutions_tex': ''}
    payloads = _capture_completion(app, monkeypatch, [json.dumps(fields)])
    assert app.ocr_solution_images_to_answer_solutions([Image.new('RGB', (90, 140), 'white')]) == fields
    parts = payloads[0]['messages'][0]['content']
    assert SKILL_MARKER in parts[0]['text']
    assert '只包含两个字段：answer_tex 与 solutions_tex' in parts[0]['text']
    assert OCR_CROP_INSTRUCTIONS in parts[0]['text']
    assert parts[2]['type'] == 'image_url'
