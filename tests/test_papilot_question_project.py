import hashlib
from pathlib import Path

import pytest

from services.papilot_question_project import prepare_question_project, read_question_project
from utils.image_assets import graphics_references


DEFAULTS = dict(year='2026', paper_type='M', paper='书稿', number='1', subject='电路')


def test_fragment_reopens_without_overwriting_and_freezes_imported_figures(tmp_path):
    library = tmp_path / '题库'
    question = library / 'chapter'
    drawings = question / '相关图'
    drawings.mkdir(parents=True)
    figure = drawings / '电路.png'
    figure.write_bytes(b'original-image')
    (library / 'UtopiaPhyCyclus_book.cls').write_text('% bank class', encoding='utf-8')
    root = tmp_path / 'managed' / 'question'
    source = '电路题，含图。\r\n' + r'\includegraphics{相关图/电路}' + '\r\n'

    result = prepare_question_project(root, source, '电路：1/2', library, (question, library), DEFAULTS)
    folder = Path(result['folder'])
    assert folder.parent == root and folder.name == '电路：1_2'
    wrapper = (folder / 'main.tex').read_text(encoding='utf-8')
    assert wrapper.startswith('% !TeX program = xelatex')
    assert r'\input{question.tex}' in wrapper
    assert (folder / 'UtopiaPhyCyclus_book.cls').is_file()
    assert result['baseline'] == hashlib.sha256(source.encode('utf-8')).hexdigest()
    assert read_question_project(root) == source

    draft_file = folder / 'question.tex'
    draft = draft_file.read_text(encoding='utf-8')
    copied = folder / graphics_references(draft)[0]['path']
    assert copied.read_bytes() == b'original-image'
    copied.write_bytes(b'agent-image')
    draft_file.write_text(draft.replace('电路题', '改过的电路题'), encoding='utf-8')
    assert prepare_question_project(root, '别的题', '新名称', library, (question,), DEFAULTS) == result

    accepted = read_question_project(root)
    approved_asset = Path(graphics_references(accepted)[0]['path'])
    assert '改过的电路题' in accepted and '\\documentclass' not in accepted
    assert approved_asset.is_relative_to(root / 'imports')
    assert approved_asset.read_bytes() == b'agent-image'
    assert read_question_project(root) == accepted
    copied.write_bytes(b'future-project-edit')
    assert approved_asset.read_bytes() == b'agent-image' and figure.read_bytes() == b'original-image'


def test_standalone_keeps_engine_and_freezes_nested_tex_edits(tmp_path):
    source_dir = tmp_path / 'original'
    (source_dir / 'parts').mkdir(parents=True)
    (source_dir / 'parts' / 'diagram.tex').write_bytes('磁场示意'.encode('gb18030'))
    (source_dir / 'parts' / 'body.tex').write_bytes(r'\input{diagram}'.encode())
    source = ('% !TeX program = lualatex\n\\documentclass{article}\n\\begin{document}\n'
              '\\input{parts/body}\n\\end{document}\n')
    root = tmp_path / 'managed'
    folder = Path(prepare_question_project(root, source, '讲义', source_dir, (), DEFAULTS)['folder'])
    main = folder / 'main.tex'
    assert main.read_text(encoding='utf-8').count('% !TeX program') == 1
    assert not (folder / 'question.tex').exists()
    diagram = next(folder.rglob('diagram.tex'))
    assert diagram.read_text(encoding='utf-8') == '磁场示意'
    diagram.write_text('已修正磁场方向', encoding='utf-8')

    accepted = read_question_project(root)
    assert accepted.startswith('% !TeX program = lualatex')
    imported = root / 'imports'
    assert str(imported).replace('\\', '/') in accepted
    body = next(imported.rglob('body.tex')).read_text(encoding='utf-8')
    assert str(imported).replace('\\', '/') in body
    assert next(imported.rglob('diagram.tex')).read_text(encoding='utf-8') == '已修正磁场方向'
    assert (source_dir / 'parts' / 'diagram.tex').read_bytes() == '磁场示意'.encode('gb18030')


def test_native_revision_updates_the_synced_project_and_preserves_divergent_edits(tmp_path):
    root = tmp_path / 'managed'
    original = '原始力学题'
    initial = prepare_question_project(root, original, '力学题', tmp_path, (), DEFAULTS)
    folder = Path(initial['folder'])
    draft = folder / 'question.tex'
    draft.write_text(draft.read_text(encoding='utf-8').replace(original, 'PDF 校订后的力学题'), encoding='utf-8')
    notes = folder / 'notes.json'
    notes.write_bytes(b'annotations')
    synced = read_question_project(root)
    revised = synced.replace('力学题', '电学题')
    published = prepare_question_project(root, revised, '新标题', tmp_path, (), DEFAULTS,
                                         synced_hash=hashlib.sha256(synced.encode('utf-8')).hexdigest())
    assert published['folder'] == initial['folder']
    assert published['baseline'] == hashlib.sha256(revised.encode('utf-8')).hexdigest()
    assert '电学题' in draft.read_text(encoding='utf-8')
    assert read_question_project(root) == revised
    assert notes.read_bytes() == b'annotations'

    departing = draft.read_text(encoding='utf-8').replace('电学题', '电路题')
    draft.write_text(departing, encoding='utf-8')
    with pytest.raises(ValueError, match='均已修改'):
        prepare_question_project(root, '题库草稿也作了修改', '新标题', tmp_path, (), DEFAULTS,
                                 synced_hash=published['baseline'])
    assert draft.read_text(encoding='utf-8') == departing
    assert notes.read_bytes() == b'annotations'


def test_modified_wrapper_returns_a_full_document_and_external_dependencies_are_refused(tmp_path):
    support = tmp_path / 'support'
    support.mkdir()
    root = tmp_path / 'managed'
    folder = Path(prepare_question_project(root, '简谐运动', '讲义', support, (), DEFAULTS)['folder'])
    wrapper = folder / 'main.tex'
    wrapper.write_bytes(wrapper.read_bytes().replace(b'\\pagestyle{empty}', b'\\pagestyle{plain}'))
    accepted = read_question_project(root)
    assert r'\documentclass' in accepted and r'\pagestyle{plain}' in accepted
    assert '\n% !TeX program' not in accepted
    assert list((root / 'imports').rglob('question.tex'))

    (root / 'external.tex').write_text('private data', encoding='utf-8')
    wrapper.write_bytes(wrapper.read_bytes().replace(b'question.tex', b'../external.tex'))
    with pytest.raises(ValueError, match='可复制的项目资源'):
        read_question_project(root)

    unrelated = tmp_path / 'occupied'
    unrelated.mkdir()
    (unrelated / 'other.tex').write_text('existing work', encoding='utf-8')
    with pytest.raises(ValueError, match='受管理'):
        prepare_question_project(unrelated, '题目', '讲义', support, (), DEFAULTS)
