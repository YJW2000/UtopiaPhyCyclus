from pathlib import Path

import pytest

from services import desktop_workspace, operation_log
from services.desktop_file_info import read_file_info, save_file_info
from utils import csv_ops, question_index
from utils.index_rebuild import row_from_tex


_SOURCE = '''% === Begin Label Data ===
% ID: 72
% 标签: 摩擦力
% 备注: 保留这一行
% === End  Label Data ===

\\begin{problem}{2025}{M}{测试卷}{3}{电场}
这是不可改写的题干。\\includegraphics{题目 相关图/示意图.png}
% \\input{题目 相关图/注释.tex}
\\input{题目 相关图/力学图.tex}
\\end{problem}
\\begin{answer}$R=2\\,\\Omega$。\\end{answer}
'''


@pytest.fixture
def bank(tmp_path):
    relative = '电场/2025/2025-M-测试卷-3-电场.tex'
    file = tmp_path / 'chapters' / relative
    assets = file.parent / '题目 相关图'
    assets.mkdir(parents=True)
    (assets / '示意图.png').write_bytes(b'test image bytes')
    (assets / '力学图.tex').write_bytes(b'% TikZ file bytes')
    file.write_bytes(_SOURCE.replace('\n', '\r\n').encode('gb18030'))
    csv = tmp_path / 'utils' / '题库索引表.csv'
    with question_index.connection(csv, bootstrap_from_tex=False):
        pass
    question_index.put_row(row_from_tex(_SOURCE, relative, file.stat(), {}), csv_path=csv)
    return tmp_path, relative, file, assets, csv


def test_edit_file_information_moves_index_and_rebases_existing_assets(bank):
    root, relative, original, assets, csv = bank
    before = read_file_info(root, relative)
    assert (before['id'], before['year'], before['paperType'], before['tags']) == ('72', '2025', 'M', '摩擦力')
    result = save_file_info(root, relative, {
        'year': '2026', 'paper': '新卷', 'subject': '电路, 电场', 'tags': '测试, 霍尔效应, 测试',
    }, before['sourceHash'])
    assert result['path'] == '电路/2026/2026-M-新卷-3-电路，电场.tex'
    assert not original.exists()
    new_file = root / 'chapters' / result['path']
    source = new_file.read_bytes().decode('gb18030')
    assert '\r\n' in source
    assert r'\begin{problem}{2026}{M}{新卷}{3}{电路，电场}' in source
    assert r'这是不可改写的题干。\includegraphics{chapters/电场/2025/题目 相关图/示意图.png}' in source
    assert r'\input{chapters/电场/2025/题目 相关图/力学图.tex}' in source
    assert r'% \input{题目 相关图/注释.tex}' in source
    assert r'\begin{answer}$R=2\,\Omega$。\end{answer}' in source
    assert '% 备注: 保留这一行' in source and '% ID: 72' in source
    assert '% 标签: 测试，霍尔效应' in source
    assert (assets / '力学图.tex').read_bytes() == b'% TikZ file bytes'
    assert (assets / '示意图.png').read_bytes() == b'test image bytes'
    assert read_file_info(root, result['path']) == result
    assert question_index.get_row(relative, csv_path=csv) is None
    indexed = question_index.get_row(result['path'], csv_path=csv)
    assert indexed['题目ID'] == '72' and indexed['备注'] == '保留这一行'
    assert indexed['标签'] == '测试，霍尔效应' and indexed['知识板块'] == '电路，电场'


@pytest.mark.parametrize('changed_fields', [{'paper': '回滚卷'}, {'tags': '同路径回滚'}])
def test_failed_index_update_rolls_back_original_bytes(bank, monkeypatch, changed_fields):
    root, relative, original, _assets, csv = bank
    source = original.read_bytes()
    before = read_file_info(root, relative)

    def fail_index_write(conn):
        raise RuntimeError('synthetic index update error')

    monkeypatch.setattr(question_index, '_bump', fail_index_write)
    with pytest.raises(RuntimeError, match='synthetic'):
        save_file_info(root, relative, changed_fields, before['sourceHash'])
    assert original.read_bytes() == source
    assert list(original.parent.glob('*.tex')) == [original]
    assert question_index.get_row(relative, csv_path=csv)['题目ID'] == '72'
    assert read_file_info(root, relative) == before


