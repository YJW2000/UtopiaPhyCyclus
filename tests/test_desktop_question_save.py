import sqlite3

import pytest

from services import desktop_workspace, operation_log
from utils import csv_ops, question_index
from utils.image_assets import graphics_references, resolve_image


@pytest.fixture
def question_bank(tmp_path, monkeypatch):
    library = tmp_path / 'bank'
    chapters = library / 'chapters'
    path = chapters / '电场' / '2026' / '2026-M-模拟卷-1-电场.tex'
    path.parent.mkdir(parents=True)
    index = library / 'utils' / '题库索引表.csv'
    question_index.ensure_index(index, bootstrap_from_tex=False)
    monkeypatch.setattr(desktop_workspace, 'CHAPTERS_DIR', str(chapters))
    monkeypatch.setattr(csv_ops, 'CHAPTERS_DIR', str(chapters))
    monkeypatch.setattr(csv_ops, 'CSV_INDEX_PATH', str(index))
    monkeypatch.setattr(operation_log, 'OPERATION_LOG_PATH', str(index.with_name('operations.sqlite3')))
    workspace = desktop_workspace.DesktopWorkspace(library, tmp_path / 'state')
    source = '\n'.join((
        '% === Begin Label Data ===', '% ID: 47', '% 标签: 电场', '% === End  Label Data ===',
        r'\begin{problem}{2026}{M}{模拟卷}{1}{电场}',
        r'电荷受力如图。\includegraphics{原图.png}', r'\end{problem}', '',
    ))
    image = path.parent / '原图.png'
    image.write_bytes(b'original image')
    path.write_bytes(source.encode('utf-8'))
    params = {'questionPath': path.relative_to(chapters).as_posix()}
    csv_ops.add_to_csv_index(str(path), source, '2026', 'M', '模拟卷', '1', '电场')
    yield workspace, library, path, source, params, index
    workspace.jobs.close()


def _filled_draft(workspace, params):
    document = workspace.create(params)
    source = document['source'] + '\n'.join((
        r'\begin{answer}向右\end{answer}', r'\begin{solutions}根据 $F=qE$ 判断。\end{solutions}', '',
    ))
    return workspace.save({'id': document['id'], 'source': source, 'revision': document['revision']})


def test_outside_question_is_rejected_by_index_and_desktop(question_bank, tmp_path):
    workspace, library, path, source, params, index = question_bank
    outside = tmp_path / path.name
    outside.write_text(source, encoding='utf-8')
    metadata = ('2026', 'M', '模拟卷', '1', '电场')
    with pytest.raises(ValueError, match='当前题库'):
        csv_ops.add_to_csv_index(outside, source, *metadata)
    with pytest.raises(ValueError, match='当前题库'):
        csv_ops.update_csv_index_for_edit(path, outside, source, *metadata)
    relative = '../../' + outside.name
    with pytest.raises(ValueError, match='当前题库'):
        workspace.create({'questionPath': relative})
    missing = path.relative_to(library / 'chapters').with_name('missing.tex').as_posix()
    with pytest.raises(ValueError, match='题目文件不存在或已移动'):
        workspace.create({'questionPath': missing})
    row, = question_index.read_rows(csv_path=index)
    assert row['题目ID'] == '47'
    assert row['相对文件路径'].replace('\\', '/') == params['questionPath']


def test_save_original_retains_id_index_and_images(question_bank):
    workspace, library, path, original, params, index = question_bank
    unopened = workspace.create(params)
    before = question_index.get_row(params['questionPath'], csv_path=index)
    workspace.save_question({'id': unopened['id'], 'revision': unopened['revision']})
    assert path.read_bytes() == original.encode('utf-8')
    assert not (library / '.backups').exists()

    edited = _filled_draft(workspace, params)
    assert path.read_bytes() == original.encode('utf-8')
    saved = workspace.save_question({'id': edited['id'], 'revision': edited['revision']})
    assert saved['questionPath'] == params['questionPath']
    assert saved['source'] == edited['source']
    assert workspace.create(params)['id'] == saved['id']
    written = path.read_text(encoding='utf-8')
    assert r'\begin{answer}向右\end{answer}' in written
    image = resolve_image(graphics_references(written)[0]['path'], library, path.parent, written)
    assert image.parent == path.parent / (path.stem + ' 相关图')
    assert image.read_bytes() == b'original image'
    backups = list((library / '.backups' / 'chapters' / '电场' / '2026').glob('*.tex'))
    assert len(backups) == 1 and backups[0].read_bytes() == original.encode('utf-8')
    after = question_index.get_row(params['questionPath'], csv_path=index)
    assert after['题目ID'] == before['题目ID'] == '47'
    assert after['初次录入的时间'] == before['初次录入的时间']
    assert after['答案'] == '向右'
    assert after['解析'] == r'根据 $F=qE$ 判断。'


def test_changed_original_and_stale_draft_are_preserved(question_bank):
    workspace, _library, path, original, params, _index = question_bank
    edited = _filled_draft(workspace, params)
    with pytest.raises(ValueError, match='草稿已有新版本'):
        workspace.save_question({'id': edited['id'], 'revision': 0})

    remote = original.replace('电荷受力', '粒子受力')
    path.write_bytes(remote.encode('utf-8'))
    with pytest.raises(ValueError, match='原题文件已有新版本'):
        workspace.save_question({'id': edited['id'], 'revision': edited['revision']})
    assert path.read_bytes() == remote.encode('utf-8')
    fresh = workspace.create(params)
    assert fresh['id'] != edited['id'] and '粒子受力' in fresh['source']
    assert workspace.open({'id': edited['id']})['source'] == edited['source']


def test_index_failure_restores_original_and_allows_retry(question_bank, monkeypatch):
    workspace, _library, path, original, params, index = question_bank
    edited = _filled_draft(workspace, params)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('simulated unavailable index')

    request = {'id': edited['id'], 'revision': edited['revision']}
    with monkeypatch.context() as patch:
        patch.setattr(csv_ops, 'update_csv_index_for_edit', unavailable)
        with pytest.raises(sqlite3.OperationalError, match='unavailable index'):
            workspace.save_question(request)
    assert path.read_bytes() == original.encode('utf-8')
    assert not question_index.get_row(params['questionPath'], csv_path=index)['答案']
    workspace.save_question(request)
    assert question_index.get_row(params['questionPath'], csv_path=index)['答案'] == '向右'


def test_legacy_workbench_recovers_original_link(question_bank):
    workspace, _library, _path, _original, params, _index = question_bank
    current = workspace.create(params)
    legacy = workspace._read(current['id'])
    for key in ('questionPath', '_questionHash', '_questionDraftHash'):
        legacy.pop(key)
    workspace._write(legacy)
    assert workspace.open({'id': current['id']})['questionPath'] == params['questionPath']
    assert workspace.create(params)['id'] == current['id']