def test_stale_and_colliding_paths_do_not_overwrite(bank):
    root, relative, original, _assets, _csv = bank
    before = read_file_info(root, relative)
    original.write_bytes(original.read_bytes() + b'% external edit')
    with pytest.raises(ValueError, match='新版本'):
        save_file_info(root, relative, {'paper': '新卷'}, before['sourceHash'])
    before = read_file_info(root, relative)
    collision = original.with_name(original.name.replace('测试卷', '另一卷'))
    collision.write_bytes(b'incumbent')
    with pytest.raises(FileExistsError, match='已存在'):
        save_file_info(root, relative, {'paper': '另一卷'}, before['sourceHash'])
    assert collision.read_bytes() == b'incumbent'
    assert read_file_info(root, relative) == before
    with pytest.raises(ValueError, match='当前题库'):
        read_file_info(root, '../untouched.tex')
    with pytest.raises(ValueError, match='当前题库'):
        read_file_info(root, str(original.resolve()))


def test_workspace_repairs_missing_opening_when_updating_file_info(bank, tmp_path, monkeypatch):
    root, relative, original, _assets, csv = bank
    old_header = r'\begin{problem}{2025}{M}{测试卷}{3}{电场}'
    new_header = r'\begin{problem}{2026}{G}{测试卷}{3}{电场}'
    old_input = r'\input{题目 相关图/力学图.tex}'
    new_input = r'\input{chapters/电场/2025/题目 相关图/力学图.tex}'
    solutions = '\n' + r'\begin{solutions}根据电路关系计算。\end{solutions}' + '\n'
    damaged = _SOURCE.replace(old_header, '% ' + old_header) + solutions
    original.write_bytes(damaged.replace('\n', '\r\n').encode('gb18030'))
    monkeypatch.setattr(desktop_workspace, 'CHAPTERS_DIR', str(root / 'chapters'))
    monkeypatch.setattr(csv_ops, 'CHAPTERS_DIR', str(root / 'chapters'))
    monkeypatch.setattr(csv_ops, 'CSV_INDEX_PATH', str(csv))
    monkeypatch.setattr(operation_log, 'OPERATION_LOG_PATH', str(csv.with_name('operations.sqlite3')))

    workspace = desktop_workspace.DesktopWorkspace(root, tmp_path / 'state')
    try:
        draft = workspace.create({'questionPath': relative})
        draft = workspace.save({
            'id': draft['id'], 'revision': draft['revision'],
            'source': draft['source'].replace('这是不可改写的题干。', '这是草稿中修改过的题干。'),
        })
        request = {'id': draft['id'], 'path': relative, 'revision': draft['revision']}
        info = workspace.file_info(request)
        saved = workspace.save_file_info({
            **request, 'fields': {'paperType': 'G', 'year': '2026'}, 'expectedHash': info['sourceHash'],
        })

        new_relative = '电场/2026/2026-G-测试卷-3-电场.tex'
        updated = saved['document']
        assert saved['file']['path'] == updated['questionPath'] == new_relative
        assert saved['file']['id'] == '72'
        assert not original.exists()
        current = root / 'chapters' / new_relative
        expected = damaged.replace('这是不可改写的题干。', new_header + '\n这是不可改写的题干。').replace(old_input, new_input)
        expected = expected.replace(r'\includegraphics{题目 相关图/示意图.png}',
                                    r'\includegraphics{chapters/电场/2025/题目 相关图/示意图.png}')
        assert current.read_bytes() == expected.replace('\n', '\r\n').encode('gb18030')
        assert updated['source'] == draft['source'].replace(
            '这是草稿中修改过的题干。', new_header + '\n这是草稿中修改过的题干。',
        ).replace(old_input, new_input)
        assert workspace.create({'questionPath': new_relative})['id'] == draft['id']
        assert question_index.get_row(relative, csv_path=csv) is None
        indexed = question_index.get_row(new_relative, csv_path=csv)
        assert indexed['题目ID'] == '72' and indexed['试卷类型'] == 'G'

        workspace.save_question({'id': updated['id'], 'revision': updated['revision']})
        saved_source = current.read_text(encoding='utf-8')
        assert '这是草稿中修改过的题干。' in saved_source
        assert new_input in saved_source
    finally:
        workspace.jobs.close()


def test_ambiguous_missing_opening_does_not_rewrite_file(bank):
    root, relative, original, _assets, csv = bank
    header = r'\begin{problem}{2025}{M}{测试卷}{3}{电场}'
    damaged = (_SOURCE.replace(header + '\n', '') + r'\end{problem}').encode('utf-8')
    original.write_bytes(damaged)
    before = read_file_info(root, relative)

    with pytest.raises(ValueError, match='problem'):
        save_file_info(root, relative, {'paperType': 'G'}, before['sourceHash'])

    assert original.read_bytes() == damaged
    assert list(original.parent.glob('*.tex')) == [original]
    assert question_index.get_row(relative, csv_path=csv)['题目ID'] == '72'
